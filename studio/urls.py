from django.urls import path
from . import views

app_name = 'studio'
urlpatterns = [
    path('', views.index, name='index'),
    path('new/', views.create, name='create'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/edit/', views.edit, name='edit'),
    path('<int:pk>/cards/new/', views.element_edit, name='element_create'),
    path('<int:pk>/cards/<int:element_pk>/edit/', views.element_edit, name='element_edit'),
    path('<int:pk>/action/', views.action, name='action'),
    path('<int:pk>/brief/', views.convert, name='convert'),
    path('<int:pk>/approved/', views.approved, name='approved'),
    path('<int:pk>/references/<int:element_pk>/', views.image, name='image'),
]
