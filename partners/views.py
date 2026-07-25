"""View app partners: CRUD Supplier (mục 1d — bổ sung, không có mã FR).

Phân quyền: giống catalog (mục 1c) — không có cột "Partners"/"Supplier" trong
Permission Matrix (BACKLOG mục 1a), nên quyền tạo/sửa gán cho role MANAGER hoặc
ADMIN; mọi user đã đăng nhập đều XEM được (GRN/PO ở Phase sau cần tham chiếu
Supplier qua dropdown).
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.audit import client_ip, log_action
from accounts.models import AuditLog
from accounts.pagination import paginate_queryset

from .forms import SupplierForm
from .models import Supplier

User = get_user_model()


def can_manage_partners(user):
    """MANAGER hoặc ADMIN (role) hoặc Django superuser được tạo/sửa Supplier."""
    return user.is_superuser or user.role in (User.Role.MANAGER, User.Role.ADMIN)


def partners_manager_required(view):
    """Decorator: chưa đăng nhập -> về login; đã đăng nhập nhưng không đủ quyền -> 403."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not can_manage_partners(request.user):
            raise PermissionDenied('Chỉ Quản lý (Manager) hoặc Admin được quản lý Supplier.')
        return view(request, *args, **kwargs)

    return wrapper


@login_required
def supplier_list(request):
    """READ — danh sách Supplier (mọi user đã đăng nhập đều xem được)."""
    suppliers = Supplier.objects.all()
    status = request.GET.get('status', '')
    if status:
        suppliers = suppliers.filter(status=status)
    q = request.GET.get('q', '').strip()
    if q:
        suppliers = suppliers.filter(Q(supplier_code__icontains=q) | Q(name__icontains=q))
    page_obj, page_size = paginate_queryset(request, suppliers)
    return render(request, 'partners/supplier_list.html', {
        'suppliers': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'statuses': Supplier.Status.choices, 'selected_status': status, 'q': q,
    })


@login_required
def supplier_detail(request, pk):
    """READ — chi tiết Supplier theo 5 nhóm field, kèm PO gần đây tham chiếu NCC này."""
    obj = get_object_or_404(Supplier, pk=pk)
    return render(request, 'partners/supplier_detail.html', {
        'obj': obj,
        'purchase_orders': obj.purchase_orders.order_by('-created_at')[:20],
        'can_manage': can_manage_partners(request.user),
    })


@partners_manager_required
def supplier_create(request):
    """CREATE — tạo Supplier mới."""
    form = SupplierForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj,
            description=f'Tạo Nhà cung cấp {obj.supplier_code}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã tạo Nhà cung cấp "{obj.supplier_code}".')
        return redirect('partners:supplier_list')
    return render(request, 'partners/supplier_form.html', {'form': form, 'mode': 'create'})


@partners_manager_required
def supplier_update(request, pk):
    """UPDATE — sửa Supplier (kể cả is_active, không tách khoá/mở riêng như Warehouse)."""
    obj = get_object_or_404(Supplier, pk=pk)
    form = SupplierForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=f'Cập nhật Nhà cung cấp {obj.supplier_code}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã cập nhật Nhà cung cấp "{obj.supplier_code}".')
        return redirect('partners:supplier_list')
    return render(request, 'partners/supplier_form.html', {'form': form, 'mode': 'update', 'obj': obj})
