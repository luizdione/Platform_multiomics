"""bgzf.py — leitura MÍNIMA e LIMITADA de BGZF, VCF.gz/BCF e índices TBI/CSI (só stdlib).

Peça dos validadores da genômica (omica/contrato/formatos.py, F5). Fica num módulo à parte
porque é parse BINÁRIO — struct, zlib e offsets virtuais —, e é aí que mora a classe de bug
que um portão não pode ter: a exceção crua (IndexError, struct.error, zlib.error) que vira 500
no meio do registro de uma entrada, com a cópia já no disco. Aqui TODO erro de estrutura sai
como `Ilegivel`, com mensagem só de posição e contagem (nunca bytes do arquivo); OSError passa,
porque quem chama sabe dizer "arquivo ilegível" sem ecoar o caminho.

Lê como o htslib lê (bgzf.c, hts.c, tbx.c, vcf.c):
  - bloco BGZF = gzip com FEXTRA, XLEN=6 e subcampo 'BC' de 2 bytes (BSIZE): o check_header;
  - cada bloco é descomprimido ISOLADO e conferido por CRC32 e ISIZE;
  - o arquivo termina no bloco EOF de 28 bytes (EOF_BGZF);
  - índice TBI/CSI = BGZF de um binário: magic, refs, bins com chunks (offsets virtuais
    coff<<16|uoff) e o pseudo-bin de metadados, que guarda a contagem de registros.

Tetos (crítica F5 #2, bomba de descompressão). Um bloco de 51 KB virava 52 MB (1028×) num
zlib.decompress sem teto de saída: um .tbi forjado de 64 MiB chegava a dezenas de GB no
container web. Agora:
  - cada bloco sai com no máximo MAX_BLOCO bytes (o máximo do formato) e ISIZE ≤ MAX_BLOCO;
  - o índice inteiro, com no máximo MAX_INDICE_DESCOMPRIMIDO (um índice real do Run 8, Chr1,
    tem 117 KB comprimido e ~1 MB aberto) e MAX_BINS / MAX_REFS;
  - a contagem por fluxo para numa linha de VCF maior que MAX_LINHA sem '\\n' (antes o resto
    pendente crescia sem limite) e num registro BCF com l_shared+l_indiv > MAX_REGISTRO_BCF;
  - a leitura do cabeçalho para em MAX_CABECALHO, e o 1º registro em MAX_LINHA.
Os tetos são lidos em tempo de chamada (os testes os apertam com monkeypatch).

Nada aqui conhece Veredito: quem transforma leitura em reprovação é o formatos.py.
"""
from __future__ import annotations

import functools
import hashlib
import struct
import zlib
from typing import Any, BinaryIO, Callable, Optional, TypeVar

EOF_BGZF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")

MAX_BLOCO = 1 << 16                     # ISIZE máximo de um bloco BGZF (spec SAM §4.1)
MAX_CABECALHO = 64 << 20                # cabeçalho VCF/BCF descomprimido
MAX_INDICE = 64 << 20                   # bytes de um .tbi/.csi no disco
MAX_INDICE_DESCOMPRIMIDO = 128 << 20    # o mesmo índice aberto
MAX_BINS = 2_000_000                    # bins somados de todos os contigs de um índice
MAX_REFS = 200_000                      # contigs de um índice (ARS-UCD/Run 8: 2.212)
MAX_LINHA = 4 << 20                     # linha de VCF (1º registro e contagem por fluxo)
MAX_REGISTRO_BCF = 64 << 20             # l_shared + l_indiv de um registro BCF
MAX_CONTIGS_FLUXO = 100_000             # contigs distintos vistos na contagem por fluxo

_T = TypeVar("_T")


class Ilegivel(ValueError):
    """Estrutura que o htslib não leria (ou leria de outro jeito). A mensagem já é a falha:
    só posição e contagem, nunca bytes do arquivo."""


class NaoBgzf(Ilegivel):
    """Sem o cabeçalho de bloco BGZF: gzip comum, outro formato ou cadeia quebrada no meio."""


def _protegido(onde: str) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Exceção crua de parse vira Ilegivel com a mensagem fixa — nunca sai do módulo."""
    def deco(func: Callable[..., _T]) -> Callable[..., _T]:
        @functools.wraps(func)
        def envolto(*args: Any, **kwargs: Any) -> _T:
            try:
                return func(*args, **kwargs)
            except Ilegivel:
                raise
            except (struct.error, zlib.error, IndexError, OverflowError):
                raise Ilegivel(f"estrutura binária inválida em {onde}") from None
        return envolto
    return deco


def _cabecalho_de_bloco_ok(h: bytes) -> bool:
    """O check_header do htslib (bgzf.c): 1f 8b 08, FLG com FEXTRA, XLEN=6, subcampo 'BC' de 2."""
    return (len(h) >= 18 and h[:3] == b"\x1f\x8b\x08" and bool(h[3] & 4) and h[10:12] == b"\x06\x00"
            and h[12:14] == b"BC" and h[14:16] == b"\x02\x00")


def _descomprimir_bloco(bruto: bytes, coff: int) -> bytes:
    """Um bloco inteiro (cabeçalho + deflate + CRC32 + ISIZE) → dados, com teto de saída."""
    crc, isize = struct.unpack_from("<II", bruto, len(bruto) - 8)
    if isize > MAX_BLOCO:
        raise Ilegivel(f"bloco BGZF no byte {coff} declara {isize} bytes (o máximo do formato é {MAX_BLOCO})")
    d = zlib.decompressobj(-15)
    try:
        # +1: um bloco que abriria mais que o máximo é pego pelo `eof` falso, sem materializar
        dados = d.decompress(bruto[18:-8], MAX_BLOCO + 1)
    except zlib.error:
        raise Ilegivel(f"bloco BGZF no byte {coff} com deflate inválido") from None
    if not d.eof or d.unconsumed_tail or len(dados) != isize or zlib.crc32(dados) != crc:
        raise Ilegivel(f"bloco BGZF no byte {coff} corrompido (CRC/ISIZE ou conteúdo além do declarado)")
    return dados


class Leitor:
    """Acesso aleatório bloco a bloco a um arquivo BGZF aberto em binário."""

    def __init__(self, f: BinaryIO, tamanho: int) -> None:
        self.f, self.tamanho = f, tamanho

    def tamanho_bloco(self, coff: int) -> int:
        self.f.seek(coff)
        h = self.f.read(18)
        if not _cabecalho_de_bloco_ok(h):
            raise NaoBgzf(f"sem cabeçalho de bloco BGZF no byte {coff}")
        total = struct.unpack_from("<H", h, 16)[0] + 1
        if total < 26:  # 18 de cabeçalho + 8 de CRC32/ISIZE
            raise Ilegivel(f"bloco BGZF no byte {coff} menor que o mínimo do formato")
        if coff + total > self.tamanho:
            raise Ilegivel(f"bloco BGZF no byte {coff} passa do fim do arquivo (truncado)")
        return total

    def ler(self, coff: int) -> tuple[bytes, int]:
        """(dados descomprimidos do bloco em `coff`, offset do bloco seguinte)."""
        total = self.tamanho_bloco(coff)
        self.f.seek(coff)
        bruto = self.f.read(total)
        if len(bruto) != total:
            raise Ilegivel(f"bloco BGZF no byte {coff} encolheu durante a leitura")
        return _descomprimir_bloco(bruto, coff), coff + total


def inteiro(bruto: bytes) -> bytes:
    """Todos os blocos de um BGZF pequeno (índice) já em memória, com teto total de saída."""
    teto = MAX_INDICE_DESCOMPRIMIDO
    out, coff, n = bytearray(), 0, len(bruto)
    while coff < n:
        h = bruto[coff:coff + 18]
        if not _cabecalho_de_bloco_ok(h):
            raise NaoBgzf(f"sem cabeçalho de bloco BGZF no byte {coff}")
        total = struct.unpack_from("<H", h, 16)[0] + 1
        if total < 26 or coff + total > n:
            raise Ilegivel(f"bloco BGZF no byte {coff} passa do fim do arquivo (truncado)")
        out += _descomprimir_bloco(bruto[coff:coff + total], coff)
        if len(out) > teto:
            raise Ilegivel(f"índice com mais de {teto} bytes descomprimido (bomba de descompressão?)")
        coff += total
    return bytes(out)


# -------------------------------------------------------------------------- índices
def pseudo_bin(depth: int) -> int:
    """Número do pseudo-bin de metadados (37450 com depth 5, o do TBI)."""
    return ((1 << ((depth + 1) * 3)) - 1) // 7 + 1


@_protegido("índice")
def ler_indice(dados: bytes) -> dict[str, Any]:
    """TBI/CSI JÁ descomprimido → {tipo, min_shift, depth, n_ref, refs, n_no_coor}.

    `refs` só traz os contigs com bins ou pseudo-bin (o resto não é usado e, num índice
    forjado com 200 mil contigs vazios, seria memória à toa): cada um com tid, nome (do TBI ou
    do aux do CSI; None no CSI de BCF), bins (conjunto), meta (voff_ini, voff_fim, n_mapped,
    n_unmapped) ou None, e o menor/maior offset virtual dos chunks."""
    mg = dados[:4]
    tbi = mg == b"TBI\x01"
    if tbi:
        n_ref, _fmt, _cs, _cb, _ce, _meta, _skip, l_nm = struct.unpack_from("<8i", dados, 4)
        if l_nm < 0 or 36 + l_nm > len(dados):
            raise Ilegivel("nomes do TBI passam do fim do índice")
        nomes: Optional[list[str]] = [x.decode("utf-8", "replace") for x in dados[36:36 + l_nm].split(b"\x00")]
        off, min_shift, depth = 36 + l_nm, 14, 5
    elif mg == b"CSI\x01":
        min_shift, depth, l_aux = struct.unpack_from("<3i", dados, 4)
        if not (0 < min_shift <= 32 and 0 <= depth <= 16 and min_shift + 3 * depth <= 63):
            raise Ilegivel("CSI com min_shift/depth fora do que o htslib aceita")
        if l_aux < 0 or 16 + l_aux > len(dados):
            raise Ilegivel("campo aux do CSI passa do fim do índice")
        aux, off = dados[16:16 + l_aux], 16 + l_aux
        nomes = None
        if l_aux >= 28:  # CSI de VCF.gz: a configuração do tabix e os nomes vão no aux
            l_nm = struct.unpack_from("<i", aux, 24)[0]
            if l_nm < 0 or 28 + l_nm > l_aux:
                raise Ilegivel("nomes do CSI passam do fim do campo aux")
            nomes = [x.decode("utf-8", "replace") for x in aux[28:28 + l_nm].split(b"\x00") if x]
        (n_ref,) = struct.unpack_from("<i", dados, off)
        off += 4
    else:
        raise Ilegivel("índice sem magic TBI\\1 nem CSI\\1")
    if n_ref < 0 or n_ref > MAX_REFS or n_ref > (len(dados) - off) // 4:
        raise Ilegivel(f"índice declara {n_ref} contigs (impossível para o tamanho, ou acima de {MAX_REFS})")
    pseudo = pseudo_bin(depth)
    refs: list[dict[str, Any]] = []
    total_bins = 0
    for tid in range(n_ref):
        (n_bin,) = struct.unpack_from("<i", dados, off)
        off += 4
        if n_bin < 0:
            raise Ilegivel(f"nº de bins negativo no contig {tid} do índice")
        total_bins += n_bin
        if total_bins > MAX_BINS:
            raise Ilegivel(f"índice com mais de {MAX_BINS} bins")
        bins: set[int] = set()
        meta: Optional[tuple[int, ...]] = None
        vmin: Optional[int] = None
        vmax = 0
        for _ in range(n_bin):
            if tbi:
                bin_id, n_chunk = struct.unpack_from("<Ii", dados, off)
                off += 8
            else:
                bin_id, _loff, n_chunk = struct.unpack_from("<IQi", dados, off)
                off += 16
            if n_chunk < 0 or off + 16 * n_chunk > len(dados):
                raise Ilegivel(f"bin com chunks além do fim do índice (contig {tid})")
            ch = struct.unpack_from(f"<{2 * n_chunk}Q", dados, off)
            off += 16 * n_chunk
            if bin_id == pseudo:
                if n_chunk != 2:  # (voff_ini, voff_fim) e (n_mapped, n_unmapped)
                    raise Ilegivel(f"pseudo-bin de metadados malformado no contig {tid}")
                meta = ch
            else:
                bins.add(bin_id)
                for a, b in zip(ch[::2], ch[1::2]):
                    vmin = a if vmin is None else min(vmin, a)
                    vmax = max(vmax, b)
        if tbi:  # índice linear: só pulado
            (n_intv,) = struct.unpack_from("<i", dados, off)
            off += 4
            if n_intv < 0 or off + 8 * n_intv > len(dados):
                raise Ilegivel(f"índice linear além do fim do índice (contig {tid})")
            off += 8 * n_intv
        if bins or meta is not None:
            nome = nomes[tid] if nomes is not None and tid < len(nomes) else None
            refs.append({"tid": tid, "nome": nome, "bins": bins, "meta": meta, "vmin": vmin, "vmax": vmax})
    n_no_coor = struct.unpack_from("<Q", dados, off)[0] if len(dados) - off >= 8 else None
    return {"tipo": "tbi" if tbi else "csi", "min_shift": min_shift, "depth": depth, "n_ref": n_ref,
            "refs": refs, "n_no_coor": n_no_coor}


def reg2bins(beg: int, end: int, min_shift: int, depth: int, teto: int = 200_000) -> Optional[set[int]]:
    """hts_reg2bins: bins que podem conter registro sobrepondo [beg, end) (0-based). None =
    região larga demais para listar (quem chama a trata como "pode ter registro")."""
    s = min_shift + depth * 3
    if beg >= end:
        return set()
    end = min(end, 1 << s) - 1
    out: set[int] = set()
    t = 0
    for lv in range(depth + 1):
        b, e = t + (beg >> s), t + (end >> s)
        if len(out) + e - b + 1 > teto:
            return None
        out.update(range(b, e + 1))
        s -= 3
        t += 1 << (lv * 3)
    return out


@_protegido("registro apontado pelo índice")
def registro_em(bg: Leitor, voff: int, tipo: str) -> Optional[object]:
    """Contig (VCF, texto) ou tid (BCF, int) do registro para onde o índice aponta. None = o
    offset não cai no início de um registro."""
    coff, uoff = voff >> 16, voff & 0xFFFF
    dados, prox = bg.ler(coff)
    while len(dados) - uoff < 64 and prox < bg.tamanho - len(EOF_BGZF):
        mais, prox = bg.ler(prox)
        dados += mais
    if uoff >= len(dados):
        return None
    if tipo == "vcf":
        if uoff > 0 and dados[uoff - 1:uoff] != b"\n":
            return None
        return bytes(dados[uoff:uoff + 256]).split(b"\t", 1)[0].decode("utf-8", "replace")
    if len(dados) - uoff < 16:
        return None
    return struct.unpack_from("<i", dados, uoff + 8)[0]


# ---------------------------------------------------------------- início do arquivo
def _utf8(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        raise Ilegivel("cabeçalho não é texto UTF-8") from None


@_protegido("cabeçalho")
def inicio(bg: Leitor) -> dict[str, Any]:
    """Blocos do início até ter o cabeçalho inteiro e o 1º registro (ou o fim do arquivo).
    {tipo: vcf|bcf, texto: cabeçalho, primeiro: bytes da 1ª linha (VCF) | {tid, pos0,
    n_amostras} (BCF) | None, bytes_lidos}. A busca do '#CHROM' é incremental (cada bloco é
    varrido uma vez) e o 1º registro tem teto: uma linha de 100 MB sem '\\n' logo depois do
    cabeçalho fazia o buffer crescer até o teto do cabeçalho, revarrendo tudo a cada bloco."""
    buf, coff = bytearray(), 0
    i_chrom = j_fim_chrom = -1
    pos = 0
    while True:
        fim = coff >= bg.tamanho
        if buf[:3] == b"BCF" and len(buf) >= 9:
            if buf[3:4] != b"\x02":
                raise Ilegivel("BCF de versão diferente de 2.x")
            l_text = struct.unpack_from("<I", buf, 5)[0]
            if l_text > MAX_CABECALHO:
                raise Ilegivel(f"cabeçalho BCF com mais de {MAX_CABECALHO} bytes")
            ini_reg = 9 + l_text
            if len(buf) >= ini_reg + 8:
                l_sh, _l_in = struct.unpack_from("<II", buf, ini_reg)
                if len(buf) >= ini_reg + 8 + min(l_sh, 32) or fim:
                    reg = None
                    if l_sh >= 24 and len(buf) >= ini_reg + 32:
                        chrom, pos0, _rlen, _qual, _n_ai, n_fs = struct.unpack_from("<iiifII", buf, ini_reg + 8)
                        reg = {"tid": chrom, "pos0": pos0, "n_amostras": n_fs & 0xFFFFFF}
                    return {"tipo": "bcf", "texto": _utf8(bytes(buf[9:ini_reg]).split(b"\x00", 1)[0]),
                            "primeiro": reg, "bytes_lidos": coff}
            elif fim:
                return {"tipo": "bcf", "texto": bytes(buf[9:]).split(b"\x00", 1)[0].decode("utf-8", "replace"),
                        "primeiro": None, "bytes_lidos": coff}
        elif buf[:1] == b"#":
            if i_chrom < 0:
                i_chrom = buf.find(b"\n#CHROM", pos)
                pos = max(0, len(buf) - 6) if i_chrom < 0 else i_chrom + 1
            if i_chrom >= 0 and j_fim_chrom < 0:
                j_fim_chrom = buf.find(b"\n", pos)
                pos = len(buf) if j_fim_chrom < 0 else j_fim_chrom + 1
            if j_fim_chrom >= 0:
                k = buf.find(b"\n", pos)
                if k >= 0 or fim:
                    prim = bytes(buf[j_fim_chrom + 1:k if k >= 0 else len(buf)])
                    return {"tipo": "vcf", "texto": _utf8(bytes(buf[:j_fim_chrom])),
                            "primeiro": prim or None, "bytes_lidos": coff}
                pos = len(buf)
                if len(buf) - (j_fim_chrom + 1) > MAX_LINHA:
                    raise Ilegivel(f"1º registro com mais de {MAX_LINHA} bytes sem quebra de linha")
            if fim:
                return {"tipo": "vcf", "texto": bytes(buf).decode("utf-8", "replace"), "primeiro": None,
                        "bytes_lidos": coff}
        elif buf:
            raise Ilegivel("conteúdo BGZF não começa com '#' (VCF) nem com 'BCF' (BCF)")
        if fim:
            raise Ilegivel("arquivo BGZF sem conteúdo")
        if len(buf) > MAX_CABECALHO:
            raise Ilegivel(f"cabeçalho com mais de {MAX_CABECALHO} bytes")
        dados, coff = bg.ler(coff)
        buf += dados


# --------------------------------------------------------------- contagem por fluxo
def _id_contig(nome: bytes) -> bytes:
    """Contig guardado como hash de 8 bytes: a memória não cresce com nome gigante."""
    return hashlib.blake2b(nome, digest_size=8).digest()


@_protegido("contagem dos registros VCF")
def contar_fluxo_vcf(bg: Leitor) -> dict[str, Any]:
    """Conta por fluxo, bloco a bloco (prova a cadeia BGZF inteira e cada CRC) e confere o que
    o tabix exige: cada contig num trecho contíguo e POS não decrescente dentro dele. Conta
    também os registros cujo ALT é só '*' (spanning deletion), que o frequencias.py descarta."""
    est = {"n": 0, "n_estrela": 0, "regride": 0, "primeiro_regride": None, "atual": None, "ult_pos": 0}
    vistos: set[bytes] = set()

    def registro(linha: bytes) -> None:
        est["n"] += 1
        c = linha.split(b"\t", 5)
        crom = c[0]
        pos = int(c[1]) if len(c) > 1 and 0 < len(c[1]) <= 18 and c[1].isdigit() else -1
        if len(c) > 4 and c[4] == b"*":
            est["n_estrela"] += 1
        if crom != est["atual"]:
            ident = _id_contig(crom)
            if ident in vistos:
                est["regride"] += 1
                est["primeiro_regride"] = est["primeiro_regride"] or est["n"]
            vistos.add(ident)
            if len(vistos) > MAX_CONTIGS_FLUXO:
                raise Ilegivel(f"mais de {MAX_CONTIGS_FLUXO} contigs distintos na contagem por fluxo")
            est["atual"], est["ult_pos"] = crom, pos
        else:
            if pos < est["ult_pos"]:
                est["regride"] += 1
                est["primeiro_regride"] = est["primeiro_regride"] or est["n"]
            est["ult_pos"] = pos

    pend = b""
    coff = 0
    while coff < bg.tamanho:
        dados, coff = bg.ler(coff)
        if not dados:
            continue
        partes = (pend + dados).split(b"\n")
        pend = partes.pop()
        if len(pend) > MAX_LINHA:
            raise Ilegivel(f"linha com mais de {MAX_LINHA} bytes sem quebra de linha (antes do byte {coff})")
        for linha in partes:
            if linha and linha[:1] != b"#":
                registro(linha)
    if pend.strip() and pend[:1] != b"#":
        registro(pend)  # última linha sem '\n': o htslib a lê
    return {"n": est["n"], "n_alt_so_estrela": est["n_estrela"], "fora_de_ordem": est["regride"],
            "primeiro_fora_de_ordem": est["primeiro_regride"], "n_contigs": len(vistos)}


@_protegido("contagem dos registros BCF")
def contar_fluxo_bcf(bg: Leitor) -> dict[str, Any]:
    """Pula o cabeçalho (9 + l_text bytes, que atravessa vários blocos) e anda registro a
    registro por l_shared + l_indiv. Sobra no fim = último registro truncado."""
    n = 0
    buf = bytearray()
    coff = 0
    pular: Optional[int] = None
    while coff < bg.tamanho:
        dados, coff = bg.ler(coff)
        buf += dados
        if pular is None:
            if len(buf) < 9:
                continue
            pular = 9 + struct.unpack_from("<I", buf, 5)[0]
        if pular:
            k = min(pular, len(buf))
            del buf[:k]
            pular -= k
            if pular:
                continue
        i = 0
        while len(buf) - i >= 8:
            l_sh, l_in = struct.unpack_from("<II", buf, i)
            if l_sh + l_in > MAX_REGISTRO_BCF:
                raise Ilegivel(f"registro BCF de {l_sh + l_in} bytes (acima de {MAX_REGISTRO_BCF}): "
                               "tamanho forjado ou arquivo corrompido")
            if len(buf) - i < 8 + l_sh + l_in:
                break
            n += 1
            i += 8 + l_sh + l_in
        del buf[:i]
    return {"n": n, "bytes_sobrando": len(buf)}
