"""URL app shipping: GIN (mục 3b)."""
from django.urls import path

from . import views

app_name = 'shipping'

urlpatterns = [
    path('', views.gin_list, name='gin_list'),
    path('new/', views.gin_create, name='gin_create'),
    path('<int:pk>/', views.gin_detail, name='gin_detail'),
    path('<int:pk>/print/', views.gin_print, name='gin_print'),
    path('<int:pk>/start-picking/', views.gin_start_picking, name='gin_start_picking'),
    path('<int:pk>/confirm-request/', views.gin_confirm_request, name='gin_confirm_request'),
    path('<int:pk>/confirm-request/approve/', views.gin_approve_confirmation, name='gin_approve_confirmation'),
    path('<int:pk>/confirm-request/reject/', views.gin_reject_confirmation, name='gin_reject_confirmation'),
    path('<int:pk>/cancel/', views.gin_cancel, name='gin_cancel'),
    path('<int:pk>/picking/', views.gin_picking, name='gin_picking'),
    path('allocations/<int:pk>/override/', views.gin_allocation_override, name='gin_allocation_override'),
    path('<int:pk>/issue/', views.gin_issue, name='gin_issue'),
    path('<int:pk>/close/', views.gin_close, name='gin_close'),
]
