# forms/models.py
import json
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator
from django.conf import settings # 👈 Importa esto
from django.db.models.signals import post_save
from django.dispatch import receiver
# =====================================================
# USUARIOS / PERFILES
# =====================================================
class Usuario(AbstractUser):
    ROL_CHOICES = (('ADMIN','Admin'),('PSICOLOGO','Psicólogo'),('ESTUDIANTE','Estudiante'))
    rol = models.CharField(max_length=20, choices=ROL_CHOICES, default='ESTUDIANTE', blank=True)

    def save(self, *args, **kwargs):
        self.rol = (self.rol or 'ESTUDIANTE').upper()
        super().save(*args, **kwargs)


class Perfil(models.Model):

    ROL_CHOICES = (
        ('ESTUDIANTE', 'Estudiante'),
        ('PSICOLOGO', 'Psicólogo'),
    )

    ADSCRIPCION_CHOICES = (
        # Institutos
        ('ICSA', 'Instituto de Ciencias de la Salud'),
        ('ICBI', 'Instituto de Ciencias Básicas e Ingeniería'),
        ('ICSHU', 'Instituto de Ciencias Sociales y Humanidades'),
        ('ICEA', 'Instituto de Ciencias Económico Administrativas'),
        ('ICAP', 'Instituto de Ciencias Agropecuarias'),
        ('IA', 'Instituto de Artes'),

        # Escuelas Superiores
        ('ES_ACTOPAN', 'Escuela Superior de Actopan'),
        ('ES_ATOTONILCO', 'Escuela Superior de Atotonilco de Tula'),
        ('ES_SAHAGUN', 'Escuela Superior de Ciudad Sahagún'),
        ('ES_TEPEJI', 'Escuela Superior de Tepeji del Río'),
        ('ES_TIZAYUCA', 'Escuela Superior de Tizayuca'),
        ('ES_TLAHUELILPAN', 'Escuela Superior de Tlahuelilpan'),
        ('ES_ZIMAPAN', 'Escuela Superior de Zimapán'),
        ('ES_APAN', 'Escuela Superior de Apan'),
    )

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='perfil'
    )

    # Rol funcional (NO admin)
    rol = models.CharField(
        max_length=15,
        choices=ROL_CHOICES,
        default='ESTUDIANTE'
    )

    # Datos generales
    nombre_completo = models.CharField(max_length=100)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    sexo = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        choices=[('M', 'Masculino'), ('F', 'Femenino'), ('O', 'Otro')]
    )
    telefono = models.CharField(max_length=15, null=True, blank=True)

    # Adscripción UAEH (Instituto o Escuela)
    adscripcion = models.CharField(
        max_length=20,
        choices=ADSCRIPCION_CHOICES,
        null=True,
        blank=True
    )

    # Estudiantes
    matricula = models.CharField(
        max_length=10,
        null=True,
        blank=True,
    )
    carrera = models.CharField(max_length=100, null=True, blank=True)
    semestre = models.IntegerField(
        null=True,
        blank=True,
        choices=[(i, f"{i}° Semestre") for i in range(1, 13)]
    )

    # Psicólogos (opcional)
    cedula_profesional = models.CharField(
        max_length=50,
        null=True,
        blank=True
    )

    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre_completo or self.usuario.username


@receiver(post_save, sender=Usuario)
def ensure_perfil(sender, instance, created, **kwargs):
    if (instance.rol or '').upper() in ('PSICOLOGO','ESTUDIANTE'):
        Perfil.objects.get_or_create(usuario=instance)







# =====================================================
# CATÁLOGO DE CUESTIONARIOS
# =====================================================
class Cuestionario(models.Model):
    codigo = models.CharField(max_length=20)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    version = models.CharField(max_length=10, default='1.0')
    activo = models.BooleanField(default=True)
    auto_sumar_likert = models.BooleanField(default=True)  # <- nuevo
    # Archivos/comentario de propuesta (opcionales)
    archivo_propuesta = models.FileField(upload_to='propuestas/', null=True, blank=True)
    comentario_propuesta = models.TextField(blank=True, default='')
    comentario_admin = models.TextField(blank=True, default='')       # 👈 nuevo
    config = models.JSONField(default=dict, blank=True)  # 👈 NUEVO

    # Autor de la propuesta (opcional)
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='propuestas_creadas'
    )

    # 👇 ESTO VA DENTRO DE LA CLASE
    ESTADO_CHOICES = (
    ('draft', 'Borrador'),
    ('EN_REVISION', 'En revisión'),
    ('APROBADA', 'Aprobada'),     # ← tu término preferido
    ('ACEPTADA', 'Aceptada'),     # ← opcional para compat (puedes quitarlo luego)
    ('RECHAZADA', 'Rechazada'),
    ('published', 'Publicado'),
)

    estado = models.CharField(max_length=12, choices=ESTADO_CHOICES, default='draft')
    fecha_publicacion = models.DateTimeField(null=True, blank=True)

    # Algoritmos: mantener simple por ahora
    ALGORITMO_CHOICES = (
        ('SUM', 'Suma simple'),
        ('NONE', 'Sin algoritmo'),
    )
    algoritmo = models.CharField(max_length=20, choices=ALGORITMO_CHOICES, default='SUM')

    class Meta:
        unique_together = (('codigo', 'version'),)

    def __str__(self):
        return f"{self.codigo or 'SIN-COD'} v{self.version}"


class Pregunta(models.Model):
    class Tipos(models.TextChoices):
        TEXTO = "TEXTO", "Texto Abierto"
        NUMERICA = "NUMERICA", "Número"
        FECHA = "FECHA", "Fecha"
        SI_NO = "SI_NO", "Sí / No"
        OPCION_UNICA = "OPCION_UNICA", "Opción Única (Radio)"
        OPCION_MULTIPLE = "OPCION_MULTIPLE", "Opción Múltiple (Checkbox)"
        ESCALA = "ESCALA", "Escala Numérica (Likert)"

    cuestionario = models.ForeignKey('Cuestionario', on_delete=models.CASCADE, related_name='preguntas')
    texto = models.TextField()
    tipo_respuesta = models.CharField(max_length=20, choices=Tipos.choices, default=Tipos.OPCION_UNICA)
    orden = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    codigo = models.CharField(max_length=50, blank=True, default='')
    requerido = models.BooleanField(default=True)
    ayuda = models.CharField(max_length=255, blank=True)
    config = models.JSONField(default=dict, blank=True, help_text="Configuraciones JSON para ciertos tipos")

    class Meta:
        ordering = ['orden', 'id']
        constraints = [
            models.UniqueConstraint(fields=['cuestionario', 'orden'], name='uq_pregunta_orden_por_cuestionario')
        ]

    def __str__(self):
        base = self.codigo or f"item{self.orden}"
        return f"{self.cuestionario.codigo}:{base}"

    # ==========================
    # ✅ Helpers para Likert
    # ==========================



    
    @property
    def likert_labels_json(self) -> str:
        """
        Devuelve labels como JSON string.
        Ej: {"1":"Nada","2":"Poco",...}
        """
        cfg = self.config or {}
        labels = cfg.get("labels") or {}
        if not isinstance(labels, dict):
            labels = {}
        return json.dumps(labels, ensure_ascii=False)

    @property
    def likert_labels_spec(self) -> str:
        """
        Devuelve formato '1=Nada, 2=Poco, ...' (compatible con parsePerValue).
        """
        cfg = self.config or {}
        labels = cfg.get("labels") or {}
        if not isinstance(labels, dict):
            return ""

        def _knum(k):
            try:
                return int(k)
            except Exception:
                return 10**9

        parts = [f"{k}={labels[k]}" for k in sorted(labels.keys(), key=_knum)]
        return ", ".join(parts)
    
    

class Opcion(models.Model):
    pregunta = models.ForeignKey(Pregunta, on_delete=models.CASCADE, related_name='opciones')
    texto = models.CharField(max_length=255)
    # --- CAMBIO CLAVE: De DecimalField a CharField para permitir texto ---
    valor = models.CharField(max_length=100, help_text="Valor a guardar en la BD (ej: '1', 'H', 'M', 'totalmente_desacuerdo')")
    orden = models.PositiveIntegerField(default=1)
    es_otro = models.BooleanField(default=False, help_text="Marcar si esta opción habilita un campo de texto 'Otro'")
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ['orden', 'id']
        constraints = [models.UniqueConstraint(fields=['pregunta', 'orden'], name='uq_opcion_orden_por_pregunta')]

    def __str__(self):
        return f"{self.texto} ({self.valor})"



# =====================================================
# SESIONES / RESPUESTAS / REPORTES
# =====================================================
# =====================================================
# SESIONES / RESPUESTAS / REPORTES
# =====================================================
from django.db import models
from django.utils import timezone

class SesionEvaluacion(models.Model):
    ESTADO_CHOICES = (
        ('PENDIENTE', 'Pendiente'),
        ('EN_CURSO', 'En curso'),
        ('COMPLETADA', 'Completada'),
    )
    fecha_inicio = models.DateTimeField(auto_now_add=True)
    fecha_fin = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='PENDIENTE')

    cuestionario = models.ForeignKey('forms.Cuestionario', on_delete=models.CASCADE)
    estudiante = models.ForeignKey('forms.Perfil', on_delete=models.CASCADE, related_name='sesiones')

    # Psicólogo asignado (sin renombrar el campo)
    psicologo = models.ForeignKey(
        'forms.Perfil',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='sesiones_asignadas',      # nombre claro para el reverse
        limit_choices_to={'usuario__rol': 'PSICOLOGO'}
          # si tu Perfil tiene 'rol'
    )
    # NUEVO: cuándo se asignó
    fecha_asignacion = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Sesión {self.id} - {self.cuestionario.codigo} - {self.estudiante}"

    class Meta:
        indexes = [
            models.Index(fields=['psicologo', 'estado']),
            models.Index(fields=['estudiante', 'estado']),
        ]

    def puede_completarse(self):
        from django.db.models import Q

        total_requeridas = self.cuestionario.preguntas.filter(requerido=True).count()

        respondidas = self.respuestas.exclude(
            Q(opcion_seleccionada__isnull=True) &
            Q(valor_texto__isnull=True) &
            Q(valor_numerico__isnull=True) &
            Q(opciones_multiple=[])
        ).count()

        return respondidas >= total_requeridas

    # Helper opcional
    def asignar_a(self, perfil_psicologo):
        self.psicologo = perfil_psicologo
        self.fecha_asignacion = timezone.now()
        self.save(update_fields=['psicologo', 'fecha_asignacion'])



class Respuesta(models.Model):
    sesion = models.ForeignKey(SesionEvaluacion, on_delete=models.CASCADE, related_name='respuestas')
    pregunta = models.ForeignKey(Pregunta, on_delete=models.CASCADE)
    opcion_seleccionada = models.ForeignKey(Opcion, null=True, blank=True, on_delete=models.SET_NULL)
    valor_numerico = models.FloatField(null=True, blank=True)
    valor_texto = models.TextField(null=True, blank=True)
    opciones_multiple = models.JSONField(default=list, blank=True)  # para múltiples seleccionadas

    def __str__(self):
        return f"Resp({self.sesion_id}) {self.pregunta_id}"

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['sesion', 'pregunta'], name='uq_respuesta_por_sesion_pregunta'),
        ]


class ReporteEvaluacion(models.Model):
    sesion = models.OneToOneField(SesionEvaluacion, on_delete=models.CASCADE, related_name='reporte')
    fecha_generacion = models.DateTimeField(auto_now_add=True)
    resultado = models.JSONField()      # totales / subescalas / niveles
    interpretacion = models.TextField() # texto libre del psicólogo o sistema

    def __str__(self):
        return f"Reporte sesión {self.sesion_id}"


    

# --- IMPORTS de tus otros modelos arriba ---
from django.db import models
from django.core.validators import MinValueValidator

# ... (Cuestionario, Pregunta, Opcion, SesionEvaluacion, Respuesta, ReporteEvaluacion) ...

class ScoringProfile(models.Model):
    cuestionario = models.ForeignKey(Cuestionario, on_delete=models.CASCADE, related_name='scoring_profiles')
    nombre       = models.CharField(max_length=120)
    activo       = models.BooleanField(default=True)
    algoritmo    = models.CharField(max_length=10, choices=[('SUM','Suma'),('AVG','Promedio')], default='SUM')
    creado       = models.DateTimeField(auto_now_add=True)  # sin null=True

    class Meta:
        unique_together = [('cuestionario', 'nombre')]

    def __str__(self):
        return f"{self.cuestionario.codigo} · {self.nombre}"


class ScoringRule(models.Model):
    """Regla: rango de preguntas + mapeos + peso."""
    profile        = models.ForeignKey(ScoringProfile, on_delete=models.CASCADE, related_name='rules')
    q_from         = models.PositiveIntegerField(default=1)
    q_to           = models.PositiveIntegerField(default=1)
    weight         = models.FloatField(default=1.0)
    num_map        = models.JSONField(default=dict, blank=True)
    txt_map        = models.JSONField(default=dict, blank=True)  # si quieres permitir mapear texto
    include_tipos  = models.CharField(max_length=120, default="*")
    descripcion    = models.CharField(max_length=255, blank=True, default='')
    creado         = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f"[{self.q_from}-{self.q_to}] x{self.weight}"


class CalificacionSesion(models.Model):
    """Resultado de aplicar un perfil a una sesión."""
    # IMPORTANTE: PK normal (id). NO uses primary_key en 'sesion'
    sesion   = models.ForeignKey(SesionEvaluacion, on_delete=models.CASCADE, related_name='calificaciones')
    profile  = models.ForeignKey(ScoringProfile, on_delete=models.PROTECT, related_name='calificaciones')
    total    = models.FloatField(default=0.0)
    detalle  = models.JSONField(default=dict, blank=True)
    creado   = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        unique_together = [('sesion', 'profile')]

    def __str__(self):
        return f"Calif S{self.sesion_id} · {self.profile.nombre} = {self.total:.2f}"


