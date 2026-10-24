from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from creators.models import CreatorProfile
from portfolio.models import PortfolioItem
from .forms import JobPostingForm
from .models import Application, JobPosting
from .services import save_posting, change_status
from .tests import OpportunityTestCase
from .views import edit


class BrandAuditTests(OpportunityTestCase):
    def setUp(self):
        self.client.force_login(self.brand)

    def test_project_cards_show_timing_and_define_total_counts(self):
        self.open_posting()
        Application.objects.create(posting=self.posting, creator=self.creator, status='WITHDRAWN')
        for url in [reverse('dashboard'), self.url('index')]:
            response = self.client.get(url)
            self.assertContains(response, '1 applicant')
            self.assertContains(response, 'including withdrawn')
            self.assertContains(response, 'Applications close')
            self.assertContains(response, self.posting.application_deadline.strftime('%Y-%m-%d'))
            self.assertContains(response, 'Delivery due')
        response = self.client.get(self.url('manage'))
        self.assertContains(response, 'including withdrawn')

    def test_draft_card_has_truthful_missing_deadlines(self):
        self.posting.application_deadline = None
        self.posting.deadline = None
        self.posting.save()
        response = self.client.get(self.url('index'))
        self.assertContains(response, 'Application deadline to define')
        self.assertContains(response, 'Delivery deadline to define')

    def test_applicants_preview_available_work_without_inventing_relevance(self):
        self.open_posting()
        Application.objects.create(posting=self.posting, creator=self.creator)
        item = PortfolioItem.objects.create(creator=self.creator, title='Actual public sample',
            image='portfolios/test/sample.jpg', format='IMAGE', aspect_ratio='1:1')
        response = self.client.get(self.url('applicants'))
        self.assertContains(response, item.image.url)
        self.assertContains(response, 'Portfolio samples')
        self.assertNotContains(response, 'Relevant portfolio work')
        self.assertContains(response, item.get_absolute_url())

    def test_hidden_creator_work_is_not_exposed_in_applicant_review(self):
        self.open_posting()
        Application.objects.create(posting=self.posting, creator=self.creator, message='Keep this application record')
        self.posting.tools.add(self.tool)
        item = PortfolioItem.objects.create(creator=self.creator, title='No longer public work',
            image='portfolios/test/hidden.jpg')
        item.tools.add(self.tool)
        for changes in [{'is_active': False}, {'is_active': True, 'role': 'BRAND'}]:
            User.objects.filter(pk=self.creator_user.pk).update(**changes)
            response = self.client.get(self.url('applicants'))
            self.assertContains(response, 'Keep this application record')
            self.assertContains(response, 'This creator profile is no longer public.')
            self.assertNotContains(response, item.title)
            self.assertNotContains(response, item.image.url)
            self.assertNotContains(response, self.creator.get_absolute_url())
            self.assertEqual(self.client.get(item.get_absolute_url()).status_code, 404)

    def test_applicant_previews_remain_bounded_and_prefer_relevant_work(self):
        self.open_posting()
        self.posting.tools.add(self.tool)
        Application.objects.create(posting=self.posting, creator=self.creator)
        items = [PortfolioItem.objects.create(creator=self.creator, title=f'Sample {i}',
            image=f'portfolios/test/{i}.jpg') for i in range(5)]
        for item in items[:4]:
            item.tools.add(self.tool)
        response = self.client.get(self.url('applicants'))
        self.assertContains(response, 'Relevant portfolio work')
        for item in items[1:4]:
            self.assertContains(response, item.image.url)
        for item in [items[0], items[4]]:
            self.assertNotContains(response, item.image.url)

    def test_inactive_brand_is_rejected_even_with_authenticated_request(self):
        self.brand.is_active = False
        request = RequestFactory().post(self.url('create'), self.payload())
        request.user = self.brand
        with self.assertRaises(PermissionDenied):
            edit(request)
        with self.assertRaises(PermissionDenied):
            change_status(self.brand, self.posting.pk, 'close', 0)

    def test_publication_and_taxonomies_roll_back_together(self):
        form = JobPostingForm(self.payload(publish_consent='on'), instance=self.posting, publish=True)
        self.assertTrue(form.is_valid(), form.errors)
        save_relationships = form._save_m2m

        def fail_after_relationships():
            save_relationships()
            raise RuntimeError('Simulated relationship failure')

        with patch.object(form, '_save_m2m', side_effect=fail_after_relationships):
            with self.assertRaises(RuntimeError):
                save_posting(form, self.brand)
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.status, 'DRAFT')
        self.assertEqual(self.posting.version, 0)
        self.assertIsNone(self.posting.published_at)
        self.assertFalse(self.posting.skills.exists())

    def test_historic_published_posting_can_be_edited_and_closed(self):
        self.open_posting()
        application_date = timezone.localdate() - timedelta(days=3)
        delivery_date = timezone.localdate() - timedelta(days=1)
        JobPosting.objects.filter(pk=self.posting.pk).update(application_deadline=application_date, deadline=delivery_date)
        response = self.client.post(self.url('edit'), self.payload(action='save', publish_consent='on',
            title='Historic clarified description', application_deadline=str(application_date), deadline=str(delivery_date)))
        self.assertRedirects(response, self.url('manage'))
        self.client.post(self.url('transition'), {'action': 'close', 'version': 1})
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.status, 'CLOSED')
        self.assertEqual(self.posting.deadline, delivery_date)
        self.assertContains(self.client.get(self.url('detail')), 'Historic clarified description')

    def test_new_registered_brand_draft_then_publication_enters_available_query(self):
        self.client.logout()
        response = self.client.post(reverse('register'), {'first_name': 'Audit Brand', 'username': 'audit-new-brand',
            'email': 'audit-new@example.invalid', 'role': 'BRAND', 'password1': 'Test-only-new-brand!2468',
            'password2': 'Test-only-new-brand!2468'})
        self.assertRedirects(response, reverse('dashboard'))
        self.client.post(self.url('create'), self.payload(title='New brand draft'))
        posting = JobPosting.objects.get(title='New brand draft')
        self.assertEqual(posting.brand.username, 'audit-new-brand')
        self.assertEqual(list(posting.skills.all()), [self.skill])
        self.assertFalse(JobPosting.objects.available().filter(pk=posting.pk).exists())
        self.client.post(self.url('edit', posting.pk), self.payload(title=posting.title,
            action='publish', publish_consent='on'))
        self.assertTrue(JobPosting.objects.available().filter(pk=posting.pk).exists())
        self.assertContains(self.client.get(reverse('opportunities:explore')), posting.title)

    def test_applicant_related_loading_is_constant_and_paginated(self):
        self.open_posting()
        Application.objects.create(posting=self.posting, creator=self.creator)
        PortfolioItem.objects.create(creator=self.creator, title='First sample', image='portfolios/test/first.jpg')
        with CaptureQueriesContext(connection) as single:
            self.client.get(self.url('applicants'))
        for index in range(12):
            user = User.objects.create_user(username=f'audit-page-{index}', email=f'audit-page-{index}@example.invalid', role='CREATOR')
            creator = CreatorProfile.objects.create(user=user, display_name=f'Audit applicant {index}')
            creator.skills.add(self.skill)
            creator.tools.add(self.tool)
            PortfolioItem.objects.create(creator=creator, title=f'Sample {index}', image=f'portfolios/test/{index}.jpg')
            Application.objects.create(posting=self.posting, creator=creator)
        with CaptureQueriesContext(connection) as multiple:
            response = self.client.get(self.url('applicants'))
        self.assertEqual(len(response.context['page_obj']), 12)
        self.assertEqual(response.context['page_obj'].paginator.count, 13)
        self.assertEqual(len(single), len(multiple), 'Related loading must not grow per applicant or portfolio.')
