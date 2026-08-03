from django.contrib import admin

from .models import (
    ProcurementAllocation,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseRequest,
    PurchaseRequestItem,
)


class ServiceManagedAdminMixin:
    """Chỉ cho xem qua Admin, không cho thêm/sửa/xoá (BUG-19, 2026-07-30, xem
    CLAUDE.md "Any model that a service layer says don't create directly").

    Workflow PO (DRAFT -> APPROVED -> SENT -> PARTIAL_RECEIVED/RECEIVED ->
    CLOSED) và PR (DRAFT -> PENDING_DEPT/PENDING_PUR -> APPROVED/REJECTED) chỉ
    được vận hành qua ``purchasing.services`` (duyệt/gửi/đóng PO,
    submit/decide PR qua ``accounts.approvals``) — sửa trực tiếp qua Admin (vd
    đổi ``status`` -> APPROVED) bỏ qua transaction đó và không ghi AuditLog
    (mirror ``inventory.admin.ServiceManagedAdminMixin``).
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 1


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('po_no', 'supplier', 'status', 'expected_delivery_date', 'received_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('po_no',)
    inlines = [PurchaseOrderItemInline]


class PurchaseRequestItemInline(admin.TabularInline):
    model = PurchaseRequestItem
    extra = 1


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('request_no', 'warehouse', 'requested_by', 'status', 'linked_po', 'created_at')
    list_filter = ('status', 'warehouse')
    search_fields = ('request_no',)
    inlines = [PurchaseRequestItemInline]


@admin.register(ProcurementAllocation)
class ProcurementAllocationAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('pr_item', 'po_no_snapshot', 'product_code_snapshot', 'qty_allocated', 'status', 'created_at')
    list_filter = ('status',)
