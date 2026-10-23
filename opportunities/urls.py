from django.urls import path
from . import views

app_name = 'opportunities'
urlpatterns = [
    path('', views.explore, name='explore'),
    path('brands/<int:brand_id>/', views.brand_overview, name='brand'),
    path('applications/', views.my_applications, name='my_applications'),
    path('applications/<int:pk>/', views.application_detail, name='application'),
    path('applications/<int:pk>/considering/', views.considering, name='considering'),
    path('applications/<int:pk>/contact/', views.contact_consent, name='contact_consent'),
    path('applications/<int:pk>/brand-contact/', views.brand_contact, name='brand_contact'),
    path('applications/<int:pk>/request-contact/', views.contact_request, name='contact_request'),
    path('applications/<int:pk>/collaborate/', views.collaborate, name='collaborate'),
    path('notifications/<int:pk>/read/', views.notification_read, name='notification_read'),
    path('mine/', views.index, name='index'),
    path('new/', views.edit, name='create'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/apply/', views.apply, name='apply'),
    path('<int:pk>/recommendations/', views.recommendations, name='recommendations'),
    path('<int:pk>/manage/', views.manage, name='manage'),
    path('<int:pk>/edit/', views.edit, name='edit'),
    path('<int:pk>/status/', views.transition, name='transition'),
    path('<int:pk>/applicants/', views.applicants, name='applicants'),
]
