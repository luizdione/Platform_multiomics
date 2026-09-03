"""ml_classificar.py — classificador de ancestralidade com CV aninhada + SHAP.

Migração curada do nested_cv_fst03.py do bovine_aim_gwas_ml_thermalcore —
decisões herdadas EXPLÍCITAS: (1) CV ANINHADA (busca de hiperparâmetros só no
laço interno), (2) métricas BALANCEADAS (balanced accuracy, F1-macro, MCC,
ROC-AUC, matriz de confusão) porque as classes são desbalanceadas, (3) painel
mínimo selecionado DENTRO de cada fold de treino (sem vazamento), (4) imputação
por mediana. Agnóstico ao marcador: a matriz pode ser SNP (0/1/2), CpG ou gene.

Uso:
  python3 modules/ml/scripts/ml_classificar.py \
      --matriz <genotipos.tsv> --rotulos <rotulos.tsv> --k-painel 50 --saida <dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             confusion_matrix, f1_score, matthews_corrcoef,
                             roc_auc_score)
from sklearn.model_selection import GridSearchCV, StratifiedKFold
from xgboost import XGBClassifier

from _comum import escrever_resumo, log

SEMENTE = 42  # herdada do thermalcore (reprodutibilidade)


def _metricas(y_true, y_pred, y_prob) -> dict:
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_true, y_pred)), 4),
        "f1_macro": round(float(f1_score(y_true, y_pred, average="macro")), 4),
        "mcc": round(float(matthews_corrcoef(y_true, y_pred)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, y_prob)), 4) if y_prob is not None else None,
        "confusion": confusion_matrix(y_true, y_pred).tolist(),
    }


def _nested_cv(nome: str, estimador, grade: dict, X, y, k_painel: int):
    externo = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEMENTE)
    y_pred = np.empty_like(y)
    y_prob = np.empty(len(y), dtype=float)
    y_pred_painel = np.empty_like(y)
    for treino, teste in externo.split(X, y):
        interno = GridSearchCV(estimador, grade, scoring="balanced_accuracy",
                               cv=StratifiedKFold(3, shuffle=True, random_state=SEMENTE),
                               n_jobs=1)
        interno.fit(X[treino], y[treino])
        melhor = interno.best_estimator_
        y_pred[teste] = melhor.predict(X[teste])
        y_prob[teste] = melhor.predict_proba(X[teste])[:, 1]
        # painel mínimo DENTRO do fold de treino (sem vazamento): top-k por
        # importância do modelo do fold, re-treinado só com essas features
        ordem = np.argsort(melhor.feature_importances_)[::-1][:k_painel]
        reduzido = type(melhor)(**melhor.get_params()).fit(X[treino][:, ordem], y[treino])
        y_pred_painel[teste] = reduzido.predict(X[teste][:, ordem])
    return {
        "painel_completo": _metricas(y, y_pred, y_prob),
        f"painel_minimo_top{k_painel}": _metricas(y, y_pred_painel, None),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Classificação com nested CV + SHAP")
    p.add_argument("--matriz", required=True, help="TSV: variante × amostras (0/1/2/NA)")
    p.add_argument("--rotulos", required=True, help="TSV: amostra, grupo")
    p.add_argument("--k-painel", type=int, default=50)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    matriz_path, rotulos_path, saida = Path(args.matriz), Path(args.rotulos), Path(args.saida)
    if not matriz_path.exists():
        raise SystemExit(f"matriz não encontrada: {matriz_path} (rode o passo do dataset antes)")
    saida.mkdir(parents=True, exist_ok=True)

    matriz = pd.read_csv(matriz_path, sep="\t", index_col=0, na_values="NA")
    rotulos = pd.read_csv(rotulos_path, sep="\t", index_col=0)["grupo"]
    X_bruto = matriz.T.loc[rotulos.index]
    classes = sorted(rotulos.unique())
    y = (rotulos == classes[1]).astype(int).values  # 1 = classe alfabeticamente maior
    log(f"Matriz: {X_bruto.shape[1]} variantes × {X_bruto.shape[0]} amostras; "
        f"classes: {classes[0]}={int((y == 0).sum())}, {classes[1]}={int((y == 1).sum())}")
    X = SimpleImputer(strategy="median").fit_transform(X_bruto.values)

    modelos = {
        "random_forest": (RandomForestClassifier(random_state=SEMENTE, n_jobs=2),
                          {"n_estimators": [300], "max_depth": [None, 12]}),
        "xgboost": (XGBClassifier(random_state=SEMENTE, n_jobs=2, eval_metric="logloss"),
                    {"n_estimators": [300], "max_depth": [3, 6]}),
    }
    resultados = {}
    for nome, (est, grade) in modelos.items():
        log(f"Nested CV (5 externos × 3 internos): {nome}…")
        resultados[nome] = _nested_cv(nome, est, grade, X, y, args.k_painel)
        log(f"  balanced_accuracy (painel completo): "
            f"{resultados[nome]['painel_completo']['balanced_accuracy']}")

    log("SHAP no modelo final (RF, dados completos)…")
    import shap

    rf_final = RandomForestClassifier(n_estimators=300, random_state=SEMENTE, n_jobs=2).fit(X, y)
    explicador = shap.TreeExplainer(rf_final)
    valores = explicador.shap_values(X)
    media_abs = np.abs(valores[1] if isinstance(valores, list) else valores[..., 1]
                       if np.ndim(valores) == 3 else valores).mean(axis=0)
    ordem = np.argsort(media_abs)[::-1]
    top = [(matriz.index[i], float(media_abs[i])) for i in ordem[: args.k_painel]]

    shap_tsv = saida / "shap_top.tsv"
    with shap_tsv.open("w", encoding="utf-8") as f:
        f.write("VARIANTE\tSHAP_MEDIO_ABS\n")
        for var, v in top:
            f.write(f"{var}\t{v:.6g}\n")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 5.5), dpi=120)
    nomes20 = [t[0] for t in top[:20]][::-1]
    vals20 = [t[1] for t in top[:20]][::-1]
    ax.barh(nomes20, vals20, color="#0f766e")
    ax.set_xlabel("|SHAP| médio (contribuição p/ classificação)")
    ax.set_title(f"Top 20 marcadores — {classes[0]} × {classes[1]} (RF)")
    ax.tick_params(axis="y", labelsize=7)
    fig.tight_layout()
    png = saida / "shap_top.png"
    fig.savefig(png)

    escrever_resumo(saida, "ml_resumo.json", {
        "classes": {classes[0]: int((y == 0).sum()), classes[1]: int((y == 1).sum())},
        "n_variantes": int(X.shape[1]),
        "n_amostras": int(X.shape[0]),
        "desenho": "nested CV 5×3 (herdado do thermalcore); painel mínimo intra-fold; imputação mediana",
        "k_painel_minimo": args.k_painel,
        "modelos": resultados,
        "shap_top_tsv": str(shap_tsv),
        "shap_png": str(png),
    })


if __name__ == "__main__":
    main()
