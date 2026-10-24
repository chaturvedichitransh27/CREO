"""Run only on an explicitly configured isolated PostgreSQL test database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.db import connection, connections, close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone
from django.utils.datastructures import MultiValueDict
from accounts.models import User
from .models import Brief, ReferenceImage
from .forms import BriefForm
from .references import ReferenceForm, save_with_references
from .test_references import upload


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL concurrent writers')
class ConcurrentReferenceTests(TransactionTestCase):
    def test_simultaneous_uploads_cannot_exceed_three(self):
        brand = User.objects.create_user(username='reference-race', email='race@example.invalid', role='BRAND')
        data = dict(title='Race', description='Test', deliverables='Film', content_type='FILM',
            style='Cinematic', aspect_ratio='16:9', budget_min=1, budget_max=2, currency='INR',
            deadline=timezone.localdate()+timedelta(days=10), status='DRAFT')
        brief = Brief.objects.create(brand=brand, **data)
        barrier = Barrier(2)
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        field = ReferenceImage._meta.get_field('image')
        original = field.storage
        field.storage = FileSystemStorage(location=directory.name)
        self.addCleanup(setattr, field, 'storage', original)

        def attempt():
            close_old_connections()
            try:
                parent = Brief.objects.get(pk=brief.pk)
                actor = User.objects.get(pk=brand.pk)
                form = BriefForm(data, instance=parent)
                refs = ReferenceForm({}, MultiValueDict({'reference_uploads':[upload(),upload()]}), parent=parent)
                assert form.is_valid() and refs.is_valid()
                barrier.wait(timeout=10)
                try:
                    save_with_references(form, refs, actor, form.save)
                    return 'saved'
                except ValidationError:
                    return 'full'
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(outcomes, ['saved', 'full'])
        self.assertEqual(brief.reference_images.filter(active=True).count(), 2)
