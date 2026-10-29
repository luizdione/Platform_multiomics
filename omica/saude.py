"""saude.py — o retrato de saúde da plataforma, compartilhado pela UI (/saude, home) e
pela API v1 (/api/v1/saude). Antes vivia dentro de web/app.py; a API não pode importar
o app (o app é quem inclui o router da API), então o código veio para cá em vez de ser
duplicado.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional

from omica import __version__
from omica.config import get_config
from omica.jobs import guarda_ram, queue
from omica.registry import Registry, load_registry

# rótulo de erro dos loaders v2 para o que vem de <OMICA_DATA>/projetos (rascunhos e legados
# privados, fora do git): "dados:<especie>/<slug>"; o do repositório é "repo:..."
ORIGEM_DADOS = "dados:"

_log = logging.getLogger(__name__)


def erros_de_carga(reg: Optional[Registry] = None) -> dict[str, Any]:
    """Crítica v2, médio 15 (anti-lição nº 2, nada morre em silêncio): espécie ou subprojeto
    cujo YAML não carrega some da 1ª página — sem isto, só a home avisava. Devolve as contagens
    e os erros com as chaves dos próprios loaders; uma exceção no carregamento inteiro vira a
    entrada `_catalogo` (e não um /saude que quebra)."""
    from omica import especies as especies_mod
    from omica import subprojetos as subprojetos_mod

    try:
        especies, erros_esp = especies_mod.carregar_especies(reg=reg)
        subs, erros_sub = subprojetos_mod.carregar_subprojetos(especies, reg=reg)
    except Exception as e:  # noqa: BLE001 — reportado, nunca engolido
        # o /saude da UI não pede credencial: só a classe da exceção (a mensagem pode trazer
        # caminho do host, como na API v1); o traceback inteiro vai para o log
        _log.exception("falha ao carregar o catálogo de espécies/subprojetos")
        return {"especies": 0, "subprojetos": 0, "erros_subprojetos": {},
                "erros_especies": {"_catalogo": f"falha ao carregar o catálogo: {type(e).__name__}"}}
    return {"especies": len(especies), "subprojetos": len(subs),
            "erros_especies": dict(erros_esp), "erros_subprojetos": dict(erros_sub)}


def redigir_erros_subprojetos(erros: Mapping[str, str], detalhar: bool) -> dict[str, str]:
    """Crítica v2, médio 16: a mensagem de erro de um subprojeto de <dados> pode trazer trecho
    do próprio YAML (pergunta, título) de um subprojeto PRIVADO. Sem a autorização do autor,
    esses erros viram uma linha só, com a contagem; os do repositório (públicos) ficam inteiros."""
    if detalhar:
        return dict(erros)
    publicos = {k: v for k, v in erros.items() if not k.startswith(ORIGEM_DADOS)}
    ocultos = len(erros) - len(publicos)
    if ocultos:
        publicos[ORIGEM_DADOS + "*"] = (f"{ocultos} subprojeto(s) fora do repositório com erro de carga "
                                        "(detalhe só para o autor: python -m omica doctor no host)")
    return publicos


def dados_saude() -> dict[str, Any]:
    cfg = get_config()
    reg = load_registry()
    backend = "rq" if queue._rq_queue() is not None else "thread"
    carga = erros_de_carga(reg)
    return {
        "versao": __version__,
        "ram_pct": guarda_ram.uso_ram_pct(),
        "ram_limite_pct": cfg.ram_max_pct,
        "fila_backend": backend,
        "modulos_validos": len(reg.modules),
        "erros_manifests": reg.erros,
        "data_dir": str(cfg.data_dir),
        # v2 (médio 15): o plugin Bioinfo ignora chaves novas; a API v1 as repassa inteiras
        "erros_especies": carga["erros_especies"],
        "erros_subprojetos": carga["erros_subprojetos"],
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
