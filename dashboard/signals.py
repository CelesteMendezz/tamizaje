# dashboard/signals.py
from django.db.models.signals import post_migrate
from django.dispatch import receiver
from django.contrib.auth.models import Group  # (Permission si lo usas)

ROLE_NAMES = ["ADMIN", "PSICOLOGO", "ESTUDIANTE"]

@receiver(post_migrate)
def create_default_roles(sender, **kwargs):
    """
    Se ejecuta después de aplicar migraciones.
    Es seguro tocar la BD aquí y es idempotente.
    """
    for name in ROLE_NAMES:
        Group.objects.get_or_create(name=name)

    # (Opcional) dar permisos a ADMIN
    # from django.contrib.auth.models import Permission
    # admin_group, _ = Group.objects.get_or_create(name="ADMIN")
    # admin_group.permissions.set(Permission.objects.all())
