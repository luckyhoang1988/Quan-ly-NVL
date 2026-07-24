"""URL app stocktake: Stock Opname (mục 4)."""
from django.urls import path

from . import views

app_name = 'stocktake'

urlpatterns = [
    path('', views.so_list, name='so_list'),
    path('new/', views.so_create, name='so_create'),
    path('<int:pk>/', views.so_detail, name='so_detail'),
    path('<int:pk>/start-execution/', views.so_start_execution, name='so_start_execution'),
    path('<int:pk>/execution/', views.so_execution, name='so_execution'),
    path('<int:pk>/submit-reconciliation/', views.so_submit_reconciliation, name='so_submit_reconciliation'),
    path('<int:pk>/apply-adjustment/', views.so_apply_adjustment, name='so_apply_adjustment'),
]
