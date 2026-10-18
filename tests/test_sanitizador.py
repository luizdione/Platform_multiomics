"""Sanitizador de parâmetros (omica/jobs/dispatch.py) — F1 da integração com o n8n.

O padrão antigo tinha dois furos de injeção de argumentos: aceitava espaço
(`x --outra-flag` virava duas entradas do argv) e não barrava '-' inicial (`--help`,
sem espaço nenhum, já é flag para o argparse). Com um LLM gerando parâmetros, isso
deixa de ser tolerável. Canários abaixo; controles negativos também.
"""
from __future__ import annotations

import pytest

from omica.jobs.dispatch import _validar_valor, montar_cmd
from omica.registry import ParamSpec, ScriptSpec, load_registry

P = ParamSpec(name="x", rotulo="X")


@pytest.mark.parametrize("valor", [
    "x --outra-flag",            # espaço: 2 argumentos
    "--help",                    # flag sem espaço nenhum
    "--construir-indice",        # flag real do salmon_quant.py
    "-rf",
    "a\\b",                      # o shlex POSIX come a barra invertida
    "C:\\Windows",
    "/etc/passwd",               # formato ok, raiz proibida
    "../x",
    "modules/x/../../etc",
    "Angus=/etc/shadow",         # convenção Nome=caminho com caminho proibido
    "=/data/x",                  # grupo sem nome
    "x;rm",                      # metacaractere de shell
    "$(id)",
    "a`b`",
    "",
])
def test_canarios_recusados(valor):
    with pytest.raises(ValueError):
        _validar_valor(P, valor)


@pytest.mark.parametrize("valor", [
    "modules/transcriptomica/demo_data/counts_granulosa.tsv",
    "modules/genomica/data/paineis/termotolerancia.txt",
    "/data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf",
    "Angus=/data/1000bg_metadata/samples_angus.txt",
    "14:1-2500000",
    "0.05",
    "15000",
    "Nelore",
])
def test_controles_aceitos(valor):
    assert _validar_valor(P, valor) == valor


@pytest.mark.parametrize("tipo,valor,ok", [
    ("inteiro", "15000", True), ("inteiro", "-3", True), ("inteiro", "1.5", False),
    ("inteiro", "--3", False), ("decimal", "0.05", True), ("decimal", "-1e-5", True),
    ("decimal", "abc", False), ("decimal", "1 2", False),
])
def test_tipos_numericos(tipo, valor, ok):
    p = ParamSpec(name="n", rotulo="N", tipo=tipo)
    if ok:
        assert _validar_valor(p, valor) == valor
    else:
        with pytest.raises(ValueError):
            _validar_valor(p, valor)


def test_barra_invertida_ganha_dica_do_caminho_do_container():
    with pytest.raises(ValueError, match="E:\\\\data é montado em /data"):
        _validar_valor(P, "E:\\data\\1000bg_vcf\\x.bcf")


def test_todos_os_defaults_dos_manifests_continuam_montando():
    """Regressão obrigatória: a regex nova não pode quebrar nenhum default real. A 1ª
    versão desenhada no plano (sem '/' inicial) recusava 3 deles — os caminhos /data/..."""
    falhas = []
    for m in load_registry().modules:
        for s in m.scripts:
            try:
                montar_cmd(s)
            except ValueError as e:
                falhas.append(f"{m.id}/{s.key}: {e}")
    assert falhas == []


def test_invariante_de_estrutura_do_comando():
    """2ª trava: mesmo que um valor passasse na regex, o nº de argumentos não pode mudar."""
    spec = ScriptSpec(key="k", titulo="t", runtime="image:omica-web",
                      cmd="python3 a.py --entrada {x}", params=[P])
    assert montar_cmd(spec, {"x": "modules/m/data/a.tsv"}).endswith("modules/m/data/a.tsv")
