
from django.contrib import admin
from .models import InviteKey

@admin.register(InviteKey)
class InviteKeyAdmin(admin.ModelAdmin):
    list_display = ("token", "rol", "used_count", "max_uses", "expires_at", "revoked", "created_at")
    search_fields = ("token", "rol")
    list_filter = ("rol", "revoked")
