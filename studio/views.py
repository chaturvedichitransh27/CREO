from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.decorators.vary import vary_on_headers
from briefs.forms import BriefForm
from briefs.models import Brief
from briefs.views import brand_required
from engagements.models import Engagement
from . import ai, services
from .forms import BoardForm, ElementForm, IdeaForm, RevisionForm
from .models import Moodboard, Element


def owned(request, pk, lock=False):
    queryset = Moodboard.objects.select_for_update() if lock else Moodboard.objects
    return get_object_or_404(queryset, pk=pk, owner=request.user)


def editor_context(board, error=''):
    return {'board': board, 'cards': services.direction_cards(board), 'error': error}


def mutation_response(request, board, error=''):
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'studio/workspace.html', editor_context(board, error))
    if error:
        messages.error(request, error)
    return redirect(board)


@never_cache
@brand_required
@require_GET
def index(request):
    boards = Moodboard.objects.filter(owner=request.user).select_related('brief')
    return render(request, 'studio/index.html', {'page_obj': Paginator(boards, 12).get_page(request.GET.get('page'))})


@never_cache
@brand_required
@require_http_methods(['GET', 'POST'])
def create(request):
    form = IdeaForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            direction = ai.generate(form.cleaned_data['idea'], request.user) if form.cleaned_data['mode'] == 'gemini' else None
        except ai.BuilderError as exc:
            form.add_error(None, str(exc))
        else:
            board = services.create_from_idea(request.user, form.cleaned_data['idea'], direction)
            return redirect(board)
    return render(request, 'studio/create.html', {'form': form, 'gemini_ready': ai.is_configured()})


@never_cache
@brand_required
@require_GET
def detail(request, pk):
    return render(request, 'studio/detail.html', editor_context(owned(request, pk)))


@never_cache
@brand_required
@require_http_methods(['GET', 'POST'])
def edit(request, pk):
    with transaction.atomic():
        board = owned(request, pk, lock=request.method == 'POST')
        form = BoardForm(request.POST if request.method == 'POST' else None, instance=board, user=request.user)
        if request.method == 'POST' and form.is_valid():
            try:
                services.check_revision(board, form.cleaned_data['revision'])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                try:
                    with transaction.atomic():
                        form.save()
                        services.bump(board)
                except IntegrityError:
                    form.add_error('brief', 'This brief already has a moodboard. Choose another brief.')
                else:
                    return redirect(board)
    return render(request, 'studio/form.html', {'form': form, 'board': board, 'heading': 'Shape the direction', 'button': 'Save direction'})


@never_cache
@brand_required
@require_http_methods(['GET', 'POST'])
def element_edit(request, pk, element_pk=None):
    with transaction.atomic():
        board = owned(request, pk, lock=request.method == 'POST')
        element = get_object_or_404(Element, pk=element_pk, board=board) if element_pk else Element(board=board)
        form = ElementForm(request.POST if request.method == 'POST' else None, request.FILES or None,
                           instance=element, initial={'revision': board.revision})
        if request.method == 'POST' and form.is_valid():
            try:
                services.check_revision(board, form.cleaned_data['revision'])
                if not element.pk and board.elements.count() >= services.MAX_ELEMENTS:
                    raise ValidationError('A board can contain up to 36 cards. Remove a card before adding another.')
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                if not element.pk:
                    last = board.elements.order_by('-position').first()
                    form.instance.position = last.position + 1 if last else 0
                form.save()
                services.bump(board)
                return redirect(board)
    return render(request, 'studio/form.html', {'form': form, 'board': board, 'heading': 'Edit your card' if element_pk else 'Add a creative element',
                                              'button': 'Save card', 'element_form': True})


@never_cache
@vary_on_headers('HX-Request')
@brand_required
@require_POST
def action(request, pk):
    error = ''
    with transaction.atomic():
        board = owned(request, pk, lock=True)
        form = RevisionForm(request.POST)
        try:
            if not form.is_valid():
                raise ValidationError('Reload the board before making changes.')
            services.check_revision(board, form.cleaned_data['revision'])
            action_name = request.POST.get('action')
            if action_name == 'approve':
                services.approve(board)
            elif action_name == 'withdraw':
                board.approved_at = board.approved_revision = None
                board.approved_snapshot = {}
                board.save(update_fields=['approved_at', 'approved_revision', 'approved_snapshot', 'updated_at'])
                services.bump(board)
            elif action_name in {'up', 'down', 'remove'}:
                try:
                    element_id = int(request.POST.get('element', ''))
                except (TypeError, ValueError):
                    raise Http404
                element = get_object_or_404(Element, board=board, pk=element_id)
                if action_name == 'remove':
                    # Retain immutable image bytes: an approved snapshot may still use them.
                    element.delete()
                else:
                    ordered = list(board.elements.all())
                    index = next(i for i, item in enumerate(ordered) if item.pk == element.pk)
                    target = index + (-1 if action_name == 'up' else 1)
                    if 0 <= target < len(ordered):
                        ordered[index], ordered[target] = ordered[target], ordered[index]
                        for position, item in enumerate(ordered):
                            item.position = position
                        Element.objects.bulk_update(ordered, ['position'])
                services.bump(board)
            else:
                raise ValidationError('Choose a supported board action.')
        except ValidationError as exc:
            error = ' '.join(exc.messages)
    return mutation_response(request, board, error)


@never_cache
@brand_required
@require_http_methods(['GET', 'POST'])
def convert(request, pk):
    with transaction.atomic():
        board = owned(request, pk, lock=request.method == 'POST')
        if board.brief_id:
            return redirect('brief_edit', pk=board.brief_id)
        brief = Brief(brand=request.user)
        form = BriefForm(request.POST if request.method == 'POST' else None, instance=brief, initial=services.brief_initial(board))
        revision_form = RevisionForm(request.POST if request.method == 'POST' else None, initial={'revision': board.revision})
        if request.method == 'POST' and form.is_valid() and revision_form.is_valid():
            try:
                services.check_revision(board, revision_form.cleaned_data['revision'])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                brief = form.save()
                board.brief = brief
                board.save(update_fields=['brief'])
                services.bump(board)
                messages.success(request, 'Editable brief created. Approve your direction when it is ready to share and use for matching.')
                return redirect(board)
    return render(request, 'studio/convert.html', {'board': board, 'form': form, 'revision_form': revision_form})


def can_view_approved(user, board):
    if user.pk == board.owner_id:
        return True
    return (user.role == 'CREATOR' and board.brief_id and board.approved_at and
            Engagement.objects.filter(brief_id=board.brief_id, creator__user=user)
            .exclude(status__in=['DECLINED', 'CANCELLED']).exists())


@never_cache
@login_required
@require_GET
def approved(request, pk):
    board = get_object_or_404(Moodboard, pk=pk, approved_at__isnull=False)
    if not can_view_approved(request.user, board):
        raise Http404
    snapshot = board.approved_snapshot
    return render(request, 'studio/approved.html', {'board': board, 'snapshot': snapshot, 'cards': snapshot.get('cards', []), 'published': True})


@never_cache
@login_required
@require_GET
def image(request, pk, element_pk):
    board = get_object_or_404(Moodboard, pk=pk)
    published = request.GET.get('approved') == '1' or request.user.pk != board.owner_id
    if published:
        if not board.approved_at or not can_view_approved(request.user, board):
            raise Http404
        card = next((c for c in board.approved_snapshot.get('cards', []) if c['id'] == element_pk), {})
        name = card.get('image')
    else:
        name = get_object_or_404(Element, pk=element_pk, board=board).image.name
    if not name:
        raise Http404
    storage = Element._meta.get_field('image').storage
    try:
        response = FileResponse(storage.open(name, 'rb'), content_type='image/webp')
    except (FileNotFoundError, ValueError):
        raise Http404
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
