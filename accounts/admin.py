from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils import timezone

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """Admin cho custom User: giữ nguyên UserAdmin mặc định và thêm field NVL/WMS."""

    list_display = (
        'username', 'email', 'role', 'department', 'is_manager',
        'is_active', 'is_deleted', 'is_staff',
    )
    list_filter = UserAdmin.list_filter + ('role', 'department', 'is_manager', 'is_deleted')
    readonly_fields = ('deleted_at',)
    fieldsets = UserAdmin.fieldsets + (
        ('NVL/WMS', {'fields': ('role', 'department', 'is_manager', 'is_deleted', 'deleted_at')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('NVL/WMS', {'fields': ('role', 'department', 'is_manager')}),
    )

    def save_model(self, request, obj, form, change):
        """M4: đánh dấu ``is_deleted`` trực tiếp qua form admin không đi qua
        ``User.soft_delete()`` nên ``deleted_at`` (readonly, không nằm trong dữ
        liệu POST) không bao giờ được set — user "đã xoá" trên admin nhưng
        ``deleted_at`` mãi là None. Lưu form bình thường trước (giữ mọi field
        khác admin sửa cùng lúc), rồi backfill ``deleted_at`` nếu vừa thành
        ``is_deleted=True`` mà chưa có mốc thời gian xoá."""
        super().save_model(request, obj, form, change)
        if obj.is_deleted and obj.deleted_at is None:
            obj.deleted_at = timezone.now()
            obj.save(update_fields=['deleted_at'])
