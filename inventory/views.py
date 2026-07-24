"""View app inventory (mục 3a): dashboard tồn kho real-time (FR-WM-03) kèm
cảnh báo Min/Max Level (FR-WM-04/05), danh sách/chi tiết lô hàng (FR-INV-01)
kèm cảnh báo lô sắp hết hạn (FR-INV-02), và điều chuyển tồn kho (FR-WM-06).
Phần dashboard/lô hàng vẫn read-only (dữ liệu do GRN/QC/GIN/Stocktake ghi qua
``inventory.services``); điều chuyển là thao tác ghi duy nhất của app này
nhưng cũng không cần permission theo module (giống ``warehouse``/``catalog``:
mọi user đã đăng nhập đều dùng được — BACKLOG không có cột 'inventory' riêng
trong Permission Matrix).
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.audit import client_ip
from accounts.pagination import paginate_queryset
from catalog.models import Product
from warehouse.models import Warehouse

from .forms import StockTransferForm
from .models import Batch, Inventory, StockTransfer
from .services import calculate_eoq, expiring_soon_batches, sync_expired_batches, transfer_stock


@login_required
def inventory_list(request):
    """FR-WM-03: tồn real-time theo kho (qty on-hand/reserved/quarantine/
    available). FR-WM-04/05: đánh dấu dòng dưới Min Level / trên Max Level so
    với ``Product.min_level``/``max_level``, tính on-the-fly khi load trang
    (⏸️ auto-tạo PO khi dưới Min Level dời Phase 5 theo CLAUDE.md).
    """
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

    rows = []
    below_min_count = 0
    above_max_count = 0
    for inv in inventories:
        min_level = inv.product.min_level
        max_level = inv.product.max_level
        below_min = min_level is not None and inv.qty_on_hand < min_level
        above_max = max_level is not None and inv.qty_on_hand > max_level
        below_min_count += int(below_min)
        above_max_count += int(above_max)
        suggested_po_qty = None
        if below_min:
            # FR-PO-02: gợi ý Qty đặt = đầy Max Level nếu có cấu hình, else về
            # lại Min Level — chỉ tính khi thực sự dưới Min (below_min).
            target_level = max_level if max_level is not None else min_level
            suggested_po_qty = target_level - inv.qty_on_hand
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
        'below_min_count': below_min_count,
        'above_max_count': above_max_count,
        'q': q,
    })


@login_required
def batch_list(request):
    """FR-INV-01: quản lý lô hàng — danh sách batch (mã lô/NSX/HSD/NCC, qty
    received/used/available, status). FR-INV-02: cảnh báo lô ``ACTIVE`` sắp
    hết hạn trong 30 ngày, tính on-the-fly bằng ``expiring_soon_batches()``
    (⏸️ theo CLAUDE.md, không cron).
    """
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

    page_obj, page_size = paginate_queryset(request, batches)
    return render(request, 'inventory/batch_list.html', {
        'batches': page_obj,
        'page_obj': page_obj,
        'page_size': page_size,
        'expiring_ids': expiring_ids,
        'expiring_count': len(expiring_ids),
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
    batch = get_object_or_404(
        Batch.objects.select_related('product', 'supplier', 'location__warehouse'), pk=pk,
    )
    is_expiring_soon = expiring_soon_batches().filter(pk=batch.pk).exists()
    return render(request, 'inventory/batch_detail.html', {
        'batch': batch,
        'movements': batch.movements.select_related('created_by').all(),
        'is_expiring_soon': is_expiring_soon,
    })


@login_required
def transfer_create(request):
    """FR-WM-06: điều chuyển batch sang vị trí khác (cùng kho hoặc khác kho).

    ``?batch=<pk>`` (vd link từ ``batch_detail``) prefill sẵn batch nguồn.
    Transaction thật nằm ở ``transfer_stock()`` — view chỉ thu input + hiển
    thị lỗi qua ``messages`` (cùng convention với ``shipping.views.gin_*``).
    """
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
    })


@login_required
def product_eoq(request, pk):
    """FR-INV-05: tính EOQ cho 1 SKU — xem ``inventory.services.calculate_eoq``
    cho công thức/nguồn dữ liệu. Chỉ đọc, không ghi gì.
    """
    product = get_object_or_404(Product, pk=pk)
    return render(request, 'inventory/product_eoq.html', {
        'product': product,
        'result': calculate_eoq(product),
    })
