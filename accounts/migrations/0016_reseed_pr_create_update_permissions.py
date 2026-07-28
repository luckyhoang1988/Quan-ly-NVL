"""Re-seed ``user_permissions`` cho role PURCHASING (thêm 'create') và STAFF (thêm
'update') trên module 'pr' — bug-fix 2026-07-28 "PURCHASING không create PR mặc định"
+ "PR không DRAFT / không sửa được": PURCHASING giờ tự tạo được PR (vd từ gợi ý Min
Level), STAFF giờ tự sửa được PR còn ở state DRAFT trước khi Nộp (xem
``purchasing.views.pr_update``). Mirror đúng pattern
``0012_reseed_purchasing_pr_permissions.py``: chỉ re-seed đúng 2 role bị đổi quyền,
không đụng tới phân quyền chi tiết admin đã tuỳ chỉnh riêng cho role khác.
"""
from django.db import migrations

from accounts.permissions import codenames_for_role


def reseed_permissions(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    Permission = apps.get_model('auth', 'Permission')
    for role in ('PURCHASING', 'STAFF'):
        perms = Permission.objects.filter(
            content_type__app_label='accounts',
            codename__in=codenames_for_role(role),
        )
        for user in User.objects.filter(role=role):
            user.user_permissions.set(perms)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_alter_auditlog_action'),
    ]

    operations = [
        migrations.RunPython(reseed_permissions, noop_reverse),
    ]
