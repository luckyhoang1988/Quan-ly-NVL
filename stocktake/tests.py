"""Unit test cho Stock Opname (BACKLOG mục 4, Phase 4) — DoD Phase 4:
"chênh lệch dương/âm đều tạo đúng Adjustment, Inventory cập nhật đúng chiều".

Theo cùng bố cục với ``shipping.tests`` (Model -> Service theo từng bước
workflow -> View/permission), mã test case ``TC-SO-<FR#>-<seq>``.
"""
import datetime
from unittest import mock

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

    def test_TC_SO_MODEL_004_so_no_retries_on_integrity_error_collision(self):
        """Bug fix 2026-07-27: save() phải retry khi generate_so_no() trả về số đã
        tồn tại (race condition giữa 2 request song song tạo phiếu cùng lúc), không
        để IntegrityError văng ra ngoài — mirror StockTransfer.save() (xem CLAUDE.md)."""
        existing = StocktakeSession.objects.create(warehouse=self.warehouse, created_by=self.manager)
        colliding_no = existing.so_no
        next_no = f'{colliding_no[:-3]}{int(colliding_no[-3:]) + 1:03d}'
        with mock.patch.object(
            StocktakeSession, 'generate_so_no', side_effect=[colliding_no, next_no],
        ):
            session = StocktakeSession(warehouse=self.warehouse, created_by=self.manager)
            session.save()
        self.assertEqual(session.so_no, next_no)


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

    def test_TC_SO_07_003_qty_system_uses_location_batch_qty_not_whole_warehouse_inventory(self):
        """Bug fix 2026-07-27: kho có 2 vị trí, A=60/B=40 (Inventory cả kho=100)
        — phiếu chỉ giới hạn vị trí A phải lấy qty_system=60, không phải 100
        (Inventory cả kho) — nếu không, đếm đúng A=60 vẫn báo chênh lệch giả
        -40 (xem CLAUDE.md)."""
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        Batch.objects.create(
            product=self.product, batch_code='LOT-A', supplier=self.supplier, location=self.location_a,
            qty_received=60, exp_date=self.today + datetime.timedelta(days=10),
        )
        Batch.objects.create(
            product=self.product, batch_code='LOT-B', supplier=self.supplier, location=self.location_b,
            qty_received=40, exp_date=self.today + datetime.timedelta(days=10),
        )
        session = create_session(
            warehouse=self.warehouse, created_by=self.manager, location=self.location_a, actor=self.manager)
        item = session.items.get(product=self.product)
        self.assertEqual(item.qty_system, 60)

    def test_TC_SO_07_004_qty_system_at_location_subtracts_qty_used(self):
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        batch = Batch.objects.create(
            product=self.product, batch_code='LOT-A', supplier=self.supplier, location=self.location_a,
            qty_received=60, exp_date=self.today + datetime.timedelta(days=10),
        )
        batch.qty_used = 20
        batch.save(update_fields=['qty_used'])
        session = create_session(
            warehouse=self.warehouse, created_by=self.manager, location=self.location_a, actor=self.manager)
        item = session.items.get(product=self.product)
        self.assertEqual(item.qty_system, 40)

    def test_TC_SO_07_005_blank_location_still_uses_warehouse_inventory(self):
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        Batch.objects.create(
            product=self.product, batch_code='LOT-A', supplier=self.supplier, location=self.location_a,
            qty_received=60, exp_date=self.today + datetime.timedelta(days=10),
        )
        session = create_session(warehouse=self.warehouse, created_by=self.manager, actor=self.manager)
        item = session.items.get(product=self.product)
        self.assertEqual(item.qty_system, 100)

    def test_TC_SO_07_006_rejects_location_from_different_warehouse(self):
        """Bug fix 2026-07-27: chọn kho A + vị trí thuộc kho B phải bị chặn,
        không được ra danh sách SKU/qty sai hoặc rỗng (xem CLAUDE.md)."""
        other_warehouse = Warehouse.objects.create(code='KHO-HCM', name='Kho Hồ Chí Minh')
        other_location = Location.objects.create(warehouse=other_warehouse, code='C-01')
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        with self.assertRaises(ValidationError):
            create_session(
                warehouse=self.warehouse, created_by=self.manager, location=other_location, actor=self.manager)


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
    ``TC-SO-05-009`` trở đi: bug fix 2026-07-27 — ``apply_adjustment`` phải
    đồng bộ ``Batch`` chứ không chỉ ``Inventory`` (xem CLAUDE.md).
    """

    def setUp(self):
        self.manager = User.objects.create_user(username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.other_product = Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg')
        self.session = StocktakeSession.objects.create(
            warehouse=self.warehouse, created_by=self.manager, status=StocktakeSession.Status.RECONCILIATION)
        self._lot_seq = 0

    def _inventory(self, product, qty_on_hand):
        return Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=qty_on_hand)

    def _batch(self, product, qty, batch_code=None, **kwargs):
        self._lot_seq += 1
        return Batch.objects.create(
            product=product, batch_code=batch_code or f'LOT-{self._lot_seq:04d}', supplier=self.supplier,
            location=self.location, qty_received=qty, **kwargs,
        )

    def _item(self, product, qty_system, qty_actual):
        return StocktakeItem.objects.create(
            session=self.session, product=product, qty_system=qty_system, qty_actual=qty_actual)

    def test_TC_SO_05_001_positive_variance_increases_inventory(self):
        self._inventory(self.product, 100)
        self._batch(self.product, 100)
        self._item(self.product, qty_system=100, qty_actual=110)
        apply_adjustment(self.session, actor=self.manager)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 110)

    def test_TC_SO_05_002_negative_variance_decreases_inventory(self):
        self._inventory(self.product, 100)
        self._batch(self.product, 100)
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
        self._batch(self.product, 100)
        self._batch(self.other_product, 50)
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
        self._batch(self.product, 100)
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

    def test_TC_SO_05_009_surplus_creates_new_active_batch_for_fifo(self):
        """Bug fix 2026-07-27: thừa phải tạo Batch mới ACTIVE — nếu không, FIFO/GIN
        không thấy lô nào để xuất phần thừa dù Inventory đã tăng."""
        self._inventory(self.product, 100)
        self._batch(self.product, 100)
        self._item(self.product, qty_system=100, qty_actual=110)
        apply_adjustment(self.session, actor=self.manager)

        new_batch = Batch.objects.get(batch_code=f'ADJ-{self.session.so_no}-{self.product.product_code}')
        self.assertEqual(new_batch.status, Batch.Status.ACTIVE)
        self.assertEqual(new_batch.qty_available, 10)
        self.assertEqual(new_batch.supplier, self.supplier)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        total_batch_qty = sum(b.qty_available for b in Batch.objects.filter(product=self.product))
        self.assertEqual(total_batch_qty, inv.qty_on_hand)

    def test_TC_SO_05_010_shortage_reduces_existing_batch_qty(self):
        """Bug fix 2026-07-27: thiếu phải trừ vào batch hiện có — nếu không, batch
        vẫn báo đủ hàng để FIFO xuất dù Inventory đã giảm."""
        self._inventory(self.product, 100)
        batch = self._batch(self.product, 100)
        self._item(self.product, qty_system=100, qty_actual=90)
        apply_adjustment(self.session, actor=self.manager)

        batch.refresh_from_db()
        self.assertEqual(batch.qty_available, 90)
        self.assertEqual(batch.status, Batch.Status.PARTIAL_USED)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(batch.qty_available, inv.qty_on_hand)

    def test_TC_SO_05_011_shortage_consumes_multiple_batches_fifo_order_and_closes_depleted(self):
        today = timezone.now().date()
        older = self._batch(self.product, 40, batch_code='LOT-OLD', exp_date=today)
        newer = self._batch(self.product, 60, batch_code='LOT-NEW', exp_date=today + datetime.timedelta(days=30))
        self._inventory(self.product, 100)
        self._item(self.product, qty_system=100, qty_actual=50)
        apply_adjustment(self.session, actor=self.manager)

        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.qty_available, 0)
        self.assertEqual(older.status, Batch.Status.CLOSED)
        self.assertEqual(newer.qty_available, 50)
        self.assertEqual(newer.status, Batch.Status.PARTIAL_USED)

    def test_TC_SO_05_012_shortage_rejects_when_not_enough_batch_to_cover(self):
        self._inventory(self.product, 100)
        self._batch(self.product, 20)
        self._item(self.product, qty_system=100, qty_actual=70)
        with self.assertRaises(ValidationError):
            apply_adjustment(self.session, actor=self.manager)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 100)

    def test_TC_SO_05_013_surplus_rejects_when_product_has_no_batch_history(self):
        self._inventory(self.other_product, 50)
        self._item(self.other_product, qty_system=50, qty_actual=60)
        with self.assertRaises(ValidationError):
            apply_adjustment(self.session, actor=self.manager)

    def test_TC_SO_05_014_shortage_consumes_expired_batch_and_keeps_expired_status(self):
        """BUG-08: Inventory.qty_on_hand tính cả batch EXPIRED (hàng vẫn nằm vật
        lý trong kho) nên trừ thiếu phải chọn được batch này, KHÔNG chỉ
        ACTIVE/PARTIAL_USED — nếu không, service báo "không đủ lô" và rollback
        toàn phiếu dù Inventory dư sức trừ."""
        self._inventory(self.product, 100)
        batch = self._batch(self.product, 100, status=Batch.Status.EXPIRED)
        self._item(self.product, qty_system=100, qty_actual=90)
        apply_adjustment(self.session, actor=self.manager)

        batch.refresh_from_db()
        self.assertEqual(batch.qty_available, 90)
        self.assertEqual(batch.status, Batch.Status.EXPIRED, 'trừ một phần không được đẩy EXPIRED thành PARTIAL_USED (sẽ vô tình FIFO-eligible trở lại)')
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 90)

    def test_TC_SO_05_015_shortage_fully_depleting_expired_batch_closes_it(self):
        self._inventory(self.product, 10)
        batch = self._batch(self.product, 10, status=Batch.Status.EXPIRED)
        self._item(self.product, qty_system=10, qty_actual=0)
        apply_adjustment(self.session, actor=self.manager)

        batch.refresh_from_db()
        self.assertEqual(batch.qty_available, 0)
        self.assertEqual(batch.status, Batch.Status.CLOSED)

    def test_TC_SO_05_016_shortage_consumes_pending_receipt_batch_and_keeps_status(self):
        self._inventory(self.product, 50)
        batch = self._batch(self.product, 50, status=Batch.Status.PENDING_RECEIPT)
        self._item(self.product, qty_system=50, qty_actual=40)
        apply_adjustment(self.session, actor=self.manager)

        batch.refresh_from_db()
        self.assertEqual(batch.qty_available, 40)
        self.assertEqual(batch.status, Batch.Status.PENDING_RECEIPT)

    def test_TC_SO_05_017_shortage_consumes_quarantine_batch_in_scrap_warehouse(self):
        scrap_warehouse = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        scrap_location = Location.objects.create(warehouse=scrap_warehouse, code='PHE-01')
        scrap_session = StocktakeSession.objects.create(
            warehouse=scrap_warehouse, created_by=self.manager, status=StocktakeSession.Status.RECONCILIATION)
        batch = Batch.objects.create(
            product=self.product, batch_code='LOT-QT-0001', supplier=self.supplier,
            location=scrap_location, qty_received=30, status=Batch.Status.QUARANTINE,
        )
        Inventory.objects.create(product=self.product, warehouse=scrap_warehouse, qty_on_hand=30)
        StocktakeItem.objects.create(session=scrap_session, product=self.product, qty_system=30, qty_actual=25)

        apply_adjustment(scrap_session, actor=self.manager)

        batch.refresh_from_db()
        self.assertEqual(batch.qty_available, 25)
        self.assertEqual(batch.status, Batch.Status.QUARANTINE)
        inv = Inventory.objects.get(product=self.product, warehouse=scrap_warehouse)
        self.assertEqual(inv.qty_on_hand, 25)

    def test_TC_SO_05_018_shortage_scoped_to_location_consumes_non_active_batch_there(self):
        other_location = Location.objects.create(warehouse=self.warehouse, code='A-02')
        located_session = StocktakeSession.objects.create(
            warehouse=self.warehouse, location=self.location, created_by=self.manager,
            status=StocktakeSession.Status.RECONCILIATION,
        )
        in_scope = self._batch(self.product, 20, status=Batch.Status.EXPIRED)
        out_of_scope = Batch.objects.create(
            product=self.product, batch_code='LOT-OTHER-LOC', supplier=self.supplier,
            location=other_location, qty_received=100, status=Batch.Status.ACTIVE,
        )
        self._inventory(self.product, 120)
        StocktakeItem.objects.create(session=located_session, product=self.product, qty_system=20, qty_actual=15)

        apply_adjustment(located_session, actor=self.manager)

        in_scope.refresh_from_db()
        out_of_scope.refresh_from_db()
        self.assertEqual(in_scope.qty_available, 15)
        self.assertEqual(in_scope.status, Batch.Status.EXPIRED)
        self.assertEqual(out_of_scope.qty_available, 100, 'batch ở vị trí khác không được đụng tới')

    def test_TC_SO_05_019_shortage_fifo_order_spans_mixed_statuses(self):
        """FIFO theo exp_date/created_at áp dụng xuyên suốt mọi status trong
        PHYSICAL_BATCH_STATUSES, không chỉ trong nội bộ ACTIVE/PARTIAL_USED."""
        today = timezone.now().date()
        expired_older = self._batch(
            self.product, 10, batch_code='LOT-EXP', status=Batch.Status.EXPIRED, exp_date=today)
        active_newer = self._batch(
            self.product, 50, batch_code='LOT-ACT', status=Batch.Status.ACTIVE,
            exp_date=today + datetime.timedelta(days=30))
        self._inventory(self.product, 60)
        self._item(self.product, qty_system=60, qty_actual=45)
        apply_adjustment(self.session, actor=self.manager)

        expired_older.refresh_from_db()
        active_newer.refresh_from_db()
        self.assertEqual(expired_older.qty_available, 0)
        self.assertEqual(expired_older.status, Batch.Status.CLOSED)
        self.assertEqual(active_newer.qty_available, 45)
        self.assertEqual(active_newer.status, Batch.Status.PARTIAL_USED)


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
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=100,
        )

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


class SoListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (status/warehouse/tìm kiếm) trên so_list."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.client.force_login(self.staff)
        self.wh_a = Warehouse.objects.create(code='KHO-A', name='Kho A')
        self.wh_b = Warehouse.objects.create(code='KHO-B', name='Kho B')
        StocktakeSession.objects.bulk_create([
            StocktakeSession(
                so_no=f'SO-TEST-{i:04d}',
                warehouse=self.wh_a if i % 2 == 0 else self.wh_b,
                status=StocktakeSession.Status.PLANNING if i % 2 == 0 else StocktakeSession.Status.EXECUTION,
                created_by=self.staff,
            )
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('stocktake:so_list'))
        self.assertEqual(len(response.context['sessions']), 30)

    def test_page_size_50_shows_all(self):
        response = self.client.get(reverse('stocktake:so_list'), {'page_size': 50})
        self.assertEqual(len(response.context['sessions']), 35)

    def test_filter_status(self):
        response = self.client.get(
            reverse('stocktake:so_list'), {'status': StocktakeSession.Status.EXECUTION, 'page_size': 50})
        self.assertTrue(
            all(s.status == StocktakeSession.Status.EXECUTION for s in response.context['sessions']))

    def test_filter_warehouse(self):
        response = self.client.get(
            reverse('stocktake:so_list'), {'warehouse': self.wh_a.pk, 'page_size': 50})
        self.assertTrue(all(s.warehouse_id == self.wh_a.pk for s in response.context['sessions']))

    def test_filter_search_by_so_no(self):
        response = self.client.get(reverse('stocktake:so_list'), {'q': 'SO-TEST-0001'})
        so_nos = [s.so_no for s in response.context['sessions']]
        self.assertEqual(so_nos, ['SO-TEST-0001'])
