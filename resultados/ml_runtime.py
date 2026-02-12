import joblib
import numpy as np
import pandas as pd
from functools import lru_cache
from django.conf import settings
from pathlib import Path




@lru_cache(maxsize=1)
def load_bundle():
    # Ajusta ruta
    path = Path(settings.BASE_DIR) / "resultados" / "ml" / "modelo_tamizaje_bundle.pkl"
    bundle = joblib.load(path)
    return bundle

def predict_proba_row(x_dict: dict) -> dict:
    """
    x_dict: {"X_PANAS_Negativo":..., ...}
    retorna: {"proba": float, "nivel": "BAJO|MEDIO|ALTO", "thr_medio":..., "thr_alto":...}
    """
    bundle = load_bundle()
    model = bundle["model"]
    feature_cols = bundle["feature_cols"]
    thr_medio = float(bundle["thresholds"]["thr_medio"])
    thr_alto  = float(bundle["thresholds"]["thr_alto"])

    # DataFrame con columnas exactas
    X = pd.DataFrame([[x_dict.get(c) for c in feature_cols]], columns=feature_cols)
    proba = float(model.predict_proba(X)[0, 1])

    if proba >= thr_alto:
        nivel = "ALTO"
    elif proba >= thr_medio:
        nivel = "MEDIO"
    else:
        nivel = "BAJO"

    return {"proba": proba, "nivel": nivel, "thr_medio": thr_medio, "thr_alto": thr_alto}

import numpy as np
from sklearn.pipeline import Pipeline

def get_model_explanation(features_usadas: dict | None = None):
    """
    Devuelve explicación del modelo:
    - feature
    - coef
    - odds_ratio
    - valor_estudiante
    - interpretación clínica
    """

    bundle = load_bundle()
    model = bundle["model"]
    feature_cols = bundle["feature_cols"]

    # ==========================================================
    # 1) Extraer regresión logística del Pipeline
    # ==========================================================
    if isinstance(model, Pipeline):
        clf = model.steps[-1][1]   # último paso
    else:
        clf = model

    if not hasattr(clf, "coef_"):
        return []

    # ==========================================================
    # 2) Coeficientes y odds ratios
    # ==========================================================
    coefs = clf.coef_[0]
    odds = np.exp(coefs)

    # ==========================================================
    # 3) Construir explicación
    # ==========================================================
    explanation = []

    for f, c, o in zip(feature_cols, coefs, odds):
        val = features_usadas.get(f) if features_usadas else None

        if o > 1:
            interp = "Aumenta la probabilidad de riesgo"
        elif o < 1:
            interp = "Disminuye la probabilidad de riesgo"
        else:
            interp = "Sin efecto relevante"

        explanation.append({
            "feature": f,
            "coef": float(c),
            "odds": float(o),
            "valor": val,
            "interpretacion": interp,
        })

    # Ordenar por impacto absoluto
    explanation.sort(key=lambda x: abs(x["coef"]), reverse=True)

    return explanation


