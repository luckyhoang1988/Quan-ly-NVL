"""Model app shipping: GIN (Phiếu Xuất) — Phase 3, mục 3b.

``Gin``/``GinItem``/``GinBatchAllocation`` theo Data Model + Workflow States ở
BACKLOG mục 3b. Workflow state (DRAFT -> PICKING -> ISSUED -> CLOSED) và
transaction xuất kho (FIFO suggest, override batch, cập nhật Inventory/Batch)
là logic nghiệp vụ — CHƯA cài ở đây, chỉ model + auto-numbering (mirror BR-GRN-001
của ``receiving.Grn``). Audit trail state-transition dùng lại hạ tầng đã có ở
Phase 1 (``accounts.audit.log_action``), không tạo bảng audit riêng.

``GinBatchAllocation`` tách riêng khỏi ``GinItem`` vì FIFO có thể chia 1 dòng
yêu cầu (1 product, 1 qty_requested) thành nhiều batch (giống
``inventory.services.suggest_fifo_batches`` trả về list nhiều batch) — mỗi
batch được phân bổ là 1 dòng, giữ cả cờ ``is_override``/``override_reason``
cho FR-GIN-03 và dữ liệu in phiếu (FR-GIN-06) sau này.
"""
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from warehouse.models import Warehouse


class Gin(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Nháp'
        PICKING = 'PICKING', 'Đang soạn hàng'
        ISSUED = 'ISSUED', 'Đã xuất'
        CLOSED = 'CLOSED', 'Đã đóng'

    class ReferenceType(models.TextChoices):
        PO = 'PO', 'Đơn mua hàng'
        PRODUCTION = 'PRODUCTION', 'Sản xuất'
        SALES = 'SALES', 'Bán hàng'

    gin_no = models.CharField(
        max_length=30, unique=True, editable=False, help_text='Tự sinh: GIN-YYYYMM-XXX.')
    warehouse = models.ForeignKey('warehouse.Warehouse', on_delete=models.PROTECT, related_name='gins')
    reference_type = models.CharField(max_length=20, choices=ReferenceType.choices)
    reference_no = models.CharField(
        max_length=50, blank=True, help_text='Vd PO-0001 (khi PO), hoặc mã lệnh SX/đơn bán ngoài hệ thống.')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, related_name='gins_requested')
    issued_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.gin_no

    def clean(self):
        super().clean()
        if self.warehouse_id and self.warehouse.warehouse_type != Warehouse.WarehouseType.MAIN:
            raise ValidationError('Kho xuất GIN phải thuộc loại "Kho thành phẩm" — không thể xuất từ Kho chờ/Kho phế.')

    @classmethod
    def generate_gin_no(cls):
        """BR-GIN (mirror BR-GRN-001): GIN-YYYYMM-XXX, XXX = sequence trong tháng hiện tại."""
        prefix = f'GIN-{timezone.localdate():%Y%m}-'
        with transaction.atomic():
            last = (
                cls.objects.select_for_update()
                .filter(gin_no__startswith=prefix)
                .order_by('-gin_no')
                .first()
            )
            seq = int(last.gin_no.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:03d}'

    def save(self, *args, **kwargs):
        if not self.gin_no:
            self.gin_no = self.generate_gin_no()
        super().save(*args, **kwargs)


class GinItem(models.Model):
    gin = models.ForeignKey(Gin, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='gin_items')
    qty_requested = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    qty_issued = models.PositiveIntegerField(default=0, help_text='FR-GIN-05: qty thực xuất, có thể khác qty_requested.')

    def __str__(self):
        return f'{self.gin.gin_no} - {self.product.product_code} x{self.qty_issued}/{self.qty_requested}'


class GinBatchAllocation(models.Model):
    """1 batch được phân bổ cho 1 ``GinItem`` (FR-GIN-02 gợi ý FIFO / FR-GIN-03
    override). Một ``GinItem`` có thể có nhiều dòng nếu FIFO phải tách nhiều batch.
    """

    gin_item = models.ForeignKey(GinItem, on_delete=models.CASCADE, related_name='allocations')
    batch = models.ForeignKey('inventory.Batch', on_delete=models.PROTECT, related_name='gin_allocations')
    qty_allocated = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    is_override = models.BooleanField(
        default=False, help_text='True nếu người dùng đổi batch khác gợi ý FIFO (FR-GIN-03).')
    override_reason = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f'{self.gin_item} - {self.batch.batch_code} x{self.qty_allocated}'
