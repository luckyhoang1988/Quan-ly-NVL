from django.contrib import admin

from .models import Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('supplier_code', 'name', 'contact', 'lead_time_days', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('supplier_code', 'name')
