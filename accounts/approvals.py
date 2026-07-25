"""Cơ chế duyệt chung "nhân viên nộp -> quản lý phòng ban duyệt" (Phase B/C).

Dùng cho GRN submit, GIN confirm, GrnReturn QC-confirm... Transition THẬT của
đối tượng (chuyển state GRN/GIN/GrnReturn...) do app gọi truyền vào qua closure
``on_approve``/``on_reject`` — module này chỉ quản lý vòng đời của bản ghi
``Approval`` (PENDING -> APPROVED/REJECTED) + thông báo, không phụ thuộc ngược
vào service của app nghiệp vụ nào.
"""
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .audit import log_action
from .models import Approval, AuditLog, User
from .notifications import notify


@transaction.atomic
def create_approval(target, department, action_label, submitted_by, ip_address=None):
    """Tạo 1 yêu cầu duyệt PENDING cho ``target`` + báo toàn bộ quản lý ``department``.

    Chặn tạo trùng: ``target`` đang có Approval PENDING khác thì báo lỗi thay vì
    tạo thêm — tránh 2 yêu cầu duyệt chồng nhau trên cùng 1 đối tượng.
    """
    content_type = ContentType.objects.get_for_model(target.__class__)
    target_id = str(target.pk)
    if Approval.objects.filter(
        target_type=content_type, target_id=target_id, status=Approval.Status.PENDING,
    ).exists():
        raise ValidationError('Đối tượng này đang có yêu cầu duyệt chưa xử lý xong.')

    approval = Approval.objects.create(
        target_type=content_type, target_id=target_id,
        department=department, action_label=action_label, submitted_by=submitted_by,
    )
    managers = User.objects.filter(department=department, is_manager=True, is_active=True)
    notify(managers, f'{submitted_by.username} nộp yêu cầu duyệt: {action_label}.', target=target)
    log_action(
        submitted_by, AuditLog.Action.UPDATE, target=target,
        description=f'Nộp yêu cầu duyệt: {action_label}.', ip_address=ip_address,
    )
    return approval


@transaction.atomic
def decide_approval(approval, approved, actor, note='', on_approve=None, on_reject=None, ip_address=None):
    """Quyết định 1 ``Approval`` đang PENDING.

    Gọi ``on_approve``/``on_reject`` TRƯỚC khi lưu status — nếu transition thật
    (callback) raise lỗi thì cả quyết định duyệt cũng rollback theo (atomic).
    """
    approval = Approval.objects.select_for_update().get(pk=approval.pk)
    if approval.status != Approval.Status.PENDING:
        raise ValidationError(f'Yêu cầu duyệt này đã được xử lý ({approval.get_status_display()}).')

    if approved:
        if on_approve:
            on_approve()
        approval.status = Approval.Status.APPROVED
    else:
        if on_reject:
            on_reject()
        approval.status = Approval.Status.REJECTED

    approval.decided_by = actor
    approval.decided_at = timezone.now()
    approval.decision_note = note
    approval.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])

    verb = f'Yêu cầu "{approval.action_label}" đã được {"duyệt" if approved else "từ chối"}.'
    notify(approval.submitted_by, verb, target=approval.target)
    log_action(
        actor, AuditLog.Action.APPROVE if approved else AuditLog.Action.REJECT,
        target=approval.target, description=verb, reason=note, ip_address=ip_address,
    )
    return approval


def latest_approval_for(target):
    """``Approval`` gần nhất (theo ``submitted_at``) gắn với ``target`` — dùng hiển
    thị khung thời gian nộp/duyệt ở trang detail. Trả ``None`` nếu chưa từng có.
    """
    if target is None or target.pk is None:
        return None
    content_type = ContentType.objects.get_for_model(target.__class__)
    return (
        Approval.objects.filter(target_type=content_type, target_id=str(target.pk))
        .select_related('submitted_by', 'decided_by')
        .order_by('-submitted_at')
        .first()
    )
