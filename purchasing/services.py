"""Transaction nghiệp vụ Purchase Order (Phase 5, FR-PO-01..06).

``sync_po_status`` (đã có từ Phase 1e/Phase 2) đồng bộ ``PurchaseOrder.status``
theo Qty đã nhận lũy kế từ mọi GRN tham chiếu tới PO (FR-GRN-04/FR-PO-04: hỗ trợ
nhận nhiều đợt/1 PO). Dùng ``qty_received`` (Qty thực nhận tại cổng kho, ghi ở
state PENDING_QC) chứ không phải ``qty_pass`` (kết quả QC) — PO phản ánh tiến độ
giao hàng của NCC, không phụ thuộc QC pass/fail. Item bị QC REJECT (trả hàng)
được loại khỏi tổng, để 1 PO có thể được giao lại (re-ship) ở GRN kế tiếp mà
không bị tính trùng.

``approve_po``/``send_po``/``close_po`` là transition thật của workflow
DRAFT -> APPROVED -> SENT -> (PARTIAL_RECEIVED/RECEIVED tự động) -> CLOSED.
Không có nhánh auto-approve theo ngưỡng tiền — mọi PO đều cần Manager/Admin
duyệt thủ công (quyết định nghiệp vụ, xem PHÂN quyền ở view: ``approve_po``
chỉ gọi được bởi actor có quyền ``approve`` trên module 'po').
"""
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from accounts.approvals import create_approval, decide_approval
from accounts.audit import log_action
from accounts.models import AuditLog, User
from accounts.notifications import notify
from partners.models import Supplier
from receiving.models import GrnItem

from .models import PurchaseOrder, PurchaseOrderItem, PurchaseRequest


def sync_po_status(po):
    """Đối chiếu qty_received lũy kế so với qty_ordered từng dòng PO.

    - Mọi dòng đã nhận đủ (>=) -> ``RECEIVED`` (set ``received_at`` lần đầu).
    - Có nhận nhưng chưa đủ -> ``PARTIAL_RECEIVED``.
    - Chưa nhận gì -> giữ nguyên status hiện tại.
    """
    po_items = list(PurchaseOrderItem.objects.filter(purchase_order=po))
    if not po_items:
        return po

    received_by_product = dict(
        GrnItem.objects.filter(grn__po=po)
        .exclude(status=GrnItem.Status.REJECTED)
        .values('product_id')
        .annotate(total=Sum('qty_received'))
        .values_list('product_id', 'total')
    )

    all_fulfilled = all(
        received_by_product.get(item.product_id, 0) >= item.qty_ordered for item in po_items)
    any_received = any(
        received_by_product.get(item.product_id, 0) > 0 for item in po_items)

    if all_fulfilled:
        new_status = PurchaseOrder.Status.RECEIVED
    elif any_received:
        new_status = PurchaseOrder.Status.PARTIAL_RECEIVED
    else:
        return po

    if po.status != new_status:
        update_fields = ['status']
        po.status = new_status
        if new_status == PurchaseOrder.Status.RECEIVED and not po.received_at:
            po.received_at = timezone.localdate()
            update_fields.append('received_at')
        po.save(update_fields=update_fields)
    return po


@transaction.atomic
def approve_po(po, actor=None, ip_address=None):
    """DRAFT -> APPROVED. Quyền ``approve`` trên module 'po' gác ở view (chỉ
    Manager/Admin) — không có nhánh tự động, mọi PO đều cần duyệt thủ công.
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError(f'Không thể duyệt PO khi đang ở trạng thái {po.status}.')

    po.status = PurchaseOrder.Status.APPROVED
    po.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.APPROVE, target=po,
        description=f'Duyệt PO: {po.po_no} DRAFT -> APPROVED.',
        ip_address=ip_address,
    )
    return po


@transaction.atomic
def send_po(po, actor=None, ip_address=None):
    """APPROVED -> SENT (gửi PO tới NCC, khoá sửa).

    Best-effort: nếu NCC có ``contact_email`` thì gửi kèm 1 email thông báo
    (mirror style ``accounts.views._send_account_created_email`` — tiếng Việt,
    ``fail_silently=True``, ``from_email=None`` dùng ``DEFAULT_FROM_EMAIL``).
    Không có email thì bỏ qua, không chặn transition — việc gửi PO cho NCC vẫn
    có thể thực hiện qua kênh khác (điện thoại, fax...), hệ thống chỉ hỗ trợ
    thêm chứ không bắt buộc.
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    if po.status != PurchaseOrder.Status.APPROVED:
        raise ValidationError(f'Không thể gửi PO khi đang ở trạng thái {po.status}.')

    po.status = PurchaseOrder.Status.SENT
    po.save(update_fields=['status'])

    email_sent = _send_po_email(po)

    log_action(
        actor, AuditLog.Action.UPDATE, target=po,
        description=(
            f'Gửi PO: {po.po_no} APPROVED -> SENT. Đã gửi email tới NCC ({po.supplier.contact_email}).'
            if email_sent else
            f'Gửi PO: {po.po_no} APPROVED -> SENT. NCC chưa có email, chỉ cập nhật trạng thái.'
        ),
        ip_address=ip_address,
    )
    po._email_sent = email_sent
    return po


def _send_po_email(po):
    """Gửi email PO cho NCC nếu có ``contact_email``. Trả về True/False đã gửi hay chưa."""
    if not po.supplier.contact_email:
        return False
    lines = [
        f'Kính gửi {po.supplier.name},\n',
        f'NVL/WMS gửi đơn mua hàng {po.po_no} với nội dung như sau:\n',
    ]
    for item in po.items.select_related('product').all():
        lines.append(f'- {item.product.product_code} ({item.product.name}): {item.qty_ordered} x {item.unit_price}')
    lines.append('')
    lines.append(f'Ngày giao dự kiến: {po.expected_delivery_date or "chưa xác định"}.')
    lines.append('\nVui lòng xác nhận và giao hàng đúng hẹn. Xin cảm ơn.')
    send_mail(
        subject=f'[NVL/WMS] Đơn mua hàng {po.po_no}',
        message='\n'.join(lines),
        from_email=None,  # dùng DEFAULT_FROM_EMAIL
        recipient_list=[po.supplier.contact_email],
        fail_silently=True,
    )
    return True


@transaction.atomic
def close_po(po, actor=None, reason='', ip_address=None):
    """{SENT, PARTIAL_RECEIVED, RECEIVED} -> CLOSED (archive).

    Cho phép đóng từ SENT/PARTIAL_RECEIVED (không chỉ RECEIVED) vì Manager có
    thể chủ động đóng PO khi NCC không giao nốt phần còn lại — nhưng khi đóng
    sớm kiểu đó, bắt buộc ``reason`` (lý do) để ghi lại vì sao PO không đợi
    nhận đủ; đóng từ RECEIVED (đã nhận đủ) không cần lý do. Re-validate lại
    đây, không chỉ tin ``PurchaseOrderCloseForm.clean()`` đã lọc (form chỉ thu
    input, service mới là nơi thật sự gác constraint — pattern lặp lại khắp
    dự án, xem CLAUDE.md).
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    closeable = (PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIAL_RECEIVED, PurchaseOrder.Status.RECEIVED)
    if po.status not in closeable:
        raise ValidationError(f'Không thể đóng PO khi đang ở trạng thái {po.status}.')
    reason = (reason or '').strip()
    if po.status != PurchaseOrder.Status.RECEIVED and not reason:
        raise ValidationError('Bắt buộc nhập lý do khi đóng PO trước khi NCC giao đủ hàng.')

    po.status = PurchaseOrder.Status.CLOSED
    po.close_reason = reason
    po.save(update_fields=['status', 'close_reason'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=po,
        description=f'Đóng PO: {po.po_no} -> CLOSED.' + (f' Lý do: {reason}' if reason else ''),
        ip_address=ip_address,
    )
    return po


def supplier_price_history(product):
    """FR-PO-03: so sánh giá từ nhiều NCC cho 1 sản phẩm — giá lần cuối, giá
    trung bình, số lần đặt, theo từng NCC đã từng có dòng PO chứa sản phẩm này.
    """
    rows = (
        PurchaseOrderItem.objects.filter(product=product)
        .values('purchase_order__supplier', 'purchase_order__supplier__supplier_code', 'purchase_order__supplier__name')
        .annotate(avg_price=Avg('unit_price'), po_count=Count('id'))
        .order_by('avg_price')
    )
    last_price_by_supplier = dict(
        PurchaseOrderItem.objects.filter(product=product)
        .order_by('purchase_order__supplier_id', '-purchase_order__created_at')
        .distinct('purchase_order__supplier_id')
        .values_list('purchase_order__supplier_id', 'unit_price')
    )
    results = []
    for row in rows:
        supplier_id = row['purchase_order__supplier']
        results.append({
            'supplier_id': supplier_id,
            'supplier_code': row['purchase_order__supplier__supplier_code'],
            'supplier_name': row['purchase_order__supplier__name'],
            'avg_price': row['avg_price'],
            'last_price': last_price_by_supplier.get(supplier_id),
            'po_count': row['po_count'],
        })
    return results


def supplier_lead_time_stats():
    """FR-PO-05/FR-PO-06: với mỗi NCC active, so sánh ``lead_time_days`` cấu
    hình với lead-time thực tế (``received_at - created_at`` của các PO đã
    RECEIVED), đếm số PO đúng hạn/trễ hạn so với ``expected_delivery_date``.
    Tính on-the-fly khi load trang (⏸️ không cron/Celery, theo CLAUDE.md).
    """
    results = []
    for supplier in Supplier.objects.filter(status=Supplier.Status.ACTIVE):
        received_pos = PurchaseOrder.objects.filter(supplier=supplier, received_at__isnull=False)
        lead_times = [
            (po.received_at - timezone.localtime(po.created_at).date()).days for po in received_pos
        ]
        avg_actual_lead_time = sum(lead_times) / len(lead_times) if lead_times else None

        on_time_count = 0
        delayed_count = 0
        for po in received_pos:
            if not po.expected_delivery_date:
                continue
            if po.received_at > po.expected_delivery_date:
                delayed_count += 1
            else:
                on_time_count += 1

        results.append({
            'supplier': supplier,
            'configured_lead_time_days': supplier.lead_time_days,
            'avg_actual_lead_time_days': avg_actual_lead_time,
            'received_po_count': len(lead_times),
            'on_time_count': on_time_count,
            'delayed_count': delayed_count,
        })
    return results


@transaction.atomic
def submit_purchase_request(pr, actor, ip_address=None):
    """DRAFT -> PENDING_DEPT/PENDING_PUR: nộp PR để chờ duyệt (mirror
    ``receiving.services.request_submission``/GIN confirm), theo đúng thiết kế
    duyệt 2 cấp mô tả ở module docstring — tạo ``Approval`` cho quản lý phòng
    ban của chính người nộp trước (``PENDING_DEPT``); nếu người nộp thuộc
    chính phòng Mua hàng (hoặc không có ``department``) thì bỏ qua cấp 1, tạo
    thẳng ``Approval(department=PURCHASING)`` (``PENDING_PUR``) — tránh 1
    người tự duyệt PR của chính họ 2 lần. Không báo ``pr.assigned_to`` ở bước
    này — người này chỉ được thấy/báo PR sau khi PR ``APPROVED`` (xem
    ``decide_purchase_request``), nộp PR không phải lúc họ cần biết.
    """
    pr = PurchaseRequest.objects.select_for_update().get(pk=pr.pk)
    if pr.status != PurchaseRequest.Status.DRAFT:
        raise ValidationError(f'Chỉ nộp được yêu cầu đang ở trạng thái Nháp (hiện tại: {pr.get_status_display()}).')

    origin_department = pr.requested_by.department if pr.requested_by_id else ''
    if origin_department and origin_department != User.Department.PURCHASING:
        pr.status = PurchaseRequest.Status.PENDING_DEPT
        approval_department = origin_department
    else:
        pr.status = PurchaseRequest.Status.PENDING_PUR
        approval_department = User.Department.PURCHASING
    pr.save(update_fields=['status'])
    create_approval(
        pr, department=approval_department,
        action_label=f'Yêu cầu mua hàng {pr.request_no}', submitted_by=actor, ip_address=ip_address,
    )
    return pr


@transaction.atomic
def reopen_purchase_request(pr, actor, ip_address=None):
    """REJECTED -> DRAFT: mở lại PR bị từ chối để sửa và nộp lại (mirror lý do
    module docstring đã nêu — REJECTED không phải ngõ cụt). Giữ nguyên
    ``decided_by``/``decided_at``/``reject_reason`` làm lịch sử tham khảo (không
    xoá — đúng triết lý audit-trail của dự án), chỉ set lại ``status``. Sau khi
    reopen, PR quay lại DRAFT nên dùng lại được ngay ``pr_update``/
    ``submit_purchase_request`` đã có, không cần đường xử lý riêng.
    """
    pr = PurchaseRequest.objects.select_for_update().get(pk=pr.pk)
    if pr.status != PurchaseRequest.Status.REJECTED:
        raise ValidationError(f'Chỉ mở lại được yêu cầu đang bị từ chối (hiện tại: {pr.get_status_display()}).')

    pr.status = PurchaseRequest.Status.DRAFT
    pr.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=pr,
        description=f'Mở lại yêu cầu mua hàng {pr.request_no} (Từ chối -> Nháp) để sửa lại.',
        ip_address=ip_address,
    )
    return pr


@transaction.atomic
def delete_purchase_request(pr, actor, ip_address=None):
    """DELETE — xoá thật (không phải soft-delete) một PR còn DRAFT (L2). PR ở
    DRAFT chưa từng qua ``Approval``/quyết định duyệt nào cần giữ lại làm lịch
    sử — khác PR đã REJECTED (``reopen_purchase_request`` giữ nguyên
    ``reject_reason`` làm bằng chứng), một bản nháp bị bỏ hoàn toàn không cần
    vết tích, nên xoá cứng thay vì thêm 1 trạng thái ``CANCELLED`` chỉ cho
    riêng DRAFT. Ghi ``AuditLog`` TRƯỚC khi xoá — GenericFK vẫn giữ được
    ``target_id`` sau khi bản ghi gốc không còn tồn tại (cùng cách audit log
    tham chiếu đối tượng đã xoá ở mọi nơi khác trong dự án, xem ``seed_demo_data``).
    """
    pr = PurchaseRequest.objects.select_for_update().get(pk=pr.pk)
    if pr.status != PurchaseRequest.Status.DRAFT:
        raise ValidationError(f'Chỉ xoá được yêu cầu đang ở trạng thái Nháp (hiện tại: {pr.get_status_display()}).')

    request_no = pr.request_no
    log_action(
        actor, AuditLog.Action.DELETE, target=pr,
        description=f'Xoá yêu cầu mua hàng {request_no} (nháp)',
        ip_address=ip_address,
    )
    pr.delete()
    return request_no


@transaction.atomic
def forward_purchase_request(pr, staff, actor, ip_address=None):
    """Quản lý phòng Mua hàng (hoặc Manager/Admin) chuyển tiếp 1 PR đã ``APPROVED``
    cho 1 nhân viên phòng Mua hàng cụ thể để tạo PO — set/ghi đè ``assigned_to``
    (cùng field với chỉ định lúc tạo PR, xem ``PurchaseRequest.assigned_to``),
    quyết định hiển thị PR cho nhân viên đó ở ``purchasing.views._pr_can_view_all``.
    """
    pr = PurchaseRequest.objects.select_for_update().get(pk=pr.pk)
    if pr.status != PurchaseRequest.Status.APPROVED:
        raise ValidationError(f'Chỉ chuyển tiếp được yêu cầu đã duyệt (hiện tại: {pr.get_status_display()}).')
    if pr.linked_po_id:
        raise ValidationError('Yêu cầu này đã có PO liên kết, không cần chuyển tiếp nữa.')

    pr.assigned_to = staff
    pr.save(update_fields=['assigned_to'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=pr,
        description=f'Chuyển tiếp yêu cầu mua hàng {pr.request_no} cho {staff.username} tạo PO.',
        ip_address=ip_address,
    )
    notify(staff, f'{actor.username} chuyển tiếp yêu cầu mua hàng {pr.request_no} cho bạn tạo PO.', target=pr)
    return pr


@transaction.atomic
def decide_purchase_request(approval, approved, actor, note='', ip_address=None):
    """Quản lý phòng ban đang giữ quyền quyết định ở cấp hiện tại (bộ phận gốc
    ở ``PENDING_DEPT``, Mua hàng ở ``PENDING_PUR`` — hoặc Manager/Admin) duyệt/
    từ chối 1 PR đang chờ, thông qua ``Approval`` (xem
    ``accounts.approvals.decide_approval``).

    Duyệt ở ``PENDING_DEPT`` chỉ CHUYỂN TIẾP sang cấp Mua hàng
    (``PENDING_PUR``) — chưa phải quyết định cuối cùng nên không set
    ``decided_by``/``decided_at``. Duyệt ở ``PENDING_PUR`` mới là quyết định
    cuối (``APPROVED``) và là lúc báo ``pr.assigned_to`` để tạo PO (xem
    ``submit_purchase_request`` — không báo lúc nộp). Từ chối ở cấp nào cũng
    kết thúc PR ngay (``REJECTED``).

    ⚠️ Việc tạo ``Approval`` cho cấp 2 PHẢI làm SAU KHI ``decide_approval()``
    trả về, không được làm trong ``on_approve()`` — ``decide_approval()`` gọi
    callback trước khi lưu ``approval.status=APPROVED``, nên tạo Approval mới
    trong lúc bản ghi cấp 1 vẫn còn ``status='PENDING'`` trong DB sẽ đụng ràng
    buộc ``unique_pending_approval_per_target`` (2 PENDING cùng target).
    """
    pr = PurchaseRequest.objects.select_for_update().get(pk=approval.target_id)
    stage = pr.status
    if stage not in (PurchaseRequest.Status.PENDING_DEPT, PurchaseRequest.Status.PENDING_PUR):
        raise ValidationError(f'Yêu cầu "{pr.request_no}" không ở trạng thái chờ duyệt.')

    advance_to_pur = False

    def on_approve():
        nonlocal advance_to_pur
        if stage == PurchaseRequest.Status.PENDING_DEPT:
            pr.status = PurchaseRequest.Status.PENDING_PUR
            pr.save(update_fields=['status'])
            advance_to_pur = True
        else:
            pr.status = PurchaseRequest.Status.APPROVED
            pr.decided_by = actor
            pr.decided_at = timezone.now()
            pr.save(update_fields=['status', 'decided_by', 'decided_at'])
            if pr.assigned_to_id:
                notify(
                    pr.assigned_to,
                    f'Yêu cầu mua hàng {pr.request_no} đã được duyệt — hãy tạo PO.', target=pr)

    def on_reject():
        pr.status = PurchaseRequest.Status.REJECTED
        pr.decided_by = actor
        pr.decided_at = timezone.now()
        pr.reject_reason = note
        pr.save(update_fields=['status', 'decided_by', 'decided_at', 'reject_reason'])

    decide_approval(
        approval, approved, actor=actor, note=note,
        on_approve=on_approve, on_reject=on_reject, ip_address=ip_address,
    )
    if advance_to_pur:
        create_approval(
            pr, department=User.Department.PURCHASING,
            action_label=f'Yêu cầu mua hàng {pr.request_no}', submitted_by=pr.requested_by, ip_address=ip_address,
        )
    return pr
