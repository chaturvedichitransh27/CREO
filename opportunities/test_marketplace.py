from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from creators.models import CreatorProfile
from engagements.models import Engagement
from portfolio.models import PortfolioItem
from .forms import ApplicationForm, ContactConsentForm, ConsideringForm
from .models import Application, JobPosting, Notification
from .services import apply_to_posting, top_applicants, approved_contacts, consider_application
from .tests import OpportunityTestCase


class MarketplaceTests(OpportunityTestCase):
    def setUp(self):
        self.open_posting()

    def route(self, name, pk=None):
        return reverse('opportunities:' + name, args=[pk] if pk is not None else [])

    def submit(self, user=None, **fields):
        self.client.force_login(user or self.creator_user)
        data = {'message': 'My actual application.', 'version': 0, **fields}
        response = self.client.post(self.route('apply', self.posting.pk), data)
        self.assertEqual(response.status_code, 302)
        return Application.objects.get(posting=self.posting, creator__user=user or self.creator_user)

    def consider(self, application):
        self.client.force_login(self.brand)
        response = self.client.post(self.route('considering', application.pk),
            {'version': application.version, 'brand_email': 'professional-brand@example.com', 'brand_share_email': 'on'})
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()

    def test_duplicate_submission_is_idempotent_and_notifies_brand_once(self):
        app = self.submit()
        self.submit(message='A repeated click must not overwrite the first application.')
        self.assertEqual(Application.objects.count(), 1)
        app.refresh_from_db()
        self.assertEqual(app.message, 'My actual application.')
        self.assertEqual(Notification.objects.filter(recipient=self.brand, kind='APPLIED').count(), 1)
        self.assertContains(self.client.get(self.route('detail', self.posting.pk)), 'View my application')
        self.assertContains(self.client.get(self.route('my_applications')), self.posting.title)

    def test_contact_details_hidden_until_considering_and_revoke_immediately(self):
        app = self.submit(creator_email='approved-creator@example.com', share_email='on', creator_phone='+44 123 456 789', share_phone='on')
        self.client.force_login(self.brand)
        response = self.client.get(app.get_absolute_url())
        self.assertNotContains(response, 'approved-creator@example.com')
        self.assertNotContains(response, '+44 123 456 789')
        self.consider(app)
        response = self.client.get(app.get_absolute_url())
        self.assertContains(response, 'approved-creator@example.com')
        self.assertContains(response, 'Contact Creator')
        self.assertNotContains(response, self.creator_user.email)
        self.client.force_login(self.creator_user)
        self.assertContains(self.client.get(app.get_absolute_url()), 'professional-brand@example.com')
        self.assertNotContains(self.client.get(app.get_absolute_url()), self.brand.email)
        self.client.post(self.route('contact_consent', app.pk), {'version': app.version})
        app.refresh_from_db()
        self.assertEqual(app.creator_email, '')
        self.assertEqual(app.creator_phone, '')
        self.client.force_login(self.brand)
        response = self.client.get(app.get_absolute_url())
        self.assertNotContains(response, 'approved-creator@example.com')
        self.assertContains(response, 'Request contact permission')
        self.client.post(self.route('brand_contact', app.pk), {'version': app.version})
        self.client.force_login(self.creator_user)
        self.assertNotContains(self.client.get(app.get_absolute_url()), 'professional-brand@example.com')

    def test_unconsented_data_not_stored_and_permissions_can_be_requested(self):
        app = self.submit(creator_email='not-authorized@example.com', creator_phone='123456789')
        self.assertEqual(app.creator_email, '')
        self.assertEqual(app.creator_phone, '')
        self.consider(app)
        for _ in range(2):
            self.client.post(self.route('contact_request', app.pk))
        self.assertEqual(Notification.objects.filter(application=app, kind='CONTACT_REQUEST').count(), 1)
        self.client.force_login(self.creator_user)
        self.assertContains(self.client.get(reverse('engagement_inbox')), 'requested permission')
        response = self.client.post(self.route('contact_consent', app.pk), {'version': app.version, 'creator_email': 'now-approved@example.com', 'share_email': 'on'})
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertEqual(approved_contacts(app)['creator_email'], 'now-approved@example.com')

    def test_repeated_considering_deduplicates_notifications(self):
        app = self.submit()
        self.consider(app)
        self.consider(app)
        self.assertEqual(Notification.objects.filter(application=app, kind='CONSIDERING').count(), 1)
        self.client.force_login(self.creator_user)
        response = self.client.get(reverse('engagement_inbox'))
        self.assertContains(response, 'is considering you')
        notification = Notification.objects.get(application=app, kind='CONSIDERING')
        self.assertRedirects(self.client.post(self.route('notification_read', notification.pk)), app.get_absolute_url())
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

    def test_private_applications_and_actions_are_role_and_owner_scoped(self):
        app = self.submit()
        second_user = User.objects.create_user(username='other-creator', email='oc@example.com', role='CREATOR')
        CreatorProfile.objects.create(user=second_user, display_name='Other creator')
        for user in [self.other, second_user]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(app.get_absolute_url()).status_code, 404)
        self.client.force_login(self.other)
        for route in ['considering', 'collaborate', 'brand_contact']:
            self.assertEqual(self.client.get(self.route(route, app.pk)).status_code, 404)
            self.assertEqual(self.client.post(self.route(route, app.pk), {'version': 0}).status_code, 404)
        self.assertEqual(self.client.post(self.route('contact_request', app.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.route('recommendations', self.posting.pk)).status_code, 404)
        self.client.force_login(self.creator_user)
        for route in ['considering', 'collaborate', 'brand_contact']:
            self.assertEqual(self.client.post(self.route(route, app.pk)).status_code, 403)
        self.client.force_login(self.brand)
        self.assertEqual(self.client.post(self.route('apply', self.posting.pk)).status_code, 403)
        self.assertEqual(self.client.post(self.route('contact_consent', app.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.route('my_applications')).status_code, 403)

    def test_csrf_and_post_only_actions(self):
        app = self.submit()
        client = Client(enforce_csrf_checks=True)
        for user, names in [(self.brand, ['considering', 'collaborate', 'contact_request', 'brand_contact']),
                            (self.creator_user, ['contact_consent'])]:
            client.force_login(user)
            for name in names:
                self.assertEqual(client.post(self.route(name, app.pk)).status_code, 403)
        client.force_login(self.creator_user)
        self.assertEqual(client.post(self.route('apply', self.posting.pk)).status_code, 403)
        client.force_login(self.brand)
        self.assertEqual(client.post(self.route('recommendations', self.posting.pk)).status_code, 403)
        self.client.force_login(self.brand)
        self.assertEqual(self.client.get(self.route('contact_request', app.pk)).status_code, 405)
        self.assertEqual(self.client.get(self.route('recommendations', self.posting.pk)).status_code, 405)
        notification = Notification.objects.get(kind='APPLIED')
        self.assertEqual(self.client.get(self.route('notification_read', notification.pk)).status_code, 405)
        self.client.force_login(self.creator_user)
        self.assertEqual(self.client.post(self.route('notification_read', notification.pk)).status_code, 404)

    def test_closed_and_expired_postings_reject_new_applications(self):
        form = ApplicationForm({'version': 0})
        self.assertTrue(form.is_valid())
        for update in [{'status': 'CLOSED'}, {'status': 'OPEN', 'application_deadline': timezone.localdate() - timedelta(days=1)}]:
            JobPosting.objects.filter(pk=self.posting.pk).update(**update)
            with self.assertRaises(ValidationError):
                apply_to_posting(self.creator_user, self.posting.pk, form.cleaned_data)
        self.assertFalse(Application.objects.exists())

    def test_stale_consent_cannot_undo_newer_choices(self):
        app = self.submit()
        self.consider(app)
        self.client.force_login(self.creator_user)
        data = {'version': app.version, 'share_email': 'on', 'creator_email': 'new-choice@example.com'}
        self.client.post(self.route('contact_consent', app.pk), data)
        response = self.client.post(self.route('contact_consent', app.pk), {'version': app.version})
        self.assertContains(response, 'changed')
        app.refresh_from_db()
        self.assertEqual(app.creator_email, 'new-choice@example.com')

    def test_consent_validation_and_application_message_limit(self):
        for data in [{'share_email': 'on'}, {'share_phone': 'on'}, {'creator_email': 'invalid', 'share_email': 'on'},
                     {'creator_phone': 'javascript:alert(1)', 'share_phone': 'on'}, {'message': 'x' * 3001}, {'version': -1}]:
            form = ApplicationForm({'version': 0, **data})
            self.assertFalse(form.is_valid(), data)
        self.assertFalse(ConsideringForm({'version': 0, 'brand_email': 'brand@example.com'}).is_valid())

    def test_public_brand_overview_uses_only_published_content(self):
        draft = self.make_posting(title='Secret draft', brand_name='Secret brand name')
        response = self.client.get(self.route('brand', self.brand.pk))
        self.assertContains(response, 'Public Studio')
        for secret in [draft.title, draft.brand_name, self.brand.email, self.brand.username]:
            self.assertNotContains(response, secret)
        self.assertEqual(self.client.get(self.route('brand', self.other.pk)).status_code, 404)

    def test_feed_filters_live_data_and_currency_without_conversion(self):
        self.posting.skills.add(self.skill)
        self.posting.tools.add(self.tool)
        filters = {'q': 'Public Studio', 'skill': self.skill.pk, 'tool': self.tool.pk, 'work_type': 'FILMMAKING',
                   'currency': 'INR', 'budget_min': 500, 'budget_max': 2000}
        self.assertContains(self.client.get(self.route('explore'), filters), self.posting.title)
        self.assertNotContains(self.client.get(self.route('explore'), {'currency': 'USD'}), self.posting.title)
        response = self.client.get(self.route('explore'), {'budget_min': 50})
        self.assertTrue(response.context['filter_form'].errors)
        self.posting.status = 'CLOSED'
        self.posting.save()
        self.assertNotContains(self.client.get(self.route('explore')), self.posting.title)
        self.assertContains(self.client.get(self.route('explore'), {'include_closed': 'on'}), self.posting.title)

    def test_feed_status_and_pagination_preserve_filters(self):
        app = self.submit()
        response = self.client.get(self.route('explore'))
        self.assertContains(response, 'Application: Submitted')
        for index in range(14):
            self.make_posting(title=f'Film {index}', status='OPEN', published_at=timezone.now())
        response = self.client.get(self.route('explore'), {'currency': 'INR', 'q': 'film'})
        self.assertEqual(len(response.context['page_obj']), 12)
        self.assertContains(response, 'currency=INR')
        self.assertContains(response, 'page=2')

    def test_top_five_only_real_active_applicants_no_gemini(self):
        self.posting.skills.add(self.skill)
        self.posting.content_type = 'FILM'
        self.posting.save()
        self.creator.skills.add(self.skill)
        self.creator.price_min = 100
        self.creator.price_max = 500
        self.creator.turnaround_days = 5
        self.creator.save()
        PortfolioItem.objects.create(creator=self.creator, title='A film for a new idea', description=self.posting.description, format='FILM', aspect_ratio='16:9')
        primary = self.submit()
        for index in range(6):
            user = User.objects.create_user(username=f'ranked{index}', email=f'ranked{index}@example.com', role='CREATOR')
            profile = CreatorProfile.objects.create(user=user, display_name=f'Applicant {index}')
            Application.objects.create(posting=self.posting, creator=profile)
        self.client.force_login(self.brand)
        with patch('discovery.semantic.cached_insights', side_effect=AssertionError('No Gemini cache or calls for job ranking')):
            matches = top_applicants(self.brand, self.posting.pk)
        self.assertEqual(len(matches), 5)
        self.assertEqual(matches[0]['profile'], self.creator)
        ids = set(self.posting.applications.values_list('creator_id', flat=True))
        self.assertTrue({match['profile'].pk for match in matches} <= ids)
        self.assertTrue(all(0 <= match['score'] <= 100 and match['rows'] for match in matches))
        primary.status = 'WITHDRAWN'
        primary.save()
        self.assertNotIn(self.creator.pk, [match['profile'].pk for match in top_applicants(self.brand, self.posting.pk)])
        self.assertContains(self.client.post(self.route('recommendations', self.posting.pk)), 'rule-based compatibility')

    def test_optional_format_does_not_create_missing_requirements(self):
        self.submit()
        matches = top_applicants(self.brand, self.posting.pk)
        self.assertEqual(len(matches), 1)
        self.assertNotIn('Content type', [row['label'] for row in matches[0]['rows']])
        self.assertNotIn('Aspect ratio', [row['label'] for row in matches[0]['rows']])

    def test_applicant_filtering_shortlisted_and_name(self):
        app = self.submit()
        self.client.force_login(self.brand)
        response = self.client.get(self.route('applicants', self.posting.pk), {'review': 'shortlisted'})
        self.assertEqual(response.context['page_obj'].paginator.count, 0)
        self.consider(app)
        response = self.client.get(self.route('applicants', self.posting.pk), {'review': 'shortlisted', 'q': self.creator.display_name, 'sort': 'name'})
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_engagement_uniqueness_snapshot_ownership_and_contact_revocation(self):
        app = self.submit(creator_email='allowed@example.com', share_email='on')
        self.consider(app)
        url = self.route('collaborate', app.pk)
        data = {'message': 'Let us create this film together.', 'share': 'on'}
        self.client.post(url, data)
        self.client.post(url, data)
        engagement = Engagement.objects.get(posting=self.posting)
        self.assertEqual(engagement.events.count(), 1)
        self.assertEqual(Notification.objects.filter(kind='INVITED').count(), 1)
        self.assertIsNone(engagement.brief_id)
        self.assertNotIn('allowed@example.com', str(engagement.brief_snapshot))
        self.posting.title = 'Changed scope after invitation'
        self.posting.save()
        self.assertNotEqual(engagement.brief_snapshot['title'], self.posting.title)
        for user in [self.other]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(engagement.get_absolute_url()).status_code, 404)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Engagement.objects.create(posting=self.posting, creator=self.creator)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Engagement.objects.create(creator=self.creator)

    def test_consideration_required_for_collaboration_and_contact_request(self):
        app = self.submit()
        self.client.force_login(self.brand)
        response = self.client.post(self.route('collaborate', app.pk), {'message': 'Please join this creative project.', 'share': 'on'})
        self.assertContains(response, 'Choose Considering')
        self.assertFalse(Engagement.objects.exists())
        self.client.post(self.route('contact_request', app.pk))
        self.assertFalse(Notification.objects.filter(kind='CONTACT_REQUEST').exists())


class MarketplaceEndToEndTests(OpportunityTestCase):
    def route(self, name, pk=None):
        return reverse('opportunities:' + name, args=[pk] if pk is not None else [])

    # Execute the requested journey with real HTTP views and persisted records.
    def test_new_brand_two_creators_to_revisions_and_completion(self):
        brand_client, creator_a, creator_b = Client(), Client(), Client()
        for client, name, role in [(brand_client, 'fresh-brand', 'BRAND'), (creator_a, 'creator-a', 'CREATOR'), (creator_b, 'creator-b', 'CREATOR')]:
            response = client.post(reverse('register'), {'first_name': name, 'username': name, 'email': name + '@example.com',
                'role': role, 'password1': 'Test-journey-only-2026!', 'password2': 'Test-journey-only-2026!'})
            self.assertEqual(response.status_code, 302)
            client.logout()
            self.assertTrue(client.login(username=name, password='Test-journey-only-2026!'))
        response = brand_client.post(self.route('create'), self.payload(title='New brand public opportunity', action='publish', publish_consent='on'))
        self.assertEqual(response.status_code, 302)
        posting = JobPosting.objects.get(title='New brand public opportunity')
        for client in [creator_a, creator_b]:
            self.assertContains(client.get(self.route('explore')), posting.title)
            response = client.post(self.route('apply', posting.pk), {'message': 'I can deliver this real test project.', 'version': 0,
                'creator_email': 'professional-maker@example.com', 'share_email': 'on'})
            self.assertEqual(response.status_code, 302)
        self.assertEqual(posting.applications.count(), 2)
        response = brand_client.get(self.route('applicants', posting.pk))
        for app in posting.applications.all():
            self.assertContains(response, app.creator.display_name)
            self.assertEqual(brand_client.get(app.creator.get_absolute_url()).status_code, 200)
        response = brand_client.post(self.route('recommendations', posting.pk))
        self.assertEqual({match['profile'].pk for match in response.context['matches']}, set(posting.applications.values_list('creator_id', flat=True)))
        app = posting.applications.get(creator__user__username='creator-a')
        response = brand_client.post(self.route('considering', app.pk), {'version': 0, 'brand_email': 'fresh-professional@example.com', 'brand_share_email': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertContains(creator_a.get(reverse('engagement_inbox')), 'is considering you')
        self.assertContains(creator_a.get(app.get_absolute_url()), 'fresh-professional@example.com')
        self.assertContains(brand_client.get(app.get_absolute_url()), 'professional-maker@example.com')
        response = brand_client.post(self.route('collaborate', app.pk), {'message': 'Let us make this project together.', 'share': 'on'})
        self.assertEqual(response.status_code, 302)
        engagement = Engagement.objects.get(posting=posting)
        for client, status in [(creator_a, 'ACCEPTED'), (creator_a, 'IN_PROGRESS'), (creator_a, 'SUBMITTED'),
                               (brand_client, 'IN_PROGRESS'), (creator_a, 'SUBMITTED'), (brand_client, 'COMPLETED')]:
            engagement.refresh_from_db()
            response = client.post(engagement.get_absolute_url(), {'version': engagement.version, 'status': status,
                'note': 'Update from the end-to-end project.', 'delivery_url': 'https://example.com/approved-work'})
            self.assertEqual(response.status_code, 302)
        engagement.refresh_from_db()
        self.assertEqual(engagement.status, 'COMPLETED')
        self.assertEqual(engagement.events.count(), 7)
        self.assertContains(brand_client.get(reverse('engagement_inbox')), posting.title)
        self.assertContains(creator_a.get(reverse('engagement_inbox')), 'Completed')
        self.assertEqual(creator_b.get(engagement.get_absolute_url()).status_code, 404)
        self.assertEqual(brand_client.get(reverse('studio:index')).status_code, 200)
        self.assertEqual(brand_client.get(reverse('brief_create')).status_code, 200)
        self.assertEqual(creator_a.get(reverse('directory')).status_code, 200)


class OpportunitySeedTests(OpportunityTestCase):
    @override_settings(DEBUG=True)
    def test_seed_is_explicit_idempotent_and_never_overwrites_existing_users(self):
        with self.assertRaises(CommandError):
            call_command('seed_opportunities', stdout=StringIO())
        collision = User.objects.create_user(username='creo_opportunity_demo_01', email='real@example.com', role='BRAND')
        call_command('seed_opportunities', development=True, stdout=StringIO())
        self.assertEqual(JobPosting.objects.filter(is_demo=True).count(), 4)
        self.assertFalse(JobPosting.objects.filter(brand=collision).exists())
        before = list(JobPosting.objects.values_list('pk', 'updated_at'))
        call_command('seed_opportunities', development=True, stdout=StringIO())
        self.assertEqual(before, list(JobPosting.objects.values_list('pk', 'updated_at')))
        for job in JobPosting.objects.filter(is_demo=True):
            self.assertFalse(job.brand.has_usable_password())
            self.assertIn('fictional', job.brand_name)
            job.full_clean()
        self.assertEqual(User.objects.get(pk=collision.pk).email, 'real@example.com')

    @override_settings(DEBUG=False)
    def test_production_seed_is_refused(self):
        with self.assertRaises(CommandError):
            call_command('seed_opportunities', development=True, stdout=StringIO())
