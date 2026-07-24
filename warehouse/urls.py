"""URL app warehouse: CRUD kho (FR-WM-01) + vị trí lưu trữ (FR-WM-02)."""
from django.urls import path

from . import views

app_name = 'warehouse'

urlpatterns = [
    path('', views.warehouse_list, name='warehouse_list'),
    path('new/', views.warehouse_create, name='warehouse_create'),
    path('<int:pk>/', views.warehouse_detail, name='warehouse_detail'),
    path('<int:pk>/edit/', views.warehouse_update, name='warehouse_update'),
    path('<int:pk>/deactivate/', views.warehouse_deactivate, name='warehouse_deactivate'),
    path('<int:pk>/activate/', views.warehouse_activate, name='warehouse_activate'),

    path('<int:pk>/locations/new/', views.location_create, name='location_create'),
    path('<int:pk>/locations/<int:loc_pk>/edit/', views.location_update, name='location_update'),
    path('<int:pk>/locations/<int:loc_pk>/toggle/', views.location_toggle_active, name='location_toggle_active'),
]
