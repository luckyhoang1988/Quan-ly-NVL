"""Unit test cho GIN (mục 3b, Phase 3) — DoD Phase 3 GIN checklist.

FIFO thuần thuật toán (multi-batch split, thứ tự exp_date, insufficient stock,
loại QUARANTINE/EXPIRED) đã có unit test riêng ở ``inventory.tests.
FifoSuggestionServiceTest`` (Task 1) — ở đây test tích hợp qua
``shipping.services`` (start_picking/override_allocation/issue_gin/close_gin)
và view/permission, không lặp lại test thuật toán FIFO thuần.
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

from .models import Gin, GinBatchAllocation, GinItem
from .services import close_gin, issue_gin, override_allocation, start_picking

User = get_user_model()


class GinModelTest(TestCase):
    """Gin auto-numbering + default status (mirror BR-GRN-001). ``TC-GIN-001-<seq>``."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')

    def _gin(self, **overrides):
        payload = dict(
            warehouse=self.warehouse, reference_type=Gin.ReferenceType.SALES, requested_by=self.manager)
        payload.update(overrides)
        return Gin.objects.create(**payload)

    def test_TC_GIN_001_001_gin_no_auto_generated_with_current_yyyymm_prefix(self):
        gin = self._gin()
        today = datetime.date.today()
        self.assertEqual(gin.gin_no, f'GIN-{today:%Y%m}-001')

    def test_TC_GIN_001_002_gin_no_sequence_increments_within_same_month(self):
        first = self._gin()
        second = self._gin()
        self.assertEqual(first.gin_no[:-3], second.gin_no[:-3])
        self.assertEqual(int(second.gin_no[-3:]), int(first.gin_no[-3:]) + 1)

    def test_TC_GIN_001_003_gin_no_not_overwritten_on_update(self):
        gin = self._gin()
        original_no = gin.gin_no
        gin.notes = 'cập nhật ghi chú'
        gin.save()
        gin.refresh_from_db()
        self.assertEqual(gin.gin_no, original_no)

    def test_TC_GIN_001_004_default_status_is_draft(self):
        gin = self._gin()
        self.assertEqual(gin.status, Gin.Status.DRAFT)


class GinPickingServiceTest(TestCase):
    """``start_picking`` (FR-GIN-01/FR-GIN-02): DRAFT -> PICKING, tạo allocation
    FIFO. ``TC-GIN-PICK-<seq>``.
    """

    def setUp(self):
        self.manager = User.objects.create_user(
            username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.today = timezone.now().date()

    def _batch(self, code, exp_date, qty, status=Batch.Status.ACTIVE):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier, location=self.location,
            qty_received=qty, exp_date=exp_date, status=status,
        )

    def _gin(self, qty_requested=10, status=Gin.Status.DRAFT):
        gin = Gin.objects.create(
            warehouse=self.warehouse, reference_type=Gin.ReferenceType.SALES,
            requested_by=self.manager, status=status,
        )
        GinItem.objects.create(gin=gin, product=self.product, qty_requested=qty_requested)
        return gin

    def test_TC_GIN_PICK_001_creates_allocation_and_transitions_to_picking(self):
        batch = self._batch('LOT-0001', self.today + datetime.timedelta(days=10), 100)
        gin = self._gin(qty_requested=30)
        start_picking(gin, actor=self.manager)
        gin.refresh_from_db()
        self.assertEqual(gin.status, Gin.Status.PICKING)
        item = gin.items.first()
        allocations = list(item.allocations.all())
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].batch, batch)
        self.assertEqual(allocations[0].qty_allocated, 30)
        self.assertFalse(allocations[0].is_override)

    def test_TC_GIN_PICK_002_splits_across_multiple_batches_ordered_by_exp_date(self):
        near = self._batch('LOT-NEAR', self.today + datetime.timedelta(days=5), 20)
        far = self._batch('LOT-FAR', self.today + datetime.timedelta(days=30), 100)
        gin = self._gin(qty_requested=50)
        start_picking(gin, actor=self.manager)
        item = gin.items.first()
        allocations = list(item.allocations.order_by('id'))
        self.assertEqual(len(allocations), 2)
        self.assertEqual(allocations[0].batch, near)
        self.assertEqual(allocations[0].qty_allocated, 20)
        self.assertEqual(allocations[1].batch, far)
        self.assertEqual(allocations[1].qty_allocated, 30)

    def test_TC_GIN_PICK_003_excludes_quarantine_and_expired_batches(self):
        self._batch('LOT-Q', self.today + datetime.timedelta(days=5), 100, status=Batch.Status.QUARANTINE)
        self._batch('LOT-EXP', self.today - datetime.timedelta(days=1), 100, status=Batch.Status.ACTIVE)
        ok_batch = self._batch('LOT-OK', self.today + datetime.timedelta(days=5), 20)
        gin = self._gin(qty_requested=20)
        start_picking(gin, actor=self.manager)
        item = gin.items.first()
        allocations = list(item.allocations.all())
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].batch, ok_batch)

    def test_TC_GIN_PICK_004_insufficient_stock_raises_and_stays_draft(self):
        self._batch('LOT-0001', self.today + datetime.timedelta(days=5), 10)
        gin = self._gin(qty_requested=999)
        with self.assertRaises(ValidationError):
            start_picking(gin, actor=self.manager)
        gin.refresh_from_db()
        self.assertEqual(gin.status, Gin.Status.DRAFT)
        self.assertEqual(GinBatchAllocation.objects.filter(gin_item__gin=gin).count(), 0)

    def test_TC_GIN_PICK_005_rejects_when_not_draft(self):
        self._batch('LOT-0001', self.today + datetime.timedelta(days=5), 100)
        gin = self._gin(qty_requested=10, status=Gin.Status.PICKING)
        with self.assertRaises(ValidationError):
            start_picking(gin, actor=self.manager)

    def test_TC_GIN_PICK_006_rejects_when_no_items(self):
        gin = Gin.objects.create(
            warehouse=self.warehouse, reference_type=Gin.ReferenceType.SALES, requested_by=self.manager)
        with self.assertRaises(ValidationError):
            start_picking(gin, actor=self.manager)

    def test_TC_GIN_PICK_007_recalling_replaces_old_allocations(self):
        self._batch('LOT-0001', self.today + datetime.timedelta(days=5), 100)
        gin = self._gin(qty_requested=10)
        start_picking(gin, actor=self.manager)
        item = gin.items.first()
        old_alloc_id = item.allocations.first().pk

        gin.status = Gin.Status.DRAFT
        gin.save(update_fields=['status'])
        start_picking(gin, actor=self.manager)
        allocations = list(item.allocations.all())
        self.assertEqual(len(allocations), 1)
        self.assertNotEqual(allocations[0].pk, old_alloc_id)

    def test_TC_GIN_PICK_008_writes_audit_log(self):
        self._batch('LOT-0001', self.today + datetime.timedelta(days=5), 100)
        gin = self._gin(qty_requested=10)
        start_picking(gin, actor=self.manager)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.UPDATE, target_id=str(gin.pk)).exists())


class GinOverrideServiceTest(TestCase):
    """``override_allocation`` (FR-GIN-03). ``TC-GIN-OVERRIDE-<seq>``."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.other_product = Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg')
        self.today = timezone.now().date()
        self.suggested_batch = Batch.objects.create(
            product=self.product, batch_code='LOT-SUGGEST', supplier=self.supplier, location=self.location,
            qty_received=100, exp_date=self.today + datetime.timedelta(days=5),
        )
        self.gin = Gin.objects.create(
            warehouse=self.warehouse, reference_type=Gin.ReferenceType.SALES,
            requested_by=self.manager, status=Gin.Status.PICKING,
        )
        self.item = GinItem.objects.create(gin=self.gin, product=self.product, qty_requested=10)
        self.allocation = GinBatchAllocation.objects.create(
            gin_item=self.item, batch=self.suggested_batch, qty_allocated=10)

    def _batch(self, code, qty, status=Batch.Status.ACTIVE, product=None):
        return Batch.objects.create(
            product=product or self.product, batch_code=code, supplier=self.supplier, location=self.location,
            qty_received=qty, exp_date=self.today + datetime.timedelta(days=20), status=status,
        )

    def test_TC_GIN_OVERRIDE_001_success_marks_is_override_and_reason(self):
        new_batch = self._batch('LOT-NEW', 50)
        override_allocation(self.allocation, new_batch, 'Lô gợi ý gần hết, đổi lô khác', actor=self.manager)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.batch, new_batch)
        self.assertTrue(self.allocation.is_override)
        self.assertEqual(self.allocation.override_reason, 'Lô gợi ý gần hết, đổi lô khác')

    def test_TC_GIN_OVERRIDE_002_writes_audit_log(self):
        new_batch = self._batch('LOT-NEW', 50)
        override_allocation(self.allocation, new_batch, 'lý do', actor=self.manager)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.UPDATE, target_id=str(self.gin.pk)).exists())

    def test_TC_GIN_OVERRIDE_003_rejects_when_not_picking(self):
        self.gin.status = Gin.Status.DRAFT
        self.gin.save(update_fields=['status'])
        new_batch = self._batch('LOT-NEW', 50)
        with self.assertRaises(ValidationError):
            override_allocation(self.allocation, new_batch, 'lý do', actor=self.manager)

    def test_TC_GIN_OVERRIDE_004_rejects_without_reason(self):
        new_batch = self._batch('LOT-NEW', 50)
        with self.assertRaises(ValidationError):
            override_allocation(self.allocation, new_batch, '', actor=self.manager)

    def test_TC_GIN_OVERRIDE_005_rejects_different_product(self):
        wrong_product_batch = self._batch('LOT-WRONG', 50, product=self.other_product)
        with self.assertRaises(ValidationError):
            override_allocation(self.allocation, wrong_product_batch, 'lý do', actor=self.manager)

    def test_TC_GIN_OVERRIDE_006_rejects_quarantine_batch(self):
        quarantine_batch = self._batch('LOT-Q', 50, status=Batch.Status.QUARANTINE)
        with self.assertRaises(ValidationError):
            override_allocation(self.allocation, quarantine_batch, 'lý do', actor=self.manager)

    def test_TC_GIN_OVERRIDE_007_rejects_insufficient_qty(self):
        small_batch = self._batch('LOT-SMALL', 5)
        with self.assertRaises(ValidationError):
            override_allocation(self.allocation, small_batch, 'lý do', actor=self.manager)


class GinIssueServiceTest(TestCase):
    """``issue_gin`` (FR-GIN-04/FR-GIN-05/BR-GIN-006). ``TC-GIN-ISSUE-<seq>``."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.today = timezone.now().date()
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)

    def _batch(self, code, qty, exp_date=None):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier, location=self.location,
            qty_received=qty, exp_date=exp_date or (self.today + datetime.timedelta(days=10)),
        )

    def _gin_picking(self, qty_requested=10):
        gin = Gin.objects.create(
            warehouse=self.warehouse, reference_type=Gin.ReferenceType.SALES,
            requested_by=self.manager, status=Gin.Status.PICKING,
        )
        item = GinItem.objects.create(gin=gin, product=self.product, qty_requested=qty_requested)
        return gin, item

    def test_TC_GIN_ISSUE_001_deducts_inventory_and_sets_issued(self):
        batch = self._batch('LOT-0001', 100)
        gin, item = self._gin_picking(qty_requested=30)
        GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=30)

        issue_gin(gin, actor=self.manager)

        gin.refresh_from_db()
        item.refresh_from_db()
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(gin.status, Gin.Status.ISSUED)
        self.assertIsNotNone(gin.issued_at)
        self.assertEqual(item.qty_issued, 30)
        self.assertEqual(inv.qty_on_hand, 70)

    def test_TC_GIN_ISSUE_002_batch_closed_when_fully_used(self):
        batch = self._batch('LOT-0001', 30)
        gin, item = self._gin_picking(qty_requested=30)
        GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=30)

        issue_gin(gin, actor=self.manager)
        batch.refresh_from_db()
        self.assertEqual(batch.qty_used, 30)
        self.assertEqual(batch.qty_available, 0)
        self.assertEqual(batch.status, Batch.Status.CLOSED)

    def test_TC_GIN_ISSUE_003_batch_partial_used_when_remaining(self):
        batch = self._batch('LOT-0001', 100)
        gin, item = self._gin_picking(qty_requested=30)
        GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=30)

        issue_gin(gin, actor=self.manager)
        batch.refresh_from_db()
        self.assertEqual(batch.qty_available, 70)
        self.assertEqual(batch.status, Batch.Status.PARTIAL_USED)

    def test_TC_GIN_ISSUE_004_splits_across_multiple_batches_sums_correctly(self):
        batch1 = self._batch('LOT-0001', 20)
        batch2 = self._batch('LOT-0002', 100)
        gin, item = self._gin_picking(qty_requested=50)
        GinBatchAllocation.objects.create(gin_item=item, batch=batch1, qty_allocated=20)
        GinBatchAllocation.objects.create(gin_item=item, batch=batch2, qty_allocated=30)

        issue_gin(gin, actor=self.manager)
        item.refresh_from_db()
        batch1.refresh_from_db()
        batch2.refresh_from_db()
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(item.qty_issued, 50)
        self.assertEqual(batch1.status, Batch.Status.CLOSED)
        self.assertEqual(batch2.qty_available, 70)
        self.assertEqual(inv.qty_on_hand, 50)

    def test_TC_GIN_ISSUE_005_creates_stock_movement_issue_entry(self):
        batch = self._batch('LOT-0001', 100)
        gin, item = self._gin_picking(qty_requested=30)
        GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=30)

        issue_gin(gin, actor=self.manager)
        movement = StockMovement.objects.get(reference=gin.gin_no)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.ISSUE)
        self.assertEqual(movement.qty, -30)
        self.assertEqual(movement.batch, batch)

    def test_TC_GIN_ISSUE_006_rejects_when_not_picking(self):
        batch = self._batch('LOT-0001', 100)
        gin, item = self._gin_picking(qty_requested=30)
        GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=30)
        gin.status = Gin.Status.DRAFT
        gin.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            issue_gin(gin, actor=self.manager)

    def test_TC_GIN_ISSUE_007_rejects_when_item_has_no_allocation(self):
        gin, item = self._gin_picking(qty_requested=30)
        with self.assertRaises(ValidationError):
            issue_gin(gin, actor=self.manager)

    def test_TC_GIN_ISSUE_008_rejects_and_rolls_back_when_batch_used_up_after_picking(self):
        """BR-GIN-001: batch bị GIN khác dùng bớt sau khi suggest -> kiểm tra lại
        lúc issue, không cho trừ Inventory âm."""
        batch = self._batch('LOT-0001', 30)
        gin, item = self._gin_picking(qty_requested=30)
        GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=30)
        batch.qty_used = 20  # giả lập GIN khác đã dùng bớt, chỉ còn 10 available
        batch.save(update_fields=['qty_used'])

        with self.assertRaises(ValidationError):
            issue_gin(gin, actor=self.manager)

        gin.refresh_from_db()
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(gin.status, Gin.Status.PICKING)
        self.assertEqual(inv.qty_on_hand, 100)

    def test_TC_GIN_ISSUE_009_writes_audit_log(self):
        batch = self._batch('LOT-0001', 100)
        gin, item = self._gin_picking(qty_requested=30)
        GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=30)
        issue_gin(gin, actor=self.manager)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.APPROVE, target_id=str(gin.pk)).exists())


class GinCloseServiceTest(TestCase):
    """``close_gin``: ISSUED -> CLOSED (archive). ``TC-GIN-CLOSE-<seq>``."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')

    def _gin(self, status):
        return Gin.objects.create(
            warehouse=self.warehouse, reference_type=Gin.ReferenceType.SALES,
            requested_by=self.manager, status=status,
        )

    def test_TC_GIN_CLOSE_001_issued_transitions_to_closed(self):
        gin = self._gin(Gin.Status.ISSUED)
        close_gin(gin, actor=self.manager)
        gin.refresh_from_db()
        self.assertEqual(gin.status, Gin.Status.CLOSED)

    def test_TC_GIN_CLOSE_002_writes_audit_log(self):
        gin = self._gin(Gin.Status.ISSUED)
        close_gin(gin, actor=self.manager)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.UPDATE, target_id=str(gin.pk)).exists())

    def test_TC_GIN_CLOSE_003_rejects_when_not_issued(self):
        gin = self._gin(Gin.Status.PICKING)
        with self.assertRaises(ValidationError):
            close_gin(gin, actor=self.manager)


class GinViewTest(TestCase):
    """View/URL/permission cho GIN theo ma trận quyền ``accounts.permissions``
    (STAFF chỉ CR; MANAGER/ADMIN có đủ update+approve). ``TC-GIN-VIEW-<seq>``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.manager = User.objects.create_user(
            username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.qc_user = User.objects.create_user(
            username='qc1', password='qc-pass-123', role=User.Role.QC)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.today = timezone.now().date()

    def _create_payload(self, **overrides):
        payload = {
            'warehouse': self.warehouse.pk,
            'reference_type': Gin.ReferenceType.SALES,
            'reference_no': '',
            'notes': '',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-qty_requested': 10,
        }
        payload.update(overrides)
        return payload

    def _gin_with_batch(self, qty=10, status=Gin.Status.DRAFT):
        Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=100, exp_date=self.today + datetime.timedelta(days=10),
        )
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=100)
        gin = Gin.objects.create(
            warehouse=self.warehouse, reference_type=Gin.ReferenceType.SALES,
            requested_by=self.manager, status=status,
        )
        GinItem.objects.create(gin=gin, product=self.product, qty_requested=qty)
        return gin

    def test_TC_GIN_VIEW_001_001_anonymous_redirected_to_login(self):
        response = self.client.get(reverse('shipping:gin_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_GIN_VIEW_001_002_staff_can_create_gin(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('shipping:gin_create'), self._create_payload())
        gin = Gin.objects.get(warehouse=self.warehouse)
        self.assertRedirects(response, reverse('shipping:gin_detail', args=[gin.pk]))
        self.assertEqual(gin.status, Gin.Status.DRAFT)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(gin.pk)).exists())

    def test_TC_GIN_VIEW_002_001_staff_forbidden_to_start_picking(self):
        self.client.force_login(self.staff)
        gin = self._gin_with_batch()
        response = self.client.post(reverse('shipping:gin_start_picking', args=[gin.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_GIN_VIEW_002_002_manager_can_start_picking(self):
        self.client.force_login(self.manager)
        gin = self._gin_with_batch()
        response = self.client.post(reverse('shipping:gin_start_picking', args=[gin.pk]))
        gin.refresh_from_db()
        self.assertRedirects(response, reverse('shipping:gin_detail', args=[gin.pk]))
        self.assertEqual(gin.status, Gin.Status.PICKING)

    def test_TC_GIN_VIEW_003_001_staff_forbidden_to_issue(self):
        self.client.force_login(self.staff)
        gin = self._gin_with_batch(status=Gin.Status.PICKING)
        response = self.client.post(reverse('shipping:gin_issue', args=[gin.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_GIN_VIEW_003_002_manager_can_issue(self):
        self.client.force_login(self.manager)
        gin = self._gin_with_batch(status=Gin.Status.PICKING)
        item = gin.items.first()
        batch = Batch.objects.get(batch_code='LOT-0001')
        GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=10)
        response = self.client.post(reverse('shipping:gin_issue', args=[gin.pk]))
        gin.refresh_from_db()
        self.assertRedirects(response, reverse('shipping:gin_detail', args=[gin.pk]))
        self.assertEqual(gin.status, Gin.Status.ISSUED)

    def test_TC_GIN_VIEW_004_001_manager_can_close_issued_gin(self):
        self.client.force_login(self.manager)
        gin = self._gin_with_batch(status=Gin.Status.ISSUED)
        response = self.client.post(reverse('shipping:gin_close', args=[gin.pk]))
        gin.refresh_from_db()
        self.assertRedirects(response, reverse('shipping:gin_detail', args=[gin.pk]))
        self.assertEqual(gin.status, Gin.Status.CLOSED)

    def test_TC_GIN_VIEW_005_001_detail_readable_by_qc_role(self):
        gin = self._gin_with_batch()
        self.client.force_login(self.qc_user)
        response = self.client.get(reverse('shipping:gin_detail', args=[gin.pk]))
        self.assertEqual(response.status_code, 200)

    def test_TC_GIN_VIEW_007_001_detail_shows_batch_location(self):
        """FR-GIN-07: trang chi tiết GIN hiển thị vị trí batch đã phân bổ."""
        gin = self._gin_with_batch(status=Gin.Status.PICKING)
        item = gin.items.first()
        batch = Batch.objects.get(batch_code='LOT-0001')
        GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=10)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('shipping:gin_detail', args=[gin.pk]))
        self.assertContains(response, str(self.location))

    def test_TC_GIN_VIEW_007_002_picking_page_shows_batch_location(self):
        """FR-GIN-07: trang soạn hàng hiển thị vị trí batch (cả gợi ý FIFO và
        dropdown đổi batch khác)."""
        gin = self._gin_with_batch(status=Gin.Status.PICKING)
        item = gin.items.first()
        batch = Batch.objects.get(batch_code='LOT-0001')
        GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=10)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('shipping:gin_picking', args=[gin.pk]))
        self.assertContains(response, str(self.location))

    def test_TC_GIN_VIEW_006_001_staff_forbidden_to_override_allocation(self):
        self.client.force_login(self.staff)
        gin = self._gin_with_batch(status=Gin.Status.PICKING)
        item = gin.items.first()
        batch = Batch.objects.get(batch_code='LOT-0001')
        alloc = GinBatchAllocation.objects.create(gin_item=item, batch=batch, qty_allocated=10)
        response = self.client.post(reverse('shipping:gin_allocation_override', args=[alloc.pk]), {
            f'override-{alloc.pk}-batch': batch.pk, f'override-{alloc.pk}-reason': 'lý do',
        })
        self.assertEqual(response.status_code, 403)


class GinPrintViewTest(TestCase):
    """Trang in phiếu GIN kèm barcode (FR-GIN-06). ``TC-GIN-006-<seq>``."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        today = timezone.now().date()
        self.batch = Batch.objects.create(
            product=self.product, batch_code='LOT-PRINT', supplier=self.supplier, location=self.location,
            qty_received=10, exp_date=today + datetime.timedelta(days=10),
        )
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=10)
        self.gin = Gin.objects.create(
            warehouse=self.warehouse, reference_type=Gin.ReferenceType.SALES, requested_by=self.manager)
        self.item = GinItem.objects.create(gin=self.gin, product=self.product, qty_requested=10)
        self.client.force_login(self.manager)

    def test_TC_GIN_006_001_print_link_hidden_before_issued(self):
        response = self.client.get(reverse('shipping:gin_detail', args=[self.gin.pk]))
        self.assertNotContains(response, reverse('shipping:gin_print', args=[self.gin.pk]))

    def test_TC_GIN_006_002_print_link_shown_after_issued(self):
        start_picking(self.gin, actor=self.manager)
        issue_gin(self.gin, actor=self.manager)
        response = self.client.get(reverse('shipping:gin_detail', args=[self.gin.pk]))
        self.assertContains(response, reverse('shipping:gin_print', args=[self.gin.pk]))

    def test_TC_GIN_006_003_print_view_returns_200_with_batch_code_and_gin_no(self):
        start_picking(self.gin, actor=self.manager)
        issue_gin(self.gin, actor=self.manager)
        response = self.client.get(reverse('shipping:gin_print', args=[self.gin.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(self.gin.gin_no, content)
        self.assertIn('LOT-PRINT', content)

    def test_TC_GIN_006_004_print_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('shipping:gin_print', args=[self.gin.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_GIN_006_005_print_link_shown_when_closed(self):
        start_picking(self.gin, actor=self.manager)
        issue_gin(self.gin, actor=self.manager)
        close_gin(self.gin, actor=self.manager)
        response = self.client.get(reverse('shipping:gin_detail', args=[self.gin.pk]))
        self.assertContains(response, reverse('shipping:gin_print', args=[self.gin.pk]))
