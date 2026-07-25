"""Nghiệp vụ inventory (mục 3a): audit trail chuyển động (FR-INV-03), FIFO
issue helper (FR-INV-04) cho GIN dùng, cảnh báo/tự-đóng hạn (FR-INV-02), và
tính EOQ (FR-INV-05).

Theo convention ⏸️ của CLAUDE.md: EXPIRED và cảnh báo sắp hết hạn tính
on-the-fly mỗi lần cần (gọi trước khi FIFO chọn batch / khi xem dashboard),
KHÔNG dùng Celery/cron.
"""
import datetime
import math
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Sum
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog, User
from accounts.notifications import notify
from warehouse.models import Warehouse
from warehouse.services import get_default_location, get_scrap_warehouse

from .models import Batch, Inventory, StockMovement, StockTransfer, WarehouseHandoff


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


def stale_quarantine_batches(days=7, warehouse=None):
    """BACKLOG mục 2c "Quarantine batch": lô ``QUARANTINE`` (QC fail/partial,
    nằm ở Kho phế) đã tạo quá ``days`` ngày mà chưa được xử lý — alert-only,
    KHÔNG có thao tác scrap/return/rework tự động (phạm vi đã chốt với user).
    """
    threshold = timezone.now() - datetime.timedelta(days=days)
    qs = Batch.objects.filter(
        status=Batch.Status.QUARANTINE, created_at__lt=threshold,
    ).select_related('product', 'location__warehouse')
    if warehouse is not None:
        qs = qs.filter(location__warehouse=warehouse)
    return qs.order_by('created_at')


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
def move_batch_qty(*, source_batch, qty, to_location, new_batch_code, new_status, actor=None, reference=''):
    """Nguyên thủy dùng chung: tách ``qty`` từ ``source_batch`` thành 1 batch
    MỚI tại ``to_location`` với ``new_status``, batch nguồn CLOSED/PARTIAL_USED
    tuỳ còn dư (batch bất biến về vị trí — không sửa tại chỗ).

    Dùng bởi ``transfer_stock()`` (tạo thêm ``StockTransfer``, riêng dưới đây),
    bởi ``quality.services`` cho các quyết định QC, và bởi
    ``reject_handoff(..., destination=TO_SCRAP)`` khi kho từ chối nhận 1 batch
    ``PENDING_RECEIPT`` (không tạo ``StockTransfer`` — tự ghi ``log_action``
    riêng ở tầng gọi). Khác kho thì cập nhật ``Inventory`` 2 đầu qua
    ``StockMovement`` TRANSFER_OUT/TRANSFER_IN; cùng kho thì Inventory không
    đổi. ``grn_item`` của batch mới copy từ ``source_batch`` để giữ lineage qua
    nhiều lần tách (Kho chờ -> MAIN/SCRAP).
    """
    source_batch = Batch.objects.select_for_update().get(pk=source_batch.pk)
    if source_batch.status not in (
        Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED, Batch.Status.PENDING_RECEIPT,
    ):
        raise ValidationError(
            f'Không thể tách batch đang ở trạng thái {source_batch.get_status_display()}.'
        )
    if qty <= 0:
        raise ValidationError('Số lượng phải lớn hơn 0.')
    if qty > source_batch.qty_available:
        raise ValidationError(
            f'Batch "{source_batch.batch_code}" chỉ còn {source_batch.qty_available}, không đủ {qty}.'
        )
    if not to_location.is_active:
        raise ValidationError(f'Vị trí "{to_location}" đã ngừng hoạt động.')

    from_location = source_batch.location
    same_warehouse = from_location.warehouse_id == to_location.warehouse_id

    new_batch = Batch.objects.create(
        product=source_batch.product, batch_code=new_batch_code, supplier=source_batch.supplier,
        location=to_location, grn_item=source_batch.grn_item,
        mfg_date=source_batch.mfg_date, exp_date=source_batch.exp_date,
        qty_received=qty, status=new_status,
    )
    source_batch.qty_used += qty
    source_batch.status = (
        Batch.Status.CLOSED if source_batch.qty_available <= 0 else Batch.Status.PARTIAL_USED
    )
    source_batch.save(update_fields=['qty_used', 'status'])

    if not same_warehouse:
        src_inv = Inventory.objects.select_for_update().get(
            product=source_batch.product, warehouse=from_location.warehouse,
        )
        src_inv.qty_on_hand -= qty
        src_inv.save(update_fields=['qty_on_hand', 'updated_at'])
        record_movement(
            product=source_batch.product, warehouse=from_location.warehouse, batch=source_batch,
            movement_type=StockMovement.MovementType.TRANSFER_OUT, qty=-qty,
            reference=reference, actor=actor,
        )
        dst_inv, _ = Inventory.objects.select_for_update().get_or_create(
            product=source_batch.product, warehouse=to_location.warehouse,
        )
        dst_inv.qty_on_hand += qty
        dst_inv.save(update_fields=['qty_on_hand', 'updated_at'])
        record_movement(
            product=source_batch.product, warehouse=to_location.warehouse, batch=new_batch,
            movement_type=StockMovement.MovementType.TRANSFER_IN, qty=qty,
            reference=reference, actor=actor,
        )
    return new_batch


@transaction.atomic
def transfer_stock(*, batch, to_location, qty, note='', actor=None, ip_address=None):
    """FR-WM-06: điều chuyển ``qty`` từ ``batch`` sang ``to_location``.

    Validate riêng của điều chuyển thủ công (vị trí đích phải khác vị trí
    hiện tại, sinh mã ``-T{seq}``, tạo ``StockTransfer`` để có màn hình
    audit riêng — FR-WM-06) rồi delegate phần tách batch/Inventory cho
    ``move_batch_qty()``. Batch đang ở Kho chờ (STAGING) bị chặn: hàng ở đó
    phải đi qua QC (Pass/Fail/Partial Pass), không được điều chuyển tay.
    """
    batch = Batch.objects.select_for_update().get(pk=batch.pk)
    if not to_location.is_active:
        raise ValidationError(f'Vị trí "{to_location}" đã ngừng hoạt động.')
    if to_location.pk == batch.location_id:
        raise ValidationError('Vị trí đích phải khác vị trí hiện tại của batch.')
    if batch.location.warehouse.warehouse_type == Warehouse.WarehouseType.STAGING:
        raise ValidationError(
            'Không thể điều chuyển thủ công batch đang ở Kho chờ — '
            'phải xử lý qua QC (Pass/Fail/Partial Pass).'
        )

    from_location = batch.location
    seq = batch.transfers_from.count() + 1
    # Sinh trước transfer_no (classmethod tự khoá select_for_update trong cùng
    # transaction atomic) để truyền làm reference cho StockMovement ngay trong
    # move_batch_qty(), thay vì tạo StockTransfer trước rồi phải biết batch mới.
    transfer_no = StockTransfer.generate_transfer_no()
    new_batch = move_batch_qty(
        source_batch=batch, qty=qty, to_location=to_location,
        new_batch_code=f'{batch.batch_code}-T{seq}', new_status=Batch.Status.ACTIVE,
        actor=actor, reference=transfer_no,
    )
    batch.refresh_from_db()

    transfer = StockTransfer.objects.create(
        transfer_no=transfer_no, batch=batch, new_batch=new_batch,
        from_location=from_location, to_location=to_location,
        qty=qty, note=note, created_by=actor if getattr(actor, 'pk', None) else None,
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


def _handoff_recipients(warehouse):
    """Nhân viên phụ trách ``warehouse`` (``Warehouse.staff``); fallback toàn
    bộ ``department=WAREHOUSE`` nếu kho đích chưa gán ai — không để phiếu bàn
    giao nào "mồ côi" không ai nhận được (BACKLOG mục 5).
    """
    staff = list(warehouse.staff.filter(is_active=True))
    if staff:
        return staff
    return list(User.objects.filter(department=User.Department.WAREHOUSE, is_active=True))


@transaction.atomic
def create_handoff(*, batch, qc_inspection, destination_warehouse, assigned_to=None):
    """Tạo ``WarehouseHandoff`` PENDING cho 1 batch ``PENDING_RECEIPT`` mới tạo
    bởi ``qc_pass``/``qc_partial_pass`` + báo người nhận (mục 5): ``assigned_to``
    cụ thể thì chỉ báo người đó; để trống thì báo ``_handoff_recipients()``.
    """
    handoff = WarehouseHandoff.objects.create(
        batch=batch, qc_inspection=qc_inspection, destination_warehouse=destination_warehouse,
        assigned_to=assigned_to,
    )
    recipients = [assigned_to] if assigned_to else _handoff_recipients(destination_warehouse)
    notify(
        recipients,
        f'Có lô hàng "{batch.batch_code}" từ QC PASS chờ xác nhận nhận hàng tại '
        f'{destination_warehouse.code}.',
        target=handoff,
    )
    return handoff


@transaction.atomic
def accept_handoff(handoff, actor, ip_address=None):
    """NV kho "Nhận" (mục 6): batch ``PENDING_RECEIPT`` -> ``ACTIVE`` (khả dụng
    FIFO), báo lại QC inspector đã tạo đợt kiểm liên quan.
    """
    handoff = WarehouseHandoff.objects.select_for_update().get(pk=handoff.pk)
    if handoff.status != WarehouseHandoff.Status.PENDING:
        raise ValidationError(f'Phiếu bàn giao này đã được xử lý ({handoff.get_status_display()}).')
    batch = Batch.objects.select_for_update().get(pk=handoff.batch_id)
    if batch.status != Batch.Status.PENDING_RECEIPT:
        raise ValidationError(f'Batch "{batch.batch_code}" không ở trạng thái chờ xác nhận.')

    batch.status = Batch.Status.ACTIVE
    batch.save(update_fields=['status'])

    handoff.status = WarehouseHandoff.Status.ACCEPTED
    handoff.decided_by = actor
    handoff.decided_at = timezone.now()
    handoff.save(update_fields=['status', 'decided_by', 'decided_at'])

    log_action(
        actor, AuditLog.Action.APPROVE, target=handoff,
        description=(
            f'Kho đã nhận lô "{batch.batch_code}" tại {handoff.destination_warehouse.code} -> ACTIVE.'
        ),
        ip_address=ip_address,
    )
    notify(handoff.qc_inspection.inspector, f'Kho đã nhận lô hàng "{batch.batch_code}".', target=handoff)
    return handoff


@transaction.atomic
def reject_handoff(handoff, actor, reason, destination, ip_address=None):
    """NV kho "Từ chối" (mục 6, bắt buộc ``reason``) — 2 nhánh xử lý:

    - ``TO_SCRAP``: tách toàn bộ batch sang Kho phế (QUARANTINE) qua
      ``move_batch_qty`` — dùng guard đã mở rộng cho nguồn ``PENDING_RECEIPT``.
    - ``BACK_TO_QC``: KHÔNG đảo ngược Batch/Inventory (``QcInspection`` PASS
      không tự re-open — cùng boundary đã chốt cho QC override), chỉ đổi
      ``status`` -> REJECTED + báo phòng QC xử lý thủ công (điều chuyển bằng
      tay qua ``transfer_stock`` nếu cần).
    """
    if not reason:
        raise ValidationError('Bắt buộc nhập lý do từ chối.')
    if destination not in WarehouseHandoff.RejectDestination.values:
        raise ValidationError('Lựa chọn xử lý khi từ chối không hợp lệ.')

    handoff = WarehouseHandoff.objects.select_for_update().get(pk=handoff.pk)
    if handoff.status != WarehouseHandoff.Status.PENDING:
        raise ValidationError(f'Phiếu bàn giao này đã được xử lý ({handoff.get_status_display()}).')
    batch = Batch.objects.select_for_update().get(pk=handoff.batch_id)
    if batch.status != Batch.Status.PENDING_RECEIPT:
        raise ValidationError(f'Batch "{batch.batch_code}" không ở trạng thái chờ xác nhận.')

    if destination == WarehouseHandoff.RejectDestination.TO_SCRAP:
        scrap_location = get_default_location(get_scrap_warehouse())
        move_batch_qty(
            source_batch=batch, qty=batch.qty_available, to_location=scrap_location,
            new_batch_code=f'{batch.batch_code}-REJ', new_status=Batch.Status.QUARANTINE,
            actor=actor, reference=f'HANDOFF-{handoff.pk}',
        )

    handoff.status = WarehouseHandoff.Status.REJECTED
    handoff.decided_by = actor
    handoff.decided_at = timezone.now()
    handoff.reject_reason = reason
    handoff.reject_destination = destination
    handoff.save(update_fields=[
        'status', 'decided_by', 'decided_at', 'reject_reason', 'reject_destination',
    ])

    verb = (
        f'Kho từ chối nhận lô "{batch.batch_code}" (lý do: {reason}) — đã chuyển kho phế.'
        if destination == WarehouseHandoff.RejectDestination.TO_SCRAP
        else f'Kho từ chối nhận lô "{batch.batch_code}" (lý do: {reason}) — trả về QC xử lý thủ công.'
    )
    log_action(actor, AuditLog.Action.REJECT, target=handoff, description=verb, reason=reason, ip_address=ip_address)
    notify(User.objects.filter(department=User.Department.QC, is_active=True), verb, target=handoff)
    return handoff


def calculate_eoq(product):
    """FR-INV-05: EOQ = sqrt(2 x D x S / H).

    - D (nhu cầu năm): tổng qty xuất (``StockMovement`` loại ISSUE, trị tuyệt
      đối) trong 365 ngày gần nhất, gộp mọi kho — EOQ là quyết định đặt hàng
      ở mức SKU, không tách theo từng kho.
    - S (chi phí đặt hàng/lần): ``product.ordering_cost``, nhập tay.
    - H (chi phí lưu kho/đơn vị/năm): ``product.holding_cost_rate`` % nhân
      đơn giá bình quân, đơn giá lấy từ lịch sử ``PurchaseOrderItem.unit_price``
      (mọi NCC, mọi PO) — không có bảng giá riêng nên dùng giá mua thực tế.

    Trả về dict: khi đủ dữ liệu có ``eoq`` (làm tròn) + các input đã dùng;
    khi thiếu dữ liệu, ``eoq=None`` kèm ``missing`` liệt kê lý do — không tự
    suy đoán giá trị mặc định cho S/H vì mỗi SKU có đặc thù chi phí khác nhau.
    """
    from purchasing.models import PurchaseOrderItem

    since = timezone.now() - datetime.timedelta(days=365)
    issued = StockMovement.objects.filter(
        product=product, movement_type=StockMovement.MovementType.ISSUE, created_at__gte=since,
    ).aggregate(total=Sum('qty'))['total'] or 0
    annual_demand = abs(issued)

    avg_unit_cost = PurchaseOrderItem.objects.filter(product=product).aggregate(
        avg=Avg('unit_price'))['avg']

    missing = []
    if annual_demand <= 0:
        missing.append('Chưa có lịch sử xuất kho (ISSUE) trong 365 ngày qua để tính nhu cầu năm (D).')
    if not avg_unit_cost:
        missing.append('Chưa có lịch sử đơn giá mua (PurchaseOrderItem) để tính đơn giá bình quân.')
    if not product.ordering_cost:
        missing.append('Chưa cấu hình "Chi phí đặt hàng" (ordering_cost) cho SKU này.')
    if not product.holding_cost_rate:
        missing.append('Chưa cấu hình "% chi phí lưu kho" (holding_cost_rate) cho SKU này.')

    result = {
        'annual_demand': annual_demand,
        'avg_unit_cost': avg_unit_cost,
        'ordering_cost': product.ordering_cost,
        'holding_cost_rate': product.holding_cost_rate,
        'holding_cost_per_unit': None,
        'eoq': None,
        'missing': missing,
    }
    if missing:
        return result

    holding_cost_per_unit = avg_unit_cost * (product.holding_cost_rate / Decimal('100'))
    result['holding_cost_per_unit'] = holding_cost_per_unit
    if holding_cost_per_unit <= 0:
        result['missing'].append('Chi phí lưu kho/đơn vị tính ra bằng 0 — kiểm tra lại đơn giá/tỷ lệ %.')
        return result

    eoq = math.sqrt(2 * float(annual_demand) * float(product.ordering_cost) / float(holding_cost_per_unit))
    result['eoq'] = round(eoq)
    return result
