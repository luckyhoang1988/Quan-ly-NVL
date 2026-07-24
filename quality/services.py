"""Transaction nghiệp vụ GRN↔QC (BACKLOG mục 2c) — PASS/FAIL/PARTIAL_PASS.

Mỗi hàm là MỘT transaction atomic (``@transaction.atomic``), all-or-nothing,
đúng yêu cầu "Inventory Update Triggers" của mục 2c:

- ``start_qc``: glue tối thiểu để GRN có thể được QC — DRAFT/PENDING_QC ->
  QC_IN_PROGRESS, tạo ``QcInspection``. Không phải workflow đầy đủ (chưa có
  view/form nhập Qty thực nhận ở state PENDING_QC), chỉ đủ để qc_pass/fail/
  partial_pass có input hợp lệ.
- ``qc_pass``: Batch ACTIVE full qty_received từng item, Inventory += qty,
  GRN -> RECEIVED.
- ``qc_fail``: GRN_RETURN, GRN -> REJECTED, KHÔNG tạo Batch, KHÔNG đụng
  Inventory (đây là nhánh dễ sai nhất theo CLAUDE.md).
- ``qc_partial_pass``: mỗi item nhận ``qty_pass`` riêng (0 <= qty_pass <=
  qty_received) -> tách 2 Batch (ACTIVE phần pass + QUARANTINE phần fail),
  Inventory chỉ cộng phần pass.

Mọi transition đều ghi ``AuditLog`` qua ``accounts.audit.log_action`` (hạ
tầng có sẵn từ Phase 1) — một dòng log cho mỗi transaction nghiệp vụ (không
log riêng từng Batch/GrnItem bị đụng tới bên trong).
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog
from inventory.models import Batch, Inventory
from receiving.models import Grn, GrnItem, GrnReturn

from .models import QcInspection


def _batch_code(grn_item, suffix=''):
    return f'{grn_item.label_code}{suffix}'


def _credit_inventory(product, warehouse, qty):
    inv, _ = Inventory.objects.select_for_update().get_or_create(product=product, warehouse=warehouse)
    inv.qty_on_hand += qty
    inv.save(update_fields=['qty_on_hand', 'updated_at'])
    return inv


def _require_pending_inspection(inspection):
    if inspection.status != QcInspection.Result.PENDING_QC:
        raise ValidationError('QC inspection này đã có kết quả, không thể ghi lại.')
    if inspection.grn.status != Grn.Status.QC_IN_PROGRESS:
        raise ValidationError('GRN không ở trạng thái QC_IN_PROGRESS.')


@transaction.atomic
def start_qc(grn, inspector, actor=None, ip_address=None):
    """DRAFT/PENDING_QC -> QC_IN_PROGRESS, tạo ``QcInspection`` mới cho GRN."""
    grn = Grn.objects.select_for_update().get(pk=grn.pk)
    if grn.status not in (Grn.Status.DRAFT, Grn.Status.PENDING_QC):
        raise ValidationError(f'Không thể bắt đầu QC khi GRN đang ở trạng thái {grn.status}.')

    inspection = QcInspection.objects.create(grn=grn, inspector=inspector, started_at=timezone.now())
    grn.status = Grn.Status.QC_IN_PROGRESS
    grn.save(update_fields=['status'])

    log_action(
        actor, AuditLog.Action.UPDATE, target=grn,
        description=f'Submit to QC: {grn.grn_no} -> QC_IN_PROGRESS ({inspection.qc_no}).',
        ip_address=ip_address,
    )
    return inspection


@transaction.atomic
def qc_pass(inspection, actor=None, location=None, ip_address=None):
    """QC PASS: Batch ACTIVE full qty_received từng item, Inventory tăng, GRN RECEIVED."""
    _require_pending_inspection(inspection)
    grn = inspection.grn

    for item in grn.items.select_for_update():
        Batch.objects.create(
            product=item.product, batch_code=_batch_code(item), supplier=grn.supplier,
            location=location, mfg_date=item.mfg_date, exp_date=item.exp_date,
            qty_received=item.qty_received, status=Batch.Status.ACTIVE,
        )
        _credit_inventory(item.product, location.warehouse, item.qty_received)
        item.qty_pass = item.qty_received
        item.status = GrnItem.Status.RECEIVED
        item.save(update_fields=['qty_pass', 'status'])

    grn.status = Grn.Status.RECEIVED
    grn.save(update_fields=['status'])
    inspection.status = QcInspection.Result.PASS
    inspection.completed_at = timezone.now()
    inspection.save(update_fields=['status', 'completed_at'])

    log_action(
        actor, AuditLog.Action.APPROVE, target=grn,
        description=f'QC PASS: {grn.grn_no} -> RECEIVED, batch tạo tự động.',
        ip_address=ip_address,
    )
    return grn


@transaction.atomic
def qc_fail(inspection, actor=None, reason='QC Fail', ip_address=None):
    """QC FAIL: GRN_RETURN, GRN REJECTED — KHÔNG tạo Batch, KHÔNG đụng Inventory."""
    _require_pending_inspection(inspection)
    grn = inspection.grn

    grn.items.update(status=GrnItem.Status.REJECTED, qty_pass=0)
    grn.status = Grn.Status.REJECTED
    grn.save(update_fields=['status'])
    inspection.status = QcInspection.Result.FAIL
    inspection.completed_at = timezone.now()
    inspection.save(update_fields=['status', 'completed_at'])

    ret = GrnReturn.objects.create(grn=grn, reason=reason)

    log_action(
        actor, AuditLog.Action.REJECT, target=grn,
        description=f'QC FAIL: {grn.grn_no} -> REJECTED, tạo {ret}.',
        reason=reason, ip_address=ip_address,
    )
    return ret


@transaction.atomic
def qc_partial_pass(inspection, item_results, actor=None, location=None, ip_address=None):
    """PARTIAL_PASS: mỗi item tách Batch ACTIVE(pass) + QUARANTINE(fail).

    ``item_results``: ``{grn_item_id: qty_pass}`` — bắt buộc có đủ mọi item
    của GRN, ``0 <= qty_pass <= qty_received``. Inventory chỉ cộng phần pass.
    """
    _require_pending_inspection(inspection)
    grn = inspection.grn
    items = list(grn.items.select_for_update())

    missing = {item.pk for item in items} - set(item_results)
    if missing:
        raise ValidationError(f'Thiếu kết quả QC cho {len(missing)} item.')

    for item in items:
        qty_pass = item_results[item.pk]
        if not (0 <= qty_pass <= item.qty_received):
            raise ValidationError(f'qty_pass không hợp lệ cho item {item}.')
        qty_fail = item.qty_received - qty_pass
        has_both = qty_pass > 0 and qty_fail > 0

        if qty_pass > 0:
            Batch.objects.create(
                product=item.product, batch_code=_batch_code(item, '-A' if has_both else ''),
                supplier=grn.supplier, location=location, mfg_date=item.mfg_date, exp_date=item.exp_date,
                qty_received=qty_pass, status=Batch.Status.ACTIVE,
            )
            _credit_inventory(item.product, location.warehouse, qty_pass)
        if qty_fail > 0:
            Batch.objects.create(
                product=item.product, batch_code=_batch_code(item, '-Q' if has_both else ''),
                supplier=grn.supplier, location=location, mfg_date=item.mfg_date, exp_date=item.exp_date,
                qty_received=qty_fail, status=Batch.Status.QUARANTINE,
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
        description=f'QC PARTIAL_PASS: {grn.grn_no} -> RECEIVED, batch ACTIVE+QUARANTINE split.',
        ip_address=ip_address,
    )
    return grn
