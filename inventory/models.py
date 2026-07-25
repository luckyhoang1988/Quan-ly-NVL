"""Model app inventory: Inventory + Batch (mục 1f — bổ sung, chỉ tạo schema).

GRN ở Phase 2 cần 2 model này tồn tại để ghi ``qty_on_hand`` / tạo Batch khi QC
pass. Logic nghiệp vụ đầy đủ (FIFO issue, cảnh báo Min/Max Level, EOQ) dời qua
Phase 3 (mục 3a) — ở đây CHƯA có view/form CRUD, CHƯA có transition logic, chỉ
có model + admin để GRN/QC (Phase 2) và unit test BR-WM-001/002 có chỗ bám vào.

``qty_available`` LUÔN là property tính toán (derived), không lưu cột riêng —
theo quy ước cross-cutting trong CLAUDE.md: "qty_available = qty_on_hand -
qty_reserved là computed, không phải stored input".
"""
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


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
    grn_item = models.ForeignKey(
        'receiving.GrnItem', on_delete=models.PROTECT, null=True, blank=True, related_name='batches',
        help_text='Nguồn gốc GRN item (lineage) — null cho batch không sinh trực tiếp từ GRN. '
                   'Batch con tách ra qua move_batch_qty copy lại field này từ batch nguồn.',
    )
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


class StockMovement(models.Model):
    """Lịch sử chuyển động tồn kho (FR-INV-03) — ledger append-only.

    Ghi lại mọi thay đổi ``Inventory.qty_on_hand`` (nhập từ GRN/QC, xuất từ
    GIN, điều chỉnh kiểm kê ở Phase 4...) kèm số dư sau giao dịch, khác với
    ``AuditLog`` (ghi state-transition who/what/when, không ghi số lượng).
    Đừng tạo trực tiếp — dùng ``inventory.services.record_movement()``.
    """

    class MovementType(models.TextChoices):
        RECEIPT = 'RECEIPT', 'Nhập kho'
        ISSUE = 'ISSUE', 'Xuất kho'
        ADJUSTMENT = 'ADJUSTMENT', 'Điều chỉnh'
        TRANSFER_OUT = 'TRANSFER_OUT', 'Điều chuyển (xuất)'
        TRANSFER_IN = 'TRANSFER_IN', 'Điều chuyển (nhập)'

    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='stock_movements')
    warehouse = models.ForeignKey('warehouse.Warehouse', on_delete=models.PROTECT, related_name='stock_movements')
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, null=True, blank=True, related_name='movements')
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    qty = models.IntegerField(help_text='Dương = nhập, âm = xuất.')
    qty_on_hand_after = models.PositiveIntegerField()
    reference = models.CharField(max_length=50, blank=True, help_text='Vd GRN-2607-0001, GIN-2607-0001.')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_movements',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_movement_type_display()} {self.qty} — {self.product.product_code}'


class StockTransfer(models.Model):
    """Phiếu điều chuyển tồn kho (FR-WM-06).

    Batch bất biến về vị trí một khi đã tạo (cùng convention với cách
    ``qc_partial_pass`` tách batch thay vì sửa tại chỗ): mọi điều chuyển —
    dù cùng kho (chỉ đổi vị trí) hay khác kho — đều tách ``qty`` từ ``batch``
    nguồn sang ``new_batch`` mới (ACTIVE) tại ``to_location``. Nếu khác kho,
    ``Inventory`` 2 đầu được cập nhật qua ``StockMovement`` TRANSFER_OUT/
    TRANSFER_IN; cùng kho thì Inventory không đổi (chỉ là đổi vị trí nội bộ).
    Đừng tạo trực tiếp — dùng ``inventory.services.transfer_stock()``.
    """

    transfer_no = models.CharField(
        max_length=30, unique=True, editable=False, help_text='Tự sinh: TRF-YYYYMM-XXX.')
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name='transfers_from')
    new_batch = models.ForeignKey(
        Batch, on_delete=models.PROTECT, null=True, blank=True, related_name='transferred_from',
        help_text='Batch mới tách ra tại vị trí đích.',
    )
    from_location = models.ForeignKey(
        'warehouse.Location', on_delete=models.PROTECT, related_name='stock_transfers_out')
    to_location = models.ForeignKey(
        'warehouse.Location', on_delete=models.PROTECT, related_name='stock_transfers_in')
    qty = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_transfers',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.transfer_no

    @classmethod
    def generate_transfer_no(cls):
        """TRF-YYYYMM-XXX, XXX = sequence trong tháng hiện tại (mirror BR-GRN-001)."""
        prefix = f'TRF-{timezone.localdate():%Y%m}-'
        with transaction.atomic():
            last = (
                cls.objects.select_for_update()
                .filter(transfer_no__startswith=prefix)
                .order_by('-transfer_no')
                .first()
            )
            seq = int(last.transfer_no.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:03d}'

    def save(self, *args, **kwargs):
        if not self.transfer_no:
            self.transfer_no = self.generate_transfer_no()
        super().save(*args, **kwargs)
