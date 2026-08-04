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
import logging
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Avg, Count, F, Q, Sum
from django.utils import timezone

from accounts.approvals import create_approval, decide_approval
from accounts.audit import log_action
from accounts.models import Approval, AuditLog, User
from accounts.notifications import notify
from catalog.models import Product
from partners.models import Supplier
from receiving.models import Grn, GrnItem

from .models import (
    ProcurementAllocation,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseRequest,
    PurchaseRequestItem,
)

logger = logging.getLogger(__name__)


def received_qty_by_product(po):
    """Qty đã nhận lũy kế theo Product cho 1 PO — nguồn tính DÙNG CHUNG cho
    ``sync_po_status`` và ``po_detail`` (PUR-FND-01). Trước đây ``po_detail`` tự
    viết 1 bản sao query riêng thiếu ``.exclude(grn__status=CANCELLED)``, khiến
    trang chi tiết và quy trình đồng bộ trạng thái báo Qty khác nhau cho cùng 1
    PO — rút thành 1 hàm để lớp bug "sửa 1 chỗ quên chỗ kia" không tái diễn.

    Loại trừ dòng GRN đã ``REJECTED`` (QC trả hàng) và toàn bộ GRN đã
    ``CANCELLED`` (huỷ coi như chưa từng nhận hàng) — giống hệt điều kiện gốc
    của ``sync_po_status``.

    Chỉ đúng khi mỗi Product xuất hiện tối đa 1 dòng trong PO
    (``UniqueConstraint('purchase_order', 'product')``, PUR-FND-06) — nếu không,
    việc so `received_by_product[product] >= qty_ordered` theo từng dòng riêng
    lẻ (thay vì tổng `qty_ordered` của mọi dòng cùng Product) sẽ sai.
    """
    return dict(
        GrnItem.objects.filter(grn__po=po)
        .exclude(status=GrnItem.Status.REJECTED)
        .exclude(grn__status=Grn.Status.CANCELLED)
        .values('product_id')
        .annotate(total=Sum('qty_received'))
        .values_list('product_id', 'total')
    )


def qty_received_by_allocation(po_item):
    """Mục 4 điểm 6: chia total_received (GRN đã ghi nhận cho product của po_item) cho các
    ProcurementAllocation ACTIVE trỏ tới po_item, theo tỷ lệ qty_allocated/qty_ordered — phần dư
    do làm tròn xuống dồn hết vào allocation CUỐI CÙNG theo pk tăng dần, đảm bảo tổng luôn khớp
    chính xác total_received (không thừa/thiếu). Trả dict {allocation_id: qty_received}.
    """
    total_received = received_qty_by_product(po_item.purchase_order).get(po_item.product_id, 0)
    allocations = list(
        po_item.allocations.filter(status=ProcurementAllocation.Status.ACTIVE).order_by('pk')
    )
    if not allocations or po_item.qty_ordered == 0:
        return {}
    result = {}
    distributed = 0
    for allocation in allocations[:-1]:
        share = (total_received * allocation.qty_allocated) // po_item.qty_ordered
        result[allocation.pk] = share
        distributed += share
    last = allocations[-1]
    result[last.pk] = total_received - distributed
    return result


def find_allocation_migration_exceptions():
    """Đọc lại (bất kỳ lúc nào, không chỉ lúc migrate) danh sách PurchaseRequestItem có
    linked_po nhưng CHƯA có ProcurementAllocation nào — dùng model thật (không phải
    apps.get_model(), vì đây không phải RunPython bên trong migration).
    """
    return list(
        PurchaseRequestItem.objects
        .filter(purchase_request__linked_po__isnull=False, allocations__isnull=True)
        .select_related('purchase_request', 'purchase_request__linked_po', 'product')
        .distinct()
    )


def _business_days_before(reference_date, business_days):
    """Trừ lùi N ngày làm việc (bỏ qua Thứ 7/CN, chưa tính lịch nghỉ lễ — đủ cho MVP theo pattern
    ⏸️ đơn giản hoá, mục 6 FSD Stage 2)."""
    current = reference_date
    counted = 0
    while counted < business_days:
        current -= timedelta(days=1)
        if current.weekday() < 5:  # 0=Thứ 2 .. 4=Thứ 6
            counted += 1
    return current


def overdue_non_catalog_items(reference_date=None, business_days=3):
    """Mục 6: dòng non-catalog (product__isnull=True) mà PR đã có Approval(department=
    PURCHASING) — mốc "PUR tiếp nhận" — quá ``business_days`` ngày làm việc mà vẫn chưa map.
    """
    reference_date = reference_date or timezone.localdate()
    threshold = _business_days_before(reference_date, business_days)
    pr_content_type = ContentType.objects.get_for_model(PurchaseRequest)
    overdue = []
    for item in PurchaseRequestItem.objects.filter(product__isnull=True).select_related('purchase_request'):
        first_pur_approval = (
            Approval.objects.filter(
                target_type=pr_content_type, target_id=str(item.purchase_request_id),
                department=User.Department.PURCHASING,
            ).order_by('submitted_at').first()
        )
        # timezone.localtime(...).date() — KHÔNG bare .date() trên datetime UTC aware (CLAUDE.md:
        # so sánh ngày nghiệp vụ phải quy đổi VN-local trước, .date() trần luôn là ngày UTC).
        if first_pur_approval and timezone.localtime(first_pur_approval.submitted_at).date() <= threshold:
            overdue.append(item)
    return overdue


def find_duplicate_po_products():
    """PUR-FND-06 Bước 1 — báo cáo read-only các PO đang có ≥2 dòng cùng Product,
    chạy thủ công (``manage.py shell``/management command tạm) TRƯỚC KHI migrate
    ``UniqueConstraint(['purchase_order', 'product'])`` để biết trước quy mô dữ
    liệu cần dọn. Trả về list các tuple ``(po, product, [item_ids])``.

    Chỉ dùng ở tầng ứng dụng — migration guard (``ensure_no_duplicate_po_products``
    trong migration thêm constraint) KHÔNG được gọi lại hàm này, viết độc lập
    bằng historical model để không phụ thuộc model/service hiện tại (xem mục 9
    FSD Foundation).
    """
    groups = (
        PurchaseOrderItem.objects
        .values('purchase_order_id', 'product_id')
        .annotate(item_count=Count('id'))
        .filter(item_count__gt=1)
    )
    results = []
    for group in groups:
        item_ids = list(
            PurchaseOrderItem.objects.filter(
                purchase_order_id=group['purchase_order_id'], product_id=group['product_id'],
            ).values_list('id', flat=True)
        )
        results.append((
            PurchaseOrder.objects.get(pk=group['purchase_order_id']),
            Product.objects.get(pk=group['product_id']),
            item_ids,
        ))
    return results


def _create_allocation_locked(pr_item, po, po_item, qty, actor, ip_address=None):
    """Phần validate + ghi dữ liệu của PUR-PR-04, KHÔNG tự khoá — giả định `po`/`po_item`/`pr_item`
    caller truyền vào đã là bản `select_for_update()` mới nhất (create_allocation() khoá 1 cặp;
    build_po_from_allocations() khoá nhiều PurchaseRequestItem theo pk trước khi gọi hàm này lần
    lượt cho từng cặp — xem Task 2.5)."""
    if po.source != PurchaseOrder.Source.FROM_PR:
        raise ValidationError('Chỉ tạo được phân bổ cho PO nguồn Từ yêu cầu mua hàng.')
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError(
            f'Chỉ tạo được phân bổ khi PO đang ở trạng thái Nháp (hiện tại: {po.get_status_display()}).')
    if pr_item.purchase_request.status != PurchaseRequest.Status.APPROVED:
        raise ValidationError('Chỉ tạo được phân bổ cho dòng PR đã duyệt.')
    if pr_item.product_id is None:
        raise ValidationError('Dòng yêu cầu mua hàng này chưa được map sang sản phẩm trong danh mục.')
    if pr_item.product_id != po_item.product_id:
        raise ValidationError('Sản phẩm của dòng PR và dòng PO không khớp.')
    if qty < 1:
        raise ValidationError('Số lượng phân bổ phải lớn hơn 0.')
    if qty > pr_item.qty_open:
        raise ValidationError(f'Số lượng phân bổ ({qty}) vượt quá số lượng còn mở ({pr_item.qty_open}).')

    allocation = ProcurementAllocation.objects.create(
        pr_item=pr_item, po_item=po_item, qty_allocated=qty,
        po_no_snapshot=po.po_no, product_code_snapshot=po_item.product.product_code,
        created_by=actor,
    )
    po_item.qty_ordered = F('qty_ordered') + qty
    po_item.save(update_fields=['qty_ordered'])
    po_item.refresh_from_db(fields=['qty_ordered'])  # F() -> phải refresh trước khi dùng lại giá trị số

    log_action(
        actor, AuditLog.Action.CREATE, target=allocation,
        description=(
            f'Phân bổ {qty} từ dòng PR "{pr_item}" sang PO "{po.po_no}" '
            f'(sản phẩm {po_item.product.product_code}) — qty_ordered mới: {po_item.qty_ordered}.'
        ),
        ip_address=ip_address,
    )
    return allocation


@transaction.atomic
def create_allocation(pr_item, po_item, qty, actor, ip_address=None):
    """PUR-PR-04: tạo 1 ProcurementAllocation, đồng thời tăng po_item.qty_ordered
    đúng bằng qty (mục 4 điểm 4 FSD Stage 2 — điểm DUY NHẤT được phép tăng qty_ordered
    của PO nguồn FROM_PR). Lock order: PurchaseOrder -> PurchaseOrderItem ->
    PurchaseRequestItem -> ProcurementAllocation (mục 4 điểm 2).

    ``select_for_update(of=('self',))`` khi kết hợp ``select_related('product')``
    (mẫu BUG-16, xem CLAUDE.md / stocktake.services.apply_adjustment) — Postgres
    ``FOR UPDATE`` không kèm ``OF <table>`` sẽ khoá LUÔN mọi bảng JOIN trong câu
    query, nên nếu không giới hạn ``of`` thì dòng ``Product`` bị khoá "oan" theo,
    dù hàm này không ghi vào ``Product`` — chỉ join để tránh N+1 khi đọc
    ``po_item.product``. Giới hạn ``of=('self',)`` để chỉ khoá ``PurchaseOrderItem``.
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po_item.purchase_order_id)
    po_item = (
        PurchaseOrderItem.objects
        .select_related('product')
        .select_for_update(of=('self',))
        .get(pk=po_item.pk)
    )
    pr_item = PurchaseRequestItem.objects.select_for_update().get(pk=pr_item.pk)
    return _create_allocation_locked(pr_item, po, po_item, qty, actor, ip_address=ip_address)


def _release_allocation_locked(allocation, po, po_item, pr_item, reason, actor, ip_address=None):
    """Phần validate + ghi dữ liệu của việc giải phóng 1 allocation, KHÔNG tự khoá — giả định
    `allocation`/`po`/`po_item`/`pr_item` caller truyền vào đã là bản `select_for_update()` mới
    nhất. Trả về `qty_released` (int thật, đã refresh) để caller tự quyết định có xoá po_item
    rỗng hay không (release_allocation() xoá ngay; delete_draft_po_item_with_allocations() gọi
    với ý định xoá po_item đúng 1 lần ở cuối vòng lặp, không xoá lặp lại ở đây)."""
    if allocation.status != ProcurementAllocation.Status.ACTIVE:
        raise ValidationError('Chỉ giải phóng được phân bổ đang hiệu lực.')
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError(
            f'Chỉ giải phóng được phân bổ khi PO đang ở trạng thái Nháp (hiện tại: {po.get_status_display()}).')

    qty_released = allocation.qty_allocated
    allocation.status = ProcurementAllocation.Status.RELEASED
    allocation.released_reason = reason
    allocation.released_by = actor
    allocation.released_at = timezone.now()
    allocation.save(update_fields=['status', 'released_reason', 'released_by', 'released_at'])

    po_item.qty_ordered = F('qty_ordered') - qty_released
    po_item.save(update_fields=['qty_ordered'])
    po_item.refresh_from_db(fields=['qty_ordered'])

    log_action(
        actor, AuditLog.Action.UPDATE, target=allocation,
        description=(
            f'Giải phóng phân bổ {qty_released} của dòng PR "{pr_item}" khỏi PO "{po.po_no}" '
            f'— lý do: {reason}. qty_ordered mới: {po_item.qty_ordered}.'
        ),
        ip_address=ip_address,
    )
    notify(pr_item.purchase_request.requested_by, (
        f'Phân bổ {qty_released} của dòng yêu cầu mua hàng "{pr_item}" vừa được giải phóng khỏi '
        f'PO "{po.po_no}" — số lượng còn mở đã tăng trở lại.'
    ), target=pr_item.purchase_request)
    return qty_released


@transaction.atomic
def release_allocation(allocation, reason, actor, *, delete_empty_po_item=True, ip_address=None):
    """Chuyển 1 ProcurementAllocation ACTIVE -> RELEASED, trừ po_item.qty_ordered
    tương ứng trong cùng transaction (mục 3/mục 4 điểm 4). ``delete_empty_po_item=False``
    dùng bởi delete_draft_po_item_with_allocations() để tự quản lý xoá po_item đúng 1 lần
    (Nghiêm trọng #4, review lần 3).

    Cùng mẫu BUG-16 như ``create_allocation`` (xem CLAUDE.md /
    stocktake.services.apply_adjustment): dùng ``select_for_update(of=('self',))`` khi kết hợp
    ``select_related('product')`` để không khoá "oan" dòng ``Product`` — chỉ join tránh N+1.
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Bắt buộc nhập lý do khi giải phóng phân bổ.')
    allocation = ProcurementAllocation.objects.select_related('po_item', 'pr_item').get(pk=allocation.pk)
    if allocation.status != ProcurementAllocation.Status.ACTIVE:
        raise ValidationError('Chỉ giải phóng được phân bổ đang hiệu lực.')
    if allocation.po_item_id is None:
        raise ValidationError('Phân bổ này không còn gắn với dòng PO nào.')

    po = PurchaseOrder.objects.select_for_update().get(pk=allocation.po_item.purchase_order_id)
    po_item = (
        PurchaseOrderItem.objects
        .select_related('product')
        .select_for_update(of=('self',))
        .get(pk=allocation.po_item_id)
    )
    pr_item = PurchaseRequestItem.objects.select_for_update().get(pk=allocation.pr_item_id)
    allocation = ProcurementAllocation.objects.select_for_update().get(pk=allocation.pk)

    _release_allocation_locked(allocation, po, po_item, pr_item, reason, actor, ip_address=ip_address)

    deleted = False
    if delete_empty_po_item and po_item.qty_ordered == 0:
        po_item_repr = str(po_item)
        po_item.delete()
        deleted = True
        # po_item FK là on_delete=SET_NULL: DB đã tự SET NULL po_item_id của allocation,
        # nhưng object `allocation` đang giữ trong tay vẫn còn giá trị cũ trong bộ nhớ.
        allocation.refresh_from_db(fields=['po_item'])
        log_action(
            actor, AuditLog.Action.DELETE, target=po,
            description=f'Xoá dòng PO-item "{po_item_repr}" khỏi PO "{po.po_no}" — hết số lượng sau khi giải phóng.',
            ip_address=ip_address,
        )
    return allocation, deleted


@transaction.atomic
def delete_draft_po_item_with_allocations(po_item, actor, ip_address=None):
    """Nghiêm trọng #4 review lần 3: điều phối release TOÀN BỘ allocation ACTIVE
    của po_item rồi xoá hẳn po_item ĐÚNG 1 LẦN — tránh 2 tầng (release_allocation
    tự xoá khi về 0, formset.save() xoá lại) cùng xoá 1 row.

    Khoá TOÀN BỘ PurchaseRequestItem liên quan theo pk tăng dần MỘT LẦN trước, rồi TOÀN BỘ
    ProcurementAllocation liên quan theo pk, thay vì gọi release_allocation() công khai lặp lại
    (nó tự khoá lại từ đầu cho từng allocation, tạo thứ tự khoá xen kẽ PRItem→Allocation→PRItem→
    Allocation — nguy cơ deadlock thật nếu 2 lời gọi song song trên 2 po_item khác nhau có tập
    pr_item chung theo thứ tự tương đối khác nhau).

    ``select_for_update(of=('self',))`` khi kết hợp ``select_related('product')`` (mẫu BUG-16,
    xem CLAUDE.md) — nếu không giới hạn ``of`` thì Postgres ``FOR UPDATE`` khoá luôn dòng
    ``Product`` join vào, dù hàm này không ghi vào ``Product``.
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po_item.purchase_order_id)
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError('Chỉ xoá được dòng PO-item khi PO đang ở trạng thái Nháp.')
    po_item = (
        PurchaseOrderItem.objects
        .select_related('product')
        .select_for_update(of=('self',))
        .get(pk=po_item.pk)
    )

    pr_item_ids_sorted = sorted(
        ProcurementAllocation.objects.filter(po_item=po_item, status=ProcurementAllocation.Status.ACTIVE)
        .values_list('pr_item_id', flat=True).distinct()
    )
    locked_pr_items = {
        obj.pk: obj
        for obj in PurchaseRequestItem.objects.select_for_update()
        .filter(pk__in=pr_item_ids_sorted).order_by('pk')
    }
    active_allocations = list(
        ProcurementAllocation.objects.select_for_update()
        .filter(po_item=po_item, status=ProcurementAllocation.Status.ACTIVE)
        .order_by('pk')
    )
    for allocation in active_allocations:
        _release_allocation_locked(
            allocation, po, po_item, locked_pr_items[allocation.pr_item_id],
            reason=f'PO-item bị xoá khỏi PO {po.po_no} khi còn Nháp.', actor=actor, ip_address=ip_address,
        )

    po_item_repr = str(po_item)
    po_item.refresh_from_db()
    po_item.delete()
    log_action(
        actor, AuditLog.Action.DELETE, target=po,
        description=f'Xoá dòng PO-item "{po_item_repr}" khỏi PO "{po.po_no}".',
        ip_address=ip_address,
    )
    return po_item_repr


@transaction.atomic
def build_po_from_allocations(supplier, allocation_requests, unit_price_by_product, actor,
                               expected_delivery_date=None, ip_address=None):
    """PUR-PR-05 (po_build_from_pr_lines): tạo PurchaseOrder(DRAFT, FROM_PR) từ nhiều dòng PR,
    gộp theo product thành đúng 1 PurchaseOrderItem/product (PUR-FND-06). ``qty_ordered=0`` khởi
    tạo là giá trị TẠM trong transaction chưa commit (mục 4 điểm 4) — _create_allocation_locked()
    tự cộng dồn tới giá trị thật cho từng cặp, hàm này không tự tính tổng.

    Khoá TOÀN BỘ PurchaseRequestItem liên quan theo pk tăng dần MỘT LẦN trước, rồi lặp qua
    allocation_requests gọi _create_allocation_locked() (không tự khoá) cho từng cặp — không gọi
    lại create_allocation() công khai, vì nó tự khoá lại PurchaseRequestItem theo thứ tự caller
    truyền vào (thứ tự người dùng chọn dòng trên UI), và 2 lời gọi song song cùng tập pr_item
    nhưng khác thứ tự chọn có thể khoá ngược chiều nhau -> deadlock thật. po/po_item không cần
    khoá thêm ở đây: cả hai đều được tạo mới trong chính transaction này, chưa transaction nào
    khác có thể tham chiếu tới pk của chúng trước khi commit.

    allocation_requests: list[(pr_item, qty)]. unit_price_by_product: {product_id: Decimal}.
    """
    if not allocation_requests:
        raise ValidationError('Phải chọn ít nhất 1 dòng yêu cầu mua hàng để tạo PO.')

    # Khoá + đọc lại supplier từ DB — instance caller truyền vào (lấy từ form/queryset trước đó)
    # có thể đã stale nếu status bị đổi ở transaction khác giữa lúc form filter và lúc submit.
    supplier = Supplier.objects.select_for_update().get(pk=supplier.pk)
    if supplier.status != Supplier.Status.ACTIVE:
        raise ValidationError(
            f'Nhà cung cấp "{supplier.name}" đã ngừng giao dịch hoặc bị tạm khóa, không thể tạo PO.')

    po = PurchaseOrder.objects.create(
        supplier=supplier, source=PurchaseOrder.Source.FROM_PR, created_by=actor,
        expected_delivery_date=expected_delivery_date,
    )

    pr_item_ids_sorted = sorted({pr_item.pk for pr_item, _qty in allocation_requests})
    locked_pr_items = {
        obj.pk: obj
        for obj in PurchaseRequestItem.objects.select_for_update()
        .filter(pk__in=pr_item_ids_sorted).order_by('pk')
    }

    po_item_by_product = {}
    for pr_item, qty in allocation_requests:
        pr_item = locked_pr_items[pr_item.pk]  # dùng bản đã khoá — không dùng object caller truyền vào
        if pr_item.product_id is None:
            raise ValidationError(f'Dòng yêu cầu mua hàng "{pr_item}" chưa được map sang sản phẩm.')
        product_id = pr_item.product_id
        if product_id not in po_item_by_product:
            unit_price = unit_price_by_product.get(product_id)
            if unit_price is None:
                raise ValidationError(f'Thiếu đơn giá cho sản phẩm "{pr_item.product.product_code}".')
            if unit_price < 0:
                raise ValidationError(
                    f'Đơn giá cho sản phẩm "{pr_item.product.product_code}" không được âm.')
            po_item_by_product[product_id] = PurchaseOrderItem.objects.create(
                purchase_order=po, product=pr_item.product, qty_ordered=0, unit_price=unit_price)
        _create_allocation_locked(pr_item, po, po_item_by_product[product_id], qty, actor, ip_address=ip_address)

    log_action(
        actor, AuditLog.Action.CREATE, target=po,
        description=f'Tạo PO {po.po_no} từ {len(allocation_requests)} dòng yêu cầu mua hàng.',
        ip_address=ip_address,
    )
    return po


@transaction.atomic
def reconcile_legacy_po_item_allocations(po_item, allocations, actor, ip_address=None):
    """T9 (review lần 4/5): recovery procedure MỘT LẦN cho PO legacy backfill từ linked_po
    (mục 9 migration 0018) không khớp allocation tự động được. TẠO THÊM allocation khớp CHÍNH
    XÁC qty_ordered hiện có — KHÔNG cộng thêm (ngoại lệ duy nhất so với create_allocation()).
    Chỉ gọi qua management command `reconcile_legacy_po_item_allocations` (Task 4.3), không lộ
    ra UI/luồng tạo PO thông thường.

    ``allocations``: list[(pr_item, qty)] — không rỗng, mỗi pr_item chỉ 1 lần.

    Lock order (đặc biệt quan trọng — đường DUY NHẤT tạo allocation cho PO legacy đã APPROVED,
    có thể chạy đồng thời với send_po() trên cùng PO):
    PurchaseOrder -> PurchaseOrderItem -> PurchaseRequestItem (pk asc) -> ProcurementAllocation (pk asc).
    ``select_for_update(of=('self',))`` khi kết hợp ``select_related(...)`` trên cả po_item
    (join 'product') và trên batch pr_item (join 'purchase_request', 'product') — mẫu BUG-16
    (xem CLAUDE.md): không giới hạn ``of`` sẽ khoá luôn các bảng join vào. Với batch pr_item,
    đây không chỉ là dư thừa: ``PurchaseRequestItem.product`` là FK nullable (dòng non-catalog),
    nên ``select_related('product')`` tạo LEFT OUTER JOIN — Postgres từ chối thẳng
    ``FOR UPDATE`` không giới hạn ``of`` trên join đó (``NotSupportedError: FOR UPDATE cannot be
    applied to the nullable side of an outer join``), không chỉ là nguy cơ khoá oan như các chỗ
    BUG-16 khác trong file này.

    Thiết kế 2 lượt (validate-toàn-bộ-trước, tạo-toàn-bộ-sau) — không tạo allocation nào trong
    lượt validate, nên 1 dòng sai ở giữa batch không để lại allocation dở dang của các dòng hợp
    lệ đứng trước nó (AC #30), không chỉ dựa vào rollback transaction.
    """
    # (1) actor — kiểm trước khi khoá gì (thuần thuộc tính actor, không cần DB lock).
    if not (actor.role == User.Role.ADMIN or actor.is_superuser):
        raise ValidationError('Chỉ Admin/superuser mới chạy được reconciliation.')
    if not actor.is_active or actor.is_deleted:
        raise ValidationError('Tài khoản actor phải đang hoạt động (không bị khoá/xoá mềm).')
    # (4) batch không rỗng.
    if not allocations:
        raise ValidationError('Danh sách allocation không được rỗng.')
    # (5) không trùng pr_item trong input — kiểm trước khi khoá, thuần trên list truyền vào.
    pr_item_ids = [pr_item.pk for pr_item, _qty in allocations]
    if len(pr_item_ids) != len(set(pr_item_ids)):
        raise ValidationError('Một dòng yêu cầu mua hàng không được xuất hiện quá 1 lần trong batch.')

    # Lock order: PurchaseOrder -> PurchaseOrderItem -> PurchaseRequestItem (pk asc) -> ProcurementAllocation (pk asc)
    po = PurchaseOrder.objects.select_for_update().get(pk=po_item.purchase_order_id)
    po_item = (
        PurchaseOrderItem.objects
        .select_related('product')
        .select_for_update(of=('self',))
        .get(pk=po_item.pk)
    )
    locked_pr_items = {
        item.pk: item
        for item in PurchaseRequestItem.objects
        .select_related('purchase_request', 'product')
        .select_for_update(of=('self',))
        .filter(pk__in=pr_item_ids).order_by('pk')
    }
    existing_allocations = list(
        ProcurementAllocation.objects.select_for_update()
        .filter(po_item=po_item, status=ProcurementAllocation.Status.ACTIVE).order_by('pk')
    )

    # (2)(3) PO nguồn + trạng thái.
    if po.source != PurchaseOrder.Source.FROM_PR:
        raise ValidationError('Chỉ reconcile được PO nguồn Từ yêu cầu mua hàng.')
    if po.status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.APPROVED):
        raise ValidationError(f'Không thể reconcile khi PO đang ở trạng thái {po.get_status_display()}.')

    existing_total = sum(a.qty_allocated for a in existing_allocations)
    existing_pr_item_ids = {a.pr_item_id for a in existing_allocations}
    batch_total = 0
    for pr_item, qty in allocations:
        locked_pr_item = locked_pr_items[pr_item.pk]
        # (6) PR đã duyệt.
        if locked_pr_item.purchase_request.status != PurchaseRequest.Status.APPROVED:
            raise ValidationError(f'Dòng PR "{locked_pr_item}" chưa ở trạng thái Đã duyệt.')
        # (7) product khớp.
        if locked_pr_item.product_id != po_item.product_id:
            raise ValidationError(f'Sản phẩm của dòng PR "{locked_pr_item}" không khớp dòng PO.')
        # (8) qty >= 1.
        if qty < 1:
            raise ValidationError(f'Số lượng của dòng "{locked_pr_item}" phải lớn hơn 0.')
        # (9) linked_po BẮT BUỘC khớp — rỗng cũng reject (review lần 5 điểm 3).
        if locked_pr_item.purchase_request.linked_po_id != po_item.purchase_order_id:
            raise ValidationError(
                f'Dòng PR "{locked_pr_item}" chưa từng liên kết đúng PO này qua linked_po — '
                f'ngoài phạm vi recovery procedure này, cần điều tra riêng.')
        # (11) không trùng allocation ACTIVE đã có cho đúng cặp.
        if locked_pr_item.pk in existing_pr_item_ids:
            raise ValidationError(f'Đã tồn tại allocation đang hiệu lực cho dòng PR "{locked_pr_item}".')
        # (10) qty <= qty_open, tính SAU khi đã khoá toàn bộ pr_item trong batch (review lần 5 điểm 4).
        if qty > locked_pr_item.qty_open:
            raise ValidationError(
                f'Số lượng ({qty}) vượt quá số lượng còn mở ({locked_pr_item.qty_open}) của dòng "{locked_pr_item}".')
        batch_total += qty

    # Rule tổng: existing + batch phải khớp CHÍNH XÁC qty_ordered (==, không phải <=).
    if existing_total + batch_total != po_item.qty_ordered:
        raise ValidationError(
            f'Tổng allocation sau khi reconcile ({existing_total + batch_total}) không khớp chính xác '
            f'qty_ordered ({po_item.qty_ordered}).')

    # (12) tạo toàn bộ — chỉ chạy sau khi TOÀN BỘ batch đã validate sạch ở trên.
    created = []
    for pr_item, qty in allocations:
        locked_pr_item = locked_pr_items[pr_item.pk]
        created.append(ProcurementAllocation.objects.create(
            pr_item=locked_pr_item, po_item=po_item, qty_allocated=qty,
            po_no_snapshot=po.po_no, product_code_snapshot=po_item.product.product_code,
            created_by=actor,
        ))

    # (14) re-assert lần cuối trước khi commit.
    final_total = (
        ProcurementAllocation.objects.filter(po_item=po_item, status=ProcurementAllocation.Status.ACTIVE)
        .aggregate(total=Sum('qty_allocated'))['total'] or 0
    )
    if final_total != po_item.qty_ordered:
        raise ValidationError('Re-assert thất bại: tổng allocation cuối cùng không khớp qty_ordered — rollback.')

    # (13) 1 dòng AuditLog cho cả batch. `detail` nhúng str(pr_item) không giới hạn độ dài theo
    # số dòng trong batch -> đưa vào `reason` (TextField, không giới hạn), không phải
    # `description` (CharField(255) — tràn sẽ raise StringDataRightTruncation, rollback cả
    # transaction, xem CLAUDE.md mục log_action).
    detail = '; '.join(f'{locked_pr_items[pr_item.pk]}: {qty}' for pr_item, qty in allocations)
    log_action(
        actor, AuditLog.Action.CREATE, target=po,
        description=f'Reconcile legacy allocation cho dòng PO-item #{po_item.pk} (PO {po.po_no}).',
        reason=detail,
        ip_address=ip_address,
    )
    return created


def sync_po_status(po):
    """Đối chiếu qty_received lũy kế so với qty_ordered từng dòng PO.

    - Mọi dòng đã nhận đủ (>=) -> ``RECEIVED`` (set ``received_at`` lần đầu).
    - Có nhận nhưng chưa đủ -> ``PARTIAL_RECEIVED``.
    - Chưa nhận gì -> ``SENT`` (downgrade nếu trước đó đã lên PARTIAL_RECEIVED/
      RECEIVED nhờ 1 GRN mà nay đã bị hủy — xem ``cancel_grn``).

    Dòng thuộc GRN đã ``CANCELLED`` bị loại khỏi tổng, giống hệt dòng bị QC
    REJECTED — GRN hủy coi như chưa từng nhận hàng. PO đã ``CLOSED`` không bị
    hàm này đổi lại status (tránh 1 GRN dở dang vô tình "hồi sinh" PO đã đóng).
    """
    if po.status == PurchaseOrder.Status.CLOSED:
        return po

    po_items = list(PurchaseOrderItem.objects.filter(purchase_order=po))
    if not po_items:
        return po

    received_by_product = received_qty_by_product(po)

    all_fulfilled = all(
        received_by_product.get(item.product_id, 0) >= item.qty_ordered for item in po_items)
    any_received = any(
        received_by_product.get(item.product_id, 0) > 0 for item in po_items)

    if all_fulfilled:
        new_status = PurchaseOrder.Status.RECEIVED
    elif any_received:
        new_status = PurchaseOrder.Status.PARTIAL_RECEIVED
    elif po.status in (PurchaseOrder.Status.PARTIAL_RECEIVED, PurchaseOrder.Status.RECEIVED):
        new_status = PurchaseOrder.Status.SENT
    else:
        return po

    if po.status != new_status:
        update_fields = ['status']
        po.status = new_status
        if new_status == PurchaseOrder.Status.RECEIVED and not po.received_at:
            po.received_at = timezone.localdate()
            update_fields.append('received_at')
        elif new_status != PurchaseOrder.Status.RECEIVED and po.received_at:
            po.received_at = None
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


_SEND_PO_EMAIL_LOG_DESCRIPTIONS = {
    PurchaseOrder.EmailStatus.SENT:
        'Gửi PO: {po_no} APPROVED -> SENT. Đã gửi email tới NCC ({email}).',
    PurchaseOrder.EmailStatus.FAILED:
        'Gửi PO: {po_no} APPROVED -> SENT. Gửi email tới NCC ({email}) thất bại.',
    PurchaseOrder.EmailStatus.SKIPPED_NO_EMAIL:
        'Gửi PO: {po_no} APPROVED -> SENT. NCC chưa có email, chỉ cập nhật trạng thái.',
}


@transaction.atomic
def send_po(po, actor=None, ip_address=None):
    """APPROVED -> SENT (gửi PO tới NCC, khoá sửa).

    ``sent_at`` (PUR-FND-03) set VÔ ĐIỀU KIỆN ngay khi transition xảy ra —
    thời điểm hệ thống phát hành/thử gửi PO, không phải lúc NCC xác nhận đã
    nhận. Không phụ thuộc kết quả email — kể cả ``email_status`` sau đó là
    ``FAILED``/``SKIPPED_NO_EMAIL``, ``sent_at`` vẫn có giá trị, vì bản thân
    transition PO vẫn xảy ra, chỉ email là kênh thông báo phụ. Dùng làm mốc
    lead-time thực tế NCC (``supplier_lead_time_stats()``) thay cho
    ``created_at`` (thời điểm tạo nháp, không phải lúc gửi thật).

    Best-effort: nếu NCC có ``contact_email`` thì gửi kèm 1 email thông báo
    (mirror style ``accounts.views._send_account_created_email`` — tiếng Việt,
    ``from_email=None`` dùng ``DEFAULT_FROM_EMAIL``). Không có email thì bỏ
    qua, không chặn transition — việc gửi PO cho NCC vẫn có thể thực hiện qua
    kênh khác (điện thoại, fax...), hệ thống chỉ hỗ trợ thêm chứ không bắt
    buộc. Email thất bại (exception hoặc ``send_mail()`` trả về 0) cũng không
    rollback transition — ``_send_po_email()`` tự bắt exception bên trong nó
    (PUR-FND-02), nên lỗi SMTP không bao giờ khiến transaction này rollback.

    ``email_status`` PHẢI được ``save(update_fields=['email_status'])`` riêng
    sau khi gọi ``_send_po_email()`` — set trên instance không đủ, chưa lưu
    xuống DB thì mất vĩnh viễn kết quả gửi (PUR-FND-02, mục 3 "Transaction/save
    behavior").
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    if po.status != PurchaseOrder.Status.APPROVED:
        raise ValidationError(f'Không thể gửi PO khi đang ở trạng thái {po.status}.')

    if po.source == PurchaseOrder.Source.FROM_PR:
        mismatched_lines = []
        for po_item in po.items.select_related('product'):
            total_allocated = po_item.allocations.filter(
                status=ProcurementAllocation.Status.ACTIVE,
            ).aggregate(total=Sum('qty_allocated'))['total'] or 0
            if po_item.qty_ordered != total_allocated:
                mismatched_lines.append(
                    f'{po_item.product.product_code} (đặt: {po_item.qty_ordered}, đã phân bổ: {total_allocated})')
        if mismatched_lines:
            raise ValidationError(
                'PO có dòng chưa khớp số lượng phân bổ, không thể gửi NCC: ' + '; '.join(mismatched_lines))

    po.status = PurchaseOrder.Status.SENT
    po.sent_at = timezone.now()
    po.save(update_fields=['status', 'sent_at'])

    email_status = _send_po_email(po)
    po.email_status = email_status
    po.save(update_fields=['email_status'])

    log_action(
        actor, AuditLog.Action.UPDATE, target=po,
        description=_SEND_PO_EMAIL_LOG_DESCRIPTIONS[email_status].format(
            po_no=po.po_no, email=po.supplier.contact_email),
        ip_address=ip_address,
    )
    return po


def _send_po_email(po):
    """Gửi email PO cho NCC (PUR-FND-02). Trả về đúng 1 trong 3 giá trị
    ``PurchaseOrder.EmailStatus`` khả dĩ tại runtime (``SENT``/``FAILED``/
    ``SKIPPED_NO_EMAIL`` — ``NOT_ATTEMPTED``/``UNKNOWN_LEGACY`` không bao giờ
    do hàm này trả về). ``SENT`` chỉ khi ``send_mail()`` không raise VÀ trả về
    > 0 (số email gửi thành công) — không chỉ dựa vào "không có exception",
    vì ``send_mail()`` có thể trả về ``0`` mà không raise (backend chấp nhận
    nhưng không gửi được).
    """
    if not po.supplier.contact_email:
        return PurchaseOrder.EmailStatus.SKIPPED_NO_EMAIL
    lines = [
        f'Kính gửi {po.supplier.name},\n',
        f'NVL/WMS gửi đơn mua hàng {po.po_no} với nội dung như sau:\n',
    ]
    for item in po.items.select_related('product').all():
        lines.append(f'- {item.product.product_code} ({item.product.name}): {item.qty_ordered} x {item.unit_price}')
    lines.append('')
    lines.append(f'Ngày giao dự kiến: {po.expected_delivery_date or "chưa xác định"}.')
    lines.append('\nVui lòng xác nhận và giao hàng đúng hẹn. Xin cảm ơn.')
    try:
        sent_count = send_mail(
            subject=f'[NVL/WMS] Đơn mua hàng {po.po_no}',
            message='\n'.join(lines),
            from_email=None,  # dùng DEFAULT_FROM_EMAIL
            recipient_list=[po.supplier.contact_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Gửi email PO %s cho NCC %s thất bại.', po.po_no, po.supplier.contact_email)
        return PurchaseOrder.EmailStatus.FAILED
    return PurchaseOrder.EmailStatus.SENT if sent_count > 0 else PurchaseOrder.EmailStatus.FAILED


_RETRY_PO_EMAIL_LOG_DESCRIPTIONS = {
    PurchaseOrder.EmailStatus.SENT:
        'Gửi lại email PO: {po_no} (SENT) — đã gửi email tới NCC ({email}).',
    PurchaseOrder.EmailStatus.FAILED:
        'Gửi lại email PO: {po_no} (SENT) — gửi email tới NCC ({email}) thất bại.',
    PurchaseOrder.EmailStatus.SKIPPED_NO_EMAIL:
        'Gửi lại email PO: {po_no} (SENT) — NCC chưa có email, chỉ cập nhật trạng thái.',
}


@transaction.atomic
def retry_po_email(po, actor=None, ip_address=None):
    """PUR-FND-07 — gửi lại email PO khi lần gửi trong ``send_po()`` thất bại
    hoặc NCC lúc đó chưa có email. KHÔNG chạy lại transition
    ``APPROVED -> SENT`` (``status``/``sent_at`` giữ nguyên), chỉ gửi lại email
    và cập nhật ``email_status``. Dùng lại nguyên ``_send_po_email()`` của
    PUR-FND-02 để 2 đường gửi (lần đầu qua ``send_po()``, gửi lại qua đây)
    không bao giờ lệch điều kiện SENT/FAILED với nhau.

    2 điều kiện độc lập, CẢ HAI đều bắt buộc — thiếu 1 trong 2 đều
    ``ValidationError`` (không gọi ``send_mail``, không tạo ``AuditLog`` mới):
    1. ``po.status == SENT`` và ``po.email_status in (FAILED, SKIPPED_NO_EMAIL)``.
    2. ``po.supplier.contact_email`` (đọc lại từ DB tại thời điểm gọi — ``po``
       vừa được ``select_for_update()`` lại nên không dùng instance/quan hệ đã
       cache của caller) phải khác rỗng. Đây là con đường hợp lệ DUY NHẤT để
       thoát khỏi ``SKIPPED_NO_EMAIL``: bỏ qua check này sẽ cho phép gọi vô
       nghĩa khi NCC vẫn chưa có email (chắc chắn lại trả ``SKIPPED_NO_EMAIL``,
       sinh ``AuditLog`` rác mỗi lần bấm).

    Giới hạn đã biết (không phải bug): không có cơ chế tự động phát hiện/ngăn
    gửi trùng — nếu email đã gửi thành công nhưng lưu DB ngay sau đó tự lỗi
    khiến transaction rollback, người xử lý phải tự xác nhận với NCC trước khi
    gửi lại thủ công (mục 3 FSD Foundation).
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    if po.status != PurchaseOrder.Status.SENT or po.email_status not in (
            PurchaseOrder.EmailStatus.FAILED, PurchaseOrder.EmailStatus.SKIPPED_NO_EMAIL):
        raise ValidationError('PO chưa ở trạng thái có thể gửi lại email.')
    if not po.supplier.contact_email:
        raise ValidationError('Nhà cung cấp chưa có email, vui lòng bổ sung trước khi gửi lại.')

    email_status = _send_po_email(po)
    po.email_status = email_status
    po.save(update_fields=['email_status'])

    log_action(
        actor, AuditLog.Action.UPDATE, target=po,
        description=_RETRY_PO_EMAIL_LOG_DESCRIPTIONS[email_status].format(
            po_no=po.po_no, email=po.supplier.contact_email),
        ip_address=ip_address,
    )
    return po


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
    """FR-PO-05/FR-PO-06/PUR-FND-03: với mỗi NCC active, so sánh
    ``lead_time_days`` cấu hình với lead-time thực tế (``received_at -
    sent_at``, không phải ``created_at`` — ``created_at`` là lúc tạo bản nháp,
    không phải lúc PO thật sự gửi NCC), đếm số PO đúng hạn/trễ hạn so với
    ``expected_delivery_date``. Chỉ tính PO có CẢ HAI ``sent_at``/``received_at``
    — PO cũ trước khi có ``sent_at`` (PUR-FND-02) bị loại khỏi thống kê thay vì
    tính sai bằng ``created_at``. Tính on-the-fly khi load trang (⏸️ không
    cron/Celery, theo CLAUDE.md).

    ``received_at`` là ``DateField`` còn ``sent_at`` là ``DateTimeField`` — 2
    kiểu này không trừ trực tiếp được (``date.__sub__(datetime)`` raise
    ``TypeError``), bắt buộc ép ``sent_at`` về ``date`` theo giờ Việt Nam
    trước (``timezone.localtime()``, không phải ``.date()`` trần trên
    datetime UTC — quy ước so sánh ngày theo giờ VN, xem CLAUDE.md).
    """
    results = []
    for supplier in Supplier.objects.filter(status=Supplier.Status.ACTIVE):
        received_pos = PurchaseOrder.objects.filter(
            supplier=supplier, received_at__isnull=False, sent_at__isnull=False)
        lead_times = [
            (po.received_at - timezone.localtime(po.sent_at).date()).days for po in received_pos
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
    if not pr.department_snapshot:
        pr.department_snapshot = origin_department
    pr.save(update_fields=['status', 'department_snapshot'])
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


MAPPABLE_PR_STATUSES = {
    PurchaseRequest.Status.PENDING_DEPT,
    PurchaseRequest.Status.PENDING_PUR,
    PurchaseRequest.Status.APPROVED,
}


@transaction.atomic
def map_non_catalog_item(pr_item, product, actor, ip_address=None):
    """PUR-PR-06 (quyết định #9): gán Product cho 1 dòng PR non-catalog. Chỉ gọi được khi PR đang ở
    một trong `MAPPABLE_PR_STATUSES` (mục 4 điểm 10) — dùng allow-list thay vì chặn mỗi `DRAFT`,
    vì `REJECTED` mở lại được về `DRAFT` (`reopen_purchase_request`) nên vẫn mang đúng rủi ro "Product
    rác nếu Requester đổi ý" mà rule này muốn tránh.

    ``select_for_update(of=('self',))`` khi kết hợp ``select_related('purchase_request')`` (mẫu
    BUG-16, xem CLAUDE.md) — Postgres ``FOR UPDATE`` không kèm ``OF <table>`` sẽ khoá LUÔN
    ``PurchaseRequest`` theo, dù hàm này không ghi vào ``PurchaseRequest``, chỉ join để đọc
    ``pr_item.purchase_request.status``. Không giới hạn ``of`` từng gây deadlock thật (BUG-24)
    với ``decide_purchase_request`` — hàm đó khoá theo thứ tự ``PurchaseRequest`` ->
    ``PurchaseRequestItem`` ở nhánh ``PENDING_PUR``, ngược chiều với thứ tự "khoá oan" mà join
    không giới hạn ``of`` tạo ra ở đây.
    """
    pr_item = (
        PurchaseRequestItem.objects
        .select_related('purchase_request')
        .select_for_update(of=('self',))
        .get(pk=pr_item.pk)
    )
    if pr_item.product_id is not None:
        raise ValidationError('Dòng này đã có sản phẩm, không cần map lại.')
    if pr_item.purchase_request.status not in MAPPABLE_PR_STATUSES:
        raise ValidationError('Chỉ map sản phẩm được khi yêu cầu mua hàng đang chờ duyệt hoặc đã duyệt.')

    product = Product.objects.select_for_update().get(pk=product.pk)
    if not product.is_active:
        raise ValidationError('Chỉ được map sang sản phẩm đang hoạt động.')

    old_non_catalog_name = pr_item.non_catalog_name
    pr_item.product = product
    pr_item.non_catalog_name = ''
    pr_item.non_catalog_uom = ''
    pr_item.non_catalog_note = ''
    pr_item.save(update_fields=['product', 'non_catalog_name', 'non_catalog_uom', 'non_catalog_note'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=pr_item.purchase_request,
        description=f'Map dòng PR #{pr_item.pk} sang sản phẩm "{product.product_code}".',
        changes={
            'product_id': [None, product.pk],
            'non_catalog_name': [old_non_catalog_name, ''],
        },
        ip_address=ip_address,
    )
    return pr_item


@transaction.atomic
def cancel_pr_item_open_qty(pr_item, qty, reason, actor, ip_address=None):
    """PUR-PR-07: huỷ 1 phần qty_open của dòng PR (không xoá dòng, không đổi qty_requested/
    qty_approved). Không có giới hạn "chỉ PR APPROVED" — dòng đã APPROVED mới có qty_open > 0 để
    huỷ, điều kiện tự nhiên đã chặn (mục 4 điểm 9). Hàm KHÔNG tự kiểm quyền actor — quyền kiểm ở
    view (mục 1/mục 4 điểm 9).
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Bắt buộc nhập lý do khi huỷ phần còn mở.')
    pr_item = PurchaseRequestItem.objects.select_related('purchase_request').select_for_update().get(pk=pr_item.pk)
    if qty < 1:
        raise ValidationError('Số lượng huỷ phải lớn hơn 0.')
    if qty > pr_item.qty_open:
        raise ValidationError(f'Số lượng huỷ ({qty}) vượt quá số lượng còn mở ({pr_item.qty_open}).')

    old_qty_cancelled = pr_item.qty_cancelled
    pr_item.qty_cancelled = F('qty_cancelled') + qty
    pr_item.save(update_fields=['qty_cancelled'])
    pr_item.refresh_from_db(fields=['qty_cancelled'])

    log_action(
        actor, AuditLog.Action.CANCEL, target=pr_item.purchase_request,
        description=f'Huỷ {qty} số lượng còn mở của dòng PR #{pr_item.pk}.',
        reason=reason,
        changes={'qty_cancelled': [old_qty_cancelled, pr_item.qty_cancelled]},
        ip_address=ip_address,
    )
    return pr_item


@transaction.atomic
def decide_purchase_request(approval, approved, actor, note='', ip_address=None, qty_approved_overrides=None):
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

    ``qty_approved_overrides``: dict ``{pr_item_id: int}``, chỉ áp dụng ở
    nhánh duyệt cấp ``PENDING_PUR``; dòng không có trong dict giữ mặc định
    ``qty_approved = qty_requested``.

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

    qty_approved_overrides = qty_approved_overrides or {}
    advance_to_pur = False

    def on_approve():
        nonlocal advance_to_pur
        if stage == PurchaseRequest.Status.PENDING_DEPT:
            pr.status = PurchaseRequest.Status.PENDING_PUR
            pr.save(update_fields=['status'])
            advance_to_pur = True
        else:
            items = list(pr.items.select_for_update().order_by('pk'))
            changed = {}
            for item in items:
                requested = item.qty_requested
                approved_qty = qty_approved_overrides.get(item.pk, requested)
                if approved_qty > requested:
                    raise ValidationError(f'Không được duyệt tăng số lượng dòng PR #{item.pk} (yêu cầu: {requested}).')
                if approved_qty < 0:
                    raise ValidationError(f'Số lượng duyệt của dòng PR #{item.pk} không được âm.')
                if approved_qty != requested:
                    changed[str(item.pk)] = [requested, approved_qty]
                item.qty_approved = approved_qty
                item.save(update_fields=['qty_approved'])
            if items and all(item.qty_approved == 0 for item in items):
                raise ValidationError(
                    'Không thể duyệt yêu cầu với toàn bộ dòng có số lượng duyệt = 0 — dùng "Từ chối" thay thế.')
            pr.status = PurchaseRequest.Status.APPROVED
            pr.decided_by = actor
            pr.decided_at = timezone.now()
            pr.save(update_fields=['status', 'decided_by', 'decided_at'])
            if changed:
                # description KHÔNG nhúng str(item)/giá trị tự do — non_catalog_name dài tới 200
                # ký tự có thể vượt AuditLog.description (max_length=255), cùng lớp lỗi đã sửa ở
                # cancel_pr_item_open_qty. Dùng changes= (JSONField, không giới hạn) cho chi tiết.
                log_action(
                    actor, AuditLog.Action.APPROVE, target=pr,
                    description=f'Duyệt yêu cầu {pr.request_no} — điều chỉnh số lượng duyệt {len(changed)} dòng.',
                    changes={'qty_approved': changed},
                    ip_address=ip_address,
                )
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
