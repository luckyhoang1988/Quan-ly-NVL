"""View app receiving: GRN (mục 2a) — DRAFT (tạo/sửa/submit) và PENDING_QC (nhập
Qty thực nhận + Submit to QC). QC_IN_PROGRESS trở đi (nhập kết quả QC) thuộc app
``quality`` (xem ``quality/views.py:qc_result``) vì gắn với ``QcInspection``.

Phân quyền: dùng RBAC thật qua ``user.can(action, 'grn')`` (BACKLOG mục 1a Permission
Matrix) — MANAGER/STAFF/ADMIN có Create+Update trên GRN (STAFF cần 'update' để nhập
Qty thực nhận + Submit to QC ở PENDING_QC, xem ``accounts/permissions.py``); QC/
PURCHASING/ACCOUNTANT chỉ Read.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.audit import client_ip, log_action
from accounts.models import AuditLog
from accounts.pagination import paginate_queryset
from partners.models import Supplier
from purchasing.services import sync_po_status
from quality.services import overdue_inspections, start_qc

from .forms import GrnForm, GrnItemFormSet, ReceiveQtyFormSet, SubmitToQcForm
from .models import Grn, GrnReturn
from .services import (
    approve_return,
    close_grn,
    close_return,
    mark_return_returned,
    submit_to_pending_qc,
    tolerance_alerts,
)


def grn_permission_required(action):
    """Decorator factory: chưa đăng nhập -> login; thiếu quyền ``action`` trên
    module 'grn' -> 403.
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not request.user.can(action, 'grn'):
                raise PermissionDenied(f'Không có quyền "{action}" trên GRN.')
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


@grn_permission_required('read')
def grn_list(request):
    """READ — danh sách GRN."""
    grns = Grn.objects.select_related('supplier', 'po').all()
    selected_status = request.GET.get('status', '')
    if selected_status:
        grns = grns.filter(status=selected_status)
    selected_supplier = None
    supplier_id = request.GET.get('supplier')
    if supplier_id:
        try:
            selected_supplier = int(supplier_id)
        except (TypeError, ValueError):
            selected_supplier = None
    if selected_supplier:
        grns = grns.filter(supplier_id=selected_supplier)
    q = request.GET.get('q', '').strip()
    if q:
        grns = grns.filter(grn_no__icontains=q)
    overdue_grn_ids = set(overdue_inspections().values_list('grn_id', flat=True))

    page_obj, page_size = paginate_queryset(request, grns)
    return render(request, 'receiving/grn_list.html', {
        'grns': page_obj,
        'page_obj': page_obj,
        'page_size': page_size,
        'can_create': request.user.can('create', 'grn'),
        'statuses': Grn.Status.choices,
        'suppliers': Supplier.objects.filter(status=Supplier.Status.ACTIVE),
        'selected_status': selected_status,
        'selected_supplier': selected_supplier,
        'q': q,
        'overdue_grn_ids': overdue_grn_ids,
        'overdue_qc_count': len(overdue_grn_ids),
    })


@grn_permission_required('read')
def grn_detail(request, pk):
    """READ — chi tiết GRN: items, lịch sử QC, phiếu trả hàng (nếu có)."""
    grn = get_object_or_404(
        Grn.objects.select_related('supplier', 'po').prefetch_related('items', 'qc_inspections', 'returns'), pk=pk)
    can_close = (
        request.user.can('approve', 'grn')
        and grn.status in (Grn.Status.RECEIVED, Grn.Status.REJECTED)
    )
    return render(request, 'receiving/grn_detail.html', {
        'grn': grn,
        'can_update': request.user.can('update', 'grn'),
        'can_qc': request.user.can('approve', 'qc'),
        'can_override_qc': request.user.can('override', 'qc'),
        'can_close': can_close,
        'can_manage_return': request.user.can('update', 'grn'),
        'can_approve_return': request.user.can('approve', 'grn'),
    })


@grn_permission_required('read')
def grn_print(request, pk):
    """READ — trang in phiếu GRN kèm barcode để dán lên hàng (FR-GRN-06).

    Barcode encode ``item.label_code`` — thuần UI/print, không đụng QC/Batch/
    Inventory (barcode ở đây là mã dự kiến dán lên hàng lúc nhận, không phải
    truy vấn lại Batch đã tạo sau QC — xem GrnItem.label_code).
    """
    grn = get_object_or_404(Grn.objects.select_related('supplier', 'po').prefetch_related('items__product'), pk=pk)
    return render(request, 'receiving/grn_print.html', {'grn': grn})


@grn_permission_required('create')
def grn_create(request):
    """CREATE — tạo GRN (state DRAFT) kèm chi tiết hàng (formset).

    Nút "Lưu nháp" chỉ lưu DRAFT; nút "Lưu & Submit" lưu xong chuyển luôn
    DRAFT -> PENDING_QC (mục 2a Workflow States: Save/Submit/Cancel).
    """
    form = GrnForm(request.POST or None)
    formset = GrnItemFormSet(request.POST or None, instance=Grn(), prefix='items')
    form_valid = request.method == 'POST' and form.is_valid()
    if form_valid:
        # BR mục 2a "Qty validation" (FR-GRN-07/FR-GRN-04) cần biết PO trước khi
        # validate formset — GRN chưa save nên phải gán tạm vào instance rỗng.
        formset.instance.po = form.cleaned_data['po']
    if form_valid and formset.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            formset.instance = obj
            formset.save()
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj,
            description=f'Tạo GRN {obj.grn_no}',
            ip_address=client_ip(request),
        )
        if request.POST.get('action') == 'submit':
            try:
                submit_to_pending_qc(obj, actor=request.user, ip_address=client_ip(request))
                messages.success(request, f'Đã tạo và submit GRN "{obj.grn_no}" sang PENDING_QC.')
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(request, f'Đã lưu nháp GRN "{obj.grn_no}".')
        return redirect('receiving:grn_detail', pk=obj.pk)
    return render(request, 'receiving/grn_form.html', {'form': form, 'formset': formset, 'mode': 'create'})


@grn_permission_required('update')
def grn_update(request, pk):
    """UPDATE — sửa GRN + chi tiết hàng. Chỉ cho sửa khi còn ở state DRAFT."""
    obj = get_object_or_404(Grn, pk=pk)
    if obj.status != Grn.Status.DRAFT:
        messages.error(request, f'Không thể sửa GRN "{obj.grn_no}" khi đã qua state DRAFT.')
        return redirect('receiving:grn_detail', pk=obj.pk)

    form = GrnForm(request.POST or None, instance=obj)
    formset = GrnItemFormSet(request.POST or None, instance=obj, prefix='items')
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            obj = form.save()
            formset.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=f'Cập nhật GRN {obj.grn_no}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã cập nhật GRN "{obj.grn_no}".')
        return redirect('receiving:grn_detail', pk=obj.pk)
    return render(
        request, 'receiving/grn_form.html',
        {'form': form, 'formset': formset, 'mode': 'update', 'obj': obj},
    )


@grn_permission_required('update')
def grn_submit(request, pk):
    """DRAFT -> PENDING_QC cho 1 GRN đã lưu nháp từ trước (POST-only)."""
    obj = get_object_or_404(Grn, pk=pk)
    if request.method == 'POST':
        try:
            submit_to_pending_qc(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã submit GRN "{obj.grn_no}" sang PENDING_QC.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('receiving:grn_detail', pk=obj.pk)


@grn_permission_required('update')
def grn_receive_qty(request, pk):
    """State PENDING_QC: nhập Qty thực tế nhận từng item, rồi Submit to QC
    (PENDING_QC -> QC_IN_PROGRESS, gọi ``quality.services.start_qc``).
    """
    obj = get_object_or_404(Grn, pk=pk)
    if obj.status != Grn.Status.PENDING_QC:
        messages.error(request, f'GRN "{obj.grn_no}" không ở trạng thái PENDING_QC.')
        return redirect('receiving:grn_detail', pk=obj.pk)

    formset = ReceiveQtyFormSet(request.POST or None, instance=obj, prefix='items')
    submit_form = SubmitToQcForm(request.POST or None)
    if request.method == 'POST' and formset.is_valid() and submit_form.is_valid():
        try:
            with transaction.atomic():
                formset.save()
                sync_po_status(obj.po)
                inspection = start_qc(
                    obj, submit_form.cleaned_data['inspector'],
                    actor=request.user, ip_address=client_ip(request),
                )
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            for alert in tolerance_alerts(obj):
                messages.warning(request, alert)
            messages.success(request, f'Đã submit GRN "{obj.grn_no}" sang QC ({inspection.qc_no}).')
            return redirect('receiving:grn_detail', pk=obj.pk)
    return render(request, 'receiving/grn_receive_qty.html', {
        'grn': obj, 'formset': formset, 'submit_form': submit_form,
    })


@grn_permission_required('approve')
def grn_close(request, pk):
    """RECEIVED/REJECTED -> CLOSED (archive, POST-only)."""
    obj = get_object_or_404(Grn, pk=pk)
    if request.method == 'POST':
        try:
            close_grn(obj, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã đóng GRN "{obj.grn_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('receiving:grn_detail', pk=obj.pk)


@grn_permission_required('approve')
def grn_return_approve(request, pk):
    """GRN_RETURN: PENDING -> APPROVED (POST-only)."""
    ret = get_object_or_404(GrnReturn, pk=pk)
    if request.method == 'POST':
        try:
            approve_return(ret, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã duyệt phiếu trả hàng RETURN-{ret.grn.grn_no}.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('receiving:grn_detail', pk=ret.grn_id)


@grn_permission_required('update')
def grn_return_mark_returned(request, pk):
    """GRN_RETURN: APPROVED -> RETURNED (POST-only)."""
    ret = get_object_or_404(GrnReturn, pk=pk)
    if request.method == 'POST':
        try:
            mark_return_returned(ret, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã xác nhận trả hàng RETURN-{ret.grn.grn_no}.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('receiving:grn_detail', pk=ret.grn_id)


@grn_permission_required('approve')
def grn_return_close(request, pk):
    """GRN_RETURN: RETURNED -> CLOSED (archive, POST-only)."""
    ret = get_object_or_404(GrnReturn, pk=pk)
    if request.method == 'POST':
        try:
            close_return(ret, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã đóng phiếu trả hàng RETURN-{ret.grn.grn_no}.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('receiving:grn_detail', pk=ret.grn_id)
