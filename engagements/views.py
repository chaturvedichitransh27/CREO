from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from briefs.models import Brief
from briefs.views import brand_required, owned_brief
from creators.models import CreatorProfile
from .models import Engagement, EngagementEvent
from .forms import InvitationForm, TransitionForm
from .services import TRANSITIONS, snapshot, transition, participant_role


@brand_required
def invite(request, brief_pk, creator_pk):
    brief = owned_brief(request, brief_pk)
    creator = get_object_or_404(CreatorProfile, pk=creator_pk, user__is_active=True, user__role='CREATOR')
    existing = Engagement.objects.filter(brief=brief, creator=creator).first()
    if existing:
        return redirect(existing)
    form = InvitationForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                current = Brief.objects.select_for_update().get(pk=brief.pk)
                if current.status != 'ACTIVE' or current.deadline < timezone.localdate():
                    raise ValidationError('Set the brief to Ready to shortlist with a current or future deadline before inviting.')
                engagement = Engagement.objects.create(brief=current, creator=creator, brief_snapshot=snapshot(current), message=form.cleaned_data['message'])
                EngagementEvent.objects.create(engagement=engagement, actor=request.user, status='INVITED', note='Invitation sent; brief snapshot shared.')
        except ValidationError as exc:
            form.add_error(None, exc)
        except IntegrityError:
            return redirect(get_object_or_404(Engagement, brief=brief, creator=creator))
        else:
            messages.success(request, 'Invitation sent to the creator’s CREO workspace.')
            return redirect(engagement)
    return render(request, 'engagements/invite.html', {'form': form, 'brief': brief, 'creator': creator})


def visible_engagements(user):
    condition = Q(brief__brand=user) if user.role == 'BRAND' else Q(creator__user=user)
    return Engagement.objects.filter(condition).select_related('brief__brand', 'creator__user')


@login_required
def inbox(request):
    return render(request, 'engagements/inbox.html', {'engagements': visible_engagements(request.user)})


@login_required
def detail(request, pk):
    engagement = get_object_or_404(visible_engagements(request.user), pk=pk)
    role = participant_role(engagement, request.user)
    choices = TRANSITIONS.get(role, {}).get(engagement.status, [])
    form = TransitionForm(request.POST if request.method == 'POST' else None, choices=choices, initial={'version': engagement.version})
    if request.method == 'POST' and form.is_valid():
        try:
            transition(engagement, request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Engagement updated. Both workspaces now show the new status.')
            return redirect(engagement)
    from studio.models import Moodboard
    from studio.views import can_view_approved
    approved_moodboard = Moodboard.objects.filter(brief=engagement.brief, approved_at__isnull=False).first()
    if approved_moodboard and not can_view_approved(request.user, approved_moodboard):
        approved_moodboard = None
    return render(request, 'engagements/detail.html', {'engagement': engagement, 'form': form, 'choices': choices, 'approved_moodboard': approved_moodboard,
                  'events': engagement.events.select_related('actor'), 'snapshot': engagement.brief_snapshot})
