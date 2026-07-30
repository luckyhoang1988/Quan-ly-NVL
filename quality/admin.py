from django.contrib import admin

from .models import QcCriteria, QcInspection, QcInspectionItem


class ServiceManagedAdminMixin:
    """Chỉ cho xem qua Admin, không cho thêm/sửa/xoá (BUG-19, 2026-07-30, xem
    CLAUDE.md "Any model that a service layer says don't create directly").

    ``QcInspection`` chỉ được tạo/chuyển trạng thái qua ``quality.services``
    (``start_qc``/``qc_pass``/``qc_fail``/``qc_partial_pass``/
    ``cancel_qc_inspection``) — sửa trực tiếp qua Admin (vd đổi ``status`` ->
    PASS) bỏ qua transaction Batch/Inventory tương ứng và không ghi AuditLog
    (mirror ``inventory.admin.ServiceManagedAdminMixin``). KHÔNG áp dụng cho
    ``QcCriteria`` — đó là master data thuần (tiêu chuẩn QC tham khảo), không
    có bất biến Batch/Inventory nào cần bảo vệ.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class QcInspectionItemInline(admin.TabularInline):
    model = QcInspectionItem
    extra = 1


@admin.register(QcInspection)
class QcInspectionAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('qc_no', 'grn', 'inspector', 'status', 'started_at', 'completed_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('qc_no', 'grn__grn_no')
    inlines = [QcInspectionItemInline]


@admin.register(QcCriteria)
class QcCriteriaAdmin(admin.ModelAdmin):
    list_display = ('category', 'name', 'pass_rule', 'fail_rule', 'is_active')
    list_filter = ('category', 'is_active')
    search_fields = ('category', 'name')
