"""Model app stocktake: StocktakeSession + StocktakeItem — Phase 4, mục 4.

``status`` mirror đúng 4 Phase nghiệp vụ ở BACKLOG mục 4 (PLANNING -> EXECUTION
-> RECONCILIATION -> ADJUSTMENT), dùng luôn tên Phase làm giá trị enum thay vì
đặt tên khác (DRAFT/IN_PROGRESS/...) — cùng quy ước với ``shipping.Gin.Status``
(DRAFT/PICKING/ISSUED/CLOSED dùng thẳng tên bước nghiệp vụ). ``ADJUSTMENT`` là
state cuối (terminal): phiếu đã đối soát xong và Inventory đã được điều chỉnh.

``qty_system`` chụp (snapshot) tại thời điểm tạo phiếu — không tính lại động,
vì phiếu kiểm kê là biên bản đối chiếu tại 1 thời điểm, Inventory có thể tiếp
tục biến động (GRN/GIN khác) trong lúc nhân viên đang đếm hàng thực tế.

``location`` trên session chỉ là bộ lọc SKU để lập danh sách đếm (FR-SO-07,
`SHOULD`) — KHÔNG phải một chiều số lượng riêng: ``Inventory`` (mục 1f) chỉ
lưu tồn theo product x warehouse, không có tồn theo từng vị trí, nên
``qty_system`` vẫn luôn snapshot từ ``Inventory.qty_on_hand`` ở cấp kho, dù
phiếu kiểm kê chỉ giới hạn ở 1 vị trí.

Điều chỉnh tồn kho (FR-SO-05) tái dùng ``StockMovement.MovementType.ADJUSTMENT``
đã có sẵn từ Phase 1 (xem ``inventory.models.StockMovement`` docstring) qua
``inventory.services.record_movement()`` — không tạo model "Adjustment" riêng.
"""
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


class StocktakeSession(models.Model):
    class Status(models.TextChoices):
        PLANNING = 'PLANNING', 'Lập kế hoạch'
        EXECUTION = 'EXECUTION', 'Đang kiểm đếm'
        RECONCILIATION = 'RECONCILIATION', 'Đối soát chênh lệch'
        ADJUSTMENT = 'ADJUSTMENT', 'Đã điều chỉnh (hoàn tất)'

    so_no = models.CharField(
        max_length=30, unique=True, editable=False, help_text='Tự sinh: SO-YYYYMM-XXX.')
    warehouse = models.ForeignKey('warehouse.Warehouse', on_delete=models.PROTECT, related_name='stocktake_sessions')
    location = models.ForeignKey(
        'warehouse.Location', on_delete=models.PROTECT, null=True, blank=True,
        related_name='stocktake_sessions',
        help_text='FR-SO-07: để trống = kiểm toàn kho, chọn = giới hạn theo vị trí.',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNING)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='stocktake_sessions_created')
    reconciled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.so_no

    @classmethod
    def generate_so_no(cls):
        """Mirror BR-GRN-001/BR-GIN: SO-YYYYMM-XXX, XXX = sequence trong tháng hiện tại."""
        prefix = f'SO-{timezone.localdate():%Y%m}-'
        with transaction.atomic():
            last = (
                cls.objects.select_for_update()
                .filter(so_no__startswith=prefix)
                .order_by('-so_no')
                .first()
            )
            seq = int(last.so_no.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:03d}'

    def save(self, *args, **kwargs):
        if not self.so_no:
            self.so_no = self.generate_so_no()
        super().save(*args, **kwargs)


class StocktakeItem(models.Model):
    """1 dòng SKU trong phiếu kiểm kê (FR-SO-01 lập danh sách, FR-SO-02 nhập Qty
    thực tế, FR-SO-03 tính chênh lệch, FR-SO-04 lý do chênh lệch).
    """

    class Reason(models.TextChoices):
        LOSS = 'LOSS', 'Thất thoát'
        DAMAGE = 'DAMAGE', 'Hư hỏng'
        THEFT = 'THEFT', 'Mất cắp'
        COUNTING_ERROR = 'COUNTING_ERROR', 'Sai sót đếm/nhập liệu trước đó'
        EXPIRED = 'EXPIRED', 'Hết hạn sử dụng'

    session = models.ForeignKey(StocktakeSession, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='stocktake_items')
    qty_system = models.IntegerField(help_text='Snapshot Inventory.qty_on_hand lúc tạo phiếu.')
    qty_actual = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(0)],
        help_text='FR-SO-02: Qty đếm thực tế, null = chưa quét/nhập.')
    reason = models.CharField(max_length=20, choices=Reason.choices, blank=True)
    counted_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, null=True, blank=True, related_name='stocktake_items_counted')
    counted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['product__product_code']
        constraints = [
            models.UniqueConstraint(fields=['session', 'product'], name='unique_stocktake_item_per_session'),
        ]

    def __str__(self):
        return f'{self.session.so_no} - {self.product.product_code}'

    @property
    def variance(self):
        """FR-SO-03: chênh lệch = Qty thực tế - Qty hệ thống (âm = thiếu, dương = thừa)."""
        if self.qty_actual is None:
            return None
        return self.qty_actual - self.qty_system
