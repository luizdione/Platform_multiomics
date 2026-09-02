"""extracao_regioes.py — extrai variantes de um VCF/BCF pelas regiões de um BED.

Migração curada (Fase 2) da rotina de extração da genomes_platform
(`bcftools view -R painel.bed`), reescrita sobre o contrato de jobs da ômica.

Uso:
  python3 modules/genomica/scripts/extracao_regioes.py \
      --entrada <vcf/bcf> --bed <painel.bed> --saida <dir>
"""
from __future__ import annotations

import argparse
from pathlib import Path

from _comum import contar_variantes, escrever_resumo, log, rodar


def main() -> None:
    p = argparse.ArgumentParser(description="Extração de variantes por painel de regiões")
    p.add_argument("--entrada", required=True, help="VCF/BCF de entrada (com índice)")
    p.add_argument("--bed", required=True, help="BED com as regiões do painel")
    p.add_argument("--saida", required=True, help="diretório de saída")
    args = p.parse_args()

    entrada, bed, saida = Path(args.entrada), Path(args.bed), Path(args.saida)
    if not entrada.exists():
        raise SystemExit(f"entrada não encontrada: {entrada}")
    if not bed.exists():
        raise SystemExit(f"BED não encontrado: {bed}")
    saida.mkdir(parents=True, exist_ok=True)

    regioes = [l for l in bed.read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.startswith(("#", "track"))]
    log(f"Painel: {len(regioes)} regiões ({bed.name})")

    extraido = saida / "extraido.vcf.gz"
    rodar(["bcftools", "view", "-R", str(bed), "-O", "z", "-o", str(extraido), str(entrada)])
    rodar(["bcftools", "index", "-t", "-f", str(extraido)])
    n = contar_variantes(extraido)
    log(f"Variantes extraídas: {n}")

    escrever_resumo(saida, "extracao_resumo.json", {
        "entrada": entrada.name,
        "painel_bed": bed.name,
        "n_regioes": len(regioes),
        "n_variantes_extraidas": n,
        "vcf_extraido": str(extraido),
    })


if __name__ == "__main__":
    main()
