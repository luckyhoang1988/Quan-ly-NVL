"""Nghiệp vụ inventory (mục 3a): audit trail chuyển động (FR-INV-03), FIFO
issue helper (FR-INV-04) cho GIN dùng, và cảnh báo/tự-đóng hạn (FR-INV-02).

Theo convention ⏸️ của CLAUDE.md: EXPIRED và cảnh báo sắp hết hạn tính
on-the-fly mỗi lần cần (gọi trước khi FIFO chọn batch / khi xem dashboard),
KHÔNG dùng Celery/cron.
"""
import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog

from .models import Batch, Inventory, StockMovement, StockTransfer


def sync_expired_batches():
    """BR-GIN-007: chuyển ACTIVE -> EXPIRED cho batch có ``exp_date`` < hôm nay.

    Tính on-the-fly, không cron — gọi trước khi FIFO chọn batch để status
    luôn phản ánh đúng thực tế trước khi dùng.
    """
    today = timezone.now().date()
    return Batch.objects.filter(
        status=Batch.Status.ACTIVE, exp_date__isnull=False, exp_date__lt=today,
    ).update(status=Batch.Status.EXPIRED)


def expiring_soon_batches(days=30, warehouse=None):
    """FR-INV-02: lô ACTIVE sẽ hết hạn trong vòng ``days`` ngày tới."""
    threshold = timezone.now().date() + datetime.timedelta(days=days)
    qs = Batch.objects.filter(
        status=Batch.Status.ACTIVE, exp_date__isnull=False, exp_date__lt=threshold,
    ).select_related('product', 'location__warehouse')
    if warehouse is not None:
        qs = qs.filter(location__warehouse=warehouse)
    return qs.order_by('exp_date')


def suggest_fifo_batches(product, warehouse, qty_needed):
    """FR-INV-04/FR-GIN-02: gợi ý danh sách batch FIFO đáp ứng ``qty_needed``.

    Chỉ chọn batch ``status=ACTIVE`` (loại QUARANTINE/EXPIRED — BR-GIN-007),
    sắp theo ``exp_date`` ASC rồi ``created_at`` ASC, duyệt tới khi đủ số
    lượng — tách nhiều batch nếu 1 batch không đủ (BACKLOG mục 3b). Raise
    ``ValidationError`` nếu tổng tồn ACTIVE không đủ, không cho issue âm.

    Trả về list dict ``{batch, batch_id, qty_to_issue, exp_date, location}``.
    """
    if qty_needed <= 0:
        raise ValidationError('Số lượng cần xuất phải lớn hơn 0.')

    sync_expired_batches()
    batches = Batch.objects.filter(
        product=product, location__warehouse=warehouse, status=Batch.Status.ACTIVE,
    ).order_by('exp_date', 'created_at')

    plan = []
    remaining = qty_needed
    for batch in batches:
        available = batch.qty_available
        if available <= 0:
            continue
        take = min(available, remaining)
        plan.append({
            'batch': batch,
            'batch_id': batch.pk,
            'qty_to_issue': take,
            'exp_date': batch.exp_date,
            'location': batch.location,
        })
        remaining -= take
        if remaining <= 0:
            break

    if remaining > 0:
        raise ValidationError(
            f'Không đủ tồn kho FIFO cho {product}: thiếu {remaining} '
            f'(đã duyệt hết batch ACTIVE khả dụng tại {warehouse}).'
        )
    return plan


def record_movement(*, product, warehouse, movement_type, qty, batch=None, reference='', actor=None):
    """FR-INV-03: ghi 1 dòng ``StockMovement``.

    Gọi SAU KHI ``Inventory.qty_on_hand`` đã cập nhật — cần số dư mới để
    snapshot ``qty_on_hand_after``.
    """
    inv = Inventory.objects.get(product=product, warehouse=warehouse)
    return StockMovement.objects.create(
        product=product, warehouse=warehouse, batch=batch,
        movement_type=movement_type, qty=qty, qty_on_hand_after=inv.qty_on_hand,
        reference=reference, created_by=actor if getattr(actor, 'pk', None) else None,
    )


@transaction.atomic
def transfer_stock(*, batch, to_location, qty, note='', actor=None, ip_address=None):
    """FR-WM-06: điều chuyển ``qty`` từ ``batch`` sang ``to_location``.

    Batch bất biến về vị trí (giống cách ``qc_partial_pass`` tách batch thay
    vì sửa tại chỗ): luôn tách ``qty`` thành 1 batch mới ACTIVE tại vị trí
    đích, batch nguồn tăng ``qty_used`` (CLOSED nếu hết, không thì
    PARTIAL_USED — cùng convention BR-GIN-006). Nếu khác kho, trừ/cộng
    ``Inventory`` 2 đầu qua ``StockMovement`` TRANSFER_OUT/TRANSFER_IN; cùng
    kho (chỉ đổi vị trí nội bộ) thì Inventory không đổi.
    """
    batch = Batch.objects.select_for_update().get(pk=batch.pk)
    if batch.status not in (Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED):
        raise ValidationError(
            f'Không thể điều chuyển batch đang ở trạng thái {batch.get_status_display()}.'
        )
    if qty <= 0:
        raise ValidationError('Số lượng điều chuyển phải lớn hơn 0.')
    if qty > batch.qty_available:
        raise ValidationError(
            f'Batch "{batch.batch_code}" chỉ còn {batch.qty_available}, không đủ {qty}.'
        )
    if not to_location.is_active:
        raise ValidationError(f'Vị trí "{to_location}" đã ngừng hoạt động.')
    if to_location.pk == batch.location_id:
        raise ValidationError('Vị trí đích phải khác vị trí hiện tại của batch.')

    from_location = batch.location
    same_warehouse = from_location.warehouse_id == to_location.warehouse_id

    seq = batch.transfers_from.count() + 1
    new_batch = Batch.objects.create(
        product=batch.product, batch_code=f'{batch.batch_code}-T{seq}', supplier=batch.supplier,
        location=to_location, mfg_date=batch.mfg_date, exp_date=batch.exp_date,
        qty_received=qty, status=Batch.Status.ACTIVE,
    )
    batch.qty_used += qty
    batch.status = Batch.Status.CLOSED if batch.qty_available <= 0 else Batch.Status.PARTIAL_USED
    batch.save(update_fields=['qty_used', 'status'])

    transfer = StockTransfer.objects.create(
        batch=batch, new_batch=new_batch, from_location=from_location, to_location=to_location,
        qty=qty, note=note, created_by=actor if getattr(actor, 'pk', None) else None,
    )

    if not same_warehouse:
        src_inv = Inventory.objects.select_for_update().get(
            product=batch.product, warehouse=from_location.warehouse,
        )
        src_inv.qty_on_hand -= qty
        src_inv.save(update_fields=['qty_on_hand', 'updated_at'])
        record_movement(
            product=batch.product, warehouse=from_location.warehouse, batch=batch,
            movement_type=StockMovement.MovementType.TRANSFER_OUT, qty=-qty,
            reference=transfer.transfer_no, actor=actor,
        )
        dst_inv, _ = Inventory.objects.select_for_update().get_or_create(
            product=batch.product, warehouse=to_location.warehouse,
        )
        dst_inv.qty_on_hand += qty
        dst_inv.save(update_fields=['qty_on_hand', 'updated_at'])
        record_movement(
            product=batch.product, warehouse=to_location.warehouse, batch=new_batch,
            movement_type=StockMovement.MovementType.TRANSFER_IN, qty=qty,
            reference=transfer.transfer_no, actor=actor,
        )

    log_action(
        actor, AuditLog.Action.CREATE, target=transfer,
        description=(
            f'Điều chuyển {transfer.transfer_no}: {batch.batch_code} @ {from_location} '
            f'-> {new_batch.batch_code} @ {to_location} (qty={qty}).'
        ),
        ip_address=ip_address,
    )
    return transfer
