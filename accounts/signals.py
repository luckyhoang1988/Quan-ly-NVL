"""Signal audit cho đăng nhập/đăng xuất (FR-USER-03 + FR-USER-05).

Nối vào 3 signal auth của Django để MỌI đường đăng nhập (form login, admin,
API sau này) đều tự động được ghi audit — không cần rải log_action ở từng view:

- ``user_logged_in``    -> LOGIN
- ``user_logged_out``   -> LOGOUT
- ``user_login_failed`` -> LOGIN_FAILED (actor=None, giữ lại username đã thử)

Kết nối trong ``AccountsConfig.ready()`` bằng cách import module này.
"""
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver

from .audit import client_ip, log_action
from .models import AuditLog


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    log_action(user, AuditLog.Action.LOGIN, ip_address=client_ip(request))


@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    # user có thể None nếu session không gắn user — log_action coi như hệ thống.
    log_action(user, AuditLog.Action.LOGOUT, ip_address=client_ip(request))


@receiver(user_login_failed)
def on_user_login_failed(sender, credentials, request=None, **kwargs):
    # Django đã mask 'password' thành '********' trước khi phát signal -> an toàn.
    username = (credentials or {}).get('username', '')
    log_action(
        None,
        AuditLog.Action.LOGIN_FAILED,
        description=f'Đăng nhập thất bại: {username}'.strip(),
        ip_address=client_ip(request),
    )
