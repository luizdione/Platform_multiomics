"""volcano_api.py — volcano plot da tabela DESeq2 nos runs orquestrados (scripts_api, n8n).

Por que um script novo, e não o volcano_plot.py: aquele é SELADO (hash_scripts do
wf_de_granulosa) e traz os rótulos fixos do demo — eixo "summer_heat vs winter_control" e
título "granulosa Angus". Num run, a referência é escolha do autor: com
referencia=summer_heat o DESeq2 calcula winter_control vs summer_heat, e a figura anunciava o
contraste INVERTIDO; com dados de outro usuário, anunciava outro experimento (revisão
adversarial da F2, achado #17). Aqui o rótulo vem do de_resumo.json que o expressao_deseq2.R
grava — o contraste que ele DE FATO calculou —, e o volcano_resumo.json repete esse contraste
para o relatório conferir. Resumo sem contraste é falha: o script nunca chuta rótulo.

A plotagem é a do volcano_plot.py, copiada: o selado não pode ser editado, e importá-lo
traria os rótulos fixos junto.

Uso:
  python3 modules/transcriptomica/scripts/volcano_api.py \
      --de <de_table.tsv> --resumo <de_resumo.json> --padj 0.05 --lfc 1 --saida <dir>
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from _comum import escrever_resumo, log

FATOR = "condicao"  # o expressao_deseq2.R usa design ~condicao: resultsNames = condicao_<A>_vs_<B>


def _ocorrencias(texto: str, trecho: str) -> int:
    """Ocorrências de `trecho` contando as SOBREPOSTAS: o split conta 'x_vs_vs_y' como uma só."""
    return sum(1 for i in range(len(texto)) if texto.startswith(trecho, i))


def rotulos(contraste: str) -> tuple[str, str]:
    """(rótulo do eixo x, título) a partir do nome do contraste do DESeq2. Só divide em
    tratamento × referência quando o '_vs_' aparece UMA vez, contando as ocorrências
    sobrepostas: 'tratado_vs_vs_ctrl' vem tanto de 'tratado_vs' × 'ctrl' quanto de 'tratado' ×
    'vs_ctrl' (o split achava uma só e escolhia a 2ª leitura — chute). Ambíguo, usa o nome cru,
    que nunca está invertido. O coldata já recusa esses níveis na entrada (formatos.py); isto
    é a 2ª trava. O nível aparece como o DESeq2 o nomeia (make.names: 'heat-stress' vira
    'heat.stress' — conferido no R 4.5.3)."""
    base = contraste[len(FATOR) + 1:] if contraste.startswith(FATOR + "_") else contraste
    partes = base.split("_vs_")
    if _ocorrencias(base, "_vs_") == 1 and len(partes) == 2 and all(partes):
        tratamento, referencia = partes
        return (f"log2 fold change ({tratamento} vs {referencia}; referência = {referencia})",
                f"Volcano — DESeq2: {tratamento} vs {referencia}")
    return f"log2 fold change ({contraste})", f"Volcano — DESeq2 ({contraste})"


def ler_contraste(resumo: Path) -> str:
    try:
        dados = json.loads(resumo.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"resumo do DESeq2 ilegível: {resumo} ({type(e).__name__})")
    contraste = dados.get("contraste") if isinstance(dados, dict) else None
    if not isinstance(contraste, str) or not contraste.strip():
        raise SystemExit(f"resumo do DESeq2 sem 'contraste': {resumo} — sem ele a figura não "
                         "tem como dizer qual condição é a referência")
    return contraste.strip()


def main() -> None:
    p = argparse.ArgumentParser(description="Volcano plot (DESeq2), rotulado pelo contraste calculado")
    p.add_argument("--de", required=True)
    p.add_argument("--resumo", required=True, help="de_resumo.json do expressao_deseq2.R")
    p.add_argument("--padj", type=float, default=0.05)
    p.add_argument("--lfc", type=float, default=1.0)
    p.add_argument("--destaques", type=int, default=10)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    de_path, resumo_path, saida = Path(args.de), Path(args.resumo), Path(args.saida)
    if not de_path.exists():
        raise SystemExit(f"tabela DE não encontrada: {de_path} (rode o DESeq2 antes)")
    contraste = ler_contraste(resumo_path)
    rotulo_x, titulo = rotulos(contraste)
    saida.mkdir(parents=True, exist_ok=True)

    lfc, mlogp, cores, nomes = [], [], [], []
    with de_path.open(encoding="utf-8") as f:
        cab = f.readline().rstrip("\n").split("\t")
        faltam = [c for c in ("log2FoldChange", "padj", "gene_name") if c not in cab]
        if faltam:
            raise SystemExit(f"tabela DE sem as colunas {faltam}: {de_path}")
        i_lfc, i_padj, i_nome = cab.index("log2FoldChange"), cab.index("padj"), cab.index("gene_name")
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
            nomes.append(campos[i_nome] if sig else "")

    n_sig = sum(1 for c in cores if c != "#c8d1d9")
    log(f"Contraste: {contraste}")
    log(f"Pontos: {len(lfc)}; significativos (padj<{args.padj}, |lfc|>={args.lfc}): {n_sig}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 5.2), dpi=120)
    ax.scatter(lfc, mlogp, c=cores, s=8, linewidths=0)
    ax.axhline(-math.log10(args.padj), color="#5c6b7a", ls="--", lw=1)
    for v in (-args.lfc, args.lfc):
        ax.axvline(v, color="#5c6b7a", ls="--", lw=1)
    destacados = sorted((m, x, r) for x, m, r in zip(lfc, mlogp, nomes) if r)[-args.destaques:]
    for m, x, r in destacados:
        ax.annotate(r, (x, m), fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel(rotulo_x)
    ax.set_ylabel("−log10(padj)")
    ax.set_title(titulo)
    fig.tight_layout()
    png = saida / "volcano.png"
    fig.savefig(png)
    log(f"Figura: {png}")

    escrever_resumo(saida, "volcano_resumo.json", {
        "tabela_de": de_path.name,
        "contraste": contraste,
        "rotulo_x": ax.get_xlabel(),
        "titulo": ax.get_title(),
        "n_pontos": len(lfc),
        "n_significativos": n_sig,
        "criterios": {"padj": args.padj, "lfc": args.lfc},
        "figura_png": str(png),
    })


if __name__ == "__main__":
    main()
