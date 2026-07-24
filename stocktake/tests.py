"""Unit test cho Stock Opname (BACKLOG mục 4, Phase 4) — DoD Phase 4:
"chênh lệch dương/âm đều tạo đúng Adjustment, Inventory cập nhật đúng chiều".

Theo cùng bố cục với ``shipping.tests`` (Model -> Service theo từng bước
workflow -> View/permission), mã test case ``TC-SO-<FR#>-<seq>``.
"""
import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog
from catalog.models import Product
from inventory.models import Batch, Inventory, StockMovement
from partners.models import Supplier
from warehouse.models import Location, Warehouse

from .models import StocktakeItem, StocktakeSession
from .services import apply_adjustment, create_session, record_count, start_execution, submit_reconciliation

User = get_user_model()


class StocktakeSessionModelTest(TestCase):
    """Auto-numbering + default status (mirror BR-GRN-001). ``TC-SO-MODEL-<seq>``."""

    def setUp(self):
        self.manager = User.objects.create_user(username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')

    def test_TC_SO_MODEL_001_so_no_auto_generated_with_current_yyyymm_prefix(self):
        session = StocktakeSession.objects.create(warehouse=self.warehouse, created_by=self.manager)
        today = datetime.date.today()
        self.assertEqual(session.so_no, f'SO-{today:%Y%m}-001')

    def test_TC_SO_MODEL_002_so_no_sequence_increments_within_same_month(self):
        first = StocktakeSession.objects.create(warehouse=self.warehouse, created_by=self.manager)
        second = StocktakeSession.objects.create(warehouse=self.warehouse, created_by=self.manager)
        self.assertEqual(first.so_no[:-3], second.so_no[:-3])
        self.assertEqual(int(second.so_no[-3:]), int(first.so_no[-3:]) + 1)

    def test_TC_SO_MODEL_003_default_status_is_planning(self):
        session = StocktakeSession.objects.create(warehouse=self.warehouse, created_by=self.manager)
        self.assertEqual(session.status, StocktakeSession.Status.PLANNING)


class StocktakeItemVarianceTest(TestCase):
    """FR-SO-03: ``StocktakeItem.variance`` tính on-the-fly. ``TC-SO-03-<seq>``."""

    def setUp(self):
        self.manager = User.objects.create_user(username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.session = StocktakeSession.objects.create(warehouse=self.warehouse, created_by=self.manager)

    def _item(self, qty_system=100, qty_actual=None):
        return StocktakeItem.objects.create(
            session=self.session, product=self.product, qty_system=qty_system, qty_actual=qty_actual)

    def test_TC_SO_03_001_variance_is_none_when_not_yet_counted(self):
        item = self._item(qty_actual=None)
        self.assertIsNone(item.variance)

    def test_TC_SO_03_002_variance_positive_when_actual_greater(self):
        item = self._item(qty_system=100, qty_actual=110)
        self.assertEqual(item.variance, 10)

    def test_TC_SO_03_003_variance_negative_when_actual_less(self):
        item = self._item(qty_system=100, qty_actual=90)
        self.assertEqual(item.variance, -10)

    def test_TC_SO_03_004_variance_zero_when_match(self):
        item = self._item(qty_system=100, qty_actual=100)
        self.assertEqual(item.variance, 0)


class CreateSessionServiceTest(TestCase):
    """``create_session`` (FR-SO-01/FR-SO-07). ``TC-SO-01-<seq>`` / ``TC-SO-07-<seq>``."""

    def setUp(self):
        self.manager = User.objects.create_user(username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location_a = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.location_b = Location.objects.create(warehouse=self.warehouse, code='B-01')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.other_product = Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg')
        self.today = timezone.now().date()

    def test_TC_SO_01_001_creates_planning_session_with_snapshot_items(self):
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        session = create_session(warehouse=self.warehouse, created_by=self.manager, actor=self.manager)
        self.assertEqual(session.status, StocktakeSession.Status.PLANNING)
        item = session.items.get(product=self.product)
        self.assertEqual(item.qty_system, 100)
        self.assertIsNone(item.qty_actual)

    def test_TC_SO_01_002_rejects_when_no_inventory_in_warehouse(self):
        with self.assertRaises(ValidationError):
            create_session(warehouse=self.warehouse, created_by=self.manager, actor=self.manager)

    def test_TC_SO_01_003_excludes_inactive_products(self):
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        self.product.is_active = False
        self.product.save(update_fields=['is_active'])
        with self.assertRaises(ValidationError):
            create_session(warehouse=self.warehouse, created_by=self.manager, actor=self.manager)

    def test_TC_SO_01_004_writes_audit_log(self):
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        session = create_session(warehouse=self.warehouse, created_by=self.manager, actor=self.manager)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(session.pk)).exists())

    def test_TC_SO_07_001_location_filters_to_products_with_batch_at_location(self):
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        Inventory.objects.create(product=self.other_product, warehouse=self.warehouse, qty_on_hand=50)
        Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location_a,
            qty_received=100, exp_date=self.today + datetime.timedelta(days=10),
        )
        Batch.objects.create(
            product=self.other_product, batch_code='LOT-0002', supplier=self.supplier, location=self.location_b,
            qty_received=50, exp_date=self.today + datetime.timedelta(days=10),
        )
        session = create_session(
            warehouse=self.warehouse, created_by=self.manager, location=self.location_a, actor=self.manager)
        products_in_session = set(session.items.values_list('product_id', flat=True))
        self.assertEqual(products_in_session, {self.product.pk})

    def test_TC_SO_07_002_blank_location_includes_all_warehouse_products(self):
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        Inventory.objects.create(product=self.other_product, warehouse=self.warehouse, qty_on_hand=50)
        session = create_session(warehouse=self.warehouse, created_by=self.manager, actor=self.manager)
        products_in_session = set(session.items.values_list('product_id', flat=True))
        self.assertEqual(products_in_session, {self.product.pk, self.other_product.pk})


class StartExecutionServiceTest(TestCase):
    """``start_execution``: PLANNING -> EXECUTION."""

    def setUp(self):
        self.manager = User.objects.create_user(username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.session = StocktakeSession.objects.create(warehouse=self.warehouse, created_by=self.manager)

    def test_TC_SO_EXEC_001_planning_transitions_to_execution(self):
        start_execution(self.session, actor=self.manager)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, StocktakeSession.Status.EXECUTION)

    def test_TC_SO_EXEC_002_rejects_when_not_planning(self):
        self.session.status = StocktakeSession.Status.EXECUTION
        self.session.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            start_execution(self.session, actor=self.manager)


class RecordCountServiceTest(TestCase):
    """``record_count`` (FR-SO-02/FR-SO-04). ``TC-SO-02-<seq>`` / ``TC-SO-04-<seq>``."""

    def setUp(self):
        self.manager = User.objects.create_user(username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.session = StocktakeSession.objects.create(
            warehouse=self.warehouse, created_by=self.manager, status=StocktakeSession.Status.EXECUTION)
        self.item = StocktakeItem.objects.create(session=self.session, product=self.product, qty_system=100)

    def test_TC_SO_02_001_records_qty_actual_and_counted_by(self):
        record_count(self.item, 110, counted_by=self.staff, reason=StocktakeItem.Reason.COUNTING_ERROR, actor=self.staff)
        self.item.refresh_from_db()
        self.assertEqual(self.item.qty_actual, 110)
        self.assertEqual(self.item.counted_by, self.staff)
        self.assertIsNotNone(self.item.counted_at)

    def test_TC_SO_02_002_rejects_when_not_execution(self):
        self.session.status = StocktakeSession.Status.PLANNING
        self.session.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            record_count(self.item, 100, counted_by=self.staff, actor=self.staff)

    def test_TC_SO_02_003_rejects_negative_qty(self):
        with self.assertRaises(ValidationError):
            record_count(self.item, -5, counted_by=self.staff, actor=self.staff)

    def test_TC_SO_02_004_writes_audit_log(self):
        record_count(self.item, 100, counted_by=self.staff, actor=self.staff)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.UPDATE, target_id=str(self.session.pk)).exists())

    def test_TC_SO_04_001_requires_reason_when_variance_nonzero(self):
        with self.assertRaises(ValidationError):
            record_count(self.item, 110, counted_by=self.staff, reason='', actor=self.staff)

    def test_TC_SO_04_002_reason_not_required_when_match(self):
        record_count(self.item, 100, counted_by=self.staff, reason='', actor=self.staff)
        self.item.refresh_from_db()
        self.assertEqual(self.item.qty_actual, 100)
        self.assertEqual(self.item.reason, '')


class SubmitReconciliationServiceTest(TestCase):
    """``submit_reconciliation``: EXECUTION -> RECONCILIATION."""

    def setUp(self):
        self.manager = User.objects.create_user(username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.session = StocktakeSession.objects.create(
            warehouse=self.warehouse, created_by=self.manager, status=StocktakeSession.Status.EXECUTION)
        self.item = StocktakeItem.objects.create(session=self.session, product=self.product, qty_system=100)

    def test_TC_SO_RECON_001_transitions_when_all_counted(self):
        self.item.qty_actual = 100
        self.item.save(update_fields=['qty_actual'])
        submit_reconciliation(self.session, actor=self.manager)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, StocktakeSession.Status.RECONCILIATION)
        self.assertIsNotNone(self.session.reconciled_at)

    def test_TC_SO_RECON_002_rejects_when_items_uncounted(self):
        with self.assertRaises(ValidationError):
            submit_reconciliation(self.session, actor=self.manager)

    def test_TC_SO_RECON_003_rejects_when_not_execution(self):
        self.session.status = StocktakeSession.Status.PLANNING
        self.session.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            submit_reconciliation(self.session, actor=self.manager)


class ApplyAdjustmentServiceTest(TestCase):
    """``apply_adjustment`` (FR-SO-05) — DoD Phase 4: "chênh lệch dương/âm đều
    tạo đúng Adjustment, Inventory cập nhật đúng chiều". ``TC-SO-05-<seq>``.
    """

    def setUp(self):
        self.manager = User.objects.create_user(username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.other_product = Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg')
        self.session = StocktakeSession.objects.create(
            warehouse=self.warehouse, created_by=self.manager, status=StocktakeSession.Status.RECONCILIATION)

    def _inventory(self, product, qty_on_hand):
        return Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=qty_on_hand)

    def _item(self, product, qty_system, qty_actual):
        return StocktakeItem.objects.create(
            session=self.session, product=product, qty_system=qty_system, qty_actual=qty_actual)

    def test_TC_SO_05_001_positive_variance_increases_inventory(self):
        self._inventory(self.product, 100)
        self._item(self.product, qty_system=100, qty_actual=110)
        apply_adjustment(self.session, actor=self.manager)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 110)

    def test_TC_SO_05_002_negative_variance_decreases_inventory(self):
        self._inventory(self.product, 100)
        self._item(self.product, qty_system=100, qty_actual=90)
        apply_adjustment(self.session, actor=self.manager)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 90)

    def test_TC_SO_05_003_zero_variance_no_movement_created_and_inventory_unchanged(self):
        self._inventory(self.product, 100)
        self._item(self.product, qty_system=100, qty_actual=100)
        apply_adjustment(self.session, actor=self.manager)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 100)
        self.assertFalse(
            StockMovement.objects.filter(
                product=self.product, movement_type=StockMovement.MovementType.ADJUSTMENT).exists())

    def test_TC_SO_05_004_creates_stock_movement_adjustment_entries_with_correct_sign(self):
        self._inventory(self.product, 100)
        self._inventory(self.other_product, 50)
        self._item(self.product, qty_system=100, qty_actual=110)
        self._item(self.other_product, qty_system=50, qty_actual=40)
        apply_adjustment(self.session, actor=self.manager)

        gain_movement = StockMovement.objects.get(
            product=self.product, movement_type=StockMovement.MovementType.ADJUSTMENT)
        loss_movement = StockMovement.objects.get(
            product=self.other_product, movement_type=StockMovement.MovementType.ADJUSTMENT)
        self.assertEqual(gain_movement.qty, 10)
        self.assertEqual(loss_movement.qty, -10)
        self.assertEqual(gain_movement.reference, self.session.so_no)

    def test_TC_SO_05_005_rejects_when_adjustment_would_make_inventory_negative(self):
        self._inventory(self.product, 5)
        self._item(self.product, qty_system=100, qty_actual=0)
        with self.assertRaises(ValidationError):
            apply_adjustment(self.session, actor=self.manager)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 5)

    def test_TC_SO_05_006_rejects_when_not_reconciliation(self):
        self.session.status = StocktakeSession.Status.EXECUTION
        self.session.save(update_fields=['status'])
        self._inventory(self.product, 100)
        self._item(self.product, qty_system=100, qty_actual=110)
        with self.assertRaises(ValidationError):
            apply_adjustment(self.session, actor=self.manager)

    def test_TC_SO_05_007_writes_audit_log(self):
        self._inventory(self.product, 100)
        self._item(self.product, qty_system=100, qty_actual=110)
        apply_adjustment(self.session, actor=self.manager)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.APPROVE, target_id=str(self.session.pk)).exists())

    def test_TC_SO_05_008_sets_completed_at_and_status_adjustment(self):
        self._inventory(self.product, 100)
        self._item(self.product, qty_system=100, qty_actual=100)
        apply_adjustment(self.session, actor=self.manager)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, StocktakeSession.Status.ADJUSTMENT)
        self.assertIsNotNone(self.session.completed_at)


class StocktakeViewTest(TestCase):
    """View/URL/permission cho Stock Opname theo ma trận quyền
    ``accounts.permissions`` (module ``opname``: STAFF không có ``approve``;
    QC/PURCHASING/ACCOUNTANT không có quyền gì). ``TC-SO-VIEW-<seq>``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.manager = User.objects.create_user(username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.qc_user = User.objects.create_user(username='qc1', password='qc-pass-123', role=User.Role.QC)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)

    def _session_in_execution(self):
        session = create_session(warehouse=self.warehouse, created_by=self.manager, actor=self.manager)
        start_execution(session, actor=self.manager)
        return session

    def test_TC_SO_VIEW_001_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('stocktake:so_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_SO_VIEW_002_staff_can_create_session(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('stocktake:so_create'), {
            'warehouse': self.warehouse.pk, 'location': '', 'notes': '',
        })
        session = StocktakeSession.objects.get()
        self.assertRedirects(response, reverse('stocktake:so_detail', args=[session.pk]))
        self.assertEqual(session.status, StocktakeSession.Status.PLANNING)

    def test_TC_SO_VIEW_003_staff_can_execute_count(self):
        session = self._session_in_execution()
        self.client.force_login(self.staff)
        response = self.client.post(reverse('stocktake:so_execution', args=[session.pk]), {
            'product_code': self.product.product_code, 'qty_actual': 110,
            'reason': StocktakeItem.Reason.COUNTING_ERROR,
        })
        self.assertRedirects(response, reverse('stocktake:so_execution', args=[session.pk]))
        item = StocktakeItem.objects.get(session=session, product=self.product)
        self.assertEqual(item.qty_actual, 110)

    def test_TC_SO_VIEW_004_unknown_sku_shows_form_error_not_500(self):
        session = self._session_in_execution()
        self.client.force_login(self.staff)
        response = self.client.post(reverse('stocktake:so_execution', args=[session.pk]), {
            'product_code': 'NVL-9999', 'qty_actual': 1, 'reason': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'không có trong phiếu kiểm kê')

    def test_TC_SO_VIEW_005_staff_forbidden_to_apply_adjustment(self):
        session = self._session_in_execution()
        StocktakeItem.objects.filter(session=session).update(qty_actual=100)
        submit_reconciliation(session, actor=self.manager)
        self.client.force_login(self.staff)
        response = self.client.post(reverse('stocktake:so_apply_adjustment', args=[session.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_SO_VIEW_006_manager_can_apply_adjustment(self):
        session = self._session_in_execution()
        StocktakeItem.objects.filter(session=session).update(qty_actual=110)
        submit_reconciliation(session, actor=self.manager)
        self.client.force_login(self.manager)
        response = self.client.post(reverse('stocktake:so_apply_adjustment', args=[session.pk]))
        session.refresh_from_db()
        self.assertRedirects(response, reverse('stocktake:so_detail', args=[session.pk]))
        self.assertEqual(session.status, StocktakeSession.Status.ADJUSTMENT)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 110)

    def test_TC_SO_VIEW_007_qc_forbidden_read(self):
        self.client.force_login(self.qc_user)
        response = self.client.get(reverse('stocktake:so_list'))
        self.assertEqual(response.status_code, 403)


class StocktakeDetailTemplateTest(TestCase):
    """FR-SO-06 + UI color-highlight rule (BACKLOG mục 4: xanh/vàng/đỏ theo
    variance). ``TC-SO-06-<seq>``.
    """

    def setUp(self):
        self.manager = User.objects.create_user(username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.match_product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.minor_product = Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg')
        self.major_product = Product.objects.create(product_code='NVL-0003', name='Muối', uom='kg')
        self.session = StocktakeSession.objects.create(warehouse=self.warehouse, created_by=self.manager)
        StocktakeItem.objects.create(
            session=self.session, product=self.match_product, qty_system=100, qty_actual=100)
        StocktakeItem.objects.create(
            session=self.session, product=self.minor_product, qty_system=100, qty_actual=104)
        StocktakeItem.objects.create(
            session=self.session, product=self.major_product, qty_system=100, qty_actual=80)
        self.client.force_login(self.manager)

    def test_TC_SO_06_001_matched_row_highlighted_success(self):
        response = self.client.get(reverse('stocktake:so_detail', args=[self.session.pk]))
        content = response.content.decode()
        match_row_start = content.index('NVL-0001')
        self.assertIn('table-success', content[max(0, match_row_start - 400):match_row_start])

    def test_TC_SO_06_002_minor_variance_highlighted_warning(self):
        response = self.client.get(reverse('stocktake:so_detail', args=[self.session.pk]))
        content = response.content.decode()
        row_start = content.index('NVL-0002')
        self.assertIn('table-warning', content[max(0, row_start - 400):row_start])

    def test_TC_SO_06_003_major_variance_highlighted_danger(self):
        response = self.client.get(reverse('stocktake:so_detail', args=[self.session.pk]))
        content = response.content.decode()
        row_start = content.index('NVL-0003')
        self.assertIn('table-danger', content[max(0, row_start - 400):row_start])
