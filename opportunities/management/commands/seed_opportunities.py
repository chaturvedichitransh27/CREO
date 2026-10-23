"""Explicit, local-development-only fictional opportunities; no usable credentials."""
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from accounts.models import User
from creators.models import Skill, Tool, Specialization
from opportunities.models import JobPosting


class Command(BaseCommand):
    help = 'Create five fictional brands/projects in a DEBUG SQLite development database only.'

    def add_arguments(self, parser):
        parser.add_argument('--development', action='store_true', help='Explicitly confirm this is a local development database.')

    @transaction.atomic
    def handle(self, *args, **options):
        if not options['development'] or not settings.DEBUG or connection.vendor != 'sqlite':
            raise CommandError('Use --development with DEBUG=True and a local SQLite database. PostgreSQL/production seeding is disabled.')
        examples = [
            ('Lumen Studio', 'Cinematic launch film editing', 'EDITING', 'FILM', 'Cinematic storytelling', 'DaVinci Resolve', 'AI filmmaking', 40000, 70000),
            ('Velvet Tomorrow', 'Fashion advertisement production', 'FILMMAKING', 'FILM', 'Art direction', 'Runway', 'Fashion & beauty', 60000, 120000),
            ('Orbit Paper', 'Playful AI animation series', 'ANIMATION', 'ANIMATION', 'Animation', 'Blender', 'Animation', 30000, 60000),
            ('Form & Field', 'Product photography campaign', 'PHOTOGRAPHY', 'PRODUCT', 'Product visualization', 'Adobe Firefly', 'Product campaigns', 20000, 45000),
            ('Coral Social', 'Social media content collection', 'DESIGN', 'IMAGE', 'Motion design', 'After Effects', 'Product campaigns', 15000, 35000),
        ]
        created = 0
        for index, (brand, title, work_type, content_type, skill, tool, spec, low, high) in enumerate(examples, 1):
            username = f'creo_opportunity_demo_{index:02}'
            email = f'{username}@example.invalid'
            if User.objects.filter(Q(username=username) | Q(email__iexact=email)).exists():
                continue  # Never claim or modify a pre-existing account, even a demo.
            user = User(username=username, email=email, first_name=f'{brand} (fictional)', role='BRAND')
            user.set_unusable_password()
            user.save()
            posting = JobPosting.objects.create(brand=user, brand_name=f'{brand} — fictional demo',
                title=title, description=f'Fictional development project: {title.lower()}. Develop a thoughtful visual concept, share an initial treatment and deliver a polished creative collection. This is not a real job offer.',
                work_type=work_type, content_type=content_type, style='Cinematic, expressive, modern', aspect_ratio='16:9',
                deliverables='Three campaign assets, an initial treatment, and one revision round.', output_format='MP4 / JPG exports and editable source files',
                budget_min=low, budget_max=high, currency='INR', duration_days=21, min_experience_years=1,
                application_deadline=timezone.localdate() + timedelta(days=14), deadline=timezone.localdate() + timedelta(days=45),
                additional_requirements='Fictional demo for local testing only. Discuss usage and source licensing before real work.',
                status='OPEN', published_at=timezone.now(), is_demo=True)
            posting.skills.add(Skill.objects.get_or_create(name=skill)[0])
            posting.tools.add(Tool.objects.get_or_create(name=tool)[0])
            posting.specializations.add(Specialization.objects.get_or_create(name=spec)[0])
            created += 1
        self.stdout.write(self.style.SUCCESS(f'Created {created} fictional brand opportunities. No usable passwords were created.'))
