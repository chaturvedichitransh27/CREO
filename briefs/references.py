"""Optional reference form and atomic writes shared by existing brief systems."""
from io import BytesIO
import logging
from PIL import Image, ImageOps
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction, DatabaseError
from portfolio.validators import validate_image
from .models import ReferenceImage

LIMIT_ERROR = 'You can upload a maximum of 3 reference images.'


class MultipleImages(forms.ClearableFileInput):
    allow_multiple_selected = True


class ImageUploads(forms.FileField):
    widget = MultipleImages

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if len(files) > 3:
            raise ValidationError(LIMIT_ERROR)
        result = []
        for value in files:
            value = super().clean(value)
            validate_image(value)
            try:
                with Image.open(value) as source:
                    decoded = ImageOps.exif_transpose(source).convert('RGB')
                    decoded.thumbnail((2400, 2400))
                    output = BytesIO()
                    # No EXIF, filenames, ancillary metadata or trailing payloads.
                    decoded.save(output, 'WEBP', quality=88)
            except (OSError, ValueError):
                raise ValidationError("One of your images couldn't be processed. Please choose another image.") from None
            finally:
                value.seek(0)
            image = ContentFile(output.getvalue(), name='reference.webp')
            validate_image(image)
            result.append(image)
        return result


class ReferenceForm(forms.Form):
    reference_uploads = ImageUploads(required=False, label='Add reference images',
        widget=MultipleImages(attrs={'accept': 'image/jpeg,image/png,image/webp'}))
    remove_references = forms.ModelMultipleChoiceField(queryset=ReferenceImage.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, parent, **kwargs):
        super().__init__(*args, **kwargs)
        self.existing = parent.reference_images.filter(active=True) if parent.pk else ReferenceImage.objects.none()
        self.fields['remove_references'].queryset = self.existing

    def clean(self):
        data = super().clean()
        if self.existing.count() - len(data.get('remove_references', [])) + len(data.get('reference_uploads', [])) > 3:
            raise ValidationError(LIMIT_ERROR)
        return data


def save_with_references(form, references, user, save):
    """Lock the parent for writes/invitations; constraints also cap active slots.

    Caller passes the existing validated parent save, so existing business logic
    and taxonomy writes remain authoritative. Files are not overwritten in place.
    """
    parent = form.instance
    if not user.is_authenticated or not user.is_active or user.role != 'BRAND' or parent.brand_id != user.pk:
        raise PermissionDenied
    original_pk = parent.pk
    staged = []
    try:
        with transaction.atomic():
            if parent.pk:
                type(parent).objects.select_for_update().get(pk=parent.pk, brand=user)
            current = list(parent.reference_images.filter(active=True)) if parent.pk else []
            removed = {item.pk for item in references.cleaned_data['remove_references']}
            if not removed.issubset({item.pk for item in current}):
                raise ValidationError('These references changed. Reload the page before saving.')
            kept = [item for item in current if item.pk not in removed]
            uploads = references.cleaned_data['reference_uploads']
            if len(kept) + len(uploads) > 3:
                raise ValidationError(LIMIT_ERROR)
            parent = save()
            for item in current:
                if item.pk in removed:
                    if item.shared_engagements.exists():
                        item.active = False
                        item.save(update_fields=['active'])
                    else:
                        item.delete()
            slots = [slot for slot in range(3) if slot not in {item.position for item in kept}]
            relation = 'brief' if parent._meta.label_lower == 'briefs.brief' else 'posting'
            for slot, upload in zip(slots, uploads):
                item = ReferenceImage(**{relation: parent}, position=slot)
                item.image.save(upload.name, upload, save=False)
                staged.append((item.image.storage, item.image.name))
                item.save()
            return parent
    except Exception as error:
        for storage, name in staged:
            try:
                storage.delete(name)
            except Exception:
                logging.getLogger(__name__).exception('Could not clean up a staged reference image')
        if not original_pk:
            form.instance.pk = None
            form.instance._state.adding = True
        if isinstance(error, (OSError, DatabaseError)):
            raise ValidationError('The references could not be saved. Your previous images are unchanged; please try again.') from error
        raise
