from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.vary import vary_on_headers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .forms import ProfileForm
from .models import CreatorProfile, Skill, Tool, Specialization


def public_profiles():
    from portfolio.models import PortfolioItem
    from engagements.reputation import with_reputation
    return with_reputation(CreatorProfile.objects.filter(user__is_active=True, user__role='CREATOR')).select_related('user').prefetch_related(
        'skills', 'tools', 'specializations', Prefetch('portfolio_items', queryset=PortfolioItem.objects.select_related('creator').prefetch_related('tools', 'evidence')))

def home(request):
    return render(request, 'home.html', {'creators': public_profiles()[:3], 'creator_count': public_profiles().count()})

@vary_on_headers('HX-Request')
def directory(request):
    from discovery.forms import DiscoveryForm
    from discovery.search import filter_profiles, relaxation_suggestions
    form = DiscoveryForm(request.GET)
    valid = form.is_valid()
    profiles = filter_profiles(public_profiles(), form.cleaned_data) if valid else public_profiles().none()
    page = Paginator(profiles, 9).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    suggestions = relaxation_suggestions(public_profiles(), form.cleaned_data, params) if valid and page.paginator.count == 0 else []
    template = 'creators/results.html' if request.headers.get('HX-Request') == 'true' else 'creators/directory.html'
    return render(request, template, {'page_obj': page, 'q': form.cleaned_data.get('q', ''), 'filter_form': form,
                                     'filter_query': params.urlencode(), 'suggestions': suggestions, 'invalid_filters': not valid})

def detail(request, pk):
    profile = get_object_or_404(public_profiles(), pk=pk)
    from engagements.reputation import eligible_reviews, category_averages
    reviews = eligible_reviews().filter(engagement__creator=profile).select_related('engagement__posting')
    return render(request, 'creators/detail.html', {'profile': profile, 'items': profile.portfolio_items.all(),
        'review_page': Paginator(reviews, 6).get_page(request.GET.get('reviews_page')),
        'rating_factors': category_averages(profile)})

@login_required
def edit_profile(request):
    if request.user.role != 'CREATOR':
        raise PermissionDenied
    profile, _ = CreatorProfile.objects.get_or_create(user=request.user, defaults={'display_name': request.user.first_name or request.user.username})
    form = ProfileForm(request.POST or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Profile updated. Looking good.')
        return redirect('dashboard')
    return render(request, 'form.html', {'form': form, 'eyebrow': 'YOUR CREATIVE IDENTITY', 'title': 'Make it yours.', 'description': 'Show brands what you do best. Skills and tools are self-reported.', 'button': 'Save profile'})

@api_view(['GET'])
@permission_classes([AllowAny])
def taxonomy(request):
    return Response({name: list(model.objects.values('id', 'name')) for name, model in [('skills', Skill), ('tools', Tool), ('specializations', Specialization)]})
