"""Nghiệp vụ Reporting & Analytics (Phase 6, FR-RPT-01..05).

Toàn bộ tính on-the-fly khi load trang (đúng convention ⏸️ của CLAUDE.md), KHÔNG
lưu bảng báo cáo riêng — mọi hàm ở đây chỉ ĐỌC dữ liệu đã có từ các app khác
(catalog/inventory/receiving/quality/purchasing/partners).
"""
from decimal import Decimal

from django.db.models import Avg, Count, Max, Sum
from django.utils import timezone

from catalog.models import Product
from inventory.models import Inventory, StockMovement
from inventory.services import expiring_soon_batches
from purchasing.models import PurchaseOrder, PurchaseOrderItem
from purchasing.services import supplier_lead_time_stats
from quality.models import QcInspection
from receiving.models import Grn
from warehouse.models import Warehouse


def latest_unit_price_by_product():
    """Đơn giá gần nhất theo từng product, lấy từ ``PurchaseOrderItem`` của PO mới
    nhất (mirror pattern ``last_price_by_supplier`` ở ``purchasing.services.supplier_price_history``).
    """
    rows = (
        PurchaseOrderItem.objects.order_by('product_id', '-purchase_order__created_at')
        .distinct('product_id')
        .values_list('product_id', 'unit_price')
    )
    return dict(rows)


def dashboard_kpis():
    """FR-RPT-01: các chỉ số KPI tổng hợp cho Dashboard."""
    prices = latest_unit_price_by_product()

    total_inventory_value = Decimal('0')
    qty_by_product = {}
    for inv in Inventory.objects.select_related('product').filter(
        warehouse__warehouse_type=Warehouse.WarehouseType.MAIN,
    ):
        price = prices.get(inv.product_id)
        if price is not None:
            total_inventory_value += inv.qty_on_hand * price
        qty_by_product[inv.product_id] = qty_by_product.get(inv.product_id, 0) + inv.qty_on_hand

    # Gộp qty theo SKU trên tất cả kho MAIN trước khi so min_level — 1 SKU nằm ở
    # nhiều kho chỉ được đếm low-stock 1 lần, dựa trên tổng tồn thực tế của SKU đó
    # (bug fix 2026-07-27, xem CLAUDE.md), thay vì so min_level với từng dòng
    # Inventory riêng lẻ (double-count / thiếu chính xác khi 1 SKU trải nhiều kho).
    # Duyệt trực tiếp Product (không phải Inventory) để SKU active có cấu hình
    # min_level nhưng CHƯA TỪNG có dòng Inventory ở BẤT KỲ kho nào (chưa từng
    # GRN nhập kho) vẫn bị tính dưới Min — tồn thực tế = 0 (bug fix 2026-07-30,
    # xem CLAUDE.md); trước fix chỉ duyệt ``Inventory.objects`` (lọc MAIN) nên
    # SKU này vô hình khỏi cảnh báo. SKU đã có tồn ở Kho chờ/Kho phế nhưng chưa
    # có ở MAIN (hàng đã về, đang chờ QC) KHÔNG rơi vào nhánh "chưa từng nhập
    # kho" này — giữ nguyên hành vi loại khỏi KPI đã có từ trước (M6,
    # TC-RPT-01-007), vì hàng đó không phải "thiếu hàng cần mua".
    product_ids_ever_received = set(Inventory.objects.values_list('product_id', flat=True))
    low_stock_count = 0
    for product in Product.objects.filter(is_active=True, min_level__isnull=False):
        if product.id in qty_by_product:
            total_qty = qty_by_product[product.id]
        elif product.id in product_ids_ever_received:
            continue
        else:
            total_qty = 0
        if total_qty < product.min_level:
            low_stock_count += 1

    return {
        'total_inventory_value': total_inventory_value,
        'sku_count': Product.objects.filter(is_active=True).count(),
        'low_stock_count': low_stock_count,
        'near_expiry_count': len(expiring_soon_batches(
            days=30, warehouse_type=Warehouse.WarehouseType.MAIN)),
        'pending_po_count': PurchaseOrder.objects.filter(
            status__in=[PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIAL_RECEIVED],
        ).count(),
        # Cả 3 trạng thái đều là "GRN đang chờ xử lý ở kho/QC", chưa RECEIVED —
        # PENDING_QC là trạng thái duy nhất được tính trước fix (bug fix 2026-07-27,
        # xem CLAUDE.md), khiến GRN đang PENDING_APPROVAL hoặc QC_IN_PROGRESS bị
        # thiếu khỏi KPI "GRN chờ".
        'pending_grn_count': Grn.objects.filter(status__in=[
            Grn.Status.PENDING_APPROVAL, Grn.Status.PENDING_QC, Grn.Status.QC_IN_PROGRESS,
        ]).count(),
    }


def abc_analysis():
    """FR-RPT-02: phân loại A/B/C theo % giá trị tồn kho tích luỹ (Pareto 80-20).

    A: tích luỹ <= 80%, B: tích luỹ <= 95%, C: phần còn lại. SKU chưa từng có
    ``PurchaseOrderItem`` (không xác định được đơn giá) không đủ dữ liệu để xếp
    hạng — trả riêng trong ``unpriced``, không gán class.
    """
    prices = latest_unit_price_by_product()
    qty_by_product = dict(
        Inventory.objects.filter(warehouse__warehouse_type=Warehouse.WarehouseType.MAIN)
        .values('product_id').annotate(total_qty=Sum('qty_on_hand'))
        .values_list('product_id', 'total_qty')
    )
    products = {p.pk: p for p in Product.objects.filter(pk__in=qty_by_product.keys())}

    priced, unpriced = [], []
    for product_id, total_qty in qty_by_product.items():
        if not total_qty:
            continue
        product = products[product_id]
        unit_price = prices.get(product_id)
        if unit_price is None:
            unpriced.append({'product': product, 'total_qty': total_qty})
            continue
        priced.append({
            'product': product, 'total_qty': total_qty, 'unit_price': unit_price,
            'value': total_qty * unit_price,
        })

    priced.sort(key=lambda row: row['value'], reverse=True)
    grand_total = sum(row['value'] for row in priced)

    recommendations = {
        'A': 'Đếm hàng tuần, kiểm soát chặt Min/Max',
        'B': 'Đếm hàng tháng, kiểm soát vừa phải',
        'C': 'Đếm hàng quý, chấp nhận sai số cao hơn',
    }
    cumulative = Decimal('0')
    for row in priced:
        cumulative += row['value']
        cumulative_pct = (cumulative / grand_total * 100) if grand_total else Decimal('0')
        if cumulative_pct <= 80:
            row_class = 'A'
        elif cumulative_pct <= 95:
            row_class = 'B'
        else:
            row_class = 'C'
        row['cumulative_pct'] = cumulative_pct
        row['class'] = row_class
        row['recommendation'] = recommendations[row_class]

    return {'rows': priced, 'unpriced': unpriced}


def slow_moving_items(days=180):
    """FR-RPT-03: SKU còn tồn (qty_on_hand > 0, tổng mọi kho) nhưng không có giao
    dịch xuất (``StockMovement`` loại ISSUE) trong ``days`` ngày qua. SKU chưa
    từng xuất dùng ``product.created_at`` làm mốc thay thế, tránh gắn nhãn "tồn
    lỏng" cho SKU mới tạo chưa kịp phát sinh giao dịch.
    """
    today = timezone.localdate()
    prices = latest_unit_price_by_product()
    qty_by_product = dict(
        Inventory.objects.filter(warehouse__warehouse_type=Warehouse.WarehouseType.MAIN)
        .values('product_id').annotate(total_qty=Sum('qty_on_hand'))
        .values_list('product_id', 'total_qty')
    )
    last_issue_by_product = dict(
        StockMovement.objects.filter(movement_type=StockMovement.MovementType.ISSUE)
        .values('product_id').annotate(last=Max('created_at'))
        .values_list('product_id', 'last')
    )
    products = {p.pk: p for p in Product.objects.filter(pk__in=qty_by_product.keys())}

    results = []
    for product_id, total_qty in qty_by_product.items():
        if not total_qty:
            continue
        product = products[product_id]
        last_issue = last_issue_by_product.get(product_id)
        # timezone.localtime(...).date() chứ không phải .date() trực tiếp trên
        # datetime aware UTC — .date() cắt theo giờ UTC, sai lệch 1 ngày trong
        # khung 00:00-06:59 giờ VN (UTC+7) (xem CLAUDE.md "timezone.now().date()
        # là ngày UTC").
        reference_date = (
            timezone.localtime(last_issue).date() if last_issue
            else timezone.localtime(product.created_at).date()
        )
        days_idle = (today - reference_date).days
        if days_idle <= days:
            continue
        unit_price = prices.get(product_id)
        results.append({
            'product': product,
            'total_qty': total_qty,
            'value': total_qty * unit_price if unit_price is not None else None,
            'days_idle': days_idle,
            'last_issue_date': timezone.localtime(last_issue).date() if last_issue else None,
            'recommendation': 'Thanh lý (Scrap)' if days_idle > 365 else 'Giảm giá / Markdown',
        })

    results.sort(key=lambda row: row['days_idle'], reverse=True)
    return results


def supplier_performance():
    """FR-RPT-04: mở rộng ``purchasing.services.supplier_lead_time_stats()`` (đã
    có on-time/delayed count + avg lead time) với % đúng hạn, % QC pass, và đơn
    giá bình quân mọi SKU — không sửa hàm gốc ở ``purchasing`` (dùng chung cho PO
    detail page), chỉ bổ sung field ở lớp reports.
    """
    base_rows = supplier_lead_time_stats()
    supplier_ids = [row['supplier'].pk for row in base_rows]

    qc_counts = {}
    for row in (
        QcInspection.objects.filter(grn__supplier_id__in=supplier_ids)
        .exclude(status=QcInspection.Result.PENDING_QC)
        .values('grn__supplier_id', 'status').annotate(count=Count('id'))
    ):
        supplier_id = row['grn__supplier_id']
        qc_counts.setdefault(supplier_id, {}).__setitem__(row['status'], row['count'])

    avg_price_by_supplier = dict(
        PurchaseOrderItem.objects.filter(purchase_order__supplier_id__in=supplier_ids)
        .values('purchase_order__supplier_id').annotate(avg=Avg('unit_price'))
        .values_list('purchase_order__supplier_id', 'avg')
    )

    results = []
    for row in base_rows:
        supplier_id = row['supplier'].pk
        on_time = row['on_time_count']
        delayed = row['delayed_count']
        on_time_pct = (on_time / (on_time + delayed) * 100) if (on_time + delayed) else None

        counts = qc_counts.get(supplier_id, {})
        qc_pass = counts.get(QcInspection.Result.PASS, 0)
        qc_fail = counts.get(QcInspection.Result.FAIL, 0)
        qc_partial = counts.get(QcInspection.Result.PARTIAL_PASS, 0)
        qc_total = qc_pass + qc_fail + qc_partial
        qc_pass_pct = (qc_pass / qc_total * 100) if qc_total else None

        results.append({
            **row,
            'on_time_pct': on_time_pct,
            'qc_pass_pct': qc_pass_pct,
            'avg_price': avg_price_by_supplier.get(supplier_id),
        })
    return results
