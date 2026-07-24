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
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render

from accounts.audit import client_ip, log_action
from accounts.models import AuditLog
from accounts.pagination import paginate_queryset
from catalog.models import Product
from partners.models import Supplier
from receiving.models import GrnItem

from .forms import PurchaseOrderForm, PurchaseOrderItemFormSet
from .models import PurchaseOrder
from .services import approve_po, close_po, send_po, supplier_lead_time_stats, supplier_price_history


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


@po_permission_required('read')
def po_list(request):
    """READ — danh sách PO, kèm cờ giao hàng trễ hạn (FR-PO-06, tính on-the-fly)."""
    orders = PurchaseOrder.objects.select_related('supplier').all()
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
    overdue_count = sum(
        1 for po in orders if (po.delivery_status() or {}).get('code') == 'DELAYED')
    page_obj, page_size = paginate_queryset(request, orders)
    return render(request, 'purchasing/po_list.html', {
        'orders': page_obj,
        'page_obj': page_obj,
        'page_size': page_size,
        'overdue_count': overdue_count,
        'can_create': request.user.can('create', 'po'),
        'can_update': request.user.can('update', 'po'),
        'statuses': PurchaseOrder.Status.choices,
        'suppliers': Supplier.objects.filter(is_active=True),
        'selected_status': selected_status,
        'selected_supplier': selected_supplier,
        'q': q,
    })


@po_permission_required('read')
def po_detail(request, pk):
    """READ — chi tiết PO: item kèm Qty đã nhận/còn lại (FR-PO-04 reconciliation,
    tính on-the-fly từ GrnItem giống ``services.sync_po_status``), badge giao
    hàng (FR-PO-06), nút chuyển trạng thái theo quyền.
    """
    po = get_object_or_404(PurchaseOrder.objects.select_related('supplier').prefetch_related('items__product'), pk=pk)

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
    return render(request, 'purchasing/po_detail.html', {
        'po': po,
        'item_rows': item_rows,
        'delivery_status': po.delivery_status(),
        'can_update': can_update,
        'can_approve': can_approve,
        'can_edit': can_update and po.status == PurchaseOrder.Status.DRAFT,
        'can_close': can_approve and po.status in closeable_statuses,
    })


@po_permission_required('create')
def po_create(request):
    """CREATE — tạo PO kèm chi tiết đơn hàng (formset), tối thiểu 1 dòng item.

    ``?product=<id>&qty=<n>`` (FR-PO-02, link từ dashboard tồn kho dưới Min
    Level — xem ``inventory/views.py::inventory_list``) prefill sẵn dòng item
    đầu tiên; NCC vẫn để trống, người dùng tự chọn (xem ``po_price_comparison``
    để so sánh giá trước khi chọn).
    """
    initial = None
    if request.method == 'GET':
        product_id = request.GET.get('product')
        qty = request.GET.get('qty')
        if product_id and qty:
            initial = [{'product': product_id, 'qty_ordered': qty}]

    form = PurchaseOrderForm(request.POST or None)
    formset = PurchaseOrderItemFormSet(
        request.POST or None, instance=PurchaseOrder(), prefix='items', initial=initial)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            obj = form.save()
            formset.instance = obj
            formset.save()
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj,
            description=f'Tạo PO {obj.po_no}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã tạo PO "{obj.po_no}".')
        return redirect('purchasing:po_detail', pk=obj.pk)
    return render(request, 'purchasing/po_form.html', {'form': form, 'formset': formset, 'mode': 'create'})


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
            send_po(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã gửi PO "{obj.po_no}" tới NCC.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:po_detail', pk=obj.pk)


@po_permission_required('approve')
def po_close(request, pk):
    """{SENT, PARTIAL_RECEIVED, RECEIVED} -> CLOSED (POST-only)."""
    obj = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method == 'POST':
        try:
            close_po(obj, actor=request.user, ip_address=client_ip(request))
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
