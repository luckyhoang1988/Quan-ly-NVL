"""Model app purchasing: Purchase Order đầy đủ (Phase 5, FR-PO-01..06) — nâng cấp
từ PO stub Phase 1 (mục 1e, giải quyết circular dependency GRN <-> PO: GRN ở
Phase 2 cần po_id FK hợp lệ trước khi PO workflow đầy đủ tồn tại).

Workflow: DRAFT -> APPROVED (Manager/Admin duyệt) -> SENT (gửi NCC, khoá sửa) ->
PARTIAL_RECEIVED/RECEIVED (tự động theo Qty GRN thực nhận, xem
``services.sync_po_status``) -> CLOSED (archive).

``PurchaseRequest``/``PurchaseRequestItem`` (bổ sung ngoài FR, không có mã FR
riêng) là "Yêu cầu mua hàng" nhân viên kho gửi lên trước khi có PO — tách biệt
với PO thật: PENDING -> APPROVED/REJECTED, và 1 PR đã duyệt convert thành đúng
1 PO (``linked_po``) qua ``purchasing.views.po_create(?from_pr=<pk>)``. Không
tách nhiều NCC cho từng dòng, không auto-approve, không Celery/email.
"""
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Nháp'
        APPROVED = 'APPROVED', 'Đã duyệt'
        SENT = 'SENT', 'Đã gửi NCC'
        PARTIAL_RECEIVED = 'PARTIAL_RECEIVED', 'Nhận một phần'
        RECEIVED = 'RECEIVED', 'Đã nhận đủ'
        CLOSED = 'CLOSED', 'Đã đóng'

    po_no = models.CharField(max_length=30, unique=True, verbose_name='Mã PO', help_text='Mã PO, vd PO-0001.')
    supplier = models.ForeignKey(
        'partners.Supplier', on_delete=models.PROTECT, related_name='purchase_orders', verbose_name='Nhà cung cấp')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, verbose_name='Trạng thái')
    expected_delivery_date = models.DateField(
        null=True, blank=True, verbose_name='Ngày giao hàng dự kiến',
        help_text='Ngày giao hàng dự kiến — dùng để theo dõi On time/Delayed (FR-PO-06).')
    received_at = models.DateField(
        null=True, blank=True, verbose_name='Ngày nhận đủ',
        help_text='Ngày PO chuyển sang RECEIVED (set tự động 1 lần, dùng tính lead-time thực tế FR-PO-05).')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Đơn mua hàng'
        verbose_name_plural = 'Đơn mua hàng'

    def __str__(self):
        return self.po_no

    def delivery_status(self):
        """FR-PO-06: phân loại giao hàng On time/Delayed/Partial, tính on-the-fly
        (không lưu cột riêng) từ ``status``/``expected_delivery_date``/``received_at``.
        Trả ``None`` khi chưa đủ dữ liệu để đánh giá (chưa gửi NCC, hoặc chưa có
        ngày giao dự kiến).
        """
        if not self.expected_delivery_date:
            return None
        if self.status in (self.Status.DRAFT, self.Status.APPROVED):
            return None
        if self.status == self.Status.PARTIAL_RECEIVED:
            return {'code': 'PARTIAL', 'label': 'Nhận một phần', 'css': 'warning'}
        if self.status == self.Status.SENT:
            if timezone.localdate() > self.expected_delivery_date:
                return {'code': 'DELAYED', 'label': 'Trễ hạn (chưa nhận)', 'css': 'danger'}
            return None
        if self.status in (self.Status.RECEIVED, self.Status.CLOSED):
            if self.received_at and self.received_at > self.expected_delivery_date:
                return {'code': 'DELAYED', 'label': 'Trễ hạn', 'css': 'danger'}
            if self.received_at:
                return {'code': 'ON_TIME', 'label': 'Đúng hạn', 'css': 'success'}
        return None


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name='items', verbose_name='Đơn mua hàng')
    product = models.ForeignKey(
        'catalog.Product', on_delete=models.PROTECT, related_name='po_items', verbose_name='Sản phẩm')
    qty_ordered = models.PositiveIntegerField(validators=[MinValueValidator(1)], verbose_name='Số lượng đặt')
    unit_price = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(0)], verbose_name='Đơn giá')

    class Meta:
        verbose_name = 'Dòng đơn mua hàng'
        verbose_name_plural = 'Dòng đơn mua hàng'

    def __str__(self):
        return f'{self.purchase_order.po_no} - {self.product.product_code} x{self.qty_ordered}'


class PurchaseRequest(models.Model):
    """Yêu cầu mua hàng (PR) — nhân viên kho đề nghị mua, Purchasing/Manager
    duyệt rồi tạo PO thật từ đây (xem ``purchasing.views.po_create``).
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Chờ duyệt'
        APPROVED = 'APPROVED', 'Đã duyệt'
        REJECTED = 'REJECTED', 'Từ chối'

    request_no = models.CharField(
        max_length=30, unique=True, editable=False, verbose_name='Số yêu cầu',
        help_text='Tự sinh: PR-YYYYMM-XXX.')
    requested_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, related_name='purchase_requests', verbose_name='Người yêu cầu')
    warehouse = models.ForeignKey(
        'warehouse.Warehouse', on_delete=models.PROTECT, related_name='purchase_requests', verbose_name='Kho',
        help_text='Kho đang thiếu hàng (chỉ kho loại MAIN).')
    note = models.TextField(blank=True, verbose_name='Ghi chú')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, verbose_name='Trạng thái')
    decided_by = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.PROTECT, related_name='+',
        verbose_name='Người duyệt')
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name='Ngày duyệt')
    reject_reason = models.CharField(max_length=255, blank=True, verbose_name='Lý do từ chối')
    linked_po = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name='source_requests',
        verbose_name='PO liên kết')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Yêu cầu mua hàng'
        verbose_name_plural = 'Yêu cầu mua hàng'

    def __str__(self):
        return self.request_no

    @classmethod
    def generate_request_no(cls):
        """PR-YYYYMM-XXX, XXX = sequence trong tháng hiện tại (mirror ``Grn.generate_grn_no``)."""
        prefix = f'PR-{timezone.localdate():%Y%m}-'
        with transaction.atomic():
            last = (
                cls.objects.select_for_update()
                .filter(request_no__startswith=prefix)
                .order_by('-request_no')
                .first()
            )
            seq = int(last.request_no.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:03d}'

    def save(self, *args, **kwargs):
        if not self.request_no:
            self.request_no = self.generate_request_no()
        super().save(*args, **kwargs)


class PurchaseRequestItem(models.Model):
    purchase_request = models.ForeignKey(
        PurchaseRequest, on_delete=models.CASCADE, related_name='items', verbose_name='Yêu cầu mua hàng')
    product = models.ForeignKey(
        'catalog.Product', on_delete=models.PROTECT, related_name='pr_items', verbose_name='Sản phẩm')
    qty_requested = models.PositiveIntegerField(validators=[MinValueValidator(1)], verbose_name='Số lượng yêu cầu')

    class Meta:
        verbose_name = 'Dòng yêu cầu mua hàng'
        verbose_name_plural = 'Dòng yêu cầu mua hàng'

    def __str__(self):
        return f'{self.purchase_request.request_no} - {self.product.product_code} x{self.qty_requested}'
