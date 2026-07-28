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
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.approvals import latest_approval_for
from accounts.audit import client_ip, log_action
from accounts.models import Approval, AuditLog, User
from accounts.pagination import paginate_queryset
from catalog.models import Product
from partners.models import Supplier
from receiving.models import GrnItem

from .forms import (
    PurchaseOrderCloseForm,
    PurchaseOrderForm,
    PurchaseOrderItemFormSet,
    PurchaseRequestForm,
    PurchaseRequestForwardForm,
    PurchaseRequestItemFormSet,
    PurchaseRequestRejectForm,
)
from .models import PurchaseOrder, PurchaseRequest
from .services import (
    approve_po,
    close_po,
    decide_purchase_request,
    delete_purchase_request,
    forward_purchase_request,
    reopen_purchase_request,
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


def _pr_pending_count(user):
    """Badge số PR đang chờ duyệt — tính on-the-fly (mirror ``overdue_count`` ở po_list).
    Phải lọc theo cùng quyền xem với ``pr_list`` (``_pr_can_view_all``): nhân viên
    phòng Mua hàng thường chỉ nên thấy số PR của chính mình đang chờ, không phải
    tổng số PENDING toàn hệ thống — nếu không badge sẽ hiển thị con số không liên
    quan đến hàng đợi thật của họ.
    """
    prs = PurchaseRequest.objects.filter(status=PurchaseRequest.Status.PENDING)
    if not _pr_can_view_all(user):
        prs = prs.filter(Q(requested_by=user) | Q(assigned_to=user))
    return prs.count()


def can_decide_pr(user):
    """Quyền DUYỆT/từ chối PR: quản lý phòng Mua hàng (oversight, xem/quyết định mọi
    PR) hoặc Manager/Admin (fallback 'approve' cũ) — KHÔNG còn cấp cho mọi nhân
    viên role PURCHASING nữa (mirror ``receiving.views.can_decide_grn_submission``).
    """
    return user.is_department_manager(User.Department.PURCHASING) or user.can('approve', 'pr')


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
    """Ai xem được TOÀN BỘ PR (không chỉ của mình): quản lý phòng Mua hàng cần
    bức tranh tổng để duyệt/chuyển tiếp, Manager/Admin có quyền oversight sẵn
    có. Nhân viên phòng Mua hàng thường (không phải quản lý) hay người ở phòng
    ban khác chỉ thấy PR do chính mình tạo hoặc được chỉ định/chuyển tiếp
    (``assigned_to`` — xem lọc ở ``pr_list``/``pr_detail``), KHÔNG còn thấy toàn
    bộ chỉ vì cùng role PURCHASING nữa.
    """
    return (
        user.is_superuser
        or user.role in (User.Role.MANAGER, User.Role.ADMIN)
        or user.is_department_manager(User.Department.PURCHASING)
    )


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

    received_by_product = dict(
        GrnItem.objects.filter(grn__po=po)
        .exclude(status=GrnItem.Status.REJECTED)
        .values('product_id')
        .annotate(total=Sum('qty_received'))
        .values_list('product_id', 'total')
    )
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
    return render(request, 'purchasing/po_detail.html', {
        'po': po,
        'item_rows': item_rows,
        'delivery_status': po.delivery_status(),
        'can_update': can_update,
        'can_approve': can_approve,
        'can_edit': can_update and po.status == PurchaseOrder.Status.DRAFT,
        'can_close': can_close,
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
        if (
            not _pr_can_view_all(request.user)
            and source_pr.requested_by_id != request.user.id
            and source_pr.assigned_to_id != request.user.id
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
    """
    obj = get_object_or_404(PurchaseOrder, pk=pk)
    if obj.status != PurchaseOrder.Status.DRAFT:
        messages.error(request, f'Không thể sửa PO "{obj.po_no}" khi đã qua state DRAFT.')
        return redirect('purchasing:po_detail', pk=obj.pk)

    form = PurchaseOrderForm(request.POST or None, instance=obj)
    formset = PurchaseOrderItemFormSet(request.POST or None, instance=obj, prefix='items')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            obj = form.save()
            formset.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=f'Cập nhật PO {obj.po_no}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã cập nhật PO "{obj.po_no}".')
        return redirect('purchasing:po_detail', pk=obj.pk)
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
    """APPROVED -> SENT (POST-only)."""
    obj = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method == 'POST':
        try:
            obj = send_po(obj, actor=request.user, ip_address=client_ip(request))
            if obj._email_sent:
                messages.success(request, f'Đã gửi PO "{obj.po_no}" và email thông báo tới NCC.')
            else:
                messages.warning(
                    request,
                    f'Đã chuyển PO "{obj.po_no}" sang trạng thái Gửi NCC, nhưng NCC chưa có email liên hệ '
                    f'— vui lòng tự thông báo cho NCC qua kênh khác.')
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
    """READ — danh sách PR (Tab 1). Nhân viên phòng Mua hàng/Manager/Admin xem
    toàn bộ (cần bức tranh tổng để xử lý/duyệt); nhân viên phòng khác chỉ xem PR
    do chính mình tạo (``_pr_can_view_all``).
    """
    prs = PurchaseRequest.objects.select_related('warehouse', 'requested_by', 'assigned_to', 'linked_po').all()
    if not _pr_can_view_all(request.user):
        prs = prs.filter(Q(requested_by=request.user) | Q(assigned_to=request.user))
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
    """READ — chi tiết PR: item, trạng thái duyệt (qua ``Approval``), link sang PO
    tạo từ PR này (nếu có). Người ngoài phòng Mua hàng, hoặc nhân viên phòng Mua
    hàng thường không được chỉ định/chuyển tiếp, chỉ xem được PR do chính mình
    tạo (mirror lọc ở ``pr_list`` — chặn cả truy cập trực tiếp qua URL).
    """
    obj = get_object_or_404(
        PurchaseRequest.objects
        .select_related('warehouse', 'requested_by', 'assigned_to', 'decided_by', 'linked_po')
        .prefetch_related('items__product'),
        pk=pk,
    )
    if (
        not _pr_can_view_all(request.user)
        and obj.requested_by_id != request.user.id
        and obj.assigned_to_id != request.user.id
    ):
        raise PermissionDenied('Bạn chỉ xem được yêu cầu mua hàng do chính mình tạo hoặc được giao phụ trách.')
    can_forward = (
        can_decide_pr(request.user)
        and obj.status == PurchaseRequest.Status.APPROVED
        and not obj.linked_po_id
    )
    is_owner_editable = _pr_can_edit(request.user, obj)
    return render(request, 'purchasing/pr_detail.html', {
        'obj': obj,
        'approval': latest_approval_for(obj),
        'can_approve': can_decide_pr(request.user) and obj.status == PurchaseRequest.Status.PENDING,
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
    """Ai sửa/nộp/mở lại được 1 PR: chỉ đúng chủ (``requested_by``) hoặc người có
    tầm nhìn toàn bộ (``_pr_can_view_all`` — quản lý phòng Mua hàng/Manager/Admin),
    mirror check hiển thị ở ``pr_detail`` — không dựa mỗi decorator quyền module.
    """
    return _pr_can_view_all(user) or pr.requested_by_id == user.id


@pr_permission_required('update')
def pr_update(request, pk):
    """UPDATE — sửa PR + chi tiết đơn hàng. Chỉ cho sửa khi còn DRAFT (mirror
    ``po_update``) và chỉ đúng chủ hoặc người có tầm nhìn toàn bộ mới sửa được
    (mirror check hiển thị ở ``pr_detail`` — quyền module ``update`` một mình
    không đủ, còn phải đúng người).
    """
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not _pr_can_edit(request.user, obj):
        raise PermissionDenied('Bạn chỉ sửa được yêu cầu mua hàng do chính mình tạo.')
    if obj.status != PurchaseRequest.Status.DRAFT:
        messages.error(request, f'Không thể sửa yêu cầu "{obj.request_no}" khi đã qua state Nháp.')
        return redirect('purchasing:pr_detail', pk=obj.pk)

    form = PurchaseRequestForm(request.POST or None, instance=obj)
    formset = PurchaseRequestItemFormSet(request.POST or None, instance=obj, prefix='items')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            obj = form.save()
            formset.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=f'Cập nhật yêu cầu mua hàng {obj.request_no}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã cập nhật yêu cầu mua hàng "{obj.request_no}".')
        return redirect('purchasing:pr_detail', pk=obj.pk)
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
def pr_approve(request, pk):
    """Quản lý phòng Mua hàng (hoặc Manager/Admin) duyệt PR: PENDING -> APPROVED (POST-only)."""
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not can_decide_pr(request.user):
        raise PermissionDenied('Không có quyền duyệt yêu cầu mua hàng.')
    if request.method == 'POST':
        approval = latest_approval_for(obj)
        try:
            if approval is None or approval.status != Approval.Status.PENDING:
                raise ValidationError('Yêu cầu này không có phiếu duyệt nào đang chờ xử lý.')
            decide_purchase_request(approval, True, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã duyệt yêu cầu "{obj.request_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:pr_detail', pk=obj.pk)


@login_required
def pr_reject(request, pk):
    """Quản lý phòng Mua hàng (hoặc Manager/Admin) từ chối PR kèm lý do: PENDING ->
    REJECTED (POST-only)."""
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not can_decide_pr(request.user):
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
    if not can_decide_pr(request.user):
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
