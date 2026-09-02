"""frequencias.py — tabela de frequências alélicas (AF/MAF) de um VCF.

Migração curada (Fase 2): usa as tags INFO AF/AC/AN já presentes nos VCFs do
1000 Bull Genomes (Run 8) — por isso funciona no demo sem genótipos. Gera um
TSV ordenado por MAF e um resumo JSON; a UI lê ambos via omica/results.py.

Uso:
  python3 modules/genomica/scripts/frequencias.py --entrada <vcf> --saida <dir>
"""
from __future__ import annotations

import argparse
from pathlib import Path

from _comum import escrever_resumo, log, rodar


def main() -> None:
    p = argparse.ArgumentParser(description="Frequências alélicas (AF/MAF) via INFO")
    p.add_argument("--entrada", required=True, help="VCF com INFO/AF (ex.: saída da extração)")
    p.add_argument("--saida", required=True, help="diretório de saída")
    p.add_argument("--maf-corte", type=float, default=0.05, help="corte de MAF para o resumo")
    args = p.parse_args()

    entrada, saida = Path(args.entrada), Path(args.saida)
    if not entrada.exists():
        raise SystemExit(f"entrada não encontrada: {entrada} (rode a extração antes)")
    saida.mkdir(parents=True, exist_ok=True)

    bruto = rodar(["bcftools", "query", "-f",
                   "%CHROM\t%POS\t%ID\t%REF\t%ALT\t%INFO/AF\n", str(entrada)])

    linhas: list[tuple[str, str, str, str, str, float, float]] = []
    sem_af = 0
    for linha in bruto.splitlines():
        campos = linha.split("\t")
        if len(campos) != 6:
            continue
        crom, pos, vid, ref, alt, af_txt = campos
        try:
            af = float(af_txt.split(",")[0])  # multialélico: 1º ALT
        except ValueError:
            sem_af += 1
            continue
        maf = min(af, 1.0 - af)
        linhas.append((crom, pos, vid, ref, alt, af, maf))

    linhas.sort(key=lambda x: x[6], reverse=True)
    tsv = saida / "frequencias.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write("CHROM\tPOS\tID\tREF\tALT\tAF\tMAF\n")
        for crom, pos, vid, ref, alt, af, maf in linhas:
            f.write(f"{crom}\t{pos}\t{vid}\t{ref}\t{alt}\t{af:.6g}\t{maf:.6g}\n")

    n = len(linhas)
    n_comuns = sum(1 for x in linhas if x[6] >= args.maf_corte)
    maf_media = (sum(x[6] for x in linhas) / n) if n else 0.0
    log(f"Variantes com AF: {n} (sem AF: {sem_af}); MAF ≥ {args.maf_corte}: {n_comuns}")

    escrever_resumo(saida, "frequencias_resumo.json", {
        "entrada": entrada.name,
        "n_variantes_com_af": n,
        "n_sem_af": sem_af,
        "maf_corte": args.maf_corte,
        "n_maf_acima_do_corte": n_comuns,
        "maf_media": round(maf_media, 5),
        "tabela_tsv": str(tsv),
    })


if __name__ == "__main__":
    main()
