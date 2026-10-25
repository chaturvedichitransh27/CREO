"""Read-only discovery derived solely from explicitly published postings."""
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from .models import JobPosting


def public_brand_projects(query=''):
    """One representative public project per Brand, with unfiltered open counts.

    Search can match any published project, never a private account or draft. The
    newest matching publication represents each account; this is not a company
    profile. Correlated subqueries avoid duplicates and work on SQLite/PostgreSQL.
    """
    published = JobPosting.objects.public().filter(brand__role='BRAND')
    matching = published
    if query:
        matching = matching.filter(
            Q(brand_name__icontains=query) | Q(title__icontains=query)
            | Q(description__icontains=query) | Q(work_type__icontains=query)
            | Q(skills__name__icontains=query) | Q(tools__name__icontains=query)
            | Q(specializations__name__icontains=query)
        )
    representative = matching.filter(brand_id=OuterRef('brand_id')).order_by('-published_at', '-pk')
    available_count = JobPosting.objects.available().filter(brand_id=OuterRef('brand_id')).order_by().values('brand_id').annotate(total=Count('pk')).values('total')
    return published.filter(pk=Subquery(representative.values('pk')[:1])).annotate(
        open_project_count=Coalesce(Subquery(available_count), 0)
    ).prefetch_related('skills', 'tools', 'specializations').order_by('-published_at', '-pk')
