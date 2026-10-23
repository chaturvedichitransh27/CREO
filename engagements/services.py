from django.core.exceptions import ValidationError, PermissionDenied
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import Engagement, EngagementEvent

TRANSITIONS = {
    'CREATOR': {'INVITED': [('ACCEPTED', 'Accept invitation'), ('DECLINED', 'Decline')],
                'ACCEPTED': [('IN_PROGRESS', 'Start work'), ('CANCELLED', 'Cancel engagement')],
                'IN_PROGRESS': [('SUBMITTED', 'Submit for review'), ('CANCELLED', 'Cancel engagement')]},
    'BRAND': {'INVITED': [('CANCELLED', 'Withdraw invitation')], 'ACCEPTED': [('CANCELLED', 'Cancel engagement')],
              'IN_PROGRESS': [('CANCELLED', 'Cancel engagement')],
              'SUBMITTED': [('COMPLETED', 'Mark completed'), ('IN_PROGRESS', 'Request revision'), ('CANCELLED', 'Cancel engagement')]},
}


def snapshot(brief):
    data = {field: str(getattr(brief, field)) for field in ['title', 'description', 'audience', 'deliverables', 'content_type',
            'style', 'aspect_ratio', 'budget_min', 'budget_max', 'currency', 'deadline', 'usage_requirements']}
    data['commercial_use'] = brief.commercial_use
    for name in ['tools', 'skills', 'specializations']:
        data[name] = list(getattr(brief, name).values_list('name', flat=True))
    return data


def participant_role(engagement, user):
    if user.role == 'BRAND' and engagement.brand_id == user.pk:
        return 'BRAND'
    if user.role == 'CREATOR' and engagement.creator.user_id == user.pk:
        return 'CREATOR'
    raise PermissionDenied


@transaction.atomic
def transition(engagement, user, status, version, note='', delivery_url=''):
    role = participant_role(engagement, user)
    allowed = dict(TRANSITIONS.get(role, {}).get(engagement.status, []))
    if status not in allowed:
        raise ValidationError('That status change is not available for your role.')
    if status == 'SUBMITTED' and not delivery_url:
        raise ValidationError('Provide an HTTPS delivery link when submitting work.')
    if delivery_url:
        URLValidator(schemes=['https'])(delivery_url)
    if status in {'DECLINED', 'CANCELLED'} or (engagement.status == 'SUBMITTED' and status == 'IN_PROGRESS'):
        if not note.strip():
            raise ValidationError('Add a short reason for this change.')
    updates = {'status': status, 'version': F('version') + 1, 'updated_at': timezone.now()}
    if status == 'SUBMITTED':
        updates['delivery_url'] = delivery_url
    if not Engagement.objects.filter(pk=engagement.pk, status=engagement.status, version=version).update(**updates):
        raise ValidationError('This engagement changed in another tab. Reload before trying again.')
    EngagementEvent.objects.create(engagement=engagement, actor=user, status=status, note=note)
