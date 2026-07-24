import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog
from catalog.models import Product
from partners.models import Supplier
from purchasing.models import PurchaseOrder, PurchaseOrderItem
from quality.models import QcInspection
from warehouse.models import Location, Warehouse

from .models import Grn, GrnItem, GrnReturn
from .services import approve_return, close_grn, close_return, mark_return_returned

User = get_user_model()


class GrnModelTest(TestCase):
    """GRN (mục 2a — schema/model, chưa có workflow). ``TC-GRN-<seq>``."""

    def setUp(self):
        self.creator = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.other_supplier = Supplier.objects.create(supplier_code='NCC-0002', name='Công ty XYZ')
        self.po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
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
        self.po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
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

    def test_TC_GRN_006_001_label_code_uses_batch_code_when_provided(self):
        """FR-GRN-06: NCC có mã lô riêng -> barcode dùng đúng mã đó."""
        item = GrnItem.objects.create(
            grn=self.grn, product=self.product, qty_ordered=10, qty_received=10,
            unit_price=Decimal('15000.00'), batch_code='LOT-NCC-9999',
        )
        self.assertEqual(item.label_code, 'LOT-NCC-9999')

    def test_TC_GRN_006_002_label_code_falls_back_to_grn_no_and_product_code(self):
        """FR-GRN-06: chưa có mã lô từ NCC -> fallback GRN_NO-product_code (cùng
        công thức với Batch.batch_code khi QC PASS)."""
        item = GrnItem.objects.create(
            grn=self.grn, product=self.product, qty_ordered=10, qty_received=10,
            unit_price=Decimal('15000.00'),
        )
        self.assertEqual(item.label_code, f'{self.grn.grn_no}-{self.product.product_code}')


class GrnReturnModelTest(TestCase):
    """GRN_RETURN (mục 2c). ``TC-GRN-RET-<seq>``."""

    def setUp(self):
        self.creator = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
        self.grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.creator)

    def test_TC_GRN_RET_001_001_default_status_pending_and_reason_qc_fail(self):
        ret = GrnReturn.objects.create(grn=self.grn)
        self.assertEqual(ret.status, GrnReturn.Status.PENDING)
        self.assertEqual(ret.reason, 'QC Fail')


class GrnCloseServiceTest(TestCase):
    """State CLOSED (mục 2a Workflow States) — ``close_grn``. ``TC-GRN-CLOSE-<seq>``."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)

    def _grn(self, status):
        return Grn.objects.create(
            po=self.po, supplier=self.supplier, created_by=self.manager, status=status)

    def test_TC_GRN_CLOSE_001_received_transitions_to_closed(self):
        grn = self._grn(Grn.Status.RECEIVED)
        close_grn(grn, actor=self.manager)
        grn.refresh_from_db()
        self.assertEqual(grn.status, Grn.Status.CLOSED)

    def test_TC_GRN_CLOSE_002_writes_audit_log(self):
        grn = self._grn(Grn.Status.RECEIVED)
        close_grn(grn, actor=self.manager)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.UPDATE, target_id=str(grn.pk)).exists())

    def test_TC_GRN_CLOSE_003_draft_cannot_be_closed(self):
        grn = self._grn(Grn.Status.DRAFT)
        with self.assertRaises(ValidationError):
            close_grn(grn, actor=self.manager)

    def test_TC_GRN_CLOSE_004_pending_qc_cannot_be_closed(self):
        grn = self._grn(Grn.Status.PENDING_QC)
        with self.assertRaises(ValidationError):
            close_grn(grn, actor=self.manager)

    def test_TC_GRN_CLOSE_005_rejected_with_unresolved_return_cannot_be_closed(self):
        grn = self._grn(Grn.Status.REJECTED)
        GrnReturn.objects.create(grn=grn)  # mặc định PENDING
        with self.assertRaises(ValidationError):
            close_grn(grn, actor=self.manager)
        grn.refresh_from_db()
        self.assertEqual(grn.status, Grn.Status.REJECTED)

    def test_TC_GRN_CLOSE_006_rejected_with_returned_return_can_be_closed(self):
        grn = self._grn(Grn.Status.REJECTED)
        GrnReturn.objects.create(grn=grn, status=GrnReturn.Status.RETURNED)
        close_grn(grn, actor=self.manager)
        grn.refresh_from_db()
        self.assertEqual(grn.status, Grn.Status.CLOSED)


class GrnReturnWorkflowServiceTest(TestCase):
    """GRN_RETURN Workflow (mục 2c): PENDING -> APPROVED -> RETURNED -> CLOSED.
    ``TC-GRN-RET-<seq>``.
    """

    def setUp(self):
        self.manager = User.objects.create_user(
            username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
        self.grn = Grn.objects.create(
            po=self.po, supplier=self.supplier, created_by=self.manager, status=Grn.Status.REJECTED)

    def test_TC_GRN_RET_002_001_approve_pending_to_approved(self):
        ret = GrnReturn.objects.create(grn=self.grn)
        approve_return(ret, actor=self.manager)
        ret.refresh_from_db()
        self.assertEqual(ret.status, GrnReturn.Status.APPROVED)

    def test_TC_GRN_RET_002_002_approve_rejects_when_not_pending(self):
        ret = GrnReturn.objects.create(grn=self.grn, status=GrnReturn.Status.APPROVED)
        with self.assertRaises(ValidationError):
            approve_return(ret, actor=self.manager)

    def test_TC_GRN_RET_003_001_mark_returned_approved_to_returned(self):
        ret = GrnReturn.objects.create(grn=self.grn, status=GrnReturn.Status.APPROVED)
        mark_return_returned(ret, actor=self.manager)
        ret.refresh_from_db()
        self.assertEqual(ret.status, GrnReturn.Status.RETURNED)

    def test_TC_GRN_RET_003_002_mark_returned_rejects_when_not_approved(self):
        ret = GrnReturn.objects.create(grn=self.grn)  # PENDING
        with self.assertRaises(ValidationError):
            mark_return_returned(ret, actor=self.manager)

    def test_TC_GRN_RET_004_001_close_returned_to_closed(self):
        ret = GrnReturn.objects.create(grn=self.grn, status=GrnReturn.Status.RETURNED)
        close_return(ret, actor=self.manager)
        ret.refresh_from_db()
        self.assertEqual(ret.status, GrnReturn.Status.CLOSED)

    def test_TC_GRN_RET_004_002_close_rejects_when_not_returned(self):
        ret = GrnReturn.objects.create(grn=self.grn, status=GrnReturn.Status.APPROVED)
        with self.assertRaises(ValidationError):
            close_return(ret, actor=self.manager)

    def test_TC_GRN_RET_005_001_full_lifecycle_then_grn_can_be_closed(self):
        ret = GrnReturn.objects.create(grn=self.grn)
        approve_return(ret, actor=self.manager)
        mark_return_returned(ret, actor=self.manager)
        close_return(ret, actor=self.manager)
        ret.refresh_from_db()
        self.assertEqual(ret.status, GrnReturn.Status.CLOSED)
        close_grn(self.grn, actor=self.manager)
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.CLOSED)


class GrnCloseAndReturnViewTest(TestCase):
    """View/URL/permission cho grn_close + grn_return_* (mục 2a/2c). ``TC-GRN-VIEW-<seq>``."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.manager = User.objects.create_user(
            username='qlk1', password='ql-pass-123', role=User.Role.MANAGER)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)

    def _grn(self, status, created_by=None):
        return Grn.objects.create(
            po=self.po, supplier=self.supplier, created_by=created_by or self.manager, status=status)

    def test_TC_GRN_VIEW_005_001_staff_forbidden_to_close_grn(self):
        self.client.force_login(self.staff)
        grn = self._grn(Grn.Status.RECEIVED)
        response = self.client.post(reverse('receiving:grn_close', args=[grn.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_GRN_VIEW_005_002_manager_can_close_received_grn(self):
        self.client.force_login(self.manager)
        grn = self._grn(Grn.Status.RECEIVED)
        response = self.client.post(reverse('receiving:grn_close', args=[grn.pk]))
        grn.refresh_from_db()
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[grn.pk]))
        self.assertEqual(grn.status, Grn.Status.CLOSED)

    def test_TC_GRN_VIEW_005_003_close_button_only_shown_for_received_or_rejected(self):
        self.client.force_login(self.manager)
        draft = self._grn(Grn.Status.DRAFT)
        response = self.client.get(reverse('receiving:grn_detail', args=[draft.pk]))
        self.assertNotContains(response, reverse('receiving:grn_close', args=[draft.pk]))
        received = self._grn(Grn.Status.RECEIVED)
        response = self.client.get(reverse('receiving:grn_detail', args=[received.pk]))
        self.assertContains(response, reverse('receiving:grn_close', args=[received.pk]))

    def test_TC_GRN_VIEW_006_001_staff_forbidden_to_approve_return(self):
        self.client.force_login(self.staff)
        grn = self._grn(Grn.Status.REJECTED)
        ret = GrnReturn.objects.create(grn=grn)
        response = self.client.post(reverse('receiving:grn_return_approve', args=[ret.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_GRN_VIEW_006_002_manager_can_approve_return(self):
        self.client.force_login(self.manager)
        grn = self._grn(Grn.Status.REJECTED)
        ret = GrnReturn.objects.create(grn=grn)
        response = self.client.post(reverse('receiving:grn_return_approve', args=[ret.pk]))
        ret.refresh_from_db()
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[grn.pk]))
        self.assertEqual(ret.status, GrnReturn.Status.APPROVED)

    def test_TC_GRN_VIEW_006_003_staff_can_mark_return_returned(self):
        """STAFF có 'update' trên grn (không có 'approve') -> đủ quyền xác nhận đã trả hàng."""
        self.client.force_login(self.staff)
        grn = self._grn(Grn.Status.REJECTED)
        ret = GrnReturn.objects.create(grn=grn, status=GrnReturn.Status.APPROVED)
        response = self.client.post(reverse('receiving:grn_return_mark_returned', args=[ret.pk]))
        ret.refresh_from_db()
        self.assertRedirects(response, reverse('receiving:grn_detail', args=[grn.pk]))
        self.assertEqual(ret.status, GrnReturn.Status.RETURNED)

    def test_TC_GRN_VIEW_006_004_invalid_transition_shows_error_message(self):
        self.client.force_login(self.manager)
        grn = self._grn(Grn.Status.REJECTED)
        ret = GrnReturn.objects.create(grn=grn)  # PENDING
        response = self.client.post(
            reverse('receiving:grn_return_mark_returned', args=[ret.pk]), follow=True)
        ret.refresh_from_db()
        self.assertEqual(ret.status, GrnReturn.Status.PENDING)
        error_messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('Không thể xác nhận đã trả hàng' in m for m in error_messages))


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
        self.po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('15000.00'))
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

    def test_TC_GRN_VIEW_003_002_receive_qty_out_of_tolerance_warns_and_updates_po_status(self):
        """FR-GRN-07: qty_received=5/10 (chênh 50%) vượt tolerance mặc định 5%
        của Supplier -> cảnh báo (không chặn) + PO đồng bộ PARTIAL_RECEIVED
        (FR-GRN-04, dùng chung sync_po_status)."""
        grn = self._create_grn(status=Grn.Status.PENDING_QC)
        item = grn.items.first()
        response = self.client.post(reverse('receiving:grn_receive_qty', args=[grn.pk]), {
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-id': item.pk, 'items-0-qty_received': 5,
            'inspector': self.qc_user.pk,
        }, follow=True)
        warning_messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('chênh' in m and 'vượt ngưỡng' in m for m in warning_messages))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.PARTIAL_RECEIVED)

    def test_TC_GRN_VIEW_003_003_receive_qty_within_tolerance_no_warning(self):
        """qty_received=10/10 (0% chênh) -> không cảnh báo."""
        grn = self._create_grn(status=Grn.Status.PENDING_QC)
        item = grn.items.first()
        response = self.client.post(reverse('receiving:grn_receive_qty', args=[grn.pk]), {
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-id': item.pk, 'items-0-qty_received': 10,
            'inspector': self.qc_user.pk,
        }, follow=True)
        warning_messages = [str(m) for m in response.context['messages']]
        self.assertFalse(any('vượt ngưỡng' in m for m in warning_messages))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.RECEIVED)


class GrnPoQtyValidationTest(TestCase):
    """BR mục 2a "Qty validation" (FR-GRN-07 hard cap) + FR-GRN-04 (nhận nhiều
    đợt/1 PO không vượt tổng Qty PO). ``TC-GRN-QTY-<seq>``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('15000.00'))
        self.client.force_login(self.staff)

    def _payload(self, qty_ordered, **overrides):
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
            'items-0-qty_ordered': qty_ordered,
            'items-0-unit_price': '15000.00',
            'items-0-mfg_date': '',
            'items-0-exp_date': '',
            'items-0-batch_code': '',
            'action': 'save',
        }
        payload.update(overrides)
        return payload

    def test_TC_GRN_QTY_001_qty_exceeding_po_remaining_rejected(self):
        response = self.client.post(reverse('receiving:grn_create'), self._payload(11))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Grn.objects.filter(po=self.po).exists())

    def test_TC_GRN_QTY_002_multi_shipment_within_po_remaining_allowed(self):
        """FR-GRN-04: 2 GRN riêng biệt cùng PO, tổng qty_ordered = đúng qty PO -> cho phép."""
        first = self.client.post(reverse('receiving:grn_create'), self._payload(6))
        self.assertEqual(first.status_code, 302)
        second = self.client.post(reverse('receiving:grn_create'), self._payload(4))
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Grn.objects.filter(po=self.po).count(), 2)

    def test_TC_GRN_QTY_003_third_shipment_exceeding_remaining_after_two_rejected(self):
        self.client.post(reverse('receiving:grn_create'), self._payload(6))
        self.client.post(reverse('receiving:grn_create'), self._payload(4))
        third = self.client.post(reverse('receiving:grn_create'), self._payload(1))
        self.assertEqual(third.status_code, 200)
        self.assertEqual(Grn.objects.filter(po=self.po).count(), 2)

    def test_TC_GRN_QTY_004_rejected_grn_item_freed_up_for_reship(self):
        """Item bị QC REJECT không tính vào tổng đã đặt -> PO có thể được giao lại."""
        first_resp = self.client.post(reverse('receiving:grn_create'), self._payload(10))
        first = Grn.objects.get(po=self.po)
        first.items.update(status=GrnItem.Status.REJECTED)
        second = self.client.post(reverse('receiving:grn_create'), self._payload(10))
        self.assertEqual(first_resp.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Grn.objects.filter(po=self.po).count(), 2)

    def test_TC_GRN_QTY_005_product_not_in_po_rejected(self):
        other_product = Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg')
        response = self.client.post(
            reverse('receiving:grn_create'),
            self._payload(5, **{'items-0-product': other_product.pk}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Grn.objects.filter(po=self.po).exists())


class GrnPrintViewTest(TestCase):
    """Trang in phiếu GRN kèm barcode (FR-GRN-06, Task 5). ``TC-GRN-006-<seq>``."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.grn = Grn.objects.create(
            po=self.po, supplier=self.supplier, created_by=self.staff, status=Grn.Status.RECEIVED)
        self.item = GrnItem.objects.create(
            grn=self.grn, product=self.product, qty_ordered=10, qty_received=10,
            unit_price=Decimal('15000.00'), batch_code='LOT-0001',
        )
        self.client.force_login(self.staff)

    def test_TC_GRN_006_003_print_view_returns_200_with_label_code_and_grn_no(self):
        response = self.client.get(reverse('receiving:grn_print', args=[self.grn.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn(self.grn.grn_no, content)
        self.assertIn('LOT-0001', content)

    def test_TC_GRN_006_004_print_view_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('receiving:grn_print', args=[self.grn.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_GRN_006_005_print_button_shown_on_detail_only_when_received(self):
        draft_grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.staff)
        response = self.client.get(reverse('receiving:grn_detail', args=[draft_grn.pk]))
        self.assertNotContains(response, reverse('receiving:grn_print', args=[draft_grn.pk]))
        response = self.client.get(reverse('receiving:grn_detail', args=[self.grn.pk]))
        self.assertContains(response, reverse('receiving:grn_print', args=[self.grn.pk]))


class GrnListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (status/supplier/tìm kiếm) trên grn_list."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.client.force_login(self.staff)
        self.supplier_a = Supplier.objects.create(supplier_code='NCC-A', name='NCC A')
        self.supplier_b = Supplier.objects.create(supplier_code='NCC-B', name='NCC B')
        po = PurchaseOrder.objects.create(po_no='PO-TEST-0001', supplier=self.supplier_a)
        Grn.objects.bulk_create([
            Grn(
                grn_no=f'GRN-TEST-{i:04d}', po=po,
                supplier=self.supplier_a if i % 2 == 0 else self.supplier_b,
                status=Grn.Status.DRAFT if i % 2 == 0 else Grn.Status.PENDING_QC,
                created_by=self.staff,
            )
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('receiving:grn_list'))
        self.assertEqual(len(response.context['grns']), 30)

    def test_page_size_50_shows_all(self):
        response = self.client.get(reverse('receiving:grn_list'), {'page_size': 50})
        self.assertEqual(len(response.context['grns']), 35)

    def test_filter_status(self):
        response = self.client.get(
            reverse('receiving:grn_list'), {'status': Grn.Status.PENDING_QC, 'page_size': 50})
        self.assertTrue(all(g.status == Grn.Status.PENDING_QC for g in response.context['grns']))

    def test_filter_supplier(self):
        response = self.client.get(
            reverse('receiving:grn_list'), {'supplier': self.supplier_a.pk, 'page_size': 50})
        self.assertTrue(all(g.supplier_id == self.supplier_a.pk for g in response.context['grns']))

    def test_filter_search_by_grn_no(self):
        response = self.client.get(reverse('receiving:grn_list'), {'q': 'GRN-TEST-0001'})
        grn_nos = [g.grn_no for g in response.context['grns']]
        self.assertEqual(grn_nos, ['GRN-TEST-0001'])
