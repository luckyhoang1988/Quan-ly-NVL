from django.contrib import admin

from .models import Gin, GinBatchAllocation, GinItem


class GinBatchAllocationInline(admin.TabularInline):
    model = GinBatchAllocation
    extra = 0


class GinItemInline(admin.TabularInline):
    model = GinItem
    extra = 1


@admin.register(Gin)
class GinAdmin(admin.ModelAdmin):
    list_display = (
        'gin_no', 'warehouse', 'reference_type', 'reference_no', 'status', 'requested_by', 'created_at',
    )
    list_filter = ('status', 'reference_type', 'warehouse')
    search_fields = ('gin_no', 'reference_no')
    inlines = [GinItemInline]


@admin.register(GinItem)
class GinItemAdmin(admin.ModelAdmin):
    list_display = ('gin', 'product', 'qty_requested', 'qty_issued')
    list_filter = ('gin__status',)
    search_fields = ('gin__gin_no', 'product__product_code')
    inlines = [GinBatchAllocationInline]
