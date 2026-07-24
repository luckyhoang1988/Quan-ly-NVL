from django.contrib import admin

from .models import PurchaseOrder, PurchaseOrderItem


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 1


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('po_no', 'supplier', 'status', 'expected_delivery_date', 'received_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('po_no',)
    inlines = [PurchaseOrderItemInline]
