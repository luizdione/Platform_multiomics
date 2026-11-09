"""montar_coverages.py (F6, ESPEC §3.2.2 + adendo v2 §A9.2): o passo `montar` dos pipelines
epigenomica_dmc e epigenomica_mofa.

O script roda no worker methyl, que não importa o pacote omica: as regras de
omica/contrato/diretorio.py e formatos.py estão REPETIDAS nele. Aqui:
  - sincronia: constantes, classificar, stem_coverage, nome_por_condicao, o laço dos membros
    gzip (bateria de bytes, comparando o veredito do validador com o do montar), os tetos
    (por membro e agregados, com monkeypatch nos dois lados) e a razão agregada (a bomba
    partida em 200 membros abaixo de 1 MiB; controles: o demo e o W15);
  - controle: o demo (6 nomes HF__/Nelore__, tamanhos e bytes iguais aos da origem, código 0,
    a porta passa no validador coverages_por_condicao, o resumo só com contagens);
  - canários: cada recusa sai com código ≠ 0, a substring do motivo e NENHUMA `coverages/`
    criada — tudo é conferido antes da primeira escrita.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from omica.config import REPO_ROOT
from omica.contrato import diretorio, formatos

SCRIPTS = REPO_ROOT / "modules" / "epigenomica" / "scripts"
SCRIPT = SCRIPTS / "montar_coverages.py"
DEMO = REPO_ROOT / "modules" / "epigenomica" / "demo_data"
CD4 = REPO_ROOT / "modules" / "epigenomica" / "data" / "amostras_cd4_demo.tsv"
W15 = Path(r"E:\data\epigenomas\NCBI_GSE161113\W15_2026")   # só leitura; skip sem o E:
CHAVES_RESUMO = {"gerado_em", "n_amostras", "n_condicoes", "por_condicao", "condicao_y0", "condicao_y1", "n_y0",
                 "n_y1", "n_arquivos_sem_amostra", "n_recomprimidos", "bytes"}


def carregar(caminho: Path = SCRIPT):
    """Importa o script como o job o roda (a pasta scripts/ no sys.path, por causa do
    `from _comum import`), sem deixar o `_comum` da epigenômica no sys.modules."""
    antes = sys.modules.get("_comum")
    sys.path.insert(0, str(caminho.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"t_{caminho.stem}", caminho)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)
        if antes is None:
            sys.modules.pop("_comum", None)
        else:
            sys.modules["_comum"] = antes


@pytest.fixture(scope="module")
def m():
    return carregar()


@pytest.fixture(autouse=True)
def _cache_limpo():
    formatos._validar_pasta_cache.cache_clear()
    yield
    formatos._validar_pasta_cache.cache_clear()


def rodar(coverages: Path, amostras: Path, saida: Path, script: Path = SCRIPT) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), "--coverages", str(coverages), "--amostras", str(amostras),
                           "--saida", str(saida)], capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT,
                          env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _linha(crom: str = "1", pos: int = 100, mm: int = 3, u: int = 1) -> str:
    return f"{crom}\t{pos}\t{pos}\t{100 * mm / (mm + u)!r}\t{mm}\t{u}\n"


def _cov(n: int = 5, crom: str = "1", inicio: int = 100) -> str:
    return "".join(_linha(crom, inicio + 10 * i, 1 + i % 3, 2) for i in range(n))


def _gz(texto: str) -> bytes:
    return gzip.compress(texto.encode("ascii"), mtime=0)


def _pasta(base: Path, membros: dict[str, bytes], nome: str = "cov") -> Path:
    p = base / nome
    p.mkdir()
    for n, b in membros.items():
        (p / n).write_bytes(b)
    return p


def _tabela(base: Path, pares: list[tuple[str, str]], nome: str = "amostras.tsv") -> Path:
    arq = base / nome
    arq.write_text("amostra\tcondicao\n" + "".join(f"{a}\t{c}\n" for a, c in pares), encoding="utf-8")
    return arq


def _quatro(base: Path, nomes=("a1", "a2", "b1", "b2"), conds=("A", "A", "B", "B")) -> tuple[Path, Path]:
    pasta = _pasta(base, {f"{n}.cov.gz": _gz(_cov(4, inicio=100 + 7 * i)) for i, n in enumerate(nomes)})
    return pasta, _tabela(base, list(zip(nomes, conds)))


# ============================================================ sincronia com diretorio/formatos
def test_sincronia_constantes(m):
    assert (m.NOME_MEMBRO.pattern, m.NOME_MEMBRO.flags) == (diretorio.NOME_MEMBRO.pattern, diretorio.NOME_MEMBRO.flags)
    assert (m.MAX_MEMBROS, m.MAX_VARRIDAS) == (diretorio.MAX_MEMBROS, diretorio.MAX_VARRIDAS)
    assert m.MAX_RAZAO_GZIP == diretorio.MAX_RAZAO_GZIP
    assert m.MAX_LINHAS_PASTA == diretorio.MAX_LINHAS_PASTA
    assert m.MAX_DESCOMPRIMIDO_PASTA == diretorio.MAX_DESCOMPRIMIDO_PASTA
    assert m.RECUSA_HARDLINK == diretorio.RECUSA_HARDLINK
    for formato in ("coverages_bismark", "coverages_por_condicao"):
        assert formatos.DIRETORIOS[formato] == {"leitura": m.LEITURA, "fixos": None}
    assert m.MAX_LINHAS_COV == formatos._MAX_LINHAS_COV
    assert m.MAX_DESCOMPRIMIDO_COV == formatos._MAX_DESCOMPRIMIDO_COV
    assert m.MIN_DESCOMPRIMIDO_RAZAO == formatos._MIN_DESCOMPRIMIDO_RAZAO
    assert m.GZ_MAGIC == formatos._GZ_MAGIC and m.BLOCO_GZ == formatos._BLOCO_GZ
    assert m.SUFIXOS_COV == formatos._SUFIXOS_COV and m.SUFIXO_POR_CONDICAO == formatos._SUFIXO_POR_CONDICAO


NOMES = ["x.cov", "x.cov.gz", "x.bismark.cov", "x.bismark.cov.gz", "X.COV", "x.Cov.gz", "x.cov.GZ", "x.cov.gz.bak",
         "x.covx", "README.txt", "x.cov.gzip", ".x.cov.gz", "cov", ".cov", "a.b.cov", "x.bismark.cov.tar",
         "HF__FILTERED_H82_HF_ERS4410317_CD4.bismark.cov.gz", "x.bed", "x.cov.gz\n", "x.c0v", "x.cov.Gz"]


@pytest.mark.parametrize("nome", NOMES)
def test_sincronia_classificar_stem_e_nome_final(m, nome):
    assert m.classificar(nome) == diretorio.classificar(nome, formatos.DIRETORIOS["coverages_bismark"])
    assert m.stem_coverage(nome) == formatos.stem_coverage(nome)
    assert m.nome_por_condicao("HF", nome) == formatos.nome_por_condicao("HF", nome)


def _bomba() -> bytes:
    linha = _linha("1", 100).encode("ascii")
    membro = gzip.compress(linha * 1_000_000, mtime=0)
    return membro * 3


# (nome do membro, bytes — gerados na hora: a bomba tem ~70 MB descomprimidos —, motivo ou None)
BATERIA = {
    "ok": ("a.cov.gz", lambda: _gz(_cov(5)), None),
    "ok_ok2": ("a.cov.gz", lambda: _gz(_cov(5)) + _gz(_cov(3, inicio=900)), None),
    "crlf": ("a.cov.gz", lambda: _gz(_cov(4).replace("\n", "\r\n")), None),
    "sem_quebra_final": ("a.cov.gz", lambda: _gz(_cov(4)[:-1]), None),
    "cov_texto": ("a.cov", lambda: _cov(6).encode("ascii"), None),
    "ok_lixo": ("a.cov.gz", lambda: _gz(_cov(5)) + b"lixo", "lixo depois do gzip"),
    "truncado": ("a.cov.gz", lambda: _gz(_cov(50))[:-12], "truncado"),
    "nao_gzip": ("a.cov.gz", lambda: _cov(3).encode("ascii"), "não é gzip"),
    "vazio": ("a.cov.gz", lambda: b"", "vazio"),
    "cov_com_gzip": ("a.cov", lambda: _gz(_cov(3)), "conteúdo gzip"),
    "bomba": ("a.cov.gz", _bomba, "razão de compressão"),
}


@pytest.mark.parametrize("caso", sorted(BATERIA))
def test_sincronia_laco_gzip_com_o_validador(m, tmp_path, caso):
    """O mesmo veredito (e o mesmo motivo) do validador da entrada sobre o mesmo membro; quando
    aprova, as mesmas linhas e os mesmos bytes descomprimidos."""
    nome, gerar, motivo = BATERIA[caso]
    dados = gerar()
    v = formatos.validar("coverages_bismark", _pasta(tmp_path, {nome: dados}))
    total = m.Pasta()
    try:
        m.ler_membro(io.BytesIO(dados), nome, total)
        erro = None
    except m.Recusa as e:
        erro = str(e)
    if motivo is None:
        assert v.ok, v.falhas
        assert erro is None, erro
        assert (total.linhas, total.descomprimido) == (v.perfil["n_linhas"], v.perfil["descomprimido"])
    else:
        assert not v.ok and any(motivo in f for f in v.falhas), v.falhas
        assert erro is not None and motivo in erro, erro


def test_sincronia_linhas_e_bytes_do_demo(m):
    v = formatos.validar("coverages_bismark", DEMO)
    assert v.ok and v.perfil["n_linhas"] == 77_358
    total = m.Pasta()
    for nome in sorted(x.name for x in DEMO.iterdir()):
        with open(DEMO / nome, "rb") as f:
            m.ler_membro(f, nome, total)
    assert (total.linhas, total.descomprimido) == (v.perfil["n_linhas"], v.perfil["descomprimido"])


@pytest.mark.parametrize("constante, valor, trecho", [
    ("MAX_LINHAS_PASTA", 50_000, "linhas somando os membros"),
    ("MAX_DESCOMPRIMIDO_PASTA", 1 << 20, "bytes descomprimidos somando os membros"),
], ids=["linhas_pasta", "bytes_pasta"])
def test_sincronia_tetos_agregados_no_demo(m, tmp_path, monkeypatch, constante, valor, trecho):
    """Adendo §A6.2: com o teto agregado reduzido, o demo (77.358 linhas, 6 membros) reprova
    no validador E no montar — que sai com código ≠ 0 sem criar coverages/. Controle: os tetos
    reais (test_controle_demo)."""
    monkeypatch.setattr(diretorio, constante, valor)
    monkeypatch.setattr(m, constante, valor)
    v = formatos.validar("coverages_bismark", DEMO)
    assert not v.ok and any(trecho in f for f in v.falhas) and "teto" in v.perfil, v.falhas
    saida = tmp_path / "s"
    monkeypatch.setattr(sys, "argv", ["montar_coverages.py", "--coverages", str(DEMO), "--amostras", str(CD4),
                                      "--saida", str(saida)])
    with pytest.raises(SystemExit) as e:
        m.main()
    assert trecho in str(e.value.code) and not (saida / "coverages").exists()


_LINHA_BOMBA = b"1\t10\t10\t100\t1\t0\n"   # 16 B, válida


def _texto_bomba() -> bytes:
    """(1 MiB − 64 B) de linhas válidas idênticas: abaixo de 1 MiB, onde a razão por membro não
    vale; ~2 KB de gzip."""
    return _LINHA_BOMBA * (((1 << 20) - 64) // len(_LINHA_BOMBA))


def _ler_todos(m, pasta: Path):
    total = m.Pasta()
    for nome in sorted(x.name for x in pasta.iterdir()):
        with open(pasta / nome, "rb") as f:
            m.ler_membro(f, nome, total)
    return total


def test_sincronia_razao_agregada_com_o_validador(m, tmp_path):
    """Achado gzip_agregado: 200 membros .cov.gz de (1 MiB − 64 B), ~500:1 somados e nenhum
    acima de 1 MiB. O validador e o montar reprovam pela razão SOMANDO os membros — e o montar
    para no 2º membro, sem ler os 209,7 MB."""
    um = gzip.compress(_texto_bomba(), mtime=0)
    pasta = _pasta(tmp_path, {f"s{i:03d}.cov.gz": um for i in range(200)})
    v = formatos.validar("coverages_bismark", pasta)
    assert not v.ok and any(f"razão de compressão > {diretorio.MAX_RAZAO_GZIP}:1 somando os membros" in f
                            for f in v.falhas), v.falhas
    total = m.Pasta()
    with pytest.raises(m.Recusa) as e:
        for i in range(200):
            m.ler_membro(io.BytesIO(um), f"s{i:03d}.cov.gz", total)
    assert f"razão de compressão > {m.MAX_RAZAO_GZIP}:1 somando os membros" in str(e.value), e.value
    assert total.descomprimido < 3 << 20


@pytest.mark.parametrize("pasta", [DEMO, W15], ids=["demo", "w15"])
def test_sincronia_razao_agregada_nos_controles_reais(m, pasta):
    """Controle: o demo e o W15 (só leitura) passam nos dois lados com a regra ARMADA (Σ > 1 MiB),
    e o gzip que a cópia grava é o comprimido que o validador soma (só .cov.gz: Σ dos arquivos)."""
    if not pasta.exists():
        pytest.skip(f"{pasta.name} ausente (dado real fora do repositório)")
    v = formatos.validar("coverages_bismark", pasta)
    assert v.ok, v.falhas
    total = _ler_todos(m, pasta)
    assert (total.gravado, total.descomprimido) == (v.perfil["bytes"], v.perfil["descomprimido"])
    assert m.MIN_DESCOMPRIMIDO_RAZAO < total.descomprimido < 6 * total.gravado


def test_sincronia_teto_por_membro(m, tmp_path, monkeypatch):
    monkeypatch.setattr(formatos, "_MAX_LINHAS_COV", 1000)
    monkeypatch.setattr(m, "MAX_LINHAS_COV", 1000)
    v = formatos.validar("coverages_bismark", DEMO)
    assert not v.ok and any("linhas num membro" in f for f in v.falhas)
    saida = tmp_path / "s"
    monkeypatch.setattr(sys, "argv", ["montar_coverages.py", "--coverages", str(DEMO), "--amostras", str(CD4),
                                      "--saida", str(saida)])
    with pytest.raises(SystemExit) as e:
        m.main()
    assert "linhas num membro" in str(e.value.code) and not (saida / "coverages").exists()


# ============================================================ controle: o demo
def test_controle_demo(tmp_path):
    saida = tmp_path / "montar"
    r = rodar(DEMO, CD4, saida)
    assert r.returncode == 0, r.stderr
    cov = saida / "coverages"
    nomes = sorted(x.name for x in cov.iterdir())
    origem = sorted(x.name for x in DEMO.iterdir())
    assert nomes == sorted(f"{x.split('_')[2]}__{x}" for x in origem)  # FILTERED_H82_<grupo>_...
    for o in origem:
        destino = cov / f"{o.split('_')[2]}__{o}"
        assert destino.stat().st_size == (DEMO / o).stat().st_size and _sha(destino) == _sha(DEMO / o)
    resumo = json.loads((saida / "montar_resumo.json").read_text(encoding="utf-8"))
    assert set(resumo) == CHAVES_RESUMO
    assert {k: v for k, v in resumo.items() if k != "gerado_em"} == {
        "n_amostras": 6, "n_condicoes": 2, "por_condicao": {"HF": 3, "Nelore": 3}, "condicao_y0": "HF",
        "condicao_y1": "Nelore", "n_y0": 3, "n_y1": 3, "n_arquivos_sem_amostra": 0, "n_recomprimidos": 0,
        "bytes": 439_723}
    # a porta passa no validador da porta (o mesmo que o envelope roda)
    v = formatos.validar("coverages_por_condicao", cov)
    assert v.ok, v.falhas
    assert v.perfil["por_condicao"] == {"HF": 3, "Nelore": 3} and v.perfil["n_linhas"] == 77_358


def test_resumo_sem_nome_de_arquivo_nem_caminho(tmp_path):
    """Crítica L9: o resumo vira fato e vai ao redator externo. Com um arquivo FORA da tabela,
    ele aparece só como contagem; o nome fica no log do job."""
    pasta, amostras = _quatro(tmp_path, nomes=("a1", "a2", "b1", "b2"))
    (pasta / "sobra_x.cov.gz").write_bytes(_gz(_cov(3)))
    r = rodar(pasta, amostras, tmp_path / "s")
    assert r.returncode == 0, r.stderr
    texto = (tmp_path / "s" / "montar_resumo.json").read_text(encoding="utf-8")
    resumo = json.loads(texto)
    assert resumo["n_arquivos_sem_amostra"] == 1 and "sobra_x" not in texto
    assert not [v for v in resumo.values() if isinstance(v, str) and (v.endswith((".cov", ".cov.gz")) or "/" in v
                                                                    or "\\" in v)]
    assert "sobra_x.cov.gz" in r.stdout  # só no log


def test_segunda_tentativa_limpa_a_subpasta(tmp_path):
    """Um membro de tentativa anterior reprovaria a porta: o montar apaga SÓ <saida>/coverages."""
    pasta, amostras = _quatro(tmp_path)
    saida = tmp_path / "s"
    (saida / "coverages").mkdir(parents=True)
    (saida / "coverages" / "Z__velho.bismark.cov.gz").write_bytes(b"x")
    (saida / "outro.txt").write_text("fica", encoding="utf-8")
    r = rodar(pasta, amostras, saida)
    assert r.returncode == 0, r.stderr
    assert sorted(x.name for x in (saida / "coverages").iterdir()) == [
        "A__a1.bismark.cov.gz", "A__a2.bismark.cov.gz", "B__b1.bismark.cov.gz", "B__b2.bismark.cov.gz"]
    assert (saida / "outro.txt").read_text(encoding="utf-8") == "fica"


def test_cov_recomprimido_deterministico(tmp_path):
    """.cov vira gzip com mtime 0 e sem nome no cabeçalho: duas execuções, os mesmos bytes; o
    conteúdo é o da origem; a porta passa."""
    pares = [("a1", "A"), ("a2", "A"), ("b1", "B"), ("b2", "B")]
    textos = {a: _cov(5, inicio=100 + 11 * i) for i, (a, _) in enumerate(pares)}
    pasta = _pasta(tmp_path, {f"{a}.cov": t.encode("ascii") for a, t in textos.items()})
    amostras = _tabela(tmp_path, pares)
    shas = []
    for n in (1, 2):
        r = rodar(pasta, amostras, tmp_path / f"s{n}")
        assert r.returncode == 0, r.stderr
        cov = tmp_path / f"s{n}" / "coverages"
        shas.append({x.name: _sha(x) for x in cov.iterdir()})
        resumo = json.loads((tmp_path / f"s{n}" / "montar_resumo.json").read_text(encoding="utf-8"))
        assert resumo["n_recomprimidos"] == 4
    assert shas[0] == shas[1]
    cov = tmp_path / "s1" / "coverages"
    for a, c in pares:
        bruto = (cov / f"{c}__{a}.bismark.cov.gz").read_bytes()
        # cabeçalho gzip: FLG = 0 (sem nome de arquivo) e MTIME = 0 — os bytes não dependem
        # da hora nem do caminho (duas execuções no mesmo segundo não provariam isso)
        assert bruto[3:8] == b"\x00" * 5, bruto[:10]
        assert gzip.decompress(bruto).decode("ascii") == textos[a]
    assert formatos.validar("coverages_por_condicao", cov).ok


# ============================================================ canários (código ≠ 0, nada escrito)
def _reprova(r: subprocess.CompletedProcess, saida: Path, trecho: str) -> None:
    assert r.returncode != 0, r.stdout
    assert trecho in r.stderr, r.stderr
    assert not (saida / "coverages").exists()


def _demo_renomeado(tmp_path: Path, conds: list[str]) -> tuple[Path, Path]:
    linhas = CD4.read_text(encoding="utf-8").splitlines()[1:]
    pares = [(x.split("\t")[0], c) for x, c in zip(linhas, conds)]
    return DEMO, _tabela(tmp_path, pares)


@pytest.mark.parametrize("conds", [["cov"] * 3 + ["Nelore"] * 3, ["Gir"] * 3 + ["Girolando"] * 3,
                                   ["HF"] * 3 + ["H82"] * 3],
                         ids=["cov", "gir_girolando", "nivel_no_nome_da_amostra"])
def test_canario_disjuncao_por_substring(tmp_path, conds):
    """ESPEC §5.1: condição 'cov' casa com todo nome final; 'Gir' casa com 'Girolando__…';
    'H82' está no nome das amostras HF. O R e o perfil_tss juntariam os grupos."""
    pasta, amostras = _demo_renomeado(tmp_path, conds)
    _reprova(rodar(pasta, amostras, tmp_path / "s"), tmp_path / "s", "casa por SUBSTRING")


def test_canario_x_X_y_z(tmp_path):
    """Crítica M10 (adendo §A6.2): x.cov.gz e X.bismark.cov.gz passam na listagem (nomes
    diferentes sem caixa), mas os stems x e X — e os nomes finais HF__x/HF__X — sobrescreveriam
    um ao outro no NTFS."""
    pasta = _pasta(tmp_path, {"x.cov.gz": _gz(_cov(3)), "X.bismark.cov.gz": _gz(_cov(3, inicio=500)),
                              "y.cov.gz": _gz(_cov(3, inicio=900)), "z.cov.gz": _gz(_cov(3, inicio=1300))})
    amostras = _tabela(tmp_path, [("x", "HF"), ("X", "HF"), ("y", "N"), ("z", "N")])
    assert formatos.validar("coverages_bismark", pasta).ok   # a pasta em si passa (D8)
    _reprova(rodar(pasta, amostras, tmp_path / "s"), tmp_path / "s", "só diferem na caixa")


@pytest.mark.parametrize("membros, pares, trecho", [
    ({"a1.cov.gz": 1, "a2.cov.gz": 2, "b1.cov.gz": 3},
     [("a1", "A"), ("a2", "A"), ("b1", "B"), ("b2", "B")], "sem arquivo na pasta"),
    ({"a1.cov.gz": 1, "a1.cov": 1, "a2.cov.gz": 2, "b1.cov.gz": 3, "b2.cov.gz": 4},
     [("a1", "A"), ("a2", "A"), ("b1", "B"), ("b2", "B")], "dois arquivos da mesma amostra"),
    ({"a1.cov.gz": 1, "a2.cov.gz": 2, "b1.cov.gz": 3, "b2.cov.gz": 4},
     [("a1", "A__x"), ("a2", "A__x"), ("b1", "B"), ("b2", "B")], "'__'"),
    ({"a" * 120 + ".cov.gz": 1, "a2.cov.gz": 2, "b1.cov.gz": 3, "b2.cov.gz": 4},
     [("a" * 120, "Alongado"), ("a2", "Alongado"), ("b1", "B"), ("b2", "B")], "fora do padrão de membro"),
    ({".a1.cov.gz": 1, "a1.cov.gz": 1, "a2.cov.gz": 2, "b1.cov.gz": 3, "b2.cov.gz": 4},
     [("a1", "A"), ("a2", "A"), ("b1", "B"), ("b2", "B")], "oculto"),
    ({"a1.COV.gz": 1, "a1.cov.gz": 1, "a2.cov.gz": 2, "b1.cov.gz": 3, "b2.cov.gz": 4},
     [("a1", "A"), ("a2", "A"), ("b1", "B"), ("b2", "B")], "ignorando a caixa"),
    ({"a 1.cov.gz": 1, "a1.cov.gz": 1, "a2.cov.gz": 2, "b1.cov.gz": 3, "b2.cov.gz": 4},
     [("a1", "A"), ("a2", "A"), ("b1", "B"), ("b2", "B")], "nome fora do padrão"),
], ids=["amostra_sem_arquivo", "cov_e_cov_gz", "condicao_com_sublinhado_duplo", "nome_final_longo",
        "membro_oculto", "so_casa_sem_caixa", "nome_com_espaco"])
def test_canarios_de_nome(tmp_path, membros, pares, trecho):
    pasta = _pasta(tmp_path, {n: _gz(_cov(3, inicio=100 * i)) for n, i in membros.items()})
    _reprova(rodar(pasta, _tabela(tmp_path, pares), tmp_path / "s"), tmp_path / "s", trecho)


@pytest.mark.parametrize("caso", ["ok_lixo", "truncado", "bomba"])
def test_canarios_de_gzip_antes_da_escrita(tmp_path, caso):
    """O conteúdo ruim está no ÚLTIMO membro na ordem do nome final: os anteriores passam, e
    mesmo assim nada é escrito (a leitura inteira vem antes da cópia)."""
    nome, gerar, trecho = BATERIA[caso]
    pasta, amostras = _quatro(tmp_path)
    (pasta / "b2.cov.gz").write_bytes(gerar())
    _reprova(rodar(pasta, amostras, tmp_path / "s"), tmp_path / "s", trecho)


def test_canario_cov_que_comprimido_vira_bomba(tmp_path):
    """Um .cov de texto é recomprimido: a razão vale sobre o gzip que a cópia gravaria (a porta
    reprovaria). Controle: o .cov comum de test_cov_recomprimido_deterministico."""
    pasta, amostras = _quatro(tmp_path)
    (pasta / "b2.cov.gz").unlink()
    (pasta / "b2.cov").write_bytes(_linha("1", 100).encode("ascii") * 200_000)
    _reprova(rodar(pasta, amostras, tmp_path / "s"), tmp_path / "s", "gzip da cópia teria razão")


def test_canario_bomba_partida_em_200_membros(tmp_path):
    """Achado gzip_agregado, pelo job: os 200 membros .cov.gz de (1 MiB − 64 B) na tabela de
    amostras saem com código ≠ 0 pela razão agregada e nada é escrito."""
    um = gzip.compress(_texto_bomba(), mtime=0)
    nomes = [f"s{i:03d}" for i in range(200)]
    pasta = _pasta(tmp_path, {f"{n}.cov.gz": um for n in nomes})
    amostras = _tabela(tmp_path, [(n, "A" if i < 100 else "B") for i, n in enumerate(nomes)])
    _reprova(rodar(pasta, amostras, tmp_path / "s"), tmp_path / "s",
             f"razão de compressão > {diretorio.MAX_RAZAO_GZIP}:1 somando os membros da pasta")


def test_canario_covs_que_recomprimidos_viram_bomba_partida(tmp_path):
    """Três .cov de texto de (1 MiB − 64 B): a entrada passa (o .cov é lido 1:1 e fica fora da
    soma do validador), mas a cópia gravaria três gzip a ~500:1 que a PORTA reprovaria pela razão
    agregada. O montar soma o gzip recomprimido e recusa antes de escrever. Controle: os .cov de
    test_cov_recomprimido_deterministico."""
    texto = _texto_bomba()
    pasta = _pasta(tmp_path, {f"{a}.cov": texto for a in ("a1", "a2", "b1")})
    assert formatos.validar("coverages_bismark", pasta).ok
    porta = _pasta(tmp_path, {f"{c}__{a}.bismark.cov.gz": gzip.compress(texto, mtime=0)
                              for a, c in (("a1", "A"), ("a2", "A"), ("b1", "B"))}, "porta")
    v = formatos.validar("coverages_por_condicao", porta)
    assert not v.ok and any("somando os membros .cov.gz" in f for f in v.falhas), v.falhas
    amostras = _tabela(tmp_path, [("a1", "A"), ("a2", "A"), ("b1", "B")])
    _reprova(rodar(pasta, amostras, tmp_path / "s"), tmp_path / "s",
             "somando os membros da pasta (o gzip que a cópia grava)")


def test_canario_hardlink(tmp_path):
    pasta, amostras = _quatro(tmp_path)
    fora = tmp_path / "fora.cov.gz"
    fora.write_bytes(_gz(_cov(3)))
    (pasta / "b2.cov.gz").unlink()
    try:
        os.link(fora, pasta / "b2.cov.gz")
    except OSError as e:
        pytest.skip(f"hardlink indisponível ({e})")
    _reprova(rodar(pasta, amostras, tmp_path / "s"), tmp_path / "s", "hardlink")


def test_canario_juncao_ou_symlink_como_membro(tmp_path):
    pasta, amostras = _quatro(tmp_path)
    alvo = tmp_path / "alvo_dir"
    alvo.mkdir()
    (pasta / "b2.cov.gz").unlink()
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(alvo), str(pasta / "b2.cov.gz"))
    else:
        os.symlink(tmp_path / "fora.cov.gz", pasta / "b2.cov.gz")
    r = rodar(pasta, amostras, tmp_path / "s")
    assert r.returncode != 0 and ("junção" in r.stderr or "symlink" in r.stderr), r.stderr
    assert not (tmp_path / "s" / "coverages").exists()


def test_canario_pasta_ausente_e_destino_que_nao_e_pasta(tmp_path):
    pasta, amostras = _quatro(tmp_path)
    _reprova(rodar(tmp_path / "nao_existe", amostras, tmp_path / "s"), tmp_path / "s", "não existe")
    saida = tmp_path / "s2"
    saida.mkdir()
    (saida / "coverages").write_text("arquivo no lugar da subpasta", encoding="utf-8")
    r = rodar(pasta, amostras, saida)
    assert r.returncode != 0 and "não é pasta comum" in r.stderr, r.stderr
    assert (saida / "coverages").is_file()   # nada apagado


def test_canario_colisao_do_sistema_de_arquivos_nao_sobrescreve(m, tmp_path, monkeypatch):
    """Cada destino é criado com 'xb'. Para ver a regra sozinha, o planejamento é trocado por
    um que deixa passar dois nomes finais que só diferem na caixa (o que as validações barram):
    num sistema de arquivos sem distinção de caixa (NTFS), o segundo falha em vez de
    sobrescrever o primeiro."""
    pasta, amostras = _quatro(tmp_path)
    if not (pasta / "A1.COV.GZ").exists():
        pytest.skip("sistema de arquivos distingue a caixa: não há colisão a provocar")
    membros = dict(m.listar_membros(pasta))
    finais = {"A__a1.bismark.cov.gz": ("A", "a1.cov.gz", membros["a1.cov.gz"].st_ino),
              "A__A1.bismark.cov.gz": ("A", "a2.cov.gz", membros["a2.cov.gz"].st_ino)}
    monkeypatch.setattr(m, "planejar", lambda p, pares: (finais, []))
    saida = tmp_path / "s"
    monkeypatch.setattr(sys, "argv", ["montar_coverages.py", "--coverages", str(pasta), "--amostras", str(amostras),
                                      "--saida", str(saida)])
    with pytest.raises(SystemExit) as e:
        m.main()
    assert "cópia interrompida" in str(e.value.code)
    copiado = list((saida / "coverages").iterdir())
    assert len(copiado) == 1 and _sha(copiado[0]) == _sha(pasta / "a2.cov.gz" if copiado[0].name == "A__A1.bismark.cov.gz"
                                                         else pasta / "a1.cov.gz")
