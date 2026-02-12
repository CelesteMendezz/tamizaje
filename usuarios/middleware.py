# en usuarios/middleware.py

from django.contrib.auth import get_user, logout
from django.shortcuts import redirect
from django.urls import reverse

class AccountStatusMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Primero, procesa la petición como lo haría normalmente
        response = self.get_response(request)

        # Solo actúa si el usuario está autenticado
        user = get_user(request)
        if user.is_authenticated:
            # Si el usuario NO está activo Y NO está ya en la página de "cuenta desactivada"
            if not user.is_active and request.path != reverse('account_disabled'):
                # Cierra la sesión para evitar bucles y redirige
                logout(request)
                return redirect('account_disabled')

        return response