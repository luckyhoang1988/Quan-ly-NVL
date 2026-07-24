from django.contrib import admin

from .models import StocktakeItem, StocktakeSession


class StocktakeItemInline(admin.TabularInline):
    model = StocktakeItem
    extra = 0


@admin.register(StocktakeSession)
class StocktakeSessionAdmin(admin.ModelAdmin):
    list_display = ('so_no', 'warehouse', 'location', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'warehouse')
    search_fields = ('so_no',)
    inlines = [StocktakeItemInline]


@admin.register(StocktakeItem)
class StocktakeItemAdmin(admin.ModelAdmin):
    list_display = ('session', 'product', 'qty_system', 'qty_actual', 'reason')
    list_filter = ('session__status', 'reason')
    search_fields = ('session__so_no', 'product__product_code')
