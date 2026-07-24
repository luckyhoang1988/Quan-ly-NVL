"""Model app purchasing: Purchase Order đầy đủ (Phase 5, FR-PO-01..06) — nâng cấp
từ PO stub Phase 1 (mục 1e, giải quyết circular dependency GRN <-> PO: GRN ở
Phase 2 cần po_id FK hợp lệ trước khi PO workflow đầy đủ tồn tại).

Workflow: DRAFT -> APPROVED (Manager/Admin duyệt) -> SENT (gửi NCC, khoá sửa) ->
PARTIAL_RECEIVED/RECEIVED (tự động theo Qty GRN thực nhận, xem
``services.sync_po_status``) -> CLOSED (archive).
"""
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Nháp'
        APPROVED = 'APPROVED', 'Đã duyệt'
        SENT = 'SENT', 'Đã gửi NCC'
        PARTIAL_RECEIVED = 'PARTIAL_RECEIVED', 'Nhận một phần'
        RECEIVED = 'RECEIVED', 'Đã nhận đủ'
        CLOSED = 'CLOSED', 'Đã đóng'

    po_no = models.CharField(max_length=30, unique=True, help_text='Mã PO, vd PO-0001.')
    supplier = models.ForeignKey(
        'partners.Supplier', on_delete=models.PROTECT, related_name='purchase_orders')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    expected_delivery_date = models.DateField(
        null=True, blank=True, help_text='Ngày giao hàng dự kiến — dùng để theo dõi On time/Delayed (FR-PO-06).')
    received_at = models.DateField(
        null=True, blank=True,
        help_text='Ngày PO chuyển sang RECEIVED (set tự động 1 lần, dùng tính lead-time thực tế FR-PO-05).')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

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
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='po_items')
    qty_ordered = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])

    def __str__(self):
        return f'{self.purchase_order.po_no} - {self.product.product_code} x{self.qty_ordered}'
