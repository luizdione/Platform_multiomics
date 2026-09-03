"""Smoke da Fase 4 (transcriptômica). Dois grupos com skips independentes:
  - DESeq2 + volcano → container omica-methyl:
      docker compose run --rm worker-methyl pytest -m smoke -q tests/test_scripts_smoke_rna.py
  - FastQC/MultiQC (+ Salmon, se o FASTQ demo existir) → container omica-bioinfo:
      docker compose run --rm worker-bioinfo pytest -m smoke -q tests/test_scripts_smoke_rna.py
O download SRA (rede) NÃO roda por padrão: exige OMICA_SMOKE_REDE=1.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from omica.config import REPO_ROOT

SCRIPTS = REPO_ROOT / "modules/transcriptomica/scripts"
DEMO = REPO_ROOT / "modules/transcriptomica/demo_data"

precisa_r = pytest.mark.skipif(
    shutil.which("Rscript") is None or not Path("/rlib/DESeq2").exists(),
    reason="Rscript/DESeq2 ausentes (rodar no container omica-methyl)",
)
precisa_fastqc = pytest.mark.skipif(
    shutil.which("fastqc") is None or shutil.which("multiqc") is None,
    reason="fastqc/multiqc ausentes (rodar no container omica-bioinfo)",
)


def _py(script: str, *args: str) -> None:
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                       capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.returncode == 0, f"{script} falhou:\n{r.stdout}\n{r.stderr[-1500:]}"


@pytest.mark.smoke
@precisa_r
def test_smoke_deseq2_e_volcano(tmp_path):
    saida_de = tmp_path / "de"
    r = subprocess.run(
        ["Rscript", str(SCRIPTS / "expressao_deseq2.R"),
         "--counts", str(DEMO / "counts_granulosa.tsv"),
         "--coldata", str(DEMO / "coldata_granulosa.tsv"),
         "--referencia", "winter_control", "--saida", str(saida_de)],
        capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.returncode == 0, f"expressao_deseq2.R falhou:\n{r.stdout}\n{r.stderr[-1500:]}"
    resumo = json.loads((saida_de / "de_resumo.json").read_text(encoding="utf-8"))
    assert resumo["n_amostras"] == 8
    assert resumo["n_genes_testados"] > 10000
    assert resumo["n_deg_padj"] > 0  # contraste real de calor tem sinal

    pytest.importorskip("matplotlib")
    saida_v = tmp_path / "volcano"
    _py("volcano_plot.py", "--de", str(saida_de / "de_table.tsv"), "--saida", str(saida_v))
    resumo_v = json.loads((saida_v / "volcano_resumo.json").read_text(encoding="utf-8"))
    # o volcano plota só genes com padj definido — a filtragem independente do
    # DESeq2 deixa alguns como NA, então n_pontos <= n_genes_testados
    assert 10000 < resumo_v["n_pontos"] <= resumo["n_genes_testados"]
    assert resumo_v["n_significativos"] > 0
    assert (saida_v / "volcano.png").stat().st_size > 10000


@pytest.mark.smoke
@precisa_fastqc
def test_smoke_qc_fastq(tmp_path):
    if not list(DEMO.glob("*.fastq.gz")):
        pytest.skip("FASTQ demo ainda não versionado (gerar com baixar_sra --spots 25000)")
    _py("qc_fastq.py", "--dir", str(DEMO), "--saida", str(tmp_path))
    resumo = json.loads((tmp_path / "qc_resumo.json").read_text(encoding="utf-8"))
    assert resumo["n_fastq"] >= 1
    assert (tmp_path / "multiqc_report.html").exists()


@pytest.mark.smoke
@precisa_fastqc
@pytest.mark.skipif(os.environ.get("OMICA_SMOKE_REDE") != "1",
                    reason="download SRA exige rede — ative com OMICA_SMOKE_REDE=1")
def test_smoke_baixar_sra(tmp_path):
    _py("baixar_sra.py", "--srr", "SRR24953465", "--spots", "2000", "--saida", str(tmp_path))
    assert list(tmp_path.glob("SRR24953465*.fastq.gz"))
