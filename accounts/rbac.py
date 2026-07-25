"""RBAC runtime: đồng bộ Group ⇄ ma trận quyền, và user.role ⇄ Group membership.

Tách khỏi ``models.py`` để tránh import vòng và để signal / management command /
test dùng lại chung một logic (idempotent).
"""
from django.contrib.auth.models import Group, Permission

from .permissions import ROLE_PERMISSIONS, codenames_for_role


def sync_roles():
    """Tạo/đồng bộ 6 Group theo ROLE_PERMISSIONS.

    Idempotent: dùng ``set()`` (clear + add) nên chạy lại nhiều lần vẫn ra đúng
    trạng thái, kể cả khi ma trận đã bị chỉnh (quyền thừa sẽ bị gỡ).
    """
    for role in ROLE_PERMISSIONS:
        group, _ = Group.objects.get_or_create(name=role)
        perms = Permission.objects.filter(
            content_type__app_label='accounts',
            codename__in=codenames_for_role(role),
        )
        group.permissions.set(perms)


def sync_user_group(user):
    """Đồng bộ membership: user chỉ thuộc đúng Group ứng với ``role`` hiện tại.

    Gỡ user khỏi mọi role-group khác rồi thêm vào group đúng role. Các group KHÔNG
    phải role-group (nếu sau này có) được giữ nguyên, không đụng tới.
    """
    role_groups = Group.objects.filter(name__in=ROLE_PERMISSIONS.keys())
    user.groups.remove(*role_groups)
    if user.role:
        target = role_groups.filter(name=user.role).first()
        if target:
            user.groups.add(target)


def sync_user_permissions(user):
    """Đặt lại ``user.user_permissions`` đúng theo mặc định của ``role`` hiện tại
    (ghi đè/xoá mọi phân quyền chi tiết đã tuỳ chỉnh riêng trước đó cho user này).

    Chỉ nên gọi khi TẠO user mới hoặc khi ROLE vừa đổi (xem ``on_user_post_save``)
    — không gọi ở mọi lần lưu User, để sửa các field khác (email, họ tên...)
    không vô tình xoá mất phân quyền chi tiết admin đã set qua trang "Phân quyền".
    """
    if not user.pk:
        return
    perms = Permission.objects.filter(
        content_type__app_label='accounts',
        codename__in=codenames_for_role(user.role),
    )
    user.user_permissions.set(perms)


# --- Signal handlers (kết nối trong AccountsConfig.ready) ---

def on_post_migrate(sender, **kwargs):
    """Sau mỗi lần migrate: đảm bảo 6 Group RBAC tồn tại & đúng quyền."""
    sync_roles()


def on_user_pre_save(sender, instance, **kwargs):
    """Nhớ lại role CŨ (trước khi ghi đè) để post_save biết role có thật sự đổi
    không — xem lý do ở ``sync_user_permissions``."""
    if instance.pk:
        instance._previous_role = (
            sender.objects.filter(pk=instance.pk).values_list('role', flat=True).first()
        )
    else:
        instance._previous_role = None


def on_user_post_save(sender, instance, created, **kwargs):
    """Sau mỗi lần lưu User: đồng bộ Group membership theo role (luôn luôn);
    reset phân quyền trực tiếp theo role CHỈ khi user mới tạo hoặc role vừa đổi.
    """
    sync_user_group(instance)
    previous_role = getattr(instance, '_previous_role', None)
    if created or previous_role != instance.role:
        sync_user_permissions(instance)
