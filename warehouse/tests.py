from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import AuditLog
from catalog.models import Product
from inventory.models import Batch, Inventory
from inventory.services import transfer_stock
from partners.models import Supplier

from .models import Location, MIN_LOCATIONS_PER_WAREHOUSE, STAGING_AGING_DAYS, Warehouse
from .services import (
    activate_warehouse, deactivate_warehouse, get_default_location, get_scrap_warehouse, get_staging_warehouse,
    location_capacity_alerts, location_occupancy, ops_snapshot,
)

User = get_user_model()


class LocationOccupancyServiceTest(TestCase):
    """``location_occupancy`` (A1). TC-WH-LOC-001, 002, 004."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.other_warehouse = Warehouse.objects.create(code='KHO-HCM', name='Kho HCM')
        self.other_location = Location.objects.create(warehouse=self.other_warehouse, code='A-01')

    def _batch(self, code, location, status, qty_received=10, qty_used=0):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier,
            location=location, qty_received=qty_received, qty_used=qty_used, status=status,
        )

    def test_TC_WH_LOC_001_excludes_closed_and_other_warehouse(self):
        active = self._batch('LOT-01', self.location, Batch.Status.ACTIVE)
        self._batch('LOT-02', self.location, Batch.Status.CLOSED, qty_used=10)
        self._batch('LOT-03', self.other_location, Batch.Status.ACTIVE)
        result = list(location_occupancy(self.warehouse))
        self.assertEqual(result, [active])

    def test_TC_WH_LOC_002_includes_every_physical_status(self):
        statuses = [
            Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED, Batch.Status.PENDING_RECEIPT,
            Batch.Status.EXPIRED, Batch.Status.QUARANTINE,
        ]
        expected_codes = set()
        for i, status in enumerate(statuses):
            code = f'LOT-{i:02d}'
            self._batch(code, self.location, status, qty_used=1 if status == Batch.Status.PARTIAL_USED else 0)
            expected_codes.add(code)
        result_codes = {b.batch_code for b in location_occupancy(self.warehouse)}
        self.assertEqual(result_codes, expected_codes)

    def test_TC_WH_LOC_004_batch_closed_by_transfer_is_excluded(self):
        location2 = Location.objects.create(warehouse=self.warehouse, code='A-02')
        batch = self._batch('LOT-01', self.location, Batch.Status.ACTIVE, qty_received=10)
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=10)
        transfer_stock(batch=batch, to_location=location2, qty=10, actor=None)
        batch.refresh_from_db()
        self.assertEqual(batch.status, Batch.Status.CLOSED)
        result_ids = {b.pk for b in location_occupancy(self.warehouse)}
        self.assertNotIn(batch.pk, result_ids)


class WarehouseDetailOccupancyCardTest(TestCase):
    """Card "Tồn kho theo vị trí" (A1) trên warehouse_detail. TC-WH-LOC-003."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        Batch.objects.bulk_create([
            Batch(
                product=self.product, batch_code=f'LOT-{i:03d}', supplier=self.supplier,
                location=self.location, qty_received=1, status=Batch.Status.ACTIVE,
            )
            for i in range(35)
        ])
        self.client.force_login(self.staff)

    def test_TC_WH_LOC_003_card_paginates_default_30_and_shows_ui_text(self):
        response = self.client.get(reverse('warehouse:warehouse_detail', args=[self.warehouse.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tồn kho theo vị trí')
        self.assertEqual(len(response.context['page_obj']), 30)
        self.assertEqual(response.context['page_obj'].paginator.count, 35)

    def test_query_count_does_not_grow_with_batch_count(self):
        """N+1 guard (AC-WH-LOC-02): không dùng ngưỡng tuyệt đối (giòn khi Task 8/10
        thêm query sau này) — so query count khi 35 batch vs 5 batch, phải BẰNG NHAU vì
        select_related đã gộp join và trang chỉ đọc tối đa page_size=30 dòng bất kể
        tổng số batch thật có bao nhiêu. Warmup request trước mỗi lần đo để tránh sai
        lệch do cache nguội (ContentType/session...) — request đầu tiên trong test luôn
        tốn thêm vài query so với các request sau, không liên quan gì đến N+1."""
        url = reverse('warehouse:warehouse_detail', args=[self.warehouse.pk])
        self.client.get(url)  # warmup — bỏ kết quả, không đo
        with CaptureQueriesContext(connection) as ctx_many:
            self.client.get(url)
        Batch.objects.filter(location=self.location).exclude(
            batch_code__in=[f'LOT-{i:03d}' for i in range(5)]).delete()
        self.client.get(url)  # warmup lại sau khi đổi dữ liệu
        with CaptureQueriesContext(connection) as ctx_few:
            self.client.get(url)
        self.assertEqual(len(ctx_many.captured_queries), len(ctx_few.captured_queries))


class WarehouseDetailCapacityBadgeTest(TestCase):
    """Badge OK/Gần đầy/Vượt trên warehouse_detail (A2.a). TC-WH-CAP-008."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội', capacity=1000)
        self.loc_ok = Location.objects.create(warehouse=self.warehouse, code='A-01', capacity=100)
        self.loc_warn = Location.objects.create(warehouse=self.warehouse, code='A-02', capacity=100)
        self.loc_over = Location.objects.create(warehouse=self.warehouse, code='A-03', capacity=100)
        self.loc_none = Location.objects.create(warehouse=self.warehouse, code='A-04')
        Batch.objects.create(
            product=self.product, batch_code='LOT-OK', supplier=self.supplier,
            location=self.loc_ok, qty_received=10, status=Batch.Status.ACTIVE)
        Batch.objects.create(
            product=self.product, batch_code='LOT-WARN', supplier=self.supplier,
            location=self.loc_warn, qty_received=95, status=Batch.Status.ACTIVE)
        Batch.objects.create(
            product=self.product, batch_code='LOT-OVER', supplier=self.supplier,
            location=self.loc_over, qty_received=150, status=Batch.Status.ACTIVE)
        self.client.force_login(self.staff)

    def test_TC_WH_CAP_008_badges_match_ratio_thresholds(self):
        response = self.client.get(reverse('warehouse:warehouse_detail', args=[self.warehouse.pk]))
        badges = {loc.code: loc.capacity_badge for loc in response.context['locations']}
        self.assertEqual(badges['A-01']['css'], 'bg-success')
        self.assertEqual(badges['A-02']['css'], 'bg-warning text-dark')
        self.assertEqual(badges['A-03']['css'], 'bg-danger')
        self.assertIsNone(badges['A-04'])
        # tổng occupied kho = 10+95+150 = 255 / capacity 1000 = 0.255 -> OK
        self.assertEqual(response.context['warehouse_badge']['css'], 'bg-success')


class OpsSnapshotServiceTest(TestCase):
    """``ops_snapshot`` (A3). TC-WH-OPS-001, 002, 003."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')

    def test_TC_WH_OPS_001_staging_counts_active_qty_and_aged(self):
        staging = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        location = Location.objects.create(warehouse=staging, code='A-01')
        Batch.objects.create(
            product=self.product, batch_code='LOT-FRESH', supplier=self.supplier,
            location=location, qty_received=10, status=Batch.Status.ACTIVE)
        aged = Batch.objects.create(
            product=self.product, batch_code='LOT-AGED', supplier=self.supplier,
            location=location, qty_received=5, status=Batch.Status.ACTIVE)
        Batch.objects.filter(pk=aged.pk).update(
            created_at=timezone.now() - timedelta(days=STAGING_AGING_DAYS + 1))
        Batch.objects.create(
            product=self.product, batch_code='LOT-CLOSED', supplier=self.supplier,
            location=location, qty_received=1, qty_used=1, status=Batch.Status.CLOSED)
        snapshot = ops_snapshot(staging)
        self.assertEqual(snapshot['active_count'], 2)
        self.assertEqual(snapshot['active_qty'], 15)
        self.assertEqual(snapshot['aged_count'], 1)

    def test_TC_WH_OPS_002_main_counts_pending_handoff_only(self):
        """Mirror fixture pattern của ``inventory.tests.StockTransferPendingReceiptGuardTest``
        (Grn không cần GrnItem, QcInspection không cần started_at, WarehouseHandoff tạo
        thẳng bằng ``.objects.create`` — service ``create_handoff()`` chỉ cần khi test
        quan tâm tới side-effect notify(), không phải trường hợp ở đây)."""
        from inventory.models import WarehouseHandoff
        from inventory.services import accept_handoff
        from purchasing.models import PurchaseOrder
        from quality.models import QcInspection
        from receiving.models import Grn

        qc_user = User.objects.create_user(username='qc1', password='qc-pass-123', role=User.Role.QC)
        main = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        other_main = Warehouse.objects.create(code='KHO-HCM', name='Kho HCM')
        location = Location.objects.create(warehouse=main, code='A-01')
        po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        grn = Grn.objects.create(po=po, supplier=self.supplier, created_by=qc_user)
        inspection = QcInspection.objects.create(grn=grn, inspector=qc_user)
        batch = Batch.objects.create(
            product=self.product, batch_code='LOT-PENDING', supplier=self.supplier,
            location=location, qty_received=10, status=Batch.Status.PENDING_RECEIPT)
        handoff = WarehouseHandoff.objects.create(
            batch=batch, qc_inspection=inspection, destination_warehouse=main)

        self.assertEqual(ops_snapshot(main)['pending_handoff_count'], 1)
        self.assertEqual(ops_snapshot(other_main)['pending_handoff_count'], 0)

        accept_handoff(handoff, actor=qc_user)
        self.assertEqual(ops_snapshot(main)['pending_handoff_count'], 0)

    def test_TC_WH_OPS_003_scrap_sums_quarantine_qty_only(self):
        scrap = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        location = Location.objects.create(warehouse=scrap, code='A-01')
        Batch.objects.create(
            product=self.product, batch_code='LOT-Q1', supplier=self.supplier,
            location=location, qty_received=10, status=Batch.Status.QUARANTINE)
        Batch.objects.create(
            product=self.product, batch_code='LOT-Q2', supplier=self.supplier,
            location=location, qty_received=5, qty_used=2, status=Batch.Status.QUARANTINE)
        Batch.objects.create(
            product=self.product, batch_code='LOT-CLOSED', supplier=self.supplier,
            location=location, qty_received=1, qty_used=1, status=Batch.Status.CLOSED)
        snapshot = ops_snapshot(scrap)
        self.assertEqual(snapshot['quarantine_qty'], 13)


class LocationCapacityAlertsServiceTest(TestCase):
    """``location_capacity_alerts`` (A2). TC-WH-CAP-001, 002, 003, 009."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')

    def _batch(self, code, location, qty_received):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier,
            location=location, qty_received=qty_received, status=Batch.Status.ACTIVE,
        )

    def test_TC_WH_CAP_001_empty_when_both_capacity_none(self):
        self._batch('LOT-01', self.location, 100)
        self.assertEqual(location_capacity_alerts(self.location), [])

    def test_capacity_zero_treated_as_not_configured(self):
        self.location.capacity = 0
        self.location.save(update_fields=['capacity'])
        self._batch('LOT-01', self.location, 5)
        self.assertEqual(location_capacity_alerts(self.location), [])

    def test_TC_WH_CAP_002_location_near_full_warehouse_capacity_none(self):
        self.location.capacity = 100
        self.location.save(update_fields=['capacity'])
        self._batch('LOT-01', self.location, 95)  # ratio 0.95
        alerts = location_capacity_alerts(self.location)
        self.assertEqual(len(alerts), 1)
        self.assertIn('gần đầy', alerts[0])

    def test_TC_WH_CAP_003_location_over_capacity_warehouse_below_warn(self):
        self.location.capacity = 100
        self.location.save(update_fields=['capacity'])
        self.warehouse.capacity = 10000
        self.warehouse.save(update_fields=['capacity'])
        self._batch('LOT-01', self.location, 120)  # location ratio 1.2, warehouse ratio 0.012
        alerts = location_capacity_alerts(self.location)
        self.assertEqual(len(alerts), 1)
        self.assertIn('vượt dung tích', alerts[0])

    def test_TC_WH_CAP_009_both_levels_over_warn_return_two_messages(self):
        """AC-WH-CAP-08: warehouse ratio cộng dồn MỌI location của kho, gồm cả
        location đang xét (100) cộng location2 (90) -> 190/200 = 0.95 >= 0.9."""
        location2 = Location.objects.create(warehouse=self.warehouse, code='A-02')
        self.location.capacity = 100
        self.location.save(update_fields=['capacity'])
        self.warehouse.capacity = 200
        self.warehouse.save(update_fields=['capacity'])
        self._batch('LOT-01', self.location, 100)  # location ratio 1.0 -> OVER
        self._batch('LOT-02', location2, 90)
        alerts = location_capacity_alerts(self.location)
        self.assertEqual(len(alerts), 2)


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

    def test_TC_WM_01_003_menu_access_revoked_forbids_list(self):
        """Khối "Truy cập menu" (accounts/permissions.py::MENU_ITEMS) mặc định cấp cho mọi
        role — thu hồi riêng ``can_view_menu_warehouse`` của 1 user phải chặn được
        ``warehouse_list``, kể cả khi role của họ vẫn được xem kho bình thường."""
        perm = Permission.objects.get(
            codename='can_view_menu_warehouse', content_type__app_label='accounts')
        self.staff.user_permissions.remove(perm)
        self.client.force_login(self.staff)
        response = self.client.get(reverse('warehouse:warehouse_list'))
        self.assertEqual(response.status_code, 403)

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

    def test_TC_WM_01_004b_menu_access_revoked_forbids_create_and_detail(self):
        """BUG-06: thu hồi ``can_view_menu_warehouse`` phải chặn được cả
        ``warehouse_create``/``warehouse_update``/... lẫn ``warehouse_detail``,
        không chỉ ``warehouse_list`` (xem TC-WM-01-003)."""
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        perm = Permission.objects.get(
            codename='can_view_menu_warehouse', content_type__app_label='accounts')
        self.manager.user_permissions.remove(perm)
        response = self.client.post(reverse('warehouse:warehouse_create'), self._create_payload())
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse('warehouse:warehouse_detail', args=[warehouse.pk]))
        self.assertEqual(response.status_code, 403)

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
