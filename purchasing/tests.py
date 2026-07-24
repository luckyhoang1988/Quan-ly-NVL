from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog
from catalog.models import Product
from partners.models import Supplier

from .models import PurchaseOrder, PurchaseOrderItem

User = get_user_model()


class PurchaseOrderCrudTest(TestCase):
    """PO stub (mục 1e — bổ sung, chưa có mã FR riêng ở Phase 1; FR-PO-* đầy đủ
    dời qua Phase 5). Khác catalog/partners: 'po' LÀ module có thật trong
    Permission Matrix nên phân quyền dùng RBAC thật (``user.can``) — MANAGER,
    PURCHASING, ADMIN có Create/Update; STAFF/QC/ACCOUNTANT chỉ Read.

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
            'status': PurchaseOrder.Status.SENT,
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
        self.assertRedirects(response, reverse('purchasing:po_list'))
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
        self.assertRedirects(response, reverse('purchasing:po_list'))
        self.assertTrue(PurchaseOrder.objects.filter(po_no='PO-0002').exists())

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
            self._payload(status=PurchaseOrder.Status.CLOSED, **{
                'items-INITIAL_FORMS': '1',
                'items-0-id': item.pk,
                'items-0-qty_ordered': 20,
            }),
        )
        po.refresh_from_db()
        item.refresh_from_db()
        self.assertRedirects(response, reverse('purchasing:po_list'))
        self.assertEqual(po.status, PurchaseOrder.Status.CLOSED)
        self.assertEqual(item.qty_ordered, 20)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE, target_id=str(po.pk)).exists())
