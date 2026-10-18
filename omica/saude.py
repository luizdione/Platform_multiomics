"""saude.py — o retrato de saúde da plataforma, compartilhado pela UI (/saude, home) e
pela API v1 (/api/v1/saude). Antes vivia dentro de web/app.py; a API não pode importar
o app (o app é quem inclui o router da API), então o código veio para cá em vez de ser
duplicado.
"""
from __future__ import annotations

import json
from typing import Any

from omica import __version__
from omica.config import get_config
from omica.jobs import guarda_ram, queue
from omica.registry import load_registry


def dados_saude() -> dict[str, Any]:
    cfg = get_config()
    reg = load_registry()
    backend = "rq" if queue._rq_queue() is not None else "thread"
    return {
        "versao": __version__,
        "ram_pct": guarda_ram.uso_ram_pct(),
        "ram_limite_pct": cfg.ram_max_pct,
        "fila_backend": backend,
        "modulos_validos": len(reg.modules),
        "erros_manifests": reg.erros,
        "data_dir": str(cfg.data_dir),
    }


def estado_gpu() -> dict[str, Any]:
    """Lê o interruptor da GPU do autor. Arquivo ausente ou ilegível NÃO vira "liberado":
    quem chama o LLM trata `liberada=False` como recusa (falha fechada). O ollama-delegate
    assume "ligado" quando o arquivo falta; aqui um mount esquecido não pode abrir a GPU."""
    caminho = get_config().gpu_switch
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"liberada": False, "estado": "desconhecido", "motivo": f"interruptor ausente: {caminho}"}
    except (OSError, json.JSONDecodeError) as e:
        return {"liberada": False, "estado": "desconhecido", "motivo": f"interruptor ilegível: {e}"}
    estado = str(dados.get("estado", "")).lower()
    return {"liberada": estado == "on", "estado": estado or "desconhecido",
            "motivo": dados.get("motivo", ""), "desde": dados.get("em", "")}
