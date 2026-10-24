from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from .models import ReferenceImage


@never_cache
@require_GET
def reference_image(request, pk):
    image = get_object_or_404(ReferenceImage.objects.select_related('brief', 'posting'), pk=pk)
    parent = image.brief or image.posting
    public = image.active and image.posting_id and image.posting.published_at and image.posting.status in {'OPEN', 'CLOSED'}
    authorized = False
    user = request.user
    if user.is_authenticated and user.is_active:
        authorized = user.role == 'BRAND' and parent.brand_id == user.pk and image.active
        if not authorized:
            if user.role == 'BRAND':
                authorized = image.shared_engagements.filter(Q(brief__brand=user) | Q(posting__brand=user)).exists()
            elif user.role == 'CREATOR':
                authorized = image.shared_engagements.filter(creator__user=user).exists()
    if not public and not authorized:
        raise Http404
    try:
        response = FileResponse(image.image.open('rb'), content_type='image/webp')
    except (FileNotFoundError, ValueError):
        raise Http404
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Disposition'] = 'inline; filename="reference.webp"'
    return response
