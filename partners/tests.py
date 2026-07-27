from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog

from .models import Supplier

User = get_user_model()


class SupplierCrudTest(TestCase):
    """Supplier master data (mục 1d — bổ sung, không có mã FR riêng).

    Đặt tên test theo quy ước ``TC-PTN-001-<seq>`` (dùng "001" thay cho FR#
    vì partners không có mã FR trong SRS gốc).
    """

    def setUp(self):
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.client.force_login(self.manager)

    def _create_payload(self, **overrides):
        payload = {
            'supplier_code': 'NCC-0001', 'name': 'Công ty TNHH ABC',
            'contact_phone': '0901234567', 'lead_time_days': 7,
            'qty_tolerance_percent': 5, 'currency': 'VND', 'status': 'ACTIVE',
        }
        payload.update(overrides)
        return payload

    def test_TC_PTN_001_001_non_manager_forbidden(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('partners:supplier_create'), self._create_payload())
        self.assertEqual(response.status_code, 403)

    def test_TC_PTN_001_002_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('partners:supplier_create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_PTN_001_003_create_and_audit(self):
        response = self.client.post(reverse('partners:supplier_create'), self._create_payload())
        supplier = Supplier.objects.get(supplier_code='NCC-0001')
        self.assertRedirects(response, reverse('partners:supplier_list'))
        log = AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(supplier.pk)).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.manager)

    def test_TC_PTN_001_004_duplicate_code_rejected(self):
        Supplier.objects.create(supplier_code='NCC-0001', name='Cũ')
        response = self.client.post(reverse('partners:supplier_create'), self._create_payload())
        self.assertEqual(response.status_code, 200)  # re-render form với lỗi, không redirect
        self.assertFalse(Supplier.objects.filter(name='Công ty TNHH ABC').exists())

    def test_TC_PTN_001_005_any_authenticated_user_can_view(self):
        Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.client.force_login(self.staff)
        response = self.client.get(reverse('partners:supplier_list'))
        self.assertEqual(response.status_code, 200)

    def test_TC_PTN_001_006_update_and_audit(self):
        supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        response = self.client.post(
            reverse('partners:supplier_update', args=[supplier.pk]),
            self._create_payload(name='Công ty TNHH ABC (đổi tên)', status='INACTIVE'),
        )
        supplier.refresh_from_db()
        self.assertRedirects(response, reverse('partners:supplier_list'))
        self.assertEqual(supplier.name, 'Công ty TNHH ABC (đổi tên)')
        self.assertEqual(supplier.status, Supplier.Status.INACTIVE)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE, target_id=str(supplier.pk)).exists())

    def test_TC_PTN_001_007_purchasing_can_create_and_becomes_managed_by(self):
        """Nhân viên mua hàng (role PURCHASING) được tạo Supplier — NCC tự gán
        ``managed_by`` = người tạo (bổ sung theo yêu cầu người dùng 2026-07-26)."""
        purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        self.client.force_login(purchasing_user)
        response = self.client.post(reverse('partners:supplier_create'), self._create_payload())
        supplier = Supplier.objects.get(supplier_code='NCC-0001')
        self.assertRedirects(response, reverse('partners:supplier_list'))
        self.assertEqual(supplier.managed_by, purchasing_user)

    def test_TC_PTN_001_008_purchasing_can_edit_own_supplier(self):
        purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        supplier = Supplier.objects.create(
            supplier_code='NCC-0001', name='Công ty TNHH ABC', managed_by=purchasing_user)
        self.client.force_login(purchasing_user)
        response = self.client.post(
            reverse('partners:supplier_update', args=[supplier.pk]),
            self._create_payload(name='Công ty TNHH ABC (đổi tên)'),
        )
        supplier.refresh_from_db()
        self.assertRedirects(response, reverse('partners:supplier_list'))
        self.assertEqual(supplier.name, 'Công ty TNHH ABC (đổi tên)')

    def test_TC_PTN_001_009_purchasing_cannot_edit_others_supplier(self):
        """Role PURCHASING không sửa được NCC do đồng nghiệp mua hàng khác (hoặc
        Manager) tạo — chỉ sửa được đúng NCC mình quản lý."""
        purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        other_purchasing_user = User.objects.create_user(
            username='mua2', password='mua2-pass-123', role=User.Role.PURCHASING)
        supplier = Supplier.objects.create(
            supplier_code='NCC-0001', name='Công ty TNHH ABC', managed_by=other_purchasing_user)
        self.client.force_login(purchasing_user)
        response = self.client.post(
            reverse('partners:supplier_update', args=[supplier.pk]), self._create_payload())
        self.assertEqual(response.status_code, 403)

    def test_TC_PTN_001_010_manager_can_edit_any_supplier_regardless_of_managed_by(self):
        purchasing_user = User.objects.create_user(
            username='mua', password='mua-pass-123', role=User.Role.PURCHASING)
        supplier = Supplier.objects.create(
            supplier_code='NCC-0001', name='Công ty TNHH ABC', managed_by=purchasing_user)
        response = self.client.post(
            reverse('partners:supplier_update', args=[supplier.pk]),
            self._create_payload(name='Công ty TNHH ABC (Manager sửa)'),
        )
        supplier.refresh_from_db()
        self.assertRedirects(response, reverse('partners:supplier_list'))
        self.assertEqual(supplier.name, 'Công ty TNHH ABC (Manager sửa)')


class SupplierListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (status/tìm kiếm) trên supplier_list."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.client.force_login(self.staff)
        Supplier.objects.bulk_create([
            Supplier(
                supplier_code=f'NCC-{i:04d}', name=f'NCC {i}',
                status=Supplier.Status.ACTIVE if i % 2 == 0 else Supplier.Status.INACTIVE,
            )
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('partners:supplier_list'))
        self.assertEqual(len(response.context['suppliers']), 30)

    def test_page_size_50_shows_all(self):
        response = self.client.get(reverse('partners:supplier_list'), {'page_size': 50})
        self.assertEqual(len(response.context['suppliers']), 35)

    def test_filter_status_inactive(self):
        response = self.client.get(
            reverse('partners:supplier_list'), {'status': 'INACTIVE', 'page_size': 50})
        self.assertTrue(all(s.status == 'INACTIVE' for s in response.context['suppliers']))

    def test_filter_search_by_code(self):
        response = self.client.get(reverse('partners:supplier_list'), {'q': 'NCC-0001'})
        codes = [s.supplier_code for s in response.context['suppliers']]
        self.assertEqual(codes, ['NCC-0001'])
