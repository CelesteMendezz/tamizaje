# dashboard/decorators.py
from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect
from catalogo.models import EncuestaSociodemografica

def require_sociodemo_completed(view_func):
    """
    Si el estudiante no tiene sociodemo registrada, lo manda a /dashboard/sociodemo/
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        perfil = getattr(request.user, "perfil", None)
        if not perfil:
            # si no hay perfil, deja que tu lógica actual lo maneje
            return view_func(request, *args, **kwargs)

        ok = EncuestaSociodemografica.objects.filter(estudiante=perfil).exists()
        if not ok:
            messages.warning(
                request,
                "Antes de responder cualquier cuestionario debes completar la encuesta sociodemográfica."
            )
            return redirect("dashboard:sociodemo_form")
        return view_func(request, *args, **kwargs)
    return _wrapped
