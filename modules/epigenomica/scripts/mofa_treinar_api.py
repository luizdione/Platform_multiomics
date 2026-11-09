"""mofa_treinar_api.py — MOFA (mofapy2) com os rótulos da TABELA DE AMOSTRAS.

Passo `mofa` do pipeline epigenomica_mofa (F6, decisão 9 da ESPEC; adendo v2 §A9.5 e M8). O
mofa_treinar.py selado não serve a outros grupos: os rótulos Nelore/HF estão fixos nele
(mofa_treinar.py:129,143-144) e no grupos.tsv do mofa_preparar.py (mofa_preparar.py:119, que
rotula "HF" tudo que não tem "Nelore" no nome). Com outros grupos a separação virava NaN. Este
script NÃO lê o grupos.tsv: cada coluna das views é `<condicao>__<amostra>` (o nome que o passo
`montar` deu ao coverage) e a condição vem do coldata, por igualdade exata.

O treino é o do selado, COPIADO sem mudar a lógica (mofa_treinar.py:54-112: as opções do
entry_point, a captura do SystemExit, o R² no espaço escalado e a checagem de sanidade). O
tests/test_mofa_treinar_api.py confere a sincronia textual (mofa_treinar.py:64-71 e 96-105) e
o smoke no worker methyl confere a paridade (variancia_explicada.tsv com o sha do selado).

Separação das condições: y0 é a menor condição em ordem alfabética e y1 a maior (a MESMA regra
do montar_coverages.py, teste de sincronia); separacao_por_fator[k] = |média(Z[y1,k]) −
média(Z[y0,k])|, com 3 casas. Valor não finito sai com código ≠ 0. Sem hdf5.

Conferência do rótulo (verificador da F6, M8): n_y0/n_y1 não pegam um rótulo trocado que
preserve as contagens (o grupos.tsv do selado com um coldata Lote1 × Lote2 que mistura HF e
Nelore dá 3/3 dos dois lados). O resumo grava `n_rotulo_igual_prefixo`: quantas colunas têm a
condição atribuída igual ao prefixo do NOME antes do primeiro '__' (o montar grava
`<condicao>__<amostra>` e recusa condição com '__'). É recalculado do nome, não do coldata, e a
invariante do epigenomica_mofa o compara com montar.n_amostras.

Uso:
  python3 modules/epigenomica/scripts/mofa_treinar_api.py \
      --promotor <view_promotor.tsv> --corpo <view_corpo.tsv> --amostras <amostras.tsv> \
      --fatores 3 --saida <dir>
"""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np

from _comum import escrever_resumo, log

MOTOR = "mofapy2 entry_point direto (sem wrapper R — decisão 2026-09-03); rótulos do coldata"
CORES = ["#e67e22", "#0f766e", "#6d28d9", "#b91c1c", "#1d4ed8", "#4d7c0f"]


def _ler_view(caminho: Path) -> tuple[list[str], list[str], np.ndarray]:
    with caminho.open(encoding="utf-8") as f:
        leitor = csv.reader(f, delimiter="\t")
        cab = next(leitor)
        amostras = cab[1:]
        feats, linhas = [], []
        for linha in leitor:
            feats.append(linha[0])
            linhas.append([float(v) if v != "NA" else np.nan for v in linha[1:]])
    return amostras, feats, np.array(linhas)  # features × amostras


def ler_amostras(caminho: Path) -> list[tuple[str, str]]:
    """(amostra, condicao) do coldata, lido como o validador `coldata` (TAB, sem aspas,
    cabeçalho aparado de espaço, linha vazia pulada)."""
    with caminho.open(encoding="utf-8", newline="") as f:
        leitor = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        cab = [c.strip(" ") for c in next(leitor, None) or []]
        if "amostra" not in cab or "condicao" not in cab:
            raise SystemExit("a tabela de amostras precisa das colunas 'amostra' e 'condicao'")
        ia, ic = cab.index("amostra"), cab.index("condicao")
        return [(linha[ia], linha[ic]) for linha in leitor if linha and len(linha) == len(cab)]


def condicoes_extremas(condicoes: list[str]) -> tuple[str, str]:
    """(y0, y1): a menor e a maior condição em ordem alfabética (código de caractere). A MESMA
    regra do montar_coverages.py (teste de sincronia): as invariantes do epigenomica_mofa
    comparam n_y0/n_y1 dos dois resumos."""
    return min(condicoes), max(condicoes)


def condicoes_das_colunas(colunas: list[str], pares: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(amostra, condição) de cada coluna das views, na ordem delas. A coluna é
    `<condicao>__<amostra>` (o mofa_preparar tira o .bismark.cov.gz do nome que o montar deu).
    Coluna sem par no coldata, coluna repetida ou amostra do coldata ausente: código ≠ 0."""
    por_coluna = {f"{c}__{a}": (a, c) for a, c in pares}
    if len(set(colunas)) != len(colunas):
        raise SystemExit("colunas repetidas nas views")
    sem_par = [x for x in colunas if x not in por_coluna]
    if sem_par:
        raise SystemExit(f"{len(sem_par)} coluna(s) das views sem amostra no coldata (ex.: {sem_par[0]!r}): a "
                         "coluna tem de ser <condicao>__<amostra>, o nome que o passo montar deu ao coverage")
    ausentes = [a for a, c in pares if f"{c}__{a}" not in set(colunas)]
    if ausentes:
        raise SystemExit(f"{len(ausentes)} amostra(s) do coldata ausente(s) das views (ex.: {ausentes[0]!r})")
    return [por_coluna[x] for x in colunas]


def n_rotulo_igual_prefixo(colunas: list[str], condicoes: list[str]) -> int:
    """Quantas colunas têm a condição atribuída igual ao prefixo do nome antes do primeiro '__'.
    Lê só o NOME da coluna (nunca o coldata nem o mapeamento acima): um rótulo vindo de outra
    fonte (o grupos.tsv do selado) ou na ordem errada baixa o número, mesmo com n_y0/n_y1 certos."""
    if len(colunas) != len(condicoes):
        raise SystemExit(f"{len(condicoes)} rótulo(s) para {len(colunas)} coluna(s) das views")
    return sum(col.partition("__")[0] == c for col, c in zip(colunas, condicoes))


def treinar(matrizes: list[np.ndarray], amostras: list[str], feats_p: list[str], feats_c: list[str],
            args: argparse.Namespace) -> tuple[np.ndarray, dict[str, list[float]]]:
    """O treino e o R² do mofa_treinar.py selado (linhas 54-112), sem mudar a lógica. Devolve
    Z (amostras × fatores) e o R² por view/fator (%)."""
    # mofapy2 espera AMOSTRAS × FEATURES e centra internamente. ATENÇÃO
    # (2026-09-03): set_data_matrix MUTA a lista recebida (desembrulha o nível
    # de grupos in-place) — por isso guardamos NOSSAS matrizes à parte e
    # entregamos ao mofa uma lista descartável.
    dados = [[matrizes[0].copy()], [matrizes[1].copy()]]  # descartável p/ o mofa

    from mofapy2.run.entry_point import entry_point

    ep = entry_point()
    ep.set_data_options(scale_views=True)
    # mofapy2 exige nomes de feature únicos ENTRE views → sufixo por view
    ep.set_data_matrix(dados, views_names=["promotor", "corpo"],
                       groups_names=["cd4"], samples_names=[amostras],
                       features_names=[[f"{g}_prom" for g in feats_p],
                                       [f"{g}_corpo" for g in feats_c]])
    ep.set_model_options(factors=args.fatores, spikeslab_weights=True, ard_weights=True)
    ep.set_train_options(iter=300, convergence_mode="fast", seed=42, verbose=False)
    ep.build()
    # mofapy2 0.6.x chama sys.exit() ao convergir fora de sessão interativa —
    # SystemExit sai com rc=0 e enganaria a fila como "done" sem resultados
    # (visto 2026-09-03). Capturamos e seguimos: o modelo está em ep.model.
    try:
        ep.run()
    except SystemExit:
        log("mofapy2 chamou sys.exit() pós-convergência — capturado; modelo em memória")

    modelo = ep.model
    expect = modelo.getExpectations()
    Z = expect["Z"]["E"]                      # amostras × fatores
    Ws = [expect["W"][v]["E"] for v in range(2)]  # features × fatores por view
    log(f"Treinado: Z {Z.shape}; W promotor {Ws[0].shape}, corpo {Ws[1].shape}")

    # variância explicada por fator/view: R²_k = 1 − SS(Y − Z_k W_kᵀ)/SS(Y)
    nomes_views = ["promotor", "corpo"]
    r2 = {}
    for vi, (nome, W) in enumerate(zip(nomes_views, Ws)):
        # O modelo treinou com scale_views=True (set_data_options, acima): W e Z
        # vivem no espaço ESCALADO. Comparar Z·Wᵀ com um Y apenas centrado mede
        # resíduo em unidade diferente da do sinal e o R² sai sem sentido (podia
        # até passar de 100% ou ficar negativo). Reproduzimos aqui a MESMA
        # transformação do treino: centrar e dividir pelo desvio-padrão da view.
        Y = matrizes[vi] - np.nanmean(matrizes[vi], axis=0, keepdims=True)
        escala = float(np.nanstd(Y))
        if escala > 0:
            Y = Y / escala
        mascara = ~np.isnan(Y)
        ss_total = np.nansum(Y[mascara] ** 2)
        r2[nome] = []
        for k in range(args.fatores):
            residuo = Y - np.outer(Z[:, k], W[:, k])
            r2[nome].append(round(100 * (1 - np.nansum(residuo[mascara] ** 2) / ss_total), 2))

    for nome in nomes_views:  # sanidade: R² fora de [-100, 100] denuncia escala errada
        for k, v in enumerate(r2[nome]):
            if not (-100.0 <= v <= 100.0):
                raise SystemExit(
                    f"R² implausível ({v}%) no Factor{k + 1} da view {nome} — "
                    "escala de Y incompatível com a do modelo")
    return Z, r2


def separacao(Z: np.ndarray, condicoes: list[str], fatores: int) -> tuple[str, str, int, int, list[float]]:
    """(y0, y1, n_y0, n_y1, separação por fator). Não finito → código ≠ 0."""
    y0, y1 = condicoes_extremas(sorted(set(condicoes)))
    arr = np.array(condicoes)
    em0, em1 = arr == y0, arr == y1
    sep = []
    for k in range(fatores):
        v = abs(float(Z[em1, k].mean() - Z[em0, k].mean()))
        if not math.isfinite(v):
            raise SystemExit(f"separação não finita no Factor{k + 1} ({y0} × {y1})")
        sep.append(round(v, 3))
    return y0, y1, int(em0.sum()), int(em1.sum()), sep


def main() -> None:
    p = argparse.ArgumentParser(description="MOFA (mofapy2 direto) com os rótulos do coldata")
    p.add_argument("--promotor", required=True)
    p.add_argument("--corpo", required=True)
    p.add_argument("--amostras", required=True)
    p.add_argument("--fatores", type=int, default=3)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    if args.fatores < 2:
        raise SystemExit(f"--fatores {args.fatores}: são precisos ≥ 2 (a figura e a separação usam o Fator 2)")
    saida = Path(args.saida)
    colunas, feats_p, Yp = _ler_view(Path(args.promotor))
    colunas_c, feats_c, Yc = _ler_view(Path(args.corpo))
    if colunas_c != colunas:
        raise SystemExit("as duas views têm de ter as mesmas amostras, na mesma ordem")
    rotulos = condicoes_das_colunas(colunas, ler_amostras(Path(args.amostras)))
    amostras = [a for a, _ in rotulos]
    condicoes = [c for _, c in rotulos]
    if len(set(condicoes)) < 2:
        raise SystemExit("as views têm uma condição só: não há separação a medir")
    n_prefixo = n_rotulo_igual_prefixo(colunas, condicoes)
    log(f"Views: promotor {Yp.shape[0]}×{Yp.shape[1]}, corpo {Yc.shape[0]}×{Yc.shape[1]}")
    saida.mkdir(parents=True, exist_ok=True)

    matrizes = [Yp.T.copy(), Yc.T.copy()]           # amostras × features (nossas)
    Z, r2 = treinar(matrizes, colunas, feats_p, feats_c, args)
    nomes_views = ["promotor", "corpo"]
    y0, y1, n_y0, n_y1, sep = separacao(Z, condicoes, args.fatores)

    with (saida / "variancia_explicada.tsv").open("w", encoding="utf-8", newline="\n") as f:
        f.write("fator\t" + "\t".join(nomes_views) + "\n")
        for k in range(args.fatores):
            f.write(f"Factor{k + 1}\t" + "\t".join(str(r2[v][k]) for v in nomes_views) + "\n")

    with (saida / "fatores_por_amostra.tsv").open("w", encoding="utf-8", newline="\n") as f:
        f.write("amostra\tcondicao\t" + "\t".join(f"Factor{k + 1}" for k in range(args.fatores)) + "\n")
        for i, (a, c) in enumerate(rotulos):
            f.write(a + "\t" + c + "\t" + "\t".join(f"{Z[i, k]:.4f}" for k in range(args.fatores)) + "\n")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    niveis = sorted(set(condicoes))
    fig, ax = plt.subplots(figsize=(7, 5), dpi=120)
    for j, nivel in enumerate(niveis):
        idx = [i for i, c in enumerate(condicoes) if c == nivel]
        ax.scatter(Z[idx, 0], Z[idx, 1], c=CORES[j % len(CORES)], s=110, label=f"{nivel} (n={len(idx)})")
    for i, c in enumerate(condicoes):
        ax.annotate(c, (Z[i, 0], Z[i, 1]), fontsize=7, xytext=(4, 4), textcoords="offset points")
    ax.set_xlabel(f"Fator 1 ({r2['promotor'][0]:.1f}% prom / {r2['corpo'][0]:.1f}% corpo)")
    ax.set_ylabel("Fator 2")
    ax.set_title("MOFA — promotor × corpo")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(saida / "mofa_fatores.png")
    log(f"Separação {y0}×{y1} no Fator 1: {sep[0]:.3f}")

    escrever_resumo(saida, "mofa_resumo.json", {
        "motor": MOTOR,
        "n_fatores": args.fatores,
        "n_amostras": len(amostras),
        "genes_por_view": {"promotor": len(feats_p), "corpo": len(feats_c)},
        "variancia_explicada_pct": r2,
        "condicao_y0": y0,
        "condicao_y1": y1,
        "n_y0": n_y0,
        "n_y1": n_y1,
        # conferência do rótulo pelo NOME da coluna (<condicao>__<amostra>): = n_amostras do montar
        "n_rotulo_igual_prefixo": n_prefixo,
        "separacao_por_fator": sep,
        "separacao_fator1": sep[0],
    })


if __name__ == "__main__":
    main()
