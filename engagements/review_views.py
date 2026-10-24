from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .models import CreatorReview
from .reputation import eligible_engagements, owned_review_engagement, submit_review
from .review_forms import CreatorReviewForm


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def review(request, pk):
    engagement = owned_review_engagement(request.user, pk)
    if not eligible_engagements().filter(pk=pk).exists():
        raise PermissionDenied('This collaboration is not eligible for a verified review.')
    existing = CreatorReview.objects.filter(engagement=engagement).first()
    form = CreatorReviewForm(request.POST if request.method == 'POST' else None)
    if existing:
        return render(request, 'engagements/review.html', {'engagement':engagement, 'review':existing},
                      status=409 if request.method == 'POST' else 200)
    if request.method == 'POST' and form.is_valid():
        try:
            submit_review(request.user, pk, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, ' '.join(error.messages))
        else:
            messages.success(request, "Your verified review has been added to this Creator's reputation.")
            return redirect('engagement_review', pk=pk)
    return render(request, 'engagements/review.html', {'engagement':engagement, 'form':form})
