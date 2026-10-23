from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from portfolio.models import PortfolioItem
from .forms import JobPostingForm, PostingActionForm
from .models import JobPosting
from .services import change_status, owned_posting, require_brand, save_posting


def brand_required(view):
    @never_cache
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        require_brand(request.user)
        return view(request, *args, **kwargs)
    return wrapped


@brand_required
@require_GET
def index(request):
    postings = JobPosting.objects.filter(brand=request.user).annotate(applicant_count=Count('applications')).order_by('-updated_at', '-pk')
    page = Paginator(postings, 12).get_page(request.GET.get('page'))
    return render(request, 'opportunities/index.html', {'page_obj': page})


@brand_required
@require_http_methods(['GET', 'POST'])
def edit(request, pk=None):
    posting = owned_posting(request.user, pk) if pk else JobPosting(brand=request.user)
    action = request.POST.get('action', 'draft' if posting.status == JobPosting.Status.DRAFT else 'save')
    form = JobPostingForm(request.POST if request.method == 'POST' else None, instance=posting, publish=action == 'publish')
    if request.method == 'POST' and form.is_valid():
        allowed = {'draft', 'publish'} if posting.status == JobPosting.Status.DRAFT else {'save'}
        if action not in allowed:
            form.add_error(None, 'Choose a valid save action for this posting.')
        else:
            try:
                posting = save_posting(form, request.user)
            except ValidationError as error:
                form.add_error(None, ' '.join(error.messages))
            else:
                messages.success(request, 'Project published.' if action == 'publish' else 'Project saved.')
                return redirect('opportunities:manage', pk=posting.pk)
    return render(request, 'opportunities/form.html', {'form': form, 'posting': posting})


@brand_required
@require_GET
def manage(request, pk):
    posting = owned_posting(request.user, pk)
    return render(request, 'opportunities/manage.html', {'posting': posting, 'applicant_count': posting.applications.count()})


@brand_required
@require_POST
def transition(request, pk):
    owned_posting(request.user, pk)
    form = PostingActionForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Invalid action. Reload this page and try again.')
    else:
        try:
            change_status(request.user, pk, **form.cleaned_data)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        else:
            messages.success(request, 'Applications closed.' if form.cleaned_data['action'] == 'close' else 'Applications reopened.')
    return redirect('opportunities:manage', pk=pk)


@require_GET
def detail(request, pk):
    posting = get_object_or_404(JobPosting.objects.public().prefetch_related('skills', 'tools', 'specializations'), pk=pk)
    return render(request, 'opportunities/detail.html', {'posting': posting})


@brand_required
@require_GET
def applicants(request, pk):
    posting = owned_posting(request.user, pk)
    applications = posting.applications.select_related('creator').prefetch_related(
        'creator__skills', 'creator__tools',
        Prefetch('creator__portfolio_items', queryset=PortfolioItem.objects.prefetch_related('tools').order_by('-pk')))
    page = Paginator(applications, 12).get_page(request.GET.get('page'))
    # Display only portfolio work with a concrete taxonomy/format/style overlap.
    tools = set(posting.tools.values_list('pk', flat=True))
    for application in page:
        application.relevant_work = [item for item in application.creator.portfolio_items.all()
            if (posting.content_type and item.format == posting.content_type)
            or (posting.style and item.style.casefold() == posting.style.casefold())
            or tools.intersection(tool.pk for tool in item.tools.all())][:3]
    return render(request, 'opportunities/applicants.html', {'posting': posting, 'page_obj': page})
