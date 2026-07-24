"""Transaction nghiệp vụ Stock Opname (BACKLOG mục 4) — PLANNING -> EXECUTION ->
RECONCILIATION -> ADJUSTMENT.

- ``create_session``: FR-SO-01, tạo phiếu PLANNING + snapshot danh sách SKU từ
  Inventory hiện có tại kho (lọc thêm theo vị trí nếu FR-SO-07 chỉ định — xem
  ``stocktake.models`` docstring vì sao lọc qua ``Batch.location`` thay vì
  Inventory, do Inventory không lưu tồn theo từng vị trí).
- ``start_execution``: PLANNING -> EXECUTION, mở khoá nhập Qty thực tế.
- ``record_count``: FR-SO-02/FR-SO-03/FR-SO-04, nhân viên quét barcode nhập
  Qty thực tế cho 1 dòng — variance tính on-the-fly qua ``StocktakeItem.
  variance`` (không lưu cột riêng). Bắt buộc ``reason`` nếu có chênh lệch.
- ``submit_reconciliation``: EXECUTION -> RECONCILIATION, yêu cầu mọi dòng đã
  đếm xong (``qty_actual`` không null).
- ``apply_adjustment``: FR-SO-05, RECONCILIATION -> ADJUSTMENT (terminal). Với
  mỗi dòng có chênh lệch (``variance != 0`` — xem ghi chú ở hàm), cộng/trừ
  ``Inventory.qty_on_hand`` theo đúng chiều variance và ghi ``StockMovement.
  ADJUSTMENT`` qua ``inventory.services.record_movement`` (tái dùng, không tạo
  model "Adjustment" riêng — xem ``stocktake.models`` docstring). Dòng khớp
  đúng (variance = 0) không tạo movement.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog
from inventory.models import Batch, Inventory, StockMovement
from inventory.services import record_movement

from .models import StocktakeItem, StocktakeSession


@transaction.atomic
def create_session(*, warehouse, created_by, location=None, notes='', actor=None, ip_address=None):
    """FR-SO-01: tạo phiếu kiểm kê PLANNING, snapshot danh sách SKU + qty_system.

    Danh sách SKU lấy từ các sản phẩm đang có Inventory tại ``warehouse``
    (sản phẩm chưa từng nhập kho này thì không có gì để đối soát, không đưa
    vào danh sách). Nếu chỉ định ``location`` (FR-SO-07), lọc còn lại các SKU
    có ít nhất 1 Batch tại vị trí đó.
    """
    inventories = Inventory.objects.select_related('product').filter(
        warehouse=warehouse, product__is_active=True,
    )
    if location is not None:
        product_ids = Batch.objects.filter(location=location).values_list('product_id', flat=True)
        inventories = inventories.filter(product_id__in=product_ids)
    inventories = list(inventories)
    if not inventories:
        raise ValidationError('Không có SKU nào để kiểm kê (kho/vị trí chưa có tồn kho).')

    session = StocktakeSession.objects.create(
        warehouse=warehouse, location=location, notes=notes, created_by=created_by,
    )
    StocktakeItem.objects.bulk_create([
        StocktakeItem(session=session, product=inv.product, qty_system=inv.qty_on_hand)
        for inv in inventories
    ])

    log_action(
        actor, AuditLog.Action.CREATE, target=session,
        description=f'Tạo phiếu kiểm kê {session.so_no}: {len(inventories)} SKU.',
        ip_address=ip_address,
    )
    return session


@transaction.atomic
def start_execution(session, actor=None, ip_address=None):
    """PLANNING -> EXECUTION: mở khoá cho nhân viên kho bắt đầu quét & nhập Qty thực tế."""
    session = StocktakeSession.objects.select_for_update().get(pk=session.pk)
    if session.status != StocktakeSession.Status.PLANNING:
        raise ValidationError(f'Không thể bắt đầu kiểm đếm khi phiếu đang ở trạng thái {session.status}.')

    session.status = StocktakeSession.Status.EXECUTION
    session.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=session,
        description=f'Phiếu kiểm kê {session.so_no}: PLANNING -> EXECUTION.',
        ip_address=ip_address,
    )
    return session


@transaction.atomic
def record_count(item, qty_actual, counted_by, reason='', actor=None, ip_address=None):
    """FR-SO-02/FR-SO-03/FR-SO-04: nhập Qty thực tế đếm được cho 1 dòng SKU.

    Chỉ cho phép khi phiếu đang ở EXECUTION. Bắt buộc ``reason`` nếu có chênh
    lệch so với ``qty_system`` (FR-SO-04); không bắt buộc nếu khớp đúng.
    """
    item = StocktakeItem.objects.select_related('session').select_for_update().get(pk=item.pk)
    session = item.session
    if session.status != StocktakeSession.Status.EXECUTION:
        raise ValidationError(f'Không thể nhập Qty thực tế khi phiếu đang ở trạng thái {session.status}.')
    if qty_actual < 0:
        raise ValidationError('Qty thực tế không được âm.')
    if qty_actual != item.qty_system and not reason:
        raise ValidationError('Phải ghi lý do khi có chênh lệch (FR-SO-04).')

    item.qty_actual = qty_actual
    item.reason = reason
    item.counted_by = counted_by
    item.counted_at = timezone.now()
    item.save(update_fields=['qty_actual', 'reason', 'counted_by', 'counted_at'])

    log_action(
        actor, AuditLog.Action.UPDATE, target=session,
        description=(
            f'Kiểm kê {session.so_no}: SKU {item.product.product_code} '
            f'qty_actual={qty_actual} (hệ thống={item.qty_system}).'
        ),
        ip_address=ip_address,
    )
    return item


@transaction.atomic
def submit_reconciliation(session, actor=None, ip_address=None):
    """EXECUTION -> RECONCILIATION: yêu cầu mọi dòng SKU đã có Qty thực tế."""
    session = StocktakeSession.objects.select_for_update().get(pk=session.pk)
    if session.status != StocktakeSession.Status.EXECUTION:
        raise ValidationError(f'Không thể đối soát khi phiếu đang ở trạng thái {session.status}.')
    if session.items.filter(qty_actual__isnull=True).exists():
        raise ValidationError('Còn dòng SKU chưa nhập Qty thực tế.')

    session.status = StocktakeSession.Status.RECONCILIATION
    session.reconciled_at = timezone.now()
    session.save(update_fields=['status', 'reconciled_at'])

    log_action(
        actor, AuditLog.Action.UPDATE, target=session,
        description=f'Phiếu kiểm kê {session.so_no}: EXECUTION -> RECONCILIATION.',
        ip_address=ip_address,
    )
    return session


@transaction.atomic
def apply_adjustment(session, actor=None, ip_address=None):
    """FR-SO-05: RECONCILIATION -> ADJUSTMENT (terminal), tự động điều chỉnh
    Inventory theo chênh lệch từng dòng.

    BACKLOG viết "tự động tạo Adjustment nếu chênh lệch > 0" nhưng DoD lại yêu
    cầu "chênh lệch dương/âm đều tạo đúng Adjustment" — hiểu đúng là bất kỳ
    dòng nào có ``variance != 0`` (thừa lẫn thiếu), không chỉ dương. Mỗi dòng
    thừa/thiếu cộng/trừ thẳng vào ``Inventory.qty_on_hand`` hiện tại (không
    ghi đè bằng ``qty_actual``, vì Inventory có thể đã biến động qua GRN/GIN
    khác kể từ lúc snapshot ``qty_system`` — xem ``stocktake.models``
    docstring) rồi ghi ``StockMovement.ADJUSTMENT``.
    """
    session = StocktakeSession.objects.select_for_update().get(pk=session.pk)
    if session.status != StocktakeSession.Status.RECONCILIATION:
        raise ValidationError(f'Không thể điều chỉnh khi phiếu đang ở trạng thái {session.status}.')

    adjusted = 0
    for item in session.items.select_related('product').select_for_update():
        variance = item.variance
        if not variance:
            continue

        inv = Inventory.objects.select_for_update().get(product=item.product, warehouse=session.warehouse)
        if inv.qty_on_hand + variance < 0:
            raise ValidationError(
                f'Điều chỉnh SKU {item.product.product_code} sẽ làm tồn kho âm '
                f'(hiện có {inv.qty_on_hand}, chênh lệch {variance}).'
            )
        inv.qty_on_hand += variance
        inv.save(update_fields=['qty_on_hand', 'updated_at'])
        record_movement(
            product=item.product, warehouse=session.warehouse,
            movement_type=StockMovement.MovementType.ADJUSTMENT, qty=variance,
            reference=session.so_no, actor=actor,
        )
        adjusted += 1

    session.status = StocktakeSession.Status.ADJUSTMENT
    session.completed_at = timezone.now()
    session.save(update_fields=['status', 'completed_at'])

    log_action(
        actor, AuditLog.Action.APPROVE, target=session,
        description=f'Phiếu kiểm kê {session.so_no}: RECONCILIATION -> ADJUSTMENT, {adjusted} SKU điều chỉnh.',
        ip_address=ip_address,
    )
    return session
