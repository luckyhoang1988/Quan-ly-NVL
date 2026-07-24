from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """Admin cho custom User: giữ nguyên UserAdmin mặc định và thêm field NVL/WMS."""

    list_display = ('username', 'email', 'role', 'is_active', 'is_deleted', 'is_staff')
    list_filter = UserAdmin.list_filter + ('role', 'is_deleted')
    readonly_fields = ('deleted_at',)
    fieldsets = UserAdmin.fieldsets + (
        ('NVL/WMS', {'fields': ('role', 'is_deleted', 'deleted_at')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('NVL/WMS', {'fields': ('role',)}),
    )
