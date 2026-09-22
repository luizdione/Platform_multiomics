"""Smoke da Fase 3 (epigenômica) — roda no container omica-methyl:
  docker compose run --rm worker-methyl pytest -m smoke -q tests/test_scripts_smoke_epi.py
Arquivo separado do smoke genômico: cada worker só coleta o que consegue importar/rodar.
"""
import json
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

precisa_r = pytest.mark.skipif(
    shutil.which("Rscript") is None or not Path("/rlib/methylKit").exists(),
    reason="Rscript/methylKit ausentes (rodar no container omica-methyl)",
)


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
