"""URL app inventory (mục 3a): dashboard tồn kho real-time, danh sách/chi tiết
lô hàng, điều chuyển tồn kho (FR-WM-06), phiếu chờ nhận hàng (Phase D).
"""
from django.urls import path

from . import views

app_name = 'inventory'

urlpatterns = [
    path('', views.inventory_list, name='inventory_list'),
    path('batches/', views.batch_list, name='batch_list'),
    path('batches/<int:pk>/', views.batch_detail, name='batch_detail'),
    path('batches/<int:pk>/dispose/', views.batch_dispose, name='batch_dispose'),
    path('transfers/', views.transfer_list, name='transfer_list'),
    path('transfers/new/', views.transfer_create, name='transfer_create'),
    path('products/<int:pk>/eoq/', views.product_eoq, name='product_eoq'),
    path('handoffs/', views.handoff_list, name='handoff_list'),
    path('handoffs/<int:pk>/accept/', views.handoff_accept, name='handoff_accept'),
    path('handoffs/<int:pk>/reject/', views.handoff_reject, name='handoff_reject'),
]
