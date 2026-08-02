from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product
from inventory.models import Batch, Inventory, StockMovement
from partners.models import Supplier
from purchasing.models import PurchaseOrder, PurchaseOrderItem
from quality.models import QcInspection
from receiving.models import Grn, GrnItem
from warehouse.models import Location, Warehouse

from .services import abc_analysis, dashboard_kpis, slow_moving_items, supplier_performance

User = get_user_model()


class DashboardKpisTest(TestCase):
    """FR-RPT-01: Dashboard KPI tính đúng từ dữ liệu Inventory/Batch/PO/GRN có sẵn.
    ``TC-RPT-01-<seq>``.
    """

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty ABC')
        self.creator = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)

        self.product = Product.objects.create(
            product_code='NVL-0001', name='Bột mì', uom='kg', min_level=20)
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=10)

        po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
        PurchaseOrderItem.objects.create(
            purchase_order=po, product=self.product, qty_ordered=10, unit_price=Decimal('15000.00'))

        Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier,
            location=self.location, qty_received=10,
            exp_date=timezone.localdate() + timedelta(days=10),
        )

        Grn.objects.create(
            po=po, supplier=self.supplier, created_by=self.creator, status=Grn.Status.PENDING_QC)

    def test_TC_RPT_01_001_total_inventory_value_uses_latest_po_price(self):
        kpis = dashboard_kpis()
        self.assertEqual(kpis['total_inventory_value'], Decimal('150000.00'))

    def test_TC_RPT_01_002_sku_count(self):
        self.assertEqual(dashboard_kpis()['sku_count'], 1)

    def test_TC_RPT_01_003_low_stock_count(self):
        # qty_on_hand=10 < min_level=20 -> below Min.
        self.assertEqual(dashboard_kpis()['low_stock_count'], 1)

    def test_TC_RPT_01_004_near_expiry_count(self):
        self.assertEqual(dashboard_kpis()['near_expiry_count'], 1)

    def test_TC_RPT_01_005_pending_po_count(self):
        self.assertEqual(dashboard_kpis()['pending_po_count'], 1)

    def test_TC_RPT_01_006_pending_grn_count(self):
        self.assertEqual(dashboard_kpis()['pending_grn_count'], 1)

    def test_TC_RPT_01_007_excludes_staging_and_scrap_inventory(self):
        """M6: tồn ở Kho chờ/Kho phế không được cộng vào KPI — hàng chưa qua QC
        hoặc đã bị loại không phải "tồn khả dụng"."""
        staging = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        scrap = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        other_product = Product.objects.create(
            product_code='NVL-0002', name='Đường', uom='kg', min_level=20)
        Inventory.objects.create(product=other_product, warehouse=staging, qty_on_hand=5)
        Inventory.objects.create(product=self.product, warehouse=scrap, qty_on_hand=999)

        kpis = dashboard_kpis()
        # other_product chỉ có tồn ở Kho chờ (5 < min_level=20) -> không tính vào low_stock_count.
        self.assertEqual(kpis['low_stock_count'], 1)
        # self.product tồn thêm 999 ở Kho phế -> nếu không lọc, total_inventory_value sẽ tăng vọt.
        self.assertEqual(kpis['total_inventory_value'], Decimal('150000.00'))

    def test_TC_RPT_01_008_near_expiry_excludes_staging_and_scrap_batches(self):
        """M6: lô sắp hết hạn ở Kho chờ/Kho phế không được cộng vào near_expiry_count
        — Kho chờ giữ batch ``ACTIVE`` trong lúc chờ QC (Phase D) nên vẫn khớp filter
        status của ``expiring_soon_batches`` nếu không lọc thêm theo warehouse_type."""
        staging = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        scrap = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        staging_location = Location.objects.create(warehouse=staging, code='B-01')
        scrap_location = Location.objects.create(warehouse=scrap, code='C-01')
        Batch.objects.create(
            product=self.product, batch_code='LOT-STG', supplier=self.supplier,
            location=staging_location, qty_received=5,
            exp_date=timezone.localdate() + timedelta(days=5),
        )
        Batch.objects.create(
            product=self.product, batch_code='LOT-SCR', supplier=self.supplier,
            location=scrap_location, qty_received=5, status=Batch.Status.QUARANTINE,
            exp_date=timezone.localdate() + timedelta(days=5),
        )

        # setUp đã tạo 1 lô sắp hết hạn ở kho MAIN -> vẫn phải đúng 1, không phải 3.
        self.assertEqual(dashboard_kpis()['near_expiry_count'], 1)

    def test_TC_RPT_01_009_low_stock_not_double_counted_across_warehouses(self):
        """Bug fix 2026-07-27: 1 SKU nằm ở nhiều kho MAIN, mỗi kho riêng lẻ đều dưới
        min_level, chỉ được đếm 1 lần trong low_stock_count (không nhân theo số kho)."""
        other_warehouse = Warehouse.objects.create(code='KHO-HCM', name='Kho HCM')
        multi_product = Product.objects.create(
            product_code='NVL-0003', name='Muối', uom='kg', min_level=50)
        Inventory.objects.create(product=multi_product, warehouse=self.warehouse, qty_on_hand=10)
        Inventory.objects.create(product=multi_product, warehouse=other_warehouse, qty_on_hand=15)

        # self.product (setUp, qty=10 < min=20) + multi_product (10+15=25 < min=50) -> 2, không phải 3.
        self.assertEqual(dashboard_kpis()['low_stock_count'], 2)

    def test_TC_RPT_01_010_low_stock_uses_combined_qty_not_per_warehouse(self):
        """Bug fix 2026-07-27: SKU dưới min_level ở từng kho riêng lẻ nhưng tổng các
        kho MAIN đã đủ Min thì không được tính vào low_stock_count."""
        other_warehouse = Warehouse.objects.create(code='KHO-DN', name='Kho Đà Nẵng')
        combined_product = Product.objects.create(
            product_code='NVL-0004', name='Đường tinh', uom='kg', min_level=20)
        Inventory.objects.create(product=combined_product, warehouse=self.warehouse, qty_on_hand=12)
        Inventory.objects.create(product=combined_product, warehouse=other_warehouse, qty_on_hand=10)

        # combined_product: 12 và 10 đều < 20 riêng lẻ, nhưng tổng 22 >= 20 -> không tính.
        # Chỉ còn self.product (setUp, 10 < 20) -> vẫn là 1.
        self.assertEqual(dashboard_kpis()['low_stock_count'], 1)

    def test_TC_RPT_01_011_pending_grn_count_includes_pending_approval_and_qc_in_progress(self):
        """Bug fix 2026-07-27: pending_grn_count trước đây chỉ đếm PENDING_QC, thiếu
        PENDING_APPROVAL và QC_IN_PROGRESS -> KPI "GRN chờ" bị đếm thiếu."""
        po = PurchaseOrder.objects.create(
            po_no='PO-0002', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
        Grn.objects.create(
            po=po, supplier=self.supplier, created_by=self.creator,
            status=Grn.Status.PENDING_APPROVAL)
        Grn.objects.create(
            po=po, supplier=self.supplier, created_by=self.creator,
            status=Grn.Status.QC_IN_PROGRESS)

        # setUp đã có 1 GRN PENDING_QC -> tổng 3 (PENDING_APPROVAL + PENDING_QC + QC_IN_PROGRESS).
        self.assertEqual(dashboard_kpis()['pending_grn_count'], 3)

    def test_TC_RPT_01_012_pending_grn_count_excludes_received_and_cancelled(self):
        """GRN đã RECEIVED/CANCELLED/REJECTED/CLOSED không còn "chờ xử lý" -> không
        được tính vào pending_grn_count."""
        po = PurchaseOrder.objects.create(
            po_no='PO-0003', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
        Grn.objects.create(
            po=po, supplier=self.supplier, created_by=self.creator, status=Grn.Status.RECEIVED)
        Grn.objects.create(
            po=po, supplier=self.supplier, created_by=self.creator, status=Grn.Status.CANCELLED)

        # setUp đã có 1 GRN PENDING_QC -> vẫn là 1, RECEIVED/CANCELLED không được tính thêm.
        self.assertEqual(dashboard_kpis()['pending_grn_count'], 1)


class AbcAnalysisTest(TestCase):
    """FR-RPT-02: phân loại A/B/C theo % giá trị tồn kho tích luỹ.
    ``TC-RPT-02-<seq>``.
    """

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty ABC')

        # value: A=1000 (10x100), B=200 (10x20), C=100 (10x10). Total=1300.
        # Cumulative: A=76.9% (<=80 -> A), A+B=92.3% (<=95 -> B), A+B+C=100% (-> C).
        self.product_a = self._make_product('NVL-A', qty=10, price='100.00')
        self.product_b = self._make_product('NVL-B', qty=10, price='20.00')
        self.product_c = self._make_product('NVL-C', qty=10, price='10.00')
        self.product_unpriced = Product.objects.create(product_code='NVL-D', name='Chưa có giá', uom='kg')
        Inventory.objects.create(
            product=self.product_unpriced, warehouse=self.warehouse, qty_on_hand=5)

    def _make_product(self, code, qty, price):
        product = Product.objects.create(product_code=code, name=code, uom='kg')
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=qty)
        po = PurchaseOrder.objects.create(po_no=f'PO-{code}', supplier=self.supplier)
        PurchaseOrderItem.objects.create(
            purchase_order=po, product=product, qty_ordered=qty, unit_price=Decimal(price))
        return product

    def test_TC_RPT_02_001_classifies_a_b_c_by_cumulative_value(self):
        result = abc_analysis()
        classes = {row['product'].product_code: row['class'] for row in result['rows']}
        self.assertEqual(classes[self.product_a.product_code], 'A')
        self.assertEqual(classes[self.product_b.product_code], 'B')
        self.assertEqual(classes[self.product_c.product_code], 'C')

    def test_TC_RPT_02_002_sorted_by_value_descending(self):
        result = abc_analysis()
        codes = [row['product'].product_code for row in result['rows']]
        self.assertEqual(codes, [self.product_a.product_code, self.product_b.product_code, self.product_c.product_code])

    def test_TC_RPT_02_003_unpriced_sku_excluded_from_ranking(self):
        result = abc_analysis()
        priced_codes = [row['product'].product_code for row in result['rows']]
        self.assertNotIn(self.product_unpriced.product_code, priced_codes)
        unpriced_codes = [row['product'].product_code for row in result['unpriced']]
        self.assertIn(self.product_unpriced.product_code, unpriced_codes)

    def test_TC_RPT_02_004_excludes_staging_and_scrap_inventory(self):
        """M6: qty tồn ở Kho chờ/Kho phế không được cộng vào total_qty dùng để xếp hạng A/B/C."""
        staging = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        Inventory.objects.create(product=self.product_a, warehouse=staging, qty_on_hand=9999)
        result = abc_analysis()
        row = next(r for r in result['rows'] if r['product'] == self.product_a)
        self.assertEqual(row['total_qty'], 10)


class SlowMovingItemsTest(TestCase):
    """FR-RPT-03: SKU còn tồn nhưng không xuất > 180 ngày. ``TC-RPT-03-<seq>``."""

    def setUp(self):
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')

    def _make_product_with_issue(self, code, days_ago):
        product = Product.objects.create(product_code=code, name=code, uom='kg')
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)
        movement = StockMovement.objects.create(
            product=product, warehouse=self.warehouse,
            movement_type=StockMovement.MovementType.ISSUE, qty=-5, qty_on_hand_after=10,
        )
        StockMovement.objects.filter(pk=movement.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago))
        return product

    def _make_product_never_issued(self, code, created_days_ago):
        product = Product.objects.create(product_code=code, name=code, uom='kg')
        Product.objects.filter(pk=product.pk).update(
            created_at=timezone.now() - timedelta(days=created_days_ago))
        product.refresh_from_db()
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)
        return product

    def test_TC_RPT_03_001_flags_sku_idle_more_than_180_days(self):
        idle_product = self._make_product_with_issue('NVL-IDLE', days_ago=200)
        result = slow_moving_items(days=180)
        codes = [row['product'].product_code for row in result]
        self.assertIn(idle_product.product_code, codes)

    def test_TC_RPT_03_002_excludes_recently_issued_sku(self):
        recent_product = self._make_product_with_issue('NVL-RECENT', days_ago=10)
        result = slow_moving_items(days=180)
        codes = [row['product'].product_code for row in result]
        self.assertNotIn(recent_product.product_code, codes)

    def test_TC_RPT_03_003_never_issued_but_old_sku_is_flagged(self):
        old_product = self._make_product_never_issued('NVL-OLD', created_days_ago=200)
        result = slow_moving_items(days=180)
        codes = [row['product'].product_code for row in result]
        self.assertIn(old_product.product_code, codes)

    def test_TC_RPT_03_004_never_issued_recent_sku_is_excluded(self):
        new_product = self._make_product_never_issued('NVL-NEW', created_days_ago=5)
        result = slow_moving_items(days=180)
        codes = [row['product'].product_code for row in result]
        self.assertNotIn(new_product.product_code, codes)

    def test_TC_RPT_03_005_recommendation_scrap_vs_markdown(self):
        markdown_product = self._make_product_with_issue('NVL-MID', days_ago=200)
        scrap_product = self._make_product_with_issue('NVL-OLD2', days_ago=400)
        result = {row['product'].product_code: row for row in slow_moving_items(days=180)}
        self.assertEqual(result[markdown_product.product_code]['recommendation'], 'Giảm giá / Markdown')
        self.assertEqual(result[scrap_product.product_code]['recommendation'], 'Thanh lý (Scrap)')

    def test_TC_RPT_03_006_staging_only_inventory_excluded(self):
        """M6: sản phẩm chỉ tồn ở Kho chờ (chưa qua QC) không được liệt vào slow-moving."""
        staging = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        staging_product = Product.objects.create(product_code='NVL-STG', name='Chỉ ở Kho chờ', uom='kg')
        Product.objects.filter(pk=staging_product.pk).update(
            created_at=timezone.now() - timedelta(days=200))
        Inventory.objects.create(product=staging_product, warehouse=staging, qty_on_hand=10)

        result = slow_moving_items(days=180)
        codes = [row['product'].product_code for row in result]
        self.assertNotIn(staging_product.product_code, codes)


class SupplierPerformanceTest(TestCase):
    """FR-RPT-04: % đúng hạn, % QC pass, đơn giá bình quân theo NCC.
    ``TC-RPT-04-<seq>``.
    """

    def setUp(self):
        self.creator = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.qc_user = User.objects.create_user(
            username='qc', password='qc-pass-123', role=User.Role.QC)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty ABC', lead_time_days=5)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')

        expected = timezone.localdate()
        self.po_on_time = PurchaseOrder.objects.create(
            po_no='PO-ON-TIME', supplier=self.supplier, status=PurchaseOrder.Status.RECEIVED,
            expected_delivery_date=expected, received_at=expected, sent_at=timezone.now() - timedelta(days=5),
        )
        PurchaseOrderItem.objects.create(
            purchase_order=self.po_on_time, product=self.product, qty_ordered=10, unit_price=Decimal('100.00'))

        self.po_delayed = PurchaseOrder.objects.create(
            po_no='PO-DELAYED', supplier=self.supplier, status=PurchaseOrder.Status.RECEIVED,
            expected_delivery_date=expected, received_at=expected + timedelta(days=5),
            sent_at=timezone.now() - timedelta(days=10),
        )
        PurchaseOrderItem.objects.create(
            purchase_order=self.po_delayed, product=self.product, qty_ordered=10, unit_price=Decimal('200.00'))

        grn_pass = Grn.objects.create(po=self.po_on_time, supplier=self.supplier, created_by=self.creator)
        QcInspection.objects.create(grn=grn_pass, inspector=self.qc_user, status=QcInspection.Result.PASS)

        grn_fail = Grn.objects.create(po=self.po_delayed, supplier=self.supplier, created_by=self.creator)
        QcInspection.objects.create(grn=grn_fail, inspector=self.qc_user, status=QcInspection.Result.FAIL)

    def test_TC_RPT_04_001_on_time_pct(self):
        row = supplier_performance()[0]
        self.assertEqual(row['on_time_pct'], 50.0)

    def test_TC_RPT_04_002_qc_pass_pct(self):
        row = supplier_performance()[0]
        self.assertEqual(row['qc_pass_pct'], 50.0)

    def test_TC_RPT_04_003_avg_price(self):
        row = supplier_performance()[0]
        self.assertEqual(row['avg_price'], Decimal('150.00'))


class ReportsPermissionAndExportTest(TestCase):
    """FR-RPT-05: quyền truy cập + export Excel/PDF. ``TC-RPT-05-<seq>``."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)

    def test_TC_RPT_05_001_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('reports:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_RPT_05_002_staff_can_view_dashboard(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('reports:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_TC_RPT_05_003_abc_analysis_html_by_default(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('reports:abc_analysis'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'reports/abc_analysis.html')

    def test_TC_RPT_05_004_export_excel_content_type(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('reports:abc_analysis'), {'export': 'excel'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def test_TC_RPT_05_005_export_pdf_content_type(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('reports:slow_moving'), {'export': 'pdf'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_TC_RPT_05_006_supplier_performance_export_excel(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('reports:supplier_performance'), {'export': 'excel'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def test_TC_RPT_05_007_slow_moving_invalid_days_falls_back_to_default(self):
        """Bug fix 2026-07-27: ?days= không phải số nguyên trước đây làm
        int(...) raise ValueError -> HTTP 500. Nay phải fallback về mặc định 180."""
        self.client.force_login(self.staff)
        response = self.client.get(reverse('reports:slow_moving'), {'days': 'abc'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['days'], 180)

    def test_TC_RPT_05_008_slow_moving_valid_days_still_respected(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('reports:slow_moving'), {'days': '90'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['days'], 90)
