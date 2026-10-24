from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from creators.models import CreatorProfile
from portfolio.models import PortfolioItem


class JobPostingQuerySet(models.QuerySet):
    def public(self):
        return self.filter(status__in=['OPEN', 'CLOSED'], published_at__isnull=False)

    def available(self):
        return self.public().filter(status='OPEN', application_deadline__gte=timezone.localdate())

    def with_application_status(self, user):
        if not user.is_authenticated or user.role != 'CREATOR':
            return self
        own = Application.objects.filter(posting=models.OuterRef('pk'), creator__user=user)
        return self.annotate(viewer_application_id=models.Subquery(own.values('pk')[:1]),
                             viewer_status=models.Subquery(own.values('status')[:1]))


class JobPosting(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        OPEN = 'OPEN', 'Open'
        CLOSED = 'CLOSED', 'Closed'

    class WorkType(models.TextChoices):
        EDITING = 'EDITING', 'Video editing'
        ANIMATION = 'ANIMATION', 'Animation'
        FILMMAKING = 'FILMMAKING', 'Filmmaking'
        DESIGN = 'DESIGN', 'Graphic design'
        PHOTOGRAPHY = 'PHOTOGRAPHY', 'Photography'
        AI_CONTENT = 'AI_CONTENT', 'AI creative production'
        OTHER = 'OTHER', 'Other creative work'

    brand = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='job_postings')
    brand_name = models.CharField(max_length=120, blank=True, help_text='The brand name you want displayed publicly. Account details are never filled in automatically.')
    title = models.CharField(max_length=140)
    description = models.TextField(max_length=5000, blank=True)
    work_type = models.CharField(max_length=15, choices=WorkType.choices, blank=True)
    skills = models.ManyToManyField('creators.Skill', blank=True, related_name='job_postings')
    tools = models.ManyToManyField('creators.Tool', blank=True, related_name='job_postings')
    specializations = models.ManyToManyField('creators.Specialization', blank=True, related_name='job_postings')
    style = models.CharField(max_length=100, blank=True)
    deliverables = models.TextField(max_length=2000, blank=True)
    output_format = models.CharField(max_length=250, blank=True, help_text='For example: three MP4 videos, 1080 × 1920, plus editable source files.')
    content_type = models.CharField(max_length=15, choices=PortfolioItem.Format.choices, blank=True)
    aspect_ratio = models.CharField(max_length=5, choices=PortfolioItem.Aspect.choices, blank=True)
    min_experience_years = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(60)])
    budget_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(1)])
    budget_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(1)])
    currency = models.CharField(max_length=3, choices=CreatorProfile._meta.get_field('currency').choices, default='INR')
    duration_days = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(3650)], help_text='Expected calendar days of work, including revisions.')
    application_deadline = models.DateField(null=True, blank=True, help_text='Applications close at the end of this date in CREO’s timezone (Asia/Kolkata).')
    deadline = models.DateField('final delivery deadline', null=True, blank=True)
    additional_requirements = models.TextField(max_length=3000, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, editable=False)
    published_at = models.DateTimeField(null=True, blank=True, editable=False)
    version = models.PositiveIntegerField(default=0, editable=False)
    is_demo = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = JobPostingQuerySet.as_manager()

    class Meta:
        ordering = ['-updated_at', '-pk']
        indexes = [models.Index(fields=['brand', 'status']), models.Index(fields=['status', 'application_deadline'])]
        constraints = [
            models.CheckConstraint(condition=(models.Q(budget_min__isnull=True, budget_max__isnull=True) | models.Q(budget_min__isnull=False, budget_max__isnull=False, budget_min__gte=1, budget_max__gte=models.F('budget_min'))), name='job_valid_budget'),
            models.CheckConstraint(condition=models.Q(duration_days__isnull=True) | models.Q(duration_days__gte=1, duration_days__lte=3650), name='job_valid_duration'),
            models.CheckConstraint(condition=models.Q(min_experience_years__isnull=True) | models.Q(min_experience_years__lte=60), name='job_valid_experience'),
            models.CheckConstraint(condition=models.Q(application_deadline__isnull=True) | models.Q(deadline__isnull=True) | models.Q(deadline__gte=models.F('application_deadline')), name='job_deadline_order'),
            models.CheckConstraint(condition=(models.Q(status='DRAFT', published_at__isnull=True) | models.Q(status__in=['OPEN', 'CLOSED'], published_at__isnull=False)), name='job_publication_state'),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('opportunities:detail', args=[self.pk])

    @property
    def accepting_applications(self):
        return bool(self.status == self.Status.OPEN and self.published_at and self.application_deadline and self.application_deadline >= timezone.localdate())

    @property
    def display_status(self):
        if self.status == self.Status.OPEN and not self.accepting_applications:
            return 'Closed · application deadline passed'
        return self.get_status_display()

    def clean(self):
        errors = {}
        if self.brand_id and self.brand.role != 'BRAND':
            raise ValidationError('Only brand accounts can own job postings.')
        if (self.budget_min is None) != (self.budget_max is None):
            errors['budget_max'] = 'Supply both budget amounts, or leave both blank in a draft.'
        elif self.budget_min is not None and self.budget_max < self.budget_min:
            errors['budget_max'] = 'Maximum budget must be at least the minimum budget.'
        if self.application_deadline and self.deadline and self.deadline < self.application_deadline:
            errors['deadline'] = 'Delivery cannot be earlier than the application deadline.'
        if errors:
            raise ValidationError(errors)

        if self.status != self.Status.DRAFT:
            self.validate_publication()

    def validate_publication(self, *, opening=False):
        """Publication is explicit. Closing a historic posting never requires new dates."""
        errors = {}
        for name in ('brand_name', 'title', 'description', 'work_type', 'deliverables', 'output_format',
                     'budget_min', 'budget_max', 'duration_days', 'application_deadline', 'deadline'):
            value = getattr(self, name)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors[name] = 'Required before publishing.'
        if opening:
            for name in ('application_deadline', 'deadline'):
                value = getattr(self, name)
                if value and value < timezone.localdate():
                    errors[name] = 'Choose today or a future date before opening applications.'
        if errors:
            raise ValidationError(errors)


class Application(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = 'SUBMITTED', 'Submitted'
        WITHDRAWN = 'WITHDRAWN', 'Withdrawn'

    class Consideration(models.TextChoices):
        UNREVIEWED = 'UNREVIEWED', 'Not reviewed'
        REVIEWED = 'REVIEWED', 'Reviewed'
        SHORTLISTED = 'SHORTLISTED', 'Shortlisted'
        CONSIDERING = 'CONSIDERING', 'Considering'
        DECLINED = 'DECLINED', 'Declined'

    posting = models.ForeignKey(JobPosting, on_delete=models.PROTECT, related_name='applications')
    creator = models.ForeignKey(CreatorProfile, on_delete=models.PROTECT, related_name='opportunity_applications')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SUBMITTED)
    consideration_status = models.CharField(max_length=12, choices=Consideration.choices, default=Consideration.UNREVIEWED)
    message = models.TextField(max_length=3000, blank=True)
    creator_email = models.EmailField(blank=True)
    creator_phone = models.CharField(max_length=30, blank=True)
    share_email = models.BooleanField(default=False)
    share_phone = models.BooleanField(default=False)
    consent_updated_at = models.DateTimeField(null=True, blank=True)
    brand_email = models.EmailField(blank=True)
    brand_share_email = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['posting', 'creator'], name='one_application_per_creator_job'),
            models.CheckConstraint(condition=models.Q(status__in=['SUBMITTED', 'WITHDRAWN']), name='application_valid_status'),
            models.CheckConstraint(condition=models.Q(consideration_status__in=['UNREVIEWED', 'REVIEWED', 'SHORTLISTED', 'CONSIDERING', 'DECLINED']), name='application_valid_consideration'),
        ]
        indexes = [models.Index(fields=['posting', 'status']), models.Index(fields=['posting', 'consideration_status']), models.Index(fields=['creator', 'status'])]

    def clean(self):
        if self.creator_id and self.creator.user.role != 'CREATOR':
            raise ValidationError('Applications must belong to a creator account.')
        if self._state.adding and self.posting_id and not self.posting.accepting_applications:
            raise ValidationError('This posting is not accepting applications.')

    def get_absolute_url(self):
        return reverse('opportunities:application', args=[self.pk])

    @property
    def contact_enabled(self):
        return self.status == self.Status.SUBMITTED and self.consideration_status in {self.Consideration.CONSIDERING, self.Consideration.SHORTLISTED}


class Notification(models.Model):
    class Kind(models.TextChoices):
        APPLIED = 'APPLIED', 'New application'
        CONSIDERING = 'CONSIDERING', 'Brand is considering you'
        CONTACT_REQUEST = 'CONTACT_REQUEST', 'Contact permission requested'
        CONTACT_UPDATED = 'CONTACT_UPDATED', 'Contact permission updated'
        INVITED = 'INVITED', 'Collaboration invitation'

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='marketplace_notifications')
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='notifications')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['recipient', 'application', 'kind'], name='unique_application_notification')]
        indexes = [models.Index(fields=['recipient', 'read_at', '-created_at'])]

    def get_absolute_url(self):
        return self.application.get_absolute_url()
