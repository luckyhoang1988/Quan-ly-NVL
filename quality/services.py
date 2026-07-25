"""Transaction nghiệp vụ GRN↔QC (BACKLOG mục 2c) — PASS/FAIL/PARTIAL_PASS.

Mỗi hàm là MỘT transaction atomic (``@transaction.atomic``), all-or-nothing,
đúng yêu cầu "Inventory Update Triggers" của mục 2c. Từ khi có Kho chờ (kế
hoạch phân loại kho), toàn bộ hàng "đã nhận nhưng chưa QC" nằm thật trong
Batch + Inventory tại Kho chờ (STAGING) — không còn "biến mất" khỏi hệ thống
giữa lúc xác nhận Qty thực nhận và lúc có kết quả QC:

- ``start_qc``: DRAFT/PENDING_QC -> QC_IN_PROGRESS, tạo ``QcInspection`` +
  1 Batch ACTIVE/item tại Kho chờ (RECEIPT thật — hàng đã nhận vật lý).
- ``qc_pass``: tiêu thụ batch Kho chờ, tách toàn bộ qty_received sang Batch
  ACTIVE tại kho MAIN (``move_batch_qty`` — TRANSFER_OUT/IN, không phải
  RECEIPT nữa vì hàng đã có trong hệ thống từ ``start_qc``), GRN -> RECEIVED.
- ``qc_fail``: tiêu thụ batch Kho chờ, tách toàn bộ qty_received sang Batch
  QUARANTINE tại Kho phế (SCRAP), GRN -> REJECTED, vẫn tạo ``GrnReturn``.
- ``qc_partial_pass``: mỗi item nhận ``qty_pass`` riêng (0 <= qty_pass <=
  qty_received) -> tách batch Kho chờ làm 2 (ACTIVE phần pass tại kho MAIN +
  QUARANTINE phần fail tại Kho phế).

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
from inventory.services import move_batch_qty, record_movement
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
    hàng nhận. FIXED: ``value``, nhưng không vượt quá ``qty_received``.
    """
    if qty_received <= 0:
        return 0
    if product.qc_sampling_method == Product.SamplingMethod.FIXED:
        return min(product.qc_sampling_value, qty_received)
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


def _require_pending_inspection(inspection):
    if inspection.status != QcInspection.Result.PENDING_QC:
        raise ValidationError('QC inspection này đã có kết quả, không thể ghi lại.')
    if inspection.grn.status != Grn.Status.QC_IN_PROGRESS:
        raise ValidationError('GRN không ở trạng thái QC_IN_PROGRESS.')


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
    """
    grn = Grn.objects.select_for_update().get(pk=grn.pk)
    if grn.status not in (Grn.Status.DRAFT, Grn.Status.PENDING_QC):
        raise ValidationError(f'Không thể bắt đầu QC khi GRN đang ở trạng thái {grn.status}.')

    staging_warehouse = get_staging_warehouse()
    staging_location = get_default_location(staging_warehouse)

    inspection = QcInspection.objects.create(grn=grn, inspector=inspector, started_at=timezone.now())

    for item in grn.items.select_for_update():
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
def qc_pass(inspection, actor=None, location=None, ip_address=None):
    """QC PASS: tiêu thụ batch Kho chờ, tách toàn bộ qty_received sang Batch
    ACTIVE tại ``location`` (phải thuộc kho loại MAIN), GRN -> RECEIVED.
    """
    _require_pending_inspection(inspection)
    if location.warehouse.warehouse_type != Warehouse.WarehouseType.MAIN:
        raise ValidationError('Vị trí đích PASS phải thuộc kho loại "Kho thành phẩm".')
    grn = inspection.grn

    for item in grn.items.select_for_update():
        item.qty_pass = item.qty_received
        item.status = GrnItem.Status.RECEIVED
        item.save(update_fields=['qty_pass', 'status'])
        if item.qty_received <= 0:
            continue
        staging_batch = _get_staging_batch(item)
        move_batch_qty(
            source_batch=staging_batch, qty=item.qty_received, to_location=location,
            new_batch_code=_batch_code(item), new_status=Batch.Status.ACTIVE,
            actor=actor, reference=grn.grn_no,
        )

    grn.status = Grn.Status.RECEIVED
    grn.save(update_fields=['status'])
    inspection.status = QcInspection.Result.PASS
    inspection.completed_at = timezone.now()
    inspection.save(update_fields=['status', 'completed_at'])

    log_action(
        actor, AuditLog.Action.APPROVE, target=grn,
        description=f'QC PASS: {grn.grn_no} -> RECEIVED, chuyển Kho chờ -> {location.warehouse.code}.',
        ip_address=ip_address,
    )
    return grn


@transaction.atomic
def qc_fail(inspection, actor=None, reason='QC Fail', ip_address=None):
    """QC FAIL: tiêu thụ batch Kho chờ, tách toàn bộ qty_received sang Batch
    QUARANTINE tại Kho phế, GRN -> REJECTED, vẫn tạo ``GrnReturn``.
    """
    _require_pending_inspection(inspection)
    grn = inspection.grn
    scrap_warehouse = get_scrap_warehouse()
    scrap_location = get_default_location(scrap_warehouse)

    for item in grn.items.select_for_update():
        item.status = GrnItem.Status.REJECTED
        item.qty_pass = 0
        item.save(update_fields=['status', 'qty_pass'])
        if item.qty_received <= 0:
            continue
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
def qc_partial_pass(inspection, item_results, actor=None, location=None, ip_address=None):
    """PARTIAL_PASS: tiêu thụ batch Kho chờ, tách mỗi item thành Batch ACTIVE
    (phần pass, tại ``location`` thuộc kho MAIN) + Batch QUARANTINE (phần
    fail, tại Kho phế).

    ``item_results``: ``{grn_item_id: qty_pass}`` — bắt buộc có đủ mọi item
    của GRN, ``0 <= qty_pass <= qty_received``.
    """
    _require_pending_inspection(inspection)
    if location.warehouse.warehouse_type != Warehouse.WarehouseType.MAIN:
        raise ValidationError('Vị trí đích PASS phải thuộc kho loại "Kho thành phẩm".')
    grn = inspection.grn
    items = list(grn.items.select_for_update())

    missing = {item.pk for item in items} - set(item_results)
    if missing:
        raise ValidationError(f'Thiếu kết quả QC cho {len(missing)} item.')

    scrap_warehouse = get_scrap_warehouse()
    scrap_location = get_default_location(scrap_warehouse)

    for item in items:
        qty_pass = item_results[item.pk]
        if not (0 <= qty_pass <= item.qty_received):
            raise ValidationError(f'qty_pass không hợp lệ cho item {item}.')
        qty_fail = item.qty_received - qty_pass
        has_both = qty_pass > 0 and qty_fail > 0

        if item.qty_received > 0:
            staging_batch = _get_staging_batch(item)
            if qty_pass > 0:
                move_batch_qty(
                    source_batch=staging_batch, qty=qty_pass, to_location=location,
                    new_batch_code=_batch_code(item, '-A' if has_both else ''),
                    new_status=Batch.Status.ACTIVE, actor=actor, reference=grn.grn_no,
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
        description=f'QC PARTIAL_PASS: {grn.grn_no} -> RECEIVED, Kho chờ tách MAIN+SCRAP.',
        ip_address=ip_address,
    )
    return grn
