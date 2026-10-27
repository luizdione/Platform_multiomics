"""conftest — isola os testes do ambiente real.

OMICA_DATA aponta para um diretório temporário e OMICA_REDIS_URL fica vazio
(força o fallback de thread), para que a suíte rode igual no host Windows, no
container e no CI ubuntu — sem tocar em E:\\data.

A RAM também é isolada: sem isso a suíte lia a memória REAL da máquina e quatro
testes de fila quebravam quando havia outro job pesado rodando (auditoria de
2026-09-14: 4 failed com a máquina a 83%, 0 failed com ela ociosa) — o CI verde
só significava "o runner estava vazio". Testes que exercitam a guarda continuam
monkeypatchando psutil dentro do próprio teste, e passam a valer de verdade.

Pelo mesmo motivo, nenhuma OMICA_* do shell chega aos testes (revisão F2, achado #38): os
limites de runs eram lidos do ambiente e mudavam o veredito da suíte.
"""
from __future__ import annotations

import os

import pytest

from omica import config as config_mod
from omica.config import reset_config_cache
from omica.jobs import guarda_ram


class _MemoriaFake:
    def __init__(self, percent: float) -> None:
        self.percent = percent


# Opções do PRÓPRIO harness de teste (ex.: OMICA_SMOKE_REDE=1 liga o smoke com rede): o
# autor exporta de propósito para a rodada, e elas não mudam a plataforma.
_OPCOES_DO_HARNESS = ("OMICA_SMOKE_",)


@pytest.fixture(autouse=True)
def ambiente_isolado(tmp_path, monkeypatch):
    # Toda OMICA_* exportada no shell sai antes de cada teste; as que a suíte precisa são
    # postas logo abaixo. Revisão F2 (achado #38): runs.py lê OMICA_MAX_RUNS_ATIVOS e
    # OMICA_MAX_RUNS_ABERTOS direto do ambiente, e com OMICA_MAX_RUNS_ATIVOS=2 no shell o
    # test_um_run_ativo_por_vez reprovava (200 em vez de 409) — e um canário de limite
    # passaria a aprovar. Varrer o prefixo, e não listar nomes, cobre a próxima variável
    # lida direto do ambiente sem depender de alguém lembrar de acrescentá-la aqui.
    for nome in [n for n in os.environ if n.upper().startswith("OMICA_")]:
        if not nome.upper().startswith(_OPCOES_DO_HARNESS):
            monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv("OMICA_DATA", str(tmp_path / "dados"))
    monkeypatch.setenv("GENOME_DATA", str(tmp_path))
    monkeypatch.setenv("OMICA_REDIS_URL", "")
    monkeypatch.setenv("OMICA_MAX_RAM_GB", "24")
    monkeypatch.setenv("OMICA_RAM_MAX_PCT", "80")
    # sem isto, na máquina do autor a suíte leria o plugin Bioinfo de verdade
    monkeypatch.setenv("BIOINFO_PLUGIN_DIR", str(tmp_path / "sem_plugin"))
    monkeypatch.delenv("OMICA_IN_DOCKER", raising=False)
    monkeypatch.setattr(guarda_ram.psutil, "virtual_memory", lambda: _MemoriaFake(12.0))
    # O .env real nunca entra nos testes. Ele guarda o OMICA_API_TOKEN, e `_obter` faz
    # `env or .env or default`: um OMICA_API_TOKEN="" no ambiente NÃO força "vazio" —
    # cairia no .env e a suíte passaria a depender (e a poder imprimir) do segredo real.
    monkeypatch.setattr(config_mod, "_ler_dotenv", lambda caminho: {})
    monkeypatch.delenv("OMICA_API_TOKEN", raising=False)  # já saiu na varredura; explícito de propósito
    # interruptor da GPU fictício: os testes nunca leem o switch.json real do autor
    monkeypatch.setenv("OMICA_GPU_SWITCH", str(tmp_path / "gpu_switch" / "switch.json"))
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture()
def cfg():
    from omica.config import get_config

    c = get_config()
    c.ensure_dirs()
    return c
