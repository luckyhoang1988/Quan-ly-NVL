from django.contrib import admin

from .models import Batch, Inventory, StockMovement, StockTransfer


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ('product', 'warehouse', 'qty_on_hand', 'qty_reserved', 'qty_available')
    list_filter = ('warehouse',)
    search_fields = ('product__product_code', 'product__name')

    @admin.display(description='Qty available')
    def qty_available(self, obj):
        return obj.qty_available


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'movement_type', 'product', 'warehouse', 'batch',
        'qty', 'qty_on_hand_after', 'reference', 'created_by',
    )
    list_filter = ('movement_type', 'warehouse')
    search_fields = ('product__product_code', 'reference', 'batch__batch_code')


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = (
        'transfer_no', 'batch', 'new_batch', 'from_location', 'to_location',
        'qty', 'created_by', 'created_at',
    )
    list_filter = ('from_location__warehouse', 'to_location__warehouse')
    search_fields = ('transfer_no', 'batch__batch_code', 'new_batch__batch_code')


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ('batch_code', 'product', 'supplier', 'location', 'status', 'qty_received', 'qty_used', 'qty_available', 'exp_date')
    list_filter = ('status', 'location__warehouse')
    search_fields = ('batch_code', 'product__product_code')

    @admin.display(description='Qty available')
    def qty_available(self, obj):
        return obj.qty_available
