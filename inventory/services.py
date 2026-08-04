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
    """BR-GIN-007: chuyển ACTIVE/PARTIAL_USED -> EXPIRED cho batch có
    ``exp_date`` < hôm nay.

    Tính on-the-fly, không cron — gọi trước khi FIFO chọn batch để status
    luôn phản ánh đúng thực tế trước khi dùng. Bao gồm cả PARTIAL_USED: lô đã
    xuất một phần vẫn còn ``qty_available`` > 0 nên vẫn phải hết hạn đúng lúc,
    không chỉ ACTIVE mới hết hạn được (bug fix 2026-07-27, xem CLAUDE.md).

    Đi từng batch một (``select_for_update()`` + ``save()``) thay vì
    ``.update()`` hàng loạt — mọi chuyển trạng thái Batch đều cần ghi
    ``AuditLog`` (xem CLAUDE.md "Audit trail"), ``.update()`` bỏ qua
    ``save()`` nên không ghi được (BUG-11, 2026-07-29). ``actor=None`` vì đây
    là hành động hệ thống, không phải do người dùng bấm.
    """
    today = timezone.localdate()
    with transaction.atomic():
        batches = list(Batch.objects.select_for_update().filter(
            status__in=[Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED],
            exp_date__isnull=False, exp_date__lt=today,
        ))
        for batch in batches:
            old_status = batch.status
            batch.status = Batch.Status.EXPIRED
            batch.save(update_fields=['status'])
            log_action(
                None, AuditLog.Action.UPDATE, target=batch,
                description=f'Batch {batch.batch_code} hết hạn: {old_status} -> EXPIRED (tự động).',
                changes={'status': [old_status, Batch.Status.EXPIRED]},
            )
    return len(batches)


def expiring_soon_batches(days=30, warehouse=None, warehouse_type=None):
    """FR-INV-02: lô ACTIVE/PARTIAL_USED sẽ hết hạn trong vòng ``days`` ngày tới.

    ``warehouse_type`` (vd ``Warehouse.WarehouseType.MAIN``) lọc theo LOẠI kho,
    khác ``warehouse`` (1 kho cụ thể) — dùng ở ``reports.services.dashboard_kpis``
    để lô đang nằm Kho chờ (``ACTIVE`` nhưng chưa qua QC, xem Phase D ở CLAUDE.md)
    hoặc Kho phế không lọt vào KPI "sắp hết hạn", cùng invariant với
    ``total_inventory_value``/``low_stock_count`` trong hàm đó (bug fix
    2026-07-27, xem CLAUDE.md). ``batch_list``/``batch_detail`` (mục 3a) cố ý
    không truyền tham số này — trang quản lý lô hàng cần thấy cảnh báo hết hạn
    ở MỌI kho, kể cả Kho chờ/Kho phế.
    """
    threshold = timezone.localdate() + datetime.timedelta(days=days)
    qs = Batch.objects.filter(
        status__in=[Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED],
        exp_date__isnull=False, exp_date__lt=threshold,
    ).select_related('product', 'location__warehouse')
    if warehouse is not None:
        qs = qs.filter(location__warehouse=warehouse)
    if warehouse_type is not None:
        qs = qs.filter(location__warehouse__warehouse_type=warehouse_type)
    return qs.order_by('exp_date')


def stale_quarantine_batches(days=7, warehouse=None):
    """BACKLOG mục 2c "Quarantine batch": lô ``QUARANTINE`` (QC fail/partial,
    nằm ở Kho phế) đã tạo quá ``days`` ngày mà chưa được xử lý — alert on-the-fly.
    Thao tác đóng vòng: ``scrap_writeoff`` / ``return_quarantine_to_supplier`` /
    ``release_quarantine_to_main`` (Wave 1 QC disposition).
    """
    threshold = timezone.now() - datetime.timedelta(days=days)
    qs = Batch.objects.filter(
        status=Batch.Status.QUARANTINE, created_at__lt=threshold,
    ).select_related('product', 'location__warehouse')
    if warehouse is not None:
        qs = qs.filter(location__warehouse=warehouse)
    return qs.order_by('created_at')


_OPEN_RETURN_STATUSES = ('PENDING', 'APPROVED')


def _quarantine_blocked_by_open_return(batch):
    """True nếu có GrnReturn mở gắn đúng batch, hoặc return legacy (batch null)
    của cùng GRN — chặn disposition trùng.
    """
    from receiving.models import GrnReturn

    if GrnReturn.objects.filter(batch=batch, status__in=_OPEN_RETURN_STATUSES).exists():
        return True
    if batch.grn_item_id is None:
        return False
    return GrnReturn.objects.filter(
        grn_id=batch.grn_item.grn_id, batch__isnull=True, status__in=_OPEN_RETURN_STATUSES,
    ).exists()


def _assert_quarantine_scrap_batch(batch):
    if batch.status != Batch.Status.QUARANTINE:
        raise ValidationError(
            f'Chỉ xử lý được lô Quarantine (hiện tại: {batch.get_status_display()}).'
        )
    if batch.location.warehouse.warehouse_type != Warehouse.WarehouseType.SCRAP:
        raise ValidationError('Lô Quarantine phải nằm ở Kho phế.')


def _deduct_quarantine_qty(*, batch, qty, actor, reference, skip_open_return_check=False):
    """Trừ ``qty`` khỏi batch QUARANTINE @ SCRAP + ADJUSTMENT. Partial giữ
    ``QUARANTINE`` (không promote ``PARTIAL_USED``); hết → ``CLOSED``.
    Caller phải đã nằm trong ``transaction.atomic`` và đã khoá Inventory nếu cần.
    """
    if qty <= 0:
        raise ValidationError('Số lượng phải lớn hơn 0.')
    _assert_quarantine_scrap_batch(batch)
    if not skip_open_return_check and _quarantine_blocked_by_open_return(batch):
        raise ValidationError('Lô đang có phiếu trả hàng NCC chưa hoàn tất — không thể xử lý.')
    if qty > batch.qty_available:
        raise ValidationError(
            f'Batch "{batch.batch_code}" chỉ còn {batch.qty_available}, không đủ {qty}.'
        )

    warehouse = batch.location.warehouse
    inv = lock_inventories(batch.product, [warehouse])[warehouse.id]
    batch = Batch.objects.select_for_update().get(pk=batch.pk)
    _assert_quarantine_scrap_batch(batch)
    if qty > batch.qty_available:
        raise ValidationError(
            f'Batch "{batch.batch_code}" chỉ còn {batch.qty_available}, không đủ {qty}.'
        )

    batch.qty_used += qty
    if batch.qty_available <= 0:
        batch.status = Batch.Status.CLOSED
    # else: giữ QUARANTINE
    batch.save(update_fields=['qty_used', 'status'])

    inv.qty_on_hand -= qty
    inv.save(update_fields=['qty_on_hand', 'updated_at'])
    record_movement(
        product=batch.product, warehouse=warehouse, batch=batch,
        movement_type=StockMovement.MovementType.ADJUSTMENT, qty=-qty,
        reference=reference, actor=actor,
    )
    return batch


def _move_quarantine_to_main(*, source_batch, qty, to_location, new_batch_code, actor, reference):
    """Tách qty từ QUARANTINE @ SCRAP → batch mới PENDING_RECEIPT @ MAIN.
    Partial nguồn giữ QUARANTINE; hết → CLOSED.
    """
    _assert_quarantine_scrap_batch(source_batch)
    if to_location.warehouse.warehouse_type != Warehouse.WarehouseType.MAIN:
        raise ValidationError('Vị trí đích phải thuộc kho loại "Kho thành phẩm".')
    if not to_location.is_active:
        raise ValidationError(f'Vị trí "{to_location}" đã ngừng hoạt động.')
    if not to_location.warehouse.is_active:
        raise ValidationError(f'Kho "{to_location.warehouse}" đã ngừng hoạt động.')
    if qty <= 0:
        raise ValidationError('Số lượng phải lớn hơn 0.')

    ref = Batch.objects.select_related('product', 'location__warehouse').get(pk=source_batch.pk)
    locked_inv = lock_inventories(ref.product, [ref.location.warehouse, to_location.warehouse])
    source_batch = Batch.objects.select_for_update().get(pk=source_batch.pk)
    _assert_quarantine_scrap_batch(source_batch)
    if qty > source_batch.qty_available:
        raise ValidationError(
            f'Batch "{source_batch.batch_code}" chỉ còn {source_batch.qty_available}, không đủ {qty}.'
        )

    new_batch = Batch.objects.create(
        product=source_batch.product, batch_code=new_batch_code, supplier=source_batch.supplier,
        location=to_location, grn_item=source_batch.grn_item,
        mfg_date=source_batch.mfg_date, exp_date=source_batch.exp_date,
        qty_received=qty, status=Batch.Status.PENDING_RECEIPT,
    )
    source_batch.qty_used += qty
    source_batch.status = (
        Batch.Status.CLOSED if source_batch.qty_available <= 0 else Batch.Status.QUARANTINE
    )
    source_batch.save(update_fields=['qty_used', 'status'])

    src_inv = locked_inv[source_batch.location.warehouse_id]
    src_inv.qty_on_hand -= qty
    src_inv.save(update_fields=['qty_on_hand', 'updated_at'])
    record_movement(
        product=source_batch.product, warehouse=source_batch.location.warehouse, batch=source_batch,
        movement_type=StockMovement.MovementType.TRANSFER_OUT, qty=-qty,
        reference=reference, actor=actor,
    )
    dst_inv = locked_inv[to_location.warehouse_id]
    dst_inv.qty_on_hand += qty
    dst_inv.save(update_fields=['qty_on_hand', 'updated_at'])
    record_movement(
        product=source_batch.product, warehouse=to_location.warehouse, batch=new_batch,
        movement_type=StockMovement.MovementType.TRANSFER_IN, qty=qty,
        reference=reference, actor=actor,
    )
    return new_batch


@transaction.atomic
def scrap_writeoff(*, batch, qty, reason, actor, ip_address=None):
    """Wave 1 disposition: huỷ tồn SCRAP (ADJUSTMENT) + đóng/giảm lô QUARANTINE."""
    if not actor or not actor.can('approve', 'qc'):
        raise ValidationError('Bạn không có quyền scrap write-off lô Quarantine.')
    if not (reason or '').strip():
        raise ValidationError({'reason': 'Lý do là bắt buộc.'})

    batch = Batch.objects.select_related('product', 'location__warehouse', 'grn_item').get(pk=batch.pk)
    batch = _deduct_quarantine_qty(
        batch=batch, qty=qty, actor=actor, reference=batch.batch_code,
    )
    log_action(
        actor, AuditLog.Action.UPDATE, target=batch,
        description=f'Scrap write-off lô {batch.batch_code} (qty={qty}).',
        reason=reason.strip(), ip_address=ip_address,
        changes={'qty_written_off': qty, 'status': batch.status},
    )
    return batch


@transaction.atomic
def return_quarantine_to_supplier(*, batch, qty, reason, actor, ip_address=None):
    """Wave 1 disposition: tạo ``GrnReturn(batch, qty)`` PENDING — chưa trừ Inventory."""
    from receiving.models import GrnReturn

    if not actor or not actor.can('approve', 'qc'):
        raise ValidationError('Bạn không có quyền tạo phiếu trả hàng từ lô Quarantine.')
    if not (reason or '').strip():
        raise ValidationError({'reason': 'Lý do là bắt buộc.'})

    batch = Batch.objects.select_related(
        'product', 'location__warehouse', 'grn_item__grn',
    ).select_for_update(of=('self',)).get(pk=batch.pk)
    _assert_quarantine_scrap_batch(batch)
    if _quarantine_blocked_by_open_return(batch):
        raise ValidationError('Lô đang có phiếu trả hàng NCC chưa hoàn tất — không thể tạo return mới.')
    if batch.grn_item_id is None:
        raise ValidationError('Lô không có nguồn GRN — không thể tạo phiếu trả NCC.')
    if qty <= 0 or qty > batch.qty_available:
        raise ValidationError(f'Số lượng trả phải trong khoảng 1..{batch.qty_available}.')

    ret = GrnReturn.objects.create(
        grn=batch.grn_item.grn, batch=batch, qty=qty, reason=reason.strip(),
    )
    log_action(
        actor, AuditLog.Action.CREATE, target=ret,
        description=f'Tạo trả hàng NCC từ lô {batch.batch_code} (qty={qty}).',
        reason=reason.strip(), ip_address=ip_address,
    )
    notify(
        User.objects.filter(department=User.Department.PURCHASING, is_active=True),
        f'Có phiếu trả hàng RETURN-{ret.grn.grn_no} từ lô Quarantine {batch.batch_code}.',
        target=ret,
    )
    return ret


@transaction.atomic
def consume_scrap_for_returned(grn_return, actor=None):
    """Gọi từ ``mark_return_returned``: trừ tồn SCRAP khi hàng đã trả NCC."""
    from receiving.models import GrnReturn

    grn_return = GrnReturn.objects.select_related(
        'batch__location__warehouse', 'batch__product', 'grn',
    ).get(pk=grn_return.pk)

    if grn_return.batch_id:
        qty = grn_return.qty or grn_return.batch.qty_available
        _deduct_quarantine_qty(
            batch=grn_return.batch, qty=qty, actor=actor,
            reference=f'RETURN-{grn_return.grn.grn_no}',
            skip_open_return_check=True,
        )
        return

    batches = (
        Batch.objects.filter(
            status=Batch.Status.QUARANTINE,
            grn_item__grn_id=grn_return.grn_id,
        )
        .select_related('product', 'location__warehouse')
        .order_by('product_id', 'pk')
    )
    for batch in batches:
        if batch.qty_available <= 0:
            continue
        _deduct_quarantine_qty(
            batch=batch, qty=batch.qty_available, actor=actor,
            reference=f'RETURN-{grn_return.grn.grn_no}',
            skip_open_return_check=True,
        )


@transaction.atomic
def release_quarantine_to_main(
    *, batch, qty, location, reason, actor, assigned_to=None, ip_address=None,
):
    """Wave 1 disposition: Use-as-is có kiểm soát — SCRAP → MAIN PENDING_RECEIPT + handoff."""
    from quality.models import QcInspection

    if not actor or not actor.can('override', 'qc'):
        raise ValidationError('Bạn không có quyền phóng thích lô Quarantine về kho chính.')
    if not (reason or '').strip():
        raise ValidationError({'reason': 'Lý do là bắt buộc.'})

    batch = Batch.objects.select_related(
        'product', 'location__warehouse', 'grn_item__grn',
    ).get(pk=batch.pk)
    if _quarantine_blocked_by_open_return(batch):
        raise ValidationError('Lô đang có phiếu trả hàng NCC chưa hoàn tất — không thể phóng thích.')
    if batch.grn_item_id is None:
        raise ValidationError('Lô không có nguồn GRN — không xác định được đợt QC để bàn giao.')

    inspection = (
        QcInspection.objects.filter(grn_id=batch.grn_item.grn_id)
        .exclude(status=QcInspection.Result.CANCELLED)
        .exclude(status=QcInspection.Result.PENDING_QC)
        .order_by('-completed_at', '-pk')
        .first()
    )
    if inspection is None:
        raise ValidationError('Không tìm thấy đợt QC đã hoàn tất cho GRN của lô này.')

    new_code = f'{batch.batch_code}-REL'
    # uniqueness: append short suffix if collision
    if Batch.objects.filter(batch_code=new_code).exists():
        new_code = f'{batch.batch_code}-REL{batch.pk}'

    new_batch = _move_quarantine_to_main(
        source_batch=batch, qty=qty, to_location=location, new_batch_code=new_code,
        actor=actor, reference=batch.batch_code,
    )
    create_handoff(
        batch=new_batch, qc_inspection=inspection,
        destination_warehouse=location.warehouse, assigned_to=assigned_to,
    )
    log_action(
        actor, AuditLog.Action.UPDATE, target=new_batch,
        description=(
            f'Phóng thích Quarantine {batch.batch_code} -> {new_batch.batch_code} '
            f'@ {location} (qty={qty}).'
        ),
        reason=reason.strip(), ip_address=ip_address,
    )
    return new_batch


def suggest_fifo_batches(product, warehouse, qty_needed):
    """FR-INV-04/FR-GIN-02: gợi ý danh sách batch FIFO đáp ứng ``qty_needed``.

    Chọn batch ``status`` ACTIVE hoặc PARTIAL_USED — lô đã xuất một phần vẫn
    còn ``qty_available`` > 0 nên vẫn phải khả dụng FIFO tiếp; loại nó ra sẽ
    khiến phần tồn còn lại "kẹt" vĩnh viễn, không bao giờ xuất được nữa dù
    ``Inventory.qty_on_hand`` vẫn còn (bug fix 2026-07-27, xem CLAUDE.md).
    Loại QUARANTINE/EXPIRED/PENDING_RECEIPT (BR-GIN-007). Sắp theo
    ``exp_date`` ASC rồi ``created_at`` ASC, duyệt tới khi đủ số lượng — tách
    nhiều batch nếu 1 batch không đủ (BACKLOG mục 3b). Raise
    ``ValidationError`` nếu tổng tồn không đủ, không cho issue âm.

    Trả về list dict ``{batch, batch_id, qty_to_issue, exp_date, location}``.
    """
    if qty_needed <= 0:
        raise ValidationError('Số lượng cần xuất phải lớn hơn 0.')

    sync_expired_batches()
    batches = Batch.objects.filter(
        product=product, location__warehouse=warehouse,
        status__in=[Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED],
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


def lock_inventories(product, warehouses):
    """Khoá 1+ dòng ``Inventory`` của ``product`` tại nhiều kho, theo thứ tự
    ỔN ĐỊNH (``warehouse_id`` tăng dần) — chuẩn hoá thứ tự khoá toàn hệ thống
    ``Inventory`` -> ``Batch`` -> ``WarehouseHandoff`` (BUG-16, 2026-07-29, xem
    CLAUDE.md), đồng thời tránh deadlock Inventory-Inventory nếu 2 giao dịch
    cùng chạm 2 kho theo chiều ngược nhau (vd 2 lượt điều chuyển ngược hướng
    cùng lúc). Trả về dict ``{warehouse_id: Inventory}`` (tạo mới nếu kho đó
    chưa từng có dòng Inventory cho SKU này — mirror ``get_or_create`` gốc).
    Mọi hàm khoá cả Batch lẫn Inventory của cùng 1 giao dịch phải gọi hàm này
    (hoặc tự khoá Inventory theo đúng thứ tự) TRƯỚC KHI khoá Batch.
    """
    deduped = sorted({w.id: w for w in warehouses}.values(), key=lambda w: w.id)
    return {
        warehouse.id: Inventory.objects.select_for_update().get_or_create(
            product=product, warehouse=warehouse)[0]
        for warehouse in deduped
    }


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

    Khoá Inventory (nếu khác kho) TRƯỚC Batch — chuẩn hoá thứ tự khoá
    ``Inventory -> Batch -> WarehouseHandoff`` toàn hệ thống (BUG-16,
    2026-07-29, xem CLAUDE.md). Đọc ``source_batch`` UNLOCKED trước để biết
    SKU/kho nguồn cần khoá Inventory nào — an toàn vì vị trí/sản phẩm của 1
    batch không bao giờ đổi sau khi tạo (bất biến, xem docstring trên), cùng
    nguyên tắc "chỉ tin FK ổn định để CHỌN hàng cần khoá" đã dùng ở BUG-15.
    Caller nào tự cần khoá Batch trước khi gọi hàm này (validate riêng trước
    khi mutate) phải tự gọi ``lock_inventories()`` trước đó — xem
    ``transfer_stock``/``reject_handoff``/``quality.services.qc_pass`` làm ví
    dụ; nếu không, Batch sẽ bị khoá trước Inventory ngay tại caller, phá vỡ
    thứ tự dù bản thân hàm này vẫn khoá đúng thứ tự nội bộ.
    """
    ref = Batch.objects.select_related('product', 'location__warehouse').get(pk=source_batch.pk)
    same_warehouse = ref.location.warehouse_id == to_location.warehouse_id
    locked_inv = {} if same_warehouse else lock_inventories(
        ref.product, [ref.location.warehouse, to_location.warehouse])

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
    if not to_location.warehouse.is_active:
        raise ValidationError(f'Kho "{to_location.warehouse}" đã ngừng hoạt động.')

    from_location = source_batch.location

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
        src_inv = locked_inv[from_location.warehouse_id]
        src_inv.qty_on_hand -= qty
        src_inv.save(update_fields=['qty_on_hand', 'updated_at'])
        record_movement(
            product=source_batch.product, warehouse=from_location.warehouse, batch=source_batch,
            movement_type=StockMovement.MovementType.TRANSFER_OUT, qty=-qty,
            reference=reference, actor=actor,
        )
        dst_inv = locked_inv[to_location.warehouse_id]
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

    Batch ``PENDING_RECEIPT`` mà ``WarehouseHandoff`` liên quan còn PENDING
    cũng bị chặn — hàng đó đang chờ kho Nhận/Từ chối qua
    ``accept_handoff()``/``reject_handoff()``, điều chuyển tay lúc này sẽ đổi
    ``status`` của batch nguồn (CLOSED/PARTIAL_USED) trong khi handoff vẫn
    trỏ vào nó, khiến accept/reject sau đó luôn fail vì batch không còn
    PENDING_RECEIPT — phiếu bàn giao kẹt vĩnh viễn. Vẫn cho phép điều chuyển
    khi handoff đã REJECTED với ``BACK_TO_QC`` (batch giữ nguyên
    PENDING_RECEIPT có chủ đích — xem ``reject_handoff()`` — để QC tự điều
    chuyển tay sau đó).

    Vị trí đích cũng bị chặn nếu thuộc kho STAGING/SCRAP — đối xứng với chặn
    nguồn STAGING ở trên: cả hai kho hệ thống này chỉ được nạp hàng qua đúng
    luồng QC (``start_qc``/``qc_pass``/``qc_fail``/``qc_partial_pass``/
    ``reject_handoff``), không qua điều chuyển tay — nếu không, batch mới sẽ
    mang ``status=ACTIVE`` trong Kho chờ/Kho phế, vi phạm invariant "hàng ở
    STAGING/SCRAP luôn phải qua QC quyết định trước".

    Khoá Inventory (nếu khác kho) TRƯỚC Batch (BUG-16, 2026-07-29, xem
    CLAUDE.md) — đọc unlocked trước để biết SKU/kho nguồn cần khoá Inventory
    nào, an toàn vì vị trí/sản phẩm của batch bất biến (cùng nguyên tắc dùng ở
    ``move_batch_qty``). ``move_batch_qty()`` gọi sau đó sẽ tự khoá lại đúng
    những dòng Inventory này (vô hại, cùng transaction) rồi mới khoá Batch —
    khoá Batch ở đây PHẢI đứng sau bước này, không được đứng trước.
    """
    ref = Batch.objects.select_related('product', 'location__warehouse').get(pk=batch.pk)
    if ref.location.warehouse_id != to_location.warehouse_id:
        lock_inventories(ref.product, [ref.location.warehouse, to_location.warehouse])

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
    if to_location.warehouse.warehouse_type != Warehouse.WarehouseType.MAIN:
        raise ValidationError(
            f'Không thể điều chuyển thủ công vào kho "{to_location.warehouse}" — '
            'Kho chờ/Kho phế chỉ được nạp hàng qua luồng QC.'
        )
    if not to_location.warehouse.is_active:
        raise ValidationError(f'Kho "{to_location.warehouse}" đã ngừng hoạt động.')
    if batch.status == Batch.Status.PENDING_RECEIPT:
        handoff = getattr(batch, 'handoff', None)
        if handoff is not None and handoff.status == WarehouseHandoff.Status.PENDING:
            raise ValidationError(
                'Không thể điều chuyển thủ công batch đang chờ kho xác nhận nhận hàng — '
                'phải xử lý qua phiếu bàn giao (Nhận/Từ chối) trước.'
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

    Khóa ``Batch`` trước rồi mới đến ``WarehouseHandoff`` — khớp thứ tự
    ``stocktake.services._consume_shortage_batches`` đã dùng (khóa Batch rồi
    mới khóa WarehouseHandoff PENDING trỏ vào nó khi đóng batch). Thứ tự khóa
    phải thống nhất toàn hệ thống cho cặp (Batch, WarehouseHandoff), nếu
    không kiểm kê và xử lý handoff chạy đồng thời trên cùng batch có thể
    deadlock (BUG-15, 2026-07-29). ``handoff.batch_id`` từ đối tượng chưa
    khóa vẫn tin được để chọn Batch cần khóa vì FK này không bao giờ bị gán
    lại sau khi tạo handoff.
    """
    batch = Batch.objects.select_for_update().get(pk=handoff.batch_id)
    handoff = WarehouseHandoff.objects.select_for_update().get(pk=handoff.pk)
    if handoff.status != WarehouseHandoff.Status.PENDING:
        raise ValidationError(f'Phiếu bàn giao này đã được xử lý ({handoff.get_status_display()}).')
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

    Khóa ``Batch`` trước rồi mới đến ``WarehouseHandoff`` — cùng lý do và thứ
    tự đã áp dụng ở ``accept_handoff`` (BUG-15). Nhánh ``TO_SCRAP`` còn đụng
    ``Inventory`` (qua ``move_batch_qty``, batch nguồn luôn ở kho MAIN khác
    Kho phế) nên phải khoá Inventory hai đầu TRƯỚC CẢ Batch — nếu không, batch
    bị khoá ở đây (dòng dưới) trước khi ``move_batch_qty`` kịp khoá Inventory,
    phá vỡ thứ tự chuẩn ``Inventory -> Batch -> WarehouseHandoff`` toàn hệ
    thống (BUG-16, 2026-07-29, xem CLAUDE.md).
    """
    if not reason:
        raise ValidationError('Bắt buộc nhập lý do từ chối.')
    if destination not in WarehouseHandoff.RejectDestination.values:
        raise ValidationError('Lựa chọn xử lý khi từ chối không hợp lệ.')

    if destination == WarehouseHandoff.RejectDestination.TO_SCRAP:
        ref = Batch.objects.select_related('product', 'location__warehouse').get(pk=handoff.batch_id)
        lock_inventories(ref.product, [ref.location.warehouse, get_scrap_warehouse()])

    batch = Batch.objects.select_for_update().get(pk=handoff.batch_id)
    handoff = WarehouseHandoff.objects.select_for_update().get(pk=handoff.pk)
    if handoff.status != WarehouseHandoff.Status.PENDING:
        raise ValidationError(f'Phiếu bàn giao này đã được xử lý ({handoff.get_status_display()}).')
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
