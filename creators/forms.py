from django import forms
from .models import CreatorProfile

class ProfileForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['currency'].required = False

    def clean_currency(self):
        return self.cleaned_data.get('currency') or self.instance.currency or 'INR'

    class Meta:
        model = CreatorProfile
        fields = ['display_name', 'headline', 'bio', 'location', 'availability', 'available_from', 'price_min', 'price_max', 'currency', 'turnaround_days', 'experience_years', 'specializations', 'skills', 'tools']
        widgets = {'bio': forms.Textarea(attrs={'rows': 5}), 'skills': forms.CheckboxSelectMultiple(),
                   'tools': forms.CheckboxSelectMultiple(), 'specializations': forms.CheckboxSelectMultiple(), 'available_from': forms.DateInput(attrs={'type': 'date'})}
