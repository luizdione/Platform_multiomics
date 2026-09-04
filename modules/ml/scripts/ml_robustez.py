"""ml_robustez.py — robustez do painel mínimo (bootstrap + recorrência + IC95%).

Migração curada de bovine_aim_gwas_ml_thermalcore/scripts/robustness/
A1A2A3_bootstrap_folds_ic.py — as TRÊS perguntas do parecer, herdadas:
  A1  estabilidade: B reamostragens bootstrap estratificadas; em cada uma o
      top-k é re-selecionado por ganho do XGBoost → frequência de inclusão de
      cada marcador (núcleo estável = presente em ≥80% dos bootstraps);
  A2  recorrência: CV externa 5-fold; top-k re-selecionado DENTRO de cada fold
      de treino → quantos marcadores recorrem em 5/5, 4/5, 3/5 folds;
  A3  IC95% percentílico (bootstrap) das métricas do painel mínimo.

Uso:
  python3 modules/ml/scripts/ml_robustez.py \
      --matriz <genotipos.tsv> --rotulos <rotulos.tsv> \
      --k-painel 50 --n-boot 100 --saida <dir>
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.metrics import balanced_accuracy_score, f1_score, matthews_corrcoef
from sklearn.model_selection import StratifiedKFold
from xgboost import XGBClassifier

from _comum import escrever_resumo, log

SEMENTE = 42


def _xgb() -> XGBClassifier:
    return XGBClassifier(n_estimators=300, max_depth=6, random_state=SEMENTE,
                         n_jobs=2, eval_metric="logloss")


def _top_k(modelo: XGBClassifier, k: int) -> np.ndarray:
    return np.argsort(modelo.feature_importances_)[::-1][:k]


def main() -> None:
    p = argparse.ArgumentParser(description="Robustez A1/A2/A3 do painel mínimo")
    p.add_argument("--matriz", required=True)
    p.add_argument("--rotulos", required=True)
    p.add_argument("--k-painel", type=int, default=50)
    p.add_argument("--n-boot", type=int, default=100)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    matriz_path, saida = Path(args.matriz), Path(args.saida)
    if not matriz_path.exists():
        raise SystemExit(f"matriz não encontrada: {matriz_path} (rode o dataset antes)")
    saida.mkdir(parents=True, exist_ok=True)

    matriz = pd.read_csv(matriz_path, sep="\t", index_col=0, na_values="NA")
    rotulos = pd.read_csv(args.rotulos, sep="\t", index_col=0)["grupo"]
    X_df = matriz.T.loc[rotulos.index]
    classes = sorted(rotulos.unique())
    y = (rotulos == classes[1]).astype(int).values
    X = SimpleImputer(strategy="median").fit_transform(X_df.values)
    nomes = list(matriz.index)
    k = min(args.k_painel, X.shape[1])
    rng = np.random.default_rng(SEMENTE)
    log(f"{X.shape[1]} marcadores × {X.shape[0]} amostras; k={k}; B={args.n_boot}")

    # A1 — estabilidade por bootstrap estratificado
    log("A1: bootstrap de seleção do painel…")
    inclusao: Counter[int] = Counter()
    idx_por_classe = [np.where(y == c)[0] for c in (0, 1)]
    for b in range(args.n_boot):
        amostra = np.concatenate([rng.choice(ix, size=len(ix), replace=True)
                                  for ix in idx_por_classe])
        m = _xgb().fit(X[amostra], y[amostra])
        inclusao.update(_top_k(m, k).tolist())
    freq = {nomes[i]: inclusao.get(i, 0) / args.n_boot for i in range(len(nomes))}
    nucleo = sorted((v for v in freq.items() if v[1] >= 0.8),
                    key=lambda kv: kv[1], reverse=True)
    log(f"A1: núcleo estável (freq ≥ 0,80): {len(nucleo)} marcadores")

    # A2 — recorrência entre folds externos
    log("A2: recorrência entre 5 folds externos…")
    cv = StratifiedKFold(5, shuffle=True, random_state=SEMENTE)
    recorrencia: Counter[int] = Counter()
    metricas_fold = []
    for treino, teste in cv.split(X, y):
        m = _xgb().fit(X[treino], y[treino])
        sel = _top_k(m, k)
        recorrencia.update(sel.tolist())
        reduzido = _xgb().fit(X[treino][:, sel], y[treino])
        pred = reduzido.predict(X[teste][:, sel])
        metricas_fold.append({
            "balanced_accuracy": balanced_accuracy_score(y[teste], pred),
            "f1_macro": f1_score(y[teste], pred, average="macro"),
            "mcc": matthews_corrcoef(y[teste], pred),
        })
    contagem_rec = Counter(recorrencia.values())
    rec = {"5/5": contagem_rec.get(5, 0), "4/5": contagem_rec.get(4, 0),
           "3/5": contagem_rec.get(3, 0)}
    log(f"A2: recorrência 5/5={rec['5/5']}, 4/5={rec['4/5']}, 3/5={rec['3/5']}")

    # A3 — IC95% percentílico (bootstrap das métricas por fold agregadas)
    log("A3: IC95% percentílico das métricas do painel mínimo…")
    ic = {}
    for chave in ("balanced_accuracy", "f1_macro", "mcc"):
        valores = np.array([m[chave] for m in metricas_fold])
        reamostras = [float(np.mean(rng.choice(valores, size=len(valores), replace=True)))
                      for _ in range(args.n_boot * 10)]
        ic[chave] = {"media": round(float(valores.mean()), 4),
                     "ic95": [round(float(np.percentile(reamostras, 2.5)), 4),
                              round(float(np.percentile(reamostras, 97.5)), 4)]}

    tsv = saida / "nucleo_estavel.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write("MARCADOR\tFREQ_BOOTSTRAP\tRECORRENCIA_FOLDS\n")
        for nome, fr in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:k]:
            i = nomes.index(nome)
            f.write(f"{nome}\t{fr:.3f}\t{recorrencia.get(i, 0)}/5\n")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=120)
    ax.hist(list(freq.values()), bins=20, color="#0f766e")
    ax.axvline(0.8, color="#e74c3c", ls="--", lw=1.5,
               label=f"núcleo estável (≥0,80): {len(nucleo)}")
    ax.set_xlabel(f"Frequência de inclusão no top-{k} ({args.n_boot} bootstraps)")
    ax.set_ylabel("Nº de marcadores")
    ax.set_title("A1 — Estabilidade do painel mínimo")
    ax.legend()
    fig.tight_layout()
    png = saida / "robustez.png"
    fig.savefig(png)

    escrever_resumo(saida, "robustez_resumo.json", {
        "desenho": "A1/A2/A3 herdados de A1A2A3_bootstrap_folds_ic.py (thermalcore)",
        "k_painel": k, "n_boot": args.n_boot,
        "A1_nucleo_estavel_n": len(nucleo),
        "A1_top5_nucleo": [n for n, _ in nucleo[:5]],
        "A2_recorrencia": rec,
        "A3_ic95": ic,
        "tabela_tsv": str(tsv), "figura_png": str(png),
    })


if __name__ == "__main__":
    main()
