"""URL app quality: nhập kết quả QC (mục 2c)."""
from django.urls import path

from . import views

app_name = 'quality'

urlpatterns = [
    path('grn/<int:grn_pk>/result/', views.qc_result, name='qc_result'),

    path('criteria/', views.qc_criteria_list, name='qc_criteria_list'),
    path('criteria/new/', views.qc_criteria_create, name='qc_criteria_create'),
    path('criteria/<int:pk>/edit/', views.qc_criteria_update, name='qc_criteria_update'),
    path('criteria/<int:pk>/toggle/', views.qc_criteria_toggle_active, name='qc_criteria_toggle_active'),
]
