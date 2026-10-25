from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from briefs.models import Brief
from .models import Application, JobPosting
from .selectors import public_brand_projects
from .tests import OpportunityTestCase
from .views import brands


class BrandDirectoryTests(OpportunityTestCase):
    def setUp(self):
        self.client.force_login(self.creator_user)
        self.open_posting()
        self.directory = reverse('opportunities:brands')

    def test_real_publications_appear_once_per_brand_and_link_to_existing_overview(self):
        newest = self.make_posting(title='Newest public direction', status='CLOSED', published_at=timezone.now())
        response = self.client.get(self.directory)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(response.context['page_obj'][0], newest)
        self.assertContains(response, '1 open project')
        self.assertContains(response, reverse('opportunities:brand', args=[self.brand.pk]))
        self.assertContains(response, newest.get_absolute_url())
        self.assertEqual(self.client.get(reverse('opportunities:brand', args=[self.brand.pk])).status_code, 200)
        other = User.objects.create_user(username='fresh-real-brand', role='BRAND', email='fresh-private@example.invalid')
        self.make_posting(brand=other, brand_name='New Published Brand', status='OPEN', published_at=timezone.now())
        response = self.client.get(self.directory)
        self.assertEqual(response.context['page_obj'].paginator.count, 2)
        self.assertContains(response, 'New Published Brand')

    def test_private_records_do_not_appear_or_match_search(self):
        self.make_posting(brand_name='Secret Draft Brand', title='Secret Draft Title', description='Private draft description')
        self.make_posting(brand=self.other, title='Never published account')
        self.brand.first_name = 'Private legal name'
        self.brand.save()
        Brief.objects.create(brand=self.brand, title='Secret private brief', budget_min=1, budget_max=2, deadline=timezone.localdate())
        Application.objects.create(posting=self.posting, creator=self.creator, message='Secret application message',
            creator_email='consented-private@example.invalid', creator_phone='123456789', share_email=True, share_phone=True,
            brand_email='private-professional@example.invalid', brand_share_email=True, consideration_status='CONSIDERING')
        secrets = ['Secret Draft Brand', 'Secret Draft Title', 'Private draft description', 'Never published account',
            self.brand.email, self.brand.username, 'Private legal name', 'Secret private brief', 'Secret application message',
            'consented-private@example.invalid', '123456789', 'private-professional@example.invalid']
        response = self.client.get(self.directory)
        for secret in secrets:
            self.assertNotContains(response, secret)
            self.assertEqual(self.client.get(self.directory, {'q': secret}).context['page_obj'].paginator.count, 0)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_search_matches_older_published_projects_and_taxonomies_without_duplicate_cards(self):
        self.posting.skills.add(self.skill)
        self.posting.tools.add(self.tool)
        self.posting.specializations.add(self.specialization)
        self.make_posting(title='An unrelated newest project', status='OPEN', published_at=timezone.now())
        for query in [self.posting.title, self.posting.description, self.posting.brand_name,
                      self.skill.name, self.tool.name, self.specialization.name, '  film  ']:
            response = self.client.get(self.directory, {'q': query})
            self.assertEqual(response.context['page_obj'].paginator.count, 1, query)
            self.assertEqual(response.context['page_obj'][0].open_project_count, 2)
        response = self.client.get(self.directory, {'q': self.skill.name})
        self.assertEqual(response.context['page_obj'][0], self.posting)
        self.assertContains(response, self.skill.name)
        self.assertContains(response, self.tool.name)
        self.assertContains(response, self.specialization.name)

    def test_open_counts_reuse_availability_today_inclusive_and_expired_excluded(self):
        today = timezone.localdate()
        JobPosting.objects.filter(pk=self.posting.pk).update(application_deadline=today)
        self.make_posting(status='OPEN', published_at=timezone.now(), application_deadline=today - timedelta(days=1))
        self.make_posting(status='CLOSED', published_at=timezone.now())
        self.make_posting(title='Private future draft')
        self.assertEqual(public_brand_projects().get().open_project_count, 1)
        with patch('django.utils.timezone.localdate', return_value=today + timedelta(days=1)):
            response = self.client.get(self.directory)
            self.assertEqual(response.context['page_obj'][0].open_project_count, 0)
            self.assertContains(response, 'No projects currently accepting applications')
            self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_pagination_is_unique_and_search_is_preserved(self):
        for index in range(13):
            user = User.objects.create_user(username=f'discover-brand-{index}', role='BRAND', email=f'discover-{index}@example.invalid')
            for title in ['Earlier', 'Later']:
                self.make_posting(brand=user, brand_name=f'Catalog Brand {index}', title=title, status='OPEN', published_at=timezone.now())
        first = self.client.get(self.directory, {'q': 'Catalog', 'page': 1})
        second = self.client.get(self.directory, {'q': 'Catalog', 'page': 2})
        self.assertEqual(len(first.context['page_obj']), 12)
        self.assertEqual(len(second.context['page_obj']), 1)
        self.assertFalse({p.brand_id for p in first.context['page_obj']} & {p.brand_id for p in second.context['page_obj']})
        self.assertContains(first, 'q=Catalog')
        self.assertContains(first, 'page=2')
        self.assertEqual(self.client.get(self.directory, {'page': 'invalid'}).status_code, 200)

    def test_empty_invalid_search_and_escaping(self):
        self.assertContains(self.client.get(self.directory, {'q': 'no-such-brand'}), 'No published brands match')
        response = self.client.get(self.directory, {'q': 'x' * 141})
        self.assertTrue(response.context['search_form'].errors)
        self.assertEqual(response.context['page_obj'].paginator.count, 0)
        self.assertNotContains(self.client.get(self.directory, {'q': '<script>bad()</script>'}), '<script>bad()</script>')
        JobPosting.objects.filter(pk=self.posting.pk).update(brand_name='<script>brand()</script>')
        response = self.client.get(self.directory)
        self.assertContains(response, '&lt;script&gt;brand()&lt;/script&gt;')
        self.assertNotContains(response, '<script>brand()</script>')

    def test_active_creator_only_and_read_only(self):
        self.assertEqual(self.client.post(self.directory).status_code, 405)
        self.assertIn('no-store', self.client.get(self.directory)['Cache-Control'])
        self.client.logout()
        self.assertRedirects(self.client.get(self.directory), reverse('login') + '?next=' + self.directory)
        self.client.force_login(self.brand)
        self.assertEqual(self.client.get(self.directory).status_code, 403)
        request = RequestFactory().get(self.directory)
        self.creator_user.is_active = False
        request.user = self.creator_user
        with self.assertRaises(PermissionDenied):
            brands(request)

    def test_brand_cards_load_taxonomies_in_constant_queries(self):
        self.posting.skills.add(self.skill)
        self.posting.tools.add(self.tool)
        with CaptureQueriesContext(connection) as single:
            self.client.get(self.directory)
        for index in range(5):
            user = User.objects.create_user(username=f'query-brand-{index}', email=f'query-brand-{index}@example.invalid', role='BRAND')
            posting = self.make_posting(brand=user, status='OPEN', published_at=timezone.now())
            posting.skills.add(self.skill)
            posting.tools.add(self.tool)
        with CaptureQueriesContext(connection) as multiple:
            self.client.get(self.directory)
        self.assertEqual(len(single), len(multiple))

    @override_settings(DEBUG=True)
    def test_existing_demo_catalog_is_labeled_and_idempotent_in_discovery(self):
        output = StringIO()
        call_command('seed_demo_brands', stdout=output)
        before = list(JobPosting.objects.filter(is_demo=True).values())
        call_command('seed_demo_brands', stdout=output)
        self.assertEqual(before, list(JobPosting.objects.filter(is_demo=True).values()))
        response = self.client.get(self.directory, {'q': 'fictional demo'})
        self.assertEqual(response.context['page_obj'].paginator.count, 5)
        for posting in response.context['page_obj']:
            self.assertTrue(posting.is_demo)
            self.assertEqual(posting.open_project_count, 2)
            self.assertFalse(posting.brand.has_usable_password())
            self.assertContains(response, posting.get_absolute_url())
        for name in ['Lumen Studio', 'Velvet Tomorrow', 'Orbit Paper', 'Form &amp; Field', 'Coral Social']:
            self.assertContains(response, name)
        self.assertContains(response, 'Fictional demo brand', count=6)  # five cards and directory disclosure
        self.assertContains(response, 'not real job offers')
