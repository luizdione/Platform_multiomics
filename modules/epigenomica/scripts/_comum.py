"""_comum.py — utilitários compartilhados pelos scripts do módulo genômica.

Contrato com omica/jobs/script_job.py: tudo que for impresso vai para o console
do job; a linha `RESULTADO: <caminho>` aponta o arquivo de resumo para a UI.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


def rodar(cmd: list[str]) -> str:
    """Executa uma ferramenta externa e devolve o stdout (erros vão à exceção)."""
    log("  $ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"comando falhou ({r.returncode}): {cmd[0]}")
    return r.stdout


def contar_variantes(vcf: Path) -> int:
    out = rodar(["bcftools", "view", "-H", str(vcf)])
    return sum(1 for linha in out.splitlines() if linha.strip())


def escrever_resumo(saida_dir: Path, nome: str, dados: dict) -> Path:
    saida_dir.mkdir(parents=True, exist_ok=True)
    destino = saida_dir / nome
    dados = {"gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S"), **dados}
    destino.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"RESULTADO: {destino}")
    return destino
