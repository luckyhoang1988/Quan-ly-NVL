from django.contrib import admin

from .models import Batch, Inventory


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ('product', 'warehouse', 'qty_on_hand', 'qty_reserved', 'qty_available', 'qty_quarantine')
    list_filter = ('warehouse',)
    search_fields = ('product__product_code', 'product__name')

    @admin.display(description='Qty available')
    def qty_available(self, obj):
        return obj.qty_available


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ('batch_code', 'product', 'supplier', 'location', 'status', 'qty_received', 'qty_used', 'qty_available', 'exp_date')
    list_filter = ('status', 'location__warehouse')
    search_fields = ('batch_code', 'product__product_code')

    @admin.display(description='Qty available')
    def qty_available(self, obj):
        return obj.qty_available
