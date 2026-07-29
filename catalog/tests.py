from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog
from partners.models import Supplier

from .models import Product

User = get_user_model()


class ProductCrudTest(TestCase):
    """Product/SKU master data (mục 1c — bổ sung, không có mã FR riêng).

    Đặt tên test theo quy ước ``TC-CAT-001-<seq>`` (dùng "001" thay cho FR#
    vì catalog không có mã FR trong SRS gốc).
    """

    def setUp(self):
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.client.force_login(self.manager)

    def _create_payload(self, **overrides):
        payload = {
            'product_code': 'NVL-0001', 'name': 'Bột mì', 'category': 'Nguyên liệu',
            'uom': 'kg', 'min_level': 10, 'max_level': 100, 'is_active': True,
            'qc_sampling_method': Product.SamplingMethod.PERCENT, 'qc_sampling_value': 10,
        }
        payload.update(overrides)
        return payload

    def test_TC_CAT_001_001_non_manager_forbidden(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('catalog:product_create'), self._create_payload())
        self.assertEqual(response.status_code, 403)

    def test_TC_CAT_001_002_anonymous_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('catalog:product_create'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_TC_CAT_001_003_create_and_audit(self):
        response = self.client.post(reverse('catalog:product_create'), self._create_payload())
        product = Product.objects.get(product_code='NVL-0001')
        self.assertRedirects(response, reverse('catalog:product_list'))
        log = AuditLog.objects.filter(action=AuditLog.Action.CREATE, target_id=str(product.pk)).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.manager)

    def test_TC_CAT_001_004_duplicate_code_rejected(self):
        Product.objects.create(product_code='NVL-0001', name='Cũ', uom='kg')
        response = self.client.post(reverse('catalog:product_create'), self._create_payload())
        self.assertEqual(response.status_code, 200)  # re-render form với lỗi, không redirect
        self.assertFalse(Product.objects.filter(name='Bột mì').exists())

    def test_TC_CAT_001_004b_menu_access_revoked_forbids_create_and_update(self):
        """BUG-06: thu hồi ``can_view_menu_catalog`` phải chặn được cả
        ``product_create`` lẫn ``product_update``, không chỉ ``product_list``."""
        product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        perm = Permission.objects.get(
            codename='can_view_menu_catalog', content_type__app_label='accounts')
        self.manager.user_permissions.remove(perm)
        response = self.client.post(reverse('catalog:product_create'), self._create_payload())
        self.assertEqual(response.status_code, 403)
        response = self.client.get(reverse('catalog:product_update', args=[product.pk]))
        self.assertEqual(response.status_code, 403)

    def test_TC_CAT_001_005_any_authenticated_user_can_view(self):
        Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.client.force_login(self.staff)
        response = self.client.get(reverse('catalog:product_list'))
        self.assertEqual(response.status_code, 200)

    def test_TC_CAT_001_006_min_level_greater_than_max_level_rejected(self):
        response = self.client.post(
            reverse('catalog:product_create'),
            self._create_payload(min_level=100, max_level=10),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(product_code='NVL-0001').exists())

    def test_TC_CAT_001_008_holding_cost_rate_over_100_rejected(self):
        response = self.client.post(
            reverse('catalog:product_create'),
            self._create_payload(holding_cost_rate='150.00'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(product_code='NVL-0001').exists())

    def test_TC_CAT_001_009_holding_cost_rate_negative_rejected(self):
        response = self.client.post(
            reverse('catalog:product_create'),
            self._create_payload(holding_cost_rate='-1.00'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(product_code='NVL-0001').exists())

    def test_TC_CAT_001_010_holding_cost_rate_100_accepted(self):
        response = self.client.post(
            reverse('catalog:product_create'),
            self._create_payload(holding_cost_rate='100.00'),
        )
        product = Product.objects.get(product_code='NVL-0001')
        self.assertRedirects(response, reverse('catalog:product_list'))
        self.assertEqual(product.holding_cost_rate, 100)

    def test_TC_CAT_001_011_inactive_preferred_supplier_rejected_on_create(self):
        """Bước D: form lọc NCC ACTIVE — NCC inactive không chọn được khi tạo mới."""
        supplier = Supplier.objects.create(
            supplier_code='NCC-0001', name='NCC ngừng giao dịch', status=Supplier.Status.INACTIVE)
        response = self.client.post(
            reverse('catalog:product_create'),
            self._create_payload(preferred_supplier=supplier.pk),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(product_code='NVL-0001').exists())

    def test_TC_CAT_001_012_active_preferred_supplier_saved(self):
        supplier = Supplier.objects.create(supplier_code='NCC-0002', name='NCC hoạt động')
        response = self.client.post(
            reverse('catalog:product_create'),
            self._create_payload(preferred_supplier=supplier.pk),
        )
        product = Product.objects.get(product_code='NVL-0001')
        self.assertRedirects(response, reverse('catalog:product_list'))
        self.assertEqual(product.preferred_supplier_id, supplier.pk)

    def test_TC_CAT_001_013_update_keeps_existing_inactive_preferred_supplier_selectable(self):
        """Sản phẩm đã gán NCC từ trước, NCC đó sau đó bị khoá — vẫn sửa/lưu lại được
        sản phẩm giữ nguyên NCC đó (không bị form lọc rơi mất giá trị cũ)."""
        supplier = Supplier.objects.create(supplier_code='NCC-0003', name='NCC sắp khoá')
        product = Product.objects.create(
            product_code='NVL-0001', name='Bột mì', uom='kg', preferred_supplier=supplier)
        supplier.status = Supplier.Status.INACTIVE
        supplier.save(update_fields=['status'])

        response = self.client.post(
            reverse('catalog:product_update', args=[product.pk]),
            self._create_payload(name='Bột mì loại 1', preferred_supplier=supplier.pk),
        )
        product.refresh_from_db()
        self.assertRedirects(response, reverse('catalog:product_list'))
        self.assertEqual(product.name, 'Bột mì loại 1')
        self.assertEqual(product.preferred_supplier_id, supplier.pk)

    def test_TC_CAT_001_014_qc_sampling_percent_over_100_rejected(self):
        """BUG-10: PERCENT là % trên qty_received, >100 vô nghĩa."""
        response = self.client.post(
            reverse('catalog:product_create'),
            self._create_payload(qc_sampling_method=Product.SamplingMethod.PERCENT, qc_sampling_value=101),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(product_code='NVL-0001').exists())

    def test_TC_CAT_001_015_qc_sampling_percent_100_accepted(self):
        response = self.client.post(
            reverse('catalog:product_create'),
            self._create_payload(qc_sampling_method=Product.SamplingMethod.PERCENT, qc_sampling_value=100),
        )
        product = Product.objects.get(product_code='NVL-0001')
        self.assertRedirects(response, reverse('catalog:product_list'))
        self.assertEqual(product.qc_sampling_value, 100)

    def test_TC_CAT_001_016_qc_sampling_fixed_over_100_accepted(self):
        """FIXED là số lượng cố định — không có trần 100 như PERCENT."""
        response = self.client.post(
            reverse('catalog:product_create'),
            self._create_payload(qc_sampling_method=Product.SamplingMethod.FIXED, qc_sampling_value=500),
        )
        product = Product.objects.get(product_code='NVL-0001')
        self.assertRedirects(response, reverse('catalog:product_list'))
        self.assertEqual(product.qc_sampling_value, 500)

    def test_TC_CAT_001_017_switching_fixed_over_100_to_percent_rejected(self):
        product = Product.objects.create(
            product_code='NVL-0001', name='Bột mì', uom='kg',
            qc_sampling_method=Product.SamplingMethod.FIXED, qc_sampling_value=500)
        response = self.client.post(
            reverse('catalog:product_update', args=[product.pk]),
            self._create_payload(qc_sampling_method=Product.SamplingMethod.PERCENT, qc_sampling_value=500),
        )
        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.qc_sampling_method, Product.SamplingMethod.FIXED)
        self.assertEqual(product.qc_sampling_value, 500)

    def test_TC_CAT_001_007_update_and_audit(self):
        product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        response = self.client.post(
            reverse('catalog:product_update', args=[product.pk]),
            self._create_payload(name='Bột mì loại 1', is_active=False),
        )
        product.refresh_from_db()
        self.assertRedirects(response, reverse('catalog:product_list'))
        self.assertEqual(product.name, 'Bột mì loại 1')
        self.assertFalse(product.is_active)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE, target_id=str(product.pk)).exists())


class ProductListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (category/tìm kiếm) trên product_list."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.client.force_login(self.staff)
        Product.objects.bulk_create([
            Product(
                product_code=f'NVL-{i:04d}', name=f'Sản phẩm {i}', uom='kg',
                category='Bột' if i % 2 == 0 else 'Đường',
            )
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('catalog:product_list'))
        self.assertEqual(len(response.context['products']), 30)

    def test_page_size_40(self):
        response = self.client.get(reverse('catalog:product_list'), {'page_size': 40})
        self.assertEqual(len(response.context['products']), 35)

    def test_filter_category(self):
        response = self.client.get(
            reverse('catalog:product_list'), {'category': 'Đường', 'page_size': 50})
        self.assertTrue(all(p.category == 'Đường' for p in response.context['products']))

    def test_filter_search_by_product_code(self):
        response = self.client.get(reverse('catalog:product_list'), {'q': 'NVL-0001'})
        codes = [p.product_code for p in response.context['products']]
        self.assertEqual(codes, ['NVL-0001'])
