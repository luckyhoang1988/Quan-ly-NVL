"""Transaction nghiệp vụ GRN (mục 2a) — state DRAFT -> PENDING_QC -> ... -> CLOSED,
và workflow GRN_RETURN (mục 2c) PENDING -> APPROVED -> RETURNED -> CLOSED.

Chuyển tiếp PENDING_QC -> QC_IN_PROGRESS (``start_qc``) và các nhánh QC PASS/
FAIL/PARTIAL_PASS nằm ở ``quality.services`` vì gắn liền với ``QcInspection``
(mục 2c) — module ``receiving`` chỉ giữ transition thuần GRN, chưa đụng QC.
"""
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.approvals import create_approval, decide_approval
from accounts.audit import log_action
from accounts.models import Approval, AuditLog, User
from accounts.notifications import notify

from .models import Grn, GrnReturn


@transaction.atomic
def submit_to_pending_qc(grn, actor=None, ip_address=None):
    """PENDING_APPROVAL -> PENDING_QC (quản lý Kho đã duyệt nộp, gọi từ
    ``decide_grn_submission``) — hoặc DRAFT -> PENDING_QC trực tiếp khi gọi lập
    trình (vd seed demo data), bỏ qua bước duyệt.
    """
    grn = Grn.objects.select_for_update().get(pk=grn.pk)
    if grn.status not in (Grn.Status.DRAFT, Grn.Status.PENDING_APPROVAL):
        raise ValidationError(f'Không thể submit khi GRN đang ở trạng thái {grn.status}.')
    if not grn.items.exists():
        raise ValidationError('GRN chưa có dòng hàng nào.')

    grn.status = Grn.Status.PENDING_QC
    grn.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=grn,
        description=f'Submit GRN: {grn.grn_no} -> PENDING_QC.',
        ip_address=ip_address,
    )
    return grn


@transaction.atomic
def request_submission(grn, actor, ip_address=None):
    """DRAFT -> PENDING_APPROVAL: nhân viên nộp GRN, chờ quản lý Kho duyệt trước
    khi thật sự chuyển sang PENDING_QC (luồng nộp/duyệt 2 cấp)."""
    grn = Grn.objects.select_for_update().get(pk=grn.pk)
    if grn.status != Grn.Status.DRAFT:
        raise ValidationError(f'Không thể nộp khi GRN đang ở trạng thái {grn.status}.')
    if not grn.items.exists():
        raise ValidationError('GRN chưa có dòng hàng nào.')

    grn.status = Grn.Status.PENDING_APPROVAL
    grn.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=grn,
        description=f'Nộp GRN {grn.grn_no}: DRAFT -> PENDING_APPROVAL (chờ duyệt).',
        ip_address=ip_address,
    )
    create_approval(
        grn, department=User.Department.WAREHOUSE, action_label=f'Nộp phiếu GRN {grn.grn_no}',
        submitted_by=actor, ip_address=ip_address,
    )
    return grn


@transaction.atomic
def decide_grn_submission(approval, approved, actor, note='', ip_address=None):
    """Quản lý Kho duyệt/từ chối yêu cầu nộp GRN (``request_submission``) — duyệt
    thì thật sự chạy ``submit_to_pending_qc``; từ chối thì trả GRN về DRAFT."""
    grn = Grn.objects.select_for_update().get(pk=approval.target_id)
    if grn.status != Grn.Status.PENDING_APPROVAL:
        raise ValidationError(f'GRN "{grn.grn_no}" không ở trạng thái chờ duyệt nộp.')

    def on_approve():
        submit_to_pending_qc(grn, actor=actor, ip_address=ip_address)

    def on_reject():
        grn.status = Grn.Status.DRAFT
        grn.save(update_fields=['status'])
        log_action(
            actor, AuditLog.Action.REJECT, target=grn,
            description=f'Từ chối nộp GRN {grn.grn_no}: PENDING_APPROVAL -> DRAFT.',
            reason=note, ip_address=ip_address,
        )

    decide_approval(
        approval, approved, actor=actor, note=note,
        on_approve=on_approve, on_reject=on_reject, ip_address=ip_address,
    )
    return grn


@transaction.atomic
def cancel_grn(grn, actor=None, note='', ip_address=None):
    """Hủy GRN (mọi state trừ RECEIVED/REJECTED/CANCELLED/CLOSED) — quyền kiểm
    tra ở view theo ``grn.current_department`` (quản lý phòng ban đang giữ phiếu
    ở bước hiện tại được hủy). Mọi yêu cầu duyệt PENDING còn treo trên GRN này bị
    tự động từ chối (tránh mồ côi khi GRN đã hủy)."""
    grn = Grn.objects.select_for_update().get(pk=grn.pk)
    if grn.status in (Grn.Status.RECEIVED, Grn.Status.REJECTED, Grn.Status.CANCELLED, Grn.Status.CLOSED):
        raise ValidationError(f'Không thể hủy GRN khi đang ở trạng thái {grn.status}.')

    Approval.objects.filter(
        target_type=ContentType.objects.get_for_model(Grn), target_id=str(grn.pk),
        status=Approval.Status.PENDING,
    ).update(
        status=Approval.Status.REJECTED, decided_by=actor, decided_at=timezone.now(),
        decision_note='GRN đã bị hủy.',
    )

    grn.status = Grn.Status.CANCELLED
    grn.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.CANCEL, target=grn,
        description=f'Hủy GRN {grn.grn_no}.', reason=note, ip_address=ip_address,
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
    """GRN_RETURN: PENDING -> APPROVED (Mua hàng, hoặc Manager/Admin, duyệt trả
    hàng NCC) — báo cho cả QC và Kho theo dõi (QC sẽ là người xác nhận bước kế
    tiếp, Kho cần biết hàng đang chờ trả)."""
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
    notify(
        User.objects.filter(department__in=[User.Department.QC, User.Department.WAREHOUSE], is_active=True),
        f'Phiếu trả hàng RETURN-{grn_return.grn.grn_no} đã được duyệt — chờ QC xác nhận đã trả NCC.',
        target=grn_return,
    )
    return grn_return


@transaction.atomic
def request_return_confirmation(grn_return, actor, ip_address=None):
    """QC xác nhận đã kiểm tra hàng trả NCC (APPROVED) -> tạo yêu cầu duyệt cho
    quản lý QC (chỉ QC xác nhận được, phải qua quản lý QC duyệt)."""
    grn_return = GrnReturn.objects.select_for_update().get(pk=grn_return.pk)
    if grn_return.status != GrnReturn.Status.APPROVED:
        raise ValidationError(f'Không thể xác nhận khi phiếu trả hàng đang ở trạng thái {grn_return.status}.')

    log_action(
        actor, AuditLog.Action.UPDATE, target=grn_return,
        description=f'QC xác nhận đã kiểm tra RETURN-{grn_return.grn.grn_no}, chờ quản lý QC duyệt.',
        ip_address=ip_address,
    )
    create_approval(
        grn_return, department=User.Department.QC,
        action_label=f'Xác nhận đã trả hàng RETURN-{grn_return.grn.grn_no}',
        submitted_by=actor, ip_address=ip_address,
    )
    return grn_return


@transaction.atomic
def decide_return_confirmation(approval, approved, actor, note='', ip_address=None):
    """Quản lý QC duyệt/từ chối xác nhận trả hàng — duyệt thì thật sự chạy
    ``mark_return_returned`` và tự động báo cho phòng Kho (forward về kho)."""
    grn_return = GrnReturn.objects.select_for_update().get(pk=approval.target_id)
    if grn_return.status != GrnReturn.Status.APPROVED:
        raise ValidationError(
            f'Phiếu trả hàng RETURN-{grn_return.grn.grn_no} không ở trạng thái chờ xác nhận.')

    def on_approve():
        mark_return_returned(grn_return, actor=actor, ip_address=ip_address)
        notify(
            User.objects.filter(department=User.Department.WAREHOUSE, is_active=True),
            f'Hàng trả NCC RETURN-{grn_return.grn.grn_no} đã hoàn tất.',
            target=grn_return,
        )

    decide_approval(
        approval, approved, actor=actor, note=note, on_approve=on_approve, ip_address=ip_address,
    )
    return grn_return


@transaction.atomic
def mark_return_returned(grn_return, actor=None, ip_address=None):
    """GRN_RETURN: APPROVED -> RETURNED (đã trả hàng vật lý cho NCC).

    Gọi nội bộ từ ``decide_return_confirmation`` (QC xác nhận + quản lý QC duyệt)
    — không còn expose trực tiếp qua view cho STAFF gọi thẳng như trước.
    """
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
