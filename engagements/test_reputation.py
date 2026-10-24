from datetime import timedelta
from decimal import Decimal
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.test import TestCase, Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from accounts.models import User
from briefs.models import Brief
from creators.models import CreatorProfile
from creators.views import public_profiles
from opportunities.models import JobPosting
from .models import Engagement, EngagementEvent, CreatorReview
from .services import transition, snapshot
from .reputation import FACTORS, eligible_engagements, eligible_reviews, submit_review, with_reputation
from .review_forms import CreatorReviewForm
from .admin import CreatorReviewAdmin


def scores(**updates):
    return {**{name:5 for name,_ in FACTORS}, **updates}


def complete(engagement, brand, creator, revisions=False):
    steps = [(creator,'ACCEPTED'),(creator,'IN_PROGRESS'),(creator,'SUBMITTED')]
    if revisions:
        steps += [(brand,'IN_PROGRESS'),(creator,'SUBMITTED')]
    steps += [(brand,'COMPLETED')]
    for actor,status in steps:
        engagement.refresh_from_db()
        transition(engagement,actor,status,engagement.version,note='Normal workflow',delivery_url='https://example.invalid/final-delivery')
    engagement.refresh_from_db()
    return engagement


class ReputationTests(TestCase):
    def setUp(self):
        self.brand = User.objects.create_user(username='rating-brand',email='private-brand@example.invalid',first_name='Private owner',role='BRAND')
        self.other = User.objects.create_user(username='other-brand',email='other@example.invalid',role='BRAND')
        self.creator = User.objects.create_user(username='rating-creator',email='private-creator@example.invalid',role='CREATOR')
        self.profile = CreatorProfile.objects.create(user=self.creator,display_name='Rated Creative')
        self.engagement = self.make_engagement()
        self.client.force_login(self.brand)

    def make_engagement(self, posting=False, brand=None):
        brand = brand or self.brand
        if posting:
            parent = JobPosting.objects.create(brand=brand,brand_name='Public Studio',title='Public Campaign',
                status='CLOSED',published_at=timezone.now(),deadline=timezone.localdate()+timedelta(days=10))
            return Engagement.objects.create(posting=parent,creator=self.profile,brief_snapshot={'title':'Public Campaign'},message='Private invitation')
        parent = Brief.objects.create(brand=brand,title='Confidential project title',description='Secret brief contents',
            deliverables='Private scope',content_type='FILM',style='Cinematic',aspect_ratio='16:9',budget_min=10,budget_max=20,
            deadline=timezone.localdate()+timedelta(days=10),commercial_use=False,status='ACTIVE')
        return Engagement.objects.create(brief=parent,creator=self.profile,brief_snapshot=snapshot(parent),message='Private invitation')

    def finish(self, engagement=None, revisions=False):
        return complete(engagement or self.engagement,self.brand,self.creator,revisions)

    def review_url(self, engagement=None):
        return reverse('engagement_review',args=[(engagement or self.engagement).pk])

    def reputation(self):
        return with_reputation(CreatorProfile.objects.filter(pk=self.profile.pk)).get().reputation

    def test_normal_workflow_and_required_action_only_after_completion(self):
        for status in ['INVITED','ACCEPTED','IN_PROGRESS','SUBMITTED']:
            if status != 'INVITED':
                self.engagement.refresh_from_db()
                transition(self.engagement,self.creator,status,self.engagement.version,delivery_url='https://example.invalid/work')
            self.assertNotContains(self.client.get(self.engagement.get_absolute_url()),'Rate This Creator')
            self.assertEqual(self.client.get(self.review_url()).status_code,403)
            self.assertEqual(self.client.post(self.review_url(),scores()).status_code,403)
        self.engagement.refresh_from_db()
        transition(self.engagement,self.brand,'COMPLETED',self.engagement.version)
        self.assertContains(self.client.get(self.engagement.get_absolute_url()),'Rate This Creator')
        self.assertContains(self.client.get(self.review_url()),'Once submitted, your review cannot be edited.')
        self.assertRedirects(self.client.post(self.review_url(),scores()),self.review_url())
        self.assertContains(self.client.get(self.engagement.get_absolute_url()),'Review Submitted')
        self.assertNotContains(self.client.get(self.engagement.get_absolute_url()),'Rate This Creator')
        self.assertContains(self.client.get(self.review_url()),'5.0 / 5')

    def test_both_sources_and_revision_flow_are_eligible(self):
        self.finish(revisions=True)
        second=self.make_engagement(posting=True)
        self.finish(second)
        self.assertEqual(eligible_engagements().count(),2)
        submit_review(self.brand,self.engagement.pk,scores())
        submit_review(self.brand,second.pk,scores())
        self.assertEqual(self.reputation()['count'],2)

    def test_status_alone_and_wrong_actor_history_are_not_evidence(self):
        Engagement.objects.filter(pk=self.engagement.pk).update(status='COMPLETED',delivery_url='https://example.invalid/work')
        self.assertFalse(eligible_engagements().exists())
        for status in ['ACCEPTED','IN_PROGRESS','SUBMITTED','COMPLETED']:
            EngagementEvent.objects.create(engagement=self.engagement,actor=self.other,status=status)
        self.assertFalse(eligible_engagements().exists())
        with self.assertRaises(ValidationError):
            submit_review(self.brand,self.engagement.pk,scores())

    def test_out_of_order_events_and_missing_delivery_do_not_qualify(self):
        Engagement.objects.filter(pk=self.engagement.pk).update(status='COMPLETED',delivery_url='https://example.invalid/work')
        for actor,status in [(self.brand,'COMPLETED'),(self.creator,'SUBMITTED'),(self.creator,'IN_PROGRESS'),(self.creator,'ACCEPTED')]:
            EngagementEvent.objects.create(engagement=self.engagement,actor=actor,status=status)
        self.assertFalse(eligible_engagements().exists())
        e=self.make_engagement();self.finish(e)
        Engagement.objects.filter(pk=e.pk).update(delivery_url='')
        self.assertFalse(eligible_engagements().exists())

    def test_cancelled_and_declined_history_invalidate_counts_and_reviews(self):
        self.finish();submit_review(self.brand,self.engagement.pk,scores())
        self.assertEqual(self.reputation()['count'],1)
        EngagementEvent.objects.create(engagement=self.engagement,actor=self.brand,status='CANCELLED')
        self.assertEqual(self.reputation()['count'],0)
        self.assertEqual(self.reputation()['completed'],0)
        self.assertEqual(CreatorReview.objects.count(),1)  # Preserved provenance, not publicly counted.
        self.assertEqual(self.client.get(self.review_url()).status_code,403)

    def test_demo_work_is_not_verified(self):
        self.finish()
        self.profile.is_demo=True;self.profile.save()
        self.assertFalse(eligible_engagements().exists())
        self.profile.is_demo=False;self.profile.save()
        e=self.make_engagement(posting=True);self.finish(e)
        e.posting.is_demo=True;e.posting.save()
        self.assertFalse(eligible_engagements().filter(pk=e.pk).exists())

    def test_wrong_users_and_privileged_owners_cannot_submit(self):
        self.finish()
        for actor in [self.other,self.creator]:
            self.client.force_login(actor)
            self.assertIn(self.client.post(self.review_url(),scores()).status_code,[403,404])
        self.client.logout()
        self.assertEqual(self.client.post(self.review_url(),scores()).status_code,302)
        for actor in [self.brand,self.other]:
            for attrs in [{'is_staff':True,'is_superuser':False},{'is_staff':False,'is_superuser':True},{'is_active':False,'is_staff':False,'is_superuser':False}]:
                for key,value in attrs.items():setattr(actor,key,value)
                actor.save()
                with self.assertRaises(PermissionDenied):submit_review(actor,self.engagement.pk,scores())
        self.assertFalse(CreatorReview.objects.exists())

    def test_nonexistent_engagement_and_forged_ownership(self):
        self.finish()
        with self.assertRaises(Http404):submit_review(self.brand,999999,scores())
        for key in ['brand','brand_id','creator','creator_id','engagement','engagement_id']:
            response=self.client.post(self.review_url(),scores(**{key:self.other.pk}))
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'cannot be submitted or changed')
            with self.assertRaises(ValidationError):submit_review(self.brand,self.engagement.pk,scores(**{key:self.other.pk}))
        self.assertFalse(CreatorReview.objects.exists())

    def test_all_categories_required_and_strict_integer_validation(self):
        self.finish()
        for name,_ in FACTORS:
            for value in ['',0,-1,6,1.5,'1.0','5.0','NaN','bad',True,None]:
                with self.subTest(field=name,value=value):
                    data=scores(**{name:value})
                    with self.assertRaises(ValidationError):submit_review(self.brand,self.engagement.pk,data)
                    self.assertFalse(CreatorReviewForm(data).is_valid())
            data=scores();data.pop(name)
            with self.assertRaises(ValidationError):submit_review(self.brand,self.engagement.pk,data)
        self.assertFalse(CreatorReview.objects.exists())

    def test_written_feedback_optional_trimmed_and_bounded(self):
        self.finish()
        with self.assertRaises(ValidationError):submit_review(self.brand,self.engagement.pk,scores(written_feedback='x'*1001))
        review=submit_review(self.brand,self.engagement.pk,scores(written_feedback='  Clear communication.  '))
        self.assertEqual(review.written_feedback,'Clear communication.')
        second=self.make_engagement();self.finish(second)
        self.assertEqual(submit_review(self.brand,second.pk,scores()).written_feedback,'')

    def test_exact_averages_category_values_and_independent_project_counts(self):
        self.finish();review=submit_review(self.brand,self.engagement.pk,scores(timeliness_rating=4,professionalism_rating=4))
        self.assertEqual(review.overall,Decimal('4.6'))
        second=self.make_engagement();self.finish(second)
        submit_review(self.brand,second.pk,scores(timeliness_rating=3,professionalism_rating=3))
        third=self.make_engagement();self.finish(third)
        self.assertEqual(self.reputation(),{'count':2,'completed':3,'average':Decimal('4.4'),'percent':Decimal('88.0')})
        submit_review(self.brand,third.pk,scores())
        self.assertEqual(self.reputation()['average'],Decimal('4.6'))
        from .reputation import category_averages
        categories=category_averages(self.profile)
        self.assertEqual(categories[0]['average'],Decimal('5.0'))
        self.assertEqual(categories[1]['average'],Decimal('4.0'))
        self.make_engagement() # An invitation is not a completed project.
        self.assertEqual(self.reputation()['completed'],3)

    def test_neutral_empty_state_and_unreviewed_completion(self):
        response=self.client.get(self.profile.get_absolute_url())
        self.assertContains(response,'Not yet rated')
        self.assertNotContains(response,'0.0 / 5')
        self.finish()
        response=self.client.get(self.profile.get_absolute_url())
        self.assertContains(response,'0 Verified Reviews')
        self.assertContains(response,'1 Completed Project')

    def test_duplicate_replay_and_database_uniqueness(self):
        self.finish();review=submit_review(self.brand,self.engagement.pk,scores())
        with self.assertRaises(ValidationError):submit_review(self.brand,self.engagement.pk,scores(quality_rating=1))
        self.assertEqual(self.client.post(self.review_url(),scores()).status_code,409)
        with self.assertRaises(IntegrityError),transaction.atomic():
            CreatorReview.objects.bulk_create([CreatorReview(engagement=self.engagement,**scores())])
        review.refresh_from_db();self.assertEqual(review.quality_rating,5)

    def test_database_checks_and_model_immutability(self):
        self.finish();review=submit_review(self.brand,self.engagement.pk,scores())
        for name,_ in FACTORS:
            for value in [0,6,-1]:
                with self.assertRaises(IntegrityError),transaction.atomic():
                    CreatorReview.objects.filter(pk=review.pk).update(**{name:value})
        review.quality_rating=1
        with self.assertRaises(ValidationError):review.save()
        with self.assertRaises(ValidationError):review.delete()
        for method in ['put','patch','delete']:
            self.assertEqual(getattr(self.client,method)(self.review_url()).status_code,405)

    def test_csrf_protection_and_get_is_read_only(self):
        self.finish();client=Client(enforce_csrf_checks=True);client.force_login(self.brand)
        self.assertEqual(client.post(self.review_url(),scores()).status_code,403)
        self.assertEqual(self.client.get(self.review_url()).status_code,200)
        self.assertFalse(CreatorReview.objects.exists())

    def test_admin_cannot_add_change_delete_even_as_superuser(self):
        self.finish();review=submit_review(self.brand,self.engagement.pk,scores())
        owner=User.objects.create_superuser(username='admin',email='admin@example.invalid',password='test-only-password')
        request=RequestFactory().get('/admin/');request.user=owner
        admin=CreatorReviewAdmin(CreatorReview,AdminSite())
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_change_permission(request,review))
        self.assertFalse(admin.has_delete_permission(request,review))
        self.client.force_login(owner)
        for route,args in [('add',[]),('change',[review.pk]),('delete',[review.pk])]:
            url=reverse('admin:engagements_creatorreview_'+route,args=args)
            self.assertEqual(self.client.post(url,scores()).status_code,403)
        self.assertEqual(self.client.get(reverse('admin:engagements_creatorreview_changelist')).status_code,200)

    def test_public_review_privacy_escaping_and_public_opportunity_labels(self):
        self.finish();submit_review(self.brand,self.engagement.pk,scores(written_feedback='<script>alert(1)</script>'))
        self.client.logout()
        response=self.client.get(self.profile.get_absolute_url())
        self.assertContains(response,'Verified Collaboration')
        self.assertContains(response,'Private CREO project')
        self.assertContains(response,'Verified Brand')
        self.assertContains(response,'&lt;script&gt;')
        for secret in [self.brand.email,self.brand.username,self.brand.first_name,self.creator.email,'Confidential project title','Private invitation','Secret brief contents','<script>alert(1)</script>']:
            self.assertNotContains(response,secret)
        public=self.make_engagement(posting=True);self.finish(public);submit_review(self.brand,public.pk,scores())
        response=self.client.get(self.profile.get_absolute_url())
        self.assertContains(response,'Public Studio');self.assertContains(response,'Public Campaign')

    def test_reviews_newest_first_and_paginated(self):
        for n in range(7):
            e=self.engagement if n==0 else self.make_engagement();self.finish(e)
            submit_review(self.brand,e.pk,scores(written_feedback='Review number '+str(n)))
        response=self.client.get(self.profile.get_absolute_url())
        self.assertEqual(len(response.context['review_page']),6)
        self.assertEqual(response.context['review_page'][0].written_feedback,'Review number 6')
        self.assertContains(response,'Next reviews')
        self.assertContains(self.client.get(self.profile.get_absolute_url()+'?reviews_page=2'),'Review number 0')

    def test_discovery_comparison_and_matching_display_do_not_change_scores(self):
        from discovery.matching import score_creator
        before=score_creator(self.engagement.brief,self.profile)
        self.finish();submit_review(self.brand,self.engagement.pk,scores())
        enriched=public_profiles().get(pk=self.profile.pk)
        after=score_creator(self.engagement.brief,enriched)
        self.assertEqual(before['score'],after['score']);self.assertEqual(before['rows'],after['rows'])
        self.assertContains(self.client.get(reverse('directory')),'1 Verified Review')
        self.assertContains(self.client.get(reverse('dashboard')),'1 Verified Review')
        self.client.post(reverse('comparison_update',args=[self.profile.pk]))
        self.assertContains(self.client.get(reverse('creator_compare')),'1 Verified Review')
        self.assertContains(self.client.get(reverse('brief_matches',args=[self.engagement.brief_id])),'1 Verified Review')

    def test_summary_access_does_not_add_per_creator_queries(self):
        for n in range(4):
            user=User.objects.create_user(username='summary'+str(n),email=f'summary{n}@example.invalid')
            CreatorProfile.objects.create(user=user,display_name=str(n))
        profiles=list(with_reputation(CreatorProfile.objects.all()))
        with self.assertNumQueries(0):
            for profile in profiles:self.assertEqual(profile.reputation['count'],0)
