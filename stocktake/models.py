"""Model app stocktake: StocktakeSession + StocktakeItem — Phase 4, mục 4.

``status`` mirror đúng 4 Phase nghiệp vụ ở BACKLOG mục 4 (PLANNING -> EXECUTION
-> RECONCILIATION -> ADJUSTMENT), dùng luôn tên Phase làm giá trị enum thay vì
đặt tên khác (DRAFT/IN_PROGRESS/...) — cùng quy ước với ``shipping.Gin.Status``
(DRAFT/PICKING/ISSUED/CLOSED dùng thẳng tên bước nghiệp vụ). ``ADJUSTMENT`` là
state cuối (terminal): phiếu đã đối soát xong và Inventory đã được điều chỉnh.

``qty_system`` chụp (snapshot) tại thời điểm tạo phiếu — không tính lại động,
vì phiếu kiểm kê là biên bản đối chiếu tại 1 thời điểm, Inventory có thể tiếp
tục biến động (GRN/GIN khác) trong lúc nhân viên đang đếm hàng thực tế.

``location`` trên session giới hạn cả danh sách SKU lẫn ``qty_system`` (FR-SO-07,
`SHOULD`): ``Inventory`` (mục 1f) chỉ lưu tồn theo product x warehouse, không
có tồn theo từng vị trí, nên khi chọn ``location``, ``qty_system`` không lấy từ
``Inventory.qty_on_hand`` cấp kho nữa (sai — nhân viên chỉ đếm 1 vị trí mà so
sánh với tồn cả kho sẽ ra chênh lệch giả cho phần chưa đếm ở vị trí khác, bug
fix 2026-07-27, xem CLAUDE.md) mà tính lại từ tổng ``Batch.qty_available`` tại
đúng vị trí đó (xem ``stocktake.services.create_session``). Để trống
``location`` (kiểm toàn kho) vẫn dùng ``Inventory.qty_on_hand`` như cũ.

Điều chỉnh tồn kho (FR-SO-05) tái dùng ``StockMovement.MovementType.ADJUSTMENT``
đã có sẵn từ Phase 1 (xem ``inventory.models.StockMovement`` docstring) qua
``inventory.services.record_movement()`` — không tạo model "Adjustment" riêng.
``apply_adjustment`` cũng đồng bộ ``Batch`` theo chiều chênh lệch (bug fix
2026-07-27, xem CLAUDE.md) — xem ``stocktake.services`` docstring.
"""
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone


class StocktakeSession(models.Model):
    class Status(models.TextChoices):
        PLANNING = 'PLANNING', 'Lập kế hoạch'
        EXECUTION = 'EXECUTION', 'Đang kiểm đếm'
        RECONCILIATION = 'RECONCILIATION', 'Đối soát chênh lệch'
        ADJUSTMENT = 'ADJUSTMENT', 'Đã điều chỉnh (hoàn tất)'

    so_no = models.CharField(
        max_length=30, unique=True, editable=False, verbose_name='Số phiếu kiểm kê',
        help_text='Tự sinh: SO-YYYYMM-XXX.')
    warehouse = models.ForeignKey(
        'warehouse.Warehouse', on_delete=models.PROTECT, related_name='stocktake_sessions', verbose_name='Kho')
    location = models.ForeignKey(
        'warehouse.Location', on_delete=models.PROTECT, null=True, blank=True,
        related_name='stocktake_sessions', verbose_name='Vị trí',
        help_text='FR-SO-07: để trống = kiểm toàn kho, chọn = giới hạn theo vị trí.',
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNING, verbose_name='Trạng thái')
    notes = models.TextField(blank=True, verbose_name='Ghi chú')
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, related_name='stocktake_sessions_created',
        verbose_name='Người tạo')
    reconciled_at = models.DateTimeField(null=True, blank=True, verbose_name='Ngày đối soát')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Ngày hoàn tất')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Phiếu kiểm kê'
        verbose_name_plural = 'Phiếu kiểm kê'

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
        """Retry-on-collision (mirror ``StockTransfer.save()`` bên inventory,
        bug fix 2026-07-27, xem CLAUDE.md) — ``generate_so_no()`` chỉ khoá được các
        dòng đã tồn tại, không ngăn được 2 phiếu kiểm kê song song tính ra cùng số
        thứ tự trước khi lần nào INSERT xong."""
        if self.so_no:
            super().save(*args, **kwargs)
            return
        attempts = 5
        for attempt in range(attempts):
            self.so_no = self.generate_so_no()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                return
            except IntegrityError:
                if attempt == attempts - 1:
                    raise
                self.so_no = ''


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

    session = models.ForeignKey(
        StocktakeSession, on_delete=models.CASCADE, related_name='items', verbose_name='Phiếu kiểm kê')
    product = models.ForeignKey(
        'catalog.Product', on_delete=models.PROTECT, related_name='stocktake_items', verbose_name='Sản phẩm')
    qty_system = models.IntegerField(
        verbose_name='Số lượng hệ thống', help_text='Snapshot Inventory.qty_on_hand lúc tạo phiếu.')
    qty_actual = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(0)], verbose_name='Số lượng thực tế',
        help_text='FR-SO-02: Qty đếm thực tế, null = chưa quét/nhập.')
    reason = models.CharField(max_length=20, choices=Reason.choices, blank=True, verbose_name='Lý do chênh lệch')
    counted_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, null=True, blank=True, related_name='stocktake_items_counted',
        verbose_name='Người đếm')
    counted_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời điểm đếm')

    class Meta:
        ordering = ['product__product_code']
        constraints = [
            models.UniqueConstraint(fields=['session', 'product'], name='unique_stocktake_item_per_session'),
        ]
        verbose_name = 'Dòng kiểm kê'
        verbose_name_plural = 'Dòng kiểm kê'

    def __str__(self):
        return f'{self.session.so_no} - {self.product.product_code}'

    @property
    def variance(self):
        """FR-SO-03: chênh lệch = Qty thực tế - Qty hệ thống (âm = thiếu, dương = thừa)."""
        if self.qty_actual is None:
            return None
        return self.qty_actual - self.qty_system
