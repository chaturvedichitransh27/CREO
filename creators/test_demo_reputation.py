from decimal import Decimal
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .models import CreatorProfile


class DemoReputationTests(SimpleTestCase):
    def test_samples_are_stable_varied_and_need_no_database(self):
        samples = [CreatorProfile(pk=i).demo_reputation for i in range(1, 11)]
        self.assertEqual(samples[0], samples[5])
        self.assertEqual({sample['completed'] for sample in samples}, {3, 5, 6, 7, 8})
        for sample in samples:
            self.assertTrue(Decimal(1) <= sample['average'] <= Decimal(5))
            self.assertEqual(sample['percent'], sample['average'] * 20)

    def test_all_profiles_show_explicitly_labeled_samples_without_changing_real_totals(self):
        for is_demo in (False, True):
            profile = CreatorProfile(pk=2, is_demo=is_demo)
            profile.reputation = {'count': 0, 'completed': 0, 'average': None}
            html = render_to_string('creators/reputation_summary.html', {'profile': profile})
            self.assertIn('DEMO · SAMPLE DATA', html)
            self.assertIn('5 projects done', html)
            self.assertIn('not verified reviews or completed work', html)
            self.assertIn('Not yet rated', html)
            self.assertIn('0 Verified Reviews', html)
            self.assertIn('0 Completed Projects', html)
            self.assertEqual(profile.reputation['completed'], 0)

    def test_real_reputation_remains_visible(self):
        profile = CreatorProfile(pk=1)
        profile.reputation = {'count': 2, 'completed': 4, 'average': Decimal('4.2'), 'percent': 84}
        html = render_to_string('creators/reputation_summary.html', {'profile': profile})
        self.assertIn('2 Verified Reviews', html)
        self.assertIn('4 Completed Projects', html)
        self.assertIn('4.2', html)
        self.assertIn('3 projects done', html)
