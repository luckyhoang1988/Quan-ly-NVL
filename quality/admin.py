from django.contrib import admin

from .models import QcCriteria, QcInspection, QcInspectionItem


class QcInspectionItemInline(admin.TabularInline):
    model = QcInspectionItem
    extra = 1


@admin.register(QcInspection)
class QcInspectionAdmin(admin.ModelAdmin):
    list_display = ('qc_no', 'grn', 'inspector', 'status', 'started_at', 'completed_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('qc_no', 'grn__grn_no')
    inlines = [QcInspectionItemInline]


@admin.register(QcCriteria)
class QcCriteriaAdmin(admin.ModelAdmin):
    list_display = ('category', 'name', 'pass_rule', 'fail_rule', 'is_active')
    list_filter = ('category', 'is_active')
    search_fields = ('category', 'name')
