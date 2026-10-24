from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.paginator import Paginator
from django.views.decorators.cache import never_cache
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
                engagement.reference_images.set(current.reference_images.filter(active=True))
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
    condition = (Q(brief__brand=user) | Q(posting__brand=user)) if user.role == 'BRAND' else Q(creator__user=user)
    return Engagement.objects.filter(condition).select_related('brief__brand', 'posting', 'creator__user')


@never_cache
@login_required
def inbox(request):
    from opportunities.models import Notification
    notifications = Notification.objects.filter(recipient=request.user).select_related('application__posting', 'application__creator')
    return render(request, 'engagements/inbox.html', {'engagements': visible_engagements(request.user),
        'notifications': Paginator(notifications, 20).get_page(request.GET.get('notifications_page'))})


@never_cache
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
    approved_moodboard = Moodboard.objects.filter(brief=engagement.brief, approved_at__isnull=False).first() if engagement.brief_id else None
    if approved_moodboard and not can_view_approved(request.user, approved_moodboard):
        approved_moodboard = None
    from opportunities.models import Application
    application = Application.objects.filter(posting_id=engagement.posting_id, creator_id=engagement.creator_id).first() if engagement.posting_id else None
    from .models import CreatorReview
    from .reputation import eligible_engagements
    verified_completion = eligible_engagements().filter(pk=engagement.pk).exists()
    review = CreatorReview.objects.filter(engagement=engagement).first() if verified_completion else None
    can_review = (verified_completion and role == 'BRAND' and request.user.is_active
                  and not request.user.is_staff and not request.user.is_superuser and not review)
    return render(request, 'engagements/detail.html', {'engagement': engagement, 'form': form, 'choices': choices, 'approved_moodboard': approved_moodboard, 'application': application,
                  'events': engagement.events.select_related('actor'), 'snapshot': engagement.brief_snapshot,
                  'reference_images': engagement.reference_images.all(), 'creator_review': review, 'can_review': can_review})
