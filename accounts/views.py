from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction, IntegrityError
from django.shortcuts import redirect, render
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


@login_required
def dashboard(request):
    if request.user.role == 'BRAND':
        from briefs.models import Brief
        from django.db.models import Count
        from opportunities.models import JobPosting
        briefs = Brief.objects.filter(brand=request.user)
        postings = JobPosting.objects.filter(brand=request.user).annotate(applicant_count=Count('applications')).order_by('-updated_at', '-pk')
        return render(request, 'accounts/brand.html', {'briefs': briefs, 'brief_count': briefs.count(),
                      'active_count': briefs.filter(status='ACTIVE').count(),
                      'job_postings': postings[:3], 'posting_count': postings.count()})
    profile, _ = CreatorProfile.objects.get_or_create(user=request.user, defaults={'display_name': request.user.first_name or request.user.username})
    return render(request, 'creators/dashboard.html', {'profile': profile, 'items': profile.portfolio_items.prefetch_related('tools')})
