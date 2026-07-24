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
            'contact': '0901234567', 'lead_time_days': 7, 'is_active': True,
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
            self._create_payload(name='Công ty TNHH ABC (đổi tên)', is_active=False),
        )
        supplier.refresh_from_db()
        self.assertRedirects(response, reverse('partners:supplier_list'))
        self.assertEqual(supplier.name, 'Công ty TNHH ABC (đổi tên)')
        self.assertFalse(supplier.is_active)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE, target_id=str(supplier.pk)).exists())


class SupplierListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (status/tìm kiếm) trên supplier_list."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.client.force_login(self.staff)
        Supplier.objects.bulk_create([
            Supplier(supplier_code=f'NCC-{i:04d}', name=f'NCC {i}', is_active=(i % 2 == 0))
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
            reverse('partners:supplier_list'), {'status': 'inactive', 'page_size': 50})
        self.assertTrue(all(not s.is_active for s in response.context['suppliers']))

    def test_filter_search_by_code(self):
        response = self.client.get(reverse('partners:supplier_list'), {'q': 'NCC-0001'})
        codes = [s.supplier_code for s in response.context['suppliers']]
        self.assertEqual(codes, ['NCC-0001'])
