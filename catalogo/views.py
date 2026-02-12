# catalogo/views.py
import json
import re
import ast
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods
from forms.models import Cuestionario, Pregunta, Opcion
from .forms import CuestionarioForm, PreguntaFormSet, OpcionFormSet, ImportJSONForm


# ==========================================================
# Helpers permisos
# ==========================================================
def _is_app_admin(u):
    rol = (getattr(u, "rol", "") or "").upper()
    return u.is_authenticated and (u.is_superuser or rol == "ADMIN")


def _is_psych(u):
    return u.is_authenticated and (getattr(u, "rol", "") or "").upper() == "PSICOLOGO"


# ==========================================================
# Helpers compat campos (para NO romper si faltan)
# ==========================================================
def _has_field(model_cls, field_name: str) -> bool:
    return any(f.name == field_name for f in model_cls._meta.get_fields())


HAS_FECHA_PUB = _has_field(Cuestionario, "fecha_publicacion")
HAS_ALGORITMO = _has_field(Cuestionario, "algoritmo")


def _set_if_has(obj, field: str, value):
    if hasattr(obj, field):
        setattr(obj, field, value)


def _c_to_dict(c: Cuestionario):
    # items: si viene annotate total_preguntas úsalo, si no cuenta
    items = getattr(c, "total_preguntas", None)
    if items is None:
        try:
            items = c.preguntas.count()
        except Exception:
            items = 0

    updated = None
    if HAS_FECHA_PUB:
        fp = getattr(c, "fecha_publicacion", None)
        updated = fp.isoformat() if fp else None

    payload = {
        "id": c.id,
        "codigo": c.codigo,
        "nombre": c.nombre,
        "version": c.version,
        "estado": c.estado,
        "items": items,
        "updated": updated,
        "activo": bool(getattr(c, "activo", False)),
    }

    if HAS_ALGORITMO:
        payload["algoritmo"] = getattr(c, "algoritmo", None)

    return payload


# ==========================================================
# Vistas HTML (Wizard catálogo)
# ==========================================================
@login_required
@user_passes_test(_is_app_admin)
def cuestionario_list(request):
    only_fields = ["id", "codigo", "nombre", "version", "estado", "activo"]
    if HAS_FECHA_PUB:
        only_fields.append("fecha_publicacion")
    if HAS_ALGORITMO:
        only_fields.append("algoritmo")

    qs = (
        Cuestionario.objects
        .exclude(estado="EN_REVISION")
        .only(*only_fields)
        .annotate(total_preguntas=Count("preguntas"))
        .order_by("-id", "codigo")
    )
    # Si existe fecha_publicacion, ordena por fecha más estable
    if HAS_FECHA_PUB:
        qs = qs.order_by("-fecha_publicacion", "codigo", "id")

    return render(request, "catalogo/cuestionario_list.html", {"items": qs})


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["GET", "POST"])
@transaction.atomic
def cuestionario_create(request):
    """
    Paso 1 (meta). Crea el cuestionario y redirige al Paso 2 (preguntas).
    """
    if request.method == "POST":
        form = CuestionarioForm(request.POST)
        if form.is_valid():
            c = form.save()
            messages.success(request, "Cuestionario creado. Ahora agrega las preguntas.")
            url = reverse("catalogo:cuestionario_update", kwargs={"pk": c.pk})
            return redirect(f"{url}?step=preguntas")
        messages.error(request, "Revisa los errores del formulario.")
    else:
        form = CuestionarioForm()

    return render(request, "catalogo/cuestionario_form.html", {
        "form": form,
        "is_new": True,
        "step": "meta",
    })


@login_required
@user_passes_test(lambda u: _is_app_admin(u) or _is_psych(u))
@require_http_methods(["GET", "POST"])
@transaction.atomic
def cuestionario_update(request, pk):
    """
    Wizard:
      - admin: puede editar todo
      - psicólogo: solo puede entrar si estado=EN_REVISION
    """
    cuestionario = get_object_or_404(Cuestionario, pk=pk)

    es_admin = _is_app_admin(request.user)
    es_psicologo_con_permiso = _is_psych(request.user) and cuestionario.estado == "EN_REVISION"

    if not es_admin and not es_psicologo_con_permiso:
        messages.error(request, "No tienes permiso para editar este cuestionario.")
        return redirect("dashboard:redirect_after_login")

    step = request.GET.get("step") or "meta"

    # helpers
    def _get_cfg():
        return getattr(cuestionario, "config", None) or {}

    def _save_scoring_json(raw_scoring: str):
        raw_scoring = (raw_scoring or "").strip()
        if not raw_scoring:
            return True

        scoring_obj = None

        # 1) JSON válido
        try:
            scoring_obj = json.loads(raw_scoring)
        except Exception:
            scoring_obj = None

        # 2) dict estilo Python con comillas simples
        if scoring_obj is None:
            try:
                scoring_obj = ast.literal_eval(raw_scoring)
            except Exception:
                scoring_obj = None

        if not isinstance(scoring_obj, dict):
            messages.error(request, "El JSON de scoring es inválido. Usa JSON real con comillas dobles en keys.")
            return False

        cfg = _get_cfg()
        cfg["scoring"] = scoring_obj
        cuestionario.config = cfg
        cuestionario.save(update_fields=["config"])
        return True

    def _subscale_options_from_cfg():
        cfg = _get_cfg()
        scoring = cfg.get("scoring", {}) if isinstance(cfg, dict) else {}
        subscales = scoring.get("subscales", {}) if isinstance(scoring, dict) else {}
        if isinstance(subscales, dict):
            return sorted(subscales.keys())
        return []

    # --------------------------
    # Step META
    # --------------------------
    if step == "meta":
        if request.method == "POST":
            form = CuestionarioForm(request.POST, instance=cuestionario)
            if form.is_valid():
                obj = form.save(commit=False)

                # guardar scoring desde textarea (si viene)
                raw_scoring = request.POST.get("config_scoring_json", "")
                if raw_scoring.strip():
                    try:
                        cfg = getattr(obj, "config", None) or {}
                        scoring_obj = json.loads(raw_scoring)
                        if isinstance(scoring_obj, dict):
                            cfg["scoring"] = scoring_obj
                            _set_if_has(obj, "config", cfg)
                        else:
                            messages.error(request, "Scoring debe ser un objeto JSON (dict).")
                            return render(request, "catalogo/cuestionario_form.html", {
                                "form": form,
                                "object": cuestionario,
                                "step": "meta",
                            })
                    except Exception as e:
                        messages.error(request, f"El JSON de scoring es inválido: {e}")
                        return render(request, "catalogo/cuestionario_form.html", {
                            "form": form,
                            "object": cuestionario,
                            "step": "meta",
                        })

                # Publicar (solo admin)
                if "_publish" in request.POST and es_admin:
                    obj.estado = "published"
                    obj.activo = True
                    if hasattr(obj, "fecha_publicacion"):
                        obj.fecha_publicacion = timezone.now()
                    obj.save()
                    messages.success(request, f"El cuestionario '{obj.codigo}' ha sido aprobado y publicado.")
                    return redirect("dashboard:admin_panel")

                obj.save()
                messages.success(request, "Datos del cuestionario guardados.")
                url = reverse("catalogo:cuestionario_update", kwargs={"pk": pk})
                return redirect(f"{url}?step=preguntas")

            messages.error(request, "Revisa los errores del formulario.")
        else:
            form = CuestionarioForm(instance=cuestionario)

        return render(request, "catalogo/cuestionario_form.html", {
            "form": form,
            "object": cuestionario,
            "step": "meta",
        })

    # --------------------------
    # Step PREGUNTAS
    # --------------------------
    subscale_options = _subscale_options_from_cfg()

    if request.method == "POST":
        formset = PreguntaFormSet(request.POST, instance=cuestionario, prefix="pregs")

        if formset.is_valid():
            formset.save()

            # guardar scoring JSON del textarea
            _save_scoring_json(request.POST.get("config_scoring_json", ""))

            messages.success(request, "Preguntas y esquema de calificación guardados.")
            url = reverse("catalogo:cuestionario_update", kwargs={"pk": pk})
            return redirect(f"{url}?step=preguntas")

        messages.error(request, "Revisa los errores en las preguntas.")
    else:
        formset = PreguntaFormSet(instance=cuestionario, prefix="pregs")

    return render(request, "catalogo/cuestionario_form.html", {
        "formset": formset,
        "object": cuestionario,
        "step": "preguntas",
        "subscale_options": subscale_options,  # ✅ para dropdown
    })


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["GET", "POST"])
@transaction.atomic
def cuestionario_delete(request, pk):
    obj = get_object_or_404(Cuestionario, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.success(request, "Cuestionario eliminado.")
        return redirect("catalogo:cuestionario_list")
    return render(request, "catalogo/confirm_delete.html", {"obj": obj})


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["GET", "POST"])
@transaction.atomic
def pregunta_opciones(request, pk):
    pregunta = get_object_or_404(Pregunta, pk=pk)

    # Tipos que suelen requerir opciones (compat viejo/nuevo)
    tipo = (getattr(pregunta, "tipo_respuesta", "") or "").upper()
    tipos_con_opciones = {"OPCION", "OPCION_UNICA", "OPCION_MULTIPLE"}

    if tipo not in tipos_con_opciones:
        messages.error(request, "Esta pregunta no admite opciones.")
        return redirect("catalogo:cuestionario_update", pk=pregunta.cuestionario_id)

    if request.method == "POST":
        formset = OpcionFormSet(request.POST, instance=pregunta, prefix="ops")
        if formset.is_valid():
            formset.save()
            messages.success(request, "Opciones actualizadas.")
            return redirect("catalogo:cuestionario_update", pk=pregunta.cuestionario_id)
        messages.error(request, "Revisa los errores en las opciones.")
    else:
        formset = OpcionFormSet(instance=pregunta, prefix="ops")

    return render(request, "catalogo/pregunta_opciones.html", {
        "pregunta": pregunta,
        "formset": formset,
    })


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["GET", "POST"])
@transaction.atomic
def cuestionario_import(request):
    if request.method == "POST":
        form = ImportJSONForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Formulario inválido.")
            return render(request, "catalogo/cuestionario_import.html", {"form": form})

        try:
            data = json.load(request.FILES["archivo"])
        except Exception as e:
            messages.error(request, f"JSON inválido: {e}")
            return render(request, "catalogo/cuestionario_import.html", {"form": form})

        codigo = (data.get("codigo") or "").strip().upper()
        if not codigo:
            messages.error(request, 'El JSON debe incluir "codigo".')
            return render(request, "catalogo/cuestionario_import.html", {"form": form})

        if Cuestionario.objects.filter(codigo=codigo).exists():
            messages.error(request, f'Ya existe un cuestionario con código "{codigo}".')
            return render(request, "catalogo/cuestionario_import.html", {"form": form})

        # fecha_publicacion opcional
        dt_publicacion = None
        if HAS_FECHA_PUB:
            fp = data.get("fecha_publicacion")
            dt_publicacion = parse_datetime(fp) if isinstance(fp, str) else fp
            if dt_publicacion is None:
                dt_publicacion = timezone.now()

        c = Cuestionario.objects.create(
            codigo=codigo,
            nombre=data.get("nombre", ""),
            descripcion=data.get("descripcion", ""),
            version=data.get("version", "1.0"),
            activo=bool(data.get("activo", True)),
            estado=data.get("estado", "draft"),
            auto_sumar_likert=bool(data.get("auto_sumar_likert", True)),
        )
        if HAS_FECHA_PUB:
            c.fecha_publicacion = dt_publicacion
        if HAS_ALGORITMO and "algoritmo" in data:
            c.algoritmo = data.get("algoritmo") or "SUM"

        # config opcional
        if "config" in data and hasattr(c, "config"):
            c.config = data.get("config") or {}

        c.save()

        # preguntas
        for p in data.get("preguntas", []):
            pr = Pregunta.objects.create(
                cuestionario=c,
                texto=p.get("texto", ""),
                tipo_respuesta=p.get("tipo_respuesta", "OPCION"),
                orden=p.get("orden", 1),
                codigo=p.get("codigo", ""),
                requerido=bool(p.get("requerido", False)),
                config=p.get("config") or {},
            )
            # opciones si aplica
            if (pr.tipo_respuesta or "").upper() in ("OPCION", "OPCION_UNICA", "OPCION_MULTIPLE"):
                for op in p.get("opciones", []):
                    Opcion.objects.create(
                        pregunta=pr,
                        texto=op.get("texto", ""),
                        valor=op.get("valor", 0),
                    )

        messages.success(request, f"Cuestionario '{c.codigo}' importado.")
        url = reverse("catalogo:cuestionario_update", kwargs={"pk": c.pk})
        return redirect(f"{url}?step=preguntas")

    form = ImportJSONForm()
    return render(request, "catalogo/cuestionario_import.html", {"form": form})


# ==========================================================
# API catálogo (admin.html)
# ==========================================================
@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["GET", "POST"])
def api_cuestionarios(request):
    """
    GET  -> lista SOLO 'draft' y 'published' (catálogo admin)
    POST -> crea cabecera
    """
    if request.method == "GET":
        qs = (
            Cuestionario.objects
            .filter(estado__in=["draft", "published"])
            .annotate(total_preguntas=Count("preguntas"))
            .order_by("codigo", "version", "id")
        )

        results = []
        for c in qs:
            item = {
                "id": c.id,
                "codigo": c.codigo,
                "nombre": c.nombre,
                "version": c.version,
                "estado": c.estado,
                "items": int(getattr(c, "total_preguntas", 0) or 0),
                "activo": bool(getattr(c, "activo", False)),
                "updated": None,
            }
            if HAS_FECHA_PUB:
                fp = getattr(c, "fecha_publicacion", None)
                item["updated"] = fp.isoformat() if fp else None
            if HAS_ALGORITMO:
                item["algoritmo"] = getattr(c, "algoritmo", None)
            results.append(item)

        return JsonResponse({"ok": True, "results": results})

    # POST
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"JSON inválido: {e}"}, status=400)

    codigo = re.sub(r"[^A-Z0-9\-]+", "", str(payload.get("codigo", "")).strip().upper())
    if not codigo:
        return JsonResponse({"ok": False, "error": "El campo 'codigo' es obligatorio."}, status=400)

    if Cuestionario.objects.filter(codigo=codigo).exists():
        return JsonResponse({"ok": False, "error": "Ya existe un cuestionario con ese código."}, status=400)

    nombre = str(payload.get("nombre", "")).strip()
    version = str(payload.get("version") or "1.0").strip()
    descripcion = payload.get("descripcion") or ""
    activo = bool(payload.get("activo", False))

    estado_req = str(payload.get("estado") or "draft").strip()
    estado = estado_req if estado_req in ("draft", "published") else "draft"

    c = Cuestionario.objects.create(
        codigo=codigo,
        nombre=nombre,
        version=version,
        estado=estado,
        activo=activo,
        descripcion=descripcion,
        auto_sumar_likert=bool(payload.get("auto_sumar_likert", True)),
    )
    if HAS_FECHA_PUB:
        c.fecha_publicacion = timezone.now()
    if HAS_ALGORITMO and "algoritmo" in payload:
        c.algoritmo = payload.get("algoritmo") or "SUM"
    if hasattr(c, "config") and "config" in payload:
        c.config = payload.get("config") or {}

    c.save()
    return JsonResponse({"ok": True, "id": c.id})


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["GET", "PATCH", "DELETE"])
def api_cuestionario_detalle(request, pk):
    c = get_object_or_404(Cuestionario, pk=pk)

    if request.method == "GET":
        return JsonResponse({"ok": True, "item": _c_to_dict(c)})

    if request.method == "PATCH":
        try:
            payload = json.loads((request.body or b"{}").decode("utf-8"))
            if not isinstance(payload, dict):
                return JsonResponse(
                    {"ok": False, "error": "JSON inválido: debe ser un objeto (dict)."},
                    status=400
                )
        except Exception as e:
            return JsonResponse({"ok": False, "error": f"JSON inválido: {e}"}, status=400)

        # --------
        # Campos editables (texto)
        # --------
        for fld in ("codigo", "nombre", "version", "descripcion"):
            if fld in payload and payload[fld] is not None:
                setattr(c, fld, str(payload[fld]).strip())

        # Normalización opcional de código
        if "codigo" in payload and payload["codigo"] is not None:
            c.codigo = (c.codigo or "").strip().upper()

        # --------
        # Algoritmo
        # --------
        if HAS_ALGORITMO and "algoritmo" in payload:
            c.algoritmo = (str(payload["algoritmo"]).strip() or "SUM")

        # --------
        # Config (si existe)
        # --------
        if hasattr(c, "config") and "config" in payload:
            cfg = payload["config"] or {}
            # si llega string, intenta parsearlo
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg) if cfg.strip() else {}
                except Exception:
                    return JsonResponse({"ok": False, "error": "config debe ser JSON válido."}, status=400)
            if not isinstance(cfg, dict):
                return JsonResponse({"ok": False, "error": "config debe ser un objeto JSON (dict)."}, status=400)
            c.config = cfg

        # --------
        # Auto-sumar likert (si existe)
        # --------
        if hasattr(c, "auto_sumar_likert") and "auto_sumar_likert" in payload:
            c.auto_sumar_likert = bool(payload["auto_sumar_likert"])

        # --------
        # Estado + Activo (REGLA DE NEGOCIO)
        # --------
        estado_req = None
        if "estado" in payload and payload["estado"] is not None:
            estado_req = str(payload["estado"]).strip()
            if estado_req not in ("draft", "published"):
                return JsonResponse(
                    {"ok": False, "error": "estado inválido (solo 'draft' o 'published')."},
                    status=400
                )
            c.estado = estado_req

        # Si te mandan 'activo' manual, lo tomamos,
        # pero la regla de negocio manda cuando cambia estado.
        if "activo" in payload:
            c.activo = bool(payload["activo"])

        # Regla fuerte: published => activo True + fecha_publicacion; draft => activo False
        if estado_req == "published":
            c.activo = True
            if hasattr(c, "fecha_publicacion"):
                c.fecha_publicacion = timezone.now()
        elif estado_req == "draft":
            c.activo = False
            if hasattr(c, "fecha_publicacion"):
                c.fecha_publicacion = None

        # --------
        # Guardar (optimiza update_fields)
        # --------
        update_fields = ["codigo", "nombre", "version", "descripcion"]

        if HAS_ALGORITMO:
            update_fields.append("algoritmo")

        update_fields += ["estado", "activo"]

        if hasattr(c, "config"):
            update_fields.append("config")
        if hasattr(c, "auto_sumar_likert"):
            update_fields.append("auto_sumar_likert")
        if hasattr(c, "fecha_publicacion"):
            update_fields.append("fecha_publicacion")

        # Quita duplicados
        update_fields = list(dict.fromkeys(update_fields))

        c.save(update_fields=update_fields)
        return JsonResponse({"ok": True, "item": _c_to_dict(c)})

    # DELETE
    c.delete()
    return JsonResponse({"ok": True})


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["POST"])
def api_cuestionario_duplicar(request, pk):
    c = get_object_or_404(Cuestionario, pk=pk)

    base = c.codigo
    new_code = base
    i = 1
    while Cuestionario.objects.filter(codigo=new_code).exists():
        i += 1
        new_code = f"{base}-{i}"

    copia = Cuestionario.objects.create(
        codigo=new_code,
        nombre=f"{c.nombre} (copia)",
        version=c.version,
        estado="draft",
        descripcion=c.descripcion,
        activo=False,
        auto_sumar_likert=getattr(c, "auto_sumar_likert", True),
    )

    if HAS_FECHA_PUB:
        copia.fecha_publicacion = timezone.now()
    if HAS_ALGORITMO:
        copia.algoritmo = getattr(c, "algoritmo", "SUM")

    if hasattr(c, "config") and hasattr(copia, "config"):
        copia.config = c.config or {}

    copia.save()
    return JsonResponse({"ok": True, "id": copia.id})


# ==========================================================
# Flujo de propuestas (EN_REVISION -> APROBADA/RECHAZADA -> published)
# ==========================================================
@login_required
@user_passes_test(_is_app_admin)
def propuesta_revisar(request, pk):
    obj = get_object_or_404(Cuestionario, pk=pk, estado="EN_REVISION")
    return render(request, "catalogo/propuesta_revisar.html", {"obj": obj})


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["POST"])
def propuesta_aprobar(request, pk):
    c = get_object_or_404(Cuestionario, pk=pk, estado="EN_REVISION")
    c.estado = "APROBADA"
    c.activo = False
    if hasattr(c, "comentario_admin"):
        c.comentario_admin = (request.POST.get("comentario_admin") or "").strip()
    if HAS_FECHA_PUB:
        c.fecha_publicacion = None
        c.save(update_fields=["estado", "activo", "comentario_admin", "fecha_publicacion"] if hasattr(c, "comentario_admin") else ["estado", "activo", "fecha_publicacion"])
    else:
        c.save(update_fields=["estado", "activo", "comentario_admin"] if hasattr(c, "comentario_admin") else ["estado", "activo"])
    messages.success(request, "Propuesta aprobada (no publicada).")
    return redirect("dashboard:admin_panel")


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["POST"])
def propuesta_rechazar(request, pk):
    c = get_object_or_404(Cuestionario, pk=pk, estado__in=["EN_REVISION", "APROBADA"])
    c.estado = "RECHAZADA"
    c.activo = False
    if hasattr(c, "comentario_admin"):
        c.comentario_admin = (request.POST.get("comentario_admin") or "").strip()
        c.save(update_fields=["estado", "activo", "comentario_admin"])
    else:
        c.save(update_fields=["estado", "activo"])
    messages.info(request, "Propuesta rechazada.")
    return redirect("dashboard:admin_panel")


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["POST"])
def propuesta_marcar_pendiente(request, pk):
    c = get_object_or_404(Cuestionario, pk=pk, estado__in=["APROBADA", "RECHAZADA"])
    c.estado = "EN_REVISION"
    c.save(update_fields=["estado"])
    messages.success(request, "Propuesta regresada a 'En revisión'.")
    return redirect("dashboard:admin_panel")


@login_required
@user_passes_test(_is_app_admin)
@require_http_methods(["POST"])
def propuesta_aprobar_desde_rechazadas(request, pk):
    c = get_object_or_404(Cuestionario, pk=pk, estado="RECHAZADA")
    c.estado = "APROBADA"
    c.activo = False
    c.save(update_fields=["estado", "activo"])
    messages.success(request, "Propuesta aprobada (no publicada).")
    return redirect("dashboard:admin_panel")



@login_required
@user_passes_test(lambda u: _is_app_admin(u) or _is_psych(u))
def cuestionario_export_json(request, pk):
    c = get_object_or_404(Cuestionario, pk=pk)

    data = {
        "cuestionario": {
            "codigo": c.codigo,
            "nombre": c.nombre,
            "descripcion": c.descripcion,
            "version": c.version,
            "activo": c.activo,
            "estado": c.estado,
            "algoritmo": getattr(c, "algoritmo", None),
            "config": c.config or {},
        },
        "preguntas": []
    }

    qs = c.pregunta_set.all().order_by("orden")  # ajusta related_name si lo tienes
    for p in qs:
        data["preguntas"].append({
            "codigo": getattr(p, "codigo", None),
            "orden": p.orden,
            "texto": p.texto,
            "tipo_respuesta": p.tipo_respuesta,
            "requerido": getattr(p, "requerido", False),
            "ayuda": getattr(p, "ayuda", "") or "",
            "config": p.config or {},
        })

    resp = HttpResponse(json.dumps(data, ensure_ascii=False, indent=2), content_type="application/json")
    resp["Content-Disposition"] = f'attachment; filename="{c.codigo}.json"'
    return resp

#Luego lo ligas con URL y un botón “Exportar JSON”.