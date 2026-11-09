"""ml_contraste.py — separa, da tabela amostra → grupo, as listas das duas classes do contraste.

Primeiro passo do pipeline ml_aims_vcf (n8n): o autor manda a tabela de rótulos (formato
rotulos_amostras) e escolhe as duas classes; este script grava os IDs de cada uma, um por
linha, que o ml_dataset_genotipos lê com o `_ler_grupo` (ml_dataset_genotipos.py:34-39).
Só stdlib: roda na imagem omica-bioinfo, sem pandas.

A tabela é lida como o validador a lê (omica/contrato/formatos.py, `rotulos_amostras`):
TAB, sem tratar aspas, linha vazia pulada, colunas `amostra` e `grupo` pelo NOME (as outras
são ignoradas). O validador já reprovou a tabela malformada antes do plano; o que este script
recusa é o CONTRASTE: classes iguais, classe ausente da tabela ou com menos de `--minimo`
amostras — com código de saída diferente de 0, para o passo não entregar.

Ordem das classes no resumo: `classe_y0`/`classe_y1` são as duas em ordem alfabética, a
MESMA regra do ml_classificar (y = 1 para a classe alfabeticamente maior,
ml_classificar.py:86-87). O relatório confere as linhas da matriz de confusão contra n_y0 e
n_y1; a classe A continua sendo a que vem primeiro nas colunas da matriz.

Uso:
  python3 modules/ml/scripts/ml_contraste.py \
      --rotulos <tabela.tsv> --classe-a Angus --classe-b Brahman --minimo 10 --saida <dir>
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from _comum import escrever_resumo, log


def ler_tabela(caminho: Path) -> dict[str, list[str]]:
    """{grupo: [amostras na ordem do arquivo]}. Estrutura fora do formato encerra o script
    (o validador já a teria reprovado; aqui só não seguimos com um contraste errado)."""
    grupos: dict[str, list[str]] = {}
    vistas: set[str] = set()
    with caminho.open(encoding="utf-8", newline="") as f:
        leitor = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        cab = next(leitor, None) or []
        if cab.count("amostra") != 1 or cab.count("grupo") != 1:
            raise SystemExit("tabela de rótulos sem as colunas 'amostra' e 'grupo' (uma de cada)")
        ia, ig = cab.index("amostra"), cab.index("grupo")
        for campos in leitor:
            if not campos:
                continue  # linha vazia: o validador também a pula
            n = leitor.line_num
            if len(campos) != len(cab):
                raise SystemExit(f"linha {n}: nº de campos diferente do cabeçalho ({len(cab)})")
            amostra, grupo = campos[ia], campos[ig]
            if not amostra or not grupo:
                raise SystemExit(f"linha {n}: amostra ou grupo vazio")
            if amostra in vistas:
                raise SystemExit(f"linha {n}: amostra repetida ({amostra})")
            vistas.add(amostra)
            grupos.setdefault(grupo, []).append(amostra)
    return grupos


def main() -> None:
    p = argparse.ArgumentParser(description="Listas das duas classes do contraste (ML)")
    p.add_argument("--rotulos", required=True, help="TSV com as colunas amostra e grupo")
    p.add_argument("--classe-a", required=True)
    p.add_argument("--classe-b", required=True)
    p.add_argument("--minimo", type=int, default=10, help="mínimo de amostras por classe")
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    rotulos, saida = Path(args.rotulos), Path(args.saida)
    if not rotulos.is_file():
        raise SystemExit(f"tabela de rótulos não encontrada: {rotulos}")
    a, b = args.classe_a, args.classe_b
    if a == b:
        raise SystemExit(f"classes iguais ({a!r}): o contraste precisa de duas classes diferentes")
    grupos = ler_tabela(rotulos)
    ausentes = [c for c in (a, b) if c not in grupos]
    if ausentes:
        raise SystemExit(f"classe(s) ausente(s) da tabela de rótulos: {ausentes} "
                         f"(a tabela tem {len(grupos)} grupo(s))")
    ids_a, ids_b = sorted(grupos[a]), sorted(grupos[b])
    poucos = [f"{c} com {len(ids)}" for c, ids in ((a, ids_a), (b, ids_b)) if len(ids) < args.minimo]
    if poucos:
        raise SystemExit(f"classe(s) abaixo do mínimo de {args.minimo} amostras: {', '.join(poucos)}")

    saida.mkdir(parents=True, exist_ok=True)
    for nome, ids in (("grupo_a.txt", ids_a), ("grupo_b.txt", ids_b)):
        (saida / nome).write_text("\n".join(ids) + "\n", encoding="utf-8", newline="\n")
    log(f"{a}: {len(ids_a)} amostras; {b}: {len(ids_b)}; {len(grupos)} grupo(s) na tabela")

    y0, y1 = min(a, b), max(a, b)
    n = {a: len(ids_a), b: len(ids_b)}
    escrever_resumo(saida, "contraste_resumo.json", {
        "classe_a": a, "classe_b": b, "n_a": n[a], "n_b": n[b],
        "classe_y0": y0, "classe_y1": y1, "n_y0": n[y0], "n_y1": n[y1],
        "n_niveis_tabela": len(grupos), "minimo": args.minimo,
    })


if __name__ == "__main__":
    main()
