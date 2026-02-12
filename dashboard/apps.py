# dashboard/apps.py
from django.apps import AppConfig

class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard'

    def ready(self):
        # Solo registra señales. NO hagas consultas a la BD aquí.
        import dashboard.signals  # noqa: F401
