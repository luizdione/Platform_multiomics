"""baixar_sra.py — baixa (parcialmente) uma corrida SRA como FASTQ comprimido.

Migração curada do download SRA da genomes_platform (SRA Toolkit, mesma fonte
do Dockerfile legado). `--spots N` usa fastq-dump -X para baixar SÓ os N
primeiros spots — é assim que o demo versionado (25 mil spots do SRR24953465,
o inverno-controle da granulosa) foi gerado. Requer rede.

Uso:
  python3 modules/transcriptomica/scripts/baixar_sra.py \
      --srr SRR24953465 --spots 25000 --saida <dir>
"""
from __future__ import annotations

import argparse
from pathlib import Path

from _comum import escrever_resumo, log, rodar


def main() -> None:
    p = argparse.ArgumentParser(description="Download parcial/total de corrida SRA")
    p.add_argument("--srr", required=True)
    p.add_argument("--spots", type=int, default=25000,
                   help="0 = corrida inteira (cuidado com o tamanho)")
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    cmd = ["fastq-dump", "--split-files", "--gzip", "--outdir", str(saida)]
    if args.spots > 0:
        cmd += ["-X", str(args.spots)]
    cmd.append(args.srr)
    log(f"Baixando {args.srr} ({args.spots or 'todos'} spots)…")
    rodar(cmd)

    arquivos = sorted(saida.glob(f"{args.srr}*.fastq.gz"))
    if not arquivos:
        raise SystemExit("fastq-dump terminou mas nenhum FASTQ apareceu")
    for a in arquivos:
        log(f"  {a.name}: {a.stat().st_size / 1e6:.1f} MB")

    escrever_resumo(saida, f"{args.srr}_resumo.json", {
        "srr": args.srr,
        "spots": args.spots or "todos",
        "arquivos": {a.name: round(a.stat().st_size / 1e6, 2) for a in arquivos},
    })


if __name__ == "__main__":
    main()
