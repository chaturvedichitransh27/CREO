from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from .matching import rank_creators, commercial_warnings
from django.views.decorators.cache import never_cache


def selected_ids(request):
    values = request.session.get('comparison', [])
    return list(dict.fromkeys(v for v in values if type(v) is int and v > 0))[:3]


@require_POST
def update(request, pk):
    from creators.views import public_profiles
    selected = selected_ids(request)
    # Prune inactive/deleted creators so they never consume a comparison slot.
    visible = set(public_profiles().filter(pk__in=selected).values_list('pk', flat=True))
    selected = [v for v in selected if v in visible]
    if request.POST.get('action') == 'remove':
        selected = [v for v in selected if v != pk]
        messages.success(request, 'Creator removed from comparison.')
    else:
        get_object_or_404(public_profiles(), pk=pk)
        if pk not in selected:
            if len(selected) == 3:
                messages.info(request, 'Compare up to three creators. Remove one to add another.')
            else:
                selected.append(pk)
                messages.success(request, 'Creator added to comparison.')
    request.session['comparison'] = selected
    destination = request.POST.get('next', reverse('creator_compare'))
    if not url_has_allowed_host_and_scheme(destination, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        destination = reverse('creator_compare')
    return redirect(destination)


@never_cache
def compare(request):
    from creators.views import public_profiles
    profiles = list(public_profiles().filter(pk__in=selected_ids(request)))
    request.session['comparison'] = [p.pk for p in profiles]
    brief = None
    if request.GET.get('brief'):
        if not request.user.is_authenticated or request.user.role != 'BRAND':
            raise PermissionDenied
        from briefs.models import Brief
        value = request.GET['brief']
        if not value.isascii() or not value.isdigit() or len(value) > 10:
            from django.http import Http404
            raise Http404
        brief = get_object_or_404(Brief.objects.prefetch_related('tools', 'skills', 'specializations'), pk=int(value), brand=request.user)
    cards = rank_creators(brief, profiles) if brief else []
    for profile in profiles:
        if not brief:
            items = list(profile.portfolio_items.all())
            cards.append({'profile': profile, 'items': items, 'warnings': commercial_warnings(None, profile, items)})
    briefs = request.user.briefs.exclude(status='ARCHIVED') if request.user.is_authenticated and request.user.role == 'BRAND' else []
    return render(request, 'creators/compare.html', {'cards': cards, 'brief': brief, 'briefs': briefs})
