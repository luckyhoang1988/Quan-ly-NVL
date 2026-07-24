"""URL app quality: nhập kết quả QC (mục 2c)."""
from django.urls import path

from . import views

app_name = 'quality'

urlpatterns = [
    path('grn/<int:grn_pk>/result/', views.qc_result, name='qc_result'),
]
