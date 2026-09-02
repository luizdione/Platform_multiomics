"""demo.py — job de demonstração da Fase 1 (aceite: atravessar a fila com console vivo).

Não faz ciência: verifica a infraestrutura de ponta a ponta (fila → execução →
log → arquivo de resultado → UI lendo o arquivo). Serve de gabarito para os
jobs reais dos módulos e para o modo aula.
"""
from __future__ import annotations

import json
import time

from omica.config import get_config
from omica.jobs.queue import append_log, write_status

PASSOS = [
    "Lendo configuração única (omica/config.py)",
    "Conferindo diretórios de dados (raw/refs/results/work)",
    "Simulando etapa de extração",
    "Simulando etapa de análise",
    "Gravando arquivo de resultado",
]


def executar(job_id: str) -> None:
    cfg = get_config()
    cfg.ensure_dirs()
    append_log(job_id, "Job demo iniciado — plataforma ômica")
    soma = 0
    for i, passo in enumerate(PASSOS, start=1):
        append_log(job_id, f"({i}/{len(PASSOS)}) {passo}")
        soma += i
        time.sleep(0.4)
    destino = cfg.results_dir / "core" / "demo"
    destino.mkdir(parents=True, exist_ok=True)
    resultado = destino / f"{job_id}.json"
    resultado.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "passos_executados": len(PASSOS),
                "soma_verificacao": soma,
                "mensagem_pt": "Infraestrutura da ômica funcionando de ponta a ponta.",
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    append_log(job_id, f"Resultado gravado em {resultado}")
    write_status(job_id, "done", result_path=str(resultado))
