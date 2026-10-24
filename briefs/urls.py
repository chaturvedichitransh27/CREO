from django.urls import path
from . import views

urlpatterns = [path('new/', views.edit, name='brief_create'), path('builder/', views.builder, name='brief_builder'),
               path('<int:pk>/', views.detail, name='brief_detail'), path('<int:pk>/edit/', views.edit, name='brief_edit'),
               path('<int:pk>/delete/', views.delete, name='brief_delete'), path('<int:pk>/matches/', views.matches, name='brief_matches')]

from .reference_views import reference_image
urlpatterns += [path("references/<int:pk>/image/", reference_image, name="brief_reference_image")]
