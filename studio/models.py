import uuid
from django.conf import settings
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from django.urls import reverse
from portfolio.validators import validate_image
from trust.models import evidence_storage


def reference_path(instance, filename):
    return f'studio/{uuid.uuid4().hex}.webp'


class Moodboard(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='moodboards')
    brief = models.OneToOneField('briefs.Brief', null=True, blank=True, on_delete=models.SET_NULL, related_name='moodboard')
    title = models.CharField(max_length=140)
    idea = models.TextField(max_length=4000, blank=True)
    visual_style = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=10, choices=[('MANUAL', 'Manual direction'), ('GEMINI', 'Gemini-assisted direction')], default='MANUAL')
    revision = models.PositiveIntegerField(default=1)
    approved_revision = models.PositiveIntegerField(null=True, editable=False)
    approved_at = models.DateTimeField(null=True, editable=False)
    approved_snapshot = models.JSONField(default=dict, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-pk']

    def get_absolute_url(self):
        return reverse('studio:detail', args=[self.pk])

    @property
    def has_draft_changes(self):
        return self.approved_revision is not None and self.revision != self.approved_revision


class Element(models.Model):
    class Kind(models.TextChoices):
        COLOR = 'COLOR', 'Color swatch'
        MOOD = 'MOOD', 'Mood & atmosphere'
        SHOT = 'SHOT', 'Shot idea'
        REFERENCE = 'REFERENCE', 'Creative reference'
        DELIVERABLE = 'DELIVERABLE', 'Deliverable'
        NOTE = 'NOTE', 'Direction note'

    board = models.ForeignKey(Moodboard, on_delete=models.CASCADE, related_name='elements')
    kind = models.CharField(max_length=12, choices=Kind.choices)
    title = models.CharField(max_length=100)
    text = models.TextField(max_length=1500, blank=True)
    color = models.CharField(max_length=7, blank=True, validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Use a six-digit hex color, such as #A78BFA.')])
    image = models.ImageField(upload_to=reference_path, storage=evidence_storage, blank=True,
                              validators=[validate_image, FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp'])])
    credit = models.CharField(max_length=300, blank=True, help_text='Source, creator, and permission or license. Original work may be labeled “My original image”.')
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['position', 'pk']

    def as_card(self):
        return {key: getattr(self, key) for key in ['id', 'kind', 'title', 'text', 'color', 'credit']} | {'image': self.image.name}
