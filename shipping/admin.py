from django.contrib import admin

from .models import Gin, GinBatchAllocation, GinItem


class ServiceManagedAdminMixin:
    """Chỉ cho xem qua Admin, không cho thêm/sửa/xoá (BUG-19, 2026-07-30, xem
    CLAUDE.md "Any model that a service layer says don't create directly").

    Workflow GIN (DRAFT -> PICKING -> ISSUED -> CLOSED) chỉ được vận hành qua
    ``shipping.services`` (FIFO suggest, override batch, trừ Inventory/Batch) —
    sửa trực tiếp qua Admin (vd đổi ``status`` -> ISSUED, hay ``qty_issued``)
    bỏ qua toàn bộ transaction đó, phá đồng bộ Batch/Inventory và không ghi
    AuditLog (mirror ``inventory.admin.ServiceManagedAdminMixin``).
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class GinBatchAllocationInline(admin.TabularInline):
    model = GinBatchAllocation
    extra = 0


class GinItemInline(admin.TabularInline):
    model = GinItem
    extra = 1


@admin.register(Gin)
class GinAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = (
        'gin_no', 'warehouse', 'reference_type', 'reference_no', 'status', 'requested_by', 'created_at',
    )
    list_filter = ('status', 'reference_type', 'warehouse')
    search_fields = ('gin_no', 'reference_no')
    inlines = [GinItemInline]


@admin.register(GinItem)
class GinItemAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('gin', 'product', 'qty_requested', 'qty_issued')
    list_filter = ('gin__status',)
    search_fields = ('gin__gin_no', 'product__product_code')
    inlines = [GinBatchAllocationInline]
