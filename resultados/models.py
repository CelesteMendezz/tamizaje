# resultados/models.py
from django.db import models
from django.utils import timezone


class PrediccionRiesgo(models.Model):
    estudiante = models.OneToOneField(
        'forms.Perfil',
        on_delete=models.CASCADE,
        related_name='prediccion_riesgo'
    )

    features = models.JSONField(default=dict, blank=True)

    probabilidad = models.FloatField(null=True, blank=True)  # 0..1

    nivel = models.CharField(
        max_length=20,
        default='SIN_DATOS',
        choices=[
            ('SIN_DATOS', 'Sin datos'),
            ('BAJO', 'Bajo'),
            ('MODERADO', 'Moderado'),  # lo dejamos para no romper
            ('ALTO', 'Alto'),
        ]
    )

    modelo_version = models.CharField(max_length=40, default='rl_v1')
    actualizado = models.DateTimeField(auto_now=True)  # ✅ mejor

    def __str__(self):
        return f"{self.estudiante_id} {self.nivel} ({self.probabilidad})"

