"""Smoke da Fase 2 (marcador `smoke`): roda os scripts REAIS do módulo genômica
sobre o demo versionado (fatia do 1000 Bull Genomes). Critério de entrada de
script migrado — anti-lição nº 2: só entra código que roda.

Pulados automaticamente onde a ferramenta não existe (host Windows / CI sem
bcftools); no container omica-bioinfo rodam completos (apontar SÓ este arquivo —
a imagem bioinfo não tem fastapi, então a coleta de tests/test_web.py falharia):
  docker compose run --rm worker-bioinfo pytest -m smoke -q tests/test_scripts_smoke.py
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from omica.config import REPO_ROOT

DEMO_VCF = REPO_ROOT / "modules/genomica/demo_data/demo_bta14_inicio.vcf.gz"
DEMO_BED = REPO_ROOT / "modules/genomica/demo_data/painel_demo.bed"
SCRIPTS = REPO_ROOT / "modules/genomica/scripts"

precisa_bcftools = pytest.mark.skipif(
    shutil.which("bcftools") is None, reason="bcftools ausente (rodar no container omica-bioinfo)"
)
precisa_snpeff = pytest.mark.skipif(
    not Path("/opt/snpEff/snpEff.jar").exists()
    or not Path("/data/snpeff_data/ARS-UCD1.3.113").exists(),
    reason="SnpEff/banco ausentes (rodar no container omica-bioinfo com E:\\data montado)",
)


def _rodar(script: str, *args: str) -> None:
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert r.returncode == 0, f"{script} falhou:\n{r.stdout}\n{r.stderr}"


@pytest.mark.smoke
@precisa_bcftools
def test_smoke_extracao_e_frequencias(tmp_path):
    saida_ext = tmp_path / "extracao"
    _rodar("extracao_regioes.py", "--entrada", str(DEMO_VCF),
           "--bed", str(DEMO_BED), "--saida", str(saida_ext))
    resumo = json.loads((saida_ext / "extracao_resumo.json").read_text(encoding="utf-8"))
    assert resumo["n_regioes"] == 3
    assert resumo["n_variantes_extraidas"] > 1000  # janelas densas do BTA14
    assert (saida_ext / "extraido.vcf.gz").exists()

    saida_freq = tmp_path / "frequencias"
    _rodar("frequencias.py", "--entrada", str(saida_ext / "extraido.vcf.gz"),
           "--saida", str(saida_freq))
    resumo_f = json.loads((saida_freq / "frequencias_resumo.json").read_text(encoding="utf-8"))
    assert resumo_f["n_variantes_com_af"] > 1000
    assert 0.0 < resumo_f["maf_media"] < 0.5
    cabecalho = (saida_freq / "frequencias.tsv").open(encoding="utf-8").readline()
    assert cabecalho.strip() == "CHROM\tPOS\tID\tREF\tALT\tAF\tMAF"


@pytest.mark.smoke
@pytest.mark.skipif(not Path("/data/snpeff_data/ARS-UCD1.3.113/genes.gtf.gz").exists(),
                    reason="GTF local ausente (rodar no container bioinfo com E:\\data)")
def test_smoke_coordenadas_painel(tmp_path):
    _rodar("coordenadas_painel.py",
           "--painel", "modules/genomica/data/paineis/termotolerancia.txt",
           "--saida", str(tmp_path))
    resumo = json.loads((tmp_path / "termotolerancia_resumo.json").read_text(encoding="utf-8"))
    assert resumo["n_simbolos"] == 14
    assert resumo["n_localizados"] >= 12  # nomes de símbolo podem variar no GTF
    bed = (tmp_path / "termotolerancia.bed").read_text(encoding="utf-8").splitlines()
    assert len(bed) == resumo["n_localizados"]
    assert "DGAT1" in {l.split("\t")[3] for l in bed}


@pytest.mark.smoke
@precisa_bcftools
@pytest.mark.skipif(not Path("/data/1000bg_vcf").exists(),
                    reason="E:\\data\\1000bg_vcf ausente")
def test_smoke_integridade_vcf(tmp_path):
    _rodar("integridade_vcf.py", "--dir", "/data/1000bg_vcf", "--saida", str(tmp_path))
    resumo = json.loads((tmp_path / "integridade_resumo.json").read_text(encoding="utf-8"))
    assert resumo["n_arquivos"] > 0
    assert resumo["n_ok"] + resumo["n_problemas"] == resumo["n_arquivos"]


@pytest.mark.smoke
@precisa_bcftools
@pytest.mark.skipif(shutil.which("plink2") is None
                    or not Path("/data/1000bg_metadata/samples_angus.txt").exists(),
                    reason="plink2 ou listas de raça ausentes (container bioinfo + E:\\data)")
def test_smoke_fst_racas(tmp_path):
    _rodar("fst_racas.py",
           "--vcf", "/data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf",
           "--regiao", "14:400000-700000",
           "--grupo-a", "Angus=/data/1000bg_metadata/samples_angus.txt",
           "--grupo-b", "Nelore=/data/1000bg_metadata/samples_nelore.txt",
           "--saida", str(tmp_path))
    resumo = json.loads((tmp_path / "fst_resumo.json").read_text(encoding="utf-8"))
    assert resumo["grupo_a"]["n_amostras"] >= 2 and resumo["grupo_b"]["n_amostras"] >= 2
    assert resumo["n_variantes"] > 100
    assert resumo["fst_global"] is not None and 0.0 <= resumo["fst_global"] <= 1.0


@pytest.mark.smoke
@precisa_bcftools
@precisa_snpeff
def test_smoke_anotacao_snpeff(tmp_path):
    saida_ext = tmp_path / "extracao"
    _rodar("extracao_regioes.py", "--entrada", str(DEMO_VCF),
           "--bed", str(DEMO_BED), "--saida", str(saida_ext))
    saida_ann = tmp_path / "anotacao"
    _rodar("anotacao_snpeff.py", "--entrada", str(saida_ext / "extraido.vcf.gz"),
           "--saida", str(saida_ann))
    resumo = json.loads((saida_ann / "anotacao_resumo.json").read_text(encoding="utf-8"))
    assert resumo["n_variantes_anotadas"] > 1000
    assert resumo["banco"] == "ARS-UCD1.3.113"
    assert resumo["efeito_mais_comum"]
