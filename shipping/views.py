"""View app shipping: GIN (mục 3b) — DRAFT (tạo) -> PICKING (FIFO suggest +
override batch) -> ISSUED (trừ Inventory/Batch) -> CLOSED (archive). View chỉ
thu thập input rồi gọi thẳng transaction atomic trong ``shipping.services`` —
không lặp lại logic nghiệp vụ ở đây (cùng convention với ``receiving``/``quality``).

Phân quyền: ``user.can(action, 'gin')`` (BACKLOG mục 1a Permission Matrix) —
khác GRN, ở GIN chỉ MANAGER/ADMIN có 'update' (bắt đầu soạn hàng, đổi batch) và
'approve' (xuất kho, đóng phiếu); STAFF chỉ có 'create'+'read'. Từ Phase C, STAFF
dùng quyền 'create' đó để "xác nhận yêu cầu xuất" (DRAFT -> PENDING_APPROVAL),
rồi phải chờ quản lý Kho duyệt mới thật sự sang PICKING — mirror luồng nộp/duyệt
GRN (``receiving.views``).
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.approvals import latest_approval_for
from accounts.audit import client_ip, log_action
from accounts.models import Approval, AuditLog, User
from accounts.pagination import paginate_queryset
from warehouse.models import Warehouse

from .forms import GinAllocationOverrideForm, GinForm, GinItemFormSet
from .models import Gin, GinBatchAllocation
from .services import (
    cancel_gin,
    close_gin,
    decide_gin_confirmation,
    issue_gin,
    override_allocation,
    request_confirmation,
    start_picking,
)


def can_decide_gin_confirmation(user):
    """Quản lý Kho, hoặc Manager/Admin qua quyền ``approve`` sẵn có trên GIN."""
    return user.is_department_manager(User.Department.WAREHOUSE) or user.can('approve', 'gin')


def can_cancel_gin(user):
    """Quản lý Kho, hoặc Manager/Admin qua quyền ``delete`` sẵn có trên GIN."""
    return user.is_department_manager(User.Department.WAREHOUSE) or user.can('delete', 'gin')


def gin_permission_required(action):
    """Decorator factory: chưa đăng nhập -> login; thiếu quyền ``action`` trên
    module 'gin' -> 403.
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not request.user.can(action, 'gin'):
                raise PermissionDenied(f'Không có quyền "{action}" trên GIN.')
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


@gin_permission_required('read')
def gin_list(request):
    """READ — danh sách GIN."""
    gins = Gin.objects.select_related('warehouse', 'requested_by').all()
    selected_status = request.GET.get('status', '')
    if selected_status:
        gins = gins.filter(status=selected_status)
    selected_warehouse = None
    warehouse_id = request.GET.get('warehouse')
    if warehouse_id:
        try:
            selected_warehouse = int(warehouse_id)
        except (TypeError, ValueError):
            selected_warehouse = None
    if selected_warehouse:
        gins = gins.filter(warehouse_id=selected_warehouse)
    q = request.GET.get('q', '').strip()
    if q:
        gins = gins.filter(gin_no__icontains=q)
    page_obj, page_size = paginate_queryset(request, gins)
    return render(request, 'shipping/gin_list.html', {
        'gins': page_obj,
        'page_obj': page_obj,
        'page_size': page_size,
        'can_create': request.user.can('create', 'gin'),
        'statuses': Gin.Status.choices,
        'warehouses': Warehouse.objects.filter(is_active=True, warehouse_type=Warehouse.WarehouseType.MAIN),
        'selected_status': selected_status,
        'selected_warehouse': selected_warehouse,
        'q': q,
    })


@gin_permission_required('read')
def gin_detail(request, pk):
    """READ — chi tiết GIN: dòng hàng + batch đã phân bổ + yêu cầu duyệt xác nhận."""
    gin = get_object_or_404(
        Gin.objects.select_related('warehouse', 'requested_by')
        .prefetch_related('items__product', 'items__allocations__batch__location'),
        pk=pk,
    )
    can_update = request.user.can('update', 'gin')
    return render(request, 'shipping/gin_detail.html', {
        'gin': gin,
        'can_update': can_update,
        'can_approve': request.user.can('approve', 'gin'),
        'can_confirm_request': request.user.can('create', 'gin') and not can_update,
        'can_cancel': can_cancel_gin(request.user),
        'can_decide_confirmation': can_decide_gin_confirmation(request.user),
        'latest_confirmation_approval': latest_approval_for(gin),
    })


@gin_permission_required('read')
def gin_print(request, pk):
    """READ — trang in phiếu GIN kèm barcode để kiểm soát khi xuất kho (FR-GIN-06).

    Barcode encode ``alloc.batch.batch_code`` — batch đã tồn tại thật (không như
    GRN phải tự sinh label_code dự kiến), vì GIN chỉ xuất từ batch đã có sẵn
    trong kho (xem ``GinBatchAllocation``).
    """
    gin = get_object_or_404(
        Gin.objects.select_related('warehouse')
        .prefetch_related('items__product', 'items__allocations__batch__location'),
        pk=pk,
    )
    return render(request, 'shipping/gin_print.html', {'gin': gin})


@gin_permission_required('create')
def gin_create(request):
    """CREATE — tạo GIN (state DRAFT) kèm dòng hàng yêu cầu xuất (formset, FR-GIN-01)."""
    form = GinForm(request.POST or None)
    formset = GinItemFormSet(request.POST or None, instance=Gin(), prefix='items')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.requested_by = request.user
            obj.save()
            formset.instance = obj
            formset.save()
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj,
            description=f'Tạo GIN {obj.gin_no}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã tạo GIN "{obj.gin_no}".')
        return redirect('shipping:gin_detail', pk=obj.pk)
    return render(request, 'shipping/gin_form.html', {'form': form, 'formset': formset})


@gin_permission_required('update')
def gin_start_picking(request, pk):
    """DRAFT -> PICKING: gợi ý FIFO cho từng dòng hàng (FR-GIN-02, POST-only).

    Chỉ user có quyền ``update`` (Manager/Admin) mới thấy nút này — tự bắt đầu
    soạn hàng không cần qua duyệt vì họ chính là cấp duyệt. STAFF (chỉ có
    ``create``) dùng nút "Xác nhận yêu cầu xuất" (``gin_confirm_request``) thay
    thế, phải chờ quản lý Kho duyệt mới thật sự chạy ``start_picking``.
    """
    obj = get_object_or_404(Gin, pk=pk)
    if request.method == 'POST':
        try:
            start_picking(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã chuyển GIN "{obj.gin_no}" sang PICKING, đã gợi ý FIFO.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('shipping:gin_detail', pk=obj.pk)


@login_required
def gin_confirm_request(request, pk):
    """DRAFT -> PENDING_APPROVAL: NV xác nhận yêu cầu xuất, chờ quản lý Kho duyệt
    (POST-only). Gate bằng quyền 'create' — đủ cho STAFF (không có 'update' để
    tự bắt đầu soạn hàng trực tiếp như Manager/Admin)."""
    obj = get_object_or_404(Gin, pk=pk)
    if not request.user.can('create', 'gin'):
        raise PermissionDenied('Không có quyền xác nhận yêu cầu xuất GIN.')
    if request.method == 'POST':
        try:
            request_confirmation(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã xác nhận yêu cầu xuất GIN "{obj.gin_no}", chờ quản lý Kho duyệt.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('shipping:gin_detail', pk=obj.pk)


@login_required
def gin_approve_confirmation(request, pk):
    """Quản lý Kho duyệt yêu cầu xác nhận xuất: PENDING_APPROVAL -> PICKING (POST-only)."""
    obj = get_object_or_404(Gin, pk=pk)
    if not can_decide_gin_confirmation(request.user):
        raise PermissionDenied('Không có quyền duyệt yêu cầu xác nhận xuất GIN.')
    if request.method == 'POST':
        approval = latest_approval_for(obj)
        try:
            if approval is None or approval.status != Approval.Status.PENDING:
                raise ValidationError('GIN này không có yêu cầu duyệt nào đang chờ xử lý.')
            decide_gin_confirmation(approval, True, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã duyệt xác nhận xuất GIN "{obj.gin_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('shipping:gin_detail', pk=obj.pk)


@login_required
def gin_reject_confirmation(request, pk):
    """Quản lý Kho từ chối yêu cầu xác nhận xuất: PENDING_APPROVAL -> DRAFT (POST-only)."""
    obj = get_object_or_404(Gin, pk=pk)
    if not can_decide_gin_confirmation(request.user):
        raise PermissionDenied('Không có quyền từ chối yêu cầu xác nhận xuất GIN.')
    if request.method == 'POST':
        approval = latest_approval_for(obj)
        note = request.POST.get('note', '')
        try:
            if approval is None or approval.status != Approval.Status.PENDING:
                raise ValidationError('GIN này không có yêu cầu duyệt nào đang chờ xử lý.')
            decide_gin_confirmation(approval, False, actor=request.user, note=note, ip_address=client_ip(request))
            messages.success(request, f'Đã từ chối xác nhận xuất GIN "{obj.gin_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('shipping:gin_detail', pk=obj.pk)


@login_required
def gin_cancel(request, pk):
    """Hủy GIN (POST-only) — quản lý Kho, hoặc Manager/Admin qua quyền delete."""
    obj = get_object_or_404(Gin, pk=pk)
    if not can_cancel_gin(request.user):
        raise PermissionDenied('Không có quyền hủy GIN này.')
    if request.method == 'POST':
        note = request.POST.get('note', '')
        try:
            cancel_gin(obj, actor=request.user, note=note, ip_address=client_ip(request))
            messages.success(request, f'Đã hủy GIN "{obj.gin_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('shipping:gin_detail', pk=obj.pk)


@gin_permission_required('update')
def gin_picking(request, pk):
    """State PICKING: xem allocation FIFO đã gợi ý, đổi batch nếu cần (FR-GIN-03)."""
    obj = get_object_or_404(
        Gin.objects.prefetch_related('items__product', 'items__allocations__batch__location'), pk=pk,
    )
    if obj.status != Gin.Status.PICKING:
        messages.error(request, f'GIN "{obj.gin_no}" không ở trạng thái PICKING.')
        return redirect('shipping:gin_detail', pk=obj.pk)

    for item in obj.items.all():
        for alloc in item.allocations.all():
            # Gắn trực tiếp lên instance để template dùng {{ alloc.override_form.* }},
            # tránh phải tự viết filter lookup-by-variable-key trong Django template.
            alloc.override_form = GinAllocationOverrideForm(
                prefix=f'override-{alloc.pk}', product=item.product, warehouse=obj.warehouse,
            )
    return render(request, 'shipping/gin_picking.html', {'gin': obj})


@gin_permission_required('update')
def gin_allocation_override(request, pk):
    """FR-GIN-03: đổi batch khác gợi ý FIFO cho 1 dòng allocation (POST-only)."""
    allocation = get_object_or_404(
        GinBatchAllocation.objects.select_related('gin_item__gin', 'gin_item__product'), pk=pk,
    )
    gin = allocation.gin_item.gin
    if request.method == 'POST':
        form = GinAllocationOverrideForm(
            request.POST, prefix=f'override-{pk}',
            product=allocation.gin_item.product, warehouse=gin.warehouse,
        )
        if form.is_valid():
            try:
                override_allocation(
                    allocation, form.cleaned_data['batch'], form.cleaned_data['reason'],
                    actor=request.user, ip_address=client_ip(request),
                )
                messages.success(request, 'Đã đổi batch cho dòng hàng.')
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
        else:
            messages.error(request, 'Chọn batch/lý do không hợp lệ.')
    return redirect('shipping:gin_picking', pk=gin.pk)


@gin_permission_required('approve')
def gin_issue(request, pk):
    """PICKING -> ISSUED (FR-GIN-04/FR-GIN-05): trừ Inventory/Batch (POST-only)."""
    obj = get_object_or_404(Gin, pk=pk)
    if request.method == 'POST':
        try:
            issue_gin(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã xuất kho GIN "{obj.gin_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('shipping:gin_detail', pk=obj.pk)


@gin_permission_required('approve')
def gin_close(request, pk):
    """ISSUED -> CLOSED (archive, POST-only)."""
    obj = get_object_or_404(Gin, pk=pk)
    if request.method == 'POST':
        try:
            close_gin(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã đóng GIN "{obj.gin_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('shipping:gin_detail', pk=obj.pk)
