"""Validadores da genômica (F5): omica/contrato/formatos.py + omica/contrato/bgzf.py.

Regra do projeto: todo portão é VISTO reprovando (canário, conferido pelo MOTIVO — substring da
falha) e o caso legítimo vizinho continua passando (controle). Os canários da crítica da
especificação G1 (2026-10-26) dizem o número do item que fecham; os que provam um teto ou um
decorador trazem também a prova por mutação (sem a correção, o canário não reprova).

Os VCF/BCF/TBI/CSI sintéticos são escritos aqui (só stdlib) a partir dos primeiros registros
do demo versionado no repo; servem a ESTE validador e não foram conferidos contra o htslib.
Arquivos reais de E:\\data (saídas seladas do demo e os BCF do Run 8) são controles com skip
quando ausentes (CI, container).
"""
from __future__ import annotations

import functools
import gzip
import os
import struct
import tracemalloc
import zlib
from pathlib import Path
from typing import Optional

import pytest

from omica.config import REPO_ROOT
from omica.contrato import bgzf, formatos

GEN = REPO_ROOT / "modules" / "genomica"
DEMO = GEN / "demo_data" / "demo_bta14_inicio.vcf.gz"
DEMO_BED = GEN / "demo_data" / "painel_demo.bed"
PAINEL = GEN / "data" / "paineis" / "termotolerancia.txt"
E_DEMO = Path(r"E:\data\omica\results\genomica\demo")
E_PAINEIS = Path(r"E:\data\omica\results\genomica\paineis")
E_VCF = Path(r"E:\data\1000bg_vcf")
BCF_MT = E_VCF / "ChrMT-Run8-TAUIND-public.bcf"
TAM_BLOCO = 0xFF00  # o que o bgzip do htslib grava por bloco


@pytest.fixture(autouse=True)
def _cache_limpo():
    """O _ler_vcf guarda leituras por identidade do arquivo: um teste que aperta um teto com
    monkeypatch não pode receber a leitura feita com o teto antigo."""
    formatos._ler_vcf_cache.cache_clear()
    yield
    formatos._ler_vcf_cache.cache_clear()


def _exige(p: Path) -> Path:
    if not p.exists():
        pytest.skip(f"{p.name} ausente (dado real fora do repositório)")
    return p


def _reprova(v: formatos.Veredito, trecho: str) -> None:
    assert not v.ok and any(trecho in f for f in v.falhas), v.falhas


def _pico(func) -> int:
    tracemalloc.start()
    try:
        func()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


# ======================================================= escritores sintéticos (stdlib)
def _bloco(dados: bytes) -> bytes:
    c = zlib.compressobj(6, zlib.DEFLATED, -15)
    comp = c.compress(dados) + c.flush()
    return (b"\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff\x06\x00BC\x02\x00"
            + struct.pack("<H", 18 + len(comp) + 8 - 1) + comp + struct.pack("<II", zlib.crc32(dados), len(dados)))


def _bgzf_de(fluxo: bytes, intruso_apos: int = -1) -> tuple[bytes, list[int], int]:
    """(arquivo BGZF, coff de cada bloco, coff do EOF). intruso_apos = membro gzip COMUM
    inserido depois desse nº de blocos (cadeia quebrada no meio)."""
    saida, coffs = bytearray(), []
    for k, i in enumerate(range(0, len(fluxo), TAM_BLOCO), 1):
        coffs.append(len(saida))
        saida += _bloco(fluxo[i:i + TAM_BLOCO])
        if k == intruso_apos:
            saida += gzip.compress(b"14\t1\t.\tA\tC\t.\t.\t.\n")
    fim = len(saida)
    return bytes(saida + bgzf.EOF_BGZF), coffs, fim


def _voff(p: int, coffs: list[int], fim: int) -> int:
    k, uoff = divmod(p, TAM_BLOCO)
    return ((coffs[k] if k < len(coffs) else fim) << 16) | uoff


def _escrever(destino: Path, fluxo: bytes, marcas: list[tuple], intruso_apos: int = -1) -> list[tuple]:
    """Grava o fluxo em BGZF; marcas = (chave, pos, ini, fim) em bytes do fluxo → em voff."""
    dados, coffs, fim = _bgzf_de(fluxo, intruso_apos)
    destino.write_bytes(dados)
    return [(ch, pos, _voff(a, coffs, fim), _voff(b, coffs, fim)) for ch, pos, a, b in marcas]


def escrever_vcf(destino: Path, linhas: list[bytes], intruso_apos: int = -1) -> list[tuple]:
    marcas, p = [], 0
    for l in linhas:
        if not l.startswith(b"#"):
            c = l.split(b"\t", 2)
            marcas.append((c[0].decode(), int(c[1]) if c[1].isdigit() else 1, p, p + len(l)))
        p += len(l)
    return _escrever(destino, b"".join(linhas), marcas, intruso_apos)


def _por_chave(regs: list[tuple]) -> dict:
    por: dict = {}
    for ch, pos, a, b in regs:
        por.setdefault(ch, []).append((pos, a, b))
    return por


def _gravar_indice(destino: Path, binario: bytes, mtime_de: Optional[Path] = None) -> None:
    z = b"".join(_bloco(binario[i:i + TAM_BLOCO]) for i in range(0, len(binario), TAM_BLOCO))
    destino.write_bytes(z + bgzf.EOF_BGZF)
    if mtime_de is not None:  # índice 1 s mais novo que o dado: frescor determinístico
        t = mtime_de.stat().st_mtime_ns + 1_000_000_000
        os.utime(destino, ns=(t, t))


def escrever_tbi(destino: Path, regs: list[tuple], *, mtime_de: Optional[Path] = None,
                 chunks_pseudo: int = 2) -> None:
    """TBI mínimo: todos os chunks no bin 0, pseudo-bin 37450 com a contagem, índice linear."""
    por = _por_chave(regs)
    nomes = b"".join(n.encode() + b"\x00" for n in por)
    out = bytearray(b"TBI\x01" + struct.pack("<8i", len(por), 2, 1, 2, 0, 35, 0, len(nomes)) + nomes)
    for rs in por.values():
        beg, end = min(r[1] for r in rs), max(r[2] for r in rs)
        n_intv = ((max(r[0] for r in rs) - 1) >> 14) + 1
        out += struct.pack("<i", 2) + struct.pack("<IiQQ", 0, 1, beg, end)
        meta = (beg, end, len(rs), 0)[:2 * chunks_pseudo]
        out += struct.pack("<Ii", 37450, chunks_pseudo) + struct.pack(f"<{len(meta)}Q", *meta)
        out += struct.pack("<i", n_intv) + struct.pack(f"<{n_intv}Q", *([beg] * n_intv))
    out += struct.pack("<Q", 0)
    _gravar_indice(destino, bytes(out), mtime_de)


def escrever_csi(destino: Path, regs: list[tuple], *, nomes: Optional[list[str]] = None,
                 n_ref: int = 0, mtime_de: Optional[Path] = None) -> None:
    """CSI mínimo. Com `nomes` (VCF.gz) a configuração do tabix e os nomes vão no aux; sem
    eles (BCF) os contigs são os tids 0..n_ref-1."""
    por = _por_chave(regs)
    if nomes is None:
        aux, ordem = b"", list(range(n_ref))
    else:
        nm = b"".join(n.encode() + b"\x00" for n in nomes)
        aux, ordem = struct.pack("<7i", 2, 1, 2, 0, ord("#"), 0, len(nm)) + nm, nomes
    out = bytearray(b"CSI\x01" + struct.pack("<3i", 14, 5, len(aux)) + aux + struct.pack("<i", len(ordem)))
    for chave in ordem:
        rs = por.get(chave, [])
        if not rs:
            out += struct.pack("<i", 0)
            continue
        beg, end = min(r[1] for r in rs), max(r[2] for r in rs)
        out += struct.pack("<i", 2) + struct.pack("<IQi", 0, beg, 1) + struct.pack("<QQ", beg, end)
        out += struct.pack("<IQi", 37450, 0, 2) + struct.pack("<QQQQ", beg, end, len(rs), 0)
    out += struct.pack("<Q", 0)
    _gravar_indice(destino, bytes(out), mtime_de)


def escrever_bcf(destino: Path, contigs: list[str], registros: list[tuple[int, int]], n_amostras: int = 2,
                 l_indiv: int = 0) -> list[tuple]:
    """BCF 2.2 mínimo: só o que o validador lê (l_shared/l_indiv e os 24 bytes fixos)."""
    amostras = "".join(f"\tS{i}" for i in range(n_amostras))
    texto = ("##fileformat=VCFv4.2\n" + "".join(f"##contig=<ID={c},length=1000000>\n" for c in contigs)
             + '##INFO=<ID=AF,Number=A,Type=Float,Description="f">\n'
             + '##FORMAT=<ID=GT,Number=1,Type=String,Description="g">\n'
             + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO" + ("\tFORMAT" + amostras if n_amostras else "") + "\n")
    t = texto.encode() + b"\x00"
    fluxo = bytearray(b"BCF\x02\x02" + struct.pack("<I", len(t)) + t)
    marcas = []
    for tid, pos in registros:
        comp = struct.pack("<iiifII", tid, pos - 1, 1, 0.0, 2 << 16, (1 << 24) | n_amostras) + b"\x00" * 8
        ini = len(fluxo)
        fluxo += struct.pack("<II", len(comp), l_indiv) + comp + b"\x00" * l_indiv
        marcas.append((tid, pos, ini, len(fluxo)))
    return _escrever(destino, bytes(fluxo), marcas)


@functools.lru_cache(maxsize=1)
def _demo(n_reg: int = 3000) -> tuple[tuple[bytes, ...], tuple[bytes, ...]]:
    cab, reg = [], []
    with gzip.open(DEMO, "rb") as f:
        for l in f:
            (cab if l.startswith(b"#") else reg).append(l)
            if len(reg) >= n_reg:
                break
    return tuple(cab), tuple(reg)


def sintetico(tmp_path: Path, nome: str, cab=None, reg=None, *, indice: Optional[str] = ".tbi",
              intruso_apos: int = -1, **kw) -> Path:
    c, r = _demo()
    cab = list(c) if cab is None else cab
    reg = list(r) if reg is None else reg
    p = tmp_path / nome
    regs = escrever_vcf(p, cab + reg, intruso_apos)
    if indice == ".tbi":
        escrever_tbi(Path(f"{p}.tbi"), regs, mtime_de=p, **kw)
    elif indice == ".csi":
        escrever_csi(Path(f"{p}.csi"), regs, nomes=list(_por_chave(regs)) or ["14"], mtime_de=p)
    return p


def _texto(tmp_path: Path, nome: str, conteudo: str) -> Path:
    p = tmp_path / nome
    p.write_bytes(conteudo.encode("utf-8"))
    return p


# ============================================================= vcf_indexado: controles
def test_demo_do_repo_passa_contado_por_fluxo():
    v = formatos.validar("vcf_indexado", DEMO)
    assert v.ok, v.falhas
    assert v.perfil["n_registros"] == 122_372 and v.perfil["contagem"] == "fluxo"
    assert v.perfil["n_registros_indice"] == 122_372 and v.perfil["indices"] == [".tbi"]
    assert v.perfil["contigs_com_dados"] == ["14"] and v.perfil["tem_info_af"]
    assert set(v.perfil["indices_sha256"]) == {".tbi"}


def test_extraido_selado_passa():
    v = formatos.validar("vcf_indexado", _exige(E_DEMO / "extracao" / "extraido.vcf.gz"))
    assert v.ok, v.falhas
    assert v.perfil["n_registros"] == 52_881 and v.perfil["n_alt_so_estrela"] == 651


@pytest.mark.parametrize("nome", ["ChrMT-Run8-TAUIND-public.bcf", "Chr14-Run8-TAUIND-public.bcf",
                                  "Chr1-Run8-TAUIND-public.bcf"])
def test_bcf_do_run8_passa(nome):
    import time

    p = _exige(E_VCF / nome)
    t0 = time.perf_counter()
    v = formatos.validar("vcf_indexado", p)
    assert v.ok, v.falhas
    assert time.perf_counter() - t0 < 5
    assert v.perfil["conteiner"] == "bcf" and v.perfil["indices"] == [".csi"] and v.perfil["n_amostras"] == 1842
    if p.stat().st_size > formatos._MAX_CONTAGEM_FLUXO:
        assert v.perfil["contagem"] == "indice" and v.perfil["avisos"]


def test_sintetico_com_tbi_e_com_csi_passam(tmp_path):
    v = formatos.validar("vcf_indexado", sintetico(tmp_path, "t.vcf.gz"))
    assert v.ok and v.perfil["n_registros"] == 3000, v.falhas
    (tmp_path / "c").mkdir()
    v = formatos.validar("vcf_indexado", sintetico(tmp_path / "c", "c.vcf.gz", indice=".csi"))
    assert v.ok and v.perfil["indice_canonico"] == ".csi", v.falhas


def test_bcf_sintetico_com_csi_passa_e_com_tbi_reprova(tmp_path):
    """BCF só se indexa com .csi (o tabix não indexa BCF): um .tbi ao lado não serve."""
    p = tmp_path / "x.bcf"
    regs = escrever_bcf(p, ["14", "15"], [(0, 10), (0, 20), (1, 5)])
    escrever_csi(Path(f"{p}.csi"), regs, n_ref=2, mtime_de=p)
    v = formatos.validar("vcf_indexado", p)
    assert v.ok and v.perfil["n_registros"] == 3 and v.perfil["contigs_com_dados"] == ["14", "15"], v.falhas
    q = tmp_path / "y.bcf"
    regs = escrever_bcf(q, ["14"], [(0, 10)])
    escrever_tbi(Path(f"{q}.tbi"), [("14", *r[1:]) for r in regs], mtime_de=q)
    _reprova(formatos.validar("vcf_indexado", q), "BCF com .tbi")


# ============================================================= vcf_indexado: canários
def _copia(tmp_path: Path, origem: Path, nome: str, *irmaos: tuple[Path, str]) -> Path:
    p = tmp_path / nome
    p.write_bytes(origem.read_bytes())
    for fonte, sufixo in irmaos:
        Path(f"{p}{sufixo}").write_bytes(fonte.read_bytes())
    return p


def test_sem_indice_sem_eof_e_truncado_reprovam(tmp_path):
    base = sintetico(tmp_path, "base.vcf.gz")
    tbi = Path(f"{base}.tbi")
    _reprova(formatos.validar("vcf_indexado", _copia(tmp_path, base, "sem_indice.vcf.gz")), "sem índice ao lado")
    sem_eof = tmp_path / "sem_eof.vcf.gz"
    sem_eof.write_bytes(base.read_bytes()[:-28])
    Path(f"{sem_eof}.tbi").write_bytes(tbi.read_bytes())
    _reprova(formatos.validar("vcf_indexado", sem_eof), "bloco EOF")
    metade = tmp_path / "metade.vcf.gz"
    metade.write_bytes(base.read_bytes()[:base.stat().st_size // 2])
    Path(f"{metade}.tbi").write_bytes(tbi.read_bytes())
    _reprova(formatos.validar("vcf_indexado", metade), "bloco EOF")


def test_indice_mais_velho_que_o_dado_reprova(tmp_path):
    p = sintetico(tmp_path, "velho.vcf.gz")
    t = Path(f"{p}.tbi").stat().st_mtime_ns
    os.utime(p, ns=(t + 60 * 10**9, t + 60 * 10**9))
    _reprova(formatos.validar("vcf_indexado", p), "mais velho que o dado")
    # mtime IGUAL passa (comparação estrita em ns)
    os.utime(p, ns=(t, t))
    assert formatos.validar("vcf_indexado", p).ok


def test_indice_de_outro_arquivo_reprova(tmp_path):
    """O .tbi do demo (122 mil registros) ao lado de um VCF de 3 mil: aponta além do fim."""
    p = _copia(tmp_path, sintetico(tmp_path, "a.vcf.gz"), "trocado.vcf.gz", (Path(f"{DEMO}.tbi"), ".tbi"))
    t = p.stat().st_mtime_ns + 10**9
    os.utime(Path(f"{p}.tbi"), ns=(t, t))
    _reprova(formatos.validar("vcf_indexado", p), "além do fim do dado")


def test_conteudo_trocado_com_o_sufixo_reprova(tmp_path):
    """BCF com nome .vcf.gz e VCF com nome .bcf: o plink2 receberia a flag errada."""
    p = tmp_path / "bcf_disfarcado.vcf.gz"
    regs = escrever_bcf(p, ["14"], [(0, 10)])
    escrever_csi(Path(f"{p}.csi"), regs, n_ref=1, mtime_de=p)
    _reprova(formatos.validar("vcf_indexado", p), "conteúdo BCF com extensão de VCF")
    v = sintetico(tmp_path, "vcf_disfarcado.bcf", indice=".csi")
    _reprova(formatos.validar("vcf_indexado", v), "conteúdo VCF com extensão de BCF")
    _reprova(formatos.validar("vcf_indexado", sintetico(tmp_path, "vcf.dat")), "extensão deve ser .vcf.gz ou .bcf")


def test_zero_registros_reprova(tmp_path):
    """Contig divergente: o bcftools termina com exit 0 e devolve só o cabeçalho."""
    p = sintetico(tmp_path, "zero.vcf.gz", reg=[])
    _reprova(formatos.validar("vcf_indexado", p), "0 registros")


def test_registros_repetidos_ou_fora_de_ordem_reprovam(tmp_path):
    _, reg = _demo()
    dup = list(reg[:200]) + list(reg[100:200])  # região sobreposta sem fusão
    _reprova(formatos.validar("vcf_indexado", sintetico(tmp_path, "dup.vcf.gz", reg=dup)), "fora de ordem")


def test_cabecalho_e_primeiro_registro_malformados_reprovam(tmp_path):
    cab, reg = _demo()
    sem_chrom = [x for x in cab if not x.startswith(b"#CHROM")]
    _reprova(formatos.validar("vcf_indexado", sintetico(tmp_path, "sc.vcf.gz", cab=sem_chrom, reg=list(reg[:50]))),
             "#CHROM")
    sete = [b"\t".join(reg[0].split(b"\t")[:7]) + b"\n"] + list(reg[1:20])
    _reprova(formatos.validar("vcf_indexado", sintetico(tmp_path, "c7.vcf.gz", reg=sete)), "1º registro")


def test_membro_gzip_comum_no_meio_da_cadeia_reprova(tmp_path):
    _reprova(formatos.validar("vcf_indexado", sintetico(tmp_path, "cadeia.vcf.gz", intruso_apos=2)),
             "cadeia BGZF quebrada")


def test_gzip_comum_do_snpeff_nao_e_bgzf(tmp_path):
    """anotado.vcf.gz: o SnpEff grava com gzip.open — legível em sequência, não indexável."""
    cab, reg = _demo()
    p = tmp_path / "anotado.vcf.gz"
    p.write_bytes(gzip.compress(b"".join(cab + reg[:100])))
    v = formatos.validar("vcf_indexado", p)
    _reprova(v, "não é BGZF")
    assert any("bloco EOF" in f for f in v.falhas)


def test_anotado_selado_nao_e_bgzf():
    _reprova(formatos.validar("vcf_indexado", _exige(E_DEMO / "anotacao" / "anotado.vcf.gz")), "não é BGZF")


# --------------------------------------------- crítica #4: .tbi e .csi ao mesmo tempo
def test_tbi_e_csi_juntos_reprovam_como_ambiguos(tmp_path):
    """bcftools 1.19: .csi lixo ao lado de um .tbi bom → rc 255 (o htslib carrega o .csi); o
    esboço consultava o .tbi e aprovava. Controle: só o .tbi bom passa."""
    p = sintetico(tmp_path, "amb.vcf.gz")
    assert formatos.validar("vcf_indexado", p).ok
    csi = Path(f"{p}.csi")
    csi.write_bytes(b"lixo que nao e indice")
    v = formatos.validar("vcf_indexado", p)
    _reprova(v, "dois índices ao lado")
    assert v.perfil["indices"] == [".tbi", ".csi"] and set(v.perfil["indices_sha256"]) == {".tbi", ".csi"}
    # mesmo com os dois VÁLIDOS: ambíguo, e o que o htslib usaria é o .csi
    (tmp_path / "d").mkdir()
    q = sintetico(tmp_path / "d", "amb2.vcf.gz")
    regs = escrever_vcf(tmp_path / "d" / "tmp.vcf.gz", list(_demo()[0]) + list(_demo()[1]))
    escrever_csi(Path(f"{q}.csi"), regs, nomes=["14"], mtime_de=q)
    v = formatos.validar("vcf_indexado", q)
    _reprova(v, "dois índices ao lado")
    assert v.perfil["indice_canonico"] == ".csi"


# --------------------------------------------------- crítica #2: tetos de descompressão
def _bomba(n_saida: int = 50 * 1024 * 1024) -> bytes:
    """UM bloco BGZF válido no cabeçalho cujo deflate abre 50 MB (o ISIZE mente 65536)."""
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    comp = c.compress(b"\x00" * n_saida) + c.flush()
    assert 18 + len(comp) + 8 <= 65536
    return (b"\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff\x06\x00BC\x02\x00" + struct.pack("<H", 18 + len(comp) + 7)
            + comp + struct.pack("<II", 0, 65536))


def test_bomba_de_um_bloco_no_indice_reprova_sem_estourar_memoria(tmp_path):
    bomba = _bomba()
    # prova de que o caso é uma bomba de verdade: o zlib.decompress sem teto do esboço
    # (_bgzf_inteiro) abria os 50 MB
    assert len(zlib.decompress(bomba[18:-8], -15)) == 50 * 1024 * 1024
    tbi = tmp_path / "bomba.tbi"
    tbi.write_bytes(bomba + bgzf.EOF_BGZF)
    pico = _pico(lambda: formatos.validar("indice_vcf", tbi))
    _reprova(formatos.validar("indice_vcf", tbi), "corrompido")
    assert pico < 8 * 1024 * 1024, pico
    # a mesma bomba como índice ao lado de um VCF: reprova pelo índice, sem levantar
    p = sintetico(tmp_path, "v.vcf.gz", indice=None)
    Path(f"{p}.tbi").write_bytes(bomba + bgzf.EOF_BGZF)
    os.utime(Path(f"{p}.tbi"), ns=(p.stat().st_mtime_ns + 10**9,) * 2)
    _reprova(formatos.validar("vcf_indexado", p), ".tbi ilegível")


def test_teto_total_do_indice_descomprimido(tmp_path, monkeypatch):
    """Muitos blocos legítimos somando mais que o teto: reprova antes de materializar tudo."""
    dados = b"\x00" * (TAM_BLOCO * 4)
    tbi = tmp_path / "grande.tbi"
    _gravar_indice(tbi, dados)
    monkeypatch.setattr(bgzf, "MAX_INDICE_DESCOMPRIMIDO", 2 * TAM_BLOCO)
    _reprova(formatos.validar("indice_vcf", tbi), "descomprimido")
    # controle (mutação do teto): sem ele, o arquivo é lido e reprova por OUTRO motivo (magic)
    monkeypatch.setattr(bgzf, "MAX_INDICE_DESCOMPRIMIDO", 128 << 20)
    v = formatos.validar("indice_vcf", tbi)
    assert not v.ok and not any("descomprimido" in f for f in v.falhas), v.falhas


def _linha_longa(tamanho: int) -> bytes:
    return b"14\t999999\t.\tA\tC\t.\t.\tAF=0.1;X=" + b"A" * tamanho  # sem '\n'


def test_linha_de_100mb_sem_quebra_reprova_sem_estourar_memoria(tmp_path):
    """Na contagem por fluxo, o resto pendente crescia sem limite numa linha sem '\\n'."""
    cab, reg = _demo()
    p = tmp_path / "longa.vcf.gz"
    regs = escrever_vcf(p, list(cab) + list(reg[:10]) + [_linha_longa(100 * 1024 * 1024)])
    escrever_tbi(Path(f"{p}.tbi"), regs[:10], mtime_de=p)
    assert p.stat().st_size <= formatos._MAX_CONTAGEM_FLUXO  # cai na contagem por fluxo
    pico = _pico(lambda: formatos.validar("vcf_indexado", p))
    _reprova(formatos.validar("vcf_indexado", p), "sem quebra de linha")
    assert pico < 48 * 1024 * 1024, pico


def test_teto_de_linha_e_o_que_reprova(tmp_path, monkeypatch):
    """Prova por mutação do teto: a mesma linha de 5 MB reprova com o teto de 4 MiB e deixa de
    reprovar por ele com o teto acima dela."""
    cab, reg = _demo()
    p = tmp_path / "media.vcf.gz"
    regs = escrever_vcf(p, list(cab) + list(reg[:10]) + [_linha_longa(5 * 1024 * 1024)])
    escrever_tbi(Path(f"{p}.tbi"), regs[:10], mtime_de=p)
    _reprova(formatos.validar("vcf_indexado", p), "sem quebra de linha")
    monkeypatch.setattr(bgzf, "MAX_LINHA", 16 * 1024 * 1024)
    formatos._ler_vcf_cache.cache_clear()
    v = formatos.validar("vcf_indexado", p)
    assert not any("sem quebra de linha" in f for f in v.falhas), v.falhas


def test_primeiro_registro_gigante_reprova_no_cabecalho(tmp_path):
    cab, _ = _demo()
    p = tmp_path / "prim.vcf.gz"
    escrever_vcf(p, list(cab) + [_linha_longa(100 * 1024 * 1024)])
    pico = _pico(lambda: formatos.validar("vcf_indexado", p))
    _reprova(formatos.validar("vcf_indexado", p), "1º registro com mais de")
    assert pico < 48 * 1024 * 1024, pico


def test_registro_bcf_com_tamanho_forjado_reprova(tmp_path, monkeypatch):
    """l_shared + l_indiv forjado: o buffer da contagem esperaria o registro inteiro."""
    p = tmp_path / "forjado.bcf"
    regs = escrever_bcf(p, ["14"], [(0, 10), (0, 20)], l_indiv=5000)
    escrever_csi(Path(f"{p}.csi"), regs, n_ref=1, mtime_de=p)
    assert formatos.validar("vcf_indexado", p).ok  # controle: 5 KB por registro é legítimo
    monkeypatch.setattr(bgzf, "MAX_REGISTRO_BCF", 1000)
    formatos._ler_vcf_cache.cache_clear()
    _reprova(formatos.validar("vcf_indexado", p), "registro BCF de")


# ------------------------------------------- crítica #6: IndexError não sai do parse
def test_pseudo_bin_de_um_chunk_reprova_sem_levantar(tmp_path):
    """O esboço fazia meta[2] + meta[3] num pseudo-bin de 1 chunk: IndexError cru."""
    p = sintetico(tmp_path, "p1.vcf.gz", chunks_pseudo=1)
    _reprova(formatos.validar("vcf_indexado", p), "pseudo-bin de metadados malformado")
    _reprova(formatos.validar("indice_vcf", Path(f"{p}.tbi")), "pseudo-bin")
    with pytest.raises(bgzf.Ilegivel):
        bgzf.ler_indice(bgzf.inteiro(Path(f"{p}.tbi").read_bytes()))


@pytest.mark.parametrize("corte", [40, 200, 5000])
def test_binario_truncado_nunca_levanta(tmp_path, corte):
    base = sintetico(tmp_path, "b.vcf.gz")
    p = tmp_path / "t.vcf.gz"
    p.write_bytes(base.read_bytes()[:corte])
    tbi = Path(f"{base}.tbi").read_bytes()
    Path(f"{p}.tbi").write_bytes(tbi[:min(corte // 4, len(tbi) - 30)])
    assert not formatos.validar("vcf_indexado", p).ok
    assert not formatos.validar("indice_vcf", Path(f"{p}.tbi")).ok
    for tipo in ("vcf_tem_info_af", "vcf_contigs_ars_ucd"):
        assert not formatos.CHECAGENS[tipo][0](p).ok
    assert not formatos.vcf_e_bed(p, DEMO_BED).ok


@pytest.mark.parametrize("estrago", ["n_ref", "n_bin", "n_chunk", "n_intv", "l_nm"])
def test_indice_com_contagem_forjada_reprova_sem_levantar(tmp_path, estrago):
    """Campos de tamanho forjados (negativos ou gigantes) no TBI: nunca exceção crua, nunca laço
    sem fim."""
    p = sintetico(tmp_path, "f.vcf.gz")
    dados = bytearray(bgzf.inteiro(Path(f"{p}.tbi").read_bytes()))
    l_nm = struct.unpack_from("<i", dados, 32)[0]
    off_ref = 36 + l_nm
    alvo = {"n_ref": (4, 2**31 - 1), "l_nm": (32, 2**31 - 1), "n_bin": (off_ref, -5),
            "n_chunk": (off_ref + 8, 2**30), "n_intv": (off_ref + 4 + 8 + 16 + 8 + 32, -1)}[estrago]
    struct.pack_into("<i", dados, *alvo)
    _gravar_indice(Path(f"{p}.tbi"), bytes(dados), mtime_de=p)
    _reprova(formatos.validar("indice_vcf", Path(f"{p}.tbi")), "binário ilegível")
    assert not formatos.validar("vcf_indexado", p).ok


# ------------------------------------------------------------------- cache de leitura
def test_cache_invalida_ao_trocar_ou_criar_indice(tmp_path):
    p = sintetico(tmp_path, "cache.vcf.gz")
    assert formatos.validar("vcf_indexado", p).ok
    Path(f"{p}.csi").write_bytes(b"x")  # um .csi novo muda o que o htslib lê
    _reprova(formatos.validar("vcf_indexado", p), "dois índices")
    Path(f"{p}.csi").unlink()
    assert formatos.validar("vcf_indexado", p).ok
    tbi = Path(f"{p}.tbi")
    tbi.write_bytes(tbi.read_bytes()[:-28])  # .tbi trocado (sem o EOF)
    _reprova(formatos.validar("vcf_indexado", p), "índice truncado")


# =================================================================== indice_vcf
def test_indice_vcf_controles():
    v = formatos.validar("indice_vcf", Path(f"{DEMO}.tbi"))
    assert v.ok and v.perfil["tipo"] == "tbi" and v.perfil["n_registros"] == 122_372, v.falhas
    assert v.perfil["bytes"] == 1797


def test_indice_vcf_controles_reais():
    v = formatos.validar("indice_vcf", _exige(E_DEMO / "extracao" / "extraido.vcf.gz.tbi"))
    assert v.ok and v.perfil["n_registros"] == 52_881 and v.perfil["bytes"] == 913, v.falhas
    v = formatos.validar("indice_vcf", _exige(E_VCF / "Chr14-Run8-TAUIND-public.bcf.csi"))
    assert v.ok and v.perfil["tipo"] == "csi" and v.perfil["n_ref"] == 2212, v.falhas


def test_indice_vcf_canarios(tmp_path):
    tbi = Path(f"{DEMO}.tbi").read_bytes()
    renomeado = tmp_path / "x.vcf.gz.csi"
    renomeado.write_bytes(tbi)
    _reprova(formatos.validar("indice_vcf", renomeado), ".csi com conteúdo TBI")
    truncado = tmp_path / "t.tbi"
    truncado.write_bytes(tbi[:100])
    v = formatos.validar("indice_vcf", truncado)
    assert not v.ok and any("EOF" in f or "truncado" in f for f in v.falhas), v.falhas
    comum = tmp_path / "g.tbi"
    comum.write_bytes(gzip.compress(bgzf.inteiro(tbi)))
    _reprova(formatos.validar("indice_vcf", comum), "não é BGZF")
    vazio = tmp_path / "v.tbi"
    _gravar_indice(vazio, b"TBI\x01" + struct.pack("<8i", 0, 2, 1, 2, 0, 35, 0, 0) + struct.pack("<Q", 0))
    _reprova(formatos.validar("indice_vcf", vazio), "nenhum contig com dados")


# =========================================================== vcf_anotado_snpeff
_ANN_CAB = (b"##fileformat=VCFv4.2\n##SnpEffVersion=\"5.2\"\n"
            b"##INFO=<ID=ANN,Number=.,Type=String,Description=\"a\">\n"
            b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n")


def _anotado(tmp_path: Path, corpo: bytes, cab: bytes = _ANN_CAB, nome: str = "anotado.vcf.gz") -> Path:
    p = tmp_path / nome
    p.write_bytes(gzip.compress(cab + corpo))
    return p


_REG_ANN = b"14\t100\t.\tA\tC\t.\t.\tAF=0.1;ANN=C|intron_variant|MODIFIER|G|G1|t|T1|p|1/2||||||\n"


def test_anotado_controles(tmp_path):
    v = formatos.validar("vcf_anotado_snpeff", _anotado(tmp_path, _REG_ANN * 3))
    assert v.ok and v.perfil["n_registros"] == 3 and v.perfil["indexavel"] is False, v.falhas


def test_anotado_selado_passa():
    v = formatos.validar("vcf_anotado_snpeff", _exige(E_DEMO / "anotacao" / "anotado.vcf.gz"))
    assert v.ok and v.perfil["n_registros"] == 52_881 and v.perfil["n_erro_cromossomo"] == 0, v.falhas


def test_anotado_canarios(tmp_path):
    bom = gzip.compress(_ANN_CAB + _REG_ANN * 2000)
    truncado = tmp_path / "trunc.vcf.gz"
    truncado.write_bytes(bom[:len(bom) // 2])
    _reprova(formatos.validar("vcf_anotado_snpeff", truncado), "gzip truncado")
    _reprova(formatos.validar("vcf_anotado_snpeff",
                              _anotado(tmp_path, _REG_ANN, cab=_ANN_CAB.replace(b"##SnpEffVersion", b"##Outro"),
                                       nome="sv.vcf.gz")), "##SnpEffVersion")
    erro = _REG_ANN.replace(b"||||||\n", b"|||||ERROR_CHROMOSOME_NOT_FOUND|\n")
    _reprova(formatos.validar("vcf_anotado_snpeff", _anotado(tmp_path, _REG_ANN + erro, nome="e.vcf.gz")),
             "ERROR_CHROMOSOME_NOT_FOUND")
    _reprova(formatos.validar("vcf_anotado_snpeff", _anotado(tmp_path, b"", nome="z.vcf.gz")), "0 registros")
    sem_ann = b"14\t100\t.\tA\tC\t.\t.\tAF=0.1\n"
    _reprova(formatos.validar("vcf_anotado_snpeff", _anotado(tmp_path, sem_ann, nome="n.vcf.gz")), "nenhum registro com ANN")
    texto = tmp_path / "t.vcf.gz"
    texto.write_bytes(_ANN_CAB + _REG_ANN)
    _reprova(formatos.validar("vcf_anotado_snpeff", texto), "não é gzip")
    pos_gigante = b"14\t" + b"9" * 5000 + b"\t.\tA\tC\t.\t.\tANN=x\n"
    _reprova(formatos.validar("vcf_anotado_snpeff", _anotado(tmp_path, pos_gigante, nome="p.vcf.gz")), "POS")


# ============================================================ painel_simbolos
def test_painel_controles(tmp_path):
    v = formatos.validar("painel_simbolos", PAINEL)
    assert v.ok and v.perfil["n_simbolos"] == 14 and PAINEL.stat().st_size == 387, v.falhas
    v = formatos.validar("painel_simbolos", _texto(tmp_path, "painel.txt", "# x\nDGAT1\r\nISG12(B)\n\n"))
    assert v.ok and v.perfil["simbolos"] == ["DGAT1", "ISG12(B)"] and v.perfil["crlf"], v.falhas


@pytest.mark.parametrize("conteudo,trecho", [
    ("\ufeffDGAT1\nPRLR\n", "BOM"),
    ("DGAT1\n  # nota\n", "espaço antes do '#'"),
    ("DGAT1 HSF1\n", "não é UM símbolo"),
    ("DGAT1\t14\n", "não é UM símbolo"),
    ("DGAT1\ndgat1\n", "repetido"),
    ("# só comentário\n", "nenhum símbolo"),
    # crítica #5a: separadores que só o splitlines corta, procurados ANTES dele
    ("DGAT1\x0bHSF1\n", "separador de linha incomum"),
    ("DGAT1\u2028HSF1\n", "separador de linha incomum"),
    ("DGAT1\x1cHSF1\n", "separador de linha incomum"),
], ids=["bom", "comentario_indentado", "dois_simbolos", "tab", "repetido_caixa", "so_comentario",
        "x0b", "u2028", "x1c"])
def test_painel_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("painel_simbolos", _texto(tmp_path, "painel.txt", conteudo)), trecho)


def test_separador_procurado_depois_do_splitlines_era_codigo_morto():
    """Prova da crítica #5a: o splitlines já corta no \\x0b e no U+2028 — procurar o
    separador nas linhas que ele devolve nunca acha nada (o esboço aprovava '3 símbolos')."""
    for texto in ("DGAT1\x0bHSF1\n", "DGAT1\u2028HSF1\n"):
        assert not any(formatos._SEP_SPLITLINES.search(l) for l in texto.splitlines())
        assert formatos._SEP_SPLITLINES.search(texto)


def test_painel_tetos(tmp_path):
    muitos = "\n".join(f"G{i}" for i in range(formatos._MAX_SIMBOLOS + 1)) + "\n"
    _reprova(formatos.validar("painel_simbolos", _texto(tmp_path, "m.txt", muitos)), "mais de 10000 símbolos")
    grande = _texto(tmp_path, "g.txt", "DGAT1\n" + "#" * (formatos._MAX_PAINEL + 1) + "\n")
    _reprova(formatos.validar("painel_simbolos", grande), "bytes")


def test_painel_adaptado_passa_no_conteudo_e_reprova_no_nome(tmp_path):
    p = _texto(tmp_path, "painel.adaptado.tsv", "DGAT1\n")
    assert formatos.validar("painel_simbolos", p).ok
    _reprova(formatos.validar_com_nome("painel_simbolos", p), "STEM")


# =================================================================== bed_regioes
def test_bed_controles(tmp_path):
    v = formatos.validar("bed_regioes", DEMO_BED)
    assert v.ok and v.perfil["n_regioes"] == 3 and DEMO_BED.stat().st_size == 112, v.falhas
    # crítica #7: CRLF passa (o bcftools 1.19 dá as mesmas contagens que LF)
    v = formatos.validar("bed_regioes", _texto(tmp_path, "crlf.bed", "# c\r\n14\t0\t100\r\n14\t200\t300\r\n"))
    assert v.ok and v.perfil["n_regioes"] == 2 and v.perfil["crlf"], v.falhas
    sobre = _texto(tmp_path, "s.bed", "14\t0\t100\tA\n14\t50\t150\tB\n1\t0\t10\tC\n")
    v = formatos.validar("bed_regioes", sobre)
    assert v.ok and v.perfil["n_sobreposicoes"] == 1 and v.perfil["pb_uniao"] == 160, v.perfil


def test_bed_termotolerancia_gerado_passa():
    v = formatos.validar("bed_regioes", _exige(E_PAINEIS / "termotolerancia.bed"))
    assert v.ok and v.perfil["n_regioes"] == 13 and v.perfil["n_sobreposicoes"] == 1, v.falhas


@pytest.mark.parametrize("conteudo,trecho", [
    ("track name=x\n14\t0\t100\n", "track/browser"),
    ("browser position 14\n14\t0\t100\n", "track/browser"),
    ("14\t100\t100\n", "início ≥ fim"),
    ("14\t1e5\t2e5\n", "inteiros decimais"),
    ("14 0 100\n", "3 colunas"),
    ("chrom\tstart\tend\n14\t0\t9\n", "inteiros decimais"),
    ("14\t0\t100\n\n", "linha vazia"),
    ("14 \t0\t100\n", "contig vazio ou com espaço"),
    ("", "nenhuma região"),
    ("14\t" + "9" * 5000 + "\t" + "9" * 5001 + "\n", "inteiros decimais"),  # int() > 4300 dígitos levantaria
    # crítica #7: '\r' isolado — o bcftools lê UMA linha e extrai 0 variantes com rc 0
    ("14\t0\t100\r14\t200\t300\n", "'\\r' isolado"),
    ("14\t0\t100\x0c14\t200\t300\n", "separador de linha incomum"),
], ids=["track", "browser", "ini_igual_fim", "1e5", "espaco", "cabecalho", "linha_vazia", "contig_espaco",
        "vazio", "int_gigante", "cr_isolado", "x0c"])
def test_bed_canarios(tmp_path, conteudo, trecho):
    p = tmp_path / "r.bed"
    p.write_bytes(conteudo.encode("utf-8"))
    v = formatos.bed_regioes(p) if not conteudo else formatos.validar("bed_regioes", p)
    _reprova(v, trecho)


def test_cr_isolado_era_quebra_de_linha_para_o_esboco(tmp_path):
    """Prova da crítica #7 por mutação do leitor: com newline='' (o _linhas do esboço) o '\\r'
    isolado vira fim de linha e o arquivo parece ter 2 regiões boas."""
    p = _texto(tmp_path, "cr.bed", "14\t0\t100\r14\t200\t300\n")
    with p.open(encoding="utf-8", newline="") as f:
        assert len(f.readlines()) == 2
    assert [l for _, l in formatos._linhas_lf(p, 10, "teto")] == ["14\t0\t100\r14\t200\t300"]


def test_bed_regioes_txt_passa_no_conteudo_e_reprova_no_nome(tmp_path):
    p = _texto(tmp_path, "regioes.txt", "14\t0\t100\n")
    assert formatos.validar("bed_regioes", p).ok
    _reprova(formatos.validar_com_nome("bed_regioes", p), "1-based")


# ===================================================== tabela_frequencias
_CAB_F = "CHROM\tPOS\tID\tREF\tALT\tAF\tMAF\tN_ALT\n"
_F_OK = _CAB_F + "14\t10\t.\tA\tC\t0.4\t0.4\t1\n14\t20\t.\tA\tC,G\t0.3\t0.3\t2\n14\t30\t.\tA\tC,*\t0.1\t0.1\t1\n"


def test_frequencias_controles(tmp_path):
    v = formatos.validar("tabela_frequencias", _texto(tmp_path, "f.tsv", _F_OK))
    assert v.ok and v.perfil == {"n_variantes": 3, "n_maf_ge_0_05": 3, "n_multialelicos": 1}, v.falhas


def test_frequencias_selada_passa():
    p = _exige(E_DEMO / "frequencias" / "frequencias.tsv")
    v = formatos.validar("tabela_frequencias", p)
    assert v.ok and v.perfil == {"n_variantes": 52_230, "n_maf_ge_0_05": 5_744, "n_multialelicos": 1_831}, v.falhas


@pytest.mark.parametrize("conteudo,trecho", [
    ("CHROM\tPOS\tID\tREF\tALT\tMAF\tAF\tN_ALT\n14\t10\t.\tA\tC\t0.4\t0.4\t1\n", "cabeçalho"),
    (_CAB_F + "14\t10\t.\tA\tC\t0.1\t0.1\t1\n14\t20\t.\tA\tC\t0.4\t0.4\t1\n", "MAF crescente"),
    (_CAB_F + "14\t10\t.\tA\tC,G\t0.6\t0.6\t2\n", "MAF fora"),
    (_CAB_F + "14\t10\t.\tA\tC\t0.3\t0.3\t2\n", "N_ALT"),
    (_CAB_F + "14\t10\t.\tA\tC,*\t0.3\t0.3\t2\n", "N_ALT"),
    (_CAB_F + "14\t10\t.\tA\tC\t0.3\t0.3\t1\n14\t10\t.\tA\tC\t0.3\t0.3\t1\n", "repetida"),
    (_CAB_F + "14\t10\t.\tA\tC\tnan\t0.3\t1\n", "finito"),
    (_CAB_F, "sem variantes"),
    (_CAB_F + "14\t" + "9" * 5000 + "\t.\tA\tC\t0.3\t0.3\t1\n", "POS"),
], ids=["cabecalho", "maf_crescente", "maf_06", "nalt_maior", "nalt_estrela", "chave_repetida", "nan",
        "so_cabecalho", "pos_gigante"])
def test_frequencias_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("tabela_frequencias", _texto(tmp_path, "f.tsv", conteudo)), trecho)


# ================================================= contagens top-15 (SnpEff e VEP)
def test_contagens_controles(tmp_path):
    v = formatos.validar("contagem_efeitos_snpeff",
                         _texto(tmp_path, "e.tsv", "EFEITO\tN\nintergenic_region\t9\nsplice_region_variant&intron_variant\t3\n"))
    assert v.ok and v.perfil["topo"] == "intergenic_region", v.falhas
    v = formatos.validar("contagem_consequencias_vep", _texto(tmp_path, "c.tsv", "CONSEQUENCIA\tN\nintron_variant\t9\n"))
    assert v.ok, v.falhas


def test_contagens_seladas_passam():
    v = formatos.validar("contagem_efeitos_snpeff", _exige(E_DEMO / "anotacao" / "efeitos.tsv"))
    assert v.ok and v.perfil["topo"] == "intergenic_region", v.falhas
    v = formatos.validar("contagem_consequencias_vep", _exige(E_DEMO / "vep" / "efeitos_vep.tsv"))
    assert v.ok and v.perfil["topo"] == "intron_variant", v.falhas


_SNP = "EFEITO\tN\n" + "".join(f"efeito_{i}\t{100 - i}\n" for i in range(15))


@pytest.mark.parametrize("formato,conteudo,trecho", [
    ("contagem_efeitos_snpeff", _SNP + "x_variant\t1\n", "16 linhas"),
    ("contagem_efeitos_snpeff", "EFEITO\tN\nintron_variant\t5\nintron_variant\t4\n", "repetido"),
    ("contagem_efeitos_snpeff", "EFEITO\tN\nintron_variant\t0\n", "inteiro ≥1"),
    ("contagem_efeitos_snpeff", "EFEITO\tN\na_variant\t3\nb_variant\t5\n", "crescente"),
    ("contagem_efeitos_snpeff", "EFEITO\tN\n\t5\n", "vazio"),
    ("contagem_efeitos_snpeff", "EFFECT\tN\nintron_variant\t3\n", "cabeçalho"),
    ("contagem_efeitos_snpeff", 'EFEITO\tN\n"intron_variant"\t3\n', "vocabulário"),
    ("contagem_consequencias_vep", "CONSEQUENCIA\tN\nintron_variant,NMD_transcript_variant\t9\n", "vírgula"),
    ("contagem_consequencias_vep", "CONSEQUENCIA\tN\nsplice_region_variant&intron_variant\t9\n", "vocabulário"),
], ids=["16_linhas", "repetido", "n_zero", "n_crescente", "termo_vazio", "cabecalho_ingles", "aspas",
        "vep_virgula", "vep_e_comercial"])
def test_contagens_canarios(tmp_path, formato, conteudo, trecho):
    _reprova(formatos.validar(formato, _texto(tmp_path, "c.tsv", conteudo)), trecho)


# =================================================================== tabela_vep
_CAB_VEP = ("## ENSEMBL VARIANT EFFECT PREDICTOR v113.0\n## Output produced at 2026-09-02\n"
            "#Uploaded_variation\tLocation\tAllele\tGene\tFeature\tFeature_type\tConsequence\tExtra\n")
_VEP_OK = (_CAB_VEP + "14_100_A/C\t14:100\tC\tG1\tT1\tTranscript\tintron_variant\t-\n"
           "14_100_A/C\t14:100\tC\tG1\tT2\tTranscript\tintron_variant,NMD_transcript_variant\t-\n"
           "14_200_AT/A\t14:201-202\t-\tG2\tT3\tTranscript\tupstream_gene_variant\t-\n")


def test_vep_controles(tmp_path):
    v = formatos.validar("tabela_vep", _texto(tmp_path, "vep.tsv", _VEP_OK))
    assert v.ok and v.perfil["n_variantes"] == 2 and v.perfil["consequencia_topo"] == "intron_variant", v.falhas


def test_vep_selado_passa_e_recontagem_bate_com_efeitos_vep():
    v = formatos.validar("tabela_vep", _exige(E_DEMO / "vep" / "vep.tsv"))
    assert v.ok and v.perfil["n_variantes"] == 52_230 and v.perfil["n_consequencias_distintas"] == 26, v.falhas
    top = [l.split("\t") for l in (E_DEMO / "vep" / "efeitos_vep.tsv").read_text(encoding="utf-8").splitlines()[1:]]
    assert [[t, int(n)] for t, n in top] == v.perfil["recontagem_top15"]


@pytest.mark.parametrize("conteudo,trecho", [
    (_VEP_OK.replace("Consequence", "Csq"), "ausentes"),
    (_CAB_VEP + "14_100_A/C\t14:100\tC\tG1\tT1\tTranscript\tintron_variant\n", "nº de campos"),
    (_CAB_VEP + "14_100_A/C\t14-100\tC\tG1\tT1\tTranscript\tintron_variant\t-\n", "Location"),
    (_CAB_VEP, "nenhuma linha"),
], ids=["sem_consequence", "campo_a_menos", "location", "so_cabecalho"])
def test_vep_canarios(tmp_path, conteudo, trecho):
    _reprova(formatos.validar("tabela_vep", _texto(tmp_path, "vep.tsv", conteudo)), trecho)


# =================================================================== checagens
def _contig_trocado(tmp_path: Path, nome: str, de: bytes, para: bytes) -> Path:
    cab, reg = _demo()
    cab2 = [x.replace(b"##contig=<ID=" + de + b",", b"##contig=<ID=" + para + b",") for x in cab]
    reg2 = [para + x[len(de):] for x in reg[:500]]
    return sintetico(tmp_path, nome, cab=cab2, reg=reg2)


def test_checagens_controles():
    assert formatos.vcf_tem_info_af(DEMO).ok
    v = formatos.vcf_contigs_ars_ucd(DEMO)
    assert v.ok and v.perfil["contigs"] == ["14"] and v.perfil["fonte"] == "indice", v
    v = formatos.vcf_e_bed(DEMO, DEMO_BED)
    assert v.ok and v.perfil["n_regioes_certamente_vazias"] == 0, v


def test_chrmt_real_passa_com_aviso():
    v = formatos.vcf_contigs_ars_ucd(_exige(BCF_MT))
    assert v.ok and v.perfil["contigs_sem_par_na_anotacao"] == ["M"] and v.perfil["avisos"], v


def test_contig_m_passa_com_aviso_e_nao_casa_com_bed_em_mt(tmp_path):
    m = _contig_trocado(tmp_path, "m.vcf.gz", b"14", b"M")
    v = formatos.vcf_contigs_ars_ucd(m)
    assert v.ok and v.perfil["avisos"] == ["M: o GTF/SnpEff/VEP usam 'MT'"], v
    _reprova(formatos.vcf_e_bed(m, _texto(tmp_path, "mt.bed", "MT\t0\t16000\tmito\n")), "ausentes do cabeçalho")


def test_checagens_canarios(tmp_path):
    cab, reg = _demo()
    sem_af = sintetico(tmp_path, "sem_af.vcf.gz", cab=[x for x in cab if not x.startswith(b"##INFO=<ID=AF,")],
                       reg=list(reg[:200]))
    assert formatos.validar("vcf_indexado", sem_af).ok  # o FORMATO é válido; a checagem é que reprova
    _reprova(formatos.vcf_tem_info_af(sem_af), "##INFO=<ID=AF>")
    chr14 = _contig_trocado(tmp_path, "chr14.vcf.gz", b"14", b"chr14")
    assert formatos.validar("vcf_indexado", chr14).ok
    _reprova(formatos.vcf_contigs_ars_ucd(chr14), "prefixo 'chr'")
    _reprova(formatos.vcf_e_bed(chr14, DEMO_BED), "ausentes do cabeçalho")
    _reprova(formatos.vcf_e_bed(DEMO, _texto(tmp_path, "longe.bed", "14\t50000000\t50100000\n")), "NENHUMA região")
    _reprova(formatos.vcf_e_bed(DEMO, _texto(tmp_path, "alem.bed", "14\t900000000\t900000100\n")),
             "depois do fim do contig")


def test_checagem_sobre_vcf_ilegivel_reprova(tmp_path):
    """Crítica #5c: o esboço ignorava r['falhas'] e aprovava a lista vazia de contigs."""
    p = tmp_path / "ilegivel.vcf.gz"
    p.write_bytes(b"\x1f\x8b\x08\x00" + b"\x00" * 100)
    for tipo in ("vcf_tem_info_af", "vcf_contigs_ars_ucd"):
        _reprova(formatos.CHECAGENS[tipo][0](p), "vcf:")
    _reprova(formatos.vcf_e_bed(p, DEMO_BED), "vcf:")


def test_checagem_nunca_levanta_o_decorador_e_que_segura(tmp_path, monkeypatch):
    """Crítica #6: runs._checagens chama a função crua. Prova por mutação: sem o decorador
    (__wrapped__) a mesma falha de leitura sobe como exceção."""
    def quebra(_caminho):
        raise IndexError("simulado")

    monkeypatch.setattr(formatos, "_ler_vcf", quebra)
    for tipo in ("vcf_tem_info_af", "vcf_contigs_ars_ucd"):
        func = formatos.CHECAGENS[tipo][0]
        _reprova(func(DEMO), "IndexError")
        with pytest.raises(IndexError):
            func.__wrapped__(DEMO)
    _reprova(formatos.vcf_e_bed(DEMO, DEMO_BED), "IndexError")
    with pytest.raises(IndexError):
        formatos.vcf_e_bed.__wrapped__(DEMO, DEMO_BED)


# =================================================== tabelas e regras de nome
def test_tabelas_em_sincronia():
    for f, sufixos in formatos.SUFIXOS.items():
        assert f in formatos.VALIDADORES and formatos.EXTENSOES[f] in sufixos, f
    # formato de PASTA (F6) não tem extensão: fica fora de EXTENSOES por definição (ESPEC F6 §2.1)
    assert set(formatos.EXTENSOES) == set(formatos.VALIDADORES) - set(formatos.DIRETORIOS)
    for f, por_sufixo in formatos.IRMAOS.items():
        assert set(por_sufixo) <= set(formatos.SUFIXOS[f]), f
    assert formatos.REFERENCIAVEIS <= set(formatos.VALIDADORES)
    assert set(formatos.CHECAGENS) == set(formatos.FORMATOS_CHECAGEM)
    for tipo, (_, campos) in formatos.CHECAGENS.items():
        assert set(campos) == set(formatos.FORMATOS_CHECAGEM[tipo]), tipo
    for f, chave in formatos.COLUNAS_CHAVE.items():
        assert set(chave) <= set(formatos.COLUNAS_GARANTIDAS[f]), f
    # tabela_vep fora (o fatos.ler_tsv leria a 1ª linha '##' como cabeçalho); frequências fora
    # das chaves (chave composta)
    assert "tabela_vep" not in formatos.COLUNAS_GARANTIDAS
    assert "tabela_frequencias" in formatos.COLUNAS_GARANTIDAS
    assert "tabela_frequencias" not in formatos.COLUNAS_CHAVE


@pytest.mark.parametrize("formato,nome,origem,trecho", [
    ("bed_regioes", "regioes.txt", False, "1-based"),
    ("bed_regioes", "regioes.txt", True, "arquivo de origem"),
    ("bed_regioes", "x.BED", False, "1-based"),  # o nome FINAL é da plataforma: estrito
    ("painel_simbolos", "painel.adaptado.tsv", False, "STEM"),
    ("painel_simbolos", "meu painel.txt", False, "STEM"),
    ("painel_simbolos", "genes.lst", True, ".txt"),
    ("vcf_indexado", "x.vcf", False, ".vcf.gz"),
    ("vcf_indexado", "x.gz", True, ".vcf.gz"),
    ("indice_vcf", "x.idx", False, ".tbi"),
    ("vcf_anotado_snpeff", "anotado.vcf", False, ".vcf.gz"),
])
def test_regra_de_nome_canarios(formato, nome, origem, trecho):
    falhas = formatos.regra_de_nome(formato, nome, origem=origem)
    assert falhas and any(trecho in f for f in falhas), falhas


@pytest.mark.parametrize("formato,nome,origem", [
    ("bed_regioes", "regioes.bed", False), ("bed_regioes", "termotolerancia.bed", True),
    # o htslib lê '.BED' 0-based (sufixo sem caixa; medido no bcftools 1.19): na origem passa
    ("bed_regioes", "x.BED", True), ("bed_regioes", "Painel.Bed", True),
    ("painel_simbolos", "painel.txt", False), ("painel_simbolos", "meu painel v2.txt", True),
    ("vcf_indexado", "x.vcf.gz", False), ("vcf_indexado", "Chr14-Run8-TAUIND-public.bcf", True),
    ("indice_vcf", "extraido.vcf.gz.tbi", False), ("indice_vcf", "x.bcf.csi", False),
    ("vcf_anotado_snpeff", "anotado.vcf.gz", False),
    # a transcriptômica não muda: formatos sem regra aceitam qualquer nome (o adaptador lê CSV)
    ("contagens_genes", "counts.adaptando.ab12.tsv", False), ("coldata", "coldata.csv", True),
])
def test_regra_de_nome_controles(formato, nome, origem):
    assert formatos.regra_de_nome(formato, nome, origem=origem) == []


def test_sufixo_de_destino_e_candidatos_irmaos():
    assert formatos.sufixo_de_destino("vcf_indexado", "Chr14.bcf") == ".bcf"
    assert formatos.sufixo_de_destino("vcf_indexado", "demo.vcf.gz") == ".vcf.gz"
    assert formatos.sufixo_de_destino("vcf_indexado", "x.gz") == ".vcf.gz"
    assert formatos.sufixo_de_destino("vcf_indexado", None) == ".vcf.gz"
    assert formatos.sufixo_de_destino("bed_regioes", "regioes.txt") == ".bed"
    assert formatos.sufixo_de_destino("coldata", "coldata.csv") == ".tsv"
    assert formatos.sufixo_de_destino("inventado", "x") == ".dat"
    assert formatos.candidatos_irmaos("vcf_indexado", "x.vcf.gz") == (".tbi", ".csi")
    assert formatos.candidatos_irmaos("vcf_indexado", "x.bcf") == (".csi",)
    assert formatos.candidatos_irmaos("vcf_indexado", "x.vcf") == ()
    assert formatos.candidatos_irmaos("bed_regioes", "x.bed") == ()


@pytest.mark.parametrize("formato,nome,esperado", [
    ("vcf_indexado", "Chr14-Run8-TAUIND-public.bcf", True),
    ("vcf_indexado", "demo.vcf.gz", True),
    # crítica #1: arquivos da raiz de referência que não são VCF não chegam a stat/sha
    ("vcf_indexado", "amostras_racas.csv", False),
    ("vcf_indexado", "biosamples_cache.json", False),
    ("vcf_indexado", "gtonly_1000bg.sh", False),
    ("vcf_indexado", ".gtonly.lock", False),
    # crítica #3: arquivo em construção pelo gtonly (depois movido por cima do .bcf)
    ("vcf_indexado", "Chr14-Run8-TAUIND-public.building.bcf", False),
    ("vcf_indexado", "Chr14.bcf.csi", False),
    ("bed_regioes", "x.bed", False),
])
def test_nome_de_referencia_ok(formato, nome, esperado):
    assert formatos.nome_de_referencia_ok(formato, nome) is esperado


def test_validar_com_nome_confere_a_origem(tmp_path):
    """Crítica #5b: na cópia, o nome final é '<entrada>.bed', escolhido pelo servidor — a regra
    só sobre ele nunca reprova. Com o nome da ORIGEM, o '.txt' 1-based reprova."""
    final = _texto(tmp_path, "regioes.bed", "14\t0\t100\n")
    assert formatos.validar_com_nome("bed_regioes", final).ok  # o que a regra antiga via
    _reprova(formatos.validar_com_nome("bed_regioes", final, nome_origem="regioes.txt"), "nome: arquivo de origem")
    assert formatos.validar_com_nome("bed_regioes", final, nome_origem="painel_demo.bed").ok
    # o conteúdo continua sendo julgado junto
    ruim = _texto(tmp_path, "ruim.bed", "14\t5\t5\n")
    v = formatos.validar_com_nome("bed_regioes", ruim, nome_origem="r.txt")
    assert not v.ok and any("início ≥ fim" in f for f in v.falhas) and any(f.startswith("nome:") for f in v.falhas)


def test_falhas_nao_ecoam_conteudo(tmp_path):
    """Um caminho mal escolhido não vira leitor de arquivo: nem o valor do segredo nem a linha
    voltam nas falhas (nem no perfil de um arquivo reprovado)."""
    segredo = "OMICA_API_TOKEN=sk-segredo-123"
    for formato, nome in [("painel_simbolos", "p.txt"), ("bed_regioes", "r.bed"), ("tabela_frequencias", "f.tsv"),
                          ("contagem_efeitos_snpeff", "e.tsv"), ("tabela_vep", "v.tsv")]:
        v = formatos.validar(formato, _texto(tmp_path, nome, segredo + "\n" + segredo + "\tx\ty\n"))
        assert not v.ok
        assert "segredo" not in repr(v.to_dict()), (formato, v)
