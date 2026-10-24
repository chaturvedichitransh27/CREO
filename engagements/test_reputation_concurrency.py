from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.core.exceptions import ValidationError
from django.db import connection, connections, close_old_connections
from django.test import TransactionTestCase
from . import test_reputation as fixtures
from .test_reputation import complete, scores
from .models import CreatorReview
from .reputation import submit_review
from accounts.models import User


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL concurrent writers')
class ConcurrentReviewTests(TransactionTestCase):
    make_engagement = fixtures.ReputationTests.make_engagement

    def test_duplicate_requests_create_exactly_one_review(self):
        # Reuse normal lifecycle fixtures, but use committed rows for two writers.
        fixtures.ReputationTests.setUp(self)
        complete(self.engagement,self.brand,self.creator)
        barrier=Barrier(2)
        actor_id=self.brand.pk
        engagement_id=self.engagement.pk
        def attempt():
            close_old_connections()
            try:
                actor=User.objects.get(pk=actor_id)
                barrier.wait(timeout=10)
                try:
                    submit_review(actor,engagement_id,scores())
                    return 'saved'
                except ValidationError:
                    return 'duplicate'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes=list(workers.map(lambda _:attempt(),range(2)))
        self.assertCountEqual(outcomes,['saved','duplicate'])
        self.assertEqual(CreatorReview.objects.count(),1)
