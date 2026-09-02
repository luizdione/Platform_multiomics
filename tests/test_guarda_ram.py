"""Guarda de RAM: um validador que aprova tudo é suspeito — aqui provamos que
ela também RECUSA (lição do check_dois.py registrada na memória do autor)."""
import json

import pytest

from omica.jobs import guarda_ram


class _VM:
    def __init__(self, pct):
        self.percent = pct


def test_recusa_quando_sistema_acima_do_teto(monkeypatch):
    monkeypatch.setattr(guarda_ram.psutil, "virtual_memory", lambda: _VM(93.0))
    with pytest.raises(guarda_ram.RecusadoPorRAM, match="93%"):
        guarda_ram.autorizar(1.0)


def test_recusa_quando_orcamento_de_jobs_estoura(monkeypatch, cfg):
    monkeypatch.setattr(guarda_ram.psutil, "virtual_memory", lambda: _VM(40.0))
    ativo = cfg.jobs_dir / "job-ativo"
    ativo.mkdir(parents=True)
    (ativo / "status.json").write_text(
        json.dumps({"job_id": "job-ativo", "status": "running", "mem_gb": 20.0}),
        encoding="utf-8",
    )
    with pytest.raises(guarda_ram.RecusadoPorRAM, match="Orçamento"):
        guarda_ram.autorizar(8.0)  # 20 + 8 > 24


def test_autoriza_quando_cabe(monkeypatch, cfg):
    monkeypatch.setattr(guarda_ram.psutil, "virtual_memory", lambda: _VM(40.0))
    guarda_ram.autorizar(2.0)  # não levanta


def test_job_concluido_nao_conta_no_orcamento(monkeypatch, cfg):
    monkeypatch.setattr(guarda_ram.psutil, "virtual_memory", lambda: _VM(40.0))
    feito = cfg.jobs_dir / "job-feito"
    feito.mkdir(parents=True)
    (feito / "status.json").write_text(
        json.dumps({"job_id": "job-feito", "status": "done", "mem_gb": 23.0}),
        encoding="utf-8",
    )
    guarda_ram.autorizar(8.0)  # done não soma
