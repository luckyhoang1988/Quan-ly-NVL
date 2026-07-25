from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import AuditLog

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
