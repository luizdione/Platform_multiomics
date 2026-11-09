"""ml_diagnostico.py — diagnóstico de informatividade da matriz antes da classificação.

Passo do pipeline ml_aims_vcf logo depois do dataset (rápido; falha cedo). Responde a duas
perguntas que o painel mínimo, o núcleo estável e a recorrência entre folds NÃO respondem
sozinhos:
  1. Empate em importância zero: o top-k do ml_robustez é `argsort(feature_importances_)`
     (ml_robustez.py:40-41). Se o XGBoost dá ganho > 0 a menos de k marcadores, o resto do
     top-k sai de um empate em zero, decidido pela ordem do argsort — não por estabilidade.
     No demo (Angus × Brahman) um único SNP separa as classes e o XGBoost para nele.
  2. Separador perfeito: um marcador que, sozinho, divide as duas classes por um corte.
     Acurácia no teto, nesse caso, mede o marcador, não a força de um painel.

O pré-processamento é IDÊNTICO ao do ml_robustez (ml_robustez.py:58-63): matriz com
index_col=0 e na_values='NA', amostras na ordem dos rótulos, y = 1 para a classe
alfabeticamente maior, imputação pela mediana. Os modelos são os mesmos: `_xgb()` do robustez
(ml_robustez.py:35-37), nos dados completos e nos 5 folds de StratifiedKFold(5, shuffle,
semente 42) do A2 (ml_robustez.py:85); o RF final do ml_classificar (ml_classificar.py:108).

Saída: diagnostico_resumo.json. Os alertas são 0/1 (int, não bool: bool não vira fato do
relatório) e o pipeline os transforma em frase obrigatória do relatório.

Uso:
  python3 modules/ml/scripts/ml_diagnostico.py \
      --matriz <genotipos.tsv> --rotulos <rotulos.tsv> --k-painel 50 --saida <dir>
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.model_selection import StratifiedKFold
from xgboost import XGBClassifier

from _comum import escrever_resumo, log

SEMENTE = 42  # a mesma do robustez e do classificar


def _xgb() -> XGBClassifier:
    # = ml_robustez._xgb (ml_robustez.py:35-37)
    return XGBClassifier(n_estimators=300, max_depth=6, random_state=SEMENTE,
                         n_jobs=2, eval_metric="logloss")


def _rf() -> RandomForestClassifier:
    # = o RF final do SHAP (ml_classificar.py:108)
    return RandomForestClassifier(n_estimators=300, random_state=SEMENTE, n_jobs=2)


def separadores_perfeitos(X: np.ndarray, y: np.ndarray) -> list[int]:
    """Índices (na ordem da matriz) dos marcadores com um corte t tal que uma classe fica toda
    ≤ t e a outra toda > t — isto é, max de uma classe < min da outra."""
    a, b = X[y == 0], X[y == 1]
    sep = (a.max(axis=0) < b.min(axis=0)) | (b.max(axis=0) < a.min(axis=0))
    return np.flatnonzero(sep).tolist()


def main() -> None:
    p = argparse.ArgumentParser(description="Diagnóstico de informatividade (empates em zero)")
    p.add_argument("--matriz", required=True)
    p.add_argument("--rotulos", required=True)
    p.add_argument("--k-painel", type=int, default=50)
    p.add_argument("--saida", required=True)
    args = p.parse_args()

    matriz_path, saida = Path(args.matriz), Path(args.saida)
    if not matriz_path.exists():
        raise SystemExit(f"matriz não encontrada: {matriz_path} (rode o dataset antes)")
    if args.k_painel < 1:
        raise SystemExit(f"--k-painel precisa ser ≥ 1: {args.k_painel}")

    # pré-processamento do ml_robustez.py:58-63, linha a linha
    matriz = pd.read_csv(matriz_path, sep="\t", index_col=0, na_values="NA")
    rotulos = pd.read_csv(args.rotulos, sep="\t", index_col=0)["grupo"]
    X_df = matriz.T.loc[rotulos.index]
    classes = sorted(rotulos.unique())
    if len(classes) != 2:
        raise SystemExit(f"os rótulos precisam de exatamente 2 classes (achei {len(classes)})")
    y = (rotulos == classes[1]).astype(int).values
    X = SimpleImputer(strategy="median").fit_transform(X_df.values)
    nomes = list(matriz.index)
    if X.shape[1] != len(nomes):
        # o SimpleImputer descarta a coluna toda NA e os nomes deixariam de casar com as
        # colunas (o validador matriz_marcadores já reprova essa linha)
        raise SystemExit(f"{len(nomes) - X.shape[1]} marcador(es) todo NA: o imputador os descarta")
    log(f"{X.shape[1]} marcadores × {X.shape[0]} amostras; classes {classes[0]} (y=0) e "
        f"{classes[1]} (y=1); k={args.k_painel}")

    n_xgb_completo = int((_xgb().fit(X, y).feature_importances_ > 0).sum())
    cv = StratifiedKFold(5, shuffle=True, random_state=SEMENTE)
    por_fold = [int((_xgb().fit(X[treino], y[treino]).feature_importances_ > 0).sum())
                for treino, _teste in cv.split(X, y)]
    n_xgb_min = min(por_fold)
    n_rf = int((_rf().fit(X, y).feature_importances_ > 0).sum())
    seps = separadores_perfeitos(X, y)
    log(f"XGBoost: ganho > 0 em {n_xgb_completo} marcador(es) nos dados completos; por fold "
        f"{por_fold} (mínimo {n_xgb_min}). RF: importância > 0 em {n_rf}. "
        f"Separadores perfeitos: {len(seps)}")

    resumo: dict = {
        "n_marcadores": int(X.shape[1]), "n_amostras": int(X.shape[0]), "k_painel": args.k_painel,
        "n_ganho_pos_xgb_completo": n_xgb_completo,
        "n_ganho_pos_xgb_min_folds": n_xgb_min,
        "n_imp_pos_rf": n_rf,
        "n_separadores_perfeitos": len(seps),
    }
    if seps:
        resumo["primeiro_separador"] = str(nomes[seps[0]])
    resumo.update({
        "k_informativo": min(args.k_painel, n_xgb_min),
        "alerta_empate_zero": int(n_xgb_min < args.k_painel),
        "alerta_separador_perfeito": int(len(seps) >= 1),
    })
    escrever_resumo(saida, "diagnostico_resumo.json", resumo)


if __name__ == "__main__":
    main()
