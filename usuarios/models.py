# usuarios/models.py
from django.db import models
from django.conf import settings
from django.utils import timezone
import uuid

User = settings.AUTH_USER_MODEL

def generate_token():
    return uuid.uuid4().hex   

class InviteKey(models.Model):
    TOKEN_LEN = 64
    token = models.CharField(max_length=TOKEN_LEN, unique=True, default=generate_token)
    rol = models.CharField(max_length=20, default='PSICOLOGO')
    max_uses = models.PositiveIntegerField(default=1)
    used_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='invites_created')
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        if self.revoked: return False
        if self.expires_at and timezone.now() > self.expires_at: return False
        return self.used_count < self.max_uses

    def __str__(self):
        return f"{self.rol} | {self.token[:8]}… (uses {self.used_count}/{self.max_uses})"
