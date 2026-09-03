"""salmon_quant.py — quantificação de transcritos com Salmon (single sample).

Migração curada: o acervo nf-core do sub03 (34 amostras) foi mantido em
E:\\data\\rnaseq e é REAPROVEITADO como resultado; este script cobre o caso
didático/incremental (quantificar UMA amostra nova contra o índice bovino).
O índice é construído uma vez em refs/salmon_bostau a partir do
genome.transcripts.fa que o próprio nf-core legado deixou (--construir-indice).

Uso:
  python3 modules/transcriptomica/scripts/salmon_quant.py \
      --fastq1 <R1.fastq.gz> [--fastq2 <R2.fastq.gz>] \
      --indice /data/omica/refs/salmon_bostau --saida <dir> [--construir-indice]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _comum import escrever_resumo, log, rodar

TRANSCRITOS_LEGADO = "/data/rnaseq/nfcore_output/sub03/genome/genome.transcripts.fa"


def main() -> None:
    p = argparse.ArgumentParser(description="Salmon quant (SE ou PE)")
    p.add_argument("--fastq1", required=True)
    p.add_argument("--fastq2", default="")
    p.add_argument("--indice", default="/data/omica/refs/salmon_bostau")
    p.add_argument("--construir-indice", action="store_true",
                   help=f"constrói o índice a partir de {TRANSCRITOS_LEGADO} se ausente")
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    fq1, indice, saida = Path(args.fastq1), Path(args.indice), Path(args.saida)
    if not fq1.exists():
        raise SystemExit(f"FASTQ não encontrado: {fq1}")
    if not indice.exists():
        if not args.construir_indice:
            raise SystemExit(f"índice ausente: {indice} (use --construir-indice na 1ª vez)")
        if not Path(TRANSCRITOS_LEGADO).exists():
            raise SystemExit(f"transcritos legados ausentes: {TRANSCRITOS_LEGADO}")
        log("Construindo índice Salmon (1ª vez — alguns minutos)…")
        rodar(["salmon", "index", "-t", TRANSCRITOS_LEGADO, "-i", str(indice),
               "-p", str(args.threads)])
    saida.mkdir(parents=True, exist_ok=True)

    cmd = ["salmon", "quant", "-i", str(indice), "-l", "A",
           "-p", str(args.threads), "--validateMappings", "-o", str(saida)]
    if args.fastq2:
        cmd += ["-1", str(fq1), "-2", args.fastq2]
    else:
        cmd += ["-r", str(fq1)]
    rodar(cmd)

    quant = saida / "quant.sf"
    n_transcritos = sum(1 for _ in quant.open(encoding="utf-8")) - 1
    meta = saida / "aux_info" / "meta_info.json"
    mapeamento = None
    if meta.exists():
        dados = json.loads(meta.read_text(encoding="utf-8"))
        mapeamento = dados.get("percent_mapped")
    log(f"Transcritos quantificados: {n_transcritos}; % mapeada: {mapeamento}")

    escrever_resumo(saida, "quant_resumo.json", {
        "fastq1": fq1.name,
        "fastq2": Path(args.fastq2).name if args.fastq2 else None,
        "indice": str(indice),
        "n_transcritos": n_transcritos,
        "percent_mapeada": mapeamento,
        "quant_sf": str(quant),
    })


if __name__ == "__main__":
    main()
