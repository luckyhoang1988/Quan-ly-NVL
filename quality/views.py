"""View app quality: nhập kết quả QC PASS/FAIL/PARTIAL_PASS (mục 2c) ở state
QC_IN_PROGRESS. View chỉ thu thập input rồi gọi thẳng transaction atomic trong
``quality.services`` (đã có unit test riêng từng nhánh) — không lặp lại logic
nghiệp vụ ở đây.

Phân quyền: chốt 1 permission duy nhất — ``user.can('approve', 'qc')``. Theo
Permission Matrix (BACKLOG mục 1a), quyền 'approve' trên module 'qc' chỉ QC/
MANAGER/ADMIN có (STAFF/PURCHASING/ACCOUNTANT chỉ Read) — khớp đúng nhóm được
phép ra quyết định PASS/FAIL/PARTIAL_PASS, kể cả case "Supervisor override"
(mục 2b QC Criteria & Sampling).
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from accounts.audit import client_ip, log_action
from accounts.models import AuditLog
from receiving.models import Grn

from .forms import QcCriteriaForm, QcInspectionItemFormSet, QcItemResultFormSet, QcResultForm
from .models import QcCriteria, QcInspection
from .services import qc_fail, qc_partial_pass, qc_pass


def qc_permission_required(action):
    """Decorator factory: chưa đăng nhập -> login; thiếu quyền ``action`` trên
    module 'qc' -> 403.
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not request.user.can(action, 'qc'):
                raise PermissionDenied(f'Không có quyền "{action}" trên QC.')
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


@qc_permission_required('approve')
def qc_result(request, grn_pk):
    """State QC_IN_PROGRESS: nhập kết quả Pass/Fail/Partial (mục 2a Workflow States).

    2 <form> độc lập cùng post về view này:
    - Form quyết định tổng — 3 nút hành động (``name="action"``) ứng với 3
      nhánh transaction mục 2c: ``pass``/``fail``/``partial`` ->
      ``quality.services.qc_pass/qc_fail/qc_partial_pass``.
    - Form "Lưu kết quả từng tiêu chuẩn" (không có field ``action``) -> lưu
      ``QcInspectionItemFormSet`` (FR-QC-03, PASS/FAIL từng criteria trên từng
      GrnItem). Đây thuần là ghi nhận dữ liệu, KHÔNG gate quyết định tổng —
      transaction PASS/FAIL/PARTIAL vẫn hoạt động độc lập như trước (mục 2c
      không yêu cầu đủ criteria mới được quyết định, vì sampling có thể chỉ
      kiểm 1 phần).
    """
    grn = get_object_or_404(Grn, pk=grn_pk)
    if grn.status != Grn.Status.QC_IN_PROGRESS:
        messages.error(request, f'GRN "{grn.grn_no}" không ở trạng thái QC_IN_PROGRESS.')
        return redirect('receiving:grn_detail', pk=grn.pk)

    inspection = grn.qc_inspections.filter(status=QcInspection.Result.PENDING_QC).order_by('-created_at').first()
    if inspection is None:
        messages.error(request, f'Không tìm thấy QC inspection đang chờ kết quả cho "{grn.grn_no}".')
        return redirect('receiving:grn_detail', pk=grn.pk)

    action = request.POST.get('action') if request.method == 'POST' else None
    items_submit = request.method == 'POST' and action is None

    result_form = QcResultForm(request.POST if action else None)
    formset = QcItemResultFormSet(request.POST if action else None, instance=grn, prefix='items')
    item_formset = QcInspectionItemFormSet(
        request.POST if items_submit else None, request.FILES if items_submit else None,
        instance=inspection, prefix='qcitems', form_kwargs={'grn': grn},
    )

    if items_submit:
        if item_formset.is_valid():
            item_formset.save()
            messages.success(request, 'Đã lưu kết quả QC từng tiêu chuẩn.')
            return redirect('quality:qc_result', grn_pk=grn.pk)
        messages.error(request, 'Kết quả QC từng tiêu chuẩn có lỗi, vui lòng kiểm tra lại.')

    elif action in ('pass', 'fail', 'partial'):
        needs_location = action in ('pass', 'partial')
        if needs_location and result_form.is_valid() and not result_form.cleaned_data['location']:
            result_form.add_error('location', 'Bắt buộc chọn vị trí lưu kho.')

        if result_form.is_valid() and (action != 'partial' or formset.is_valid()):
            location = result_form.cleaned_data['location']
            reason = result_form.cleaned_data['reason'] or 'QC Fail'
            ip_address = client_ip(request)
            try:
                if action == 'pass':
                    qc_pass(inspection, actor=request.user, location=location, ip_address=ip_address)
                elif action == 'fail':
                    qc_fail(inspection, actor=request.user, reason=reason, ip_address=ip_address)
                else:
                    item_results = {
                        form.instance.pk: form.cleaned_data.get('qty_pass') or 0
                        for form in formset.forms
                    }
                    qc_partial_pass(
                        inspection, item_results, actor=request.user,
                        location=location, ip_address=ip_address,
                    )
                messages.success(request, f'Đã ghi kết quả QC cho "{grn.grn_no}".')
                return redirect('receiving:grn_detail', pk=grn.pk)
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))

    return render(request, 'quality/qc_result.html', {
        'grn': grn, 'inspection': inspection, 'result_form': result_form, 'formset': formset,
        'item_formset': item_formset, 'criteria_ref': QcCriteria.objects.filter(is_active=True),
    })


@qc_permission_required('read')
def qc_criteria_list(request):
    """READ — danh sách tiêu chuẩn QC master data (FR-QC-02)."""
    criteria = QcCriteria.objects.all()
    return render(request, 'quality/qc_criteria_list.html', {
        'criteria': criteria, 'can_manage': request.user.can('create', 'qc'),
    })


@qc_permission_required('create')
def qc_criteria_create(request):
    """CREATE — thêm tiêu chuẩn QC mới (FR-QC-02)."""
    form = QcCriteriaForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj,
            description=f'Tạo tiêu chuẩn QC {obj.category} - {obj.name}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã tạo tiêu chuẩn QC "{obj}".')
        return redirect('quality:qc_criteria_list')
    return render(request, 'quality/qc_criteria_form.html', {'form': form, 'mode': 'create'})


@qc_permission_required('update')
def qc_criteria_update(request, pk):
    """UPDATE — sửa tiêu chuẩn QC (FR-QC-02)."""
    obj = get_object_or_404(QcCriteria, pk=pk)
    form = QcCriteriaForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=f'Cập nhật tiêu chuẩn QC {obj.category} - {obj.name}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã cập nhật tiêu chuẩn QC "{obj}".')
        return redirect('quality:qc_criteria_list')
    return render(request, 'quality/qc_criteria_form.html', {'form': form, 'mode': 'update', 'obj': obj})


@qc_permission_required('update')
def qc_criteria_toggle_active(request, pk):
    """Bật/tắt hoạt động 1 tiêu chuẩn QC (POST-only, không cần trang xác nhận —
    cùng cơ chế soft-delete với ``warehouse.location_toggle_active``).
    """
    obj = get_object_or_404(QcCriteria, pk=pk)
    if request.method == 'POST':
        obj.is_active = not obj.is_active
        obj.save(update_fields=['is_active'])
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=f'{"Mở lại" if obj.is_active else "Khoá"} tiêu chuẩn QC {obj.category} - {obj.name}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã {"mở lại" if obj.is_active else "khoá"} tiêu chuẩn QC "{obj}".')
    return redirect('quality:qc_criteria_list')
