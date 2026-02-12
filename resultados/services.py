# resultados/services.py
from __future__ import annotations

import os
import joblib
import numpy as np

from django.conf import settings
from django.utils import timezone
from django.db.models import Q

from forms.models import SesionEvaluacion, Respuesta
from resultados.ml_runtime import get_model_explanation
from .models import PrediccionRiesgo
import pandas as pd
import numpy as np
from io import BytesIO
import base64
import matplotlib.pyplot as plt
from .ml_runtime import load_bundle
from .ml_utils import CLINICAL_METADATA

# ============================================================
# CONFIG
# ============================================================

# Ruta del bundle exportado desde notebook
MODEL_BUNDLE_PATH = os.path.join(
    settings.BASE_DIR, "resultados", "ml", "modelo_tamizaje_bundle.pkl"
)

# Activa debug fácil:
# - por defecto usa settings.DEBUG
# - o puedes forzar con env var ML_DEBUG=1
ML_DEBUG = bool(getattr(settings, "DEBUG", False)) or (os.environ.get("ML_DEBUG") == "1")

def _dbg(*args):
    pass


_bundle_cache = None


# ============================================================
# 1) Bundle loader + triage
# ============================================================

def _load_bundle():
    global _bundle_cache
    if _bundle_cache is not None:
        _dbg("bundle cache HIT")
        return _bundle_cache

    _dbg("MODEL_BUNDLE_PATH =", MODEL_BUNDLE_PATH, "exists?", os.path.exists(MODEL_BUNDLE_PATH))
    if not os.path.exists(MODEL_BUNDLE_PATH):
        return None

    _bundle_cache = joblib.load(MODEL_BUNDLE_PATH)
    _dbg("bundle keys:", list(_bundle_cache.keys()) if isinstance(_bundle_cache, dict) else type(_bundle_cache))
    return _bundle_cache


def _nivel_por_prob(p: float | None, thr_medio: float, thr_alto: float) -> str:
    """
    Choices del proyecto: BAJO / MODERADO / ALTO / SIN_DATOS
    """
    if p is None:
        return "SIN_DATOS"
    if p >= float(thr_alto):
        return "ALTO"
    if p >= float(thr_medio):
        return "MODERADO"
    return "BAJO"


# ============================================================
# 2) Sesión completada (robusta a códigos)
# ============================================================

def _normalize_code(s: str) -> str:
    return (s or "").strip().upper()


def _get_last_completed_session(perfil, codigos: str | list[str]):
    """
    Busca la última sesión COMPLETADA del estudiante para uno o varios códigos.
    Soporta iexact y lista.
    """
    if isinstance(codigos, str):
        codigos = [codigos]

    codigos_norm = [_normalize_code(c) for c in codigos if (c or "").strip()]
    if not codigos_norm:
        return None

    q = Q()
    for c in codigos_norm:
        q |= Q(cuestionario__codigo__iexact=c)

    s = (
        SesionEvaluacion.objects
        .select_related("cuestionario")
        .filter(estudiante=perfil, estado="COMPLETADA")
        .filter(q)
        .order_by("-fecha_fin", "-fecha_inicio", "-id")
        .first()
    )

    if s:
        _dbg("last_completed_session for", codigos_norm, "->", s.id, s.cuestionario.codigo)
    else:
        _dbg("last_completed_session for", codigos_norm, "-> None")

    return s


# ============================================================
# 3) Lectura numérica robusta (valor_numerico u opcion.valor)
#    + fallback por orden si pregunta.codigo viene vacío
# ============================================================

def _value_from_respuesta(r: Respuesta) -> float | None:
    if r.valor_numerico is not None:
        try:
            return float(r.valor_numerico)
        except Exception:
            return None

    if r.opcion_seleccionada and r.opcion_seleccionada.valor is not None:
        try:
            return float(r.opcion_seleccionada.valor)
        except Exception:
            return None

    return None


def _get_answers_dict_by_prefix(session_id: int, prefix: str, n_items: int) -> dict[str, float]:
    """
    Devuelve dict {PREFIX_XX: valor} para XX=01..n_items

    Regla:
      - Si pregunta.codigo existe y empieza con prefix -> usa ese código
      - Si pregunta.codigo está vacío -> usa pregunta.orden para construir PREFIX_XX

    OJO: prefix debe venir como "PANAS_" / "CASO_" / "WHOQOL_"
    """
    prefix = (prefix or "").strip().upper()
    if not prefix.endswith("_"):
        prefix = prefix + "_"

    qs = (
        Respuesta.objects
        .select_related("pregunta", "opcion_seleccionada")
        .filter(sesion_id=session_id)
        .order_by("pregunta__orden", "id")
    )

    out: dict[str, float] = {}
    miss_val = 0

    for r in qs:
        v = _value_from_respuesta(r)
        if v is None:
            miss_val += 1
            continue

        code = (getattr(r.pregunta, "codigo", "") or "").strip().upper()
        orden = getattr(r.pregunta, "orden", None)

        # 1) código real (si existe)
        if code.startswith(prefix):
            out[code] = v
            continue

        # 2) fallback por orden
        if isinstance(orden, int) and 1 <= orden <= n_items:
            gen_code = f"{prefix}{orden:02d}"
            out[gen_code] = v

    valid_codes = {f"{prefix}{i:02d}" for i in range(1, n_items + 1)}
    out = {k: out[k] for k in out.keys() if k in valid_codes}

    _dbg(
        f"answers_by_prefix session={session_id} prefix={prefix} n_items={n_items} -> out={len(out)} "
        f"miss_val={miss_val} qs_count={qs.count()}"
    )
    return out


def _sum_values(d: dict[str, float], codes: list[str]) -> float | None:
    vals = [d.get(c) for c in codes if d.get(c) is not None]
    return float(sum(vals)) if vals else None


def _mean_values(d: dict[str, float], codes: list[str]) -> float | None:
    vals = [d.get(c) for c in codes if d.get(c) is not None]
    return float(np.mean(vals)) if vals else None


# ============================================================
# 4) PANAS -> positivo / negativo
# ============================================================

PANAS_POS_IDX = [1, 3, 5, 9, 10, 12, 14, 16, 17, 19]
PANAS_NEG_IDX = [2, 4, 6, 7, 8, 11, 13, 15, 18, 20]

def _build_panas_features(perfil) -> dict:
    s = _get_last_completed_session(perfil, "PANAS")
    if not s:
        return {
            "X_PANAS_Positivo": None,
            "X_PANAS_Negativo": None,
            "PANAS_POS_SUM": None,
            "PANAS_NEG_SUM": None,
            "PANAS_POS_MEAN": None,
            "PANAS_NEG_MEAN": None,
            "PANAS_N_RESP": 0,
        }

    ans = _get_answers_dict_by_prefix(s.id, "PANAS_", 20)

    pos_codes = [f"PANAS_{i:02d}" for i in PANAS_POS_IDX]
    neg_codes = [f"PANAS_{i:02d}" for i in PANAS_NEG_IDX]

    pos_sum  = _sum_values(ans, pos_codes)
    neg_sum  = _sum_values(ans, neg_codes)
    pos_mean = _mean_values(ans, pos_codes)
    neg_mean = _mean_values(ans, neg_codes)
    n_resp = len(ans)

    _dbg("PANAS session", s.id, "n_resp", n_resp, "pos_mean", pos_mean, "neg_mean", neg_mean)

    return {
        # ML inputs (los del modelo final)
        "X_PANAS_Positivo": pos_mean,
        "X_PANAS_Negativo": neg_mean,

        # Para mostrar SIN ML
        "PANAS_POS_SUM": pos_sum,
        "PANAS_NEG_SUM": neg_sum,
        "PANAS_POS_MEAN": pos_mean,
        "PANAS_NEG_MEAN": neg_mean,
        "PANAS_N_RESP": n_resp,
        "PANAS_SESSION_ID": s.id,
    }


# ============================================================
# 5) CASO-A30 -> total + mean
# ============================================================

def _build_caso_features(perfil) -> dict:
    s = _get_last_completed_session(perfil, ["CASO-A30", "CASO-30", "CASO"])
    if not s:
        return {
            "X_CASO_MEAN": None,
            "CASO_TOTAL": None,
            "CASO_N_RESP": 0,
        }

    ans = _get_answers_dict_by_prefix(s.id, "CASO_", 30)
    codes = [f"CASO_{i:02d}" for i in range(1, 31)]

    total = _sum_values(ans, codes)
    mean_ = (float(total) / 30.0) if total is not None else None

    _dbg("CASO session", s.id, "n_resp", len(ans), "total", total, "mean", mean_)

    return {
        # ML
        "X_CASO_MEAN": mean_,

        # Mostrar SIN ML
        "CASO_TOTAL": total,
        "CASO_MEAN": mean_,
        "CASO_N_RESP": len(ans),
        "CASO_SESSION_ID": s.id,
        "CASO_INTERP": (
            "Suma de 30 ítems (1–5). Altas = buena asertividad. "
            "Bajas = pasividad o agresividad indirecta. Media teórica: 90."
        ),
    }


# ============================================================
# 6) WHOQOL-BREF -> dominios (con reversa)
# ============================================================

WHOQOL_REVERSE = {3, 4, 26}
WHOQOL_PHYS   = [3, 4, 10, 15, 16, 17, 18]
WHOQOL_PSYCH  = [5, 6, 7, 11, 19, 26]
WHOQOL_SOCIAL = [20, 21, 22]
WHOQOL_ENV    = [8, 9, 12, 13, 14, 23, 24, 25]

def _whoqol_score_item(i: int, v: float | None) -> float | None:
    if v is None:
        return None
    try:
        v = float(v)
    except Exception:
        return None
    if v < 1 or v > 5:
        return None
    if i in WHOQOL_REVERSE:
        return 6.0 - v
    return v

def _build_whoqol_features(perfil) -> dict:
    # tu código real del cuestionario es WHO-QOL
    s = _get_last_completed_session(perfil, ["WHO-QOL", "WHOQOL", "WHOQOL-BREF"])
    if not s:
        return {
            "X_WHOQOL_PHYS_MEAN": None,
            "X_WHOQOL_PSYCH_MEAN": None,
            "X_WHOQOL_SOCIAL_MEAN": None,
            "WHOQOL_ENV_MEAN": None,
            "WHOQOL_OVERALL_MEAN": None,
            "WHOQOL_TOTAL_MEAN": None,
            "WHOQOL_N_RESP": 0,
        }

    raw = _get_answers_dict_by_prefix(s.id, "WHOQOL_", 26)

    scored: dict[int, float | None] = {}
    for i in range(1, 27):
        code = f"WHOQOL_{i:02d}"
        scored[i] = _whoqol_score_item(i, raw.get(code))

    def mean_items(items: list[int]) -> float | None:
        vals = [scored[i] for i in items if scored[i] is not None]
        return float(np.mean(vals)) if vals else None

    overall = mean_items([1, 2])
    phys    = mean_items(WHOQOL_PHYS)
    psych   = mean_items(WHOQOL_PSYCH)
    social  = mean_items(WHOQOL_SOCIAL)
    env     = mean_items(WHOQOL_ENV)
    total   = mean_items(list(range(1, 27)))
    n_resp  = sum(1 for i in range(1, 27) if scored[i] is not None)

    _dbg("WHOQOL session", s.id, "n_resp", n_resp, "phys", phys, "psych", psych, "social", social)

    return {
        # ML
        "X_WHOQOL_PHYS_MEAN": phys,
        "X_WHOQOL_PSYCH_MEAN": psych,
        "X_WHOQOL_SOCIAL_MEAN": social,

        # Dashboard
        "WHOQOL_OVERALL_MEAN": overall,
        "WHOQOL_PHYS_MEAN": phys,
        "WHOQOL_PSYCH_MEAN": psych,
        "WHOQOL_SOCIAL_MEAN": social,
        "WHOQOL_ENV_MEAN": env,
        "WHOQOL_TOTAL_MEAN": total,
        "WHOQOL_N_RESP": n_resp,
        "WHOQOL_SESSION_ID": s.id,

        # NUEVO

    }


def interpret_whoqol_total(mean_value: float | None) -> dict:
    """
    Clasificación clínica oficial WHOQOL-BREF
    """
    if mean_value is None:
        return {
            "nivel": "Sin datos suficientes",
            "descripcion": "No se pudo calcular el promedio general."
        }

    if 1.0 <= mean_value <= 2.9:
        return {
            "nivel": "Baja Calidad de Vida",
            "descripcion": "El individuo percibe carencias graves en su bienestar."
        }

    if 3.0 <= mean_value <= 3.9:
        return {
            "nivel": "Calidad de Vida Media",
            "descripcion": "Existe un equilibrio general, pero con áreas claras de oportunidad."
        }

    if 4.0 <= mean_value <= 5.0:
        return {
            "nivel": "Alta Calidad de Vida",
            "descripcion": "Percepción óptima de bienestar y satisfacción general."
        }

    return {
        "nivel": "Fuera de rango",
        "descripcion": "Valor fuera del rango esperado (1–5)."
    }

def _build_whoqol_features_from_session(s) -> dict:
    """
    Calcula WHOQOL directamente desde una sesión específica.
    No busca sesión adicional.
    """

    raw = _get_answers_dict_by_prefix(s.id, "WHOQOL_", 26)

    scored = {}
    for i in range(1, 27):
        code = f"WHOQOL_{i:02d}"
        scored[i] = _whoqol_score_item(i, raw.get(code))

    def mean_items(items):
        vals = [scored[i] for i in items if scored[i] is not None]
        return float(np.mean(vals)) if vals else None

    overall = mean_items([1, 2])
    phys = mean_items(WHOQOL_PHYS)
    psych = mean_items(WHOQOL_PSYCH)
    social = mean_items(WHOQOL_SOCIAL)
    env = mean_items(WHOQOL_ENV)
    total = mean_items(list(range(1, 27)))
    n_resp = sum(1 for i in range(1, 27) if scored[i] is not None)

    # ===== CLASIFICACIÓN AUTOMÁTICA =====
    nivel = None

    if total is not None:
        if total <= 2.9:
            nivel = "Baja Calidad de Vida"
        elif total <= 3.9:
            nivel = "Calidad de Vida Media"
        else:
            nivel = "Alta Calidad de Vida"

    return {
        "WHOQOL_OVERALL_MEAN": overall,
        "WHOQOL_PHYS_MEAN": phys,
        "WHOQOL_PSYCH_MEAN": psych,
        "WHOQOL_SOCIAL_MEAN": social,
        "WHOQOL_ENV_MEAN": env,
        "WHOQOL_TOTAL_MEAN": total,
        "WHOQOL_N_RESP": n_resp,
        "WHOQOL_LEVEL": nivel,
        "WHOQOL_SESSION_ID": s.id,
    }

def _clasificar_whoqol(valor: float | None) -> str | None:
    if valor is None:
        return None
    if valor <= 2.9:
        return "Baja"
    elif valor <= 3.9:
        return "Media"
    return "Alta"

# ============================================================
# 7) Build features GLOBAL (all + ml)
# ============================================================

def build_features(perfil):
    feats_all: dict = {}
    feats_all.update(_build_panas_features(perfil))
    feats_all.update(_build_whoqol_features(perfil))
    feats_all.update(_build_caso_features(perfil))

    bundle = _load_bundle()
    if not bundle or not isinstance(bundle, dict):
        return feats_all, {}

    feature_cols = bundle.get("feature_cols") or []
    feats_ml = {k: feats_all.get(k) for k in feature_cols}

    _dbg("feature_cols", feature_cols)
    _dbg("feats_ml", feats_ml)
    return feats_all, feats_ml


# ============================================================
# 8) Servicio PRINCIPAL: actualizar_prediccion_estudiante
# ============================================================

def actualizar_prediccion_estudiante(perfil):
    """
    - Construye features
    - Si faltan features del modelo -> SIN_DATOS
    - Si están -> predict_proba + triage -> guarda
    """
    feats_all, feats_ml = build_features(perfil)
    obj, _ = PrediccionRiesgo.objects.get_or_create(estudiante=perfil)

    bundle = _load_bundle()
    if not bundle or not isinstance(bundle, dict):
        obj.features = feats_all
        obj.probabilidad = None
        obj.nivel = "SIN_DATOS"
        obj.modelo_version = "bundle_missing"
        obj.actualizado = timezone.now()
        obj.save()
        return obj

    model = bundle.get("model")
    feature_cols = bundle.get("feature_cols") or []
    thresholds = bundle.get("thresholds") or {}

    thr_medio = float(thresholds.get("thr_medio", 0.40))
    thr_alto  = float(thresholds.get("thr_alto", 0.75))

    # Validar features completas
    missing = [k for k in feature_cols if feats_ml.get(k) is None]
    _dbg("missing", missing)

    if missing or model is None:
        obj.features = {
            **feats_all,
            "ML_MISSING": missing,
            "ML_FEATURE_COLS": feature_cols,
        }
        obj.probabilidad = None
        obj.nivel = "SIN_DATOS"
        obj.modelo_version = "bundle_incomplete"
        obj.actualizado = timezone.now()
        obj.save()
        return obj

    # --- IMPORTANTE: usar DataFrame con columnas para evitar warning ---
    try:
        import pandas as pd  # requerido por este fix
        X_df = pd.DataFrame([{k: float(feats_ml[k]) for k in feature_cols}], columns=feature_cols)
        p = float(model.predict_proba(X_df)[0][1])
    except Exception as e:
        obj.features = {**feats_all, "ML_ERROR": str(e)}
        obj.probabilidad = None
        obj.nivel = "SIN_DATOS"
        obj.modelo_version = "bundle_predict_error"
        obj.actualizado = timezone.now()
        obj.save()
        return obj

    nivel = _nivel_por_prob(p, thr_medio=thr_medio, thr_alto=thr_alto)

    obj.features = {
        **feats_all,
        "ML_INPUTS": feats_ml,
        "thr_medio": thr_medio,
        "thr_alto": thr_alto,
    }
    obj.probabilidad = p
    obj.nivel = nivel
    obj.modelo_version = "tamizaje_rl_bundle_v1"
    obj.actualizado = timezone.now()
    obj.save()

    return obj


# ============================================================
# 9) ML readiness + urgencia
# ============================================================

REQUIRED_CODES = ["PANAS", "WHO-QOL", "CASO-A30"]

def ml_ready_for_estudiante(perfil_estudiante):
    """
    True si el estudiante tiene al menos 1 sesión COMPLETADA de cada código requerido.
    Retorna: (ready, completed_count, total_required)
    """
    total = len(REQUIRED_CODES)

    n = (
        SesionEvaluacion.objects
        .filter(estudiante=perfil_estudiante, estado="COMPLETADA")
        .filter(cuestionario__codigo__in=REQUIRED_CODES)
        .values("cuestionario__codigo")
        .distinct()
        .count()
    )
    return (n >= total), n, total


def urgencia_rank(nivel: str) -> int:
    """
    Ranking para ordenar por urgencia:
      ALTO (3) > MODERADO (2) > BAJO (1) > SIN_DATOS (0)
    """
    nivel = (nivel or "").upper().strip()
    if nivel == "ALTO":
        return 3
    if nivel == "MODERADO":
        return 2
    if nivel == "BAJO":
        return 1
    return 0


def get_prediccion_dict(estudiante_perfil):
    """
    Devuelve dict con info ML si existe (no recalcula).
    """
    try:
        p = PrediccionRiesgo.objects.get(estudiante=estudiante_perfil)
        return {
            "nivel": p.nivel or "SIN_DATOS",
            "probabilidad": p.probabilidad,
            "features": p.features or {},
            "actualizado": p.actualizado,
            "modelo_version": p.modelo_version,
        }
    except PrediccionRiesgo.DoesNotExist:
        return None




import math
from sklearn.pipeline import Pipeline

def build_ml_explanation(pred):

    if not pred or not pred.features:
        return {}

    bundle = load_bundle()
    model = bundle["model"]
    feature_cols = bundle["feature_cols"]

    # Extraer regresión logística si está en pipeline
    if isinstance(model, Pipeline):
        clf = model.steps[-1][1]
    else:
        clf = model

    if not hasattr(clf, "coef_"):
        return {}

    coefs = clf.coef_[0]
    inputs = pred.features.get("ML_INPUTS", {})

    risk_factors = []
    protective_factors = []

    for feature, coef in zip(feature_cols, coefs):

        value = float(inputs.get(feature, 0))
        impacto = coef * value
        odds = round(math.exp(coef), 3)

        meta = CLINICAL_METADATA.get(feature, {})

        item = {
            "feature": meta.get("titulo", feature),
            "descripcion": meta.get("descripcion", ""),
            "ejemplo": meta.get("ejemplo", ""),
            "cuestionario": meta.get("cuestionario", ""),
            "value": round(value, 2),
            "odds": odds,
            "impacto": round(abs(impacto), 3),
        }

        if impacto > 0:
            item["direction"] = "up"
            item["interpretacion"] = "Contribuye al aumento del riesgo."
            risk_factors.append(item)

        elif impacto < 0:
            item["direction"] = "down"
            item["interpretacion"] = "Actúa como factor protector."
            protective_factors.append(item)

    # Ordenar por impacto real
    risk_factors.sort(key=lambda x: x["impacto"], reverse=True)
    protective_factors.sort(key=lambda x: x["impacto"], reverse=True)

    narrativa = generar_narrativa_clinica(
        pred.probabilidad,
        risk_factors,
        protective_factors
    )


    return {
        "probabilidad": round(pred.probabilidad * 100, 2),
        "nivel": calcular_nivel_riesgo(pred.probabilidad),
        "risk_factors": risk_factors[:3],
        "protective_factors": protective_factors[:3],
        "narrative": narrativa,
    }


# ===============================
# Nivel de riesgo
# ===============================

def calcular_nivel_riesgo(prob):

    if prob >= 0.75:
        return "ALTO"
    elif prob >= 0.45:
        return "MODERADO"
    else:
        return "BAJO"


# ===============================
# Narrativa clínica automática
# ===============================

def generar_narrativa_clinica(prob, risk_factors, protective_factors):

    nivel = calcular_nivel_riesgo(prob)

    texto = f"El modelo estima una probabilidad de riesgo {nivel.lower()} ({round(prob*100,2)}%). "

    if risk_factors:
        principales = ", ".join([f["feature"] for f in risk_factors[:2]])
        texto += f"Los principales factores asociados al aumento del riesgo son: {principales}. "

    if protective_factors:
        protectores = ", ".join([f["feature"] for f in protective_factors[:2]])
        texto += f"Como elementos protectores se identifican: {protectores}. "

    texto += "La interpretación debe integrarse con la valoración clínica profesional."

    return texto


# ===============================
# Recomendación automática
# ===============================





def generate_contrib_chart(shap_vals, features):
    contrib = pd.Series(shap_vals, index=features).sort_values(key=abs, ascending=False)
    fig, ax = plt.subplots(figsize=(8, 6))
    colors = ['#b91c1c' if v > 0 else '#065f46' for v in contrib.values]
    ax.barh(contrib.index, contrib.values, color=colors)
    ax.set_xlabel('Contribución SHAP al Riesgo')
    ax.set_title('Factores Influyentes en la Predicción')
    
    buf = BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return f"data:image/png;base64,{base64.b64encode(buf.read()).decode('utf-8')}"

# ============================================================
# 10) Resumen SIN ML por sesión (para psico_sesion_detalle)
# ============================================================

def score_summary_for_session(s: SesionEvaluacion) -> dict:
    """
    Devuelve dict para template:
      { titulo: str, items: [{label,value,fmt?}], debug?: {...} }
    """
    codigo = (s.cuestionario.codigo or "").strip().upper()

    # CASO-A30
    if codigo == "CASO-A30":
        ans = _get_answers_dict_by_prefix(s.id, "CASO_", 30)
        codes = [f"CASO_{i:02d}" for i in range(1, 31)]
        total = _sum_values(ans, codes)
        mean_ = (float(total) / 30.0) if total is not None else None
        return {
            "titulo": "Resultados del cuestionario (sin ML) — CASO-A30",
            "items": [
                {"label": "Suma de 30 ítems (1–5)", "value": total, "fmt": "float2"},
                {"label": "Promedio", "value": mean_, "fmt": "float2"},
                {"label": "Interpretación", "value": "Altas = buena asertividad. Bajas = pasividad o agresividad indirecta. Media teórica: 90."},
            ],
            "debug": {"n_respuestas_encontradas": len(ans)},
        }

    # PANAS
    if codigo == "PANAS":
        ans = _get_answers_dict_by_prefix(s.id, "PANAS_", 20)
        pos_codes = [f"PANAS_{i:02d}" for i in PANAS_POS_IDX]
        neg_codes = [f"PANAS_{i:02d}" for i in PANAS_NEG_IDX]
        pos_sum  = _sum_values(ans, pos_codes)
        neg_sum  = _sum_values(ans, neg_codes)
        pos_mean = _mean_values(ans, pos_codes)
        neg_mean = _mean_values(ans, neg_codes)
        return {
            "titulo": "Resultados del cuestionario (sin ML) — PANAS",
            "items": [
                {"label": "Afecto positivo (suma 10 ítems)", "value": pos_sum, "fmt": "float2"},
                {"label": "Afecto negativo (suma 10 ítems)", "value": neg_sum, "fmt": "float2"},
                {"label": "Afecto positivo (promedio)", "value": pos_mean, "fmt": "float2"},
                {"label": "Afecto negativo (promedio)", "value": neg_mean, "fmt": "float2"},
            ],
            "debug": {"n_respuestas_encontradas": len(ans)},
        }

    # WHO-QOL
    if codigo in {"WHO-QOL", "WHOQOL", "WHOQOL-BREF"}:
        raw = _get_answers_dict_by_prefix(s.id, "WHOQOL_", 26)

        scored = {}
        for i in range(1, 27):
            scored[i] = _whoqol_score_item(i, raw.get(f"WHOQOL_{i:02d}"))

        def mean_items(items):
            vals = [scored[i] for i in items if scored[i] is not None]
            return float(np.mean(vals)) if vals else None

        overall = mean_items([1, 2])
        phys    = mean_items(WHOQOL_PHYS)
        psych   = mean_items(WHOQOL_PSYCH)
        social  = mean_items(WHOQOL_SOCIAL)
        env     = mean_items(WHOQOL_ENV)
        total   = mean_items(list(range(1, 27)))

        return {
            "titulo": "Resultados del cuestionario (sin ML) — WHOQOL-BREF",
            "items": [
                {"label": "Overall (Q1–Q2) promedio", "value": overall, "fmt": "float2"},
                {"label": "Físico (dominio) promedio", "value": phys, "fmt": "float2"},
                {"label": "Psicológico (dominio) promedio", "value": psych, "fmt": "float2"},
                {"label": "Social (dominio) promedio", "value": social, "fmt": "float2"},
                {"label": "Ambiente (dominio) promedio", "value": env, "fmt": "float2"},
                {"label": "Total (26 ítems) promedio", "value": total, "fmt": "float2"},
                {"label": "Nota", "value": "Incluye reversa en ítems 3, 4 y 26 (6−x)."},
            ],
            "debug": {"n_respuestas_encontradas": len(raw)},
        }

    # Default
    return {
        "titulo": f"Resultados del cuestionario (sin ML) — {s.cuestionario.codigo}",
        "items": [{"label": "Nota", "value": "Este cuestionario aún no tiene resumen sin ML configurado aquí."}],
    }


# ============================================================
# 11) Debug helpers importables desde shell
# ============================================================

def _get_numeric_by_codes_debug(session_id: int, codes: list[str]) -> dict[str, float]:
    qs = (
        Respuesta.objects
        .select_related("pregunta", "opcion_seleccionada")
        .filter(sesion_id=session_id, pregunta__codigo__in=codes)
    )
    out = {}
    missing_code = 0
    missing_val = 0

    for r in qs:
        code = (getattr(r.pregunta, "codigo", "") or "").strip()
        if not code:
            missing_code += 1
            continue
        val = _value_from_respuesta(r)
        if val is None:
            missing_val += 1
            continue
        out[code] = float(val)

    print(f"[DEBUG] _get_numeric_by_codes_debug session={session_id}")
    print(f"        qs_count={qs.count()} out_count={len(out)} missing_code={missing_code} missing_val={missing_val}")
    if qs.exists():
        sample = list(qs.values("pregunta__codigo", "valor_numerico", "opcion_seleccionada__valor")[:10])
        print("[DEBUG] sample 10 rows:", sample)

    return out


def debug_ml_ready(perfil):
    got = list(
        SesionEvaluacion.objects
        .filter(estudiante=perfil, estado="COMPLETADA", cuestionario__codigo__in=REQUIRED_CODES)
        .values_list("cuestionario__codigo", flat=True)
        .distinct()
    )
    print("\n[DEBUG] REQUIRED_CODES =", REQUIRED_CODES)
    print("[DEBUG] completed distinct required =", got)
    print("[DEBUG] count:", len(got), "/", len(REQUIRED_CODES))
    return got


def debug_panas(perfil):
    s = _get_last_completed_session(perfil, "PANAS")
    print("\n[DEBUG] PANAS session:", s.id if s else None)
    if not s:
        return None
    codes = [f"PANAS_{i:02d}" for i in range(1, 21)]
    ans = _get_answers_dict_by_prefix(s.id, "PANAS_", 20)
    # muestra
    print("[DEBUG] PANAS answered:", len(ans), "/20")
    pos = [f"PANAS_{i:02d}" for i in PANAS_POS_IDX]
    neg = [f"PANAS_{i:02d}" for i in PANAS_NEG_IDX]
    pos_sum = _sum_values(ans, pos); neg_sum = _sum_values(ans, neg)
    pos_mean = _mean_values(ans, pos); neg_mean = _mean_values(ans, neg)
    print("[DEBUG] PANAS pos_sum:", pos_sum, "pos_mean:", pos_mean)
    print("[DEBUG] PANAS neg_sum:", neg_sum, "neg_mean:", neg_mean)
    return {
        "X_PANAS_Positivo": pos_mean,
        "X_PANAS_Negativo": neg_mean,
        "PANAS_POS_SUM": pos_sum,
        "PANAS_NEG_SUM": neg_sum,
        "PANAS_POS_MEAN": pos_mean,
        "PANAS_NEG_MEAN": neg_mean,
    }


def debug_whoqol(perfil, codigo="WHO-QOL"):
    s = _get_last_completed_session(perfil, codigo)
    print("\n[DEBUG] WHOQOL session:", s.id if s else None, "codigo usado:", codigo)
    if not s:
        return None
    raw = _get_answers_dict_by_prefix(s.id, "WHOQOL_", 26)
    print("[DEBUG] WHOQOL answered:", len(raw), "/26")
    keys = sorted(raw.keys())[:10]
    print("[DEBUG] WHOQOL first keys:", keys)
    print("[DEBUG] WHOQOL first vals:", [raw[k] for k in keys])
    return {"WHOQOL_N": len(raw)}


def debug_caso(perfil):
    s = _get_last_completed_session(perfil, "CASO-A30")
    print("\n[DEBUG] CASO session:", s.id if s else None)
    if not s:
        return None
    ans = _get_answers_dict_by_prefix(s.id, "CASO_", 30)
    codes = [f"CASO_{i:02d}" for i in range(1, 31)]
    total = _sum_values(ans, codes)
    mean = (float(total) / 30.0) if total is not None else None
    print("[DEBUG] CASO total:", total, "mean:", mean, "answered:", len(ans), "/30")
    return {"X_CASO_MEAN": mean, "CASO_TOTAL": total}


def debug_predict_and_save(perfil):
    bundle = _load_bundle()
    if not bundle:
        print("[STOP] No bundle.")
        return None

    # ready?
    got = debug_ml_ready(perfil)
    if len(got) < len(REQUIRED_CODES):
        print("[STOP] No está listo (faltan cuestionarios).")
        return None

    # intenta predicción real
    obj = actualizar_prediccion_estudiante(perfil)
    print("[OK] Predicción guardada ->", obj.nivel, obj.probabilidad)
    return obj




