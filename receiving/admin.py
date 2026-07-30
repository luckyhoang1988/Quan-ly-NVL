from django.contrib import admin

from .models import Grn, GrnItem, GrnReturn


class ServiceManagedAdminMixin:
    """Chỉ cho xem qua Admin, không cho thêm/sửa/xoá (BUG-19, 2026-07-30, xem
    CLAUDE.md "Any model that a service layer says don't create directly").

    Workflow GRN/QC/Batch/Inventory là MỘT transaction (xem CLAUDE.md "GRN ->
    QC -> Batch -> Inventory") vận hành qua ``receiving.services``/
    ``quality.services`` — sửa trực tiếp qua Admin (vd đổi ``status`` ->
    RECEIVED, hay qty item) bỏ qua transaction đó, phá đồng bộ Batch/Inventory
    và không ghi AuditLog (mirror ``inventory.admin.ServiceManagedAdminMixin``).
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class GrnItemInline(admin.TabularInline):
    model = GrnItem
    extra = 1


@admin.register(Grn)
class GrnAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('grn_no', 'po', 'supplier', 'grn_date', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'supplier')
    search_fields = ('grn_no', 'po__po_no')
    inlines = [GrnItemInline]


@admin.register(GrnReturn)
class GrnReturnAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('__str__', 'grn', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('grn__grn_no',)
