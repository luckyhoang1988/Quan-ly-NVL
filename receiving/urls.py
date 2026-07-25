"""URL app receiving: GRN (mục 2a)."""
from django.urls import path

from . import views

app_name = 'receiving'

urlpatterns = [
    path('', views.grn_list, name='grn_list'),
    path('new/', views.grn_create, name='grn_create'),
    path('<int:pk>/', views.grn_detail, name='grn_detail'),
    path('<int:pk>/print/', views.grn_print, name='grn_print'),
    path('<int:pk>/edit/', views.grn_update, name='grn_update'),
    path('<int:pk>/submit/', views.grn_submit, name='grn_submit'),
    path('<int:pk>/submit/approve/', views.grn_approve_submission, name='grn_approve_submission'),
    path('<int:pk>/submit/reject/', views.grn_reject_submission, name='grn_reject_submission'),
    path('<int:pk>/cancel/', views.grn_cancel, name='grn_cancel'),
    path('<int:pk>/receive/', views.grn_receive_qty, name='grn_receive_qty'),
    path('<int:pk>/close/', views.grn_close, name='grn_close'),
    path('returns/<int:pk>/approve/', views.grn_return_approve, name='grn_return_approve'),
    path(
        'returns/<int:pk>/request-confirmation/',
        views.grn_return_request_confirmation, name='grn_return_request_confirmation',
    ),
    path(
        'returns/<int:pk>/confirmation/approve/',
        views.grn_return_approve_confirmation, name='grn_return_approve_confirmation',
    ),
    path(
        'returns/<int:pk>/confirmation/reject/',
        views.grn_return_reject_confirmation, name='grn_return_reject_confirmation',
    ),
    path('returns/<int:pk>/close/', views.grn_return_close, name='grn_return_close'),
]
