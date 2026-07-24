"""Model app catalog: Product/SKU master data (mục 1c — bổ sung, không có mã FR
riêng trong SRS gốc, nhưng GRN/PO/Inventory ở các Phase sau đều cần FK tới đây).
"""
from django.core.exceptions import ValidationError
from django.db import models


class Product(models.Model):
    product_code = models.CharField(max_length=30, unique=True, help_text='Mã SKU, vd NVL-0001.')
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100, blank=True)
    uom = models.CharField(max_length=20, verbose_name='Đơn vị tính', help_text='vd kg, cái, thùng.')
    min_level = models.PositiveIntegerField(null=True, blank=True, help_text='Ngưỡng tồn kho tối thiểu.')
    max_level = models.PositiveIntegerField(null=True, blank=True, help_text='Ngưỡng tồn kho tối đa.')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['product_code']

    def __str__(self):
        return f'{self.product_code} - {self.name}'

    def clean(self):
        if self.min_level is not None and self.max_level is not None and self.min_level > self.max_level:
            raise ValidationError({'max_level': 'max_level phải >= min_level.'})
