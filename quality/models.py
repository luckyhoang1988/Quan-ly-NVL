"""Model app quality: QC (Quality Control) — Phase 2, mục 2b.

``QcInspection``/``QcInspectionItem`` theo Data Model ở FSD §6.4 + BACKLOG mục
2b. ``QcCriteria`` là master data (FR-QC-02, định nghĩa tiêu chuẩn QC theo từng
category sản phẩm) — không FK cứng từ ``QcInspectionItem`` vì FSD ghi nhận
``criteria_name``/``expected_value`` như snapshot tại thời điểm kiểm (giống
``GrnItem.qty_ordered`` snapshot từ PO), tránh vỡ lịch sử khi criteria đổi sau.

Transaction QC PASS/FAIL/PARTIAL_PASS (đổi status GRN, tạo Batch/GrnReturn, cập
nhật Inventory) là logic nghiệp vụ — CHƯA cài ở đây, chỉ model.
"""
from django.db import models, transaction
from django.utils import timezone


class QcCriteria(models.Model):
    """Tiêu chuẩn QC master data (FR-QC-02), định nghĩa theo category sản phẩm."""

    category = models.CharField(max_length=100, help_text='Vd: Bột mì, Đường...')
    name = models.CharField(max_length=100, help_text='Vd: Ngoại hình, Trọng lượng, Seal integrity.')
    pass_rule = models.CharField(max_length=255, blank=True)
    fail_rule = models.CharField(max_length=255, blank=True)
    reference_image = models.ImageField(
        upload_to='qc_criteria_ref/%Y/%m/', blank=True, null=True,
        help_text='Ảnh mẫu minh hoạ tiêu chuẩn (vd: màu sắc đạt, seal integrity đạt).',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

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

    qc_no = models.CharField(max_length=30, unique=True, editable=False, help_text='Tự sinh: QC-YYYYMM-XXX.')
    grn = models.ForeignKey('receiving.Grn', on_delete=models.PROTECT, related_name='qc_inspections')
    inspector = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='qc_inspections')
    status = models.CharField(max_length=20, choices=Result.choices, default=Result.PENDING_QC)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # QC approval override (BACKLOG mục 2b) — Supervisor (Manager/Admin) ghi
    # chú lý do xem lại 1 kết quả đã quyết định. Phạm vi đã chốt với user:
    # CHỈ annotation, KHÔNG đảo ngược Batch/Inventory đã tạo bởi
    # qc_pass/qc_fail/qc_partial_pass.
    override_note = models.TextField(blank=True)
    overridden_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, null=True, blank=True, related_name='qc_overrides',
    )
    overridden_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

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

    inspection = models.ForeignKey(QcInspection, on_delete=models.CASCADE, related_name='items')
    grn_item = models.ForeignKey('receiving.GrnItem', on_delete=models.PROTECT, related_name='qc_items')
    criteria_name = models.CharField(max_length=100)
    expected_value = models.CharField(max_length=100, blank=True)
    actual_value = models.CharField(max_length=100, blank=True)
    result = models.CharField(max_length=10, choices=Result.choices)
    notes = models.TextField(blank=True)
    image = models.ImageField(
        upload_to='qc_evidence/%Y/%m/', blank=True, null=True,
        help_text='Ảnh evidence thực tế lúc kiểm (FR-QC-06).',
    )

    def __str__(self):
        return f'{self.inspection.qc_no} - {self.criteria_name}: {self.result}'
