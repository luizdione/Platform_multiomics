"""painel_janelas.py — painel de genes (JSON) → BED de janelas ±pad, para a interseção.

Passo `janelas` do pipeline epigenomica_dmc (F6, ESPEC §3.2.2): o painel entra UMA vez, como
`painel_genes_json`, e o BED que o intersecao_dmc_painel.py (selado) lê sai daqui. Com o
painel_108.json e pad 15000 o resultado é byte a byte o painel_108.bed versionado (sha256
170e413f…, tests/test_painel_janelas.py).

Ordem: (cromossomo como TEXTO, início), sort estável — a mesma do BED versionado ('10' vem
antes de '2'). Cada linha: cromossomo, max(0, início − pad), fim + pad, símbolo, ensembl_id
(vazio quando o painel não tem; no painel_genes_json o campo é opcional). LF, sem cabeçalho.

Só biblioteca padrão (roda em qualquer worker).

Uso:
  python3 modules/epigenomica/scripts/painel_janelas.py \
      --painel modules/epigenomica/data/paineis/painel_108.json --pad 15000 --saida <dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _comum import escrever_resumo, log


def janelas(genes: dict, pad: int) -> list[str]:
    """As linhas do BED (com o '\\n'), na ordem (str(cromossomo), início)."""
    ordem = sorted(genes, key=lambda s: (str(genes[s]["cromossomo"]), genes[s]["inicio"]))
    linhas = []
    for simbolo in ordem:
        g = genes[simbolo]
        linhas.append(f"{g['cromossomo']}\t{max(0, g['inicio'] - pad)}\t{g['fim'] + pad}\t{simbolo}\t"
                      f"{g.get('ensembl_id') or ''}\n")
    return linhas


def main() -> None:
    p = argparse.ArgumentParser(description="Painel de genes (JSON) → janelas ±pad (BED)")
    p.add_argument("--painel", required=True)
    p.add_argument("--pad", type=int, required=True)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    if args.pad < 0:
        raise SystemExit(f"--pad negativo ({args.pad}): a janela encolheria o gene")
    painel, saida = Path(args.painel), Path(args.saida)
    genes = json.loads(painel.read_text(encoding="utf-8")).get("genes")
    if not isinstance(genes, dict) or not genes:
        raise SystemExit("painel sem 'genes' (objeto símbolo → cromossomo, início, fim, fita)")
    linhas = janelas(genes, args.pad)

    saida.mkdir(parents=True, exist_ok=True)
    bed = saida / "janelas.bed"
    with bed.open("w", encoding="utf-8", newline="\n") as f:
        f.writelines(linhas)
    cromossomos = {str(g["cromossomo"]) for g in genes.values()}
    log(f"{len(linhas)} janelas ±{args.pad} pb em {len(cromossomos)} cromossomos")

    escrever_resumo(saida, "janelas_resumo.json", {
        "n_genes": len(genes),
        "n_janelas": len(linhas),
        "pad_pb": args.pad,
        "n_fita_negativa": sum(1 for g in genes.values() if g.get("fita") == -1),
        "n_sem_ensembl": sum(1 for g in genes.values() if not g.get("ensembl_id")),
        "n_cromossomos": len(cromossomos),
    })


if __name__ == "__main__":
    main()
