"""Transaction nghiệp vụ Stock Opname (BACKLOG mục 4) — PLANNING -> EXECUTION ->
RECONCILIATION -> ADJUSTMENT.

- ``create_session``: FR-SO-01, tạo phiếu PLANNING + snapshot danh sách SKU.
  Không chỉ định vị trí: ``qty_system`` lấy từ ``Inventory.qty_on_hand`` cấp
  kho (không có tồn theo vị trí ở mức Inventory). Có chỉ định ``location``
  (FR-SO-07): ``qty_system`` tính lại từ tổng ``Batch.qty_available`` của các
  batch đang nằm tại đúng vị trí đó — KHÔNG dùng ``Inventory.qty_on_hand``
  (cấp kho, sai một khi phiếu chỉ giới hạn 1 vị trí — bug fix 2026-07-27, xem
  CLAUDE.md) — nên danh sách SKU đưa vào phiếu cũng suy trực tiếp từ tập
  batch này thay vì lọc riêng.
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
  đúng (variance = 0) không tạo movement. Đồng thời đồng bộ luôn ``Batch``
  (bug fix 2026-07-27, xem CLAUDE.md): thừa thì tạo 1 Batch mới ACTIVE đại
  diện phần phát hiện thêm; thiếu thì trừ dần vào các batch ACTIVE/
  PARTIAL_USED hiện có (thứ tự FIFO) — không làm vậy thì Inventory và tổng
  Batch lệch nhau, FIFO/GIN không thấy đúng thực tế.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog
from inventory.models import Batch, Inventory, StockMovement
from inventory.services import record_movement
from warehouse.services import get_default_location

from .models import StocktakeItem, StocktakeSession


@transaction.atomic
def create_session(*, warehouse, created_by, location=None, notes='', actor=None, ip_address=None):
    """FR-SO-01: tạo phiếu kiểm kê PLANNING, snapshot danh sách SKU + qty_system.

    Danh sách SKU lấy từ các sản phẩm đang có Inventory tại ``warehouse``
    (sản phẩm chưa từng nhập kho này thì không có gì để đối soát, không đưa
    vào danh sách). Nếu chỉ định ``location`` (FR-SO-07): SKU nào đưa vào
    phiếu và ``qty_system`` của nó đều suy từ tổng ``Batch.qty_available`` tại
    đúng vị trí đó (không dùng ``Inventory.qty_on_hand`` cấp kho — bug fix
    2026-07-27, xem ``stocktake.services`` docstring/CLAUDE.md: dùng tồn cả
    kho làm qty_system cho phiếu chỉ đếm 1 vị trí sẽ ra chênh lệch giả khi kho
    có nhiều vị trí, khiến ``apply_adjustment`` trừ/cộng nhầm cả phần chưa
    đếm).
    """
    if location is not None and location.warehouse_id != warehouse.id:
        raise ValidationError('Vị trí này không thuộc kho đã chọn.')

    inventories = Inventory.objects.select_related('product').filter(
        warehouse=warehouse, product__is_active=True,
    )
    location_qty_by_product = None
    if location is not None:
        location_qty_by_product = dict(
            Batch.objects.filter(location=location)
            .values('product_id')
            .annotate(total=Sum(F('qty_received') - F('qty_used')))
            .values_list('product_id', 'total')
        )
        inventories = inventories.filter(product_id__in=location_qty_by_product)
    inventories = list(inventories)
    if not inventories:
        raise ValidationError('Không có SKU nào để kiểm kê (kho/vị trí chưa có tồn kho).')

    session = StocktakeSession.objects.create(
        warehouse=warehouse, location=location, notes=notes, created_by=created_by,
    )
    StocktakeItem.objects.bulk_create([
        StocktakeItem(
            session=session, product=inv.product,
            qty_system=(
                location_qty_by_product[inv.product_id]
                if location_qty_by_product is not None else inv.qty_on_hand
            ),
        )
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
        if variance > 0:
            _create_surplus_batch(session=session, item=item, qty=variance)
        else:
            _consume_shortage_batches(session=session, item=item, qty=-variance)
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


def _create_surplus_batch(*, session, item, qty):
    """Chênh lệch dương (thừa, bug fix 2026-07-27, xem CLAUDE.md): tạo 1 Batch
    mới ``ACTIVE`` đại diện phần tồn phát hiện thêm khi kiểm kê, tại vị trí
    phiếu giới hạn (FR-SO-07) hoặc vị trí mặc định của kho nếu kiểm toàn kho.
    Không có GRN nguồn nên ``grn_item``/``mfg_date``/``exp_date`` để trống;
    ``supplier`` không có gì để tra ngoài lô gần nhất từng nhập của đúng SKU
    này (best-effort — xem ``_infer_supplier``). Thiếu bước này thì
    ``Inventory.qty_on_hand`` tăng nhưng FIFO/GIN không thấy lô nào để xuất
    phần thừa.
    """
    location = session.location or get_default_location(session.warehouse)
    Batch.objects.create(
        product=item.product, batch_code=f'ADJ-{session.so_no}-{item.product.product_code}',
        supplier=_infer_supplier(item.product), location=location,
        qty_received=qty, status=Batch.Status.ACTIVE,
    )


def _infer_supplier(product):
    """Không có nghiệp vụ nào cho biết NCC của phần tồn thừa phát hiện qua
    kiểm kê — suy đoán tốt nhất là NCC của lô gần nhất từng nhập cho đúng SKU
    này (mọi kho). Nếu SKU chưa từng có lô nào (Inventory tồn tại nhưng không
    qua Batch — chỉ xảy ra với dữ liệu dựng tay/test), không có cơ sở suy
    đoán — báo lỗi rõ ràng thay vì đoán bừa, yêu cầu xử lý thủ công (tạo GRN/
    Batch trước).
    """
    last_batch = Batch.objects.filter(product=product).order_by('-created_at').first()
    if last_batch is None:
        raise ValidationError(
            f'SKU {product.product_code} chưa từng có lô hàng nào — không thể tự suy ra nhà cung cấp '
            f'để tạo lô mới cho phần chênh lệch thừa. Tạo GRN/Batch cho SKU này trước rồi mới áp dụng '
            f'điều chỉnh.'
        )
    return last_batch.supplier


def _consume_shortage_batches(*, session, item, qty):
    """Chênh lệch âm (thiếu, bug fix 2026-07-27, xem CLAUDE.md): trừ dần
    ``qty`` vào các batch ``ACTIVE``/``PARTIAL_USED`` hiện có, thứ tự FIFO
    (``exp_date`` rồi ``created_at`` — cùng convention
    ``inventory.services.suggest_fifo_batches``), giới hạn đúng vị trí nếu
    phiếu chỉ kiểm 1 vị trí (FR-SO-07), cả kho nếu kiểm toàn kho. Không đủ
    batch để phủ hết phần thiếu thì báo lỗi rõ ràng thay vì âm thầm để
    Inventory lệch khỏi tổng Batch — nếu không làm vậy, Inventory đã giảm
    nhưng batch vẫn báo đủ hàng để FIFO xuất tiếp.
    """
    batches = Batch.objects.select_for_update().filter(
        product=item.product, status__in=[Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED],
        **({'location': session.location} if session.location_id else {'location__warehouse': session.warehouse}),
    ).order_by('exp_date', 'created_at')

    remaining = qty
    for batch in batches:
        if remaining <= 0:
            break
        available = batch.qty_available
        if available <= 0:
            continue
        take = min(available, remaining)
        batch.qty_used += take
        batch.status = Batch.Status.CLOSED if batch.qty_available <= 0 else Batch.Status.PARTIAL_USED
        batch.save(update_fields=['qty_used', 'status'])
        remaining -= take

    if remaining > 0:
        raise ValidationError(
            f'SKU {item.product.product_code}: không đủ lô hàng (ACTIVE/PARTIAL_USED) tại '
            f'{"vị trí " + str(session.location) if session.location_id else session.warehouse} '
            f'để trừ đúng phần thiếu {qty} (còn thiếu {remaining} không có lô nào che phủ).'
        )
