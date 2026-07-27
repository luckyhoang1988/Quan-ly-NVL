"""Model app quality: QC (Quality Control) — Phase 2, mục 2b.

``QcInspection``/``QcInspectionItem`` theo Data Model ở FSD §6.4 + BACKLOG mục
2b. ``QcCriteria`` là master data (FR-QC-02, định nghĩa tiêu chuẩn QC theo từng
category sản phẩm) — không FK cứng từ ``QcInspectionItem`` vì FSD ghi nhận
``criteria_name``/``expected_value`` như snapshot tại thời điểm kiểm (giống
``GrnItem.qty_ordered`` snapshot từ PO), tránh vỡ lịch sử khi criteria đổi sau.

Transaction QC PASS/FAIL/PARTIAL_PASS (đổi status GRN, tạo Batch/GrnReturn, cập
nhật Inventory) là logic nghiệp vụ — CHƯA cài ở đây, chỉ model.
"""
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

MAX_IMAGE_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.gif')


def validate_image_upload(file):
    """Giới hạn dung lượng + phần mở rộng cho ảnh QC upload (evidence/tham chiếu).

    ``ImageField`` mặc định của Django chỉ xác nhận file mở được bằng Pillow
    (đúng là ảnh), không giới hạn size hay whitelist phần mở rộng tường minh —
    thiếu 2 thứ này thì 1 ảnh hợp lệ nhưng cực lớn vẫn được chấp nhận.
    """
    if file.size > MAX_IMAGE_UPLOAD_SIZE:
        raise ValidationError('Dung lượng ảnh không được vượt quá 5MB.')
    if not file.name.lower().endswith(ALLOWED_IMAGE_EXTENSIONS):
        raise ValidationError('Chỉ chấp nhận ảnh định dạng JPG, PNG, WEBP hoặc GIF.')


class QcCriteria(models.Model):
    """Tiêu chuẩn QC master data (FR-QC-02), định nghĩa theo category sản phẩm."""

    category = models.CharField(max_length=100, verbose_name='Danh mục', help_text='Vd: Bột mì, Đường...')
    name = models.CharField(
        max_length=100, verbose_name='Tên tiêu chuẩn', help_text='Vd: Ngoại hình, Trọng lượng, Seal integrity.')
    pass_rule = models.CharField(max_length=255, blank=True, verbose_name='Điều kiện đạt')
    fail_rule = models.CharField(max_length=255, blank=True, verbose_name='Điều kiện không đạt')
    reference_image = models.ImageField(
        upload_to='qc_criteria_ref/%Y/%m/', blank=True, null=True, verbose_name='Ảnh tham chiếu',
        help_text='Ảnh mẫu minh hoạ tiêu chuẩn (vd: màu sắc đạt, seal integrity đạt). Tối đa 5MB, JPG/PNG/WEBP/GIF.',
        validators=[validate_image_upload],
    )
    is_active = models.BooleanField(default=True, verbose_name='Đang hoạt động')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        ordering = ['category', 'name']
        verbose_name_plural = 'QC criteria'
        constraints = [
            models.UniqueConstraint(fields=['category', 'name'], name='unique_qc_criteria_category_name'),
        ]

    def __str__(self):
        return f'{self.category} - {self.name}'


class QcInspection(models.Model):
    class Result(models.TextChoices):
        PENDING_QC = 'PENDING_QC', 'Chờ QC'
        PASS = 'PASS', 'Đạt'
        FAIL = 'FAIL', 'Không đạt'
        PARTIAL_PASS = 'PARTIAL_PASS', 'Đạt một phần'
        CANCELLED = 'CANCELLED', 'Đã hủy'

    qc_no = models.CharField(
        max_length=30, unique=True, editable=False, verbose_name='Số phiếu QC',
        help_text='Tự sinh: QC-YYYYMM-XXX.')
    grn = models.ForeignKey(
        'receiving.Grn', on_delete=models.PROTECT, related_name='qc_inspections', verbose_name='Phiếu GRN')
    inspector = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, related_name='qc_inspections', verbose_name='Người kiểm tra')
    status = models.CharField(
        max_length=20, choices=Result.choices, default=Result.PENDING_QC, verbose_name='Kết quả')
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='Bắt đầu lúc')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Hoàn tất lúc')
    notes = models.TextField(blank=True, verbose_name='Ghi chú')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    # QC approval override (BACKLOG mục 2b) — Supervisor (Manager/Admin) ghi
    # chú lý do xem lại 1 kết quả đã quyết định. Phạm vi đã chốt với user:
    # CHỈ annotation, KHÔNG đảo ngược Batch/Inventory đã tạo bởi
    # qc_pass/qc_fail/qc_partial_pass.
    override_note = models.TextField(blank=True, verbose_name='Ghi chú override')
    overridden_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, null=True, blank=True, related_name='qc_overrides',
        verbose_name='Người override',
    )
    overridden_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời điểm override')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # Khớp đúng WHERE clause của overdue_inspections() (SLA quét
            # PENDING_QC quá hạn) — bảng chỉ tăng dần, không có index này thì
            # mỗi lần grn_list load là 1 full table scan.
            models.Index(fields=['status', 'started_at']),
        ]
        verbose_name = 'Phiếu kiểm tra QC'
        verbose_name_plural = 'Phiếu kiểm tra QC'

    def __str__(self):
        return self.qc_no

    @classmethod
    def generate_qc_no(cls):
        prefix = f'QC-{timezone.localdate():%Y%m}-'
        with transaction.atomic():
            last = (
                cls.objects.select_for_update()
                .filter(qc_no__startswith=prefix)
                .order_by('-qc_no')
                .first()
            )
            seq = int(last.qc_no.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:03d}'

    def save(self, *args, **kwargs):
        if not self.qc_no:
            self.qc_no = self.generate_qc_no()
        super().save(*args, **kwargs)


class QcInspectionItem(models.Model):
    class Result(models.TextChoices):
        PASS = 'PASS', 'Đạt'
        FAIL = 'FAIL', 'Không đạt'

    inspection = models.ForeignKey(
        QcInspection, on_delete=models.CASCADE, related_name='items', verbose_name='Phiếu QC')
    grn_item = models.ForeignKey(
        'receiving.GrnItem', on_delete=models.PROTECT, related_name='qc_items', verbose_name='Dòng GRN')
    criteria_name = models.CharField(max_length=100, verbose_name='Tiêu chuẩn')
    expected_value = models.CharField(max_length=100, blank=True, verbose_name='Giá trị chuẩn')
    actual_value = models.CharField(max_length=100, blank=True, verbose_name='Giá trị thực tế')
    result = models.CharField(max_length=10, choices=Result.choices, verbose_name='Kết quả')
    notes = models.TextField(blank=True, verbose_name='Ghi chú')
    image = models.ImageField(
        upload_to='qc_evidence/%Y/%m/', blank=True, null=True, verbose_name='Ảnh evidence',
        help_text='Ảnh evidence thực tế lúc kiểm (FR-QC-06). Tối đa 5MB, JPG/PNG/WEBP/GIF.',
        validators=[validate_image_upload],
    )

    class Meta:
        verbose_name = 'Dòng kiểm tra QC'
        verbose_name_plural = 'Dòng kiểm tra QC'

    def __str__(self):
        return f'{self.inspection.qc_no} - {self.criteria_name}: {self.result}'
