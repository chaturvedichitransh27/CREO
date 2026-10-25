from django.test import override_settings
from django.urls import reverse
from opportunities.models import JobPosting
from opportunities.tests import OpportunityTestCase


class CreatorBrandNavigationTests(OpportunityTestCase):
    def test_creator_header_home_and_search_lead_to_brands(self):
        self.open_posting()
        self.client.force_login(self.creator_user)
        response = self.client.get(reverse('home'))
        directory = reverse('opportunities:brands')
        self.assertContains(response, f'<a href="{directory}">Discover Brands</a>', count=2, html=True)
        self.assertContains(response, f'<a class="button" href="{directory}">Discover Brands <span aria-hidden="true">→</span></a>', html=True)
        self.assertContains(response, f'<form class="nav-search" method="get" action="{directory}" role="search">')
        self.assertContains(response, 'Search brands or projects')
        self.assertContains(response, '<a class="button button-outline" href="/dashboard/">My Workspace</a>', html=True)
        self.assertContains(response, '<a href="/creators/">Explore fellow creators</a>', html=True)
        self.assertNotContains(response, 'href="/accounts/register/')
        self.assertNotContains(response, 'Find the Right Creator')
        self.assertContains(response, self.posting.brand_name)
        self.assertContains(response, reverse('opportunities:brand', args=[self.brand.pk]))
        self.assertEqual(list(response.context['featured_brands']), [self.posting])

    def test_brand_and_anonymous_discovery_content_is_preserved(self):
        for user in [None, self.brand]:
            if user:
                self.client.force_login(user)
            response = self.client.get(reverse('home'), {'role': 'CREATOR'})
            self.assertContains(response, '<a href="/creators/">Discover creators</a>', count=2, html=True)
            self.assertContains(response, '<form class="nav-search" method="get" action="/creators/" role="search">')
            self.assertContains(response, 'Search creators')
            self.assertContains(response, 'Explore Creators')
            self.assertContains(response, 'I’m a Creator')
            self.assertContains(response, 'Find the Right Creator')
            self.assertContains(response, 'Explore all creators')
            self.assertContains(response, 'Post a Project')
            self.assertContains(response, 'Find your creative edge')
            self.assertNotContains(response, 'Discover Brands')
            self.assertContains(response, self.creator.display_name)
        response = self.client.get(reverse('dashboard'))
        for route in ['opportunities:create', 'opportunities:index', 'brief_create', 'studio:index', 'brief_builder', 'creator_compare']:
            self.assertContains(response, reverse(route))
        self.assertNotContains(response, 'Discover Brands')

    def test_role_query_cannot_change_creator_navigation(self):
        self.client.force_login(self.creator_user)
        response = self.client.get(reverse('home'), {'role': 'BRAND'})
        self.assertContains(response, 'Discover Brands')
        self.assertNotContains(response, 'Post a Project')

    def test_creator_workspace_and_contextual_creator_workflows_remain_available(self):
        self.client.force_login(self.creator_user)
        response = self.client.get(reverse('dashboard'))
        for route in ['opportunities:brands', 'opportunities:explore', 'opportunities:my_applications', 'profile_edit', 'portfolio_create', 'evidence_list', 'engagement_inbox']:
            self.assertContains(response, reverse(route))
        for route in ['directory', 'creator_compare', 'profile_edit', 'portfolio_create', 'opportunities:my_applications', 'engagement_inbox']:
            self.assertEqual(self.client.get(reverse(route)).status_code, 200, route)
        self.assertContains(self.client.get(self.creator.get_absolute_url()), '← Back to the collective')
        self.assertContains(self.client.get(reverse('creator_compare')), '← Discover creators')
        self.assertEqual(self.client.get(reverse('directory'), HTTP_HX_REQUEST='true').status_code, 200)

    def test_creator_featured_brands_are_public_and_bounded_with_empty_state(self):
        self.client.force_login(self.creator_user)
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'New creative opportunities are on their way')
        self.assertNotContains(response, self.posting.title)
        self.open_posting()
        self.make_posting(title='Newer published project', status='OPEN', published_at=self.posting.published_at)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.context['brand_count'], 1)
        self.assertEqual(len(response.context['featured_brands']), 1)
        self.assertEqual(response.context['featured_brands'][0].open_project_count, 2)

    @override_settings(DEBUG=False)
    def test_not_found_discovery_link_is_role_specific(self):
        self.client.force_login(self.creator_user)
        response = self.client.get('/missing-brand-discovery-test/')
        self.assertContains(response, '<a class="button" href="/opportunities/brands/">Discover Brands</a>', status_code=404, html=True)
        self.client.force_login(self.brand)
        response = self.client.get('/missing-brand-discovery-test/')
        self.assertContains(response, '<a class="button" href="/creators/">Explore creators</a>', status_code=404, html=True)
