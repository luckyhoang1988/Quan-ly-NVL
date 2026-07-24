from django.urls import path

from . import views

app_name = 'purchasing'

urlpatterns = [
    path('', views.po_list, name='po_list'),
    path('new/', views.po_create, name='po_create'),
    path('price-comparison/', views.po_price_comparison, name='po_price_comparison'),
    path('supplier-performance/', views.po_supplier_performance, name='po_supplier_performance'),
    path('<int:pk>/', views.po_detail, name='po_detail'),
    path('<int:pk>/edit/', views.po_update, name='po_update'),
    path('<int:pk>/approve/', views.po_approve, name='po_approve'),
    path('<int:pk>/send/', views.po_send, name='po_send'),
    path('<int:pk>/close/', views.po_close, name='po_close'),
]
