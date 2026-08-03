"""View app purchasing: Purchase Order đầy đủ (Phase 5, FR-PO-01..06).

Phân quyền: 'po' LÀ module có thật trong Permission Matrix (BACKLOG mục 1a, xem
``accounts/permissions.py`` MODULES['po'] và ROLE_PERMISSIONS), nên dùng thẳng
RBAC thật qua ``user.can(action, 'po')``. Theo ma trận: MANAGER/PURCHASING/ADMIN
có Create+Update; chỉ MANAGER/ADMIN có Approve (duyệt PO, đóng PO); STAFF/QC/
ACCOUNTANT chỉ Read.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.approvals import approval_history_for, latest_approval_for
from accounts.audit import client_ip, log_action
from accounts.models import Approval, AuditLog, User
from accounts.pagination import paginate_queryset
from catalog.models import Product
from partners.models import Supplier

from .forms import (
    PrItemMapProductForm,
    PurchaseOrderCloseForm,
    PurchaseOrderForm,
    PurchaseOrderItemFormSet,
    PurchaseRequestForm,
    PurchaseRequestForwardForm,
    PurchaseRequestItemFormSet,
    PurchaseRequestRejectForm,
)
from .models import PurchaseOrder, PurchaseRequest, PurchaseRequestItem
from .services import (
    approve_po,
    cancel_pr_item_open_qty,
    close_po,
    decide_purchase_request,
    delete_purchase_request,
    forward_purchase_request,
    map_non_catalog_item,
    received_qty_by_product,
    reopen_purchase_request,
    retry_po_email,
    send_po,
    submit_purchase_request,
    supplier_lead_time_stats,
    supplier_price_history,
)


def po_permission_required(action):
    """Decorator factory: chưa đăng nhập -> login; thiếu quyền ``action`` trên
    module 'po' -> 403.
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not request.user.can(action, 'po'):
                raise PermissionDenied(f'Không có quyền "{action}" trên Purchase Order.')
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def pr_permission_required(action):
    """Decorator factory: chưa đăng nhập -> login; thiếu quyền ``action`` trên
    module 'pr' -> 403.
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not request.user.can(action, 'pr'):
                raise PermissionDenied(f'Không có quyền "{action}" trên Yêu cầu mua hàng.')
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


_PENDING_PR_STATUSES = (PurchaseRequest.Status.PENDING_DEPT, PurchaseRequest.Status.PENDING_PUR)


def _pr_pending_count(user):
    """Badge số PR đang chờ xử lý — tính on-the-fly (mirror ``overdue_count`` ở
    po_list), theo đúng cấp user phụ trách trong luồng duyệt 2 cấp: quản lý
    phòng ban gốc chỉ đếm PR đang ``PENDING_DEPT`` của phòng mình; quản lý
    phòng Mua hàng chỉ đếm PR đang ``PENDING_PUR``; Manager/Admin đếm tổng cả 2
    cấp toàn hệ thống; nhân viên thường đếm PR CHÍNH MÌNH tạo đang chờ (không
    tính PR ``assigned_to`` — họ chưa thấy được PR lúc còn PENDING, chỉ thấy
    sau khi APPROVED, xem ``_pr_visible_queryset``).
    """
    if _pr_can_view_all(user):
        return PurchaseRequest.objects.filter(status__in=_PENDING_PR_STATUSES).count()
    if user.is_manager and user.department == User.Department.PURCHASING:
        return PurchaseRequest.objects.filter(status=PurchaseRequest.Status.PENDING_PUR).count()
    if user.is_manager and user.department:
        return PurchaseRequest.objects.filter(
            status=PurchaseRequest.Status.PENDING_DEPT, requested_by__department=user.department,
        ).count()
    return PurchaseRequest.objects.filter(requested_by=user, status__in=_PENDING_PR_STATUSES).count()


def can_decide_pr(user, pr):
    """Quyền DUYỆT/từ chối 1 PR cụ thể: quản lý ĐÚNG phòng đang giữ quyền quyết
    định ở cấp hiện tại của PR đó (``Approval`` mới nhất — phòng gốc khi
    ``PENDING_DEPT``, phòng Mua hàng khi ``PENDING_PUR``), hoặc Manager/Admin
    (fallback 'approve' cũ, xét mọi cấp). Không còn hard-code phòng Mua hàng
    như trước — mirror ``receiving.views.can_decide_grn_submission`` nhưng
    phòng ban động theo từng PR/từng cấp thay vì cố định.
    """
    if user.can('approve', 'pr'):
        return True
    approval = latest_approval_for(pr)
    return approval is not None and user.is_department_manager(approval.department)


def can_manage_pur_pr(user):
    """Quyền quản lý PR ở tầm phòng Mua hàng, không gắn với 1 PR/1 cấp cụ thể —
    ý nghĩa cũ của ``can_decide_pr(user)`` trước khi tách theo cấp. Dùng cho
    ``pr_forward`` (chuyển tiếp PR đã ``APPROVED`` cho nhân viên — luôn là tác
    vụ ở cấp Mua hàng, không phụ thuộc PR từng qua cấp nào) và gate
    ``?from_pr=`` ở ``po_create`` (PUR-manager phải tạo PO được từ MỌI PR đã
    duyệt xong, không chỉ PR nằm trong tầng "xem toàn bộ" của
    ``_pr_can_view_all``).
    """
    return user.is_department_manager(User.Department.PURCHASING) or user.can('approve', 'pr')


def can_cancel_pr_item_open_qty(user, pr):
    """Mục 1/mục 4 điểm 9: update trên 'pr' + (quản lý phòng Mua hàng HOẶC đúng
    assigned_to của PR đó) — PUR Staff KHÁC (dù cùng phòng ban) không được, kể cả
    có quyền update trên 'pr'.
    """
    if not user.can('update', 'pr'):
        return False
    return user.is_department_manager(User.Department.PURCHASING) or pr.assigned_to_id == user.id


def can_map_non_catalog(user):
    """Map non-catalog sang Product đi theo 2 quyền (mục 1 FSD Stage 2): update
    trên 'pr' VÀ xem menu 'catalog'; "Manager" ở đây là PUR Manager
    (``is_department_manager('PURCHASING')``), không phải bất kỳ Manager nào.
    """
    if not (user.can('update', 'pr') and user.can_view_menu('catalog')):
        return False
    if user.is_superuser or user.role == User.Role.ADMIN:
        return True
    if user.role == User.Role.MANAGER:
        return user.is_department_manager(User.Department.PURCHASING)
    return user.department == User.Department.PURCHASING


def _po_can_view_all(user):
    """PO là dữ liệu chung để mọi phòng ban đối chiếu (STAFF cần biết đơn nào sắp
    về để tạo GRN, QC/ACCOUNTANT cần xem để đối soát) nên KHÔNG áp dụng lọc
    "chỉ của mình" đại trà như PR — chỉ nhân viên phòng Mua hàng THƯỜNG (role
    PURCHASING, không phải quản lý phòng) mới bị giới hạn xem đúng PO do chính
    mình tạo (``created_by``); quản lý phòng Mua hàng vẫn cần bức tranh tổng để
    oversight/điều phối nên không bị giới hạn.
    """
    if user.role != User.Role.PURCHASING:
        return True
    return user.is_superuser or user.is_department_manager(User.Department.PURCHASING)


def _pr_can_view_all(user):
    """Ai xem+sửa được TOÀN BỘ PR (không chỉ của mình/phòng mình): chỉ
    superuser/MANAGER/ADMIN (oversight hệ thống). Quản lý phòng Mua hàng
    KHÔNG còn nằm trong tầng này nữa kể từ khi PR chuyển sang duyệt 2 cấp —
    họ chỉ xem được PR đã/đang qua cấp Mua hàng (xem
    ``_pr_visible_queryset``/``_pr_can_view``), không toàn quyền xem/sửa mọi
    PR như trước (kể cả PR nháp của phòng khác chưa từng tới lượt họ).
    """
    return user.is_superuser or user.role in (User.Role.MANAGER, User.Role.ADMIN)


def _pr_content_type():
    return ContentType.objects.get_for_model(PurchaseRequest)


def _pr_ids_with_pur_approval():
    """PK các PurchaseRequest đã/đang từng có ``Approval(department=PURCHASING)``
    — nghĩa là đã/đang ở cấp duyệt Mua hàng (``PENDING_PUR`` trở đi), bất kể
    trạng thái hiện tại (``APPROVED``/``REJECTED`` cũng tính) — dùng để quản lý
    phòng Mua hàng vẫn xem được PR đã qua tay mình dù đã kết thúc.
    """
    target_ids = Approval.objects.filter(
        target_type=_pr_content_type(), department=User.Department.PURCHASING,
    ).values_list('target_id', flat=True).distinct()
    return [int(tid) for tid in target_ids]


def _pr_reached_pur_approval(pr_pk):
    """Bản 1-PR của ``_pr_ids_with_pur_approval`` — dùng ở check đơn lẻ
    (``_pr_can_view``) để không phải fetch cả danh sách chỉ để kiểm tra 1 pk.
    """
    return Approval.objects.filter(
        target_type=_pr_content_type(), target_id=str(pr_pk), department=User.Department.PURCHASING,
    ).exists()


def _pr_visible_queryset(user, base_qs):
    """4 tầng nhìn PR (chi tiết xem CLAUDE.md mục "Purchase Request (PR)"):
    1. Toàn quyền (``_pr_can_view_all``) -> xem hết.
    2. Quản lý phòng ban gốc -> PR đã nộp (khác DRAFT) của đúng phòng mình, kể
       cả sau khi đã chuyển sang cấp Mua hàng (read-only — không còn quyền
       duyệt/sửa ở cấp đó, xem ``can_decide_pr``/``_pr_can_edit``), cộng PR do
       chính họ tự tạo (mọi trạng thái, kể cả DRAFT của chính họ).
    3. Quản lý phòng Mua hàng -> PR đã/đang ở cấp Mua hàng
       (``_pr_ids_with_pur_approval``), không phụ thuộc trạng thái hiện tại,
       cộng PR do chính họ tự tạo.
    4. Còn lại -> PR do chính mình tạo, hoặc được chỉ định (``assigned_to``)
       VÀ đã ``APPROVED`` — chưa duyệt xong thì người được chỉ định chưa được
       thấy (tránh lộ PR sớm hơn mức cần thiết).
    """
    if _pr_can_view_all(user):
        return base_qs
    if user.is_manager and user.department == User.Department.PURCHASING:
        return base_qs.filter(Q(pk__in=_pr_ids_with_pur_approval()) | Q(requested_by=user))
    if user.is_manager and user.department:
        return base_qs.filter(
            Q(requested_by=user)
            | (Q(requested_by__department=user.department) & ~Q(status=PurchaseRequest.Status.DRAFT))
        )
    return base_qs.filter(
        Q(requested_by=user) | (Q(assigned_to=user) & Q(status=PurchaseRequest.Status.APPROVED))
    )


def _pr_can_view(user, pr):
    """Check tầm nhìn cho 1 PR cụ thể — mirror đúng 4 tầng ở
    ``_pr_visible_queryset`` (dùng ở ``pr_detail`` cho truy cập trực tiếp qua
    URL, không chỉ lọc danh sách — tránh lệch giữa 2 nơi, xem cảnh báo về bẫy
    này trong CLAUDE.md).
    """
    if _pr_can_view_all(user):
        return True
    if pr.requested_by_id == user.id:
        return True
    if user.is_manager and user.department == User.Department.PURCHASING:
        return _pr_reached_pur_approval(pr.pk)
    if (
        user.is_manager and user.department
        and pr.requested_by_id and pr.requested_by.department == user.department
    ):
        return pr.status != PurchaseRequest.Status.DRAFT
    return pr.assigned_to_id == user.id and pr.status == PurchaseRequest.Status.APPROVED


@po_permission_required('read')
def po_list(request):
    """READ — danh sách PO, kèm cờ giao hàng trễ hạn (FR-PO-06, tính on-the-fly).
    Nhân viên phòng Mua hàng thường chỉ xem PO do chính mình tạo (``_po_can_view_all``);
    mọi role khác (STAFF/QC/ACCOUNTANT/Manager/Admin/quản lý phòng Mua hàng) xem toàn bộ.
    """
    orders = PurchaseOrder.objects.select_related('supplier', 'created_by').all()
    if not _po_can_view_all(request.user):
        # created_by=NULL nghĩa là không xác định được người tạo thật (dữ liệu
        # cũ trước migration 0007, hoặc tạo qua script) — không gán bừa cho một
        # người, nên vẫn hiển thị cho mọi nhân viên PURCHASING thay vì ẩn vĩnh
        # viễn (xem 0009_backfill_po_created_by.py).
        orders = orders.filter(Q(created_by=request.user) | Q(created_by__isnull=True))
    selected_status = request.GET.get('status', '')
    if selected_status:
        orders = orders.filter(status=selected_status)
    selected_supplier = None
    supplier_id = request.GET.get('supplier')
    if supplier_id:
        try:
            selected_supplier = int(supplier_id)
        except (TypeError, ValueError):
            selected_supplier = None
    if selected_supplier:
        orders = orders.filter(supplier_id=selected_supplier)
    q = request.GET.get('q', '').strip()
    if q:
        orders = orders.filter(po_no__icontains=q)
    # Đếm ở DB (count()) thay vì lặp Python trên toàn bộ orders trước paginate —
    # tránh full scan/fetch hết mọi PurchaseOrder khớp filter chỉ để đếm.
    # Logic phải khớp PurchaseOrder.delivery_status() ở model.
    overdue_count = orders.filter(
        Q(status=PurchaseOrder.Status.SENT,
          expected_delivery_date__isnull=False,
          expected_delivery_date__lt=timezone.localdate())
        | Q(status__in=[PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.CLOSED],
            expected_delivery_date__isnull=False,
            received_at__isnull=False,
            received_at__gt=F('expected_delivery_date'))
    ).count()
    page_obj, page_size = paginate_queryset(request, orders)
    return render(request, 'purchasing/po_list.html', {
        'orders': page_obj,
        'page_obj': page_obj,
        'page_size': page_size,
        'overdue_count': overdue_count,
        'can_create': request.user.can('create', 'po'),
        'can_update': request.user.can('update', 'po'),
        'statuses': PurchaseOrder.Status.choices,
        'suppliers': Supplier.objects.filter(status=Supplier.Status.ACTIVE),
        'selected_status': selected_status,
        'selected_supplier': selected_supplier,
        'q': q,
        'pr_pending_count': _pr_pending_count(request.user),
        'can_view_all_po': _po_can_view_all(request.user),
    })


@po_permission_required('read')
def po_detail(request, pk):
    """READ — chi tiết PO: item kèm Qty đã nhận/còn lại (FR-PO-04 reconciliation,
    tính on-the-fly từ GrnItem giống ``services.sync_po_status``), badge giao
    hàng (FR-PO-06), nút chuyển trạng thái theo quyền. KHÔNG giới hạn theo
    ``created_by`` như ``po_list`` — PO là tác vụ nhiều vai trò cùng xử lý một
    phiếu (Manager duyệt, một nhân viên Mua hàng khác có thể là người gửi NCC),
    và được tham chiếu trực tiếp từ ``pr_detail``/GRN, nên chặn theo người tạo ở
    đây sẽ cản trở phối hợp thật; chỉ ``po_list`` (mục lục tổng quan) mới cần
    thu gọn cho gọn hàng đợi của từng nhân viên.
    """
    po = get_object_or_404(
        PurchaseOrder.objects.select_related('supplier', 'created_by').prefetch_related('items__product'), pk=pk)

    received_by_product = received_qty_by_product(po)
    item_rows = []
    for item in po.items.all():
        qty_received = received_by_product.get(item.product_id, 0)
        item_rows.append({
            'item': item,
            'qty_received': qty_received,
            'qty_remaining': item.qty_ordered - qty_received,
        })

    can_update = request.user.can('update', 'po')
    can_approve = request.user.can('approve', 'po')
    closeable_statuses = (
        PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIAL_RECEIVED, PurchaseOrder.Status.RECEIVED)
    can_close = can_approve and po.status in closeable_statuses
    can_retry_email = (
        can_update and po.status == PurchaseOrder.Status.SENT
        and po.email_status in (PurchaseOrder.EmailStatus.FAILED, PurchaseOrder.EmailStatus.SKIPPED_NO_EMAIL))
    return render(request, 'purchasing/po_detail.html', {
        'po': po,
        'item_rows': item_rows,
        'delivery_status': po.delivery_status(),
        'can_update': can_update,
        'can_approve': can_approve,
        'can_edit': can_update and po.status == PurchaseOrder.Status.DRAFT,
        'can_close': can_close,
        'can_retry_email': can_retry_email,
        'close_reason_required': po.status != PurchaseOrder.Status.RECEIVED,
        'close_form': PurchaseOrderCloseForm(po=po) if can_close else None,
    })


@po_permission_required('create')
def po_create(request):
    """CREATE — tạo PO kèm chi tiết đơn hàng (formset), tối thiểu 1 dòng item.
    Không còn lối tắt ``?product=<id>&qty=<n>`` tạo PO thẳng từ gợi ý Min Level
    (đã bỏ — mọi PO phát sinh từ tồn kho dưới Min Level giờ phải qua PR trước,
    xem ``pr_create``/``inventory/views.py::inventory_list``); PO tạo trực
    tiếp ở đây luôn ``source=MANUAL``.

    ``?from_pr=<pk>`` (nút "Tạo PO từ yêu cầu này" ở ``pr_detail``) prefill mọi
    dòng item từ 1 ``PurchaseRequest`` đã APPROVED và CHƯA có PO liên kết
    (``source`` set thành ``FROM_PR``), kèm gợi ý sẵn NCC từ
    ``Product.preferred_supplier`` của dòng item đầu tiên nếu có (người dùng
    vẫn đổi được); sau khi PO tạo thành công, gán ``source_pr.linked_po`` để
    PR biết đã convert xong.

    Truy cập ``from_pr`` được khoá 2 lớp để không lách được cơ chế chuyển tiếp
    ở ``forward_purchase_request``/``pr_detail``:
    - Hiển thị/quyền xem: mirror đúng check ở ``pr_detail`` — chỉ người tạo,
      người được chỉ định (``assigned_to``), quản lý phòng Mua hàng, hoặc
      Manager/Admin mới thấy/dùng được PR này để tạo PO, dù họ có quyền
      ``create`` chung trên module ``po``.
    - Chống convert trùng/đua nhau: PR đã ``linked_po`` bị loại ngay ở
      ``get_object_or_404`` (không chỉ ẩn nút trên UI); và khi POST thật sự
      tạo PO, khoá lại PR bằng ``select_for_update()`` rồi kiểm tra lại
      trạng thái + ``linked_po_id`` bên trong transaction — tránh 2 request
      đồng thời cùng vượt qua check ban đầu rồi cùng tạo PO cho 1 PR.
    """
    initial = None
    source_pr = None
    po_initial = {}
    from_pr_id = request.POST.get('from_pr') or request.GET.get('from_pr')
    if from_pr_id:
        source_pr = get_object_or_404(
            PurchaseRequest, pk=from_pr_id, status=PurchaseRequest.Status.APPROVED, linked_po__isnull=True)
        if not (
            _pr_can_view_all(request.user)
            or source_pr.requested_by_id == request.user.id
            or source_pr.assigned_to_id == request.user.id
            or can_manage_pur_pr(request.user)
        ):
            raise PermissionDenied(
                'Bạn chỉ tạo được PO từ yêu cầu mua hàng do chính mình tạo hoặc được giao phụ trách.')
        if request.method == 'GET':
            initial = [
                {'product': item.product_id, 'qty_ordered': item.qty_requested}
                for item in source_pr.items.all()
            ]
            first_item = source_pr.items.select_related('product').first()
            if first_item and first_item.product.preferred_supplier_id:
                po_initial['supplier'] = first_item.product.preferred_supplier_id

    form = PurchaseOrderForm(request.POST or None, initial=po_initial)
    formset = PurchaseOrderItemFormSet(
        request.POST or None, instance=PurchaseOrder(), prefix='items', initial=initial)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        try:
            with transaction.atomic():
                if source_pr:
                    source_pr = PurchaseRequest.objects.select_for_update().get(pk=source_pr.pk)
                    if source_pr.status != PurchaseRequest.Status.APPROVED:
                        raise ValidationError(
                            f'Yêu cầu "{source_pr.request_no}" không còn ở trạng thái Đã duyệt.')
                    if source_pr.linked_po_id:
                        raise ValidationError(f'Yêu cầu "{source_pr.request_no}" đã có PO liên kết.')
                obj = form.save(commit=False)
                obj.created_by = request.user
                if source_pr:
                    obj.source = PurchaseOrder.Source.FROM_PR
                obj.save()
                formset.instance = obj
                formset.save()
                if source_pr:
                    source_pr.linked_po = obj
                    source_pr.save(update_fields=['linked_po'])
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return redirect('purchasing:pr_detail', pk=from_pr_id)
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj,
            description=f'Tạo PO {obj.po_no}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã tạo PO "{obj.po_no}".')
        return redirect('purchasing:po_detail', pk=obj.pk)
    return render(request, 'purchasing/po_form.html', {
        'form': form, 'formset': formset, 'mode': 'create', 'source_pr': source_pr,
    })


@po_permission_required('update')
def po_update(request, pk):
    """UPDATE — sửa PO + chi tiết đơn hàng. Chỉ cho sửa khi còn ở state DRAFT
    (mirror ``receiving.views.grn_update``) — sau APPROVED, trạng thái chỉ đổi
    qua transition (``po_approve``/``po_send``/``po_close``), không sửa tay.
    Status được re-check dưới ``select_for_update()`` bên trong transaction
    (không chỉ qua ``get_object_or_404`` trước transaction) để chặn race: nếu
    PO bị duyệt bởi request khác ngay giữa lúc form A đang validate, ``save()``
    trên instance cũ (còn giữ status DRAFT trong bộ nhớ) sẽ ghi đè status mới
    xuống DB — instance dùng để save phải là bản đã khóa, không phải ``obj``
    fetch trước đó.
    """
    obj = get_object_or_404(PurchaseOrder, pk=pk)
    if obj.status != PurchaseOrder.Status.DRAFT:
        messages.error(request, f'Không thể sửa PO "{obj.po_no}" khi đã qua state DRAFT.')
        return redirect('purchasing:po_detail', pk=obj.pk)

    if request.method == 'POST':
        with transaction.atomic():
            locked_obj = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=pk)
            if locked_obj.status != PurchaseOrder.Status.DRAFT:
                messages.error(
                    request, f'Không thể sửa PO "{locked_obj.po_no}" khi đã qua state DRAFT.')
                return redirect('purchasing:po_detail', pk=pk)
            form = PurchaseOrderForm(request.POST, instance=locked_obj)
            formset = PurchaseOrderItemFormSet(request.POST, instance=locked_obj, prefix='items')
            if form.is_valid() and formset.is_valid():
                obj = form.save()
                formset.save()
                log_action(
                    request.user, AuditLog.Action.UPDATE, target=obj,
                    description=f'Cập nhật PO {obj.po_no}',
                    ip_address=client_ip(request),
                )
                messages.success(request, f'Đã cập nhật PO "{obj.po_no}".')
                return redirect('purchasing:po_detail', pk=obj.pk)
    else:
        form = PurchaseOrderForm(instance=obj)
        formset = PurchaseOrderItemFormSet(instance=obj, prefix='items')
    return render(
        request, 'purchasing/po_form.html',
        {'form': form, 'formset': formset, 'mode': 'update', 'obj': obj},
    )


@po_permission_required('approve')
def po_approve(request, pk):
    """DRAFT -> APPROVED (POST-only). Chỉ Manager/Admin (quyền ``approve``)."""
    obj = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method == 'POST':
        try:
            approve_po(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã duyệt PO "{obj.po_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:po_detail', pk=obj.pk)


@po_permission_required('update')
def po_send(request, pk):
    """APPROVED -> SENT (POST-only). Flash message theo đúng 3 nhánh runtime
    của ``email_status`` (PUR-FND-02, mục 5 FSD) — không còn suy luận nhị phân.
    """
    obj = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method == 'POST':
        try:
            obj = send_po(obj, actor=request.user, ip_address=client_ip(request))
            if obj.email_status == PurchaseOrder.EmailStatus.SENT:
                messages.success(request, f'Đã gửi PO "{obj.po_no}" và email thông báo tới NCC.')
            elif obj.email_status == PurchaseOrder.EmailStatus.FAILED:
                messages.error(
                    request,
                    f'Đã chuyển PO "{obj.po_no}" sang trạng thái Gửi NCC, nhưng gửi email thông báo cho NCC '
                    f'thất bại — vui lòng tự thông báo cho NCC qua kênh khác.')
            else:
                messages.warning(
                    request,
                    f'Đã chuyển PO "{obj.po_no}" sang trạng thái Gửi NCC, nhưng NCC chưa có email liên hệ '
                    f'— vui lòng tự thông báo cho NCC qua kênh khác.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:po_detail', pk=obj.pk)


@po_permission_required('update')
def po_retry_email(request, pk):
    """PUR-FND-07 — gửi lại email PO khi lần gửi trong ``po_send`` thất bại
    hoặc NCC lúc đó chưa có email (POST-only). Không chạy lại transition, chỉ
    cập nhật ``email_status`` — quyền giữ nguyên ``update``, giống ``po_send``.
    """
    obj = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method == 'POST':
        try:
            obj = retry_po_email(obj, actor=request.user, ip_address=client_ip(request))
            if obj.email_status == PurchaseOrder.EmailStatus.SENT:
                messages.success(request, f'Đã gửi lại email PO "{obj.po_no}" tới NCC.')
            else:
                messages.error(request, f'Gửi lại email PO "{obj.po_no}" vẫn thất bại — vui lòng thử lại sau.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:po_detail', pk=obj.pk)


@po_permission_required('approve')
def po_close(request, pk):
    """{SENT, PARTIAL_RECEIVED, RECEIVED} -> CLOSED (POST-only). Đóng sớm từ
    SENT/PARTIAL_RECEIVED bắt buộc nhập lý do (``PurchaseOrderCloseForm``, xem
    ``services.close_po`` — service tự re-validate lại, form chỉ thu input)."""
    obj = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method == 'POST':
        form = PurchaseOrderCloseForm(request.POST, po=obj)
        try:
            if not form.is_valid():
                raise ValidationError('Bắt buộc nhập lý do khi đóng PO trước khi NCC giao đủ hàng.')
            close_po(
                obj, actor=request.user, reason=form.cleaned_data['close_reason'],
                ip_address=client_ip(request))
            messages.success(request, f'Đã đóng PO "{obj.po_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:po_detail', pk=obj.pk)


@po_permission_required('read')
def po_price_comparison(request):
    """FR-PO-03: so sánh giá từ nhiều NCC cho 1 sản phẩm để chọn optimal."""
    products = Product.objects.filter(is_active=True)
    selected_product = None
    rows = []
    product_id = request.GET.get('product')
    if product_id:
        selected_product = get_object_or_404(Product, pk=product_id)
        rows = supplier_price_history(selected_product)
    return render(request, 'purchasing/po_price_comparison.html', {
        'products': products,
        'selected_product': selected_product,
        'rows': rows,
    })


@po_permission_required('read')
def po_supplier_performance(request):
    """FR-PO-05/FR-PO-06: lead-time thực tế và tỉ lệ đúng hạn/trễ hạn theo NCC."""
    rows = supplier_lead_time_stats()
    return render(request, 'purchasing/po_supplier_performance.html', {'rows': rows})


@pr_permission_required('read')
def pr_list(request):
    """READ — danh sách PR (Tab 1), lọc theo 4 tầng nhìn của luồng duyệt 2 cấp
    (``_pr_visible_queryset`` — xem docstring hàm đó để biết chi tiết từng
    tầng).
    """
    prs = PurchaseRequest.objects.select_related('warehouse', 'requested_by', 'assigned_to', 'linked_po').all()
    prs = _pr_visible_queryset(request.user, prs)
    selected_status = request.GET.get('status', '')
    if selected_status:
        prs = prs.filter(status=selected_status)
    q = request.GET.get('q', '').strip()
    if q:
        prs = prs.filter(request_no__icontains=q)
    page_obj, page_size = paginate_queryset(request, prs)
    return render(request, 'purchasing/pr_list.html', {
        'prs': page_obj,
        'page_obj': page_obj,
        'page_size': page_size,
        'can_create': request.user.can('create', 'pr'),
        'can_view_all': _pr_can_view_all(request.user),
        'statuses': PurchaseRequest.Status.choices,
        'selected_status': selected_status,
        'q': q,
        'pr_pending_count': _pr_pending_count(request.user),
    })


@pr_permission_required('read')
def pr_detail(request, pk):
    """READ — chi tiết PR: item, lịch sử duyệt đủ 2 cấp (``approval_history_for``
    — khác ``pr_list`` chỉ cần đếm, trang chi tiết cần thấy từng bước ai duyệt),
    link sang PO tạo từ PR này (nếu có). Tầm nhìn dùng ``_pr_can_view`` — mirror
    đúng ``_pr_visible_queryset`` ở ``pr_list`` để chặn cả truy cập trực tiếp
    qua URL, không chỉ ẩn khỏi danh sách.
    """
    obj = get_object_or_404(
        PurchaseRequest.objects
        .select_related('warehouse', 'requested_by', 'assigned_to', 'decided_by', 'linked_po')
        .prefetch_related('items__product'),
        pk=pk,
    )
    if not _pr_can_view(request.user, obj):
        raise PermissionDenied('Bạn chỉ xem được yêu cầu mua hàng do chính mình tạo hoặc được giao phụ trách.')
    can_forward = (
        can_manage_pur_pr(request.user)
        and obj.status == PurchaseRequest.Status.APPROVED
        and not obj.linked_po_id
    )
    is_owner_editable = _pr_can_edit(request.user, obj)
    return render(request, 'purchasing/pr_detail.html', {
        'obj': obj,
        'approvals': approval_history_for(obj),
        'can_approve': obj.status in _PENDING_PR_STATUSES and can_decide_pr(request.user, obj),
        'can_create_po': (
            request.user.can('create', 'po')
            and obj.status == PurchaseRequest.Status.APPROVED
            and not obj.linked_po_id
        ),
        'can_forward': can_forward,
        'forward_form': PurchaseRequestForwardForm() if can_forward else None,
        'reject_form': PurchaseRequestRejectForm(),
        'can_edit': is_owner_editable and obj.status == PurchaseRequest.Status.DRAFT,
        'can_submit': is_owner_editable and obj.status == PurchaseRequest.Status.DRAFT,
        'can_reopen': is_owner_editable and obj.status == PurchaseRequest.Status.REJECTED,
        'can_delete': (
            is_owner_editable and obj.status == PurchaseRequest.Status.DRAFT
            and request.user.can('delete', 'pr')
        ),
        'can_map_non_catalog': can_map_non_catalog(request.user),
        'can_cancel_pr_item': can_cancel_pr_item_open_qty(request.user, obj),
    })


@pr_permission_required('create')
def pr_create(request):
    """CREATE — tiếp nhận yêu cầu mua hàng từ nhân viên các phòng ban (Tab 1), kèm
    nhiều dòng SKU (formset). ``requested_by`` luôn là người đang đăng nhập. Chỉ
    lưu ở state DRAFT (mirror ``receiving.views.grn_create``) — người tạo tự sửa
    tiếp qua ``pr_update`` rồi bấm "Nộp yêu cầu" (``pr_submit``) khi đã sẵn sàng,
    không tự động nộp thẳng vào luồng ``Approval`` như trước.

    ``?product=<id>&qty=<n>&warehouse=<id>`` (gợi ý tồn kho dưới Min Level, link
    từ ``inventory/views.py::inventory_list`` — thay cho lối tắt tạo PO thẳng đã
    bỏ ở ``po_create``) prefill dòng item đầu tiên + kho; PR tạo từ đây được
    đánh dấu ``origin=MIN_LEVEL``. Query string GET không tự sống sót qua submit
    form nên giá trị này được giữ qua 1 hidden input ``min_level_origin`` trên
    ``pr_form.html`` và đọc lại từ ``request.POST`` khi lưu.
    """
    initial = None
    pr_initial = {}
    if request.method == 'GET':
        product_id = request.GET.get('product')
        qty = request.GET.get('qty')
        if product_id and qty:
            initial = [{'product': product_id, 'qty_requested': qty}]
        warehouse_id = request.GET.get('warehouse')
        if warehouse_id:
            pr_initial['warehouse'] = warehouse_id
    min_level_origin = request.POST.get('min_level_origin') if request.method == 'POST' else bool(initial)

    form = PurchaseRequestForm(request.POST or None, initial=pr_initial)
    formset = PurchaseRequestItemFormSet(
        request.POST or None, instance=PurchaseRequest(), prefix='items', initial=initial)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.requested_by = request.user
            if min_level_origin:
                obj.origin = PurchaseRequest.Origin.MIN_LEVEL
            obj.save()
            formset.instance = obj
            formset.save()
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj,
            description=f'Tạo yêu cầu mua hàng {obj.request_no} (nháp)',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã lưu nháp yêu cầu mua hàng "{obj.request_no}" — bấm "Nộp yêu cầu" để gửi duyệt.')
        return redirect('purchasing:pr_detail', pk=obj.pk)
    return render(request, 'purchasing/pr_form.html', {
        'form': form, 'formset': formset, 'mode': 'create', 'min_level_origin': bool(min_level_origin),
    })


def _pr_can_edit(user, pr):
    """Ai sửa/nộp/mở lại/xoá được 1 PR: chỉ đúng chủ (``requested_by``) hoặc
    người có tầm nhìn toàn bộ (``_pr_can_view_all`` — nay chỉ còn
    superuser/MANAGER/ADMIN, KHÔNG còn quản lý phòng Mua hàng — họ chỉ
    read-only trên PR của phòng khác kể từ khi chuyển sang duyệt 2 cấp).
    """
    return _pr_can_view_all(user) or pr.requested_by_id == user.id


@pr_permission_required('update')
def pr_update(request, pk):
    """UPDATE — sửa PR + chi tiết đơn hàng. Chỉ cho sửa khi còn DRAFT (mirror
    ``po_update``) và chỉ đúng chủ hoặc người có tầm nhìn toàn bộ mới sửa được
    (mirror check hiển thị ở ``pr_detail`` — quyền module ``update`` một mình
    không đủ, còn phải đúng người). Cả sở hữu lẫn status được re-check dưới
    ``select_for_update()`` bên trong transaction — không chỉ tin vào check
    trước transaction — để chặn race: PR có thể vừa được submit/duyệt bởi
    request khác ngay giữa lúc form đang validate, và ``save()`` phải chạy
    trên bản đã khóa chứ không phải ``obj`` fetch trước đó (vẫn giữ status
    DRAFT cũ trong bộ nhớ).
    """
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not _pr_can_edit(request.user, obj):
        raise PermissionDenied('Bạn chỉ sửa được yêu cầu mua hàng do chính mình tạo.')
    if obj.status != PurchaseRequest.Status.DRAFT:
        messages.error(request, f'Không thể sửa yêu cầu "{obj.request_no}" khi đã qua state Nháp.')
        return redirect('purchasing:pr_detail', pk=obj.pk)

    if request.method == 'POST':
        with transaction.atomic():
            locked_obj = get_object_or_404(PurchaseRequest.objects.select_for_update(), pk=pk)
            if not _pr_can_edit(request.user, locked_obj):
                raise PermissionDenied('Bạn chỉ sửa được yêu cầu mua hàng do chính mình tạo.')
            if locked_obj.status != PurchaseRequest.Status.DRAFT:
                messages.error(
                    request, f'Không thể sửa yêu cầu "{locked_obj.request_no}" khi đã qua state Nháp.')
                return redirect('purchasing:pr_detail', pk=pk)
            form = PurchaseRequestForm(request.POST, instance=locked_obj)
            formset = PurchaseRequestItemFormSet(request.POST, instance=locked_obj, prefix='items')
            if form.is_valid() and formset.is_valid():
                obj = form.save()
                formset.save()
                log_action(
                    request.user, AuditLog.Action.UPDATE, target=obj,
                    description=f'Cập nhật yêu cầu mua hàng {obj.request_no}',
                    ip_address=client_ip(request),
                )
                messages.success(request, f'Đã cập nhật yêu cầu mua hàng "{obj.request_no}".')
                return redirect('purchasing:pr_detail', pk=obj.pk)
    else:
        form = PurchaseRequestForm(instance=obj)
        formset = PurchaseRequestItemFormSet(instance=obj, prefix='items')
    return render(
        request, 'purchasing/pr_form.html',
        {'form': form, 'formset': formset, 'mode': 'update', 'obj': obj},
    )


@pr_permission_required('update')
def pr_submit(request, pk):
    """DRAFT -> PENDING (POST-only, mirror ``receiving.views.grn_submit``): nộp
    PR để chờ quản lý phòng Mua hàng duyệt. Cùng check sở hữu như ``pr_update``
    — quyền ``update`` trên module ``pr`` chỉ xác nhận vai trò được phép tự nộp
    yêu cầu của MÌNH, không phải nộp hộ PR của người khác.
    """
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not _pr_can_edit(request.user, obj):
        raise PermissionDenied('Bạn chỉ nộp được yêu cầu mua hàng do chính mình tạo.')
    if request.method == 'POST':
        try:
            submit_purchase_request(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(
                request, f'Đã gửi yêu cầu mua hàng "{obj.request_no}", chờ quản lý phòng Mua hàng duyệt.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:pr_detail', pk=obj.pk)


@pr_permission_required('update')
def pr_reopen(request, pk):
    """REJECTED -> DRAFT (POST-only): mở lại PR bị từ chối để sửa và nộp lại.
    Cùng check sở hữu như ``pr_update``/``pr_submit``.
    """
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not _pr_can_edit(request.user, obj):
        raise PermissionDenied('Bạn chỉ mở lại được yêu cầu mua hàng do chính mình tạo.')
    if request.method == 'POST':
        try:
            reopen_purchase_request(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã mở lại yêu cầu mua hàng "{obj.request_no}" — bạn có thể sửa và nộp lại.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:pr_detail', pk=obj.pk)


@pr_permission_required('delete')
def pr_delete(request, pk):
    """DELETE (L2) — xoá thật 1 PR còn DRAFT (POST-only). Cùng check sở hữu như
    ``pr_update``/``pr_submit``/``pr_reopen`` — quyền ``delete`` trên module
    ``pr`` (mặc định chỉ MANAGER/ADMIN, xem ``accounts/permissions.py``) một
    mình không đủ, còn phải đúng chủ hoặc người có tầm nhìn toàn bộ
    (``_pr_can_edit``) mới xoá được.
    """
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not _pr_can_edit(request.user, obj):
        raise PermissionDenied('Bạn chỉ xoá được yêu cầu mua hàng do chính mình tạo.')
    if request.method == 'POST':
        try:
            request_no = delete_purchase_request(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã xoá yêu cầu mua hàng "{request_no}".')
            return redirect('purchasing:pr_list')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:pr_detail', pk=obj.pk)


@login_required
def pr_item_cancel_open_qty(request, pk):
    """POST-only: huỷ 1 phần qty_open của 1 dòng PR (PUR-PR-07). Quyền: mục 1/
    mục 4 điểm 9 (``can_cancel_pr_item_open_qty``)."""
    item = get_object_or_404(PurchaseRequestItem.objects.select_related('purchase_request'), pk=pk)
    if not can_cancel_pr_item_open_qty(request.user, item.purchase_request):
        raise PermissionDenied('Không có quyền huỷ phần còn mở của dòng yêu cầu mua hàng này.')
    if request.method == 'POST':
        try:
            qty = int(request.POST.get('qty', ''))
        except ValueError:
            messages.error(request, 'Số lượng huỷ không hợp lệ.')
            return redirect('purchasing:pr_detail', pk=item.purchase_request_id)
        reason = request.POST.get('reason', '')
        try:
            cancel_pr_item_open_qty(item, qty, reason, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã huỷ {qty} số lượng còn mở của dòng "{item}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:pr_detail', pk=item.purchase_request_id)


@login_required
def pr_item_map_product(request, pk):
    """Map 1 dòng PR non-catalog sang Product có sẵn hoặc Product mới tạo tại
    chỗ (PUR-PR-06). Quyền: ``can_map_non_catalog`` (Task 3.3)."""
    item = get_object_or_404(PurchaseRequestItem.objects.select_related('purchase_request'), pk=pk)
    if not can_map_non_catalog(request.user):
        raise PermissionDenied('Không có quyền map sản phẩm cho dòng yêu cầu mua hàng này.')
    if not item.is_non_catalog:
        messages.error(request, 'Dòng này đã có sản phẩm.')
        return redirect('purchasing:pr_detail', pk=item.purchase_request_id)

    form = PrItemMapProductForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                original_non_catalog_name = item.non_catalog_name
                product = form.cleaned_data.get('existing_product')
                if not product:
                    product = Product.objects.create(
                        product_code=form.cleaned_data['new_product_code'],
                        name=form.cleaned_data['new_product_name'],
                        uom=form.cleaned_data['new_product_uom'],
                        category=form.cleaned_data['new_product_category'],
                    )
                map_non_catalog_item(item, product, actor=request.user, ip_address=client_ip(request))
            messages.success(
                request,
                f'Đã map dòng "{original_non_catalog_name}" sang sản phẩm "{product.product_code}".')
            return redirect('purchasing:pr_detail', pk=item.purchase_request_id)
        except IntegrityError:
            # Chặn race: mã chưa tồn tại lúc form validate nhưng transaction khác
            # vừa tạo trước INSERT của transaction này — exception thoát khỏi
            # atomic nên transaction đã rollback sạch trước khi render lại form.
            form.add_error('new_product_code', 'Mã sản phẩm đã tồn tại.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return render(request, 'purchasing/pr_item_map_product.html', {'item': item, 'form': form})


@login_required
def pr_approve(request, pk):
    """Quản lý phòng đang giữ quyền quyết định ở cấp hiện tại (bộ phận gốc khi
    ``PENDING_DEPT``, Mua hàng khi ``PENDING_PUR``), hoặc Manager/Admin, duyệt
    PR: chuyển sang cấp kế tiếp hoặc ``APPROVED`` nếu đã ở cấp cuối (POST-only,
    xem ``purchasing.services.decide_purchase_request``)."""
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not can_decide_pr(request.user, obj):
        raise PermissionDenied('Không có quyền duyệt yêu cầu mua hàng.')
    if request.method == 'POST':
        approval = latest_approval_for(obj)
        qty_approved_overrides = {}
        if obj.status == PurchaseRequest.Status.PENDING_PUR:
            for item in obj.items.all():
                raw = request.POST.get(f'qty_approved_{item.pk}', '').strip()
                if raw:
                    try:
                        qty_approved_overrides[item.pk] = int(raw)
                    except ValueError:
                        messages.error(request, f'Số lượng duyệt không hợp lệ cho dòng "{item}".')
                        return redirect('purchasing:pr_detail', pk=obj.pk)
        try:
            if approval is None or approval.status != Approval.Status.PENDING:
                raise ValidationError('Yêu cầu này không có phiếu duyệt nào đang chờ xử lý.')
            decide_purchase_request(
                approval, True, actor=request.user, ip_address=client_ip(request),
                qty_approved_overrides=qty_approved_overrides,
            )
            messages.success(request, f'Đã duyệt yêu cầu "{obj.request_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:pr_detail', pk=obj.pk)


@login_required
def pr_reject(request, pk):
    """Quản lý phòng đang giữ quyền quyết định ở cấp hiện tại (hoặc Manager/
    Admin) từ chối PR kèm lý do — kết thúc PR ngay ở cấp nào cũng vậy
    (``-> REJECTED``, POST-only)."""
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not can_decide_pr(request.user, obj):
        raise PermissionDenied('Không có quyền từ chối yêu cầu mua hàng.')
    if request.method == 'POST':
        approval = latest_approval_for(obj)
        form = PurchaseRequestRejectForm(request.POST)
        try:
            if approval is None or approval.status != Approval.Status.PENDING:
                raise ValidationError('Yêu cầu này không có phiếu duyệt nào đang chờ xử lý.')
            if not form.is_valid():
                raise ValidationError('Vui lòng nhập lý do từ chối.')
            decide_purchase_request(
                approval, False, actor=request.user, note=form.cleaned_data['reject_reason'],
                ip_address=client_ip(request),
            )
            messages.success(request, f'Đã từ chối yêu cầu "{obj.request_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:pr_detail', pk=obj.pk)


@login_required
def pr_forward(request, pk):
    """Quản lý phòng Mua hàng (hoặc Manager/Admin) chuyển tiếp 1 PR đã duyệt cho
    1 nhân viên phòng Mua hàng cụ thể tạo PO (POST-only) — xem
    ``purchasing.services.forward_purchase_request``."""
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not can_manage_pur_pr(request.user):
        raise PermissionDenied('Không có quyền chuyển tiếp yêu cầu mua hàng.')
    if request.method == 'POST':
        form = PurchaseRequestForwardForm(request.POST)
        try:
            if not form.is_valid():
                raise ValidationError('Vui lòng chọn nhân viên để chuyển tiếp.')
            forward_purchase_request(
                obj, form.cleaned_data['staff'], actor=request.user, ip_address=client_ip(request))
            messages.success(
                request,
                f'Đã chuyển tiếp yêu cầu "{obj.request_no}" cho {form.cleaned_data["staff"].username}.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:pr_detail', pk=obj.pk)
