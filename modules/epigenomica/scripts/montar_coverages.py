"""montar_coverages.py — pasta de coverages + tabela de amostras → pasta por CONDIÇÃO.

Passo `montar` dos pipelines epigenomica_dmc e epigenomica_mofa (F6, ESPEC §3.2.2 + adendo v2
§A9.2). Os scripts selados escolhem o grupo por SUBSTRING do nome do arquivo
(metilacao_dmc.R:32-33 com fixed=TRUE, perfil_tss.py:76-77) e o mofa_preparar.py tira a
amostra do nome. Em vez de tocar neles, este passo copia cada coverage listado na tabela de
amostras (coldata) para `<condicao>__<amostra>.bismark.cov.gz`: o grupo passa a estar no nome.

A `amostra` do coldata é o nome do arquivo sem o sufixo, por IGUALDADE exata (decisão 7):
.bismark.cov.gz, .cov.gz, .bismark.cov, .cov — do mais longo ao mais curto.

TUDO é conferido ANTES da primeira escrita (código ≠ 0 e nenhuma `coverages/` criada):
  - os membros da pasta pela regra de omica/contrato/diretorio.py (oculto, nome fora do
    padrão, symlink, junção, não regular, hardlink, colisão de caixa, nome que só casa
    ignorando a caixa, tetos de entradas e de membros);
  - dois arquivos da mesma amostra (x.cov e x.cov.gz) e amostras que só diferem na caixa
    (crítica M10: no NTFS os nomes finais se sobrescreveriam);
  - amostra do coldata sem arquivo;
  - nome final fora de NOME_MEMBRO (até 128 caracteres) ou repetido sem distinção de caixa;
  - condição com '__' (o validador da porta parte o nome no primeiro '__');
  - a disjunção por substring: os nomes finais que contêm a condição L são exatamente os de L;
  - o gzip lido em TODOS os membros, com a razão de compressão e os tetos — os dois por membro
    e somando a pasta — de omica/contrato/formatos.py (crítica M11/M12; um .cov é recomprimido e
    a razão vale sobre o gzip que será gravado).

Cópia: `shutil.rmtree` só de <saida>/coverages (um membro de tentativa anterior reprovaria o
envelope); depois, na ordem do nome final, cada destino é CRIADO com modo 'xb' — uma colisão
do sistema de arquivos falha em vez de sobrescrever. `.gz` vai byte a byte, sem copystat (o
mtime cai dentro da janela do job); `.cov` vai por gzip com mtime 0 e sem nome no cabeçalho
(bytes determinísticos).

O resumo leva só contagens (crítica L9): nenhum nome de arquivo e nenhum caminho — a lista dos
arquivos sem amostra fica só no log do job.

Só biblioteca padrão: roda no worker methyl, que não importa o pacote omica. Por isso as regras
de diretorio.py/formatos.py estão REPETIDAS aqui; tests/test_montar_coverages.py confere a
sincronia (constantes, classificar, stem_coverage, nome_por_condicao e o laço do gzip).

Uso:
  python3 modules/epigenomica/scripts/montar_coverages.py \
      --coverages <pasta> --amostras <amostras.tsv> --saida <dir>
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
import re
import shutil
import stat
import zlib
from pathlib import Path
from typing import Iterator, Optional

from _comum import escrever_resumo, log

# ------------------------------------------------ cópia de omica/contrato/diretorio.py
NOME_MEMBRO = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}", re.ASCII)
MAX_MEMBROS, MAX_VARRIDAS = 1024, 100_000
MAX_RAZAO_GZIP = 20                    # descomprimido/comprimido por membro e da pasta, acima de 1 MiB
MAX_LINHAS_PASTA = 60_000_000          # Σ das linhas de todos os membros
MAX_DESCOMPRIMIDO_PASTA = 2 << 30      # Σ dos bytes descomprimidos de todos os membros
RECUSA_HARDLINK = True
# regra de leitura de formatos.DIRETORIOS["coverages_bismark"] (o list.files do R ∪ o glob)
LEITURA = r"\.cov(?:\.gz)?\Z"

# ------------------------------------------------ cópia de omica/contrato/formatos.py
MAX_LINHAS_COV = 50_000_000            # linhas por membro
MAX_DESCOMPRIMIDO_COV = 4 << 30        # bytes descomprimidos por membro
MIN_DESCOMPRIMIDO_RAZAO = 1 << 20      # a razão só vale acima de 1 MiB descomprimido
GZ_MAGIC = b"\x1f\x8b"
BLOCO_GZ = 16 << 10                    # entrada comprimida por leitura (a razão é medida em fluxo)
SUFIXOS_COV = (".bismark.cov.gz", ".cov.gz", ".bismark.cov", ".cov")
SUFIXO_POR_CONDICAO = ".bismark.cov.gz"

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARIO = getattr(os, "O_BINARY", 0)
_NAO_BLOQUEIA = getattr(os, "O_NONBLOCK", 0)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_RE_LEITURA = re.compile(LEITURA, re.ASCII)
_RE_LEITURA_SEM_CAIXA = re.compile(LEITURA, re.ASCII | re.IGNORECASE)


class Recusa(Exception):
    """Motivo de recusa (o job sai com código ≠ 0 antes de escrever)."""


def _mostrar(nome: str) -> str:
    return nome if NOME_MEMBRO.fullmatch(nome) else "<omitido>"


# ================================================================ regras de nome
def stem_coverage(nome: str) -> Optional[str]:
    """O nome do arquivo sem o sufixo de coverage (= a `amostra` do coldata). None = não é
    coverage. Igual a formatos.stem_coverage."""
    for s in SUFIXOS_COV:
        if nome.endswith(s):
            return nome[:-len(s)] or None
    return None


def nome_por_condicao(nivel: str, amostra: str) -> str:
    """Nome final na porta montar.coverages. Igual a formatos.nome_por_condicao."""
    return f"{nivel}__{amostra}{SUFIXO_POR_CONDICAO}"


def classificar(nome: str) -> str:
    """'membro' | 'ignorar' | 'recusar' — diretorio.classificar com a regra dos coverages
    (sem `fixos`): recusa o nome que só casa ignorando a caixa (o glob do Windows o leria; o R
    e o Linux, não)."""
    if _RE_LEITURA.search(nome):
        return "membro"
    if _RE_LEITURA_SEM_CAIXA.search(nome):
        return "recusar"
    return "ignorar"


def condicoes_extremas(condicoes: list[str]) -> tuple[str, str]:
    """(y0, y1): a menor e a maior condição em ordem alfabética (código de caractere). A MESMA
    regra do mofa_treinar_api.py (teste de sincronia): as invariantes do epigenomica_mofa
    comparam n_y0/n_y1 dos dois resumos."""
    return min(condicoes), max(condicoes)


# ================================================================ membros da pasta
def _problema_do_membro(st: os.stat_result) -> Optional[str]:
    if stat.S_ISLNK(st.st_mode):
        return "membro é symlink: a leitura seguiria para fora da pasta"
    if (getattr(st, "st_file_attributes", 0) or 0) & _REPARSE:
        return "membro é junção ou link do NTFS (reparse point): a leitura seguiria para fora da pasta"
    if not stat.S_ISREG(st.st_mode):
        return ("membro não é arquivo regular (subpasta, FIFO ou dispositivo): o list.files do R lista a "
                "subpasta e um FIFO travaria a leitura")
    if st.st_ino == 0:
        return "membro sem inode no stat: não há como conferir troca entre a listagem e a leitura"
    if RECUSA_HARDLINK and st.st_nlink > 1:
        return "membro é hardlink (mais de um nome no disco): o mesmo conteúdo existe fora da pasta"
    return None


def listar_membros(pasta: Path) -> list[tuple[str, os.stat_result]]:
    """[(nome, lstat)] dos membros, por nome — a regra de diretorio.listar_membros. O stat é o
    lstat do arquivo (inode e nlink reais), nunca o cache do scandir."""
    try:
        st_pasta = os.lstat(pasta)
    except OSError:
        raise Recusa("a pasta de coverages não existe ou não pode ser lida") from None
    if (stat.S_ISLNK(st_pasta.st_mode) or (getattr(st_pasta, "st_file_attributes", 0) or 0) & _REPARSE
            or not stat.S_ISDIR(st_pasta.st_mode)):
        raise Recusa("esperava uma pasta (diretório comum: sem symlink nem junção do NTFS)")
    achados: list[tuple[str, os.stat_result]] = []
    dobras: dict[str, str] = {}
    n = 0
    with os.scandir(pasta) as it:
        for e in it:
            n += 1
            if n > MAX_VARRIDAS:
                raise Recusa(f"mais de {MAX_VARRIDAS} entradas na pasta (teto da listagem)")
            nome = e.name
            tipo = classificar(nome)
            if tipo == "ignorar":
                continue
            if tipo == "recusar":
                raise Recusa(f"{_mostrar(nome)}: o nome só casa com .cov/.cov.gz ignorando a caixa")
            if nome.startswith("."):
                raise Recusa("membro oculto (começa com '.'): o glob do Python o lê e o list.files do R não")
            if not NOME_MEMBRO.fullmatch(nome):
                raise Recusa("membro com nome fora do padrão (só letras ASCII, dígitos, '_', '.' e '-', até 128 "
                             "caracteres)")
            try:
                st = os.lstat(pasta / nome)
            except OSError:
                raise Recusa(f"{nome}: membro sumiu ou não pode ser lido (stat)") from None
            problema = _problema_do_membro(st)
            if problema:
                raise Recusa(f"{nome}: {problema}")
            dobra = nome.casefold()
            if dobra in dobras:
                raise Recusa(f"membros que só diferem na caixa: {dobras[dobra]} e {nome}")
            dobras[dobra] = nome
            achados.append((nome, st))
            if len(achados) > MAX_MEMBROS:
                raise Recusa(f"mais de {MAX_MEMBROS} membros na pasta")
    achados.sort(key=lambda par: par[0])
    return achados


def abrir_membro(pasta: Path, nome: str, ino: int) -> int:
    """fd só leitura, com o MESMO inode da listagem (o arquivo não foi trocado no meio)."""
    try:
        fd = os.open(pasta / nome, os.O_RDONLY | _NOFOLLOW | _NAO_BLOQUEIA | _BINARIO)
    except OSError:
        raise Recusa(f"{nome}: membro não abre para leitura (symlink, removido ou sem permissão)") from None
    try:
        st = os.fstat(fd)
        if st.st_ino != ino:
            raise Recusa(f"{nome}: membro trocado entre a listagem e a leitura (o inode mudou)")
        problema = _problema_do_membro(st)
        if problema:
            raise Recusa(f"{nome}: {problema}")
    except BaseException:
        os.close(fd)
        raise
    return fd


# ================================================================ gzip e tetos
def blocos_gzip(f, nome: str, lidos: list[int]) -> Iterator[bytes]:
    """Bytes descomprimidos de TODOS os membros gzip, como o gzip do Python e o gzfile do R:
    a cada fim de membro, um decompressobj(31) novo sobre o que sobrou. O mesmo laço de
    formatos._blocos_gzip. `lidos[0]` conta os bytes comprimidos lidos."""
    d = None
    buf = b""
    fim = False
    n_membros = 0
    while True:
        if not buf and not fim:
            buf = f.read(BLOCO_GZ)
            lidos[0] += len(buf)
            fim = not buf
        if d is None:
            if not buf:
                if not n_membros:
                    raise Recusa(f"{nome}: '.cov.gz' vazio")
                return
            if len(buf) < 2 and not fim:
                mais = f.read(BLOCO_GZ)
                lidos[0] += len(mais)
                fim = not mais
                buf += mais
                continue
            if buf[:2] != GZ_MAGIC:
                if not n_membros:
                    raise Recusa(f"{nome}: '.cov.gz' que não é gzip (sem o magic 1f8b)")
                raise Recusa(f"{nome}: lixo depois do gzip (bytes que não são outro membro gzip)")
            d = zlib.decompressobj(31)
            n_membros += 1
        if not buf:
            raise Recusa(f"{nome}: gzip truncado: o arquivo acaba no meio de um membro")
        try:
            saida = d.decompress(buf)
        except zlib.error:
            raise Recusa(f"{nome}: gzip truncado ou corrompido (erro do zlib)") from None
        buf = b""
        if saida:
            yield saida
        if d.eof:
            buf, d = d.unused_data, None


class _Contador:
    """Arquivo de escrita que só conta bytes: o tamanho do gzip que a cópia de um .cov gravaria,
    sem gravar nada (a razão de compressão vale sobre ele)."""

    def __init__(self) -> None:
        self.n = 0

    def write(self, b) -> int:
        self.n += len(b)
        return len(b)

    def flush(self) -> None:
        pass


class Pasta:
    """Σ de linhas e de bytes descomprimidos dos membros lidos (os tetos por pasta valem em
    fluxo, entre membros) e Σ do gzip que a cópia grava (a razão agregada)."""

    def __init__(self) -> None:
        self.linhas = 0
        self.descomprimido = 0
        # o .gz lido (vai byte a byte) e o .cov recomprimido: a pasta que a porta mede. Numa
        # pasta só de .cov.gz, é o gz_comprimido de formatos._LeituraCov (teste de sincronia)
        self.gravado = 0


def _razao_agregada(total: Pasta, lendo: int = 0) -> None:
    """A razão somando os membros (formatos._ler_cov_membro): sem ela, a bomba partida em
    membros abaixo de 1 MiB passava pela razão de cada um. `lendo` = comprimido já lido do
    membro .gz em curso."""
    if (total.descomprimido > MIN_DESCOMPRIMIDO_RAZAO
            and total.descomprimido > MAX_RAZAO_GZIP * (total.gravado + lendo)):
        raise Recusa(f"razão de compressão > {MAX_RAZAO_GZIP}:1 somando os membros da pasta (o gzip que a cópia "
                     "grava): não é coverage Bismark")


def ler_membro(f, nome: str, total: Pasta) -> None:
    """Lê um coverage em fluxo e aplica a razão de compressão (por membro e da pasta) e os
    tetos (por membro e da pasta) de formatos._ler_cov_membro. As linhas contam como lá: cada
    '\\n' e o resto final não vazio. O CONTEÚDO das linhas é do validador da entrada e do da
    porta, não daqui. A razão agregada vale sobre o gzip que será gravado: o .cov entra com o
    tamanho recomprimido, conferido no fim do membro (o compressor retém saída no meio)."""
    lidos = [0]
    gz = nome.endswith(".gz")
    recomprimido: Optional[gzip.GzipFile] = None
    contador = _Contador()
    if gz:
        blocos: Iterator[bytes] = blocos_gzip(f, nome, lidos)
    else:
        inicio = f.read(2)
        if inicio == GZ_MAGIC:
            raise Recusa(f"{nome}: '.cov' com conteúdo gzip: seria comprimido de novo; renomeie para .cov.gz")

        def _cru() -> Iterator[bytes]:
            yield inicio
            yield from iter(lambda: f.read(1 << 20), b"")
        blocos = _cru()
        recomprimido = gzip.GzipFile(filename="", mode="wb", fileobj=contador, mtime=0)
    desc = n = 0
    resto = b""
    for bloco in blocos:
        desc += len(bloco)
        total.descomprimido += len(bloco)
        if desc > MAX_DESCOMPRIMIDO_COV:
            raise Recusa(f"{nome}: mais de {MAX_DESCOMPRIMIDO_COV} bytes descomprimidos num membro (teto)")
        if total.descomprimido > MAX_DESCOMPRIMIDO_PASTA:
            raise Recusa(f"mais de {MAX_DESCOMPRIMIDO_PASTA} bytes descomprimidos somando os membros (teto)")
        if gz and desc > MIN_DESCOMPRIMIDO_RAZAO and desc > MAX_RAZAO_GZIP * lidos[0]:
            raise Recusa(f"{nome}: razão de compressão > {MAX_RAZAO_GZIP}:1: não é coverage Bismark")
        if gz:
            _razao_agregada(total, lidos[0])
        if recomprimido is not None:
            recomprimido.write(bloco)
        partes = (resto + bloco).split(b"\n")
        resto = partes.pop()
        n += len(partes)
        total.linhas += len(partes)
        if n > MAX_LINHAS_COV:
            raise Recusa(f"{nome}: mais de {MAX_LINHAS_COV} linhas num membro (teto)")
        if total.linhas > MAX_LINHAS_PASTA:
            raise Recusa(f"mais de {MAX_LINHAS_PASTA} linhas somando os membros (teto)")
    if resto:
        n += 1
        total.linhas += 1
        if n > MAX_LINHAS_COV or total.linhas > MAX_LINHAS_PASTA:
            raise Recusa(f"{nome}: teto de linhas")
    if recomprimido is not None:
        recomprimido.close()
        if desc > MIN_DESCOMPRIMIDO_RAZAO and desc > MAX_RAZAO_GZIP * contador.n:
            raise Recusa(f"{nome}: o gzip da cópia teria razão de compressão > {MAX_RAZAO_GZIP}:1: não é "
                         "coverage Bismark")
        total.gravado += contador.n
        _razao_agregada(total)
    else:
        total.gravado += lidos[0]


# ================================================================ tabela de amostras
def ler_amostras(caminho: Path) -> list[tuple[str, str]]:
    """(amostra, condicao) de cada linha do coldata, na ordem do arquivo — lido como o
    validador `coldata` o lê (TAB, sem aspas, cabeçalho aparado de espaço, linha vazia pulada).
    O run já validou a tabela; aqui só se recusa o que tornaria a cópia ambígua."""
    with caminho.open(encoding="utf-8", newline="") as f:
        leitor = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        cab = [c.strip(" ") for c in next(leitor, None) or []]
        if "amostra" not in cab or "condicao" not in cab:
            raise Recusa("a tabela de amostras precisa das colunas 'amostra' e 'condicao'")
        ia, ic = cab.index("amostra"), cab.index("condicao")
        pares: list[tuple[str, str]] = []
        for linha in leitor:
            if not linha:
                continue
            if len(linha) != len(cab):
                raise Recusa(f"tabela de amostras: linha {leitor.line_num} com nº de campos diferente do cabeçalho")
            a, c = linha[ia], linha[ic]
            if not a or not c:
                raise Recusa(f"tabela de amostras: amostra ou condição vazia na linha {leitor.line_num}")
            pares.append((a, c))
    vistas: set[str] = set()
    for a, _ in pares:
        if a in vistas:
            raise Recusa(f"amostra repetida na tabela: {_mostrar(a)}")
        vistas.add(a)
    if not pares:
        raise Recusa("tabela de amostras sem linhas")
    return pares


# ================================================================ plano da cópia
def planejar(pasta: Path, pares: list[tuple[str, str]]) -> tuple[dict[str, tuple[str, str, int]], list[str]]:
    """{nome final: (condição, membro de origem, inode)} e os arquivos sem amostra. Levanta
    Recusa no primeiro problema — nada foi escrito ainda."""
    membros = listar_membros(pasta)
    por_stem: dict[str, tuple[str, int]] = {}
    dobras: dict[str, str] = {}
    for nome, st in membros:
        s = stem_coverage(nome)
        if s is None:
            raise Recusa(f"{nome}: membro sem amostra no nome")
        if s in por_stem:
            raise Recusa(f"dois arquivos da mesma amostra ({por_stem[s][0]} e {nome}): não há como saber qual copiar")
        if s.casefold() in dobras:
            raise Recusa(f"amostras que só diferem na caixa ({dobras[s.casefold()]} × {s}): no NTFS os nomes finais "
                         "se sobrescreveriam e o DMC rodaria com uma amostra a menos")
        por_stem[s] = (nome, st.st_ino)
        dobras[s.casefold()] = s
    sem_arquivo = [a for a, _ in pares if a not in por_stem]
    if sem_arquivo:
        raise Recusa(f"{len(sem_arquivo)} amostra(s) da tabela sem arquivo na pasta (ex.: {_mostrar(sem_arquivo[0])}): "
                     "a amostra é o nome do arquivo sem .bismark.cov.gz/.cov.gz/.bismark.cov/.cov")
    finais: dict[str, tuple[str, str, int]] = {}
    dobras_finais: set[str] = set()
    for a, c in pares:
        if "__" in c:
            raise Recusa(f"condição com '__' ({_mostrar(c)}): o nome final é <condição>__<amostra> e seria lido "
                         "partido no lugar errado")
        final = nome_por_condicao(c, a)
        if not NOME_MEMBRO.fullmatch(final):
            raise Recusa(f"nome final de {_mostrar(a)} fora do padrão de membro (até 128 caracteres; só letras ASCII, "
                         "dígitos, '_', '.', '-')")
        if final.casefold() in dobras_finais:
            raise Recusa(f"nome final repetido sem distinção de caixa ({_mostrar(final)}): no NTFS um sobrescreveria "
                         "o outro")
        dobras_finais.add(final.casefold())
        finais[final] = (c, *por_stem[a])
    for nivel in sorted({c for _, c in pares}):
        alheios = sorted(n for n, (c, _, _) in finais.items() if nivel in n and c != nivel)
        if alheios:
            raise Recusa(f"a condição {_mostrar(nivel)} casa por SUBSTRING com {len(alheios)} arquivo(s) de outra "
                         f"condição (ex.: {alheios[0]}): o metilacao_dmc.R e o perfil_tss.py juntariam os grupos; "
                         "renomeie a condição")
    do_coldata = {a for a, _ in pares}
    sem_amostra = sorted(nome for s, (nome, _) in por_stem.items() if s not in do_coldata)
    return finais, sem_amostra


def conferir_tetos(pasta: Path, finais: dict[str, tuple[str, str, int]]) -> None:
    """Lê os membros que serão copiados, na ordem do nome final, parando no primeiro excesso."""
    total = Pasta()
    for final in sorted(finais):
        _, origem, ino = finais[final]
        with os.fdopen(abrir_membro(pasta, origem, ino), "rb") as f:
            ler_membro(f, origem, total)


def copiar(pasta: Path, finais: dict[str, tuple[str, str, int]], destino: Path) -> tuple[int, int]:
    """Grava cada nome final (criação exclusiva 'xb'). Devolve (bytes gravados, recomprimidos)."""
    n_bytes = n_recomprimidos = 0
    for final in sorted(finais):
        _, origem, ino = finais[final]
        alvo = destino / final
        with os.fdopen(abrir_membro(pasta, origem, ino), "rb") as fi, open(alvo, "xb") as fo:
            if origem.endswith(".gz"):
                shutil.copyfileobj(fi, fo, 1 << 20)
            else:
                with gzip.GzipFile(filename="", mode="wb", fileobj=fo, mtime=0) as gz:
                    shutil.copyfileobj(fi, gz, 1 << 20)
                n_recomprimidos += 1
        n_bytes += alvo.stat().st_size
    return n_bytes, n_recomprimidos


def main() -> None:
    p = argparse.ArgumentParser(description="Coverages por condição (<condicao>__<amostra>.bismark.cov.gz)")
    p.add_argument("--coverages", required=True, help="pasta de coverages Bismark (.cov ou .cov.gz)")
    p.add_argument("--amostras", required=True, help="tabela de amostras (amostra, condicao)")
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    pasta, saida = Path(args.coverages), Path(args.saida)
    destino = saida / "coverages"
    try:
        pares = ler_amostras(Path(args.amostras))
        finais, sem_amostra = planejar(pasta, pares)
        conferir_tetos(pasta, finais)
    except Recusa as e:
        raise SystemExit(f"montar_coverages: {e}") from None
    if sem_amostra:
        # só no log do job: o resumo vira fato e iria ao redator externo (crítica L9)
        log(f"{len(sem_amostra)} arquivo(s) da pasta sem amostra na tabela (não copiados): "
            + ", ".join(_mostrar(n) for n in sem_amostra[:50]))

    saida.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(destino):
        # só <saida>/coverages: um membro de tentativa anterior reprovaria a porta. Symlink,
        # junção ou arquivo no lugar da subpasta não é apagado (o rmtree seguiria a junção)
        st = os.lstat(destino)
        if (stat.S_ISLNK(st.st_mode) or (getattr(st, "st_file_attributes", 0) or 0) & _REPARSE
                or not stat.S_ISDIR(st.st_mode)):
            raise SystemExit("montar_coverages: <saida>/coverages existe e não é pasta comum (link, junção ou "
                             "arquivo): não apago")
        shutil.rmtree(destino)
    destino.mkdir()
    try:
        n_bytes, n_recomprimidos = copiar(pasta, finais, destino)
    except (Recusa, FileExistsError) as e:
        raise SystemExit(f"montar_coverages: cópia interrompida ({e})") from None

    por_condicao: dict[str, int] = {}
    for c, _, _ in finais.values():
        por_condicao[c] = por_condicao.get(c, 0) + 1
    y0, y1 = condicoes_extremas(list(por_condicao))
    log(f"{len(finais)} coverages em {len(por_condicao)} condições ({n_recomprimidos} recomprimidos)")
    escrever_resumo(saida, "montar_resumo.json", {
        "n_amostras": len(finais),
        "n_condicoes": len(por_condicao),
        "por_condicao": dict(sorted(por_condicao.items())),
        "condicao_y0": y0,
        "condicao_y1": y1,
        "n_y0": por_condicao[y0],
        "n_y1": por_condicao[y1],
        "n_arquivos_sem_amostra": len(sem_amostra),
        "n_recomprimidos": n_recomprimidos,
        "bytes": n_bytes,
    })


if __name__ == "__main__":
    main()
