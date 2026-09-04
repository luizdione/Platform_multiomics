"""mofa_preparar.py — monta as views do MOFA a partir dos coverages Bismark.

DESENHO HONESTO DO DEMO: as 6 amostras CD4 têm metilação, não genótipos — não
há pareamento animal-a-animal com o 1000BG. Por isso as duas views do demo são
biológicas e REAIS da mesma modalidade: metilação de PROMOTOR (±2 kb do TSS)
× metilação de CORPO GÊNICO (dentro do gene, fora do promotor), por gene do
painel 108. Com animais pareados WGS+RRBS (caso do paper), troque a segunda
view pela matriz de genótipos — o resto do fluxo não muda.

Uso:
  python3 modules/epigenomica/scripts/mofa_preparar.py \
      --dir modules/epigenomica/demo_data \
      --painel modules/epigenomica/data/paineis/painel_108.json \
      --promotor 2000 --min-amostras 4 --saida <dir>
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path

from _comum import escrever_resumo, log


def main() -> None:
    p = argparse.ArgumentParser(description="Views promotor×corpo p/ MOFA (painel 108)")
    p.add_argument("--dir", required=True)
    p.add_argument("--painel", required=True)
    p.add_argument("--promotor", type=int, default=2000)
    p.add_argument("--min-amostras", type=int, default=4)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    pasta, saida = Path(args.dir), Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    genes = json.loads(Path(args.painel).read_text(encoding="utf-8"))["genes"]

    regioes = []  # (crom, ini, fim, gene, view)
    for simbolo, g in genes.items():
        crom = str(g["cromossomo"])
        tss = g["inicio"] if g["fita"] >= 0 else g["fim"]
        regioes.append((crom, tss - args.promotor, tss + args.promotor, simbolo, "promotor"))
        regioes.append((crom, g["inicio"], g["fim"], simbolo, "corpo"))
    por_crom: dict[str, list] = defaultdict(list)
    for r in regioes:
        por_crom[r[0]].append(r)

    arquivos = sorted(pasta.glob("*.cov.gz")) + sorted(pasta.glob("*.cov"))
    if len(arquivos) < 4:
        raise SystemExit(f"amostras insuficientes em {pasta}")
    amostras = [a.name.replace(".bismark.cov.gz", "").replace(".cov.gz", "") for a in arquivos]
    log(f"{len(arquivos)} amostras; {len(genes)} genes → views promotor(±{args.promotor}) × corpo")

    # soma_meth/soma_total por (view, gene, amostra) → % média ponderada
    acum: dict[tuple, list] = defaultdict(lambda: [0.0, 0.0])
    for idx, arq in enumerate(arquivos):
        with gzip.open(arq, "rt", encoding="utf-8", errors="replace") if arq.suffix == ".gz" \
                else arq.open(encoding="utf-8") as f:
            for linha in f:
                campos = linha.split("\t")
                if len(campos) < 6:
                    continue
                crom, pos = campos[0], int(campos[1])
                met, nao = float(campos[4]), float(campos[5])
                for _, ini, fim, gene, view in por_crom.get(crom, ()):
                    if ini <= pos <= fim:
                        chave = (view, gene, idx)
                        acum[chave][0] += met
                        acum[chave][1] += met + nao

    resumo_views = {}
    for view in ("promotor", "corpo"):
        linhas_out = []
        for gene in sorted(genes):
            valores = []
            for idx in range(len(arquivos)):
                m, t = acum.get((view, gene, idx), (0.0, 0.0))
                valores.append(f"{100 * m / t:.3f}" if t > 0 else "NA")
            if sum(v != "NA" for v in valores) >= args.min_amostras:
                linhas_out.append((gene, valores))
        destino = saida / f"view_{view}.tsv"
        with destino.open("w", encoding="utf-8", newline="\n") as f:
            f.write("feature\t" + "\t".join(amostras) + "\n")
            for gene, valores in linhas_out:
                f.write(gene + "\t" + "\t".join(valores) + "\n")
        resumo_views[view] = len(linhas_out)
        log(f"view {view}: {len(linhas_out)} genes com ≥{args.min_amostras} amostras")

    grupos = saida / "grupos.tsv"
    with grupos.open("w", encoding="utf-8", newline="\n") as f:
        f.write("amostra\tgrupo\n")
        for a in amostras:
            f.write(f"{a}\t{'Nelore' if 'Nelore' in a else 'HF'}\n")

    escrever_resumo(saida, "views_resumo.json", {
        "desenho": "views promotor × corpo gênico (painel 108) — pareamento real por amostra",
        "nota": "com animais WGS+RRBS pareados, a 2ª view vira genótipos (fluxo idêntico)",
        "n_amostras": len(amostras),
        "genes_por_view": resumo_views,
        "views": {v: str(saida / f'view_{v}.tsv') for v in resumo_views},
        "grupos_tsv": str(grupos),
    })


if __name__ == "__main__":
    main()
