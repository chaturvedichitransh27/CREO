from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import JobPosting


class JobPostingForm(forms.ModelForm):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)
    publish_consent = forms.BooleanField(required=False, label='I approve these project details for public display.',
        help_text='All fields in this form will be public after publication. Keep confidential information and personal contact details out of your posting. Account email addresses are never added.')

    class Meta:
        model = JobPosting
        fields = ['brand_name', 'title', 'description', 'work_type', 'skills', 'tools', 'specializations',
                  'style', 'content_type', 'aspect_ratio', 'deliverables', 'output_format', 'min_experience_years',
                  'budget_min', 'budget_max', 'currency', 'duration_days', 'application_deadline', 'deadline', 'additional_requirements']
        widgets = {**{name: forms.Textarea(attrs={'rows': 4}) for name in ['description', 'deliverables', 'additional_requirements']},
                   **{name: forms.CheckboxSelectMultiple() for name in ['skills', 'tools', 'specializations']},
                   **{name: forms.DateInput(attrs={'type': 'date'}) for name in ['application_deadline', 'deadline']}}
        labels = {'brand_name': 'Public brand name', 'work_type': 'Type of creative work', 'tools': 'Required tools / software',
                  'skills': 'Required skills', 'specializations': 'Preferred specializations', 'style': 'Creative style',
                  'content_type': 'Portfolio format preference (optional)', 'aspect_ratio': 'Aspect ratio (optional)',
                  'duration_days': 'Expected duration (days)', 'min_experience_years': 'Minimum experience (years, optional)'}

    def __init__(self, *args, publish=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.publish = publish
        self.public_edit = self.instance.status != JobPosting.Status.DRAFT
        self.fields['version'].initial = self.instance.version
        if publish or self.public_edit:
            self.fields['publish_consent'].required = True
            for name in ('brand_name', 'description', 'work_type', 'deliverables', 'output_format', 'budget_min',
                         'budget_max', 'duration_days', 'application_deadline', 'deadline'):
                self.fields[name].required = True

    def clean(self):
        data = super().clean()
        for name in ('application_deadline', 'deadline'):
            value = data.get(name)
            if value and value < timezone.localdate() and (self.publish or value != getattr(self.instance, name)):
                self.add_error(name, 'Choose today or a future date.')
        if self.instance.pk and data.get('version') != self.instance.version:
            raise ValidationError('This posting changed in another tab. Reload it before saving.')
        return data

    def groups(self):
        groups = [
            ('01 / The project', ['brand_name', 'title', 'description', 'work_type']),
            ('02 / The creative fit', ['skills', 'tools', 'specializations', 'style', 'min_experience_years']),
            ('03 / The output', ['deliverables', 'output_format', 'content_type', 'aspect_ratio']),
            ('04 / Budget & timing', ['budget_min', 'budget_max', 'currency', 'duration_days', 'application_deadline', 'deadline']),
            ('05 / Final details', ['additional_requirements', 'publish_consent']),
        ]
        return [(title, [self[name] for name in fields]) for title, fields in groups]


class PostingActionForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    action = forms.ChoiceField(choices=[('close', 'Close'), ('reopen', 'Reopen')])
