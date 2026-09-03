"""qc_fastq.py — FastQC por arquivo + MultiQC consolidado de um diretório FASTQ.

Migração curada do QC da genomes_platform — que estava QUEBRADO no legado (a
página 5 existia mas o endpoint não rodava). Aqui: FastQC (zip, mesma fonte do
Dockerfile legado) + MultiQC (pip) na imagem omica-bioinfo.

Uso:
  python3 modules/transcriptomica/scripts/qc_fastq.py --dir <dir com *.fastq.gz> --saida <dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _comum import escrever_resumo, log, rodar


def main() -> None:
    p = argparse.ArgumentParser(description="FastQC + MultiQC")
    p.add_argument("--dir", required=True)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    pasta, saida = Path(args.dir), Path(args.saida)
    fastqs = sorted(list(pasta.glob("*.fastq.gz")) + list(pasta.glob("*.fq.gz")))
    if not fastqs:
        raise SystemExit(f"nenhum FASTQ em {pasta}")
    saida.mkdir(parents=True, exist_ok=True)
    log(f"FastQC em {len(fastqs)} arquivo(s) de {pasta}")

    rodar(["fastqc", "--outdir", str(saida), "--quiet", *[str(f) for f in fastqs]])
    rodar(["multiqc", str(saida), "--outdir", str(saida), "--force", "--no-ansi"])

    relatorio = saida / "multiqc_report.html"
    stats = saida / "multiqc_data" / "multiqc_fastqc.txt"
    n_seqs_total = None
    if stats.exists():
        linhas = stats.read_text(encoding="utf-8").splitlines()
        cab = linhas[0].split("\t")
        if "Total Sequences" in cab:
            col = cab.index("Total Sequences")
            try:
                n_seqs_total = sum(int(float(l.split("\t")[col])) for l in linhas[1:] if l.strip())
            except (ValueError, IndexError):
                n_seqs_total = None
    log(f"MultiQC: {relatorio}")

    escrever_resumo(saida, "qc_resumo.json", {
        "diretorio": str(pasta),
        "n_fastq": len(fastqs),
        "arquivos": [f.name for f in fastqs],
        "n_sequencias_total": n_seqs_total,
        "relatorio_multiqc": str(relatorio),
    })


if __name__ == "__main__":
    main()
