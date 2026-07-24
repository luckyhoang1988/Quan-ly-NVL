from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog

from .models import Location, MIN_LOCATIONS_PER_WAREHOUSE, Warehouse

User = get_user_model()


class WarehouseCrudTest(TestCase):
    """FR-WM-01: CRUD kho (TC-WM-01-*)."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.client.force_login(self.manager)

    def _create_payload(self, **overrides):
        payload = {'code': 'KHO-HN', 'name': 'Kho Hà Nội', 'address': '123 Đường A', 'capacity': 1000}
        payload.update(overrides)
        return payload

    def test_TC_WM_01_001_non_manager_forbidden(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('warehouse:warehouse_create'), self._create_payload())
        self.assertEqual(response.status_code, 403)

    def test_TC_WM_01_002_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('warehouse:warehouse_create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_WM_01_003_create_auto_seeds_min_locations_and_audits(self):
        response = self.client.post(reverse('warehouse:warehouse_create'), self._create_payload())
        warehouse = Warehouse.objects.get(code='KHO-HN')
        self.assertRedirects(response, reverse('warehouse:warehouse_detail', args=[warehouse.pk]))
        self.assertEqual(warehouse.locations.count(), MIN_LOCATIONS_PER_WAREHOUSE)
        log = AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(warehouse.pk)).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.manager)
        self.assertEqual(log.ip_address, '127.0.0.1')

    def test_TC_WM_01_004_duplicate_code_rejected(self):
        Warehouse.objects.create(code='KHO-HN', name='Kho cũ')
        response = self.client.post(reverse('warehouse:warehouse_create'), self._create_payload())
        self.assertEqual(response.status_code, 200)  # re-render form với lỗi, không redirect
        self.assertFalse(Warehouse.objects.filter(name='Kho Hà Nội').exists())

    def test_TC_WM_01_005_any_authenticated_user_can_view(self):
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.client.force_login(self.staff)
        list_response = self.client.get(reverse('warehouse:warehouse_list'))
        detail_response = self.client.get(reverse('warehouse:warehouse_detail', args=[warehouse.pk]))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)

    def test_TC_WM_01_006_deactivate_then_activate_audits_both(self):
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.client.post(reverse('warehouse:warehouse_deactivate', args=[warehouse.pk]))
        warehouse.refresh_from_db()
        self.assertFalse(warehouse.is_active)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.DELETE, target_id=str(warehouse.pk)).exists())

        self.client.post(reverse('warehouse:warehouse_activate', args=[warehouse.pk]))
        warehouse.refresh_from_db()
        self.assertTrue(warehouse.is_active)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE, target_id=str(warehouse.pk)).exists())

    def test_TC_WM_01_007_update_does_not_touch_is_active(self):
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.client.post(
            reverse('warehouse:warehouse_update', args=[warehouse.pk]),
            {'code': 'KHO-HN', 'name': 'Kho Hà Nội (sửa)', 'address': '', 'capacity': ''},
        )
        warehouse.refresh_from_db()
        self.assertEqual(warehouse.name, 'Kho Hà Nội (sửa)')
        self.assertTrue(warehouse.is_active)


class LocationCrudTest(TestCase):
    """FR-WM-02: CRUD vị trí lưu trữ (TC-WM-02-*)."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.client.force_login(self.manager)

    def test_TC_WM_02_001_duplicate_code_in_same_warehouse_rejected(self):
        Location.objects.create(warehouse=self.warehouse, code='B-01')
        self.client.post(
            reverse('warehouse:location_create', args=[self.warehouse.pk]), {'code': 'B-01'})
        self.assertEqual(
            Location.objects.filter(warehouse=self.warehouse, code='B-01').count(), 1)

    def test_TC_WM_02_002_create_adds_location_and_audits(self):
        self.client.post(
            reverse('warehouse:location_create', args=[self.warehouse.pk]), {'code': 'B-01', 'capacity': 50})
        loc = Location.objects.get(warehouse=self.warehouse, code='B-01')
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.CREATE, target_id=str(loc.pk)).exists())

    def test_TC_WM_02_003_toggle_active_flips_flag_and_audits(self):
        loc = Location.objects.create(warehouse=self.warehouse, code='B-01')
        self.client.post(reverse('warehouse:location_toggle_active', args=[self.warehouse.pk, loc.pk]))
        loc.refresh_from_db()
        self.assertFalse(loc.is_active)
        self.client.post(reverse('warehouse:location_toggle_active', args=[self.warehouse.pk, loc.pk]))
        loc.refresh_from_db()
        self.assertTrue(loc.is_active)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.UPDATE, target_id=str(loc.pk)).count(), 2)

    def test_TC_WM_02_004_non_manager_forbidden(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('warehouse:location_create', args=[self.warehouse.pk]), {'code': 'B-01'})
        self.assertEqual(response.status_code, 403)


class WarehouseListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (status/tìm kiếm) trên warehouse_list."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.client.force_login(self.staff)
        Warehouse.objects.bulk_create([
            Warehouse(code=f'KHO-{i:03d}', name=f'Kho {i}', is_active=(i % 2 == 0))
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('warehouse:warehouse_list'))
        self.assertEqual(len(response.context['warehouses']), 30)

    def test_page_size_50_shows_all(self):
        response = self.client.get(reverse('warehouse:warehouse_list'), {'page_size': 50})
        self.assertEqual(len(response.context['warehouses']), 35)

    def test_filter_status_active(self):
        response = self.client.get(
            reverse('warehouse:warehouse_list'), {'status': 'active', 'page_size': 50})
        self.assertTrue(all(w.is_active for w in response.context['warehouses']))

    def test_filter_search_by_code(self):
        response = self.client.get(reverse('warehouse:warehouse_list'), {'q': 'KHO-001'})
        codes = [w.code for w in response.context['warehouses']]
        self.assertEqual(codes, ['KHO-001'])
