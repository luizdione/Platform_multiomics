"""Smoke da Fase 3 (epigenômica) — roda no container omica-methyl:
  docker compose run --rm worker-methyl pytest -m smoke -q tests/test_scripts_smoke_epi.py
Arquivo separado do smoke genômico: cada worker só coleta o que consegue importar/rodar.

F6 (adendo v2 §A11): os scripts novos dos pipelines epigenomica_dmc/epigenomica_mofa no
Python do worker (3.10) — montar_coverages e painel_janelas; o DMC, a interseção e o TSS lendo
a pasta do `montar` (a paridade com o selado: dmc.tsv, todas_cpgs.tsv e intersecao.tsv com os
mesmos sha256, e as 13 invariantes do relatório sobre as saídas reais, com o motifs do selado
porque o HOMER leva ~25 min); e o mofa_treinar_api com o mofapy2 de verdade, no demo e no
canário "CD4 renomeado" (S1–S6, Holandes × Zebu): variancia_explicada.tsv com o sha do selado,
separação 1,248, n_rotulo_igual_prefixo 6 e as 11 invariantes do epigenomica_mofa sobre os
resumos do smoke.
"""
import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from omica.config import REPO_ROOT

DEMO_DIR = REPO_ROOT / "modules/epigenomica/demo_data"
SCRIPTS = REPO_ROOT / "modules/epigenomica/scripts"
PAINEL_BED = REPO_ROOT / "modules/epigenomica/data/paineis/painel_108.bed"
PAINEL_JSON = REPO_ROOT / "modules/epigenomica/data/paineis/painel_108.json"
CD4 = REPO_ROOT / "modules/epigenomica/data/amostras_cd4_demo.tsv"
# o demo selado: /data no container, E: no host (skip quando ausente)
SELADOS = [Path("/data/omica/results/epigenomica/demo"), Path(r"E:\data\omica\results\epigenomica\demo")]
SHA = {
    "janelas.bed": "170e413f7643fbf18d754068677d3db6ac95fba397d583e5162a8b575d95e757",
    "dmc.tsv": "6e356ab9730f7f3e287aabc3ac8ce2bce05484112bc3571ffaf3efb970336730",
    "todas_cpgs.tsv": "ff00282e2574d20fd8e14c7cf1b9efb7beb1ff7fc12e27ca1be483be68879d53",
    "intersecao.tsv": "1e3760823555a5874fae4b344d19cf16160431023832b24af25e12553bb746e0",
    "variancia_explicada.tsv": "581ba9e7548244a99fa72ffddbab6d4ac8c0967ce625fd63e872c337167ea5fc",
}

precisa_r = pytest.mark.skipif(
    shutil.which("Rscript") is None or not Path("/rlib/methylKit").exists(),
    reason="Rscript/methylKit ausentes (rodar no container omica-methyl)",
)
precisa_mofa = pytest.mark.skipif(importlib.util.find_spec("mofapy2") is None,
                                  reason="mofapy2 ausente (rodar no container omica-methyl)")


def _py(script: str, *args: str) -> None:
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                       capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.returncode == 0, f"{script} falhou:\n{r.stdout}\n{r.stderr}"


@pytest.mark.smoke
@precisa_r
def test_smoke_dmc_e_intersecao(tmp_path):
    saida_dmc = tmp_path / "dmc"
    r = subprocess.run(
        ["Rscript", str(SCRIPTS / "metilacao_dmc.R"),
         "--dir", str(DEMO_DIR), "--grupo-a", "Nelore", "--grupo-b", "HF",
         "--min-cov", "5", "--diff", "15", "--qvalue", "0.05",
         "--saida", str(saida_dmc)],
        capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.returncode == 0, f"metilacao_dmc.R falhou:\n{r.stdout}\n{r.stderr}"
    resumo = json.loads((saida_dmc / "dmc_resumo.json").read_text(encoding="utf-8"))
    assert resumo["grupo_a"]["n"] == 3 and resumo["grupo_b"]["n"] == 3
    assert resumo["n_cpg_testados"] > 100
    # `n_dmc >= 0` era tautológico (passava com o script quebrado). O demo do
    # Zenodo produz DMCs de verdade — se não produzir, algo quebrou.
    assert resumo["n_dmc"] > 0, "nenhuma DMC no demo: pipeline quebrado, não biologia"
    assert resumo["n_hiper_no_grupo_a"] + resumo["n_hipo_no_grupo_a"] == resumo["n_dmc"]
    # `startswith("BH")` NÃO servia: a string do bug era "BH (methylKit/SLIM)",
    # que também começa com BH — a assertiva passaria com o código errado.
    correcao = resumo["criterios"]["correcao"]
    assert "Benjamini" in correcao and "SLIM" not in correcao, (
        f"o resumo tem de declarar a correção que o R realmente rodou: {correcao!r}")
    assert (saida_dmc / "todas_cpgs.tsv").exists()  # insumo do HOMER

    saida_int = tmp_path / "intersecao"
    _py("intersecao_dmc_painel.py", "--dmc", str(saida_dmc / "dmc.tsv"),
        "--painel", str(PAINEL_BED), "--saida", str(saida_int))
    resumo_i = json.loads((saida_int / "intersecao_resumo.json").read_text(encoding="utf-8"))
    assert resumo_i["n_genes_painel"] == 108
    assert resumo_i["n_dmc_total"] == resumo["n_dmc"]
    # O bug publicava 320 "no painel" de 314 no total. Esta é a checagem que
    # teria pego, e ela vale para qualquer dado:
    assert resumo_i["n_dmc_no_painel"] <= resumo_i["n_dmc_total"], (
        "mais DMCs dentro do painel do que no total — dupla contagem por janela")
    assert resumo_i["n_atribuicoes_gene"] >= resumo_i["n_dmc_no_painel"]
    assert resumo_i["n_genes_com_dmc"] <= resumo_i["n_genes_painel"]


@pytest.mark.smoke
def test_smoke_perfil_tss(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("matplotlib")
    _py("perfil_tss.py", "--dir", str(DEMO_DIR), "--painel", str(PAINEL_JSON),
        "--grupo-a", "Nelore", "--grupo-b", "HF",
        "--janela", "2000", "--bin", "100", "--saida", str(tmp_path))
    resumo = json.loads((tmp_path / "tss_resumo.json").read_text(encoding="utf-8"))
    assert resumo["n_tss"] == 108  # vem do painel
    # …e ESTES vêm do dado: a versão anterior do smoke passava com o metaperfil
    # VAZIO, porque só conferia a contagem do painel e o tamanho do PNG.
    assert resumo["grupo_a"]["cpgs_agregados"] > 0
    assert resumo["grupo_b"]["cpgs_agregados"] > 0
    assert resumo["n_tss_fita_negativa"] > 0, (
        "o painel tem genes na fita −; se a contagem for 0, a orientação se perdeu")
    assert (tmp_path / "tss_perfil.png").stat().st_size > 5000


# ============================================================ F6: pipelines da epigenômica
def _rodar(script: str, *args: str) -> None:
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True,
                       encoding="utf-8", cwd=REPO_ROOT,
                       env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.returncode == 0, f"{script} falhou:\n{r.stdout}\n{r.stderr}"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _selado() -> Path:
    for p in SELADOS:
        if (p / "mofa" / "views" / "view_promotor.tsv").is_file():
            return p
    pytest.skip("demo selado da epigenômica ausente")


def _resumo(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _dados(p: Path) -> list[str]:
    """As linhas de dado (sem o cabeçalho, que no run ganha o prefixo <condicao>__)."""
    return p.read_text(encoding="utf-8").splitlines()[1:]


@pytest.mark.smoke
def test_smoke_montar_e_janelas_no_python_do_worker(tmp_path):
    from omica.contrato import formatos

    _rodar("montar_coverages.py", "--coverages", str(DEMO_DIR), "--amostras", str(CD4), "--saida", str(tmp_path / "m"))
    r = _resumo(tmp_path / "m" / "montar_resumo.json")
    assert (r["n_amostras"], r["por_condicao"], r["bytes"]) == (6, {"HF": 3, "Nelore": 3}, 439_723)
    v = formatos.validar("coverages_por_condicao", tmp_path / "m" / "coverages")
    assert v.ok, v.falhas
    _rodar("painel_janelas.py", "--painel", str(PAINEL_JSON), "--pad", "15000", "--saida", str(tmp_path / "j"))
    assert _sha(tmp_path / "j" / "janelas.bed") == SHA["janelas.bed"]


@pytest.mark.smoke
@precisa_r
def test_smoke_dmc_tss_intersecao_sobre_a_pasta_do_montar(tmp_path):
    """E2E-EPI-A sem o HOMER: os selados lendo <condicao>__<amostra>.bismark.cov.gz dão as
    mesmas tabelas do demo selado; o relatório fecha as 13 invariantes sobre as saídas reais."""
    pytest.importorskip("numpy")
    pytest.importorskip("matplotlib")
    sel = _selado()
    from test_relatorio_epigenomica import RUN_DMC, ficha_de, montar_run
    from omica.relatorio.portao_numeros import conferir_invariantes

    cov = tmp_path / "montar" / "coverages"
    _rodar("montar_coverages.py", "--coverages", str(DEMO_DIR), "--amostras", str(CD4), "--saida",
           str(tmp_path / "montar"))
    _rodar("painel_janelas.py", "--painel", str(PAINEL_JSON), "--pad", "15000", "--saida", str(tmp_path / "janelas"))
    r = subprocess.run(["Rscript", str(SCRIPTS / "metilacao_dmc.R"), "--dir", str(cov), "--grupo-a", "Nelore",
                        "--grupo-b", "HF", "--min-cov", "5", "--diff", "15", "--qvalue", "0.05",
                        "--saida", str(tmp_path / "dmc")], capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.returncode == 0, f"metilacao_dmc.R falhou:\n{r.stdout}\n{r.stderr}"
    assert _sha(tmp_path / "dmc" / "dmc.tsv") == SHA["dmc.tsv"]
    assert _sha(tmp_path / "dmc" / "todas_cpgs.tsv") == SHA["todas_cpgs.tsv"]
    d = _resumo(tmp_path / "dmc" / "dmc_resumo.json")
    assert (d["n_cpg_testados"], d["n_dmc"], d["n_hiper_no_grupo_a"], d["n_hipo_no_grupo_a"]) == (3714, 275, 185, 90)
    _rodar("intersecao_dmc_painel.py", "--dmc", str(tmp_path / "dmc" / "dmc.tsv"), "--painel",
           str(tmp_path / "janelas" / "janelas.bed"), "--saida", str(tmp_path / "intersecao"))
    assert _sha(tmp_path / "intersecao" / "intersecao.tsv") == SHA["intersecao.tsv"]
    _rodar("perfil_tss.py", "--dir", str(cov), "--painel", str(PAINEL_JSON), "--grupo-a", "Nelore", "--grupo-b", "HF",
           "--janela", "2000", "--bin", "100", "--saida", str(tmp_path / "tss"))
    t = _resumo(tmp_path / "tss" / "tss_resumo.json")
    assert (t["n_tss"], t["n_tss_fita_negativa"], t["grupo_a"]["cpgs_agregados"], t["grupo_b"]["cpgs_agregados"]) == (
        108, 45, 10711, 10643)
    (tmp_path / "motifs").mkdir()
    for n in ("motifs_top.tsv", "motifs_resumo.json"):
        shutil.copyfile(sel / "motifs" / n, tmp_path / "motifs" / n)
    b = tmp_path
    portas = {
        "montar": {"montar_resumo": b / "montar" / "montar_resumo.json"},
        "janelas": {"bed": b / "janelas" / "janelas.bed", "janelas_resumo": b / "janelas" / "janelas_resumo.json"},
        "dmc": {"dmc": b / "dmc" / "dmc.tsv", "todas_cpgs": b / "dmc" / "todas_cpgs.tsv",
                "dmc_resumo": b / "dmc" / "dmc_resumo.json"},
        "intersecao": {"intersecao": b / "intersecao" / "intersecao.tsv",
                       "intersecao_resumo": b / "intersecao" / "intersecao_resumo.json"},
        "tss": {"tss_png": b / "tss" / "tss_perfil.png", "tss_resumo": b / "tss" / "tss_resumo.json"},
        "motifs": {"motifs_top": b / "motifs" / "motifs_top.tsv", "motifs_resumo": b / "motifs" / "motifs_resumo.json"},
    }
    pasta, _, plano = montar_run(tmp_path / "relatorio", "epigenomica_dmc", RUN_DMC, portas,
                                 {"grupo_a": "Nelore", "grupo_b": "HF"})
    ficha = ficha_de(pasta, plano)
    assert conferir_invariantes(ficha) == []
    assert not [f for f in ficha["fatos"] if isinstance(f["valor"], str) and f["valor"].endswith((".cov", ".cov.gz"))]


@pytest.mark.smoke
@precisa_mofa
@pytest.mark.parametrize("caso", ["demo", "cd4_renomeado"])
def test_smoke_mofa_treinar_api(tmp_path, caso):
    """E2E-EPI-B no worker (demo, HF × Nelore) e o canário "CD4 renomeado" (adendo §A11): os
    mesmos coverages com outros nomes e condições Holandes × Zebu. As views e a variância saem
    iguais às do selado; os rótulos vêm do coldata (o grupos.tsv do selado diz 6 × HF no
    renomeado); toda coluna tem a condição do prefixo do nome (n_rotulo_igual_prefixo 6) e as 11
    invariantes do epigenomica_mofa fecham sobre os resumos do smoke."""
    pytest.importorskip("matplotlib")
    sel = _selado()
    from test_mofa_treinar_api import cd4_renomeado
    from test_relatorio_epigenomica import ficha_de, ficha_mofa, saidas_mofa
    from omica.relatorio.portao_numeros import conferir_invariantes

    if caso == "demo":
        pasta, coldata, y = DEMO_DIR, CD4, ("HF", "Nelore")
    else:
        pasta, coldata = cd4_renomeado(tmp_path)
        y = ("Holandes", "Zebu")
    _rodar("montar_coverages.py", "--coverages", str(pasta), "--amostras", str(coldata), "--saida",
           str(tmp_path / "montar"))
    _rodar("mofa_preparar.py", "--dir", str(tmp_path / "montar" / "coverages"), "--painel", str(PAINEL_JSON),
           "--promotor", "2000", "--min-amostras", "4", "--saida", str(tmp_path / "views"))
    for v in ("view_promotor.tsv", "view_corpo.tsv"):
        assert _dados(tmp_path / "views" / v) == _dados(sel / "mofa" / "views" / v), v
    grupos = [x.split("\t")[1] for x in _dados(tmp_path / "views" / "grupos.tsv")]
    if caso == "cd4_renomeado":
        assert grupos == ["HF"] * 6   # o defeito documentado do selado: religar o treino a isto reprova
    _rodar("mofa_treinar_api.py", "--promotor", str(tmp_path / "views" / "view_promotor.tsv"), "--corpo",
           str(tmp_path / "views" / "view_corpo.tsv"), "--amostras", str(coldata), "--fatores", "3",
           "--saida", str(tmp_path / "mofa"))
    assert _sha(tmp_path / "mofa" / "variancia_explicada.tsv") == SHA["variancia_explicada.tsv"]
    r = _resumo(tmp_path / "mofa" / "mofa_resumo.json")
    assert (r["condicao_y0"], r["n_y0"], r["condicao_y1"], r["n_y1"]) == (y[0], 3, y[1], 3)
    assert r["n_rotulo_igual_prefixo"] == 6
    assert r["separacao_fator1"] == 1.248
    assert r["variancia_explicada_pct"] == {"promotor": [0.08, 8.35, 0.0], "corpo": [7.3, 0.14, 0.0]}
    # Factor1..3 iguais aos do selado, na mesma ordem das amostras
    assert [x.split("\t")[2:] for x in _dados(tmp_path / "mofa" / "fatores_por_amostra.tsv")] == [
        x.split("\t")[2:] for x in _dados(sel / "mofa" / "modelo" / "fatores_por_amostra.tsv")]
    m = _resumo(tmp_path / "montar" / "montar_resumo.json")
    assert (m["condicao_y0"], m["n_y0"], m["condicao_y1"], m["n_y1"]) == (y[0], 3, y[1], 3)
    run, _, plano = ficha_mofa(tmp_path / "relatorio", saidas_mofa(tmp_path / "montar", tmp_path / "views",
                                                                    tmp_path / "mofa"))
    assert conferir_invariantes(ficha_de(run, plano)) == []
