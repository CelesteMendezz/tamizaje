# resultados/admin.py
from django.contrib import admin
from .models import PrediccionRiesgo

@admin.register(PrediccionRiesgo)
class PrediccionRiesgoAdmin(admin.ModelAdmin):
    list_display = ('estudiante', 'nivel', 'probabilidad', 'modelo_version', 'actualizado')
    search_fields = ('estudiante__usuario__username', 'estudiante__usuario__first_name', 'estudiante__usuario__last_name')
