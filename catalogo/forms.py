# catalogo/forms.py
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from django import forms
from django.core.exceptions import ValidationError
from django.forms import ModelForm, inlineformset_factory, BaseInlineFormSet

from forms.models import Cuestionario, Pregunta, Opcion


def _model_has_field(model_cls, field_name: str) -> bool:
    return any(f.name == field_name for f in model_cls._meta.get_fields())


# =========================
# Cuestionario
# =========================
class CuestionarioForm(ModelForm):
    """
    - Guarda Cuestionario.config (JSON) si existe
    - Guarda fecha_publicacion si existe
    """

    # Solo lo mostramos si el modelo lo tiene
    fecha_publicacion = forms.DateTimeField(required=False)
    config = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": '{ "scoring": { ... } }'}),
        help_text="JSON opcional (se guarda en Cuestionario.config)."
    )

    class Meta:
        model = Cuestionario
        # tu modelo SI tiene algoritmo
        fields = ["codigo", "nombre", "descripcion", "version", "activo", "estado", "algoritmo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Widgets simples
        self.fields["codigo"].widget = forms.TextInput(attrs={"class": "form-control"})
        self.fields["nombre"].widget = forms.TextInput(attrs={"class": "form-control"})
        self.fields["descripcion"].widget = forms.Textarea(attrs={"class": "form-control", "rows": 3})
        self.fields["version"].widget = forms.TextInput(attrs={"class": "form-control"})
        self.fields["estado"].widget = forms.Select(attrs={"class": "form-select"})
        self.fields["algoritmo"].widget = forms.Select(attrs={"class": "form-select"})

        # Normaliza extra fields según modelo real
        if not _model_has_field(Cuestionario, "fecha_publicacion"):
            self.fields.pop("fecha_publicacion", None)
        else:
            self.fields["fecha_publicacion"].widget = forms.DateTimeInput(attrs={"type": "datetime-local"})

        if not _model_has_field(Cuestionario, "config"):
            self.fields.pop("config", None)
        else:
            cfg = getattr(self.instance, "config", None)
            if isinstance(cfg, dict) and cfg:
                self.initial["config"] = json.dumps(cfg, ensure_ascii=False, indent=2)

    def clean_codigo(self):
        codigo = (self.cleaned_data.get("codigo") or "").strip().upper()
        if not codigo:
            raise forms.ValidationError("El código es obligatorio.")
        return codigo

    def clean_config(self):
        if "config" not in self.fields:
            return None
        raw = (self.cleaned_data.get("config") or "").strip()
        if raw == "":
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise forms.ValidationError(f"Config debe ser JSON válido. Error: {e}")
        if not isinstance(data, dict):
            raise forms.ValidationError("Config debe ser un objeto JSON (dict).")
        return data
    

def save(self, commit=True):
        obj = super().save(commit=False)

        if _model_has_field(Cuestionario, "config") and "config" in self.cleaned_data:
            obj.config = self.cleaned_data["config"] or {}

        if _model_has_field(Cuestionario, "fecha_publicacion") and "fecha_publicacion" in self.cleaned_data:
            fp = self.cleaned_data.get("fecha_publicacion")
            if fp:
                obj.fecha_publicacion = fp

        if commit:
            obj.save()
        return obj


# =========================
# Pregunta
# =========================
class PreguntaForm(ModelForm):
    # ✅ Ahora config es el hidden “source of truth” para JS
    config = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        help_text="JSON opcional (min/max/labels/reverse/subscale/var). Se guarda en Pregunta.config."
    )

    class Meta:
        model = Pregunta
        fields = ["texto", "orden", "tipo_respuesta", "requerido", "ayuda", "config"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Widgets/estilos (los tuyos)
        self.fields["texto"].widget = forms.Textarea(attrs={"rows": 2, "class": "form-control"})
        self.fields["orden"].widget = forms.NumberInput(attrs={"min": 1, "class": "form-control"})
        self.fields["tipo_respuesta"].choices = Pregunta.Tipos.choices
        self.fields["tipo_respuesta"].widget.attrs.update({"class": "form-select"})
        self.fields["ayuda"].widget = forms.TextInput(attrs={"class": "form-control"})
        self.fields["requerido"].widget = forms.CheckboxInput(attrs={"class": "form-check-input"})

        # Si tu modelo no tiene config, removemos el campo para evitar errores
        if not _model_has_field(Pregunta, "config"):
            self.fields.pop("config", None)
            return

        # ✅ Asegurar que el hidden config SIEMPRE sea JSON válido (string)
        cfg = getattr(self.instance, "config", None) or {}
        if isinstance(cfg, dict):
            self.initial["config"] = json.dumps(cfg, ensure_ascii=False)
        else:
            # por seguridad: si viene basura o string raro, intenta rescatarlo
            try:
                _ = json.loads(cfg) if isinstance(cfg, str) and cfg.strip() else {}
                self.initial["config"] = cfg
            except Exception:
                self.initial["config"] = json.dumps({}, ensure_ascii=False)

    def clean_config(self):
        if "config" not in self.fields:
            return None

        raw = (self.cleaned_data.get("config") or "").strip()
        if raw == "":
            return {}

        # ✅ Debe ser JSON válido
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise forms.ValidationError(f"Config debe ser JSON válido. Error: {e}")

        if not isinstance(data, dict):
            raise forms.ValidationError("Config debe ser un objeto JSON (dict).")

        tipo = (self.cleaned_data.get("tipo_respuesta") or "").upper()

        def _to_float(key: str) -> Optional[float]:
            v = data.get(key, None)
            if v in (None, ""):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                raise forms.ValidationError(f"'{key}' en config debe ser numérico.")

        # ✅ Validación para escalas / numéricas
        if tipo in ("NUMERICA", "ESCALA"):
            mn = _to_float("min")
            mx = _to_float("max")
            st = _to_float("step")

            if mn is not None and mx is not None and mn > mx:
                raise forms.ValidationError("En config: min no puede ser mayor que max.")
            if st is not None and st <= 0:
                raise forms.ValidationError("En config: step debe ser > 0.")

            labels = data.get("labels", None)
            # ideal: dict; permitimos str si quieres pegar texto, pero lo más limpio es dict
            if labels is not None and not isinstance(labels, (dict, str)):
                raise forms.ValidationError("En config: labels debe ser un objeto JSON (dict) o texto.")

            if "reverse" in data and not isinstance(data["reverse"], bool):
                raise forms.ValidationError("En config: reverse debe ser true/false.")

            if "subscale" in data and data["subscale"] is not None and not isinstance(data["subscale"], str):
                raise forms.ValidationError("En config: subscale debe ser texto.")

            # ✅ NUEVO: var (para ML) si existe debe ser string
            if "var" in data and data["var"] is not None and not isinstance(data["var"], str):
                raise forms.ValidationError("En config: var debe ser texto.")
            # opcional: no permitir vacío si viene
            if isinstance(data.get("var"), str) and data["var"].strip() == "":
                # si viene pero vacío, lo eliminamos para no guardar basura
                data.pop("var", None)

        return data

    def save(self, commit=True):
        obj = super().save(commit=False)

        if _model_has_field(Pregunta, "config") and "config" in self.cleaned_data:
            obj.config = self.cleaned_data["config"] or {}

        if commit:
            obj.save()
        return obj



class BasePreguntaFormSet(BaseInlineFormSet):
    """Evita llegar al IntegrityError por orden duplicado (mensaje amigable)."""
    def clean(self):
        super().clean()
        seen = set()
        duplicates = set()

        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue

            orden = form.cleaned_data.get("orden")
            if not orden:
                continue

            if orden in seen:
                duplicates.add(orden)
            seen.add(orden)

        if duplicates:
            dup = ", ".join(str(x) for x in sorted(duplicates))
            raise ValidationError(f"Hay órdenes repetidos en preguntas: {dup}. Cada pregunta debe tener un orden único.")


PreguntaFormSet = inlineformset_factory(
    Cuestionario, Pregunta,
    form=PreguntaForm,
    formset=BasePreguntaFormSet,
    extra=0,
    can_delete=True
)


# =========================
# Opciones
# =========================
class OpcionForm(forms.ModelForm):
    class Meta:
        model = Opcion
        fields = ['orden', 'texto', 'valor', 'es_otro', 'activo']
        widgets = {
            'orden': forms.NumberInput(attrs={'class': 'form-control'}),
            'texto': forms.TextInput(attrs={'class': 'form-control'}),
            'valor': forms.TextInput(attrs={'class': 'form-control'}),
            'es_otro': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


OpcionFormSet = inlineformset_factory(
    Pregunta, Opcion,
    form=OpcionForm,
    extra=0,
    can_delete=True
)


# =========================
# Import JSON (archivo)
# =========================
class ImportJSONForm(forms.Form):
    archivo = forms.FileField(label="Archivo JSON")

from .models import EncuestaSociodemografica

class EncuestaSociodemograficaForm(forms.ModelForm):
    class Meta:
        model = EncuestaSociodemografica
        fields = [
            "municipio","edad","sexo",
            "tiene_pareja","tiempo_relacion_meses","tipo_relacion","tipo_relacion_otro",
            "tiene_hijos","cuantos_hijos",
            "vive_semana","vive_semana_otro",
            "vive_fin","vive_fin_otro",
            "estado_civil_padres","estado_civil_padres_otro",
            "escolaridad_padre","escolaridad_madre",
            "ocupacion_padre","ocupacion_madre",
            "trabaja_actualmente","depende_de",
            "padece_enfermedad","correo_opcional"
        ]
        widgets = {
            "municipio": forms.TextInput(attrs={"maxlength":"120"}),
            "edad": forms.NumberInput(attrs={"min":"10","max":"99"}),
            "tiempo_relacion_meses": forms.NumberInput(attrs={"min":"0","max":"600"}),
            "cuantos_hijos": forms.NumberInput(attrs={"min":"0","max":"20"}),
        }

    def clean(self):
        c = super().clean()

        # Pareja: si NO, limpiar campos relacionados
        if c.get("tiene_pareja") == "NO":
            c["tiempo_relacion_meses"] = None
            c["tipo_relacion"] = None
            c["tipo_relacion_otro"] = ""
        else:
            # si SI, tiempo_relacion debería venir
            if c.get("tiempo_relacion_meses") is None:
                self.add_error("tiempo_relacion_meses", "Indica el tiempo (en meses).")

        # Tipo relación OTRO -> requiere texto
        if c.get("tipo_relacion") == "OTRO" and not (c.get("tipo_relacion_otro") or "").strip():
            self.add_error("tipo_relacion_otro", "Especifica el tipo de relación.")

        # Hijos: si NO -> limpiar cuantos
        if c.get("tiene_hijos") == "NO":
            c["cuantos_hijos"] = None
        else:
            if c.get("cuantos_hijos") is None:
                self.add_error("cuantos_hijos", "Indica cuántos hijos tienes.")

        # Vive con OTRO -> requiere texto
        for campo, otro in [("vive_semana","vive_semana_otro"), ("vive_fin","vive_fin_otro")]:
            if c.get(campo) == "OTRO" and not (c.get(otro) or "").strip():
                self.add_error(otro, "Especifica 'otro'.")

        # Estado civil padres OTRO -> requiere texto
        if c.get("estado_civil_padres") == "OTRO" and not (c.get("estado_civil_padres_otro") or "").strip():
            self.add_error("estado_civil_padres_otro", "Especifica 'otro'.")

        # Trabaja: si SI -> depende_de no aplica
        if c.get("trabaja_actualmente") == "SI":
            c["depende_de"] = ""
        else:
            if not (c.get("depende_de") or "").strip():
                self.add_error("depende_de", "Indica de quién dependes económicamente.")

        return c