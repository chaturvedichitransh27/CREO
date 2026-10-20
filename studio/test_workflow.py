"""Actual HTTP requests against an isolated local WSGI server, never production."""
import http.cookiejar
import re
from datetime import timedelta
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor
from unittest.mock import patch
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from accounts.models import User
from creators.models import CreatorProfile
from portfolio.models import PortfolioItem
from .models import Moodboard


@override_settings(GEMINI_FREE_TIER_CONFIRMED=False)
class StudioWorkflowTests(LiveServerTestCase):
    def test_idea_to_approved_direction_matching_and_invited_creator_over_http(self):
        creator = User.objects.create_user(username='http-maker', email='http-maker@example.com', password='Local-test-password-715')
        profile = CreatorProfile.objects.create(user=creator, display_name='HTTP maker', currency='USD', price_min=100,
            price_max=200, turnaround_days=2, experience_years=3)
        PortfolioItem.objects.create(creator=profile, title='Cinematic sneaker film', description='Dramatic sneaker advertisement',
            style='Cinematic', format='FILM', aspect_ratio='9:16', workflow='Dramatic lighting',
            video_url='https://vimeo.com/123456789')
        brand_session = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        creator_session = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

        def get(session, path):
            with session.open(self.live_server_url + path, timeout=10) as response:
                return response.geturl(), response.read().decode()

        def post(session, path, values, token_page=None):
            if token_page is None:
                _, token_page = get(session, path)
            token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', token_page)[1]
            req = Request(self.live_server_url + path, data=urlencode({'csrfmiddlewaretoken': token, **values}).encode(),
                          headers={'Referer': self.live_server_url + path})
            with session.open(req, timeout=10) as response:
                return response.geturl(), response.read().decode()

        with patch('studio.ai.urlopen') as gemini:
            url, _ = post(brand_session, '/accounts/register/', {'first_name': 'HTTP brand', 'username': 'http-brand',
                'email': 'http-brand@example.com', 'role': 'BRAND', 'password1': 'Local-test-password-816',
                'password2': 'Local-test-password-816'})
            self.assertTrue(url.endswith('/dashboard/'))
            url, page = post(brand_session, '/studio/new/', {'idea': 'A cinematic sneaker advertisement', 'mode': 'manual'})
            board_path = urlsplit(url).path
            self.assertRegex(board_path, r'^/studio/\d+/$')
            board = Moodboard.objects.get(pk=int(board_path.strip('/').split('/')[-1]))
            url, page = post(brand_session, board_path + 'edit/', {'revision': board.revision,
                'title': 'Cinematic sneaker direction', 'idea': board.idea, 'visual_style': 'Cinematic'})
            board.refresh_from_db()
            url, page = post(brand_session, board_path + 'cards/new/', {'revision': board.revision,
                'kind': 'COLOR', 'title': 'Midnight', 'color': '#17131D'})
            self.assertIn('--swatch: #17131D', page)
            board.refresh_from_db()
            url, page = post(brand_session, board_path + 'brief/', {'revision': board.revision, 'title': board.title,
                'description': board.idea, 'deliverables': 'One film', 'content_type': 'FILM', 'aspect_ratio': '9:16',
                'style': 'Minimal', 'budget_min': '100', 'budget_max': '500', 'currency': 'USD',
                'deadline': (timezone.localdate()+timedelta(days=14)).isoformat(), 'status': 'ACTIVE'})
            board.refresh_from_db()
            self.assertIsNotNone(board.brief_id)
            url, page = post(brand_session, board_path + 'action/', {'action': 'approve', 'revision': board.revision}, token_page=page)
            self.assertIn('Approved revision', page)
            _, matching = get(brand_session, f'/briefs/{board.brief_id}/matches/')
            self.assertIn('HTTP maker', matching)
            self.assertIn('Local relevance fallback', matching)
            self.assertIn('Budget', matching)
            self.assertIn('Deadline', matching)
            self.assertIn('Why this match', matching)
            board.brief.refresh_from_db()
            self.assertEqual(board.brief.style, 'Cinematic')
            from django.urls import reverse
            invitation_path = reverse('invite_creator', args=[board.brief_id, profile.pk])
            url, page = post(brand_session, invitation_path, {'message': 'Please review the approved creative direction.', 'share': 'on'})
            engagement_path = urlsplit(url).path
            self.assertIn('View approved moodboard', page)
            post(creator_session, '/accounts/login/', {'username': 'http-maker', 'password': 'Local-test-password-715'})
            _, page = get(creator_session, engagement_path)
            self.assertIn('View approved moodboard', page)
            _, page = get(creator_session, board_path + 'approved/')
            self.assertIn('Cinematic sneaker direction', page)
            self.assertIn('Read-only snapshot', page)
            self.assertNotIn('Save direction', page)
            gemini.assert_not_called()
