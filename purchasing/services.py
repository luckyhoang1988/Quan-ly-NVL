"""Transaction nghiệp vụ Purchase Order (Phase 5, FR-PO-01..06).

``sync_po_status`` (đã có từ Phase 1e/Phase 2) đồng bộ ``PurchaseOrder.status``
theo Qty đã nhận lũy kế từ mọi GRN tham chiếu tới PO (FR-GRN-04/FR-PO-04: hỗ trợ
nhận nhiều đợt/1 PO). Dùng ``qty_received`` (Qty thực nhận tại cổng kho, ghi ở
state PENDING_QC) chứ không phải ``qty_pass`` (kết quả QC) — PO phản ánh tiến độ
giao hàng của NCC, không phụ thuộc QC pass/fail. Item bị QC REJECT (trả hàng)
được loại khỏi tổng, để 1 PO có thể được giao lại (re-ship) ở GRN kế tiếp mà
không bị tính trùng.

``approve_po``/``send_po``/``close_po`` là transition thật của workflow
DRAFT -> APPROVED -> SENT -> (PARTIAL_RECEIVED/RECEIVED tự động) -> CLOSED.
Không có nhánh auto-approve theo ngưỡng tiền — mọi PO đều cần Manager/Admin
duyệt thủ công (quyết định nghiệp vụ, xem PHÂN quyền ở view: ``approve_po``
chỉ gọi được bởi actor có quyền ``approve`` trên module 'po').
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog
from partners.models import Supplier
from receiving.models import GrnItem

from .models import PurchaseOrder, PurchaseOrderItem


def sync_po_status(po):
    """Đối chiếu qty_received lũy kế so với qty_ordered từng dòng PO.

    - Mọi dòng đã nhận đủ (>=) -> ``RECEIVED`` (set ``received_at`` lần đầu).
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
        update_fields = ['status']
        po.status = new_status
        if new_status == PurchaseOrder.Status.RECEIVED and not po.received_at:
            po.received_at = timezone.localdate()
            update_fields.append('received_at')
        po.save(update_fields=update_fields)
    return po


@transaction.atomic
def approve_po(po, actor=None, ip_address=None):
    """DRAFT -> APPROVED. Quyền ``approve`` trên module 'po' gác ở view (chỉ
    Manager/Admin) — không có nhánh tự động, mọi PO đều cần duyệt thủ công.
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError(f'Không thể duyệt PO khi đang ở trạng thái {po.status}.')

    po.status = PurchaseOrder.Status.APPROVED
    po.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.APPROVE, target=po,
        description=f'Duyệt PO: {po.po_no} DRAFT -> APPROVED.',
        ip_address=ip_address,
    )
    return po


@transaction.atomic
def send_po(po, actor=None, ip_address=None):
    """APPROVED -> SENT (gửi PO tới NCC, khoá sửa)."""
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    if po.status != PurchaseOrder.Status.APPROVED:
        raise ValidationError(f'Không thể gửi PO khi đang ở trạng thái {po.status}.')

    po.status = PurchaseOrder.Status.SENT
    po.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=po,
        description=f'Gửi PO: {po.po_no} APPROVED -> SENT.',
        ip_address=ip_address,
    )
    return po


@transaction.atomic
def close_po(po, actor=None, ip_address=None):
    """{SENT, PARTIAL_RECEIVED, RECEIVED} -> CLOSED (archive).

    Cho phép đóng từ SENT/PARTIAL_RECEIVED (không chỉ RECEIVED) vì Manager có
    thể chủ động đóng PO khi NCC không giao nốt phần còn lại.
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    closeable = (PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIAL_RECEIVED, PurchaseOrder.Status.RECEIVED)
    if po.status not in closeable:
        raise ValidationError(f'Không thể đóng PO khi đang ở trạng thái {po.status}.')

    po.status = PurchaseOrder.Status.CLOSED
    po.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=po,
        description=f'Đóng PO: {po.po_no} -> CLOSED.',
        ip_address=ip_address,
    )
    return po


def supplier_price_history(product):
    """FR-PO-03: so sánh giá từ nhiều NCC cho 1 sản phẩm — giá lần cuối, giá
    trung bình, số lần đặt, theo từng NCC đã từng có dòng PO chứa sản phẩm này.
    """
    rows = (
        PurchaseOrderItem.objects.filter(product=product)
        .values('purchase_order__supplier', 'purchase_order__supplier__supplier_code', 'purchase_order__supplier__name')
        .annotate(avg_price=Avg('unit_price'), po_count=Count('id'))
        .order_by('avg_price')
    )
    last_price_by_supplier = dict(
        PurchaseOrderItem.objects.filter(product=product)
        .order_by('purchase_order__supplier_id', '-purchase_order__created_at')
        .distinct('purchase_order__supplier_id')
        .values_list('purchase_order__supplier_id', 'unit_price')
    )
    results = []
    for row in rows:
        supplier_id = row['purchase_order__supplier']
        results.append({
            'supplier_id': supplier_id,
            'supplier_code': row['purchase_order__supplier__supplier_code'],
            'supplier_name': row['purchase_order__supplier__name'],
            'avg_price': row['avg_price'],
            'last_price': last_price_by_supplier.get(supplier_id),
            'po_count': row['po_count'],
        })
    return results


def supplier_lead_time_stats():
    """FR-PO-05/FR-PO-06: với mỗi NCC active, so sánh ``lead_time_days`` cấu
    hình với lead-time thực tế (``received_at - created_at`` của các PO đã
    RECEIVED), đếm số PO đúng hạn/trễ hạn so với ``expected_delivery_date``.
    Tính on-the-fly khi load trang (⏸️ không cron/Celery, theo CLAUDE.md).
    """
    results = []
    for supplier in Supplier.objects.filter(is_active=True):
        received_pos = PurchaseOrder.objects.filter(supplier=supplier, received_at__isnull=False)
        lead_times = [
            (po.received_at - po.created_at.date()).days for po in received_pos
        ]
        avg_actual_lead_time = sum(lead_times) / len(lead_times) if lead_times else None

        on_time_count = 0
        delayed_count = 0
        for po in received_pos:
            if not po.expected_delivery_date:
                continue
            if po.received_at > po.expected_delivery_date:
                delayed_count += 1
            else:
                on_time_count += 1

        results.append({
            'supplier': supplier,
            'configured_lead_time_days': supplier.lead_time_days,
            'avg_actual_lead_time_days': avg_actual_lead_time,
            'received_po_count': len(lead_times),
            'on_time_count': on_time_count,
            'delayed_count': delayed_count,
        })
    return results
