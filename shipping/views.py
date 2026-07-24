"""View app shipping: GIN (mục 3b) — DRAFT (tạo) -> PICKING (FIFO suggest +
override batch) -> ISSUED (trừ Inventory/Batch) -> CLOSED (archive). View chỉ
thu thập input rồi gọi thẳng transaction atomic trong ``shipping.services`` —
không lặp lại logic nghiệp vụ ở đây (cùng convention với ``receiving``/``quality``).

Phân quyền: ``user.can(action, 'gin')`` (BACKLOG mục 1a Permission Matrix) —
khác GRN, ở GIN chỉ MANAGER/ADMIN có 'update' (bắt đầu soạn hàng, đổi batch) và
'approve' (xuất kho, đóng phiếu); STAFF chỉ có 'create'+'read' (tạo yêu cầu xuất,
không tự thực hiện soạn/xuất).
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.audit import client_ip, log_action
from accounts.models import AuditLog

from .forms import GinAllocationOverrideForm, GinForm, GinItemFormSet
from .models import Gin, GinBatchAllocation
from .services import close_gin, issue_gin, override_allocation, start_picking


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
    return render(request, 'shipping/gin_list.html', {
        'gins': gins, 'can_create': request.user.can('create', 'gin'),
    })


@gin_permission_required('read')
def gin_detail(request, pk):
    """READ — chi tiết GIN: dòng hàng + batch đã phân bổ."""
    gin = get_object_or_404(
        Gin.objects.select_related('warehouse', 'requested_by')
        .prefetch_related('items__product', 'items__allocations__batch__location'),
        pk=pk,
    )
    return render(request, 'shipping/gin_detail.html', {
        'gin': gin,
        'can_update': request.user.can('update', 'gin'),
        'can_approve': request.user.can('approve', 'gin'),
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
    """DRAFT -> PICKING: gợi ý FIFO cho từng dòng hàng (FR-GIN-02, POST-only)."""
    obj = get_object_or_404(Gin, pk=pk)
    if request.method == 'POST':
        try:
            start_picking(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã chuyển GIN "{obj.gin_no}" sang PICKING, đã gợi ý FIFO.')
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
