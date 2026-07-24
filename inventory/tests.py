import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog
from catalog.models import Product
from partners.models import Supplier
from purchasing.models import PurchaseOrder, PurchaseOrderItem
from warehouse.models import Location, Warehouse

from .models import Batch, Inventory, StockMovement, StockTransfer
from .services import (
    calculate_eoq, expiring_soon_batches, record_movement, suggest_fifo_batches, sync_expired_batches,
    transfer_stock,
)

User = get_user_model()


class InventoryModelTest(TestCase):
    """Inventory (mục 1f — bổ sung, schema only, chưa có mã FR riêng).

    Bao test cho BR-WM-001 (qty_on_hand >= 0), BR-WM-002 (qty_available auto
    calculate) và Phase 1 DoD dòng "qty_available tính đúng (unit test)".

    Đặt tên test theo quy ước ``TC-INV-001-<seq>``.
    """

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')

    def test_TC_INV_001_001_qty_available_computed_from_on_hand_and_reserved(self):
        inv = Inventory.objects.create(
            product=self.product, warehouse=self.warehouse, qty_on_hand=100, qty_reserved=30)
        self.assertEqual(inv.qty_available, 70)

    def test_TC_INV_001_002_negative_qty_on_hand_rejected_by_validation(self):
        inv = Inventory(product=self.product, warehouse=self.warehouse, qty_on_hand=-5)
        with self.assertRaises(ValidationError):
            inv.full_clean()

    def test_TC_INV_001_003_unique_per_product_and_warehouse(self):
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=10)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=5)


class BatchModelTest(TestCase):
    """Batch (mục 1f — bổ sung, schema only). ``TC-INV-002-<seq>``."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')

    def test_TC_INV_002_001_qty_available_computed_from_received_and_used(self):
        batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=100, qty_used=40,
        )
        self.assertEqual(batch.qty_available, 60)

    def test_TC_INV_002_002_default_status_is_active(self):
        batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=50,
        )
        self.assertEqual(batch.status, Batch.Status.ACTIVE)

    def test_TC_INV_002_003_batch_code_unique(self):
        Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=50,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Batch.objects.create(
                    product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
                    qty_received=10,
                )

    def test_TC_INV_002_004_exp_date_optional(self):
        batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=50, mfg_date=datetime.date(2026, 1, 1),
        )
        self.assertIsNone(batch.exp_date)


class StockMovementServiceTest(TestCase):
    """``record_movement`` (FR-INV-03 — audit trail chuyển động). ``TC-INV-MOVE-<seq>``."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=50)

    def test_TC_INV_MOVE_001_record_movement_snapshots_qty_on_hand_after(self):
        movement = record_movement(
            product=self.product, warehouse=self.warehouse,
            movement_type=StockMovement.MovementType.RECEIPT, qty=50, reference='GRN-TEST',
        )
        self.assertEqual(movement.qty_on_hand_after, 50)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.RECEIPT)

    def test_TC_INV_MOVE_002_created_by_null_when_actor_has_no_pk(self):
        movement = record_movement(
            product=self.product, warehouse=self.warehouse,
            movement_type=StockMovement.MovementType.ADJUSTMENT, qty=-5,
        )
        self.assertIsNone(movement.created_by)


class ExpiredBatchSyncServiceTest(TestCase):
    """``sync_expired_batches`` (BR-GIN-007 / TC-INV-001-001). ``TC-INV-EXP-<seq>``."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.today = timezone.now().date()

    def _batch(self, code, exp_date, status=Batch.Status.ACTIVE, qty=50):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier, location=self.location,
            qty_received=qty, exp_date=exp_date, status=status,
        )

    def test_TC_INV_EXP_001_active_batch_past_exp_date_becomes_expired(self):
        batch = self._batch('LOT-0001', self.today - datetime.timedelta(days=1))
        count = sync_expired_batches()
        batch.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(batch.status, Batch.Status.EXPIRED)

    def test_TC_INV_EXP_002_batch_without_exp_date_not_affected(self):
        batch = self._batch('LOT-0001', None)
        sync_expired_batches()
        batch.refresh_from_db()
        self.assertEqual(batch.status, Batch.Status.ACTIVE)

    def test_TC_INV_EXP_003_batch_not_yet_expired_not_affected(self):
        batch = self._batch('LOT-0001', self.today + datetime.timedelta(days=1))
        sync_expired_batches()
        batch.refresh_from_db()
        self.assertEqual(batch.status, Batch.Status.ACTIVE)

    def test_TC_INV_EXP_004_expiring_soon_includes_within_window_excludes_beyond(self):
        near = self._batch('LOT-NEAR', self.today + datetime.timedelta(days=10))
        far = self._batch('LOT-FAR', self.today + datetime.timedelta(days=60))
        result = list(expiring_soon_batches(days=30))
        self.assertIn(near, result)
        self.assertNotIn(far, result)

    def test_TC_INV_EXP_005_expiring_soon_excludes_quarantine_status(self):
        quarantine = self._batch(
            'LOT-Q', self.today + datetime.timedelta(days=5), status=Batch.Status.QUARANTINE)
        result = list(expiring_soon_batches(days=30))
        self.assertNotIn(quarantine, result)


class FifoSuggestionServiceTest(TestCase):
    """``suggest_fifo_batches`` (FR-INV-04/FR-GIN-02) — DoD Phase 3, phần dễ
    sai nhất theo BACKLOG, bắt buộc test riêng cho từng edge case.
    ``TC-INV-FIFO-<seq>``.
    """

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.today = timezone.now().date()

    def _batch(self, code, exp_date, qty, status=Batch.Status.ACTIVE):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier, location=self.location,
            qty_received=qty, exp_date=exp_date, status=status,
        )

    def test_TC_INV_FIFO_001_single_batch_covers_qty_needed(self):
        batch = self._batch('LOT-0001', self.today + datetime.timedelta(days=10), 100)
        plan = suggest_fifo_batches(self.product, self.warehouse, 60)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]['batch'], batch)
        self.assertEqual(plan[0]['qty_to_issue'], 60)

    def test_TC_INV_FIFO_002_orders_by_exp_date_ascending(self):
        far = self._batch('LOT-FAR', self.today + datetime.timedelta(days=30), 50)
        near = self._batch('LOT-NEAR', self.today + datetime.timedelta(days=5), 50)
        plan = suggest_fifo_batches(self.product, self.warehouse, 10)
        self.assertEqual(plan[0]['batch'], near)
        self.assertNotEqual(plan[0]['batch'], far)

    def test_TC_INV_FIFO_003_splits_across_multiple_batches_when_one_not_enough(self):
        batch1 = self._batch('LOT-0001', self.today + datetime.timedelta(days=5), 30)
        batch2 = self._batch('LOT-0002', self.today + datetime.timedelta(days=10), 100)
        plan = suggest_fifo_batches(self.product, self.warehouse, 50)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[0]['batch'], batch1)
        self.assertEqual(plan[0]['qty_to_issue'], 30)
        self.assertEqual(plan[1]['batch'], batch2)
        self.assertEqual(plan[1]['qty_to_issue'], 20)
        self.assertEqual(sum(p['qty_to_issue'] for p in plan), 50)

    def test_TC_INV_FIFO_004_insufficient_total_stock_raises_validation_error(self):
        self._batch('LOT-0001', self.today + datetime.timedelta(days=5), 10)
        with self.assertRaises(ValidationError):
            suggest_fifo_batches(self.product, self.warehouse, 999)

    def test_TC_INV_FIFO_005_excludes_quarantine_and_expired_batches(self):
        self._batch('LOT-Q', self.today + datetime.timedelta(days=5), 100, status=Batch.Status.QUARANTINE)
        self._batch('LOT-EXP', self.today - datetime.timedelta(days=1), 100, status=Batch.Status.ACTIVE)
        active = self._batch('LOT-OK', self.today + datetime.timedelta(days=5), 20)

        plan = suggest_fifo_batches(self.product, self.warehouse, 20)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]['batch'], active)
        with self.assertRaises(ValidationError):
            suggest_fifo_batches(self.product, self.warehouse, 21)

    def test_TC_INV_FIFO_006_zero_qty_needed_rejected(self):
        with self.assertRaises(ValidationError):
            suggest_fifo_batches(self.product, self.warehouse, 0)


class InventoryDashboardViewTest(TestCase):
    """``inventory_list`` (mục 3a): FR-WM-03 tồn real-time + FR-WM-04/05 cảnh
    báo Min/Max Level. ``TC-INV-DASH-<seq>``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.other_warehouse = Warehouse.objects.create(code='KHO-SG', name='Kho Sài Gòn')
        self.client.force_login(self.staff)

    def test_TC_INV_DASH_001_login_required(self):
        self.client.logout()
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertEqual(response.status_code, 302)

    def test_TC_INV_DASH_002_flags_row_below_min_level(self):
        product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg', min_level=50, max_level=200)
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertEqual(response.context['below_min_count'], 1)
        self.assertEqual(response.context['above_max_count'], 0)
        self.assertTrue(response.context['rows'][0]['below_min'])

    def test_TC_INV_DASH_003_flags_row_above_max_level(self):
        product = Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg', min_level=10, max_level=100)
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=500)
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertEqual(response.context['above_max_count'], 1)
        self.assertTrue(response.context['rows'][0]['above_max'])

    def test_TC_INV_DASH_004_row_within_range_not_flagged(self):
        product = Product.objects.create(product_code='NVL-0003', name='Muối', uom='kg', min_level=10, max_level=100)
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=50)
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertEqual(response.context['below_min_count'], 0)
        self.assertEqual(response.context['above_max_count'], 0)

    def test_TC_INV_DASH_005_no_level_configured_never_flagged(self):
        product = Product.objects.create(product_code='NVL-0004', name='Tiêu', uom='kg')
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=0)
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertEqual(response.context['below_min_count'], 0)
        self.assertEqual(response.context['above_max_count'], 0)

    def test_TC_INV_DASH_006_filter_by_warehouse(self):
        product = Product.objects.create(product_code='NVL-0005', name='Ớt', uom='kg')
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)
        Inventory.objects.create(product=product, warehouse=self.other_warehouse, qty_on_hand=20)
        response = self.client.get(reverse('inventory:inventory_list'), {'warehouse': self.warehouse.pk})
        self.assertEqual(len(response.context['rows']), 1)
        self.assertEqual(response.context['rows'][0]['inventory'].warehouse, self.warehouse)


class BatchViewTest(TestCase):
    """``batch_list``/``batch_detail`` (mục 3a): FR-INV-01 quản lý lô hàng +
    FR-INV-02 cảnh báo sắp hết hạn. ``TC-INV-BATCH-<seq>``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.other_warehouse = Warehouse.objects.create(code='KHO-SG', name='Kho Sài Gòn')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.other_location = Location.objects.create(warehouse=self.other_warehouse, code='B-01')
        self.today = timezone.now().date()
        self.client.force_login(self.staff)

    def _batch(self, code, exp_date=None, status=Batch.Status.ACTIVE, qty=50, location=None):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier,
            location=location or self.location, qty_received=qty, exp_date=exp_date, status=status,
        )

    def test_TC_INV_BATCH_001_list_login_required(self):
        self.client.logout()
        response = self.client.get(reverse('inventory:batch_list'))
        self.assertEqual(response.status_code, 302)

    def test_TC_INV_BATCH_002_detail_login_required(self):
        batch = self._batch('LOT-0001')
        self.client.logout()
        response = self.client.get(reverse('inventory:batch_detail', args=[batch.pk]))
        self.assertEqual(response.status_code, 302)

    def test_TC_INV_BATCH_003_detail_404_for_missing_batch(self):
        response = self.client.get(reverse('inventory:batch_detail', args=[9999]))
        self.assertEqual(response.status_code, 404)

    def test_TC_INV_BATCH_004_list_flags_expiring_soon_batch(self):
        near = self._batch('LOT-NEAR', self.today + datetime.timedelta(days=10))
        far = self._batch('LOT-FAR', self.today + datetime.timedelta(days=60))
        response = self.client.get(reverse('inventory:batch_list'))
        self.assertEqual(response.context['expiring_count'], 1)
        self.assertIn(near.pk, response.context['expiring_ids'])
        self.assertNotIn(far.pk, response.context['expiring_ids'])

    def test_TC_INV_BATCH_005_list_filter_by_warehouse(self):
        self._batch('LOT-HN', location=self.location)
        self._batch('LOT-SG', location=self.other_location)
        response = self.client.get(reverse('inventory:batch_list'), {'warehouse': self.warehouse.pk})
        codes = [b.batch_code for b in response.context['batches']]
        self.assertEqual(codes, ['LOT-HN'])

    def test_TC_INV_BATCH_006_list_filter_by_status(self):
        self._batch('LOT-ACTIVE', status=Batch.Status.ACTIVE)
        self._batch('LOT-QUARANTINE', status=Batch.Status.QUARANTINE)
        response = self.client.get(reverse('inventory:batch_list'), {'status': Batch.Status.QUARANTINE})
        codes = [b.batch_code for b in response.context['batches']]
        self.assertEqual(codes, ['LOT-QUARANTINE'])

    def test_TC_INV_BATCH_007_detail_flags_expiring_soon(self):
        batch = self._batch('LOT-NEAR', self.today + datetime.timedelta(days=5))
        response = self.client.get(reverse('inventory:batch_detail', args=[batch.pk]))
        self.assertTrue(response.context['is_expiring_soon'])

    def test_TC_INV_BATCH_008_detail_not_flagged_when_far_from_expiry(self):
        batch = self._batch('LOT-FAR', self.today + datetime.timedelta(days=90))
        response = self.client.get(reverse('inventory:batch_detail', args=[batch.pk]))
        self.assertFalse(response.context['is_expiring_soon'])

    def test_TC_INV_BATCH_009_detail_shows_related_movements(self):
        batch = self._batch('LOT-0001')
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=50)
        movement = record_movement(
            product=self.product, warehouse=self.warehouse,
            movement_type=StockMovement.MovementType.RECEIPT, qty=50, batch=batch, reference='GRN-TEST',
        )
        response = self.client.get(reverse('inventory:batch_detail', args=[batch.pk]))
        self.assertIn(movement, response.context['movements'])


class StockTransferServiceTest(TestCase):
    """``transfer_stock`` (FR-WM-06) — điều chuyển tồn kho cùng kho (đổi vị
    trí) và khác kho (cập nhật Inventory 2 đầu). ``TC-INV-TRF-<seq>``.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.other_warehouse = Warehouse.objects.create(code='KHO-SG', name='Kho Sài Gòn')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.location2 = Location.objects.create(warehouse=self.warehouse, code='A-02')
        self.other_location = Location.objects.create(warehouse=self.other_warehouse, code='B-01')
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)

    def _batch(self, code='LOT-0001', qty=100, status=Batch.Status.ACTIVE, location=None):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier,
            location=location or self.location, qty_received=qty, status=status,
        )

    def test_TC_INV_TRF_001_transfer_no_auto_generated_with_prefix(self):
        batch = self._batch()
        transfer = transfer_stock(batch=batch, to_location=self.location2, qty=20, actor=self.user)
        self.assertTrue(transfer.transfer_no.startswith('TRF-'))

    def test_TC_INV_TRF_002_same_warehouse_full_qty_closes_source_creates_new_batch(self):
        batch = self._batch(qty=30)
        transfer = transfer_stock(batch=batch, to_location=self.location2, qty=30, actor=self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.status, Batch.Status.CLOSED)
        self.assertEqual(batch.qty_available, 0)
        self.assertEqual(transfer.new_batch.status, Batch.Status.ACTIVE)
        self.assertEqual(transfer.new_batch.qty_received, 30)
        self.assertEqual(transfer.new_batch.location, self.location2)

    def test_TC_INV_TRF_003_same_warehouse_partial_qty_leaves_source_partial_used(self):
        batch = self._batch(qty=100)
        transfer_stock(batch=batch, to_location=self.location2, qty=40, actor=self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.status, Batch.Status.PARTIAL_USED)
        self.assertEqual(batch.qty_available, 60)

    def test_TC_INV_TRF_004_same_warehouse_transfer_does_not_change_inventory_or_movement(self):
        batch = self._batch(qty=100)
        transfer_stock(batch=batch, to_location=self.location2, qty=40, actor=self.user)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 100)
        self.assertFalse(StockMovement.objects.exists())

    def test_TC_INV_TRF_005_cross_warehouse_debits_source_credits_destination_inventory(self):
        batch = self._batch(qty=100)
        transfer_stock(batch=batch, to_location=self.other_location, qty=30, actor=self.user)
        src_inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        dst_inv = Inventory.objects.get(product=self.product, warehouse=self.other_warehouse)
        self.assertEqual(src_inv.qty_on_hand, 70)
        self.assertEqual(dst_inv.qty_on_hand, 30)

    def test_TC_INV_TRF_006_cross_warehouse_creates_transfer_out_and_transfer_in_movements(self):
        batch = self._batch(qty=100)
        transfer = transfer_stock(batch=batch, to_location=self.other_location, qty=30, actor=self.user)
        out_move = StockMovement.objects.get(movement_type=StockMovement.MovementType.TRANSFER_OUT)
        in_move = StockMovement.objects.get(movement_type=StockMovement.MovementType.TRANSFER_IN)
        self.assertEqual(out_move.qty, -30)
        self.assertEqual(out_move.warehouse, self.warehouse)
        self.assertEqual(in_move.qty, 30)
        self.assertEqual(in_move.warehouse, self.other_warehouse)
        self.assertEqual(out_move.reference, transfer.transfer_no)
        self.assertEqual(in_move.reference, transfer.transfer_no)

    def test_TC_INV_TRF_007_cross_warehouse_creates_destination_inventory_if_missing(self):
        self.assertFalse(Inventory.objects.filter(warehouse=self.other_warehouse).exists())
        batch = self._batch(qty=100)
        transfer_stock(batch=batch, to_location=self.other_location, qty=30, actor=self.user)
        self.assertTrue(Inventory.objects.filter(product=self.product, warehouse=self.other_warehouse).exists())

    def test_TC_INV_TRF_008_qty_exceeding_available_raises_validation_error(self):
        batch = self._batch(qty=10)
        with self.assertRaises(ValidationError):
            transfer_stock(batch=batch, to_location=self.location2, qty=11, actor=self.user)

    def test_TC_INV_TRF_009_same_location_raises_validation_error(self):
        batch = self._batch(qty=10)
        with self.assertRaises(ValidationError):
            transfer_stock(batch=batch, to_location=self.location, qty=5, actor=self.user)

    def test_TC_INV_TRF_010_quarantine_batch_rejected(self):
        batch = self._batch(qty=10, status=Batch.Status.QUARANTINE)
        with self.assertRaises(ValidationError):
            transfer_stock(batch=batch, to_location=self.location2, qty=5, actor=self.user)

    def test_TC_INV_TRF_011_expired_batch_rejected(self):
        batch = self._batch(qty=10, status=Batch.Status.EXPIRED)
        with self.assertRaises(ValidationError):
            transfer_stock(batch=batch, to_location=self.location2, qty=5, actor=self.user)

    def test_TC_INV_TRF_012_inactive_location_rejected(self):
        self.location2.is_active = False
        self.location2.save(update_fields=['is_active'])
        batch = self._batch(qty=10)
        with self.assertRaises(ValidationError):
            transfer_stock(batch=batch, to_location=self.location2, qty=5, actor=self.user)

    def test_TC_INV_TRF_013_new_batch_code_derived_from_source_with_transfer_suffix(self):
        batch = self._batch(code='LOT-0001', qty=10)
        transfer = transfer_stock(batch=batch, to_location=self.location2, qty=5, actor=self.user)
        self.assertEqual(transfer.new_batch.batch_code, 'LOT-0001-T1')

    def test_TC_INV_TRF_014_creates_audit_log_entry(self):
        batch = self._batch(qty=10)
        transfer = transfer_stock(batch=batch, to_location=self.location2, qty=5, actor=self.user)
        self.assertTrue(
            AuditLog.objects.filter(target_id=str(transfer.pk), action=AuditLog.Action.CREATE).exists()
        )


class StockTransferViewTest(TestCase):
    """``transfer_create``/``transfer_list`` (FR-WM-06). ``TC-INV-TRF-VIEW-<seq>``."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.location2 = Location.objects.create(warehouse=self.warehouse, code='A-02')
        self.batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier,
            location=self.location, qty_received=50,
        )
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=50)
        self.client.force_login(self.staff)

    def test_TC_INV_TRF_VIEW_001_create_login_required(self):
        self.client.logout()
        response = self.client.get(reverse('inventory:transfer_create'))
        self.assertEqual(response.status_code, 302)

    def test_TC_INV_TRF_VIEW_002_list_login_required(self):
        self.client.logout()
        response = self.client.get(reverse('inventory:transfer_list'))
        self.assertEqual(response.status_code, 302)

    def test_TC_INV_TRF_VIEW_003_get_create_prefills_batch_from_query_param(self):
        response = self.client.get(reverse('inventory:transfer_create'), {'batch': self.batch.pk})
        self.assertEqual(response.context['form'].initial.get('batch'), str(self.batch.pk))

    def test_TC_INV_TRF_VIEW_004_post_valid_creates_transfer_and_redirects(self):
        response = self.client.post(reverse('inventory:transfer_create'), {
            'batch': self.batch.pk, 'to_location': self.location2.pk, 'qty': 20, 'note': 'Sắp xếp lại kho',
        })
        self.assertRedirects(response, reverse('inventory:transfer_list'))
        self.assertEqual(StockTransfer.objects.count(), 1)
        self.assertEqual(StockTransfer.objects.first().qty, 20)

    def test_TC_INV_TRF_VIEW_005_post_invalid_qty_shows_error_and_rerenders_form(self):
        response = self.client.post(reverse('inventory:transfer_create'), {
            'batch': self.batch.pk, 'to_location': self.location2.pk, 'qty': 999, 'note': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(StockTransfer.objects.count(), 0)
        messages = list(response.context['messages'])
        self.assertTrue(any('không đủ' in str(m) for m in messages))

    def test_TC_INV_TRF_VIEW_006_list_shows_created_transfer(self):
        transfer_stock(batch=self.batch, to_location=self.location2, qty=10, actor=self.staff)
        response = self.client.get(reverse('inventory:transfer_list'))
        self.assertContains(response, self.location2.code)


class InventoryListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (warehouse/tìm kiếm) trên inventory_list."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.client.force_login(self.staff)
        Inventory.objects.bulk_create([
            Inventory(
                product=Product.objects.create(product_code=f'NVL-{i:04d}', name=f'SP {i}', uom='kg'),
                warehouse=self.warehouse, qty_on_hand=10,
            )
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertEqual(len(response.context['rows']), 30)

    def test_page_size_50_shows_all(self):
        response = self.client.get(reverse('inventory:inventory_list'), {'page_size': 50})
        self.assertEqual(len(response.context['rows']), 35)

    def test_filter_search_by_product_code(self):
        response = self.client.get(reverse('inventory:inventory_list'), {'q': 'NVL-0001'})
        codes = [r['inventory'].product.product_code for r in response.context['rows']]
        self.assertEqual(codes, ['NVL-0001'])


class BatchListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (tìm kiếm) trên batch_list — dropdown warehouse/status đã có test riêng."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.client.force_login(self.staff)
        Batch.objects.bulk_create([
            Batch(
                product=self.product, batch_code=f'LOT-{i:04d}', supplier=self.supplier,
                location=self.location, qty_received=10,
            )
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('inventory:batch_list'))
        self.assertEqual(len(response.context['batches']), 30)

    def test_page_size_50_shows_all(self):
        response = self.client.get(reverse('inventory:batch_list'), {'page_size': 50})
        self.assertEqual(len(response.context['batches']), 35)

    def test_filter_search_by_batch_code(self):
        response = self.client.get(reverse('inventory:batch_list'), {'q': 'LOT-0001'})
        codes = [b.batch_code for b in response.context['batches']]
        self.assertEqual(codes, ['LOT-0001'])


class TransferListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (warehouse/tìm kiếm) trên transfer_list."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.other_warehouse = Warehouse.objects.create(code='KHO-SG', name='Kho Sài Gòn')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.location2 = Location.objects.create(warehouse=self.warehouse, code='A-02')
        self.other_location = Location.objects.create(warehouse=self.other_warehouse, code='B-01')
        self.batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier,
            location=self.location, qty_received=1000,
        )
        self.client.force_login(self.staff)
        StockTransfer.objects.bulk_create([
            StockTransfer(
                transfer_no=f'TRF-TEST-{i:04d}', batch=self.batch,
                from_location=self.location if i % 2 == 0 else self.other_location,
                to_location=self.location2, qty=1, created_by=self.staff,
            )
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('inventory:transfer_list'))
        self.assertEqual(len(response.context['transfers']), 30)

    def test_page_size_50_shows_all(self):
        response = self.client.get(reverse('inventory:transfer_list'), {'page_size': 50})
        self.assertEqual(len(response.context['transfers']), 35)

    def test_filter_warehouse(self):
        response = self.client.get(
            reverse('inventory:transfer_list'), {'warehouse': self.warehouse.pk, 'page_size': 50})
        self.assertTrue(all(t.from_location_id == self.location.pk for t in response.context['transfers']))

    def test_filter_search_by_transfer_no(self):
        response = self.client.get(reverse('inventory:transfer_list'), {'q': 'TRF-TEST-0001'})
        transfer_nos = [t.transfer_no for t in response.context['transfers']]
        self.assertEqual(transfer_nos, ['TRF-TEST-0001'])


class EoqServiceTest(TestCase):
    """``calculate_eoq`` (FR-INV-05). ``TC-INV-EOQ-<seq>``."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')

    def _issue_movement(self, qty, created_at=None):
        movement = StockMovement.objects.create(
            product=self.product, warehouse=self.warehouse,
            movement_type=StockMovement.MovementType.ISSUE, qty=-qty, qty_on_hand_after=0,
        )
        if created_at is not None:
            StockMovement.objects.filter(pk=movement.pk).update(created_at=created_at)
        return movement

    def _po_item(self, unit_price):
        po = PurchaseOrder.objects.create(po_no=f'PO-{PurchaseOrder.objects.count() + 1:04d}', supplier=self.supplier)
        return PurchaseOrderItem.objects.create(
            purchase_order=po, product=self.product, qty_ordered=10, unit_price=unit_price)

    def test_TC_INV_EOQ_001_missing_all_data_lists_every_reason(self):
        result = calculate_eoq(self.product)
        self.assertIsNone(result['eoq'])
        self.assertEqual(len(result['missing']), 4)

    def test_TC_INV_EOQ_002_computes_eoq_when_data_complete(self):
        self.product.ordering_cost = 100000
        self.product.holding_cost_rate = 20
        self.product.save()
        self._po_item(50)
        self._issue_movement(1200)

        result = calculate_eoq(self.product)
        self.assertEqual(result['annual_demand'], 1200)
        self.assertEqual(result['missing'], [])
        # D=1200, S=100000, đơn giá=50, H=50*20%=10 -> EOQ=sqrt(2*1200*100000/10)=4899 (làm tròn)
        self.assertEqual(result['eoq'], 4899)

    def test_TC_INV_EOQ_003_excludes_issue_movements_older_than_365_days(self):
        self.product.ordering_cost = 100000
        self.product.holding_cost_rate = 20
        self.product.save()
        self._po_item(50)
        self._issue_movement(1200)
        self._issue_movement(500, created_at=timezone.now() - datetime.timedelta(days=400))

        result = calculate_eoq(self.product)
        self.assertEqual(result['annual_demand'], 1200)

    def test_TC_INV_EOQ_004_missing_ordering_cost_only(self):
        self.product.holding_cost_rate = 20
        self.product.save()
        self._po_item(50)
        self._issue_movement(1200)

        result = calculate_eoq(self.product)
        self.assertIsNone(result['eoq'])
        self.assertEqual(len(result['missing']), 1)
        self.assertIn('Chi phí đặt hàng', result['missing'][0])


class EoqViewTest(TestCase):
    """View ``product_eoq`` (FR-INV-05). ``TC-INV-EOQ-VIEW-<seq>``."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')

    def test_TC_INV_EOQ_VIEW_001_login_required(self):
        response = self.client.get(reverse('inventory:product_eoq', args=[self.product.pk]))
        self.assertEqual(response.status_code, 302)

    def test_TC_INV_EOQ_VIEW_002_shows_missing_data_warning(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('inventory:product_eoq', args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Chưa đủ dữ liệu')
