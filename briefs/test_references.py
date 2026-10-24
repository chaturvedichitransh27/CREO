from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PIL import Image
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, Client
from django.urls import reverse
from creators.models import CreatorProfile
from engagements.models import Engagement
from portfolio.storage import DatabaseMediaStorage
from .models import Brief, ReferenceImage
from . import tests as brief_tests


def upload(name='reference.png', fmt='PNG', size=(40, 70)):
    out = BytesIO()
    Image.new('RGB', size, 'purple').save(out, format=fmt)
    return SimpleUploadedFile(name, out.getvalue(), content_type='image/'+fmt.lower())


class ReferenceTests(TestCase):
    payload = brief_tests.BriefTests.payload

    def setUp(self):
        brief_tests.BriefTests.setUp(self)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        field = ReferenceImage._meta.get_field('image')
        self.original_storage = field.storage
        field.storage = FileSystemStorage(location=self.directory.name)
        self.addCleanup(setattr, field, 'storage', self.original_storage)
        self.profile = CreatorProfile.objects.create(user=self.creator, display_name='Reference viewer')

    def create(self, count=0, **overrides):
        response = self.client.post(reverse('brief_create'), self.payload(**{'reference_uploads': [upload() for _ in range(count)], **overrides}))
        self.assertEqual(response.status_code, 302)
        return Brief.objects.latest('pk')

    def edit(self, brief, **data):
        return self.client.post(reverse('brief_edit', args=[brief.pk]), self.payload(**data))

    def test_optional_zero_one_and_three(self):
        for count in [0, 1, 3]:
            brief = self.create(count)
            refs = list(brief.reference_images.all())
            self.assertEqual(len(refs), count)
            self.assertEqual([r.position for r in refs], list(range(count)))
            response = self.client.get(brief.get_absolute_url())
            self.assertEqual(response.content.count(b'data-reference-carousel'), int(bool(count)))
            self.assertEqual(response.content.count(b'class="reference-slide"'), count)
            for ref in refs:
                with ref.image.open('rb') as file, Image.open(file) as image:
                    self.assertEqual(image.format, 'WEBP')
                    self.assertEqual(image.size, (40, 70))
                    self.assertFalse(image.getexif())

    def test_reject_four_without_saving_parent(self):
        response = self.client.post(reverse('brief_create'), self.payload(reference_uploads=[upload() for _ in range(4)]))
        self.assertContains(response, 'maximum of 3 reference images')
        self.assertFalse(Brief.objects.exists())
        self.assertFalse(ReferenceImage.objects.exists())

    def test_reject_invalid_corrupt_empty_oversized_and_unsupported(self):
        bad = [SimpleUploadedFile('fake.png', b'not an image'), SimpleUploadedFile('empty.png', b''),
               SimpleUploadedFile('large.png', b'x'*(5*1024*1024+1)), upload('wrong.gif', 'GIF'),
               SimpleUploadedFile('corrupt.png', upload().read()[:30])]
        for image in bad:
            with self.subTest(name=image.name):
                response = self.client.post(reverse('brief_create'), self.payload(reference_uploads=[image]))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['references'].errors)
        self.assertFalse(Brief.objects.exists())

    def test_supported_formats_and_filename_not_persisted(self):
        for fmt in ['JPEG', 'PNG', 'WEBP']:
            brief = self.create(reference_uploads=[upload('private-login@example.com.'+fmt.lower(), fmt)])
            self.assertNotIn('private-login', brief.reference_images.get().image.name)

    def test_add_remove_replace_and_unchanged(self):
        brief = self.create(2)
        original = list(brief.reference_images.all())
        self.assertEqual(self.edit(brief, reference_uploads=[upload()]).status_code, 302)
        self.assertEqual(brief.reference_images.filter(active=True).count(), 3)
        names = list(brief.reference_images.values_list('image', flat=True))
        self.assertEqual(self.edit(brief, title='Text only').status_code, 302)
        self.assertEqual(list(brief.reference_images.values_list('image', flat=True)), names)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.edit(brief, remove_references=[original[0].pk], reference_uploads=[upload()])
        self.assertEqual(response.status_code, 302)
        refs = list(brief.reference_images.all())
        self.assertEqual([r.position for r in refs], [0, 1, 2])
        self.assertEqual(refs[1].pk, original[1].pk)
        self.assertFalse(original[0].image.storage.exists(original[0].image.name))
        with self.captureOnCommitCallbacks(execute=True):
            self.edit(brief, remove_references=[refs[0].pk])
        self.assertEqual(brief.reference_images.count(), 2)

    def test_total_limit_and_failed_parent_validation_preserve_images(self):
        brief = self.create(3)
        ids = list(brief.reference_images.values_list('pk', flat=True))
        response = self.edit(brief, reference_uploads=[upload()])
        self.assertContains(response, 'maximum of 3 reference images')
        response = self.edit(brief, title='', remove_references=[ids[0]], reference_uploads=[upload()])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(brief.reference_images.values_list('pk', flat=True)), ids)

    def test_cross_brand_creator_anonymous_and_csrf(self):
        brief = self.create(1)
        ref = brief.reference_images.get()
        for user in [self.other, self.creator, None]:
            self.client.logout()
            if user:
                self.client.force_login(user)
            response = self.edit(brief, remove_references=[ref.pk], reference_uploads=[upload()])
            self.assertIn(response.status_code, [302, 403, 404])
            self.assertEqual(self.client.get(ref.get_absolute_url()).status_code, 404)
        self.assertTrue(ReferenceImage.objects.filter(pk=ref.pk).exists())
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.brand)
        self.assertEqual(client.post(reverse('brief_edit', args=[brief.pk]), self.payload(remove_references=[ref.pk])).status_code, 403)

    def test_foreign_removal_ids_rejected_and_get_does_not_remove(self):
        first, second = self.create(1), self.create(1)
        foreign = second.reference_images.get()
        self.assertEqual(self.edit(first, remove_references=[foreign.pk]).status_code, 200)
        self.client.get(reverse('brief_edit', args=[first.pk]), {'remove_references': foreign.pk})
        self.assertEqual(ReferenceImage.objects.count(), 2)

    def test_invitation_snapshot_retains_old_images_and_never_exposes_later_uploads(self):
        brief = self.create(1)
        old = brief.reference_images.get()
        response = self.client.post(reverse('invite_creator', args=[brief.pk, self.profile.pk]), {'message': 'Please create this campaign.', 'share': 'on'})
        self.assertEqual(response.status_code, 302)
        engagement = Engagement.objects.get(brief=brief)
        self.edit(brief, remove_references=[old.pk], reference_uploads=[upload()], status='DRAFT', title='Private later direction')
        old.refresh_from_db()
        self.assertFalse(old.active)
        new = brief.reference_images.get(active=True)
        self.client.force_login(self.creator)
        response = self.client.get(engagement.get_absolute_url())
        self.assertContains(response, old.get_absolute_url())
        self.assertNotContains(response, new.get_absolute_url())
        self.assertNotContains(response, 'Private later direction')
        response = self.client.get(old.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        response.close()
        self.assertEqual(self.client.get(new.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(brief.get_absolute_url()).status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(old.get_absolute_url()).status_code, 404)

    def test_failed_database_save_rolls_back_parent_removal_and_new_files(self):
        brief = self.create(1)
        old = brief.reference_images.get()
        with patch.object(ReferenceImage, 'save', side_effect=IntegrityError('simulated failure')):
            response = self.edit(brief, title='Must not save', remove_references=[old.pk], reference_uploads=[upload()])
        self.assertContains(response, 'previous images are unchanged')
        brief.refresh_from_db()
        self.assertNotEqual(brief.title, 'Must not save')
        self.assertTrue(old.image.storage.exists(old.image.name))
        self.assertEqual(len(list(Path(self.directory.name).rglob('*.webp'))), 1)
        self.assertTrue(ReferenceImage.objects.filter(pk=old.pk).exists())

    def test_database_constraints_cap_slots_and_require_one_parent(self):
        brief = self.create(3)
        for data in [dict(brief=brief, position=0), dict(brief=brief, position=3), dict(position=0)]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                ReferenceImage.objects.create(image='not-written.webp', **data)

    def test_database_media_storage_and_delete_cleanup(self):
        ReferenceImage._meta.get_field('image').storage = DatabaseMediaStorage()
        brief = self.create(1)
        ref = brief.reference_images.get()
        response = self.client.get(ref.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        response.close()
        with self.assertRaises(ValueError):
            _ = ref.image.url
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('brief_delete', args=[brief.pk]))
        self.assertFalse(ref.image.storage.exists(ref.image.name))

class OpportunityReferenceTests(TestCase):
    def setUp(self):
        from opportunities import tests as posting_tests
        posting_tests.OpportunityTestCase.setUpTestData.__func__(type(self))
        self.payload = posting_tests.OpportunityTestCase.payload.__get__(self)
        self.client.force_login(self.brand)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        field = ReferenceImage._meta.get_field('image')
        original = field.storage
        field.storage = FileSystemStorage(location=self.directory.name)
        self.addCleanup(setattr, field, 'storage', original)

    @classmethod
    def make_posting(cls, **kwargs):
        from opportunities import tests as posting_tests
        return posting_tests.OpportunityTestCase.make_posting.__func__(cls, **kwargs)

    def edit(self, **kwargs):
        return self.client.post(reverse('opportunities:edit', args=[self.posting.pk]), self.payload(**kwargs))

    def test_draft_private_published_public_closed_visible_and_no_unrelated_images(self):
        self.assertEqual(self.edit(reference_uploads=[upload()]).status_code, 302)
        ref = self.posting.reference_images.get()
        self.client.logout()
        self.assertEqual(self.client.get(ref.get_absolute_url()).status_code, 404)
        self.client.force_login(self.brand)
        self.assertEqual(self.edit(action='publish', publish_consent='on', version=1).status_code, 302)
        self.client.logout()
        response = self.client.get(ref.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertContains(self.client.get(self.posting.get_absolute_url()), ref.get_absolute_url())
        other = self.make_posting(brand=self.other, title='Unrelated draft')
        ReferenceImage.objects.create(posting=other, image='brief_references/unrelated.webp', position=0)
        self.assertNotContains(self.client.get(self.posting.get_absolute_url()), 'unrelated.webp')
        from opportunities.services import change_status
        change_status(self.brand, self.posting.pk, 'close', 2)
        self.assertContains(self.client.get(self.posting.get_absolute_url()), ref.get_absolute_url())

    def test_existing_opportunity_fields_and_images_survive_invalid_upload(self):
        self.edit(reference_uploads=[upload()])
        ref = self.posting.reference_images.get()
        response = self.edit(version=1, title='Must not change', remove_references=[ref.pk], reference_uploads=[upload('bad.gif', 'GIF')])
        self.assertEqual(response.status_code, 200)
        self.posting.refresh_from_db()
        self.assertEqual(self.posting.version, 1)
        self.assertNotEqual(self.posting.title, 'Must not change')
        self.assertEqual(self.posting.reference_images.get().pk, ref.pk)

    def test_service_rechecks_total_after_form_validation(self):
        from briefs.forms import BriefForm
        from briefs.references import ReferenceForm, save_with_references
        # Use briefs (no existing version token) to exercise the independent limit.
        data = dict(title='Race', description='Test', deliverables='Film', content_type='FILM',
            style='Cinematic', aspect_ratio='16:9', budget_min=1, budget_max=2, currency='INR',
            deadline=self.posting.deadline, status='DRAFT')
        brief = Brief.objects.create(brand=self.brand, **data)
        first = BriefForm(data, instance=brief)
        second = BriefForm(data, instance=Brief.objects.get(pk=brief.pk))
        from django.utils.datastructures import MultiValueDict
        a = ReferenceForm({}, MultiValueDict({'reference_uploads':[upload(), upload()]}), parent=brief)
        b = ReferenceForm({}, MultiValueDict({'reference_uploads':[upload(), upload()]}), parent=brief)
        self.assertTrue(first.is_valid(), first.errors)
        self.assertTrue(second.is_valid(), second.errors)
        self.assertTrue(a.is_valid())
        self.assertTrue(b.is_valid())
        save_with_references(first, a, self.brand, first.save)
        from django.core.exceptions import ValidationError
        with self.assertRaisesMessage(ValidationError, 'maximum of 3'):
            save_with_references(second, b, self.brand, second.save)
        self.assertEqual(brief.reference_images.count(), 2)

    def test_opportunity_invitation_preserves_selected_images(self):
        from opportunities.models import Application
        from opportunities.services import start_collaboration
        self.edit(action='publish', publish_consent='on', reference_uploads=[upload()])
        ref = self.posting.reference_images.get()
        app = Application.objects.create(posting=self.posting, creator=self.creator, consideration_status='CONSIDERING')
        engagement = start_collaboration(self.brand, app.pk, {'share':True, 'message':'Please create this film.'})
        self.assertEqual(list(engagement.reference_images.all()), [ref])
        self.edit(action='save', publish_consent='on', version=1, remove_references=[ref.pk])
        self.client.logout()
        self.assertEqual(self.client.get(ref.get_absolute_url()).status_code, 404)
        self.client.force_login(self.creator_user)
        self.assertContains(self.client.get(engagement.get_absolute_url()), ref.get_absolute_url())
        response = self.client.get(ref.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_opportunity_cross_brand_creator_and_forged_reference(self):
        self.edit(reference_uploads=[upload()])
        ref = self.posting.reference_images.get()
        for user in [self.other, self.creator_user]:
            self.client.force_login(user)
            self.assertIn(self.edit(version=1, remove_references=[ref.pk]).status_code, [403,404])
        self.client.force_login(self.brand)
        self.assertEqual(self.edit(version=1, remove_references=[999999]).status_code, 200)
        self.assertEqual(self.posting.reference_images.count(), 1)
