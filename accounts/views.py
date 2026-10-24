from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction, IntegrityError
from django.shortcuts import redirect, render
from django.core.paginator import Paginator
from django.views.decorators.cache import never_cache
from creators.models import CreatorProfile
from .forms import RegistrationForm


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
                if user.role == 'CREATOR':
                    CreatorProfile.objects.create(user=user, display_name=user.first_name)
        except IntegrityError:
            form.add_error(None, 'That username or email is already in use.')
        else:
            login(request, user)
            messages.success(request, 'Welcome to CREO. Your next chapter starts here.')
            return redirect('dashboard')
    return render(request, 'accounts/register.html', {'form': form})


@never_cache
@login_required
def dashboard(request):
    from opportunities.models import Application, JobPosting, Notification
    notifications = Notification.objects.filter(recipient=request.user, read_at__isnull=True).select_related('application__posting', 'application__creator')
    common = {'is_workspace': True, 'workspace_notifications': notifications[:3], 'unread_count': notifications.count()}
    if request.user.role == 'BRAND':
        from briefs.models import Brief
        from django.db.models import Count
        from creators.views import public_profiles
        from discovery.forms import DiscoveryForm
        from discovery.search import filter_profiles
        # Keep keyword behavior identical to creator discovery; advanced filters
        # remain on the existing directory rather than creating another search.
        search = DiscoveryForm({'q': request.GET.get('q', '')})
        profiles = filter_profiles(public_profiles(), search.cleaned_data) if search.is_valid() else public_profiles().none()
        creators = Paginator(profiles, 6).get_page(request.GET.get('page'))
        briefs = Brief.objects.filter(brand=request.user)
        postings = JobPosting.objects.filter(brand=request.user).annotate(applicant_count=Count('applications')).order_by('-updated_at', '-pk')
        return render(request, 'accounts/brand.html', {**common, 'briefs': briefs, 'brief_count': briefs.count(),
                      'active_count': briefs.filter(status='ACTIVE').count(),
                      'job_postings': postings[:3], 'posting_count': postings.count(),
                      'creator_page': creators, 'creator_search': search, 'q': search.cleaned_data.get('q', '')})
    profile, _ = CreatorProfile.objects.get_or_create(user=request.user, defaults={'display_name': request.user.first_name or request.user.username})
    postings = JobPosting.objects.available().with_application_status(request.user).prefetch_related('skills', 'tools').order_by('-published_at', '-pk')
    applications = Application.objects.filter(creator=profile).select_related('posting')
    return render(request, 'creators/dashboard.html', {**common, 'profile': profile,
        'items': profile.portfolio_items.prefetch_related('tools'),
        'available_projects': Paginator(postings, 6).get_page(request.GET.get('projects_page')),
        'applications': applications[:5], 'application_count': applications.count()})
