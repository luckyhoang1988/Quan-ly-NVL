import datetime
import shutil
import tempfile
import threading
import time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.db.utils import OperationalError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Notification
from catalog.models import Product
from inventory.models import Batch, Inventory, StockMovement, WarehouseHandoff
from inventory.services import accept_handoff, reject_handoff
from partners.models import Supplier
from purchasing.models import PurchaseOrder
from receiving.models import Grn, GrnItem, GrnReturn
from warehouse.models import Location, Warehouse
from warehouse.services import get_staging_warehouse

from .forms import QcInspectionItemForm, QcResultForm
from .models import QcCriteria, QcInspection, QcInspectionItem, validate_image_upload
from .services import (
    _get_staging_batch,
    _lock_pending_inspection,
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

    def test_TC_QC_SAMPLE_004_fixed_zero_value_floors_at_1(self):
        product = Product.objects.create(
            product_code='NVL-F0', name='Muối tinh', uom='kg',
            qc_sampling_method=Product.SamplingMethod.FIXED, qc_sampling_value=0)
        self.assertEqual(suggested_sample_qty(product, 100), 1)


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

    def _add_criteria_item(self, inspection, result=QcInspectionItem.Result.PASS, grn_item=None):
        return QcInspectionItem.objects.create(
            inspection=inspection, grn_item=grn_item or self.grn_item,
            criteria_name='Ngoại hình', result=result,
        )


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
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        self.assertEqual(overdue_inspections().count(), 0)


class QcPassTransactionTest(QcServiceTestBase):
    """QC PASS (mục 2c). ``TC-QC-PASS-<seq>``."""

    def test_TC_QC_PASS_001_001_consumes_staging_batch_and_credits_main_inventory(self):
        inspection = self._start_qc()
        staging_batch = Batch.objects.get(grn_item=self.grn_item, status=Batch.Status.ACTIVE)
        self._add_criteria_item(inspection)
        grn = qc_pass(inspection, actor=self.qc_user, location=self.location)

        self.assertEqual(grn.status, Grn.Status.RECEIVED)
        staging_batch.refresh_from_db()
        self.assertEqual(staging_batch.status, Batch.Status.CLOSED)
        # Phase D: batch đích PASS dừng ở PENDING_RECEIPT, chưa ACTIVE (chờ kho xác nhận).
        batch = Batch.objects.get(status=Batch.Status.PENDING_RECEIPT)
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
        handoff = WarehouseHandoff.objects.get(batch=batch)
        self.assertEqual(handoff.status, WarehouseHandoff.Status.PENDING)
        self.assertEqual(handoff.qc_inspection, inspection)
        self.assertEqual(handoff.destination_warehouse, self.warehouse)
        self.assertIsNone(handoff.assigned_to)

    def test_TC_QC_PASS_001_002_raises_when_inspection_already_resolved(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
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
        self._add_criteria_item(inspection)
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
        self._add_criteria_item(inspection)
        qc_fail(inspection, actor=self.qc_user)
        with self.assertRaises(ValidationError):
            qc_fail(inspection, actor=self.qc_user)


class QcPartialPassTransactionTest(QcServiceTestBase):
    """PARTIAL_PASS (mục 2c) — split Batch ACTIVE+QUARANTINE. ``TC-QC-PARTIAL-<seq>``."""

    def test_TC_QC_PARTIAL_001_001_splits_batch_and_credits_only_passed_qty(self):
        inspection = self._start_qc()
        staging_batch = Batch.objects.get(grn_item=self.grn_item, status=Batch.Status.ACTIVE)
        self._add_criteria_item(inspection)
        grn = qc_partial_pass(
            inspection, {self.grn_item.pk: 6}, actor=self.qc_user, location=self.location)

        self.assertEqual(grn.status, Grn.Status.RECEIVED)
        staging_batch.refresh_from_db()
        self.assertEqual(staging_batch.status, Batch.Status.CLOSED)
        # Phase D: phần pass dừng ở PENDING_RECEIPT (chờ kho xác nhận) — chỉ phần fail vào QUARANTINE ngay.
        active = Batch.objects.get(status=Batch.Status.PENDING_RECEIPT)
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
        self.assertTrue(WarehouseHandoff.objects.filter(batch=active, status=WarehouseHandoff.Status.PENDING).exists())
        self.assertFalse(WarehouseHandoff.objects.filter(batch=quarantine).exists())

    def test_TC_QC_PARTIAL_002_001_missing_item_result_raises(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        with self.assertRaises(ValidationError):
            qc_partial_pass(inspection, {}, actor=self.qc_user, location=self.location)

    def test_TC_QC_PARTIAL_003_001_qty_pass_greater_than_qty_received_raises(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        with self.assertRaises(ValidationError):
            qc_partial_pass(
                inspection, {self.grn_item.pk: 11}, actor=self.qc_user, location=self.location)

    def test_TC_QC_PARTIAL_004_001_rejects_non_main_warehouse_location(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_partial_pass(
                inspection, {self.grn_item.pk: 6}, actor=self.qc_user, location=self.scrap_location)

    def test_TC_QC_PARTIAL_005_001_all_zero_qty_pass_raises(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        with self.assertRaises(ValidationError):
            qc_partial_pass(
                inspection, {self.grn_item.pk: 0}, actor=self.qc_user, location=self.location)


class QcCriteriaGateTest(QcServiceTestBase):
    """Wave 2 — gate ≥1 dòng criteria trước khi PASS/FAIL/PARTIAL. TC-QC-CRIT-001..004."""

    def test_TC_QC_CRIT_001_qc_pass_rejects_when_no_items(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_pass(inspection, actor=self.qc_user, location=self.location)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.PENDING_QC)
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.QC_IN_PROGRESS)

    def test_TC_QC_CRIT_002_qc_fail_rejects_when_no_items(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_fail(inspection, actor=self.qc_user, reason='x')
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.PENDING_QC)

    def test_TC_QC_CRIT_003_qc_partial_pass_rejects_when_no_items(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_partial_pass(
                inspection, {self.grn_item.pk: 5}, actor=self.qc_user, location=self.location)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.PENDING_QC)

    def test_TC_QC_CRIT_004_qc_pass_succeeds_with_one_item(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.PASS)


class QcCriteriaGateToctouTest(QcServiceTestBase):
    """TOCTOU giữa lần check ``inspection.items.exists()`` SỚM (trước khoá) và
    lúc khoá thật (``_lock_pending_inspection``): form riêng "Lưu kết quả từng
    tiêu chuẩn" (``QcInspectionItemFormSet``, có ``can_delete``) chạy trên 1
    request/transaction độc lập, không bị khoá ``Grn``/``QcInspection`` của
    ``qc_pass``/``qc_fail``/``qc_partial_pass`` chặn — 1 request khác có thể
    xoá hết dòng criteria và commit đúng lúc request hiện tại đã qua check sớm
    nhưng chưa khoá xong. Mô phỏng bằng ``side_effect`` patch lên
    ``_lock_pending_inspection`` (xoá item ngay trước khi khoá thật chạy) thay
    vì threading thật — cùng kỹ thuật ``receiving.tests`` đã dùng cho lớp TOCTOU
    ``*_update`` (patch ``get_object_or_404`` với side_effect mutate state giữa
    2 lần gọi, xem CLAUDE.md)."""

    def _delete_items_then_lock(self):
        def _sneaky_lock(inspection):
            QcInspectionItem.objects.filter(inspection=inspection).delete()
            return _lock_pending_inspection(inspection)

        return patch('quality.services._lock_pending_inspection', side_effect=_sneaky_lock)

    def test_qc_pass_rejected_when_items_deleted_between_precheck_and_lock(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        with self._delete_items_then_lock():
            with self.assertRaises(ValidationError):
                qc_pass(inspection, actor=self.qc_user, location=self.location)
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.QC_IN_PROGRESS)
        self.assertFalse(Batch.objects.filter(status=Batch.Status.PENDING_RECEIPT).exists())
        inv = Inventory.objects.get(product=self.product, warehouse=self.staging_warehouse)
        self.assertEqual(inv.qty_on_hand, 10)

    def test_qc_fail_rejected_when_items_deleted_between_precheck_and_lock(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        with self._delete_items_then_lock():
            with self.assertRaises(ValidationError):
                qc_fail(inspection, actor=self.qc_user, reason='x')
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.QC_IN_PROGRESS)
        self.assertFalse(Batch.objects.filter(status=Batch.Status.QUARANTINE).exists())

    def test_qc_partial_pass_rejected_when_items_deleted_between_precheck_and_lock(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        with self._delete_items_then_lock():
            with self.assertRaises(ValidationError):
                qc_partial_pass(
                    inspection, {self.grn_item.pk: 6}, actor=self.qc_user, location=self.location)
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.QC_IN_PROGRESS)
        self.assertFalse(Batch.objects.filter(status=Batch.Status.PENDING_RECEIPT).exists())
        self.assertFalse(Batch.objects.filter(status=Batch.Status.QUARANTINE).exists())


class QcCriteriaItemPostLockRaceTest(TransactionTestCase):
    """Residual TOCTOU tìm thấy ở vòng re-review sau bản vá đầu (P2 gốc): lần
    check ``_require_criteria_items`` NGAY SAU ``_lock_pending_inspection``
    ban đầu chỉ đọc lại (``inspection.items.exists()``, không khoá) — đóng
    đúng khe hở "xoá TRƯỚC lần đọc lại thứ 2" (``QcCriteriaGateToctouTest`` ở
    trên), nhưng KHÔNG khoá được ``QcInspectionItem`` nên vẫn còn khe hở "xoá
    SAU lần đọc lại, TRƯỚC khi Batch/Inventory được tạo".

    Khe hở này chỉ tái hiện được bằng 2 transaction/2 kết nối DB THẬT chạy
    đồng thời — trong 1 transaction/1 luồng duy nhất (như
    ``QcCriteriaGateToctouTest`` dùng qua ``side_effect``) không có "khe hở"
    thật giữa 2 câu lệnh SQL kế tiếp để mô phỏng. Dùng ``TransactionTestCase``
    + 2 thread, cùng kỹ thuật các lớp Deadlock ở ``stocktake.tests``
    (BUG-15/16/17, xem CLAUDE.md).

    Fix (``quality.services._require_criteria_items(inspection, lock=True)``):
    lần kiểm thứ 2 giờ khoá thật từng dòng ``QcInspectionItem`` qua
    ``select_for_update()`` — 1 DELETE đồng thời đúng lúc này phải CHỜ tới khi
    transaction QC (PASS/FAIL/PARTIAL) commit/rollback.
    """

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
        Location.objects.create(warehouse=self.staging_warehouse, code='A-01')
        self.scrap_warehouse = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        Location.objects.create(warehouse=self.scrap_warehouse, code='A-01')
        self.grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.purchasing_user)
        self.grn_item = GrnItem.objects.create(
            grn=self.grn, product=self.product, qty_ordered=10, qty_received=10,
            unit_price=Decimal('15000.00'),
        )
        self.inspection = start_qc(self.grn, self.qc_user, actor=self.qc_user)
        self.criteria_item = QcInspectionItem.objects.create(
            inspection=self.inspection, grn_item=self.grn_item,
            criteria_name='Ngoại hình', result=QcInspectionItem.Result.PASS,
        )

    def test_TC_QC_CRIT_010_concurrent_delete_of_criteria_item_blocks_until_qc_pass_commits(self):
        """A (``qc_pass``): khoá ``QcInspectionItem`` ở lần kiểm thứ 2 rồi bị
        giữ lại (patch ``get_staging_warehouse`` — gọi ngay sau lần kiểm đó —
        để sleep, mô phỏng transaction còn đang mở). B: DELETE đúng dòng
        criteria đó qua transaction/kết nối riêng, chỉ bắt đầu SAU khi chắc
        chắn A đã khoá xong (``reached_lock`` event). Kỳ vọng: không deadlock
        (không ``OperationalError``), và DELETE của B phải chờ hết thời gian A
        giữ khoá mới hoàn tất — chứng minh khe hở đã được đóng bằng khoá thật,
        không chỉ đọc lại.
        """
        reached_lock = threading.Event()
        results = {}
        hold_seconds = 0.6

        def slow_get_staging_warehouse():
            reached_lock.set()
            time.sleep(hold_seconds)
            return get_staging_warehouse()

        def run_qc_pass():
            try:
                with patch('quality.services.get_staging_warehouse', side_effect=slow_get_staging_warehouse):
                    qc_pass(self.inspection, actor=self.qc_user, location=self.location)
                results['qc_pass'] = 'ok'
            except Exception as exc:  # noqa: BLE001 - ghi lại để assert bên ngoài thread
                results['qc_pass'] = exc
            finally:
                connection.close()

        def run_delete():
            try:
                if not reached_lock.wait(timeout=5):
                    results['delete'] = TimeoutError('reached_lock không được set trong 5s')
                    return
                start = time.monotonic()
                with transaction.atomic():
                    QcInspectionItem.objects.filter(pk=self.criteria_item.pk).delete()
                results['delete_elapsed'] = time.monotonic() - start
            except Exception as exc:  # noqa: BLE001
                results['delete'] = exc
            finally:
                connection.close()

        t_pass = threading.Thread(target=run_qc_pass)
        t_delete = threading.Thread(target=run_delete)
        t_pass.start()
        t_delete.start()
        t_pass.join(timeout=10)
        t_delete.join(timeout=10)

        self.assertFalse(t_pass.is_alive(), 'qc_pass bị treo quá 10s (nghi deadlock)')
        self.assertFalse(t_delete.is_alive(), 'DELETE bị treo quá 10s (nghi deadlock)')

        for name in ('qc_pass', 'delete'):
            exc = results.get(name)
            if isinstance(exc, Exception):
                self.assertNotIsInstance(
                    exc, OperationalError,
                    f'{name} raised {exc!r} — nghi deadlock (residual TOCTOU regression)',
                )
                if name == 'delete':
                    self.fail(f'DELETE raised unexpected exception: {exc!r}')

        self.assertEqual(results.get('qc_pass'), 'ok')
        self.assertGreaterEqual(
            results.get('delete_elapsed', 0), hold_seconds * 0.7,
            'DELETE trên QcInspectionItem không bị chặn bởi khoá select_for_update của qc_pass — '
            'residual TOCTOU chưa được đóng.',
        )


class WarehouseHandoffTest(QcServiceTestBase):
    """Bàn giao batch PASS/PARTIAL_PASS (``PENDING_RECEIPT``) cho kho xác nhận
    nhận hàng (Phase D, mục 5/6) — ``create_handoff``/``accept_handoff``/
    ``reject_handoff``. ``TC-QC-HANDOFF-<seq>``.
    """

    def setUp(self):
        super().setUp()
        self.warehouse_staff = User.objects.create_user(
            username='nvk1', password='nvk-pass-123', role=User.Role.STAFF,
            department=User.Department.WAREHOUSE)
        self.warehouse_manager = User.objects.create_user(
            username='qlk-wh', password='qlk-pass-123', role=User.Role.MANAGER,
            department=User.Department.WAREHOUSE, is_manager=True)
        self.qc_dept_user = User.objects.create_user(
            username='qc-dept', password='qc-pass-123', role=User.Role.QC,
            department=User.Department.QC)

    def test_TC_QC_HANDOFF_001_001_no_assigned_to_notifies_department_fallback(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        batch = Batch.objects.get(status=Batch.Status.PENDING_RECEIPT)
        handoff = WarehouseHandoff.objects.get(batch=batch)
        self.assertIsNone(handoff.assigned_to)
        # Warehouse.staff rỗng cho kho đích -> fallback toàn bộ department=WAREHOUSE.
        self.assertTrue(
            Notification.objects.filter(recipient=self.warehouse_staff, target_id=str(handoff.pk)).exists())
        self.assertTrue(
            Notification.objects.filter(recipient=self.warehouse_manager, target_id=str(handoff.pk)).exists())

    def test_TC_QC_HANDOFF_001_002_assigned_to_specific_staff_only_notifies_them(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location, assigned_to=self.warehouse_staff)
        batch = Batch.objects.get(status=Batch.Status.PENDING_RECEIPT)
        handoff = WarehouseHandoff.objects.get(batch=batch)
        self.assertEqual(handoff.assigned_to, self.warehouse_staff)
        self.assertTrue(
            Notification.objects.filter(recipient=self.warehouse_staff, target_id=str(handoff.pk)).exists())
        self.assertFalse(
            Notification.objects.filter(recipient=self.warehouse_manager, target_id=str(handoff.pk)).exists())

    def test_TC_QC_HANDOFF_002_001_accept_transitions_batch_to_active_and_notifies_inspector(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        batch = Batch.objects.get(status=Batch.Status.PENDING_RECEIPT)
        handoff = WarehouseHandoff.objects.get(batch=batch)
        accept_handoff(handoff, actor=self.warehouse_staff)
        batch.refresh_from_db()
        handoff.refresh_from_db()
        self.assertEqual(batch.status, Batch.Status.ACTIVE)
        self.assertEqual(handoff.status, WarehouseHandoff.Status.ACCEPTED)
        self.assertEqual(handoff.decided_by, self.warehouse_staff)
        self.assertIsNotNone(handoff.decided_at)
        self.assertTrue(
            Notification.objects.filter(recipient=self.qc_user, target_id=str(handoff.pk)).exists())

    def test_TC_QC_HANDOFF_002_002_accept_already_decided_raises(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        handoff = WarehouseHandoff.objects.get(batch__status=Batch.Status.PENDING_RECEIPT)
        accept_handoff(handoff, actor=self.warehouse_staff)
        with self.assertRaises(ValidationError):
            accept_handoff(handoff, actor=self.warehouse_staff)

    def test_TC_QC_HANDOFF_003_001_reject_to_scrap_moves_batch_and_updates_inventory(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        batch = Batch.objects.get(status=Batch.Status.PENDING_RECEIPT)
        handoff = WarehouseHandoff.objects.get(batch=batch)
        reject_handoff(
            handoff, actor=self.warehouse_staff, reason='Hàng bị ẩm khi kiểm tra lại',
            destination=WarehouseHandoff.RejectDestination.TO_SCRAP,
        )
        handoff.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(handoff.status, WarehouseHandoff.Status.REJECTED)
        self.assertEqual(handoff.reject_destination, WarehouseHandoff.RejectDestination.TO_SCRAP)
        self.assertEqual(handoff.reject_reason, 'Hàng bị ẩm khi kiểm tra lại')
        # Batch PENDING_RECEIPT gốc bị đóng lại (đã tách hết qty sang Kho phế).
        self.assertEqual(batch.status, Batch.Status.CLOSED)
        scrap_batch = Batch.objects.get(status=Batch.Status.QUARANTINE, batch_code__endswith='-REJ')
        self.assertEqual(scrap_batch.qty_received, 10)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 0)
        scrap_inv = Inventory.objects.get(product=self.product, warehouse=self.scrap_warehouse)
        self.assertEqual(scrap_inv.qty_on_hand, 10)
        self.assertTrue(
            Notification.objects.filter(recipient=self.qc_dept_user, target_id=str(handoff.pk)).exists())

    def test_TC_QC_HANDOFF_003_002_reject_back_to_qc_leaves_batch_untouched(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        batch = Batch.objects.get(status=Batch.Status.PENDING_RECEIPT)
        handoff = WarehouseHandoff.objects.get(batch=batch)
        reject_handoff(
            handoff, actor=self.warehouse_staff, reason='Nghi ngờ sai lô, cần QC kiểm tra lại',
            destination=WarehouseHandoff.RejectDestination.BACK_TO_QC,
        )
        handoff.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(handoff.status, WarehouseHandoff.Status.REJECTED)
        self.assertEqual(handoff.reject_destination, WarehouseHandoff.RejectDestination.BACK_TO_QC)
        # KHÔNG đảo ngược Batch/Inventory — batch vẫn PENDING_RECEIPT tại kho MAIN, chờ xử lý thủ công.
        self.assertEqual(batch.status, Batch.Status.PENDING_RECEIPT)
        inv = Inventory.objects.get(product=self.product, warehouse=self.warehouse)
        self.assertEqual(inv.qty_on_hand, 10)
        self.assertTrue(
            Notification.objects.filter(recipient=self.qc_dept_user, target_id=str(handoff.pk)).exists())

    def test_TC_QC_HANDOFF_003_003_reject_without_reason_raises(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        handoff = WarehouseHandoff.objects.get(batch__status=Batch.Status.PENDING_RECEIPT)
        with self.assertRaises(ValidationError):
            reject_handoff(
                handoff, actor=self.warehouse_staff, reason='',
                destination=WarehouseHandoff.RejectDestination.TO_SCRAP,
            )

    def test_TC_QC_HANDOFF_003_004_reject_already_decided_raises(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        handoff = WarehouseHandoff.objects.get(batch__status=Batch.Status.PENDING_RECEIPT)
        accept_handoff(handoff, actor=self.warehouse_staff)
        with self.assertRaises(ValidationError):
            reject_handoff(
                handoff, actor=self.warehouse_staff, reason='quá trễ',
                destination=WarehouseHandoff.RejectDestination.TO_SCRAP,
            )


class GetStagingBatchTest(QcServiceTestBase):
    """``_get_staging_batch`` — helper nội bộ tìm batch Kho chờ theo lineage
    ``grn_item``. ``TC-QC-STG-<seq>``.
    """

    def test_TC_QC_STG_001_raises_when_grn_not_yet_submitted_to_qc(self):
        with self.assertRaises(ValidationError):
            _get_staging_batch(self.grn_item)

    def test_TC_QC_STG_002_raises_after_batch_already_consumed(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
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


class QcInspectionItemFormWidgetTest(QcServiceTestBase):
    """TC-QC-CRIT-009 (nửa đầu — data-category). Nửa sau (context criteria_by_category)
    ở Task 3.2, `test_TC_QC_CRIT_009_02_...` — FSD gộp 2 test này chung 1 dòng TC-009 (AC 06,07),
    tách thành 2 method riêng cho rõ ràng khi implement, giữ chung tiền tố ID."""

    def test_TC_QC_CRIT_009_01_grn_item_option_has_data_category(self):
        self.product.category = 'Bột mì'
        self.product.save(update_fields=['category'])
        form = QcInspectionItemForm(grn=self.grn)
        rendered = str(form['grn_item'])
        self.assertIn('data-category="Bột mì"', rendered)


class QcResultViewTest(QcServiceTestBase):
    """View ``qc_result`` (Task 3) — 3 nút Pass/Fail/Partial gọi thẳng quality.services
    đã có unit test riêng ở trên; test ở đây chỉ xác nhận view/form/permission nối
    đúng dây. ``TC-QC-VIEW-<seq>``.
    """

    def setUp(self):
        super().setUp()
        self.manager = User.objects.create_user(
            username='qlk', password='qlk-pass-123', role=User.Role.MANAGER)
        self.inspection = self._start_qc()
        self._add_criteria_item(self.inspection)
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
        self.assertTrue(Batch.objects.filter(product=self.product, status=Batch.Status.PENDING_RECEIPT).exists())
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
        self.assertTrue(Batch.objects.filter(status=Batch.Status.PENDING_RECEIPT, qty_received=6).exists())
        self.assertTrue(Batch.objects.filter(status=Batch.Status.QUARANTINE, qty_received=4).exists())

    def test_TC_QC_VIEW_001_006_manager_can_also_submit_result(self):
        self.client.force_login(self.manager)
        response = self.client.post(self._url(), self._payload(action='pass'))
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.RECEIVED)

    def test_TC_QC_CRIT_005_view_disables_actions_when_no_items(self):
        empty_grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.purchasing_user)
        empty_item = GrnItem.objects.create(
            grn=empty_grn, product=self.product, qty_ordered=5, qty_received=5,
            unit_price=Decimal('15000.00'),
        )
        start_qc(empty_grn, self.qc_user, actor=self.qc_user)
        response = self.client.get(reverse('quality:qc_result', args=[empty_grn.pk]))
        self.assertContains(response, 'disabled', count=3)
        self.assertContains(response, 'Chưa có dòng kết quả tiêu chuẩn nào')

    def test_TC_QC_CRIT_005_02_post_action_pass_bypass_blocked_when_no_items(self):
        empty_grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.purchasing_user)
        empty_item = GrnItem.objects.create(
            grn=empty_grn, product=self.product, qty_ordered=5, qty_received=5,
            unit_price=Decimal('15000.00'),
        )
        start_qc(empty_grn, self.qc_user, actor=self.qc_user)
        response = self.client.post(reverse('quality:qc_result', args=[empty_grn.pk]), {
            'action': 'pass', 'location': self.location.pk, 'reason': '',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-id': empty_item.pk, 'items-0-qty_pass': 5,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ít nhất 1 dòng kết quả kiểm tra tiêu chuẩn')
        empty_grn.refresh_from_db()
        self.assertEqual(empty_grn.status, Grn.Status.QC_IN_PROGRESS)
        self.assertFalse(Batch.objects.filter(grn_item=empty_item, status=Batch.Status.PENDING_RECEIPT).exists())

    def test_TC_QC_CRIT_006_fail_item_warns_near_pass_partial_buttons(self):
        self._add_criteria_item(self.inspection, result=QcInspectionItem.Result.FAIL)
        response = self.client.get(self._url())
        self.assertContains(response, 'Đã có dòng tiêu chuẩn Không đạt', count=2)

    def test_TC_QC_CRIT_007_pass_item_warns_near_fail_button(self):
        response = self.client.get(self._url())
        self.assertContains(response, 'Đã có dòng tiêu chuẩn Đạt', count=1)

    def test_TC_QC_CRIT_008_no_mismatch_when_only_pass_items(self):
        response = self.client.get(self._url())
        self.assertNotContains(response, 'Đã có dòng tiêu chuẩn Không đạt')

    def test_TC_QC_CRIT_009_02_context_criteria_by_category_only_active(self):
        QcCriteria.objects.create(category='Bột mì', name='Trọng lượng', pass_rule='1000g ± 10g')
        QcCriteria.objects.create(category='Bột mì', name='Cũ', is_active=False)
        response = self.client.get(self._url())
        self.assertIn('Trọng lượng', response.context['criteria_by_category']['Bột mì'][0]['name'])
        self.assertEqual(len(response.context['criteria_by_category']['Bột mì']), 1)

    def test_TC_WH_CAP_005_pass_action_over_capacity_location_warns(self):
        self.location.capacity = 5
        self.location.save(update_fields=['capacity'])
        response = self.client.post(self._url(), self._payload(action='pass'), follow=True)
        warning_messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('vượt dung tích' in m for m in warning_messages))
        self.assertTrue(Batch.objects.filter(product=self.product, status=Batch.Status.PENDING_RECEIPT).exists())

    def test_TC_WH_CAP_006_partial_action_over_capacity_location_warns(self):
        self.location.capacity = 3
        self.location.save(update_fields=['capacity'])
        response = self.client.post(
            self._url(), self._payload(**{'items-0-qty_pass': 6, 'action': 'partial'}), follow=True)
        warning_messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('vượt dung tích' in m for m in warning_messages))


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
        self._add_criteria_item(self.inspection)
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


class ImageUploadValidatorTest(TestCase):
    """SEC: ``validate_image_upload`` (giới hạn dung lượng + phần mở rộng ảnh
    QC evidence/reference) — ``ImageField`` mặc định của Django chỉ xác nhận
    file mở được bằng Pillow, không tự giới hạn size/whitelist type.
    """

    def test_TC_QC_IMG_004_oversized_image_rejected(self):
        big_content = SMALL_GIF + b'0' * (6 * 1024 * 1024)
        image = SimpleUploadedFile('big.gif', big_content, content_type='image/gif')
        with self.assertRaises(ValidationError):
            validate_image_upload(image)

    def test_TC_QC_IMG_005_disallowed_extension_rejected(self):
        image = SimpleUploadedFile('evidence.bmp', SMALL_GIF, content_type='image/bmp')
        with self.assertRaises(ValidationError):
            validate_image_upload(image)

    def test_TC_QC_IMG_006_allowed_extension_and_size_passes(self):
        image = SimpleUploadedFile('evidence.gif', SMALL_GIF, content_type='image/gif')
        validate_image_upload(image)


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
