from io import BytesIO
from PIL import Image, ImageOps
from django import forms
from django.core.files.base import ContentFile
from briefs.models import Brief
from portfolio.validators import validate_image
from .models import Moodboard, Element


class BoardForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput, required=False)

    class Meta:
        model = Moodboard
        fields = ['title', 'idea', 'visual_style', 'brief']
        widgets = {'idea': forms.Textarea(attrs={'rows': 3})}
        labels = {'brief': 'Linked brief (optional)', 'visual_style': 'Selected visual style'}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['brief'].queryset = Brief.objects.filter(brand=user).filter(
            models_available(self.instance))
        if self.instance.pk:
            self.fields['revision'].required = True
            self.initial['revision'] = self.instance.revision
            # Once linked, keep the brief association to avoid moving shared snapshots.
            self.fields['brief'].disabled = bool(self.instance.brief_id)


def models_available(board):
    from django.db.models import Q
    return Q(moodboard__isnull=True) | Q(moodboard=board.pk) if board.pk else Q(moodboard__isnull=True)


class IdeaForm(forms.Form):
    idea = forms.CharField(min_length=10, max_length=4000, widget=forms.Textarea(attrs={'rows': 5}),
                          label='Describe the world you want to create')
    mode = forms.ChoiceField(choices=[('manual', 'Start manually — no AI required'), ('gemini', 'Generate direction with Gemini')])
    consent = forms.BooleanField(required=False, label='Send this idea to Google Gemini.',
                                help_text='Use non-confidential ideas. Free-tier input may be used to improve Google products. Uploaded images are never sent.')

    def clean(self):
        data = super().clean()
        if data.get('mode') == 'gemini' and not data.get('consent'):
            self.add_error('consent', 'Confirm before sending your idea, or choose manual creation.')
        return data


class ElementForm(forms.ModelForm):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    rights_confirmed = forms.BooleanField(required=False, label='I own this image or have permission to upload and share it with invited creators.')

    class Meta:
        model = Element
        fields = ['kind', 'title', 'text', 'color', 'image', 'credit']
        widgets = {'text': forms.Textarea(attrs={'rows': 4}), 'color': forms.TextInput(attrs={'placeholder': '#A78BFA'})}
        labels = {'text': 'Creative direction', 'image': 'Reference image (optional)'}
        help_texts = {'image': 'JPEG, PNG or WebP, up to 5 MB. Images are reference uploads, not AI-generated output.'}

    def clean(self):
        data = super().clean()
        if data.get('kind') == Element.Kind.COLOR and not data.get('color'):
            self.add_error('color', 'Choose a hex color for this swatch.')
        if data.get('image') and data.get('kind') != Element.Kind.REFERENCE:
            self.add_error('kind', 'Images belong on creative reference cards.')
        if data.get('image') and not data.get('credit'):
            self.add_error('credit', 'Describe the source and your permission to use this image.')
        if self.files.get('image') and not data.get('rights_confirmed'):
            self.add_error('rights_confirmed', 'Confirm your permission before uploading.')
        return data

    def clean_image(self):
        value = self.cleaned_data.get('image')
        if not self.files.get('image') or not value:
            return value
        validate_image(value)
        # Decode and re-encode: discard EXIF, original names and trailing payloads.
        value.seek(0)
        try:
            with Image.open(value) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((2400, 2400))
                output = BytesIO()
                image.save(output, 'WEBP', quality=88)
        except (OSError, ValueError):
            raise forms.ValidationError('This image could not be decoded. Upload a valid JPEG, PNG or WebP.') from None
        finally:
            value.seek(0)
        result = ContentFile(output.getvalue(), name='reference.webp')
        validate_image(result)
        return result


class RevisionForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
