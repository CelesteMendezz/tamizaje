from django.db import models
from forms.models import Perfil

# Create your models here.
from django.core.validators import MinValueValidator, MaxValueValidator

class EncuestaSociodemografica(models.Model):
    estudiante = models.OneToOneField(
        Perfil,
        on_delete=models.CASCADE,
        related_name="sociodemo"
    )

    municipio = models.CharField(max_length=120)

    edad = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(10), MaxValueValidator(99)]
    )

    SEXO_CHOICES = [
        ("M", "M"),
        ("F", "F"),
    ]
    sexo = models.CharField(max_length=1, choices=SEXO_CHOICES)

    TIENE_PAREJA_CHOICES = [("SI", "Sí"), ("NO", "No")]
    tiene_pareja = models.CharField(max_length=2, choices=TIENE_PAREJA_CHOICES)

    # si no tiene pareja, lo guardamos como 0 o null (más limpio null)
    tiempo_relacion_meses = models.PositiveSmallIntegerField(null=True, blank=True)

    TIPO_REL_CHOICES = [
        ("NOVIO", "Novio(a)"),
        ("ESPOSO", "Esposo(a)"),
        ("UNION_LIBRE", "Unión libre"),
        ("FREE", "Free"),
        ("AMIGOVIO", "Amigovio"),
        ("OTRO", "Otro"),
    ]
    tipo_relacion = models.CharField(max_length=20, choices=TIPO_REL_CHOICES, null=True, blank=True)
    tipo_relacion_otro = models.CharField(max_length=80, null=True, blank=True)

    tiene_hijos = models.CharField(max_length=2, choices=TIENE_PAREJA_CHOICES)
    cuantos_hijos = models.PositiveSmallIntegerField(null=True, blank=True)

    VIVE_CON_CHOICES = [
        ("PADRES", "Padres"),
        ("PAREJA", "Pareja"),
        ("PAREJA_HIJOS", "Pareja e hijos"),
        ("AMIGOS", "Amigos"),
        ("SOLO", "Solo"),
        ("OTRO", "Otro"),
    ]
    vive_semana = models.CharField(max_length=20, choices=VIVE_CON_CHOICES)
    vive_semana_otro = models.CharField(max_length=80, null=True, blank=True)

    vive_fin = models.CharField(max_length=20, choices=VIVE_CON_CHOICES)
    vive_fin_otro = models.CharField(max_length=80, null=True, blank=True)

    ESTADO_CIVIL_PADRES_CHOICES = [
        ("CASADOS", "Casados"),
        ("DIVORCIADOS", "Divorciados"),
        ("UNION_LIBRE", "Unión libre"),
        ("SEPARADOS", "Separados"),
        ("OTRO", "Otro"),
    ]
    estado_civil_padres = models.CharField(max_length=20, choices=ESTADO_CIVIL_PADRES_CHOICES)
    estado_civil_padres_otro = models.CharField(max_length=80, null=True, blank=True)

    escolaridad_padre = models.CharField(max_length=120)
    escolaridad_madre = models.CharField(max_length=120)

    ocupacion_padre = models.CharField(max_length=120)
    ocupacion_madre = models.CharField(max_length=120)

    trabaja_actualmente = models.CharField(max_length=2, choices=TIENE_PAREJA_CHOICES)
    depende_de = models.CharField(max_length=180, null=True, blank=True)

    padece_enfermedad = models.CharField(max_length=180, blank=True, default="")

    correo_opcional = models.EmailField(null=True, blank=True)

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Sociodemo de {self.estudiante}"