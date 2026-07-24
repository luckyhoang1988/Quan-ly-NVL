from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        # Kết nối signal RBAC. Import trong ready() để chắc chắn app registry đã sẵn sàng.
        from django.db.models.signals import post_migrate, post_save

        from . import rbac
        from .models import User

        # Sau migrate: tạo/đồng bộ 6 Group theo ma trận quyền.
        post_migrate.connect(rbac.on_post_migrate, sender=self)
        # Sau khi lưu User: đồng bộ membership group theo role.
        post_save.connect(rbac.on_user_post_save, sender=User)

        # Import để đăng ký receiver audit đăng nhập/đăng xuất (@receiver tự nối).
        from . import signals  # noqa: F401
