"""Transaction nghiệp vụ PO stub — đồng bộ ``PurchaseOrder.status`` theo Qty đã
nhận lũy kế từ mọi GRN tham chiếu tới PO (FR-GRN-04: hỗ trợ nhận nhiều đợt/1 PO).

Dùng ``qty_received`` (Qty thực nhận tại cổng kho, ghi ở state PENDING_QC) chứ
không phải ``qty_pass`` (kết quả QC) — PO phản ánh tiến độ giao hàng của NCC,
không phụ thuộc QC pass/fail. Item bị QC REJECT (trả hàng) được loại khỏi tổng,
để 1 PO có thể được giao lại (re-ship) ở GRN kế tiếp mà không bị tính trùng.
"""
from django.db.models import Sum

from receiving.models import GrnItem

from .models import PurchaseOrder, PurchaseOrderItem


def sync_po_status(po):
    """Đối chiếu qty_received lũy kế so với qty_ordered từng dòng PO.

    - Mọi dòng đã nhận đủ (>=) -> ``RECEIVED``.
    - Có nhận nhưng chưa đủ -> ``PARTIAL_RECEIVED``.
    - Chưa nhận gì -> giữ nguyên status hiện tại.
    """
    po_items = list(PurchaseOrderItem.objects.filter(purchase_order=po))
    if not po_items:
        return po

    received_by_product = dict(
        GrnItem.objects.filter(grn__po=po)
        .exclude(status=GrnItem.Status.REJECTED)
        .values('product_id')
        .annotate(total=Sum('qty_received'))
        .values_list('product_id', 'total')
    )

    all_fulfilled = all(
        received_by_product.get(item.product_id, 0) >= item.qty_ordered for item in po_items)
    any_received = any(
        received_by_product.get(item.product_id, 0) > 0 for item in po_items)

    if all_fulfilled:
        new_status = PurchaseOrder.Status.RECEIVED
    elif any_received:
        new_status = PurchaseOrder.Status.PARTIAL_RECEIVED
    else:
        return po

    if po.status != new_status:
        po.status = new_status
        po.save(update_fields=['status'])
    return po
