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

    # CRUD user (FR-USER-01) — Admin-only.
    path('users/', views.user_list, name='user_list'),
    path('users/new/', views.user_create, name='user_create'),
    path('users/<int:pk>/', views.user_detail, name='user_detail'),
    path('users/<int:pk>/edit/', views.user_update, name='user_update'),
    path('users/<int:pk>/delete/', views.user_delete, name='user_delete'),

    path('', views.dashboard, name='dashboard'),
]
