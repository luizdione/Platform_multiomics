"""coordenadas_painel.py — lista de símbolos de genes → BED do painel (±pad).

Migração curada da etapa "coordenadas Ensembl" da genomes_platform, com uma
CORREÇÃO DE FONTE decidida em 2026-09-02: o Ensembl REST atual serve o assembly
ARS-UCD2.0 (verificado ao vivo: DGAT1 em 14:603.035-612.781), incompatível com
os VCFs do 1000 Bull Genomes Run 8 (ARS-UCD1.2). A fonte passa a ser o
genes.gtf.gz do banco SnpEff LOCAL (ARS-UCD1.3.113 — autossomos idênticos ao
1.2): determinística, offline e no MESMO sistema de coordenadas dos VCFs.

Uso:
  python3 modules/genomica/scripts/coordenadas_painel.py \
      --painel modules/genomica/data/paineis/termotolerancia.txt \
      --gtf /data/snpeff_data/ARS-UCD1.3.113/genes.gtf.gz \
      --pad 15000 --saida <dir>
"""
from __future__ import annotations

import argparse
import gzip
import re
from pathlib import Path

from _comum import escrever_resumo, log

RE_GENE_NAME = re.compile(r'gene_name "([^"]+)"')
RE_GENE_ID = re.compile(r'gene_id "([^"]+)"')


def main() -> None:
    p = argparse.ArgumentParser(description="Painel de genes → BED (fonte: GTF local do SnpEff)")
    p.add_argument("--painel", required=True, help="txt com um símbolo de gene por linha")
    p.add_argument("--gtf", default="/data/snpeff_data/ARS-UCD1.3.113/genes.gtf.gz")
    p.add_argument("--pad", type=int, default=15000,
                   help="flanco em pb de cada lado (convenção do painel 108: 15000)")
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    painel_path, gtf, saida = Path(args.painel), Path(args.gtf), Path(args.saida)
    if not painel_path.exists():
        raise SystemExit(f"painel não encontrado: {painel_path}")
    if not gtf.exists():
        raise SystemExit(f"GTF não encontrado: {gtf} (montar E:\\data como /data)")
    saida.mkdir(parents=True, exist_ok=True)

    alvos = {l.strip().upper() for l in painel_path.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")}
    log(f"Painel {painel_path.name}: {len(alvos)} símbolos; flanco ±{args.pad} pb")

    achados: dict[str, tuple[str, int, int, str]] = {}
    with gzip.open(gtf, "rt", encoding="utf-8", errors="replace") as f:
        for linha in f:
            if linha.startswith("#"):
                continue
            campos = linha.split("\t")
            if len(campos) < 9 or campos[2] != "gene":
                continue
            m_nome = RE_GENE_NAME.search(campos[8])
            if not m_nome:
                continue
            simbolo = m_nome.group(1).upper()
            if simbolo in alvos and simbolo not in achados:
                gid = RE_GENE_ID.search(campos[8])
                achados[simbolo] = (campos[0], int(campos[3]), int(campos[4]),
                                    gid.group(1) if gid else "")
                if len(achados) == len(alvos):
                    break

    nao_encontrados = sorted(alvos - set(achados))
    bed = saida / f"{painel_path.stem}.bed"
    with bed.open("w", encoding="utf-8", newline="\n") as f:
        for simbolo in sorted(achados, key=lambda s: (achados[s][0], achados[s][1])):
            crom, ini, fim, gid = achados[simbolo]
            f.write(f"{crom}\t{max(0, ini - 1 - args.pad)}\t{fim + args.pad}\t{simbolo}\t{gid}\n")
    log(f"Genes localizados: {len(achados)}/{len(alvos)}"
        + (f" — NÃO encontrados: {', '.join(nao_encontrados)}" if nao_encontrados else ""))

    escrever_resumo(saida, f"{painel_path.stem}_resumo.json", {
        "painel": painel_path.name,
        "fonte": f"GTF local {gtf.name} (ARS-UCD1.3; autossomos = ARS-UCD1.2 dos VCFs Run 8)",
        "aviso_ensembl": "Ensembl REST atual serve ARS-UCD2.0 — NÃO usar para estes VCFs",
        "pad_pb": args.pad,
        "n_simbolos": len(alvos),
        "n_localizados": len(achados),
        "nao_encontrados": nao_encontrados,
        "bed": str(bed),
    })


if __name__ == "__main__":
    main()
