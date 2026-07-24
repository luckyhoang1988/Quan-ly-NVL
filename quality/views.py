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

from accounts.audit import client_ip
from receiving.models import Grn

from .forms import QcItemResultFormSet, QcResultForm
from .models import QcInspection
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

    3 nút hành động (``name="action"``) ứng với 3 nhánh transaction mục 2c:
    ``pass``/``fail``/``partial`` -> ``quality.services.qc_pass/qc_fail/qc_partial_pass``.
    """
    grn = get_object_or_404(Grn, pk=grn_pk)
    if grn.status != Grn.Status.QC_IN_PROGRESS:
        messages.error(request, f'GRN "{grn.grn_no}" không ở trạng thái QC_IN_PROGRESS.')
        return redirect('receiving:grn_detail', pk=grn.pk)

    inspection = grn.qc_inspections.filter(status=QcInspection.Result.PENDING_QC).order_by('-created_at').first()
    if inspection is None:
        messages.error(request, f'Không tìm thấy QC inspection đang chờ kết quả cho "{grn.grn_no}".')
        return redirect('receiving:grn_detail', pk=grn.pk)

    result_form = QcResultForm(request.POST or None)
    formset = QcItemResultFormSet(request.POST or None, instance=grn, prefix='items')
    action = request.POST.get('action')

    if request.method == 'POST' and action in ('pass', 'fail', 'partial'):
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
    })
