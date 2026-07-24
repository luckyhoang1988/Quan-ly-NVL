"""Management command: tạo/đồng bộ 6 Group RBAC theo ma trận quyền.

Dùng khi cần chạy tay (vd sau khi sửa accounts/permissions.py):
    python manage.py sync_roles

Signal post_migrate cũng gọi cùng logic này sau mỗi lần migrate, nên thường
không cần chạy tay — command chỉ để tiện thao tác/kiểm tra.
"""
from django.core.management.base import BaseCommand

from accounts.rbac import sync_roles


class Command(BaseCommand):
    help = 'Tạo/đồng bộ 6 Group RBAC theo permission matrix (accounts/permissions.py).'

    def handle(self, *args, **options):
        sync_roles()
        self.stdout.write(self.style.SUCCESS('Đã đồng bộ 6 Group RBAC theo ma trận quyền.'))
