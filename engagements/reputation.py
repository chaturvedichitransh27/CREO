"""Eligibility, submission and exact reputation aggregation. No AI scoring."""
from decimal import Decimal, ROUND_HALF_UP
from functools import reduce
from operator import add
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from .models import Engagement, EngagementEvent

FACTORS = (
    ('quality_rating', 'Work Quality'),
    ('timeliness_rating', 'Timely Delivery'),
    ('communication_rating', 'Communication'),
    ('professionalism_rating', 'Professionalism'),
    ('requirements_rating', 'Requirements & Revisions'),
)


def eligible_engagements():
    """Require the actual ordered, participant-authored workflow event trail."""
    events = EngagementEvent.objects.filter(engagement_id=OuterRef('pk'))
    creator_events = events.filter(actor_id=OuterRef('creator__user_id'))
    return Engagement.objects.filter(status='COMPLETED', delivery_url__startswith='https://',
        creator__user__role='CREATOR', creator__is_demo=False).exclude(posting__is_demo=True).annotate(
        _accepted=Subquery(creator_events.filter(status='ACCEPTED').order_by('pk').values('pk')[:1]),
    ).annotate(
        _started=Subquery(creator_events.filter(status='IN_PROGRESS', pk__gt=OuterRef('_accepted')).order_by('pk').values('pk')[:1]),
    ).annotate(
        _submitted=Subquery(creator_events.filter(status='SUBMITTED', pk__gt=OuterRef('_started')).order_by('-pk').values('pk')[:1]),
    ).annotate(
        _completed=Exists(events.filter(status='COMPLETED', pk__gt=OuterRef('_submitted'),
            actor_id=Coalesce(OuterRef('posting__brand_id'), OuterRef('brief__brand_id')))),
        _invalidated=Exists(events.filter(status__in=['CANCELLED', 'DECLINED'])),
    ).filter(_accepted__isnull=False, _started__isnull=False, _submitted__isnull=False,
             _completed=True, _invalidated=False)


def eligible_reviews():
    from .models import CreatorReview
    return CreatorReview.objects.filter(engagement_id__in=eligible_engagements().values('pk'))


def validate_scores(data):
    clean = {}
    for name, label in FACTORS:
        value = data.get(name)
        if type(value) is str and value in {'1', '2', '3', '4', '5'}:
            value = int(value)
        if type(value) is not int or value not in range(1, 6):
            raise ValidationError({name: 'Choose a whole-number rating from 1 to 5.'})
        clean[name] = value
    return clean


def require_reviewer(user):
    if (not user.is_authenticated or not user.is_active or user.role != 'BRAND'
            or user.is_staff or user.is_superuser):
        raise PermissionDenied


def owned_review_engagement(user, pk, lock=False):
    require_reviewer(user)
    query = Engagement.objects.filter(Q(brief__brand=user) | Q(posting__brand=user))
    if lock:
        # Lock just Engagement; nullable joins cannot use PostgreSQL FOR UPDATE.
        query = query.select_for_update(of=('self',))
    return get_object_or_404(query.select_related('brief', 'posting', 'creator__user'), pk=pk)


@transaction.atomic
def submit_review(user, engagement_id, data):
    from .models import CreatorReview
    engagement = owned_review_engagement(user, engagement_id, lock=True)
    if set(data) - ({name for name, _ in FACTORS} | {'written_feedback'}):
        raise ValidationError('Review ownership and project details cannot be submitted or changed.')
    if not eligible_engagements().filter(pk=engagement.pk).exists():
        raise ValidationError('Only a completed CREO collaboration with verified acceptance and delivery can be reviewed.')
    if CreatorReview.objects.filter(engagement=engagement).exists():
        raise ValidationError('A review has already been submitted for this collaboration.')
    scores = validate_scores(data)
    feedback = data.get('written_feedback', '')
    if not isinstance(feedback, str):
        raise ValidationError('Please enter valid written feedback.')
    feedback = feedback.strip()
    if len(feedback) > 1000:
        raise ValidationError({'written_feedback': 'Keep your feedback to 1,000 characters or fewer.'})
    review = CreatorReview(engagement=engagement, written_feedback=feedback, **scores)
    try:
        with transaction.atomic():
            review.save()
    except IntegrityError:
        raise ValidationError('A review has already been submitted for this collaboration. Reload the page.') from None
    return review


def with_reputation(profiles):
    """Three correlated aggregates; no per-card queries or mutable counters."""
    total = reduce(add, [F(name) for name, _ in FACTORS])
    reviews = eligible_reviews().filter(engagement__creator_id=OuterRef('pk')).order_by().values('engagement__creator_id').annotate(
        count=Count('pk'), total=Sum(total))
    projects = eligible_engagements().filter(creator_id=OuterRef('pk')).order_by().values('creator_id').annotate(count=Count('pk'))
    return profiles.annotate(_review_count=Coalesce(Subquery(reviews.values('count')[:1]), 0),
        _rating_total=Coalesce(Subquery(reviews.values('total')[:1]), 0),
        _completed_count=Coalesce(Subquery(projects.values('count')[:1]), 0))


def format_score(score):
    return score.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def summary(count, total, completed):
    average = Decimal(total) / (Decimal(count) * 5) if count else None
    return {'count': count, 'completed': completed, 'average': format_score(average) if average is not None else None,
            'percent': format_score(average * 20) if average is not None else 0}


def category_averages(profile):
    values = eligible_reviews().filter(engagement__creator=profile).aggregate(count=Count('pk'),
        **{name: Sum(name) for name, _ in FACTORS})
    if not values['count']:
        return []
    return [{'label': label, 'average': format_score(Decimal(values[name]) / values['count'])}
            for name, label in FACTORS]
