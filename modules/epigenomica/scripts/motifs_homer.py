"""motifs_homer.py — motifs de TF (HOMER, known motifs) nas CpGs diferenciais.

Migração curada da etapa HOMER da epigenome_platform. Como o demo produz POUCAS
DMCs estritas (|diff|>15%, q<0,05 → 1 no CD4 demo), o script seleciona as
top-N CpGs por evidência com limiar RELAXADO E DECLARADO no resumo — uso
exploratório honesto; para o dado completo do paper, aperte os parâmetros.
Genoma: FASTA do banco SnpEff local (mesmo assembly dos demais módulos);
preparse persistido em {dados}/refs/homer_preparsed (1ª vez demora).

Uso:
  python3 modules/epigenomica/scripts/motifs_homer.py \
      --cpgs <todas_cpgs.tsv> --q-max 0.25 --top-n 200 --flanco 100 \
      --genoma /data/snpeff_data/ARS-UCD1.3.113/sequences.fa --saida <dir>
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from _comum import escrever_resumo, log, rodar


def main() -> None:
    p = argparse.ArgumentParser(description="HOMER known motifs em CpGs diferenciais")
    p.add_argument("--cpgs", required=True, help="todas_cpgs.tsv do methylKit")
    p.add_argument("--q-max", type=float, default=0.25)
    p.add_argument("--top-n", type=int, default=200)
    p.add_argument("--flanco", type=int, default=100)
    p.add_argument("--size", type=int, default=200)
    p.add_argument("--genoma", default="/data/snpeff_data/ARS-UCD1.3.113/sequences.fa")
    p.add_argument("--preparsed", default="/data/omica/refs/homer_preparsed")
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    cpgs_path, saida = Path(args.cpgs), Path(args.saida)
    if not cpgs_path.exists():
        raise SystemExit(f"tabela de CpGs não encontrada: {cpgs_path} (rode o DMC antes)")
    if not Path(args.genoma).exists():
        raise SystemExit(f"FASTA do genoma ausente: {args.genoma}")
    saida.mkdir(parents=True, exist_ok=True)
    Path(args.preparsed).mkdir(parents=True, exist_ok=True)

    with cpgs_path.open(encoding="utf-8") as f:
        leitor = csv.DictReader(f, delimiter="\t")
        linhas = [l for l in leitor if l.get("qvalue") not in (None, "", "NA")]
    candidatas = [l for l in linhas if float(l["qvalue"]) < args.q_max]
    candidatas.sort(key=lambda l: abs(float(l["meth.diff"])), reverse=True)
    selecao = candidatas[: args.top_n]
    if len(selecao) < 10:
        raise SystemExit(f"só {len(selecao)} CpGs com q<{args.q_max} — relaxe --q-max "
                         "ou use o dado completo do estudo")
    log(f"CpGs testadas: {len(linhas)}; q<{args.q_max}: {len(candidatas)}; "
        f"selecionadas (top |diff|): {len(selecao)}")

    bed = saida / "cpgs_selecionadas.bed"
    with bed.open("w", encoding="utf-8", newline="\n") as f:
        for i, l in enumerate(selecao):
            ini = max(0, int(l["start"]) - 1 - args.flanco)
            fim = int(l["end"]) + args.flanco
            f.write(f"{l['chr']}\t{ini}\t{fim}\tcpg_{i}\t0\t+\n")

    homer_out = saida / "homer"
    rodar(["findMotifsGenome.pl", str(bed), args.genoma, str(homer_out),
           "-size", str(args.size), "-p", "2", "-nomotif",
           "-preparsedDir", args.preparsed])

    conhecidos = homer_out / "knownResults.txt"
    # Falso-done: sem este arquivo, o script escrevia uma tabela só com cabeçalho,
    # marcava o job como concluído e o HTML linkado no resumo nem existia — e uma
    # tabela vazia se lê como "nenhum motif enriquecido", que é outra afirmação.
    if not conhecidos.exists():
        raise SystemExit(
            f"HOMER não produziu {conhecidos.name} — ver o log acima "
            "(genoma/preparsedDir? regiões demais? BED vazio?)")
    top = []
    with conhecidos.open(encoding="utf-8", errors="replace") as f:
        leitor = csv.reader(f, delimiter="\t")
        next(leitor)  # cabeçalho do HOMER
        for linha in leitor:
            if len(linha) >= 5:
                top.append((linha[0].split("/")[0], linha[2], linha[4]))
            if len(top) >= 15:
                break
    if not top:
        raise SystemExit(f"{conhecidos.name} sem nenhuma linha ranqueável — resultado inutilizável")
    tsv = saida / "motifs_top.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write("MOTIF\tP_VALUE\tQ_VALUE\n")
        for nome, pval, qval in top:
            f.write(f"{nome}\t{pval}\t{qval}\n")
    log(f"Motifs conhecidos ranqueados: {len(top)} (top: {top[0][0]})")

    escrever_resumo(saida, "motifs_resumo.json", {
        "criterio_selecao": f"q<{args.q_max}, top {args.top_n} por |meth.diff|, flanco ±{args.flanco} pb",
        # Esta nota era um texto FIXO que afirmava "1 DMC estrita no CD4" enquanto o
        # dmc_resumo.json do mesmo dado reportava 314 DMCs: número cravado à mão
        # dentro de um arquivo de RESULTADO (anti-lição nº 4). Agora sai do dado.
        "aviso_didatico": (
            f"seleção exploratória: q<{args.q_max} (mais frouxo que o corte de DMC), "
            f"{len(selecao)} regiões de {len(candidatas)} candidatas "
            f"entre {len(linhas)} CpGs testadas"),
        "n_cpgs_testadas": len(linhas),
        "n_candidatas_q": len(candidatas),
        "n_regioes": len(selecao),
        "genoma": args.genoma,
        "motif_top": top[0][0],
        "tabela_tsv": str(tsv),
        "relatorio_html": str(homer_out / "knownResults.html"),
    })


if __name__ == "__main__":
    main()
