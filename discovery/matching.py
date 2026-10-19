"""Smart Matching 2.0: bounded, explainable compatibility, never a hiring guarantee."""
import re
import hashlib
import json
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.core.cache import cache
from django.utils import timezone

VERSION = 'smart-match-2.0'
WEIGHTS = {'Skills': 12, 'Tools': 10, 'Content type': 10, 'Aspect ratio': 5,
           'Visual style': 10, 'Specializations': 5, 'Budget': 12, 'Deadline': 10,
           'Availability': 8, 'Experience': 8, 'Creative relevance': 10}
STOP_WORDS = {'a', 'an', 'the', 'and', 'or', 'for', 'to', 'of', 'in', 'with', 'on', 'we', 'our', 'is'}
CONCEPTS = [('cinematic', 'filmic', 'cinema'), ('luxury', 'premium', 'upscale'),
            ('minimal', 'minimalist', 'minimalism', 'restrained'), ('surreal', 'dreamlike', 'dreamy'),
            ('futuristic', 'futurist', 'scifi', 'futurism'), ('product', 'packshot'),
            ('colorful', 'colourful', 'vibrant'), ('animation', 'animated', 'animate'),
            ('advertisement', 'advertising', 'ad', 'campaign'), ('monochrome', 'grayscale')]


def words(value):
    return set(re.findall(r'\w+', value.casefold()))


def concepts(value):
    tokens = words(value) - STOP_WORDS
    for group in CONCEPTS:
        if tokens.intersection(group):
            tokens.difference_update(group)
            tokens.add(group[0])
    return tokens


def overlap(requested, actual):
    wanted = concepts(requested)
    return len(wanted & concepts(actual)) / len(wanted) if wanted else 0


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def brief_data(brief, requirements):
    fields = ['pk', 'brand_id', 'title', 'description', 'audience', 'deliverables', 'content_type', 'aspect_ratio',
              'style', 'budget_min', 'budget_max', 'currency', 'deadline', 'commercial_use', 'usage_requirements', 'min_experience_years']
    return {**{f: getattr(brief, f) for f in fields},
            **{f: format(Decimal(getattr(brief, f)), '.2f') for f in ['budget_min', 'budget_max']},
            **{k: sorted((v.pk, v.name) for v in values) for k, values in requirements.items()}}


def profile_data(profile, items):
    fields = ['pk', 'display_name', 'headline', 'bio', 'availability', 'available_from', 'price_min', 'price_max',
              'currency', 'turnaround_days', 'experience_years', 'is_demo']
    return {**{f: getattr(profile, f) for f in fields},
            **{f: format(Decimal(getattr(profile, f)), '.2f') if getattr(profile, f) is not None else None for f in ['price_min', 'price_max']},
            **{k: sorted((v.pk, v.name) for v in getattr(profile, k).all()) for k in ['tools', 'skills', 'specializations']},
            'projects': [{**{f: getattr(i, f) for f in ['pk', 'title', 'description', 'format', 'aspect_ratio', 'style', 'workflow', 'models_used', 'licensing_notes', 'video_url', 'is_demo', 'revision']},
                          'tools': sorted((t.pk, t.name) for t in i.tools.all())} for i in items]}


def commercial_warnings(brief, profile, items):
    warnings = []
    if profile.is_demo or any(item.is_demo for item in items):
        warnings.append('Fictional demo work is not evidence of commercial readiness or production capability.')
    if brief and brief.commercial_use:
        if not items:
            warnings.append('No portfolio entries are available to review for source and usage information.')
        missing = sum(not item.licensing_notes.strip() for item in items)
        if missing:
            warnings.append(f'{missing} portfolio project(s) have no source or usage notes. Request those details.')
        restrictions = ('non-commercial', 'noncommercial', 'personal use only', 'editorial use only', 'not for commercial', 'research only')
        flagged = [item.title for item in items if any(term in item.licensing_notes.casefold() for term in restrictions)]
        if flagged:
            warnings.append('Usage restriction language appears in: ' + ', '.join(flagged) + '. Review the original terms with the creator.')
        if any(item.video_url for item in items):
            warnings.append('A hosted video link does not establish rights to footage, music, likenesses, or source assets.')
        warnings.append('Confirm model/tool terms for the actual plan and version, source licenses, and any required releases for your intended use.')
    warnings.append('All usage information is self-reported. No automated check can establish legal clearance.')
    return warnings


def score_creator(brief, profile, requirements=None, insight=None):
    items = list(profile.portfolio_items.all())
    requirements = requirements if requirements is not None else {name: list(getattr(brief, name).all()) for name in ['skills', 'tools', 'specializations']}
    key = 'match:' + fingerprint([VERSION, timezone.localdate(), brief_data(brief, requirements), profile_data(profile, items), insight])
    stored = cache.get(key)
    if stored is not None:
        return {**stored, 'profile': profile, 'items': items}
    rows, missing = [], []

    def row(label, fraction, reason, known=True, warning=None):
        fraction = max(0, min(1, float(fraction)))
        weight = WEIGHTS[label]
        rows.append({'label': label, 'weight': weight, 'earned': round(weight * fraction, 2),
                     'percent': round(100 * fraction), 'reason': reason, 'known': known})
        if warning:
            missing.append(warning)

    formats = [i for i in items if i.format == brief.content_type]
    aspect = [i for i in formats if i.aspect_ratio == brief.aspect_ratio]
    row('Content type', bool(formats), f'{len(formats)} portfolio projects list {brief.get_content_type_display()}.',
        warning=None if formats else 'No portfolio project demonstrates the requested content type.')
    row('Aspect ratio', bool(aspect), f'{len(aspect)} projects combine the requested format and {brief.aspect_ratio}.',
        warning=None if aspect else 'No project combines the requested format and aspect ratio.')
    if brief.style.strip():
        style = max((overlap(brief.style, i.style) for i in formats), default=0)
        row('Visual style', style, 'Style terms and a small synonym dictionary, within the requested content type. No image analysis.',
            warning=None if style == 1 else 'Requested style is only partially demonstrated or absent.')
    if requirements['tools']:
        listed = {t.pk for t in profile.tools.all()}
        in_work = {t.pk for i in items for t in i.tools.all()}
        demonstrated = [t.name for t in requirements['tools'] if t.pk in in_work]
        claimed = [t.name for t in requirements['tools'] if t.pk in listed - in_work]
        absent = [t.name for t in requirements['tools'] if t.pk not in listed | in_work]
        row('Tools', (len(demonstrated) + .5 * len(claimed)) / len(requirements['tools']),
            f'Portfolio-listed: {", ".join(demonstrated) or "none"}. Profile-only, half credit: {", ".join(claimed) or "none"}.',
            warning='Missing tools: ' + ', '.join(absent) if absent else None)
    for field, label in [('skills', 'Skills'), ('specializations', 'Specializations')]:
        wanted = requirements[field]
        if wanted:
            listed = {v.pk for v in getattr(profile, field).all()}
            matched = [v.name for v in wanted if v.pk in listed]
            absent = [v.name for v in wanted if v.pk not in listed]
            row(label, len(matched) / len(wanted), f'Self-declared matches: {", ".join(matched) or "none"}.',
                warning=f'Missing {label.lower()}: ' + ', '.join(absent) if absent else None)
    if profile.price_min is None or profile.price_max is None:
        row('Budget', 0, 'Creator has not supplied a project price range.', False, 'Project pricing is unknown; request a quote.')
    elif profile.currency != brief.currency:
        row('Budget', 0, f'Creator quotes {profile.currency}; brief uses {brief.currency}. No exchange-rate conversion is assumed.', False,
            'Currencies differ; a comparable quote is required.')
    else:
        low, high, ceiling = profile.price_min, profile.price_max, brief.budget_max
        fit = 1 if high <= ceiling else (0 if low > ceiling else float((ceiling-low) / (high-low)))
        row('Budget', fit, f'Typical project range {profile.currency} {low}–{high}; budget ceiling {ceiling}. Fraction of the range within budget; confirm scope.',
            warning=None if fit == 1 else 'Typical price exceeds all or part of the budget.')
    today = timezone.localdate()
    if brief.deadline < today:
        row('Deadline', 0, 'The brief deadline has passed.', warning='This brief’s deadline has passed. Update it before evaluating availability.')
    elif profile.availability == 'UNAVAILABLE':
        row('Deadline', 0, 'Creator is currently booked.', warning='Creator is booked; the delivery date cannot be assumed.')
    elif profile.turnaround_days is None or (profile.availability == 'LIMITED' and profile.available_from is None):
        row('Deadline', 0, 'Turnaround or earliest start date is missing.', False, 'Schedule is incomplete; confirm turnaround and start date.')
    else:
        start = max(today, profile.available_from or today)
        delivery = start + timedelta(days=profile.turnaround_days)
        row('Deadline', delivery <= brief.deadline, f'Earliest estimated delivery: {delivery.isoformat()} ({profile.turnaround_days} calendar days). Confirm scope and commitment.',
            warning=None if delivery <= brief.deadline else 'Estimated delivery is later than the brief deadline.')
    availability = {'AVAILABLE': 1, 'LIMITED': .5, 'UNAVAILABLE': 0}.get(profile.availability, 0)
    if profile.available_from and profile.available_from > brief.deadline:
        availability = 0
    row('Availability', availability, profile.get_availability_display() + '; self-reported.',
        warning=None if availability == 1 else 'Availability is limited, booked, or starts after the deadline.')
    if brief.min_experience_years:
        years = profile.experience_years
        row('Experience', min(1, years / brief.min_experience_years) if years is not None else 0,
            f'{years if years is not None else "Unknown"} self-reported years; {brief.min_experience_years} requested. Years do not measure artistic quality.',
            years is not None, None if years is not None and years >= brief.min_experience_years else 'Relevant experience is below the requested minimum or unknown.')
    target = f'{brief.description} {brief.deliverables} {brief.style}'
    if insight and items:
        relevance = insight['similarity'] / 100
        explanation = 'Gemini semantic estimate: ' + insight['reason']
        semantic_mode = 'Gemini semantic analysis'
    else:
        relevance = max((overlap(target, f'{i.title} {i.description} {i.style} {i.workflow}') for i in items), default=0)
        explanation = 'Local keyword and synonym overlap with the best project. Gemini analysis has not been applied to this version.'
        semantic_mode = 'Local relevance fallback'
    row('Creative relevance', relevance, explanation,
        warning=None if relevance >= .5 else 'Portfolio text provides limited evidence of the creative requirements.')
    if insight and items:
        rows[-1].update(project_id=insight['project_id'], evidence_quote=insight['evidence_quote'])
    total = sum(r['weight'] for r in rows)
    score = int((Decimal(str(sum(r['earned'] for r in rows))) * 100 / total).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    result = {'score': score, 'rows': rows, 'weight_total': total, 'warnings': commercial_warnings(brief, profile, items),
              'missing': missing, 'coverage': round(100 * sum(r['weight'] for r in rows if r['known']) / total),
              'semantic_mode': semantic_mode, 'demo': profile.is_demo or any(i.is_demo for i in items)}
    cache.set(key, result, timeout=300)
    return {**result, 'profile': profile, 'items': items}


def rank_creators(brief, profiles, use_ai=True):
    requirements = {name: list(getattr(brief, name).all()) for name in ['skills', 'tools', 'specializations']}
    profiles = list(profiles)
    insights = {}
    if use_ai:
        from .semantic import cached_insights
        insights = cached_insights(brief, profiles, requirements)
    return sorted((score_creator(brief, p, requirements, insights.get(str(p.pk))) for p in profiles),
                  key=lambda match: (-match['score'], match['profile'].display_name.casefold(), match['profile'].pk))
