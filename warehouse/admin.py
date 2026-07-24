from django.contrib import admin

from .models import Location, Warehouse


class LocationInline(admin.TabularInline):
    model = Location
    extra = 0


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_active', 'capacity')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')
    inlines = [LocationInline]


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ('warehouse', 'code', 'is_active', 'capacity')
    list_filter = ('is_active', 'warehouse')
    search_fields = ('code', 'warehouse__code')
