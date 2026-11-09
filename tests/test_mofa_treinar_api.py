"""mofa_treinar_api.py (F6, decisão 9 da ESPEC; adendo v2 §A9.5 e crítica M8).

No host não há mofapy2: o TREINO (função `treinar`, cópia sem mudança de lógica do selado) é
trocado por um Z/R² conhecido e todo o resto roda de verdade — o mapeamento coluna → condição
pelo coldata, a separação y0/y1, as saídas e o resumo. O treino real e a paridade com o selado
(variancia_explicada.tsv 581ba9e7…, separação 1,248) ficam no smoke do worker methyl
(tests/test_scripts_smoke_epi.py), inclusive o canário "CD4 renomeado".

Aqui:
  - sincronia TEXTUAL com o selado: mofa_treinar.py:59-112 (o bloco do treino, que contém as
    linhas 64-71 das opções do entry_point e 96-105 do R²) aparece contíguo, linha a linha;
  - sincronia da regra y0/y1 com o montar_coverages.py;
  - "CD4 renomeado" no host: montar → mofa_preparar (selado, stdlib) → mofa_treinar_api. O
    grupos.tsv do selado sai 6 × HF (o defeito documentado); o resumo sai 3 Holandes / 3 Zebu;
  - n_rotulo_igual_prefixo (verificador da F6): o número sai do NOME da coluna; um rótulo
    trocado que preserva n_y0/n_y1 o baixa (canário), o mapeamento certo dá n_amostras;
  - canários de entrada: fatores < 2, coluna sem par, amostra ausente, views desalinhadas, uma
    condição só e separação não finita — código ≠ 0.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from omica.config import REPO_ROOT
from omica.contrato import formatos
from test_montar_coverages import carregar, rodar as rodar_montar

np = pytest.importorskip("numpy")
pytest.importorskip("matplotlib")

SCRIPTS = REPO_ROOT / "modules" / "epigenomica" / "scripts"
SCRIPT = SCRIPTS / "mofa_treinar_api.py"
SELADO = SCRIPTS / "mofa_treinar.py"
DEMO = REPO_ROOT / "modules" / "epigenomica" / "demo_data"
CD4 = REPO_ROOT / "modules" / "epigenomica" / "data" / "amostras_cd4_demo.tsv"
PAINEL = REPO_ROOT / "modules" / "epigenomica" / "data" / "paineis" / "painel_108.json"
CHAVES_RESUMO = {"gerado_em", "motor", "n_fatores", "n_amostras", "genes_por_view", "variancia_explicada_pct",
                 "condicao_y0", "condicao_y1", "n_y0", "n_y1", "n_rotulo_igual_prefixo", "separacao_por_fator",
                 "separacao_fator1"}
# S1–S3 = HF (317, 318, 319) e S4–S6 = Nelore (323, 324, 325), adendo §A11
RENOMEADOS = {"S1": "FILTERED_H82_HF_ERS4410317_CD4", "S2": "FILTERED_H82_HF_ERS4410318_CD4",
              "S3": "FILTERED_H82_HF_ERS4410319_CD4", "S4": "FILTERED_H82_Nelore_ERS4410323_CD4",
              "S5": "FILTERED_H82_Nelore_ERS4410324_CD4", "S6": "FILTERED_H82_Nelore_ERS4410325_CD4"}
CONDICAO_RENOMEADA = {"S1": "Holandes", "S2": "Holandes", "S3": "Holandes", "S4": "Zebu", "S5": "Zebu", "S6": "Zebu"}


@pytest.fixture(scope="module")
def mapi():
    return carregar(SCRIPT)


# ============================================================ sincronia
def test_sincronia_textual_com_o_selado():
    """ESPEC §5.1: as três linhas ep.set_* (64-71) e a fórmula do R² (96-105) — e, de quebra, o
    bloco inteiro do treino (59-112: build, captura do SystemExit, expectativas, sanidade) —
    estão no script novo exatamente como no selado, contíguos e na mesma ordem."""
    selado = SELADO.read_text(encoding="utf-8").splitlines()
    novo = SCRIPT.read_text(encoding="utf-8").splitlines()
    assert selado[63].strip().startswith("ep.set_data_options") and selado[70].strip().startswith("ep.set_train_options")
    assert "R²" not in selado[95] and selado[95].strip().startswith("Y = matrizes[vi]")

    def contiguo(bloco: list[str]) -> bool:
        return any(novo[i:i + len(bloco)] == bloco for i in range(len(novo) - len(bloco) + 1))

    for ini, fim in ((64, 71), (96, 105), (59, 112)):
        assert contiguo(selado[ini - 1:fim]), f"mofa_treinar.py:{ini}-{fim} não está igual no mofa_treinar_api.py"
    assert selado[57] in novo  # matrizes = [Yp.T.copy(), Yc.T.copy()] (fica no main)


@pytest.mark.parametrize("condicoes", [["HF", "Nelore"], ["Zebu", "Holandes"], ["b", "B", "a"], ["Á", "A", "z"],
                                       ["10", "9"], ["N", "HF"]])
def test_sincronia_y0_y1_com_o_montar(mapi, condicoes):
    montar = carregar()
    assert mapi.condicoes_extremas(condicoes) == montar.condicoes_extremas(condicoes) == (min(condicoes),
                                                                                          max(condicoes))


# ============================================================ ambiente: treino trocado
def _treino_fixo(Z: "np.ndarray", r2: dict):
    def treinar(matrizes, amostras, feats_p, feats_c, args):
        assert len(amostras) == Z.shape[0] == matrizes[0].shape[0] == matrizes[1].shape[0]
        return Z[:, :args.fatores], {k: v[:args.fatores] for k, v in r2.items()}
    return treinar


def _rodar(mapi, monkeypatch, promotor: Path, corpo: Path, amostras: Path, saida: Path, fatores: str = "3",
           Z=None, r2=None) -> None:
    if Z is not None:
        monkeypatch.setattr(mapi, "treinar", _treino_fixo(Z, r2))
    monkeypatch.setattr(sys, "argv", ["mofa_treinar_api.py", "--promotor", str(promotor), "--corpo", str(corpo),
                                      "--amostras", str(amostras), "--fatores", fatores, "--saida", str(saida)])
    mapi.main()


def _views(tmp_path: Path, colunas: list[str], n_genes: int = 6) -> tuple[Path, Path]:
    rng = np.random.default_rng(7)
    saida = []
    for nome in ("view_promotor.tsv", "view_corpo.tsv"):
        arq = tmp_path / nome
        linhas = ["feature\t" + "\t".join(colunas)]
        for g in range(n_genes):
            linhas.append(f"G{g}\t" + "\t".join(f"{v:.3f}" for v in rng.uniform(0, 100, len(colunas))))
        arq.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        saida.append(arq)
    return saida[0], saida[1]


def _coldata(tmp_path: Path, pares: list[tuple[str, str]]) -> Path:
    arq = tmp_path / "amostras.tsv"
    arq.write_text("amostra\tcondicao\n" + "".join(f"{a}\t{c}\n" for a, c in pares), encoding="utf-8")
    return arq


PARES = [("s1", "Zebu"), ("s2", "Zebu"), ("s3", "Holandes"), ("s4", "Holandes"), ("s5", "Zebu")]
Z5 = np.array([[1.0, 0.5, 0.0], [1.2, 0.4, 0.1], [-1.0, 0.0, 0.2], [-0.8, 0.1, 0.0], [0.9, 0.6, -0.1]])
R2 = {"promotor": [7.5, 1.25, 0.0], "corpo": [3.0, 0.5, 0.0]}


def test_controle_saidas_e_resumo(mapi, tmp_path, monkeypatch):
    promotor, corpo = _views(tmp_path, [f"{c}__{a}" for a, c in PARES])
    saida = tmp_path / "s"
    _rodar(mapi, monkeypatch, promotor, corpo, _coldata(tmp_path, PARES), saida, Z=Z5, r2=R2)
    resumo = json.loads((saida / "mofa_resumo.json").read_text(encoding="utf-8"))
    assert set(resumo) == CHAVES_RESUMO
    # y0 = menor em ordem alfabética (Holandes, 2), y1 = maior (Zebu, 3)
    assert (resumo["condicao_y0"], resumo["n_y0"], resumo["condicao_y1"], resumo["n_y1"]) == ("Holandes", 2, "Zebu", 3)
    zebu, hol = Z5[[0, 1, 4]], Z5[[2, 3]]
    esperado = [round(abs(float(zebu[:, k].mean() - hol[:, k].mean())), 3) for k in range(3)]
    assert resumo["separacao_por_fator"] == esperado and resumo["separacao_fator1"] == esperado[0]
    assert (resumo["n_fatores"], resumo["n_amostras"], resumo["genes_por_view"]) == (3, 5, {"promotor": 6, "corpo": 6})
    assert resumo["n_rotulo_igual_prefixo"] == 5      # toda coluna: condição do coldata = prefixo do nome
    assert resumo["variancia_explicada_pct"] == R2
    linhas = (saida / "fatores_por_amostra.tsv").read_text(encoding="utf-8").splitlines()
    assert linhas[0] == "amostra\tcondicao\tFactor1\tFactor2\tFactor3"
    assert linhas[1] == "s1\tZebu\t1.0000\t0.5000\t0.0000"    # a amostra do coldata, sem o prefixo
    assert (saida / "variancia_explicada.tsv").read_bytes() == (
        b"fator\tpromotor\tcorpo\nFactor1\t7.5\t3.0\nFactor2\t1.25\t0.5\nFactor3\t0.0\t0.0\n")
    assert formatos.validar("tabela_variancia_mofa", saida / "variancia_explicada.tsv").ok
    v = formatos.validar("fatores_mofa", saida / "fatores_por_amostra.tsv")
    assert v.ok, v.falhas
    assert (saida / "mofa_fatores.png").stat().st_size > 5000
    assert not list(saida.glob("*.hdf5"))


@pytest.mark.parametrize("mexer, trecho", [
    (lambda col, pares: (col, pares, "1"), "são precisos ≥ 2"),
    (lambda col, pares: (col[:-1] + ["Zebu__s9"], pares, "3"), "sem amostra no coldata"),
    (lambda col, pares: (col[:-1], pares, "3"), "ausente(s) das views"),
    (lambda col, pares: (col[:-1] + ["Holandes__s5"], pares, "3"), "sem amostra no coldata"),
    (lambda col, pares: ([f"Zebu__{a}" for a, _ in pares], [(a, "Zebu") for a, _ in pares], "3"),
     "uma condição só"),
], ids=["fatores_1", "coluna_sem_par", "amostra_ausente", "condicao_trocada_na_coluna", "uma_condicao"])
def test_canarios_de_entrada(mapi, tmp_path, monkeypatch, mexer, trecho):
    colunas, pares, fatores = mexer([f"{c}__{a}" for a, c in PARES], PARES)
    promotor, corpo = _views(tmp_path, colunas)
    saida = tmp_path / "s"
    with pytest.raises(SystemExit) as e:
        _rodar(mapi, monkeypatch, promotor, corpo, _coldata(tmp_path, pares), saida, fatores, Z=Z5, r2=R2)
    assert trecho in str(e.value.code), e.value.code
    assert not (saida / "mofa_resumo.json").exists() and not (saida / "variancia_explicada.tsv").exists()


@pytest.mark.parametrize("colunas, condicoes, n", [
    (["A__x", "B__y", "A__z__w"], ["A", "A", "A"], 2),   # parte no PRIMEIRO '__' (a amostra pode ter '__')
    (["Zebu__s1", "Holandes__s3"], ["Zebu", "Holandes"], 2),
    (["Zebu__s1", "Holandes__s3"], ["Holandes", "Zebu"], 0),
    (["s1", "Zebu__s2"], ["Zebu", "Zebu"], 1),          # sem '__' o prefixo é o nome todo (o montar não grava assim)
])
def test_n_rotulo_igual_prefixo_le_so_o_nome(mapi, colunas, condicoes, n):
    assert mapi.n_rotulo_igual_prefixo(colunas, condicoes) == n


def test_n_rotulo_igual_prefixo_recusa_rotulos_a_menos(mapi):
    with pytest.raises(SystemExit) as e:
        mapi.n_rotulo_igual_prefixo(["A__x", "B__y"], ["A"])
    assert "1 rótulo(s) para 2 coluna(s)" in str(e.value.code)


def test_canario_rotulo_trocado_que_preserva_as_contagens(mapi, tmp_path, monkeypatch):
    """O defeito que n_y0/n_y1 não pegam (verificador da F6, M8): s1 (Zebu) e s3 (Holandes)
    trocam de rótulo — as contagens continuam 2 Holandes / 3 Zebu, mas duas colunas passam a ter
    condição ≠ do prefixo do nome. Controle: test_controle_saidas_e_resumo (5 de 5)."""
    colunas = [f"{c}__{a}" for a, c in PARES]
    promotor, corpo = _views(tmp_path, colunas)
    certo = mapi.condicoes_das_colunas

    def trocado(cols, pares):
        r = certo(cols, pares)
        r[0], r[2] = (r[0][0], r[2][1]), (r[2][0], r[0][1])
        return r
    monkeypatch.setattr(mapi, "condicoes_das_colunas", trocado)
    saida = tmp_path / "s"
    _rodar(mapi, monkeypatch, promotor, corpo, _coldata(tmp_path, PARES), saida, Z=Z5, r2=R2)
    resumo = json.loads((saida / "mofa_resumo.json").read_text(encoding="utf-8"))
    assert (resumo["condicao_y0"], resumo["n_y0"], resumo["condicao_y1"], resumo["n_y1"]) == ("Holandes", 2, "Zebu", 3)
    assert resumo["n_rotulo_igual_prefixo"] == 3


def test_canario_views_desalinhadas(mapi, tmp_path, monkeypatch):
    colunas = [f"{c}__{a}" for a, c in PARES]
    promotor, _ = _views(tmp_path, colunas)
    corpo = tmp_path / "corpo.tsv"
    corpo.write_text("feature\t" + "\t".join(reversed(colunas)) + "\nG0\t" + "\t".join(["1"] * 5) + "\n",
                     encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        _rodar(mapi, monkeypatch, promotor, corpo, _coldata(tmp_path, PARES), tmp_path / "s", Z=Z5, r2=R2)
    assert "mesmas amostras, na mesma ordem" in str(e.value.code)


def test_canario_separacao_nao_finita(mapi, tmp_path, monkeypatch):
    promotor, corpo = _views(tmp_path, [f"{c}__{a}" for a, c in PARES])
    Z = Z5.copy()
    Z[2, 0] = np.nan
    with pytest.raises(SystemExit) as e:
        _rodar(mapi, monkeypatch, promotor, corpo, _coldata(tmp_path, PARES), tmp_path / "s", Z=Z, r2=R2)
    assert "separação não finita no Factor1" in str(e.value.code)
    assert not (tmp_path / "s" / "mofa_resumo.json").exists()


# ============================================================ CD4 renomeado (host)
def cd4_renomeado(base: Path) -> tuple[Path, Path]:
    """Os 6 coverages do demo como S1..S6.bismark.cov.gz e o coldata Holandes/Zebu (adendo
    §A11). Nenhum nome tem 'Nelore' nem 'HF': o grupos.tsv do mofa_preparar selado sai 6 × HF."""
    pasta = base / "cd4_renomeado"
    pasta.mkdir()
    for s, origem in RENOMEADOS.items():
        shutil.copyfile(DEMO / f"{origem}.bismark.cov.gz", pasta / f"{s}.bismark.cov.gz")
    coldata = base / "amostras_renomeado.tsv"
    coldata.write_text("amostra\tcondicao\n" + "".join(f"{s}\t{c}\n" for s, c in CONDICAO_RENOMEADA.items()),
                       encoding="utf-8")
    return pasta, coldata


def montar_e_views(base: Path, pasta: Path, coldata: Path) -> Path:
    """montar → mofa_preparar (o selado, como o passo `views` o roda). Devolve a pasta das views."""
    r = rodar_montar(pasta, coldata, base / "montar")
    assert r.returncode == 0, r.stderr
    import subprocess
    import os
    views = base / "views"
    r = subprocess.run([sys.executable, str(SCRIPTS / "mofa_preparar.py"), "--dir", str(base / "montar" / "coverages"),
                        "--painel", str(PAINEL), "--promotor", "2000", "--min-amostras", "4", "--saida", str(views)],
                       capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT,
                       env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.returncode == 0, r.stderr
    return views


def test_cd4_renomeado_rotulos_do_coldata_e_nao_do_grupos_tsv(mapi, tmp_path, monkeypatch):
    pasta, coldata = cd4_renomeado(tmp_path)
    views = montar_e_views(tmp_path, pasta, coldata)
    # o defeito documentado do selado: sem 'Nelore' no nome, tudo vira HF
    grupos = (views / "grupos.tsv").read_text(encoding="utf-8").splitlines()[1:]
    assert [g.split("\t")[1] for g in grupos] == ["HF"] * 6
    cab = (views / "view_promotor.tsv").read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert cab[1:] == [f"{c}__{s}" for s, c in CONDICAO_RENOMEADA.items()]
    Z = np.array([[1.0, 0.0, 0.0]] * 3 + [[-1.0, 0.0, 0.0]] * 3)
    saida = tmp_path / "mofa"
    _rodar(mapi, monkeypatch, views / "view_promotor.tsv", views / "view_corpo.tsv", coldata, saida,
           Z=Z, r2={"promotor": [1.0, 0.0, 0.0], "corpo": [1.0, 0.0, 0.0]})
    resumo = json.loads((saida / "mofa_resumo.json").read_text(encoding="utf-8"))
    assert (resumo["condicao_y0"], resumo["n_y0"], resumo["condicao_y1"], resumo["n_y1"]) == ("Holandes", 3, "Zebu", 3)
    assert resumo["n_rotulo_igual_prefixo"] == 6
    assert resumo["separacao_fator1"] == 2.0
    montar = json.loads((tmp_path / "montar" / "montar_resumo.json").read_text(encoding="utf-8"))
    assert (montar["condicao_y0"], montar["n_y0"], montar["condicao_y1"], montar["n_y1"]) == ("Holandes", 3, "Zebu", 3)
    fatores = (saida / "fatores_por_amostra.tsv").read_text(encoding="utf-8").splitlines()[1:]
    assert [tuple(x.split("\t")[:2]) for x in fatores] == list(CONDICAO_RENOMEADA.items())
