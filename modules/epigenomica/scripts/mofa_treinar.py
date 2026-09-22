"""mofa_treinar.py — MOFA via mofapy2 entry_point (python puro, sem wrapper R).

DECISÃO DE ENGENHARIA (2026-09-03): o caminho MOFA2-R(volume)+reticulate+
mofapy2 empilhou 4 incompatibilidades de época (checagem de versão, scipy.shape,
np.float, save quebrando para o fluxo "interrupted"). O treino passa a usar o
entry_point OFICIAL do mofapy2 diretamente; fatores (Z) e pesos (W) são lidos
do HDF5 e a variância explicada por view/fator é computada explicitamente
(R² = 1 − SS(Y − Z_k·W_kᵀ)/SS(Y)) — transparente e didático.

Uso:
  python3 modules/epigenomica/scripts/mofa_treinar.py \
      --views <dir com view_*.tsv + grupos.tsv> --fatores 3 --saida <dir>
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from _comum import escrever_resumo, log


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


def main() -> None:
    p = argparse.ArgumentParser(description="MOFA (mofapy2 direto) nas views preparadas")
    p.add_argument("--views", required=True)
    p.add_argument("--fatores", type=int, default=3)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    views_dir, saida = Path(args.views), Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    amostras, feats_p, Yp = _ler_view(views_dir / "view_promotor.tsv")
    _, feats_c, Yc = _ler_view(views_dir / "view_corpo.tsv")
    grupos = {}
    for linha in (views_dir / "grupos.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        a, g = linha.split("\t")
        grupos[a] = g
    log(f"Views: promotor {Yp.shape[0]}×{Yp.shape[1]}, corpo {Yc.shape[0]}×{Yc.shape[1]}")

    # mofapy2 espera AMOSTRAS × FEATURES e centra internamente. ATENÇÃO
    # (2026-09-03): set_data_matrix MUTA a lista recebida (desembrulha o nível
    # de grupos in-place) — por isso guardamos NOSSAS matrizes à parte e
    # entregamos ao mofa uma lista descartável.
    matrizes = [Yp.T.copy(), Yc.T.copy()]           # amostras × features (nossas)
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

    with (saida / "variancia_explicada.tsv").open("w", encoding="utf-8") as f:
        f.write("fator\t" + "\t".join(nomes_views) + "\n")
        for k in range(args.fatores):
            f.write(f"Factor{k + 1}\t" + "\t".join(str(r2[v][k]) for v in nomes_views) + "\n")

    with (saida / "fatores_por_amostra.tsv").open("w", encoding="utf-8") as f:
        f.write("amostra\tgrupo\t" + "\t".join(f"Factor{k + 1}" for k in range(args.fatores)) + "\n")
        for i, a in enumerate(amostras):
            f.write(a + "\t" + grupos.get(a, "?") + "\t"
                    + "\t".join(f"{Z[i, k]:.4f}" for k in range(args.fatores)) + "\n")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cores = ["#0f766e" if grupos.get(a) == "Nelore" else "#e67e22" for a in amostras]
    fig, ax = plt.subplots(figsize=(7, 5), dpi=120)
    ax.scatter(Z[:, 0], Z[:, 1] if args.fatores > 1 else np.zeros(len(amostras)),
               c=cores, s=110)
    for i, a in enumerate(amostras):
        ax.annotate(grupos.get(a, "?"), (Z[i, 0], Z[i, 1]), fontsize=7,
                    xytext=(4, 4), textcoords="offset points")
    ax.set_xlabel(f"Fator 1 ({r2['promotor'][0]:.1f}% prom / {r2['corpo'][0]:.1f}% corpo)")
    ax.set_ylabel("Fator 2")
    ax.set_title("MOFA — promotor × corpo (painel 108, CD4)")
    fig.tight_layout()
    png = saida / "mofa_fatores.png"
    fig.savefig(png)

    grupos_arr = np.array([grupos.get(a) for a in amostras])
    sep_f1 = abs(float(Z[grupos_arr == "Nelore", 0].mean() - Z[grupos_arr == "HF", 0].mean()))
    try:
        ep.save(str(saida / "mofa_modelo.hdf5"))
    except (SystemExit, Exception) as e:  # o save do 0.6.x exige train_stats que o
        # treino encerrado por sys.exit não popula — hdf5 é opcional aqui
        log(f"save do mofapy2 pulado ({e.__class__.__name__}) — tsv/png/resumo são a saída canônica")
    log(f"Separação Nelore×HF no Fator 1: {sep_f1:.3f}")

    escrever_resumo(saida, "mofa_resumo.json", {
        "motor": "mofapy2 entry_point direto (sem wrapper R — decisão 2026-09-03)",
        "n_fatores": args.fatores,
        "n_amostras": len(amostras),
        "genes_por_view": {"promotor": len(feats_p), "corpo": len(feats_c)},
        "variancia_explicada_pct": r2,
        "separacao_grupos_fator1": round(sep_f1, 3),
        "fatores_tsv": str(saida / "fatores_por_amostra.tsv"),
        "figura_png": str(png),
    })


if __name__ == "__main__":
    main()
