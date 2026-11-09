"""Formatos e checagens da Epigenômica (F6): ESPEC §2.1/§2.3 + adendo v2 §A6.2/§A6.3, mais as
peças comuns da F1a que moram em formatos.py (desvio de pasta no validar, cache, json_finito do
X2, CHAVE_LINHA, sincronia das tabelas).

Regra do projeto: todo portão é VISTO reprovando (canário conferido pelo MOTIVO — substring da
falha) e tem um controle que passa. Os controles reais: o demo versionado no repo (6 membros,
439.723 B, 77.358 linhas, 27 cromossomos) e o painel_108; as saídas seladas de
E:\\data\\omica\\results\\epigenomica\\demo e o W15_2026 (E:\\data\\epigenomas) têm skip quando
ausentes (CI, container sem o E:).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import shutil
import time
from pathlib import Path

import pytest

from omica.agentes import adaptador
from omica.config import REPO_ROOT
from omica.contrato import diretorio, formatos

EPI = REPO_ROOT / "modules" / "epigenomica"
DEMO = EPI / "demo_data"
PAINEL = EPI / "data" / "paineis" / "painel_108.json"
PAINEL_BED = EPI / "data" / "paineis" / "painel_108.bed"
E_DEMO = Path(r"E:\data\omica\results\epigenomica\demo")
W15 = Path(r"E:\data\epigenomas\NCBI_GSE161113\W15_2026")
CAB_MK = "chr\tstart\tend\tstrand\tpvalue\tqvalue\tmeth.diff"


@pytest.fixture(autouse=True)
def _cache_limpo():
    formatos._validar_pasta_cache.cache_clear()
    yield
    formatos._validar_pasta_cache.cache_clear()


def _exige(p: Path) -> Path:
    if not p.exists():
        pytest.skip(f"{p.name} ausente (dado real fora do repositório)")
    return p


def _reprova(v: formatos.Veredito, trecho: str) -> None:
    assert not v.ok and any(trecho in f for f in v.falhas), v.falhas


def _texto(tmp_path: Path, nome: str, conteudo: str) -> Path:
    p = tmp_path / nome
    p.write_text(conteudo, encoding="utf-8", newline="")
    return p


def _linha(crom: str = "1", pos: int = 100, m: int = 3, u: int = 1) -> str:
    return f"{crom}\t{pos}\t{pos}\t{100 * m / (m + u)!r}\t{m}\t{u}\n"


def _cov(n: int = 5, crom: str = "1", inicio: int = 100) -> str:
    return "".join(_linha(crom, inicio + 10 * i, 1 + i % 3, 2) for i in range(n))


def _pasta(tmp_path: Path, membros: dict[str, bytes], nome: str = "cov") -> Path:
    p = tmp_path / nome
    p.mkdir()
    for n, b in membros.items():
        (p / n).write_bytes(b)
    return p


def _gz(texto: str) -> bytes:
    return gzip.compress(texto.encode("ascii"), mtime=0)


def _cov_bismark(tmp_path: Path, conteudo: str, nome: str = "a.cov.gz") -> formatos.Veredito:
    corpo = _gz(conteudo) if nome.endswith(".gz") else conteudo.encode("ascii")
    return formatos.validar("coverages_bismark", _pasta(tmp_path, {nome: corpo, "b.cov.gz": _gz(_cov())}))


def _montada(tmp_path: Path, nome: str = "montada") -> Path:
    """A saída do montar sobre o demo: <condição>__<amostra>.bismark.cov.gz."""
    p = tmp_path / nome
    p.mkdir()
    for arq in sorted(DEMO.iterdir()):
        cond = "HF" if "_HF_" in arq.name else "Nelore"
        shutil.copyfile(arq, p / formatos.nome_por_condicao(cond, formatos.stem_coverage(arq.name)))
    return p


def _coldata_demo(tmp_path: Path, troca: dict[str, str] | None = None, nome: str = "amostras.tsv") -> Path:
    linhas = ["amostra\tcondicao"]
    for arq in sorted(DEMO.iterdir()):
        cond = "HF" if "_HF_" in arq.name else "Nelore"
        linhas.append(f"{formatos.stem_coverage(arq.name)}\t{(troca or {}).get(cond, cond)}")
    return _texto(tmp_path, nome, "\n".join(linhas) + "\n")


# ================================================== coverages_bismark: controles
def test_demo_aprova_com_os_numeros_de_controle():
    v = formatos.validar("coverages_bismark", DEMO)
    assert v.ok, v.falhas
    p = v.perfil
    assert (p["n_membros"], p["bytes"], p["n_linhas"], p["n_cromossomos"]) == (6, 439_723, 77_358, 27)
    assert p["n_crlf"] == 77_358 and p["avisos"] == []  # o demo é CRLF dentro do gzip
    assert len(p["cromossomos"]) == 27 and "teto" not in p


def test_w15_aprova_com_os_tetos_reais():
    v = formatos.validar("coverages_bismark", _exige(W15))
    assert v.ok, v.falhas
    assert v.perfil["n_membros"] == 10 and all(c.startswith("chr") for c in v.perfil["cromossomos"])


def test_dois_membros_gzip_ok_aprovam_e_contam_as_linhas_dos_dois(tmp_path):
    """Crítica M11, controle: o gzip do Python e o gzfile do R leem os dois membros."""
    p = _pasta(tmp_path, {"a.cov.gz": _gz(_cov(5)) + _gz(_cov(7, "2")), "b.cov": _cov(3).encode()})
    v = formatos.validar("coverages_bismark", p)
    assert v.ok, v.falhas
    assert v.perfil["n_linhas"] == 15 and v.perfil["cromossomos"] == ["1", "2"]


def test_fora_de_ordem_so_avisa(tmp_path):
    p = _pasta(tmp_path, {"a.cov.gz": _gz(_linha("1", 500) + _linha("1", 100) + _linha("2", 10) + _linha("1", 900))})
    v = formatos.validar("coverages_bismark", p)
    assert v.ok and "fora de ordem" in v.perfil["avisos"][0], (v.falhas, v.perfil)


# ================================================== coverages_bismark: canários
@pytest.mark.parametrize("nome,conteudo,trecho", [
    ("a.cov", None, "'.cov' com conteúdo gzip"),
    ("a.cov.gz", "texto", "não é gzip"),
    ("a.cov.gz", "truncado", "gzip truncado"),
    ("a.cov.gz", "1\t100\t100\t75.0\t3\n", "nº de campos diferente de 6"),
    ("a.cov.gz", "chr\tstart\tend\tpct\tm\tu\n" + _cov(), "cabeçalho"),
    ("a.cov.gz", "#comentario\n" + _cov(), "cabeçalho"),
    ("a.cov.gz", "1\t100\t100\t101\t3\t0\n", "[0, 100]"),
    ("a.cov.gz", "1\t100\t100\t50\t1\t3\n", "incoerente"),
    ("a.cov.gz", "1\t100\t100\t0\t0\t0\n", "= 0"),
    ("a.cov.gz", "1\t100\t100\t75.0\t3\t1\r\r\n", "'\\r' fora do fim"),
    ("a.cov.gz", "1\t100\t100\t75.0\t3\t1\n\n" + _cov(), "linha vazia"),
    ("a.cov.gz", "chr 1\t100\t100\t75.0\t3\t1\n", "cromossomo fora"),
    ("a.cov.gz", "1\t200\t100\t75.0\t3\t1\n", "1 ≤ início ≤ fim"),
    ("a.cov.gz", "1\t100\t100\tinf\t3\t1\n", "[0, 100]"),
])
def test_coverage_canarios(tmp_path, nome, conteudo, trecho):
    if conteudo is None:  # .cov com conteúdo gzip
        p = _pasta(tmp_path, {nome: _gz(_cov())})
    elif conteudo == "texto":
        p = _pasta(tmp_path, {nome: _cov().encode()})
    elif conteudo == "truncado":
        p = _pasta(tmp_path, {nome: _gz(_cov(200))[:-30]})
    else:
        p = _pasta(tmp_path, {nome: _gz(conteudo)})
    _reprova(formatos.validar("coverages_bismark", p), trecho)


def test_pasta_so_com_readme_reprova(tmp_path):
    p = _pasta(tmp_path, {"README.txt": b"nada"})
    _reprova(formatos.validar("coverages_bismark", p), "pasta sem membros")


def test_segundo_membro_gzip_com_cabecalho_reprova(tmp_path):
    """Crítica M11: um decompressobj único parava no 1º membro e o 2º (cabeçalho) passava."""
    p = _pasta(tmp_path, {"a.cov.gz": _gz(_cov()) + _gz("chr\tstart\tend\tpct\tm\tu\n" + _cov())})
    _reprova(formatos.validar("coverages_bismark", p), "cabeçalho")


def test_lixo_depois_do_gzip_reprova(tmp_path):
    p = _pasta(tmp_path, {"a.cov.gz": _gz(_cov()) + b"lixo"})
    _reprova(formatos.validar("coverages_bismark", p), "lixo depois do gzip")


def test_bomba_de_tres_membros_reprova_pela_razao_em_menos_de_1s(tmp_path):
    """Crítica M12: 3 membros gzip de 1 M linhas idênticas (~50 KB cada, razão ~300:1). Sem a
    razão, a validação leria ~45 MB e 3 M linhas válidas e APROVARIA."""
    um = gzip.compress(b"1\t1\t1\t100\t1\t0\n" * 1_000_000, compresslevel=9, mtime=0)
    assert len(um) < 100_000
    p = _pasta(tmp_path, {"a.cov.gz": um * 3})
    t = time.perf_counter()
    v = formatos.validar("coverages_bismark", p)
    assert time.perf_counter() - t < 1.0
    _reprova(v, f"razão de compressão > {diretorio.MAX_RAZAO_GZIP}:1")
    assert "teto" not in v.perfil  # razão é conteúdo (422), não tamanho (413)


_LINHA_BOMBA = b"1\t10\t10\t100\t1\t0\n"   # 16 B, válida (pct 100 = 1/(1+0))


def _membro_bomba() -> bytes:
    """(1 MiB − 64 B) de linhas válidas idênticas, ~2 KB de gzip: abaixo de 1 MiB a razão por
    membro não vale."""
    return gzip.compress(_LINHA_BOMBA * (((1 << 20) - 64) // len(_LINHA_BOMBA)), mtime=0)


def test_bomba_partida_em_200_membros_abaixo_de_1_mib_reprova_pela_razao_agregada(tmp_path):
    """Achado gzip_agregado: 200 membros de (1 MiB − 64 B) somam 209,7 MB descomprimidos de
    ~415 KB (~500:1) e nenhum chega a 1 MiB. Só com a razão por membro, a validação lia tudo
    (~55 s) e APROVAVA. A razão somando os .cov.gz da pasta reprova no 2º membro."""
    um = _membro_bomba()
    desc = len(gzip.decompress(um))
    assert desc < formatos._MIN_DESCOMPRIMIDO_RAZAO and desc > 400 * len(um)
    p = _pasta(tmp_path, {f"s{i:03d}.cov.gz": um for i in range(200)})
    t = time.perf_counter()
    v = formatos.validar("coverages_bismark", p)
    gasto = time.perf_counter() - t
    _reprova(v, f"razão de compressão > {diretorio.MAX_RAZAO_GZIP}:1 somando os membros .cov.gz da pasta")
    assert "teto" not in v.perfil  # conteúdo (422), como a razão por membro
    assert gasto < 2.0, gasto


def test_razao_agregada_controles(tmp_path, monkeypatch):
    """Controles da razão agregada: (i) um membro da bomba sozinho aprova (Σ ≤ 1 MiB: a razão não
    vale); (ii) três reprovam e, com MAX_RAZAO_GZIP acima da razão deles, aprovam — o motivo é a
    razão, não o conteúdo; (iii) um .cov de texto acima de 1 MiB fica fora da soma (é lido 1:1,
    não descomprime nada) e aprova ao lado de um .cov.gz pequeno."""
    um = _membro_bomba()
    assert formatos.validar("coverages_bismark", _pasta(tmp_path, {"s0.cov.gz": um}, "um")).ok
    tres = _pasta(tmp_path, {f"s{i}.cov.gz": um for i in range(3)}, "tres")
    _reprova(formatos.validar("coverages_bismark", tres), "somando os membros .cov.gz da pasta")
    real = diretorio.MAX_RAZAO_GZIP
    monkeypatch.setattr(diretorio, "MAX_RAZAO_GZIP", 10_000)
    v = formatos.validar("coverages_bismark", tres)
    assert v.ok and v.perfil["n_linhas"] == 3 * 65_532, v.falhas
    monkeypatch.setattr(diretorio, "MAX_RAZAO_GZIP", real)
    texto = _LINHA_BOMBA * (3 << 16)   # 3 MiB de .cov
    v = formatos.validar("coverages_bismark", _pasta(tmp_path, {"a.cov.gz": _gz(_cov(5)), "b.cov": texto}, "mista"))
    assert v.ok and v.perfil["descomprimido"] > 20 * len(_gz(_cov(5))), v.falhas


@pytest.mark.parametrize("pasta", [DEMO, W15], ids=["demo", "w15"])
def test_razao_agregada_armada_e_folgada_nos_controles_reais(pasta):
    """O demo e o W15 passam com a regra ARMADA (Σ > 1 MiB descomprimido) e longe do limite
    (5,28:1 e 5,52:1, todos .cov.gz): sem isso o controle não provaria nada."""
    v = formatos.validar("coverages_bismark", _exige(pasta))
    assert v.ok, v.falhas
    p = v.perfil
    assert all(n.endswith(".cov.gz") for n in p["membros"])
    assert formatos._MIN_DESCOMPRIMIDO_RAZAO < p["descomprimido"] < 6 * p["bytes"]


def test_teto_de_linhas_da_pasta_reprova_o_demo_como_teto(monkeypatch):
    monkeypatch.setattr(diretorio, "MAX_LINHAS_PASTA", 50_000)
    v = formatos.validar("coverages_bismark", DEMO)
    _reprova(v, "mais de 50000 linhas somando os membros")
    assert v.perfil["teto"]["limite_linhas"] == 50_000 and "limite_bytes" in v.perfil["teto"]
    monkeypatch.setattr(diretorio, "MAX_LINHAS_PASTA", 77_358)  # controle: no limite exato, passa
    assert formatos.validar("coverages_bismark", DEMO).ok


def test_teto_de_bytes_descomprimidos_da_pasta(monkeypatch):
    monkeypatch.setattr(diretorio, "MAX_DESCOMPRIMIDO_PASTA", 1_000_000)
    v = formatos.validar("coverages_bismark", DEMO)
    _reprova(v, "bytes descomprimidos somando os membros")
    assert v.perfil["teto"]["limite_bytes"] == 1_000_000


def test_teto_por_membro(tmp_path, monkeypatch):
    monkeypatch.setattr(formatos, "_MAX_LINHAS_COV", 3)
    v = formatos.validar("coverages_bismark", _pasta(tmp_path, {"a.cov.gz": _gz(_cov(5))}))
    _reprova(v, "mais de 3 linhas num membro")
    assert v.perfil["teto"] == {"limite_linhas": 3}


def test_conteudo_nao_ecoa_na_falha(tmp_path):
    p = _pasta(tmp_path, {"a.cov.gz": _gz("TOKEN=segredo\t1\t1\t100\t1\t0\n")})
    v = formatos.validar("coverages_bismark", p)
    assert not v.ok and "segredo" not in " ".join(v.falhas)


# ============================================================ desvio e cache do validar
def test_validar_pasta_e_arquivo_nao_se_trocam(tmp_path):
    arq = tmp_path / "a.cov.gz"
    arq.write_bytes(_gz(_cov()))
    _reprova(formatos.validar("coverages_bismark", arq), "esperava uma pasta")
    _reprova(formatos.validar("coverages_bismark", tmp_path / "nao_existe"), "esperava uma pasta")
    _reprova(formatos.validar("coldata", DEMO), "diretório")  # formato de arquivo: pasta continua reprovando
    assert formatos.validar_com_nome("coverages_bismark", DEMO).ok  # sem regra de nome de arquivo


def test_cache_por_identidade_dos_membros_e_copia_do_veredito(tmp_path):
    p = tmp_path / "cov"
    shutil.copytree(DEMO, p)
    v1 = formatos.validar("coverages_bismark", p)
    v1.perfil["cromossomos"].clear()  # quem chama não estraga o cache
    v2 = formatos.validar("coverages_bismark", p)
    assert formatos._validar_pasta_cache.cache_info().hits == 1 and len(v2.perfil["cromossomos"]) == 27
    alvo = p / "FILTERED_H82_HF_ERS4410317_CD4.bismark.cov.gz"
    alvo.write_bytes(gzip.compress(gzip.decompress(alvo.read_bytes()) + b"1\t5\t5\t101\t1\t0\n", mtime=0))
    _reprova(formatos.validar("coverages_bismark", p), "[0, 100]")  # o membro mudou: releu
    (p / "novo.cov").write_bytes(b"1\t1\t1\t100\t1\t0\n")
    assert formatos.validar("coverages_bismark", p).perfil["n_membros"] == 7


def test_chamada_direta_do_validador_de_pasta_nunca_levanta(tmp_path):
    arq = tmp_path / "x.cov.gz"
    arq.write_bytes(b"x")
    for formato in formatos.DIRETORIOS:
        for alvo in (arq, tmp_path / "nao_existe", tmp_path):
            v = formatos.VALIDADORES[formato](alvo)
            assert not v.ok and v.falhas and tmp_path.name not in " ".join(v.falhas)


# ========================================================= coverages_por_condicao
def test_por_condicao_controle_montar_sobre_o_demo(tmp_path):
    v = formatos.validar("coverages_por_condicao", _montada(tmp_path))
    assert v.ok, v.falhas
    assert v.perfil["por_condicao"] == {"HF": 3, "Nelore": 3} and v.perfil["n_linhas"] == 77_358


@pytest.mark.parametrize("troca,trecho", [
    ({"HF__solto.cov": "plain"}, "não é '<condição>__<amostra>.bismark.cov.gz'"),
    ({"semcondicao.bismark.cov.gz": "gz"}, "sem '<condição>__'"),
    ({"Gir__a.bismark.cov.gz": "gz", "Gir__b.bismark.cov.gz": "gz", "Girolando__c.bismark.cov.gz": "gz",
      "Girolando__d.bismark.cov.gz": "gz"}, "SUBSTRING"),
    ({"vs_x__a.bismark.cov.gz": "gz"}, "condição do nome"),
])
def test_por_condicao_canarios(tmp_path, troca, trecho):
    membros = {n: (_cov().encode() if t == "plain" else _gz(_cov())) for n, t in troca.items()}
    if "Gir__a.bismark.cov.gz" not in troca:
        membros.update({"HF__a.bismark.cov.gz": _gz(_cov()), "HF__b.bismark.cov.gz": _gz(_cov())})
    _reprova(formatos.validar("coverages_por_condicao", _pasta(tmp_path, membros)), trecho)


def test_stem_e_nome_por_condicao():
    assert formatos.stem_coverage("x.bismark.cov.gz") == "x"
    assert formatos.stem_coverage("x.cov.gz") == "x"
    assert formatos.stem_coverage("x.bismark.cov") == "x"
    assert formatos.stem_coverage("x.cov") == "x"
    assert formatos.stem_coverage("x.cov.gz.bak") is None and formatos.stem_coverage(".cov.gz") is None
    assert formatos.nome_por_condicao("HF", "x") == "HF__x.bismark.cov.gz"
    assert formatos.stem_coverage(formatos.nome_por_condicao("HF", "x")) == "HF__x"


# ============================================================== painel_genes_json
def test_painel_108_controle():
    # conteudo normalizado em LF: o checkout do Windows (autocrlf) tem 41.887 B (CRLF); o CI, 40.469 B
    bruto = PAINEL.read_bytes().replace(b"\r\n", b"\n")
    assert len(bruto) == 40_469 and hashlib.sha256(bruto).hexdigest().startswith("d90f458d")
    v = formatos.validar("painel_genes_json", PAINEL)
    assert v.ok, v.falhas
    p = v.perfil
    assert (p["n_genes"], p["n_fita_negativa"], p["n_sem_ensembl"], len(p["cromossomos"]), p["tem_meta"]) == \
        (108, 45, 17, 27, True)


def _painel(genes: dict | list | str, **extra) -> str:
    if isinstance(genes, str):
        return genes
    return json.dumps({"genes": genes, **extra})


_G = {"cromossomo": "1", "inicio": 100, "fim": 200, "fita": 1, "ensembl_id": "ENSBTAG1"}


@pytest.mark.parametrize("conteudo,trecho", [
    ('{"genes": {"A": {"cromossomo": "1", "inicio": 1, "inicio": 2, "fim": 5, "fita": 1}}}', "chave repetida"),
    (_painel({"A": {**_G, "fita": 0}}), "fita fora de {1, -1}"),
    (_painel({"A": {**_G, "fita": True}}), "fita fora de {1, -1}"),
    (_painel({"A": {**_G, "fita": "1"}}), "fita fora de {1, -1}"),
    (_painel({"A": {**_G, "inicio": 300}}), "1 ≤ inicio ≤ fim"),
    ('{"genes": {"A": {"cromossomo": "1", "inicio": NaN, "fim": 5, "fita": 1}}}', "não finito"),
    (_painel({}), "'genes' ausente, vazio"),
    (json.dumps([_G]), "raiz do JSON não é objeto"),
    (_painel({"GENE X": _G}), "símbolo fora"),
    (_painel({"A": {**_G, "inicio": 100.0}}), "não inteiro"),
    (_painel({"A": {**_G, "cromossomo": True}}), "cromossomo ausente"),
    (_painel({"A": {**_G, "ensembl_id": None}}), "ensembl_id fora"),
    (_painel({"A": {**_G, "fim": 2 ** 31}}), "< 2^31"),
])
def test_painel_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("painel_genes_json", _texto(tmp_path, "p.json", conteudo)), trecho)


def test_painel_bom_e_cromossomo_inteiro(tmp_path):
    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + _painel({"A": _G}).encode())
    _reprova(formatos.validar("painel_genes_json", p), "BOM")
    v = formatos.validar("painel_genes_json", _texto(tmp_path, "i.json", _painel({"A": {**_G, "cromossomo": 7}})))
    assert v.ok and v.perfil["cromossomos"] == ["7"]  # controle: inteiro vale (str() nos scripts)
    sem = {k: v for k, v in _G.items() if k != "ensembl_id"}
    v = formatos.validar("painel_genes_json", _texto(tmp_path, "s.json", _painel({"A": sem})))
    assert v.ok and v.perfil["n_sem_ensembl"] == 1


# ===================================================================== painel_bed
def test_painel_bed_controle():
    v = formatos.validar("painel_bed", PAINEL_BED)
    assert v.ok and v.perfil["n_janelas"] == 108, v.falhas


@pytest.mark.parametrize("conteudo,trecho", [
    ("1\t100\t200\tA\n", "nº de campos diferente de 5"),
    ("1\t100\t200\tA\tE1\n2\t100\t200\tA\tE2\n", "gene repetido"),
    ("#chr\tini\tfim\tgene\tens\n1\t100\t200\tA\tE1\n", "cabeçalho"),
    ("track name=x\n1\t100\t200\tA\tE1\n", "cabeçalho"),
    ("1\t100\t200\tA\tE1\r\n", "CRLF"),
    ("1\t200\t200\tA\tE1\n", "início ≥ fim"),
    ("", "nenhuma janela"),
])
def test_painel_bed_canarios(tmp_path, conteudo, trecho):
    p = _texto(tmp_path, "j.bed", conteudo)
    v = formatos.painel_bed(p) if not conteudo else formatos.validar("painel_bed", p)
    _reprova(v, trecho)


def test_painel_bed_quinto_campo_vazio_passa(tmp_path):
    assert formatos.validar("painel_bed", _texto(tmp_path, "j.bed", "1\t100\t200\tA\t\n")).ok


def test_painel_bed_teto_de_janelas(tmp_path, monkeypatch):
    monkeypatch.setattr(formatos, "_MAX_SIMBOLOS", 3)
    linhas = "".join(f"1\t{100 * i}\t{100 * i + 50}\tG{i}\tE{i}\n" for i in range(1, 5))
    _reprova(formatos.validar("painel_bed", _texto(tmp_path, "j.bed", linhas)), "mais de 3 janelas")
    tres = "".join(f"1\t{100 * i}\t{100 * i + 50}\tG{i}\tE{i}\n" for i in range(1, 4))
    assert formatos.validar("painel_bed", _texto(tmp_path, "k.bed", tres)).ok  # controle: no limite, passa


# ======================================================== dmc_methylkit / cpgs_methylkit
def _mk(tmp_path: Path, linhas: list[str], cab: str = CAB_MK, nome: str = "t.tsv") -> Path:
    return _texto(tmp_path, nome, cab + "\n" + "".join(l + "\n" for l in linhas))


_DMC_OK = ["1\t100\t100\t*\t1e-05\t0.001\t20.5", "1\t200\t200\t*\t0.01\t0.04\t-16"]


def test_dmc_e_cpgs_selados_controle():
    v = formatos.validar("dmc_methylkit", _exige(E_DEMO / "dmc" / "dmc.tsv"))
    assert v.ok and v.perfil == {"n_dmc": 275, "n_hiper": 185, "n_hipo": 90}, v.falhas
    v = formatos.validar("cpgs_methylkit", _exige(E_DEMO / "dmc" / "todas_cpgs.tsv"))
    assert v.ok and v.perfil == {"n_cpgs": 3714, "n_q_na": 0, "n_diff_zero": 1781}, v.falhas


def test_methylkit_sinteticos_controle(tmp_path):
    assert formatos.validar("dmc_methylkit", _mk(tmp_path, _DMC_OK)).ok
    assert formatos.validar("dmc_methylkit", _mk(tmp_path, [], nome="vazio.tsv")).ok  # 0 DMC é resultado
    cpgs = _DMC_OK + ["1\t300\t300\t*\tNA\tNA\tNA", "2\t5\t5\t+\t0.5\t0.9\t1.4210854715202e-14", "2\t9\t9\t-\t1\t1\t0"]
    v = formatos.validar("cpgs_methylkit", _mk(tmp_path, cpgs, nome="c.tsv"))
    assert v.ok and v.perfil == {"n_cpgs": 5, "n_q_na": 1, "n_diff_zero": 1}, v.falhas


def test_todas_cpgs_no_lugar_do_dmc_reprova(tmp_path):
    p = E_DEMO / "dmc" / "todas_cpgs.tsv"
    if p.exists():
        _reprova(formatos.validar("dmc_methylkit", p), "meth.diff = 0")
    _reprova(formatos.validar("dmc_methylkit", _mk(tmp_path, _DMC_OK + ["1\t300\t300\t*\t0.5\t0.9\t0"])),
             "meth.diff = 0")


@pytest.mark.parametrize("formato,linhas,cab,trecho", [
    ("dmc_methylkit", ['1\t100\t100\t*\t0.1\t0.04\t"20"'], CAB_MK, "aspas"),
    ("dmc_methylkit", ["1\t1\t100\t100\t*\t0.1\t0.04\t20"], CAB_MK, "nº de campos diferente de 7"),
    ("dmc_methylkit", ["1\t100\t100\t*\tNA\t0.04\t20"], CAB_MK, "NA numa DMC"),
    ("dmc_methylkit", ["1\t100\t100\t*\t0.1\t0.04\tinf"], CAB_MK, "meth.diff fora"),
    ("cpgs_methylkit", ["1\t100\t100\t*\t0.1\t0.04\t20"], "chr\tstart\tend\tstrand\tqvalue\tpvalue\tmeth.diff",
     "cabeçalho"),
    ("cpgs_methylkit", ["1\t100\t100\t*\t0.1\t1.5\t20"], CAB_MK, "qvalue fora de [0,1]"),
    ("cpgs_methylkit", ["1\t100\t100\t*\t0.1\t0.4\t20", "1\t100\t101\t*\t0.1\t0.4\t-20"], CAB_MK,
     "chr:start repetida"),
    ("cpgs_methylkit", ["1\t100\t100\t*\t0.1\t0.4\tinf"], CAB_MK, "meth.diff fora"),
    ("cpgs_methylkit", ["1\t1e+05\t100\t*\t0.1\t0.4\t2"], CAB_MK, "start/end não são inteiros"),
    ("cpgs_methylkit", ["1\t100\t100\t?\t0.1\t0.4\t2"], CAB_MK, "strand"),
    ("cpgs_methylkit", [], CAB_MK, "sem CpGs"),
])
def test_methylkit_canarios(tmp_path, formato, linhas, cab, trecho):
    _reprova(formatos.validar(formato, _mk(tmp_path, linhas, cab)), trecho)


# ======================================================== tabela_intersecao_painel
def test_intersecao_selada_controle():
    v = formatos.validar("tabela_intersecao_painel", _exige(E_DEMO / "intersecao" / "intersecao.tsv"))
    assert v.ok and v.perfil == {"n_genes": 43, "soma_n_dmc": 267, "gene_topo": "HOXA10"}, v.falhas


@pytest.mark.parametrize("linhas,trecho", [
    (["A\t3\t1\t1"], "HIPER + HIPO ≠ N_DMC"),
    (["A\t3\t2\t1", "A\t2\t1\t1"], "GENE repetido"),
    (["A\t2\t1\t1", "B\t3\t2\t1"], "N_DMC crescente"),
    (["A\t0\t0\t0"], "N_DMC < 1"),
])
def test_intersecao_canarios(tmp_path, linhas, trecho):
    p = _texto(tmp_path, "i.tsv", "GENE\tN_DMC\tHIPER\tHIPO\n" + "".join(l + "\n" for l in linhas))
    _reprova(formatos.validar("tabela_intersecao_painel", p), trecho)


def test_intersecao_sem_linhas_passa(tmp_path):
    assert formatos.validar("tabela_intersecao_painel", _texto(tmp_path, "i.tsv", "GENE\tN_DMC\tHIPER\tHIPO\n")).ok


# ============================================================= tabela_motifs_homer
def test_motifs_selado_controle():
    v = formatos.validar("tabela_motifs_homer", _exige(E_DEMO / "motifs" / "motifs_top.tsv"))
    assert v.ok and v.perfil["n_linhas"] == 15 and v.perfil["motif_topo"] == "Zic(Zf)", v.falhas


@pytest.mark.parametrize("conteudo,trecho", [
    ("MOTIF\tP_VALUE\tQ_VALUE\n" + "".join(f"M{i}(Zf)\t1e-5\t0.01\n" for i in range(16)), "no máximo 15"),
    ("MOTIF\tP_VALUE\tQ_VALUE\nA(Zf)\t2\t0.01\n", "fora de [0,1]"),
    ("motif\tp_value\tq_value\nA(Zf)\t1e-5\t0.01\n", "cabeçalho"),
    ("MOTIF\tP_VALUE\tQ_VALUE\nA(Zf)\t1e-3\t0.01\nB(Zf)\t1e-5\t0.01\n", "decrescente"),
    ("MOTIF\tP_VALUE\tQ_VALUE\n", "nenhum motivo"),
])
def test_motifs_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("tabela_motifs_homer", _texto(tmp_path, "m.tsv", conteudo)), trecho)


# ======================================================================= view_mofa
@pytest.mark.parametrize("nome,n_feat", [("view_promotor.tsv", 58), ("view_corpo.tsv", 55)])
def test_views_seladas_controle(nome, n_feat):
    v = formatos.validar("view_mofa", _exige(E_DEMO / "mofa" / "views" / nome))
    assert v.ok and (v.perfil["n_features"], v.perfil["n_amostras"]) == (n_feat, 6), v.falhas


_VIEW_CAB = "feature\ts1\ts2\ts3\ts4"


@pytest.mark.parametrize("conteudo,trecho", [
    ("feature\ts1\ts2\ts3\nA\t1\t2\t3\n", "precisa de ≥4"),
    (_VIEW_CAB + "\nA\t1\t2\t3\t4\nA\t1\t2\t3\t4\n", "feature repetida"),
    (_VIEW_CAB + "\nA\t1\t2\t3\t101\n", "decimal em [0,100]"),
    (_VIEW_CAB + "\n", "só o cabeçalho"),
    (_VIEW_CAB + "\nA\tNA\tNA\tNA\tNA\n", "todas as amostras NA"),
    (_VIEW_CAB + "\nA\t1\t2\tnan\t4\n", "decimal em [0,100]"),
])
def test_view_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("view_mofa", _texto(tmp_path, "v.tsv", conteudo)), trecho)


# ========================================================== tabela_variancia_mofa
def test_variancia_selada_controle():
    v = formatos.validar("tabela_variancia_mofa", _exige(E_DEMO / "mofa" / "modelo" / "variancia_explicada.tsv"))
    assert v.ok and v.perfil["n_fatores"] == 3, v.falhas


@pytest.mark.parametrize("conteudo,trecho", [
    ("fator\tpromotor\tcorpo\nFactor2\t1\t2\nFactor1\t1\t2\n", "fora de sequência"),
    ("fator\tpromotor\tcorpo\nFactor1\tnan\t2\nFactor2\t1\t2\n", "R² não é decimal"),
    ("fator\tpromotor\tcorpo\nFactor1\t1\t2\n", "K ≥ 2"),
    ("fator\tpromotor\tcorpo\nFactor1\t1\t2\nFactor2\t1\t-101\n", "R² não é decimal"),
])
def test_variancia_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("tabela_variancia_mofa", _texto(tmp_path, "v.tsv", conteudo)), trecho)


# ==================================================================== fatores_mofa
_FAT = "amostra\tcondicao\tFactor1\tFactor2\n" + "".join(
    f"s{i}\t{'HF' if i < 3 else 'Nelore'}\t{0.1 * i:.4f}\t-0.0000\n" for i in range(6))


def test_fatores_controle(tmp_path):
    v = formatos.validar("fatores_mofa", _texto(tmp_path, "f.tsv", _FAT))
    assert v.ok and v.perfil["por_condicao"] == {"HF": 3, "Nelore": 3}, v.falhas
    selado = E_DEMO / "mofa" / "modelo" / "fatores_por_amostra.tsv"
    if selado.exists():  # o selado tem 'grupo'; o mofa_treinar_api grava 'condicao'
        texto = selado.read_text(encoding="utf-8").replace("amostra\tgrupo\t", "amostra\tcondicao\t", 1)
        assert formatos.validar("fatores_mofa", _texto(tmp_path, "s.tsv", texto)).ok


@pytest.mark.parametrize("conteudo,trecho", [
    (_FAT.replace("s0\tHF", "s0\t"), "condição vazia"),
    (_FAT.replace("Factor2", "Factor3", 1), "cabeçalho"),
    (_FAT.replace("0.1000", "nan"), "não é decimal finito"),
    ("amostra\tcondicao\tFactor1\tFactor2\ns1\tA\t1\t1\ns2\tB\t1\t1\n", "≥4"),
])
def test_fatores_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("fatores_mofa", _texto(tmp_path, "f.tsv", conteudo)), trecho)


# =========================================================== checagens da epigenômica
def test_coverages_e_amostras_controle_demo(tmp_path):
    v = formatos.coverages_e_amostras(DEMO, _coldata_demo(tmp_path))
    assert v.ok, v.falhas
    assert v.perfil == {"n_amostras": 6, "por_condicao": {"HF": 3, "Nelore": 3}, "n_arquivos_sem_amostra": 0,
                        "arquivos_sem_amostra": []}


def test_coverages_e_amostras_condicao_cov_reprova(tmp_path):
    """Condição 'cov': todo nome final '<x>__<amostra>.bismark.cov.gz' contém 'cov'."""
    _reprova(formatos.coverages_e_amostras(DEMO, _coldata_demo(tmp_path, {"HF": "cov"})), "SUBSTRING")


def test_coverages_e_amostras_gir_girolando_reprova(tmp_path):
    _reprova(formatos.coverages_e_amostras(DEMO, _coldata_demo(tmp_path, {"HF": "Gir", "Nelore": "Girolando"})),
             "SUBSTRING")


def test_coverages_e_amostras_amostra_sem_arquivo_reprova(tmp_path):
    cd = _coldata_demo(tmp_path)
    cd.write_text(cd.read_text(encoding="utf-8") + "FILTERED_H82_HF_ERS9999999_CD4\tHF\n", encoding="utf-8")
    _reprova(formatos.coverages_e_amostras(DEMO, cd), "sem arquivo")


def test_coverages_e_amostras_mesma_amostra_cov_e_cov_gz_reprova(tmp_path):
    p = tmp_path / "cov"
    shutil.copytree(DEMO, p)
    origem = p / "FILTERED_H82_HF_ERS4410317_CD4.bismark.cov.gz"
    (p / "FILTERED_H82_HF_ERS4410317_CD4.cov").write_bytes(gzip.decompress(origem.read_bytes()))
    _reprova(formatos.coverages_e_amostras(p, _coldata_demo(tmp_path)), "mesma amostra")


def test_coverages_e_amostras_x_X_y_z_reprova_antes_do_plano(tmp_path):
    """Crítica M10: x.cov.gz e X.bismark.cov.gz passam na listagem (D8) e no coldata (igualdade
    exata), mas no NTFS HF__x e HF__X se sobrescreveriam."""
    corpo = _gz(_cov())
    p = _pasta(tmp_path, {"x.cov.gz": corpo, "X.bismark.cov.gz": corpo, "y.cov.gz": corpo, "z.cov.gz": corpo})
    cd = _texto(tmp_path, "cd.tsv", "amostra\tcondicao\nx\tHF\nX\tHF\ny\tN\nz\tN\n")
    assert formatos.validar("coldata", cd).ok and formatos.validar("coverages_bismark", p).ok
    _reprova(formatos.coverages_e_amostras(p, cd), "só diferem na caixa")


def test_coverages_e_amostras_nome_final_longo_reprova(tmp_path):
    longo = "a" * 120
    corpo = _gz(_cov())
    p = _pasta(tmp_path, {f"{longo}.cov.gz": corpo, "b.cov.gz": corpo, "c.cov.gz": corpo, "d.cov.gz": corpo})
    cd = _texto(tmp_path, "cd.tsv", f"amostra\tcondicao\n{longo}\tHF\nb\tHF\nc\tN\nd\tN\n")
    _reprova(formatos.coverages_e_amostras(p, cd), "fora do padrão de membro")


def test_coverages_e_amostras_arquivo_sem_amostra_so_no_perfil(tmp_path):
    p = tmp_path / "cov"
    shutil.copytree(DEMO, p)
    (p / "extra.cov.gz").write_bytes(_gz(_cov()))
    v = formatos.coverages_e_amostras(p, _coldata_demo(tmp_path))
    assert v.ok and v.perfil["n_arquivos_sem_amostra"] == 1 and v.perfil["arquivos_sem_amostra"] == ["extra.cov.gz"]


def test_coverages_e_painel_controle_e_canario_chr(tmp_path):
    v = formatos.coverages_e_painel(DEMO, PAINEL)
    assert v.ok and v.perfil["n_cromossomos_comuns"] == 27, v.falhas
    texto = PAINEL.read_text(encoding="utf-8")
    dados = json.loads(texto)
    for g in dados["genes"].values():
        g["cromossomo"] = f"chr{g['cromossomo']}"
    _reprova(formatos.coverages_e_painel(DEMO, _texto(tmp_path, "chr.json", json.dumps(dados))),
             "nenhum cromossomo em comum")


def test_coverages_contigs_ars_ucd_controle_e_canarios(tmp_path):
    v = formatos.coverages_contigs_ars_ucd(DEMO)
    assert v.ok and v.perfil["n_cromossomos"] == 27, v.falhas
    p = _pasta(tmp_path, {"a.cov.gz": _gz(_cov(3, "chr1") + _cov(3, "chrX"))})
    _reprova(formatos.coverages_contigs_ars_ucd(p), "prefixo 'chr'")
    q = _pasta(tmp_path, {"a.cov.gz": _gz(_cov(3, "scaffold_1"))}, nome="sc")
    _reprova(formatos.coverages_contigs_ars_ucd(q), "fora da nomenclatura ARS-UCD")
    m = _pasta(tmp_path, {"a.cov.gz": _gz(_cov(3, "1") + _cov(3, "MT"))}, nome="mt")
    v = formatos.coverages_contigs_ars_ucd(m)
    assert v.ok and v.perfil["avisos"] and v.perfil["avisos"][0].startswith("MT:")
    _reprova(formatos.coverages_contigs_ars_ucd(_pasta(tmp_path, {"README.txt": b"x"}, nome="vazia")),
             "coverages: pasta sem membros")


def test_coverages_contigs_w15_reprova():
    _reprova(formatos.coverages_contigs_ars_ucd(_exige(W15)), "prefixo 'chr'")


def test_checagens_nunca_levantam(tmp_path, monkeypatch):
    def quebra(*_a, **_k):
        raise IndexError("simulado")
    monkeypatch.setattr(formatos, "_pares_tabela", quebra)
    _reprova(formatos.coverages_e_amostras(DEMO, _coldata_demo(tmp_path)), "IndexError")
    with pytest.raises(IndexError):
        formatos.coverages_e_amostras.__wrapped__(DEMO, _coldata_demo(tmp_path, nome="b.tsv"))


# ========================================================== json_finito (X2) e CHAVE_LINHA
@pytest.mark.parametrize("texto", ['{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', '{"a": 1e999}',
                                   '{"a": [1, {"b": NaN}]}'])
def test_resumo_com_numero_nao_finito_reprova(tmp_path, texto):
    p = _texto(tmp_path, "r.json", texto)
    _reprova(formatos.validar("resumo_json", p), "não finito")
    dados = formatos.json_finito(texto, nao_finito="none")  # o runs._ler_resumo: None no lugar
    assert "null" in json.dumps(dados, allow_nan=False)  # o JSONResponse (allow_nan=False) transporta
    with pytest.raises(formatos.NaoFinito):
        formatos.json_finito(texto)


def test_resumo_finito_continua_passando(tmp_path):
    p = _texto(tmp_path, "r.json", '{"a": 1.5, "b": [1, 2e-5], "c": null, "d": "NaN"}')
    v = formatos.validar("resumo_json", p)
    assert v.ok and v.perfil["chaves"] == ["a", "b", "c", "d"]
    assert formatos.json_finito('{"a": 1e308}') == {"a": 1e308}
    assert math.isfinite(formatos.json_finito('{"a": -1.5e-300}')["a"])
    with pytest.raises(ValueError):
        formatos.json_finito("{}", nao_finito="talvez")


def test_chave_linha_em_sincronia_com_colunas_garantidas():
    assert set(formatos.CHAVE_LINHA) <= set(formatos.COLUNAS_GARANTIDAS)
    for f, chave in formatos.CHAVE_LINHA.items():
        assert chave and set(chave) <= set(formatos.COLUNAS_GARANTIDAS[f]), f
    assert formatos.CHAVE_LINHA["dmc_methylkit"] == formatos.CHAVE_LINHA["cpgs_methylkit"] == ("chr", "start")


# ===================================================================== sincronia (§2.1)
def test_tabelas_da_f6_em_sincronia():
    dirs = set(formatos.DIRETORIOS)
    assert dirs <= set(formatos.VALIDADORES)
    assert set(formatos.VALIDADORES) - dirs <= set(formatos.EXTENSOES)
    assert not dirs & set(formatos.EXTENSOES)
    assert not dirs & formatos.REFERENCIAVEIS
    assert not dirs & set(adaptador.ALVOS)
    assert not dirs & set(formatos.IRMAOS) and not dirs & set(formatos.SUFIXOS)
    for f, chave in formatos.COLUNAS_CHAVE.items():
        assert set(chave) <= set(formatos.COLUNAS_GARANTIDAS[f]), f
    for f, niveis in formatos.COLUNAS_DE_NIVEL.items():
        assert set(niveis) <= set(formatos.COLUNAS_GARANTIDAS[f]), f
    assert set(formatos.CHECAGENS) == set(formatos.FORMATOS_CHECAGEM)
    for tipo, (_, campos) in formatos.CHECAGENS.items():
        assert set(campos) == set(formatos.FORMATOS_CHECAGEM[tipo]), tipo
        assert set(formatos.FORMATOS_CHECAGEM[tipo].values()) <= set(formatos.VALIDADORES), tipo
    assert formatos.FORMATOS_CHECAGEM["coverages_contigs_ars_ucd"] == {"coverages": "coverages_bismark"}
    assert formatos.COLUNAS_GARANTIDAS["dmc_methylkit"] == formatos._CAB_METHYLKIT


_TSV_NOVOS = {
    "dmc_methylkit": CAB_MK + '\n1\t100\t100\t*\t0.1\t0.04\t"20"\n',
    "cpgs_methylkit": CAB_MK + '\n1\t100\t100\t*\t0.1\t0.04\t"20"\n',
    "tabela_intersecao_painel": 'GENE\tN_DMC\tHIPER\tHIPO\n"A"\t1\t1\t0\n',
    "tabela_motifs_homer": 'MOTIF\tP_VALUE\tQ_VALUE\n"A"\t0.1\t0.1\n',
    "view_mofa": 'feature\ts1\ts2\ts3\ts4\n"A"\t1\t1\t1\t1\n',
    "tabela_variancia_mofa": 'fator\tpromotor\tcorpo\nFactor1\t1\t1\n"Factor2"\t1\t1\n',
    "fatores_mofa": 'amostra\tcondicao\tFactor1\tFactor2\n' + "".join(f's{i}\t"A"\t1\t1\n' for i in range(4)),
}


@pytest.mark.parametrize("formato", sorted(_TSV_NOVOS))
def test_tsv_novos_reprovam_aspas(tmp_path, formato):
    """O portão relê as tabelas com o csv.reader padrão (fatos.ler_tsv): com aspas os dois
    leriam coisas diferentes. Todo validador de TSV novo lê com _abrir e reprova aspas."""
    _reprova(formatos.validar(formato, _texto(tmp_path, "t.tsv", _TSV_NOVOS[formato])), "aspas")


def test_dados_reais_da_pasta_nao_mudam_com_a_validacao(tmp_path):
    """A validação só lê: o sha da árvore do demo é o mesmo antes e depois (e igual ao da cópia)."""
    regra = formatos.DIRETORIOS["coverages_bismark"]
    antes = diretorio.sha_diretorio(DEMO, regra, 1 << 30)["sha256"]
    assert formatos.validar("coverages_bismark", DEMO).ok
    assert diretorio.sha_diretorio(DEMO, regra, 1 << 30)["sha256"] == antes
