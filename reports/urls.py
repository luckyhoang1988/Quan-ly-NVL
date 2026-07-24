from django.urls import path

from . import views

app_name = 'reports'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('abc-analysis/', views.abc_analysis_view, name='abc_analysis'),
    path('slow-moving/', views.slow_moving_view, name='slow_moving'),
    path('supplier-performance/', views.supplier_performance_view, name='supplier_performance'),
]
