from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.urls import reverse
from django.utils.functional import cached_property


class Taxonomy(models.Model):
    name = models.CharField(max_length=80, unique=True)
    class Meta:
        abstract = True
        ordering = ['name']
    def __str__(self):
        return self.name

class Skill(Taxonomy):
    pass

class Tool(Taxonomy):
    pass

class Specialization(Taxonomy):
    pass


class CreatorProfile(models.Model):
    @property
    def demo_reputation(self):
        """Presentation-only samples; never feed verified totals or matching."""
        from decimal import Decimal
        examples = [('4.6', 3), ('4.8', 5), ('4.5', 7), ('4.9', 8), ('4.7', 6)]
        rating, projects = examples[((self.pk or 1) - 1) % len(examples)]
        rating = Decimal(rating)
        return {'average': rating, 'percent': rating * 20, 'completed': projects}

    @cached_property
    def reputation(self):
        from engagements.reputation import summary, with_reputation
        profile = self if hasattr(self, '_review_count') else with_reputation(type(self).objects.filter(pk=self.pk)).get()
        return summary(profile._review_count, profile._rating_total, profile._completed_count)

    class Availability(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Available for work'
        LIMITED = 'LIMITED', 'Limited availability'
        UNAVAILABLE = 'UNAVAILABLE', 'Currently booked'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='creator_profile')
    display_name = models.CharField(max_length=120)
    headline = models.CharField(max_length=160, blank=True)
    bio = models.TextField(max_length=3000, blank=True)
    location = models.CharField(max_length=100, blank=True)
    availability = models.CharField(max_length=15, choices=Availability.choices, default=Availability.AVAILABLE, db_index=True)
    available_from = models.DateField(null=True, blank=True, help_text='Earliest start date. Leave blank if available immediately.')
    price_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(1)], help_text='Typical total project price, not an hourly rate. Supply both ends of the range.')
    price_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(1)])
    currency = models.CharField(max_length=3, choices=[('INR', 'INR'), ('USD', 'USD'), ('EUR', 'EUR'), ('GBP', 'GBP')], default='INR')
    turnaround_days = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(365)], help_text='Typical calendar days from start to delivery; scope must be confirmed.')
    experience_years = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(60)], help_text='Self-reported years of relevant creative experience; zero is valid.')
    skills = models.ManyToManyField(Skill, blank=True, related_name='creators')
    tools = models.ManyToManyField(Tool, blank=True, related_name='creators')
    specializations = models.ManyToManyField(Specialization, blank=True, related_name='creators')
    is_demo = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_name', 'pk']
        constraints = [
            models.CheckConstraint(condition=(models.Q(price_min__isnull=True, price_max__isnull=True) | models.Q(price_min__isnull=False, price_max__isnull=False, price_min__gte=1, price_max__gte=models.F('price_min'))), name='creator_valid_price_range'),
            models.CheckConstraint(condition=models.Q(turnaround_days__isnull=True) | models.Q(turnaround_days__gte=1, turnaround_days__lte=365), name='creator_valid_turnaround'),
            models.CheckConstraint(condition=models.Q(experience_years__isnull=True) | models.Q(experience_years__lte=60), name='creator_valid_experience'),
        ]
    def clean(self):
        if (self.price_min is None) != (self.price_max is None):
            raise ValidationError('Supply both minimum and maximum project prices, or leave both blank.')
        if self.price_min is not None and self.price_max is not None and self.price_max < self.price_min:
            raise ValidationError({'price_max': 'Maximum price must be at least the minimum price.'})
    def __str__(self):
        return self.display_name
    def get_absolute_url(self):
        return reverse('creator_detail', args=[self.pk])
    @property
    def initials(self):
        return ''.join(p[0] for p in self.display_name.split()[:2]).upper()

    @property
    def verification_label(self):
        labels = {item.verification_label for item in self.portfolio_items.all()}
        if 'Admin Reviewed' in labels:
            return 'Admin Reviewed'
        return 'Evidence Attached' if 'Evidence Attached' in labels else 'Self-declared'
