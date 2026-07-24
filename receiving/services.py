"""Transaction nghiệp vụ GRN (mục 2a) — state DRAFT -> PENDING_QC -> ... -> CLOSED,
và workflow GRN_RETURN (mục 2c) PENDING -> APPROVED -> RETURNED -> CLOSED.

Chuyển tiếp PENDING_QC -> QC_IN_PROGRESS (``start_qc``) và các nhánh QC PASS/
FAIL/PARTIAL_PASS nằm ở ``quality.services`` vì gắn liền với ``QcInspection``
(mục 2c) — module ``receiving`` chỉ giữ transition thuần GRN, chưa đụng QC.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.audit import log_action
from accounts.models import AuditLog

from .models import Grn, GrnReturn


@transaction.atomic
def submit_to_pending_qc(grn, actor=None, ip_address=None):
    """DRAFT -> PENDING_QC (mục 2a Workflow States: nút "Submit" ở state DRAFT)."""
    grn = Grn.objects.select_for_update().get(pk=grn.pk)
    if grn.status != Grn.Status.DRAFT:
        raise ValidationError(f'Không thể submit khi GRN đang ở trạng thái {grn.status}.')
    if not grn.items.exists():
        raise ValidationError('GRN chưa có dòng hàng nào.')

    grn.status = Grn.Status.PENDING_QC
    grn.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=grn,
        description=f'Submit GRN: {grn.grn_no} DRAFT -> PENDING_QC.',
        ip_address=ip_address,
    )
    return grn


@transaction.atomic
def close_grn(grn, actor=None, ip_address=None):
    """RECEIVED/REJECTED -> CLOSED (archive, State CLOSED mục 2a).

    GRN REJECTED chỉ đóng được sau khi mọi ``GrnReturn`` liên quan đã xử lý
    xong (RETURNED/CLOSED) — tránh archive khi hàng trả NCC còn dang dở.
    """
    grn = Grn.objects.select_for_update().get(pk=grn.pk)
    if grn.status not in (Grn.Status.RECEIVED, Grn.Status.REJECTED):
        raise ValidationError(f'Không thể đóng GRN khi đang ở trạng thái {grn.status}.')
    if grn.status == Grn.Status.REJECTED:
        unresolved = grn.returns.exclude(status__in=[GrnReturn.Status.RETURNED, GrnReturn.Status.CLOSED])
        if unresolved.exists():
            raise ValidationError('Còn phiếu trả hàng chưa xử lý xong (RETURNED/CLOSED), chưa thể đóng GRN.')

    grn.status = Grn.Status.CLOSED
    grn.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=grn,
        description=f'Đóng GRN: {grn.grn_no} -> CLOSED.',
        ip_address=ip_address,
    )
    return grn


@transaction.atomic
def approve_return(grn_return, actor=None, ip_address=None):
    """GRN_RETURN: PENDING -> APPROVED (Manager/Admin duyệt trả hàng NCC)."""
    grn_return = GrnReturn.objects.select_for_update().get(pk=grn_return.pk)
    if grn_return.status != GrnReturn.Status.PENDING:
        raise ValidationError(f'Không thể duyệt phiếu trả hàng khi đang ở trạng thái {grn_return.status}.')

    grn_return.status = GrnReturn.Status.APPROVED
    grn_return.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.APPROVE, target=grn_return,
        description=f'Duyệt phiếu trả hàng RETURN-{grn_return.grn.grn_no}: PENDING -> APPROVED.',
        ip_address=ip_address,
    )
    return grn_return


@transaction.atomic
def mark_return_returned(grn_return, actor=None, ip_address=None):
    """GRN_RETURN: APPROVED -> RETURNED (đã trả hàng vật lý cho NCC)."""
    grn_return = GrnReturn.objects.select_for_update().get(pk=grn_return.pk)
    if grn_return.status != GrnReturn.Status.APPROVED:
        raise ValidationError(f'Không thể xác nhận đã trả hàng khi đang ở trạng thái {grn_return.status}.')

    grn_return.status = GrnReturn.Status.RETURNED
    grn_return.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=grn_return,
        description=f'Phiếu trả hàng RETURN-{grn_return.grn.grn_no}: APPROVED -> RETURNED.',
        ip_address=ip_address,
    )
    return grn_return


@transaction.atomic
def close_return(grn_return, actor=None, ip_address=None):
    """GRN_RETURN: RETURNED -> CLOSED (archive)."""
    grn_return = GrnReturn.objects.select_for_update().get(pk=grn_return.pk)
    if grn_return.status != GrnReturn.Status.RETURNED:
        raise ValidationError(f'Không thể đóng phiếu trả hàng khi đang ở trạng thái {grn_return.status}.')

    grn_return.status = GrnReturn.Status.CLOSED
    grn_return.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=grn_return,
        description=f'Đóng phiếu trả hàng RETURN-{grn_return.grn.grn_no}: RETURNED -> CLOSED.',
        ip_address=ip_address,
    )
    return grn_return


def tolerance_alerts(grn):
    """FR-GRN-07: cảnh báo (không chặn) từng item có |qty_received - qty_ordered|
    vượt ``Supplier.qty_tolerance_percent`` — gọi sau khi Qty thực nhận đã lưu
    (state PENDING_QC), trả về danh sách message để view hiển thị.
    """
    tolerance = grn.supplier.qty_tolerance_percent
    alerts = []
    for item in grn.items.all():
        if not item.qty_ordered:
            continue
        diff_percent = abs(item.qty_received - item.qty_ordered) / item.qty_ordered * 100
        if diff_percent > tolerance:
            alerts.append(
                f'{item.product}: nhận {item.qty_received}/{item.qty_ordered} '
                f'(chênh {diff_percent:.1f}%, vượt ngưỡng {tolerance}% của NCC "{grn.supplier}").'
            )
    return alerts
