"""results.py — o ÚNICO caminho pelo qual a UI lê números científicos.

Anti-lição nº 4 da auditoria: as plataformas antigas cravavam resultados em HTML
estático, que apodrecia quando a pesquisa avançava. Aqui a UI só exibe o que for
lido, na hora, de um arquivo de resultado em disco (results_spec do manifest ou
saída de job). Sem arquivo → a UI diz "sem dados", nunca inventa número.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from omica.config import get_config


def carregar_json(caminho: Path) -> Optional[dict[str, Any]]:
    if not caminho.exists():
        return None
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def resultado_de_job(job_id: str) -> Optional[dict[str, Any]]:
    """Carrega o arquivo apontado por result_path no status do job."""
    from omica.jobs.queue import read_status

    status = read_status(job_id)
    if not status or "result_path" not in status:
        return None
    return carregar_json(Path(status["result_path"]))


def resultado_do_modulo(chave_relativa: str) -> Optional[dict[str, Any]]:
    """Carrega um resultado declarado em results_spec (relativo a results_dir)."""
    return carregar_json(get_config().results_dir / chave_relativa)


def carregar_tabela(chave_relativa: str, max_linhas: int = 15) -> Optional[dict[str, Any]]:
    """Carrega as primeiras linhas de um TSV/CSV de resultados (relativo a
    results_dir) para exibição — nunca o arquivo inteiro no navegador."""
    caminho = get_config().results_dir / chave_relativa
    if not caminho.exists():
        return None
    sep = "\t" if caminho.suffix.lower() in (".tsv", ".txt") else ","
    try:
        with caminho.open("r", encoding="utf-8") as f:
            colunas = f.readline().rstrip("\n").split(sep)
            linhas = []
            total = 0
            for linha in f:
                total += 1
                if len(linhas) < max_linhas:
                    linhas.append(linha.rstrip("\n").split(sep))
    except OSError:
        return None
    return {"colunas": colunas, "linhas": linhas, "total_linhas": total,
            "exibidas": len(linhas), "arquivo": str(caminho)}
