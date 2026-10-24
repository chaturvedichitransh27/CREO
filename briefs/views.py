import uuid
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.core.paginator import Paginator
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_headers
from django.views.decorators.http import require_http_methods
from . import ai
from .forms import BriefForm, BuilderForm
from .models import Brief
from .references import ReferenceForm, save_with_references


def brand_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.user.role != 'BRAND' or not request.user.is_active:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def owned_brief(request, pk):
    return get_object_or_404(Brief.objects.prefetch_related('skills', 'tools', 'specializations'), pk=pk, brand=request.user)


@brand_required
@require_http_methods(['GET', 'POST'])
def edit(request, pk=None):
    brief = owned_brief(request, pk) if pk else Brief(brand=request.user)
    draft_id = request.GET.get('draft', '') if not pk else ''
    draft = request.session.get('brief_drafts', {}).get(draft_id)
    if draft and draft.get('user_id') != request.user.pk:
        draft = None
    initial = draft['data'] if draft else None
    form = BriefForm(request.POST if request.method == 'POST' else None, instance=brief, initial=initial)
    references = ReferenceForm(request.POST if request.method == 'POST' else None, request.FILES or None, parent=brief)
    valid = form.is_valid() & references.is_valid() if request.method == 'POST' else False
    if valid:
        if draft:
            form.instance.source = draft['source']
        try:
            save_with_references(form, references, request.user, form.save)
        except ValidationError as exc:
            references.add_error(None, exc)
        else:
            if draft:
                drafts = request.session.get('brief_drafts', {})
                drafts.pop(draft_id, None)
                request.session['brief_drafts'] = drafts
            messages.success(request, 'Brief saved. Explore the creators who fit your vision.')
            return redirect(brief)
    return render(request, 'briefs/form.html', {'form': form, 'brief': brief, 'draft': draft, 'references': references})


@brand_required
def detail(request, pk):
    brief = owned_brief(request, pk)
    return render(request, 'briefs/detail.html', {'brief': brief, 'reference_images': brief.reference_images.filter(active=True)})


@brand_required
def delete(request, pk):
    brief = owned_brief(request, pk)
    if request.method == 'POST':
        try:
            brief.delete()
        except ProtectedError:
            messages.info(request, 'This brief has engagement history. Archive it instead of deleting it.')
            return redirect('brief_edit', pk=brief.pk)
        else:
            messages.success(request, 'Brief removed.')
            return redirect('dashboard')
    return render(request, 'briefs/delete.html', {'brief': brief})


@brand_required
def builder(request):
    form = BuilderForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            if form.cleaned_data['mode'] == 'gemini':
                if not ai.is_configured():
                    raise ai.BuilderError('Gemini is not configured for this demo. Use the local starter or write your brief manually.')
                ai.reserve_attempt(request.user)
                data = ai.generate_draft(form.cleaned_data['idea'])
                source = Brief.Source.GEMINI
            else:
                data = ai.local_draft(form.cleaned_data['idea'])
                source = Brief.Source.LOCAL
        except ai.BuilderError as exc:
            form.add_error(None, str(exc))
        else:
            drafts = request.session.get('brief_drafts', {})
            # Bound session growth and isolate simultaneous tabs with opaque draft IDs.
            drafts = dict(list(drafts.items())[-2:])
            draft_id = uuid.uuid4().hex
            drafts[draft_id] = {'data': data, 'source': source, 'user_id': request.user.pk}
            request.session['brief_drafts'] = drafts
            from django.urls import reverse
            return redirect(reverse('brief_create') + '?draft=' + draft_id)
    return render(request, 'briefs/builder.html', {'form': form, 'gemini_ready': ai.is_configured()})


@brand_required
@never_cache
@vary_on_headers('HX-Request')
@require_http_methods(['GET', 'POST'])
def matches(request, pk):
    from creators.views import public_profiles
    from discovery.matching import rank_creators
    from discovery.semantic import refresh_insights
    from .forms import SemanticMatchForm, MatchFilterForm
    from django.utils import timezone
    brief = owned_brief(request, pk)
    profiles = list(public_profiles())
    semantic_form = SemanticMatchForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and semantic_form.is_valid():
        try:
            baseline = rank_creators(brief, profiles, use_ai=False)
            count, reused = refresh_insights(brief, [m['profile'] for m in baseline], request.user)
            messages.success(request, f'{count} creators have current semantic insights' + (' (cached).' if reused else '.'))
            return redirect('brief_matches', pk=pk)
        except ai.BuilderError as exc:
            semantic_form.add_error(None, str(exc))
    ranked = rank_creators(brief, profiles)
    filters = MatchFilterForm(request.GET)
    total = len(ranked)
    if filters.is_valid():
        minimum = filters.cleaned_data.get('min_score') or 0
        ranked = [m for m in ranked if m['score'] >= minimum]
        if filters.cleaned_data.get('available_only'):
            ranked = [m for m in ranked if m['profile'].availability == 'AVAILABLE' and (not m['profile'].available_from or m['profile'].available_from <= timezone.localdate())]
    else:
        ranked = []
    page = Paginator(ranked, 12).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    template = 'briefs/match_results.html' if request.method == 'GET' and request.headers.get('HX-Request') == 'true' else 'briefs/matches.html'
    return render(request, template, {'brief': brief, 'matches': page.object_list, 'page_obj': page,
        'filter_query': params.urlencode(), 'filters': filters, 'total_creators': total,
        'semantic_form': semantic_form, 'gemini_ready': ai.is_configured()})
