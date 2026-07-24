"""URL app receiving: GRN (mục 2a)."""
from django.urls import path

from . import views

app_name = 'receiving'

urlpatterns = [
    path('', views.grn_list, name='grn_list'),
    path('new/', views.grn_create, name='grn_create'),
    path('<int:pk>/', views.grn_detail, name='grn_detail'),
    path('<int:pk>/edit/', views.grn_update, name='grn_update'),
    path('<int:pk>/submit/', views.grn_submit, name='grn_submit'),
    path('<int:pk>/receive/', views.grn_receive_qty, name='grn_receive_qty'),
]
