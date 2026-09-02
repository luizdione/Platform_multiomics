"""guarda_ram.py — orçamento de RAM antes de enfileirar qualquer job.

Regra operacional da máquina de 32 GB: nunca lançar job se o uso total do sistema
passar de 80% (ram_max_pct), e a soma de mem_gb declarada dos jobs ativos mais o
job novo não pode estourar OMICA_MAX_RAM_GB. O que exceder é recusado com
mensagem clara — nunca estourar para swap.
"""
from __future__ import annotations

import json

import psutil

from omica.config import get_config


class RecusadoPorRAM(RuntimeError):
    """Job recusado pela guarda de RAM. A mensagem já vem pronta para a UI."""


def uso_ram_pct() -> float:
    return float(psutil.virtual_memory().percent)


def jobs_ativos_mem_gb() -> float:
    """Soma de mem_gb declarada dos jobs em estado queued/running (status.json)."""
    cfg = get_config()
    total = 0.0
    if not cfg.jobs_dir.exists():
        return total
    for status_file in cfg.jobs_dir.glob("*/status.json"):
        try:
            dados = json.loads(status_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if dados.get("status") in ("queued", "running"):
            total += float(dados.get("mem_gb", 0.0))
    return total


def autorizar(mem_gb_novo: float) -> None:
    """Levanta RecusadoPorRAM se o job novo não couber no orçamento."""
    cfg = get_config()
    pct = uso_ram_pct()
    if pct >= cfg.ram_max_pct:
        raise RecusadoPorRAM(
            f"RAM do sistema em {pct:.0f}% (limite {cfg.ram_max_pct}%). "
            "Aguarde os jobs atuais terminarem antes de lançar outro."
        )
    ativos = jobs_ativos_mem_gb()
    if ativos + mem_gb_novo > cfg.max_ram_gb:
        raise RecusadoPorRAM(
            f"Orçamento de RAM dos jobs estourado: {ativos:.1f} GB ativos + "
            f"{mem_gb_novo:.1f} GB pedidos > {cfg.max_ram_gb:.0f} GB (OMICA_MAX_RAM_GB)."
        )
