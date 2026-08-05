"""Transaction nghiệp vụ GRN↔QC (BACKLOG mục 2c) — PASS/FAIL/PARTIAL_PASS.

Mỗi hàm là MỘT transaction atomic (``@transaction.atomic``), all-or-nothing,
đúng yêu cầu "Inventory Update Triggers" của mục 2c. Từ khi có Kho chờ (kế
hoạch phân loại kho), toàn bộ hàng "đã nhận nhưng chưa QC" nằm thật trong
Batch + Inventory tại Kho chờ (STAGING) — không còn "biến mất" khỏi hệ thống
giữa lúc xác nhận Qty thực nhận và lúc có kết quả QC:

- ``start_qc``: DRAFT/PENDING_QC -> QC_IN_PROGRESS, tạo ``QcInspection`` +
  1 Batch ACTIVE/item tại Kho chờ (RECEIPT thật — hàng đã nhận vật lý).
- ``qc_pass``: tiêu thụ batch Kho chờ, tách toàn bộ qty_received sang Batch
  ``PENDING_RECEIPT`` tại kho MAIN (``move_batch_qty`` — TRANSFER_OUT/IN,
  không phải RECEIPT nữa vì hàng đã có trong hệ thống từ ``start_qc``), GRN ->
  RECEIVED, tạo ``WarehouseHandoff`` chờ NV kho xác nhận nhận hàng (Phase D —
  xem ``inventory.services.create_handoff``/``accept_handoff``/``reject_handoff``)
  trước khi batch thật sự ACTIVE (khả dụng FIFO).
- ``qc_fail``: tiêu thụ batch Kho chờ, tách toàn bộ qty_received sang Batch
  QUARANTINE tại Kho phế (SCRAP), GRN -> REJECTED, vẫn tạo ``GrnReturn`` —
  KHÔNG qua bước bàn giao (hàng fail không cần NV kho xác nhận nhận).
- ``qc_partial_pass``: mỗi item nhận ``qty_pass`` riêng (0 <= qty_pass <=
  qty_received) -> tách batch Kho chờ làm 2 (``PENDING_RECEIPT`` phần pass tại
  kho MAIN, kèm ``WarehouseHandoff``, + QUARANTINE phần fail tại Kho phế).
- ``cancel_qc_inspection``: gọi từ ``receiving.services.cancel_grn`` khi GRN bị
  hủy lúc đang QC_IN_PROGRESS — đảo batch/Inventory Kho chờ do ``start_qc`` tạo
  (khác PASS/FAIL/PARTIAL_PASS, batch ở đây CHƯA có quyết định QC nên đảo được).

Mọi transition đều ghi ``AuditLog`` qua ``accounts.audit.log_action`` (hạ
tầng có sẵn từ Phase 1) — một dòng log cho mỗi transaction nghiệp vụ (không
log riêng từng Batch/GrnItem bị đụng tới bên trong).
"""
import datetime
import math

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog
from catalog.models import Product
from inventory.models import Batch, Inventory, StockMovement
from inventory.services import create_handoff, lock_inventories, move_batch_qty, record_movement
from receiving.models import Grn, GrnItem, GrnReturn
from warehouse.models import Warehouse
from warehouse.services import get_default_location, get_scrap_warehouse, get_staging_warehouse

from .models import QcInspection


def suggested_sample_qty(product, qty_received):
    """QC Criteria & Sampling (BACKLOG mục 2b): cỡ mẫu gợi ý theo cấu hình
    ``Product.qc_sampling_method``/``qc_sampling_value`` — chỉ gợi ý hiển thị,
    KHÔNG chặn quyết định PASS/FAIL/PARTIAL (mục 2c không yêu cầu đủ mẫu mới
    được quyết định).

    PERCENT: làm tròn lên ``qty_received * value / 100``, tối thiểu 1 nếu có
    hàng nhận. FIXED: ``value``, nhưng không vượt quá ``qty_received``, tối
    thiểu 1 (đồng nhất với PERCENT — ``qc_sampling_value=0`` không nên gợi ý
    cỡ mẫu bằng 0 khi thực tế có hàng nhận).
    """
    if qty_received <= 0:
        return 0
    if product.qc_sampling_method == Product.SamplingMethod.FIXED:
        return max(1, min(product.qc_sampling_value, qty_received))
    return max(1, min(math.ceil(qty_received * product.qc_sampling_value / 100), qty_received))


def overdue_inspections(sla_hours=24):
    """QC duration tracking / SLA alert (mục 2b): inspection còn ``PENDING_QC``
    quá ``sla_hours`` kể từ ``started_at`` — tính on-the-fly khi load trang
    (⏸️ theo CLAUDE.md, chưa cần Celery).
    """
    threshold = timezone.now() - datetime.timedelta(hours=sla_hours)
    return QcInspection.objects.filter(
        status=QcInspection.Result.PENDING_QC, started_at__lt=threshold,
    ).select_related('grn').order_by('started_at')


def _batch_code(grn_item, suffix=''):
    return f'{grn_item.label_code}{suffix}'


def _credit_inventory(product, warehouse, qty, batch=None, reference='', actor=None):
    """Cộng ``qty_on_hand`` + ghi ``StockMovement`` RECEIPT (FR-INV-03)."""
    inv, _ = Inventory.objects.select_for_update().get_or_create(product=product, warehouse=warehouse)
    inv.qty_on_hand += qty
    inv.save(update_fields=['qty_on_hand', 'updated_at'])
    record_movement(
        product=product, warehouse=warehouse, batch=batch, reference=reference, actor=actor,
        movement_type=StockMovement.MovementType.RECEIPT, qty=qty,
    )
    return inv


def _require_criteria_items(inspection, lock=False):
    """Chặn PASS/FAIL/PARTIAL khi phiếu QC chưa có dòng kết quả tiêu chuẩn nào
    (Wave 2 — AC-QC-CRIT-01). Gọi 2 lần trong mỗi hàm ``qc_pass``/``qc_fail``/
    ``qc_partial_pass``: TRƯỚC ``_lock_pending_inspection`` (``lock=False``,
    fail nhanh, không tốn khoá khi rõ ràng chưa có item) và NGAY SAU
    (``lock=True``).

    ``_lock_pending_inspection`` chỉ khoá ``Grn``/``QcInspection``, không khoá
    được bảng ``QcInspectionItem`` — form riêng "Lưu kết quả từng tiêu chuẩn"
    (``QcInspectionItemFormSet``, ``quality.views.qc_result``) xoá hết dòng
    criteria qua 1 request/transaction độc lập vẫn commit được giữa 2 lần gọi
    này, nên phải kiểm lại NGAY SAU khi đã khoá Grn/QcInspection, TRƯỚC khi
    đụng Batch/Inventory — cùng dạng TOCTOU đã chốt cho các view ``*_update``
    DRAFT-only, xem CLAUDE.md.

    Residual sau lần vá đầu (re-review): lần gọi thứ 2 ban đầu chỉ ĐỌC LẠI
    (``exists()``, không khoá) — đóng đúng khe hở "xoá TRƯỚC lần đọc lại" (test
    ``QcCriteriaGateToctouTest``), nhưng KHÔNG khoá được ``QcInspectionItem``
    nên vẫn còn khe hở "xoá SAU lần đọc lại, TRƯỚC khi Batch/Inventory được
    tạo" — 1 transaction khác xoá đúng lúc này vẫn commit được vì không có gì
    chặn nó. ``lock=True`` dùng ``select_for_update()`` thật trên chính các
    dòng ``QcInspectionItem`` — một DELETE đồng thời trên đúng những dòng đó
    giờ phải CHỜ tới khi transaction QC này commit/rollback, không còn xen
    được vào giữa lần kiểm và bước tạo Batch/Inventory nữa (chỉ kiểm chứng
    được bằng ``TransactionTestCase`` + 2 thread/2 kết nối DB thật — xem
    ``QcCriteriaItemPostLockRaceTest`` — vì trong 1 transaction/1 luồng duy
    nhất không có "khe hở" thật giữa 2 câu lệnh SQL kế tiếp để side_effect mô
    phỏng).
    """
    qs = inspection.items.all()
    if lock:
        exists = bool(list(qs.select_for_update()))
    else:
        exists = qs.exists()
    if not exists:
        raise ValidationError(
            'Phải nhập ít nhất 1 dòng kết quả kiểm tra tiêu chuẩn trước khi ghi nhận kết quả QC.'
        )


def _lock_pending_inspection(inspection):
    """Khoá + tải lại ``Grn`` rồi ``QcInspection`` mới nhất (theo đúng thứ tự
    ``Grn -> QcInspection`` mà ``receiving.services.cancel_grn`` /
    ``cancel_qc_inspection`` dùng) TRƯỚC KHI khoá bất kỳ ``Inventory``/``Batch``
    nào trong ``qc_pass``/``qc_fail``/``qc_partial_pass`` (BUG-18, 2026-07-30,
    xem CLAUDE.md).

    Trước fix này, 3 hàm trên chỉ gọi ``_require_pending_inspection`` — kiểm
    tra ``inspection``/``inspection.grn`` ĐANG CÓ TRONG BỘ NHỚ (không
    ``select_for_update``, có thể đã stale), rồi khoá Inventory/Batch trước,
    chỉ thật sự chạm ``Grn``/``QcInspection`` qua ``.save()`` KHÔNG khoá trước
    ở CUỐI transaction. Đó là thứ tự khoá NGƯỢC với ``cancel_grn`` (khoá
    Grn/QcInspection trước, rồi mới Inventory/Batch) — 2 giao dịch chạm cùng
    GRN đồng thời (huỷ GRN khi đang QC_IN_PROGRESS + quyết định QC) có thể tạo
    vòng chờ chéo thật (deadlock Postgres), và bản thân "đang có trong bộ nhớ"
    cũng là một TOCTOU: request khác có thể đã đổi trạng thái giữa lúc view
    load ``inspection`` và lúc service này chạy.

    Khoá SỚM, trước Inventory/Batch, để 2 đường luôn tranh chấp đúng 1 tài
    nguyên đầu tiên theo cùng thứ tự — bên thua chỉ phải chờ, không bao giờ
    deadlock — đồng thời tự khép luôn TOCTOU vì trạng thái được đọc lại ngay
    dưới khoá.
    """
    grn = Grn.objects.select_for_update().get(pk=inspection.grn_id)
    inspection = QcInspection.objects.select_for_update().get(pk=inspection.pk)
    if inspection.status != QcInspection.Result.PENDING_QC:
        raise ValidationError('QC inspection này đã có kết quả, không thể ghi lại.')
    if grn.status != Grn.Status.QC_IN_PROGRESS:
        raise ValidationError('GRN không ở trạng thái QC_IN_PROGRESS.')
    return grn, inspection


def _get_staging_batch(grn_item):
    """Batch đang nằm ở Kho chờ ứng với ``grn_item`` (tạo bởi ``start_qc``).

    Lọc theo ``grn_item`` (lineage FK) chứ không theo product/location vì 1
    GRN có thể có nhiều item cùng product — chỉ ``grn_item`` mới xác định
    đúng batch cần tiêu thụ.
    """
    batch = Batch.objects.select_for_update().filter(
        grn_item=grn_item, status=Batch.Status.ACTIVE,
        location__warehouse__warehouse_type=Warehouse.WarehouseType.STAGING,
    ).first()
    if batch is None:
        raise ValidationError(
            f'Không tìm thấy batch tại Kho chờ cho "{grn_item}" — có thể GRN chưa qua bước '
            'xác nhận Qty thực nhận, hoặc đã được xử lý QC trước đó.'
        )
    return batch


@transaction.atomic
def start_qc(grn, inspector, actor=None, ip_address=None):
    """DRAFT/PENDING_QC -> QC_IN_PROGRESS: tạo ``QcInspection`` + đưa từng item
    vào Kho chờ (1 Batch ACTIVE/item tại vị trí mặc định, Inventory Kho chờ
    tăng tương ứng, ghi RECEIPT — hàng đã nhận vật lý, chỉ chưa qua QC).

    Duyệt item theo ``product_id`` tăng dần, không theo thứ tự chèn mặc định
    (BUG-17, 2026-07-29, xem CLAUDE.md) — mỗi item khoá 1 dòng Inventory Kho
    chờ (``_credit_inventory``) tuần tự; 1 GRN nhiều SKU duyệt sai thứ tự
    chuẩn toàn hệ thống có thể khoá ngược chiều với 1 giao dịch nhiều-SKU
    khác chạm cùng cặp SKU đó và deadlock thật.
    """
    grn = Grn.objects.select_for_update().get(pk=grn.pk)
    if grn.status not in (Grn.Status.DRAFT, Grn.Status.PENDING_QC):
        raise ValidationError(f'Không thể bắt đầu QC khi GRN đang ở trạng thái {grn.status}.')

    staging_warehouse = get_staging_warehouse()
    staging_location = get_default_location(staging_warehouse)

    inspection = QcInspection.objects.create(grn=grn, inspector=inspector, started_at=timezone.now())

    for item in grn.items.select_for_update().order_by('product_id', 'pk'):
        if item.qty_received <= 0:
            continue
        batch = Batch.objects.create(
            product=item.product, batch_code=_batch_code(item, '-STG'), supplier=grn.supplier,
            location=staging_location, grn_item=item, mfg_date=item.mfg_date, exp_date=item.exp_date,
            qty_received=item.qty_received, status=Batch.Status.ACTIVE,
        )
        _credit_inventory(
            item.product, staging_warehouse, item.qty_received,
            batch=batch, reference=grn.grn_no, actor=actor,
        )

    grn.status = Grn.Status.QC_IN_PROGRESS
    grn.save(update_fields=['status'])

    log_action(
        actor, AuditLog.Action.UPDATE, target=grn,
        description=f'Submit to QC: {grn.grn_no} -> QC_IN_PROGRESS ({inspection.qc_no}), '
                    f'{grn.items.count()} item(s) vào Kho chờ.',
        ip_address=ip_address,
    )
    return inspection


@transaction.atomic
def cancel_qc_inspection(grn, actor=None, ip_address=None):
    """Hủy QC đang ``PENDING_QC`` khi GRN bị hủy giữa chừng lúc QC_IN_PROGRESS
    (gọi từ ``receiving.services.cancel_grn``) — đảo ngược batch/Inventory Kho
    chờ do ``start_qc`` tạo ra (đóng batch, trừ lại ``qty_on_hand``), rồi đánh
    dấu inspection CANCELLED.

    Khác với ranh giới "QC override chỉ annotation, không đảo Batch/Inventory
    đã tạo bởi qc_pass/fail/partial_pass" (xem ``QcInspection.override_note``):
    ở đây QC CHƯA có quyết định (batch vẫn còn nguyên ở Kho chờ, chưa qua
    ``qc_pass``/``qc_fail``/``qc_partial_pass``), nên đảo được — không phải
    undo một giao dịch nghiệp vụ đã hoàn tất.

    Khoá Inventory (mọi SKU liên quan tại Kho chờ, thứ tự ổn định theo
    ``product_id``) TRƯỚC các batch Kho chờ — chuẩn hoá thứ tự
    ``Inventory -> Batch -> WarehouseHandoff`` toàn hệ thống (BUG-16,
    2026-07-29, xem CLAUDE.md). Đọc danh sách batch UNLOCKED trước chỉ để xác
    định SKU nào cần khoá Inventory; batch thật sự được khoá ở bước dưới.
    """
    inspection = grn.qc_inspections.select_for_update().filter(
        status=QcInspection.Result.PENDING_QC).first()
    if inspection is None:
        return None

    staging_batches_ref = list(Batch.objects.filter(
        grn_item__grn=grn, status=Batch.Status.ACTIVE,
        location__warehouse__warehouse_type=Warehouse.WarehouseType.STAGING,
    ).select_related('product', 'location__warehouse'))
    products_by_id = {b.product_id: (b.product, b.location.warehouse) for b in staging_batches_ref}
    for product, staging_warehouse in sorted(products_by_id.values(), key=lambda pair: pair[0].id):
        lock_inventories(product, [staging_warehouse])

    staging_batches = list(Batch.objects.select_for_update().filter(
        grn_item__grn=grn, status=Batch.Status.ACTIVE,
        location__warehouse__warehouse_type=Warehouse.WarehouseType.STAGING,
    ))
    for batch in staging_batches:
        qty = batch.qty_available
        if qty > 0:
            inv = Inventory.objects.select_for_update().get(
                product=batch.product, warehouse=batch.location.warehouse)
            inv.qty_on_hand -= qty
            inv.save(update_fields=['qty_on_hand', 'updated_at'])
            record_movement(
                product=batch.product, warehouse=batch.location.warehouse, batch=batch,
                reference=grn.grn_no, actor=actor,
                movement_type=StockMovement.MovementType.ADJUSTMENT, qty=-qty,
            )
        batch.qty_used = batch.qty_received
        batch.status = Batch.Status.CLOSED
        batch.save(update_fields=['qty_used', 'status'])

    inspection.status = QcInspection.Result.CANCELLED
    inspection.completed_at = timezone.now()
    inspection.save(update_fields=['status', 'completed_at'])

    log_action(
        actor, AuditLog.Action.CANCEL, target=inspection,
        description=(
            f'Hủy QC {inspection.qc_no} do GRN {grn.grn_no} bị hủy — đóng '
            f'{len(staging_batches)} batch Kho chờ, trừ lại tồn tương ứng.'
        ),
        ip_address=ip_address,
    )
    return inspection


@transaction.atomic
def qc_pass(inspection, actor=None, location=None, ip_address=None, assigned_to=None):
    """QC PASS: tiêu thụ batch Kho chờ, tách toàn bộ qty_received sang Batch
    ``PENDING_RECEIPT`` tại ``location`` (phải thuộc kho loại MAIN), GRN ->
    RECEIVED, tạo ``WarehouseHandoff``/item chờ NV kho xác nhận nhận hàng
    (Phase D — ``assigned_to`` tuỳ chọn, để trống thì báo cả kho đích).

    Khoá ``Grn`` rồi ``QcInspection`` (``_lock_pending_inspection``) TRƯỚC CẢ
    Inventory/Batch — chuẩn hoá thứ tự ``Grn -> QcInspection -> Inventory ->
    Batch -> WarehouseHandoff`` toàn hệ thống (BUG-18, 2026-07-30, xem
    CLAUDE.md), khớp thứ tự ``cancel_grn``/``cancel_qc_inspection`` đã dùng —
    tránh deadlock khi huỷ GRN và quyết định QC chạy đồng thời trên cùng GRN.

    Khoá Inventory (Kho chờ + kho đích) TRƯỚC batch Kho chờ (``_get_staging_batch``
    khoá Batch) — chuẩn hoá thứ tự ``Inventory -> Batch -> WarehouseHandoff``
    toàn hệ thống (BUG-16, 2026-07-29, xem CLAUDE.md), tránh deadlock với
    ``stocktake.services.apply_adjustment`` (khoá Inventory rồi mới Batch) khi
    2 nghiệp vụ chạm cùng batch/SKU đồng thời.

    Duyệt item theo ``product_id`` tăng dần, không theo thứ tự chèn mặc định
    (BUG-17, 2026-07-29, xem CLAUDE.md) — cùng lý do đã nêu ở ``start_qc``:
    1 GRN nhiều SKU khoá Inventory từng dòng tuần tự, duyệt sai thứ tự chuẩn
    toàn hệ thống có thể khoá ngược chiều với giao dịch nhiều-SKU khác.
    """
    if location.warehouse.warehouse_type != Warehouse.WarehouseType.MAIN:
        raise ValidationError('Vị trí đích PASS phải thuộc kho loại "Kho thành phẩm".')
    if not location.warehouse.is_active:
        raise ValidationError(f'Kho "{location.warehouse}" đã ngừng hoạt động.')
    _require_criteria_items(inspection)
    grn, inspection = _lock_pending_inspection(inspection)
    _require_criteria_items(inspection, lock=True)
    staging_warehouse = get_staging_warehouse()

    for item in grn.items.select_for_update().order_by('product_id', 'pk'):
        item.qty_pass = item.qty_received
        item.status = GrnItem.Status.RECEIVED
        item.save(update_fields=['qty_pass', 'status'])
        if item.qty_received <= 0:
            continue
        lock_inventories(item.product, [staging_warehouse, location.warehouse])
        staging_batch = _get_staging_batch(item)
        new_batch = move_batch_qty(
            source_batch=staging_batch, qty=item.qty_received, to_location=location,
            new_batch_code=_batch_code(item), new_status=Batch.Status.PENDING_RECEIPT,
            actor=actor, reference=grn.grn_no,
        )
        create_handoff(
            batch=new_batch, qc_inspection=inspection, destination_warehouse=location.warehouse,
            assigned_to=assigned_to,
        )

    grn.status = Grn.Status.RECEIVED
    grn.save(update_fields=['status'])
    inspection.status = QcInspection.Result.PASS
    inspection.completed_at = timezone.now()
    inspection.save(update_fields=['status', 'completed_at'])

    log_action(
        actor, AuditLog.Action.APPROVE, target=grn,
        description=f'QC PASS: {grn.grn_no} -> RECEIVED, chuyển Kho chờ -> {location.warehouse.code} '
                    f'(chờ kho xác nhận nhận hàng).',
        ip_address=ip_address,
    )
    return grn


@transaction.atomic
def qc_fail(inspection, actor=None, reason='QC Fail', ip_address=None):
    """QC FAIL: tiêu thụ batch Kho chờ, tách toàn bộ qty_received sang Batch
    QUARANTINE tại Kho phế, GRN -> REJECTED, vẫn tạo ``GrnReturn``.

    Khoá ``Grn`` rồi ``QcInspection`` (``_lock_pending_inspection``) TRƯỚC CẢ
    Inventory/Batch — cùng lý do/thứ tự đã áp dụng ở ``qc_pass`` (BUG-18,
    2026-07-30, xem CLAUDE.md).

    Khoá Inventory (Kho chờ + Kho phế) TRƯỚC batch Kho chờ — cùng lý do/thứ
    tự đã áp dụng ở ``qc_pass`` (BUG-16). Duyệt item theo ``product_id`` tăng
    dần — cùng lý do đã nêu ở ``qc_pass``/``start_qc`` (BUG-17, 2026-07-29,
    xem CLAUDE.md).
    """
    _require_criteria_items(inspection)
    grn, inspection = _lock_pending_inspection(inspection)
    _require_criteria_items(inspection, lock=True)
    staging_warehouse = get_staging_warehouse()
    scrap_warehouse = get_scrap_warehouse()
    scrap_location = get_default_location(scrap_warehouse)

    for item in grn.items.select_for_update().order_by('product_id', 'pk'):
        item.status = GrnItem.Status.REJECTED
        item.qty_pass = 0
        item.save(update_fields=['status', 'qty_pass'])
        if item.qty_received <= 0:
            continue
        lock_inventories(item.product, [staging_warehouse, scrap_warehouse])
        staging_batch = _get_staging_batch(item)
        move_batch_qty(
            source_batch=staging_batch, qty=item.qty_received, to_location=scrap_location,
            new_batch_code=_batch_code(item, '-SCRAP'), new_status=Batch.Status.QUARANTINE,
            actor=actor, reference=grn.grn_no,
        )

    grn.status = Grn.Status.REJECTED
    grn.save(update_fields=['status'])
    inspection.status = QcInspection.Result.FAIL
    inspection.completed_at = timezone.now()
    inspection.save(update_fields=['status', 'completed_at'])

    ret = GrnReturn.objects.create(grn=grn, reason=reason)

    log_action(
        actor, AuditLog.Action.REJECT, target=grn,
        description=f'QC FAIL: {grn.grn_no} -> REJECTED, chuyển Kho chờ -> Kho phế, tạo {ret}.',
        reason=reason, ip_address=ip_address,
    )
    return ret


@transaction.atomic
def qc_partial_pass(inspection, item_results, actor=None, location=None, ip_address=None, assigned_to=None):
    """PARTIAL_PASS: tiêu thụ batch Kho chờ, tách mỗi item thành Batch
    ``PENDING_RECEIPT`` (phần pass, tại ``location`` thuộc kho MAIN, kèm
    ``WarehouseHandoff`` chờ NV kho xác nhận — Phase D) + Batch QUARANTINE
    (phần fail, tại Kho phế, không qua bàn giao).

    ``item_results``: ``{grn_item_id: qty_pass}`` — bắt buộc có đủ mọi item
    của GRN, ``0 <= qty_pass <= qty_received``.

    Khoá ``Grn`` rồi ``QcInspection`` (``_lock_pending_inspection``) TRƯỚC CẢ
    Inventory/Batch — cùng lý do/thứ tự đã áp dụng ở ``qc_pass``/``qc_fail``
    (BUG-18, 2026-07-30, xem CLAUDE.md).

    Khoá Inventory (Kho chờ + đích thực sự dùng đến — MAIN nếu ``qty_pass>0``,
    Kho phế nếu ``qty_fail>0``) TRƯỚC batch Kho chờ — cùng lý do/thứ tự đã áp
    dụng ở ``qc_pass``/``qc_fail`` (BUG-16). Duyệt item theo ``product_id``
    tăng dần — cùng lý do đã nêu ở ``qc_pass``/``start_qc`` (BUG-17,
    2026-07-29, xem CLAUDE.md).
    """
    if location.warehouse.warehouse_type != Warehouse.WarehouseType.MAIN:
        raise ValidationError('Vị trí đích PASS phải thuộc kho loại "Kho thành phẩm".')
    if not location.warehouse.is_active:
        raise ValidationError(f'Kho "{location.warehouse}" đã ngừng hoạt động.')
    _require_criteria_items(inspection)
    grn, inspection = _lock_pending_inspection(inspection)
    _require_criteria_items(inspection, lock=True)
    items = list(grn.items.select_for_update().order_by('product_id', 'pk'))

    missing = {item.pk for item in items} - set(item_results)
    if missing:
        raise ValidationError(f'Thiếu kết quả QC cho {len(missing)} item.')
    if sum(item_results.values()) == 0:
        raise ValidationError(
            'Tất cả item đều có qty_pass = 0 — dùng hành động "Fail" thay vì "Partial Pass".'
        )

    staging_warehouse = get_staging_warehouse()
    scrap_warehouse = get_scrap_warehouse()
    scrap_location = get_default_location(scrap_warehouse)

    for item in items:
        qty_pass = item_results[item.pk]
        if not (0 <= qty_pass <= item.qty_received):
            raise ValidationError(f'qty_pass không hợp lệ cho item {item}.')
        qty_fail = item.qty_received - qty_pass
        has_both = qty_pass > 0 and qty_fail > 0

        if item.qty_received > 0:
            warehouses_needed = [staging_warehouse]
            if qty_pass > 0:
                warehouses_needed.append(location.warehouse)
            if qty_fail > 0:
                warehouses_needed.append(scrap_warehouse)
            lock_inventories(item.product, warehouses_needed)
            staging_batch = _get_staging_batch(item)
            if qty_pass > 0:
                pass_batch = move_batch_qty(
                    source_batch=staging_batch, qty=qty_pass, to_location=location,
                    new_batch_code=_batch_code(item, '-A' if has_both else ''),
                    new_status=Batch.Status.PENDING_RECEIPT, actor=actor, reference=grn.grn_no,
                )
                create_handoff(
                    batch=pass_batch, qc_inspection=inspection, destination_warehouse=location.warehouse,
                    assigned_to=assigned_to,
                )
            if qty_fail > 0:
                move_batch_qty(
                    source_batch=staging_batch, qty=qty_fail, to_location=scrap_location,
                    new_batch_code=_batch_code(item, '-Q' if has_both else ''),
                    new_status=Batch.Status.QUARANTINE, actor=actor, reference=grn.grn_no,
                )

        item.qty_pass = qty_pass
        item.status = (
            GrnItem.Status.RECEIVED if qty_fail == 0
            else GrnItem.Status.REJECTED if qty_pass == 0
            else GrnItem.Status.PARTIAL_RECEIVED
        )
        item.save(update_fields=['qty_pass', 'status'])

    grn.status = Grn.Status.RECEIVED
    grn.save(update_fields=['status'])
    inspection.status = QcInspection.Result.PARTIAL_PASS
    inspection.completed_at = timezone.now()
    inspection.save(update_fields=['status', 'completed_at'])

    log_action(
        actor, AuditLog.Action.APPROVE, target=grn,
        description=f'QC PARTIAL_PASS: {grn.grn_no} -> RECEIVED, Kho chờ tách MAIN (chờ kho xác nhận)+SCRAP.',
        ip_address=ip_address,
    )
    return grn
