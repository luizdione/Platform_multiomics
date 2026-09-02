"""intersecao_dmc_painel.py — cruza DMCs com o painel 108 (janelas ±15 kb).

Migração curada da "Fase 2 · Interseção" da epigenome_platform: a versão antiga
rotulava "BEDTools" mas era Python puro — mantemos Python puro, agora com o
painel 108 VERSIONADO no repo (contagem canônica; proveniência no _meta).

Uso:
  python3 modules/epigenomica/scripts/intersecao_dmc_painel.py \
      --dmc <dmc.tsv> --painel modules/epigenomica/data/paineis/painel_108.bed \
      --saida <dir>
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from _comum import escrever_resumo, log


def main() -> None:
    p = argparse.ArgumentParser(description="Interseção DMC × painel de genes")
    p.add_argument("--dmc", required=True, help="dmc.tsv do methylKit (chr,start,end,...,meth.diff)")
    p.add_argument("--painel", required=True, help="BED do painel (chrom,start,end,gene)")
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    dmc_path, painel, saida = Path(args.dmc), Path(args.painel), Path(args.saida)
    if not dmc_path.exists():
        raise SystemExit(f"DMCs não encontradas: {dmc_path} (rode a etapa de DMC antes)")
    if not painel.exists():
        raise SystemExit(f"painel não encontrado: {painel}")
    saida.mkdir(parents=True, exist_ok=True)

    janelas: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for linha in painel.read_text(encoding="utf-8").splitlines():
        campos = linha.split("\t")
        if len(campos) >= 4:
            janelas[campos[0]].append((int(campos[1]), int(campos[2]), campos[3]))
    n_genes = sum(len(v) for v in janelas.values())
    log(f"Painel: {n_genes} janelas de genes; DMCs: {dmc_path.name}")

    contagem: dict[str, dict[str, int]] = defaultdict(lambda: {"n_dmc": 0, "hiper": 0, "hipo": 0})
    linhas_dmc = dmc_path.read_text(encoding="utf-8").splitlines()
    cab = linhas_dmc[0].split("\t")
    i_chr, i_start = cab.index("chr"), cab.index("start")
    i_diff = cab.index("meth.diff")
    n_dmc_total, n_em_painel = 0, 0
    for linha in linhas_dmc[1:]:
        campos = linha.split("\t")
        n_dmc_total += 1
        crom, pos = campos[i_chr], int(campos[i_start])
        diff = float(campos[i_diff])
        for ini, fim, gene in janelas.get(crom, ()):
            if ini <= pos <= fim:
                n_em_painel += 1
                contagem[gene]["n_dmc"] += 1
                contagem[gene]["hiper" if diff > 0 else "hipo"] += 1

    tsv = saida / "intersecao.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write("GENE\tN_DMC\tHIPER\tHIPO\n")
        for gene in sorted(contagem, key=lambda g: contagem[g]["n_dmc"], reverse=True):
            c = contagem[gene]
            f.write(f"{gene}\t{c['n_dmc']}\t{c['hiper']}\t{c['hipo']}\n")
    log(f"DMCs no painel: {n_em_painel}/{n_dmc_total} em {len(contagem)} genes")

    escrever_resumo(saida, "intersecao_resumo.json", {
        "painel": painel.name,
        "n_genes_painel": n_genes,
        "n_dmc_total": n_dmc_total,
        "n_dmc_no_painel": n_em_painel,
        "n_genes_com_dmc": len(contagem),
        "gene_top": max(contagem, key=lambda g: contagem[g]["n_dmc"]) if contagem else None,
        "tabela_tsv": str(tsv),
    })


if __name__ == "__main__":
    main()
