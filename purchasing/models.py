"""Model app purchasing: Purchase Order đầy đủ (Phase 5, FR-PO-01..06) — nâng cấp
từ PO stub Phase 1 (mục 1e, giải quyết circular dependency GRN <-> PO: GRN ở
Phase 2 cần po_id FK hợp lệ trước khi PO workflow đầy đủ tồn tại).

Workflow: DRAFT -> APPROVED (Manager/Admin duyệt) -> SENT (gửi NCC, khoá sửa) ->
PARTIAL_RECEIVED/RECEIVED (tự động theo Qty GRN thực nhận, xem
``services.sync_po_status``) -> CLOSED (archive).

``PurchaseRequest``/``PurchaseRequestItem`` (bổ sung ngoài FR, không có mã FR
riêng) là "Yêu cầu mua hàng" nhân viên các phòng ban gửi lên trước khi có PO —
tách biệt với PO thật: DRAFT (sửa được, chưa có Approval nào) -> PENDING (đã
Nộp, ``purchasing.views.pr_submit``) -> APPROVED/REJECTED; REJECTED mở lại được
về DRAFT (``purchasing.services.reopen_purchase_request``) để sửa và nộp lại,
không phải ngõ cụt (mirror đúng pattern DRAFT/submit/approve của GRN/GIN — xem
CLAUDE.md "Department-manager approval axis"). 1 PR đã duyệt convert thành đúng
1 PO (``linked_po``) qua ``purchasing.views.po_create(?from_pr=<pk>)``. Không
tách nhiều NCC cho từng dòng, không auto-approve, không Celery/email.

Duyệt PR đi qua cơ chế ``accounts.approvals`` dùng chung với GRN submit/GIN
confirm (xem CLAUDE.md "Department-manager approval axis"): người tạo có thể
chọn 1 ``assigned_to`` (nhân viên phòng Mua hàng cụ thể, tuỳ chọn — để trống
thì báo cả phòng) để biết ai sẽ xử lý, nhưng chỉ quản lý phòng Mua hàng
(``is_department_manager('PURCHASING')``) hoặc Manager/Admin mới thật sự
DUYỆT/từ chối — ``assigned_to`` chỉ mang tính thông báo/hiển thị, không tự có
quyền approve (xem ``purchasing.services.submit_purchase_request``/
``decide_purchase_request``).
"""
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils import timezone


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Nháp'
        APPROVED = 'APPROVED', 'Đã duyệt'
        SENT = 'SENT', 'Đã gửi NCC'
        PARTIAL_RECEIVED = 'PARTIAL_RECEIVED', 'Nhận một phần'
        RECEIVED = 'RECEIVED', 'Đã nhận đủ'
        CLOSED = 'CLOSED', 'Đã đóng'

    class Source(models.TextChoices):
        MANUAL = 'MANUAL', 'Tự tạo'
        FROM_PR = 'FROM_PR', 'Từ yêu cầu mua hàng'

    po_no = models.CharField(
        max_length=30, unique=True, editable=False, verbose_name='Mã PO',
        help_text='Tự sinh: PO-XXXX (tăng dần toàn hệ thống, không nhập tay — tránh trùng mã).')
    supplier = models.ForeignKey(
        'partners.Supplier', on_delete=models.PROTECT, related_name='purchase_orders', verbose_name='Nhà cung cấp')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, verbose_name='Trạng thái')
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.MANUAL, verbose_name='Nguồn tạo',
        help_text='Tự tạo trực tiếp hay chuyển đổi từ 1 Yêu cầu mua hàng đã duyệt (set tự động, không sửa tay).')
    expected_delivery_date = models.DateField(
        null=True, blank=True, verbose_name='Ngày giao hàng dự kiến',
        help_text='Ngày giao hàng dự kiến — dùng để theo dõi On time/Delayed (FR-PO-06).')
    received_at = models.DateField(
        null=True, blank=True, verbose_name='Ngày nhận đủ',
        help_text='Ngày PO chuyển sang RECEIVED (set tự động 1 lần, dùng tính lead-time thực tế FR-PO-05).')
    created_by = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='purchase_orders_created', verbose_name='Người tạo',
        help_text='Set tự động = người bấm Tạo PO — dùng để giới hạn nhân viên phòng Mua hàng '
                   'thường chỉ xem PO do chính mình tạo (xem purchasing.views._po_can_view_all).')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')
    close_reason = models.TextField(
        blank=True, verbose_name='Lý do đóng sớm',
        help_text='Bắt buộc khi đóng PO từ SENT/PARTIAL_RECEIVED (NCC chưa giao đủ hàng); '
                   'không bắt buộc khi đóng từ RECEIVED (đã nhận đủ, chỉ archive).')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Đơn mua hàng'
        verbose_name_plural = 'Đơn mua hàng'

    def __str__(self):
        return self.po_no

    @classmethod
    def generate_po_no(cls):
        """Sinh mã PO tự động PO-XXXX, tăng dần TOÀN hệ thống (không theo tháng
        như PR — PO không lặp lại theo chu kỳ). Duyệt qua từng ``po_no`` thay vì
        ``order_by('-po_no')`` để tránh so sánh chuỗi sai khi số chữ số khác nhau
        (vd 'PO-99999' > 'PO-100000' theo thứ tự chuỗi dù nhỏ hơn theo số); chỉ
        tính các mã có phần đuôi thuần số (bỏ qua mã test/import kiểu 'PO-TEST-0001').
        """
        prefix = 'PO-'
        with transaction.atomic():
            max_seq = 0
            po_nos = (
                cls.objects.select_for_update()
                .filter(po_no__startswith=prefix)
                .values_list('po_no', flat=True)
            )
            for po_no in po_nos:
                suffix = po_no[len(prefix):]
                if suffix.isdigit():
                    max_seq = max(max_seq, int(suffix))
            seq = max_seq + 1
        return f'{prefix}{seq:04d}'

    def save(self, *args, **kwargs):
        """``generate_po_no()`` chỉ khoá (``select_for_update``) các dòng hiện có — không
        thể khoá một số thứ tự chưa tồn tại, nên 2 lần tạo PO song song vẫn có thể tính
        ra cùng ``seq`` trước khi lần nào INSERT xong. Bọc INSERT trong savepoint riêng
        (``transaction.atomic()`` lồng) và thử lại với số mới nếu trùng ``unique`` —
        đây là lớp phòng vệ thật sự chống trùng mã, không phải ``select_for_update``.
        """
        if self.po_no:
            super().save(*args, **kwargs)
            return
        attempts = 5
        for attempt in range(attempts):
            self.po_no = self.generate_po_no()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                return
            except IntegrityError:
                if attempt == attempts - 1:
                    raise
                self.po_no = ''

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
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name='items', verbose_name='Đơn mua hàng')
    product = models.ForeignKey(
        'catalog.Product', on_delete=models.PROTECT, related_name='po_items', verbose_name='Sản phẩm')
    qty_ordered = models.PositiveIntegerField(validators=[MinValueValidator(1)], verbose_name='Số lượng đặt')
    unit_price = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(0)], verbose_name='Đơn giá')

    class Meta:
        verbose_name = 'Dòng đơn mua hàng'
        verbose_name_plural = 'Dòng đơn mua hàng'

    def __str__(self):
        return f'{self.purchase_order.po_no} - {self.product.product_code} x{self.qty_ordered}'


class PurchaseRequest(models.Model):
    """Yêu cầu mua hàng (PR) — nhân viên kho đề nghị mua, Purchasing/Manager
    duyệt rồi tạo PO thật từ đây (xem ``purchasing.views.po_create``).
    """

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Nháp'
        PENDING = 'PENDING', 'Chờ duyệt'
        APPROVED = 'APPROVED', 'Đã duyệt'
        REJECTED = 'REJECTED', 'Từ chối'

    class Origin(models.TextChoices):
        MANUAL = 'MANUAL', 'Tự đề xuất'
        MIN_LEVEL = 'MIN_LEVEL', 'Gợi ý dưới Min Level'

    request_no = models.CharField(
        max_length=30, unique=True, editable=False, verbose_name='Số yêu cầu',
        help_text='Tự sinh: PR-YYYYMM-XXX.')
    origin = models.CharField(
        max_length=20, choices=Origin.choices, default=Origin.MANUAL, verbose_name='Nguồn tạo',
        help_text='Tự đề xuất hay tạo từ gợi ý tồn kho dưới Min Level (set tự động, không sửa tay).')
    requested_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, related_name='purchase_requests', verbose_name='Người yêu cầu')
    assigned_to = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='assigned_purchase_requests', verbose_name='Nhân viên mua hàng phụ trách',
        help_text='Chọn 1 nhân viên phòng Mua hàng cụ thể để xử lý yêu cầu này — để trống thì '
                   'thông báo cho cả phòng Mua hàng.')
    warehouse = models.ForeignKey(
        'warehouse.Warehouse', on_delete=models.PROTECT, related_name='purchase_requests', verbose_name='Kho',
        help_text='Kho đang thiếu hàng (chỉ kho loại MAIN).')
    note = models.TextField(blank=True, verbose_name='Ghi chú')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, verbose_name='Trạng thái')
    decided_by = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.PROTECT, related_name='+',
        verbose_name='Người duyệt')
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name='Ngày duyệt')
    reject_reason = models.CharField(max_length=255, blank=True, verbose_name='Lý do từ chối')
    linked_po = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name='source_requests',
        verbose_name='PO liên kết')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Yêu cầu mua hàng'
        verbose_name_plural = 'Yêu cầu mua hàng'

    def __str__(self):
        return self.request_no

    def get_absolute_url(self):
        """M9: đích deep-link cho ``Notification``/``AuditLog`` gắn với PR này."""
        return reverse('purchasing:pr_detail', args=[self.pk])

    @classmethod
    def generate_request_no(cls):
        """PR-YYYYMM-XXX, XXX = sequence trong tháng hiện tại (mirror ``Grn.generate_grn_no``)."""
        prefix = f'PR-{timezone.localdate():%Y%m}-'
        with transaction.atomic():
            last = (
                cls.objects.select_for_update()
                .filter(request_no__startswith=prefix)
                .order_by('-request_no')
                .first()
            )
            seq = int(last.request_no.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:03d}'

    def save(self, *args, **kwargs):
        """Retry-on-collision giống ``PurchaseOrder.save()`` — ``generate_request_no()``
        chỉ khoá được các dòng đã tồn tại, không ngăn được 2 request song song tính ra
        cùng số thứ tự."""
        if self.request_no:
            super().save(*args, **kwargs)
            return
        attempts = 5
        for attempt in range(attempts):
            self.request_no = self.generate_request_no()
            try:
                with transaction.atomic():
                    super().save(*args, **kwargs)
                return
            except IntegrityError:
                if attempt == attempts - 1:
                    raise
                self.request_no = ''


class PurchaseRequestItem(models.Model):
    purchase_request = models.ForeignKey(
        PurchaseRequest, on_delete=models.CASCADE, related_name='items', verbose_name='Yêu cầu mua hàng')
    product = models.ForeignKey(
        'catalog.Product', on_delete=models.PROTECT, related_name='pr_items', verbose_name='Sản phẩm')
    qty_requested = models.PositiveIntegerField(validators=[MinValueValidator(1)], verbose_name='Số lượng yêu cầu')

    class Meta:
        verbose_name = 'Dòng yêu cầu mua hàng'
        verbose_name_plural = 'Dòng yêu cầu mua hàng'

    def __str__(self):
        return f'{self.purchase_request.request_no} - {self.product.product_code} x{self.qty_requested}'
