# forms/admin.py
from django.contrib import admin
from django.utils import timezone
from .models import (
    Cuestionario, Pregunta, Opcion,
    SesionEvaluacion, Respuesta, ReporteEvaluacion,
    Usuario, Perfil
)

class OpcionInline(admin.TabularInline):
    model = Opcion
    extra = 1

class PreguntaInline(admin.StackedInline):
    model = Pregunta
    extra = 1
    show_change_link = True

@admin.register(Cuestionario)
class CuestionarioAdmin(admin.ModelAdmin):
    # usamos métodos (callables) para saltarnos admin.E108 aunque hubiera rarezas
    list_display  = (
        'codigo_display','nombre_display','version_display',
        'estado','algoritmo','activo_display','fecha_publicacion_display'
    )
    list_filter   = ('estado','algoritmo')  # quitamos 'activo' para evitar admin.E116
    search_fields = ('codigo','nombre')
    inlines       = [PreguntaInline]
    actions       = ['publicar','clonar']

    # --- columnas como métodos seguros ---
    def codigo_display(self, obj): return getattr(obj, 'codigo', '')
    codigo_display.short_description = "Código"

    def nombre_display(self, obj): return getattr(obj, 'nombre', '')
    nombre_display.short_description = "Nombre"

    def version_display(self, obj): return getattr(obj, 'version', '')
    version_display.short_description = "Versión"

    def activo_display(self, obj):
        return bool(getattr(obj, 'activo', False))
    activo_display.boolean = True
    activo_display.short_description = "Activo"

    def fecha_publicacion_display(self, obj):
        return getattr(obj, 'fecha_publicacion', None)
    fecha_publicacion_display.short_description = "Publicación"

    # --- acciones ---
    def publicar(self, request, queryset):
        queryset.update(estado='published', fecha_publicacion=timezone.now())
    publicar.short_description = "Publicar cuestionario(s)"

import re
from django.utils import timezone
from django.db import transaction

def _next_version_str(v: str) -> str:
    """
    Genera una versión nueva de forma robusta:
    - Si es '1' -> '2'
    - Si es '1.0' -> '2.0'
    - Si es '2.3' -> '3.0' (sube major y resetea minor)
    - Si es 'v1' o raro -> 'v1-copy1' (fallback)
    """
    s = str(v or "").strip()

    # Caso numérico entero: "1"
    if re.fullmatch(r"\d+", s):
        return str(int(s) + 1)

    # Caso decimal tipo "1.0" o "2.3"
    m = re.fullmatch(r"(\d+)\.(\d+)", s)
    if m:
        major = int(m.group(1))
        return f"{major + 1}.0"

    # Fallback: agrega sufijo copy incremental
    base = s if s else "1.0"
    return f"{base}-copy1"


def _get_free_version(codigo: str, proposed_version: str) -> str:
    """
    Si (codigo, version) ya existe, incrementa un sufijo -copyN hasta encontrar libre.
    """
    v = proposed_version
    if not Cuestionario.objects.filter(codigo=codigo, version=v).exists():
        return v

    # Si ya existe, intenta -copy2, -copy3, ...
    i = 2
    while True:
        cand = f"{proposed_version}-copy{i}"
        if not Cuestionario.objects.filter(codigo=codigo, version=cand).exists():
            return cand
        i += 1


@transaction.atomic
def clonar(self, request, queryset):
    for q in queryset:
        proposed = _next_version_str(q.version)
        next_v = _get_free_version(q.codigo, proposed)

        nuevo = Cuestionario.objects.create(
            codigo=q.codigo,
            nombre=q.nombre,
            descripcion=q.descripcion,
            version=next_v,
            activo=True,
            estado='draft',
            algoritmo=q.algoritmo,
            config=q.config,  # importante: copiar config si lo usas
            auto_sumar_likert=getattr(q, "auto_sumar_likert", True),
            fecha_publicacion=None,
        )

        # Clonar preguntas+opciones
        for p in q.preguntas.all().prefetch_related("opciones"):
            p2 = Pregunta.objects.create(
                cuestionario=nuevo,
                texto=p.texto,
                tipo_respuesta=p.tipo_respuesta,
                orden=p.orden,
                codigo=p.codigo,
                requerido=p.requerido,
                ayuda=p.ayuda,
                config=p.config,
            )
            for o in p.opciones.all():
                Opcion.objects.create(
                    pregunta=p2,
                    texto=o.texto,
                    valor=o.valor,
                    orden=o.orden,
                    es_otro=o.es_otro,
                    activo=o.activo,
                )

clonar.short_description = "Clonar a nueva versión (draft)"


@admin.register(Pregunta)
class PreguntaAdmin(admin.ModelAdmin):
    list_display = ('cuestionario','orden','codigo','tipo_respuesta')
    search_fields = ('texto','codigo')
    inlines = [OpcionInline]

admin.site.register(Opcion)
admin.site.register(SesionEvaluacion)
admin.site.register(Respuesta)
admin.site.register(ReporteEvaluacion)
admin.site.register(Usuario)
admin.site.register(Perfil)
