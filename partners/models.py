"""Model app partners: Supplier master data (mục 1d — bổ sung, không có mã FR
riêng trong SRS gốc, nhưng GRN/PO/Reporting supplier performance ở các Phase sau
đều cần FK tới đây).

Field mở rộng theo 5 nhóm nghiệp vụ (định danh, pháp lý & địa chỉ, người liên hệ,
vận hành mua hàng, hệ thống & quản trị) để trang chi tiết NCC (``supplier_detail``)
có đủ thông tin thật thay vì chỉ 4 field tối thiểu ban đầu.
"""
from django.db import models


class Supplier(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Hoạt động'
        INACTIVE = 'INACTIVE', 'Ngừng giao dịch'
        SUSPENDED = 'SUSPENDED', 'Tạm khóa'

    class Currency(models.TextChoices):
        VND = 'VND', 'VND'
        USD = 'USD', 'USD'
        EUR = 'EUR', 'EUR'
        OTHER = 'OTHER', 'Khác'

    # --- Nhóm định danh ---
    supplier_code = models.CharField(max_length=30, unique=True, help_text='Mã NCC, vd NCC-0001.')
    name = models.CharField(max_length=200)
    international_name = models.CharField(
        max_length=200, blank=True, help_text='Tên quốc tế/viết tắt dùng để in chứng từ.')
    supplier_group = models.CharField(
        max_length=100, blank=True,
        help_text='Nhóm NCC (vd: vật tư phụ trợ, dịch vụ bảo trì, nhà thầu phụ...).')

    # --- Nhóm pháp lý & địa chỉ ---
    tax_code = models.CharField(max_length=20, blank=True, help_text='Mã số thuế.')
    registered_address = models.CharField(max_length=255, blank=True, help_text='Địa chỉ đăng ký kinh doanh.')
    delivery_address = models.CharField(
        max_length=255, blank=True, help_text='Địa chỉ kho/giao hàng thực tế, nếu khác trụ sở chính.')
    website = models.URLField(blank=True)

    # --- Nhóm người liên hệ ---
    contact_name = models.CharField(max_length=150, blank=True, help_text='Họ tên người liên hệ.')
    contact_title = models.CharField(max_length=100, blank=True, help_text='Chức vụ.')
    contact_phone = models.CharField(max_length=30, blank=True)
    contact_email = models.EmailField(blank=True)

    # --- Nhóm vận hành & mua hàng ---
    lead_time_days = models.PositiveIntegerField(
        null=True, blank=True, help_text='Thời gian giao hàng dự kiến (ngày) — dùng cho FR-PO-05.')
    qty_tolerance_percent = models.PositiveIntegerField(
        default=5,
        help_text='Ngưỡng % chênh lệch Qty nhận thực tế so với Qty đặt trước khi GRN cảnh báo (FR-GRN-07).')
    payment_terms = models.CharField(
        max_length=200, blank=True, help_text='Điều khoản thanh toán (vd: Công nợ 30 ngày).')
    credit_limit = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, help_text='Hạn mức công nợ tối đa.')
    currency = models.CharField(max_length=10, choices=Currency.choices, default=Currency.VND)

    # --- Nhóm hệ thống & quản trị ---
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    internal_note = models.TextField(blank=True, help_text='Ghi chú nội bộ (đánh giá, lưu ý đặc biệt...).')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['supplier_code']

    def __str__(self):
        return f'{self.supplier_code} - {self.name}'
