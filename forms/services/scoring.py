# forms/services/scoring.py
from __future__ import annotations
from typing import Tuple, Dict, Any
from django.db.models import Prefetch
from forms.models import SesionEvaluacion, Respuesta, Pregunta, Opcion



def _apply_scoring_scheme(cuestionario, total, total_min, total_max, contados):
    cfg = (getattr(cuestionario, "config", None) or {}).get("scoring", {}) or {}
    mode = (cfg.get("mode") or "SUM").upper()
    bands = cfg.get("bands") or []

    avg = (total / contados) if contados > 0 else 0.0

    span = (total_max - total_min)
    if span > 0:
        norm_0_100 = ((total - total_min) / span) * 100.0
    else:
        norm_0_100 = 0.0

    value_for_bands = total if mode == "SUM" else avg
    label = None
    for b in bands:
        bmin = float(b.get("min", float("-inf")))
        bmax = float(b.get("max", float("inf")))
        if bmin <= value_for_bands <= bmax:
            label = b.get("label") or b.get("nombre") or b.get("texto")
            break

    return {
        "mode": mode,
        "total": float(total),
        "avg": float(avg),
        "norm_0_100": float(norm_0_100),
        "label": label,
    }



def _clamp(v: float, mn: float, mx: float) -> float:
    return max(mn, min(mx, v))

def _apply_reverse_if_needed(valor: float, mn: float, mx: float, reverse: bool) -> float:
    if not reverse:
        return valor
    # inversión estándar en escalas: max + min - valor
    return (mx + mn) - valor

def _infer_var_code(cuestionario, pregunta) -> str:
    """
    Fallback si no existe config['var']:
      <CUESTIONARIO_CODIGO>_<ORDEN 2D>  (ej. PANAS_01)
    """
    codigo = getattr(cuestionario, "codigo", "Q").strip().upper() or "Q"
    orden = getattr(pregunta, "orden", None) or 0
    try:
        orden_i = int(orden)
    except Exception:
        orden_i = 0
    return f"{codigo}_{orden_i:02d}"


from typing import Tuple, Dict
import math

def compute_auto_sum_for_session(sesion: SesionEvaluacion) -> Tuple[float, Dict]:
    """
    Suma automática SOLO:
      - ESCALA (Likert): usa pregunta.config[min,max] (por defecto 1..5), valor_numerico.
      - SI_NO: SI=1, NO=0 (o min/max si existen); usa valor_texto.
    Aplica reverse si pregunta.config['reverse'] == True.

    Retorna: (score_final_para_guardar_en_total, breakdown)
    """
    cuestionario = sesion.cuestionario
    preguntas = cuestionario.preguntas.order_by("orden", "id").all()

    rs = sesion.respuestas.select_related("pregunta").all()
    resp_by_qid = {r.pregunta_id: r for r in rs}

    total = 0.0
    total_min_contado = 0.0
    total_max_contado = 0.0
    por_pregunta: Dict[int, Dict[str, Any]] = {}

    items_sumables = 0
    contados = 0

    for p in preguntas:
        tipo = (p.tipo_respuesta or "").upper()
        if tipo not in ("ESCALA", "SI_NO"):
            continue

        items_sumables += 1

        cfg = p.config or {}
        reverse = bool(cfg.get("reverse", False))

        pmin = float(cfg.get("min", 0 if tipo == "SI_NO" else 1))
        pmax = float(cfg.get("max", 1 if tipo == "SI_NO" else 5))

        # Identificador estable (opción B)
        var_code = cfg.get("var") or _infer_var_code(cuestionario, p)

        r = resp_by_qid.get(p.id)

        valor_raw = None   # valor antes de reverse
        valor = None       # valor final (posible invertido)

        if r:
            if tipo == "ESCALA":
                v = r.valor_numerico
                if v is not None and not (isinstance(v, float) and math.isnan(v)):
                    try:
                        vv = float(v)
                        vv = _clamp(vv, pmin, pmax)
                        valor_raw = vv
                    except Exception:
                        valor_raw = None

            else:  # SI_NO
                t = (r.valor_texto or "").strip().upper()
                if t in ("SI", "SÍ"):
                    base = 1.0
                elif t == "NO":
                    base = 0.0
                else:
                    base = None

                if base is not None:
                    # si rango es 0..1, usamos base directo; si no, mapeamos a min/max
                    valor_raw = base if (pmin, pmax) == (0.0, 1.0) else (pmax if base == 1.0 else pmin)

        if valor_raw is not None:
            # aplicar reverse
            valor = _apply_reverse_if_needed(valor_raw, pmin, pmax, reverse)
            # seguridad: clamp tras invertir por si algo raro llegó
            valor = _clamp(float(valor), pmin, pmax)

            contados += 1
            total += float(valor)
            total_min_contado += pmin
            total_max_contado += pmax

        por_pregunta[p.id] = {
            "var": var_code,
            "orden": getattr(p, "orden", None),
            "tipo": tipo,
            "min": pmin,
            "max": pmax,
            "reverse": reverse,
            "valor_raw": valor_raw,
            "valor": valor,
            "texto": (getattr(p, "texto", "") or "")[:180],
        }

    scheme = _apply_scoring_scheme(
        cuestionario=cuestionario,
        total=total,
        total_min=total_min_contado,
        total_max=total_max_contado,
        contados=contados,
    )

    total_to_store = scheme["total"] if scheme["mode"] == "SUM" else scheme["avg"]

    breakdown = {
        "cuestionario_id": cuestionario.id,
        "sesion_id": sesion.id,
        "items_sumables": items_sumables,
        "contados": contados,
        "total_min": total_min_contado,
        "total_max": total_max_contado,
        "por_pregunta": por_pregunta,
        "scheme": scheme,
        "nota": "Solo ESCALA (Likert) y SI/NO. reverse aplica: (max+min-valor). norm_0_100 se calcula sobre ítems contados.",
    }

    return float(total_to_store), breakdown



def compute_score_for_session(*args, **kwargs):
    return compute_auto_sum_for_session(*args, **kwargs)

