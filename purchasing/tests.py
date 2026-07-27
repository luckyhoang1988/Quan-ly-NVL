from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog, Notification
from catalog.models import Product
from partners.models import Supplier
from receiving.models import Grn, GrnItem
from warehouse.models import Warehouse

from .models import PurchaseOrder, PurchaseOrderItem, PurchaseRequest, PurchaseRequestItem
from .services import (
    approve_po,
    close_po,
    forward_purchase_request,
    send_po,
    submit_purchase_request,
    supplier_lead_time_stats,
    supplier_price_history,
    sync_po_status,
)

User = get_user_model()


class PurchaseOrderCrudTest(TestCase):
    """CRUD PO (Phase 5 nâng cấp từ PO stub Phase 1 mục 1e). 'po' LÀ module có
    thật trong Permission Matrix nên phân quyền dùng RBAC thật (``user.can``) —
    MANAGER, PURCHASING, ADMIN có Create/Update; STAFF/QC/ACCOUNTANT chỉ Read.
    PO mới luôn tạo ở DRAFT — đổi status chỉ qua transition (xem
    ``PurchaseOrderWorkflowTest``), không còn field ``status`` sửa tay trên form.

    Đặt tên test theo quy ước ``TC-PUR-001-<seq>`` (dùng "001" thay cho FR#).
    """

    def setUp(self):
        self.purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.client.force_login(self.purchasing_user)

    def _payload(self, **overrides):
        payload = {
            'supplier': self.supplier.pk,
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-qty_ordered': 10,
            'items-0-unit_price': '15000.00',
        }
        payload.update(overrides)
        return payload

    def test_TC_PUR_001_001_readonly_role_forbidden_to_create(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('purchasing:po_create'), self._payload())
        self.assertEqual(response.status_code, 403)

    def test_TC_PUR_001_002_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('purchasing:po_create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_PUR_001_003_create_with_items_and_audit(self):
        response = self.client.post(reverse('purchasing:po_create'), self._payload())
        po = PurchaseOrder.objects.get()
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[po.pk]))
        self.assertEqual(po.status, PurchaseOrder.Status.DRAFT)
        self.assertTrue(po.po_no.startswith('PO-'))
        self.assertEqual(po.items.count(), 1)
        item = po.items.first()
        self.assertEqual(item.qty_ordered, 10)
        self.assertEqual(item.unit_price, Decimal('15000.00'))
        log = AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(po.pk)).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.purchasing_user)

    def test_TC_PUR_001_004_manager_role_can_also_create(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse('purchasing:po_create'), self._payload())
        po = PurchaseOrder.objects.get()
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[po.pk]))

    def test_TC_PUR_001_005_po_no_auto_generated_unique_even_if_earlier_code_exists(self):
        """``po_no`` không còn nhập tay (FR bổ sung, tránh trùng mã toàn hệ
        thống) — tự sinh và không trùng với PO đã có sẵn."""
        PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        response = self.client.post(reverse('purchasing:po_create'), self._payload())
        new_po = PurchaseOrder.objects.exclude(po_no='PO-0001').get()
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[new_po.pk]))
        self.assertNotEqual(new_po.po_no, 'PO-0001')
        self.assertTrue(new_po.po_no.startswith('PO-'))

    def test_TC_PUR_001_006_requires_at_least_one_item(self):
        payload = self._payload(**{
            'items-0-product': '', 'items-0-qty_ordered': '', 'items-0-unit_price': '',
        })
        response = self.client.post(reverse('purchasing:po_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_TC_PUR_001_007_readonly_role_can_view_list(self):
        PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        self.client.force_login(self.staff)
        response = self.client.get(reverse('purchasing:po_list'))
        self.assertEqual(response.status_code, 200)

    def test_TC_PUR_001_008_update_items_and_audit(self):
        po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        item = PurchaseOrderItem.objects.create(
            purchase_order=po, product=self.product, qty_ordered=5, unit_price=Decimal('10000.00'))
        response = self.client.post(
            reverse('purchasing:po_update', args=[po.pk]),
            self._payload(**{
                'items-INITIAL_FORMS': '1',
                'items-0-id': item.pk,
                'items-0-qty_ordered': 20,
            }),
        )
        po.refresh_from_db()
        item.refresh_from_db()
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[po.pk]))
        self.assertEqual(item.qty_ordered, 20)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE, target_id=str(po.pk)).exists())

    def test_TC_PUR_001_009_cannot_update_once_past_draft(self):
        po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.APPROVED)
        response = self.client.post(reverse('purchasing:po_update', args=[po.pk]), self._payload())
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[po.pk]))
        po.refresh_from_db()
        self.assertEqual(po.po_no, 'PO-0001')


class PoNoGenerationTest(TestCase):
    """``PurchaseOrder.generate_po_no``: sinh mã tự động PO-XXXX tăng dần toàn hệ
    thống, tránh nhập tay trùng mã (bổ sung theo yêu cầu người dùng 2026-07-26).
    """

    def setUp(self):
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')

    def test_first_po_gets_sequence_0001(self):
        po = PurchaseOrder.objects.create(supplier=self.supplier)
        self.assertEqual(po.po_no, 'PO-0001')

    def test_sequence_increments_from_existing_max(self):
        PurchaseOrder.objects.create(po_no='PO-0007', supplier=self.supplier)
        po = PurchaseOrder.objects.create(supplier=self.supplier)
        self.assertEqual(po.po_no, 'PO-0008')

    def test_non_numeric_suffix_ignored(self):
        """Mã kiểu 'PO-TEST-0001' (import/test cũ) không làm lệch số thứ tự vì
        phần đuôi sau 'PO-' không thuần số."""
        PurchaseOrder.objects.create(po_no='PO-TEST-0001', supplier=self.supplier)
        po = PurchaseOrder.objects.create(supplier=self.supplier)
        self.assertEqual(po.po_no, 'PO-0001')

    def test_explicit_po_no_not_overwritten(self):
        po = PurchaseOrder.objects.create(po_no='PO-CUSTOM-1', supplier=self.supplier)
        self.assertEqual(po.po_no, 'PO-CUSTOM-1')


class SyncPoStatusTest(TestCase):
    """FR-GRN-04: PO.status đồng bộ theo Qty đã nhận lũy kế từ mọi GRN tham
    chiếu tới PO (hỗ trợ nhận nhiều đợt). ``TC-PUR-SYNC-<seq>``.
    """

    def setUp(self):
        self.creator = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.SENT)
        PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('15000.00'))

    def _grn_with_item(self, qty_received, status=GrnItem.Status.PENDING):
        grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.creator)
        GrnItem.objects.create(
            grn=grn, product=self.product, qty_ordered=qty_received or 1, qty_received=qty_received,
            unit_price=Decimal('15000.00'), status=status,
        )
        return grn

    def test_TC_PUR_SYNC_001_no_receipt_status_unchanged(self):
        sync_po_status(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)

    def test_TC_PUR_SYNC_002_partial_receipt_sets_partial_received(self):
        self._grn_with_item(4)
        sync_po_status(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.PARTIAL_RECEIVED)

    def test_TC_PUR_SYNC_003_full_receipt_across_two_grns_sets_received(self):
        self._grn_with_item(6)
        self._grn_with_item(4)
        sync_po_status(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.RECEIVED)
        self.assertIsNotNone(self.po.received_at)

    def test_TC_PUR_SYNC_004_rejected_item_excluded_from_total(self):
        self._grn_with_item(10, status=GrnItem.Status.REJECTED)
        sync_po_status(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)


class PurchaseOrderWorkflowTest(TestCase):
    """DRAFT -> APPROVED -> SENT -> CLOSED (FR-PO-01). Không có nhánh
    auto-approve theo ngưỡng tiền — mọi PO đều cần Manager/Admin (quyền
    ``approve``) duyệt thủ công; Purchasing (quyền ``update``) chỉ gửi NCC/tạo
    PO được. ``TC-PUR-WORKFLOW-<seq>``.
    """

    def setUp(self):
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('15000.00'))

    def test_TC_PUR_WORKFLOW_001_approve_draft_to_approved(self):
        approve_po(self.po, actor=self.manager)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.APPROVED)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.APPROVE, target_id=str(self.po.pk)).exists())

    def test_TC_PUR_WORKFLOW_002_approve_wrong_state_rejected(self):
        self.po.status = PurchaseOrder.Status.SENT
        self.po.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            approve_po(self.po, actor=self.manager)

    def test_TC_PUR_WORKFLOW_003_view_approve_forbidden_for_purchasing_role(self):
        self.client.force_login(self.purchasing_user)
        response = self.client.post(reverse('purchasing:po_approve', args=[self.po.pk]))
        self.assertEqual(response.status_code, 403)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.DRAFT)

    def test_TC_PUR_WORKFLOW_004_view_approve_allowed_for_manager(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse('purchasing:po_approve', args=[self.po.pk]))
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[self.po.pk]))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.APPROVED)

    def test_TC_PUR_WORKFLOW_005_send_requires_approved(self):
        with self.assertRaises(ValidationError):
            send_po(self.po, actor=self.purchasing_user)

    def test_TC_PUR_WORKFLOW_006_send_approved_to_sent(self):
        approve_po(self.po, actor=self.manager)
        send_po(self.po, actor=self.purchasing_user)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)

    def test_TC_PUR_WORKFLOW_007_view_send_allowed_for_purchasing(self):
        approve_po(self.po, actor=self.manager)
        self.client.force_login(self.purchasing_user)
        response = self.client.post(reverse('purchasing:po_send', args=[self.po.pk]))
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[self.po.pk]))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)

    def test_TC_PUR_WORKFLOW_008_close_from_sent(self):
        self.po.status = PurchaseOrder.Status.SENT
        self.po.save(update_fields=['status'])
        close_po(self.po, actor=self.manager)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.CLOSED)

    def test_TC_PUR_WORKFLOW_009_close_from_partial_received(self):
        self.po.status = PurchaseOrder.Status.PARTIAL_RECEIVED
        self.po.save(update_fields=['status'])
        close_po(self.po, actor=self.manager)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.CLOSED)

    def test_TC_PUR_WORKFLOW_010_close_from_draft_rejected(self):
        with self.assertRaises(ValidationError):
            close_po(self.po, actor=self.manager)

    def test_TC_PUR_WORKFLOW_011_view_close_forbidden_for_purchasing_role(self):
        self.po.status = PurchaseOrder.Status.SENT
        self.po.save(update_fields=['status'])
        self.client.force_login(self.purchasing_user)
        response = self.client.post(reverse('purchasing:po_close', args=[self.po.pk]))
        self.assertEqual(response.status_code, 403)


class PurchaseOrderVisibilityTest(TestCase):
    """Phạm vi xem PO ở ``po_list``: nhân viên phòng Mua hàng THƯỜNG (không phải
    quản lý) chỉ thấy PO do chính mình tạo (``created_by``); quản lý phòng Mua
    hàng, Manager/Admin, và mọi role khác (STAFF/QC/ACCOUNTANT — cần đối chiếu
    GRN/công nợ) vẫn xem toàn bộ. ``po_detail`` KHÔNG bị giới hạn theo
    ``created_by`` (PO là tác vụ nhiều vai trò cùng xử lý một phiếu).
    ``TC-PUR-VIS-<seq>``.
    """

    def setUp(self):
        self.purchasing_a = User.objects.create_user(
            username='mua-a', password='pass-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING)
        self.purchasing_b = User.objects.create_user(
            username='mua-b', password='pass-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING)
        self.purchasing_manager = User.objects.create_user(
            username='qlmh', password='pass-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING, is_manager=True)
        self.staff = User.objects.create_user(
            username='kho', password='pass-123', role=User.Role.STAFF)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.po_a = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, created_by=self.purchasing_a)
        self.po_b = PurchaseOrder.objects.create(
            po_no='PO-0002', supplier=self.supplier, created_by=self.purchasing_b)

    def test_TC_PUR_VIS_001_purchasing_staff_sees_only_own_po_in_list(self):
        self.client.force_login(self.purchasing_a)
        response = self.client.get(reverse('purchasing:po_list'), {'page_size': 50})
        orders = set(response.context['orders'])
        self.assertEqual(orders, {self.po_a})

    def test_TC_PUR_VIS_002_purchasing_manager_sees_all_po_in_list(self):
        self.client.force_login(self.purchasing_manager)
        response = self.client.get(reverse('purchasing:po_list'), {'page_size': 50})
        orders = set(response.context['orders'])
        self.assertEqual(orders, {self.po_a, self.po_b})

    def test_TC_PUR_VIS_003_other_role_sees_all_po_in_list(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('purchasing:po_list'), {'page_size': 50})
        orders = set(response.context['orders'])
        self.assertEqual(orders, {self.po_a, self.po_b})

    def test_TC_PUR_VIS_004_purchasing_staff_can_still_view_others_po_detail(self):
        """po_detail không giới hạn theo created_by — PO cần nhiều vai trò cùng
        xử lý (gửi NCC, tham chiếu từ GRN/PR) nên không chặn truy cập trực tiếp."""
        self.client.force_login(self.purchasing_a)
        response = self.client.get(reverse('purchasing:po_detail', args=[self.po_b.pk]))
        self.assertEqual(response.status_code, 200)

    def test_TC_PUR_VIS_005_created_by_set_automatically_on_create(self):
        self.client.force_login(self.purchasing_a)
        response = self.client.post(reverse('purchasing:po_create'), {
            'supplier': self.supplier.pk,
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg').pk,
            'items-0-qty_ordered': 5,
            'items-0-unit_price': '5000.00',
        })
        new_po = PurchaseOrder.objects.exclude(pk__in=[self.po_a.pk, self.po_b.pk]).get()
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[new_po.pk]))
        self.assertEqual(new_po.created_by, self.purchasing_a)

    def test_TC_PUR_VIS_006_null_created_by_visible_to_all_purchasing_staff(self):
        """PO created_by=NULL (dữ liệu cũ trước migration 0007, hoặc không truy
        ngược được người tạo qua AuditLog — xem 0009_backfill_po_created_by.py)
        không được gán bừa cho một người, nên phải hiển thị cho MỌI nhân viên
        PURCHASING thường, không riêng ai — filter created_by=request.user
        không khớp NULL trong SQL nên trước đây các PO này bị ẩn vĩnh viễn."""
        po_legacy = PurchaseOrder.objects.create(po_no='PO-0003', supplier=self.supplier)
        self.client.force_login(self.purchasing_a)
        response = self.client.get(reverse('purchasing:po_list'), {'page_size': 50})
        orders = set(response.context['orders'])
        self.assertEqual(orders, {self.po_a, po_legacy})

        self.client.force_login(self.purchasing_b)
        response = self.client.get(reverse('purchasing:po_list'), {'page_size': 50})
        orders = set(response.context['orders'])
        self.assertEqual(orders, {self.po_b, po_legacy})


class DeliveryStatusTest(TestCase):
    """FR-PO-06: phân loại giao hàng On time/Delayed/Partial, tính on-the-fly từ
    ``status``/``expected_delivery_date``/``received_at``. ``TC-PUR-06-<seq>``.
    """

    def setUp(self):
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')

    def _po(self, status, expected_delivery_date=None, received_at=None):
        return PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=status,
            expected_delivery_date=expected_delivery_date, received_at=received_at,
        )

    def test_TC_PUR_06_001_no_expected_date_returns_none(self):
        po = self._po(PurchaseOrder.Status.SENT)
        self.assertIsNone(po.delivery_status())

    def test_TC_PUR_06_002_draft_or_approved_returns_none(self):
        po = self._po(PurchaseOrder.Status.DRAFT, expected_delivery_date=timezone.localdate())
        self.assertIsNone(po.delivery_status())

    def test_TC_PUR_06_003_partial_received_returns_partial(self):
        po = self._po(PurchaseOrder.Status.PARTIAL_RECEIVED, expected_delivery_date=timezone.localdate())
        self.assertEqual(po.delivery_status()['code'], 'PARTIAL')

    def test_TC_PUR_06_004_sent_past_expected_date_returns_delayed(self):
        po = self._po(PurchaseOrder.Status.SENT, expected_delivery_date=timezone.localdate() - timedelta(days=1))
        self.assertEqual(po.delivery_status()['code'], 'DELAYED')

    def test_TC_PUR_06_005_sent_before_expected_date_returns_none(self):
        po = self._po(PurchaseOrder.Status.SENT, expected_delivery_date=timezone.localdate() + timedelta(days=5))
        self.assertIsNone(po.delivery_status())

    def test_TC_PUR_06_006_received_on_time(self):
        expected = timezone.localdate()
        po = self._po(PurchaseOrder.Status.RECEIVED, expected_delivery_date=expected, received_at=expected)
        self.assertEqual(po.delivery_status()['code'], 'ON_TIME')

    def test_TC_PUR_06_007_received_late(self):
        expected = timezone.localdate() - timedelta(days=5)
        received = timezone.localdate()
        po = self._po(PurchaseOrder.Status.RECEIVED, expected_delivery_date=expected, received_at=received)
        self.assertEqual(po.delivery_status()['code'], 'DELAYED')


class PriceComparisonViewTest(TestCase):
    """FR-PO-03: so sánh giá NCC theo sản phẩm. ``TC-PUR-03-<seq>``."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.supplier_a = Supplier.objects.create(supplier_code='NCC-0001', name='NCC A')
        self.supplier_b = Supplier.objects.create(supplier_code='NCC-0002', name='NCC B')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.client.force_login(self.user)

    def _po_with_item(self, supplier, price):
        po = PurchaseOrder.objects.create(po_no=f'PO-{supplier.pk}-{price}', supplier=supplier)
        PurchaseOrderItem.objects.create(
            purchase_order=po, product=self.product, qty_ordered=10, unit_price=Decimal(price))
        return po

    def test_TC_PUR_03_001_no_product_selected_shows_picker(self):
        response = self.client.get(reverse('purchasing:po_price_comparison'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['rows'])

    def test_TC_PUR_03_002_compares_price_across_suppliers(self):
        self._po_with_item(self.supplier_a, '10000.00')
        self._po_with_item(self.supplier_b, '12000.00')
        response = self.client.get(reverse('purchasing:po_price_comparison'), {'product': self.product.pk})
        rows = {row['supplier_code']: row for row in response.context['rows']}
        self.assertEqual(rows['NCC-0001']['avg_price'], Decimal('10000.00'))
        self.assertEqual(rows['NCC-0002']['avg_price'], Decimal('12000.00'))


class SupplierPerformanceViewTest(TestCase):
    """FR-PO-05/FR-PO-06: lead-time thực tế + đúng hạn/trễ hạn theo NCC.
    ``TC-PUR-05-<seq>``.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.supplier = Supplier.objects.create(
            supplier_code='NCC-0001', name='Công ty TNHH ABC', lead_time_days=7)
        self.client.force_login(self.user)

    def test_TC_PUR_05_001_supplier_with_no_received_po_shows_none(self):
        response = self.client.get(reverse('purchasing:po_supplier_performance'))
        row = next(r for r in response.context['rows'] if r['supplier'] == self.supplier)
        self.assertIsNone(row['avg_actual_lead_time_days'])
        self.assertEqual(row['received_po_count'], 0)

    def test_TC_PUR_05_002_computes_avg_lead_time_and_on_time_count(self):
        po = PurchaseOrder.objects.create(
            po_no='PO-0001', supplier=self.supplier, status=PurchaseOrder.Status.RECEIVED,
            expected_delivery_date=timezone.localdate(), received_at=timezone.localdate(),
        )
        PurchaseOrder.objects.filter(pk=po.pk).update(created_at=timezone.now() - timedelta(days=5))
        response = self.client.get(reverse('purchasing:po_supplier_performance'))
        row = next(r for r in response.context['rows'] if r['supplier'] == self.supplier)
        self.assertEqual(row['received_po_count'], 1)
        self.assertEqual(row['avg_actual_lead_time_days'], 5)
        self.assertEqual(row['on_time_count'], 1)
        self.assertEqual(row['delayed_count'], 0)


class PoCreatePrefillTest(TestCase):
    """FR-PO-02: prefill dòng item đầu từ ``?product=&qty=`` (link từ dashboard
    tồn kho dưới Min Level, xem ``inventory/views.py::inventory_list``).
    ``TC-PUR-02-<seq>``.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.client.force_login(self.user)

    def test_TC_PUR_02_001_prefills_first_item_form_from_query_params(self):
        response = self.client.get(reverse('purchasing:po_create'), {'product': self.product.pk, 'qty': 50})
        formset = response.context['formset']
        self.assertEqual(str(formset.forms[0].initial.get('product')), str(self.product.pk))
        self.assertEqual(str(formset.forms[0].initial.get('qty_ordered')), '50')

    def test_TC_PUR_02_002_no_query_params_no_prefill(self):
        response = self.client.get(reverse('purchasing:po_create'))
        formset = response.context['formset']
        self.assertNotIn('product', formset.forms[0].initial)


class PoListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (status/supplier/tìm kiếm) trên po_list."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.client.force_login(self.user)
        self.supplier_a = Supplier.objects.create(supplier_code='NCC-A', name='NCC A')
        self.supplier_b = Supplier.objects.create(supplier_code='NCC-B', name='NCC B')
        PurchaseOrder.objects.bulk_create([
            PurchaseOrder(
                po_no=f'PO-TEST-{i:04d}',
                supplier=self.supplier_a if i % 2 == 0 else self.supplier_b,
                status=PurchaseOrder.Status.DRAFT if i % 2 == 0 else PurchaseOrder.Status.SENT,
                created_by=self.user,
            )
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('purchasing:po_list'))
        self.assertEqual(len(response.context['orders']), 30)

    def test_page_size_50_shows_all(self):
        response = self.client.get(reverse('purchasing:po_list'), {'page_size': 50})
        self.assertEqual(len(response.context['orders']), 35)

    def test_filter_status(self):
        response = self.client.get(
            reverse('purchasing:po_list'), {'status': PurchaseOrder.Status.SENT, 'page_size': 50})
        self.assertTrue(all(po.status == PurchaseOrder.Status.SENT for po in response.context['orders']))

    def test_filter_supplier(self):
        response = self.client.get(
            reverse('purchasing:po_list'), {'supplier': self.supplier_a.pk, 'page_size': 50})
        self.assertTrue(all(po.supplier_id == self.supplier_a.pk for po in response.context['orders']))

    def test_filter_search_by_po_no(self):
        response = self.client.get(reverse('purchasing:po_list'), {'q': 'PO-TEST-0001'})
        po_nos = [po.po_no for po in response.context['orders']]
        self.assertEqual(po_nos, ['PO-TEST-0001'])


class PurchaseRequestCrudTest(TestCase):
    """PR (Yêu cầu mua hàng, bổ sung ngoài FR) — Tab 1 của Purchasing. STAFF (hoặc
    nhân viên phòng khác) tạo được (không tự duyệt); duyệt/từ chối đi qua
    ``Approval`` — chỉ quản lý phòng Mua hàng (``is_department_manager``) hoặc
    Manager/Admin (fallback) mới quyết định được, một nhân viên PURCHASING
    thường KHÔNG tự duyệt nữa kể cả khi được ``assigned_to`` chỉ định. Giữ tách
    biệt với quyền duyệt PO thật. ``TC-PR-001-<seq>``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING)
        self.purchasing_manager = User.objects.create_user(
            username='qlmh', password='qlmh-pass-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING, is_manager=True)
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(
            code='KHO-01', name='Kho chính', warehouse_type=Warehouse.WarehouseType.MAIN)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.client.force_login(self.staff)

    def _payload(self, **overrides):
        payload = {
            'warehouse': self.warehouse.pk,
            'note': 'Thiếu hàng gấp',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-qty_requested': 20,
        }
        payload.update(overrides)
        return payload

    def _pending_pr(self, assigned_to=None):
        """PR PENDING kèm ``Approval`` PENDING thật (mirror ``submit_purchase_request``
        production dùng khi tạo qua ``pr_create``) — cần thiết để test approve/reject
        vì các view giờ tra ``latest_approval_for`` thay vì tự chuyển state.
        """
        pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, assigned_to=assigned_to)
        submit_purchase_request(pr, actor=self.staff)
        return pr

    def test_TC_PR_001_001_staff_can_create(self):
        response = self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING)
        self.assertEqual(pr.requested_by, self.staff)
        self.assertEqual(pr.items.count(), 1)
        self.assertTrue(pr.request_no.startswith('PR-'))
        log = AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(pr.pk)).first()
        self.assertIsNotNone(log)

    def test_TC_PR_001_002_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('purchasing:pr_create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_PR_001_003_requires_at_least_one_item(self):
        payload = self._payload(**{'items-0-product': '', 'items-0-qty_requested': ''})
        response = self.client.post(reverse('purchasing:pr_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PurchaseRequest.objects.exists())

    def test_TC_PR_001_004_staff_cannot_approve(self):
        pr = self._pending_pr()
        response = self.client.post(reverse('purchasing:pr_approve', args=[pr.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_PR_001_005_staff_cannot_reject(self):
        pr = self._pending_pr()
        response = self.client.post(
            reverse('purchasing:pr_reject', args=[pr.pk]), {'reject_reason': 'Không đủ ngân sách'})
        self.assertEqual(response.status_code, 403)

    def test_TC_PR_001_006_purchasing_role_alone_cannot_approve(self):
        """Nhân viên mua hàng thường (không phải quản lý phòng) không còn tự duyệt
        được nữa, kể cả khi PR chỉ định đúng người này (``assigned_to``)."""
        pr = self._pending_pr(assigned_to=self.purchasing_user)
        self.client.force_login(self.purchasing_user)
        response = self.client.post(reverse('purchasing:pr_approve', args=[pr.pk]))
        self.assertEqual(response.status_code, 403)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING)

    def test_TC_PR_001_007_purchasing_department_manager_can_approve(self):
        pr = self._pending_pr(assigned_to=self.purchasing_user)
        self.client.force_login(self.purchasing_manager)
        response = self.client.post(reverse('purchasing:pr_approve', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.APPROVED)
        self.assertEqual(pr.decided_by, self.purchasing_manager)
        self.assertIsNotNone(pr.decided_at)

    def test_TC_PR_001_008_manager_can_reject_with_reason(self):
        """Manager (fallback ``can('approve', 'pr')``, không cần đúng ``department``)
        vẫn duyệt/từ chối được — không hạ quyền ai đang có (mirror GRN/GIN)."""
        pr = self._pending_pr()
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('purchasing:pr_reject', args=[pr.pk]), {'reject_reason': 'Không đủ ngân sách'})
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.REJECTED)
        self.assertEqual(pr.reject_reason, 'Không đủ ngân sách')

    def test_TC_PR_001_009_purchasing_still_forbidden_from_po_approve(self):
        """Giữ 2 lớp kiểm soát tách biệt: duyệt PR khác duyệt PO thật."""
        supplier = Supplier.objects.create(supplier_code='NCC-0001', name='NCC A')
        po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=supplier)
        self.client.force_login(self.purchasing_manager)
        response = self.client.post(reverse('purchasing:po_approve', args=[po.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_PR_001_010_reject_wrong_state_shows_error(self):
        pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, status=PurchaseRequest.Status.APPROVED)
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('purchasing:pr_reject', args=[pr.pk]), {'reject_reason': 'x'})
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.APPROVED)

    def test_TC_PR_001_011_create_notifies_department_manager_and_assigned_to(self):
        response = self.client.post(
            reverse('purchasing:pr_create'), self._payload(assigned_to=self.purchasing_user.pk))
        pr = PurchaseRequest.objects.get()
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertEqual(pr.assigned_to, self.purchasing_user)
        self.assertTrue(Notification.objects.filter(recipient=self.purchasing_manager).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.purchasing_user).exists())


class PurchaseRequestVisibilityTest(TestCase):
    """Phạm vi xem PR: nhân viên phòng khác chỉ thấy PR do chính mình tạo; nhân
    viên phòng Mua hàng THƯỜNG (không phải quản lý) chỉ thấy PR được chỉ định/
    chuyển tiếp cho mình (``assigned_to``); quản lý phòng Mua hàng và Manager/
    Admin xem toàn bộ (cần bức tranh tổng để duyệt/chuyển tiếp). ``TC-PR-002-<seq>``.
    """

    def setUp(self):
        self.staff_a = User.objects.create_user(
            username='kho-a', password='pass-123', role=User.Role.STAFF)
        self.staff_b = User.objects.create_user(
            username='kho-b', password='pass-123', role=User.Role.STAFF)
        self.purchasing_user = User.objects.create_user(
            username='mua', password='pass-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING)
        self.purchasing_manager = User.objects.create_user(
            username='qlmh', password='pass-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING, is_manager=True)
        self.manager = User.objects.create_user(
            username='wm', password='pass-123', role=User.Role.MANAGER)
        self.warehouse = Warehouse.objects.create(
            code='KHO-01', name='Kho chính', warehouse_type=Warehouse.WarehouseType.MAIN)
        self.pr_a = PurchaseRequest.objects.create(requested_by=self.staff_a, warehouse=self.warehouse)
        self.pr_b = PurchaseRequest.objects.create(requested_by=self.staff_b, warehouse=self.warehouse)

    def test_TC_PR_002_001_staff_sees_only_own_pr_in_list(self):
        self.client.force_login(self.staff_a)
        response = self.client.get(reverse('purchasing:pr_list'))
        prs = list(response.context['prs'])
        self.assertEqual(prs, [self.pr_a])

    def test_TC_PR_002_002_purchasing_staff_sees_only_assigned_pr_in_list(self):
        """Nhân viên mua hàng thường KHÔNG còn thấy toàn bộ PR — chỉ thấy PR
        được chỉ định/chuyển tiếp đích danh cho mình."""
        self.pr_b.assigned_to = self.purchasing_user
        self.pr_b.save(update_fields=['assigned_to'])
        self.client.force_login(self.purchasing_user)
        response = self.client.get(reverse('purchasing:pr_list'))
        prs = set(response.context['prs'])
        self.assertEqual(prs, {self.pr_b})

    def test_TC_PR_002_003_manager_sees_all_pr_in_list(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse('purchasing:pr_list'))
        prs = set(response.context['prs'])
        self.assertEqual(prs, {self.pr_a, self.pr_b})

    def test_TC_PR_002_004_staff_cannot_view_others_pr_detail(self):
        self.client.force_login(self.staff_a)
        response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr_b.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_PR_002_005_staff_can_view_own_pr_detail(self):
        self.client.force_login(self.staff_a)
        response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr_a.pk]))
        self.assertEqual(response.status_code, 200)

    def test_TC_PR_002_006_purchasing_staff_cannot_view_unassigned_pr_detail(self):
        self.client.force_login(self.purchasing_user)
        response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr_b.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_PR_002_007_purchasing_staff_can_view_assigned_pr_detail(self):
        self.pr_b.assigned_to = self.purchasing_user
        self.pr_b.save(update_fields=['assigned_to'])
        self.client.force_login(self.purchasing_user)
        response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr_b.pk]))
        self.assertEqual(response.status_code, 200)

    def test_TC_PR_002_008_purchasing_manager_sees_all_pr_in_list(self):
        self.client.force_login(self.purchasing_manager)
        response = self.client.get(reverse('purchasing:pr_list'))
        prs = set(response.context['prs'])
        self.assertEqual(prs, {self.pr_a, self.pr_b})


class PurchaseRequestForwardTest(TestCase):
    """Chuyển tiếp 1 PR đã duyệt cho 1 nhân viên phòng Mua hàng cụ thể tạo PO
    (``purchasing.services.forward_purchase_request``): chỉ quản lý phòng Mua
    hàng/Manager/Admin chuyển tiếp được; nhân viên được chuyển tiếp thấy PR
    trong danh sách của họ sau đó. ``TC-PR-003-<seq>``.
    """

    def setUp(self):
        self.purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING)
        self.other_purchasing_user = User.objects.create_user(
            username='mua2', password='mua2-pass-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING)
        self.purchasing_manager = User.objects.create_user(
            username='qlmh', password='qlmh-pass-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING, is_manager=True)
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(
            code='KHO-01', name='Kho chính', warehouse_type=Warehouse.WarehouseType.MAIN)
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, status=PurchaseRequest.Status.APPROVED)

    def test_TC_PR_003_001_manager_can_forward_to_staff(self):
        self.client.force_login(self.purchasing_manager)
        response = self.client.post(
            reverse('purchasing:pr_forward', args=[self.pr.pk]), {'staff': self.other_purchasing_user.pk})
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[self.pr.pk]))
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.assigned_to, self.other_purchasing_user)
        self.assertTrue(Notification.objects.filter(recipient=self.other_purchasing_user).exists())

    def test_TC_PR_003_002_plain_purchasing_staff_cannot_forward(self):
        self.client.force_login(self.purchasing_user)
        response = self.client.post(
            reverse('purchasing:pr_forward', args=[self.pr.pk]), {'staff': self.other_purchasing_user.pk})
        self.assertEqual(response.status_code, 403)

    def test_TC_PR_003_003_cannot_forward_pending_pr(self):
        pending_pr = PurchaseRequest.objects.create(requested_by=self.staff, warehouse=self.warehouse)
        self.client.force_login(self.purchasing_manager)
        with self.assertRaises(ValidationError):
            forward_purchase_request(pending_pr, self.other_purchasing_user, actor=self.purchasing_manager)

    def test_TC_PR_003_004_forwarded_staff_then_sees_pr_in_list_and_detail(self):
        forward_purchase_request(self.pr, self.other_purchasing_user, actor=self.purchasing_manager)
        self.client.force_login(self.other_purchasing_user)
        list_response = self.client.get(reverse('purchasing:pr_list'))
        self.assertIn(self.pr, list(list_response.context['prs']))
        detail_response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr.pk]))
        self.assertEqual(detail_response.status_code, 200)

    def test_TC_PR_003_005_cannot_forward_when_already_linked_to_po(self):
        supplier = Supplier.objects.create(supplier_code='NCC-0001', name='NCC A')
        po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=supplier)
        self.pr.linked_po = po
        self.pr.save(update_fields=['linked_po'])
        with self.assertRaises(ValidationError):
            forward_purchase_request(self.pr, self.other_purchasing_user, actor=self.purchasing_manager)


class PoCreateFromPrTest(TestCase):
    """PR APPROVED -> tạo PO qua ``?from_pr=<pk>`` -> ``linked_po`` được gán.
    ``TC-PR-PO-<seq>``.
    """

    def setUp(self):
        self.purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(
            code='KHO-01', name='Kho chính', warehouse_type=Warehouse.WarehouseType.MAIN)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='NCC A')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.staff, assigned_to=self.purchasing_user,
            warehouse=self.warehouse, status=PurchaseRequest.Status.APPROVED)
        PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=30)
        self.client.force_login(self.purchasing_user)

    def test_TC_PR_PO_001_get_prefills_items_from_pr(self):
        response = self.client.get(reverse('purchasing:po_create'), {'from_pr': self.pr.pk})
        formset = response.context['formset']
        self.assertEqual(str(formset.forms[0].initial.get('product')), str(self.product.pk))
        self.assertEqual(str(formset.forms[0].initial.get('qty_ordered')), '30')
        self.assertEqual(response.context['source_pr'], self.pr)

    def test_TC_PR_PO_002_post_creates_po_and_links_back(self):
        payload = {
            'supplier': self.supplier.pk,
            'from_pr': self.pr.pk,
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-qty_ordered': 30,
            'items-0-unit_price': '15000.00',
        }
        response = self.client.post(reverse('purchasing:po_create'), payload)
        po = PurchaseOrder.objects.get()
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[po.pk]))
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.linked_po, po)

    def test_TC_PR_PO_003_pending_pr_cannot_be_used(self):
        self.pr.status = PurchaseRequest.Status.PENDING
        self.pr.save(update_fields=['status'])
        response = self.client.get(reverse('purchasing:po_create'), {'from_pr': self.pr.pk})
        self.assertEqual(response.status_code, 404)

    def test_TC_PR_PO_004_already_linked_pr_cannot_be_reused(self):
        """PR đã convert thành PO rồi -> không tạo được PO thứ 2 từ cùng PR
        (kể cả khi vẫn còn APPROVED), dù đi thẳng URL chứ không qua nút UI."""
        other_po = PurchaseOrder.objects.create(supplier=self.supplier)
        self.pr.linked_po = other_po
        self.pr.save(update_fields=['linked_po'])
        response = self.client.get(reverse('purchasing:po_create'), {'from_pr': self.pr.pk})
        self.assertEqual(response.status_code, 404)

    def test_TC_PR_PO_005_unassigned_purchasing_staff_cannot_use_others_pr(self):
        """Nhân viên Mua hàng thường KHÔNG được chỉ định/chuyển tiếp PR này thì
        không được tạo PO từ nó chỉ bằng cách đoán pk -- mirror quyền xem ở
        pr_detail, chặn luôn ở po_create."""
        other_purchasing_user = User.objects.create_user(
            username='mua2', password='mua2-pass-123', role=User.Role.PURCHASING)
        self.client.force_login(other_purchasing_user)
        response = self.client.get(reverse('purchasing:po_create'), {'from_pr': self.pr.pk})
        self.assertEqual(response.status_code, 403)
