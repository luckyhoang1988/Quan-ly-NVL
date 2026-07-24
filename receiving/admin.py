from django.contrib import admin

from .models import Grn, GrnItem, GrnReturn


class GrnItemInline(admin.TabularInline):
    model = GrnItem
    extra = 1


@admin.register(Grn)
class GrnAdmin(admin.ModelAdmin):
    list_display = ('grn_no', 'po', 'supplier', 'grn_date', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'supplier')
    search_fields = ('grn_no', 'po__po_no')
    inlines = [GrnItemInline]


@admin.register(GrnReturn)
class GrnReturnAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'grn', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('grn__grn_no',)
