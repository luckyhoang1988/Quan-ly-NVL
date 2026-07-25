"""Seed ``user.user_permissions`` cho user đã tồn tại theo mặc định role.

Từ nay quyền hiệu lực (``user.can(...)``) chỉ tính từ ``user_permissions`` trực
tiếp, không cộng dồn từ Group nữa (xem ``accounts/backends.py``). User tạo
MỚI/đổi role sau thời điểm này tự seed qua ``rbac.sync_user_permissions`` (post_save
signal) — migration này chỉ để backfill 1 lần cho user đã có sẵn trong DB trước
khi cơ chế trên tồn tại, tránh họ đột ngột mất hết quyền sau khi deploy.
"""
from django.db import migrations

from accounts.permissions import codenames_for_role


def seed_user_permissions(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    Permission = apps.get_model('auth', 'Permission')
    for user in User.objects.all():
        perms = Permission.objects.filter(
            content_type__app_label='accounts',
            codename__in=codenames_for_role(user.role),
        )
        user.user_permissions.set(perms)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_alter_user_options'),
    ]

    operations = [
        migrations.RunPython(seed_user_permissions, noop_reverse),
    ]
