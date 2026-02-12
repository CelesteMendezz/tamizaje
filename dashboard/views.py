# dashboard/views.py
import csv
import json
import ast
import logging
from resultados.services import _build_whoqol_features, _build_whoqol_features_from_session, _clasificar_whoqol, actualizar_prediccion_estudiante
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordResetForm
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from resultados.feature_builders import build_panas_summary_for_session
from forms.guards import require_sociodemo_completed
from catalogo.models import EncuestaSociodemografica
from catalogo.forms import EncuestaSociodemograficaForm
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import localdate, localtime
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from forms.models import SesionEvaluacion, Perfil
from resultados.models import PrediccionRiesgo
from resultados.services import ml_ready_for_estudiante, urgencia_rank, actualizar_prediccion_estudiante  # <-- IMPORTANTE
from dashboard.decorators import require_sociodemo_completed
from forms.forms import PerfilForm   # 👈 correcto
from forms.models import Perfil
from resultados.services import (
    build_ml_explanation,
    ml_ready_for_estudiante,
    actualizar_prediccion_estudiante,
)
from forms.models import (
    CalificacionSesion,
    Cuestionario,
    Opcion,
    Perfil,
    Pregunta,
    ReporteEvaluacion,
    Respuesta,
    ScoringProfile,
    ScoringRule,
    SesionEvaluacion,
    Usuario,
)
from forms.services.scoring import compute_auto_sum_for_session
from forms.utils import asignar_sesion_a

logger = logging.getLogger(__name__)


# ===== Helpers de seguridad =====
def _is_app_admin(u):
    rol = (getattr(u, 'rol', '') or '').upper()
    return u.is_authenticated and (u.is_superuser or rol == 'ADMIN')

def _is_student(u):
    return u.is_authenticated and (getattr(u, 'rol', '') or '').upper() == 'ESTUDIANTE'

def _is_psych(u):
    return u.is_authenticated and (getattr(u, 'rol', '') or '').upper() == 'PSICOLOGO'

def _is_psico_or_admin(u):
    rol = (getattr(u, 'rol', '') or '').upper()
    return u.is_authenticated and (u.is_superuser or rol in {'ADMIN','PSICOLOGO'})


def _norm_tipo(tipo: str) -> str:
    t = (tipo or "").upper().strip()
    MAP = {
        "OPCION": "OPCION_UNICA",
        "RADIO": "OPCION_UNICA",
        "CHECKBOX": "OPCION_MULTIPLE",
        "LIKERT": "ESCALA",
        "ESCALA_NUMERICA": "ESCALA",
        "ESCALA NUMERICA": "ESCALA",
    }
    return MAP.get(t, t)


# ===== Vistas de panel =====
@login_required
@user_passes_test(_is_app_admin)
def dashboard_admin(request):
    # Listas de propuestas (no deben ir al catálogo)
    propuestas_pendientes = (Cuestionario.objects
        .filter(estado='EN_REVISION')
        .select_related('autor')
        .order_by('-fecha_publicacion'))

    propuestas_aprobadas = (Cuestionario.objects
        .filter(estado='APROBADA')   
        .select_related('autor')
        .order_by('-fecha_publicacion'))

    propuestas_rechazadas = (Cuestionario.objects
        .filter(estado='RECHAZADA')
        .select_related('autor')
        .order_by('-fecha_publicacion'))

    # 👇 Catálogo: sólo BORRADORES y PUBLICADOS(activos)
    catalogo_cuestionarios = (Cuestionario.objects
        .filter(Q(estado='draft') | Q(estado='published', activo=True))
        .annotate(num_preguntas=Count('preguntas'))
        .order_by('estado', 'codigo', 'version'))

    context = {
        'propuestas_pendientes': propuestas_pendientes,
        'propuestas_aprobadas': propuestas_aprobadas,
        'propuestas_rechazadas': propuestas_rechazadas,
        'catalogo_cuestionarios': catalogo_cuestionarios,
    }
    return render(request, 'dashboard/admin.html', context)


@login_required
@user_passes_test(lambda u: u.rol.upper() == "PSICOLOGO")
def dashboard_psicologo(request):
    perfil = request.user.perfil  # Obtener el perfil relacionado con el usuario

    if request.method == "POST":
        form = PerfilForm(request.POST, instance=perfil)

        # Desactivar validación de campos obligatorios que no se muestran
        form.fields['rol'].required = False
        form.fields['adscripcion'].required = False
        form.fields['matricula'].required = False
        form.fields['carrera'].required = False
        form.fields['semestre'].required = False

        if form.is_valid():
            form.save()
            messages.success(request, "¡Datos guardados correctamente!")
            return redirect("dashboard_psicologo")
        else:
            messages.error(request, "Por favor corrige los errores en el formulario.")
    else:
        form = PerfilForm(instance=perfil)
        # También desactivar required en GET para renderizar correctamente
        form.fields['rol'].required = False
        form.fields['adscripcion'].required = False
        form.fields['matricula'].required = False
        form.fields['carrera'].required = False
        form.fields['semestre'].required = False

    context = {
        "form": form,
        "perfil": perfil,
    }
    return render(request, "dashboard/psicologo.html", context)



@login_required
@user_passes_test(_is_student)
def dashboard_usuario(request):
    """
    Panel principal del estudiante:
      - POST: actualizar perfil
      - GET:  muestra 'pendientes' (publicados/activos no completados),
              'completados' y, si hay sesión abierta, botón 'Continuar'.
      - NUEVO: sección "Mi Cuenta" integrada con PerfilForm
      - NUEVO: muestra si la sociodemo está completa y bloquea visualmente los cuestionarios
    """
    try:
        perfil = request.user.perfil
    except Perfil.DoesNotExist:
        messages.error(request, "No se encontró tu perfil de estudiante. Contacta a soporte.")
        return redirect('logout')


    if not perfil.nombre_completo:
        nombre_auto = request.user.get_full_name()
        perfil.nombre_completo = nombre_auto if nombre_auto else request.user.username
        perfil.save(update_fields=["nombre_completo"])
        
    # ============================================================
    # NUEVO: formulario de Mi Cuenta
    # ============================================================
    form = PerfilForm(instance=perfil)

    # ============================================================
    # NUEVO: ¿ya llenó sociodemo?
    # ============================================================
    sociodemo_ok = EncuestaSociodemografica.objects.filter(estudiante=perfil).exists()

    # ============================================================
    # POST: actualizar perfil (SE MANTIENE TU LÓGICA)
    # ============================================================
    if request.method == 'POST':
        try:
            with transaction.atomic():
                user = request.user

                perfil.nombre_completo = (request.POST.get('nombre_completo') or '').strip()
                perfil.telefono = (request.POST.get('telefono') or '').strip()
                perfil.carrera = (request.POST.get('carrera') or '').strip()
                perfil.adscripcion = request.POST.get('adscripcion') or None
                perfil.matricula = (request.POST.get('matricula') or '').strip()


                try:
                    perfil.semestre = int(request.POST.get('semestre'))
                except (ValueError, TypeError):
                    perfil.semestre = None

                # Solo si es psicólogo
                if perfil.rol == 'PSICOLOGO':
                    perfil.cedula_profesional = (request.POST.get('cedula_profesional') or '').strip()

                perfil.save()

                user.email = (request.POST.get('correo') or '').strip()
                user.save()

            messages.success(request, "Tu perfil se ha actualizado correctamente.")
        except Exception as e:
            messages.error(request, f"Error al actualizar tu perfil: {e}")

        return redirect('dashboard:dashboard')

    # ============================================================
    # GET: listados de cuestionarios
    # ============================================================
    estados_publicado = ['published']

    sesiones_qs = (
        SesionEvaluacion.objects
        .filter(estudiante=perfil)
        .select_related('cuestionario')
    )

    sesiones_completadas = (
        sesiones_qs.filter(
            Q(estado__in=['COMPLETADA', 'FINALIZADA']) |
            Q(fecha_fin__isnull=False)
        )
        .order_by('-fecha_fin', '-id')
    )

    cuestionarios_completados_ids = sesiones_completadas.values_list(
        'cuestionario_id', flat=True
    )

    sesiones_abiertas = (
        sesiones_qs
        .filter(estado__in=['PENDIENTE', 'EN_CURSO'])
        .exclude(id__in=sesiones_completadas.values('id'))
    )

    continuar_por_cuestionario = {}
    for s in sesiones_abiertas.order_by('-id'):
        continuar_por_cuestionario.setdefault(s.cuestionario_id, s.id)

    cuestionarios_pendientes = (
        Cuestionario.objects
        .filter(estado__in=estados_publicado, activo=True)
        .exclude(id__in=cuestionarios_completados_ids)
        .order_by('codigo', 'id')
    )

    for c in cuestionarios_pendientes:
        c.sesion_abierta_id = continuar_por_cuestionario.get(c.id)
        c.bloqueado_por_sociodemo = not sociodemo_ok

    # ============================================================
    # CONTEXT FINAL
    # ============================================================
    context = {
        'perfil': perfil,
        'form': form,                         # 
        'pendientes': cuestionarios_pendientes,
        'completados': sesiones_completadas,
        'continuar_por_cuestionario': continuar_por_cuestionario,
        'sociodemo_ok': sociodemo_ok,
    }

    return render(request, 'dashboard/dashboard.html', context)



import logging

logger = logging.getLogger(__name__)

def _labels_to_spec(labels):
    if labels is None:
        return ""

    # 1) Si ya es dict, conviértelo
    if isinstance(labels, dict):
        items = []
        for k, v in labels.items():
            try:
                kk = int(k)
            except Exception:
                continue
            items.append((kk, str(v)))
        items.sort(key=lambda x: x[0])
        return ", ".join([f"{k}={v}" for k, v in items])

    # 2) Si es string, puede venir en 3 formatos: spec, JSON, o dict python str
    if isinstance(labels, str):
        s = labels.strip()
        if not s:
            return ""

        # 2a) Si ya es spec "1=..., 2=..."
        if "=" in s and "{" not in s:
            return s

        # 2b) Si parece JSON
        if s.startswith("{") and s.endswith("}"):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    return _labels_to_spec(obj)
            except Exception:
                # 2c) Si parece dict de Python con comillas simples
                try:
                    obj = ast.literal_eval(s)
                    if isinstance(obj, dict):
                        return _labels_to_spec(obj)
                except Exception:
                    return ""

        # último fallback
        return s

    return ""



@login_required
@user_passes_test(_is_student)
@require_sociodemo_completed
@require_http_methods(["GET", "POST"])
def responder_evaluacion(request, cuestionario_id):
    # 1) Perfil del estudiante
    try:
        perfil_estudiante = Perfil.objects.get(usuario=request.user)
    except Perfil.DoesNotExist:
        return HttpResponseForbidden("Perfil de estudiante no encontrado.")

    # 2) Cuestionario visible
    estados_respondibles = ["published", "APROBADA", "ACEPTADA", "PUBLICADO"]
    cuestionario = get_object_or_404(
        Cuestionario,
        pk=cuestionario_id,
        estado__in=estados_respondibles,
        activo=True,
    )

    # 3) Preguntas
    preguntas = list(
        cuestionario.preguntas
        .prefetch_related("opciones")
        .order_by("orden")
    )

    # 4) Normalizar config
    for p in preguntas:
        cfg = getattr(p, "config", None) or {}
        if isinstance(cfg, str):
            try:
                p.config = json.loads(cfg) if cfg.strip() else {}
            except Exception:
                try:
                    p.config = ast.literal_eval(cfg)
                except Exception:
                    p.config = {}
        if not isinstance(p.config, dict):
            p.config = {}

    # 5) Ya completado
    if SesionEvaluacion.objects.filter(
        estudiante=perfil_estudiante,
        cuestionario=cuestionario,
        estado="COMPLETADA",
    ).exists():
        messages.warning(request, "Este cuestionario ya lo completaste.")
        return redirect("dashboard:dashboard")

    # 6) Sesión
    sesion = None
    sid_post = (request.POST.get("sesion_id") or "").strip() if request.method == "POST" else ""
    sid_get = (request.GET.get("sesion") or "").strip()
    sid = sid_post or sid_get

    if sid.isdigit():
        sesion = SesionEvaluacion.objects.filter(
            id=int(sid),
            estudiante=perfil_estudiante,
            cuestionario=cuestionario,
            estado__in=["PENDIENTE", "EN_CURSO"],
        ).first()

    if request.method == "POST" and sid_post and not sesion:
        return HttpResponseForbidden("Sesión inválida o no autorizada.")

    if not sesion:
        sesion = SesionEvaluacion.objects.filter(
            estudiante=perfil_estudiante,
            cuestionario=cuestionario,
            estado__in=["PENDIENTE", "EN_CURSO"],
        ).order_by("-fecha_inicio").first()

    if not sesion:
        sesion = SesionEvaluacion.objects.create(
            estudiante=perfil_estudiante,
            cuestionario=cuestionario,
            estado="PENDIENTE",
        )

    # =========================
    # 7) POST
    # =========================
    if request.method == "POST":

        # 🔥 LIMPIEZA REAL DEL POST
        raw_post = dict(request.POST)
        clean_post = {}

        for key, values in raw_post.items():
            if not key.startswith("preg_"):
                continue
            cleaned = [v for v in values if v not in ("", None)]
            if cleaned:
                clean_post[key] = cleaned

        print("🧼 POST LIMPIO:", clean_post)

        # 7.1 Validación
        faltantes = []

        for pregunta in preguntas:
            if not pregunta.requerido:
                continue

            tipo = _norm_tipo(pregunta.tipo_respuesta)
            name = f"preg_{pregunta.id}"

            if tipo in ("OPCION_UNICA", "ESCALA", "SI_NO"):
                if name not in clean_post:
                    faltantes.append(pregunta.orden)

            elif tipo == "OPCION_MULTIPLE":
                if name not in clean_post or len(clean_post[name]) == 0:
                    faltantes.append(pregunta.orden)

            elif tipo == "TEXTO":
                if name not in clean_post or not clean_post[name][0].strip():
                    faltantes.append(pregunta.orden)

            elif tipo == "NUMERICA":
                if name not in clean_post:
                    faltantes.append(pregunta.orden)
                else:
                    try:
                        float(clean_post[name][0])
                    except ValueError:
                        faltantes.append(pregunta.orden)

        if faltantes:
            messages.error(
                request,
                "Faltan preguntas obligatorias: " + ", ".join(map(str, sorted(faltantes))),
            )
            return render(
                request,
                "dashboard/responder_cuestionario.html",
                {"sesion": sesion, "preguntas": preguntas},
            )

        # 7.2 Guardado
        try:
            with transaction.atomic():
                for pregunta in preguntas:
                    tipo = _norm_tipo(pregunta.tipo_respuesta)
                    name = f"preg_{pregunta.id}"

                    if name not in clean_post:
                        continue

                    defaults = {
                        "opcion_seleccionada": None,
                        "valor_numerico": None,
                        "valor_texto": None,
                        "opciones_multiple": [],
                    }

                    if tipo == "OPCION_UNICA":
                        val = clean_post[name][0]
                        opcion = Opcion.objects.filter(pk=val, pregunta=pregunta).first()
                        if opcion:
                            defaults["opcion_seleccionada"] = opcion
                            defaults["valor_texto"] = opcion.texto
                            try:
                                defaults["valor_numerico"] = float(opcion.valor)
                            except Exception:
                                pass

                    elif tipo == "OPCION_MULTIPLE":
                        defaults["opciones_multiple"] = clean_post[name]

                    elif tipo in ("TEXTO", "SI_NO"):
                        defaults["valor_texto"] = clean_post[name][0].strip()

                    elif tipo in ("NUMERICA", "ESCALA"):
                        try:
                            defaults["valor_numerico"] = float(clean_post[name][0])
                        except Exception:
                            pass

                    Respuesta.objects.update_or_create(
                        sesion=sesion,
                        pregunta=pregunta,
                        defaults=defaults,
                    )

                if not sesion.puede_completarse():
                    raise ValueError("La sesión no tiene respuestas suficientes.")

                sesion.estado = "COMPLETADA"
                sesion.fecha_fin = timezone.now()
                sesion.save(update_fields=["estado", "fecha_fin"])

            messages.success(request, "Cuestionario enviado correctamente.")
            return redirect("dashboard:dashboard")

        except Exception as e:
            logger.exception("Error al guardar evaluación: %s", e)
            messages.error(request, f"Ocurrió un error al guardar: {e}")

    # 8) GET
    if sesion.estado == "PENDIENTE":
        sesion.estado = "EN_CURSO"
        sesion.save(update_fields=["estado"])

    return render(
        request,
        "dashboard/responder_cuestionario.html",
        {"sesion": sesion, "preguntas": preguntas},
    )







@login_required
def redirect_after_login(request):
    rol = getattr(request.user, 'rol', 'ESTUDIANTE').upper()
    if rol == 'ADMIN':
        return redirect('dashboard:admin_panel')
    elif rol == 'PSICOLOGO':
        return redirect('dashboard:psico_panel')
    else:
        return redirect('dashboard:dashboard')


# ===== API: Usuarios & Roles (usada por admin.html) =====
@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["GET", "POST"])
def api_usuarios(request):
    # --- ✅ LÓGICA PARA CREAR USUARIO (POST) ---
    if request.method == 'POST':
        try:
            payload = json.loads(request.body.decode("utf-8"))
            username = payload.get('username')
            password = payload.get('password')
            rol = str(payload.get('rol', 'ESTUDIANTE')).upper()

            if not username or not password:
                return JsonResponse({"ok": False, "error": "Usuario y contraseña son requeridos."}, status=400)
            
            # Prevenir correos duplicados (tu requerimiento)
            if Usuario.objects.filter(username=username).exists():
                return JsonResponse({"ok": False, "error": "El correo electrónico ya está registrado."}, status=400)
            
            if rol not in ("ADMIN", "PSICOLOGO", "ESTUDIANTE"):
                return JsonResponse({"ok": False, "error": "Rol inválido"}, status=400)

            new_user = Usuario.objects.create_user(
                username=username,
                password=password,
                email=username, # Asumimos que username es el email
                first_name=payload.get('first_name', ''),
                last_name=payload.get('last_name', '')
            )
            new_user.rol = rol
            new_user.save()

            return JsonResponse({"ok": True, "id": new_user.id}, status=201)

        except Exception as e:
            return JsonResponse({"ok": False, "error": f"Error interno: {e}"}, status=400)

    # --- LÓGICA PARA LISTAR USUARIOS (GET) CON BÚSQUEDA Y PAGINACIÓN ---
    qs = Usuario.objects.order_by("username")

    # Búsqueda
    search_term = request.GET.get('q', '').strip()
    if search_term:
        qs = qs.filter(
            Q(username__icontains=search_term) |
            Q(first_name__icontains=search_term) |
            Q(last_name__icontains=search_term)
        )

    # Paginación
    paginator = Paginator(qs, 5) # Muestra 5 usuarios por página
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    def _nombre(u):
        return f"{u.first_name or ''} {u.last_name or ''}".strip() or ""

    data = [{
        "id": u.id,
        "username": u.username,
        "nombre": _nombre(u),
        "rol": u.rol,
        "activo": bool(u.is_active),
        "last": u.last_login.isoformat() if u.last_login else None,
    } for u in page_obj]

    return JsonResponse({
        "results": data,
        "pagination": {
            "total_pages": paginator.num_pages,
            "current_page": page_obj.number,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
        }
    })


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["PATCH", "DELETE"])
def api_usuario_detalle(request, pk):
    try:
        user = Usuario.objects.get(pk=pk)
    except Usuario.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Usuario no existe"}, status=404)

    # --- Lógica para DELETE ---
    if request.method == 'DELETE':
        if user.pk == request.user.pk:
            return JsonResponse({"ok": False, "error": "No puedes eliminar tu propia cuenta."}, status=403)
        user.delete()
        return JsonResponse({"ok": True})

    # --- Lógica para PATCH ---
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"JSON inválido: {e}"}, status=400)

    updated_fields = []
    if "rol" in payload:
        new_rol = str(payload["rol"]).upper()
        if new_rol not in ("ADMIN", "PSICOLOGO", "ESTUDIANTE"):
            return JsonResponse({"ok": False, "error": "Rol inválido"}, status=400)
        user.rol = new_rol
        updated_fields.append("rol")

    if "activo" in payload:
        user.is_active = bool(payload["activo"])
        updated_fields.append("is_active")

    if updated_fields:
        user.save(update_fields=updated_fields)

    return JsonResponse({"ok": True})

@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["POST"])
def api_usuario_reset_password(request, pk):
    try:
        user = Usuario.objects.get(pk=pk)
    except Usuario.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Usuario no existe"}, status=404)

    if not user.email:
        return JsonResponse({"ok": False, "error": "El usuario no tiene un email registrado."}, status=400)

    form = PasswordResetForm({'email': user.email})
    if form.is_valid():
        form.save(
            request=request,
            use_https=request.is_secure(),
            email_template_name='registration/password_reset_email.html',
            subject_template_name='registration/password_reset_subject.txt'
        )
        return JsonResponse({"ok": True, "message": f"Enlace de reseteo enviado a {user.email}"})
    
    return JsonResponse({"ok": False, "error": "No se pudo enviar el correo."}, status=500)


# =====================================================
# VISTAS PARA GESTIÓN DE SESIONES DE EVALUACIÓN
# =====================================================

@login_required
@user_passes_test(_is_app_admin) # O ajusta el permiso si los psicólogos también pueden verla
def sesion_evaluacion_list(request):
    """
    Vista principal que lista todas las sesiones de evaluación con filtros.
    """
    sesiones_qs = SesionEvaluacion.objects.select_related(
        'estudiante__usuario', 
        'cuestionario', 
        'psicologo__usuario'
    ).order_by('-fecha_inicio')

    # Lógica de filtros (ejemplo)
    filtro_estado = request.GET.get('estado', '')
    if filtro_estado and filtro_estado in ['PENDIENTE', 'EN_CURSO', 'COMPLETADA']:
        sesiones_qs = sesiones_qs.filter(estado=filtro_estado)

    # Preparar psicólogos para el filtro
    psicologos = Perfil.objects.filter(usuario__rol='PSICOLOGO')

    context = {
        'sesiones': sesiones_qs,
        'psicologos': psicologos,
        'filtro_estado_actual': filtro_estado,
    }
    return render(request, 'dashboard/sesiones_list.html', context)


@login_required
@user_passes_test(_is_app_admin) # O ajusta el permiso
def sesion_evaluacion_detalle(request, pk):
    """
    Vista para ver los detalles y respuestas de una sesión específica.
    """
    sesion = get_object_or_404(
        SesionEvaluacion.objects.select_related(
            'estudiante__usuario', 'cuestionario', 'psicologo__usuario'
        ), pk=pk
    )
    
    # Obtener todas las respuestas de esta sesión, ordenadas por la pregunta
    respuestas = sesion.respuestas.select_related('pregunta', 'opcion_seleccionada').order_by('pregunta__orden')

    context = {
        'sesion': sesion,
        'respuestas': respuestas,
    }
    return render(request, 'dashboard/sesion_detalle.html', context)


@login_required
@user_passes_test(_is_psych)
@require_http_methods(["GET"])
def api_mis_sesiones(request):
    """
    API endpoint que devuelve las sesiones de evaluación ASIGNADAS
    al psicólogo que ha iniciado sesión.
    """
    try:
        # Buscamos el perfil del psicólogo actual
        perfil_psicologo = Perfil.objects.get(usuario=request.user)
        
        # Filtramos las sesiones asignadas a este perfil
        sesiones_qs = SesionEvaluacion.objects.filter(
            psicologo=perfil_psicologo
        ).select_related(
            'estudiante__usuario', 
            'cuestionario'
        ).order_by('-fecha_inicio')

        data = [{
            "id": s.id,
            "folio": f"S-{s.id}",
            "estudiante_nombre": s.estudiante.nombre_completo or s.estudiante.usuario.username,
            "cuestionario_codigo": s.cuestionario.codigo,
            "estado": s.get_estado_display(),
            "estado_raw": s.estado, # Para filtrar en JS
            "fecha_inicio": s.fecha_inicio.isoformat(),
        } for s in sesiones_qs]

        return JsonResponse({"ok": True, "results": data})

    except Perfil.DoesNotExist:
        return JsonResponse({"ok": False, "error": "El perfil de psicólogo no fue encontrado."}, status=404)
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
    


def _sum_numeric_for_codes(session_id, codes):
    qs = (
        Respuesta.objects
        .select_related('pregunta')
        .filter(
            sesion_id=session_id,
            valor_numerico__isnull=False,
            pregunta__codigo__in=codes
        )
    )
    vals = [float(r.valor_numerico) for r in qs]
    if not vals:
        return None, 0
    return sum(vals), len(vals)


def _calc_sum_for_prefix(session_id, prefix):
    qs = (Respuesta.objects
          .select_related('pregunta')
          .filter(sesion_id=session_id, valor_numerico__isnull=False,
                  pregunta__codigo__startswith=prefix))
    vals = [float(r.valor_numerico) for r in qs]
    return (sum(vals), len(vals)) if vals else (None, 0)


from django.db.models import Sum

def _calc_sum_for_session(session_id):
    """
    Suma TODOS los valor_numerico de una sesión.
    Útil cuando Pregunta.codigo está vacío.
    Retorna: (total, n)
    """
    qs = (Respuesta.objects
          .filter(sesion_id=session_id, valor_numerico__isnull=False))

    n = qs.count()
    if n == 0:
        return (None, 0)

    total = qs.aggregate(t=Sum("valor_numerico"))["t"]
    total = float(total) if total is not None else None
    return (total, n)


def _score_summary_for_session(s):
    codigo = s.cuestionario.codigo

    # ===============================
    # PANAS
    # ===============================
    if codigo == "PANAS":
        return build_panas_summary_for_session(s)

    # ===============================
    # CASO-A30
    # ===============================
    if codigo == "CASO-A30":
        total, n = _calc_sum_for_session(s.id)
        mean = (total / n) if (total is not None and n > 0) else None

        return {
            "titulo": "Resultados del cuestionario — CASO-A30",
            "items": [
                {"label": "Suma de 30 ítems (1–5)", "value": total, "fmt": "float2"},
                {"label": "Promedio", "value": mean, "fmt": "float2"},
                {
                    "label": "Interpretación",
                    "value": "Puntajes altos indican mayor asertividad. Puntajes bajos pueden reflejar pasividad o agresividad indirecta. Media teórica: 90."
                },
            ],
            "debug": {"n_respuestas_encontradas": n}
        }

    # ===============================
    # WHOQOL-BREF
    # ===============================
  # ===============================
# WHOQOL-BREF
# ===============================
    if codigo in ["WHO-QOL", "WHOQOL", "WHOQOL-BREF"]:

        features = _build_whoqol_features_from_session(s)

        global_score = features.get("WHOQOL_TOTAL_MEAN")
        phys = features.get("WHOQOL_PHYS_MEAN")
        psych = features.get("WHOQOL_PSYCH_MEAN")
        social = features.get("WHOQOL_SOCIAL_MEAN")
        env = features.get("WHOQOL_ENV_MEAN")

        nivel = features.get("WHOQOL_LEVEL")

        return {
            "titulo": "Resultados del cuestionario — WHOQOL-BREF",
            "items": [
                {
                    "label": "Promedio General (26 ítems)",
                    "value": global_score,
                    "fmt": "float2"
                },
                {
                    "label": "Clasificación",
                    "value": nivel
                },
                {
                    "label": "Dominio Físico",
                    "value": phys,
                    "fmt": "float2"
                },
                {
                    "label": "Dominio Psicológico",
                    "value": psych,
                    "fmt": "float2"
                },
                {
                    "label": "Dominio Social",
                    "value": social,
                    "fmt": "float2"
                },
                {
                    "label": "Dominio Ambiente",
                    "value": env,
                    "fmt": "float2"
                },
            ],
            "debug": {"n_respuestas_encontradas": features.get("WHOQOL_N_RESP")}
        }



    # ===============================
    # DEFAULT
    # ===============================
    return {
        "titulo": f"Resultados del cuestionario — {codigo}",
        "items": [
            {
                "label": "Nota",
                "value": "Este cuestionario aún no tiene resumen sin ML configurado."
            }
        ],
        "debug": {"n_respuestas_encontradas": 0}
    }




from django.utils import timezone
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from resultados.services import build_ml_explanation
from resultados.models import PrediccionRiesgo
from resultados.services import actualizar_prediccion_estudiante  # <-- IMPORTANTE

@login_required
@user_passes_test(_is_psych)
def psico_sesion_detalle(request, pk):
    me = request.user.perfil

    # ==========================================================
    # Sesión
    # ==========================================================
    s = get_object_or_404(
        SesionEvaluacion.objects.select_related(
            'cuestionario',
            'estudiante__usuario',
            'psicologo__usuario'
        ),
        pk=pk
    )

    if s.psicologo_id and s.psicologo_id != me.id:
        return HttpResponseForbidden("No tienes permiso para ver este caso.")

    can_view_answers = (s.psicologo_id == me.id)

    # ==========================================================
    # Respuestas
    # ==========================================================
    if can_view_answers:
        respuestas = (
            Respuesta.objects
            .select_related('pregunta', 'opcion_seleccionada')
            .filter(sesion=s)
            .order_by('pregunta__orden', 'id')
        )
    else:
        respuestas = Respuesta.objects.none()

    # ==========================================================
    # Resumen SIN ML
    # ==========================================================
    score_summary = _score_summary_for_session(s)

    # ==========================================================
    # Sociodemográfico
    # ==========================================================
    sociodemo = None
    if can_view_answers:
        sociodemo = getattr(s.estudiante, "sociodemo", None)
        if sociodemo is None:
            sociodemo = (
                EncuestaSociodemografica.objects
                .filter(estudiante=s.estudiante)
                .order_by("-id")
                .first()
            )

    # ==========================================================
    # Machine Learning
    # ==========================================================
    ml_ready, nreq, nreq_total = ml_ready_for_estudiante(s.estudiante)

    ml = None
    ml_explanation = []
    ml_narrative = ""
    ml_chart = ""

    if ml_ready:
        pred = PrediccionRiesgo.objects.filter(estudiante=s.estudiante).first()

        if pred is None or pred.nivel == "SIN_DATOS" or pred.probabilidad is None:
            pred = actualizar_prediccion_estudiante(s.estudiante)

        if pred and pred.probabilidad is not None and pred.nivel != "SIN_DATOS":

            ml_data = build_ml_explanation(pred)

        ml = {
            "nivel": pred.nivel.upper(),
            "probabilidad": float(pred.probabilidad) * 100,
            "actualizado": pred.actualizado,
            "modelo_version": pred.modelo_version,
            "risk_factors": ml_data.get("risk_factors", []),
            "protective_factors": ml_data.get("protective_factors", []),
            "narrative": ml_data.get("narrative", ""),
            "recommendation": ml_data.get("recommendation", ""),
        }


    context = {
        "sesion": s,
        "respuestas": respuestas,
        "can_view_answers": can_view_answers,
        "score_summary": score_summary,
        "sociodemo": sociodemo,

        "ml_ready": ml_ready,
        "required_completed": nreq,
        "required_total": nreq_total,

        "ml": ml,
        "ml_explanation": ml_explanation,
        "ml_narrative": ml_narrative,
        "ml_chart": ml_chart,
    }

    return render(
        request,
        "dashboard/psico_sesion_detalle.html",
        context
    )








REQUIRED_CODES = ["PANAS", "WHO-QOL", "CASO-A30"]

@login_required
@user_passes_test(_is_psych)
def api_psico_sesiones(request):
    scope = request.GET.get('scope', 'asignados')
    q     = (request.GET.get('q') or '').strip()

    me = request.user.perfil

    qs = (
        SesionEvaluacion.objects
        .select_related('cuestionario', 'estudiante__usuario', 'psicologo')
        .order_by('-fecha_fin', '-fecha_inicio')
    )

    # Scopes (idénticos a tu lógica)
    if scope == 'inbox':
        qs = qs.filter(psicologo__isnull=True, estado='COMPLETADA')

    elif scope == 'asignados':
        qs = qs.filter(psicologo=me, estado='COMPLETADA')

    elif scope == 'en_curso':
        qs = qs.filter(psicologo=me, estado__in=['PENDIENTE', 'EN_CURSO'])

    elif scope == 'completados':
        qs = qs.filter(psicologo=me, estado='COMPLETADA')

    else:
        qs = qs.filter(psicologo=me, estado='COMPLETADA')

    if q:
        qs = qs.filter(
            Q(estudiante__usuario__first_name__icontains=q) |
            Q(estudiante__usuario__last_name__icontains=q) |
            Q(estudiante__usuario__username__icontains=q) |
            Q(cuestionario__codigo__icontains=q)
        )

    # === Pre-cálculo: cuantos requeridos tiene cada estudiante ===
    req_counts = (
        SesionEvaluacion.objects
        .filter(estado="COMPLETADA", cuestionario__codigo__in=REQUIRED_CODES)
        .values("estudiante_id")
        .annotate(n=Count("cuestionario__codigo", distinct=True))
    )
    req_map = {row["estudiante_id"]: row["n"] for row in req_counts}

    # === Predicciones por estudiante ===
    preds = PrediccionRiesgo.objects.in_bulk(field_name="estudiante_id")

    results = []
    total_required = len(REQUIRED_CODES)

    for s in qs[:500]:
        est_u = s.estudiante.usuario

        nreq = req_map.get(s.estudiante_id, 0)
        ml_ready = (nreq >= total_required)

        pred = preds.get(s.estudiante_id)
        if pred and ml_ready and pred.probabilidad is not None and (pred.nivel or "") != "SIN_DATOS":
            ml_prob = float(pred.probabilidad)
            ml_nivel = (pred.nivel or "SIN_DATOS")
        else:
            ml_prob = None
            ml_nivel = "SIN_DATOS"

        u_rank = urgencia_rank(ml_nivel) if ml_ready else 0

        results.append({
            "id": s.id,
            "folio": f"S{str(s.id).zfill(5)}",

            "estudiante_id": s.estudiante_id,
            "estudiante_username": est_u.username,
            "estudiante_nombre": (est_u.get_full_name() or est_u.username),

            "cuestionario_codigo": s.cuestionario.codigo,
            "cuestionario_nombre": s.cuestionario.nombre,
            "estado": s.estado,
            "fecha_inicio": s.fecha_inicio.isoformat() if s.fecha_inicio else None,
            "fecha_fin": s.fecha_fin.isoformat() if s.fecha_fin else None,

            # === ML ===
            "ml_ready": ml_ready,
            "required_completed": nreq,
            "required_total": total_required,
            "ml_prob": ml_prob,
            "ml_nivel": ml_nivel,
            "urgencia_rank": u_rank,
        })

    # Orden server-side por urgencia y luego por fecha
    def _sort_key(r):
        # usa fecha_fin si existe, si no fecha_inicio
        t = r["fecha_fin"] or r["fecha_inicio"] or ""
        return (r["urgencia_rank"], t)

    results.sort(key=_sort_key, reverse=True)

    return JsonResponse({"ok": True, "results": results})




@login_required
@user_passes_test(_is_psych)
@require_POST
def api_psico_asignar(request, pk):
    me = request.user.perfil
    sesion = get_object_or_404(SesionEvaluacion, pk=pk)

    if sesion.estado != 'COMPLETADA':
        return JsonResponse({
            "ok": False,
            "error": "Solo puedes asignar sesiones COMPLETADAS."
        }, status=400)

    if sesion.psicologo_id is not None:
        return JsonResponse({
            "ok": False,
            "error": "Este caso ya tiene psicólogo asignado."
        }, status=409)

    # 🔥 NUEVA LÓGICA: asignar TODAS las sesiones del mismo estudiante
    sesiones_a_asignar = SesionEvaluacion.objects.filter(
        estudiante=sesion.estudiante,
        estado='COMPLETADA',
        psicologo__isnull=True
    )

    now = timezone.now()

    sesiones_a_asignar.update(
        psicologo=me,
        fecha_asignacion=now
    )

    return JsonResponse({
        "ok": True,
        "asignadas": sesiones_a_asignar.count()
    })


@login_required
@user_passes_test(_is_app_admin)
def api_admin_sesiones(request):
    if request.method != 'GET':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'}, status=405)

    q = (request.GET.get('q') or '').strip()
    estado = (request.GET.get('estado') or '').strip().upper()

    qs = (SesionEvaluacion.objects
          .select_related('cuestionario', 'estudiante__usuario', 'psicologo__usuario')
          .annotate(respuestas_count=Count('respuestas'))
          .order_by('-id'))

    if estado in {'PENDIENTE', 'EN_CURSO', 'COMPLETADA'}:
        qs = qs.filter(estado=estado)

    if q:
        qs = qs.filter(
            Q(cuestionario__codigo__icontains=q) |
            Q(cuestionario__nombre__icontains=q) |
            Q(estudiante__usuario__first_name__icontains=q) |
            Q(estudiante__usuario__last_name__icontains=q) |
            Q(estudiante__usuario__username__icontains=q) |
            Q(psicologo__usuario__first_name__icontains=q) |
            Q(psicologo__usuario__last_name__icontains=q) |
            Q(psicologo__usuario__username__icontains=q) |
            Q(id__icontains=q)
        )

    results = []
    for s in qs:
        est_u = getattr(s.estudiante, 'usuario', None)
        psi_u = getattr(getattr(s, 'psicologo', None), 'usuario', None)
        estudiante = (f"{getattr(est_u, 'first_name', '')} {getattr(est_u, 'last_name', '')}".strip()
                      or getattr(est_u, 'username', '') or '—')
        psicologo = (f"{getattr(psi_u, 'first_name', '')} {getattr(psi_u, 'last_name', '')}".strip()
                     or getattr(psi_u, 'username', '') or '—')
        results.append({
            'id': s.id,
            'folio': getattr(s, 'folio', None) or f"S{str(s.id).zfill(5)}",
            'cuestionario_codigo': getattr(s.cuestionario, 'codigo', ''),
            'cuestionario_nombre': getattr(s.cuestionario, 'nombre', ''),
            'estudiante': estudiante,
            'psicologo': psicologo if s.psicologo_id else '—',
            'estado': s.estado,
            'fecha_inicio': s.fecha_inicio.isoformat() if s.fecha_inicio else None,
            'fecha_fin': s.fecha_fin.isoformat() if s.fecha_fin else None,
            'respuestas_count': s.respuestas_count,
            # 👉 link SOLO a “ver cuestionario” (sin respuestas)
            'detalle_url': reverse('dashboard:admin_sesion_cuestionario', args=[s.id]),
        })

    return JsonResponse({'ok': True, 'results': results})

@login_required
@user_passes_test(_is_app_admin)
def admin_sesion_cuestionario(request, pk: int):
    """
    Vista de SOLO LECTURA para admin:
    - Muestra metadatos de la sesión y la estructura del cuestionario (preguntas y opciones).
    - NO accede ni muestra Respuestas.
    """
    sesion = get_object_or_404(
        SesionEvaluacion.objects.select_related(
            'cuestionario', 'estudiante__usuario', 'psicologo__usuario'
        ),
        pk=pk
    )

    cuestionario = sesion.cuestionario
    preguntas = (cuestionario.preguntas
                 .prefetch_related('opciones')
                 .order_by('orden', 'id'))

    context = {
        'sesion': sesion,
        'cuestionario': cuestionario,
        'preguntas': preguntas,
        # metadatos para la barra superior/tabla
        'meta': {
            'folio': getattr(sesion, 'folio', None) or f"S{str(sesion.id).zfill(5)}",
            'estudiante': f"{getattr(sesion.estudiante.usuario, 'first_name', '')} {getattr(sesion.estudiante.usuario, 'last_name', '')}".strip()
                          or getattr(sesion.estudiante.usuario, 'username', ''),
            'psicologo': (f"{getattr(getattr(sesion.psicologo, 'usuario', None), 'first_name', '')} "
                          f"{getattr(getattr(sesion.psicologo, 'usuario', None), 'last_name', '')}".strip()
                          if sesion.psicologo_id else '—'),
            'estado': sesion.estado,
            'fecha_inicio': sesion.fecha_inicio,
            'fecha_fin': sesion.fecha_fin,
            'respuestas_count': getattr(sesion, 'respuestas_count', None)  # por si quieres mostrar el número
        }
    }
    return render(request, 'dashboard/admin_sesion_cuestionario.html', context)


@login_required
@user_passes_test(_is_app_admin)
@require_POST
def api_scoring_profile_create(request):
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "JSON inválido"}, status=400)

    cid = body.get("cuestionario_id")
    nombre = (body.get("nombre") or "").strip()
    algoritmo = (body.get("algoritmo") or "SUM").upper()

    if not cid or not nombre:
        return JsonResponse({"ok": False, "error": "Falta cuestionario_id o nombre"}, status=400)

    cu = get_object_or_404(Cuestionario, pk=int(cid))
    sp = ScoringProfile.objects.create(
        cuestionario=cu,
        nombre=nombre,
        algoritmo="AVG" if algoritmo == "AVG" else "SUM",
        activo=True,
    )
    return JsonResponse({"ok": True, "id": sp.id})



@login_required
@user_passes_test(_is_app_admin)
@require_GET
def api_scoring_catalog(request):
    """
    GET ?cuestionario_id=ID  -> devuelve preguntas y perfiles de ese cuestionario
    GET sin params          -> solo devuelve lista de cuestionarios public/activos
    """
    qid = request.GET.get('cuestionario_id')

    # 1) SIEMPRE: lista para el <select> (publicado + activo)
    cu_qs = (Cuestionario.objects
             .filter(estado='published', activo=True)
             .values('id', 'codigo', 'nombre')
             .order_by('codigo', 'id'))
    data = {
        'ok': True,
        'cuestionarios': list(cu_qs),
    }

    # 2) Si piden un cuestionario específico, añade preguntas + perfiles
    if qid:
        try:
            cu = Cuestionario.objects.get(pk=int(qid), estado='published', activo=True)
        except (ValueError, Cuestionario.DoesNotExist):
            return JsonResponse({'ok': False, 'error': 'Cuestionario no encontrado o no publicado'}, status=404)

        # Preguntas (solo metadatos que necesita el editor: orden/tipo)
        preguntas = list(
            cu.preguntas.order_by('orden', 'id')
            .values('id', 'orden', 'tipo_respuesta', 'texto')
        )

        # Perfiles (incluye algoritmo!)
        perfiles = list(
            cu.scoring_profiles
              .values('id', 'nombre', 'activo', 'algoritmo')
              .order_by('id')
        )

        data.update({
            'preguntas': preguntas,
            'perfiles': perfiles,
        })

    return JsonResponse(data)



@login_required
@user_passes_test(_is_app_admin)
@require_GET
def api_scoring_rules_list(request, profile_id):
    prof = get_object_or_404(ScoringProfile, pk=profile_id)

    qs = prof.rules.order_by('id')
    rules = []
    for i, r in enumerate(qs, start=1):
        rules.append({
            "id": r.id,
            "q_from": r.q_from,
            "q_to": r.q_to,
            "weight": r.weight,
            "num_map": r.num_map or {},
            "txt_map": r.txt_map or {},
            # compat con tu JS: "desc" existe en UI, pero en modelo es "descripcion"
            "desc": r.descripcion or "",
            # tu modelo no tiene "orden": devolvemos uno “visual”
            "orden": i,
            "include_tipos": getattr(r, "include_tipos", "*"),
        })

    return JsonResponse({"ok": True, "rules": rules})




@login_required
@user_passes_test(_is_app_admin)
@require_POST
def api_scoring_rule_upsert(request, profile_id):
    """
    Crea/actualiza una regla simple de rango alineada a tu modelo ScoringRule.
    Acepta (del frontend): {id?, q_from, q_to, weight, num_map?, txt_map?, desc?, include_tipos?}
    Ignora 'orden' si viene.
    """
    prof = get_object_or_404(ScoringProfile, pk=profile_id)

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "JSON inválido"}, status=400)

    rid = body.get("id", None)

    try:
        q_from = int(body.get("q_from"))
        q_to = int(body.get("q_to"))
        weight = float(body.get("weight") or 1.0)
    except Exception:
        return HttpResponseBadRequest("q_from, q_to y weight deben ser numéricos")

    if q_from > q_to:
        return HttpResponseBadRequest("q_from debe ser <= q_to")

    fields = {
        "q_from": q_from,
        "q_to": q_to,
        "weight": weight,
        "num_map": body.get("num_map") or {},
        "txt_map": body.get("txt_map") or {},
        "include_tipos": (body.get("include_tipos") or "*"),
        # compat: 'desc' del UI se guarda en 'descripcion' del modelo
        "descripcion": (body.get("desc") or body.get("descripcion") or ""),
    }

    if rid:
        ScoringRule.objects.filter(profile=prof, pk=rid).update(**fields)
        rule = ScoringRule.objects.get(profile=prof, pk=rid)
    else:
        rule = ScoringRule.objects.create(profile=prof, **fields)

    return JsonResponse({
        "ok": True,
        "rule": {
            "id": rule.id,
            "q_from": rule.q_from,
            "q_to": rule.q_to,
            "weight": rule.weight,
            "num_map": rule.num_map or {},
            "txt_map": rule.txt_map or {},
            "desc": rule.descripcion or "",
            "orden": 1,  # solo para no romper UI; el orden real lo da la lista
            "include_tipos": getattr(rule, "include_tipos", "*"),
        }
    })


@login_required
@user_passes_test(_is_app_admin)
@require_POST
def api_scoring_rule_delete(request, profile_id, rule_id):
    ScoringRule.objects.filter(profile_id=profile_id, pk=rule_id).delete()
    return JsonResponse({"ok": True})



# ---------- Preview / Aplicar ----------
@login_required
@user_passes_test(_is_app_admin)
@require_POST
def api_scoring_preview(request):
    """
    Acepta:
      - mode='AUTO'  -> usa auto-suma sin perfiles
      - mode='PROFILE' + profile_id -> usa tu lógica actual (si la mantienes)
    """
    try:
        body = json.loads(request.body.decode() or "{}")
    except Exception:
        body = request.POST

    sesion_id = int(body.get("sesion_id") or 0)
    mode = (body.get("mode") or "AUTO").upper()

    if not sesion_id:
        return JsonResponse({"ok": False, "error": "Sesión requerida"}, status=400)

    try:
        sesion = SesionEvaluacion.objects.select_related("cuestionario").get(pk=sesion_id)
    except SesionEvaluacion.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Sesión inexistente"}, status=404)

    if mode == "AUTO":
        total, breakdown = compute_auto_sum_for_session(sesion)
        return JsonResponse({"ok": True, "mode": "AUTO", "total": total, "breakdown": breakdown})

    # Si aún quisieras soportar “PROFILE”, deja tu bloque de perfiles aquí…
    return JsonResponse({"ok": False, "error": "Modo no soportado"}, status=400)


@login_required
@user_passes_test(_is_app_admin)
@require_POST
def api_scoring_apply(request):
    try:
        body = json.loads(request.body.decode() or "{}")
    except Exception:
        body = request.POST

    sesion_id = int(body.get("sesion_id") or 0)
    if not sesion_id:
        return JsonResponse({"ok": False, "error": "Sesión requerida"}, status=400)

    try:
        sesion = (SesionEvaluacion.objects
                  .select_related("cuestionario")
                  .get(pk=sesion_id))
    except SesionEvaluacion.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Sesión inexistente"}, status=404)

    total, breakdown = compute_auto_sum_for_session(sesion)

    # 👇 PERFIL AUTO por cuestionario (obligatorio porque CalificacionSesion.profile es requerido)
    auto_name = "Auto (SUM/AVG)"
    auto_alg = breakdown.get('scheme', {}).get('mode', 'SUM')  # para dejar rastro del modo vigente
    profile_auto, _ = ScoringProfile.objects.get_or_create(
        cuestionario=sesion.cuestionario,
        nombre=auto_name,
        defaults={'activo': True, 'algoritmo': auto_alg}
    )
    # Si ya existía, actualiza algoritmo por si cambió SUM/AVG en el esquema
    if profile_auto.algoritmo != auto_alg:
        profile_auto.algoritmo = auto_alg
        profile_auto.save(update_fields=['algoritmo'])

    # Guarda/actualiza calificación (cumple unique_together (sesion, profile))
    cal, _created = CalificacionSesion.objects.update_or_create(
        sesion=sesion,
        profile=profile_auto,
        defaults={
            "total": float(total),
            "detalle": {"mode": "AUTO", **breakdown},
        }
    )

    detail_url = reverse('dashboard:admin_calificacion_detalle', args=[cal.pk])
    return JsonResponse({"ok": True, "mode": "AUTO", "total": total, "id": cal.pk, "detail_url": detail_url})


@login_required
@user_passes_test(_is_app_admin)
def calificaciones_list(request):
    """
    Lista calificaciones autogeneradas (CalificacionSesion) con filtros simples.
    """
    qs = (CalificacionSesion.objects
          .select_related('sesion__cuestionario',
                          'sesion__estudiante__usuario',
                          'sesion__psicologo__usuario')
          .order_by('-sesion_id'))

    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(
            Q(sesion__cuestionario__codigo__icontains=q) |
            Q(sesion__cuestionario__nombre__icontains=q) |
            Q(sesion__estudiante__usuario__username__icontains=q) |
            Q(sesion__estudiante__nombre_completo__icontains=q)
        )

    cuest = request.GET.get('cuest')
    if cuest and cuest.isdigit():
        qs = qs.filter(sesion__cuestionario_id=int(cuest))

    # (Opcional) filtro por fecha fin
    f_ini = request.GET.get('fi')
    f_fin = request.GET.get('ff')
    if f_ini:
        qs = qs.filter(sesion__fecha_fin__date__gte=f_ini)
    if f_fin:
        qs = qs.filter(sesion__fecha_fin__date__lte=f_fin)

    # Paginación light
    page = int(request.GET.get('page', 1))
    page_size = 25
    start = (page-1)*page_size
    end = start + page_size

    rows = []
    for c in qs[start:end]:
        s = c.sesion
        est_u = getattr(s.estudiante, 'usuario', None)
        rows.append({
            'id': c.id,
            'sesion_id': s.id,
            'folio': getattr(s, 'folio', f"S{str(s.id).zfill(5)}"),
            'cuest_codigo': s.cuestionario.codigo,
            'cuest_nombre': s.cuestionario.nombre,
            'estudiante': (s.estudiante.nombre_completo or (est_u.get_full_name() if est_u else '') or (getattr(est_u, 'username', '') or '—')),
            'total': c.total,
            'fecha_fin': s.fecha_fin,
        })

    context = {
        'rows': rows,
        'page': page,
        'has_next': (qs.count() > end),
        'has_prev': (page > 1),
        'q': q,
        'cuest': cuest or '',
        'fi': f_ini or '',
        'ff': f_fin or '',
    }
    return render(request, 'dashboard/calificaciones_list.html', context)


@login_required
@user_passes_test(_is_app_admin)
def calificacion_detalle(request, pk):
    """
    Muestra el detalle de una calificación: total + desglose guardado.
    """
    cal = get_object_or_404(
        CalificacionSesion.objects.select_related(
            'sesion__cuestionario',
            'sesion__estudiante__usuario',
            'sesion__psicologo__usuario'
        ),
        pk=pk
    )
    return render(request, 'dashboard/calificacion_detalle.html', {'cal': cal})


@login_required
@user_passes_test(_is_app_admin)
def calificaciones_export_csv(request):
    """
    Export rápido de calificaciones (para reportes/libro).
    """
    qs = (CalificacionSesion.objects
          .select_related('sesion__cuestionario',
                          'sesion__estudiante__usuario')
          .order_by('sesion_id'))

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f"calificaciones_{localdate().isoformat()}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(['sesion_id','folio','cuestionario','codigo','estudiante','total','fecha_fin'])

    for c in qs:
        s = c.sesion
        est_u = getattr(s.estudiante, 'usuario', None)
        estudiante = (s.estudiante.nombre_completo or (est_u.get_full_name() if est_u else '') or (getattr(est_u, 'username', '') or '—'))
        writer.writerow([
            s.id,
            getattr(s, 'folio', f"S{str(s.id).zfill(5)}"),
            s.cuestionario.nombre,
            s.cuestionario.codigo,
            estudiante,
            f"{c.total:.4f}",
            s.fecha_fin.isoformat() if s.fecha_fin else ''
        ])
    return response


@login_required
@user_passes_test(_is_app_admin)
@require_GET
def api_scoring_quick_spec(request, cuestionario_id: int):
    """
    Previsualiza cómo quedaría la suma automática SOLO con ESCALA (Likert) y SI/NO
    a partir de la configuración actual (min/max) del cuestionario.
    """
    cu = get_object_or_404(Cuestionario, pk=cuestionario_id)

    preguntas = (cu.preguntas
                 .order_by('orden', 'id')
                 .all())

    items = []
    total_min = 0.0
    total_max = 0.0
    count = 0

    for p in preguntas:
        tipo = (p.tipo_respuesta or '').upper()
        if tipo not in ('ESCALA', 'SI_NO'):
            continue
        cfg = p.config or {}
        pmin = float(cfg.get('min', 0 if tipo == 'SI_NO' else 1))
        pmax = float(cfg.get('max', 1 if tipo == 'SI_NO' else 5))

        items.append({
            'id': p.id,
            'orden': getattr(p, 'orden', None),
            'tipo': tipo,
            'min': pmin,
            'max': pmax,
        })
        total_min += pmin
        total_max += pmax
        count += 1

    return JsonResponse({
        'ok': True,
        'cuestionario_id': cu.id,
        'codigo': cu.codigo,
        'items_sumables': count,
        'total_min': total_min,
        'total_max': total_max,
        'items': items,  # por si quieres ver detalle
        'nota': 'Solo se incluyen preguntas ESCALA (Likert) y SI/NO.',
    })


@login_required
@user_passes_test(_is_app_admin)
def toggle_activo_cuestionario(request, pk: int):
    cu = get_object_or_404(Cuestionario, pk=pk)
    cu.activo = not bool(cu.activo)
    cu.save(update_fields=['activo'])
    messages.success(request, f"Cuestionario [{cu.codigo}] ahora activo={cu.activo}.")
    return redirect('dashboard:admin_panel')


# --- Catálogo público para PSICÓLOGO (solo published + activo) ---
@login_required
@user_passes_test(_is_psych)
@require_GET
def api_psico_catalogo_publico(request):
    qs = (
        Cuestionario.objects
        .filter(estado='published', activo=True)
        .annotate(total_preguntas=Count('preguntas'))
        .order_by('codigo', 'version')
    )
    results = [{
        "id": c.id,
        "codigo": c.codigo,
        "nombre": c.nombre,
        "version": str(c.version),
        "preguntas": int(getattr(c, "total_preguntas", 0) or 0),
        "updated": c.fecha_publicacion.isoformat() if c.fecha_publicacion else None,
        "estado": "published",
    } for c in qs]
    return JsonResponse({"ok": True, "results": results})


# --- Detalle público (solo lectura) para PSICÓLOGO ---
@login_required
@user_passes_test(_is_psych)
def psico_cuestionario_ver(request, pk: int):
    """
    Muestra el contenido (preguntas y opciones) de un cuestionario PUBLICADO y ACTIVO.
    Sin formulario: SOLO LECTURA para psicólogos.
    """
    cu = get_object_or_404(
        Cuestionario.objects.filter(estado='published', activo=True)
                            .prefetch_related('preguntas__opciones'),
        pk=pk
    )
    preguntas = cu.preguntas.all().order_by('orden', 'id')
    return render(request, 'dashboard/psico_cuestionario_publico.html', {
        'cuestionario': cu,
        'preguntas': preguntas,
    })




@login_required
def sociodemo_form(request):
    """
    - ESTUDIANTE: edita/crea su propia encuesta.
    - PSICOLOGO: solo lectura de un estudiante asignado (opcional).
    """
    me = request.user.perfil

    # ====== Caso 1: estudiante (la llena él mismo) ======
    if _is_student(request.user):
        obj = EncuestaSociodemografica.objects.filter(estudiante=me).first()

        if request.method == "POST":
            form = EncuestaSociodemograficaForm(request.POST, instance=obj)
            if form.is_valid():
                saved = form.save(commit=False)
                saved.estudiante = me
                saved.save()
                messages.success(request, "Encuesta sociodemográfica guardada.")
                return redirect("dashboard:dashboard")  # o a donde quieras
        else:
            # IMPORTANTE: en GET NO guardamos nada
            form = EncuestaSociodemograficaForm(instance=obj)

        return render(request, "dashboard/sociodemo_form.html", {
            "form": form,
            "modo": "edit",
        })

    # ====== Caso 2: psicólogo (solo lectura) ======
    if _is_psych(request.user):
        # Espera ?estudiante=<id>
        est_id = request.GET.get("estudiante")
        if not est_id:
            return HttpResponseForbidden("Falta estudiante.")

        # Aquí ajusta según tu modelo de asignación real.
        # Si tienes SesionEvaluacion con psicologo=me, valida que exista al menos 1 sesión asignada.
        # (Te dejo un ejemplo genérico, cámbialo si tu lógica difiere.)
        from forms.models import Perfil
        estudiante = get_object_or_404(Perfil, pk=est_id)

        # Render solo lectura
        obj = EncuestaSociodemografica.objects.filter(estudiante=estudiante).first()
        return render(request, "dashboard/sociodemo_form.html", {
            "obj": obj,
            "modo": "read",
            "estudiante": estudiante,
        })

    return HttpResponseForbidden("No autorizado.")



@login_required
def mi_cuenta(request):
    perfil, _ = Perfil.objects.get_or_create(usuario=request.user)

    if request.method == "POST":
        print("POST:", request.POST)
        form = PerfilForm(request.POST, instance=perfil)
        if form.is_valid():
            form.save()
            messages.success(request, "Datos actualizados correctamente.")
            return redirect("dashboard:mi_cuenta")
    else:
        form = PerfilForm(instance=perfil)

    return render(request, "dashboard/mi_cuenta.html", {
        "form": form,
        "perfil": perfil,
    })

