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
            expected_delivery_date=expected, received_at=expected,
        )
        PurchaseOrderItem.objects.create(
            purchase_order=self.po_on_time, product=self.product, qty_ordered=10, unit_price=Decimal('100.00'))

        self.po_delayed = PurchaseOrder.objects.create(
            po_no='PO-DELAYED', supplier=self.supplier, status=PurchaseOrder.Status.RECEIVED,
            expected_delivery_date=expected, received_at=expected + timedelta(days=5),
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
