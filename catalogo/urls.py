from django.urls import path
from . import views

app_name = "catalogo"

urlpatterns = [
    path('cuestionarios/', views.cuestionario_list, name='cuestionario_list'),
    path('cuestionarios/nuevo/', views.cuestionario_create, name='cuestionario_create'),
    path('cuestionarios/<int:pk>/editar/', views.cuestionario_update, name='cuestionario_update'),
    path('cuestionarios/<int:pk>/eliminar/', views.cuestionario_delete, name='cuestionario_delete'),

    path('preguntas/<int:pk>/opciones/', views.pregunta_opciones, name='pregunta_opciones'),

    path('api/cuestionarios/', views.api_cuestionarios, name='api_cuestionarios'),
    path('api/cuestionarios/<int:pk>/', views.api_cuestionario_detalle, name='api_cuestionario_detalle'),
    path('api/cuestionarios/<int:pk>/duplicar/', views.api_cuestionario_duplicar, name='api_cuestionario_duplicar'),

    path('cuestionarios/importar/', views.cuestionario_import, name='cuestionario_import'),

    path('propuestas/<int:pk>/', views.propuesta_revisar, name='propuesta_revisar'),
    path('propuestas/<int:pk>/aprobar/', views.propuesta_aprobar, name='propuesta_aprobar'),
    path('propuestas/<int:pk>/rechazar/', views.propuesta_rechazar, name='propuesta_rechazar'),
    path('propuestas/<int:pk>/aprobar-desde-rechazadas/', views.propuesta_aprobar_desde_rechazadas, name='propuesta_aprobar_desde_rechazadas'),
    path('propuestas/<int:pk>/pendiente/', views.propuesta_marcar_pendiente, name='propuesta_pendiente'),
]
