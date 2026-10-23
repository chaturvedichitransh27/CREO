from django.conf import settings
from django.db import models
from django.urls import reverse


class Engagement(models.Model):
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
