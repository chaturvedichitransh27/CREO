from django.core.management.base import BaseCommand
from opportunities.demo import seed_brands


class Command(BaseCommand):
    help = 'Explicitly seed up to five fictional brands and ten projects in a DEBUG local/test SQLite database. Never overwrites records.'

    def handle(self, *args, **options):
        brands, projects = seed_brands()
        self.stdout.write(self.style.SUCCESS(f'Created {brands} fictional brands and {projects} demo projects. No usable credentials created.'))
