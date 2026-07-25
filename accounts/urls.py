"""URL app accounts: đăng nhập/đăng xuất (FR-USER-03), CRUD user (FR-USER-01), trang chủ.

Không đặt app_name namespace để tên 'login'/'logout' khớp mặc định Django
(LOGIN_URL, @login_required, LogoutView...). Các route user dùng tiền tố tên
'user_*' để tránh trùng tên toàn cục.
"""
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(authentication_form=LoginForm),
         name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('password/change/', views.password_change, name='password_change'),

    # CRUD user (FR-USER-01) — Admin-only.
    path('users/', views.user_list, name='user_list'),
    path('users/new/', views.user_create, name='user_create'),
    path('users/<int:pk>/', views.user_detail, name='user_detail'),
    path('users/<int:pk>/edit/', views.user_update, name='user_update'),
    path('users/<int:pk>/permissions/', views.user_permission_edit, name='user_permission_edit'),
    path('users/<int:pk>/toggle-active/', views.user_toggle_active, name='user_toggle_active'),
    path('users/<int:pk>/password/', views.user_password_set, name='user_password_set'),
    path('users/<int:pk>/delete/', views.user_delete, name='user_delete'),

    # Thông báo trong app.
    path('notifications/', views.notification_list, name='notification_list'),
    path('notifications/<int:pk>/read/', views.notification_mark_read, name='notification_mark_read'),
    path('notifications/mark-all-read/', views.notification_mark_all_read, name='notification_mark_all_read'),

    # Tra cứu Audit Log (quản lý phòng ban trở lên + Admin).
    path('audit-log/', views.audit_log_list, name='audit_log_list'),

    path('', views.dashboard, name='dashboard'),
]
