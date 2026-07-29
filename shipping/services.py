"""Transaction nghiệp vụ GIN (mục 3b) — DRAFT -> PICKING -> ISSUED -> CLOSED.

FIFO suggest dùng lại ``inventory.services.suggest_fifo_batches`` (FR-GIN-02) —
không viết lại thuật toán FIFO ở đây (đã có unit test riêng ở Task 1). Mỗi hàm
chuyển state là 1 transaction atomic, ghi ``AuditLog`` qua ``accounts.audit.
log_action`` — cùng convention với ``quality.services``/``receiving.services``.

- ``request_confirmation``/``decide_gin_confirmation``: luồng "nhân viên xác
  nhận yêu cầu xuất -> quản lý Kho duyệt" (DRAFT -> PENDING_APPROVAL -> PICKING),
  mirror ``receiving.services.request_submission``/``decide_grn_submission``
  (Phase B) qua ``accounts.approvals``.
- ``start_picking``: PENDING_APPROVAL/DRAFT -> PICKING, tạo ``GinBatchAllocation``
  theo gợi ý FIFO cho từng ``GinItem`` (mục 3b Workflow State DRAFT: "hệ thống
  suggest lô FIFO").
- ``override_allocation``: FR-GIN-03, đổi batch khác gợi ý FIFO cho 1 dòng
  allocation, bắt buộc ghi lý do (mục 3b Workflow State PICKING).
- ``issue_gin``: PICKING -> ISSUED (FR-GIN-04/FR-GIN-05) — trừ
  ``Inventory.qty_on_hand``, cộng ``Batch.qty_used``, BR-GIN-006 (batch hết
  hàng -> CLOSED), ghi ``GinItem.qty_issued`` và ``StockMovement`` ISSUE.
- ``close_gin``: ISSUED -> CLOSED (archive).
- ``cancel_gin``: hủy GIN trước khi ISSUED (mirror ``receiving.services.cancel_grn``).
"""
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.approvals import create_approval, decide_approval
from accounts.audit import log_action
from accounts.models import Approval, AuditLog, User
from inventory.models import Batch, Inventory, StockMovement
from inventory.services import record_movement, suggest_fifo_batches

from .models import Gin, GinBatchAllocation


@transaction.atomic
def request_confirmation(gin, actor, ip_address=None):
    """DRAFT -> PENDING_APPROVAL: nhân viên xác nhận yêu cầu xuất, chờ quản lý
    Kho duyệt trước khi thật sự gợi ý FIFO và chuyển PICKING (luồng nộp/duyệt
    2 cấp, mirror ``receiving.services.request_submission``)."""
    gin = Gin.objects.select_for_update().get(pk=gin.pk)
    if gin.status != Gin.Status.DRAFT:
        raise ValidationError(f'Không thể xác nhận yêu cầu xuất khi GIN đang ở trạng thái {gin.status}.')
    if not gin.items.exists():
        raise ValidationError('GIN chưa có dòng hàng nào.')

    gin.status = Gin.Status.PENDING_APPROVAL
    gin.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=gin,
        description=f'Xác nhận yêu cầu xuất GIN {gin.gin_no}: DRAFT -> PENDING_APPROVAL (chờ duyệt).',
        ip_address=ip_address,
    )
    create_approval(
        gin, department=User.Department.WAREHOUSE, action_label=f'Xác nhận yêu cầu xuất GIN {gin.gin_no}',
        submitted_by=actor, ip_address=ip_address,
    )
    return gin


@transaction.atomic
def decide_gin_confirmation(approval, approved, actor, note='', ip_address=None):
    """Quản lý Kho duyệt/từ chối yêu cầu xác nhận xuất (``request_confirmation``)
    — duyệt thì thật sự chạy ``start_picking`` (gợi ý FIFO); từ chối thì trả GIN
    về DRAFT."""
    gin = Gin.objects.select_for_update().get(pk=approval.target_id)
    if gin.status != Gin.Status.PENDING_APPROVAL:
        raise ValidationError(f'GIN "{gin.gin_no}" không ở trạng thái chờ duyệt xác nhận.')

    def on_approve():
        start_picking(gin, actor=actor, ip_address=ip_address)

    def on_reject():
        gin.status = Gin.Status.DRAFT
        gin.save(update_fields=['status'])
        log_action(
            actor, AuditLog.Action.REJECT, target=gin,
            description=f'Từ chối xác nhận yêu cầu xuất GIN {gin.gin_no}: PENDING_APPROVAL -> DRAFT.',
            reason=note, ip_address=ip_address,
        )

    decide_approval(
        approval, approved, actor=actor, note=note,
        on_approve=on_approve, on_reject=on_reject, ip_address=ip_address,
    )
    return gin


@transaction.atomic
def cancel_gin(gin, actor=None, note='', ip_address=None):
    """Hủy GIN (mọi state trừ ISSUED/CANCELLED/CLOSED — sau ISSUED đã trừ thật
    Inventory/Batch, không thể hủy đơn giản). Mọi yêu cầu duyệt PENDING còn treo
    trên GIN này bị tự động từ chối (tránh mồ côi khi GIN đã hủy)."""
    gin = Gin.objects.select_for_update().get(pk=gin.pk)
    if gin.status in (Gin.Status.ISSUED, Gin.Status.CANCELLED, Gin.Status.CLOSED):
        raise ValidationError(f'Không thể hủy GIN khi đang ở trạng thái {gin.status}.')

    Approval.objects.filter(
        target_type=ContentType.objects.get_for_model(Gin), target_id=str(gin.pk),
        status=Approval.Status.PENDING,
    ).update(
        status=Approval.Status.REJECTED, decided_by=actor, decided_at=timezone.now(),
        decision_note='GIN đã bị hủy.',
    )

    gin.status = Gin.Status.CANCELLED
    gin.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.CANCEL, target=gin,
        description=f'Hủy GIN {gin.gin_no}.', reason=note, ip_address=ip_address,
    )
    return gin


@transaction.atomic
def start_picking(gin, actor=None, ip_address=None):
    """PENDING_APPROVAL -> PICKING (quản lý Kho đã duyệt xác nhận, gọi từ
    ``decide_gin_confirmation``) — hoặc DRAFT -> PICKING trực tiếp khi gọi lập
    trình (vd seed demo data) hoặc khi Manager/Admin tự bấm "Bắt đầu soạn hàng"
    (đã có quyền ``update`` sẵn, không cần qua duyệt), bỏ qua bước duyệt. Gợi ý
    FIFO cho từng ``GinItem``, lưu thành ``GinBatchAllocation`` (is_override=False).
    Xoá allocation cũ trước khi tạo lại, phòng trường hợp gọi lại (vd sau khi sửa
    qty_requested ở DRAFT).
    """
    gin = Gin.objects.select_for_update().get(pk=gin.pk)
    if gin.status not in (Gin.Status.DRAFT, Gin.Status.PENDING_APPROVAL):
        raise ValidationError(f'Không thể bắt đầu soạn hàng khi GIN đang ở trạng thái {gin.status}.')
    if not gin.items.exists():
        raise ValidationError('GIN chưa có dòng hàng nào.')

    for item in gin.items.select_for_update():
        item.allocations.all().delete()
        plan = suggest_fifo_batches(item.product, gin.warehouse, item.qty_requested)
        for line in plan:
            GinBatchAllocation.objects.create(
                gin_item=item, batch=line['batch'], qty_allocated=line['qty_to_issue'],
            )

    gin.status = Gin.Status.PICKING
    gin.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=gin,
        description=f'GIN {gin.gin_no}: -> PICKING, đã gợi ý FIFO.',
        ip_address=ip_address,
    )
    return gin


@transaction.atomic
def override_allocation(allocation, new_batch, reason, actor=None, ip_address=None):
    """FR-GIN-03: đổi batch khác gợi ý FIFO cho 1 dòng allocation, tại state
    PICKING. ``new_batch`` phải cùng sản phẩm, cùng kho với GIN, ``status``
    ACTIVE hoặc PARTIAL_USED — cùng tập FIFO-eligible với
    ``inventory.services.suggest_fifo_batches`` (BR-GIN-007 vẫn không cho
    chọn QUARANTINE/EXPIRED/PENDING_RECEIPT; bug fix 2026-07-27, xem
    CLAUDE.md) — và đủ ``qty_available`` cho ``qty_allocated`` hiện tại. Check
    kho lặp lại ở đây dù ``GinAllocationOverrideForm`` đã lọc theo
    ``location__warehouse`` (bug fix 2026-07-27, xem CLAUDE.md) — service
    không được phụ thuộc hoàn toàn vào queryset đã lọc của form, cùng lý do
    đã nêu trong docstring của form.

    BUG-03: khoá GIN -> Allocation -> Batch (cùng thứ tự ``issue_gin`` khoá
    GIN trước tiên) trước khi đọc/ghi — nếu không, ``issue_gin`` có thể đọc
    allocation đang trỏ batch cũ ngay trước khi override đổi nó sang batch
    mới và commit, khiến tồn kho bị trừ trên batch khác với batch mà
    allocation đã lưu lại.

    BUG-04: check ``qty_available`` phải cộng luôn các allocation KHÁC (của
    GIN này) đang cùng trỏ vào ``new_batch`` — không chỉ so với
    ``allocation.qty_allocated`` của riêng dòng đang đổi. Nếu không, 2 dòng
    cùng override sang 1 batch có thể lần lượt qua được check (mỗi lần chỉ
    so 1 mình nó với ``qty_available``) trong khi tổng cộng vượt tồn thực tế
    của batch, khiến ``issue_gin`` sau đó fail giữa chừng và GIN kẹt ở
    PICKING không có cách sửa ngoài huỷ.
    """
    if not reason:
        raise ValidationError('Phải ghi lý do khi đổi batch.')

    gin = Gin.objects.select_for_update().get(pk=allocation.gin_item.gin_id)
    if gin.status != Gin.Status.PICKING:
        raise ValidationError('Chỉ có thể đổi batch khi GIN đang ở trạng thái PICKING.')

    allocation = (
        GinBatchAllocation.objects.select_for_update()
        .select_related('gin_item__product', 'batch')
        .get(pk=allocation.pk)
    )
    gin_item = allocation.gin_item

    new_batch = (
        Batch.objects.select_for_update()
        .select_related('location__warehouse')
        .get(pk=new_batch.pk)
    )
    if new_batch.product_id != gin_item.product_id:
        raise ValidationError('Batch mới phải cùng sản phẩm với dòng hàng.')
    if new_batch.location.warehouse_id != gin.warehouse_id:
        raise ValidationError('Batch mới phải cùng kho với GIN.')
    if new_batch.status not in (Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED):
        raise ValidationError(
            'Chỉ được chọn batch đang ACTIVE hoặc đã dùng một phần (không QUARANTINE/EXPIRED).')

    other_allocated = GinBatchAllocation.objects.filter(
        gin_item__gin=gin, batch=new_batch,
    ).exclude(pk=allocation.pk).aggregate(total=Sum('qty_allocated'))['total'] or 0
    required_total = other_allocated + allocation.qty_allocated
    if required_total > new_batch.qty_available:
        raise ValidationError(
            f'Batch "{new_batch.batch_code}" chỉ còn {new_batch.qty_available}, '
            f'nhưng tổng phân bổ sau khi đổi sẽ là {required_total} '
            f'(đã có {other_allocated} từ dòng khác trong cùng GIN dùng batch này).'
        )

    old_batch = allocation.batch
    allocation.batch = new_batch
    allocation.is_override = True
    allocation.override_reason = reason
    allocation.save(update_fields=['batch', 'is_override', 'override_reason'])

    log_action(
        actor, AuditLog.Action.UPDATE, target=gin,
        description=(
            f'GIN {gin.gin_no}: đổi batch dòng {gin_item.product.product_code} '
            f'{old_batch.batch_code} -> {new_batch.batch_code}. Lý do: {reason}'
        ),
        ip_address=ip_address,
    )
    return allocation


@transaction.atomic
def issue_gin(gin, actor=None, ip_address=None):
    """PICKING -> ISSUED (FR-GIN-04): trừ ``Inventory.qty_on_hand``,
    ``Batch.qty_used`` += qty_allocated, ``GinItem.qty_issued`` = tổng
    allocation (FR-GIN-05), khoá edit.

    BR-GIN-001: kiểm tra lại ``qty_allocated <= batch.qty_available`` ngay
    trước khi trừ (phòng batch đã bị GIN khác dùng bớt sau khi suggest/
    override). BR-GIN-006: batch hết hàng (qty_available == 0 sau khi trừ)
    -> CLOSED; còn lại vẫn ACTIVE -> PARTIAL_USED.

    ``suggest_fifo_batches``/FIFO override chỉ lọc status ACTIVE/PARTIAL_USED
    và loại EXPIRED (BR-GIN-007) tại thời điểm PICKING — batch có thể hết hạn,
    chuyển QUARANTINE, hoặc bị ``transfer_stock`` sang kho khác *sau* lúc đó
    nhưng *trước* khi bấm "Xuất kho" (không có cron đồng bộ EXPIRED). Cùng
    convention "form/suggest filter, service phải tự re-validate lại" —
    issue_gin phải tự kiểm tra lại status/exp_date/product/warehouse ngay
    trước khi trừ, không tin allocation đã tạo từ lúc suggest là còn hợp lệ.
    """
    gin = Gin.objects.select_for_update().get(pk=gin.pk)
    if gin.status != Gin.Status.PICKING:
        raise ValidationError(f'Không thể xuất kho khi GIN đang ở trạng thái {gin.status}.')

    for item in gin.items.select_for_update():
        allocations = list(item.allocations.select_related('batch'))
        if not allocations:
            raise ValidationError(f'Dòng "{item.product}" chưa có batch nào được phân bổ.')

        qty_issued = 0
        for alloc in allocations:
            batch = (
                Batch.objects.select_for_update()
                .select_related('location__warehouse')
                .get(pk=alloc.batch_id)
            )
            if batch.status not in (Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED):
                raise ValidationError(
                    f'Batch "{batch.batch_code}" không còn khả dụng để xuất '
                    f'({batch.get_status_display()}).'
                )
            if batch.exp_date and batch.exp_date < timezone.now().date():
                raise ValidationError(f'Batch "{batch.batch_code}" đã hết hạn.')
            if batch.product_id != item.product_id:
                raise ValidationError('Batch không thuộc sản phẩm của dòng GIN.')
            if batch.location.warehouse_id != gin.warehouse_id:
                raise ValidationError('Batch không còn nằm trong kho xuất của GIN.')
            if alloc.qty_allocated > batch.qty_available:
                raise ValidationError(
                    f'Batch "{batch.batch_code}" không còn đủ {alloc.qty_allocated} '
                    f'(hiện chỉ còn {batch.qty_available}).'
                )
            batch.qty_used += alloc.qty_allocated
            batch.status = Batch.Status.CLOSED if batch.qty_available <= 0 else Batch.Status.PARTIAL_USED
            batch.save(update_fields=['qty_used', 'status'])

            inv = Inventory.objects.select_for_update().get(product=item.product, warehouse=gin.warehouse)
            inv.qty_on_hand -= alloc.qty_allocated
            inv.save(update_fields=['qty_on_hand', 'updated_at'])
            record_movement(
                product=item.product, warehouse=gin.warehouse, batch=batch,
                movement_type=StockMovement.MovementType.ISSUE, qty=-alloc.qty_allocated,
                reference=gin.gin_no, actor=actor,
            )
            qty_issued += alloc.qty_allocated

        item.qty_issued = qty_issued
        item.save(update_fields=['qty_issued'])

    gin.status = Gin.Status.ISSUED
    gin.issued_at = timezone.now()
    gin.save(update_fields=['status', 'issued_at'])

    log_action(
        actor, AuditLog.Action.APPROVE, target=gin,
        description=f'GIN {gin.gin_no}: PICKING -> ISSUED, đã trừ Inventory/Batch.',
        ip_address=ip_address,
    )
    return gin


@transaction.atomic
def close_gin(gin, actor=None, ip_address=None):
    """ISSUED -> CLOSED (archive)."""
    gin = Gin.objects.select_for_update().get(pk=gin.pk)
    if gin.status != Gin.Status.ISSUED:
        raise ValidationError(f'Không thể đóng GIN khi đang ở trạng thái {gin.status}.')

    gin.status = Gin.Status.CLOSED
    gin.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=gin,
        description=f'Đóng GIN: {gin.gin_no} -> CLOSED.',
        ip_address=ip_address,
    )
    return gin
