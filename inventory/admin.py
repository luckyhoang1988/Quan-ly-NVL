from django.contrib import admin

from .models import Batch, Inventory, StockMovement, StockTransfer


class ServiceManagedAdminMixin:
    """Chỉ cho xem qua Admin, không cho thêm/sửa/xoá (BUG-09, 2026-07-29).

    Các model này chỉ được tạo/sửa qua service layer
    (``inventory.services``/``quality.services``/``stocktake.services``...) —
    sửa trực tiếp qua Admin bỏ qua audit log và phá đồng bộ
    Batch<->Inventory<->StockMovement (xem CLAUDE.md "Audit trail" và "Any
    code that mutates Inventory.qty_on_hand directly must also keep Batch in
    sync").
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Inventory)
class InventoryAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('product', 'warehouse', 'qty_on_hand', 'qty_reserved', 'qty_available')
    list_filter = ('warehouse',)
    search_fields = ('product__product_code', 'product__name')

    @admin.display(description='Qty available')
    def qty_available(self, obj):
        return obj.qty_available


@admin.register(StockMovement)
class StockMovementAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = (
        'created_at', 'movement_type', 'product', 'warehouse', 'batch',
        'qty', 'qty_on_hand_after', 'reference', 'created_by',
    )
    list_filter = ('movement_type', 'warehouse')
    search_fields = ('product__product_code', 'reference', 'batch__batch_code')


@admin.register(StockTransfer)
class StockTransferAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = (
        'transfer_no', 'batch', 'new_batch', 'from_location', 'to_location',
        'qty', 'created_by', 'created_at',
    )
    list_filter = ('from_location__warehouse', 'to_location__warehouse')
    search_fields = ('transfer_no', 'batch__batch_code', 'new_batch__batch_code')


@admin.register(Batch)
class BatchAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('batch_code', 'product', 'supplier', 'location', 'status', 'qty_received', 'qty_used', 'qty_available', 'exp_date')
    list_filter = ('status', 'location__warehouse')
    search_fields = ('batch_code', 'product__product_code')

    @admin.display(description='Qty available')
    def qty_available(self, obj):
        return obj.qty_available
