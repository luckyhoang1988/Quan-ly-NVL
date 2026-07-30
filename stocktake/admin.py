from django.contrib import admin

from .models import StocktakeItem, StocktakeSession


class ServiceManagedAdminMixin:
    """Chỉ cho xem qua Admin, không cho thêm/sửa/xoá (BUG-19, 2026-07-30, xem
    CLAUDE.md "Any model that a service layer says don't create directly").

    Stocktake (kiểm kê) chỉ được vận hành qua ``stocktake.services``
    (``apply_adjustment`` cập nhật Inventory/Batch đồng bộ) — sửa trực tiếp
    qua Admin (vd đổi ``status`` -> ADJUSTMENT, hay ``qty_actual``) bỏ qua
    transaction đó và không ghi AuditLog (mirror
    ``inventory.admin.ServiceManagedAdminMixin``).
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class StocktakeItemInline(admin.TabularInline):
    model = StocktakeItem
    extra = 0


@admin.register(StocktakeSession)
class StocktakeSessionAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('so_no', 'warehouse', 'location', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'warehouse')
    search_fields = ('so_no',)
    inlines = [StocktakeItemInline]


@admin.register(StocktakeItem)
class StocktakeItemAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('session', 'product', 'qty_system', 'qty_actual', 'reason')
    list_filter = ('session__status', 'reason')
    search_fields = ('session__so_no', 'product__product_code')
