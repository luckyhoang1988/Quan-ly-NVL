"""Re-seed ``user_permissions`` cho user role PURCHASING sau khi bỏ 'approve'
khỏi module 'pr' trong ``ROLE_PERMISSIONS`` (duyệt PR chuyển sang quản lý phòng
Mua hàng qua ``Approval`` — xem ``purchasing.services.submit_purchase_request``/
``decide_purchase_request``). Mirror pattern backfill của
``0007_seed_user_permissions_from_role.py``: chỉ cần re-seed đúng role bị đổi
quyền, để không đụng tới phân quyền chi tiết admin đã tuỳ chỉnh riêng cho role
khác.
"""
from django.db import migrations

from accounts.permissions import codenames_for_role


def reseed_purchasing_permissions(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    Permission = apps.get_model('auth', 'Permission')
    perms = Permission.objects.filter(
        content_type__app_label='accounts',
        codename__in=codenames_for_role('PURCHASING'),
    )
    for user in User.objects.filter(role='PURCHASING'):
        user.user_permissions.set(perms)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_approval'),
    ]

    operations = [
        migrations.RunPython(reseed_purchasing_permissions, noop_reverse),
    ]
