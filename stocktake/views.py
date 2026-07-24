"""View app stocktake: Stock Opname (BACKLOG mục 4) — PLANNING (tạo phiếu) ->
EXECUTION (quét barcode nhập Qty thực tế) -> RECONCILIATION (đối soát) ->
ADJUSTMENT (tự động điều chỉnh Inventory, terminal). View chỉ thu thập input
rồi gọi thẳng transaction atomic trong ``stocktake.services`` — không lặp lại
logic nghiệp vụ ở đây (cùng convention với ``receiving``/``quality``/``shipping``).

Phân quyền: ``user.can(action, 'opname')`` (BACKLOG mục 1a Permission Matrix,
codename module ``opname`` — xem ``accounts.permissions.MODULES``). MANAGER/
ADMIN có đủ CRUD+approve; STAFF có CRU (tạo phiếu, đếm hàng, gửi đối soát)
nhưng KHÔNG có approve — chỉ MANAGER/ADMIN mới được áp dụng Adjustment cuối
cùng (FR-SO-05); QC/PURCHASING/ACCOUNTANT không có quyền gì trên module này.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from accounts.audit import client_ip

from .forms import StocktakeCountForm, StocktakeSessionForm
from .models import StocktakeSession
from .services import apply_adjustment, create_session, record_count, start_execution, submit_reconciliation


def stocktake_permission_required(action):
    """Decorator factory: chưa đăng nhập -> login; thiếu quyền ``action`` trên
    module 'opname' -> 403.
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not request.user.can(action, 'opname'):
                raise PermissionDenied(f'Không có quyền "{action}" trên Stock Opname.')
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


@stocktake_permission_required('read')
def so_list(request):
    """READ — danh sách phiếu kiểm kê."""
    sessions = StocktakeSession.objects.select_related('warehouse', 'location', 'created_by').all()
    return render(request, 'stocktake/so_list.html', {
        'sessions': sessions, 'can_create': request.user.can('create', 'opname'),
    })


@stocktake_permission_required('read')
def so_detail(request, pk):
    """READ — chi tiết phiếu: header + toàn bộ dòng SKU kèm chênh lệch (FR-SO-06)."""
    session = get_object_or_404(
        StocktakeSession.objects.select_related('warehouse', 'location', 'created_by')
        .prefetch_related('items__product', 'items__counted_by'),
        pk=pk,
    )
    return render(request, 'stocktake/so_detail.html', {
        'session': session,
        'can_update': request.user.can('update', 'opname'),
        'can_approve': request.user.can('approve', 'opname'),
    })


@stocktake_permission_required('create')
def so_create(request):
    """CREATE — FR-SO-01/FR-SO-07: chọn kho/vị trí, tạo phiếu PLANNING kèm
    snapshot danh sách SKU (qua ``stocktake.services.create_session``).
    """
    form = StocktakeSessionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            session = create_session(
                warehouse=form.cleaned_data['warehouse'],
                location=form.cleaned_data['location'] or None,
                notes=form.cleaned_data['notes'],
                created_by=request.user,
                actor=request.user, ip_address=client_ip(request),
            )
            messages.success(request, f'Đã tạo phiếu kiểm kê "{session.so_no}".')
            return redirect('stocktake:so_detail', pk=session.pk)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return render(request, 'stocktake/so_form.html', {'form': form})


@stocktake_permission_required('update')
def so_start_execution(request, pk):
    """PLANNING -> EXECUTION (POST-only)."""
    session = get_object_or_404(StocktakeSession, pk=pk)
    if request.method == 'POST':
        try:
            start_execution(session, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã bắt đầu kiểm đếm phiếu "{session.so_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('stocktake:so_detail', pk=session.pk)


@stocktake_permission_required('update')
def so_execution(request, pk):
    """State EXECUTION: quét mã SKU + nhập Qty thực tế từng dòng (FR-SO-02),
    hiện ngay Qty hệ thống vs Qty đã đếm cho toàn bộ danh sách bên dưới form.
    """
    session = get_object_or_404(
        StocktakeSession.objects.prefetch_related('items__product', 'items__counted_by'), pk=pk,
    )
    if session.status != StocktakeSession.Status.EXECUTION:
        messages.error(request, f'Phiếu "{session.so_no}" không ở trạng thái EXECUTION.')
        return redirect('stocktake:so_detail', pk=session.pk)

    form = StocktakeCountForm(request.POST or None, session=session)
    if request.method == 'POST' and form.is_valid():
        try:
            record_count(
                form.item, form.cleaned_data['qty_actual'], counted_by=request.user,
                reason=form.cleaned_data['reason'], actor=request.user, ip_address=client_ip(request),
            )
            messages.success(request, f'Đã ghi nhận SKU "{form.item.product.product_code}".')
            return redirect('stocktake:so_execution', pk=session.pk)
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
    return render(request, 'stocktake/so_execution.html', {'session': session, 'form': form})


@stocktake_permission_required('update')
def so_submit_reconciliation(request, pk):
    """EXECUTION -> RECONCILIATION (POST-only)."""
    session = get_object_or_404(StocktakeSession, pk=pk)
    if request.method == 'POST':
        try:
            submit_reconciliation(session, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã chuyển phiếu "{session.so_no}" sang đối soát.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('stocktake:so_detail', pk=session.pk)


@stocktake_permission_required('approve')
def so_apply_adjustment(request, pk):
    """FR-SO-05: RECONCILIATION -> ADJUSTMENT (POST-only, terminal)."""
    session = get_object_or_404(StocktakeSession, pk=pk)
    if request.method == 'POST':
        try:
            apply_adjustment(session, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã áp dụng điều chỉnh cho phiếu "{session.so_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('stocktake:so_detail', pk=session.pk)
