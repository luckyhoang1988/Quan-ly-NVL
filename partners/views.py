"""View app partners: CRUD Supplier (mục 1d — bổ sung, không có mã FR).

Phân quyền: giống catalog (mục 1c) — không có cột "Partners"/"Supplier" trong
Permission Matrix (BACKLOG mục 1a), nên quyền tạo/sửa gán thẳng theo role thay
vì RBAC module thật; mọi user đã đăng nhập đều XEM được (GRN/PO ở Phase sau cần
tham chiếu Supplier qua dropdown).

Bổ sung theo yêu cầu người dùng (2026-07-26): nhân viên mua hàng (role
PURCHASING) cũng được TẠO Supplier — NCC tạo ra tự gán ``managed_by`` = người
tạo, và role PURCHASING chỉ SỬA được đúng NCC do chính mình tạo (không sửa
NCC do đồng nghiệp mua hàng khác hoặc Manager tạo trước đó); MANAGER/ADMIN
không đổi — vẫn toàn quyền sửa mọi Supplier như cũ.
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
    """MANAGER hoặc ADMIN (role) hoặc Django superuser — toàn quyền mọi Supplier."""
    return user.is_superuser or user.role in (User.Role.MANAGER, User.Role.ADMIN)


def can_create_supplier(user):
    """Ai được TẠO Supplier: ``can_manage_partners`` (toàn quyền) hoặc role PURCHASING
    (NCC tạo ra sẽ tự gán ``managed_by`` = người tạo, xem ``supplier_create``)."""
    return can_manage_partners(user) or user.role == User.Role.PURCHASING


def can_edit_supplier(user, supplier):
    """Ai được SỬA 1 Supplier cụ thể: ``can_manage_partners`` sửa được mọi NCC;
    role PURCHASING chỉ sửa được đúng NCC do chính mình tạo (``managed_by``)."""
    return can_manage_partners(user) or (user.role == User.Role.PURCHASING and supplier.managed_by_id == user.id)


def partners_create_required(view):
    """Decorator: chưa đăng nhập -> về login; không có quyền menu "Nhà cung cấp"
    hoặc không đủ quyền TẠO Supplier -> 403. Menu-access phải kiểm tra trước
    role, vì thu hồi quyền menu (Phân quyền chi tiết) phải chặn cả thao tác ghi,
    không chỉ ẩn sidebar (BUG-06, 2026-07-29)."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.can_view_menu('partners'):
            raise PermissionDenied('Bạn không có quyền truy cập mục "Nhà cung cấp".')
        if not can_create_supplier(request.user):
            raise PermissionDenied('Không có quyền tạo Nhà cung cấp.')
        return view(request, *args, **kwargs)

    return wrapper


@login_required
def supplier_list(request):
    """READ — danh sách Supplier (mọi user đã đăng nhập đều xem được, trừ khi admin thu
    hồi quyền "Truy cập menu" ``partners`` riêng cho user đó qua trang Phân quyền chi tiết)."""
    if not request.user.can_view_menu('partners'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Nhà cung cấp".')
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
    if not request.user.can_view_menu('partners'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Nhà cung cấp".')
    obj = get_object_or_404(Supplier, pk=pk)
    return render(request, 'partners/supplier_detail.html', {
        'obj': obj,
        'purchase_orders': obj.purchase_orders.order_by('-created_at')[:20],
        'can_manage': can_edit_supplier(request.user, obj),
    })


@partners_create_required
def supplier_create(request):
    """CREATE — tạo Supplier mới. ``managed_by`` luôn là người tạo (kể cả
    Manager/Admin) — role PURCHASING dùng field này để giới hạn sửa sau này
    (xem ``can_edit_supplier``); Manager/Admin không bị giới hạn bởi nó.
    """
    form = SupplierForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.managed_by = request.user
        obj.save()
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj,
            description=f'Tạo Nhà cung cấp {obj.supplier_code}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã tạo Nhà cung cấp "{obj.supplier_code}".')
        return redirect('partners:supplier_list')
    return render(request, 'partners/supplier_form.html', {'form': form, 'mode': 'create'})


@login_required
def supplier_update(request, pk):
    """UPDATE — sửa Supplier (kể cả is_active, không tách khoá/mở riêng như Warehouse).
    Quyền theo ``can_edit_supplier`` (Manager/Admin sửa mọi NCC; PURCHASING chỉ
    sửa NCC do chính mình tạo).
    """
    if not request.user.can_view_menu('partners'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Nhà cung cấp".')
    obj = get_object_or_404(Supplier, pk=pk)
    if not can_edit_supplier(request.user, obj):
        raise PermissionDenied('Bạn không có quyền sửa Nhà cung cấp này.')
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
