"""Model app partners: Supplier master data (mục 1d — bổ sung, không có mã FR
riêng trong SRS gốc, nhưng GRN/PO/Reporting supplier performance ở các Phase sau
đều cần FK tới đây).
"""
from django.db import models


class Supplier(models.Model):
    supplier_code = models.CharField(max_length=30, unique=True, help_text='Mã NCC, vd NCC-0001.')
    name = models.CharField(max_length=200)
    contact = models.CharField(max_length=200, blank=True, help_text='SĐT/email/người liên hệ.')
    lead_time_days = models.PositiveIntegerField(
        null=True, blank=True, help_text='Thời gian giao hàng dự kiến (ngày) — dùng cho FR-PO-05.')
    qty_tolerance_percent = models.PositiveIntegerField(
        default=5,
        help_text='Ngưỡng % chênh lệch Qty nhận thực tế so với Qty đặt trước khi GRN cảnh báo (FR-GRN-07).')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['supplier_code']

    def __str__(self):
        return f'{self.supplier_code} - {self.name}'
