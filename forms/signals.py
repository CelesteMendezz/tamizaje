# forms/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import Usuario, Perfil, ReporteEvaluacion, SesionEvaluacion

@receiver(post_save, sender=ReporteEvaluacion)
def sellar_sesion_al_generar_reporte(sender, instance, created, **kwargs):
    """
    Si se crea/actualiza un reporte, asegúrate de que la sesion quede 'COMPLETADA'
    y con fecha_fin marcada.
    """
    s = instance.sesion
    changed = False
    if s.estado != 'COMPLETADA':
        s.estado = 'COMPLETADA'
        changed = True
    if not s.fecha_fin:
        s.fecha_fin = timezone.now()
        changed = True
    if changed:
        s.save(update_fields=['estado', 'fecha_fin'])
