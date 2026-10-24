"""Shared fictional catalog for explicit local seed commands; never runs on startup."""
from datetime import timedelta
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from accounts.models import User
from creators.models import Skill, Tool, Specialization
from .models import JobPosting


# Preserve the original seed identities and first project so existing development
# databases can gain a second project without duplicating brands or editing records.
CATALOG = [
    ('Lumen Studio', [
        ('Cinematic launch film editing', 'EDITING', 'FILM', 'Cinematic storytelling', 'DaVinci Resolve', 'AI filmmaking', 40000, 70000,
         'Edit a 45-second launch film for a fictional outdoor clothing collection. Build a strong opening, natural sound design and a warm cinematic grade.',
         'One 45-second master, two 15-second cutdowns, subtitles and an editable timeline.', 'Cinematic, warm, energetic', '16:9', 14),
        ('Momentum sports campaign', 'FILMMAKING', 'FILM', 'Cinematic storytelling', 'After Effects', 'AI filmmaking', 65000, 110000,
         'Create an original sports advertisement for a fictional running shoe. Focus on everyday athletes, energetic movement and inclusive storytelling. Use no real sports brand names, logos or likenesses.',
         'One 30-second film, three vertical cutdowns and a storyboard. Include one revision round.', 'High-energy, kinetic, inspirational', '9:16', 21),
    ]),
    ('Velvet Tomorrow', [
        ('Fashion advertisement production', 'FILMMAKING', 'FILM', 'Art direction', 'Runway', 'Fashion & beauty', 60000, 120000,
         'Develop a cinematic fashion campaign for a fictional independent label. Combine fabric movement, editorial pacing and expressive light around a coherent treatment.',
         'One 30-second film, four campaign stills, a treatment and editable source assets.', 'Editorial, cinematic, elegant', '16:9', 21),
        ('Cinematic fashion video editing', 'EDITING', 'FILM', 'Color grading', 'DaVinci Resolve', 'Fashion & beauty', 25000, 45000,
         'Edit supplied fictional lookbook footage into a restrained fashion story. Maintain skin-tone consistency, deliver a polished grade and use cleared music.',
         'One 60-second edit, two 15-second reels, colour grade and subtitle file.', 'Minimalist, atmospheric, editorial', '9:16', 10),
    ]),
    ('Orbit Paper', [
        ('Playful AI animation series', 'ANIMATION', 'ANIMATION', 'Animation', 'Blender', 'Animation', 30000, 60000,
         'Develop a playful animated world for a fictional stationery studio. Keep characters consistent and use an expressive, hand-crafted visual language.',
         'Three 10-second animations, character sheets and an editable animation project.', 'Playful, tactile, colourful', '1:1', 21),
        ('Product animation for a modular desk lamp', 'ANIMATION', 'PRODUCT', 'Product visualization', 'Blender', 'Product campaigns', 45000, 85000,
         'Show how a fictional modular desk lamp transforms between working and ambient light. Emphasize product geometry, material finish and clear functional storytelling.',
         'One 20-second animation, three detail loops and six rendered stills.', 'Sculptural, premium, minimal', '16:9', 18),
    ]),
    ('Form & Field', [
        ('Product photography campaign', 'PHOTOGRAPHY', 'PRODUCT', 'Product visualization', 'Adobe Firefly', 'Product campaigns', 20000, 45000,
         'Create a consistent set of lifestyle and studio images for fictional ceramic homeware. Explore natural textures, soft daylight and careful product proportions.',
         'Eight retouched product images, four lifestyle compositions and layered source files.', 'Natural, tactile, warm', '4:5', 12),
        ('AI-generated advertising content', 'AI_CONTENT', 'IMAGE', 'Art direction', 'Midjourney', 'Product campaigns', 30000, 55000,
         'Explore three original advertising directions for a fictional botanical beverage. Keep packaging readable, use original compositions and document the creative process.',
         'Three concept routes, six finished ads and workflow notes covering tools and source materials.', 'Fresh, surreal, colourful', '4:5', 14),
    ]),
    ('Coral Social', [
        ('Social media content collection', 'DESIGN', 'IMAGE', 'Motion design', 'After Effects', 'Product campaigns', 15000, 35000,
         'Design a cohesive social campaign for a fictional neighbourhood food festival. Use clear hierarchy, accessible type and a bright, inviting visual system.',
         'Six static posts, three animated stories and reusable design templates.', 'Bright, welcoming, graphic', '9:16', 10),
        ('YouTube video editing for a creative series', 'EDITING', 'FILM', 'Cinematic storytelling', 'DaVinci Resolve', 'AI filmmaking', 18000, 32000,
         'Edit an eight-minute episode of a fictional design documentary series. Shape the interview narrative, integrate supplied B-roll and add clear motion titles.',
         'One eight-minute edit, a thumbnail, subtitles and three short promotional clips.', 'Documentary, engaging, natural', '16:9', 7),
    ]),
]


def require_local_seed_database():
    if not settings.DEBUG or connection.vendor != 'sqlite':
        raise CommandError('Demo seeding requires DEBUG=True and a local/test SQLite database. Production/PostgreSQL seeding is disabled.')
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):
        raise CommandError('Apply pending migrations first: python manage.py migrate. Then rerun the seed command.')


@transaction.atomic
def seed_brands(projects_per_brand=2):
    require_local_seed_database()
    created_brands = created_projects = 0
    for index, (brand_name, projects) in enumerate(CATALOG, 1):
        username = f'creo_opportunity_demo_{index:02}'
        email = f'{username}@example.invalid'
        public_name = f'{brand_name} — fictional demo'
        user = User.objects.filter(Q(username=username) | Q(email__iexact=email)).first()
        if user:
            # Only extend an identifiable old demo with an unusable password.
            # A colliding real account or unmarked existing user is never claimed.
            if (user.username != username or user.email != email or user.role != 'BRAND'
                    or user.has_usable_password() or not user.job_postings.filter(is_demo=True, brand_name=public_name).exists()):
                continue
        else:
            user = User(username=username, email=email, first_name=f'{brand_name} (fictional)', role='BRAND')
            user.set_unusable_password()
            user.save()
            created_brands += 1
        existing_count = user.job_postings.count()
        # Do not recreate edited titles or refresh dates/statuses on later runs.
        for item in projects[existing_count:projects_per_brand]:
            title, work, content, skill, tool, spec, low, high, description, deliverables, style, aspect, duration = item
            if user.job_postings.filter(title=title).exists():
                continue
            posting = JobPosting.objects.create(brand=user, brand_name=public_name, title=title,
                description=description + '\n\nFictional demo project for local development. This is not a real job offer.',
                work_type=work, content_type=content, style=style, aspect_ratio=aspect,
                deliverables=deliverables, output_format='MP4 / JPG exports, editable source files and a short handover guide.',
                budget_min=low, budget_max=high, currency='INR', duration_days=duration, min_experience_years=1,
                application_deadline=timezone.localdate() + timedelta(days=21), deadline=timezone.localdate() + timedelta(days=60),
                additional_requirements='Fictional demo. For real work, agree on usage rights, cleared source materials and one revision round before starting.',
                status='OPEN', published_at=timezone.now(), is_demo=True)
            posting.skills.add(Skill.objects.get_or_create(name=skill)[0])
            posting.tools.add(Tool.objects.get_or_create(name=tool)[0])
            posting.specializations.add(Specialization.objects.get_or_create(name=spec)[0])
            created_projects += 1
    return created_brands, created_projects
