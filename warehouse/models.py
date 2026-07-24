from django.db import models

# BR-WM-005: tạo warehouse -> tối thiểu 10 vị trí lưu trữ. Thay vì bắt buộc admin
# tự thêm đủ 10 dòng trước khi dùng được, view `warehouse_create` tự sinh sẵn
# ``MIN_LOCATIONS_PER_WAREHOUSE`` vị trí mặc định (đổi tên/khoá sau tuỳ ý).
MIN_LOCATIONS_PER_WAREHOUSE = 10


class Warehouse(models.Model):
    """Kho vật lý (FR-WM-01).

    ``is_active`` là cờ hoạt động duy nhất — "xoá" kho (CRUD Delete) nghĩa là
    khoá hoạt động (soft), không xoá cứng bản ghi, để giữ tham chiếu cho
    Inventory/GRN/GIN ở các Phase sau.
    """

    code = models.CharField(max_length=20, unique=True, help_text='Mã kho, vd KHO-HN.')
    name = models.CharField(max_length=150)
    address = models.CharField(max_length=255, blank=True)
    capacity = models.PositiveIntegerField(
        null=True, blank=True, help_text='Dung tích kho (đơn vị tuỳ chọn, vd m3/pallet).',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} - {self.name}'


class Location(models.Model):
    """Vị trí lưu trữ trong kho (FR-WM-02), vd "Giá-A-01".

    ``on_delete=PROTECT``: không cho xoá cứng Warehouse khi còn Location tham
    chiếu — hàng rào kỹ thuật bổ sung cho BR-WM-006, dù luồng UI chỉ cung cấp
    deactivate (soft), không có xoá cứng.
    """

    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='locations')
    code = models.CharField(max_length=30, help_text='Mã vị trí, vd Giá-A-01.')
    capacity = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['warehouse__code', 'code']
        constraints = [
            models.UniqueConstraint(fields=['warehouse', 'code'], name='unique_location_code_per_warehouse'),
        ]

    def __str__(self):
        return f'{self.warehouse.code} / {self.code}'
