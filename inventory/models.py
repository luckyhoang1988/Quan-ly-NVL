"""Model app inventory: Inventory + Batch (mục 1f — bổ sung, chỉ tạo schema).

GRN ở Phase 2 cần 2 model này tồn tại để ghi ``qty_on_hand`` / tạo Batch khi QC
pass. Logic nghiệp vụ đầy đủ (FIFO issue, cảnh báo Min/Max Level, EOQ) dời qua
Phase 3 (mục 3a) — ở đây CHƯA có view/form CRUD, CHƯA có transition logic, chỉ
có model + admin để GRN/QC (Phase 2) và unit test BR-WM-001/002 có chỗ bám vào.

``qty_available`` LUÔN là property tính toán (derived), không lưu cột riêng —
theo quy ước cross-cutting trong CLAUDE.md: "qty_available = qty_on_hand -
qty_reserved là computed, không phải stored input".
"""
from django.core.validators import MinValueValidator
from django.db import models


class Inventory(models.Model):
    """Tồn kho theo product x warehouse (BR-WM-001, BR-WM-002).

    Không có UOM/location riêng ở mức này — location cụ thể theo lô thuộc về
    Batch (``location_id``), Inventory chỉ tổng hợp theo kho.
    """

    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='inventories')
    warehouse = models.ForeignKey('warehouse.Warehouse', on_delete=models.PROTECT, related_name='inventories')
    qty_on_hand = models.PositiveIntegerField(default=0, help_text='BR-WM-001: không cho âm.')
    qty_reserved = models.PositiveIntegerField(default=0)
    qty_quarantine = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['warehouse__code', 'product__product_code']
        constraints = [
            models.UniqueConstraint(fields=['product', 'warehouse'], name='unique_inventory_product_warehouse'),
        ]
        verbose_name_plural = 'Inventories'

    def __str__(self):
        return f'{self.product.product_code} @ {self.warehouse.code}'

    @property
    def qty_available(self):
        """BR-WM-002: qty_available = qty_on_hand - qty_reserved (computed)."""
        return self.qty_on_hand - self.qty_reserved


class Batch(models.Model):
    """Lô hàng nhận từ GRN (mục 1f) — dùng cho FIFO issue ở Phase 3 (GIN).

    ``status`` dùng enum đầy đủ ngay từ đầu vì Phase 2 (QC PASS/PARTIAL_PASS)
    và Phase 3 (FIFO) đều cần các state này tồn tại sẵn (CLAUDE.md: chỉ batch
    ACTIVE mới được FIFO chọn; QUARANTINE/EXPIRED phải bị loại dù qty còn > 0).
    """

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Đang hoạt động'
        PARTIAL_USED = 'PARTIAL_USED', 'Đã dùng một phần'
        QUARANTINE = 'QUARANTINE', 'Chờ xử lý (QC fail/partial)'
        EXPIRED = 'EXPIRED', 'Hết hạn'
        CLOSED = 'CLOSED', 'Đã đóng (dùng hết)'

    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='batches')
    batch_code = models.CharField(max_length=40, unique=True, help_text='Mã lô, vd LOT-0001.')
    supplier = models.ForeignKey('partners.Supplier', on_delete=models.PROTECT, related_name='batches')
    location = models.ForeignKey('warehouse.Location', on_delete=models.PROTECT, related_name='batches')
    mfg_date = models.DateField(null=True, blank=True, verbose_name='Ngày sản xuất')
    exp_date = models.DateField(null=True, blank=True, verbose_name='Hạn sử dụng')
    qty_received = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    qty_used = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['exp_date', 'created_at']
        verbose_name_plural = 'Batches'

    def __str__(self):
        return self.batch_code

    @property
    def qty_available(self):
        return self.qty_received - self.qty_used
