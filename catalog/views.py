"""View app catalog: CRUD Product/SKU (mục 1c — bổ sung, không có mã FR).

Phân quyền: giống warehouse (mục 1b) — không có cột "Catalog"/"Product" trong
Permission Matrix (BACKLOG mục 1a), nên quyền tạo/sửa gán cho role MANAGER hoặc
ADMIN; mọi user đã đăng nhập đều XEM được (GRN/PO/Inventory ở Phase sau cần
tham chiếu Product qua dropdown).
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

from .forms import ProductForm
from .models import Product

User = get_user_model()


def can_manage_catalog(user):
    """MANAGER hoặc ADMIN (role) hoặc Django superuser được tạo/sửa Product."""
    return user.is_superuser or user.role in (User.Role.MANAGER, User.Role.ADMIN)


def catalog_manager_required(view):
    """Decorator: chưa đăng nhập -> về login; đã đăng nhập nhưng không đủ quyền -> 403."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not can_manage_catalog(request.user):
            raise PermissionDenied('Chỉ Quản lý (Manager) hoặc Admin được quản lý Product/SKU.')
        return view(request, *args, **kwargs)

    return wrapper


@login_required
def product_list(request):
    """READ — danh sách Product (mọi user đã đăng nhập đều xem được, trừ khi admin thu
    hồi quyền "Truy cập menu" ``catalog`` riêng cho user đó qua trang Phân quyền chi tiết)."""
    if not request.user.can_view_menu('catalog'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Sản phẩm".')
    products = Product.objects.all()
    category = request.GET.get('category', '')
    if category:
        products = products.filter(category=category)
    q = request.GET.get('q', '').strip()
    if q:
        products = products.filter(Q(product_code__icontains=q) | Q(name__icontains=q))
    categories = (
        Product.objects.exclude(category='').values_list('category', flat=True)
        .distinct().order_by('category')
    )
    page_obj, page_size = paginate_queryset(request, products)
    return render(request, 'catalog/product_list.html', {
        'products': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'categories': categories, 'selected_category': category, 'q': q,
    })


@catalog_manager_required
def product_create(request):
    """CREATE — tạo Product mới."""
    form = ProductForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj,
            description=f'Tạo Sản phẩm {obj.product_code}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã tạo Sản phẩm "{obj.product_code}".')
        return redirect('catalog:product_list')
    return render(request, 'catalog/product_form.html', {'form': form, 'mode': 'create'})


@catalog_manager_required
def product_update(request, pk):
    """UPDATE — sửa Product (kể cả is_active, không tách khoá/mở riêng như Warehouse)."""
    obj = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=f'Cập nhật Sản phẩm {obj.product_code}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã cập nhật Sản phẩm "{obj.product_code}".')
        return redirect('catalog:product_list')
    return render(request, 'catalog/product_form.html', {'form': form, 'mode': 'update', 'obj': obj})
