"""View app quality: nhập kết quả QC PASS/FAIL/PARTIAL_PASS (mục 2c) ở state
QC_IN_PROGRESS. View chỉ thu thập input rồi gọi thẳng transaction atomic trong
``quality.services`` (đã có unit test riêng từng nhánh) — không lặp lại logic
nghiệp vụ ở đây.

Phân quyền: quyền 'approve' trên module 'qc' (``user.can('approve', 'qc')``)
chỉ QC/MANAGER/ADMIN có (STAFF/PURCHASING/ACCOUNTANT chỉ Read) — khớp đúng
nhóm được phép ra quyết định PASS/FAIL/PARTIAL_PASS. Riêng "QC approval
override" (mục 2b, Supervisor xem lại 1 kết quả đã quyết định) dùng permission
RIÊNG ``user.can('override', 'qc')`` — chỉ MANAGER/ADMIN, không phải QC
Inspector (người đã ra quyết định ban đầu) — xem ``qc_override``.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.audit import client_ip, log_action
from accounts.models import AuditLog
from accounts.pagination import paginate_queryset
from receiving.models import Grn

from .forms import QcCriteriaForm, QcInspectionItemFormSet, QcItemResultFormSet, QcOverrideForm, QcResultForm
from .models import QcCriteria, QcInspection, QcInspectionItem
from .services import overdue_inspections, qc_fail, qc_partial_pass, qc_pass, suggested_sample_qty


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
    for form in formset.forms:
        form.instance.suggested_sample_qty = suggested_sample_qty(
            form.instance.product, form.instance.qty_received)
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
            assigned_to = result_form.cleaned_data['assigned_to']
            try:
                if action == 'pass':
                    qc_pass(
                        inspection, actor=request.user, location=location, ip_address=ip_address,
                        assigned_to=assigned_to,
                    )
                elif action == 'fail':
                    qc_fail(inspection, actor=request.user, reason=reason, ip_address=ip_address)
                else:
                    item_results = {
                        form.instance.pk: form.cleaned_data.get('qty_pass') or 0
                        for form in formset.forms
                    }
                    qc_partial_pass(
                        inspection, item_results, actor=request.user,
                        location=location, ip_address=ip_address, assigned_to=assigned_to,
                    )
                messages.success(request, f'Đã ghi kết quả QC cho "{grn.grn_no}".')
                return redirect('receiving:grn_detail', pk=grn.pk)
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))

    is_overdue = overdue_inspections().filter(pk=inspection.pk).exists()
    has_any_item = inspection.items.exists()
    has_fail_item = inspection.items.filter(result=QcInspectionItem.Result.FAIL).exists()
    has_pass_item = inspection.items.filter(result=QcInspectionItem.Result.PASS).exists()
    criteria_by_category = {}
    for c in QcCriteria.objects.filter(is_active=True).order_by('category', 'name'):
        criteria_by_category.setdefault(c.category, []).append(
            {'name': c.name, 'pass_rule': c.pass_rule, 'fail_rule': c.fail_rule})
    return render(request, 'quality/qc_result.html', {
        'grn': grn, 'inspection': inspection, 'result_form': result_form, 'formset': formset,
        'item_formset': item_formset, 'criteria_ref': QcCriteria.objects.filter(is_active=True),
        'is_overdue': is_overdue, 'has_any_item': has_any_item, 'has_fail_item': has_fail_item,
        'has_pass_item': has_pass_item, 'criteria_by_category': criteria_by_category,
    })


@qc_permission_required('override')
def qc_override(request, pk):
    """QC approval override (BACKLOG mục 2b) — Supervisor (Manager/Admin) ghi
    chú lý do xem lại 1 kết quả QC đã quyết định (PASS/FAIL/PARTIAL_PASS).

    Phạm vi đã chốt với user: CHỈ ghi ``override_note``/``overridden_by``/
    ``overridden_at`` lên ``QcInspection`` — KHÔNG đảo ngược Batch/Inventory
    đã tạo bởi ``qc_pass``/``qc_fail``/``qc_partial_pass``. Chỉ áp dụng cho
    inspection đã có kết quả; ``PENDING_QC`` nên sửa trực tiếp qua
    ``qc_result`` thay vì override.
    """
    inspection = get_object_or_404(QcInspection, pk=pk)
    if inspection.status == QcInspection.Result.PENDING_QC:
        messages.error(request, f'"{inspection.qc_no}" chưa có kết quả — không có gì để override.')
        return redirect('receiving:grn_detail', pk=inspection.grn_id)

    form = QcOverrideForm(request.POST or None, initial={'override_note': inspection.override_note})
    if request.method == 'POST' and form.is_valid():
        inspection.override_note = form.cleaned_data['override_note']
        inspection.overridden_by = request.user
        inspection.overridden_at = timezone.now()
        inspection.save(update_fields=['override_note', 'overridden_by', 'overridden_at'])
        log_action(
            request.user, AuditLog.Action.OVERRIDE, target=inspection.grn,
            description=f'Override QC {inspection.qc_no} (kết quả: {inspection.get_status_display()}).',
            reason=form.cleaned_data['override_note'], ip_address=client_ip(request),
        )
        messages.success(request, f'Đã ghi chú override cho "{inspection.qc_no}".')
        return redirect('receiving:grn_detail', pk=inspection.grn_id)
    return render(request, 'quality/qc_override.html', {'inspection': inspection, 'form': form})


@qc_permission_required('read')
def qc_criteria_list(request):
    """READ — danh sách tiêu chuẩn QC master data (FR-QC-02)."""
    criteria = QcCriteria.objects.all()
    category = request.GET.get('category', '')
    if category:
        criteria = criteria.filter(category=category)
    status = request.GET.get('status', '')
    if status == 'active':
        criteria = criteria.filter(is_active=True)
    elif status == 'inactive':
        criteria = criteria.filter(is_active=False)
    q = request.GET.get('q', '').strip()
    if q:
        criteria = criteria.filter(Q(name__icontains=q))
    categories = (
        QcCriteria.objects.exclude(category='').values_list('category', flat=True)
        .distinct().order_by('category')
    )
    page_obj, page_size = paginate_queryset(request, criteria)
    return render(request, 'quality/qc_criteria_list.html', {
        'criteria': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'can_manage': request.user.can('create', 'qc'),
        'categories': categories, 'selected_category': category,
        'selected_status': status, 'q': q,
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
