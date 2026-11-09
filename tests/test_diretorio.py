"""Pasta como entrada e como porta (F6): omica/contrato/diretorio.py.

Canários D1–D8 da ESPEC F6 §1.C e do adendo v2 §A5. Regra do projeto: todo portão é VISTO
reprovando pelo MOTIVO previsto (substring da mensagem) e tem um controle irmão que passa.

Host Windows × container Linux (adendo §A3.1, crítica M9/M14):
  - no host não há O_NOFOLLOW nem dir_fd; sobram o inode REAL (os.lstat/fstat, nunca o cache
    do scandir, que tem st_ino == 0), o reparse point (junção) e o hardlink — testados aqui,
    sem skip;
  - symlink e FIFO só no Linux (no host o symlink pede privilégio): skip fora do container;
  - colisão de caixa: no NTFS dois nomes que só diferem na caixa são o MESMO arquivo; no host
    a listagem é simulada (o lstat do NTFS acha o arquivo pelo outro nome), no Linux é real.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path

import pytest

from omica.config import REPO_ROOT
from omica.contrato import diretorio, formatos

EPI = REPO_ROOT / "modules" / "epigenomica"
DEMO = EPI / "demo_data"
COV = formatos.DIRETORIOS["coverages_bismark"]
WINDOWS = os.name == "nt"
MEMBRO = "FILTERED_H82_HF_ERS4410317_CD4.bismark.cov.gz"


@pytest.fixture(autouse=True)
def _cache_limpo():
    formatos._validar_pasta_cache.cache_clear()
    yield
    formatos._validar_pasta_cache.cache_clear()


@pytest.fixture()
def pasta(tmp_path: Path) -> Path:
    destino = tmp_path / "cov"
    shutil.copytree(DEMO, destino)
    return destino


def _erro(func, *args, trecho: str) -> diretorio.ErroMembros:
    with pytest.raises(diretorio.ErroMembros) as e:
        func(*args)
    assert trecho in str(e.value), str(e.value)
    return e.value


def _sha(p: Path) -> str:
    return diretorio.sha_diretorio(p, COV, 1 << 30)["sha256"]


def _junção(alvo: Path, link: Path) -> None:
    import _winapi  # só existe no Windows
    _winapi.CreateJunction(str(alvo), str(link))


def _symlink(alvo: Path, link: Path, *, pasta: bool = False) -> None:
    try:
        os.symlink(alvo, link, target_is_directory=pasta)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlink indisponível aqui ({type(e).__name__}): roda no container")


# ================================================== D1: sincronia com os leitores
NOMES_D1 = ["a.cov", "a.cov.gz", ".h.cov.gz", "x.cov.gz.cov", "a.cov.gz.bak", "a.COV.GZ", "README.txt",
            "W15_2026"]


def test_d1_classificar_e_a_uniao_do_que_o_r_e_o_python_leem():
    """A bateria da ESPEC: membro = o que o list.files do R OU os glob do Python leem (o glob lê
    oculto; o R, não — a recusa do oculto é da listagem). 'a.COV.GZ' só o glob do WINDOWS lê
    (sem caixa): recusar. README e a subpasta W15_2026: ignorar."""
    r_le = {n for n in NOMES_D1 if not n.startswith(".") and re.search(r"\.cov(\.gz)?$", n)}
    py_linux = {n for n in NOMES_D1 if fnmatch.fnmatchcase(n, "*.cov.gz") or fnmatch.fnmatchcase(n, "*.cov")}
    py_windows = {n for n in NOMES_D1 if fnmatch.fnmatchcase(n.lower(), "*.cov.gz")
                  or fnmatch.fnmatchcase(n.lower(), "*.cov")}
    tipo = {n: diretorio.classificar(n, COV) for n in NOMES_D1}
    assert {n for n, t in tipo.items() if t == "membro"} == r_le | py_linux
    assert {n for n, t in tipo.items() if t == "recusar"} == py_windows - (r_le | py_linux) == {"a.COV.GZ"}
    assert {n for n, t in tipo.items() if t == "ignorar"} == {"a.cov.gz.bak", "README.txt", "W15_2026"}


def test_d1_regra_de_leitura_e_a_dos_scripts_selados():
    """Sincronia textual: a regra de DIRETORIOS é a união do pattern do R com os glob do Python.
    Se um script selado mudar o que lê, este teste reprova antes do portão divergir."""
    r = (EPI / "scripts" / "metilacao_dmc.R").read_text(encoding="utf-8")
    assert 'pattern = "\\\\.cov(\\\\.gz)?$"' in r
    for script in ("perfil_tss.py", "mofa_preparar.py"):
        texto = (EPI / "scripts" / script).read_text(encoding="utf-8")
        assert 'glob("*.cov.gz")' in texto and 'glob("*.cov")' in texto, script
    assert formatos.DIRETORIOS["coverages_bismark"] == formatos.DIRETORIOS["coverages_por_condicao"]
    assert COV == {"leitura": r"\.cov(?:\.gz)?\Z", "fixos": None}


def test_d1_fixos_e_caixa_dos_fixos():
    regra = {"leitura": None, "fixos": ["view_promotor.tsv", "grupos.tsv"]}
    assert diretorio.classificar("view_promotor.tsv", regra) == "membro"
    assert diretorio.classificar("VIEW_PROMOTOR.tsv", regra) == "recusar"
    assert diretorio.classificar("views_resumo.json", regra) == "ignorar"


# ============================================================== D2: recusas
def test_d2_controle_readme_csv_e_subpasta_nao_mudam_o_sha(pasta):
    antes = _sha(pasta)
    (pasta / "README.txt").write_text("leia-me", encoding="utf-8")
    (pasta / "amostras.csv").write_text("a,b\n", encoding="utf-8")
    (pasta / "W15_2026").mkdir()
    (pasta / "W15_2026" / "x.cov.gz").write_bytes(b"nao e lido: so o 1o nivel")
    assert _sha(pasta) == antes
    assert [n for n, _ in diretorio.listar_membros(pasta, COV)] == sorted(p.name for p in DEMO.iterdir())


@pytest.mark.parametrize("nome,trecho", [
    (".x.cov.gz", "oculto"),
    ("com espaco.cov.gz", "fora do padrão"),
    ("-x.cov.gz", "fora do padrão"),
    ("ç.cov.gz", "fora do padrão"),
])
def test_d2_nome_recusado(pasta, nome, trecho):
    shutil.copy(pasta / MEMBRO, pasta / nome)
    e = _erro(diretorio.listar_membros, pasta, COV, trecho=trecho)
    assert not e.teto and nome not in str(e)  # nome fora de NOME_MEMBRO nunca é ecoado


def test_d2_subpasta_com_nome_de_membro_recusada(pasta):
    (pasta / "sub.cov.gz").mkdir()
    _erro(diretorio.listar_membros, pasta, COV, trecho="não é arquivo regular")


def test_d2_nome_que_so_casa_sem_caixa_recusado(pasta):
    shutil.copy(pasta / MEMBRO, pasta / "x.COV.GZ")
    _erro(diretorio.listar_membros, pasta, COV, trecho="ignorando a caixa")


def test_d2_colisao_de_caixa(pasta, monkeypatch):
    """No Linux, dois arquivos reais; no NTFS (sem distinção de caixa) é o MESMO arquivo, então
    a listagem é simulada — o lstat do NTFS acha o membro pelo outro nome."""
    if WINDOWS:
        real = os.scandir

        class _Entrada:
            def __init__(self, nome):
                self.name = nome

        class _Lista:
            def __init__(self, nomes):
                self._nomes = nomes

            def __enter__(self):
                return iter([_Entrada(n) for n in self._nomes])

            def __exit__(self, *a):
                return False

        def falso(alvo):
            with real(alvo) as it:
                nomes = [e.name for e in it]
            return _Lista(nomes + [MEMBRO.replace("FILTERED_H82_HF", "filtered_h82_hf")])
        monkeypatch.setattr(diretorio.os, "scandir", falso)
    else:
        shutil.copy(pasta / MEMBRO, pasta / MEMBRO.replace("FILTERED_H82_HF", "filtered_h82_hf"))
    _erro(diretorio.listar_membros, pasta, COV, trecho="só diferem na caixa")


@pytest.mark.parametrize("dentro", [True, False], ids=["para_dentro", "para_fora"])
def test_d2_symlink_membro_recusado(pasta, tmp_path, dentro):
    alvo = pasta / MEMBRO if dentro else tmp_path / "fora.cov.gz"
    if not dentro:
        shutil.copy(pasta / MEMBRO, alvo)
    _symlink(alvo, pasta / "link.cov.gz")
    _erro(diretorio.listar_membros, pasta, COV, trecho="symlink")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO só no Linux (container)")
def test_d2_fifo_recusado_sem_travar(pasta):
    os.mkfifo(pasta / "fila.cov.gz")
    _erro(diretorio.listar_membros, pasta, COV, trecho="não é arquivo regular")


# ================================================================ D3: tetos
def test_d3_1025_membros_dao_teto_sem_abrir_nenhum(tmp_path, monkeypatch):
    p = tmp_path / "muitos"
    p.mkdir()
    for i in range(diretorio.MAX_MEMBROS + 1):
        (p / f"m{i:04d}.cov").write_bytes(b"")
    abertos = []
    real = diretorio.os.open

    def espiao(caminho, flags, *a, **kw):
        abertos.append((str(caminho), kw.get("dir_fd")))
        return real(caminho, flags, *a, **kw)
    monkeypatch.setattr(diretorio.os, "open", espiao)
    e = _erro(diretorio.listar_membros, p, COV, trecho=f"mais de {diretorio.MAX_MEMBROS} membros")
    assert e.teto and e.limite == {"limite_membros": diretorio.MAX_MEMBROS}
    assert not [a for a in abertos if a[1] is not None or a[0].endswith(".cov")], abertos
    v = formatos.validar("coverages_bismark", p)
    assert not v.ok and v.perfil["teto"] == {"limite_membros": diretorio.MAX_MEMBROS}


def test_d3_entradas_ignoradas_contam_no_teto_da_varredura(pasta, monkeypatch):
    """O teto de entradas lidas pelo scandir conta também o que é ignorado (README, csv). O
    valor real (100 000) é trocado por 50 para o teste não criar 100 mil arquivos."""
    monkeypatch.setattr(diretorio, "MAX_VARRIDAS", 50)
    for i in range(50):
        (pasta / f"leia_{i}.txt").write_bytes(b"")
    e = _erro(diretorio.listar_membros, pasta, COV, trecho="mais de 50 entradas")
    assert e.teto and e.limite["limite_entradas"] == 50
    monkeypatch.setattr(diretorio, "MAX_VARRIDAS", 56)  # controle: 50 + 6 membros cabem
    assert len(diretorio.listar_membros(pasta, COV)) == 6


def test_d3_sha_acima_do_teto_nao_e_atestavel(pasta):
    arv = diretorio.sha_diretorio(pasta, COV, 200_000)
    assert arv["sha256"] is None and arv["bytes"] == 439_723
    assert any(m["sha256"] is None for m in arv["membros"].values())
    assert diretorio.sha_diretorio(pasta, COV, 439_723)["sha256"]  # controle: cabe exatamente


# ================================================================ D4: árvore
def test_d4_origem_e_copia_tem_a_mesma_arvore_e_o_formato_e_o_da_espec(pasta):
    origem = diretorio.sha_diretorio(DEMO, COV, 1 << 30)
    copia = diretorio.sha_diretorio(pasta, COV, 1 << 30)
    assert origem == copia
    assert copia["n_membros"] == 6 and copia["bytes"] == 439_723
    linhas = [[n, m["bytes"], m["sha256"]] for n, m in sorted(copia["membros"].items())]
    for n, m in copia["membros"].items():
        assert m["sha256"] == hashlib.sha256((pasta / n).read_bytes()).hexdigest()
    texto = json.dumps({"esquema": "omica.diretorio/1", "membros": linhas}, separators=(",", ":"), ensure_ascii=True)
    assert copia["sha256"] == hashlib.sha256(texto.encode("ascii")).hexdigest()


def test_d4_renomear_ou_trocar_um_byte_muda_e_o_mtime_nao(pasta):
    antes = _sha(pasta)
    alvo = pasta / MEMBRO
    st = alvo.stat()
    os.utime(alvo, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    assert _sha(pasta) == antes  # o mtime não entra
    b = bytearray(alvo.read_bytes())
    b[len(b) // 2] ^= 1
    alvo.write_bytes(bytes(b))
    os.utime(alvo, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert _sha(pasta) != antes
    b[len(b) // 2] ^= 1
    alvo.write_bytes(bytes(b))
    assert _sha(pasta) == antes
    alvo.rename(pasta / MEMBRO.replace("_HF_", "_Nelore_"))  # o grupo sai do nome
    assert _sha(pasta) != antes


# ===================================================== D5′: troca no meio, inode real
def test_d5_inode_real_na_listagem_sem_skip(pasta):
    """Crítica M9: no host Windows o DirEntry.stat() tem st_ino == 0 e st_nlink == 0. A
    listagem usa os.stat/lstat: inode real e nlink 1 num arquivo comum (controle do D7)."""
    membros = diretorio.listar_membros(pasta, COV)
    assert membros and all(st.st_ino != 0 for _, st in membros)
    assert all(st.st_nlink == 1 for _, st in membros)


def test_d5_membro_trocado_entre_listar_e_abrir(pasta, tmp_path):
    membros = dict(diretorio.listar_membros(pasta, COV))
    outro = tmp_path / "outro.bin"
    shutil.copy(pasta / MEMBRO, outro)
    os.replace(outro, pasta / MEMBRO)
    _erro(diretorio.abrir_membro, pasta, MEMBRO, membros[MEMBRO].st_ino, trecho="trocado")
    # controle: com o inode novo abre
    novo = dict(diretorio.listar_membros(pasta, COV))[MEMBRO].st_ino
    os.close(diretorio.abrir_membro(pasta, MEMBRO, novo))


def test_d5_inode_zero_recusado(pasta, monkeypatch):
    """Sem inode não há como conferir a troca: recusa, nunca o atalho `ino and …`."""
    real = diretorio._stat_membro

    def sem_inode(p, nome):
        st = real(p, nome)
        return os.stat_result((st.st_mode, 0, st.st_dev, 1, 0, 0, st.st_size, 0, 0, 0))
    monkeypatch.setattr(diretorio, "_stat_membro", sem_inode)
    _erro(diretorio.listar_membros, pasta, COV, trecho="sem inode")


def test_d5_abrir_recusa_nome_fora_do_padrao(pasta):
    _erro(diretorio.abrir_membro, pasta, "../fora.cov.gz", 1, trecho="fora do padrão")


# ==================================================== D6: junção (host) / symlink (Linux)
def _pasta_ligada(alvo: Path, link: Path) -> None:
    if WINDOWS:
        _junção(alvo, link)
    else:
        _symlink(alvo, link, pasta=True)


def test_d6_ligacao_como_pasta_de_entrada_reprova(pasta, tmp_path):
    link = tmp_path / "cov_link"
    _pasta_ligada(pasta, link)
    v = formatos.validar("coverages_bismark", link)
    assert not v.ok and "esperava uma pasta" in v.falhas[0]
    assert "esperava uma pasta" in formatos.coverages_bismark(link).falhas[0]  # chamada direta
    _erro(diretorio.listar_membros, link, COV, trecho="esperava uma pasta")
    _erro(diretorio.identidade_pasta, link, trecho="esperava uma pasta")
    assert diretorio.eh_reparse(link) == WINDOWS
    assert formatos.validar("coverages_bismark", pasta).ok  # controle: a pasta comum aprova


def test_d6_ligacao_como_membro_reprova(pasta, tmp_path):
    (tmp_path / "alvo").mkdir()
    _pasta_ligada(tmp_path / "alvo", pasta / "x.cov.gz")
    _erro(diretorio.listar_membros, pasta, COV, trecho="junção" if WINDOWS else "symlink")


def test_d6_ligacao_como_porta_coverages_reprova(tmp_path):
    """A porta montar.coverages é uma subpasta da saída: ligada, reprova como a entrada (o
    envelope usa o mesmo validar e a mesma listagem)."""
    real = tmp_path / "real"
    shutil.copytree(DEMO, real)
    for p in list(real.iterdir()):
        cond = "HF" if "_HF_" in p.name else "Nelore"
        p.rename(real / formatos.nome_por_condicao(cond, formatos.stem_coverage(p.name)))
    assert formatos.validar("coverages_por_condicao", real).ok  # controle
    porta = tmp_path / "saida" / "coverages"
    porta.parent.mkdir()
    _pasta_ligada(real, porta)
    v = formatos.validar("coverages_por_condicao", porta)
    assert not v.ok and "esperava uma pasta" in v.falhas[0]
    _erro(diretorio.assinatura, porta, COV, trecho="esperava uma pasta")


def test_l8_componente_pai_ligado_sai_da_raiz(tmp_path):
    """Adendo §A3.2 (L8): o O_NOFOLLOW só cobre o último componente. Um componente-PAI ligado
    para fora da raiz é pego pelo caminho real da pasta aberta (/proc/self/fd no Linux, resolve
    no Windows)."""
    raiz, fora = tmp_path / "raiz", tmp_path / "fora"
    raiz.mkdir()
    shutil.copytree(DEMO, fora / "cd4")
    _pasta_ligada(fora, raiz / "pai")
    _erro(lambda: diretorio.abrir_pasta(raiz / "pai" / "cd4", raiz=raiz.resolve()), trecho="saiu da raiz")
    shutil.copytree(DEMO, raiz / "cd4")
    with diretorio.abrir_pasta(raiz / "cd4", raiz=raiz.resolve()) as p:  # controle
        assert len(diretorio.listar_membros(p, COV)) == 6


def test_identidade_da_pasta_conferida_na_abertura(pasta, tmp_path):
    ident = diretorio.identidade_pasta(pasta)
    with diretorio.abrir_pasta(pasta, identidade=ident) as p:
        assert len(diretorio.listar_membros(p, COV)) == 6
        os.close(diretorio.abrir_membro(p, MEMBRO, dict(diretorio.listar_membros(p, COV))[MEMBRO].st_ino))
    outra = tmp_path / "outra"
    shutil.copytree(DEMO, outra)
    _erro(lambda: diretorio.abrir_pasta(outra, identidade=ident), trecho="a pasta mudou")


# ===================================================================== D7: hardlink
def test_d7_hardlink_recusado_e_arquivo_comum_passa(pasta, tmp_path):
    fora = tmp_path / "fora.cov.gz"
    shutil.copy(pasta / MEMBRO, fora)
    assert os.stat(pasta / MEMBRO).st_nlink == 1  # controle: arquivo comum
    os.link(fora, pasta / "x.cov.gz")
    _erro(diretorio.listar_membros, pasta, COV, trecho="hardlink")


def test_d7_hardlink_criado_depois_da_listagem_recusado_na_abertura(pasta, tmp_path):
    membros = dict(diretorio.listar_membros(pasta, COV))
    os.link(pasta / MEMBRO, tmp_path / "segundo_nome.gz")
    _erro(diretorio.abrir_membro, pasta, MEMBRO, membros[MEMBRO].st_ino, trecho="hardlink")


# ======================================================================== D8
def test_d8_nomes_diferentes_por_casefold_aprovam_na_listagem(tmp_path):
    """x.cov.gz × X.bismark.cov.gz: nomes distintos mesmo sem caixa — a listagem aprova; quem
    reprova a colisão de AMOSTRA (stems x × X) é a checagem coverages_e_amostras."""
    p = tmp_path / "xX"
    p.mkdir()
    shutil.copy(DEMO / MEMBRO, p / "x.cov.gz")
    shutil.copy(DEMO / MEMBRO, p / "X.bismark.cov.gz")
    assert [n for n, _ in diretorio.listar_membros(p, COV)] == ["X.bismark.cov.gz", "x.cov.gz"]


# ================================================================== assinatura
def test_assinatura_e_a_lista_do_runs(pasta):
    a = diretorio.assinatura(pasta, COV)
    assert set(a) == {"pasta", "membros"} and len(a["membros"]) == 6
    st = os.stat(pasta / MEMBRO)
    assert a["membros"][MEMBRO] == [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]
    (pasta / "novo.cov").write_bytes(b"1\t1\t1\t100\t1\t0\n")
    assert diretorio.assinatura(pasta, COV) != a  # membro novo muda a assinatura


def test_erro_membros_e_value_error_e_teto_falso_por_padrao():
    e = diretorio.ErroMembros("x")
    assert isinstance(e, ValueError) and e.teto is False and e.limite == {}
    assert stat.FILE_ATTRIBUTE_REPARSE_POINT == 0x400
