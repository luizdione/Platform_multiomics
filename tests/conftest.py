"""conftest — isola os testes do ambiente real.

OMICA_DATA aponta para um diretório temporário e OMICA_REDIS_URL fica vazio
(força o fallback de thread), para que a suíte rode igual no host Windows, no
container e no CI ubuntu — sem tocar em E:\\data.
"""
from __future__ import annotations

import os

import pytest

from omica.config import reset_config_cache


@pytest.fixture(autouse=True)
def ambiente_isolado(tmp_path, monkeypatch):
    monkeypatch.setenv("OMICA_DATA", str(tmp_path / "dados"))
    monkeypatch.setenv("GENOME_DATA", str(tmp_path))
    monkeypatch.setenv("OMICA_REDIS_URL", "")
    monkeypatch.setenv("OMICA_MAX_RAM_GB", "24")
    monkeypatch.setenv("OMICA_RAM_MAX_PCT", "80")
    monkeypatch.delenv("OMICA_IN_DOCKER", raising=False)
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture()
def cfg():
    from omica.config import get_config

    c = get_config()
    c.ensure_dirs()
    return c
