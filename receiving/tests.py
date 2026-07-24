import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog
from catalog.models import Product
from partners.models import Supplier
from purchasing.models import PurchaseOrder
from quality.models import QcInspection
from warehouse.models import Location, Warehouse

from .models import Grn, GrnItem, GrnReturn

User = get_user_model()


class GrnModelTest(TestCase):
    """GRN (mục 2a — schema/model, chưa có workflow). ``TC-GRN-<seq>``."""

    def setUp(self):
        self.creator = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.other_supplier = Supplier.objects.create(supplier_code='NCC-0002', name='Công ty XYZ')
        self.po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')

    def _create_grn(self, **overrides):
        payload = dict(po=self.po, supplier=self.supplier, created_by=self.creator)
        payload.update(overrides)
        return Grn.objects.create(**payload)

    def test_TC_GRN_001_001_grn_no_auto_generated_with_current_yyyymm_prefix(self):
        grn = self._create_grn()
        today = datetime.date.today()
        self.assertEqual(grn.grn_no, f'GRN-{today:%Y%m}-001')

    def test_TC_GRN_001_002_grn_no_sequence_increments_within_same_month(self):
        first = self._create_grn()
        second = self._create_grn()
        self.assertEqual(first.grn_no[:-3], second.grn_no[:-3])
        self.assertEqual(int(second.grn_no[-3:]), int(first.grn_no[-3:]) + 1)

    def test_TC_GRN_001_003_grn_no_not_overwritten_on_update(self):
        grn = self._create_grn()
        original_no = grn.grn_no
        grn.notes = 'cập nhật ghi chú'
        grn.save()
        grn.refresh_from_db()
        self.assertEqual(grn.grn_no, original_no)

    def test_TC_GRN_002_001_default_status_is_draft(self):
        grn = self._create_grn()
        self.assertEqual(grn.status, Grn.Status.DRAFT)

    def test_TC_GRN_002_002_supplier_must_match_po_supplier(self):
        grn = Grn(po=self.po, supplier=self.other_supplier, created_by=self.creator)
        with self.assertRaises(ValidationError):
            grn.full_clean()


class GrnItemModelTest(TestCase):
    """GRN Items (mục 2a). ``TC-GRN-<seq>``."""

    def setUp(self):
        self.creator = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.creator)

    def test_TC_GRN_004_001_qty_received_greater_than_qty_ordered_rejected(self):
        """Phụ lục B: qty_received > qty_ordered -> lỗi, không cho save (BR-GRN-001/002)."""
        item = GrnItem(
            grn=self.grn, product=self.product, qty_ordered=10, qty_received=11,
            unit_price=Decimal('15000.00'),
        )
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_TC_GRN_004_002_qty_received_equal_qty_ordered_accepted(self):
        item = GrnItem(
            grn=self.grn, product=self.product, qty_ordered=10, qty_received=10,
            unit_price=Decimal('15000.00'),
        )
        item.full_clean()  # không raise

    def test_TC_GRN_005_001_exp_date_must_be_after_mfg_date(self):
        item = GrnItem(
            grn=self.grn, product=self.product, qty_ordered=10, qty_received=10,
            unit_price=Decimal('15000.00'),
            mfg_date=datetime.date(2026, 1, 10), exp_date=datetime.date(2026, 1, 10),
        )
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_TC_GRN_005_002_default_status_is_pending(self):
        item = GrnItem.objects.create(
            grn=self.grn, product=self.product, qty_ordered=10, qty_received=0,
            unit_price=Decimal('15000.00'),
        )
        self.assertEqual(item.status, GrnItem.Status.PENDING)


class GrnReturnModelTest(TestCase):
    """GRN_RETURN (mục 2c). ``TC-GRN-RET-<seq>``."""

    def setUp(self):
        self.creator = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        self.grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.creator)

    def test_TC_GRN_RET_001_001_default_status_pending_and_reason_qc_fail(self):
        ret = GrnReturn.objects.create(grn=self.grn)
        self.assertEqual(ret.status, GrnReturn.Status.PENDING)
        self.assertEqual(ret.reason, 'QC Fail')


class GrnViewTest(TestCase):
    """Views/forms/URLs mục 2a (Task 3) — DRAFT tạo/sửa/submit, PENDING_QC nhập Qty
    + Submit to QC. ``TC-GRN-VIEW-<seq>``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.qc_user = User.objects.create_user(
            username='qc1', password='qc-pass-123', role=User.Role.QC)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.client.force_login(self.staff)

    def _create_payload(self, **overrides):
        payload = {
            'po': self.po.pk,
            'supplier': self.supplier.pk,
            'grn_date': '2026-07-24',
            'expected_arrival_date': '',
            'notes': '',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-qty_ordered': 10,
            'items-0-unit_price': '15000.00',
            'items-0-mfg_date': '',
            'items-0-exp_date': '',
            'items-0-batch_code': '',
            'action': 'save',
        }
        payload.update(overrides)
        return payload

    def _create_grn(self, status=Grn.Status.DRAFT, qty_received=0):
        grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.staff, status=status)
        GrnItem.objects.create(
            grn=grn, product=self.product, qty_ordered=10, qty_received=qty_received,
            unit_price=Decimal('15000.00'),
        )
        return grn

    def test_TC_GRN_VIEW_001_001_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('receiving:grn_create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_GRN_VIEW_001_002_qc_role_forbidden_to_create(self):
        self.client.force_login(self.qc_user)
        response = self.client.post(reverse('receiving:grn_create'), self._create_payload())
        self.assertEqual(response.status_code, 403)

    def test_TC_GRN_VIEW_001_003_staff_can_save_draft(self):
        response = self.client.post(reverse('receiving:grn_create'), self._create_payload())
        grn = Grn.objects.get(supplier=self.supplier)
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[grn.pk]))
        self.assertEqual(grn.status, Grn.Status.DRAFT)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(grn.pk)).exists())

    def test_TC_GRN_VIEW_001_004_staff_can_save_and_submit_in_one_step(self):
        response = self.client.post(reverse('receiving:grn_create'), self._create_payload(action='submit'))
        grn = Grn.objects.get(supplier=self.supplier)
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[grn.pk]))
        self.assertEqual(grn.status, Grn.Status.PENDING_QC)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.UPDATE, target_id=str(grn.pk)).exists())

    def test_TC_GRN_VIEW_002_001_submit_transitions_draft_to_pending_qc(self):
        grn = self._create_grn()
        response = self.client.post(reverse('receiving:grn_submit', args=[grn.pk]))
        grn.refresh_from_db()
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[grn.pk]))
        self.assertEqual(grn.status, Grn.Status.PENDING_QC)

    def test_TC_GRN_VIEW_002_002_submit_rejected_when_not_draft(self):
        grn = self._create_grn(status=Grn.Status.PENDING_QC)
        self.client.post(reverse('receiving:grn_submit', args=[grn.pk]))
        grn.refresh_from_db()
        self.assertEqual(grn.status, Grn.Status.PENDING_QC)

    def test_TC_GRN_VIEW_003_001_receive_qty_saves_qty_and_starts_qc(self):
        grn = self._create_grn(status=Grn.Status.PENDING_QC)
        item = grn.items.first()
        response = self.client.post(reverse('receiving:grn_receive_qty', args=[grn.pk]), {
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-id': item.pk, 'items-0-qty_received': 9,
            'inspector': self.qc_user.pk,
        })
        grn.refresh_from_db()
        item.refresh_from_db()
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[grn.pk]))
        self.assertEqual(grn.status, Grn.Status.QC_IN_PROGRESS)
        self.assertEqual(item.qty_received, 9)
        inspection = QcInspection.objects.get(grn=grn)
        self.assertEqual(inspection.inspector, self.qc_user)
        self.assertEqual(inspection.status, QcInspection.Result.PENDING_QC)

    def test_TC_GRN_VIEW_004_001_detail_readable_by_qc_role(self):
        grn = self._create_grn()
        self.client.force_login(self.qc_user)
        response = self.client.get(reverse('receiving:grn_detail', args=[grn.pk]))
        self.assertEqual(response.status_code, 200)
