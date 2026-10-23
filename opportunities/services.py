"""Ownership-checked writes and the applicant boundary for future matching.

Phase 1 deliberately has no application submission, contact consent, notification,
or ranking operation. Consideration is a review state, never contact consent.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from creators.models import CreatorProfile
from .models import Application, JobPosting


def require_brand(user):
    if not user.is_authenticated or not user.is_active or user.role != 'BRAND':
        raise PermissionDenied


def owned_posting(user, pk):
    require_brand(user)
    return get_object_or_404(JobPosting, pk=pk, brand=user)


@transaction.atomic
def save_posting(form, brand):
    require_brand(brand)
    posting = form.save(commit=False)
    if posting.pk:
        owned_posting(brand, posting.pk)
        # Compare-and-swap prevents stale writes on both PostgreSQL and SQLite.
        updated = JobPosting.objects.filter(pk=posting.pk, brand=brand, version=form.cleaned_data['version']).update(version=F('version') + 1)
        if not updated:
            raise ValidationError('This posting changed in another tab. Reload it before saving.')
        posting.version = form.cleaned_data['version'] + 1
    posting.brand = brand
    if form.publish and posting.status == JobPosting.Status.DRAFT:
        posting.status = JobPosting.Status.OPEN
        posting.published_at = timezone.now()
    if posting.status != JobPosting.Status.DRAFT:
        posting.validate_publication(opening=form.publish)
    posting.full_clean()
    posting.save()
    form.save_m2m()
    return posting


@transaction.atomic
def change_status(user, pk, action, version):
    posting = owned_posting(user, pk)
    transitions = {'close': (JobPosting.Status.OPEN, JobPosting.Status.CLOSED),
                   'reopen': (JobPosting.Status.CLOSED, JobPosting.Status.OPEN)}
    if action not in transitions or posting.status != transitions[action][0]:
        raise ValidationError('That action is not available for this posting.')
    if action == 'reopen':
        posting.validate_publication(opening=True)
    updated = JobPosting.objects.filter(pk=posting.pk, brand=user, status=posting.status, version=version).update(
        status=transitions[action][1], version=F('version') + 1, updated_at=timezone.now())
    if not updated:
        raise ValidationError('This posting changed in another tab. Reload it before trying again.')


def applicant_candidates(user, posting_id):
    """Only actual, active applicants are eligible for future AI shortlisting.

    This boundary must remain owner-scoped. Use discovery.matching.score_creator
    with a requirements adapter in Phase 2; do not rank the creator directory or
    reuse Brief primary keys/SemanticMatchCache for jobs. No AI call occurs here.
    """
    posting = owned_posting(user, posting_id)
    return CreatorProfile.objects.filter(
        opportunity_applications__posting=posting,
        opportunity_applications__status=Application.Status.SUBMITTED,
        user__is_active=True, user__role='CREATOR',
    ).select_related('user').prefetch_related('skills', 'tools', 'specializations', 'portfolio_items__tools')


def matching_requirements(user, posting_id):
    """Canonical inputs shared with Smart Matching, without scores or API calls.

    Availability, prices, experience and portfolio evidence live on the applicant
    profile, as in discovery. Unsupported brief-specific audience/licensing fields
    need an explicit adapter in Phase 2; additional requirements are not consent.
    """
    posting = owned_posting(user, posting_id)
    names = ('title', 'description', 'deliverables', 'content_type', 'aspect_ratio', 'style',
             'budget_min', 'budget_max', 'currency', 'deadline', 'min_experience_years')
    return {'source': 'opportunity', 'posting_id': posting.pk, 'version': posting.version,
            'fields': {name: getattr(posting, name) for name in names},
            'taxonomies': {name: list(getattr(posting, name).all()) for name in ('skills', 'tools', 'specializations')},
            'job_context': {'work_type': posting.work_type, 'output_format': posting.output_format,
                            'duration_days': posting.duration_days, 'additional_requirements': posting.additional_requirements}}
