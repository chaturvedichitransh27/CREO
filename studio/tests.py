import json
import tempfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from PIL import Image
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from briefs.ai import BuilderError
from briefs.models import Brief, GenerationUsage, SemanticMatchCache
from creators.models import CreatorProfile
from discovery.matching import score_creator
from engagements.models import Engagement
from portfolio.models import PortfolioItem, StoredUpload
from portfolio.storage import DatabaseMediaStorage
from . import ai
from .forms import BoardForm, ElementForm
from .models import Element, Moodboard
from .services import create_from_idea


def upload(name='reference.png'):
    buffer = BytesIO()
    Image.new('RGB', (30, 20), '#aa8877').save(buffer, 'PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


def direction():
    return {'title': 'After dark', 'visual_style': 'Cinematic', 'mood': 'Dramatic amber lighting.',
            'palette': [{'label': 'Amber', 'hex': '#CC8844'}, {'label': 'Night', 'hex': '#17131D'}],
            'shots': ['Slow reveal of the sneaker.'], 'references': ['Low-angle studio lighting.'],
            'deliverables': ['One vertical film, duration to confirm.']}


class StudioTests(TestCase):
    def setUp(self):
        cache.clear()
        self.brand = User.objects.create_user(username='studio-brand', email='studio@example.com', role='BRAND')
        self.other = User.objects.create_user(username='other-brand', email='other@example.com', role='BRAND')
        self.creator = User.objects.create_user(username='studio-creator', email='creator@example.com', role='CREATOR')
        self.profile = CreatorProfile.objects.create(user=self.creator, display_name='Studio artist', price_min=100,
            price_max=200, turnaround_days=3, experience_years=3, currency='USD')
        self.brief = Brief.objects.create(brand=self.brand, title='Sneaker campaign', description='Sneaker',
            deliverables='Film', content_type='FILM', aspect_ratio='9:16', style='Minimal',
            budget_min=100, budget_max=500, currency='USD', deadline=timezone.localdate()+timedelta(days=14), status='ACTIVE')
        self.board = create_from_idea(self.brand, 'A dramatic sneaker advertisement', direction())
        self.board.brief = self.brief
        self.board.save()
        self.client.force_login(self.brand)
        self.field = Element._meta.get_field('image')
        original = self.field.storage
        self.field.storage = DatabaseMediaStorage()
        self.addCleanup(setattr, self.field, 'storage', original)

    def url(self, name, *extra):
        return reverse('studio:' + name, args=[self.board.pk, *extra])

    def action(self, action, **kwargs):
        self.board.refresh_from_db()
        return self.client.post(self.url('action'), {'revision': self.board.revision, 'action': action, **kwargs})

    def reference(self):
        self.board.refresh_from_db()
        response = self.client.post(self.url('element_create'), {'revision': self.board.revision,
            'kind': 'REFERENCE', 'title': 'Original test reference', 'text': 'Private image',
            'credit': 'My original image', 'rights_confirmed': 'on', 'image': upload()})
        self.assertEqual(response.status_code, 302)
        return self.board.elements.get(title='Original test reference')

    def invite(self):
        return Engagement.objects.create(brief=self.brief, creator=self.profile, message='Synthetic invitation')

    def test_manual_create_save_reopen_and_private_index(self):
        with patch('studio.ai.urlopen') as remote:
            response = self.client.post(reverse('studio:create'), {'idea': 'A cinematic sneaker commercial', 'mode': 'manual'})
        remote.assert_not_called()
        board = Moodboard.objects.latest('pk')
        self.assertRedirects(response, board.get_absolute_url())
        self.assertEqual(board.source, 'MANUAL')
        self.assertContains(self.client.get(board.get_absolute_url()), 'A cinematic sneaker commercial')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('studio:index')), self.board.title)

    def test_studio_pages_render_and_css_is_scoped(self):
        for name in ['detail', 'edit', 'element_create']:
            self.assertEqual(self.client.get(self.url(name)).status_code, 200)
        for name in ['index', 'create']:
            self.assertEqual(self.client.get(reverse('studio:' + name)).status_code, 200)
        self.assertContains(self.client.get(self.url('detail')), 'css/studio.css')
        self.assertNotContains(self.client.get('/'), 'css/studio.css')
        self.assertContains(self.client.get(reverse('dashboard')), 'Creative Studio')

    def test_brand_role_and_ownership_guards(self):
        for user in [self.other, self.creator]:
            self.client.force_login(user)
            for name in ['detail', 'edit', 'element_create', 'convert']:
                self.assertIn(self.client.get(self.url(name)).status_code, (403, 404))
            self.assertIn(self.action('approve').status_code, (403, 404))
        self.client.logout()
        self.assertEqual(self.client.get(self.url('detail')).status_code, 302)

    def test_board_form_restricts_linked_briefs(self):
        foreign = Brief.objects.create(brand=self.other, title='Private', description='X', deliverables='Y',
            content_type='FILM', aspect_ratio='9:16', style='Z', budget_min=1, budget_max=2, deadline=timezone.localdate())
        unlinked = create_from_idea(self.brand, 'Unlinked board')
        form = BoardForm({'title': 'Attempt', 'idea': '', 'visual_style': '', 'brief': foreign.pk,
                          'revision': unlinked.revision}, instance=unlinked, user=self.brand)
        self.assertFalse(form.is_valid())
        form = BoardForm(instance=unlinked, user=self.brand)
        self.assertNotIn(self.brief, list(form.fields['brief'].queryset))

    def test_edit_metadata_and_link_existing_brief(self):
        unlinked = create_from_idea(self.other, 'Another direction')
        # Use a free brief owned by the same brand.
        self.board.brief = None
        self.board.save()
        response = self.client.post(self.url('edit'), {'title': 'Updated', 'idea': 'Refined', 'visual_style': 'Surreal',
            'brief': self.brief.pk, 'revision': self.board.revision})
        self.assertEqual(response.status_code, 302)
        self.board.refresh_from_db()
        self.assertEqual((self.board.title, self.board.visual_style, self.board.brief_id), ('Updated', 'Surreal', self.brief.pk))
        self.assertEqual(unlinked.owner, self.other)

    def test_stale_edits_rejected(self):
        old = self.board.revision
        first = self.board.elements.first()
        self.action('down', element=first.pk)
        response = self.client.post(self.url('edit'), {'title': 'Stale', 'idea': '', 'visual_style': 'X', 'revision': old})
        self.assertContains(response, 'another tab')
        self.board.refresh_from_db()
        self.assertNotEqual(self.board.title, 'Stale')
        response = self.client.post(self.url('action'), {'action': 'remove', 'element': first.pk, 'revision': old}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'another tab')
        self.assertTrue(Element.objects.filter(pk=first.pk).exists())

    def test_card_reordering_removal_and_htmx(self):
        first = self.board.elements.first()
        response = self.client.post(self.url('action'), {'action': 'down', 'element': first.pk,
            'revision': self.board.revision}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'studio-editor')
        self.assertNotContains(response, '<html')
        self.assertIn('HX-Request', response['Vary'])
        self.assertEqual(list(self.board.elements.all())[1].pk, first.pk)
        self.action('remove', element=first.pk)
        self.assertFalse(Element.objects.filter(pk=first.pk).exists())

    def test_card_limit_and_invalid_style_payload(self):
        form = ElementForm({'revision': 1, 'kind': 'COLOR', 'title': 'Injected', 'color': '#fff; background:url(https://evil.test)'})
        self.assertFalse(form.is_valid())
        for i in range(36 - self.board.elements.count()):
            Element.objects.create(board=self.board, kind='NOTE', title=str(i))
        response = self.client.post(self.url('element_create'), {'revision': self.board.revision, 'kind': 'NOTE', 'title': 'Overflow'})
        self.assertContains(response, 'up to 36')
        self.assertEqual(self.board.elements.count(), 36)

    def test_upload_validation_and_permission_required(self):
        base = {'revision': self.board.revision, 'kind': 'REFERENCE', 'title': 'Image', 'credit': 'Original'}
        self.assertContains(self.client.post(self.url('element_create'), {**base, 'image': upload()}), 'Confirm your permission')
        bad = SimpleUploadedFile('attack.svg', b'<svg onload=\"alert(1)\"></svg>', content_type='image/svg+xml')
        self.assertEqual(self.client.post(self.url('element_create'), {**base, 'image': bad, 'rights_confirmed': 'on'}).status_code, 200)
        self.assertFalse(StoredUpload.objects.exists())
        large = SimpleUploadedFile('large.png', b'x'*(5*1024*1024+1))
        with self.assertRaises(ValidationError):
            from portfolio.validators import validate_image
            validate_image(large)

    def test_private_database_upload_and_normalization(self):
        element = self.reference()
        self.assertTrue(element.image.name.startswith('studio/'))
        row = StoredUpload.objects.get(pk=element.image.name)
        with Image.open(BytesIO(bytes(row.content))) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertFalse(image.getexif())
        response = self.client.get(self.url('image', element.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/webp')
        self.assertIn('no-store', response['Cache-Control'])
        b''.join(response.streaming_content)
        with self.assertRaises(ValueError):
            element.image.url
        self.assertEqual(self.client.get('/media/' + element.image.name).status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('image', element.pk)).status_code, 404)

    def test_filesystem_upload_uses_private_authenticated_route(self):
        with tempfile.TemporaryDirectory() as folder:
            self.field.storage = FileSystemStorage(location=folder)
            element = self.reference()
            response = self.client.get(self.url('image', element.pk))
            self.assertEqual(response.status_code, 200)
            b''.join(response.streaming_content)
            self.assertTrue(self.field.storage.exists(element.image.name))

    def test_approval_updates_style_matching_and_invalidates_ai_cache(self):
        PortfolioItem.objects.create(creator=self.profile, title='Film', description='Sneaker', workflow='Film',
            style='Cinematic', format='FILM', aspect_ratio='9:16', video_url='https://vimeo.com/123456789')
        before = score_creator(self.brief, self.profile)['score']
        SemanticMatchCache.objects.create(brief=self.brief, fingerprint='x'*64, expires_at=timezone.now()+timedelta(days=1))
        self.action('approve')
        self.brief.refresh_from_db()
        self.board.refresh_from_db()
        self.assertEqual(self.brief.style, 'Cinematic')
        self.assertGreater(score_creator(self.brief, self.profile)['score'], before)
        self.assertEqual(self.brief.budget_max, 500)
        self.assertFalse(SemanticMatchCache.objects.filter(brief=self.brief).exists())
        self.assertContains(self.client.get(reverse('brief_matches', args=[self.brief.pk])), 'Local relevance fallback')
        self.assertEqual(self.board.approved_revision, self.board.revision)

    def test_only_invited_creators_see_approved_snapshot(self):
        element = self.reference()
        self.action('approve')
        self.client.force_login(self.creator)
        self.assertEqual(self.client.get(self.url('approved')).status_code, 404)
        engagement = self.invite()
        self.assertContains(self.client.get(self.url('approved')), 'Read-only snapshot')
        self.assertContains(self.client.get(engagement.get_absolute_url()), 'View approved moodboard')
        response = self.client.get(self.url('image', element.pk))
        self.assertEqual(response.status_code, 200)
        b''.join(response.streaming_content)
        engagement.status = 'CANCELLED'
        engagement.save()
        self.assertEqual(self.client.get(self.url('approved')).status_code, 404)
        self.assertEqual(self.client.get(self.url('image', element.pk)).status_code, 404)
        self.assertNotContains(self.client.get(engagement.get_absolute_url()), 'View approved moodboard')

    def test_draft_edits_and_removal_do_not_change_shared_snapshot(self):
        element = self.reference()
        self.invite()
        self.action('approve')
        self.action('remove', element=element.pk)
        self.board.refresh_from_db()
        self.assertTrue(self.board.has_draft_changes)
        self.client.force_login(self.creator)
        self.assertContains(self.client.get(self.url('approved')), 'Original test reference')
        response = self.client.get(self.url('image', element.pk))
        self.assertEqual(response.status_code, 200)
        b''.join(response.streaming_content)
        self.client.force_login(self.brand)
        self.action('approve')
        self.client.force_login(self.creator)
        self.assertNotContains(self.client.get(self.url('approved')), 'Original test reference')
        self.assertEqual(self.client.get(self.url('image', element.pk)).status_code, 404)

    def test_unapproved_upload_is_never_shared_with_invited_creator(self):
        self.invite()
        self.action('approve')
        element = self.reference()
        self.client.force_login(self.creator)
        self.assertEqual(self.client.get(self.url('image', element.pk)).status_code, 404)
        self.assertNotContains(self.client.get(self.url('approved')), element.title)

    def test_withdraw_sharing_keeps_brief_intact(self):
        self.invite()
        self.action('approve')
        self.action('withdraw')
        self.client.force_login(self.creator)
        self.assertEqual(self.client.get(self.url('approved')).status_code, 404)
        self.brief.refresh_from_db()
        self.assertEqual(self.brief.style, 'Cinematic')

    def test_conversion_prefills_and_saves_real_terms_once(self):
        self.board.brief = None
        self.board.save()
        response = self.client.get(self.url('convert'))
        self.assertContains(response, 'After dark')
        self.assertIsNone(response.context['form'].initial.get('budget_min'))
        data = {'title': 'From the board', 'description': 'Approved direction draft', 'deliverables': 'Film',
                'style': 'Cinematic', 'content_type': 'FILM', 'aspect_ratio': '9:16', 'budget_min': 100,
                'budget_max': 300, 'currency': 'USD', 'deadline': (timezone.localdate()+timedelta(days=10)).isoformat(),
                'status': 'DRAFT', 'revision': self.board.revision}
        self.assertRedirects(self.client.post(self.url('convert'), data), self.board.get_absolute_url())
        self.board.refresh_from_db()
        self.assertEqual(self.board.brief.brand, self.brand)
        self.assertEqual(self.board.brief.budget_max, 300)
        self.assertIsNone(self.board.approved_at)
        count = Brief.objects.count()
        self.assertEqual(self.client.post(self.url('convert'), data).status_code, 302)
        self.assertEqual(Brief.objects.count(), count)

    def test_approval_requires_style_link_and_content(self):
        self.board.visual_style = ''
        self.board.save()
        response = self.client.post(self.url('action'), {'action': 'approve', 'revision': self.board.revision}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'select a visual style')
        self.board.refresh_from_db()
        self.assertIsNone(self.board.approved_at)

    def test_csrf_get_safety_and_private_cache(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.brand)
        self.assertEqual(csrf_client.post(self.url('action'), {'action': 'approve', 'revision': 1}).status_code, 403)
        self.assertEqual(self.client.get(self.url('action')).status_code, 405)
        self.assertIn('no-store', self.client.get(self.url('detail'))['Cache-Control'])
        before = self.board.revision
        self.client.get(self.url('detail'))
        self.board.refresh_from_db()
        self.assertEqual(self.board.revision, before)

    def test_element_cannot_be_edited_or_moved_from_another_board(self):
        foreign = create_from_idea(self.other, 'Foreign private idea')
        element = foreign.elements.first()
        self.assertEqual(self.client.get(self.url('element_edit', element.pk)).status_code, 404)
        self.assertEqual(self.action('remove', element=element.pk).status_code, 404)

    def test_output_html_escaped(self):
        Element.objects.create(board=self.board, kind='NOTE', title='<script>alert(1)</script>', text='<img src=x onerror=alert(1)>')
        response = self.client.get(self.url('detail'))
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')

    @override_settings(GEMINI_API_KEY='test-only', GEMINI_FREE_TIER_CONFIRMED=True)
    def test_ai_generation_requires_consent_and_uses_shared_quota(self):
        with patch('studio.ai.urlopen') as remote:
            self.client.post(reverse('studio:create'), {'idea': 'Cinematic sneaker campaign', 'mode': 'gemini'})
            remote.assert_not_called()
        envelope = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(direction())}]}}]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
        with patch('studio.ai.urlopen', return_value=response) as remote:
            result = self.client.post(reverse('studio:create'), {'idea': 'Cinematic sneaker campaign', 'mode': 'gemini', 'consent': 'on'})
            self.assertEqual(result.status_code, 302)
            payload = json.loads(remote.call_args.args[0].data)
            self.assertEqual(payload['contents'][0]['parts'][0]['text'], 'Cinematic sneaker campaign')
            self.assertNotIn('image', payload['contents'][0]['parts'][0])
            self.assertEqual(remote.call_args.kwargs['timeout'], 20)
        board = Moodboard.objects.latest('pk')
        self.assertEqual(board.source, 'GEMINI')
        self.assertEqual(board.elements.count(), 6)
        self.assertFalse(board.elements.exclude(image='').exists())
        self.assertEqual(GenerationUsage.objects.get(user=self.brand).attempts, 1)

    @override_settings(GEMINI_FREE_TIER_CONFIRMED=False)
    def test_disabled_ai_keeps_idea_and_manual_creation(self):
        with patch('studio.ai.urlopen') as remote:
            response = self.client.post(reverse('studio:create'), {'idea': 'Cinematic sneaker campaign', 'mode': 'gemini', 'consent': 'on'})
            self.assertContains(response, 'Cinematic sneaker campaign')
            self.assertContains(response, 'not enabled')
            remote.assert_not_called()
        self.assertFalse(GenerationUsage.objects.exists())

    def test_ai_schema_rejects_extra_fields_injection_and_oversized_lists(self):
        ai.validate_direction(direction())
        for key, value in [('shots', ['x']*7), ('mood', 'x'*1501), ('references', []), ('title', 123)]:
            invalid = direction()
            invalid[key] = value
            with self.assertRaises(BuilderError):
                ai.validate_direction(invalid)
        invalid = direction()
        invalid['palette'][0]['hex'] = '#000000;url(https://evil.test)'
        with self.assertRaises(BuilderError):
            ai.validate_direction(invalid)
        invalid = direction()
        invalid['image_url'] = 'https://evil.test/x.png'
        with self.assertRaises(BuilderError):
            ai.validate_direction(invalid)

    @override_settings(GEMINI_API_KEY='test-only', GEMINI_FREE_TIER_CONFIRMED=True)
    def test_ai_network_quota_and_invalid_responses_are_safe(self):
        for error in [URLError('private request detail'), TimeoutError(), HTTPError('url', 429, 'secret', {}, None), HTTPError('url', 503, 'secret', {}, None)]:
            with patch('studio.ai.reserve_attempt'), patch('studio.ai.urlopen', side_effect=error):
                with self.assertRaises(BuilderError) as caught:
                    ai.generate('Creative idea', self.brand)
                self.assertNotIn('secret', str(caught.exception))
                self.assertNotIn('private request detail', str(caught.exception))
        for raw in [b'not json', b'x'*262145, b'{\"candidates\": []}', b'{\"candidates\": [{\"finishReason\":\"MAX_TOKENS\"}]}']:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = raw
            with patch('studio.ai.reserve_attempt'), patch('studio.ai.urlopen', return_value=response):
                with self.assertRaises(BuilderError):
                    ai.generate('Creative idea', self.brand)

    @override_settings(GEMINI_API_KEY='test-only', GEMINI_FREE_TIER_CONFIRMED=True, GEMINI_DAILY_LIMIT=1)
    def test_shared_quota_prevents_network_calls(self):
        GenerationUsage.objects.create(user=self.brand, day=timezone.localdate(), attempts=1)
        with patch('studio.ai.urlopen') as remote, self.assertRaises(BuilderError):
            ai.generate('Creative idea', self.brand)
        remote.assert_not_called()
