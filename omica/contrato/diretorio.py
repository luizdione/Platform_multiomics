"""diretorio.py — PASTA como entrada e como porta de um run (F6: ESPEC §1.A.1 + adendo v2 §A3.1).

Só biblioteca padrão e Python 3.10: é importado por formatos.py, runs.py e jobs/script_job.py,
e o omica-vep (jammy) importa o script_job.

Uma pasta vale pelos MEMBROS, e membro é o que o consumidor lê (decisão 3): a regra de
LEITURA é a união do `list.files(pattern="\\\\.cov(\\\\.gz)?$")` do R com os `glob` do Python.
Fora da regra, o arquivo é ignorado sem `stat`. Dentro dela, o que um consumidor leria e outro
não é RECUSADO: oculto (o glob lê, o R não), nome fora do padrão, symlink, junção do NTFS,
não regular (subpasta listada pelo R, FIFO que travaria a leitura), hardlink, colisão de caixa
e nome que só casa ignorando a caixa (o glob do Windows o lê, o R e o Linux não).

A `regra` = {"leitura": <regex> | None, "fixos": [nomes] | None} fica congelada no registro e
no plano.

Troca entre a listagem e a leitura (crítica M9, M14 e L8):
  - o stat de cada membro vem de `os.stat(dir_fd=…, follow_symlinks=False)` (Linux) ou de
    `os.lstat` (Windows), NUNCA do cache do `scandir`: no Windows o `DirEntry.stat()` tem
    `st_ino == 0` e `st_nlink == 0` (medido), e com isso a conferência de inode e a recusa de
    hardlink não existiriam no host;
  - `abrir_membro` exige SEMPRE `fstat(fd).st_ino == ino` (sem o atalho `ino and …` do
    protótipo); `ino == 0` já foi recusado na listagem;
  - no Linux toda listagem e abertura passa por um `dir_fd` aberto com
    `O_DIRECTORY | O_NOFOLLOW`, que pode ser conferido contra a identidade `(st_dev, st_ino)` da
    validação do caminho e contra a raiz pelo `/proc/self/fd` (`abrir_pasta`). No host Windows
    não há `O_NOFOLLOW` nem `dir_fd`: sobram o inode real, o reparse point e o hardlink (R8).

Os erros (`ErroMembros`) nunca ecoam nome fora de `NOME_MEMBRO`.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal, Optional, Union

ESQUEMA_ARVORE = "omica.diretorio/1"
NOME_MEMBRO = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}", re.ASCII)
MAX_MEMBROS, MAX_VARRIDAS = 1024, 100_000

# Tetos de DESCOMPRESSÃO dos coverages (adendo §A2, crítica M12): o formatos.py os lê daqui em
# tempo de chamada (um monkeypatch nos testes vale). O montar_coverages.py repete os três, com
# teste de sincronia.
MAX_RAZAO_GZIP = 20                    # descomprimido/comprimido por membro E somando os .cov.gz da pasta, acima de 1 MiB
MAX_LINHAS_PASTA = 60_000_000          # Σ das linhas de todos os membros
MAX_DESCOMPRIMIDO_PASTA = 2 << 30      # Σ dos bytes descomprimidos de todos os membros

# Hardlink (st_nlink > 1) é recusado: a cópia de um membro com outro nome no disco pode ler um
# arquivo de fora das raízes (crítica M14). ATIVO no host e nos testes. No container o controle
# `st_nlink == 1` de um arquivo comum do bind mount (9p/drvfs) foi medido em 2026-10-29: 1 em
# /app e em /data. Se o E2E-4(i) mostrar o controle falhando, desligue aqui só no container
# (`os.name == "nt"`) e declare no R8.
RECUSA_HARDLINK = True

_COM_DIR_FD = (hasattr(os, "O_DIRECTORY") and os.scandir in os.supports_fd
               and os.stat in os.supports_dir_fd and os.open in os.supports_dir_fd)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRETORIO = getattr(os, "O_DIRECTORY", 0)
_BINARIO = getattr(os, "O_BINARY", 0)
_NAO_BLOQUEIA = getattr(os, "O_NONBLOCK", 0)   # um FIFO trocado no lugar do membro não trava o open
_REPARSE = stat.FILE_ATTRIBUTE_REPARSE_POINT
_BLOCO = 1 << 20

MSG_NAO_PASTA = ("esperava uma pasta (diretório comum: sem symlink nem junção do NTFS) e o caminho "
                  "não é uma")


class ErroMembros(ValueError):
    """Pasta que não pode ser atestada. `teto` = True é limite de tamanho (413
    `entrada_grande_demais`, com `limite` no corpo); False é recusa (422 `caminho_recusado`).
    A mensagem nunca ecoa nome fora de NOME_MEMBRO."""

    def __init__(self, motivo: str, *, teto: bool = False, limite: Optional[dict[str, int]] = None) -> None:
        super().__init__(motivo)
        self.teto = teto
        self.limite: dict[str, int] = dict(limite or {})


def _mostrar(nome: str) -> str:
    return nome if NOME_MEMBRO.fullmatch(nome) else "<omitido>"


@functools.lru_cache(maxsize=32)
def _regex(padrao: str, ignorar_caixa: bool) -> "re.Pattern[str]":
    return re.compile(padrao, re.ASCII | (re.IGNORECASE if ignorar_caixa else 0))


def classificar(nome: str, regra: dict[str, Any]) -> Literal["membro", "ignorar", "recusar"]:
    """`membro` se a leitura casa (ASCII, sensível à caixa) ou o nome é fixo; `recusar` se só
    casa ignorando a caixa (no host Windows o glob não distingue caixa e no container distingue:
    o mesmo arquivo seria lido num lado e não no outro); `ignorar` nos outros casos."""
    leitura = regra.get("leitura")
    fixos = tuple(regra.get("fixos") or ())
    if nome in fixos or (leitura and _regex(leitura, False).search(nome)):
        return "membro"
    dobra = nome.casefold()
    if any(dobra == f.casefold() for f in fixos) or (leitura and _regex(leitura, True).search(nome)):
        return "recusar"
    return "ignorar"


def _atributos(st: os.stat_result) -> int:
    """st_file_attributes do Windows (0 fora dele, e 0 num stat_result montado à mão)."""
    return getattr(st, "st_file_attributes", 0) or 0


def eh_reparse(caminho: Union[str, Path]) -> bool:
    """Reparse point do NTFS (junção ou symlink): o `is_symlink()` não vê junção. Fora do
    Windows o atributo não existe e vale 0. Erro de stat = True (falha fechada)."""
    try:
        return bool(_atributos(os.lstat(caminho)) & _REPARSE)
    except OSError:
        return True


def _problema_da_pasta(st: os.stat_result) -> Optional[str]:
    if stat.S_ISLNK(st.st_mode) or _atributos(st) & _REPARSE or not stat.S_ISDIR(st.st_mode):
        return MSG_NAO_PASTA
    return None


def identidade_pasta(caminho: Union[str, Path]) -> tuple[int, int]:
    """(st_dev, st_ino) da pasta, pelo lstat (sem seguir o último componente). Recusa
    symlink, junção e o que não é diretório. É a identidade que `abrir_pasta` confere no dir_fd
    (Linux) ou de novo depois da cópia (Windows) — adendo §A3.2."""
    try:
        st = os.lstat(caminho)
    except OSError:
        raise ErroMembros(MSG_NAO_PASTA) from None
    problema = _problema_da_pasta(st)
    if problema:
        raise ErroMembros(problema)
    return st.st_dev, st.st_ino


class Pasta:
    """Pasta aberta para listar e ler membros. No Linux guarda um dir_fd (O_DIRECTORY |
    O_NOFOLLOW) por onde passa toda listagem e abertura; no Windows, só o caminho e o lstat.
    `st` é o stat da própria pasta (do fstat do dir_fd, ou do lstat)."""

    def __init__(self, caminho: Path, fd: Optional[int], st: os.stat_result) -> None:
        self.caminho = caminho
        self.fd = fd
        self.st = st

    def fechar(self) -> None:
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)

    def __enter__(self) -> "Pasta":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.fechar()


def abrir_pasta(caminho: Union[str, Path], *, identidade: Optional[tuple[int, int]] = None,
                raiz: Optional[Union[str, Path]] = None) -> Pasta:
    """Abre a pasta para leitura. Com `identidade`, exige o mesmo (st_dev, st_ino) — a pasta
    não foi trocada depois da validação do caminho. Com `raiz` (já resolvida), exige que o
    caminho REAL da pasta aberta esteja dentro dela: no Linux pelo `/proc/self/fd/<dfd>`, que
    pega um componente-pai trocado por symlink depois do `resolve` (L8); no Windows pelo
    `resolve` de novo. Qualquer falha é ErroMembros (422 `caminho_recusado`)."""
    caminho = Path(caminho)
    if _COM_DIR_FD:
        try:
            fd = os.open(caminho, os.O_RDONLY | _DIRETORIO | _NOFOLLOW)
        except OSError:
            raise ErroMembros(MSG_NAO_PASTA) from None
        try:
            st = os.fstat(fd)
            problema = _problema_da_pasta(st)
            if problema:
                raise ErroMembros(problema)
            if identidade is not None and (st.st_dev, st.st_ino) != tuple(identidade):
                raise ErroMembros("a pasta mudou entre a conferência do caminho e a leitura (outra pasta no lugar)")
            if raiz is not None:
                try:
                    real = Path(os.readlink(f"/proc/self/fd/{fd}"))
                except OSError:
                    raise ErroMembros("não foi possível conferir o caminho real da pasta aberta") from None
                if not real.is_relative_to(Path(raiz)):
                    raise ErroMembros("o caminho real da pasta aberta saiu da raiz (componente trocado por symlink)")
        except BaseException:
            os.close(fd)
            raise
        return Pasta(caminho, fd, st)
    try:
        st = os.lstat(caminho)
    except OSError:
        raise ErroMembros(MSG_NAO_PASTA) from None
    problema = _problema_da_pasta(st)
    if problema:
        raise ErroMembros(problema)
    if identidade is not None and (st.st_dev, st.st_ino) != tuple(identidade):
        raise ErroMembros("a pasta mudou entre a conferência do caminho e a leitura (outra pasta no lugar)")
    if raiz is not None:
        try:
            real = caminho.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ErroMembros(MSG_NAO_PASTA) from None
        if not real.is_relative_to(Path(raiz)):
            raise ErroMembros("o caminho real da pasta saiu da raiz")
    return Pasta(caminho, None, st)


@contextmanager
def _aberta(pasta: Union[str, Path, Pasta]) -> Iterator[Pasta]:
    if isinstance(pasta, Pasta):
        yield pasta
        return
    p = abrir_pasta(pasta)
    try:
        yield p
    finally:
        p.fechar()


def _stat_membro(p: Pasta, nome: str) -> os.stat_result:
    try:
        if p.fd is not None:
            return os.stat(nome, dir_fd=p.fd, follow_symlinks=False)
        return os.lstat(p.caminho / nome)
    except OSError:
        raise ErroMembros(f"{_mostrar(nome)}: membro sumiu ou não pode ser lido (stat)") from None


def _problema_do_membro(st: os.stat_result) -> Optional[str]:
    """O que impede ler um membro como arquivo comum (None = nada)."""
    if stat.S_ISLNK(st.st_mode):
        return "membro é symlink: a leitura seguiria para fora da pasta"
    if _atributos(st) & _REPARSE:
        return "membro é junção ou link do NTFS (reparse point): a leitura seguiria para fora da pasta"
    if not stat.S_ISREG(st.st_mode):
        return ("membro não é arquivo regular (subpasta, FIFO ou dispositivo): o list.files do R lista "
                "a subpasta e um FIFO travaria a leitura")
    if st.st_ino == 0:
        return "membro sem inode no stat: não há como conferir troca entre a listagem e a leitura"
    if RECUSA_HARDLINK and st.st_nlink > 1:
        return ("membro é hardlink (mais de um nome no disco): o mesmo conteúdo existe fora da pasta, "
                "talvez fora das raízes de entrada")
    return None


def _listar(p: Pasta, regra: dict[str, Any]) -> list[tuple[str, os.stat_result]]:
    achados: list[tuple[str, os.stat_result]] = []
    dobras: dict[str, str] = {}
    n = 0
    with os.scandir(p.fd if p.fd is not None else p.caminho) as it:
        for e in it:
            n += 1
            if n > MAX_VARRIDAS:
                raise ErroMembros(f"mais de {MAX_VARRIDAS} entradas na pasta (teto da listagem)", teto=True,
                                  limite={"limite_membros": MAX_MEMBROS, "limite_entradas": MAX_VARRIDAS})
            nome = e.name
            tipo = classificar(nome, regra)
            if tipo == "ignorar":
                continue  # README, csv, subpasta que não casa: fora da pasta, sem stat
            if tipo == "recusar":
                raise ErroMembros(f"{_mostrar(nome)}: o nome só casa com a regra de leitura ignorando a caixa "
                                  "(o glob do Python no Windows o leria; o R e o Linux, não)")
            if nome.startswith("."):
                raise ErroMembros("membro oculto (começa com '.'): o glob do Python o lê e o list.files do R não")
            if not NOME_MEMBRO.fullmatch(nome):
                raise ErroMembros("membro com nome fora do padrão (só letras ASCII, dígitos, '_', '.' e '-', até 128 "
                                  "caracteres, sem começar por '.' ou '-'; sem espaço nem acento)")
            st = _stat_membro(p, nome)
            problema = _problema_do_membro(st)
            if problema:
                raise ErroMembros(f"{nome}: {problema}")
            dobra = nome.casefold()
            if dobra in dobras:
                raise ErroMembros(f"membros que só diferem na caixa: {dobras[dobra]} e {nome} (no NTFS são o mesmo "
                                  "arquivo)")
            dobras[dobra] = nome
            achados.append((nome, st))
            if len(achados) > MAX_MEMBROS:
                raise ErroMembros(f"mais de {MAX_MEMBROS} membros na pasta", teto=True,
                                  limite={"limite_membros": MAX_MEMBROS})
    achados.sort(key=lambda par: par[0])
    return achados


def listar_membros(pasta: Union[str, Path, Pasta], regra: dict[str, Any]) -> list[tuple[str, os.stat_result]]:
    """[(nome, stat)] dos membros, por nome. O stat é o de os.stat(dir_fd, sem seguir) / lstat
    — inode e nlink reais, nunca o cache do scandir. Levanta ErroMembros no primeiro problema."""
    with _aberta(pasta) as p:
        return _listar(p, regra)


def abrir_membro(pasta: Union[str, Path, Pasta], nome: str, ino: int) -> int:
    """fd só leitura de um membro listado. Exige SEMPRE o mesmo inode da listagem (o arquivo
    não foi trocado entre o stat e o open), arquivo regular, sem reparse point e sem hardlink.
    No Linux abre por dir_fd com O_NOFOLLOW (e O_NONBLOCK: um FIFO posto no lugar não trava)."""
    if not NOME_MEMBRO.fullmatch(nome):
        raise ErroMembros("membro com nome fora do padrão")
    with _aberta(pasta) as p:
        try:
            if p.fd is not None:
                fd = os.open(nome, os.O_RDONLY | _NOFOLLOW | _NAO_BLOQUEIA | _BINARIO, dir_fd=p.fd)
            else:
                fd = os.open(p.caminho / nome, os.O_RDONLY | _BINARIO)
        except OSError:
            raise ErroMembros(f"{nome}: membro não abre para leitura (symlink, removido ou sem permissão)") from None
        try:
            st = os.fstat(fd)
            if st.st_ino != ino:
                raise ErroMembros(f"{nome}: membro trocado entre a listagem e a leitura (o inode mudou)")
            problema = _problema_do_membro(st)
            if problema:
                raise ErroMembros(f"{nome}: {problema}")
        except BaseException:
            os.close(fd)
            raise
        return fd


def sha_arvore(membros: dict[str, dict[str, Any]]) -> Optional[str]:
    """sha256 da árvore: json canônico de [[nome, bytes, sha], …] por nome, sem mtime nem
    caminho (origem e cópia têm a mesma árvore; renomear muda, porque o grupo sai do nome).
    None se algum membro não tem sha."""
    linhas = []
    for nome in sorted(membros):
        m = membros[nome]
        if m.get("sha256") is None:
            return None
        linhas.append([nome, m["bytes"], m["sha256"]])
    texto = json.dumps({"esquema": ESQUEMA_ARVORE, "membros": linhas}, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(texto.encode("ascii")).hexdigest()


def sha_diretorio(pasta: Union[str, Path, Pasta], regra: dict[str, Any], teto: int) -> dict[str, Any]:
    """{"sha256", "bytes", "n_membros", "membros": {nome: {"bytes", "sha256"}}}, lendo cada
    membro pelo fd de abrir_membro. Passando de `teto` bytes somados, os membros dali em diante
    ficam com sha256 None e a árvore também (não atestável)."""
    with _aberta(pasta) as p:
        listados = _listar(p, regra)
        membros: dict[str, dict[str, Any]] = {}
        total = 0
        cabe = True
        for nome, st in listados:
            if not cabe or total + st.st_size > teto:
                cabe = False
                membros[nome] = {"bytes": st.st_size, "sha256": None}
                total += st.st_size
                continue
            h, lidos = hashlib.sha256(), 0
            with os.fdopen(abrir_membro(p, nome, st.st_ino), "rb") as f:
                for bloco in iter(lambda: f.read(_BLOCO), b""):
                    lidos += len(bloco)
                    if total + lidos > teto:
                        cabe = False
                        break
                    h.update(bloco)
            membros[nome] = {"bytes": lidos if cabe else max(lidos, st.st_size),
                             "sha256": h.hexdigest() if cabe else None}
            total += membros[nome]["bytes"]
    return {"sha256": sha_arvore(membros) if cabe else None, "bytes": total, "n_membros": len(membros),
            "membros": membros}


def _assinatura_st(st: os.stat_result) -> list[int]:
    return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]


def assinatura(pasta: Union[str, Path, Pasta], regra: dict[str, Any]) -> dict[str, Any]:
    """{"pasta": [size, mtime_ns, ctime_ns, ino], "membros": {nome: [...]}}, a mesma lista de
    runs._assinatura, com o stat de listar_membros (inode e ctime reais)."""
    with _aberta(pasta) as p:
        return {"pasta": _assinatura_st(p.st),
                "membros": {nome: _assinatura_st(st) for nome, st in _listar(p, regra)}}
