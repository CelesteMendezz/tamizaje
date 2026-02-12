from django import forms
from .models import Perfil


class PerfilForm(forms.ModelForm):
    class Meta:
        model = Perfil
        fields = [
            'nombre_completo',
            'rol',
            'telefono',
            'adscripcion',
            'matricula',
            'carrera',
            'semestre',
            'cedula_profesional',
        ]
        widgets = {
            'nombre_completo': forms.TextInput(attrs={'class': 'form-control'}),
            'rol': forms.Select(attrs={'class': 'form-control'}),
            'telefono': forms.TextInput(attrs={'class': 'form-control'}),
            'adscripcion': forms.Select(attrs={'class': 'form-control'}),
            'matricula': forms.TextInput(attrs={'class': 'form-control'}),
            'carrera': forms.TextInput(attrs={'class': 'form-control'}),
            'semestre': forms.Select(attrs={'class': 'form-control'}),
            'cedula_profesional': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def clean_matricula(self):
        matricula = self.cleaned_data.get("matricula")
        if matricula:
            qs = Perfil.objects.filter(matricula=matricula)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("Esta matrícula ya está registrada.")
        return matricula
    
    
