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
    ("inteiro", "--3", False), ("decimal", "0.05", True), ("decimal", "1e-5", True),
    ("decimal", "-1.5", True), ("decimal", "abc", False), ("decimal", "1 2", False),
    # S3 (revisão F1): o argparse lê '-1e-5' como flag ("expected one argument") — o erro
    # aparecia no meio do job em vez de na validação. Dígito Unicode e '\n' final passavam.
    ("decimal", "-1e-5", False), ("inteiro", "٥", False), ("inteiro", "12\n", False),
])
def test_tipos_numericos(tipo, valor, ok):
    p = ParamSpec(name="n", rotulo="N", tipo=tipo)
    if ok:
        assert _validar_valor(p, valor) == valor
    else:
        with pytest.raises(ValueError):
            _validar_valor(p, valor)


# ---------------------------------------------- revisão adversarial da F1 (S1, S2)
@pytest.mark.parametrize("tipo,valor", [
    # S1: a convenção Nome=/caminho validava só o lado direito do '='
    ("texto", "https://atacante.example/c=/data/x"),   # read.delim do R baixava a URL
    ("texto", "http://omica-redis:6379/k=/data/x"),    # SSRF para a rede interna
    ("texto", "/etc/passwd=/data/x"),
    ("texto", "x/../y=/data/z"),
    ("caminho", "X=/data/x"),                          # o script abriria 'X=/data/x', não /data/x
    ("caminho", "https://h/x=/data/y"),
    ("dir", "ftp://h/x=/data/y"),
    ("grupo", "a/b=/data/x"),                          # nome de grupo precisa ser simples
    ("grupo", "Angus"),                                # grupo sem '='
    # S2: valor sem '/' escapava da checagem de raiz
    ("texto", "Angus=.env"),                           # fst_racas.py abria o .env do token
    ("grupo", "Angus=.env"),
    ("caminho", "pyproject.toml"),
    ("caminho", ".env"),
])
def test_canarios_da_revisao_f1(tipo, valor):
    with pytest.raises(ValueError):
        _validar_valor(ParamSpec(name="x", rotulo="X", tipo=tipo), valor)


@pytest.mark.parametrize("tipo,valor", [
    ("texto", "Angus=/data/1000bg_metadata/samples_angus.txt"),
    ("grupo", "Angus=/data/1000bg_metadata/samples_angus.txt"),
    ("grupo", "Holstein-Friesian=/data/1000bg_metadata/samples_holstein.txt"),
    ("caminho", "modules/transcriptomica/demo_data/counts_granulosa.tsv"),
    ("dir", "modules/epigenomica/demo_data"),
    ("texto", "Nelore"),
    ("texto", "14:1-2500000"),
])
def test_controles_da_revisao_f1(tipo, valor):
    assert _validar_valor(ParamSpec(name="x", rotulo="X", tipo=tipo), valor) == valor


def test_todo_parametro_dos_manifests_declara_tipo():
    """S2: o `tipo` existia mas nenhum manifest o usava — a camada de tipos era código
    morto e caminho sem '/' escapava da checagem de raiz. Lido do YAML cru: o default
    'texto' do modelo esconderia a omissão."""
    import yaml

    from omica.registry import MODULES_DIR

    sem_tipo, mal_tipado = [], []
    for manifesto in sorted(MODULES_DIR.glob("*/manifest.yaml")):
        dados = yaml.safe_load(manifesto.read_text(encoding="utf-8"))
        for secao in ("scripts", "scripts_api"):
            for s in dados.get(secao) or []:
                for p in s.get("params") or []:
                    nome = f"{manifesto.parent.name}/{s['key']}.{p['name']}"
                    if "tipo" not in p:
                        sem_tipo.append(nome)
                    elif "/" in str(p.get("default", "")) and p["tipo"] not in ("caminho", "dir", "grupo"):
                        mal_tipado.append(f"{nome}={p['tipo']}")
    assert sem_tipo == [], f"params sem tipo declarado: {sem_tipo}"
    assert mal_tipado == [], f"default com caminho mas tipo não-caminho: {mal_tipado}"


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


# ------------------------------------------- {saida} dos scripts_api (F2, runs do n8n)
def _deseq2_api() -> ScriptSpec:
    m = load_registry().por_id("transcriptomica")
    return next(s for s in m.scripts_api if s.key == "deseq2")


def _valores_run(cfg) -> dict[str, str]:
    raiz = cfg.data_dir.as_posix()
    return {"counts": f"{raiz}/raw/runs/r1/counts.tsv", "coldata": f"{raiz}/raw/runs/r1/coldata.tsv",
            "referencia": "winter_control"}


def test_saida_de_run_valida_monta_o_cmd(cfg):
    saida = f"{cfg.data_dir.as_posix()}/results/transcriptomica/runs/transcriptomica_de-20261019-000000-abc123/de"
    cmd = montar_cmd(_deseq2_api(), _valores_run(cfg), saida=saida)
    assert cmd.endswith(f"--saida {saida}")


@pytest.mark.parametrize("sufixo", [
    None,                                              # scripts_api sem pasta de run
    "/results/transcriptomica/demo/de",                # o demo SELADO
    "/raw/runs/r1",                                    # pasta de entradas
    "/results/transcriptomica/runs/r1",                # falta o passo
    "/results/transcriptomica/runs/r1/de/../../demo",  # '..'
    "/results/transcriptomica/runs/r 1/de",            # espaço
    "/work/runs/r1/de",                                # pasta de controle do run
])
def test_saida_fora_de_results_runs_e_recusada(cfg, sufixo):
    saida = None if sufixo is None else cfg.data_dir.as_posix() + sufixo
    with pytest.raises(ValueError):
        montar_cmd(_deseq2_api(), _valores_run(cfg), saida=saida)


def test_saida_fora_da_pasta_de_dados_e_recusada(cfg):
    with pytest.raises(ValueError):
        montar_cmd(_deseq2_api(), _valores_run(cfg), saida="/data/results/transcriptomica/runs/r1/de")