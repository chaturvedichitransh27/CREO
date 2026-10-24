from django import forms
from django.core.validators import URLValidator


class InvitationForm(forms.Form):
    message = forms.CharField(max_length=2000, min_length=10, widget=forms.Textarea(attrs={'rows': 4}),
                              help_text='Explain why this creator fits the project and what you would like to explore.')
    share = forms.BooleanField(label='Share a snapshot of this brief with this creator.',
        help_text='Includes the selected visual references. Later edits do not change the shared images.')


class TransitionForm(forms.Form):
    status = forms.ChoiceField(label='Next step')
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput())
    note = forms.CharField(max_length=2000, required=False, widget=forms.Textarea(attrs={'rows': 3}), label='Update / reason')
    delivery_url = forms.URLField(required=False, validators=[URLValidator(schemes=['https'])],
                                  help_text='Required when submitting work. Use an HTTPS link the brand can access.')

    def __init__(self, *args, choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['status'].choices = choices
