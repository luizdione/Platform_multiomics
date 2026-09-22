"""conftest — isola os testes do ambiente real.

OMICA_DATA aponta para um diretório temporário e OMICA_REDIS_URL fica vazio
(força o fallback de thread), para que a suíte rode igual no host Windows, no
container e no CI ubuntu — sem tocar em E:\\data.

A RAM também é isolada: sem isso a suíte lia a memória REAL da máquina e quatro
testes de fila quebravam quando havia outro job pesado rodando (auditoria de
2026-09-14: 4 failed com a máquina a 83%, 0 failed com ela ociosa) — o CI verde
só significava "o runner estava vazio". Testes que exercitam a guarda continuam
monkeypatchando psutil dentro do próprio teste, e passam a valer de verdade.
"""
from __future__ import annotations

import os

import pytest

from omica.config import reset_config_cache
from omica.jobs import guarda_ram


class _MemoriaFake:
    def __init__(self, percent: float) -> None:
        self.percent = percent


@pytest.fixture(autouse=True)
def ambiente_isolado(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICA_DATA", str(tmp_path / "dados"))
    monkeypatch.setenv("GENOME_DATA", str(tmp_path))
    monkeypatch.setenv("OMICA_REDIS_URL", "")
    monkeypatch.setenv("OMICA_MAX_RAM_GB", "24")
    monkeypatch.setenv("OMICA_RAM_MAX_PCT", "80")
    monkeypatch.delenv("OMICA_IN_DOCKER", raising=False)
    monkeypatch.setattr(guarda_ram.psutil, "virtual_memory", lambda: _MemoriaFake(12.0))
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture()
def cfg():
    from omica.config import get_config

    c = get_config()
    c.ensure_dirs()
    return c
