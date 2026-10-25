from unittest.mock import patch
from django.db import DatabaseError
from django.test import TestCase, Client
from django.urls import reverse
from creators.models import Skill, Specialization
from . import tests as fixtures
from .models import Brief


class BriefSaveFeedbackTests(TestCase):
    setUp = fixtures.BriefTests.setUp
    payload = fixtures.BriefTests.payload

    def test_server_validation_has_visible_summary_and_keeps_edits(self):
        response = self.client.post(reverse('brief_create'), self.payload(usage_requirements=''))
        self.assertContains(response, 'Brief not saved. Please check the following:')
        self.assertContains(response, 'href="#id_usage_requirements"')
        self.assertContains(response, 'Amber coffee campaign')
        self.assertFalse(Brief.objects.exists())

    def test_json_required_fields_and_conditional_validation(self):
        for changed, field in [({'title': ''}, 'id_title'), ({'usage_requirements': ''}, 'id_usage_requirements'),
                               ({'budget_max': '1'}, 'id_budget_max')]:
            with self.subTest(field=field):
                response = self.client.post(reverse('brief_create'), self.payload(**changed), HTTP_ACCEPT='application/json')
                self.assertEqual(response.status_code, 422)
                self.assertFalse(response.json()['saved'])
                self.assertIn(field, [error['field_id'] for error in response.json()['errors']])
                self.assertFalse(Brief.objects.exists())

    def test_current_edits_save_all_fields_for_manual_local_and_gemini(self):
        skill = Skill.objects.create(name='Editing')
        specialization = Specialization.objects.create(name='Campaigns')
        for source in ('MANUAL', 'LOCAL', 'GEMINI'):
            with self.subTest(source=source):
                url = reverse('brief_create')
                if source != 'MANUAL':
                    session = self.client.session
                    session['brief_drafts'] = {'current': {'user_id': self.brand.pk, 'source': source,
                        'data': self.payload(title='Original generated title', description='Original text')}}
                    session.save()
                    url += '?draft=current'
                data = self.payload(title='Edited ' + source, description='My current edited description',
                    audience='Edited audience', deliverables='Edited deliverables', style='Edited style',
                    content_type='FILM', aspect_ratio='16:9', budget_min='150', budget_max='900', currency='USD',
                    status='DRAFT', usage_requirements='Edited usage', min_experience_years='3',
                    skills=[skill.pk], specializations=[specialization.pk])
                response = self.client.post(url, data, HTTP_ACCEPT='application/json')
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['saved'])
                brief = Brief.objects.get(title=data['title'])
                brief.refresh_from_db()
                self.assertEqual(brief.source, source)
                for name, value in data.items():
                    if name in ('skills', 'tools', 'specializations'):
                        self.assertEqual(list(getattr(brief, name).values_list('pk', flat=True)), value)
                    elif name == 'commercial_use':
                        self.assertTrue(brief.commercial_use)
                    elif name.startswith('budget_'):
                        self.assertEqual(float(getattr(brief, name)), float(value))
                    else:
                        self.assertEqual(str(getattr(brief, name)), value)
                self.assertEqual(response.json()['redirect_url'], brief.get_absolute_url())
                self.assertContains(self.client.get(brief.get_absolute_url()), data['title'])
                self.assertContains(self.client.get(reverse('brief_edit', args=[brief.pk])), data['description'])
                if source != 'MANUAL':
                    self.assertNotIn('current', self.client.session['brief_drafts'])

    def test_persistence_failure_rolls_back_and_preserves_existing_brief_and_draft(self):
        self.client.post(reverse('brief_create'), self.payload())
        brief = Brief.objects.get()
        session = self.client.session
        session['brief_drafts'] = {'retry': {'user_id': self.brand.pk, 'source': 'GEMINI', 'data': self.payload()}}
        session.save()
        with patch('briefs.models.Brief.save', side_effect=DatabaseError('private database detail')):
            response = self.client.post(reverse('brief_edit', args=[brief.pk]), self.payload(title='Unsaved edit'), HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 422)
            self.assertIn('could not be saved', str(response.json()))
            self.assertNotIn('private database detail', str(response.json()))
            response = self.client.post(reverse('brief_create') + '?draft=retry', self.payload(title='Retry edit'))
            self.assertContains(response, 'Your brief could not be saved.')
            self.assertContains(response, 'Retry edit')
        brief.refresh_from_db()
        self.assertEqual(brief.title, 'Amber coffee campaign')
        self.assertEqual(Brief.objects.count(), 1)
        self.assertIn('retry', self.client.session['brief_drafts'])

    def test_json_does_not_bypass_permissions_or_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.brand)
        self.assertEqual(csrf_client.post(reverse('brief_create'), self.payload(), HTTP_ACCEPT='application/json').status_code, 403)
        self.client.force_login(self.creator)
        self.assertEqual(self.client.post(reverse('brief_create'), self.payload(), HTTP_ACCEPT='application/json').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('brief_create'), self.payload(), HTTP_ACCEPT='application/json').status_code, 302)
        self.assertFalse(Brief.objects.exists())
