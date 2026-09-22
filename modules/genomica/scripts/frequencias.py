"""frequencias.py — tabela de frequências alélicas (AF/MAF) de um VCF.

Migração curada (Fase 2): usa as tags INFO AF/AC/AN já presentes nos VCFs do
1000 Bull Genomes (Run 8) — por isso funciona no demo sem genótipos. Gera um
TSV ordenado por MAF e um resumo JSON; a UI lê ambos via omica/results.py.

Uso:
  python3 modules/genomica/scripts/frequencias.py --entrada <vcf> --saida <dir>
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from _comum import escrever_resumo, log, rodar


def maf_de_afs(afs: list[float]) -> float:
    """MAF = frequência do SEGUNDO alelo mais comum do sítio.

    Em sítio bialélico isso é min(AF, 1-AF). Em multialélico NÃO é: com ALT1=0,5
    e ALT2=0,3, o REF vale 1-0,8 = 0,2 e a MAF é 0,3 — a fórmula antiga
    (min(AF1, 1-AF1)) devolvia 0,5, e como a tabela é ORDENADA por MAF esses
    sítios subiam indevidamente para o topo exibido na UI.
    """
    freqs = sorted([max(0.0, 1.0 - sum(afs))] + list(afs), reverse=True)
    return freqs[1] if len(freqs) > 1 else 0.0


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
    # O alelo `*` (spanning deletion) é um marcador de sobreposição com um INDEL
    # vizinho, não um alelo do sítio: contar o AF dele como alelo inflaria a
    # contagem de variantes "comuns" e mudaria a MAF (revisão de 2026-09-14).

    linhas: list[tuple[str, str, str, str, str, float, float, int]] = []
    sem_af = 0
    n_multialelico = 0
    n_spanning = 0
    for linha in bruto.splitlines():
        campos = linha.split("\t")
        if len(campos) != 6:
            continue
        crom, pos, vid, ref, alt, af_txt = campos
        alts = alt.split(",")
        try:
            pares = [(a, float(x)) for a, x in zip(alts, af_txt.split(","))]
        except ValueError:
            sem_af += 1
            continue
        n_spanning += sum(1 for a, _ in pares if a == "*")
        afs = [v for a, v in pares if a != "*"]
        if not afs or any(not math.isfinite(a) for a in afs):
            sem_af += 1
            continue
        # Em sítio multialélico, 1-AF1 NÃO é a frequência do REF (há AF2..AFn no
        # meio): a MAF é a frequência do SEGUNDO alelo mais comum entre
        # [REF] + [ALT1..ALTn], com freq(REF) = 1 - soma(AF).
        if len(afs) > 1:
            n_multialelico += 1
        maf = maf_de_afs(afs)
        af = afs[0]
        linhas.append((crom, pos, vid, ref, alt, af, maf, len(afs)))

    linhas.sort(key=lambda x: x[6], reverse=True)
    tsv = saida / "frequencias.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write("CHROM\tPOS\tID\tREF\tALT\tAF\tMAF\tN_ALT\n")
        for crom, pos, vid, ref, alt, af, maf, n_alt in linhas:
            f.write(f"{crom}\t{pos}\t{vid}\t{ref}\t{alt}\t{af:.6g}\t{maf:.6g}\t{n_alt}\n")

    n = len(linhas)
    n_comuns = sum(1 for x in linhas if x[6] >= args.maf_corte)
    maf_media = (sum(x[6] for x in linhas) / n) if n else 0.0
    log(f"Variantes com AF: {n} (sem AF: {sem_af}); MAF ≥ {args.maf_corte}: {n_comuns}")

    escrever_resumo(saida, "frequencias_resumo.json", {
        "entrada": entrada.name,
        "n_variantes_com_af": n,
        "n_sem_af": sem_af,
        "n_multialelicos": n_multialelico,
        "n_alelos_spanning_descartados": n_spanning,
        "definicao_maf": "frequência do 2º alelo mais comum entre REF(1-ΣAF) e ALT1..ALTn",
        "maf_corte": args.maf_corte,
        "n_maf_acima_do_corte": n_comuns,
        "maf_media": round(maf_media, 5),
        "tabela_tsv": str(tsv),
    })


if __name__ == "__main__":
    main()
