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
from django.db import IntegrityError, models, transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone


class Inventory(models.Model):
    """Tồn kho theo product x warehouse (BR-WM-001, BR-WM-002).

    Không có UOM/location riêng ở mức này — location cụ thể theo lô thuộc về
    Batch (``location_id``), Inventory chỉ tổng hợp theo kho.
    """

    product = models.ForeignKey(
        'catalog.Product', on_delete=models.PROTECT, related_name='inventories', verbose_name='Sản phẩm')
    warehouse = models.ForeignKey(
        'warehouse.Warehouse', on_delete=models.PROTECT, related_name='inventories', verbose_name='Kho')
    qty_on_hand = models.PositiveIntegerField(
        default=0, verbose_name='Tồn kho thực tế', help_text='BR-WM-001: không cho âm.')
    qty_reserved = models.PositiveIntegerField(default=0, verbose_name='Số lượng đã giữ chỗ')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Cập nhật lần cuối')

    class Meta:
        ordering = ['warehouse__code', 'product__product_code']
        constraints = [
            models.UniqueConstraint(fields=['product', 'warehouse'], name='unique_inventory_product_warehouse'),
            models.CheckConstraint(
                check=Q(qty_reserved__lte=F('qty_on_hand')), name='inventory_reserved_lte_on_hand'),
        ]
        verbose_name = 'Tồn kho'
        verbose_name_plural = 'Tồn kho'

    def __str__(self):
        return f'{self.product.product_code} @ {self.warehouse.code}'

    @property
    def qty_available(self):
        """BR-WM-002: qty_available = qty_on_hand - qty_reserved (computed)."""
        return self.qty_on_hand - self.qty_reserved


class Batch(models.Model):
    """Lô hàng nhận từ GRN (mục 1f) — dùng cho FIFO issue ở Phase 3 (GIN).

    ``status`` dùng enum đầy đủ ngay từ đầu vì Phase 2 (QC PASS/PARTIAL_PASS)
    và Phase 3 (FIFO) đều cần các state này tồn tại sẵn (CLAUDE.md: batch
    ACTIVE hoặc PARTIAL_USED mới được FIFO chọn — lô đã xuất một phần vẫn còn
    ``qty_available`` > 0 nên vẫn phải khả dụng tiếp; QUARANTINE/EXPIRED/
    PENDING_RECEIPT phải bị loại dù qty còn > 0).

    ``PENDING_RECEIPT`` (Phase D): batch phần PASS/PARTIAL_PASS của QC không
    còn chuyển thẳng ACTIVE nữa — dừng ở đây chờ nhân viên kho xác nhận nhận
    hàng qua ``WarehouseHandoff``/``inventory.services.accept_handoff()`` rồi
    mới chuyển ACTIVE (khả dụng FIFO). ``qty_on_hand`` ở kho MAIN đích đã được
    cộng ngay từ lúc QC PASS (phản ánh đúng thực tế vật lý — hàng đã dỡ xuống
    kho) — chỉ status khác ACTIVE mới loại lô này khỏi FIFO/`qty_available`
    cấp lô, không phải giấu khỏi ``Inventory.qty_on_hand``.
    """

    class Status(models.TextChoices):
        PENDING_RECEIPT = 'PENDING_RECEIPT', 'Chờ kho xác nhận'
        ACTIVE = 'ACTIVE', 'Đang hoạt động'
        PARTIAL_USED = 'PARTIAL_USED', 'Đã dùng một phần'
        QUARANTINE = 'QUARANTINE', 'Chờ xử lý (QC fail/partial)'
        EXPIRED = 'EXPIRED', 'Hết hạn'
        CLOSED = 'CLOSED', 'Đã đóng (dùng hết)'

    product = models.ForeignKey(
        'catalog.Product', on_delete=models.PROTECT, related_name='batches', verbose_name='Sản phẩm')
    batch_code = models.CharField(
        max_length=40, unique=True, verbose_name='Mã lô', help_text='Mã lô, vd LOT-0001.')
    supplier = models.ForeignKey(
        'partners.Supplier', on_delete=models.PROTECT, related_name='batches', verbose_name='Nhà cung cấp')
    location = models.ForeignKey(
        'warehouse.Location', on_delete=models.PROTECT, related_name='batches', verbose_name='Vị trí')
    grn_item = models.ForeignKey(
        'receiving.GrnItem', on_delete=models.PROTECT, null=True, blank=True, related_name='batches',
        verbose_name='Dòng GRN nguồn',
        help_text='Nguồn gốc GRN item (lineage) — null cho batch không sinh trực tiếp từ GRN. '
                   'Batch con tách ra qua move_batch_qty copy lại field này từ batch nguồn.',
    )
    mfg_date = models.DateField(null=True, blank=True, verbose_name='Ngày sản xuất')
    exp_date = models.DateField(null=True, blank=True, verbose_name='Hạn sử dụng')
    qty_received = models.PositiveIntegerField(
        validators=[MinValueValidator(1)], verbose_name='Số lượng nhận')
    qty_used = models.PositiveIntegerField(default=0, verbose_name='Số lượng đã dùng')
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, verbose_name='Trạng thái')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        ordering = ['exp_date', 'created_at']
        constraints = [
            models.CheckConstraint(
                check=Q(qty_used__lte=F('qty_received')), name='batch_qty_used_lte_received'),
        ]
        verbose_name = 'Lô hàng'
        verbose_name_plural = 'Lô hàng'

    def __str__(self):
        return self.batch_code

    @property
    def qty_available(self):
        return self.qty_received - self.qty_used


class WarehouseHandoff(models.Model):
    """Bàn giao 1 batch PASS/PARTIAL_PASS (``PENDING_RECEIPT``) cho nhân viên kho
    xác nhận nhận hàng (Phase D) — tách rời "QC PASS" khỏi "khả dụng FIFO ngay".

    Tạo bởi ``quality.services.qc_pass``/``qc_partial_pass`` qua
    ``inventory.services.create_handoff()`` — 1 handoff / batch PENDING_RECEIPT
    tạo ra. ``assigned_to`` chọn cụ thể (tuỳ chọn trên form QC) thì chỉ báo
    người đó; để trống thì báo ``destination_warehouse.staff`` (fallback toàn
    bộ ``department=WAREHOUSE`` nếu kho đích chưa gán ai — cùng convention với
    GRN/GIN handoff notify). Đừng tạo/sửa trực tiếp — dùng
    ``inventory.services.create_handoff()``/``accept_handoff()``/``reject_handoff()``,
    hoặc để hệ thống tự chuyển ``CANCELLED`` khi
    ``stocktake.services._consume_shortage_batches`` trừ hết một batch
    PENDING_RECEIPT còn handoff PENDING (BUG-13, batch không còn tồn vật lý
    thì phiếu bàn giao không còn gì để Nhận/Từ chối).
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Chờ xác nhận'
        ACCEPTED = 'ACCEPTED', 'Đã nhận'
        REJECTED = 'REJECTED', 'Đã từ chối'
        CANCELLED = 'CANCELLED', 'Đã huỷ (kiểm kê)'

    class RejectDestination(models.TextChoices):
        BACK_TO_QC = 'BACK_TO_QC', 'Trả về QC'
        TO_SCRAP = 'TO_SCRAP', 'Chuyển kho phế'

    batch = models.OneToOneField(
        Batch, on_delete=models.PROTECT, related_name='handoff', verbose_name='Lô hàng')
    qc_inspection = models.ForeignKey(
        'quality.QcInspection', on_delete=models.PROTECT, related_name='handoffs',
        verbose_name='Đợt kiểm QC',
    )
    destination_warehouse = models.ForeignKey(
        'warehouse.Warehouse', on_delete=models.PROTECT, related_name='handoffs', verbose_name='Kho đích')
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='warehouse_handoffs', verbose_name='Người nhận chỉ định',
        help_text='Bỏ trống thì báo toàn bộ nhân viên phụ trách kho đích.',
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, verbose_name='Trạng thái')
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='warehouse_handoffs_decided', verbose_name='Người xử lý',
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời gian xử lý')
    reject_reason = models.CharField(max_length=255, blank=True, verbose_name='Lý do từ chối')
    reject_destination = models.CharField(
        max_length=20, choices=RejectDestination.choices, blank=True, verbose_name='Xử lý khi từ chối')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'assigned_to']),
        ]
        verbose_name = 'Phiếu chờ nhận hàng'
        verbose_name_plural = 'Phiếu chờ nhận hàng'

    def __str__(self):
        return f'Bàn giao {self.batch.batch_code} — {self.get_status_display()}'

    def get_absolute_url(self):
        """M9: WarehouseHandoff không có trang detail riêng — quyết định
        Nhận/Từ chối làm ngay trên hàng đợi ``handoff_list`` (Phase D), nên
        deep-link trỏ về đúng trang đó thay vì 1 URL không tồn tại."""
        return reverse('inventory:handoff_list')


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

    product = models.ForeignKey(
        'catalog.Product', on_delete=models.PROTECT, related_name='stock_movements', verbose_name='Sản phẩm')
    warehouse = models.ForeignKey(
        'warehouse.Warehouse', on_delete=models.PROTECT, related_name='stock_movements', verbose_name='Kho')
    batch = models.ForeignKey(
        Batch, on_delete=models.PROTECT, null=True, blank=True, related_name='movements', verbose_name='Lô hàng')
    movement_type = models.CharField(max_length=20, choices=MovementType.choices, verbose_name='Loại giao dịch')
    qty = models.IntegerField(verbose_name='Số lượng', help_text='Dương = nhập, âm = xuất.')
    qty_on_hand_after = models.PositiveIntegerField(verbose_name='Tồn sau giao dịch')
    reference = models.CharField(
        max_length=50, blank=True, verbose_name='Chứng từ tham chiếu',
        help_text='Vd GRN-2607-0001, GIN-2607-0001.')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_movements', verbose_name='Người tạo',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Thời gian')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Lịch sử tồn kho'
        verbose_name_plural = 'Lịch sử tồn kho'

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
        max_length=30, unique=True, editable=False, verbose_name='Số phiếu điều chuyển',
        help_text='Tự sinh: TRF-YYYYMM-XXX.')
    batch = models.ForeignKey(
        Batch, on_delete=models.PROTECT, related_name='transfers_from', verbose_name='Lô hàng')
    new_batch = models.ForeignKey(
        Batch, on_delete=models.PROTECT, null=True, blank=True, related_name='transferred_from',
        verbose_name='Lô mới tại vị trí đích', help_text='Batch mới tách ra tại vị trí đích.',
    )
    from_location = models.ForeignKey(
        'warehouse.Location', on_delete=models.PROTECT, related_name='stock_transfers_out',
        verbose_name='Vị trí nguồn')
    to_location = models.ForeignKey(
        'warehouse.Location', on_delete=models.PROTECT, related_name='stock_transfers_in',
        verbose_name='Vị trí đích')
    qty = models.PositiveIntegerField(validators=[MinValueValidator(1)], verbose_name='Số lượng')
    note = models.CharField(max_length=255, blank=True, verbose_name='Ghi chú')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stock_transfers', verbose_name='Người tạo',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Phiếu điều chuyển'
        verbose_name_plural = 'Phiếu điều chuyển'

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
        """Retry-on-collision (mirror ``PurchaseOrder.save()`` bên purchasing) —
        ``generate_transfer_no()`` chỉ khoá được các dòng đã tồn tại, không ngăn được
        2 phiếu điều chuyển song song tính ra cùng số thứ tự trước khi lần nào INSERT
        xong."""
        if self.transfer_no:
            super().save(*args, **kwargs)
            return
        attempts = 5
        for attempt in range(attempts):
            self.transfer_no = self.generate_transfer_no()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                return
            except IntegrityError:
                if attempt == attempts - 1:
                    raise
                self.transfer_no = ''
