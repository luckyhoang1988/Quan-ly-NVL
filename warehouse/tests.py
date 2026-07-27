from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog

from .models import Location, MIN_LOCATIONS_PER_WAREHOUSE, Warehouse
from .services import (
    activate_warehouse, deactivate_warehouse, get_default_location, get_scrap_warehouse, get_staging_warehouse,
)

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
        payload = {
            'code': 'KHO-HN', 'name': 'Kho Hà Nội', 'address': '123 Đường A', 'capacity': 1000,
            'warehouse_type': Warehouse.WarehouseType.MAIN,
        }
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
        Location.objects.create(warehouse=self.warehouse, code='A-01')  # đủ 2 vị trí active để B-01 khoá được
        loc = Location.objects.create(warehouse=self.warehouse, code='B-01')
        self.client.post(reverse('warehouse:location_toggle_active', args=[self.warehouse.pk, loc.pk]))
        loc.refresh_from_db()
        self.assertFalse(loc.is_active)
        self.client.post(reverse('warehouse:location_toggle_active', args=[self.warehouse.pk, loc.pk]))
        loc.refresh_from_db()
        self.assertTrue(loc.is_active)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.UPDATE, target_id=str(loc.pk)).count(), 2)

    def test_TC_WM_02_005_cannot_deactivate_last_active_location(self):
        """Bug fix: khoá hết vị trí active của 1 kho làm get_default_location()
        vỡ ở QC/stocktake sau đó — chặn ngay tại đây thay vì để lỗi trồi lên
        lúc chạy 1 luồng khác không liên quan."""
        loc = Location.objects.create(warehouse=self.warehouse, code='B-01')
        response = self.client.post(
            reverse('warehouse:location_toggle_active', args=[self.warehouse.pk, loc.pk]), follow=True)
        loc.refresh_from_db()
        self.assertTrue(loc.is_active)
        messages = list(response.context['messages'])
        self.assertTrue(any('cuối cùng' in str(m) for m in messages))

    def test_TC_WM_02_006_can_deactivate_when_another_active_location_remains(self):
        Location.objects.create(warehouse=self.warehouse, code='A-01')
        loc = Location.objects.create(warehouse=self.warehouse, code='B-01')
        self.client.post(reverse('warehouse:location_toggle_active', args=[self.warehouse.pk, loc.pk]))
        loc.refresh_from_db()
        self.assertFalse(loc.is_active)

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

    def test_filter_type_main(self):
        Warehouse.objects.create(code='KHO-STG', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        response = self.client.get(
            reverse('warehouse:warehouse_list'), {'type': 'STAGING', 'page_size': 50})
        codes = [w.code for w in response.context['warehouses']]
        self.assertEqual(codes, ['KHO-STG'])


class WarehouseTypeSingletonTest(TestCase):
    """warehouse_type mặc định MAIN + ràng buộc duy nhất STAGING/SCRAP (M2)."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.client.force_login(self.manager)

    def test_default_type_is_main(self):
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.assertEqual(warehouse.warehouse_type, Warehouse.WarehouseType.MAIN)

    def test_second_active_staging_rejected_by_db_constraint(self):
        Warehouse.objects.create(code='KHO-STG1', name='Kho chờ 1', warehouse_type=Warehouse.WarehouseType.STAGING)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Warehouse.objects.create(code='KHO-STG2', name='Kho chờ 2', warehouse_type=Warehouse.WarehouseType.STAGING)

    def test_second_active_scrap_rejected_by_form(self):
        Warehouse.objects.create(code='KHO-SCR1', name='Kho phế 1', warehouse_type=Warehouse.WarehouseType.SCRAP)
        response = self.client.post(reverse('warehouse:warehouse_create'), {
            'code': 'KHO-SCR2', 'name': 'Kho phế 2', 'address': '', 'capacity': '',
            'warehouse_type': Warehouse.WarehouseType.SCRAP,
        })
        self.assertEqual(response.status_code, 200)  # re-render với lỗi form
        self.assertFalse(Warehouse.objects.filter(code='KHO-SCR2').exists())

    def test_create_second_staging_after_deactivating_first_ok(self):
        old = Warehouse.objects.create(
            code='KHO-STG1', name='Kho chờ 1', warehouse_type=Warehouse.WarehouseType.STAGING, is_active=False)
        response = self.client.post(reverse('warehouse:warehouse_create'), {
            'code': 'KHO-STG2', 'name': 'Kho chờ 2', 'address': '', 'capacity': '',
            'warehouse_type': Warehouse.WarehouseType.STAGING,
        })
        new = Warehouse.objects.get(code='KHO-STG2')
        self.assertRedirects(response, reverse('warehouse:warehouse_detail', args=[new.pk]))
        old.refresh_from_db()
        self.assertFalse(old.is_active)

    def test_activate_second_staging_rejected_by_service(self):
        Warehouse.objects.create(code='KHO-STG1', name='Kho chờ 1', warehouse_type=Warehouse.WarehouseType.STAGING)
        old = Warehouse.objects.create(
            code='KHO-STG2', name='Kho chờ 2', warehouse_type=Warehouse.WarehouseType.STAGING, is_active=False)
        with self.assertRaises(ValidationError):
            activate_warehouse(old)
        old.refresh_from_db()
        self.assertFalse(old.is_active)

    def test_activate_second_staging_rejected_by_view_no_500(self):
        Warehouse.objects.create(code='KHO-STG1', name='Kho chờ 1', warehouse_type=Warehouse.WarehouseType.STAGING)
        old = Warehouse.objects.create(
            code='KHO-STG2', name='Kho chờ 2', warehouse_type=Warehouse.WarehouseType.STAGING, is_active=False)
        response = self.client.post(
            reverse('warehouse:warehouse_activate', args=[old.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        old.refresh_from_db()
        self.assertFalse(old.is_active)
        messages = list(response.context['messages'])
        self.assertTrue(any('đang hoạt động' in str(m) for m in messages))

    def test_activate_ok_when_no_other_active_of_same_type(self):
        old = Warehouse.objects.create(
            code='KHO-STG1', name='Kho chờ 1', warehouse_type=Warehouse.WarehouseType.STAGING, is_active=False)
        activate_warehouse(old)
        old.refresh_from_db()
        self.assertTrue(old.is_active)

    def test_warehouse_type_disabled_on_update(self):
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.client.post(reverse('warehouse:warehouse_update', args=[warehouse.pk]), {
            'code': 'KHO-HN', 'name': 'Kho Hà Nội', 'address': '', 'capacity': '',
            'warehouse_type': Warehouse.WarehouseType.SCRAP,  # cố tình đổi loại qua POST giả mạo
        })
        warehouse.refresh_from_db()
        self.assertEqual(warehouse.warehouse_type, Warehouse.WarehouseType.MAIN)  # bị bỏ qua vì disabled


class DeactivateWarehouseServiceTest(TestCase):
    """BR-WM-006: khoá kho — implement qua warehouse.services (M2)."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.client.force_login(self.manager)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')

    def test_deactivate_raises_when_qty_on_hand_positive(self):
        from catalog.models import Product
        from inventory.models import Inventory
        product = Product.objects.create(product_code='SP-01', name='Sản phẩm 1', uom='cái')
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)
        with self.assertRaises(ValidationError):
            deactivate_warehouse(self.warehouse)
        self.warehouse.refresh_from_db()
        self.assertTrue(self.warehouse.is_active)

    def test_deactivate_ok_when_no_stock(self):
        deactivate_warehouse(self.warehouse, actor=self.manager)
        self.warehouse.refresh_from_db()
        self.assertFalse(self.warehouse.is_active)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.DELETE, target_id=str(self.warehouse.pk)).exists())

    def test_view_surfaces_validation_error_as_message(self):
        from catalog.models import Product
        from inventory.models import Inventory
        product = Product.objects.create(product_code='SP-01', name='Sản phẩm 1', uom='cái')
        Inventory.objects.create(product=product, warehouse=self.warehouse, qty_on_hand=10)
        response = self.client.post(
            reverse('warehouse:warehouse_deactivate', args=[self.warehouse.pk]), follow=True)
        self.warehouse.refresh_from_db()
        self.assertTrue(self.warehouse.is_active)
        messages = list(response.context['messages'])
        self.assertTrue(any('tồn kho' in str(m) for m in messages))


class WarehouseSingletonHelpersTest(TestCase):
    """get_staging_warehouse/get_scrap_warehouse/get_default_location (M2)."""

    def test_get_staging_warehouse_raises_when_missing(self):
        with self.assertRaises(ValidationError):
            get_staging_warehouse()

    def test_get_staging_warehouse_returns_active_singleton(self):
        warehouse = Warehouse.objects.create(
            code='KHO-STG', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        self.assertEqual(get_staging_warehouse(), warehouse)

    def test_get_scrap_warehouse_returns_active_singleton(self):
        warehouse = Warehouse.objects.create(
            code='KHO-SCR', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        self.assertEqual(get_scrap_warehouse(), warehouse)

    def test_get_default_location_returns_first_active_location_by_code(self):
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        Location.objects.create(warehouse=warehouse, code='A-02')
        Location.objects.create(warehouse=warehouse, code='A-01', is_active=False)
        first_active = Location.objects.create(warehouse=warehouse, code='A-01-B')
        self.assertEqual(get_default_location(warehouse), first_active)

    def test_get_default_location_raises_when_no_active_location(self):
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        with self.assertRaises(ValidationError):
            get_default_location(warehouse)
