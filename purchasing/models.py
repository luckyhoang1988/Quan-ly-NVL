"""Model app purchasing: PO stub (mục 1e — bổ sung, giải quyết circular dependency
GRN <-> PO: GRN ở Phase 2 cần po_id FK hợp lệ, nhưng PO đầy đủ (FR-PO-*, workflow
duyệt, so sánh giá NCC) chỉ làm ở Phase 5.

``Status`` dùng chung enum với PO đầy đủ để nâng cấp tại chỗ, không đổi giá trị cũ
(BACKLOG dòng 309: "Nâng cấp PO stub đã tạo ở Phase 1 thành workflow đầy đủ"); ở
Phase 1 chỉ mặc định SENT, chưa có nút chuyển trạng thái.
"""
from django.core.validators import MinValueValidator
from django.db import models


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
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.po_no


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT, related_name='po_items')
    qty_ordered = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])

    def __str__(self):
        return f'{self.purchase_order.po_no} - {self.product.product_code} x{self.qty_ordered}'
