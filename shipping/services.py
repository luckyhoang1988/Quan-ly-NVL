"""Transaction nghiệp vụ GIN (mục 3b) — DRAFT -> PICKING -> ISSUED -> CLOSED.

FIFO suggest dùng lại ``inventory.services.suggest_fifo_batches`` (FR-GIN-02) —
không viết lại thuật toán FIFO ở đây (đã có unit test riêng ở Task 1). Mỗi hàm
chuyển state là 1 transaction atomic, ghi ``AuditLog`` qua ``accounts.audit.
log_action`` — cùng convention với ``quality.services``/``receiving.services``.

- ``start_picking``: DRAFT -> PICKING, tạo ``GinBatchAllocation`` theo gợi ý
  FIFO cho từng ``GinItem`` (mục 3b Workflow State DRAFT: "hệ thống suggest lô
  FIFO").
- ``override_allocation``: FR-GIN-03, đổi batch khác gợi ý FIFO cho 1 dòng
  allocation, bắt buộc ghi lý do (mục 3b Workflow State PICKING).
- ``issue_gin``: PICKING -> ISSUED (FR-GIN-04/FR-GIN-05) — trừ
  ``Inventory.qty_on_hand``, cộng ``Batch.qty_used``, BR-GIN-006 (batch hết
  hàng -> CLOSED), ghi ``GinItem.qty_issued`` và ``StockMovement`` ISSUE.
- ``close_gin``: ISSUED -> CLOSED (archive).
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog
from inventory.models import Batch, Inventory, StockMovement
from inventory.services import record_movement, suggest_fifo_batches

from .models import Gin, GinBatchAllocation


@transaction.atomic
def start_picking(gin, actor=None, ip_address=None):
    """DRAFT -> PICKING: gợi ý FIFO cho từng ``GinItem``, lưu thành
    ``GinBatchAllocation`` (is_override=False). Xoá allocation cũ trước khi
    tạo lại, phòng trường hợp gọi lại (vd sau khi sửa qty_requested ở DRAFT).
    """
    gin = Gin.objects.select_for_update().get(pk=gin.pk)
    if gin.status != Gin.Status.DRAFT:
        raise ValidationError(f'Không thể bắt đầu soạn hàng khi GIN đang ở trạng thái {gin.status}.')
    if not gin.items.exists():
        raise ValidationError('GIN chưa có dòng hàng nào.')

    for item in gin.items.select_for_update():
        item.allocations.all().delete()
        plan = suggest_fifo_batches(item.product, gin.warehouse, item.qty_requested)
        for line in plan:
            GinBatchAllocation.objects.create(
                gin_item=item, batch=line['batch'], qty_allocated=line['qty_to_issue'],
            )

    gin.status = Gin.Status.PICKING
    gin.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=gin,
        description=f'GIN {gin.gin_no}: DRAFT -> PICKING, đã gợi ý FIFO.',
        ip_address=ip_address,
    )
    return gin


@transaction.atomic
def override_allocation(allocation, new_batch, reason, actor=None, ip_address=None):
    """FR-GIN-03: đổi batch khác gợi ý FIFO cho 1 dòng allocation, tại state
    PICKING. ``new_batch`` phải cùng sản phẩm, ``status=ACTIVE`` (BR-GIN-007 —
    không cho chọn QUARANTINE/EXPIRED), và đủ ``qty_available`` cho
    ``qty_allocated`` hiện tại.
    """
    gin_item = allocation.gin_item
    gin = gin_item.gin
    if gin.status != Gin.Status.PICKING:
        raise ValidationError('Chỉ có thể đổi batch khi GIN đang ở trạng thái PICKING.')
    if not reason:
        raise ValidationError('Phải ghi lý do khi đổi batch.')
    if new_batch.product_id != gin_item.product_id:
        raise ValidationError('Batch mới phải cùng sản phẩm với dòng hàng.')
    if new_batch.status != Batch.Status.ACTIVE:
        raise ValidationError('Chỉ được chọn batch đang ACTIVE (không QUARANTINE/EXPIRED).')
    if new_batch.qty_available < allocation.qty_allocated:
        raise ValidationError(
            f'Batch "{new_batch.batch_code}" chỉ còn {new_batch.qty_available}, '
            f'không đủ {allocation.qty_allocated}.'
        )

    old_batch = allocation.batch
    allocation.batch = new_batch
    allocation.is_override = True
    allocation.override_reason = reason
    allocation.save(update_fields=['batch', 'is_override', 'override_reason'])

    log_action(
        actor, AuditLog.Action.UPDATE, target=gin,
        description=(
            f'GIN {gin.gin_no}: đổi batch dòng {gin_item.product.product_code} '
            f'{old_batch.batch_code} -> {new_batch.batch_code}. Lý do: {reason}'
        ),
        ip_address=ip_address,
    )
    return allocation


@transaction.atomic
def issue_gin(gin, actor=None, ip_address=None):
    """PICKING -> ISSUED (FR-GIN-04): trừ ``Inventory.qty_on_hand``,
    ``Batch.qty_used`` += qty_allocated, ``GinItem.qty_issued`` = tổng
    allocation (FR-GIN-05), khoá edit.

    BR-GIN-001: kiểm tra lại ``qty_allocated <= batch.qty_available`` ngay
    trước khi trừ (phòng batch đã bị GIN khác dùng bớt sau khi suggest/
    override). BR-GIN-006: batch hết hàng (qty_available == 0 sau khi trừ)
    -> CLOSED; còn lại vẫn ACTIVE -> PARTIAL_USED.
    """
    gin = Gin.objects.select_for_update().get(pk=gin.pk)
    if gin.status != Gin.Status.PICKING:
        raise ValidationError(f'Không thể xuất kho khi GIN đang ở trạng thái {gin.status}.')

    for item in gin.items.select_for_update():
        allocations = list(item.allocations.select_related('batch'))
        if not allocations:
            raise ValidationError(f'Dòng "{item.product}" chưa có batch nào được phân bổ.')

        qty_issued = 0
        for alloc in allocations:
            batch = Batch.objects.select_for_update().get(pk=alloc.batch_id)
            if alloc.qty_allocated > batch.qty_available:
                raise ValidationError(
                    f'Batch "{batch.batch_code}" không còn đủ {alloc.qty_allocated} '
                    f'(hiện chỉ còn {batch.qty_available}).'
                )
            batch.qty_used += alloc.qty_allocated
            batch.status = Batch.Status.CLOSED if batch.qty_available <= 0 else Batch.Status.PARTIAL_USED
            batch.save(update_fields=['qty_used', 'status'])

            inv = Inventory.objects.select_for_update().get(product=item.product, warehouse=gin.warehouse)
            inv.qty_on_hand -= alloc.qty_allocated
            inv.save(update_fields=['qty_on_hand', 'updated_at'])
            record_movement(
                product=item.product, warehouse=gin.warehouse, batch=batch,
                movement_type=StockMovement.MovementType.ISSUE, qty=-alloc.qty_allocated,
                reference=gin.gin_no, actor=actor,
            )
            qty_issued += alloc.qty_allocated

        item.qty_issued = qty_issued
        item.save(update_fields=['qty_issued'])

    gin.status = Gin.Status.ISSUED
    gin.issued_at = timezone.now()
    gin.save(update_fields=['status', 'issued_at'])

    log_action(
        actor, AuditLog.Action.APPROVE, target=gin,
        description=f'GIN {gin.gin_no}: PICKING -> ISSUED, đã trừ Inventory/Batch.',
        ip_address=ip_address,
    )
    return gin


@transaction.atomic
def close_gin(gin, actor=None, ip_address=None):
    """ISSUED -> CLOSED (archive)."""
    gin = Gin.objects.select_for_update().get(pk=gin.pk)
    if gin.status != Gin.Status.ISSUED:
        raise ValidationError(f'Không thể đóng GIN khi đang ở trạng thái {gin.status}.')

    gin.status = Gin.Status.CLOSED
    gin.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=gin,
        description=f'Đóng GIN: {gin.gin_no} -> CLOSED.',
        ip_address=ip_address,
    )
    return gin
