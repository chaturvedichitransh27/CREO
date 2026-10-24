from django.conf import settings
from django.db import models
from django.urls import reverse


class Engagement(models.Model):
    reference_images = models.ManyToManyField('briefs.ReferenceImage', blank=True, related_name='shared_engagements')
    class Status(models.TextChoices):
        INVITED = 'INVITED', 'Invited'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        DECLINED = 'DECLINED', 'Declined'
        IN_PROGRESS = 'IN_PROGRESS', 'In progress'
        SUBMITTED = 'SUBMITTED', 'Submitted for review'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    brief = models.ForeignKey('briefs.Brief', on_delete=models.PROTECT, related_name='engagements', null=True, blank=True)
    posting = models.ForeignKey('opportunities.JobPosting', on_delete=models.PROTECT, related_name='engagements', null=True, blank=True)
    creator = models.ForeignKey('creators.CreatorProfile', on_delete=models.PROTECT, related_name='engagements')
    brief_snapshot = models.JSONField(default=dict, editable=False)
    message = models.TextField(max_length=2000)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.INVITED, db_index=True)
    version = models.PositiveIntegerField(default=0)
    delivery_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['brief', 'creator'], name='one_invitation_per_brief_creator'),
                       models.UniqueConstraint(fields=['posting', 'creator'], name='one_invitation_per_job_creator'),
                       models.CheckConstraint(condition=(models.Q(brief__isnull=False, posting__isnull=True) | models.Q(brief__isnull=True, posting__isnull=False)), name='engagement_one_source')]

    @property
    def brand_id(self):
        return self.posting.brand_id if self.posting_id else self.brief.brand_id

    @property
    def brand_display_name(self):
        return self.brief_snapshot.get('brand_name', self.posting.brand_name) if self.posting_id else (self.brief.brand.first_name or self.brief.brand.username)

    def get_absolute_url(self):
        return reverse('engagement_detail', args=[self.pk])


class EngagementEvent(models.Model):
    engagement = models.ForeignKey(Engagement, on_delete=models.CASCADE, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    status = models.CharField(max_length=15, choices=Engagement.Status.choices)
    note = models.TextField(max_length=2000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']
        indexes = [models.Index(fields=['engagement', 'status', 'actor'], name='engagement_evidence_idx')]


class CreatorReview(models.Model):
    # Participants are derived from this immutable provenance, never client IDs.
    engagement = models.OneToOneField(Engagement, on_delete=models.PROTECT, related_name='creator_review')
    quality_rating = models.PositiveSmallIntegerField()
    timeliness_rating = models.PositiveSmallIntegerField()
    communication_rating = models.PositiveSmallIntegerField()
    professionalism_rating = models.PositiveSmallIntegerField()
    requirements_rating = models.PositiveSmallIntegerField()
    written_feedback = models.TextField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.CheckConstraint(condition=models.Q(**{name + '__in': [1, 2, 3, 4, 5]}), name='review_' + name + '_valid')
                       for name in ['quality_rating', 'timeliness_rating', 'communication_rating', 'professionalism_rating', 'requirements_rating']]

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        from .reputation import FACTORS, validate_scores, eligible_engagements
        if not self._state.adding:
            raise ValidationError('Submitted reviews cannot be edited.')
        validate_scores({name: getattr(self, name) for name, _ in FACTORS})
        if not eligible_engagements().filter(pk=self.engagement_id).exists():
            raise ValidationError('This collaboration is not eligible for a verified review.')
        self.written_feedback = self.written_feedback.strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('Submitted reviews cannot be deleted.')

    @property
    def overall(self):
        from decimal import Decimal
        from .reputation import FACTORS, format_score
        return format_score(Decimal(sum(getattr(self, name) for name, _ in FACTORS)) / 5)

    @property
    def percent(self):
        return self.overall * 20

    @property
    def public_brand_name(self):
        posting = self.engagement.posting
        return posting.brand_name if posting and posting.published_at and posting.status in {'OPEN', 'CLOSED'} else 'Verified Brand'

    @property
    def public_project_title(self):
        posting = self.engagement.posting
        return posting.title if posting and posting.published_at and posting.status in {'OPEN', 'CLOSED'} else 'Private CREO project'

    @property
    def factors(self):
        from .reputation import FACTORS
        return [{'label': label, 'score': getattr(self, name)} for name, label in FACTORS]
