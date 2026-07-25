import datetime
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product
from inventory.models import Batch, Inventory, StockMovement
from partners.models import Supplier
from purchasing.models import PurchaseOrder
from receiving.models import Grn, GrnItem, GrnReturn
from warehouse.models import Location, Warehouse

from .forms import QcResultForm
from .models import QcCriteria, QcInspection, QcInspectionItem
from .services import (
    _get_staging_batch,
    overdue_inspections,
    qc_fail,
    qc_partial_pass,
    qc_pass,
    start_qc,
    suggested_sample_qty,
)

User = get_user_model()

# GIF 1x1px hợp lệ nhỏ nhất — đủ để Pillow/ImageField validate mà không cần file ảnh thật.
SMALL_GIF = (
    b'GIF87a\x01\x00\x01\x00\x80\x01\x00\x00\x00\x00ccc,\x00'
    b'\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
)


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


class SuggestedSampleQtyTest(TestCase):
    """Sampling method config per SKU (mục 2b). ``TC-QC-SAMPLE-<seq>``."""

    def test_TC_QC_SAMPLE_001_percent_rounds_up_and_caps_at_qty_received(self):
        product = Product.objects.create(
            product_code='NVL-P', name='Bột mì', uom='kg',
            qc_sampling_method=Product.SamplingMethod.PERCENT, qc_sampling_value=10)
        self.assertEqual(suggested_sample_qty(product, 100), 10)
        self.assertEqual(suggested_sample_qty(product, 25), 3)  # ceil(2.5) = 3
        self.assertEqual(suggested_sample_qty(product, 5), 1)  # ceil(0.5) = 1, min 1

    def test_TC_QC_SAMPLE_002_fixed_capped_at_qty_received(self):
        product = Product.objects.create(
            product_code='NVL-F', name='Đường', uom='kg',
            qc_sampling_method=Product.SamplingMethod.FIXED, qc_sampling_value=20)
        self.assertEqual(suggested_sample_qty(product, 100), 20)
        self.assertEqual(suggested_sample_qty(product, 5), 5)  # không vượt qty_received

    def test_TC_QC_SAMPLE_003_zero_qty_received_returns_zero(self):
        product = Product.objects.create(product_code='NVL-Z', name='Muối', uom='kg')
        self.assertEqual(suggested_sample_qty(product, 0), 0)


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
        self.warehouse = Warehouse.objects.create(
            code='KHO-HN', name='Kho Hà Nội', warehouse_type=Warehouse.WarehouseType.MAIN)
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.staging_warehouse = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        self.staging_location = Location.objects.create(warehouse=self.staging_warehouse, code='A-01')
        self.scrap_warehouse = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        self.scrap_location = Location.objects.create(warehouse=self.scrap_warehouse, code='A-01')
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

    def test_TC_QC_START_002_001_creates_active_batch_at_staging_and_credits_inventory(self):
        self._start_qc()
        batch = Batch.objects.get(grn_item=self.grn_item)
        self.assertEqual(batch.status, Batch.Status.ACTIVE)
        self.assertEqual(batch.location, self.staging_location)
        self.assertEqual(batch.qty_received, 10)
        inv = Inventory.objects.get(product=self.product, warehouse=self.staging_warehouse)
        self.assertEqual(inv.qty_on_hand, 10)
        self.assertTrue(StockMovement.objects.filter(
            product=self.product, warehouse=self.staging_warehouse,
            movement_type=StockMovement.MovementType.RECEIPT, qty=10,
        ).exists())

    def test_TC_QC_START_002_002_skips_item_with_zero_qty_received(self):
        zero_item = GrnItem.objects.create(
            grn=self.grn, product=self.product, qty_ordered=5, qty_received=0,
            unit_price=Decimal('15000.00'),
        )
        self._start_qc()
        self.assertFalse(Batch.objects.filter(grn_item=zero_item).exists())

    def test_TC_QC_START_003_001_raises_when_no_staging_warehouse_configured(self):
        # Kịch bản "≥2 kho STAGING active" bị chặn ở tầng DB (unique_active_staging_scrap_warehouse
        # — xem warehouse.tests.WarehouseTypeSingletonTest), nên chỉ còn kịch bản 0 kho khả thi ở đây.
        self.staging_warehouse.is_active = False
        self.staging_warehouse.save(update_fields=['is_active'])
        with self.assertRaises(ValidationError):
            self._start_qc()


class OverdueInspectionsTest(QcServiceTestBase):
    """QC SLA alert 24h (mục 2b). ``TC-QC-SLA-<seq>``."""

    def test_TC_QC_SLA_001_not_overdue_within_sla(self):
        self._start_qc()
        self.assertEqual(overdue_inspections().count(), 0)

    def test_TC_QC_SLA_002_overdue_past_24h_still_pending(self):
        inspection = self._start_qc()
        QcInspection.objects.filter(pk=inspection.pk).update(
            started_at=timezone.now() - datetime.timedelta(hours=25))
        self.assertEqual(list(overdue_inspections()), [
            QcInspection.objects.get(pk=inspection.pk)])

    def test_TC_QC_SLA_003_not_overdue_once_completed(self):
        inspection = self._start_qc()
        QcInspection.objects.filter(pk=inspection.pk).update(
            started_at=timezone.now() - datetime.timedelta(hours=25))
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        self.assertEqual(overdue_inspections().count(), 0)


class QcPassTransactionTest(QcServiceTestBase):
    """QC PASS (mục 2c). ``TC-QC-PASS-<seq>``."""

    def test_TC_QC_PASS_001_001_consumes_staging_batch_and_credits_main_inventory(self):
        inspection = self._start_qc()
        staging_batch = Batch.objects.get(grn_item=self.grn_item, status=Batch.Status.ACTIVE)
        grn = qc_pass(inspection, actor=self.qc_user, location=self.location)

        self.assertEqual(grn.status, Grn.Status.RECEIVED)
        staging_batch.refresh_from_db()
        self.assertEqual(staging_batch.status, Batch.Status.CLOSED)
        batch = Batch.objects.get(status=Batch.Status.ACTIVE)
        self.assertEqual(batch.location, self.location)
        self.assertEqual(batch.qty_received, 10)
        self.assertEqual(batch.grn_item, self.grn_item)
        staging_inv = Inventory.objects.get(product=self.product, warehouse=self.staging_warehouse)
        self.assertEqual(staging_inv.qty_on_hand, 0)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 10)
        self.assertEqual(StockMovement.objects.filter(
            product=self.product, movement_type=StockMovement.MovementType.TRANSFER_OUT).count(), 1)
        self.assertEqual(StockMovement.objects.filter(
            product=self.product, movement_type=StockMovement.MovementType.TRANSFER_IN).count(), 1)
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

    def test_TC_QC_PASS_002_001_rejects_non_main_warehouse_location(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_pass(inspection, actor=self.qc_user, location=self.staging_location)


class QcFailTransactionTest(QcServiceTestBase):
    """QC FAIL (mục 2c) — nhánh dễ sai nhất trước đây (không đụng Inventory);
    nay tiêu thụ batch Kho chờ và chuyển toàn bộ sang Kho phế (QUARANTINE).
    ``TC-QC-FAIL-<seq>``.
    """

    def test_TC_QC_FAIL_001_001_creates_return_rejects_grn_moves_to_scrap(self):
        inspection = self._start_qc()
        staging_batch = Batch.objects.get(grn_item=self.grn_item, status=Batch.Status.ACTIVE)
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

        staging_batch.refresh_from_db()
        self.assertEqual(staging_batch.status, Batch.Status.CLOSED)
        scrap_batch = Batch.objects.get(status=Batch.Status.QUARANTINE)
        self.assertEqual(scrap_batch.location, self.scrap_location)
        self.assertEqual(scrap_batch.qty_received, 10)
        self.assertEqual(scrap_batch.grn_item, self.grn_item)
        staging_inv = Inventory.objects.get(product=self.product, warehouse=self.staging_warehouse)
        self.assertEqual(staging_inv.qty_on_hand, 0)
        scrap_inv = Inventory.objects.get(product=self.product, warehouse=self.scrap_warehouse)
        self.assertEqual(scrap_inv.qty_on_hand, 10)

    def test_TC_QC_FAIL_001_002_raises_when_inspection_already_resolved(self):
        inspection = self._start_qc()
        qc_fail(inspection, actor=self.qc_user)
        with self.assertRaises(ValidationError):
            qc_fail(inspection, actor=self.qc_user)


class QcPartialPassTransactionTest(QcServiceTestBase):
    """PARTIAL_PASS (mục 2c) — split Batch ACTIVE+QUARANTINE. ``TC-QC-PARTIAL-<seq>``."""

    def test_TC_QC_PARTIAL_001_001_splits_batch_and_credits_only_passed_qty(self):
        inspection = self._start_qc()
        staging_batch = Batch.objects.get(grn_item=self.grn_item, status=Batch.Status.ACTIVE)
        grn = qc_partial_pass(
            inspection, {self.grn_item.pk: 6}, actor=self.qc_user, location=self.location)

        self.assertEqual(grn.status, Grn.Status.RECEIVED)
        staging_batch.refresh_from_db()
        self.assertEqual(staging_batch.status, Batch.Status.CLOSED)
        active = Batch.objects.get(status=Batch.Status.ACTIVE)
        quarantine = Batch.objects.get(status=Batch.Status.QUARANTINE)
        self.assertEqual(active.qty_received, 6)
        self.assertEqual(active.location, self.location)
        self.assertEqual(quarantine.qty_received, 4)
        self.assertEqual(quarantine.location, self.scrap_location)
        self.assertNotEqual(active.batch_code, quarantine.batch_code)
        staging_inv = Inventory.objects.get(product=self.product, warehouse=self.staging_warehouse)
        self.assertEqual(staging_inv.qty_on_hand, 0)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 6)
        scrap_inv = Inventory.objects.get(product=self.product, warehouse=self.scrap_warehouse)
        self.assertEqual(scrap_inv.qty_on_hand, 4)
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

    def test_TC_QC_PARTIAL_004_001_rejects_non_main_warehouse_location(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_partial_pass(
                inspection, {self.grn_item.pk: 6}, actor=self.qc_user, location=self.scrap_location)


class GetStagingBatchTest(QcServiceTestBase):
    """``_get_staging_batch`` — helper nội bộ tìm batch Kho chờ theo lineage
    ``grn_item``. ``TC-QC-STG-<seq>``.
    """

    def test_TC_QC_STG_001_raises_when_grn_not_yet_submitted_to_qc(self):
        with self.assertRaises(ValidationError):
            _get_staging_batch(self.grn_item)

    def test_TC_QC_STG_002_raises_after_batch_already_consumed(self):
        inspection = self._start_qc()
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        with self.assertRaises(ValidationError):
            _get_staging_batch(self.grn_item)


class QcResultFormTest(QcServiceTestBase):
    """``QcResultForm.location`` chỉ liệt kê vị trí thuộc kho MAIN (M5) — field
    này giờ chỉ dùng cho đích PASS/PARTIAL_PASS, không được trỏ vào Kho chờ/Kho phế.
    """

    def test_location_queryset_excludes_staging_and_scrap(self):
        form = QcResultForm()
        locations = list(form.fields['location'].queryset)
        self.assertIn(self.location, locations)
        self.assertNotIn(self.staging_location, locations)
        self.assertNotIn(self.scrap_location, locations)


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

    def test_TC_QC_VIEW_001_004_fail_action_creates_return_moves_to_scrap(self):
        response = self.client.post(self._url(), self._payload(
            location='', reason='Ngoại hình không đạt', action='fail'))
        self.grn.refresh_from_db()
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[self.grn.pk]))
        self.assertEqual(self.grn.status, Grn.Status.REJECTED)
        self.assertTrue(Batch.objects.filter(status=Batch.Status.QUARANTINE, qty_received=10).exists())
        scrap_inv = Inventory.objects.get(product=self.product, warehouse=self.scrap_warehouse)
        self.assertEqual(scrap_inv.qty_on_hand, 10)
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


class QcOverrideViewTest(QcServiceTestBase):
    """QC approval override (BACKLOG mục 2b) — ``TC-QC-OVERRIDE-<seq>``.

    Phạm vi đã chốt với user: CHỈ ghi ``override_note``/``overridden_by``/
    ``overridden_at`` — KHÔNG đảo ngược Batch/Inventory (transaction gốc của
    ``qc_pass`` đã có test riêng ở ``QcPassTransactionTest``); test ở đây xác
    nhận đúng permission (Manager/Admin, không phải QC Inspector) và bất biến
    "không đụng transaction gốc".
    """

    def setUp(self):
        super().setUp()
        self.manager = User.objects.create_user(
            username='qlk', password='qlk-pass-123', role=User.Role.MANAGER)
        self.inspection = self._start_qc()
        qc_pass(self.inspection, actor=self.qc_user, location=self.location)
        self.client.force_login(self.manager)

    def _url(self, inspection=None):
        return reverse('quality:qc_override', args=[(inspection or self.inspection).pk])

    def test_TC_QC_OVERRIDE_001_manager_can_submit_override_note(self):
        inv_before = Inventory.objects.get(product=self.product, warehouse=self.warehouse).qty_on_hand
        response = self.client.post(self._url(), {'override_note': 'Xem lại theo yêu cầu supplier.'})
        self.inspection.refresh_from_db()
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[self.grn.pk]))
        self.assertEqual(self.inspection.override_note, 'Xem lại theo yêu cầu supplier.')
        self.assertEqual(self.inspection.overridden_by, self.manager)
        self.assertIsNotNone(self.inspection.overridden_at)
        # Annotation-only: KHÔNG đảo ngược status/Batch/Inventory đã tạo bởi qc_pass.
        self.assertEqual(self.inspection.status, QcInspection.Result.PASS)
        inv_after = Inventory.objects.get(product=self.product, warehouse=self.warehouse).qty_on_hand
        self.assertEqual(inv_after, inv_before)

    def test_TC_QC_OVERRIDE_002_qc_inspector_forbidden(self):
        self.client.force_login(self.qc_user)
        response = self.client.post(self._url(), {'override_note': 'x'})
        self.assertEqual(response.status_code, 403)

    def test_TC_QC_OVERRIDE_003_read_only_role_forbidden(self):
        self.client.force_login(self.purchasing_user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 403)

    def test_TC_QC_OVERRIDE_004_empty_note_rejected(self):
        response = self.client.post(self._url(), {'override_note': ''})
        self.assertEqual(response.status_code, 200)
        self.inspection.refresh_from_db()
        self.assertIsNone(self.inspection.overridden_at)

    def test_TC_QC_OVERRIDE_005_pending_inspection_rejected(self):
        grn2 = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.purchasing_user)
        GrnItem.objects.create(
            grn=grn2, product=self.product, qty_ordered=5, qty_received=5, unit_price=Decimal('15000.00'))
        pending_inspection = start_qc(grn2, self.qc_user, actor=self.qc_user)
        response = self.client.get(self._url(pending_inspection))
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[grn2.pk]))


class QcInspectionItemResultViewTest(QcServiceTestBase):
    """Form "Lưu kết quả từng tiêu chuẩn" trên trang ``qc_result`` (FR-QC-03,
    Task 3) -- POST không có field ``action`` -> lưu ``QcInspectionItemFormSet``,
    tách biệt khỏi quyết định Pass/Fail/Partial. ``TC-QC-VIEW-ITEM-<seq>``.
    """

    def setUp(self):
        super().setUp()
        self.inspection = self._start_qc()
        self.client.force_login(self.qc_user)

    def _url(self):
        return reverse('quality:qc_result', args=[self.grn.pk])

    def _items_payload(self, **overrides):
        payload = {
            'qcitems-TOTAL_FORMS': '3', 'qcitems-INITIAL_FORMS': '0',
            'qcitems-MIN_NUM_FORMS': '0', 'qcitems-MAX_NUM_FORMS': '1000',
            'qcitems-0-id': '', 'qcitems-0-grn_item': self.grn_item.pk,
            'qcitems-0-criteria_name': 'Trọng lượng', 'qcitems-0-expected_value': '1000g ± 10g',
            'qcitems-0-actual_value': '1005g', 'qcitems-0-result': 'PASS', 'qcitems-0-notes': '',
            'qcitems-1-id': '', 'qcitems-1-grn_item': '', 'qcitems-1-criteria_name': '',
            'qcitems-1-expected_value': '', 'qcitems-1-actual_value': '', 'qcitems-1-result': '', 'qcitems-1-notes': '',
            'qcitems-2-id': '', 'qcitems-2-grn_item': '', 'qcitems-2-criteria_name': '',
            'qcitems-2-expected_value': '', 'qcitems-2-actual_value': '', 'qcitems-2-result': '', 'qcitems-2-notes': '',
        }
        payload.update(overrides)
        return payload

    def test_TC_QC_VIEW_ITEM_001_qc_user_can_save_item_result(self):
        response = self.client.post(self._url(), self._items_payload())
        self.assertRedirects(response, self._url())
        item = QcInspectionItem.objects.get(inspection=self.inspection)
        self.assertEqual(item.grn_item, self.grn_item)
        self.assertEqual(item.criteria_name, 'Trọng lượng')
        self.assertEqual(item.result, QcInspectionItem.Result.PASS)

    def test_TC_QC_VIEW_ITEM_002_grn_item_restricted_to_this_grn(self):
        other_po = PurchaseOrder.objects.create(po_no='PO-0002', supplier=self.supplier)
        other_grn = Grn.objects.create(po=other_po, supplier=self.supplier, created_by=self.purchasing_user)
        other_item = GrnItem.objects.create(
            grn=other_grn, product=self.product, qty_ordered=5, qty_received=5,
            unit_price=Decimal('15000.00'),
        )
        response = self.client.post(self._url(), self._items_payload(**{'qcitems-0-grn_item': other_item.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(QcInspectionItem.objects.exists())

    def test_TC_QC_VIEW_ITEM_003_missing_result_not_saved(self):
        response = self.client.post(self._url(), self._items_payload(**{'qcitems-0-result': ''}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(QcInspectionItem.objects.exists())

    def test_TC_QC_VIEW_ITEM_004_does_not_change_grn_or_inspection_status(self):
        self.client.post(self._url(), self._items_payload())
        self.grn.refresh_from_db()
        self.inspection.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.QC_IN_PROGRESS)
        self.assertEqual(self.inspection.status, QcInspection.Result.PENDING_QC)

    def test_TC_QC_VIEW_ITEM_005_delete_existing_item(self):
        item = QcInspectionItem.objects.create(
            inspection=self.inspection, grn_item=self.grn_item,
            criteria_name='Ngoại hình', actual_value='Đạt', result=QcInspectionItem.Result.PASS,
        )
        payload = self._items_payload(**{
            'qcitems-INITIAL_FORMS': '1',
            'qcitems-0-id': item.pk, 'qcitems-0-grn_item': self.grn_item.pk,
            'qcitems-0-criteria_name': 'Ngoại hình', 'qcitems-0-actual_value': 'Đạt',
            'qcitems-0-result': 'PASS', 'qcitems-0-DELETE': 'on',
        })
        response = self.client.post(self._url(), payload)
        self.assertRedirects(response, self._url())
        self.assertFalse(QcInspectionItem.objects.filter(pk=item.pk).exists())

    def test_TC_QC_VIEW_ITEM_006_read_only_role_forbidden(self):
        self.client.force_login(self.purchasing_user)
        response = self.client.post(self._url(), self._items_payload())
        self.assertEqual(response.status_code, 403)


class QcImageUploadTest(QcServiceTestBase):
    """FR-QC-06: upload ảnh evidence trên ``QcInspectionItem`` + ảnh reference
    trên ``QcCriteria`` master data. Ghi vào ``MEDIA_ROOT`` tạm (xoá sau khi
    chạy xong) để không để lại file thật trong repo. ``TC-QC-IMG-<seq>``.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix='nvlwms_test_media_')
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.inspection = self._start_qc()
        self.client.force_login(self.qc_user)

    def _result_url(self):
        return reverse('quality:qc_result', args=[self.grn.pk])

    def test_TC_QC_IMG_001_evidence_image_saved_on_inspection_item(self):
        image = SimpleUploadedFile('evidence.gif', SMALL_GIF, content_type='image/gif')
        payload = {
            'qcitems-TOTAL_FORMS': '1', 'qcitems-INITIAL_FORMS': '0',
            'qcitems-MIN_NUM_FORMS': '0', 'qcitems-MAX_NUM_FORMS': '1000',
            'qcitems-0-id': '', 'qcitems-0-grn_item': self.grn_item.pk,
            'qcitems-0-criteria_name': 'Màu sắc', 'qcitems-0-expected_value': 'Trắng đều',
            'qcitems-0-actual_value': 'Trắng đều', 'qcitems-0-result': 'PASS', 'qcitems-0-notes': '',
            'qcitems-0-image': image,
        }
        response = self.client.post(self._result_url(), payload)
        self.assertRedirects(response, self._result_url())
        item = QcInspectionItem.objects.get(inspection=self.inspection)
        self.assertTrue(item.image.name)

    def test_TC_QC_IMG_002_reference_image_saved_on_criteria(self):
        image = SimpleUploadedFile('ref.gif', SMALL_GIF, content_type='image/gif')
        response = self.client.post(reverse('quality:qc_criteria_create'), {
            'category': 'Đường', 'name': 'Màu sắc', 'pass_rule': 'Trắng',
            'fail_rule': 'Vàng/xám', 'is_active': 'on', 'reference_image': image,
        })
        self.assertRedirects(response, reverse('quality:qc_criteria_list'))
        criteria = QcCriteria.objects.get(category='Đường', name='Màu sắc')
        self.assertTrue(criteria.reference_image.name)

    def test_TC_QC_IMG_003_image_optional_not_required(self):
        payload = {
            'qcitems-TOTAL_FORMS': '1', 'qcitems-INITIAL_FORMS': '0',
            'qcitems-MIN_NUM_FORMS': '0', 'qcitems-MAX_NUM_FORMS': '1000',
            'qcitems-0-id': '', 'qcitems-0-grn_item': self.grn_item.pk,
            'qcitems-0-criteria_name': 'Seal integrity', 'qcitems-0-expected_value': 'Nguyên vẹn',
            'qcitems-0-actual_value': 'Nguyên vẹn', 'qcitems-0-result': 'PASS', 'qcitems-0-notes': '',
        }
        response = self.client.post(self._result_url(), payload)
        self.assertRedirects(response, self._result_url())
        item = QcInspectionItem.objects.get(inspection=self.inspection)
        self.assertFalse(item.image)


class QcCriteriaCrudTest(TestCase):
    """FR-QC-02: UI CRUD cho QcCriteria (list/create/update/toggle active) —
    RBAC theo Permission Matrix mục 1a: role QC và ADMIN có Create/Update trên
    module 'qc'; MANAGER/PURCHASING/ACCOUNTANT chỉ Read; STAFF không có quyền
    gì trên 'qc' (kể cả Read). ``TC-QC-CRIT-<seq>``.
    """

    def setUp(self):
        self.qc_user = User.objects.create_user(
            username='qc1', password='qc-pass-123', role=User.Role.QC)
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.admin = User.objects.create_user(
            username='admin', password='admin-pass-123', role=User.Role.ADMIN)
        self.criteria = QcCriteria.objects.create(
            category='Bột mì', name='Ngoại hình', pass_rule='Không mốc, không vón cục')
        self.client.force_login(self.qc_user)

    def _payload(self, **overrides):
        payload = {
            'category': 'Bột mì', 'name': 'Trọng lượng',
            'pass_rule': '25kg ± 0.5kg', 'fail_rule': 'Lệch quá 0.5kg', 'is_active': 'on',
        }
        payload.update(overrides)
        return payload

    def test_TC_QC_CRIT_001_qc_role_can_create(self):
        response = self.client.post(reverse('quality:qc_criteria_create'), self._payload())
        self.assertRedirects(response, reverse('quality:qc_criteria_list'))
        self.assertTrue(QcCriteria.objects.filter(category='Bột mì', name='Trọng lượng').exists())

    def test_TC_QC_CRIT_002_admin_can_create(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('quality:qc_criteria_create'), self._payload())
        self.assertRedirects(response, reverse('quality:qc_criteria_list'))

    def test_TC_QC_CRIT_003_manager_forbidden_to_create(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse('quality:qc_criteria_create'), self._payload())
        self.assertEqual(response.status_code, 403)

    def test_TC_QC_CRIT_004_manager_can_view_list(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('quality:qc_criteria_list'))
        self.assertEqual(response.status_code, 200)

    def test_TC_QC_CRIT_005_staff_forbidden_even_read(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('quality:qc_criteria_list'))
        self.assertEqual(response.status_code, 403)

    def test_TC_QC_CRIT_006_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('quality:qc_criteria_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_QC_CRIT_007_duplicate_category_name_rejected(self):
        response = self.client.post(
            reverse('quality:qc_criteria_create'), self._payload(name='Ngoại hình'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(QcCriteria.objects.filter(category='Bột mì', name='Ngoại hình').count(), 1)

    def test_TC_QC_CRIT_008_update_criteria(self):
        response = self.client.post(
            reverse('quality:qc_criteria_update', args=[self.criteria.pk]),
            self._payload(name='Ngoại hình', pass_rule='Không mốc, không vón, không lẫn tạp chất'),
        )
        self.criteria.refresh_from_db()
        self.assertRedirects(response, reverse('quality:qc_criteria_list'))
        self.assertEqual(self.criteria.pass_rule, 'Không mốc, không vón, không lẫn tạp chất')

    def test_TC_QC_CRIT_009_toggle_active_deactivates_then_reactivates(self):
        url = reverse('quality:qc_criteria_toggle_active', args=[self.criteria.pk])
        self.client.post(url)
        self.criteria.refresh_from_db()
        self.assertFalse(self.criteria.is_active)

        self.client.post(url)
        self.criteria.refresh_from_db()
        self.assertTrue(self.criteria.is_active)

    def test_TC_QC_CRIT_010_manager_forbidden_to_toggle(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse('quality:qc_criteria_toggle_active', args=[self.criteria.pk]))
        self.assertEqual(response.status_code, 403)


class QcCriteriaListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (category/status/tìm kiếm) trên qc_criteria_list."""

    def setUp(self):
        self.qc_user = User.objects.create_user(
            username='qc1', password='qc-pass-123', role=User.Role.QC)
        self.client.force_login(self.qc_user)
        QcCriteria.objects.bulk_create([
            QcCriteria(
                category='Bột mì' if i % 2 == 0 else 'Đường',
                name=f'Tiêu chuẩn {i}', pass_rule='OK', is_active=(i % 2 == 0),
            )
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('quality:qc_criteria_list'))
        self.assertEqual(len(response.context['criteria']), 30)

    def test_page_size_50_shows_all(self):
        response = self.client.get(reverse('quality:qc_criteria_list'), {'page_size': 50})
        self.assertEqual(len(response.context['criteria']), 35)

    def test_filter_category(self):
        response = self.client.get(
            reverse('quality:qc_criteria_list'), {'category': 'Đường', 'page_size': 50})
        self.assertTrue(all(c.category == 'Đường' for c in response.context['criteria']))

    def test_filter_search_by_name(self):
        response = self.client.get(reverse('quality:qc_criteria_list'), {'q': 'Tiêu chuẩn 1'})
        names = [c.name for c in response.context['criteria']]
        self.assertIn('Tiêu chuẩn 1', names)
        self.assertTrue(all('Tiêu chuẩn 1' in n for n in names))
