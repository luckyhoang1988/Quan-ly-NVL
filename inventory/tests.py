import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from catalog.models import Product
from partners.models import Supplier
from warehouse.models import Location, Warehouse

from .models import Batch, Inventory


class InventoryModelTest(TestCase):
    """Inventory (mục 1f — bổ sung, schema only, chưa có mã FR riêng).

    Bao test cho BR-WM-001 (qty_on_hand >= 0), BR-WM-002 (qty_available auto
    calculate) và Phase 1 DoD dòng "qty_available tính đúng (unit test)".

    Đặt tên test theo quy ước ``TC-INV-001-<seq>``.
    """

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')

    def test_TC_INV_001_001_qty_available_computed_from_on_hand_and_reserved(self):
        inv = Inventory.objects.create(
            product=self.product, warehouse=self.warehouse, qty_on_hand=100, qty_reserved=30)
        self.assertEqual(inv.qty_available, 70)

    def test_TC_INV_001_002_negative_qty_on_hand_rejected_by_validation(self):
        inv = Inventory(product=self.product, warehouse=self.warehouse, qty_on_hand=-5)
        with self.assertRaises(ValidationError):
            inv.full_clean()

    def test_TC_INV_001_003_unique_per_product_and_warehouse(self):
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=10)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=5)


class BatchModelTest(TestCase):
    """Batch (mục 1f — bổ sung, schema only). ``TC-INV-002-<seq>``."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')

    def test_TC_INV_002_001_qty_available_computed_from_received_and_used(self):
        batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=100, qty_used=40,
        )
        self.assertEqual(batch.qty_available, 60)

    def test_TC_INV_002_002_default_status_is_active(self):
        batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=50,
        )
        self.assertEqual(batch.status, Batch.Status.ACTIVE)

    def test_TC_INV_002_003_batch_code_unique(self):
        Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=50,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Batch.objects.create(
                    product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
                    qty_received=10,
                )

    def test_TC_INV_002_004_exp_date_optional(self):
        batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier, location=self.location,
            qty_received=50, mfg_date=datetime.date(2026, 1, 1),
        )
        self.assertIsNone(batch.exp_date)
