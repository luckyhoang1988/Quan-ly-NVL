from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        # Kết nối signal RBAC. Import trong ready() để chắc chắn app registry đã sẵn sàng.
        from django.db.models.signals import post_migrate, post_save, pre_save

        from . import rbac
        from .models import User

        # Sau migrate: tạo/đồng bộ 6 Group theo ma trận quyền.
        post_migrate.connect(rbac.on_post_migrate, sender=self)
        # Trước khi lưu User: nhớ role cũ (để post_save biết role có đổi không).
        pre_save.connect(rbac.on_user_pre_save, sender=User)
        # Sau khi lưu User: đồng bộ membership group theo role + reset quyền
        # trực tiếp nếu là user mới hoặc role vừa đổi.
        post_save.connect(rbac.on_user_post_save, sender=User)

        # Import để đăng ký receiver audit đăng nhập/đăng xuất (@receiver tự nối).
        from . import signals  # noqa: F401
