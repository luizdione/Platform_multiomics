"""Smoke do módulo ML.
  - dataset (bcftools) → container omica-bioinfo:
      docker compose run --rm worker-bioinfo pytest -m smoke -q tests/test_scripts_smoke_ml.py
  - classificação (sklearn/xgboost/shap) → container omica-ml:
      docker compose run --rm worker-ml pytest -m smoke -q tests/test_scripts_smoke_ml.py
O classificador roda numa matriz sintética pequena (rápida) — o dado REAL entra
no E2E via workflow wf_ml_aims.

F6 (adendo v2 §A8.2): o ml_diagnostico roda sobre o dataset SELADO do demo (genotipos 515 × 157,
Angus × Brahman). Os valores de aceite são os medidos NO WORKER omica-ml (sklearn 1.5.2,
xgboost 2.1.3; docker/Dockerfile.ml) em 2026-10-29 — DIAGNOSTICO_DEMO abaixo, a tabela do
E2E-ML. Os independentes de versão valem em qualquer máquina com o dado; os que dependem do
motor (contagens de importância) só são exigidos com as versões da imagem. Mais um par
sintético de canário/controle para cada alerta.
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

# ================================================================== ml_diagnostico (F6)
_DATASET_SELADO = (Path("/data/omica/results/ml/demo/dataset"),
                   Path(r"E:\data\omica\results\ml\demo\dataset"))
# Medidos com `docker exec omica-worker-ml python3 modules/ml/scripts/ml_diagnostico.py` sobre o
# dataset selado (2026-10-29). No host (sklearn 1.6.1, xgboost 3.2.0) deram os mesmos números,
# mas o aceite é o do container.
DIAGNOSTICO_DEMO = {
    "n_marcadores": 515, "n_amostras": 157, "k_painel": 50,
    "n_ganho_pos_xgb_completo": 1, "n_ganho_pos_xgb_min_folds": 1, "n_imp_pos_rf": 263,
    "n_separadores_perfeitos": 2, "primeiro_separador": "14:378564",
    "k_informativo": 1, "alerta_empate_zero": 1, "alerta_separador_perfeito": 1,
}
_INDEPENDENTES_DE_VERSAO = ("n_marcadores", "n_amostras", "k_painel", "n_separadores_perfeitos",
                            "primeiro_separador", "alerta_empate_zero", "alerta_separador_perfeito")
_VERSOES_DA_IMAGEM = ("1.5.2", "2.1.3")  # scikit-learn, xgboost (docker/Dockerfile.ml)


def _diagnostico(tmp_path: Path, matriz: Path, rotulos: Path, k: str = "50") -> dict:
    r = _py("ml_diagnostico.py", "--matriz", str(matriz), "--rotulos", str(rotulos),
            "--k-painel", k, "--saida", str(tmp_path))
    assert r.returncode == 0, f"diagnostico falhou:\n{r.stdout}\n{r.stderr[-1500:]}"
    resumo = json.loads((tmp_path / "diagnostico_resumo.json").read_text(encoding="utf-8"))
    resumo.pop("gerado_em")
    # alertas e contagens são int (bool não vira fato do relatório)
    assert all(type(v) is int for k_, v in resumo.items() if k_ != "primeiro_separador"), resumo
    return resumo


@pytest.mark.smoke
def test_smoke_diagnostico_no_demo_selado(tmp_path):
    pytest.importorskip("sklearn")
    pytest.importorskip("xgboost")
    dataset = next((d for d in _DATASET_SELADO if (d / "genotipos.tsv").exists()), None)
    if dataset is None:
        pytest.skip("dataset selado do demo ausente (/data/omica/results/ml/demo)")
    resumo = _diagnostico(tmp_path, dataset / "genotipos.tsv", dataset / "rotulos.tsv")
    assert {k: resumo[k] for k in _INDEPENDENTES_DE_VERSAO} == {
        k: DIAGNOSTICO_DEMO[k] for k in _INDEPENDENTES_DE_VERSAO}
    import sklearn
    import xgboost

    if (sklearn.__version__, xgboost.__version__) == _VERSOES_DA_IMAGEM:
        assert resumo == DIAGNOSTICO_DEMO  # o aceite inteiro, no worker omica-ml


def _sintetico(tmp_path: Path, *, separador: bool):
    """40 × 60 amostras, 120 marcadores 0/1/2: 30 informativos com SOBREPOSIÇÃO entre as classes
    (nenhum separa sozinho) e ruído. Com `separador`, o marcador 14:9999 separa perfeitamente."""
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(7)
    n_a, n_b, n_var = 40, 60, 120
    base = rng.integers(0, 3, size=(n_a + n_b, n_var)).astype(int)
    base[:n_a, :30] = rng.choice([0, 1, 2], size=(n_a, 30), p=[0.7, 0.2, 0.1])
    base[n_a:, :30] = rng.choice([0, 1, 2], size=(n_b, 30), p=[0.1, 0.2, 0.7])
    nomes = [f"14:{1000 + j}" for j in range(n_var)]
    if separador:
        base[:, 60] = [0] * n_a + [2] * n_b
        nomes[60] = "14:9999"
    amostras = [f"A{i}" for i in range(n_a)] + [f"B{i}" for i in range(n_b)]
    matriz = tmp_path / "genotipos.tsv"
    with matriz.open("w", encoding="utf-8") as f:
        f.write("variante\t" + "\t".join(amostras) + "\n")
        for j in range(n_var):
            f.write(f"{nomes[j]}\t" + "\t".join(str(v) for v in base[:, j]) + "\n")
    rotulos = tmp_path / "rotulos.tsv"
    rotulos.write_text("amostra\tgrupo\n" + "".join(
        f"{s}\t{'Angus' if s.startswith('A') else 'Brahman'}\n" for s in amostras), encoding="utf-8")
    return matriz, rotulos


@pytest.mark.smoke
def test_smoke_diagnostico_sintetico_canarios_e_controle(tmp_path):
    """Controle: sinal distribuído, sem separador perfeito e k pequeno → os dois alertas 0 e
    `primeiro_separador` ausente. Canário do separador: um marcador que divide as classes →
    alerta 1 e o nome dele. Canário do empate: k maior que os marcadores com ganho > 0 em algum
    fold → alerta_empate_zero 1 e k_informativo = o mínimo dos folds."""
    pytest.importorskip("sklearn")
    pytest.importorskip("xgboost")
    m, r = _sintetico(tmp_path, separador=False)
    ok = _diagnostico(tmp_path / "ok", m, r, k="5")
    assert ok["alerta_separador_perfeito"] == 0 and ok["n_separadores_perfeitos"] == 0
    assert "primeiro_separador" not in ok
    assert ok["n_ganho_pos_xgb_min_folds"] >= 5 and ok["alerta_empate_zero"] == 0 and ok["k_informativo"] == 5
    assert (ok["n_marcadores"], ok["n_amostras"]) == (120, 100)

    (tmp_path / "sep").mkdir()
    m2, r2 = _sintetico(tmp_path / "sep", separador=True)
    sep = _diagnostico(tmp_path / "sep_out", m2, r2, k="5")
    assert sep["alerta_separador_perfeito"] == 1 and sep["primeiro_separador"] == "14:9999"
    assert sep["n_separadores_perfeitos"] == 1

    empate = _diagnostico(tmp_path / "empate", m, r, k="121")
    assert empate["alerta_empate_zero"] == 1
    assert empate["k_informativo"] == empate["n_ganho_pos_xgb_min_folds"] < 121


@pytest.mark.smoke
def test_smoke_diagnostico_recusa_rotulos_com_3_classes(tmp_path):
    """3 classes binarizariam classes[1] contra o resto, em silêncio: o diagnóstico recusa."""
    pytest.importorskip("sklearn")
    pytest.importorskip("xgboost")
    m, r = _sintetico(tmp_path, separador=False)
    texto = r.read_text(encoding="utf-8").replace("B5\tBrahman", "B5\tGir")
    r.write_text(texto, encoding="utf-8")
    res = _py("ml_diagnostico.py", "--matriz", str(m), "--rotulos", str(r), "--k-painel", "5",
              "--saida", str(tmp_path / "x"))
    assert res.returncode != 0 and "exatamente 2 classes" in res.stderr, res.stderr


# sha256 do dataset selado do wf_ml_aims (E:\data\omica\results\ml\demo\dataset, 2026-10-19)
_SHA_GENOTIPOS_SELADO = "defc2f2884a27cc003070ba132894415e7ea2e04e59838a5be903d2abf7fa2fb"
_SHA_ROTULOS_SELADO = "966d6785e8b6ad128522ff9cf41aafb5444ffe28f9e27c478009dccfe1c4d208"


@pytest.mark.smoke
@precisa_bcftools
def test_smoke_contraste_e_dataset_reproduzem_o_selado(tmp_path):
    """Paridade do pipeline ml_aims_vcf com o selo: as listas que o ml_contraste tira da tabela
    de exemplo (Angus × Brahman), passadas ao MESMO ml_dataset_genotipos com os parâmetros do
    selado, dão o genotipos.tsv e o rotulos.tsv selados byte a byte (medido no omica-bioinfo em
    2026-10-29)."""
    import hashlib

    tabela = REPO_ROOT / "modules/ml/data/rotulos_1000bg_racas.tsv"
    r = _py("ml_contraste.py", "--rotulos", str(tabela), "--classe-a", "Angus", "--classe-b", "Brahman",
            "--minimo", "10", "--saida", str(tmp_path / "contraste"))
    assert r.returncode == 0, r.stderr
    r = _py("ml_dataset_genotipos.py", "--vcf", "/data/1000bg_vcf/Chr14-Run8-TAUIND-public.bcf",
            "--regiao", "14:1-2500000",
            "--grupo-a", f"Angus={tmp_path / 'contraste' / 'grupo_a.txt'}",
            "--grupo-b", f"Brahman={tmp_path / 'contraste' / 'grupo_b.txt'}",
            "--maf", "0.2", "--passo", "15", "--saida", str(tmp_path / "dataset"))
    assert r.returncode == 0, f"dataset falhou:\n{r.stdout}\n{r.stderr[-1200:]}"
    sha = {n: hashlib.sha256((tmp_path / "dataset" / n).read_bytes()).hexdigest()
           for n in ("genotipos.tsv", "rotulos.tsv")}
    assert sha == {"genotipos.tsv": _SHA_GENOTIPOS_SELADO, "rotulos.tsv": _SHA_ROTULOS_SELADO}
