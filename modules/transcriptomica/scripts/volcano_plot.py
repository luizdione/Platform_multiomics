"""volcano_plot.py — volcano plot da tabela DESeq2 (passo 2 do workflow de DE).

Consome o de_table.tsv do passo anterior (encadeamento validado no manifest) e
produz o PNG exibido na página do módulo. matplotlib do worker omica-methyl.

Uso:
  python3 modules/transcriptomica/scripts/volcano_plot.py \
      --de <de_table.tsv> --padj 0.05 --lfc 1 --saida <dir>
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from _comum import escrever_resumo, log


def main() -> None:
    p = argparse.ArgumentParser(description="Volcano plot (DESeq2)")
    p.add_argument("--de", required=True)
    p.add_argument("--padj", type=float, default=0.05)
    p.add_argument("--lfc", type=float, default=1.0)
    p.add_argument("--destaques", type=int, default=10)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    de_path, saida = Path(args.de), Path(args.saida)
    if not de_path.exists():
        raise SystemExit(f"tabela DE não encontrada: {de_path} (rode o DESeq2 antes)")
    saida.mkdir(parents=True, exist_ok=True)

    lfc, mlogp, cores, rotulos = [], [], [], []
    with de_path.open(encoding="utf-8") as f:
        cab = f.readline().rstrip("\n").split("\t")
        i_lfc, i_padj = cab.index("log2FoldChange"), cab.index("padj")
        i_nome = cab.index("gene_name")
        for linha in f:
            campos = linha.rstrip("\n").split("\t")
            try:
                x = float(campos[i_lfc])
                padj = float(campos[i_padj])
            except (ValueError, IndexError):
                continue
            if padj <= 0:
                padj = 1e-300
            lfc.append(x)
            mlogp.append(-math.log10(padj))
            sig = padj < args.padj and abs(x) >= args.lfc
            cores.append("#e74c3c" if (sig and x > 0) else "#2980b9" if sig else "#c8d1d9")
            rotulos.append(campos[i_nome] if sig else "")

    n_sig = sum(1 for c in cores if c != "#c8d1d9")
    log(f"Pontos: {len(lfc)}; significativos (padj<{args.padj}, |lfc|>={args.lfc}): {n_sig}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 5.2), dpi=120)
    ax.scatter(lfc, mlogp, c=cores, s=8, linewidths=0)
    ax.axhline(-math.log10(args.padj), color="#5c6b7a", ls="--", lw=1)
    for v in (-args.lfc, args.lfc):
        ax.axvline(v, color="#5c6b7a", ls="--", lw=1)
    destacados = sorted((m, x, r) for x, m, r in zip(lfc, mlogp, rotulos) if r)[-args.destaques:]
    for m, x, r in destacados:
        ax.annotate(r, (x, m), fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("log2 fold change (summer_heat vs winter_control)")
    ax.set_ylabel("−log10(padj)")
    ax.set_title("Volcano — granulosa Angus, estresse térmico (DESeq2)")
    fig.tight_layout()
    png = saida / "volcano.png"
    fig.savefig(png)
    log(f"Figura: {png}")

    escrever_resumo(saida, "volcano_resumo.json", {
        "tabela_de": de_path.name,
        "n_pontos": len(lfc),
        "n_significativos": n_sig,
        "criterios": {"padj": args.padj, "lfc": args.lfc},
        "figura_png": str(png),
    })


if __name__ == "__main__":
    main()
