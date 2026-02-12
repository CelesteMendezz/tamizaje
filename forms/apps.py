#forms/apps.py

from django.apps import AppConfig



class FormsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "forms"     # ruta del módulo
    label = "forms"         # 🔴 app label visible para Django (migraciones/FKs)
    verbose_name = "Forms (Cuestionarios y Evaluación)"