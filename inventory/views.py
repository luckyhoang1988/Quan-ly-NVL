"""View app inventory (mục 3a): dashboard tồn kho real-time (FR-WM-03) kèm
cảnh báo Min/Max Level (FR-WM-04/05), danh sách/chi tiết lô hàng (FR-INV-01)
kèm cảnh báo lô sắp hết hạn (FR-INV-02), và điều chuyển tồn kho (FR-WM-06).
Phần dashboard/lô hàng vẫn read-only (dữ liệu do GRN/QC/GIN/Stocktake ghi qua
``inventory.services``). Điều chuyển là thao tác ghi duy nhất của app này —
không có cột 'inventory' riêng trong Permission Matrix CRUD nên không dùng
``user.can(...)``, nhưng vẫn phải qua ``can_view_menu('inventory')`` (giống
``inventory_list``) VÀ ``can_transfer_inventory()`` (giống ``can_decide_handoff``)
— nếu không thì bất kỳ user đã đăng nhập nào (kể cả QC/Kế toán/Mua hàng, hoặc
user đã bị thu hồi quyền menu Tồn kho) cũng POST thẳng được để điều chuyển
tồn kho thật (BUG-05, 2026-07-29).
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render

from accounts.audit import client_ip
from accounts.models import User
from accounts.pagination import paginate_queryset
from catalog.models import Product
from warehouse.models import Warehouse

from .forms import StockTransferForm
from .models import Batch, Inventory, StockTransfer, WarehouseHandoff
from .services import (
    accept_handoff, calculate_eoq, expiring_soon_batches, reject_handoff, stale_quarantine_batches,
    sync_expired_batches, transfer_stock,
)


@login_required
def inventory_list(request):
    """FR-WM-03: tồn real-time theo kho (qty on-hand/reserved/available).
    Hàng quarantine (QC fail/partial) không có cột riêng — xem trực tiếp dòng
    Inventory của kho SCRAP (đã tách kho theo warehouse_type, xem CLAUDE.md).
    FR-WM-04/05: đánh dấu dòng dưới Min Level / trên Max Level so với
    ``Product.min_level``/``max_level``, tính on-the-fly khi load trang
    (⏸️ auto-tạo PO khi dưới Min Level dời Phase 5 theo CLAUDE.md).
    """
    if not request.user.can_view_menu('inventory'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Tồn kho".')
    selected_warehouse = None
    warehouse_id = request.GET.get('warehouse')
    if warehouse_id:
        try:
            selected_warehouse = int(warehouse_id)
        except ValueError:
            selected_warehouse = None

    inventories = Inventory.objects.select_related('product', 'warehouse')
    if selected_warehouse:
        inventories = inventories.filter(warehouse_id=selected_warehouse)
    q = request.GET.get('q', '').strip()
    if q:
        inventories = inventories.filter(
            Q(product__product_code__icontains=q) | Q(product__name__icontains=q))

    # Gộp tổng qty theo SKU trên tất cả kho MAIN trước khi so Min/Max Level — 1
    # SKU trải nhiều kho MAIN phải so trên tổng công ty, không so riêng từng
    # dòng Inventory, nếu không cùng một SKU dưới Min ở từng kho dù tổng đã đủ
    # (hoặc ngược lại) sẽ báo sai (BUG-07, 2026-07-29). Cùng convention với
    # ``reports.services.dashboard_kpis()``. Truy vấn riêng, không phụ thuộc bộ
    # lọc kho/tìm kiếm của trang, để lọc theo 1 kho vẫn so đúng trên tổng SKU.
    main_totals = dict(
        Inventory.objects.filter(warehouse__warehouse_type=Warehouse.WarehouseType.MAIN)
        .values('product_id').annotate(total=Sum('qty_on_hand')).values_list('product_id', 'total')
    )

    rows = []
    below_min_products = set()
    above_max_products = set()
    for inv in inventories:
        # Cảnh báo Min/Max chỉ có ý nghĩa cho tồn khả dụng ở Kho thành phẩm —
        # tồn ở Kho chờ/Kho phế vẫn hiển thị trong danh sách nhưng không tính vào
        # cảnh báo/gợi ý đặt hàng (hàng đó chưa qua QC hoặc đã bị loại).
        is_main = inv.warehouse.warehouse_type == Warehouse.WarehouseType.MAIN
        min_level = inv.product.min_level
        max_level = inv.product.max_level
        total_main_qty = main_totals.get(inv.product_id, 0)
        below_min = is_main and min_level is not None and total_main_qty < min_level
        above_max = is_main and max_level is not None and total_main_qty > max_level
        if below_min:
            below_min_products.add(inv.product_id)
        if above_max:
            above_max_products.add(inv.product_id)
        suggested_po_qty = None
        if below_min:
            # FR-PO-02: gợi ý Qty đặt = đầy Max Level nếu có cấu hình, else về
            # lại Min Level, tính trên tổng tồn MAIN của cả SKU (không phải
            # riêng dòng kho này) để không gợi ý đặt dư khi SKU còn tồn ở kho khác.
            target_level = max_level if max_level is not None else min_level
            suggested_po_qty = target_level - total_main_qty
        rows.append({
            'inventory': inv, 'below_min': below_min, 'above_max': above_max,
            'suggested_po_qty': suggested_po_qty,
        })

    page_obj, page_size = paginate_queryset(request, rows)
    return render(request, 'inventory/inventory_list.html', {
        'rows': page_obj,
        'page_obj': page_obj,
        'page_size': page_size,
        'warehouses': Warehouse.objects.filter(is_active=True),
        'selected_warehouse': selected_warehouse,
        'below_min_count': len(below_min_products),
        'above_max_count': len(above_max_products),
        'q': q,
        'can_create_pr': request.user.can('create', 'pr'),
    })


@login_required
def batch_list(request):
    """FR-INV-01: quản lý lô hàng — danh sách batch (mã lô/NSX/HSD/NCC, qty
    received/used/available, status). FR-INV-02: cảnh báo lô ``ACTIVE`` sắp
    hết hạn trong 30 ngày, tính on-the-fly bằng ``expiring_soon_batches()``
    (⏸️ theo CLAUDE.md, không cron). Cảnh báo lô QUARANTINE tồn quá 7 ngày
    chưa xử lý bằng ``stale_quarantine_batches()`` (alert-only, xem BACKLOG
    mục 2c).
    """
    if not request.user.can_view_menu('inventory'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Tồn kho".')
    sync_expired_batches()

    selected_warehouse = None
    warehouse_id = request.GET.get('warehouse')
    if warehouse_id:
        try:
            selected_warehouse = int(warehouse_id)
        except ValueError:
            selected_warehouse = None

    selected_status = request.GET.get('status') or ''
    q = request.GET.get('q', '').strip()

    batches = Batch.objects.select_related('product', 'supplier', 'location__warehouse')
    if selected_warehouse:
        batches = batches.filter(location__warehouse_id=selected_warehouse)
    if selected_status:
        batches = batches.filter(status=selected_status)
    if q:
        batches = batches.filter(Q(batch_code__icontains=q) | Q(product__product_code__icontains=q))

    expiring_ids = set(expiring_soon_batches().values_list('pk', flat=True))
    stale_quarantine_ids = set(stale_quarantine_batches().values_list('pk', flat=True))

    page_obj, page_size = paginate_queryset(request, batches)
    return render(request, 'inventory/batch_list.html', {
        'batches': page_obj,
        'page_obj': page_obj,
        'page_size': page_size,
        'expiring_ids': expiring_ids,
        'expiring_count': len(expiring_ids),
        'stale_quarantine_ids': stale_quarantine_ids,
        'stale_quarantine_count': len(stale_quarantine_ids),
        'statuses': Batch.Status.choices,
        'warehouses': Warehouse.objects.filter(is_active=True),
        'selected_warehouse': selected_warehouse,
        'selected_status': selected_status,
        'q': q,
    })


@login_required
def batch_detail(request, pk):
    """FR-INV-01: chi tiết 1 lô hàng, kèm lịch sử chuyển động (StockMovement)
    liên quan để truy vết. FR-INV-02: cờ cảnh báo nếu lô nằm trong danh sách
    sắp hết hạn.
    """
    if not request.user.can_view_menu('inventory'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Tồn kho".')
    batch = get_object_or_404(
        Batch.objects.select_related('product', 'supplier', 'location__warehouse'), pk=pk,
    )
    is_expiring_soon = expiring_soon_batches().filter(pk=batch.pk).exists()
    is_stale_quarantine = stale_quarantine_batches().filter(pk=batch.pk).exists()
    return render(request, 'inventory/batch_detail.html', {
        'batch': batch,
        'movements': batch.movements.select_related('created_by').all(),
        'is_expiring_soon': is_expiring_soon,
        'is_stale_quarantine': is_stale_quarantine,
        'can_transfer_inventory': can_transfer_inventory(request.user),
    })


def can_transfer_inventory(user):
    """Ai được tạo ``StockTransfer`` (FR-WM-06) — thao tác ghi duy nhất của app
    này, tách biệt với quyền xem (``can_view_menu('inventory')``). Không có cột
    'inventory' trong Permission Matrix CRUD nên dùng ``role`` trực tiếp thay vì
    ``user.can(...)``: ADMIN/MANAGER/STAFF đều là role "thuộc Kho" theo đúng
    nhãn của chúng (``Role.MANAGER`` = "Quản lý kho", ``Role.STAFF`` = "Nhân
    viên kho") — cùng nhóm role vốn đã có quyền create/update ``grn``/``gin``/
    ``opname`` trong ``ROLE_PERMISSIONS``, nên điều chuyển tồn kho vật lý nằm
    trong đúng phạm vi công việc của họ. QC/PURCHASING/ACCOUNTANT (role riêng,
    không phải "Kho") bị chặn dù vẫn xem được dashboard/lô hàng/lịch sử điều
    chuyển qua ``can_view_menu('inventory')``.
    """
    return (
        user.is_superuser
        or user.role in (User.Role.ADMIN, User.Role.MANAGER, User.Role.STAFF)
    )


@login_required
def transfer_create(request):
    """FR-WM-06: điều chuyển batch sang vị trí khác (cùng kho hoặc khác kho).

    ``?batch=<pk>`` (vd link từ ``batch_detail``) prefill sẵn batch nguồn.
    Transaction thật nằm ở ``transfer_stock()`` — view chỉ thu input + hiển
    thị lỗi qua ``messages`` (cùng convention với ``shipping.views.gin_*``).
    """
    if not request.user.can_view_menu('inventory'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Tồn kho".')
    if not can_transfer_inventory(request.user):
        raise PermissionDenied('Bạn không có quyền điều chuyển tồn kho.')
    initial = {}
    batch_id = request.GET.get('batch')
    if batch_id:
        initial['batch'] = batch_id

    form = StockTransferForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            transfer = transfer_stock(
                batch=form.cleaned_data['batch'], to_location=form.cleaned_data['to_location'],
                qty=form.cleaned_data['qty'], note=form.cleaned_data['note'],
                actor=request.user, ip_address=client_ip(request),
            )
            messages.success(request, f'Đã tạo phiếu điều chuyển "{transfer.transfer_no}".')
            return redirect('inventory:transfer_list')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return render(request, 'inventory/transfer_form.html', {'form': form})


@login_required
def transfer_list(request):
    """FR-WM-06: lịch sử điều chuyển tồn kho (audit trail)."""
    if not request.user.can_view_menu('inventory'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Tồn kho".')
    transfers = StockTransfer.objects.select_related(
        'batch__product', 'new_batch', 'from_location__warehouse',
        'to_location__warehouse', 'created_by',
    )
    selected_warehouse = None
    warehouse_id = request.GET.get('warehouse')
    if warehouse_id:
        try:
            selected_warehouse = int(warehouse_id)
        except ValueError:
            selected_warehouse = None
    if selected_warehouse:
        transfers = transfers.filter(from_location__warehouse_id=selected_warehouse)
    q = request.GET.get('q', '').strip()
    if q:
        transfers = transfers.filter(
            Q(transfer_no__icontains=q) | Q(batch__product__product_code__icontains=q))

    page_obj, page_size = paginate_queryset(request, transfers)
    return render(request, 'inventory/transfer_list.html', {
        'transfers': page_obj,
        'page_obj': page_obj,
        'page_size': page_size,
        'warehouses': Warehouse.objects.filter(is_active=True),
        'selected_warehouse': selected_warehouse,
        'q': q,
        'can_transfer_inventory': can_transfer_inventory(request.user),
    })


@login_required
def product_eoq(request, pk):
    """FR-INV-05: tính EOQ cho 1 SKU — xem ``inventory.services.calculate_eoq``
    cho công thức/nguồn dữ liệu. Chỉ đọc, không ghi gì.
    """
    if not request.user.can_view_menu('inventory'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Tồn kho".')
    product = get_object_or_404(Product, pk=pk)
    return render(request, 'inventory/product_eoq.html', {
        'product': product,
        'result': calculate_eoq(product),
    })


def can_decide_handoff(user, handoff):
    """Ai được Nhận/Từ chối 1 ``WarehouseHandoff`` (Phase D, mục 6):

    Admin/superuser luôn được (oversight toàn hệ thống, mirror sidebar gate ở
    ``base.html`` và quy ước ``is_superuser or role == ADMIN`` dùng ở
    ``accounts.views``); quản lý phòng Kho luôn được (oversight); nếu handoff
    chỉ định ``assigned_to`` cụ thể thì chỉ đúng người đó; nếu để trống thì bất
    kỳ nhân viên nào thuộc ``destination_warehouse.staff``, fallback toàn bộ
    ``department=WAREHOUSE`` nếu kho đích chưa gán ai (mirror
    ``inventory.services._handoff_recipients``).
    """
    if user.is_superuser or user.role == User.Role.ADMIN:
        return True
    if user.is_department_manager(User.Department.WAREHOUSE):
        return True
    if handoff.assigned_to_id:
        return user.pk == handoff.assigned_to_id
    staff_ids = set(handoff.destination_warehouse.staff.filter(is_active=True).values_list('pk', flat=True))
    if staff_ids:
        return user.pk in staff_ids
    return user.department == User.Department.WAREHOUSE


@login_required
def handoff_list(request):
    """Phiếu chờ nhận hàng (Phase D, mục 6): danh sách ``WarehouseHandoff``
    PENDING mà ``request.user`` có quyền quyết định (xem ``can_decide_handoff``)
    — Admin/superuser và quản lý phòng Kho thấy toàn bộ, NV kho chỉ thấy phiếu
    liên quan tới mình.
    """
    if not request.user.can_view_menu('handoff'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Phiếu chờ nhận hàng".')
    pending = WarehouseHandoff.objects.select_related(
        'batch__product', 'destination_warehouse', 'assigned_to', 'qc_inspection__grn',
    ).filter(status=WarehouseHandoff.Status.PENDING)
    if request.user.is_superuser or request.user.role == User.Role.ADMIN \
            or request.user.is_department_manager(User.Department.WAREHOUSE):
        handoffs = list(pending)
    else:
        handoffs = [h for h in pending if can_decide_handoff(request.user, h)]

    page_obj, page_size = paginate_queryset(request, handoffs)
    return render(request, 'inventory/handoff_list.html', {
        'handoffs': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'reject_destinations': WarehouseHandoff.RejectDestination.choices,
    })


@login_required
def handoff_accept(request, pk):
    """NV kho "Nhận" 1 phiếu bàn giao — batch PENDING_RECEIPT -> ACTIVE."""
    handoff = get_object_or_404(WarehouseHandoff, pk=pk)
    if not can_decide_handoff(request.user, handoff):
        raise PermissionDenied('Không có quyền xác nhận nhận hàng cho phiếu này.')
    if request.method == 'POST':
        try:
            accept_handoff(handoff, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã nhận hàng cho lô "{handoff.batch.batch_code}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('inventory:handoff_list')


@login_required
def handoff_reject(request, pk):
    """NV kho "Từ chối" 1 phiếu bàn giao — bắt buộc lý do + chọn xử lý
    (chuyển kho phế / trả về QC).
    """
    handoff = get_object_or_404(WarehouseHandoff, pk=pk)
    if not can_decide_handoff(request.user, handoff):
        raise PermissionDenied('Không có quyền xử lý phiếu này.')
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        destination = request.POST.get('destination', '')
        try:
            reject_handoff(
                handoff, actor=request.user, reason=reason, destination=destination,
                ip_address=client_ip(request),
            )
            messages.success(request, f'Đã từ chối nhận lô "{handoff.batch.batch_code}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('inventory:handoff_list')
