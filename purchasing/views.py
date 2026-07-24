"""View app purchasing: CRUD PO stub (mục 1e — bổ sung).

Phân quyền: KHÁC catalog/partners/warehouse — 'po' LÀ module có thật trong
Permission Matrix (BACKLOG mục 1a, xem ``accounts/permissions.py`` MODULES['po']
và ROLE_PERMISSIONS), nên dùng thẳng RBAC thật qua ``user.can(action, 'po')``
thay vì check role MANAGER/ADMIN thủ công. Theo ma trận: MANAGER/PURCHASING/ADMIN
có Create+Update; STAFF/QC/ACCOUNTANT chỉ Read.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.audit import client_ip, log_action
from accounts.models import AuditLog

from .forms import PurchaseOrderForm, PurchaseOrderItemFormSet
from .models import PurchaseOrder


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
    """READ — danh sách PO."""
    orders = PurchaseOrder.objects.select_related('supplier').all()
    return render(request, 'purchasing/po_list.html', {'orders': orders})


@po_permission_required('create')
def po_create(request):
    """CREATE — tạo PO kèm chi tiết đơn hàng (formset), tối thiểu 1 dòng item."""
    form = PurchaseOrderForm(request.POST or None)
    formset = PurchaseOrderItemFormSet(request.POST or None, instance=PurchaseOrder(), prefix='items')
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
        return redirect('purchasing:po_list')
    return render(request, 'purchasing/po_form.html', {'form': form, 'formset': formset, 'mode': 'create'})


@po_permission_required('update')
def po_update(request, pk):
    """UPDATE — sửa PO + chi tiết đơn hàng."""
    obj = get_object_or_404(PurchaseOrder, pk=pk)
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
        return redirect('purchasing:po_list')
    return render(
        request, 'purchasing/po_form.html',
        {'form': form, 'formset': formset, 'mode': 'update', 'obj': obj},
    )
