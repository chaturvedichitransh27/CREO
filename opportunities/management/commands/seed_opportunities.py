"""Backward-compatible five-project seed using the shared fictional catalog."""
from django.core.management.base import BaseCommand, CommandError
from opportunities.demo import seed_brands


class Command(BaseCommand):
    help = 'Create five fictional brand opportunities in a DEBUG local/test SQLite database.'

    def add_arguments(self, parser):
        parser.add_argument('--development', action='store_true', help='Confirm this is a local development database.')

    def handle(self, *args, **options):
        if not options['development']:
            raise CommandError('Use --development with a DEBUG local/test SQLite database.')
        brands, projects = seed_brands(projects_per_brand=1)
        self.stdout.write(self.style.SUCCESS(f'Created {projects} fictional brand opportunities. No usable passwords were created.'))
