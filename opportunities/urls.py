from django.urls import path
from . import views

app_name = 'opportunities'
urlpatterns = [
    path('mine/', views.index, name='index'),
    path('new/', views.edit, name='create'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/manage/', views.manage, name='manage'),
    path('<int:pk>/edit/', views.edit, name='edit'),
    path('<int:pk>/status/', views.transition, name='transition'),
    path('<int:pk>/applicants/', views.applicants, name='applicants'),
]
