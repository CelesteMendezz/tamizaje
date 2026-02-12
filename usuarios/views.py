# usuarios/views.py
from urllib.parse import urlparse
import json
from forms.models import Usuario
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponseBadRequest, Http404
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django.db.models.signals import post_save
from django.dispatch import receiver
from usuarios.models import InviteKey
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group


User = get_user_model()

# ----- Helpers de rol -----
def _is_psych(u):
    return (getattr(u, 'rol', '') or '').upper() == 'PSICOLOGO'

def _is_app_admin(u):
    r = (getattr(u, 'rol', '') or '').upper()
    return u.is_superuser or r == 'ADMIN'



# ----- Registro -----
def registro_usuario(request):
    if request.method == 'POST':
        nombre = (request.POST.get('nombre') or '').strip()
        email  = (request.POST.get('email')  or '').strip()
        rol    = (request.POST.get('rol')   or 'ESTUDIANTE').strip().upper()
        p1     = request.POST.get('password1') or ''
        p2     = request.POST.get('password2') or ''

        # --- Validaciones básicas ---
        if not email or not p1:
            messages.error(request, "Correo y contraseña son obligatorios.")
            return render(request, 'usuarios/signup.html')

        if p1 != p2:
            messages.error(request, "Las contraseñas no coinciden.")
            return render(request, 'usuarios/signup.html')

        if User.objects.filter(username=email).exists():
            messages.error(request, "Este correo ya está registrado.")
            return render(request, 'usuarios/signup.html')

        # --- Determinar rol final ANTES de crear el usuario ---
        inv = None
        final_rol = rol or 'ESTUDIANTE'

        if final_rol == 'PSICOLOGO':
            token = (request.POST.get('token') or '').strip()
            if not token:
                messages.error(request, "El token de invitación es obligatorio para registrarse como Psicólogo.")
                return render(request, 'usuarios/signup.html')

            try:
                inv = InviteKey.objects.get(token=token)
            except InviteKey.DoesNotExist:
                messages.error(request, "Token de invitación inválido.")
                return render(request, 'usuarios/signup.html')

            if not inv.is_valid():
                messages.error(request, "Token expirado, revocado o sin cupo.")
                return render(request, 'usuarios/signup.html')

            # Si el token trae rol específico, respétalo
            final_rol = getattr(inv, 'rol', 'PSICOLOGO')

        # --- Crear usuario PASANDO el rol (campo requerido) ---
        user = User.objects.create_user(
            username=email,
            first_name=nombre,
            email=email,
            password=p1,
            rol=final_rol,
        )

        # Dar permisos de staff si es ADMIN
        if (user.rol or '').upper() == 'ADMIN':
            user.is_staff = True
            user.save(update_fields=['is_staff'])

        # Consumir/contabilizar el token
        if inv:
            inv.used_count += 1
            inv.save(update_fields=['used_count'])

        # --- Login + redirección por rol ---
        login(request, user)
        role = (user.rol or '').upper()
        if user.is_superuser or role == 'ADMIN':
            return redirect('dashboard:admin_panel')
        elif role == 'PSICOLOGO':
            return redirect('dashboard:psico_panel')
        else:
            return redirect('dashboard:dashboard')

    # GET -> muestra el formulario
    return render(request, 'signup.html')


def custom_login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:redirect_after_login')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.is_active:
                login(request, user)
                return redirect('dashboard:redirect_after_login')
            else:
                return redirect('account_disabled')
        else:
            messages.error(request, "Tu usuario y contraseña no coinciden.")
    
    return render(request, 'registration/login.html')


# ----- Dashboards -----
@ensure_csrf_cookie  # <- garantiza cookie CSRF para los fetch POST del dashboard
@login_required(login_url=reverse_lazy('login'))
@user_passes_test(_is_app_admin, login_url=reverse_lazy('login'))
def dashboard_admin(request):
    return render(request, 'dashboard/admin.html')

@login_required(login_url=reverse_lazy('login'))
@user_passes_test(_is_psych, login_url=reverse_lazy('login'))
def dashboard_psicologo(request):
    return render(request, 'dashboard/psicologo.html')

@login_required(login_url=reverse_lazy('login'))
def dashboard_usuario(request):
    return render(request, 'dashboard/usuario.html')


# ===== API: Invites =====
@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["GET"])
def api_invites_list(request):
    qs = InviteKey.objects.order_by("-created_at")
    data = [{
        "id": inv.id,
        "token": str(inv.token),
        "rol": inv.rol,
        "max_uses": inv.max_uses,
        "used_count": inv.used_count,
        "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
        "revoked": inv.revoked,
        "created_at": inv.created_at.isoformat(),
    } for inv in qs]
    resp = JsonResponse({"ok": True, "results": data})
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    return resp


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["POST"])
def api_invites_create(request):
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("JSON inválido")

    # Forzar que siempre sea PSICOLOGO
    rol = "PSICOLOGO"

    try:
        max_uses = int(data.get("max_uses", 1))
        if max_uses < 1:
            raise ValueError()
    except Exception:
        return HttpResponseBadRequest("max_uses inválido")

    expires_at = None
    if data.get("expires_at"):
        expires_at = parse_datetime(str(data["expires_at"]))
        if expires_at is None:
            return HttpResponseBadRequest("expires_at inválido (usa ISO 8601)")

    inv = InviteKey.objects.create(
        rol=rol, max_uses=max_uses, expires_at=expires_at, created_by=request.user
    )
    return JsonResponse({"ok": True, "id": inv.id, "token": str(inv.token)})


@login_required
@user_passes_test(_is_app_admin)
@require_POST
def api_invites_revoke(request, pk):
    # Idempotente: si no existe -> 404; si ya está revocado -> ok:true
    try:
        inv = InviteKey.objects.get(pk=pk)
    except InviteKey.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Invite no existe"}, status=404)

    if not inv.revoked:
        inv.revoked = True
        inv.save(update_fields=["revoked"])

    return JsonResponse({"ok": True})



def account_disabled_view(request):
    return render(request, 'account_disabled.html')


