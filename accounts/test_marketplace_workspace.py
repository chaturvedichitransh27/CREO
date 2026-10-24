from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command, CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from creators.models import CreatorProfile
from engagements.models import Engagement
from opportunities.models import Application, JobPosting, Notification
from opportunities.tests import OpportunityTestCase


class WorkspaceMarketplaceTests(OpportunityTestCase):
    def setUp(self):
        self.open_posting()

    def test_creator_home_shows_open_projects_from_every_brand(self):
        other = self.make_posting(brand=self.other, title='Another brand project', status='OPEN', published_at=timezone.now())
        draft = self.make_posting(title='Private unpublished project')
        closed = self.make_posting(title='Closed project', status='CLOSED', published_at=timezone.now())
        expired = self.make_posting(title='Expired project', status='OPEN', published_at=timezone.now(), application_deadline=timezone.localdate() - timedelta(days=1))
        self.posting.skills.add(self.skill)
        self.posting.tools.add(self.tool)
        self.client.force_login(self.creator_user)
        response = self.client.get(reverse('dashboard'))
        for text in [self.posting.title, other.title, self.posting.brand_name, self.skill.name, self.tool.name, "I'm Interested"]:
            self.assertContains(response, text)
        for text in [draft.title, closed.title, expired.title]:
            self.assertNotContains(response, text)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotContains(response, 'class="container workspace-shortcuts"')

    def test_direct_interest_optional_message_and_applied_state(self):
        self.client.force_login(self.creator_user)
        url = reverse('opportunities:apply', args=[self.posting.pk])
        response = self.client.post(url, {'version': 0, 'return_to_workspace': '1', 'message': 'I love this creative direction.'})
        self.assertRedirects(response, reverse('dashboard') + '#available-projects')
        application = Application.objects.get(posting=self.posting, creator=self.creator)
        self.assertEqual(application.message, 'I love this creative direction.')
        self.assertFalse(application.share_email)
        self.assertFalse(application.share_phone)
        self.assertEqual(application.creator_email, '')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, '>Applied</p>')
        self.assertNotContains(response, f'action="{url}"')
        self.assertContains(response, application.get_absolute_url())
        self.client.post(url, {'version': 0, 'return_to_workspace': '1'})
        self.assertEqual(Application.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(kind='APPLIED').count(), 1)

    def test_direct_interest_without_message_and_no_open_redirect(self):
        self.client.force_login(self.creator_user)
        response = self.client.post(reverse('opportunities:apply', args=[self.posting.pk]),
            {'version': 0, 'return_to_workspace': '1', 'next': 'https://malicious.example/'})
        self.assertEqual(response.url, reverse('dashboard') + '#available-projects')
        self.assertEqual(Application.objects.get().message, '')

    def test_invalid_message_keeps_existing_validation(self):
        self.client.force_login(self.creator_user)
        response = self.client.post(reverse('opportunities:apply', args=[self.posting.pk]),
            {'version': 0, 'return_to_workspace': '1', 'message': 'x' * 3001})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)
        self.assertFalse(Application.objects.exists())

    def test_applied_state_and_notifications_are_creator_specific(self):
        other_user = User.objects.create_user(username='workspace-other', email='workspace-other@example.com', role='CREATOR')
        other_creator = CreatorProfile.objects.create(user=other_user, display_name='Other creator')
        app = Application.objects.create(creator=other_creator, posting=self.posting)
        Notification.objects.create(application=app, recipient=other_user, kind='CONSIDERING')
        self.client.force_login(self.creator_user)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['application_count'], 0)
        self.assertEqual(response.context['unread_count'], 0)
        self.assertIsNone(response.context['available_projects'][0].viewer_application_id)

    def test_creator_home_is_paginated_without_hiding_older_projects(self):
        for index in range(8):
            self.make_posting(title=f'Open project {index}', status='OPEN', published_at=timezone.now())
        self.client.force_login(self.creator_user)
        page1 = self.client.get(reverse('dashboard')).context['available_projects']
        page2 = self.client.get(reverse('dashboard'), {'projects_page': 2}).context['available_projects']
        self.assertEqual(len(page1), 6)
        self.assertEqual(len(page2), 3)
        self.assertFalse({job.pk for job in page1} & {job.pk for job in page2})
        self.assertEqual(self.client.get(reverse('dashboard'), {'projects_page': 'invalid'}).status_code, 200)

    def test_brand_home_uses_existing_search_for_skills_tools_and_names(self):
        self.creator.skills.add(self.skill)
        self.creator.tools.add(self.tool)
        self.client.force_login(self.brand)
        for search in [self.creator.display_name, self.skill.name, self.tool.name]:
            response = self.client.get(reverse('dashboard'), {'q': search})
            self.assertEqual(list(response.context['creator_page']), [self.creator])
            self.assertContains(response, 'View Profile')
            self.assertContains(response, self.skill.name)
            self.assertContains(response, self.tool.name)
        self.assertEqual(self.client.get(reverse('dashboard'), {'q': 'does-not-match'}).context['creator_page'].paginator.count, 0)
        self.assertTrue(self.client.get(reverse('dashboard'), {'q': 'x' * 121}).context['creator_search'].errors)

    def test_brand_home_excludes_inactive_profiles_and_other_brand_projects(self):
        self.creator_user.is_active = False
        self.creator_user.save()
        self.make_posting(brand=self.other, title='Other brand private draft')
        self.client.force_login(self.brand)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.context['creator_page'].paginator.count, 0)
        self.assertNotContains(response, 'Other brand private draft')
        self.assertContains(response, reverse('opportunities:applicants', args=[self.posting.pk]))

    def test_existing_tools_stay_accessible_in_secondary_navigation(self):
        self.client.force_login(self.brand)
        response = self.client.get(reverse('dashboard'))
        for route in ['studio:index', 'brief_builder', 'brief_create', 'creator_compare', 'engagement_inbox', 'opportunities:index']:
            self.assertContains(response, reverse(route))
        self.client.force_login(self.creator_user)
        response = self.client.get(reverse('dashboard'))
        for route in ['profile_edit', 'portfolio_create', 'evidence_list', 'engagement_inbox', 'opportunities:my_applications']:
            self.assertContains(response, reverse(route))
        self.assertContains(response, self.creator.get_absolute_url())

    def test_workspace_application_csrf_and_roles_remain_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.creator_user)
        url = reverse('opportunities:apply', args=[self.posting.pk])
        self.assertEqual(client.post(url, {'version': 0, 'return_to_workspace': '1'}).status_code, 403)
        self.client.force_login(self.brand)
        self.assertEqual(self.client.post(url, {'version': 0, 'return_to_workspace': '1'}).status_code, 403)

    def test_dashboard_journey_from_new_brand_to_existing_collaboration(self):
        brand_client = Client()
        response = brand_client.post(reverse('register'), {'first_name': 'New Workspace Brand', 'username': 'new-workspace-brand',
            'email': 'workspace-brand@example.com', 'role': 'BRAND', 'password1': 'Workspace-test-only-2026!', 'password2': 'Workspace-test-only-2026!'})
        self.assertEqual(response.status_code, 302)
        response = brand_client.post(reverse('opportunities:create'), self.payload(title='New workspace opportunity', action='publish', publish_consent='on'))
        self.assertEqual(response.status_code, 302)
        posting = JobPosting.objects.get(title='New workspace opportunity')
        self.client.force_login(self.creator_user)
        self.assertContains(self.client.get(reverse('dashboard')), posting.title)
        response = self.client.post(reverse('opportunities:apply', args=[posting.pk]), {'version': 0, 'return_to_workspace': '1'})
        self.assertRedirects(response, reverse('dashboard') + '#available-projects')
        app = Application.objects.get(posting=posting)
        self.assertContains(brand_client.get(reverse('dashboard')), '1 applicant')
        self.assertContains(brand_client.get(reverse('opportunities:applicants', args=[posting.pk])), self.creator.display_name)
        self.assertEqual(brand_client.get(self.creator.get_absolute_url()).status_code, 200)
        ranked = brand_client.post(reverse('opportunities:recommendations', args=[posting.pk]))
        self.assertEqual([match['profile'].pk for match in ranked.context['matches']], [self.creator.pk])
        brand_client.post(reverse('opportunities:considering', args=[app.pk]), {'version': 0, 'brand_email': 'business@example.com', 'brand_share_email': 'on'})
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Brand is considering you')
        self.assertContains(response, posting.brand_name)
        self.assertContains(response, posting.title)
        response = brand_client.post(reverse('opportunities:collaborate', args=[app.pk]), {'message': 'Let us make this project together.', 'share': 'on'})
        self.assertEqual(response.status_code, 302)
        engagement = Engagement.objects.get(posting=posting)
        response = self.client.post(engagement.get_absolute_url(), {'status': 'ACCEPTED', 'version': 0})
        self.assertEqual(response.status_code, 302)
        engagement.refresh_from_db()
        self.assertEqual(engagement.status, 'ACCEPTED')
        self.assertEqual(brand_client.get(reverse('studio:index')).status_code, 200)
        self.assertEqual(brand_client.get(reverse('brief_builder')).status_code, 200)


@override_settings(DEBUG=True)
class DemoBrandSeedTests(TestCase):
    def seed(self):
        call_command('seed_demo_brands', stdout=StringIO())

    def test_five_brands_ten_realistic_jobs_and_unusable_credentials(self):
        self.seed()
        self.assertEqual(User.objects.filter(role='BRAND').count(), 5)
        self.assertEqual(JobPosting.objects.count(), 10)
        for job in JobPosting.objects.all():
            self.assertTrue(job.is_demo)
            self.assertIn('fictional demo', job.brand_name)
            self.assertFalse(job.brand.has_usable_password())
            self.assertGreater(job.application_deadline, timezone.localdate())
            self.assertGreater(job.deadline, job.application_deadline)
            self.assertTrue(job.skills.exists())
            self.assertTrue(job.tools.exists())
            job.full_clean()

    def test_repeated_seed_preserves_records_and_user_edits(self):
        self.seed()
        job = JobPosting.objects.first()
        job.title = 'User edited demo title'
        job.status = 'CLOSED'
        job.save()
        before = list(JobPosting.objects.values())
        users = list(User.objects.values())
        self.seed()
        self.assertEqual(before, list(JobPosting.objects.values()))
        self.assertEqual(users, list(User.objects.values()))

    def test_old_seed_upgrades_without_duplicate_brands_or_rewriting_jobs(self):
        call_command('seed_opportunities', development=True, stdout=StringIO())
        original = {job.pk: (job.title, job.updated_at, job.deadline) for job in JobPosting.objects.all()}
        self.seed()
        self.assertEqual(User.objects.filter(role='BRAND').count(), 5)
        self.assertEqual(JobPosting.objects.count(), 10)
        for job in JobPosting.objects.filter(pk__in=original):
            self.assertEqual(original[job.pk], (job.title, job.updated_at, job.deadline))

    def test_existing_username_or_email_collision_is_never_claimed(self):
        username_owner = User.objects.create_user(username='creo_opportunity_demo_01', email='real@example.com', role='BRAND')
        email_owner = User.objects.create_user(username='another-real-brand', email='creo_opportunity_demo_02@example.invalid', role='BRAND')
        self.seed()
        self.assertEqual(JobPosting.objects.count(), 6)
        self.assertFalse(JobPosting.objects.filter(brand__in=[username_owner, email_owner]).exists())

    @override_settings(DEBUG=False)
    def test_production_is_refused(self):
        with self.assertRaises(CommandError):
            self.seed()
        self.assertFalse(User.objects.exists())

    def test_postgresql_is_refused_even_when_debug(self):
        with patch('opportunities.demo.connection.vendor', 'postgresql'), self.assertRaises(CommandError):
            self.seed()
        self.assertFalse(User.objects.exists())

    def test_pending_migrations_give_actionable_error_without_creating_users(self):
        with patch('django.db.migrations.executor.MigrationExecutor.migration_plan', return_value=[object()]):
            with self.assertRaisesMessage(CommandError, 'python manage.py migrate'):
                self.seed()
        self.assertFalse(User.objects.exists())
