from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog
from catalog.models import Product
from partners.models import Supplier
from receiving.models import Grn, GrnItem

from .models import PurchaseOrder, PurchaseOrderItem
from .services import (
    approve_po,
    close_po,
    send_po,
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
            'po_no': 'PO-0001',
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
        po = PurchaseOrder.objects.get(po_no='PO-0001')
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[po.pk]))
        self.assertEqual(po.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(po.items.count(), 1)
        item = po.items.first()
        self.assertEqual(item.qty_ordered, 10)
        self.assertEqual(item.unit_price, Decimal('15000.00'))
        log = AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(po.pk)).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.purchasing_user)

    def test_TC_PUR_001_004_manager_role_can_also_create(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse('purchasing:po_create'), self._payload(po_no='PO-0002'))
        po = PurchaseOrder.objects.get(po_no='PO-0002')
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[po.pk]))

    def test_TC_PUR_001_005_duplicate_po_no_rejected(self):
        PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        response = self.client.post(reverse('purchasing:po_create'), self._payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PurchaseOrder.objects.filter(po_no='PO-0001').count(), 1)

    def test_TC_PUR_001_006_requires_at_least_one_item(self):
        payload = self._payload(**{
            'items-0-product': '', 'items-0-qty_ordered': '', 'items-0-unit_price': '',
        })
        response = self.client.post(reverse('purchasing:po_create'), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PurchaseOrder.objects.filter(po_no='PO-0001').exists())

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
