"""script_job.py — executor genérico de scripts de módulo (roda DENTRO do worker
da fila certa, onde as ferramentas do runtime estão no PATH).

Contrato com os scripts:
  - o comando vem do manifest (via dispatch.montar_cmd) e roda com cwd na raiz
    do repo (/app no container);
  - stdout+stderr são transmitidos linha a linha para o job.log (console SSE);
  - uma linha `RESULTADO: <caminho>` promove o caminho a result_path do job —
    é assim que a UI sabe qual arquivo de resultado exibir (omica/results.py).
"""
from __future__ import annotations

import shlex
import subprocess

from omica.config import REPO_ROOT
from omica.jobs.queue import append_log, read_status, write_status

PREFIXO_RESULTADO = "RESULTADO:"


def executar(job_id: str) -> None:
    status = read_status(job_id) or {}
    cmd = status.get("cmd")
    if not cmd:
        raise RuntimeError(f"job {job_id} sem 'cmd' no status.json")
    append_log(job_id, f"$ {cmd}")
    processo = subprocess.Popen(
        shlex.split(cmd),
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    result_path = None
    assert processo.stdout is not None
    for linha in processo.stdout:
        linha = linha.rstrip()
        if not linha:
            continue
        append_log(job_id, linha)
        if linha.startswith(PREFIXO_RESULTADO):
            result_path = linha[len(PREFIXO_RESULTADO):].strip()
    rc = processo.wait()
    if rc != 0:
        raise RuntimeError(f"script terminou com código {rc}")
    if result_path:
        write_status(job_id, "done", result_path=result_path)
    else:
        write_status(job_id, "done")
