from django.conf import settings
from django.db import models

# BR-WM-005: tạo warehouse -> tối thiểu 10 vị trí lưu trữ. Thay vì bắt buộc admin
# tự thêm đủ 10 dòng trước khi dùng được, view `warehouse_create` tự sinh sẵn
# ``MIN_LOCATIONS_PER_WAREHOUSE`` vị trí mặc định (đổi tên/khoá sau tuỳ ý).
MIN_LOCATIONS_PER_WAREHOUSE = 10

#: A2 (Wave A hardening): occupied/capacity >= ngưỡng này -> "gần đầy" (soft-warn, không chặn).
CAPACITY_WARN_RATIO = 0.9

#: A3 (Wave A hardening): batch STAGING ACTIVE có created_at quá số ngày này -> tính "tồn đọng".
STAGING_AGING_DAYS = 3


class Warehouse(models.Model):
    """Kho vật lý (FR-WM-01).

    ``is_active`` là cờ hoạt động duy nhất — "xoá" kho (CRUD Delete) nghĩa là
    khoá hoạt động (soft), không xoá cứng bản ghi, để giữ tham chiếu cho
    Inventory/GRN/GIN ở các Phase sau.

    ``warehouse_type``: MAIN là kho thường (duy nhất loại được GIN/FIFO chọn
    xuất hàng); STAGING (Kho chờ QC) và SCRAP (Kho phế) là kho hệ thống, tối
    đa 1 kho đang hoạt động cho mỗi loại (``unique_active_staging_scrap_warehouse``)
    — xem ``warehouse.services.get_staging_warehouse``/``get_scrap_warehouse``.
    """

    class WarehouseType(models.TextChoices):
        MAIN = 'MAIN', 'Kho thành phẩm'
        STAGING = 'STAGING', 'Kho chờ'
        SCRAP = 'SCRAP', 'Kho phế'

    code = models.CharField(max_length=20, unique=True, verbose_name='Mã kho', help_text='Mã kho, vd KHO-HN.')
    name = models.CharField(max_length=150, verbose_name='Tên kho')
    address = models.CharField(max_length=255, blank=True, verbose_name='Địa chỉ')
    capacity = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Dung tích',
        help_text='Dung tích kho (đơn vị tuỳ chọn, vd m3/pallet).',
    )
    warehouse_type = models.CharField(
        max_length=20, choices=WarehouseType.choices, default=WarehouseType.MAIN,
        verbose_name='Loại kho',
        help_text='MAIN: kho thường (GIN/FIFO được phép xuất). STAGING/SCRAP: kho hệ '
                   'thống, tối đa 1 kho hoạt động/loại.',
    )
    is_active = models.BooleanField(default=True, verbose_name='Đang hoạt động')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')
    staff = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='assigned_warehouses',
        verbose_name='Nhân viên phụ trách',
        help_text='Nhân viên kho được gán vào kho này — dùng để chọn/mặc định người nhận '
                   'khi QC bàn giao lô hàng đã PASS về kho.',
    )

    class Meta:
        ordering = ['code']
        constraints = [
            models.UniqueConstraint(
                fields=['warehouse_type'],
                condition=models.Q(warehouse_type__in=['STAGING', 'SCRAP'], is_active=True),
                name='unique_active_staging_scrap_warehouse',
            ),
        ]
        verbose_name = 'Kho'
        verbose_name_plural = 'Kho'

    def __str__(self):
        return f'{self.code} - {self.name}'


class Location(models.Model):
    """Vị trí lưu trữ trong kho (FR-WM-02), vd "Giá-A-01".

    ``on_delete=PROTECT``: không cho xoá cứng Warehouse khi còn Location tham
    chiếu — hàng rào kỹ thuật bổ sung cho BR-WM-006, dù luồng UI chỉ cung cấp
    deactivate (soft), không có xoá cứng.
    """

    warehouse = models.ForeignKey(
        Warehouse, on_delete=models.PROTECT, related_name='locations', verbose_name='Kho')
    code = models.CharField(max_length=30, verbose_name='Mã vị trí', help_text='Mã vị trí, vd Giá-A-01.')
    capacity = models.PositiveIntegerField(null=True, blank=True, verbose_name='Dung tích')
    is_active = models.BooleanField(default=True, verbose_name='Đang hoạt động')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        ordering = ['warehouse__code', 'code']
        constraints = [
            models.UniqueConstraint(fields=['warehouse', 'code'], name='unique_location_code_per_warehouse'),
        ]
        verbose_name = 'Vị trí lưu trữ'
        verbose_name_plural = 'Vị trí lưu trữ'

    def __str__(self):
        return f'{self.warehouse.code} / {self.code}'
