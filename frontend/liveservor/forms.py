from django import forms
from .models import Organization

class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ['name', 'logo', 'info_sup']
        widgets = {
            'info_sup': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Format JSON ou texte selon vos besoins'}),
        }