from django import forms
from .reputation import FACTORS

QUESTIONS = {
    'quality_rating': 'How satisfied were you with the quality of the final work?',
    'timeliness_rating': 'How reliably did the Creator meet the agreed timeline?',
    'communication_rating': 'How effectively did the Creator communicate throughout the project?',
    'professionalism_rating': 'How professional was the Creator throughout the collaboration?',
    'requirements_rating': 'How well did the Creator follow the brief and respond to requested changes?',
}


class CreatorReviewForm(forms.Form):
    written_feedback = forms.CharField(required=False, max_length=1000, strip=True,
        label='Your Experience Working With This Creator',
        widget=forms.Textarea(attrs={'rows':4, 'placeholder':'Share what went well during the collaboration and what other brands should know.'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, label in FACTORS:
            self.fields[name] = forms.TypedChoiceField(label=label, coerce=int,
                choices=[('', 'Choose a rating')] + [(n, f'{n} star' + ('s' if n != 1 else '')) for n in range(1,6)],
                help_text=QUESTIONS[name])
        self.order_fields([name for name, _ in FACTORS] + ['written_feedback'])

    def clean(self):
        data = super().clean()
        if any(name in self.data for name in ['brand', 'brand_id', 'creator', 'creator_id', 'engagement', 'engagement_id']):
            raise forms.ValidationError('Review ownership and project details cannot be submitted or changed.')
        return data
