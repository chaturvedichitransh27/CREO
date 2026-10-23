"""Owner-scoped marketplace writes, explicit contact consent and shared matching.

Consideration alone never authorizes contact disclosure. Collaborations use the
existing Engagement state machine, with an immutable posting snapshot.
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
    """Only actual, active submitted applicants are eligible for recommendations.

    The shared scorer receives an explicit job adapter. Never rank the creator
    directory or reuse Brief IDs/SemanticMatchCache for jobs. No AI call occurs here.
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
    are explicitly omitted by the job adapter; additional requirements are not consent.
    """
    posting = owned_posting(user, posting_id)
    names = ('title', 'description', 'deliverables', 'content_type', 'aspect_ratio', 'style',
             'budget_min', 'budget_max', 'currency', 'deadline', 'min_experience_years')
    return {'source': 'opportunity', 'posting_id': posting.pk, 'version': posting.version,
            'fields': {name: getattr(posting, name) for name in names},
            'taxonomies': {name: list(getattr(posting, name).all()) for name in ('skills', 'tools', 'specializations')},
            'job_context': {'work_type': posting.work_type, 'output_format': posting.output_format,
                            'duration_days': posting.duration_days, 'additional_requirements': posting.additional_requirements}}


def require_creator(user):
    if not user.is_authenticated or not user.is_active or user.role != 'CREATOR':
        raise PermissionDenied


def participant_application(user, pk, *, lock=False):
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied
    query = Application.objects.all()
    if lock:
        query = query.select_for_update()
    if user.role == 'BRAND':
        query = query.filter(posting__brand=user)
    elif user.role == 'CREATOR':
        query = query.filter(creator__user=user)
    else:
        raise PermissionDenied
    return get_object_or_404(query, pk=pk)


def notify(application, recipient_id, kind):
    from .models import Notification
    return Notification.objects.get_or_create(application=application, recipient_id=recipient_id, kind=kind)[0]


@transaction.atomic
def apply_to_posting(user, posting_id, data):
    require_creator(user)
    posting = get_object_or_404(JobPosting.objects.select_for_update().public(), pk=posting_id)
    creator = get_object_or_404(CreatorProfile, user=user)
    existing = Application.objects.filter(posting=posting, creator=creator).first()
    if existing:
        return existing, False
    if not posting.accepting_applications:
        raise ValidationError('This project is no longer accepting applications.')
    fields = {name: data[name] for name in ('message', 'creator_email', 'creator_phone', 'share_email', 'share_phone')}
    application, created = Application.objects.get_or_create(posting=posting, creator=creator,
        defaults={**fields, 'consent_updated_at': timezone.now()})
    if created:
        notify(application, posting.brand_id, 'APPLIED')
    return application, created


@transaction.atomic
def consider_application(user, pk, data):
    require_brand(user)
    if not data.get('brand_share_email') or not data.get('brand_email'):
        raise ValidationError('Authorize a professional email before considering this applicant.')
    application = participant_application(user, pk, lock=True)
    if application.status != 'SUBMITTED':
        raise ValidationError('This application is no longer active.')
    if not application.creator.user.is_active or application.creator.user.role != 'CREATOR':
        raise ValidationError('This creator account is not available.')
    if application.version != data['version']:
        raise ValidationError('This application changed. Reload before trying again.')
    application.brand_email = data['brand_email']
    application.brand_share_email = data['brand_share_email']
    application.consideration_status = 'CONSIDERING'
    application.version += 1
    application.save(update_fields=['brand_email', 'brand_share_email', 'consideration_status', 'version', 'updated_at'])
    notify(application, application.creator.user_id, 'CONSIDERING')
    return application


@transaction.atomic
def update_contact_consent(user, pk, data):
    require_creator(user)
    application = participant_application(user, pk, lock=True)
    if application.version != data['version']:
        raise ValidationError('This application changed. Reload before saving contact permissions.')
    for name in ('creator_email', 'creator_phone', 'share_email', 'share_phone'):
        setattr(application, name, data[name])
    application.version += 1
    application.consent_updated_at = timezone.now()
    application.save(update_fields=['creator_email', 'creator_phone', 'share_email', 'share_phone', 'version', 'consent_updated_at', 'updated_at'])
    if application.contact_enabled:
        notify(application, application.posting.brand_id, 'CONTACT_UPDATED')
    return application


@transaction.atomic
def request_contact(user, pk):
    require_brand(user)
    application = participant_application(user, pk, lock=True)
    if not application.contact_enabled:
        raise ValidationError('Mark the applicant as Considering before requesting permission.')
    notify(application, application.creator.user_id, 'CONTACT_REQUEST')
    return application


def approved_contacts(application):
    # Always read current consent. Never copy contacts into snapshots, notifications
    # or caches; revocation removes access on the very next request.
    if not application.contact_enabled:
        return {}
    return {
        'creator_email': application.creator_email if application.share_email else '',
        'creator_phone': application.creator_phone if application.share_phone else '',
        'brand_email': application.brand_email if application.brand_share_email else '',
    }


@transaction.atomic
def update_brand_contact(user, pk, data):
    require_brand(user)
    application = participant_application(user, pk, lock=True)
    if application.version != data['version']:
        raise ValidationError('This application changed. Reload before saving contact permissions.')
    application.brand_email = data['brand_email']
    application.brand_share_email = data['brand_share_email']
    application.version += 1
    application.save(update_fields=['brand_email', 'brand_share_email', 'version', 'updated_at'])
    return application


def top_applicants(user, posting_id):
    from types import SimpleNamespace
    from discovery.matching import score_creator
    posting = owned_posting(user, posting_id)
    if posting.status == 'DRAFT':
        raise ValidationError('Publish this project before evaluating applicants.')
    inputs = matching_requirements(user, posting_id)
    # A namespaced source prevents Brief/job cache collisions. Explicitly omit
    # licensing assumptions; this ranking does not assess commercial clearance.
    adapter = SimpleNamespace(**inputs['fields'], pk=f'opportunity:{posting.pk}:{posting.version}',
        brand_id=posting.brand_id, audience='', commercial_use=False, usage_requirements='',
        get_content_type_display=posting.get_content_type_display)
    profiles = applicant_candidates(user, posting_id)
    application_ids = dict(posting.applications.filter(status='SUBMITTED').values_list('creator_id', 'pk'))
    matches = []
    for profile in profiles.filter(pk__in=application_ids).iterator(chunk_size=100):
        match = score_creator(adapter, profile, requirements=inputs['taxonomies'], insight=None)
        match['application_id'] = application_ids[profile.pk]
        matches.append(match)
    return sorted(matches, key=lambda match: (-match['score'], match['profile'].display_name.casefold(), match['profile'].pk))[:5]


def posting_snapshot(posting):
    data = {name: str(getattr(posting, name) or '') for name in ('title', 'description', 'deliverables', 'content_type',
        'style', 'aspect_ratio', 'budget_min', 'budget_max', 'currency', 'deadline', 'brand_name',
        'output_format', 'duration_days', 'additional_requirements', 'work_type', 'min_experience_years')}
    data.update(audience='', usage_requirements='Confirm usage rights with the brand before work begins.', commercial_use=False)
    for name in ('tools', 'skills', 'specializations'):
        data[name] = list(getattr(posting, name).values_list('name', flat=True))
    return data


@transaction.atomic
def start_collaboration(user, pk, data):
    from engagements.models import Engagement, EngagementEvent
    require_brand(user)
    if not data.get('share'):
        raise ValidationError('Confirm sharing this project snapshot before inviting.')
    initial = participant_application(user, pk)
    posting = JobPosting.objects.select_for_update().get(pk=initial.posting_id, brand=user)
    application = participant_application(user, pk, lock=True)
    existing = Engagement.objects.filter(posting=posting, creator=application.creator).first()
    if existing:
        return existing
    if not application.contact_enabled:
        raise ValidationError('Choose Considering before inviting this applicant.')
    if posting.status == 'DRAFT' or not posting.deadline or posting.deadline < timezone.localdate():
        raise ValidationError('The project must be published with a current or future delivery deadline.')
    if not application.creator.user.is_active or application.creator.user.role != 'CREATOR':
        raise ValidationError('This creator account is not available.')
    engagement, created = Engagement.objects.get_or_create(posting=posting, creator=application.creator,
        defaults={'brief_snapshot': posting_snapshot(posting), 'message': data['message']})
    if created:
        EngagementEvent.objects.create(engagement=engagement, actor=user, status='INVITED', note='Opportunity invitation sent; project snapshot shared.')
        notify(application, application.creator.user_id, 'INVITED')
    return engagement
