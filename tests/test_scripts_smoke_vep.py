"""Smoke do VEP — roda no container omica-vep:
  docker compose run --rm worker-vep pytest -m smoke -q tests/test_scripts_smoke_vep.py
Exige o cache release-110 ARS-UCD1.2 em /data/omica/refs/vep e a extração demo
já executada (results/genomica/demo/extracao) — o smoke a refaz se preciso? Não:
usa direto o VCF demo versionado (funciona sem passos anteriores).
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from omica.config import REPO_ROOT

SCRIPTS = REPO_ROOT / "modules/genomica/scripts"
DEMO_VCF = REPO_ROOT / "modules/genomica/demo_data/demo_bta14_inicio.vcf.gz"

precisa_vep = pytest.mark.skipif(
    shutil.which("vep") is None or not Path("/data/omica/refs/vep/bos_taurus").exists(),
    reason="vep/cache ausentes (rodar no container omica-vep com E:\\data montado)",
)


@pytest.mark.smoke
@precisa_vep
def test_smoke_vep(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "vep_anotacao.py"),
         "--entrada", str(DEMO_VCF), "--saida", str(tmp_path)],
        capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.returncode == 0, f"vep_anotacao.py falhou:\n{r.stdout}\n{r.stderr[-1500:]}"
    resumo = json.loads((tmp_path / "vep_resumo.json").read_text(encoding="utf-8"))
    assert resumo["n_variantes_anotadas"] > 1000
    assert resumo["consequencia_mais_comum"]
