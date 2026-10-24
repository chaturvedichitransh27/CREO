from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from creators.models import CreatorProfile, Skill, Tool, Specialization
from portfolio.models import PortfolioItem
from .forms import JobPostingForm
from .models import Application, JobPosting
from .services import applicant_candidates, change_status, matching_requirements, save_posting


class OpportunityTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.brand = User.objects.create_user(username='job-brand', email='private-brand@example.com', role='BRAND')
        cls.other = User.objects.create_user(username='other-brand', email='other-private@example.com', role='BRAND')
        cls.creator_user = User.objects.create_user(username='job-creator', email='private-creator@example.com', role='CREATOR')
        cls.creator = CreatorProfile.objects.create(user=cls.creator_user, display_name='Public Creative Name')
        cls.skill = Skill.objects.create(name='Opportunity editing')
        cls.tool = Tool.objects.create(name='Opportunity software')
        cls.specialization = Specialization.objects.create(name='Opportunity products')
        cls.posting = cls.make_posting()

    @classmethod
    def make_posting(cls, **overrides):
        data = dict(brand=cls.brand, brand_name='Public Studio', title='A film for a new idea',
            description='A thoughtful launch film.', work_type='FILMMAKING', deliverables='One finished film',
            output_format='MP4, 4K', budget_min=100, budget_max=1000, currency='INR', duration_days=10,
            application_deadline=timezone.localdate() + timedelta(days=10), deadline=timezone.localdate() + timedelta(days=30))
        data.update(overrides)
        return JobPosting.objects.create(**data)

    def payload(self, **overrides):
        data = dict(brand_name='Public Studio', title='A film for a new idea', description='A thoughtful launch film.',
            work_type='FILMMAKING', deliverables='One finished film', output_format='MP4, 4K',
            budget_min='100', budget_max='1000', currency='INR', duration_days='10',
            application_deadline=str(timezone.localdate() + timedelta(days=10)),
            deadline=str(timezone.localdate() + timedelta(days=30)), version='0', action='draft',
            skills=[self.skill.pk], tools=[self.tool.pk], specializations=[self.specialization.pk])
        data.update(overrides)
        return data

    def url(self, name, pk=None):
        return reverse('opportunities:' + name, args=[pk or self.posting.pk] if name in {'edit', 'manage', 'detail', 'applicants', 'transition'} else [])

    def open_posting(self):
        self.posting.status = JobPosting.Status.OPEN
        self.posting.published_at = timezone.now()
        self.posting.save()


class PostingWorkflowTests(OpportunityTestCase):
    def setUp(self):
        self.client.force_login(self.brand)

    def test_title_only_draft(self):
        response = self.client.post(self.url('create'), {'title': 'A private seed', 'currency': 'INR', 'version': 0, 'action': 'draft'})
        self.assertEqual(response.status_code, 302)
        job = JobPosting.objects.get(title='A private seed')
        self.assertEqual(job.brand, self.brand)
        self.assertEqual(job.status, 'DRAFT')
        self.assertIsNone(job.published_at)
        self.assertIsNone(job.budget_min)

    def test_draft_keeps_taxonomies(self):
        response = self.client.post(self.url('edit'), self.payload(style='Sculptural'))
        self.assertRedirects(response, self.url('manage'))
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.version, 1)
        self.assertEqual(self.posting.style, 'Sculptural')
        self.assertEqual(list(self.posting.skills.all()), [self.skill])
        self.assertEqual(list(self.posting.tools.all()), [self.tool])
        self.assertEqual(list(self.posting.specializations.all()), [self.specialization])

    def test_publish_close_reopen(self):
        response = self.client.post(self.url('edit'), self.payload(action='publish', publish_consent='on'))
        self.assertRedirects(response, self.url('manage'))
        self.posting.refresh_from_db()
        published_at = self.posting.published_at
        self.assertTrue(self.posting.accepting_applications)
        self.assertEqual(self.posting.status, 'OPEN')
        self.client.post(self.url('transition'), {'action': 'close', 'version': 1})
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.status, 'CLOSED')
        self.assertFalse(self.posting.accepting_applications)
        self.assertEqual(self.client.get(self.url('detail')).status_code, 200)
        self.client.post(self.url('transition'), {'action': 'reopen', 'version': 2})
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.status, 'OPEN')
        self.assertEqual(self.posting.published_at, published_at)
        self.assertEqual(self.posting.version, 3)

    def test_publish_requires_complete_content_and_explicit_consent(self):
        for overrides in [{'publish_consent': ''}, {'description': ''}, {'brand_name': '  '}, {'work_type': ''},
                          {'deliverables': ''}, {'output_format': ''}, {'duration_days': ''},
                          {'budget_min': '', 'budget_max': ''}, {'deadline': ''}, {'application_deadline': ''}]:
            with self.subTest(overrides=overrides):
                data = self.payload(action='publish', publish_consent='on')
                data.update(overrides)
                response = self.client.post(self.url('edit'), data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.posting.refresh_from_db()
                self.assertEqual(self.posting.status, 'DRAFT')

    def test_public_edit_requires_consent_and_cannot_clear_requirements(self):
        self.open_posting()
        response = self.client.post(self.url('edit'), self.payload(action='save', title='Unapproved change'))
        self.assertFormError(response.context['form'], 'publish_consent', 'This field is required.')
        response = self.client.post(self.url('edit'), self.payload(action='save', publish_consent='on', description=''))
        self.assertTrue(response.context['form'].errors)
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.title, 'A film for a new idea')
        self.client.post(self.url('edit'), self.payload(action='save', publish_consent='on', title='Approved change'))
        self.assertContains(self.client.get(self.url('detail')), 'Approved change')

    def test_unknown_actions_cannot_mutate_state(self):
        for action in ['delete', 'reopen', 'OPEN', 'save']:
            response = self.client.post(self.url('edit'), self.payload(action=action))
            self.assertEqual(response.status_code, 200)
        for action in ['publish', 'delete', 'reopen', 'close']:
            self.client.post(self.url('transition'), {'action': action, 'version': 0})
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.status, 'DRAFT')
        self.assertEqual(self.posting.version, 0)

    def test_forged_owner_status_and_timestamp_are_ignored(self):
        self.client.post(self.url('edit'), self.payload(brand=self.other.pk, status='OPEN', published_at=timezone.now()))
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.brand, self.brand)
        self.assertEqual(self.posting.status, 'DRAFT')
        self.assertIsNone(self.posting.published_at)

    def test_stale_edit_preserves_newer_text_and_taxonomy(self):
        self.client.post(self.url('edit'), self.payload(title='Newer title'))
        response = self.client.post(self.url('edit'), self.payload(title='Stale title', skills=[]))
        self.assertContains(response, 'changed in another tab')
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.title, 'Newer title')
        self.assertEqual(list(self.posting.skills.all()), [self.skill])

    def test_race_after_form_validation_rolls_back(self):
        form = JobPostingForm(self.payload(), instance=self.posting)
        self.assertTrue(form.is_valid(), form.errors)
        JobPosting.objects.filter(pk=self.posting.pk).update(version=1, title='Concurrent title')
        with self.assertRaises(ValidationError):
            save_posting(form, self.brand)
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.title, 'Concurrent title')
        self.assertFalse(self.posting.skills.exists())

    def test_stale_status_change_is_rejected(self):
        self.open_posting()
        change_status(self.brand, self.posting.pk, 'close', 0)
        with self.assertRaises(ValidationError):
            change_status(self.brand, self.posting.pk, 'reopen', 0)
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.status, 'CLOSED')

    def test_expired_jobs_are_effectively_closed_and_cannot_reopen(self):
        self.open_posting()
        self.posting.application_deadline = timezone.localdate() - timedelta(days=1)
        self.posting.save()
        self.assertFalse(self.posting.accepting_applications)
        self.assertContains(self.client.get(self.url('detail')), 'deadline passed')
        change_status(self.brand, self.posting.pk, 'close', 0)
        with self.assertRaises(ValidationError):
            change_status(self.brand, self.posting.pk, 'reopen', 1)

    def test_today_is_an_inclusive_application_deadline(self):
        self.open_posting()
        self.posting.application_deadline = timezone.localdate()
        self.assertTrue(self.posting.accepting_applications)

    def test_historic_dates_allow_edit_but_not_publish(self):
        self.posting.application_deadline = timezone.localdate() - timedelta(days=2)
        self.posting.deadline = timezone.localdate() - timedelta(days=1)
        self.posting.save()
        data = self.payload(application_deadline=str(self.posting.application_deadline), deadline=str(self.posting.deadline))
        self.assertTrue(JobPostingForm(data, instance=self.posting).is_valid())
        form = JobPostingForm({**data, 'publish_consent': 'on'}, instance=self.posting, publish=True)
        self.assertFalse(form.is_valid())

    def test_read_pages_and_empty_state_render_without_ai(self):
        with patch('discovery.matching.rank_creators', side_effect=AssertionError('Unexpected matching')):
            for route in ['index', 'create', 'edit', 'manage', 'applicants']:
                response = self.client.get(self.url(route))
                self.assertEqual(response.status_code, 200, route)
                self.assertIn('no-store', response['Cache-Control'])
        self.assertContains(self.client.get(self.url('applicants')), 'No applications yet')

    def test_dashboard_and_home_entry_points_preserve_brief_links(self):
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, self.url('create'))
        self.assertContains(response, reverse('brief_create'))
        self.assertContains(response, self.url('manage'))
        self.assertContains(self.client.get(reverse('home')), 'Post a Project')


class PostingSecurityTests(OpportunityTestCase):
    def test_anonymous_private_routes_require_login(self):
        for name in ['index', 'create', 'edit', 'manage', 'applicants']:
            response = self.client.get(self.url(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn('next=', response.url)
        self.assertEqual(self.client.post(self.url('transition'), {'action': 'close', 'version': 0}).status_code, 302)

    def test_creator_cannot_access_or_write_brand_routes(self):
        self.client.force_login(self.creator_user)
        for name in ['index', 'create', 'edit', 'manage', 'applicants']:
            self.assertEqual(self.client.get(self.url(name)).status_code, 403)
        for name in ['create', 'edit', 'transition']:
            self.assertEqual(self.client.post(self.url(name), self.payload()).status_code, 403)

    def test_cross_brand_private_objects_are_404(self):
        self.client.force_login(self.other)
        for name in ['edit', 'manage', 'applicants']:
            self.assertEqual(self.client.get(self.url(name)).status_code, 404)
        for name in ['edit', 'transition']:
            self.assertEqual(self.client.post(self.url(name), self.payload()).status_code, 404)
        self.assertNotContains(self.client.get(self.url('index')), self.posting.title)
        self.assertNotContains(self.client.get(reverse('dashboard')), self.posting.title)

    def test_csrf_enforced_on_all_writes(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.brand)
        for route in ['create', 'edit', 'transition']:
            self.assertEqual(client.post(self.url(route), self.payload()).status_code, 403)

    def test_get_cannot_transition_and_read_pages_cannot_accept_post(self):
        self.client.force_login(self.brand)
        self.assertEqual(self.client.get(self.url('transition')).status_code, 405)
        for name in ['index', 'manage', 'applicants', 'detail']:
            self.assertEqual(self.client.post(self.url(name), self.payload()).status_code, 405)

    def test_drafts_never_have_public_detail_including_for_owner(self):
        for user in [None, self.brand, self.other, self.creator_user]:
            if user:
                self.client.force_login(user)
            self.assertEqual(self.client.get(self.url('detail')).status_code, 404)

    def test_public_details_exclude_account_and_application_secrets(self):
        self.open_posting()
        Application.objects.create(posting=self.posting, creator=self.creator, message='Private applicant message')
        response = self.client.get(self.url('detail'))
        self.assertContains(response, 'Public Studio')
        for secret in [self.brand.email, self.brand.username, self.creator_user.email, 'Private applicant message', 'Public Creative Name']:
            self.assertNotContains(response, secret)

    def test_untrusted_content_is_escaped(self):
        self.open_posting()
        self.posting.description = '<script>alert("secret")</script>'
        self.posting.save()
        response = self.client.get(self.url('detail'))
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert')

    def test_invalid_taxonomies_and_tampered_versions_fail(self):
        for overrides in [{'skills': [999999]}, {'tools': [999999]}, {'specializations': [999999]},
                          {'version': -1}, {'version': 'bad'}, {'version': ''}]:
            with self.subTest(overrides=overrides):
                form = JobPostingForm(self.payload(**overrides), instance=JobPosting(brand=self.brand))
                self.assertFalse(form.is_valid())

    def test_creator_discovery_does_not_allow_applying_to_drafts(self):
        self.client.force_login(self.creator_user)
        self.assertNotContains(self.client.get('/opportunities/'), self.posting.title)
        self.assertEqual(self.client.post(f'/opportunities/{self.posting.pk}/apply/').status_code, 404)
        self.assertFalse(Application.objects.exists())


class PostingValidationTests(OpportunityTestCase):
    def test_invalid_amounts_dates_choices_and_lengths(self):
        cases = [dict(title=''), dict(title='x' * 141), dict(description='x' * 5001), dict(budget_min='0'),
            dict(budget_min='-1'), dict(budget_min='NaN'), dict(budget_min='100.001'), dict(budget_max='50'),
            dict(budget_max=''), dict(budget_min=''), dict(currency='BTC'), dict(work_type='invented'),
            dict(duration_days='0'), dict(duration_days='3651'), dict(min_experience_years='61'),
            dict(min_experience_years='-1'), dict(application_deadline='bad date'),
            dict(application_deadline=str(timezone.localdate() - timedelta(days=1))),
            dict(deadline=str(timezone.localdate() + timedelta(days=1))), dict(output_format='x' * 251)]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                form = JobPostingForm(self.payload(**overrides), instance=JobPosting(brand=self.brand))
                self.assertFalse(form.is_valid())

    def test_zero_experience_and_optional_preferences_are_valid(self):
        form = JobPostingForm(self.payload(min_experience_years=0, skills=[], tools=[], specializations=[]), instance=JobPosting(brand=self.brand))
        self.assertTrue(form.is_valid(), form.errors)

    def test_model_rejects_nonbrand_owner_and_incomplete_publication(self):
        self.posting.brand = self.creator_user
        with self.assertRaises(ValidationError):
            self.posting.full_clean()
        self.posting.brand = self.brand
        self.posting.status = 'OPEN'
        self.posting.published_at = timezone.now()
        self.posting.description = ''
        with self.assertRaises(ValidationError):
            self.posting.full_clean()

    def test_database_constraints_survive_bypassed_form_validation(self):
        cases = [{'budget_min': -1}, {'budget_max': 1}, {'budget_min': None}, {'duration_days': 0},
                 {'duration_days': 3651}, {'min_experience_years': 61}, {'status': 'OPEN'},
                 {'status': 'INVALID'}, {'published_at': timezone.now()},
                 {'deadline': timezone.localdate()}]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                JobPosting.objects.filter(pk=self.posting.pk).update(**values)

    def test_service_save_is_owner_checked(self):
        form = JobPostingForm(self.payload(), instance=self.posting)
        self.assertTrue(form.is_valid())
        with self.assertRaises(Http404):
            save_posting(form, self.other)
        with self.assertRaises(PermissionDenied):
            save_posting(form, self.creator_user)


class ApplicantArchitectureTests(OpportunityTestCase):
    def setUp(self):
        self.open_posting()
        self.application = Application.objects.create(posting=self.posting, creator=self.creator, message='A private proposal')
        self.client.force_login(self.brand)

    def test_application_unique_per_creator_and_job(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Application.objects.create(posting=self.posting, creator=self.creator)
        other_job = self.make_posting()
        Application.objects.create(posting=other_job, creator=self.creator)
        self.assertEqual(Application.objects.count(), 2)

    def test_multiple_applicants_and_count(self):
        user = get_user_model().objects.create_user(username='second-creator', email='second@example.com', role='CREATOR')
        profile = CreatorProfile.objects.create(user=user, display_name='Second creator')
        Application.objects.create(posting=self.posting, creator=profile)
        response = self.client.get(self.url('index'))
        self.assertContains(response, '2 applicants')
        self.assertContains(self.client.get(self.url('applicants')), 'Second creator')

    def test_applicant_page_uses_real_data_and_no_contact_details_or_scores(self):
        self.creator.skills.add(self.skill)
        self.creator.tools.add(self.tool)
        self.posting.tools.add(self.tool)
        relevant = PortfolioItem.objects.create(creator=self.creator, title='Relevant work', description='Public work', format='FILM', aspect_ratio='16:9')
        relevant.tools.add(self.tool)
        response = self.client.get(self.url('applicants'))
        for value in ['Public Creative Name', self.skill.name, self.tool.name, 'Relevant work', 'A private proposal', 'Submitted', 'Not reviewed', 'Find Top 5 Matches']:
            self.assertContains(response, value)
        for value in [self.creator_user.email, self.brand.email, 'mailto:', 'tel:']:
            self.assertNotContains(response, value)
        self.assertContains(response, self.creator.get_absolute_url())

    def test_application_message_is_escaped(self):
        self.application.message = '<img src=x onerror=alert(1)>'
        self.application.save()
        response = self.client.get(self.url('applicants'))
        self.assertContains(response, '&lt;img')
        self.assertNotContains(response, '<img src=x')

    def test_candidate_boundary_excludes_nonapplicants_withdrawn_and_inactive(self):
        other_job = self.make_posting(brand=self.other)
        user = get_user_model().objects.create_user(username='other-applicant', email='other-applicant@example.com', role='CREATOR')
        profile = CreatorProfile.objects.create(user=user, display_name='Other applicant')
        Application.objects.create(posting=other_job, creator=profile)
        self.assertEqual(list(applicant_candidates(self.brand, self.posting.pk)), [self.creator])
        self.application.status = 'WITHDRAWN'
        self.application.save()
        self.assertFalse(applicant_candidates(self.brand, self.posting.pk).exists())
        self.application.status = 'SUBMITTED'
        self.application.save()
        self.creator_user.is_active = False
        self.creator_user.save()
        self.assertFalse(applicant_candidates(self.brand, self.posting.pk).exists())

    def test_matching_boundary_is_private_and_preserves_taxonomy(self):
        self.posting.skills.add(self.skill)
        requirements = matching_requirements(self.brand, self.posting.pk)
        self.assertEqual(requirements['taxonomies']['skills'], [self.skill])
        self.assertEqual(requirements['fields']['budget_min'], self.posting.budget_min)
        self.assertEqual(requirements['source'], 'opportunity')
        self.assertNotIn('brand', requirements['fields'])
        for service in [matching_requirements, applicant_candidates]:
            with self.assertRaises(Http404):
                service(self.other, self.posting.pk)
            with self.assertRaises(PermissionDenied):
                service(self.creator_user, self.posting.pk)

    def test_application_and_consideration_status_constraints(self):
        for values in [{'status': 'FAKE'}, {'consideration_status': 'FAKE'}]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                Application.objects.filter(pk=self.application.pk).update(**values)
        self.application.consideration_status = 'CONSIDERING'
        self.application.full_clean()
        self.application.save()
        response = self.client.get(self.url('applicants'))
        self.assertContains(response, 'Considering')
        self.assertNotContains(response, self.creator_user.email)

    def test_new_application_validation_rejects_closed_or_noncreator(self):
        draft = self.make_posting()
        with self.assertRaises(ValidationError):
            Application(posting=draft, creator=self.creator).full_clean()
        profile = CreatorProfile.objects.create(user=self.other, display_name='Not a creator')
        with self.assertRaises(ValidationError):
            Application(posting=self.posting, creator=profile).full_clean()

    def test_pagination_is_bounded_and_handles_invalid_page(self):
        JobPosting.objects.bulk_create([JobPosting(brand=self.brand, title=f'Draft {i}') for i in range(15)])
        first = self.client.get(self.url('index'))
        self.assertEqual(len(first.context['page_obj']), 12)
        self.assertTrue(first.context['page_obj'].has_next())
        self.assertEqual(self.client.get(self.url('index') + '?page=invalid').status_code, 200)
        self.assertEqual(self.client.get(self.url('applicants') + '?page=999').status_code, 200)
