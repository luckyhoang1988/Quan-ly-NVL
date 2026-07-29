import datetime

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog
from catalog.models import Product
from partners.models import Supplier
from purchasing.models import PurchaseOrder, PurchaseOrderItem
from quality.models import QcInspection
from receiving.models import Grn
from warehouse.models import Location, Warehouse

from .admin import BatchAdmin, InventoryAdmin, StockMovementAdmin, StockTransferAdmin
from .forms import StockTransferForm
from .models import Batch, Inventory, StockMovement, StockTransfer, WarehouseHandoff
from .services import (
    accept_handoff, calculate_eoq, expiring_soon_batches, move_batch_qty, record_movement, reject_handoff,
    stale_quarantine_batches, suggest_fifo_batches, sync_expired_batches, transfer_stock,
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

    def test_TC_INV_001_004_qty_reserved_exceeds_on_hand_rejected_by_db_constraint(self):
        """BUG-09: CheckConstraint chặn ở tầng DB — phòng khi có đường ghi nào
        (Admin, shell, script...) bỏ qua validate ở tầng service/form."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Inventory.objects.create(
                    product=self.product, warehouse=self.warehouse, qty_on_hand=10, qty_reserved=20)


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

    def test_TC_INV_002_005_qty_used_exceeds_received_rejected_by_db_constraint(self):
        """BUG-09: CheckConstraint chặn ở tầng DB — phòng khi có đường ghi nào
        (Admin, shell, script...) bỏ qua validate ở tầng service/form."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Batch.objects.create(
                    product=self.product, batch_code='LOT-0002', supplier=self.supplier,
                    location=self.location, qty_received=10, qty_used=20,
                )


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

    def test_TC_INV_EXP_006_partial_used_batch_past_exp_date_becomes_expired(self):
        """Bug fix 2026-07-27: lô PARTIAL_USED (còn qty_available > 0) hết hạn
        phải chuyển EXPIRED giống ACTIVE — trước đó filter chỉ quét ACTIVE nên
        lô PARTIAL_USED hết hạn không bao giờ bị chặn khỏi FIFO."""
        batch = self._batch('LOT-0001', self.today - datetime.timedelta(days=1), status=Batch.Status.PARTIAL_USED)
        count = sync_expired_batches()
        batch.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(batch.status, Batch.Status.EXPIRED)

    def test_TC_INV_EXP_007_active_batch_expiry_creates_audit_log(self):
        """BUG-11: chuyển ACTIVE -> EXPIRED phải ghi AuditLog, không được bỏ
        qua qua ``.update()`` hàng loạt."""
        batch = self._batch('LOT-0001', self.today - datetime.timedelta(days=1))
        sync_expired_batches()
        log = AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE,
            target_type__model='batch', target_id=str(batch.pk),
        ).first()
        self.assertIsNotNone(log)
        self.assertIsNone(log.actor)
        self.assertEqual(log.changes, {'status': [Batch.Status.ACTIVE, Batch.Status.EXPIRED]})

    def test_TC_INV_EXP_008_partial_used_batch_expiry_creates_audit_log(self):
        batch = self._batch('LOT-0001', self.today - datetime.timedelta(days=1), status=Batch.Status.PARTIAL_USED)
        sync_expired_batches()
        log = AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE,
            target_type__model='batch', target_id=str(batch.pk),
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.changes, {'status': [Batch.Status.PARTIAL_USED, Batch.Status.EXPIRED]})

    def test_TC_INV_EXP_009_non_expired_batches_get_no_audit_log(self):
        self._batch('LOT-0001', self.today + datetime.timedelta(days=1))
        sync_expired_batches()
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.UPDATE, target_type__model='batch').exists())

    def test_TC_INV_EXP_010_running_twice_does_not_duplicate_audit_log(self):
        batch = self._batch('LOT-0001', self.today - datetime.timedelta(days=1))
        sync_expired_batches()
        count_second_run = sync_expired_batches()
        self.assertEqual(count_second_run, 0, 'batch đã EXPIRED rồi thì lần 2 không quét lại')
        logs = AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE, target_type__model='batch', target_id=str(batch.pk))
        self.assertEqual(logs.count(), 1)

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

    def test_TC_INV_EXP_007_expiring_soon_includes_partial_used_status(self):
        """Bug fix 2026-07-27: lô PARTIAL_USED còn tồn vẫn phải lên cảnh báo
        sắp hết hạn, không chỉ ACTIVE."""
        partial = self._batch(
            'LOT-P', self.today + datetime.timedelta(days=5), status=Batch.Status.PARTIAL_USED)
        result = list(expiring_soon_batches(days=30))
        self.assertIn(partial, result)


class StaleQuarantineBatchesTest(TestCase):
    """``stale_quarantine_batches`` (BACKLOG mục 2c "Quarantine batch" — alert
    quarantine > 7 ngày, alert-only, không có scrap/return/rework tự động).
    ``TC-INV-QTN-<seq>``.
    """

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')

    def _batch(self, code, status=Batch.Status.QUARANTINE, qty=50):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier, location=self.location,
            qty_received=qty, status=status,
        )

    def test_TC_INV_QTN_001_quarantine_batch_past_7_days_flagged(self):
        batch = self._batch('LOT-OLD')
        Batch.objects.filter(pk=batch.pk).update(created_at=timezone.now() - datetime.timedelta(days=8))
        result = list(stale_quarantine_batches())
        self.assertIn(batch, result)

    def test_TC_INV_QTN_002_quarantine_batch_within_7_days_not_flagged(self):
        batch = self._batch('LOT-NEW')
        result = list(stale_quarantine_batches())
        self.assertNotIn(batch, result)

    def test_TC_INV_QTN_003_active_batch_not_flagged_even_if_old(self):
        batch = self._batch('LOT-ACTIVE', status=Batch.Status.ACTIVE)
        Batch.objects.filter(pk=batch.pk).update(created_at=timezone.now() - datetime.timedelta(days=8))
        result = list(stale_quarantine_batches())
        self.assertNotIn(batch, result)


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

    def _batch(self, code, exp_date, qty, status=Batch.Status.ACTIVE, qty_used=0):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier, location=self.location,
            qty_received=qty, qty_used=qty_used, exp_date=exp_date, status=status,
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

    def test_TC_INV_FIFO_008_includes_partial_used_batch_with_remaining_qty(self):
        """Bug fix 2026-07-27: batch PARTIAL_USED (đã xuất một phần, còn
        qty_available > 0) phải vẫn được FIFO gợi ý tiếp — trước đó filter chỉ
        lấy status=ACTIVE nên phần tồn còn lại của lô PARTIAL_USED bị "kẹt"
        vĩnh viễn, không bao giờ xuất được nữa dù Inventory.qty_on_hand vẫn còn."""
        partial = self._batch(
            'LOT-PARTIAL', self.today + datetime.timedelta(days=5), 50,
            status=Batch.Status.PARTIAL_USED, qty_used=30)
        self.assertEqual(partial.qty_available, 20)

        plan = suggest_fifo_batches(self.product, self.warehouse, 15)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]['batch'], partial)
        self.assertEqual(plan[0]['qty_to_issue'], 15)
        with self.assertRaises(ValidationError):
            suggest_fifo_batches(self.product, self.warehouse, 21)

    def test_TC_INV_FIFO_007_ignores_active_batch_at_staging_warehouse(self):
        """M5: batch ACTIVE ở Kho chờ (cùng product) không bao giờ được gợi ý
        cho kho MAIN, dù đủ trạng thái ACTIVE và đủ số lượng — suggest_fifo_batches
        lọc theo đúng ``location__warehouse`` được truyền vào (kho MAIN của GIN)."""
        staging_warehouse = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        staging_location = Location.objects.create(warehouse=staging_warehouse, code='A-01')
        Batch.objects.create(
            product=self.product, batch_code='LOT-STG', supplier=self.supplier, location=staging_location,
            qty_received=100, exp_date=self.today + datetime.timedelta(days=5), status=Batch.Status.ACTIVE,
        )
        main_batch = self._batch('LOT-MAIN', self.today + datetime.timedelta(days=10), 20)

        plan = suggest_fifo_batches(self.product, self.warehouse, 20)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]['batch'], main_batch)
        with self.assertRaises(ValidationError):
            suggest_fifo_batches(self.product, self.warehouse, 21)


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

    def test_TC_INV_DASH_007_staging_row_below_min_not_flagged(self):
        """M6: row ở Kho chờ dưới min_level vẫn hiển thị (không bị lọc khỏi danh
        sách) nhưng không được set below_min/suggested_po_qty — hàng chưa qua QC
        không phải "tồn khả dụng" đúng nghĩa Min/Max. Row MAIN tương đương thì có."""
        staging = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        product = Product.objects.create(product_code='NVL-0006', name='Gừng', uom='kg', min_level=50, max_level=200)
        Inventory.objects.create(product=product, warehouse=staging, qty_on_hand=10)
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)

        response = self.client.get(reverse('inventory:inventory_list'))
        rows_by_warehouse = {row['inventory'].warehouse_id: row for row in response.context['rows']}
        self.assertFalse(rows_by_warehouse[staging.pk]['below_min'])
        self.assertIsNone(rows_by_warehouse[staging.pk]['suggested_po_qty'])
        self.assertTrue(rows_by_warehouse[self.warehouse.pk]['below_min'])
        self.assertEqual(response.context['below_min_count'], 1)

    def test_TC_INV_DASH_008_below_min_link_shown_with_correct_href(self):
        """L3: link "Tạo yêu cầu mua hàng" (gợi ý PR khi dưới Min Level, thay lối
        tắt tạo PO thẳng đã bỏ — xem ``purchasing.views.pr_create`` docstring)
        hiện đúng khi ``can_create_pr`` và trỏ đúng ``product``/``qty``/``warehouse``
        querystring mà ``pr_create`` đọc để prefill dòng item + kho."""
        product = Product.objects.create(product_code='NVL-0007', name='Quế', uom='kg', min_level=50, max_level=200)
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertTrue(response.context['can_create_pr'])
        suggested_qty = response.context['rows'][0]['suggested_po_qty']
        self.assertGreater(suggested_qty, 0)
        expected_href = (
            f"{reverse('purchasing:pr_create')}?product={product.pk}"
            f"&qty={suggested_qty}&warehouse={self.warehouse.pk}"
        )
        self.assertContains(response, 'Tạo yêu cầu mua hàng')
        self.assertContains(response, expected_href)

    def test_TC_INV_DASH_009_below_min_link_hidden_without_pr_create_permission(self):
        """L3: user không có quyền ``create`` trên module ``pr`` (vd QC — chỉ
        có ``read``) không thấy link, dù row vẫn dưới Min Level."""
        User = get_user_model()
        qc_user = User.objects.create_user(username='qc1', password='qc-pass-123', role=User.Role.QC)
        product = Product.objects.create(product_code='NVL-0008', name='Hồi', uom='kg', min_level=50, max_level=200)
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)
        self.client.force_login(qc_user)
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertFalse(response.context['can_create_pr'])
        self.assertTrue(response.context['rows'][0]['below_min'])
        self.assertNotContains(response, 'Tạo yêu cầu mua hàng')

    def test_TC_INV_DASH_010_total_across_main_warehouses_not_below_min(self):
        """BUG-07: SKU tồn ở 2 kho MAIN, mỗi kho riêng lẻ dưới min_level nhưng
        tổng 2 kho đã đủ min_level -> không được báo dưới Min (trước fix, mỗi
        dòng so trực tiếp với qty_on_hand riêng của kho đó nên báo sai cả hai)."""
        product = Product.objects.create(product_code='NVL-0009', name='Nghệ', uom='kg', min_level=100)
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=60)
        Inventory.objects.create(product=product, warehouse=self.other_warehouse, qty_on_hand=60)
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertEqual(response.context['below_min_count'], 0)
        for row in response.context['rows']:
            self.assertFalse(row['below_min'])

    def test_TC_INV_DASH_011_total_across_main_warehouses_above_max(self):
        """BUG-07: tổng 2 kho MAIN vượt max_level dù từng kho riêng lẻ chưa
        vượt -> phải báo trên Max."""
        product = Product.objects.create(product_code='NVL-0010', name='Sả', uom='kg', max_level=100)
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=60)
        Inventory.objects.create(product=product, warehouse=self.other_warehouse, qty_on_hand=60)
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertEqual(response.context['above_max_count'], 1)
        for row in response.context['rows']:
            self.assertTrue(row['above_max'])

    def test_TC_INV_DASH_012_below_min_count_not_doubled_across_warehouses(self):
        """BUG-07: 1 SKU thực sự dưới Min nhưng có mặt ở 2 kho MAIN chỉ được
        đếm 1 lần trong below_min_count (không nhân theo số kho/số dòng)."""
        product = Product.objects.create(product_code='NVL-0011', name='Lá chanh', uom='kg', min_level=100)
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)
        Inventory.objects.create(product=product, warehouse=self.other_warehouse, qty_on_hand=10)
        response = self.client.get(reverse('inventory:inventory_list'))
        self.assertEqual(response.context['below_min_count'], 1)

    def test_TC_INV_DASH_013_staging_scrap_excluded_from_main_total(self):
        """BUG-07: tồn ở Kho chờ/Kho phế không được cộng vào tổng MAIN dùng để
        so Min Level của SKU."""
        staging = Warehouse.objects.create(
            code='KHO-CHO2', name='Kho chờ 2', warehouse_type=Warehouse.WarehouseType.STAGING)
        product = Product.objects.create(product_code='NVL-0012', name='Riềng', uom='kg', min_level=100)
        Inventory.objects.create(product=product, warehouse=staging, qty_on_hand=500)
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)
        response = self.client.get(reverse('inventory:inventory_list'))
        rows_by_warehouse = {row['inventory'].warehouse_id: row for row in response.context['rows']}
        self.assertTrue(rows_by_warehouse[self.warehouse.pk]['below_min'])
        self.assertEqual(response.context['below_min_count'], 1)


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

    def test_TC_INV_BATCH_010_list_flags_stale_quarantine_batch(self):
        old = self._batch('LOT-OLD', status=Batch.Status.QUARANTINE)
        Batch.objects.filter(pk=old.pk).update(created_at=timezone.now() - datetime.timedelta(days=8))
        new = self._batch('LOT-NEW', status=Batch.Status.QUARANTINE)
        response = self.client.get(reverse('inventory:batch_list'))
        self.assertEqual(response.context['stale_quarantine_count'], 1)
        self.assertIn(old.pk, response.context['stale_quarantine_ids'])
        self.assertNotIn(new.pk, response.context['stale_quarantine_ids'])

    def test_TC_INV_BATCH_011_detail_flags_stale_quarantine(self):
        batch = self._batch('LOT-OLD', status=Batch.Status.QUARANTINE)
        Batch.objects.filter(pk=batch.pk).update(created_at=timezone.now() - datetime.timedelta(days=8))
        response = self.client.get(reverse('inventory:batch_detail', args=[batch.pk]))
        self.assertTrue(response.context['is_stale_quarantine'])

    def test_TC_INV_BATCH_012_detail_not_flagged_when_quarantine_recent(self):
        batch = self._batch('LOT-NEW', status=Batch.Status.QUARANTINE)
        response = self.client.get(reverse('inventory:batch_detail', args=[batch.pk]))
        self.assertFalse(response.context['is_stale_quarantine'])

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

    def test_TC_INV_TRF_015_staging_source_rejected(self):
        """M3: batch đang ở Kho chờ (STAGING) không được điều chuyển thủ công — phải qua QC."""
        staging_warehouse = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        staging_location = Location.objects.create(warehouse=staging_warehouse, code='A-01')
        batch = self._batch(qty=10, location=staging_location)
        with self.assertRaises(ValidationError):
            transfer_stock(batch=batch, to_location=self.location2, qty=5, actor=self.user)

    def test_TC_INV_TRF_016_staging_destination_rejected(self):
        """Bug fix: điều chuyển tay vào Kho chờ (STAGING) phải bị chặn — hàng vào
        STAGING chỉ được nạp qua ``start_qc()``, không qua điều chuyển tay."""
        staging_warehouse = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        staging_location = Location.objects.create(warehouse=staging_warehouse, code='A-01')
        batch = self._batch(qty=10)
        with self.assertRaises(ValidationError):
            transfer_stock(batch=batch, to_location=staging_location, qty=5, actor=self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.status, Batch.Status.ACTIVE)
        self.assertEqual(batch.qty_available, 10)

    def test_TC_INV_TRF_017_scrap_destination_rejected(self):
        """Bug fix: điều chuyển tay vào Kho phế (SCRAP) phải bị chặn — hàng vào
        SCRAP chỉ được nạp qua QC (qc_fail/qc_partial_pass/reject_handoff)."""
        scrap_warehouse = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        scrap_location = Location.objects.create(warehouse=scrap_warehouse, code='A-01')
        batch = self._batch(qty=10)
        with self.assertRaises(ValidationError):
            transfer_stock(batch=batch, to_location=scrap_location, qty=5, actor=self.user)


class StockTransferPendingReceiptGuardTest(TestCase):
    """Bug fix (mục 6): ``transfer_stock`` chặn STAGING nhưng trước đây không
    chặn batch ``PENDING_RECEIPT`` có ``WarehouseHandoff`` còn PENDING — điều
    chuyển tay lúc đó đổi status batch nguồn (CLOSED/PARTIAL_USED) trong khi
    handoff vẫn trỏ vào nó, khiến ``accept_handoff``/``reject_handoff`` sau đó
    luôn fail (phiếu bàn giao kẹt vĩnh viễn). Vẫn phải cho phép điều chuyển
    tay khi handoff đã REJECTED với BACK_TO_QC (đường đi có chủ đích, xem
    ``reject_handoff()``). ``TC-INV-TRF-PR-<seq>``.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.qc_user = User.objects.create_user(username='qc1', password='qc-pass-123', role=User.Role.QC)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.location2 = Location.objects.create(warehouse=self.warehouse, code='A-02')
        self.scrap_warehouse = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        Location.objects.create(warehouse=self.scrap_warehouse, code='A-01')

        self.po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        self.grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.qc_user)
        self.inspection = QcInspection.objects.create(grn=self.grn, inspector=self.qc_user)

        self.batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=20, status=Batch.Status.PENDING_RECEIPT,
        )
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=20)
        self.handoff = WarehouseHandoff.objects.create(
            batch=self.batch, qc_inspection=self.inspection, destination_warehouse=self.warehouse,
        )

    def test_TC_INV_TRF_PR_001_pending_handoff_blocks_manual_transfer(self):
        with self.assertRaises(ValidationError):
            transfer_stock(batch=self.batch, to_location=self.location2, qty=5, actor=self.user)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, Batch.Status.PENDING_RECEIPT)

    def test_TC_INV_TRF_PR_002_handoff_still_decidable_after_blocked_transfer_attempt(self):
        with self.assertRaises(ValidationError):
            transfer_stock(batch=self.batch, to_location=self.location2, qty=5, actor=self.user)
        accept_handoff(self.handoff, actor=self.user)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, Batch.Status.ACTIVE)

    def test_TC_INV_TRF_PR_003_manual_transfer_allowed_after_back_to_qc_reject(self):
        reject_handoff(
            self.handoff, actor=self.qc_user, reason='Kiểm tra lại',
            destination=WarehouseHandoff.RejectDestination.BACK_TO_QC,
        )
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, Batch.Status.PENDING_RECEIPT)
        transfer = transfer_stock(batch=self.batch, to_location=self.location2, qty=5, actor=self.qc_user)
        self.assertEqual(transfer.new_batch.location, self.location2)

    def test_TC_INV_TRF_PR_004_form_offers_pending_receipt_batch_after_back_to_qc_reject(self):
        reject_handoff(
            self.handoff, actor=self.qc_user, reason='Kiểm tra lại',
            destination=WarehouseHandoff.RejectDestination.BACK_TO_QC,
        )
        form = StockTransferForm(data={
            'batch': self.batch.pk, 'to_location': self.location2.pk, 'qty': 5,
        })
        self.assertIn(self.batch, form.fields['batch'].queryset)
        self.assertTrue(form.is_valid(), form.errors)


class MoveBatchQtyServiceTest(TestCase):
    """``move_batch_qty`` (M3) — nguyên thủy tách batch dùng chung bởi
    ``transfer_stock`` và ``quality.services``. ``TC-INV-MBQ-<seq>``.
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

    def _batch(self, code='LOT-0001', qty=100, status=Batch.Status.ACTIVE, location=None, grn_item=None):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier,
            location=location or self.location, qty_received=qty, status=status, grn_item=grn_item,
        )

    def test_TC_INV_MBQ_001_same_warehouse_splits_batch_no_inventory_change(self):
        batch = self._batch(qty=100)
        new_batch = move_batch_qty(
            source_batch=batch, qty=40, to_location=self.location2,
            new_batch_code='LOT-0001-A', new_status=Batch.Status.ACTIVE, actor=self.user,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, Batch.Status.PARTIAL_USED)
        self.assertEqual(batch.qty_available, 60)
        self.assertEqual(new_batch.qty_received, 40)
        self.assertEqual(new_batch.location, self.location2)
        self.assertEqual(new_batch.status, Batch.Status.ACTIVE)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 100)
        self.assertFalse(StockMovement.objects.exists())

    def test_TC_INV_MBQ_002_cross_warehouse_updates_both_inventories_and_movements(self):
        batch = self._batch(qty=100)
        new_batch = move_batch_qty(
            source_batch=batch, qty=30, to_location=self.other_location,
            new_batch_code='LOT-0001-A', new_status=Batch.Status.QUARANTINE,
            actor=self.user, reference='QC-TEST-001',
        )
        src_inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        dst_inv = Inventory.objects.get(product=self.product, warehouse=self.other_warehouse)
        self.assertEqual(src_inv.qty_on_hand, 70)
        self.assertEqual(dst_inv.qty_on_hand, 30)
        self.assertEqual(new_batch.status, Batch.Status.QUARANTINE)
        out_move = StockMovement.objects.get(movement_type=StockMovement.MovementType.TRANSFER_OUT)
        in_move = StockMovement.objects.get(movement_type=StockMovement.MovementType.TRANSFER_IN)
        self.assertEqual(out_move.reference, 'QC-TEST-001')
        self.assertEqual(in_move.reference, 'QC-TEST-001')

    def test_TC_INV_MBQ_003_full_qty_closes_source_batch(self):
        batch = self._batch(qty=30)
        move_batch_qty(
            source_batch=batch, qty=30, to_location=self.location2,
            new_batch_code='LOT-0001-A', new_status=Batch.Status.ACTIVE, actor=self.user,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, Batch.Status.CLOSED)
        self.assertEqual(batch.qty_available, 0)

    def test_TC_INV_MBQ_004_copies_grn_item_lineage_to_new_batch(self):
        from purchasing.models import PurchaseOrder
        from receiving.models import Grn, GrnItem

        creator = User.objects.create_user(username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
        grn = Grn.objects.create(po=po, supplier=self.supplier, created_by=creator)
        grn_item = GrnItem.objects.create(
            grn=grn, product=self.product, qty_ordered=100, qty_received=100, unit_price='15000.00',
        )
        batch = self._batch(qty=100, grn_item=grn_item)
        new_batch = move_batch_qty(
            source_batch=batch, qty=40, to_location=self.location2,
            new_batch_code='LOT-0001-A', new_status=Batch.Status.ACTIVE, actor=self.user,
        )
        self.assertEqual(new_batch.grn_item, grn_item)

    def test_TC_INV_MBQ_005_batch_without_grn_item_stays_null(self):
        batch = self._batch(qty=100, grn_item=None)
        new_batch = move_batch_qty(
            source_batch=batch, qty=40, to_location=self.location2,
            new_batch_code='LOT-0001-A', new_status=Batch.Status.ACTIVE, actor=self.user,
        )
        self.assertIsNone(new_batch.grn_item)

    def test_TC_INV_MBQ_006_qty_exceeding_available_raises(self):
        batch = self._batch(qty=10)
        with self.assertRaises(ValidationError):
            move_batch_qty(
                source_batch=batch, qty=11, to_location=self.location2,
                new_batch_code='LOT-0001-A', new_status=Batch.Status.ACTIVE, actor=self.user,
            )

    def test_TC_INV_MBQ_007_quarantine_source_rejected(self):
        batch = self._batch(qty=10, status=Batch.Status.QUARANTINE)
        with self.assertRaises(ValidationError):
            move_batch_qty(
                source_batch=batch, qty=5, to_location=self.location2,
                new_batch_code='LOT-0001-A', new_status=Batch.Status.ACTIVE, actor=self.user,
            )

    def test_TC_INV_MBQ_008_inactive_destination_location_rejected(self):
        self.location2.is_active = False
        self.location2.save(update_fields=['is_active'])
        batch = self._batch(qty=10)
        with self.assertRaises(ValidationError):
            move_batch_qty(
                source_batch=batch, qty=5, to_location=self.location2,
                new_batch_code='LOT-0001-A', new_status=Batch.Status.ACTIVE, actor=self.user,
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

    def test_TC_INV_TRF_VIEW_007_qc_role_forbidden_from_create(self):
        """BUG-05: role QC không thuộc nhóm 'Kho' (ADMIN/MANAGER/STAFF) nên
        không được tự điều chuyển tồn kho vật lý, dù vẫn có quyền xem menu."""
        qc = User.objects.create_user(username='qc1', password='qc-pass-123', role=User.Role.QC)
        self.client.force_login(qc)
        response = self.client.post(reverse('inventory:transfer_create'), {
            'batch': self.batch.pk, 'to_location': self.location2.pk, 'qty': 5, 'note': '',
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(StockTransfer.objects.count(), 0)

    def test_TC_INV_TRF_VIEW_008_accountant_role_forbidden_from_create(self):
        """BUG-05."""
        accountant = User.objects.create_user(
            username='ketoan1', password='kt-pass-123', role=User.Role.ACCOUNTANT)
        self.client.force_login(accountant)
        response = self.client.post(reverse('inventory:transfer_create'), {
            'batch': self.batch.pk, 'to_location': self.location2.pk, 'qty': 5, 'note': '',
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(StockTransfer.objects.count(), 0)

    def test_TC_INV_TRF_VIEW_009_purchasing_role_forbidden_from_create(self):
        """BUG-05."""
        purchasing = User.objects.create_user(
            username='mh1', password='mh-pass-123', role=User.Role.PURCHASING)
        self.client.force_login(purchasing)
        response = self.client.get(reverse('inventory:transfer_create'))
        self.assertEqual(response.status_code, 403)

    def test_TC_INV_TRF_VIEW_010_menu_access_revoked_forbids_create_and_list(self):
        """BUG-05: thu hồi ``can_view_menu_inventory`` phải chặn được cả
        ``transfer_create`` lẫn ``transfer_list``, không chỉ ``inventory_list``."""
        perm = Permission.objects.get(
            codename='can_view_menu_inventory', content_type__app_label='accounts')
        self.staff.user_permissions.remove(perm)
        response = self.client.get(reverse('inventory:transfer_create'))
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse('inventory:transfer_list'))
        self.assertEqual(response.status_code, 403)

    def test_TC_INV_TRF_VIEW_011_manager_can_create(self):
        """BUG-05."""
        manager = User.objects.create_user(username='ql1', password='ql-pass-123', role=User.Role.MANAGER)
        self.client.force_login(manager)
        response = self.client.post(reverse('inventory:transfer_create'), {
            'batch': self.batch.pk, 'to_location': self.location2.pk, 'qty': 5, 'note': '',
        })
        self.assertRedirects(response, reverse('inventory:transfer_list'))

    def test_TC_INV_TRF_VIEW_012_menu_access_revoked_forbids_batch_and_eoq_views(self):
        """BUG-06: thu hồi ``can_view_menu_inventory`` phải chặn được cả
        ``batch_list``/``batch_detail``/``product_eoq``, không chỉ ``inventory_list``
        và ``transfer_create``/``transfer_list`` (đã sửa ở BUG-05)."""
        perm = Permission.objects.get(
            codename='can_view_menu_inventory', content_type__app_label='accounts')
        self.staff.user_permissions.remove(perm)
        response = self.client.get(reverse('inventory:batch_list'))
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse('inventory:batch_detail', args=[self.batch.pk]))
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse('inventory:product_eoq', args=[self.product.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_INV_TRF_VIEW_012_admin_can_create(self):
        """BUG-05."""
        admin = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.client.force_login(admin)
        response = self.client.post(reverse('inventory:transfer_create'), {
            'batch': self.batch.pk, 'to_location': self.location2.pk, 'qty': 5, 'note': '',
        })
        self.assertRedirects(response, reverse('inventory:transfer_list'))

    def test_TC_INV_TRF_VIEW_013_qc_can_still_view_transfer_list(self):
        """BUG-05: hạn chế ở ``transfer_create`` (ghi), không phải ``transfer_list``
        (xem) — QC vẫn xem được lịch sử điều chuyển như mọi mục inventory khác."""
        qc = User.objects.create_user(username='qc2', password='qc-pass-123', role=User.Role.QC)
        self.client.force_login(qc)
        response = self.client.get(reverse('inventory:transfer_list'))
        self.assertEqual(response.status_code, 200)


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


class WarehouseHandoffViewTest(TestCase):
    """Trang "Phiếu chờ nhận hàng" (Phase D, mục 6) — Nhận/Từ chối +
    phân quyền ``can_decide_handoff``. ``TC-INV-HANDOFF-VIEW-<seq>``.
    """

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.scrap_warehouse = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        Location.objects.create(warehouse=self.scrap_warehouse, code='A-01')

        self.qc_user = User.objects.create_user(username='qc1', password='qc-pass-123', role=User.Role.QC)
        self.po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        self.grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.qc_user)
        self.inspection = QcInspection.objects.create(grn=self.grn, inspector=self.qc_user)

        self.assigned_staff = User.objects.create_user(
            username='nvk-a', password='nvk-pass-123', role=User.Role.STAFF,
            department=User.Department.WAREHOUSE)
        self.other_staff = User.objects.create_user(
            username='nvk-b', password='nvk-pass-123', role=User.Role.STAFF,
            department=User.Department.WAREHOUSE)
        self.manager = User.objects.create_user(
            username='qlk', password='qlk-pass-123', role=User.Role.MANAGER,
            department=User.Department.WAREHOUSE, is_manager=True)
        self.outsider = User.objects.create_user(
            username='qc2', password='qc-pass-123', role=User.Role.QC, department=User.Department.QC)
        self.admin = User.objects.create_user(
            username='admin1', password='admin-pass-123', role=User.Role.ADMIN)

        self.batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=20, status=Batch.Status.PENDING_RECEIPT,
        )
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=20)
        self.handoff = WarehouseHandoff.objects.create(
            batch=self.batch, qc_inspection=self.inspection, destination_warehouse=self.warehouse,
            assigned_to=self.assigned_staff,
        )

    def test_TC_INV_HANDOFF_VIEW_000b_get_absolute_url_points_to_handoff_list(self):
        """M9: WarehouseHandoff không có trang detail riêng — Nhận/Từ chối làm
        ngay trên hàng đợi handoff_list, nên deep-link phải trỏ về đúng đó."""
        self.assertEqual(self.handoff.get_absolute_url(), reverse('inventory:handoff_list'))

    def test_TC_INV_HANDOFF_VIEW_001_login_required(self):
        response = self.client.get(reverse('inventory:handoff_list'))
        self.assertEqual(response.status_code, 302)

    def test_TC_INV_HANDOFF_VIEW_002_assigned_staff_sees_it_in_queue(self):
        self.client.force_login(self.assigned_staff)
        response = self.client.get(reverse('inventory:handoff_list'))
        self.assertContains(response, self.batch.batch_code)

    def test_TC_INV_HANDOFF_VIEW_003_unrelated_staff_does_not_see_it(self):
        self.client.force_login(self.other_staff)
        response = self.client.get(reverse('inventory:handoff_list'))
        self.assertNotContains(response, self.batch.batch_code)

    def test_TC_INV_HANDOFF_VIEW_004_manager_sees_all_pending(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('inventory:handoff_list'))
        self.assertContains(response, self.batch.batch_code)

    def test_TC_INV_HANDOFF_VIEW_005_assigned_staff_can_accept(self):
        self.client.force_login(self.assigned_staff)
        response = self.client.post(reverse('inventory:handoff_accept', args=[self.handoff.pk]))
        self.assertRedirects(response, reverse('inventory:handoff_list'))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, Batch.Status.ACTIVE)

    def test_TC_INV_HANDOFF_VIEW_006_unrelated_staff_forbidden_to_accept(self):
        self.client.force_login(self.other_staff)
        response = self.client.post(reverse('inventory:handoff_accept', args=[self.handoff.pk]))
        self.assertEqual(response.status_code, 403)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, Batch.Status.PENDING_RECEIPT)

    def test_TC_INV_HANDOFF_VIEW_007_manager_can_accept_even_if_not_assigned(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse('inventory:handoff_accept', args=[self.handoff.pk]))
        self.assertRedirects(response, reverse('inventory:handoff_list'))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, Batch.Status.ACTIVE)

    def test_TC_INV_HANDOFF_VIEW_008_outsider_department_forbidden(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('inventory:handoff_list'))
        self.assertNotContains(response, self.batch.batch_code)
        response = self.client.post(reverse('inventory:handoff_accept', args=[self.handoff.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_INV_HANDOFF_VIEW_009_reject_to_scrap_via_view(self):
        self.client.force_login(self.assigned_staff)
        response = self.client.post(reverse('inventory:handoff_reject', args=[self.handoff.pk]), {
            'reason': 'Hàng ẩm mốc', 'destination': WarehouseHandoff.RejectDestination.TO_SCRAP,
        })
        self.assertRedirects(response, reverse('inventory:handoff_list'))
        self.handoff.refresh_from_db()
        self.assertEqual(self.handoff.status, WarehouseHandoff.Status.REJECTED)

    def test_TC_INV_HANDOFF_VIEW_010_reject_without_reason_shows_error_message(self):
        self.client.force_login(self.assigned_staff)
        response = self.client.post(reverse('inventory:handoff_reject', args=[self.handoff.pk]), {
            'reason': '', 'destination': WarehouseHandoff.RejectDestination.TO_SCRAP,
        }, follow=True)
        self.handoff.refresh_from_db()
        self.assertEqual(self.handoff.status, WarehouseHandoff.Status.PENDING)
        messages = list(response.context['messages'])
        self.assertTrue(any('Bắt buộc nhập lý do' in str(m) for m in messages))

    def test_TC_INV_HANDOFF_VIEW_011_unassigned_handoff_visible_only_to_destination_warehouse_staff(self):
        self.handoff.assigned_to = None
        self.handoff.save(update_fields=['assigned_to'])
        self.warehouse.staff.add(self.other_staff)

        self.client.force_login(self.other_staff)
        response = self.client.get(reverse('inventory:handoff_list'))
        self.assertContains(response, self.batch.batch_code)

        # assigned_staff không thuộc Warehouse.staff của kho đích (đã gán cụ thể other_staff) -> không thấy.
        self.client.force_login(self.assigned_staff)
        response = self.client.get(reverse('inventory:handoff_list'))
        self.assertNotContains(response, self.batch.batch_code)

    def test_TC_INV_HANDOFF_VIEW_012_unassigned_handoff_falls_back_to_department_when_warehouse_staff_empty(self):
        self.handoff.assigned_to = None
        self.handoff.save(update_fields=['assigned_to'])
        # Warehouse.staff rỗng cho kho đích -> fallback toàn bộ department=WAREHOUSE.
        self.client.force_login(self.other_staff)
        response = self.client.get(reverse('inventory:handoff_list'))
        self.assertContains(response, self.batch.batch_code)

    def test_TC_INV_HANDOFF_VIEW_013_admin_sees_all_pending_despite_blank_department(self):
        # Admin không thuộc department nào (department để trống) nhưng vẫn phải
        # thấy toàn bộ hàng chờ, giống Manager phòng Kho — bug: trước đây
        # can_decide_handoff chỉ check is_department_manager(WAREHOUSE), không
        # có fallback role==ADMIN/is_superuser như GRN/GIN nên Admin thấy list rỗng.
        self.client.force_login(self.admin)
        response = self.client.get(reverse('inventory:handoff_list'))
        self.assertContains(response, self.batch.batch_code)

    def test_TC_INV_HANDOFF_VIEW_014_admin_can_accept_even_if_not_assigned(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('inventory:handoff_accept', args=[self.handoff.pk]))
        self.assertRedirects(response, reverse('inventory:handoff_list'))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, Batch.Status.ACTIVE)


class InventoryAdminReadOnlyTest(TestCase):
    """BUG-09 (2026-07-29): Inventory/Batch/StockMovement/StockTransfer chỉ
    được tạo/sửa qua service layer (``inventory.services``/
    ``quality.services``/``stocktake.services``...) — sửa trực tiếp qua Admin
    bỏ qua audit log và phá đồng bộ Batch<->Inventory<->StockMovement.
    ``TC-INV-ADM-<seq>``."""

    def test_TC_INV_ADM_001_inventory_admin_denies_add_change_delete(self):
        model_admin = InventoryAdmin(Inventory, admin.site)
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))

    def test_TC_INV_ADM_002_batch_admin_denies_add_change_delete(self):
        model_admin = BatchAdmin(Batch, admin.site)
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))

    def test_TC_INV_ADM_003_stock_movement_admin_denies_add_change_delete(self):
        model_admin = StockMovementAdmin(StockMovement, admin.site)
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))

    def test_TC_INV_ADM_004_stock_transfer_admin_denies_add_change_delete(self):
        model_admin = StockTransferAdmin(StockTransfer, admin.site)
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))
