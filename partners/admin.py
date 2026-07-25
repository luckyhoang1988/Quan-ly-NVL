from django.contrib import admin

from .models import Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('supplier_code', 'name', 'contact_name', 'lead_time_days', 'status')
    list_filter = ('status', 'supplier_group')
    search_fields = ('supplier_code', 'name')
