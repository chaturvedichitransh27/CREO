import json
from datetime import timedelta
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from creators.models import Tool
from .models import Brief, GenerationUsage
from . import ai


class BriefTests(TestCase):
    def setUp(self):
        self.brand = User.objects.create_user(username='brand', email='brand@example.com', role='BRAND')
        self.other = User.objects.create_user(username='other', email='other@example.com', role='BRAND')
        self.creator = User.objects.create_user(username='creator', email='creator@example.com', role='CREATOR')
        self.client.force_login(self.brand)
        self.tool = Tool.objects.create(name='Runway')

    def payload(self, **changes):
        data = {'title': 'Amber coffee campaign', 'description': 'Warm, sculptural product images for a fictional coffee launch.',
                'audience': 'Design-conscious coffee lovers', 'deliverables': 'Three finished product images', 'content_type': 'PRODUCT',
                'style': 'Sculptural product', 'aspect_ratio': '1:1', 'budget_min': '20000', 'budget_max': '50000',
                'currency': 'INR', 'deadline': (timezone.localdate() + timedelta(days=21)).isoformat(), 'commercial_use': 'on',
                'usage_requirements': 'Owned social channels in India for three months. Confirm source rights.',
                'status': 'ACTIVE', 'tools': [self.tool.pk]}
        data.update(changes)
        return data

    def create(self):
        self.client.post(reverse('brief_create'), self.payload())
        return Brief.objects.get()

    def test_owner_crud_and_dashboard(self):
        brief = self.create()
        self.assertEqual(brief.brand, self.brand)
        self.assertEqual(brief.tools.get(), self.tool)
        self.assertContains(self.client.get(reverse('dashboard')), 'Amber coffee campaign')
        self.assertContains(self.client.get(brief.get_absolute_url()), 'Three finished product images')
        self.assertContains(self.client.get(reverse('brief_matches', args=[brief.pk])), 'How the score works')
        response = self.client.post(reverse('brief_edit', args=[brief.pk]), self.payload(title='Updated direction', status='ARCHIVED'))
        self.assertRedirects(response, brief.get_absolute_url())
        brief.refresh_from_db()
        self.assertEqual(brief.title, 'Updated direction')
        self.assertEqual(brief.status, 'ARCHIVED')
        url = reverse('brief_delete', args=[brief.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(Brief.objects.exists())
        self.assertRedirects(self.client.post(url), reverse('dashboard'))
        self.assertFalse(Brief.objects.exists())

    def test_no_owner_or_source_injection(self):
        self.client.post(reverse('brief_create'), self.payload(brand=self.other.pk, source='GEMINI'))
        brief = Brief.objects.get()
        self.assertEqual(brief.brand, self.brand)
        self.assertEqual(brief.source, 'MANUAL')

    def test_other_brand_cannot_read_edit_delete_or_match(self):
        brief = self.create()
        self.client.force_login(self.other)
        for route in ['brief_detail', 'brief_edit', 'brief_delete', 'brief_matches']:
            with self.subTest(route=route):
                url = reverse(route, args=[brief.pk])
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url, self.payload()).status_code, 404)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'Amber coffee campaign')

    def test_creator_and_anonymous_access_denied(self):
        brief = self.create()
        urls = [reverse('brief_create'), reverse('brief_builder'), brief.get_absolute_url(), reverse('brief_matches', args=[brief.pk])]
        self.client.force_login(self.creator)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, self.payload()).status_code, 403)
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_invalid_budget_deadline_and_commercial_requirements(self):
        cases = [{'budget_max': '1'}, {'budget_min': '-1'}, {'deadline': '2000-01-01'}, {'usage_requirements': ''}, {'currency': 'BAD'}]
        for change in cases:
            with self.subTest(change=change):
                response = self.client.post(reverse('brief_create'), self.payload(**change))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.assertFalse(Brief.objects.exists())

    def test_past_deadline_can_be_archived_without_rewriting_history(self):
        brief = self.create()
        Brief.objects.filter(pk=brief.pk).update(deadline=timezone.localdate() - timedelta(days=3))
        response = self.client.post(reverse('brief_edit', args=[brief.pk]), self.payload(deadline=(timezone.localdate() - timedelta(days=3)).isoformat(), status='ARCHIVED'))
        self.assertEqual(response.status_code, 302)

    def test_local_builder_review_then_save(self):
        idea = 'A violet coffee campaign with original imagery and three square visuals.'
        response = self.client.post(reverse('brief_builder'), {'idea': idea, 'mode': 'local'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Brief.objects.exists())
        draft_page = self.client.get(response.url)
        self.assertContains(draft_page, 'no AI generation was used')
        self.assertContains(draft_page, idea)
        self.assertIsNone(draft_page.context['form'].initial['budget_min'])
        response = self.client.post(response.url, self.payload())
        self.assertRedirects(response, Brief.objects.get().get_absolute_url())
        self.assertEqual(Brief.objects.get().source, 'LOCAL')
        self.assertEqual(self.client.session['brief_drafts'], {})

    @patch('briefs.ai.generate_draft')
    def test_ai_requires_consent(self, generate):
        response = self.client.post(reverse('brief_builder'), {'idea': 'An original cinematic product campaign in warm light.', 'mode': 'gemini'})
        self.assertContains(response, 'Confirm before sending')
        generate.assert_not_called()

    @override_settings(GEMINI_API_KEY='', GEMINI_FREE_TIER_CONFIRMED=False)
    @patch('briefs.ai.urlopen')
    def test_unconfigured_ai_preserves_input_and_never_calls_provider(self, network):
        idea = 'An original cinematic product campaign in warm light.'
        response = self.client.post(reverse('brief_builder'), {'idea': idea, 'mode': 'gemini', 'consent': 'on'})
        self.assertContains(response, 'not configured')
        self.assertContains(response, idea)
        network.assert_not_called()

    @override_settings(GEMINI_API_KEY='test-only', GEMINI_FREE_TIER_CONFIRMED=True)
    @patch('briefs.ai.generate_draft')
    def test_gemini_draft_provenance_and_cooldown(self, generate):
        generate.return_value = ai.local_draft('A cinematic concept for a coffee launch.')
        data = {'idea': 'A cinematic concept for a coffee launch.', 'mode': 'gemini', 'consent': 'on'}
        response = self.client.post(reverse('brief_builder'), data)
        self.assertEqual(response.status_code, 302)
        self.client.post(response.url, self.payload())
        self.assertEqual(Brief.objects.get().source, 'GEMINI')
        limited = self.client.post(reverse('brief_builder'), data)
        self.assertContains(limited, 'Generation limit reached')
        self.assertEqual(generate.call_count, 1)

    def test_csrf_required_on_create_and_builder(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.brand)
        self.assertEqual(client.post(reverse('brief_create'), self.payload()).status_code, 403)
        self.assertEqual(client.post(reverse('brief_builder'), {'mode': 'local', 'idea': 'A sufficiently long creative idea.'}).status_code, 403)

    def test_generated_html_is_escaped(self):
        idea = '<script>alert(1)</script> A surreal coffee campaign.'
        response = self.client.post(reverse('brief_builder'), {'idea': idea, 'mode': 'local'}, follow=True)
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_draft_session_cannot_cross_brand_accounts(self):
        response = self.client.post(reverse('brief_builder'), {'idea': 'A private fictional campaign for the first brand.', 'mode': 'local'})
        url = response.url
        self.client.force_login(self.other)
        response = self.client.get(url)
        self.assertNotContains(response, 'A private fictional campaign for the first brand.')
        self.assertIsNone(response.context['draft'])


@override_settings(GEMINI_API_KEY='test-only-secret', GEMINI_FREE_TIER_CONFIRMED=True, GEMINI_MODEL='gemini-3.5-flash-lite')
class GeminiAdapterTests(TestCase):
    def draft(self):
        return ai.local_draft('Original product visuals for a fictional coffee launch.')

    def response(self, draft=None, envelope=None):
        if envelope is None:
            envelope = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(draft or self.draft())}]}}]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
        return response

    @patch('briefs.ai.urlopen')
    def test_structured_request_and_valid_json(self, network):
        network.return_value = self.response()
        self.assertEqual(ai.generate_draft('A fictional campaign idea.'), self.draft())
        request = network.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload['generationConfig']['responseMimeType'], 'application/json')
        self.assertEqual(payload['generationConfig']['responseSchema'], ai.SCHEMA)
        self.assertNotIn('test-only-secret', request.full_url)
        self.assertEqual(request.get_header('X-goog-api-key'), 'test-only-secret')
        self.assertEqual(network.call_args.kwargs['timeout'], 20)
        self.assertNotIn('tools', payload)

    @patch('briefs.ai.urlopen')
    def test_network_quota_and_timeout_errors_are_safe(self, network):
        errors = [HTTPError('https://provider.test', 429, 'quota', {}, None),
                  HTTPError('https://provider.test', 403, 'secret-provider-body', {}, None), URLError('secret'), TimeoutError()]
        for error in errors:
            network.side_effect = error
            with self.assertRaises(ai.BuilderError) as exc:
                ai.generate_draft('A fictional idea.')
            self.assertNotIn('secret', str(exc.exception))

    @patch('briefs.ai.urlopen')
    def test_malformed_or_blocked_provider_payloads(self, network):
        for envelope in [{}, {'candidates': []}, {'candidates': [{'finishReason': 'SAFETY'}]},
                         {'candidates': [{'finishReason': 'STOP', 'content': {'parts': ['invalid']}}]},
                         {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'not JSON'}]}}]}]:
            network.return_value = self.response(envelope=envelope)
            with self.assertRaises(ai.BuilderError):
                ai.generate_draft('A fictional idea.')

    @patch('briefs.ai.urlopen')
    def test_response_size_limit(self, network):
        network.return_value.__enter__.return_value.read.return_value = b'x' * 262145
        with self.assertRaises(ai.BuilderError):
            ai.generate_draft('A fictional idea.')

    def test_json_shape_types_ranges_and_dates(self):
        for changes in [{'brand': 42}, {'title': []}, {'title': 'x' * 141}, {'content_type': 'UNKNOWN'},
                        {'budget_min': True}, {'budget_max': 'NaN'}, {'budget_min': '5', 'budget_max': '1'},
                        {'deadline': '2000-01-01'}, {'deadline': '2027-99-99'}, {'commercial_use': 'yes'}]:
            with self.subTest(changes=changes), self.assertRaises(ai.BuilderError):
                ai.validate_draft(dict(self.draft(), **changes))

    @override_settings(GEMINI_MODEL='paid-only-unknown-model')
    @patch('briefs.ai.urlopen')
    def test_unknown_model_never_calls_provider(self, network):
        with self.assertRaises(ai.BuilderError):
            ai.generate_draft('A fictional idea.')
        network.assert_not_called()

    @override_settings(GEMINI_DAILY_LIMIT=10, GEMINI_COOLDOWN_SECONDS=30)
    def test_daily_quota_and_next_day_reset(self):
        user = User.objects.create_user(username='quota', email='quota@example.com', role='BRAND')
        usage = GenerationUsage.objects.create(user=user, day=timezone.localdate(), attempts=10,
                                               last_attempt=timezone.now() - timedelta(minutes=2))
        with self.assertRaises(ai.BuilderError):
            ai.reserve_attempt(user)
        usage.day -= timedelta(days=1)
        usage.save()
        ai.reserve_attempt(user)
        usage.refresh_from_db()
        self.assertEqual(usage.attempts, 1)
