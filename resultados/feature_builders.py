from forms.models import Respuesta, SesionEvaluacion

def get_numeric_answers_by_code(sesion: SesionEvaluacion) -> dict:
    """
    Retorna dict: { "PANAS_01": 4.0, "PANAS_02": 2.0, ... }
    Toma valor_numerico y si no existe intenta opcion_seleccionada.valor.
    """
    out = {}
    qs = (
        Respuesta.objects
        .filter(sesion=sesion)
        .select_related("pregunta", "opcion_seleccionada")
    )

    for r in qs:
        code = (getattr(r.pregunta, "codigo", "") or "").strip()
        if not code:
            continue

        val = None
        if r.valor_numerico is not None:
            val = float(r.valor_numerico)
        elif r.opcion_seleccionada and r.opcion_seleccionada.valor is not None:
            try:
                val = float(r.opcion_seleccionada.valor)
            except Exception:
                val = None

        if val is None:
            continue

        out[code] = val

    return out


import numpy as np

# AJUSTA SI TU LISTA REAL ES OTRA
PANAS_POS_ITEMS = [1,3,5,9,10,12,14,16,17,19]
PANAS_NEG_ITEMS = [2,4,6,7,8,11,13,15,18,20]

def calc_panas_features(ans: dict) -> dict:
    """
    ans: {"PANAS_01":1.0, "PANAS_02":2.0, ...}  (1..5)
    Devuelve:
      - Para mostrar: PANAS_POS_MEAN, PANAS_NEG_MEAN, PANAS_POS_SUM, PANAS_NEG_SUM
      - Para ML (nombres EXACTOS): X_PANAS_Positivo, X_PANAS_Negativo
    """
    # Extrae valores por lista de ítems
    pos_vals = [ans.get(f"PANAS_{i:02d}") for i in PANAS_POS_ITEMS]
    neg_vals = [ans.get(f"PANAS_{i:02d}") for i in PANAS_NEG_ITEMS]

    # Limpia Nones
    pos_vals = [float(v) for v in pos_vals if v is not None]
    neg_vals = [float(v) for v in neg_vals if v is not None]

    # Métricas
    pos_sum  = float(np.sum(pos_vals)) if pos_vals else None
    neg_sum  = float(np.sum(neg_vals)) if neg_vals else None
    pos_mean = float(np.mean(pos_vals)) if pos_vals else None
    neg_mean = float(np.mean(neg_vals)) if neg_vals else None

    return {
        # Para mostrar (sin ML):
        "PANAS_POS_SUM": pos_sum,
        "PANAS_NEG_SUM": neg_sum,
        "PANAS_POS_MEAN": pos_mean,
        "PANAS_NEG_MEAN": neg_mean,

        # Para ML (EXACTO como entrenaste):
        "X_PANAS_Positivo": pos_mean,
        "X_PANAS_Negativo": neg_mean,

        # Debug útil:
        "PANAS_POS_N": len(pos_vals),
        "PANAS_NEG_N": len(neg_vals),
    }



import numpy as np

def calc_caso_features(ans: dict) -> dict:
    vals = [ans.get(f"CASO_{i:02d}") for i in range(1, 31)]
    vals = [v for v in vals if v is not None]

    if not vals:
        return {"CASO_TOTAL": None, "X_CASO_MEAN": None}

    total = float(np.sum(vals))             # 30..150
    mean  = float(np.mean(vals))            # 1..5

    return {
        # Para mostrar:
        "CASO_TOTAL": total,
        "CASO_MEDIA_TEO": 90.0,
        "CASO_INTERP": (
            "Altas = buena asertividad. Bajas = pasividad o agresividad indirecta."
        ),

        # Para ML:
        "X_CASO_MEAN": mean,
    }

def build_panas_summary_for_session(sesion: SesionEvaluacion) -> dict:
    """
    Resumen SIN ML para mostrar en psico_sesion_detalle:
    - Sumas y promedios de subescalas
    - “semáforo” básico si quieres (opcional)
    """
    ans = get_numeric_answers_by_code(sesion)
    feats = calc_panas_features(ans)

    # Reglas mínimas de completitud (si quieres exigir 10/10 por subescala)
    pos_ok = feats.get("PANAS_POS_N", 0) >= 10
    neg_ok = feats.get("PANAS_NEG_N", 0) >= 10

    nota_comp = None
    if not (pos_ok and neg_ok):
        nota_comp = f"Respuestas incompletas: POS {feats.get('PANAS_POS_N',0)}/10, NEG {feats.get('PANAS_NEG_N',0)}/10."

    return {
        "titulo": "Resultados del cuestionario (sin ML) — PANAS",
        "items": [
            {"label": "Afecto Positivo (suma 10 ítems)", "value": feats.get("PANAS_POS_SUM"), "fmt": "float2"},
            {"label": "Afecto Positivo (promedio 1–5)", "value": feats.get("PANAS_POS_MEAN"), "fmt": "float2"},
            {"label": "Afecto Negativo (suma 10 ítems)", "value": feats.get("PANAS_NEG_SUM"), "fmt": "float2"},
            {"label": "Afecto Negativo (promedio 1–5)", "value": feats.get("PANAS_NEG_MEAN"), "fmt": "float2"},
            {"label": "Interpretación", "value": "Mayor afecto positivo suele asociarse a mayor energía/entusiasmo; mayor afecto negativo a mayor malestar/estrés."},
        ],
        "debug": {
            "pos_n": feats.get("PANAS_POS_N", 0),
            "neg_n": feats.get("PANAS_NEG_N", 0),
            "nota": nota_comp,
        }
    }

import numpy as np

WHOQOL_PHYS   = [3,4,10,15,16,17,18]
WHOQOL_PSYCH  = [5,6,7,11,19,26]
WHOQOL_SOCIAL = [20,21,22]
WHOQOL_ENV    = [8,9,12,13,14,23,24,25]
REVERSE_ITEMS = {3,4,26}  # 1..5 -> 6-x

def _mean(vals):
    vals = [v for v in vals if v is not None]
    return float(np.mean(vals)) if vals else None

def calc_whoqol_features(ans: dict) -> dict:
    """
    ans debe traer WHOQOL_01 ... WHOQOL_26 (valores 1..5)
    """
    scored = {}

    # 1) construir 1..26 con recodificación inversa
    for i in range(1, 27):
        key = f"WHOQOL_{i:02d}"
        v = ans.get(key, None)

        if v is None or v == "":
            scored[i] = None
            continue

        try:
            v = float(v)
        except Exception:
            scored[i] = None
            continue

        # rango estricto
        if v < 1 or v > 5:
            scored[i] = None
            continue

        if i in REVERSE_ITEMS:
            v = 6.0 - v

        scored[i] = v

    # 2) overall (Q1,Q2) - NO es dominio
    overall = _mean([scored[1], scored[2]])

    # 3) dominios
    phys  = _mean([scored[i] for i in WHOQOL_PHYS])
    psych = _mean([scored[i] for i in WHOQOL_PSYCH])
    soc   = _mean([scored[i] for i in WHOQOL_SOCIAL])
    env   = _mean([scored[i] for i in WHOQOL_ENV])

    # 4) total mean (26 ítems, opcional para auditoría)
    total = _mean([scored[i] for i in range(1, 27)])

    # 5) conteo respondidas (auditoría)
    n_resp = sum(1 for i in range(1, 27) if scored[i] is not None)

    return {
        # ===== Para mostrar en dashboard =====
        "WHOQOL_OVERALL_MEAN": overall,
        "WHOQOL_PHYS_MEAN": phys,
        "WHOQOL_PSYCH_MEAN": psych,
        "WHOQOL_SOCIAL_MEAN": soc,
        "WHOQOL_ENV_MEAN": env,
        "WHOQOL_TOTAL_MEAN": total,
        "WHOQOL_N_RESP": n_resp,

        # ===== Para ML (nombres EXACTOS del entrenamiento) =====
        # (Incluye las que uses en feature_cols)
        "X_WHOQOL_OVERALL_MEAN": overall,   # si algún día la usas
        "X_WHOQOL_PHYS_MEAN": phys,
        "X_WHOQOL_PSYCH_MEAN": psych,
        "X_WHOQOL_SOCIAL_MEAN": soc,
        "X_WHOQOL_ENV_MEAN": env,           # si algún día la usas
        "X_WHOQOL_TOTAL_MEAN": total,       # si algún día la usas
    }
