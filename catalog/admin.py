from django.contrib import admin

from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('product_code', 'name', 'category', 'uom', 'min_level', 'max_level', 'is_active')
    list_filter = ('is_active', 'category')
    search_fields = ('product_code', 'name')
