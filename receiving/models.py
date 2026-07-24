"""Model app receiving: GRN (Phiếu Nhập) — Phase 2, mục 2a.

``Grn``/``GrnItem`` theo đúng Data Model ở FSD §3.3/3.4 + BACKLOG mục 2a. Workflow
state (DRAFT -> PENDING_QC -> QC_IN_PROGRESS -> RECEIVED/REJECTED -> CLOSED) và
transaction QC PASS/FAIL/PARTIAL_PASS (tạo Batch, cập nhật Inventory) là logic
nghiệp vụ — CHƯA cài ở đây, chỉ model + auto-numbering (BR-GRN-001..006). Audit
trail state-transition dùng lại hạ tầng đã có ở Phase 1 (``accounts.audit.log_action``),
không tạo bảng audit riêng.

``GrnReturn`` (mục 2c) là phiếu trả hàng tự động sinh khi QC FAIL.
"""
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


class Grn(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Nháp'
        PENDING_QC = 'PENDING_QC', 'Chờ QC'
        QC_IN_PROGRESS = 'QC_IN_PROGRESS', 'Đang QC'
        RECEIVED = 'RECEIVED', 'Đã nhận'
        REJECTED = 'REJECTED', 'Từ chối'
        CLOSED = 'CLOSED', 'Đã đóng'

    grn_no = models.CharField(
        max_length=30, unique=True, editable=False, help_text='Tự sinh: GRN-YYYYMM-XXX.')
    po = models.ForeignKey('purchasing.PurchaseOrder', on_delete=models.PROTECT, related_name='grns')
    supplier = models.ForeignKey('partners.Supplier', on_delete=models.PROTECT, related_name='grns')
    grn_date = models.DateField(default=timezone.localdate)
    expected_arrival_date = models.DateField(null=True, blank=True)
    actual_arrival_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, related_name='grns_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.grn_no

    def clean(self):
        if self.po_id and self.supplier_id and self.po.supplier_id != self.supplier_id:
            raise ValidationError({'supplier': 'Supplier phải khớp với NCC trên PO đã chọn.'})

    @classmethod
    def generate_grn_no(cls):
        """BR-GRN-001: GRN-YYYYMM-XXX, XXX = sequence trong tháng hiện tại."""
        prefix = f'GRN-{timezone.localdate():%Y%m}-'
        with transaction.atomic():
            last = (
                cls.objects.select_for_update()
                .filter(grn_no__startswith=prefix)
                .order_by('-grn_no')
                .first()
            )
            seq = int(last.grn_no.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:03d}'

    def save(self, *args, **kwargs):
        if not self.grn_no:
            self.grn_no = self.generate_grn_no()
        super().save(*args, **kwargs)


class GrnItem(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Chờ nhận'
        RECEIVED = 'RECEIVED', 'Đã nhận'
        PARTIAL_RECEIVED = 'PARTIAL_RECEIVED', 'Nhận một phần'
        REJECTED = 'REJECTED', 'Từ chối'

    grn = models.ForeignKey(Grn, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='grn_items')
    qty_ordered = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    qty_received = models.PositiveIntegerField(default=0)
    qty_pass = models.PositiveIntegerField(
        null=True, blank=True, help_text='Số lượng đạt QC — null nghĩa là chưa QC (mục 2c).')
    mfg_date = models.DateField(null=True, blank=True, verbose_name='Ngày sản xuất')
    exp_date = models.DateField(null=True, blank=True, verbose_name='Hạn sử dụng')
    batch_code = models.CharField(max_length=40, blank=True, help_text='Mã lô từ NCC.')
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    def __str__(self):
        return f'{self.grn.grn_no} - {self.product.product_code} x{self.qty_received}/{self.qty_ordered}'

    @property
    def label_code(self):
        """Nội dung barcode in nhãn dán hàng lúc nhận (FR-GRN-06). Cùng công thức
        fallback với ``Batch.batch_code`` khi QC PASS (xem
        ``quality.services._batch_code``) để nhãn dán lúc nhận khớp với batch_code
        thật sau khi QC tạo batch.
        """
        return (self.batch_code or '').strip() or f'{self.grn.grn_no}-{self.product.product_code}'

    def clean(self):
        # BR-GRN-001/BR-GRN-002: qty_received không được vượt qty_ordered.
        if self.qty_received > self.qty_ordered:
            raise ValidationError({'qty_received': 'qty_received không được lớn hơn qty_ordered.'})
        # BR-GRN-005/BR-GRN-006: exp_date phải sau mfg_date (khi cả 2 đều có).
        if self.mfg_date and self.exp_date and self.exp_date <= self.mfg_date:
            raise ValidationError({'exp_date': 'exp_date phải sau mfg_date.'})
        # mục 2c: qty_pass (kết quả QC) không được vượt qty_received.
        if self.qty_pass is not None and self.qty_pass > self.qty_received:
            raise ValidationError({'qty_pass': 'qty_pass không được lớn hơn qty_received.'})


class GrnReturn(models.Model):
    """Phiếu trả hàng NCC (mục 2c) — tự động tạo khi QC FAIL, KHÔNG đụng Inventory."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Chờ xử lý'
        APPROVED = 'APPROVED', 'Đã duyệt'
        RETURNED = 'RETURNED', 'Đã trả hàng'
        CLOSED = 'CLOSED', 'Đã đóng'

    grn = models.ForeignKey(Grn, on_delete=models.PROTECT, related_name='returns')
    reason = models.TextField(default='QC Fail')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'RETURN-{self.grn.grn_no}'
