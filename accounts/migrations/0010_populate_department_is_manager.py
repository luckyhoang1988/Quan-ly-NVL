"""Data migration: suy department/is_manager từ role hiện có (Phase A nền tảng
luồng duyệt theo phòng ban) — MANAGER->WAREHOUSE+is_manager=True, STAFF->
WAREHOUSE, QC->QC, PURCHASING->PURCHASING, ACCOUNTANT->ACCOUNTING, ADMIN->để
trống (không thuộc phòng ban cụ thể, đã có is_superuser-like quyền qua role).
"""
from django.db import migrations

ROLE_TO_DEPARTMENT = {
    'MANAGER': 'WAREHOUSE',
    'STAFF': 'WAREHOUSE',
    'QC': 'QC',
    'PURCHASING': 'PURCHASING',
    'ACCOUNTANT': 'ACCOUNTING',
}


def populate(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    for role, department in ROLE_TO_DEPARTMENT.items():
        User.objects.filter(role=role).update(department=department)
    User.objects.filter(role='MANAGER').update(is_manager=True)


def revert(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.all().update(department='', is_manager=False)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0009_user_department_user_is_manager_notification'),
    ]

    operations = [
        migrations.RunPython(populate, revert),
    ]
