"""motor_local.py — a ÚNICA porta da plataforma para o LLM local (Ollama).

Duas travas antes de qualquer byte sair para o Ollama:

1. **Interruptor da GPU do autor** (`switch.json`, convenção do LILO/ollama-delegate). A GPU
   é do dono: durante dinâmica molecular, Boltz ou render ele a reserva, e um modelo de
   14b carregado no meio derruba a MD (VRAM transborda para a RAM). Aqui a regra é mais
   dura que a do delegate: interruptor AUSENTE ou ILEGÍVEL também é recusa — um mount
   esquecido no compose não pode abrir a GPU. O interruptor é relido DEPOIS de obter a
   trava: o dono pode ter reservado a GPU enquanto esta chamada esperava a vez.
2. **Uma chamada por vez** (trava de kernel em work/locks/llm.lock): duas inferências
   simultâneas dobram a VRAM e não ficam mais rápidas.

O n8n nunca fala com o Ollama: se falasse, contornaria as duas travas.

Saída estruturada: `format` recebe o JSON Schema (Ollama ≥0.5), `think: false` e
temperatura 0 — o adaptador quer a resposta mais provável, não criatividade. Mesmo assim a
resposta é revalidada pelo chamador; o esquema no Ollama é ajuda, não garantia.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional, Union

import httpx

from omica.config import get_config
from omica.saude import estado_gpu
from omica.travas import TravaOcupada, trava

TIMEOUT_S = 240  # primeira carga de um modelo 14b na GPU leva ~1 min; inferência, mais


class MotorIndisponivel(RuntimeError):
    """Recusa ANTES de chamar o modelo. `codigo`: gpu_reservada | gpu_desconhecida |
    motor_ocupado | ollama_fora | resposta_invalida."""

    def __init__(self, codigo: str, detalhe: str) -> None:
        super().__init__(f"{codigo}: {detalhe}")
        self.codigo, self.detalhe = codigo, detalhe


def _exigir_gpu() -> None:
    gpu = estado_gpu()
    if gpu["liberada"]:
        return
    codigo = "gpu_desconhecida" if gpu["estado"] == "desconhecido" else "gpu_reservada"
    raise MotorIndisponivel(codigo, f"GPU não liberada pelo dono ({gpu['estado']}): {gpu['motivo']}")


def extrair_json(texto: str) -> dict[str, Any]:
    """Objeto JSON de uma resposta de modelo (tolera <think> e texto em volta)."""
    from omica.professor import _extrair_json

    return _extrair_json(texto)


def chat_json(sistema: str, usuario: str, esquema: Union[dict[str, Any], str],
              modelo: Optional[str] = None, espera_s: float = 300.0,
              temperatura: float = 0.0) -> dict[str, Any]:
    """Uma chamada ao Ollama com saída JSON. Devolve {"dados", "modelo", "duracao_s"}.
    `esquema`: JSON Schema (saída estruturada) ou "json" (JSON livre, uso do Professor).

    Levanta MotorIndisponivel; nunca devolve resposta parcial."""
    cfg = get_config()
    _exigir_gpu()  # recusa barata, sem esperar a fila
    try:
        with trava(cfg.work_dir / "locks" / "llm.lock", espera_s=espera_s):
            _exigir_gpu()  # o dono pode ter reservado a GPU enquanto esperávamos
            corpo = {
                "model": modelo or cfg.ollama_model,
                "stream": False,
                "think": False,
                "format": esquema,
                "options": {"temperature": temperatura},
                "messages": [{"role": "system", "content": sistema},
                             {"role": "user", "content": usuario}],
            }
            inicio = time.monotonic()
            try:
                r = httpx.post(f"{cfg.ollama_url}/api/chat", json=corpo, timeout=TIMEOUT_S)
                r.raise_for_status()
                conteudo = r.json()["message"]["content"]
            except (httpx.HTTPError, KeyError, ValueError) as e:
                raise MotorIndisponivel("ollama_fora", f"{e.__class__.__name__}: {e}") from None
            duracao = time.monotonic() - inicio
    except TravaOcupada:
        raise MotorIndisponivel("motor_ocupado", "outra chamada ao LLM em andamento") from None
    try:
        dados = extrair_json(conteudo)
    except (ValueError, json.JSONDecodeError) as e:
        raise MotorIndisponivel("resposta_invalida", str(e)[:300]) from None
    return {"dados": dados, "modelo": corpo["model"], "duracao_s": round(duracao, 2)}
