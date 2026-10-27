"""Smoke do módulo ML.
  - dataset (bcftools) → container omica-bioinfo:
      docker compose run --rm worker-bioinfo pytest -m smoke -q tests/test_scripts_smoke_ml.py
  - classificação (sklearn/xgboost/shap) → container omica-ml:
      docker compose run --rm worker-ml pytest -m smoke -q tests/test_scripts_smoke_ml.py
O classificador roda numa matriz sintética pequena (rápida) — o dado REAL entra
no E2E via workflow wf_ml_aims.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from omica.config import REPO_ROOT

SCRIPTS = REPO_ROOT / "modules/ml/scripts"

precisa_bcftools = pytest.mark.skipif(
    shutil.which("bcftools") is None or not Path("/data/1000bg_vcf").exists(),
    reason="bcftools/E:\\data ausentes (container omica-bioinfo)",
)


def _py(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          capture_output=True, text=True, cwd=REPO_ROOT)


@pytest.mark.smoke
@precisa_bcftools
def test_smoke_dataset_genotipos(tmp_path):
    r = _py("ml_dataset_genotipos.py",
            "--vcf", "/data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf",
            "--regiao", "14:400000-900000",
            "--grupo-a", "Angus=/data/1000bg_metadata/samples_angus.txt",
            "--grupo-b", "Brahman=/data/1000bg_metadata/samples_brahman.txt",
            "--maf", "0.2", "--passo", "20", "--saida", str(tmp_path))
    assert r.returncode == 0, f"dataset falhou:\n{r.stdout}\n{r.stderr[-1200:]}"
    resumo = json.loads((tmp_path / "dataset_resumo.json").read_text(encoding="utf-8"))
    # listas vigentes (2026-10-19): 102 Angus e 55 Brahman, ambos no BCF Run 8
    assert resumo["grupo_a"]["n"] >= 100 and resumo["grupo_b"]["n"] >= 50
    # 157 amostras (e não as 497 do par antigo) dão menos variantes com MAF ≥ 0,2: 40 nesta janela
    assert resumo["n_variantes_matriz"] > 20


@pytest.mark.smoke
def test_smoke_classificar_sintetico(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    pytest.importorskip("xgboost")
    pytest.importorskip("shap")
    rng = np.random.default_rng(42)
    n_a, n_b, n_var = 40, 60, 120
    # 10 variantes realmente informativas + ruído
    base = rng.integers(0, 3, size=(n_a + n_b, n_var)).astype(float)
    base[:n_a, :10] = rng.choice([0, 1], size=(n_a, 10), p=[0.9, 0.1])
    base[n_a:, :10] = rng.choice([1, 2], size=(n_b, 10), p=[0.1, 0.9])
    amostras = [f"A{i}" for i in range(n_a)] + [f"B{i}" for i in range(n_b)]
    matriz = tmp_path / "genotipos.tsv"
    with matriz.open("w", encoding="utf-8") as f:
        f.write("variante\t" + "\t".join(amostras) + "\n")
        for j in range(n_var):
            f.write(f"14:{1000 + j}\t" + "\t".join(str(int(v)) for v in base[:, j]) + "\n")
    rotulos = tmp_path / "rotulos.tsv"
    with rotulos.open("w", encoding="utf-8") as f:
        f.write("amostra\tgrupo\n")
        for s in amostras:
            f.write(f"{s}\t{'Angus' if s.startswith('A') else 'Brahman'}\n")

    r = _py("ml_classificar.py", "--matriz", str(matriz), "--rotulos", str(rotulos),
            "--k-painel", "10", "--saida", str(tmp_path / "out"))
    assert r.returncode == 0, f"classificar falhou:\n{r.stdout}\n{r.stderr[-1500:]}"
    resumo = json.loads((tmp_path / "out" / "ml_resumo.json").read_text(encoding="utf-8"))
    ba = resumo["modelos"]["random_forest"]["painel_completo"]["balanced_accuracy"]
    assert ba > 0.9  # sinal plantado tem que ser aprendido
    assert (tmp_path / "out" / "shap_top.png").exists()
    top10 = [l.split("\t")[0] for l in
             (tmp_path / "out" / "shap_top.tsv").read_text(encoding="utf-8").splitlines()[1:11]]
    informativas = {f"14:{1000 + j}" for j in range(10)}
    assert len(informativas & set(top10)) >= 7  # SHAP acha as variantes plantadas