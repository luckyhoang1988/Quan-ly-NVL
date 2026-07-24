from django.urls import path

from . import views

app_name = 'purchasing'

urlpatterns = [
    path('', views.po_list, name='po_list'),
    path('new/', views.po_create, name='po_create'),
    path('<int:pk>/edit/', views.po_update, name='po_update'),
]
