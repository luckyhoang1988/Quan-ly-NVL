"""View app warehouse: CRUD kho (FR-WM-01) + vị trí lưu trữ (FR-WM-02).

Phân quyền quản lý kho: bảng Permission Matrix (BACKLOG mục 1a) không có cột
"Warehouse" — theo tên vai trò "Warehouse Manager", quyền tạo/sửa/khoá kho gán
cho role MANAGER hoặc ADMIN. Mọi user đã đăng nhập đều XEM được (list/detail)
vì GRN/GIN/PO ở các Phase sau đều cần tham chiếu tên kho.

Mọi thao tác ghi/đổi trạng thái đều ghi audit qua ``log_action`` (NFR-SEC-08),
tái dùng hạ tầng audit của app accounts.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.audit import client_ip, log_action
from accounts.models import AuditLog
from accounts.pagination import paginate_queryset

from .forms import LocationForm, WarehouseForm
from .models import MIN_LOCATIONS_PER_WAREHOUSE, Location, Warehouse
from .services import deactivate_warehouse

User = get_user_model()


def can_manage_warehouses(user):
    """MANAGER hoặc ADMIN (role) hoặc Django superuser được tạo/sửa/khoá kho."""
    return user.is_superuser or user.role in (User.Role.MANAGER, User.Role.ADMIN)


def warehouse_manager_required(view):
    """Decorator: chưa đăng nhập -> về login; đã đăng nhập nhưng không đủ quyền -> 403."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not can_manage_warehouses(request.user):
            raise PermissionDenied('Chỉ Quản lý kho (Manager) hoặc Admin được quản lý kho.')
        return view(request, *args, **kwargs)

    return wrapper


@login_required
def warehouse_list(request):
    """READ — danh sách kho (mọi user đã đăng nhập đều xem được)."""
    warehouses = Warehouse.objects.all()
    status = request.GET.get('status', '')
    if status == 'active':
        warehouses = warehouses.filter(is_active=True)
    elif status == 'inactive':
        warehouses = warehouses.filter(is_active=False)
    warehouse_type = request.GET.get('type', '')
    if warehouse_type in Warehouse.WarehouseType.values:
        warehouses = warehouses.filter(warehouse_type=warehouse_type)
    q = request.GET.get('q', '').strip()
    if q:
        warehouses = warehouses.filter(Q(code__icontains=q) | Q(name__icontains=q))
    page_obj, page_size = paginate_queryset(request, warehouses)
    return render(request, 'warehouse/warehouse_list.html', {
        'warehouses': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'selected_status': status, 'selected_type': warehouse_type, 'q': q,
        'warehouse_types': Warehouse.WarehouseType.choices,
    })


@login_required
def warehouse_detail(request, pk):
    """READ — chi tiết kho + danh sách vị trí lưu trữ."""
    obj = get_object_or_404(Warehouse, pk=pk)
    locations = obj.locations.all()
    return render(request, 'warehouse/warehouse_detail.html', {
        'obj': obj, 'locations': locations, 'location_form': LocationForm(),
    })


@warehouse_manager_required
def warehouse_create(request):
    """CREATE — tạo kho; tự sinh đủ ``MIN_LOCATIONS_PER_WAREHOUSE`` vị trí mặc định
    (BR-WM-005: "Tạo warehouse -> min 10 vị trí") rồi ghi audit CREATE.
    """
    form = WarehouseForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        Location.objects.bulk_create([
            Location(warehouse=obj, code=f'A-{i:02d}')
            for i in range(1, MIN_LOCATIONS_PER_WAREHOUSE + 1)
        ])
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj,
            description=f'Tạo kho {obj.code} (tự sinh {MIN_LOCATIONS_PER_WAREHOUSE} vị trí mặc định)',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã tạo kho "{obj.code}" với {MIN_LOCATIONS_PER_WAREHOUSE} vị trí mặc định.')
        return redirect('warehouse:warehouse_detail', pk=obj.pk)
    return render(request, 'warehouse/warehouse_form.html', {'form': form, 'mode': 'create'})


@warehouse_manager_required
def warehouse_update(request, pk):
    """UPDATE — sửa thông tin kho (không đụng ``is_active``, xem ``WarehouseForm``)."""
    obj = get_object_or_404(Warehouse, pk=pk)
    form = WarehouseForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=f'Cập nhật kho {obj.code}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã cập nhật kho "{obj.code}".')
        return redirect('warehouse:warehouse_detail', pk=obj.pk)
    return render(request, 'warehouse/warehouse_form.html',
                  {'form': form, 'mode': 'update', 'obj': obj})


@warehouse_manager_required
def warehouse_deactivate(request, pk):
    """DELETE (soft) — khoá hoạt động kho (BR-WM-006: chặn nếu còn qty_on_hand > 0)."""
    obj = get_object_or_404(Warehouse, pk=pk)
    if request.method == 'POST':
        try:
            deactivate_warehouse(obj, actor=request.user, ip_address=client_ip(request))
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return redirect('warehouse:warehouse_detail', pk=obj.pk)
        messages.success(request, f'Đã khoá hoạt động kho "{obj.code}".')
        return redirect('warehouse:warehouse_detail', pk=obj.pk)
    return render(request, 'warehouse/warehouse_confirm_deactivate.html', {'obj': obj})


@warehouse_manager_required
def warehouse_activate(request, pk):
    """Mở lại hoạt động 1 kho đã bị khoá (không cần trang xác nhận riêng)."""
    obj = get_object_or_404(Warehouse, pk=pk)
    if request.method == 'POST':
        obj.is_active = True
        obj.save(update_fields=['is_active'])
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=f'Mở lại hoạt động kho {obj.code}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã mở lại hoạt động kho "{obj.code}".')
    return redirect('warehouse:warehouse_detail', pk=obj.pk)


@warehouse_manager_required
def location_create(request, pk):
    """CREATE — thêm 1 vị trí lưu trữ vào kho ``pk``."""
    warehouse = get_object_or_404(Warehouse, pk=pk)
    # `warehouse` phải gán TRƯỚC form.is_valid(): validate_unique() của ModelForm
    # cần nó trên instance để kiểm tra đúng ràng buộc (warehouse, code) — gán sau
    # khi valid sẽ để lọt trùng mã và vỡ ở tầng DB (IntegrityError) thay vì lỗi form.
    form = LocationForm(request.POST or None, instance=Location(warehouse=warehouse))
    if request.method == 'POST' and form.is_valid():
        loc = form.save()
        log_action(
            request.user, AuditLog.Action.CREATE, target=loc,
            description=f'Thêm vị trí {loc.code} vào kho {warehouse.code}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã thêm vị trí "{loc.code}".')
    else:
        for error in form.errors.values():
            messages.error(request, error.as_text())
    return redirect('warehouse:warehouse_detail', pk=warehouse.pk)


@warehouse_manager_required
def location_update(request, pk, loc_pk):
    """UPDATE — sửa mã/dung tích 1 vị trí lưu trữ."""
    warehouse = get_object_or_404(Warehouse, pk=pk)
    loc = get_object_or_404(Location, pk=loc_pk, warehouse=warehouse)
    form = LocationForm(request.POST or None, instance=loc)
    if request.method == 'POST' and form.is_valid():
        loc = form.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, target=loc,
            description=f'Cập nhật vị trí {loc.code} (kho {warehouse.code})',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã cập nhật vị trí "{loc.code}".')
        return redirect('warehouse:warehouse_detail', pk=warehouse.pk)
    return render(request, 'warehouse/location_form.html',
                  {'form': form, 'warehouse': warehouse, 'obj': loc})


@warehouse_manager_required
def location_toggle_active(request, pk, loc_pk):
    """Bật/tắt hoạt động 1 vị trí lưu trữ (POST-only, không cần trang xác nhận)."""
    warehouse = get_object_or_404(Warehouse, pk=pk)
    loc = get_object_or_404(Location, pk=loc_pk, warehouse=warehouse)
    if request.method == 'POST':
        loc.is_active = not loc.is_active
        loc.save(update_fields=['is_active'])
        log_action(
            request.user, AuditLog.Action.UPDATE, target=loc,
            description=f'{"Mở lại" if loc.is_active else "Khoá"} vị trí {loc.code} (kho {warehouse.code})',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã {"mở lại" if loc.is_active else "khoá"} vị trí "{loc.code}".')
    return redirect('warehouse:warehouse_detail', pk=warehouse.pk)
