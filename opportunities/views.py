from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from portfolio.models import PortfolioItem
from .forms import (JobPostingForm, PostingActionForm, OpportunityFilterForm, ApplicationForm,
                    ContactConsentForm, ConsideringForm, ApplicantFilterForm, BrandContactForm)
from .models import JobPosting, Application, Notification
from .services import change_status, owned_posting, require_brand, save_posting
from briefs.references import ReferenceForm, save_with_references


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
    references = ReferenceForm(request.POST if request.method == 'POST' else None, request.FILES or None, parent=posting)
    valid = form.is_valid() & references.is_valid() if request.method == 'POST' else False
    if valid:
        allowed = {'draft', 'publish'} if posting.status == JobPosting.Status.DRAFT else {'save'}
        if action not in allowed:
            form.add_error(None, 'Choose a valid save action for this posting.')
        else:
            try:
                posting = save_with_references(form, references, request.user, lambda: save_posting(form, request.user))
            except ValidationError as error:
                form.add_error(None, ' '.join(error.messages))
            else:
                messages.success(request, 'Project published.' if action == 'publish' else 'Project saved.')
                return redirect('opportunities:manage', pk=posting.pk)
    return render(request, 'opportunities/form.html', {'form': form, 'posting': posting, 'references': references, 'public_references': True})


@brand_required
@require_GET
def manage(request, pk):
    posting = owned_posting(request.user, pk)
    return render(request, 'opportunities/manage.html', {'posting': posting, 'applicant_count': posting.applications.count(), 'reference_images': posting.reference_images.filter(active=True)})


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
    application = None
    if request.user.is_authenticated and request.user.role == 'CREATOR':
        application = posting.applications.filter(creator__user=request.user).first()
    return render(request, 'opportunities/detail.html', {'posting': posting, 'application': application, 'reference_images': posting.reference_images.filter(active=True)})


@brand_required
@require_GET
def applicants(request, pk):
    posting = owned_posting(request.user, pk)
    form = ApplicantFilterForm(request.GET)
    applications = posting.applications.select_related('creator__user').prefetch_related(
        'creator__skills', 'creator__tools',
        Prefetch('creator__portfolio_items', queryset=PortfolioItem.objects.filter(
            creator__user__is_active=True, creator__user__role='CREATOR'
        ).prefetch_related('tools').order_by('-pk')))
    if form.is_valid():
        filters = form.cleaned_data
        if filters['q']:
            applications = applications.filter(creator__display_name__icontains=filters['q'])
        if filters['status']:
            applications = applications.filter(status=filters['status'])
        if filters['review'] == 'shortlisted':
            applications = applications.filter(consideration_status__in=['CONSIDERING', 'SHORTLISTED'])
        elif filters['review']:
            applications = applications.filter(consideration_status=filters['review'])
        applications = applications.order_by({'oldest': 'created_at', 'name': 'creator__display_name'}.get(filters['sort'], '-created_at'), 'pk')
    else:
        applications = applications.none()
    page = Paginator(applications, 12).get_page(request.GET.get('page'))
    # Prefer concrete overlap; otherwise label available work as samples, not matches.
    tools = set(posting.tools.values_list('pk', flat=True))
    for application in page:
        application.profile_available = application.creator.user.is_active and application.creator.user.role == 'CREATOR'
        application.relevant_work = [item for item in application.creator.portfolio_items.all()
            if (posting.content_type and item.format == posting.content_type)
            or (posting.style and item.style.casefold() == posting.style.casefold())
            or tools.intersection(tool.pk for tool in item.tools.all())][:3]
        application.portfolio_previews = application.relevant_work or list(application.creator.portfolio_items.all())[:3]
    return render(request, 'opportunities/applicants.html', {'posting': posting, 'page_obj': page, 'filter_form': form, 'filter_query': pagination_query(request)})


def pagination_query(request):
    query = request.GET.copy()
    query.pop('page', None)
    return query.urlencode()


def creator_required(view):
    @never_cache
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        from .services import require_creator
        require_creator(request.user)
        return view(request, *args, **kwargs)
    return wrapped


@require_GET
def explore(request):
    form = OpportunityFilterForm(request.GET)
    postings = JobPosting.objects.public().prefetch_related('skills', 'tools').order_by('-published_at', '-pk')
    if form.is_valid():
        data = form.cleaned_data
        if not data['include_closed']:
            postings = postings.available()
        if data['q']:
            postings = postings.filter(Q(title__icontains=data['q']) | Q(description__icontains=data['q']) | Q(brand_name__icontains=data['q']))
        for field, lookup in [('work_type', 'work_type'), ('skill', 'skills'), ('tool', 'tools'), ('currency', 'currency'),
                              ('budget_min', 'budget_max__gte'), ('budget_max', 'budget_max__lte')]:
            if data.get(field):
                postings = postings.filter(**{lookup: data[field]})
    else:
        postings = postings.none()
    postings = postings.with_application_status(request.user)
    page = Paginator(postings, 12).get_page(request.GET.get('page'))
    return render(request, 'opportunities/explore.html', {'page_obj': page, 'filter_form': form, 'filter_query': pagination_query(request)})


@require_GET
def brand_overview(request, brand_id):
    postings = JobPosting.objects.public().filter(brand_id=brand_id).prefetch_related('skills', 'tools').order_by('-published_at', '-pk')
    first = get_object_or_404(postings[:1])
    return render(request, 'opportunities/brand.html', {'public_brand_name': first.brand_name,
        'page_obj': Paginator(postings, 12).get_page(request.GET.get('page'))})


@creator_required
@require_http_methods(['GET', 'POST'])
def apply(request, pk):
    from .services import apply_to_posting
    posting = get_object_or_404(JobPosting.objects.public(), pk=pk)
    existing = posting.applications.filter(creator__user=request.user).first()
    if existing:
        if request.method == 'POST' and request.POST.get('return_to_workspace') == '1':
            return redirect(reverse('dashboard') + '#available-projects')
        return redirect(existing)
    if not posting.accepting_applications:
        messages.error(request, 'This project is no longer accepting applications.')
        return redirect(posting)
    form = ApplicationForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            application, created = apply_to_posting(request.user, posting.pk, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Application sent. The brand has been notified.' if created else 'You have already applied.')
            if request.POST.get('return_to_workspace') == '1':
                return redirect(reverse('dashboard') + '#available-projects')
            return redirect(application)
    return render(request, 'opportunities/action_form.html', {'form': form, 'posting': posting,
        'heading': "I’m Interested", 'description': 'Introduce your creative approach. Contact sharing is optional and specific to this application.', 'button': 'Send application'})


@creator_required
@require_GET
def my_applications(request):
    applications = Application.objects.filter(creator__user=request.user).select_related('posting')
    return render(request, 'opportunities/my_applications.html', {'page_obj': Paginator(applications, 12).get_page(request.GET.get('page'))})


@never_cache
@login_required
@require_GET
def application_detail(request, pk):
    from .services import participant_application, approved_contacts
    from engagements.models import Engagement
    application = participant_application(request.user, pk)
    engagement = Engagement.objects.filter(posting=application.posting, creator=application.creator).first()
    return render(request, 'opportunities/application.html', {'application': application, 'posting': application.posting,
        'contacts': approved_contacts(application), 'engagement': engagement})


@brand_required
@require_http_methods(['GET', 'POST'])
def considering(request, pk):
    from .services import participant_application, consider_application
    application = participant_application(request.user, pk)
    form = ConsideringForm(request.POST if request.method == 'POST' else None,
        initial={'version': application.version, 'brand_email': application.brand_email})
    if request.method == 'POST' and form.is_valid():
        try:
            consider_application(request.user, pk, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Creator marked as Considering and notified in Engagements.')
            return redirect(application)
    return render(request, 'opportunities/action_form.html', {'form': form, 'posting': application.posting,
        'heading': 'Considering ' + application.creator.display_name, 'description': 'Confirm the professional email this applicant may use to contact you. Their details remain private unless they consent.', 'button': 'Confirm Considering'})


@creator_required
@require_http_methods(['GET', 'POST'])
def contact_consent(request, pk):
    from .services import participant_application, update_contact_consent
    application = participant_application(request.user, pk)
    form = ContactConsentForm(request.POST if request.method == 'POST' else None,
        initial={name: getattr(application, name) for name in ('version', 'creator_email', 'creator_phone', 'share_email', 'share_phone')})
    if request.method == 'POST' and form.is_valid():
        try:
            update_contact_consent(request.user, pk, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Contact permissions updated. Unchecked details are no longer shared or retained here.')
            return redirect(application)
    return render(request, 'opportunities/action_form.html', {'form': form, 'posting': application.posting,
        'heading': 'Your contact permissions', 'description': 'You control what this brand can see. Uncheck either permission to revoke access. Previously viewed information cannot be recalled.', 'button': 'Save permissions'})


@brand_required
@require_POST
def contact_request(request, pk):
    from .services import request_contact
    try:
        application = request_contact(request.user, pk)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Contact permission requested in the creator’s Engagements area.')
    return redirect('opportunities:application', pk=pk)


@brand_required
@require_http_methods(['GET', 'POST'])
def brand_contact(request, pk):
    from .services import participant_application, update_brand_contact
    application = participant_application(request.user, pk)
    form = BrandContactForm(request.POST if request.method == 'POST' else None,
        initial={name: getattr(application, name) for name in ('version', 'brand_email', 'brand_share_email')})
    if request.method == 'POST' and form.is_valid():
        try:
            update_brand_contact(request.user, pk, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Your professional contact permission has been updated.')
            return redirect(application)
    return render(request, 'opportunities/action_form.html', {'form': form, 'posting': application.posting,
        'heading': 'Your brand contact permission', 'description': 'Update or revoke email sharing for this applicant. Previously viewed information cannot be recalled.', 'button': 'Save permission'})


@brand_required
@require_POST
def recommendations(request, pk):
    from .services import top_applicants
    posting = owned_posting(request.user, pk)
    try:
        matches = top_applicants(request.user, pk)
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        return redirect('opportunities:applicants', pk=pk)
    return render(request, 'opportunities/recommendations.html', {'posting': posting, 'matches': matches})


@brand_required
@require_http_methods(['GET', 'POST'])
def collaborate(request, pk):
    from .services import participant_application, start_collaboration
    from engagements.forms import InvitationForm
    application = participant_application(request.user, pk)
    form = InvitationForm(request.POST if request.method == 'POST' else None)
    form.fields['share'].label = 'Share a snapshot of this opportunity with this creator.'
    if request.method == 'POST' and form.is_valid():
        try:
            engagement = start_collaboration(request.user, pk, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Collaboration invitation is available in both Engagements workspaces.')
            return redirect(engagement)
    return render(request, 'opportunities/action_form.html', {'form': form, 'posting': application.posting,
        'heading': 'Start Collaboration', 'description': 'Invite ' + application.creator.display_name + ' to accept this project and continue through CREO’s existing delivery workflow.', 'button': 'Send collaboration invitation'})


@never_cache
@login_required
@require_POST
def notification_read(request, pk):
    from django.utils import timezone
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    Notification.objects.filter(pk=notification.pk, recipient=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect(notification.get_absolute_url())
