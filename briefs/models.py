from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.urls import reverse
from portfolio.models import PortfolioItem
from trust.models import evidence_storage
import uuid


def reference_path(instance, filename):
    return f'brief_references/{uuid.uuid4().hex}.webp'


class ReferenceImage(models.Model):
    """Explicit references for either brief type; shared invitation bytes are immutable."""
    brief = models.ForeignKey('Brief', null=True, blank=True, on_delete=models.CASCADE, related_name='reference_images')
    posting = models.ForeignKey('opportunities.JobPosting', null=True, blank=True, on_delete=models.CASCADE, related_name='reference_images')
    image = models.ImageField(upload_to=reference_path, storage=evidence_storage)
    position = models.PositiveSmallIntegerField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position', 'pk']
        constraints = [
            models.CheckConstraint(condition=(models.Q(brief__isnull=False, posting__isnull=True) | models.Q(brief__isnull=True, posting__isnull=False)), name='reference_one_parent'),
            models.CheckConstraint(condition=models.Q(position__gte=0, position__lte=2), name='reference_three_slots'),
            models.UniqueConstraint(fields=['brief', 'position'], condition=models.Q(active=True), name='reference_brief_slot'),
            models.UniqueConstraint(fields=['posting', 'position'], condition=models.Q(active=True), name='reference_posting_slot'),
        ]

    def get_absolute_url(self):
        return reverse('brief_reference_image', args=[self.pk])


from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.db import transaction


@receiver(post_delete, sender=ReferenceImage)
def cleanup_reference(sender, instance, **kwargs):
    # Defer physical deletion until commit, including cascades; never delete a
    # file while a failed transaction can still restore its database record.
    if instance.image.name:
        storage, name = instance.image.storage, instance.image.name
        transaction.on_commit(lambda: storage.delete(name), robust=True)


class Brief(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        ACTIVE = 'ACTIVE', 'Ready to shortlist'
        ARCHIVED = 'ARCHIVED', 'Archived'

    class Source(models.TextChoices):
        MANUAL = 'MANUAL', 'Written by you'
        GEMINI = 'GEMINI', 'Gemini-assisted draft'
        LOCAL = 'LOCAL', 'Local starter template'

    brand = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='briefs')
    title = models.CharField(max_length=140)
    description = models.TextField(max_length=5000)
    audience = models.CharField(max_length=250, blank=True)
    deliverables = models.TextField(max_length=2000)
    content_type = models.CharField(max_length=15, choices=PortfolioItem.Format.choices, db_index=True)
    style = models.CharField(max_length=100)
    aspect_ratio = models.CharField(max_length=5, choices=PortfolioItem.Aspect.choices)
    budget_min = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(1)])
    budget_max = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(1)])
    currency = models.CharField(max_length=3, choices=[('INR', 'INR'), ('USD', 'USD'), ('EUR', 'EUR'), ('GBP', 'GBP')], default='INR')
    deadline = models.DateField()
    min_experience_years = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(60)], help_text='Optional minimum relevant creative experience. Leave blank for no preference.')
    commercial_use = models.BooleanField(default=True)
    usage_requirements = models.TextField(max_length=2000, blank=True, help_text='Channels, territories, duration, exclusivity, and any source or release requirements.')
    skills = models.ManyToManyField('creators.Skill', blank=True, related_name='briefs')
    tools = models.ManyToManyField('creators.Tool', blank=True, related_name='briefs')
    specializations = models.ManyToManyField('creators.Specialization', blank=True, related_name='briefs')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(budget_min__gte=1), name='brief_positive_budget'),
            models.CheckConstraint(condition=models.Q(budget_max__gte=models.F('budget_min')), name='brief_budget_order'),
            models.CheckConstraint(condition=models.Q(min_experience_years__isnull=True) | models.Q(min_experience_years__lte=60), name='brief_valid_experience'),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('brief_detail', args=[self.pk])

    def clean(self):
        if self.brand_id and self.brand.role != 'BRAND':
            raise ValidationError('Only brand accounts can own briefs.')
        if self.budget_min is not None and self.budget_max is not None and self.budget_max < self.budget_min:
            raise ValidationError({'budget_max': 'Maximum budget must be at least the minimum budget.'})
        if self.commercial_use and not self.usage_requirements.strip():
            raise ValidationError({'usage_requirements': 'Describe the intended commercial use so creators can assess it.'})


class GenerationUsage(models.Model):
    """Database-backed quota, shared across workers; stores no ideas or API responses."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True)
    day = models.DateField()
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempt = models.DateTimeField(null=True)


class SemanticMatchCache(models.Model):
    """One bounded, expiring set of AI insights per private brief, shared by workers."""
    brief = models.OneToOneField(Brief, on_delete=models.CASCADE, primary_key=True, related_name='semantic_cache')
    fingerprint = models.CharField(max_length=64)
    results = models.JSONField(default=dict)
    expires_at = models.DateTimeField(db_index=True)
