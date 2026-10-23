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


class OpportunityFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=140, label='Search projects', widget=forms.TextInput(attrs={'placeholder': 'Project, brand or creative direction'}))
    work_type = forms.ChoiceField(required=False, choices=[('', 'Any creative work')] + list(JobPosting.WorkType.choices))
    skill = forms.ModelChoiceField(required=False, queryset=JobPosting._meta.get_field('skills').related_model.objects.all())
    tool = forms.ModelChoiceField(required=False, queryset=JobPosting._meta.get_field('tools').related_model.objects.all())
    currency = forms.ChoiceField(required=False, choices=[('', 'Any currency')] + list(JobPosting._meta.get_field('currency').choices))
    budget_min = forms.DecimalField(required=False, min_value=1, max_digits=10, decimal_places=2, label='Minimum budget ceiling')
    budget_max = forms.DecimalField(required=False, min_value=1, max_digits=10, decimal_places=2, label='Maximum budget ceiling')
    include_closed = forms.BooleanField(required=False, label='Include closed projects')

    def clean(self):
        data = super().clean()
        if (data.get('budget_min') or data.get('budget_max')) and not data.get('currency'):
            self.add_error('currency', 'Select a currency when filtering by budget.')
        if data.get('budget_min') and data.get('budget_max') and data['budget_max'] < data['budget_min']:
            self.add_error('budget_max', 'Maximum must be at least the minimum.')
        return data


class ContactConsentForm(forms.Form):
    creator_email = forms.EmailField(required=False, label='Professional email (optional)', help_text='Enter the address you choose to share. Your login email is never filled in automatically.')
    share_email = forms.BooleanField(required=False, label='Share this email with this brand only if it considers or shortlists me.')
    creator_phone = forms.RegexField(r'^\+?[0-9 ()\-]{7,30}$', required=False, max_length=30, label='Professional phone (optional)')
    share_phone = forms.BooleanField(required=False, label='Share this phone with this brand only if it considers or shortlists me.')
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput, initial=0)

    def clean(self):
        data = super().clean()
        for field, flag in [('creator_email', 'share_email'), ('creator_phone', 'share_phone')]:
            if data.get(flag) and not data.get(field):
                self.add_error(field, 'Enter the professional contact you authorize for sharing.')
            if not data.get(flag):
                data[field] = ''  # Do not retain unapproved personal details.
        return data


class ApplicationForm(ContactConsentForm):
    message = forms.CharField(required=False, max_length=3000, label='Your application message', widget=forms.Textarea(attrs={'rows': 4}), help_text='Describe your fit. Keep private contact details out of this message; use the consent controls below.')


class ConsideringForm(forms.Form):
    brand_email = forms.EmailField(label='Your professional contact email')
    brand_share_email = forms.BooleanField(label='Share this professional email with this applicant while they are considered or shortlisted.')
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class BrandContactForm(forms.Form):
    brand_email = forms.EmailField(required=False, label='Professional email')
    brand_share_email = forms.BooleanField(required=False, label='Share this email with this applicant while considered or shortlisted.')
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def clean(self):
        data = super().clean()
        if data.get('brand_share_email') and not data.get('brand_email'):
            self.add_error('brand_email', 'Enter the email you authorize for sharing.')
        if not data.get('brand_share_email'):
            data['brand_email'] = ''
        return data


class ApplicantFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=120, label='Creator name')
    status = forms.ChoiceField(required=False, choices=[('', 'All applications'), ('SUBMITTED', 'Submitted'), ('WITHDRAWN', 'Withdrawn')])
    review = forms.ChoiceField(required=False, choices=[('', 'All review states'), ('UNREVIEWED', 'Not reviewed'), ('CONSIDERING', 'Considering'), ('shortlisted', 'All shortlisted')])
    sort = forms.ChoiceField(required=False, choices=[('', 'Newest first'), ('oldest', 'Oldest first'), ('name', 'Creator name')])
