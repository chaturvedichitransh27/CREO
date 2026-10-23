from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include, re_path
from creators import views
from accounts.views import register, dashboard

urlpatterns = [
    path('', views.home, name='home'),
    path('admin/', admin.site.urls),
    path('accounts/register/', register, name='register'),
    path('accounts/login/', auth_views.LoginView.as_view(template_name='accounts/login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('dashboard/', dashboard, name='dashboard'),
    path('creators/', include('creators.urls')),
    path('portfolio/', include('portfolio.urls')),
    path('briefs/', include('briefs.urls')),
    path('evidence/', include('trust.urls')),
    path('engagements/', include('engagements.urls')),
    path('studio/', include('studio.urls')),
    path('opportunities/', include('opportunities.urls')),
    path('api/taxonomy/', views.taxonomy, name='taxonomy'),
]
from config.views import public_media, health
urlpatterns += [path('health/', health, name='health'), re_path(r'^media/(?P<name>portfolios/.+)$', public_media, name='public_media')]
