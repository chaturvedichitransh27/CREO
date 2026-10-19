import copy
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction, connection
from django.test import TestCase, Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from briefs.models import Brief, SemanticMatchCache, GenerationUsage
from briefs.ai import BuilderError
from creators.models import CreatorProfile, Tool, Skill
from creators.forms import ProfileForm
from creators.views import public_profiles
from portfolio.models import PortfolioItem
from .matching import score_creator, rank_creators, overlap
from . import semantic


class SmartMatchingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.brand = User.objects.create_user(username='brand', email='b@example.com', role='BRAND')
        self.owner = User.objects.create_user(username='maker', email='m@example.com')
        self.other = User.objects.create_user(username='other', email='o@example.com', role='BRAND')
        self.profile = CreatorProfile.objects.create(user=self.owner, display_name='Maker', price_min=100, price_max=200,
            currency='USD', turnaround_days=4, experience_years=3)
        self.project = PortfolioItem.objects.create(creator=self.profile, title='Premium coffee', description='Luxury coffee campaign',
            workflow='Create a luxury coffee campaign with warm light.', style='Luxury', format='PRODUCT', aspect_ratio='1:1',
            video_url='https://vimeo.com/123456789')
        self.brief = Brief.objects.create(brand=self.brand, title='Coffee brief', description='Luxury coffee campaign',
            deliverables='Coffee campaign', style='Luxury', content_type='PRODUCT', aspect_ratio='1:1', currency='USD',
            budget_min=100, budget_max=300, min_experience_years=3, deadline=timezone.localdate()+timedelta(days=10))
        self.url = reverse('brief_matches', args=[self.brief.pk])
        self.client.force_login(self.brand)

    def row(self, label, **changes):
        for field, value in changes.items():
            setattr(self.profile, field, value)
        return next(r for r in score_creator(self.brief, self.profile)['rows'] if r['label'] == label)

    def answer(self):
        return {'matches': [{'creator_id': self.profile.pk, 'project_id': self.project.pk,
            'similarity': 85, 'reason': 'Premium and luxury describe a similar positioning.', 'evidence_quote': 'Luxury coffee campaign'}]}

    def save_insight(self):
        req = {k: list(getattr(self.brief, k).all()) for k in ['skills', 'tools', 'specializations']}
        SemanticMatchCache.objects.update_or_create(brief=self.brief, defaults={
            'fingerprint': semantic.brief_key(self.brief, req), 'expires_at': timezone.now()+timedelta(hours=1),
            'results': {str(self.profile.pk): {'fingerprint': semantic.creator_key(self.profile), 'match': self.answer()['matches'][0]}}})

    def test_budget_boundaries_and_unknown_currency(self):
        self.assertEqual(self.row('Budget')['earned'], 12)
        self.assertEqual(self.row('Budget', price_min=200, price_max=400)['earned'], 6)
        self.assertEqual(self.row('Budget', price_min=301, price_max=400)['earned'], 0)
        self.assertEqual(self.row('Budget', price_min=300, price_max=300)['earned'], 12)
        self.assertFalse(self.row('Budget', currency='INR')['known'])
        self.assertFalse(self.row('Budget', price_min=None, price_max=None)['known'])

    def test_deadline_calendar_boundaries_start_and_availability(self):
        self.assertEqual(self.row('Deadline', turnaround_days=10)['earned'], 10)
        self.assertEqual(self.row('Deadline', turnaround_days=11)['earned'], 0)
        self.assertEqual(self.row('Deadline', turnaround_days=4, available_from=timezone.localdate()+timedelta(days=7))['earned'], 0)
        self.assertFalse(self.row('Deadline', availability='LIMITED', available_from=None)['known'])
        self.assertEqual(self.row('Deadline', availability='UNAVAILABLE')['earned'], 0)
        self.brief.deadline = timezone.localdate()-timedelta(days=1)
        self.assertEqual(self.row('Deadline')['earned'], 0)

    def test_unknowns_are_not_full_matches_and_zero_experience_is_known(self):
        self.profile.price_min = self.profile.price_max = self.profile.turnaround_days = self.profile.experience_years = None
        result = score_creator(self.brief, self.profile)
        self.assertLess(result['score'], 100)
        self.assertLess(result['coverage'], 100)
        self.assertGreaterEqual(len(result['missing']), 3)
        self.assertTrue(self.row('Experience', experience_years=0)['known'])
        self.assertEqual(self.row('Experience')['earned'], 0)

    def test_optional_experience_excluded_and_synonyms_match(self):
        self.brief.min_experience_years = None
        self.assertNotIn('Experience', [r['label'] for r in score_creator(self.brief, self.profile)['rows']])
        self.assertEqual(overlap('premium minimalist', 'luxury minimalism'), 1)

    def test_score_bounds_and_explanations_reconcile(self):
        for insight in [None, self.answer()['matches'][0]]:
            result = score_creator(self.brief, self.profile, insight=insight)
            self.assertTrue(0 <= result['score'] <= 100)
            self.assertAlmostEqual(result['score'], 100*sum(r['earned'] for r in result['rows'])/result['weight_total'], delta=.5)

    def test_profile_form_and_database_validation(self):
        data = {'display_name': 'Maker', 'availability': 'AVAILABLE', 'currency': 'USD', 'price_min': 100, 'price_max': 50, 'turnaround_days': 0, 'experience_years': 61}
        form = ProfileForm(data, instance=self.profile)
        self.assertFalse(form.is_valid())
        self.assertIn('price_max', form.errors)
        self.assertIn('turnaround_days', form.errors)
        self.assertIn('experience_years', form.errors)
        for changes in [{'price_min': 0}, {'price_max': None}, {'turnaround_days': 0}, {'experience_years': 61}]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                CreatorProfile.objects.filter(pk=self.profile.pk).update(**changes)

    def test_existing_profile_can_keep_new_fields_unknown(self):
        user = User.objects.create_user(username='legacy', email='legacy@example.com')
        profile = CreatorProfile.objects.create(user=user, display_name='Legacy')
        self.assertIsNone(profile.price_min)
        self.assertIsNone(profile.turnaround_days)
        self.assertEqual(self.client.get(profile.get_absolute_url()).status_code, 200)

    def test_edit_profile_persists_terms_and_rejects_brand(self):
        self.client.force_login(self.owner)
        data = {'display_name': 'Maker', 'availability': 'AVAILABLE', 'currency': 'USD', 'price_min': '120', 'price_max': '500', 'turnaround_days': '7', 'experience_years': '2'}
        self.assertEqual(self.client.post(reverse('profile_edit'), data).status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.price_max, Decimal('500'))
        self.assertEqual(self.profile.turnaround_days, 7)
        self.client.force_login(self.brand)
        self.assertEqual(self.client.post(reverse('profile_edit'), data).status_code, 403)

    def test_permission_csrf_and_consent_before_ai(self):
        with patch('discovery.semantic.request_insights') as call:
            self.client.post(self.url, {})
            call.assert_not_called()
            client = Client(enforce_csrf_checks=True)
            client.force_login(self.brand)
            self.assertEqual(client.post(self.url, {'consent': 'on'}).status_code, 403)
            self.client.force_login(self.other)
            self.assertEqual(self.client.post(self.url, {'consent': 'on'}).status_code, 404)
            self.client.force_login(self.owner)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            call.assert_not_called()

    def test_get_never_calls_ai_and_private_responses_not_cached(self):
        with patch('discovery.semantic.request_insights') as call:
            response = self.client.get(self.url)
            self.assertContains(response, 'Smart Matching 2.0')
            self.assertIn('no-store', response['Cache-Control'])
            call.assert_not_called()

    def test_ai_validation_rejects_fabricated_ids_quotes_scores_and_extra_keys(self):
        supplied = semantic.candidates([self.profile])
        semantic.validate_response(self.answer(), supplied)
        for field, value in [('creator_id', 999), ('creator_id', True), ('project_id', 999), ('similarity', 101),
                             ('similarity', True), ('reason', 'x'*301), ('evidence_quote', 'fabricated evidence')]:
            data = self.answer()
            data['matches'][0][field] = value
            with self.assertRaises(BuilderError):
                semantic.validate_response(data, supplied)
        data = self.answer()
        data['matches'].append(copy.deepcopy(data['matches'][0]))
        with self.assertRaises(BuilderError):
            semantic.validate_response(data, supplied)
        data = self.answer()
        data['secret'] = 'unexpected'
        with self.assertRaises(BuilderError):
            semantic.validate_response(data, supplied)

    @override_settings(GEMINI_API_KEY='test-only', GEMINI_FREE_TIER_CONFIRMED=True)
    def test_semantic_generation_cache_comparison_and_quota(self):
        with patch('discovery.semantic.request_insights', return_value={str(self.profile.pk): self.answer()['matches'][0]}) as call:
            self.assertEqual(self.client.post(self.url, {'consent': 'on'}).status_code, 302)
            self.assertEqual(self.client.post(self.url, {'consent': 'on'}).status_code, 302)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(GenerationUsage.objects.get(user=self.brand).attempts, 1)
        response = self.client.get(self.url)
        self.assertContains(response, 'Gemini semantic analysis')
        self.client.post(reverse('comparison_update', args=[self.profile.pk]))
        comparison = self.client.get(reverse('creator_compare'), {'brief': self.brief.pk})
        self.assertEqual(response.context['matches'][0]['score'], comparison.context['cards'][0]['score'])

    def test_cache_invalidated_by_brief_profile_portfolio_and_taxonomy(self):
        for action in ['profile', 'project', 'brief', 'tools', 'expiry']:
            self.save_insight()
            self.assertIn('Gemini', rank_creators(self.brief, public_profiles())[0]['semantic_mode'])
            if action == 'profile':
                self.profile.turnaround_days += 1
                self.profile.save()
            elif action == 'project':
                self.project.workflow += ' New finish.'
                self.project.save()
            elif action == 'brief':
                self.brief.description += ' Updated direction.'
                self.brief.save()
            elif action == 'tools':
                self.profile.tools.add(Tool.objects.create(name='New tool'))
            else:
                SemanticMatchCache.objects.filter(brief=self.brief).update(expires_at=timezone.now()-timedelta(seconds=1))
            self.assertIn('Local', rank_creators(self.brief, public_profiles())[0]['semantic_mode'])

    def test_score_cache_invalidation_without_expiry(self):
        old = score_creator(self.brief, self.profile)['score']
        self.profile.price_min, self.profile.price_max = 1000, 2000
        new = score_creator(self.brief, self.profile)['score']
        self.assertLess(new, old)

    @override_settings(GEMINI_API_KEY='test-only', GEMINI_FREE_TIER_CONFIRMED=True)
    def test_ai_failure_falls_back_and_quota_blocks_repeated_requests(self):
        with patch('discovery.semantic.request_insights', side_effect=BuilderError('Gemini unavailable')) as call:
            self.assertContains(self.client.post(self.url, {'consent': 'on'}), 'Gemini unavailable')
            response = self.client.post(self.url, {'consent': 'on'})
            self.assertContains(response, 'Generation limit')
            self.assertEqual(call.call_count, 1)
        self.assertContains(response, 'Local relevance fallback')

    @override_settings(GEMINI_API_KEY='test-only', GEMINI_FREE_TIER_CONFIRMED=True)
    def test_transport_validation_and_no_private_evidence_sent(self):
        supplied = semantic.candidates([self.profile])
        envelope = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(self.answer())}]}}]}
        mock = MagicMock()
        mock.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
        with patch('discovery.semantic.urlopen', return_value=mock) as call:
            self.assertEqual(semantic.request_insights(self.brief, supplied)[str(self.profile.pk)]['similarity'], 85)
            payload = json.loads(call.call_args.args[0].data)
            data = json.loads(payload['contents'][0]['parts'][0]['text'])
            self.assertEqual(set(data['creators'][0]), {'creator_id', 'projects'})
            self.assertNotIn('usage_requirements', data['brief'])
        for error in [URLError('offline'), TimeoutError(), HTTPError('url', 429, 'quota', {}, None)]:
            with patch('discovery.semantic.urlopen', side_effect=error), self.assertRaises(BuilderError):
                semantic.request_insights(self.brief, supplied)
        for raw in [b'bad json', b'x'*262145, b'{"candidates":[]}']:
            mock.__enter__.return_value.read.return_value = raw
            with patch('discovery.semantic.urlopen', return_value=mock), self.assertRaises(BuilderError):
                semantic.request_insights(self.brief, supplied)

    def test_unconfigured_ai_keeps_matching_available(self):
        with override_settings(GEMINI_FREE_TIER_CONFIRMED=False), patch('discovery.semantic.urlopen') as call:
            self.assertContains(self.client.post(self.url, {'consent': 'on'}), 'not enabled')
            call.assert_not_called()

    def test_ai_html_is_escaped(self):
        self.save_insight()
        entry = SemanticMatchCache.objects.get(brief=self.brief)
        entry.results[str(self.profile.pk)]['match']['reason'] = '<script>alert(1)</script>'
        entry.save()
        response = self.client.get(self.url)
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_htmx_filters_empty_results_and_pagination(self):
        self.profile.availability = 'UNAVAILABLE'
        self.profile.save()
        response = self.client.get(self.url, {'available_only': 'on'}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'No creators match')
        self.assertNotContains(response, '<html')
        self.assertIn('HX-Request', response['Vary'])
        self.assertContains(self.client.get(self.url, {'min_score': 101}), 'Check the filters')
        for i in range(13):
            user = User.objects.create_user(username=f'list{i}', email=f'list{i}@example.com')
            CreatorProfile.objects.create(user=user, display_name=f'Person {i}')
        response = self.client.get(self.url, {'min_score': 0})
        self.assertEqual(len(response.context['matches']), 12)
        self.assertContains(response, 'min_score=0&amp;page=2')

    def test_queries_bounded_for_larger_catalog(self):
        for i in range(20):
            user = User.objects.create_user(username=f'query{i}', email=f'query{i}@example.com')
            CreatorProfile.objects.create(user=user, display_name=f'Creator {i}')
        with CaptureQueriesContext(connection) as queries:
            results = rank_creators(self.brief, public_profiles())
        self.assertEqual(len(results), 21)
        self.assertLessEqual(len(queries), 11)
