from datetime import timedelta
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.db import connection
from django.test.utils import CaptureQueriesContext
from accounts.models import User
from briefs.models import Brief
from creators.models import CreatorProfile, Tool, Skill, Specialization
from creators.views import public_profiles
from portfolio.models import PortfolioItem
from .matching import score_creator, rank_creators, commercial_warnings


class DiscoveryTests(TestCase):
    def setUp(self):
        self.brand = User.objects.create_user(username='brand', email='brand@example.com', role='BRAND')
        self.creator = User.objects.create_user(username='maker', email='maker@example.com')
        self.profile = CreatorProfile.objects.create(user=self.creator, display_name='A Maker', availability='AVAILABLE', price_min=100, price_max=300, turnaround_days=5, experience_years=4)
        self.tool = Tool.objects.create(name='Runway')
        self.skill = Skill.objects.create(name='Art direction')
        self.specialty = Specialization.objects.create(name='Product campaigns')
        self.profile.tools.add(self.tool)
        self.profile.skills.add(self.skill)
        self.profile.specializations.add(self.specialty)
        self.item = PortfolioItem.objects.create(creator=self.profile, title='Coffee in motion', description='A coffee campaign with three images',
            video_url='https://vimeo.com/123456789', format='PRODUCT', aspect_ratio='1:1', style='Sculptural product',
            workflow='Create original source imagery, animate, and finish.', licensing_notes='Original sources, terms to confirm.')
        self.item.tools.add(self.tool)
        self.brief = Brief.objects.create(brand=self.brand, title='Coffee launch', description='Coffee campaign', deliverables='Three images',
            content_type='PRODUCT', style='Sculptural product', aspect_ratio='1:1', budget_min=100, budget_max=500,
            deadline=timezone.localdate() + timedelta(days=20), min_experience_years=3, commercial_use=True, usage_requirements='Owned social for 3 months')
        self.brief.tools.add(self.tool)
        self.brief.skills.add(self.skill)
        self.brief.specializations.add(self.specialty)

    def test_combined_filters(self):
        params = {'tool': self.tool.pk, 'skill': self.skill.pk, 'specialization': self.specialty.pk, 'format': 'PRODUCT',
                  'style': 'sculptural', 'aspect': '1:1', 'availability': 'AVAILABLE'}
        response = self.client.get(reverse('directory'), params)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        params['availability'] = 'UNAVAILABLE'
        response = self.client.get(reverse('directory'), params)
        self.assertEqual(response.context['page_obj'].paginator.count, 0)
        self.assertContains(response, 'Remove availability')
        self.assertTrue(any(s['field'] == 'availability' and s['count'] == 1 for s in response.context['suggestions']))

    def test_portfolio_filters_must_match_same_item(self):
        PortfolioItem.objects.create(creator=self.profile, title='Other image', format='IMAGE', aspect_ratio='9:16',
                                     style='Neon', workflow='Original', description='Image', video_url='https://vimeo.com/123456789')
        response = self.client.get(reverse('directory'), {'format': 'PRODUCT', 'style': 'Neon'})
        self.assertEqual(response.context['page_obj'].paginator.count, 0)

    def test_invalid_filters_do_not_silently_expand_results(self):
        for data in [{'tool': '999999'}, {'skill': 'bad'}, {'availability': 'bogus'}, {'q': 'x' * 121}]:
            response = self.client.get(reverse('directory'), data)
            self.assertContains(response, 'Some filters are invalid')
            self.assertEqual(response.context['page_obj'].paginator.count, 0)

    def test_filter_pagination_retains_parameters(self):
        for i in range(10):
            user = User.objects.create_user(username=f'extra{i}', email=f'extra{i}@example.com')
            profile = CreatorProfile.objects.create(user=user, display_name=f'Extra {i}')
            profile.tools.add(self.tool)
        response = self.client.get(reverse('directory'), {'tool': self.tool.pk, 'availability': 'AVAILABLE'})
        self.assertContains(response, f'tool={self.tool.pk}&amp;availability=AVAILABLE&amp;page=2')

    def test_full_match_and_explainable_points(self):
        result = score_creator(self.brief, self.profile)
        self.assertEqual(result['score'], 100)
        self.assertEqual(sum(r['weight'] for r in result['rows']), 100)
        self.assertEqual(sum(r['earned'] for r in result['rows']), 100)

    def test_profile_only_tools_receive_half_credit(self):
        self.item.tools.clear()
        result = score_creator(self.brief, self.profile)
        tools = next(r for r in result['rows'] if r['label'] == 'Tools')
        self.assertEqual(tools['earned'], 5)
        self.assertEqual(result['score'], 95)

    def test_no_portfolio_never_gets_demonstrated_format_points(self):
        self.item.delete()
        result = score_creator(self.brief, self.profile)
        self.assertEqual(result['score'], 60)
        self.assertEqual(next(r for r in result['rows'] if r['label'] == 'Content type')['earned'], 0)

    def test_empty_optional_requirements_are_removed_from_denominator(self):
        self.brief.tools.clear()
        self.brief.skills.clear()
        self.brief.specializations.clear()
        result = score_creator(self.brief, self.profile)
        self.assertEqual(result['weight_total'], 73)
        self.assertEqual(result['score'], 100)

    def test_partial_style_and_unavailable_are_lower_scores(self):
        self.brief.style = 'Sculptural neon'
        self.profile.availability = 'UNAVAILABLE'
        result = score_creator(self.brief, self.profile)
        self.assertEqual(next(r for r in result['rows'] if r['label'] == 'Visual style')['earned'], 5)
        self.assertEqual(result['score'], 75)
        self.assertEqual(next(r for r in result['rows'] if r['label'] == 'Deadline')['earned'], 0)

    def test_restriction_missing_notes_and_demo_warning(self):
        self.item.licensing_notes = 'Personal use only; non-commercial.'
        self.item.is_demo = True
        warnings = commercial_warnings(self.brief, self.profile, [self.item])
        self.assertTrue(any('restriction language' in w for w in warnings))
        self.assertTrue(any('Fictional demo' in w for w in warnings))
        self.assertTrue(any('hosted video' in w for w in warnings))
        self.item.licensing_notes = ''
        self.assertTrue(any('no source or usage notes' in w for w in commercial_warnings(self.brief, self.profile, [self.item])))

    def test_rankings_repeatable_and_queries_bounded(self):
        for i in range(5):
            user = User.objects.create_user(username=f'more{i}', email=f'more{i}@example.com')
            CreatorProfile.objects.create(user=user, display_name=f'Creator {i}')
        with CaptureQueriesContext(connection) as queries:
            first = rank_creators(self.brief, public_profiles())
        # Portfolio evidence and semantic cache each add a single batched query.
        self.assertLessEqual(len(queries), 11)
        second = rank_creators(self.brief, public_profiles())
        self.assertEqual([(r['profile'].pk, r['score']) for r in first], [(r['profile'].pk, r['score']) for r in second])
        self.assertEqual(first[0]['profile'], self.profile)

    def test_comparison_add_remove_dedupe_and_three_limit(self):
        ids = [self.profile.pk]
        for i in range(3):
            user = User.objects.create_user(username=f'compare{i}', email=f'compare{i}@example.com')
            ids.append(CreatorProfile.objects.create(user=user, display_name=f'Compare {i}').pk)
        for pk in ids + [self.profile.pk]:
            self.client.post(reverse('comparison_update', args=[pk]))
        self.assertEqual(len(self.client.session['comparison']), 3)
        response = self.client.get(reverse('creator_compare'))
        self.assertContains(response, 'A Maker')
        self.client.post(reverse('comparison_update', args=[self.profile.pk]), {'action': 'remove'})
        self.assertNotIn(self.profile.pk, self.client.session['comparison'])

    def test_comparison_csrf_post_only_and_safe_redirect(self):
        url = reverse('comparison_update', args=[self.profile.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(url).status_code, 403)
        response = self.client.post(url, {'next': 'https://evil.example'})
        self.assertRedirects(response, reverse('creator_compare'))

    def test_private_brief_comparison_access(self):
        self.client.post(reverse('comparison_update', args=[self.profile.pk]))
        url = reverse('creator_compare') + f'?brief={self.brief.pk}'
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.brand)
        self.assertContains(self.client.get(url), '100')
        other = User.objects.create_user(username='otherbrand', email='otherbrand@example.com', role='BRAND')
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse('creator_compare'), {'brief': 'invalid'}).status_code, 404)

    def test_comparison_prunes_inactive_profiles(self):
        self.client.post(reverse('comparison_update', args=[self.profile.pk]))
        self.creator.is_active = False
        self.creator.save()
        self.assertNotContains(self.client.get(reverse('creator_compare')), 'A Maker')
        self.assertEqual(self.client.session['comparison'], [])

    def test_brand_journey_from_brief_to_ranked_comparison(self):
        self.client.force_login(self.brand)
        response = self.client.get(reverse('brief_matches', args=[self.brief.pk]))
        self.assertContains(response, 'A Maker')
        self.assertContains(response, 'Why this match')
        self.assertContains(response, '100/100')
        self.assertContains(response, 'Commercial-use review notes')
        self.assertContains(response, 'Portfolio claims remain unverified.')
        self.client.post(reverse('comparison_update', args=[self.profile.pk]))
        comparison = self.client.get(reverse('creator_compare'), {'brief': self.brief.pk})
        self.assertContains(comparison, 'Coffee launch')
        self.assertContains(comparison, 'Coffee in motion')
        self.assertEqual(comparison.context['cards'][0]['score'], response.context['matches'][0]['score'])
