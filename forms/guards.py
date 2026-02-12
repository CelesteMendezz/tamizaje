# forms/guards.py
from functools import wraps
from django.shortcuts import redirect
from django.urls import reverse

def require_sociodemo_completed(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        perfil = getattr(request.user, "perfil", None)
        if not perfil:
            return view_func(request, *args, **kwargs)

        # Si no existe encuesta, manda a capturarla
        if not hasattr(perfil, "sociodemo"):
            return redirect(reverse("dashboard:sociodemo_form"))

        return view_func(request, *args, **kwargs)
    return _wrapped
