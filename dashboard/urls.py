# dashboard/urls.py
from django.urls import path
from . import views
from . import views as v
from .views import mi_cuenta

app_name = "dashboard"

urlpatterns = [
    # Paneles
    path("", views.dashboard_usuario, name="dashboard"),
    path("admin/", views.dashboard_admin, name="admin_panel"),
    path("psico/", views.dashboard_psicologo, name="psico_panel"),
    path("redirect/", views.redirect_after_login, name="redirect_after_login"),

    # Admin APIs
    path("api/usuarios/", views.api_usuarios, name="api_usuarios"),
    path("api/usuarios/<int:pk>/", views.api_usuario_detalle, name="api_usuario_detalle"),
    path("api/usuarios/<int:pk>/reset-password/", views.api_usuario_reset_password, name="api_usuario_reset_password"),

    # Sesiones alumno
    path("sesiones/", views.sesion_evaluacion_list, name="sesion_list"),
    path("sesiones/<int:pk>/", views.sesion_evaluacion_detalle, name="sesion_detalle"),
    path("api/mis-sesiones/", views.api_mis_sesiones, name="api_mis_sesiones"),
    path("evaluacion/<int:cuestionario_id>/", views.responder_evaluacion, name="responder_evaluacion"),

    # Psicólogo (TRIAGE)
    path("api/psico/sesiones/", views.api_psico_sesiones, name="api_psico_sesiones"),
    path("api/psico/sesiones/<int:pk>/asignar-a-mi/", views.api_psico_asignar, name="api_psico_asignar"),
    path("psico/sesion/<int:pk>/", views.psico_sesion_detalle, name="psico_sesion_detalle"),

    # Catálogo público psicólogo (solo lectura)
    path("psico/api/catalogo/", views.api_psico_catalogo_publico, name="api_psico_catalogo_publico"),
    path("psico/catalogo/<int:pk>/", views.psico_cuestionario_ver, name="psico_cuestionario_ver"),

    # Admin scoring (lo tuyo)
    path('api/admin/sesiones/', views.api_admin_sesiones, name='api_admin_sesiones'),
    path('admin/sesiones/<int:pk>/', views.admin_sesion_cuestionario, name='admin_sesion_cuestionario'),

    path('api/scoring/catalog/', views.api_scoring_catalog, name='api_scoring_catalog'),
    path('api/scoring/profile/create/', views.api_scoring_profile_create, name='api_scoring_profile_create'),
    path('api/scoring/profile/<int:profile_id>/rules/', views.api_scoring_rules_list, name='api_scoring_rules_list'),
    path('api/scoring/profile/<int:profile_id>/rule/upsert/', views.api_scoring_rule_upsert, name='api_scoring_rule_upsert'),
    path('api/scoring/profile/<int:profile_id>/rule/<int:rule_id>/delete/', views.api_scoring_rule_delete, name='api_scoring_rule_delete'),
    path('api/scoring/preview/', views.api_scoring_preview, name='api_scoring_preview'),
    path('api/scoring/apply/', views.api_scoring_apply, name='api_scoring_apply'),
    path('api/scoring/quick-spec/<int:cuestionario_id>/', views.api_scoring_quick_spec, name='api_scoring_quick_spec'),

    path('admin/calificaciones/', v.calificaciones_list, name='admin_calificaciones'),
    path('admin/calificaciones/<int:pk>/', v.calificacion_detalle, name='admin_calificacion_detalle'),
    path('admin/calificaciones/export/csv/', v.calificaciones_export_csv, name='admin_calificaciones_export_csv'),
    path('admin/cuestionario/<int:pk>/toggle-activo/', views.toggle_activo_cuestionario, name='admin_toggle_activo'),

    path("sociodemo/", views.sociodemo_form, name="sociodemo_form"),


]
