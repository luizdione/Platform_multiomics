"""proveniencia.py — receita ÚNICA do sha256 do código de um passo: o script e o `_comum.py`
do módulo, com quebra de linha normalizada (mesma regra dos selos: CRLF no host, LF no
container).

Dois lados usam a mesma função, e é isso que dá sentido à comparação:
  - o PLANO (`runs._sha_script`), quando o autor aprova;
  - o WORKER (`jobs/script_job.py`), no instante em que o script começa a rodar — grava
    em `status.json` como `proveniencia_execucao`.
O envelope só atesta um passo se as duas forem iguais: conferir só ao enfileirar deixava
passar um script trocado entre a fila e a execução.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from omica.config import REPO_ROOT

EXTENSOES_SCRIPT = (".py", ".R", ".sh")


def alvo_do_cmd(cmd: str) -> Optional[str]:
    """O arquivo de script que um cmd executa (1º token com extensão de script)."""
    return next((t for t in cmd.split() if t.endswith(EXTENSOES_SCRIPT)), None)


def _sha(p: Path) -> Optional[str]:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if p.is_file() else None


def sha_codigo(modulo: str, alvo: Optional[str]) -> dict[str, Optional[str]]:
    return {"script": alvo,
            "sha_script": _sha(REPO_ROOT / alvo) if alvo else None,
            "sha_comum": _sha(REPO_ROOT / "modules" / modulo / "scripts" / "_comum.py")}
