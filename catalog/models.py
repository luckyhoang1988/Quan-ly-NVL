"""Model app catalog: Product/SKU master data (mục 1c — bổ sung, không có mã FR
riêng trong SRS gốc, nhưng GRN/PO/Inventory ở các Phase sau đều cần FK tới đây).
"""
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Product(models.Model):
    class SamplingMethod(models.TextChoices):
        PERCENT = 'PERCENT', '% trên qty nhận'
        FIXED = 'FIXED', 'Số lượng cố định'

    product_code = models.CharField(
        max_length=30, unique=True, verbose_name='Mã SKU', help_text='Mã SKU, vd NVL-0001.')
    name = models.CharField(max_length=200, verbose_name='Tên sản phẩm')
    category = models.CharField(max_length=100, blank=True, verbose_name='Danh mục')
    uom = models.CharField(max_length=20, verbose_name='Đơn vị tính', help_text='vd kg, cái, thùng.')
    min_level = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Ngưỡng tồn tối thiểu', help_text='Ngưỡng tồn kho tối thiểu.')
    max_level = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Ngưỡng tồn tối đa', help_text='Ngưỡng tồn kho tối đa.')
    ordering_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name='Chi phí đặt hàng',
        help_text='Chi phí đặt hàng mỗi lần (S, VNĐ) — dùng tính EOQ (FR-INV-05).')
    holding_cost_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, verbose_name='Tỷ lệ chi phí lưu kho (%)',
        help_text='% chi phí lưu kho/năm trên đơn giá bình quân (H) — dùng tính EOQ (FR-INV-05).',
        validators=[MinValueValidator(0), MaxValueValidator(100)])
    qc_sampling_method = models.CharField(
        max_length=10, choices=SamplingMethod.choices, default=SamplingMethod.PERCENT,
        verbose_name='Cách lấy mẫu QC', help_text='Cách tính cỡ mẫu QC gợi ý (mục 2b QC Criteria & Sampling).')
    qc_sampling_value = models.PositiveIntegerField(
        default=10, verbose_name='Giá trị lấy mẫu QC',
        help_text='PERCENT: % trên qty_received (vd 10 = 10%). FIXED: số lượng cố định.')
    is_active = models.BooleanField(default=True, verbose_name='Đang hoạt động')
    preferred_supplier = models.ForeignKey(
        'partners.Supplier', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='preferred_for_products', verbose_name='Nhà cung cấp ưu tiên',
        help_text='NCC ưu tiên cho SKU này — gợi ý tự động khi tạo PO từ yêu cầu mua hàng.')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        ordering = ['product_code']
        verbose_name = 'Sản phẩm'
        verbose_name_plural = 'Sản phẩm'

    def __str__(self):
        return f'{self.product_code} - {self.name}'

    def clean(self):
        if self.min_level is not None and self.max_level is not None and self.min_level > self.max_level:
            raise ValidationError({'max_level': 'max_level phải >= min_level.'})
