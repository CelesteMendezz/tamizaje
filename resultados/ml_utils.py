import joblib
import numpy as np
import pandas as pd
from functools import lru_cache
from django.conf import settings
from pathlib import Path
from sklearn.pipeline import Pipeline


# ==========================================================
# Variables clínicas entendibles para psicólogos
# ==========================================================

CLINICAL_METADATA = {

    # =========================
    # PANAS
    # =========================

    "X_PANAS_Negativo": {
        "titulo": "Afecto Negativo Elevado",
        "descripcion": (
            "Frecuencia de emociones displacenteras como tristeza, ansiedad, "
            "culpa, irritabilidad o temor durante las últimas semanas."
        ),
        "cuestionario": "PANAS",
        "interpretacion_alta": (
            "Puntuaciones elevadas indican presencia significativa de malestar emocional."
        ),
        "interpretacion_baja": (
            "Bajos niveles de afecto negativo sugieren estabilidad emocional."
        ),
        "ejemplo_items": [
            "Afligido/a",
            "Nervioso/a",
            "Irritable",
            "Temeroso/a"
        ],
        "tipo": "riesgo"
    },

    "X_PANAS_Positivo": {
        "titulo": "Afecto Positivo",
        "descripcion": (
            "Presencia de emociones positivas como entusiasmo, energía, "
            "motivación y capacidad de concentración."
        ),
        "cuestionario": "PANAS",
        "interpretacion_alta": (
            "Puntuaciones altas reflejan recursos emocionales y resiliencia."
        ),
        "interpretacion_baja": (
            "Bajos niveles pueden asociarse con apatía o disminución de energía."
        ),
        "ejemplo_items": [
            "Motivado/a",
            "Entusiasta",
            "Activo/a",
            "Inspirado/a"
        ],
        "tipo": "protector"
    },

    # =========================
    # WHOQOL
    # =========================

    "X_WHOQOL_PSYCH_MEAN": {
        "titulo": "Bienestar Psicológico Percibido",
        "descripcion": (
            "Evaluación subjetiva de autoestima, sentido de vida, "
            "concentración y frecuencia de pensamientos negativos."
        ),
        "cuestionario": "WHOQOL-BREF",
        "interpretacion_alta": (
            "Indica adecuada percepción de estabilidad emocional y satisfacción personal."
        ),
        "interpretacion_baja": (
            "Puede reflejar insatisfacción personal o presencia de pensamientos negativos frecuentes."
        ),
        "ejemplo_items": [
            "¿Cuánto disfruta de la vida?",
            "¿Con qué frecuencia experimenta sentimientos negativos?"
        ],
        "tipo": "protector"
    },

    "X_WHOQOL_PHYS_MEAN": {
        "titulo": "Salud Física Percibida",
        "descripcion": (
            "Nivel percibido de energía, calidad del sueño, "
            "movilidad y capacidad para realizar actividades diarias."
        ),
        "cuestionario": "WHOQOL-BREF",
        "interpretacion_alta": (
            "Indica buena percepción de salud y funcionamiento físico."
        ),
        "interpretacion_baja": (
            "Puede asociarse con fatiga, alteraciones del sueño o limitaciones funcionales."
        ),
        "ejemplo_items": [
            "¿Tiene suficiente energía para la vida diaria?",
            "¿Está satisfecho con su capacidad para trabajar?"
        ],
        "tipo": "protector"
    },

    "X_WHOQOL_SOCIAL_MEAN": {
        "titulo": "Apoyo y Relaciones Sociales",
        "descripcion": (
            "Percepción de apoyo social, satisfacción con relaciones personales "
            "y disponibilidad de ayuda."
        ),
        "cuestionario": "WHOQOL-BREF",
        "interpretacion_alta": (
            "Refleja red de apoyo funcional y relaciones satisfactorias."
        ),
        "interpretacion_baja": (
            "Puede indicar aislamiento social o insatisfacción relacional."
        ),
        "ejemplo_items": [
            "¿Está satisfecho con sus relaciones personales?",
            "¿Cuenta con apoyo de amigos o familiares?"
        ],
        "tipo": "protector"
    },

    # =========================
    # CASO
    # =========================

    "X_CASO_MEAN": {
        "titulo": "Carga Global de Malestar Psicológico",
        "descripcion": (
            "Indicador sintético de síntomas emocionales y conductuales "
            "asociados a vulnerabilidad psicológica."
        ),
        "cuestionario": "CASO",
        "interpretacion_alta": (
            "Sugiere presencia significativa de síntomas emocionales."
        ),
        "interpretacion_baja": (
            "Indica baja presencia de sintomatología clínica."
        ),
        "ejemplo_items": [
            "Síntomas persistentes de ansiedad o tristeza",
            "Dificultad para regular emociones"
        ],
        "tipo": "riesgo"
    }
}

