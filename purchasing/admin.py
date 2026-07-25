from django.contrib import admin

from .models import PurchaseOrder, PurchaseOrderItem, PurchaseRequest, PurchaseRequestItem


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 1


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('po_no', 'supplier', 'status', 'expected_delivery_date', 'received_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('po_no',)
    inlines = [PurchaseOrderItemInline]


class PurchaseRequestItemInline(admin.TabularInline):
    model = PurchaseRequestItem
    extra = 1


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    list_display = ('request_no', 'warehouse', 'requested_by', 'status', 'linked_po', 'created_at')
    list_filter = ('status', 'warehouse')
    search_fields = ('request_no',)
    inlines = [PurchaseRequestItemInline]
