"""ml_contraste.py e o scripts_api do ML (F6: ESPEC §3.1.1-§3.1.2 + adendo v2 §A7/§A8.1).

O script roda como o worker o roda (subprocess, só stdlib) no host. Controle real: a tabela de
exemplo versionada (modules/ml/data/rotulos_1000bg_racas.tsv) com Angus × Brahman → 102 e 55, e
as listas iguais às do rotulos.tsv selado do demo (skip sem o E:\\data). Canários (código de
saída ≠ 0, conferidos pelo MOTIVO): classes iguais, classe ausente da tabela, classe abaixo do
mínimo e tabela estruturalmente fora do formato.

Sincronia da regra y0/y1 com o ml_classificar.py selado (86-87): as invariantes 12-15 do
ml_aims_vcf conferem a orientação da confusão contra o n_y0/n_y1 deste script, mas com
n_y0 == n_y1 não veem nada. Quem garante a orientação aí é a sincronia TEXTUAL das duas regras
(controle e canários sobre cópias dos textos) e as linhas do selado executadas como estão.

O scripts_api: manifest carregado sem erro, nada de selo novo, os 5 selos vigentes, nenhum
parâmetro reservado, o argv do demo (via contexto.demo()) igual ao da ESPEC, e a `regiao` do
dataset com escopo de subprojeto (crítica M3: fora do demo, sem sobrescrita, recusa).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fixtures_v2 import escrever_subprojeto
from omica import contexto, selos
from omica.config import REPO_ROOT
from omica.contrato import formatos
from omica.jobs import dispatch
from omica.registry import MODULES_DIR, hash_dos_passos, load_registry

SCRIPT = REPO_ROOT / "modules" / "ml" / "scripts" / "ml_contraste.py"
CLASSIFICAR = REPO_ROOT / "modules" / "ml" / "scripts" / "ml_classificar.py"
# ml_classificar.py:86-87 (selado, wf_ml_aims): y = 1 para sorted(classes)[1]
REGRA_Y_CLASSIFICAR = ["    classes = sorted(rotulos.unique())",
                       "    y = (rotulos == classes[1]).astype(int).values  # 1 = classe alfabeticamente maior"]
# ml_contraste.py: y0 = min(A, B), y1 = max(A, B), e o resumo grava os dois e as contagens deles
REGRA_Y_CONTRASTE = ["    a, b = args.classe_a, args.classe_b",
                     "    y0, y1 = min(a, b), max(a, b)",
                     '        "classe_y0": y0, "classe_y1": y1, "n_y0": n[y0], "n_y1": n[y1],']
TABELA =REPO_ROOT / "modules" / "ml" / "data" / "rotulos_1000bg_racas.tsv"
ROTULOS_SELADO = Path(r"E:\data\omica\results\ml\demo\dataset\rotulos.tsv")
CHAVES_API = ["ml_contraste", "ml_dataset", "ml_diagnostico", "ml_classificar", "ml_robustez"]
SELOS = {"wf_painel_demo": "46124beee606ccc6", "wf_mofa_demo": "89df0a07439b5f96",
         "wf_metilacao_demo": "ef8e5355bc92acc7", "wf_de_granulosa": "ef51927233d11997",
         "wf_ml_aims": "9563a9c0e7dbc8de"}


def _contraste(saida: Path, a: str, b: str, *, rotulos: Path = TABELA, minimo: str = "10"
               ) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--rotulos", str(rotulos), "--classe-a", a,
                           "--classe-b", b, "--minimo", minimo, "--saida", str(saida)],
                          capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT)


def _resumo(saida: Path) -> dict:
    d = json.loads((saida / "contraste_resumo.json").read_text(encoding="utf-8"))
    d.pop("gerado_em")
    return d


def _tabela(tmp_path: Path, grupos: dict[str, int], *, nome: str = "r.tsv", extra: str = "",
            cab: str = "amostra\tgrupo") -> Path:
    linhas, k = [cab], 0
    for g, n in grupos.items():
        for _ in range(n):
            k += 1
            linhas.append(f"S{k:04d}\t{g}")
    p = tmp_path / nome
    p.write_text("\n".join(linhas) + "\n" + extra, encoding="utf-8", newline="")
    return p


# ================================================================== o script
def test_angus_brahman_na_tabela_de_exemplo(tmp_path):
    r = _contraste(tmp_path, "Angus", "Brahman")
    assert r.returncode == 0, r.stderr
    assert _resumo(tmp_path) == {
        "classe_a": "Angus", "classe_b": "Brahman", "n_a": 102, "n_b": 55,
        "classe_y0": "Angus", "classe_y1": "Brahman", "n_y0": 102, "n_y1": 55,
        "n_niveis_tabela": 16, "minimo": 10}
    for nome, n in (("grupo_a.txt", 102), ("grupo_b.txt", 55)):
        bruto = (tmp_path / nome).read_bytes()
        ids = bruto.decode("utf-8").split("\n")[:-1]
        assert b"\r" not in bruto and bruto.endswith(b"\n") and ids == sorted(ids) and len(ids) == n
        # a porta passa no validador do formato que o manifest declara
        v = formatos.validar("lista_amostras", tmp_path / nome)
        assert v.ok and v.perfil["n_ids"] == n, v.falhas
    assert "RESULTADO:" in r.stdout


def test_listas_iguais_as_do_rotulos_selado(tmp_path):
    """As 102 Angus e as 55 Brahman da tabela de exemplo são as MESMAS amostras do demo selado
    (wf_ml_aims): o E2E-ML refaz o selado a partir da tabela, não de outras listas."""
    if not ROTULOS_SELADO.exists():
        pytest.skip("rotulos.tsv selado ausente (dado real fora do repositório)")
    assert _contraste(tmp_path, "Angus", "Brahman").returncode == 0
    selado: dict[str, set[str]] = {}
    for linha in ROTULOS_SELADO.read_text(encoding="utf-8").splitlines()[1:]:
        amostra, grupo = linha.split("\t")
        selado.setdefault(grupo, set()).add(amostra)
    assert set((tmp_path / "grupo_a.txt").read_text(encoding="utf-8").split()) == selado["Angus"]
    assert set((tmp_path / "grupo_b.txt").read_text(encoding="utf-8").split()) == selado["Brahman"]


def test_ordem_das_classes_e_classe_y(tmp_path):
    """A classe A é a do --classe-a (vem primeiro na matriz); y0/y1 seguem a ordem alfabética,
    a regra do ml_classificar (ml_classificar.py:86-87), qualquer que seja a ordem de A e B."""
    assert _contraste(tmp_path, "Brahman", "Angus").returncode == 0
    r = _resumo(tmp_path)
    assert (r["classe_a"], r["n_a"], r["classe_b"], r["n_b"]) == ("Brahman", 55, "Angus", 102)
    assert (r["classe_y0"], r["n_y0"], r["classe_y1"], r["n_y1"]) == ("Angus", 102, "Brahman", 55)
    assert len((tmp_path / "grupo_a.txt").read_text(encoding="utf-8").split()) == 55


# ===================================================== sincronia y0/y1 com o classificador
def falhas_regra_y(contraste: str, classificar: str) -> list[str]:
    """Sincronia TEXTUAL da orientação y0/y1, o ponto cego das invariantes 12-15 do ml_aims_vcf
    (com n_y0 == n_y1 a troca não muda as somas das linhas da confusão). O ml_contraste.py grava
    y0 = min(A, B) e y1 = max(A, B); o ml_classificar.py selado (86-87) faz y = 1 para
    sorted(classes)[1], e as classes dele são os dois grupos do rotulos.tsv, que o dataset grava
    com os nomes A e B. Para duas classes diferentes, max == sorted(...)[1]: a mesma orientação,
    desde que nenhum dos dois volte a atribuir y/classes ou y0/y1 depois."""
    falhas = []
    cl = classificar.splitlines()
    if cl[85:87] != REGRA_Y_CLASSIFICAR:
        falhas.append("ml_classificar.py:86-87 não é mais a regra citada (classes = sorted(...); "
                      "y = 1 para classes[1])")
    for nome in ("classes", "y"):
        n = sum(1 for l in cl if re.match(rf"\s*{nome}\b(\[[^\]]*\])?\s*([-+*/%&|^]|//|\*\*|<<|>>)?=(?!=)", l))
        if n != 1:
            falhas.append(f"ml_classificar.py atribui `{nome}` {n} vez(es) (a regra é a única atribuição)")
    ct = contraste.splitlines()
    for linha in REGRA_Y_CONTRASTE:
        if ct.count(linha) != 1:
            falhas.append(f"ml_contraste.py sem a linha única {linha.strip()!r}")
    n = sum(1 for l in ct if re.match(r"\s*(y0|y1)\b[^=\n]*=(?!=)", l))
    if n != 1:
        falhas.append(f"ml_contraste.py atribui y0/y1 {n} vez(es) (a regra é a única atribuição)")
    return falhas


def _textos_regra_y() -> dict[str, str]:
    return {"contraste": SCRIPT.read_text(encoding="utf-8"), "classificar": CLASSIFICAR.read_text(encoding="utf-8")}


def test_sincronia_textual_regra_y_com_o_classificar():
    """Controle: os dois scripts como estão."""
    t = _textos_regra_y()
    assert falhas_regra_y(t["contraste"], t["classificar"]) == []


@pytest.mark.parametrize("onde, de, para, trecho", [
    # o contraste inverte a regra, troca as contagens no resumo ou reatribui y0/y1 depois
    ("contraste", "y0, y1 = min(a, b), max(a, b)", "y0, y1 = max(a, b), min(a, b)",
     "sem a linha única 'y0, y1 = min(a, b), max(a, b)'"),
    ("contraste", '"n_y0": n[y0], "n_y1": n[y1]', '"n_y0": n[y1], "n_y1": n[y0]',
     "sem a linha única '\"classe_y0\": y0"),
    ("contraste", "    y0, y1 = min(a, b), max(a, b)\n",
     "    y0, y1 = min(a, b), max(a, b)\n    y0, y1 = y1, y0\n", "atribui y0/y1 2 vez(es)"),
    # o classificador muda a classe positiva, desloca as linhas citadas ou inverte o y depois
    ("classificar", "(rotulos == classes[1])", "(rotulos == classes[0])", "ml_classificar.py:86-87"),
    ("classificar", "    classes = sorted(rotulos.unique())\n",
     "    # linha nova\n    classes = sorted(rotulos.unique())\n", "ml_classificar.py:86-87"),
    ("classificar", "    X = SimpleImputer", "    y = 1 - y\n    X = SimpleImputer", "atribui `y` 2 vez(es)"),
])
def test_sincronia_regra_y_reprova(onde, de, para, trecho):
    """Canários sobre CÓPIAS dos textos (os scripts não são tocados): cada divergência reprova
    pelo motivo dela, e só por ele."""
    t = _textos_regra_y()
    assert t[onde].count(de) == 1, de
    t[onde] = t[onde].replace(de, para)
    falhas = falhas_regra_y(t["contraste"], t["classificar"])
    assert len(falhas) == 1 and trecho in falhas[0], falhas


def _y_do_classificar(tmp_path: Path, a: str, b: str, n_a: int, n_b: int,
                      troca: tuple[str, str] | None = None) -> dict:
    """Roda o contraste A × B e executa as linhas 84 e 86-87 do ml_classificar.py selado (com
    `troca` aplicada numa CÓPIA do texto, para o canário) sobre o rotulos.tsv que o dataset
    gravaria (A e depois B, ml_dataset_genotipos.py:96-102). Devolve as classes, as contagens
    de y = 0 / y = 1, os grupos com y = 1 e o resumo do contraste."""
    pd = pytest.importorskip("pandas")
    saida = tmp_path / "c"
    r = _contraste(saida, a, b, rotulos=_tabela(tmp_path, {a: n_a, b: n_b}))
    assert r.returncode == 0, r.stderr
    rot = tmp_path / "rotulos.tsv"
    rot.write_text("amostra\tgrupo\n" + "".join(
        f"{s}\t{g}\n" for g, arq in ((a, "grupo_a.txt"), (b, "grupo_b.txt"))
        for s in (saida / arq).read_text(encoding="utf-8").split()), encoding="utf-8", newline="\n")
    linhas = CLASSIFICAR.read_text(encoding="utf-8").splitlines()
    assert linhas[83].strip().startswith("rotulos = pd.read_csv(rotulos_path") and linhas[85:87] == REGRA_Y_CLASSIFICAR
    codigo = textwrap.dedent("\n".join([linhas[83]] + linhas[85:87]))
    if troca:
        assert codigo.count(troca[0]) == 1, troca
        codigo = codigo.replace(*troca)
    ns = {"pd": pd, "rotulos_path": rot}
    exec(codigo, ns)  # noqa: S102 (as linhas do selado, como estão)
    y = ns["y"]
    return {"classes": [str(c) for c in ns["classes"]], "contagens": (int((y == 0).sum()), int((y == 1).sum())),
            "grupos_y1": sorted(set(ns["rotulos"][y == 1])), "resumo": _resumo(saida)}


def falhas_orientacao(r: dict) -> list[str]:
    """O y do classificador contra o contraste: classes, o GRUPO do y = 1 (vê a troca mesmo com
    classes do mesmo tamanho) e as contagens (o que as invariantes 12-15 veem)."""
    s, falhas = r["resumo"], []
    if r["classes"] != [s["classe_y0"], s["classe_y1"]]:
        falhas.append(f"classes {r['classes']} ≠ [classe_y0, classe_y1] do contraste")
    if r["grupos_y1"] != [s["classe_y1"]]:
        falhas.append(f"grupo do y = 1 {r['grupos_y1']} ≠ classe_y1 do contraste ({s['classe_y1']})")
    if r["contagens"] != (s["n_y0"], s["n_y1"]):
        falhas.append(f"contagens de y {r['contagens']} ≠ (n_y0, n_y1) do contraste")
    return falhas


@pytest.mark.parametrize("a, b, n_a, n_b", [
    ("Zebu", "Angus", 12, 10),     # A depois de B no alfabeto
    ("b", "B", 10, 11),            # ordem de código: maiúscula antes de minúscula
    ("N_Dama", "Nelore", 10, 13),  # '_' antes de 'e'
    ("A.1", "A_1", 11, 10),        # '.' antes de '_'
    ("Brahman", "Angus", 10, 10),  # mesmo tamanho: o ponto cego das invariantes 12-15
])
def test_regra_y_do_selado_executada_da_o_mesmo_que_o_contraste(tmp_path, a, b, n_a, n_b):
    """Controle: as linhas do selado dão a mesma orientação (classes e o grupo do y = 1) e as
    mesmas contagens que o contraste grava em classe_y0/y1 e n_y0/n_y1."""
    assert falhas_orientacao(_y_do_classificar(tmp_path, a, b, n_a, n_b)) == []


@pytest.mark.parametrize("n_a, n_b, trechos", [
    (12, 10, ["grupo do y = 1 ['Angus'] ≠ classe_y1", "contagens de y (12, 10) ≠"]),
    (10, 10, ["grupo do y = 1 ['Angus'] ≠ classe_y1"]),  # contagens iguais: o ponto cego
])
def test_canario_y_invertido_e_o_ponto_cego_das_contagens(tmp_path, n_a, n_b, trechos):
    """Canário (cópia do texto do selado com y = 1 para classes[0]): o grupo do y = 1 reprova nos
    dois casos; as contagens só reprovam com n_y0 ≠ n_y1 — com tamanhos iguais coincidem, que é
    o ponto cego das invariantes 12-15 do ml_aims_vcf."""
    falhas = falhas_orientacao(_y_do_classificar(tmp_path, "Zebu", "Angus", n_a, n_b,
                                                 troca=("classes[1]", "classes[0]")))
    assert len(falhas) == len(trechos) and all(t in f for t, f in zip(trechos, falhas)), falhas


def test_le_a_tabela_como_o_validador(tmp_path):
    """Colunas pelo NOME (amostra/grupo em qualquer posição, outras ignoradas), linha vazia
    pulada e CRLF (checkout com autocrlf) lido igual ao LF."""
    lf = _tabela(tmp_path, {"A": 10, "B": 12}, nome="lf.tsv")
    texto = lf.read_text(encoding="utf-8")
    crlf = tmp_path / "crlf.tsv"
    crlf.write_bytes(texto.replace("\n", "\r\n").replace("S0003\tA\r\n", "S0003\tA\r\n\r\n").encode())
    trocada = tmp_path / "trocada.tsv"
    trocada.write_text("nota\tgrupo\tamostra\n" + "".join(
        f"x y\t{l.split(chr(9))[1]}\t{l.split(chr(9))[0]}\n" for l in texto.splitlines()[1:]),
        encoding="utf-8", newline="")
    saidas = []
    for i, p in enumerate((lf, crlf, trocada)):
        assert formatos.validar("rotulos_amostras", p).ok
        r = _contraste(tmp_path / f"s{i}", "A", "B", rotulos=p)
        assert r.returncode == 0, r.stderr
        saidas.append(((tmp_path / f"s{i}" / "grupo_a.txt").read_bytes(),
                       (tmp_path / f"s{i}" / "grupo_b.txt").read_bytes(), _resumo(tmp_path / f"s{i}")))
    assert saidas[0] == saidas[1] == saidas[2]
    assert saidas[0][2]["n_a"] == 10 and saidas[0][2]["n_b"] == 12


@pytest.mark.parametrize("a, b, minimo, trecho", [
    ("Angus", "Angus", "10", "classes iguais"),          # o IndexError do ml_classificar
    ("Angus", "Nelore", "10", "ausente(s) da tabela"),    # nível que a tabela não tem
    ("Gir", "Brahman", "10", "ausente(s) da tabela"),
    ("N_Dama", "Angus", "11", "abaixo do mínimo de 11"),  # N_Dama tem 10
    ("Angus", "N_Dama", "11", "N_Dama com 10"),
])
def test_canarios_do_contraste(tmp_path, a, b, minimo, trecho):
    r = _contraste(tmp_path, a, b, minimo=minimo)
    assert r.returncode != 0 and trecho in r.stderr, (r.returncode, r.stderr)
    assert not (tmp_path / "grupo_a.txt").exists() and not (tmp_path / "contraste_resumo.json").exists()


def test_controle_do_minimo_na_borda(tmp_path):
    """Controle do canário do mínimo: N_Dama com exatamente 10 passa com mínimo 10."""
    r = _contraste(tmp_path, "N_Dama", "Angus")
    assert r.returncode == 0, r.stderr
    assert _resumo(tmp_path)["n_a"] == 10


@pytest.mark.parametrize("conteudo, trecho", [
    ("amostra\tclasse\nS1\tA\n", "colunas 'amostra' e 'grupo'"),
    ("amostra\tgrupo\tgrupo\nS1\tA\tA\n", "colunas 'amostra' e 'grupo'"),
    ("amostra\tgrupo\nS1\tA\nS2\n", "nº de campos"),
    ("amostra\tgrupo\nS1\tA\nS1\tB\n", "amostra repetida"),
    ("amostra\tgrupo\nS1\t\n", "vazio"),
])
def test_canarios_de_estrutura(tmp_path, conteudo, trecho):
    p = tmp_path / "t.tsv"
    p.write_text(conteudo, encoding="utf-8", newline="")
    r = _contraste(tmp_path / "s", "A", "B", rotulos=p, minimo="1")
    assert r.returncode != 0 and trecho in r.stderr, r.stderr


def test_grupo_pequeno_na_tabela_sintetica(tmp_path):
    """Canário e controle do mínimo numa tabela sintética: 9 amostras reprovam, 10 passam."""
    r = _contraste(tmp_path / "c", "A", "B", rotulos=_tabela(tmp_path, {"A": 9, "B": 10}, nome="c.tsv"))
    assert r.returncode != 0 and "A com 9" in r.stderr, r.stderr
    r = _contraste(tmp_path / "k", "A", "B", rotulos=_tabela(tmp_path, {"A": 10, "B": 10}, nome="k.tsv"))
    assert r.returncode == 0, r.stderr


# ============================================================ o scripts_api do ML
def _ml():
    reg = load_registry()
    assert reg.erros == {}, reg.erros
    return reg.por_id("ml")


def test_registry_sem_erro_e_selos_vigentes(capsys):
    """§A7: manifest inteiro carregado (uma chave errada some com o módulo e com o selo), os 5
    selos com os hashes de sempre e `python -m omica selos` com código 0."""
    m = _ml()
    assert [s.key for s in m.scripts_api] == CHAVES_API
    assert not {s.key for s in m.scripts} & set(CHAVES_API)
    estado = selos.conferir()
    assert {s["workflow"]: s["atual"] for s in estado} == SELOS
    assert not any(s["vencido"] for s in estado)
    wf = next(w for w in m.workflows if w.id == "wf_ml_aims")
    assert hash_dos_passos(m, wf) == wf.hash_scripts == SELOS["wf_ml_aims"]
    texto = (MODULES_DIR / "ml" / "manifest.yaml").read_text(encoding="utf-8")
    assert len(re.findall(r"^\s*hash_scripts:", texto, re.M)) == 1
    assert len(re.findall(r"^\s*testado_por:", texto, re.M)) == 1
    assert selos.main() == 0 and "vencido" not in capsys.readouterr().out


def test_scripts_api_do_ml_seguem_as_regras_dos_dois_modulos():
    """§3 e §A7: todo cmd grava em {saida} e não cita {dados}; nenhum parâmetro reservado
    (dados/saida, ref_/sub_/esp_); nada de referência de contexto; espécie no default; cada
    `consome` é um parâmetro do script e um formato que formatos sabe validar."""
    for s in _ml().scripts_api:
        assert "{saida}" in s.cmd and "{dados}" not in s.cmd, s.key
        assert s.especies == ["bos_taurus"] and s.requer_refs == [], s.key
        nomes = {p.name for p in s.params}
        assert not nomes & {"dados", "saida"}, s.key
        assert not [n for n in nomes if n.startswith(("ref_", "sub_", "esp_"))], s.key
        assert set(s.consome) <= nomes and set(s.consome.values()) <= set(formatos.VALIDADORES), s.key
        assert {p.formato for p in s.produz.values()} <= set(formatos.VALIDADORES), s.key


def test_regiao_do_dataset_e_do_dado():
    """M3: só a `regiao` tem escopo de subprojeto; os parâmetros do método ficam livres e com os
    defaults do selado."""
    escopos = {(s.key, p.name): (p.escopo, p.default) for s in _ml().scripts_api for p in s.params}
    assert escopos[("ml_dataset", "regiao")] == ("subprojeto", "14:1-2500000")
    assert [k for k, (e, _) in escopos.items() if e != "livre"] == [("ml_dataset", "regiao")]
    assert {k[1]: d for k, (_, d) in escopos.items() if k[1] in ("maf", "passo", "minimo", "n_boot")} == {
        "maf": "0.2", "passo": "15", "minimo": "10", "n_boot": "100"}


def _saida(cfg, passo: str) -> str:
    return (cfg.data_dir / "results" / "ml" / "runs" / "ml_aims_vcf-20261029-000000-abcdef" / passo).as_posix()


def test_argv_do_demo_igual_ao_da_espec(cfg):
    """O cmd de cada scripts_api no demo (contexto.demo(), defaults do manifest) é o argv da
    ESPEC §3.1.1, token a token; o `--grupo-a Classe=lista` continua 1 argumento."""
    m = _ml()
    ctx = contexto.demo()
    raw = (cfg.data_dir / "raw" / "runs" / "r").as_posix()
    d = {p: _saida(cfg, p) for p in ("contraste", "dataset", "diagnostico", "classificar", "robustez")}
    casos = {
        "ml_contraste": ({"rotulos": f"{raw}/rotulos.tsv", "classe_a": "Angus", "classe_b": "Brahman"},
                         f"python3 modules/ml/scripts/ml_contraste.py --rotulos {raw}/rotulos.tsv "
                         f"--classe-a Angus --classe-b Brahman --minimo 10 --saida {d['contraste']}"),
        "ml_dataset": ({"vcf": f"{raw}/vcf.bcf", "classe_a": "Angus", "classe_b": "Brahman",
                        "lista_a": f"{d['contraste']}/grupo_a.txt", "lista_b": f"{d['contraste']}/grupo_b.txt"},
                       f"python3 modules/ml/scripts/ml_dataset_genotipos.py --vcf {raw}/vcf.bcf "
                       f"--regiao 14:1-2500000 --grupo-a Angus={d['contraste']}/grupo_a.txt "
                       f"--grupo-b Brahman={d['contraste']}/grupo_b.txt --maf 0.2 --passo 15 "
                       f"--saida {d['dataset']}"),
    }
    matriz, rotulos = f"{d['dataset']}/genotipos.tsv", f"{d['dataset']}/rotulos.tsv"
    for chave, script, extra in (("ml_diagnostico", "ml_diagnostico.py", ""),
                                 ("ml_classificar", "ml_classificar.py", ""),
                                 ("ml_robustez", "ml_robustez.py", " --n-boot 100")):
        passo = chave.removeprefix("ml_")
        casos[chave] = ({"matriz": matriz, "rotulos": rotulos},
                        f"python3 modules/ml/scripts/{script} --matriz {matriz} --rotulos {rotulos} "
                        f"--k-painel 50{extra} --saida {d[passo]}")
    assert set(casos) == set(CHAVES_API)
    for s in m.scripts_api:
        valores, esperado = casos[s.key]
        cmd = dispatch.montar_cmd(s, valores, saida=d[s.key.removeprefix("ml_")], ctx=ctx, mod_id="ml")
        assert cmd.split() == esperado.split(), s.key
    # sem contexto (v1) o cmd é o mesmo: a regiao do demo não depende do escopo no demo
    s = next(x for x in m.scripts_api if x.key == "ml_dataset")
    assert dispatch.montar_cmd(s, casos["ml_dataset"][0], saida=d["dataset"]).split() == \
        casos["ml_dataset"][1].split()


def test_regiao_fora_do_demo_sem_sobrescrita_recusa(cfg):
    """M3 no dispatch (o 422 do plano está em test_relatorio_ml): num rascunho bovino a região
    do demo não vale em silêncio; com `parametros` do subprojeto, vale a do subprojeto."""
    s = next(x for x in _ml().scripts_api if x.key == "ml_dataset")
    valores = {"vcf": "/data/omica/raw/runs/r/vcf.bcf", "classe_a": "A", "classe_b": "B",
               "lista_a": "/data/omica/x/grupo_a.txt", "lista_b": "/data/omica/x/grupo_b.txt"}
    for slug, parametros in (("ml_sem", {}), ("ml_com", {"ml.ml_dataset.regiao": "14:1-1000"})):
        escrever_subprojeto(cfg.projetos_dir, "bos_taurus", slug, assembly="ARS-UCD1.3", modulos=["ml"],
                            pipelines=["ml_aims_vcf"], parametros=parametros)
    saida = (cfg.data_dir / "results" / "projetos" / "bos_taurus" / "{slug}" / "ml" / "runs" / "r" /
             "dataset").as_posix()
    with pytest.raises(ValueError, match="o padrão é do demo"):
        dispatch.montar_cmd(s, valores, saida=saida.format(slug="ml_sem"),
                            ctx=contexto.resolver("bos_taurus", "ml_sem"), mod_id="ml")
    cmd = dispatch.montar_cmd(s, valores, saida=saida.format(slug="ml_com"),
                              ctx=contexto.resolver("bos_taurus", "ml_com"), mod_id="ml")
    assert "--regiao 14:1-1000 " in cmd
