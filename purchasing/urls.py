from django.urls import path

from . import views

app_name = 'purchasing'

urlpatterns = [
    path('', views.po_list, name='po_list'),
    path('new/', views.po_create, name='po_create'),
    path('price-comparison/', views.po_price_comparison, name='po_price_comparison'),
    path('supplier-performance/', views.po_supplier_performance, name='po_supplier_performance'),
    path('requests/', views.pr_list, name='pr_list'),
    path('requests/new/', views.pr_create, name='pr_create'),
    path('requests/<int:pk>/', views.pr_detail, name='pr_detail'),
    path('requests/<int:pk>/edit/', views.pr_update, name='pr_update'),
    path('requests/<int:pk>/submit/', views.pr_submit, name='pr_submit'),
    path('requests/<int:pk>/reopen/', views.pr_reopen, name='pr_reopen'),
    path('requests/<int:pk>/delete/', views.pr_delete, name='pr_delete'),
    path('requests/<int:pk>/approve/', views.pr_approve, name='pr_approve'),
    path('requests/<int:pk>/reject/', views.pr_reject, name='pr_reject'),
    path('requests/<int:pk>/forward/', views.pr_forward, name='pr_forward'),
    path('pr-item/<int:pk>/cancel-open-qty/', views.pr_item_cancel_open_qty, name='pr_item_cancel_open_qty'),
    path('pr-item/<int:pk>/map-product/', views.pr_item_map_product, name='pr_item_map_product'),
    path('<int:pk>/', views.po_detail, name='po_detail'),
    path('<int:pk>/edit/', views.po_update, name='po_update'),
    path('<int:pk>/approve/', views.po_approve, name='po_approve'),
    path('<int:pk>/send/', views.po_send, name='po_send'),
    path('<int:pk>/retry-email/', views.po_retry_email, name='po_retry_email'),
    path('<int:pk>/close/', views.po_close, name='po_close'),
]
