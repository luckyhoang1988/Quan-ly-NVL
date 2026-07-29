from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Approval, AuditLog, Notification
from catalog.models import Product
from partners.models import Supplier
from receiving.models import Grn, GrnItem
from warehouse.models import Warehouse

from . import views as purchasing_views
from .models import PurchaseOrder, PurchaseOrderItem, PurchaseRequest, PurchaseRequestItem
from .services import (
    approve_po,
    close_po,
    decide_purchase_request,
    forward_purchase_request,
    reopen_purchase_request,
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
        self.assertEqual(po.source, PurchaseOrder.Source.MANUAL)
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

    def test_TC_PUR_001_010_inactive_supplier_rejected_on_create(self):
        """Bug fix: NCC status khác ACTIVE (INACTIVE/SUSPENDED) không được chọn khi tạo PO mới."""
        self.supplier.status = Supplier.Status.INACTIVE
        self.supplier.save(update_fields=['status'])
        response = self.client.post(reverse('purchasing:po_create'), self._payload())
        self.assertEqual(response.status_code, 200)  # re-render form với lỗi, không tạo PO
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_TC_PUR_001_011_inactive_product_rejected_on_create_item(self):
        """Bug fix: SKU đã is_active=False không được chọn cho dòng PO mới."""
        self.product.is_active = False
        self.product.save(update_fields=['is_active'])
        response = self.client.post(reverse('purchasing:po_create'), self._payload())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_TC_PUR_001_012_update_keeps_existing_inactive_supplier_selectable(self):
        """Sửa PO cũ không vỡ nếu NCC/SKU của nó đã chuyển inactive sau khi PO tạo."""
        po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        item = PurchaseOrderItem.objects.create(
            purchase_order=po, product=self.product, qty_ordered=5, unit_price=Decimal('10000.00'))
        self.supplier.status = Supplier.Status.INACTIVE
        self.supplier.save(update_fields=['status'])
        self.product.is_active = False
        self.product.save(update_fields=['is_active'])
        response = self.client.post(
            reverse('purchasing:po_update', args=[po.pk]),
            self._payload(**{
                'items-INITIAL_FORMS': '1',
                'items-0-id': item.pk,
                'items-0-qty_ordered': 20,
            }),
        )
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[po.pk]))
        item.refresh_from_db()
        self.assertEqual(item.qty_ordered, 20)

    def test_TC_PUR_001_013_concurrent_approve_during_update_not_overwritten(self):
        """BUG-02: nếu PO chuyển trạng thái (được duyệt bởi request khác) đúng
        vào khoảng hở giữa lần đọc đối tượng đầu tiên của ``po_update`` và lúc
        view khóa row bằng ``select_for_update()`` để lưu, view phải nhận ra
        status mới (không phải bản DRAFT cũ còn giữ trong bộ nhớ) và từ chối
        lưu — không được ghi đè PO đã APPROVED trở lại DRAFT."""
        po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        item = PurchaseOrderItem.objects.create(
            purchase_order=po, product=self.product, qty_ordered=5, unit_price=Decimal('10000.00'))

        real_get_object_or_404 = purchasing_views.get_object_or_404
        call_count = {'n': 0}

        def _sneaky_get_object_or_404(*args, **kwargs):
            call_count['n'] += 1
            if call_count['n'] == 2:
                # Mô phỏng 1 request khác vừa duyệt PO này, đúng lúc request hiện
                # tại đã qua check DRAFT ban đầu và sắp khóa row để lưu.
                PurchaseOrder.objects.filter(pk=po.pk).update(status=PurchaseOrder.Status.APPROVED)
            return real_get_object_or_404(*args, **kwargs)

        with patch('purchasing.views.get_object_or_404', side_effect=_sneaky_get_object_or_404):
            response = self.client.post(
                reverse('purchasing:po_update', args=[po.pk]),
                self._payload(**{
                    'items-INITIAL_FORMS': '1',
                    'items-0-id': item.pk,
                    'items-0-qty_ordered': 20,
                }),
            )
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[po.pk]))
        po.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.APPROVED)
        self.assertEqual(item.qty_ordered, 5)


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

    def _grn_with_item(self, qty_received, status=GrnItem.Status.PENDING, grn_status=Grn.Status.PENDING_QC):
        grn = Grn.objects.create(
            po=self.po, supplier=self.supplier, created_by=self.creator, status=grn_status)
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

    def test_TC_PUR_SYNC_005_cancelled_grn_excluded_from_total(self):
        """GRN CANCELLED coi như chưa từng nhận hàng, giống hệt item REJECTED —
        bug fix: trước đây chỉ loại trừ item REJECTED, không loại trừ cả GRN đã
        hủy hẳn."""
        self._grn_with_item(10, grn_status=Grn.Status.CANCELLED)
        sync_po_status(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)

    def test_TC_PUR_SYNC_006_downgrade_received_to_sent_when_grn_cancelled(self):
        """Bug fix: PO đã RECEIVED do 1 GRN duy nhất, nếu GRN đó bị hủy sau đó,
        PO phải hạ lại về SENT (không kẹt ở RECEIVED vĩnh viễn) và received_at
        phải được xóa."""
        grn = self._grn_with_item(10)
        sync_po_status(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.RECEIVED)
        self.assertIsNotNone(self.po.received_at)

        grn.status = Grn.Status.CANCELLED
        grn.save(update_fields=['status'])
        sync_po_status(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)
        self.assertIsNone(self.po.received_at)

    def test_TC_PUR_SYNC_007_downgrade_received_to_partial_received_when_one_grn_cancelled(self):
        """2 GRN cùng đóng góp đủ Qty (RECEIVED); hủy 1 GRN thì PO hạ về
        PARTIAL_RECEIVED (GRN còn lại vẫn còn đóng góp qty), không hạ hẳn về SENT."""
        grn_a = self._grn_with_item(6)
        self._grn_with_item(4)
        sync_po_status(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.RECEIVED)

        grn_a.status = Grn.Status.CANCELLED
        grn_a.save(update_fields=['status'])
        sync_po_status(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.PARTIAL_RECEIVED)
        self.assertIsNone(self.po.received_at)

    def test_TC_PUR_SYNC_008_closed_po_not_reopened(self):
        """Bug fix: PO đã CLOSED không bị sync_po_status đẩy ngược lại RECEIVED/
        PARTIAL_RECEIVED dù 1 GRN dở dang sau đó mới ghi Qty thực nhận."""
        self.po.status = PurchaseOrder.Status.CLOSED
        self.po.save(update_fields=['status'])
        self._grn_with_item(10)
        sync_po_status(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.CLOSED)


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

    def test_TC_PUR_WORKFLOW_007b_send_with_supplier_email_sends_mail(self):
        """Bước G: NCC có contact_email — send_po gửi kèm 1 email best-effort."""
        self.supplier.contact_email = 'ncc-abc@example.com'
        self.supplier.save(update_fields=['contact_email'])
        approve_po(self.po, actor=self.manager)
        po = send_po(self.po, actor=self.purchasing_user)
        self.assertTrue(po._email_sent)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn(self.po.po_no, sent.subject)
        self.assertEqual(sent.to, ['ncc-abc@example.com'])
        self.assertIn(self.product.product_code, sent.body)

    def test_TC_PUR_WORKFLOW_007c_send_without_supplier_email_no_mail_still_sent(self):
        """NCC không có contact_email — vẫn chuyển SENT, chỉ không gửi email."""
        approve_po(self.po, actor=self.manager)
        po = send_po(self.po, actor=self.purchasing_user)
        self.assertFalse(po._email_sent)
        self.assertEqual(len(mail.outbox), 0)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)

    def test_TC_PUR_WORKFLOW_007d_view_send_without_email_shows_warning(self):
        approve_po(self.po, actor=self.manager)
        self.client.force_login(self.purchasing_user)
        response = self.client.post(
            reverse('purchasing:po_send', args=[self.po.pk]), follow=True)
        messages_list = list(response.context['messages'])
        self.assertEqual(len(messages_list), 1)
        self.assertEqual(messages_list[0].tags, 'warning')

    def test_TC_PUR_WORKFLOW_007e_view_send_with_email_shows_success(self):
        self.supplier.contact_email = 'ncc-abc@example.com'
        self.supplier.save(update_fields=['contact_email'])
        approve_po(self.po, actor=self.manager)
        self.client.force_login(self.purchasing_user)
        response = self.client.post(
            reverse('purchasing:po_send', args=[self.po.pk]), follow=True)
        messages_list = list(response.context['messages'])
        self.assertEqual(len(messages_list), 1)
        self.assertEqual(messages_list[0].tags, 'success')
        self.assertEqual(len(mail.outbox), 1)

    def test_TC_PUR_WORKFLOW_008_close_from_sent(self):
        """Bước F: đóng sớm từ SENT bắt buộc ``reason`` — không truyền sẽ bị
        chặn (test 008b bên dưới), có ``reason`` thì đóng được và lưu lại."""
        self.po.status = PurchaseOrder.Status.SENT
        self.po.save(update_fields=['status'])
        close_po(self.po, actor=self.manager, reason='NCC báo hết hàng, không giao nốt phần còn lại.')
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.CLOSED)
        self.assertEqual(self.po.close_reason, 'NCC báo hết hàng, không giao nốt phần còn lại.')

    def test_TC_PUR_WORKFLOW_008b_close_from_sent_without_reason_rejected(self):
        self.po.status = PurchaseOrder.Status.SENT
        self.po.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            close_po(self.po, actor=self.manager)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)

    def test_TC_PUR_WORKFLOW_009_close_from_partial_received(self):
        self.po.status = PurchaseOrder.Status.PARTIAL_RECEIVED
        self.po.save(update_fields=['status'])
        close_po(self.po, actor=self.manager, reason='Chỉ nhận được 1 phần, NCC không giao thêm.')
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.CLOSED)

    def test_TC_PUR_WORKFLOW_009b_close_from_partial_received_without_reason_rejected(self):
        self.po.status = PurchaseOrder.Status.PARTIAL_RECEIVED
        self.po.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            close_po(self.po, actor=self.manager)

    def test_TC_PUR_WORKFLOW_009c_close_from_received_no_reason_required(self):
        self.po.status = PurchaseOrder.Status.RECEIVED
        self.po.save(update_fields=['status'])
        close_po(self.po, actor=self.manager)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.CLOSED)
        self.assertEqual(self.po.close_reason, '')

    def test_TC_PUR_WORKFLOW_010_close_from_draft_rejected(self):
        with self.assertRaises(ValidationError):
            close_po(self.po, actor=self.manager)

    def test_TC_PUR_WORKFLOW_011_view_close_forbidden_for_purchasing_role(self):
        self.po.status = PurchaseOrder.Status.SENT
        self.po.save(update_fields=['status'])
        self.client.force_login(self.purchasing_user)
        response = self.client.post(reverse('purchasing:po_close', args=[self.po.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_PUR_WORKFLOW_012_view_close_from_sent_without_reason_blocked(self):
        """Bước F: view ``po_close`` chặn đóng sớm thiếu lý do — status không đổi,
        có message lỗi, không 500."""
        self.po.status = PurchaseOrder.Status.SENT
        self.po.save(update_fields=['status'])
        self.client.force_login(self.manager)
        response = self.client.post(reverse('purchasing:po_close', args=[self.po.pk]))
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[self.po.pk]))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)

    def test_TC_PUR_WORKFLOW_013_view_close_from_sent_with_reason_succeeds(self):
        self.po.status = PurchaseOrder.Status.SENT
        self.po.save(update_fields=['status'])
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse('purchasing:po_close', args=[self.po.pk]),
            {'close_reason': 'NCC không còn hàng để giao tiếp.'})
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[self.po.pk]))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.CLOSED)
        self.assertEqual(self.po.close_reason, 'NCC không còn hàng để giao tiếp.')

    def test_TC_PUR_WORKFLOW_014_view_close_from_received_no_reason_needed(self):
        self.po.status = PurchaseOrder.Status.RECEIVED
        self.po.save(update_fields=['status'])
        self.client.force_login(self.manager)
        response = self.client.post(reverse('purchasing:po_close', args=[self.po.pk]))
        self.assertRedirects(response, reverse('purchasing:po_detail', args=[self.po.pk]))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.CLOSED)


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


class PoCreateNoLongerHonorsMinLevelShortcutTest(TestCase):
    """Bước E: lối tắt ``?product=&qty=`` tạo PO thẳng từ gợi ý Min Level đã bị
    bỏ (mọi PO phát sinh từ tồn kho dưới Min Level giờ phải qua PR trước — xem
    ``PrCreatePrefillTest``). Regression test đảm bảo ``po_create`` không còn
    đọc các query param này nữa dù URL cũ (đã lưu bookmark/link cũ) vẫn được gọi.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.client.force_login(self.user)

    def test_TC_PUR_02_001_product_and_qty_query_params_ignored(self):
        response = self.client.get(reverse('purchasing:po_create'), {'product': self.product.pk, 'qty': 50})
        formset = response.context['formset']
        self.assertNotIn('product', formset.forms[0].initial)
        self.assertNotIn('qty_ordered', formset.forms[0].initial)


class PrCreatePrefillTest(TestCase):
    """Bước E: prefill dòng item đầu + kho từ ``?product=&qty=&warehouse=`` (link
    từ dashboard tồn kho dưới Min Level, xem ``inventory/views.py::inventory_list``
    — thay cho lối tắt tạo PO thẳng đã bỏ). ``TC-PUR-02-<seq>``.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.warehouse = Warehouse.objects.create(
            code='KHO-01', name='Kho chính', warehouse_type=Warehouse.WarehouseType.MAIN)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.client.force_login(self.user)

    def _payload(self, **overrides):
        payload = {
            'warehouse': self.warehouse.pk,
            'min_level_origin': '1',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '1',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.pk,
            'items-0-qty_requested': 50,
        }
        payload.update(overrides)
        return payload

    def test_TC_PUR_02_002_prefills_first_item_and_warehouse_from_query_params(self):
        response = self.client.get(
            reverse('purchasing:pr_create'), {'product': self.product.pk, 'qty': 50, 'warehouse': self.warehouse.pk})
        formset = response.context['formset']
        self.assertEqual(str(formset.forms[0].initial.get('product')), str(self.product.pk))
        self.assertEqual(str(formset.forms[0].initial.get('qty_requested')), '50')
        self.assertEqual(str(response.context['form'].initial.get('warehouse')), str(self.warehouse.pk))
        self.assertTrue(response.context['min_level_origin'])

    def test_TC_PUR_02_003_no_query_params_no_prefill(self):
        response = self.client.get(reverse('purchasing:pr_create'))
        formset = response.context['formset']
        self.assertNotIn('product', formset.forms[0].initial)
        self.assertFalse(response.context['min_level_origin'])

    def test_TC_PUR_02_004_post_with_hidden_flag_sets_origin_min_level(self):
        response = self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertEqual(pr.origin, PurchaseRequest.Origin.MIN_LEVEL)

    def test_TC_PUR_02_005_post_without_hidden_flag_defaults_to_manual_origin(self):
        response = self.client.post(reverse('purchasing:pr_create'), self._payload(min_level_origin=''))
        pr = PurchaseRequest.objects.get()
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertEqual(pr.origin, PurchaseRequest.Origin.MANUAL)


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


class PurchaseRequestModelTest(TestCase):
    """PR model — ``TC-PR-MODEL-<seq>``."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff2', password='staff-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(
            code='KHO-02', name='Kho phụ', warehouse_type=Warehouse.WarehouseType.MAIN)

    def test_TC_PR_MODEL_001_get_absolute_url_points_to_pr_detail(self):
        """M9: Notification/AuditLog gắn với PR phải deep-link được tới đúng
        trang chi tiết của nó."""
        pr = PurchaseRequest.objects.create(requested_by=self.staff, warehouse=self.warehouse)
        self.assertEqual(pr.get_absolute_url(), reverse('purchasing:pr_detail', args=[pr.pk]))


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

    def _rejected_pr(self, requested_by=None):
        """PR REJECTED thật (đi qua ``_pending_pr`` + ``pr_reject`` thật) — dùng
        cho test reopen (Bước C)."""
        pr = self._pending_pr()
        if requested_by is not None:
            pr.requested_by = requested_by
            pr.save(update_fields=['requested_by'])
        self.client.force_login(self.manager)
        self.client.post(reverse('purchasing:pr_reject', args=[pr.pk]), {'reject_reason': 'Không đủ ngân sách'})
        self.client.force_login(self.staff)
        pr.refresh_from_db()
        return pr

    def test_TC_PR_001_001_staff_can_create(self):
        """``pr_create`` chỉ lưu DRAFT — không tự nộp duyệt nữa (Bước B)."""
        response = self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertEqual(pr.status, PurchaseRequest.Status.DRAFT)
        self.assertEqual(pr.requested_by, self.staff)
        self.assertEqual(pr.items.count(), 1)
        self.assertTrue(pr.request_no.startswith('PR-'))
        log = AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(pr.pk)).first()
        self.assertIsNotNone(log)
        self.assertFalse(Approval.objects.exists())

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

    def test_TC_PR_001_003b_inactive_product_rejected_in_item_form(self):
        """Bug fix: SKU đã is_active=False không được chọn cho dòng PR mới."""
        self.product.is_active = False
        self.product.save(update_fields=['is_active'])
        response = self.client.post(reverse('purchasing:pr_create'), self._payload())
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
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_PUR)

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

    def test_TC_PR_001_011_submit_notifies_department_manager_not_assigned_to(self):
        """Notify chỉ phát sinh khi thật sự Nộp (``pr_submit``), không phải lúc
        tạo nháp — mirror GRN submit. ``assigned_to`` KHÔNG được báo lúc Nộp
        nữa (xem test_TC_PR_2STAGE_008/test_TC_PR_001_019) — chỉ quản lý đang
        giữ quyền quyết định ở cấp hiện tại (ở đây: phòng Mua hàng, vì
        ``self.staff`` không có department nên bỏ qua bước 1) được báo."""
        self.client.post(reverse('purchasing:pr_create'), self._payload(assigned_to=self.purchasing_user.pk))
        pr = PurchaseRequest.objects.get()
        self.assertFalse(Notification.objects.exists())
        response = self.client.post(reverse('purchasing:pr_submit', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_PUR)
        self.assertTrue(Notification.objects.filter(recipient=self.purchasing_manager).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.purchasing_user).exists())

    def test_TC_PR_001_012_update_only_allowed_while_draft_and_owner(self):
        """``pr_update``: đúng chủ + còn DRAFT thì sửa được; sai chủ hoặc đã Nộp
        thì bị chặn."""
        self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()

        response = self.client.post(
            reverse('purchasing:pr_update', args=[pr.pk]), self._payload(note='Đã sửa'))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.note, 'Đã sửa')

        other_staff = User.objects.create_user(
            username='staff-khac', password='staff-khac-123', role=User.Role.STAFF)
        self.client.force_login(other_staff)
        response = self.client.post(
            reverse('purchasing:pr_update', args=[pr.pk]), self._payload(note='Sửa trộm'))
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.staff)
        submit_purchase_request(pr, actor=self.staff)
        response = self.client.post(
            reverse('purchasing:pr_update', args=[pr.pk]), self._payload(note='Sửa sau khi nộp'))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.note, 'Đã sửa')

    def test_TC_PR_001_012b_concurrent_submit_during_update_not_overwritten(self):
        """BUG-02: nếu PR được chính chủ nộp (qua request khác) đúng vào khoảng
        hở giữa lần đọc đối tượng đầu tiên của ``pr_update`` và lúc view khóa
        row bằng ``select_for_update()`` để lưu, view phải nhận ra status mới
        (không phải bản DRAFT cũ còn giữ trong bộ nhớ) và từ chối lưu — không
        được ghi đè PR đã PENDING_DEPT/PENDING_PUR trở lại DRAFT."""
        self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()

        real_get_object_or_404 = purchasing_views.get_object_or_404
        call_count = {'n': 0}

        def _sneaky_get_object_or_404(*args, **kwargs):
            call_count['n'] += 1
            if call_count['n'] == 2:
                submit_purchase_request(pr, actor=self.staff)
            return real_get_object_or_404(*args, **kwargs)

        with patch('purchasing.views.get_object_or_404', side_effect=_sneaky_get_object_or_404):
            response = self.client.post(
                reverse('purchasing:pr_update', args=[pr.pk]), self._payload(note='Sửa trong lúc đua'))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertNotEqual(pr.status, PurchaseRequest.Status.DRAFT)
        self.assertNotEqual(pr.note, 'Sửa trong lúc đua')

    def test_TC_PR_001_013_submit_only_allowed_while_draft_and_owner(self):
        self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()

        other_staff = User.objects.create_user(
            username='staff-khac2', password='staff-khac2-123', role=User.Role.STAFF)
        self.client.force_login(other_staff)
        response = self.client.post(reverse('purchasing:pr_submit', args=[pr.pk]))
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.staff)
        self.client.post(reverse('purchasing:pr_submit', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_PUR)

        response = self.client.post(reverse('purchasing:pr_submit', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_PUR)

    def test_TC_PR_001_014_purchasing_role_can_create_edit_submit_own_pr(self):
        """Bug-fix 2026-07-28 điểm 2: PURCHASING giờ tự tạo/sửa/nộp được PR của
        chính mình (trước đây không có quyền ``create`` trên module ``pr``)."""
        self.client.force_login(self.purchasing_user)
        response = self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertEqual(pr.status, PurchaseRequest.Status.DRAFT)

        response = self.client.post(
            reverse('purchasing:pr_update', args=[pr.pk]), self._payload(note='Mua tự sửa'))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))

        response = self.client.post(reverse('purchasing:pr_submit', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_PUR)

    def test_TC_PR_001_015_owner_can_reopen_rejected_pr(self):
        """Bước C: PR REJECTED không phải ngõ cụt — đúng chủ mở lại được về DRAFT,
        giữ nguyên ``reject_reason``/``decided_by``/``decided_at`` làm lịch sử."""
        pr = self._rejected_pr()
        old_decided_by, old_decided_at = pr.decided_by, pr.decided_at
        response = self.client.post(reverse('purchasing:pr_reopen', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.DRAFT)
        self.assertEqual(pr.reject_reason, 'Không đủ ngân sách')
        self.assertEqual(pr.decided_by, old_decided_by)
        self.assertEqual(pr.decided_at, old_decided_at)

    def test_TC_PR_001_016_reopen_forbidden_for_non_owner(self):
        pr = self._rejected_pr()
        other_staff = User.objects.create_user(
            username='staff-khac3', password='staff-khac3-123', role=User.Role.STAFF)
        self.client.force_login(other_staff)
        response = self.client.post(reverse('purchasing:pr_reopen', args=[pr.pk]))
        self.assertEqual(response.status_code, 403)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.REJECTED)

    def test_TC_PR_001_017_reopen_wrong_state_shows_error(self):
        pr = self._pending_pr()
        response = self.client.post(reverse('purchasing:pr_reopen', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_PUR)

    def test_TC_PR_001_018_reopen_then_edit_and_resubmit_creates_new_approval(self):
        """Sau reopen, sửa (``pr_update``) rồi nộp lại (``pr_submit``) tạo được
        ``Approval`` PENDING mới — không đụng ``unique_pending_approval_per_target``
        vì ``Approval`` cũ đã REJECTED, không còn PENDING."""
        pr = self._rejected_pr()
        reopen_purchase_request(pr, actor=self.staff)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.DRAFT)

        response = self.client.post(
            reverse('purchasing:pr_update', args=[pr.pk]), self._payload(note='Sửa lại sau khi mở lại'))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.note, 'Sửa lại sau khi mở lại')

        response = self.client.post(reverse('purchasing:pr_submit', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_PUR)
        self.assertEqual(Approval.objects.filter(target_id=str(pr.pk)).count(), 2)
        self.assertEqual(
            Approval.objects.filter(target_id=str(pr.pk), status=Approval.Status.PENDING).count(), 1)

    def test_TC_PR_001_019_assigned_to_notified_at_approve_not_submit(self):
        """Bug fix M1 + luồng 2 cấp: ``assigned_to`` không được báo lúc Nộp nữa
        (tránh lộ PR sớm — xem test_TC_PR_2STAGE_008). Thông báo thật sự "cần
        xử lý" (tạo PO) chỉ gửi lúc PR được duyệt hẳn (APPROVED)."""
        pr = self._pending_pr(assigned_to=self.purchasing_user)
        self.assertFalse(Notification.objects.filter(recipient=self.purchasing_user).exists())

        self.client.force_login(self.purchasing_manager)
        response = self.client.post(reverse('purchasing:pr_approve', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))

        approve_notification = Notification.objects.filter(
            recipient=self.purchasing_user, verb__contains='đã được duyệt').first()
        self.assertIsNotNone(approve_notification)
        self.assertIn('tạo PO', approve_notification.verb)

    def test_TC_PR_001_020_reject_reason_shown_only_while_rejected(self):
        """L1: ``pr_detail.html`` chỉ hiện "Lý do từ chối" khi PR đang REJECTED.
        ``reject_reason`` vẫn được giữ lại trong DB sau reopen làm lịch sử (xem
        test_TC_PR_001_015), nhưng một khi PR đã được nộp lại và duyệt thành
        APPROVED, lý do từ chối cũ không còn phản ánh trạng thái hiện tại nên
        không được hiển thị nữa."""
        pr = self._rejected_pr()
        response = self.client.get(reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertContains(response, 'Lý do từ chối')
        self.assertContains(response, 'Không đủ ngân sách')

        reopen_purchase_request(pr, actor=self.staff)
        self.client.post(reverse('purchasing:pr_update', args=[pr.pk]), self._payload(note='Sửa lại'))
        self.client.post(reverse('purchasing:pr_submit', args=[pr.pk]))
        self.client.force_login(self.purchasing_manager)
        self.client.post(reverse('purchasing:pr_approve', args=[pr.pk]))
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.APPROVED)
        self.assertEqual(pr.reject_reason, 'Không đủ ngân sách')

        response = self.client.get(reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertNotContains(response, 'Lý do từ chối')

    def test_TC_PR_001_021_manager_can_delete_draft_pr(self):
        """L2: MANAGER (có quyền ``delete`` trên module ``pr`` + tầm nhìn toàn
        bộ qua ``_pr_can_view_all``) xoá thật được 1 PR còn DRAFT."""
        response = self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()
        self.client.force_login(self.manager)
        response = self.client.post(reverse('purchasing:pr_delete', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_list'))
        self.assertFalse(PurchaseRequest.objects.filter(pk=pr.pk).exists())
        log = AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_id=str(pr.pk)).first()
        self.assertIsNotNone(log)

    def test_TC_PR_001_022_staff_owner_cannot_delete_own_draft_pr_without_permission(self):
        """L2: STAFF không có ``delete`` trên module ``pr`` theo ma trận mặc
        định (chỉ MANAGER/ADMIN) — kể cả là chủ PR, tự xoá bản nháp của mình
        vẫn bị 403; quyền module ``delete`` là gate đầu tiên, đúng như đề xuất
        review "gate _pr_can_edit + can('delete','pr')"."""
        response = self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()
        response = self.client.post(reverse('purchasing:pr_delete', args=[pr.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(PurchaseRequest.objects.filter(pk=pr.pk).exists())

    def test_TC_PR_001_023_delete_forbidden_for_non_owner_manager_can(self):
        """L2: 1 MANAGER khác vẫn xoá được (``_pr_can_view_all``), nhưng nếu
        không có tầm nhìn toàn bộ và không phải chủ thì 403 — mirror check sở
        hữu của ``pr_update``/``pr_reopen``."""
        other_manager = User.objects.create_user(
            username='wm-khac', password='wm-khac-123', role=User.Role.MANAGER)
        response = self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()
        self.client.force_login(other_manager)
        response = self.client.post(reverse('purchasing:pr_delete', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_list'))
        self.assertFalse(PurchaseRequest.objects.filter(pk=pr.pk).exists())

    def test_TC_PR_001_024_delete_wrong_state_shows_error(self):
        """L2: PR đã Nộp (PENDING) không xoá được nữa — báo lỗi, PR vẫn còn."""
        pr = self._pending_pr()
        self.client.force_login(self.manager)
        response = self.client.post(reverse('purchasing:pr_delete', args=[pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertTrue(PurchaseRequest.objects.filter(pk=pr.pk).exists())

    def test_TC_PR_001_025_delete_button_hidden_unless_draft_and_permitted(self):
        """L2: nút "Xoá yêu cầu này" chỉ hiện trên trang chi tiết khi PR còn
        DRAFT và người xem có quyền ``delete`` (ở đây: MANAGER)."""
        response = self.client.post(reverse('purchasing:pr_create'), self._payload())
        pr = PurchaseRequest.objects.get()
        self.client.force_login(self.manager)
        response = self.client.get(reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertContains(response, 'Xoá yêu cầu này')

        self.client.force_login(self.staff)
        response = self.client.get(reverse('purchasing:pr_detail', args=[pr.pk]))
        self.assertNotContains(response, 'Xoá yêu cầu này')


class PurchaseRequestVisibilityTest(TestCase):
    """Phạm vi xem PR: nhân viên phòng khác chỉ thấy PR do chính mình tạo; nhân
    viên phòng Mua hàng THƯỜNG (không phải quản lý) chỉ thấy PR được chỉ định/
    chuyển tiếp cho mình SAU KHI đã ``APPROVED`` (chưa duyệt xong thì chưa được
    thấy — tránh lộ PR sớm, xem luồng duyệt 2 cấp); quản lý phòng Mua hàng
    KHÔNG còn xem được PR nháp/chưa từng qua tay mình của phòng khác (thu hẹp
    so với thiết kế 1 cấp cũ — xem ``PurchaseRequestTwoStageVisibilityTest`` cho
    các kịch bản xoay quanh 2 cấp duyệt); chỉ Manager/Admin xem toàn bộ.
    ``TC-PR-002-<seq>``.
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

    def test_TC_PR_002_002_purchasing_staff_does_not_see_assigned_pr_before_approved(self):
        """``assigned_to`` KHÔNG thấy được PR khi còn DRAFT/chờ duyệt — chỉ
        thấy sau khi PR đã ``APPROVED`` (xem test 007)."""
        self.pr_b.assigned_to = self.purchasing_user
        self.pr_b.save(update_fields=['assigned_to'])
        self.client.force_login(self.purchasing_user)
        response = self.client.get(reverse('purchasing:pr_list'))
        prs = set(response.context['prs'])
        self.assertEqual(prs, set())

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

    def test_TC_PR_002_007_purchasing_staff_can_view_assigned_pr_detail_once_approved(self):
        self.pr_b.assigned_to = self.purchasing_user
        self.pr_b.status = PurchaseRequest.Status.APPROVED
        self.pr_b.save(update_fields=['assigned_to', 'status'])
        self.client.force_login(self.purchasing_user)
        response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr_b.pk]))
        self.assertEqual(response.status_code, 200)

    def test_TC_PR_002_007b_purchasing_staff_cannot_view_assigned_pr_detail_while_pending(self):
        """Cùng PR/cùng ``assigned_to`` như test 007 nhưng còn đang chờ duyệt
        (``PENDING_PUR``) — chưa thấy được, đúng yêu cầu #2 (không lộ PR sớm)."""
        self.pr_b.assigned_to = self.purchasing_user
        self.pr_b.status = PurchaseRequest.Status.PENDING_PUR
        self.pr_b.save(update_fields=['assigned_to', 'status'])
        self.client.force_login(self.purchasing_user)
        response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr_b.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_PR_002_008_purchasing_manager_does_not_see_other_dept_draft_pr(self):
        """Thu hẹp so với thiết kế 1 cấp cũ: quản lý phòng Mua hàng không còn
        nằm trong tầng "xem toàn bộ" — PR nháp của phòng khác, chưa từng qua
        tay Mua hàng (chưa có ``Approval(department=PURCHASING)``), không hiện
        trong danh sách của họ nữa."""
        self.client.force_login(self.purchasing_manager)
        response = self.client.get(reverse('purchasing:pr_list'))
        prs = set(response.context['prs'])
        self.assertEqual(prs, set())


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
        # Đi qua flow thật (submit + decide) thay vì gán status=APPROVED trực
        # tiếp — quản lý phòng Mua hàng chỉ xem được PR đã/đang qua cấp Mua
        # hàng nhờ lịch sử ``Approval(department=PURCHASING)`` thật
        # (``_pr_reached_pur_approval``), gán tay không tạo ra bản ghi đó.
        self.pr = PurchaseRequest.objects.create(requested_by=self.staff, warehouse=self.warehouse)
        submit_purchase_request(self.pr, actor=self.staff)
        approval = Approval.objects.get(target_id=str(self.pr.pk), status=Approval.Status.PENDING)
        decide_purchase_request(approval, True, actor=self.purchasing_manager)
        self.pr.refresh_from_db()

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
        self.pr.status = PurchaseRequest.Status.PENDING_PUR
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

    def test_TC_PR_PO_006_created_po_has_source_from_pr(self):
        """Bước E: PO tạo qua ?from_pr= luôn source=FROM_PR (ngược lại PO tạo
        thủ công mặc định source=MANUAL, xem TC_PUR_001_003)."""
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
        self.client.post(reverse('purchasing:po_create'), payload)
        po = PurchaseOrder.objects.get()
        self.assertEqual(po.source, PurchaseOrder.Source.FROM_PR)

    def test_TC_PR_PO_007_get_prefills_supplier_from_preferred_supplier(self):
        """Bước E: dòng item đầu của PR có preferred_supplier -> form PO GET
        gợi ý sẵn NCC đó (người dùng vẫn đổi được)."""
        self.product.preferred_supplier = self.supplier
        self.product.save(update_fields=['preferred_supplier'])
        response = self.client.get(reverse('purchasing:po_create'), {'from_pr': self.pr.pk})
        self.assertEqual(response.context['form'].initial.get('supplier'), self.supplier.pk)

    def test_TC_PR_PO_008_get_no_prefill_supplier_when_product_has_none(self):
        response = self.client.get(reverse('purchasing:po_create'), {'from_pr': self.pr.pk})
        self.assertNotIn('supplier', response.context['form'].initial)


class PurchaseRequestTwoStageServiceTest(TestCase):
    """Service layer thuần (``submit_purchase_request``/``decide_purchase_request``
    gọi trực tiếp, không qua URL — view layer duyệt 2 cấp còn ở bước kế tiếp)
    cho luồng mới DRAFT -> PENDING_DEPT -> PENDING_PUR -> APPROVED/REJECTED.
    ``TC-PR-2STAGE-<seq>``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            username='kho-nv', password='kho-nv-123', role=User.Role.STAFF,
            department=User.Department.WAREHOUSE)
        self.warehouse_manager = User.objects.create_user(
            username='kho-ql', password='kho-ql-123', role=User.Role.MANAGER,
            department=User.Department.WAREHOUSE, is_manager=True)
        self.purchasing_manager = User.objects.create_user(
            username='mua-ql', password='mua-ql-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING, is_manager=True)
        self.purchasing_staff = User.objects.create_user(
            username='mua-nv', password='mua-nv-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING)
        self.warehouse = Warehouse.objects.create(
            code='KHO-2ST', name='Kho test 2 cấp', warehouse_type=Warehouse.WarehouseType.MAIN)

    def _draft_pr(self, requested_by=None, assigned_to=None):
        pr = PurchaseRequest.objects.create(
            requested_by=requested_by or self.staff, warehouse=self.warehouse, assigned_to=assigned_to)
        product = Product.objects.create(product_code=f'NVL-{pr.pk}', name='NVL test', uom='kg')
        PurchaseRequestItem.objects.create(purchase_request=pr, product=product, qty_requested=10)
        return pr

    def test_TC_PR_2STAGE_001_submit_goes_to_pending_dept_for_non_purchasing_requester(self):
        pr = self._draft_pr()
        submit_purchase_request(pr, actor=self.staff)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_DEPT)
        approval = Approval.objects.get(target_id=str(pr.pk), status=Approval.Status.PENDING)
        self.assertEqual(approval.department, User.Department.WAREHOUSE)

    def test_TC_PR_2STAGE_002_dept_approve_advances_to_pending_pur_without_integrity_error(self):
        """Điểm rủi ro cao nhất của toàn bộ thiết kế 2 cấp: tạo Approval cấp 2
        (PURCHASING) ngay sau khi Approval cấp 1 (WAREHOUSE) vừa APPROVED —
        không được đụng ràng buộc ``unique_pending_approval_per_target`` (chặn
        2 bản ghi PENDING cùng target). Nếu thứ tự gọi sai (tạo Approval cấp 2
        bên trong ``on_approve()``, trước khi ``decide_approval()`` lưu status
        cấp 1), test này sẽ raise ``ValidationError`` thay vì pass."""
        pr = self._draft_pr()
        submit_purchase_request(pr, actor=self.staff)
        stage1_approval = Approval.objects.get(target_id=str(pr.pk), status=Approval.Status.PENDING)

        decide_purchase_request(stage1_approval, True, actor=self.warehouse_manager)

        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_PUR)
        self.assertIsNone(pr.decided_by)

        stage1_approval.refresh_from_db()
        self.assertEqual(stage1_approval.status, Approval.Status.APPROVED)

        self.assertEqual(Approval.objects.filter(target_id=str(pr.pk)).count(), 2)
        stage2_approval = Approval.objects.get(target_id=str(pr.pk), status=Approval.Status.PENDING)
        self.assertEqual(stage2_approval.department, User.Department.PURCHASING)

    def test_TC_PR_2STAGE_003_pur_approve_after_dept_approve_finalizes(self):
        pr = self._draft_pr(assigned_to=self.purchasing_staff)
        submit_purchase_request(pr, actor=self.staff)
        stage1 = Approval.objects.get(target_id=str(pr.pk), status=Approval.Status.PENDING)
        decide_purchase_request(stage1, True, actor=self.warehouse_manager)

        stage2 = Approval.objects.get(target_id=str(pr.pk), status=Approval.Status.PENDING)
        decide_purchase_request(stage2, True, actor=self.purchasing_manager)

        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.APPROVED)
        self.assertEqual(pr.decided_by, self.purchasing_manager)
        self.assertIsNotNone(pr.decided_at)
        self.assertTrue(Notification.objects.filter(
            recipient=self.purchasing_staff, verb__contains='tạo PO').exists())

    def test_TC_PR_2STAGE_004_reject_at_dept_stage_ends_immediately(self):
        pr = self._draft_pr()
        submit_purchase_request(pr, actor=self.staff)
        stage1 = Approval.objects.get(target_id=str(pr.pk), status=Approval.Status.PENDING)
        decide_purchase_request(stage1, False, actor=self.warehouse_manager, note='Không cần thiết')

        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.REJECTED)
        self.assertEqual(pr.reject_reason, 'Không cần thiết')
        self.assertEqual(pr.decided_by, self.warehouse_manager)
        self.assertEqual(Approval.objects.filter(target_id=str(pr.pk)).count(), 1)

    def test_TC_PR_2STAGE_005_reject_at_pur_stage_ends_immediately(self):
        pr = self._draft_pr()
        submit_purchase_request(pr, actor=self.staff)
        stage1 = Approval.objects.get(target_id=str(pr.pk), status=Approval.Status.PENDING)
        decide_purchase_request(stage1, True, actor=self.warehouse_manager)
        stage2 = Approval.objects.get(target_id=str(pr.pk), status=Approval.Status.PENDING)
        decide_purchase_request(stage2, False, actor=self.purchasing_manager, note='Giá quá cao')

        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.REJECTED)
        self.assertEqual(pr.reject_reason, 'Giá quá cao')
        self.assertEqual(pr.decided_by, self.purchasing_manager)

    def test_TC_PR_2STAGE_006_purchasing_requester_skips_stage_one(self):
        pr = self._draft_pr(requested_by=self.purchasing_staff)
        submit_purchase_request(pr, actor=self.purchasing_staff)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_PUR)
        approval = Approval.objects.get(target_id=str(pr.pk), status=Approval.Status.PENDING)
        self.assertEqual(approval.department, User.Department.PURCHASING)

    def test_TC_PR_2STAGE_007_blank_department_requester_skips_stage_one(self):
        requester = User.objects.create_user(
            username='no-dept', password='no-dept-123', role=User.Role.STAFF)
        pr = self._draft_pr(requested_by=requester)
        submit_purchase_request(pr, actor=requester)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PurchaseRequest.Status.PENDING_PUR)

    def test_TC_PR_2STAGE_008_submit_does_not_notify_assigned_to(self):
        """Bug fix: ``assigned_to`` không còn được báo lúc Nộp — chỉ báo khi PR
        thật sự APPROVED (xem test_TC_PR_2STAGE_003)."""
        pr = self._draft_pr(assigned_to=self.purchasing_staff)
        submit_purchase_request(pr, actor=self.staff)
        self.assertFalse(Notification.objects.filter(recipient=self.purchasing_staff).exists())


class PurchaseRequestTwoStageVisibilityTest(TestCase):
    """View layer (qua HTTP client, không gọi service trực tiếp) cho các quy
    tắc tầm nhìn/quyền hành động riêng của luồng duyệt 2 cấp — bổ sung cho
    ``PurchaseRequestTwoStageServiceTest`` (thuần service). ``TC-PR-2STAGE-VIS-<seq>``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            username='kho-nv2', password='kho-nv2-123', role=User.Role.STAFF,
            department=User.Department.WAREHOUSE)
        # role=STAFF (không phải MANAGER) cố ý: role MANAGER có quyền
        # 'approve' pr toàn cục (ROLE_PERMISSIONS, dùng làm fallback
        # Manager/Admin trong can_decide_pr) — dùng role đó ở đây sẽ che mất
        # đúng ranh giới "chỉ còn quyền xem, hết quyền duyệt" mà VIS_005 cần
        # kiểm chứng cho quản lý phòng ban gốc (is_department_manager-scoped).
        self.warehouse_manager = User.objects.create_user(
            username='kho-ql2', password='kho-ql2-123', role=User.Role.STAFF,
            department=User.Department.WAREHOUSE, is_manager=True)
        self.qc_manager = User.objects.create_user(
            username='qc-ql2', password='qc-ql2-123', role=User.Role.QC,
            department=User.Department.QC, is_manager=True)
        self.purchasing_manager = User.objects.create_user(
            username='mua-ql2', password='mua-ql2-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING, is_manager=True)
        self.purchasing_staff = User.objects.create_user(
            username='mua-nv2', password='mua-nv2-123', role=User.Role.PURCHASING,
            department=User.Department.PURCHASING)
        self.warehouse = Warehouse.objects.create(
            code='KHO-2STV', name='Kho test 2 cấp view', warehouse_type=Warehouse.WarehouseType.MAIN)
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, assigned_to=self.purchasing_staff)
        product = Product.objects.create(product_code=f'NVL-{self.pr.pk}', name='NVL test', uom='kg')
        PurchaseRequestItem.objects.create(purchase_request=self.pr, product=product, qty_requested=10)
        submit_purchase_request(self.pr, actor=self.staff)
        self.pr.refresh_from_db()

    def test_TC_PR_2STAGE_VIS_001_other_department_manager_cannot_view_pending_dept(self):
        """PR đang chờ duyệt ở phòng Kho — quản lý phòng QC (không liên quan)
        không xem được, dù biết pk."""
        self.assertEqual(self.pr.status, PurchaseRequest.Status.PENDING_DEPT)
        self.client.force_login(self.qc_manager)
        response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_PR_2STAGE_VIS_002_other_department_manager_cannot_approve_pending_dept(self):
        self.client.force_login(self.qc_manager)
        response = self.client.post(reverse('purchasing:pr_approve', args=[self.pr.pk]))
        self.assertEqual(response.status_code, 403)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PurchaseRequest.Status.PENDING_DEPT)

    def test_TC_PR_2STAGE_VIS_003_origin_department_manager_can_approve_stage_one_via_http(self):
        self.client.force_login(self.warehouse_manager)
        response = self.client.post(reverse('purchasing:pr_approve', args=[self.pr.pk]))
        self.assertRedirects(response, reverse('purchasing:pr_detail', args=[self.pr.pk]))
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PurchaseRequest.Status.PENDING_PUR)

    def test_TC_PR_2STAGE_VIS_004_assigned_to_cannot_view_while_pending_dept_or_pending_pur(self):
        """Yêu cầu #2: nhân viên PUR được ``assigned_to`` không thấy PR ở CẢ
        HAI cấp chờ duyệt — chỉ thấy sau khi PR ``APPROVED`` hẳn."""
        self.client.force_login(self.purchasing_staff)
        response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr.pk]))
        self.assertEqual(response.status_code, 403)

        decide_purchase_request(
            Approval.objects.get(target_id=str(self.pr.pk), status=Approval.Status.PENDING),
            True, actor=self.warehouse_manager,
        )
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PurchaseRequest.Status.PENDING_PUR)
        response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_PR_2STAGE_VIS_005_origin_department_manager_readonly_after_stage_one(self):
        """Yêu cầu chốt: sau khi PR chuyển sang cấp Mua hàng, quản lý phòng gốc
        vẫn xem được (theo dõi tiến độ) nhưng không còn action gì — không phải
        chỉ ẩn nút, view thật (``pr_approve``) cũng phải 403."""
        decide_purchase_request(
            Approval.objects.get(target_id=str(self.pr.pk), status=Approval.Status.PENDING),
            True, actor=self.warehouse_manager,
        )
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PurchaseRequest.Status.PENDING_PUR)

        self.client.force_login(self.warehouse_manager)
        response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_approve'])
        self.assertFalse(response.context['can_edit'])
        self.assertFalse(response.context['can_delete'])

        approve_response = self.client.post(reverse('purchasing:pr_approve', args=[self.pr.pk]))
        self.assertEqual(approve_response.status_code, 403)

    def test_TC_PR_2STAGE_VIS_006_purchasing_manager_sees_pr_once_at_pending_pur(self):
        """Đối xứng với ``PurchaseRequestVisibilityTest`` (không còn xem
        DRAFT/PENDING_DEPT của phòng khác): một khi PR đã tới cấp Mua hàng,
        quản lý Mua hàng phải thấy nó trong danh sách để duyệt."""
        decide_purchase_request(
            Approval.objects.get(target_id=str(self.pr.pk), status=Approval.Status.PENDING),
            True, actor=self.warehouse_manager,
        )
        self.client.force_login(self.purchasing_manager)
        response = self.client.get(reverse('purchasing:pr_list'))
        prs = set(response.context['prs'])
        self.assertEqual(prs, {self.pr})
        detail_response = self.client.get(reverse('purchasing:pr_detail', args=[self.pr.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertTrue(detail_response.context['can_approve'])
