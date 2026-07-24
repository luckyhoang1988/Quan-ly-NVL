import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product
from inventory.models import Batch, Inventory
from partners.models import Supplier
from purchasing.models import PurchaseOrder
from receiving.models import Grn, GrnItem, GrnReturn
from warehouse.models import Location, Warehouse

from .models import QcCriteria, QcInspection, QcInspectionItem
from .services import qc_fail, qc_partial_pass, qc_pass, start_qc

User = get_user_model()


class QcInspectionModelTest(TestCase):
    """QC (mục 2b — schema/model, chưa có workflow). ``TC-QC-<seq>``."""

    def setUp(self):
        self.purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.qc_user = User.objects.create_user(
            username='qc1', password='qc-pass-123', role=User.Role.QC)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.purchasing_user)
        self.grn_item = GrnItem.objects.create(
            grn=self.grn, product=self.product, qty_ordered=10, qty_received=10,
            unit_price=Decimal('15000.00'),
        )

    def test_TC_QC_001_001_qc_no_auto_generated_with_current_yyyymm_prefix(self):
        inspection = QcInspection.objects.create(grn=self.grn, inspector=self.qc_user)
        today = datetime.date.today()
        self.assertEqual(inspection.qc_no, f'QC-{today:%Y%m}-001')

    def test_TC_QC_001_002_qc_no_sequence_increments_within_same_month(self):
        first = QcInspection.objects.create(grn=self.grn, inspector=self.qc_user)
        second = QcInspection.objects.create(grn=self.grn, inspector=self.qc_user)
        self.assertEqual(int(second.qc_no[-3:]), int(first.qc_no[-3:]) + 1)

    def test_TC_QC_001_003_default_status_is_pending_qc(self):
        inspection = QcInspection.objects.create(grn=self.grn, inspector=self.qc_user)
        self.assertEqual(inspection.status, QcInspection.Result.PENDING_QC)

    def test_TC_QC_002_001_inspection_item_result_recorded_per_criteria(self):
        inspection = QcInspection.objects.create(grn=self.grn, inspector=self.qc_user)
        item = QcInspectionItem.objects.create(
            inspection=inspection, grn_item=self.grn_item,
            criteria_name='Trọng lượng', expected_value='1000g ± 10g', actual_value='1005g',
            result=QcInspectionItem.Result.PASS,
        )
        self.assertEqual(item.result, QcInspectionItem.Result.PASS)


class QcCriteriaModelTest(TestCase):
    """QC Criteria master data (FR-QC-02). ``TC-QC-CRIT-<seq>``."""

    def test_TC_QC_CRIT_001_001_unique_per_category_and_name(self):
        QcCriteria.objects.create(category='Bột mì', name='Ngoại hình')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                QcCriteria.objects.create(category='Bột mì', name='Ngoại hình')


class QcServiceTestBase(TestCase):
    """Fixture dùng chung cho các test transaction QC (mục 2c)."""

    def setUp(self):
        self.purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.qc_user = User.objects.create_user(
            username='qc1', password='qc-pass-123', role=User.Role.QC)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.purchasing_user)
        self.grn_item = GrnItem.objects.create(
            grn=self.grn, product=self.product, qty_ordered=10, qty_received=10,
            unit_price=Decimal('15000.00'),
        )

    def _start_qc(self):
        return start_qc(self.grn, self.qc_user, actor=self.qc_user)


class StartQcTest(QcServiceTestBase):
    """``TC-QC-START-<seq>``."""

    def test_TC_QC_START_001_001_transitions_grn_and_creates_inspection(self):
        inspection = self._start_qc()
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.QC_IN_PROGRESS)
        self.assertEqual(inspection.grn, self.grn)
        self.assertEqual(inspection.inspector, self.qc_user)
        self.assertEqual(inspection.status, QcInspection.Result.PENDING_QC)

    def test_TC_QC_START_001_002_raises_when_grn_not_in_draft_or_pending_qc(self):
        self._start_qc()
        self.grn.refresh_from_db()
        with self.assertRaises(ValidationError):
            start_qc(self.grn, self.qc_user, actor=self.qc_user)


class QcPassTransactionTest(QcServiceTestBase):
    """QC PASS (mục 2c). ``TC-QC-PASS-<seq>``."""

    def test_TC_QC_PASS_001_001_creates_active_batch_and_credits_inventory(self):
        inspection = self._start_qc()
        grn = qc_pass(inspection, actor=self.qc_user, location=self.location)

        self.assertEqual(grn.status, Grn.Status.RECEIVED)
        batch = Batch.objects.get(product=self.product)
        self.assertEqual(batch.status, Batch.Status.ACTIVE)
        self.assertEqual(batch.qty_received, 10)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 10)
        self.grn_item.refresh_from_db()
        self.assertEqual(self.grn_item.qty_pass, 10)
        self.assertEqual(self.grn_item.status, GrnItem.Status.RECEIVED)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.PASS)
        self.assertIsNotNone(inspection.completed_at)

    def test_TC_QC_PASS_001_002_raises_when_inspection_already_resolved(self):
        inspection = self._start_qc()
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        with self.assertRaises(ValidationError):
            qc_pass(inspection, actor=self.qc_user, location=self.location)


class QcFailTransactionTest(QcServiceTestBase):
    """QC FAIL (mục 2c) — nhánh dễ sai nhất: KHÔNG được đụng Inventory. ``TC-QC-FAIL-<seq>``."""

    def test_TC_QC_FAIL_001_001_creates_return_rejects_grn_no_inventory_change(self):
        inspection = self._start_qc()
        ret = qc_fail(inspection, actor=self.qc_user, reason='Ngoại hình không đạt')

        self.assertEqual(ret.grn, self.grn)
        self.assertEqual(ret.status, GrnReturn.Status.PENDING)
        self.assertEqual(ret.reason, 'Ngoại hình không đạt')
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.REJECTED)
        self.grn_item.refresh_from_db()
        self.assertEqual(self.grn_item.status, GrnItem.Status.REJECTED)
        self.assertEqual(self.grn_item.qty_pass, 0)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.FAIL)
        self.assertFalse(Batch.objects.exists())
        self.assertFalse(Inventory.objects.filter(product=self.product).exists())

    def test_TC_QC_FAIL_001_002_raises_when_inspection_already_resolved(self):
        inspection = self._start_qc()
        qc_fail(inspection, actor=self.qc_user)
        with self.assertRaises(ValidationError):
            qc_fail(inspection, actor=self.qc_user)


class QcPartialPassTransactionTest(QcServiceTestBase):
    """PARTIAL_PASS (mục 2c) — split Batch ACTIVE+QUARANTINE. ``TC-QC-PARTIAL-<seq>``."""

    def test_TC_QC_PARTIAL_001_001_splits_batch_and_credits_only_passed_qty(self):
        inspection = self._start_qc()
        grn = qc_partial_pass(
            inspection, {self.grn_item.pk: 6}, actor=self.qc_user, location=self.location)

        self.assertEqual(grn.status, Grn.Status.RECEIVED)
        active = Batch.objects.get(status=Batch.Status.ACTIVE)
        quarantine = Batch.objects.get(status=Batch.Status.QUARANTINE)
        self.assertEqual(active.qty_received, 6)
        self.assertEqual(quarantine.qty_received, 4)
        self.assertNotEqual(active.batch_code, quarantine.batch_code)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 6)
        self.grn_item.refresh_from_db()
        self.assertEqual(self.grn_item.qty_pass, 6)
        self.assertEqual(self.grn_item.status, GrnItem.Status.PARTIAL_RECEIVED)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.PARTIAL_PASS)

    def test_TC_QC_PARTIAL_002_001_missing_item_result_raises(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_partial_pass(inspection, {}, actor=self.qc_user, location=self.location)

    def test_TC_QC_PARTIAL_003_001_qty_pass_greater_than_qty_received_raises(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_partial_pass(
                inspection, {self.grn_item.pk: 11}, actor=self.qc_user, location=self.location)


class QcResultViewTest(QcServiceTestBase):
    """View ``qc_result`` (Task 3) — 3 nút Pass/Fail/Partial gọi thẳng quality.services
    đã có unit test riêng ở trên; test ở đây chỉ xác nhận view/form/permission nối
    đúng dây. ``TC-QC-VIEW-<seq>``.
    """

    def setUp(self):
        super().setUp()
        self.manager = User.objects.create_user(
            username='qlk', password='qlk-pass-123', role=User.Role.MANAGER)
        self._start_qc()
        self.client.force_login(self.qc_user)

    def _url(self):
        return reverse('quality:qc_result', args=[self.grn.pk])

    def _payload(self, **overrides):
        payload = {
            'location': self.location.pk, 'reason': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-id': self.grn_item.pk, 'items-0-qty_pass': 10,
        }
        payload.update(overrides)
        return payload

    def test_TC_QC_VIEW_001_001_read_only_role_forbidden(self):
        self.client.force_login(self.purchasing_user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 403)

    def test_TC_QC_VIEW_001_002_pass_action_creates_batch_and_credits_inventory(self):
        response = self.client.post(self._url(), self._payload(action='pass'))
        self.grn.refresh_from_db()
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[self.grn.pk]))
        self.assertEqual(self.grn.status, Grn.Status.RECEIVED)
        self.assertTrue(Batch.objects.filter(product=self.product, status=Batch.Status.ACTIVE).exists())
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 10)

    def test_TC_QC_VIEW_001_003_pass_action_requires_location(self):
        response = self.client.post(self._url(), self._payload(location='', action='pass'))
        self.assertEqual(response.status_code, 200)
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.QC_IN_PROGRESS)

    def test_TC_QC_VIEW_001_004_fail_action_creates_return_no_inventory_change(self):
        response = self.client.post(self._url(), self._payload(
            location='', reason='Ngoại hình không đạt', action='fail'))
        self.grn.refresh_from_db()
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[self.grn.pk]))
        self.assertEqual(self.grn.status, Grn.Status.REJECTED)
        self.assertFalse(Batch.objects.exists())
        self.assertFalse(Inventory.objects.filter(product=self.product).exists())
        self.assertTrue(GrnReturn.objects.filter(grn=self.grn, reason='Ngoại hình không đạt').exists())

    def test_TC_QC_VIEW_001_005_partial_action_splits_batches(self):
        response = self.client.post(self._url(), self._payload(**{'items-0-qty_pass': 6, 'action': 'partial'}))
        self.grn.refresh_from_db()
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[self.grn.pk]))
        self.assertEqual(self.grn.status, Grn.Status.RECEIVED)
        self.assertTrue(Batch.objects.filter(status=Batch.Status.ACTIVE, qty_received=6).exists())
        self.assertTrue(Batch.objects.filter(status=Batch.Status.QUARANTINE, qty_received=4).exists())

    def test_TC_QC_VIEW_001_006_manager_can_also_submit_result(self):
        self.client.force_login(self.manager)
        response = self.client.post(self._url(), self._payload(action='pass'))
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.RECEIVED)
