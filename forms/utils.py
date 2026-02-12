# forms/utils.py
from django.db.models import Count, Q
from django.utils import timezone
from .models import Perfil, SesionEvaluacion

def pick_psicologo_round_robin():
    return (Perfil.objects
        .filter(rol='PSICOLOGO', usuario__is_active=True)
        .annotate(c=Count('sesiones_asignadas', filter=Q(sesiones_asignadas__estado__in=['PENDIENTE','EN_CURSO'])))
        .order_by('c','id')
        .first())

def asignar_sesion_a(psicologo, sesion: SesionEvaluacion):
    sesion.psicologo = psicologo
    sesion.fecha_asignacion = timezone.now()
    sesion.save(update_fields=['psicologo','fecha_asignacion'])
    return sesion
