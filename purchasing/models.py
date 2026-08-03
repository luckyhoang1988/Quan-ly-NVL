"""Model app purchasing: Purchase Order đầy đủ (Phase 5, FR-PO-01..06) — nâng cấp
từ PO stub Phase 1 (mục 1e, giải quyết circular dependency GRN <-> PO: GRN ở
Phase 2 cần po_id FK hợp lệ trước khi PO workflow đầy đủ tồn tại).

Workflow: DRAFT -> APPROVED (Manager/Admin duyệt) -> SENT (gửi NCC, khoá sửa) ->
PARTIAL_RECEIVED/RECEIVED (tự động theo Qty GRN thực nhận, xem
``services.sync_po_status``) -> CLOSED (archive).

``PurchaseRequest``/``PurchaseRequestItem`` (bổ sung ngoài FR, không có mã FR
riêng) là "Yêu cầu mua hàng" nhân viên các phòng ban gửi lên trước khi có PO —
tách biệt với PO thật: DRAFT (sửa được, chưa có Approval nào) -> PENDING_DEPT
(đã Nộp, ``purchasing.views.pr_submit`` — chờ quản lý PHÒNG BAN GỐC của người
tạo duyệt) -> PENDING_PUR (bộ phận gốc đã duyệt, chờ quản lý phòng Mua hàng
duyệt tiếp) -> APPROVED/REJECTED. Nếu người tạo thuộc chính phòng Mua hàng
(hoặc không có department) thì bỏ qua PENDING_DEPT, vào thẳng PENDING_PUR
(tránh 1 người tự duyệt 2 lần). REJECTED (ở bất kỳ cấp nào) mở lại được về
DRAFT (``purchasing.services.reopen_purchase_request``) để sửa và nộp lại,
không phải ngõ cụt (mirror đúng pattern DRAFT/submit/approve của GRN/GIN — xem
CLAUDE.md "Department-manager approval axis"). 1 PR đã duyệt convert thành đúng
1 PO (``linked_po``) qua ``purchasing.views.po_create(?from_pr=<pk>)``. Không
tách nhiều NCC cho từng dòng, không auto-approve, không Celery/email.

Duyệt PR đi qua cơ chế ``accounts.approvals`` dùng chung với GRN submit/GIN
confirm (xem CLAUDE.md "Department-manager approval axis") — mỗi cấp tạo 1
``Approval`` riêng (2 bản ghi tuần tự cho 1 PR nếu đi đủ 2 cấp; xem
``accounts.approvals.approval_history_for``), mỗi bản tự lưu
``department``/``submitted_by``/``decided_by``/``decided_at``/``decision_note``
riêng — đây là nguồn lịch sử "ai duyệt ở cấp nào", không lưu lặp lại lên PR.
Người tạo có thể chọn 1 ``assigned_to`` (nhân viên phòng Mua hàng cụ thể, tuỳ
chọn — để trống thì báo cả phòng) để biết ai sẽ xử lý, nhưng ``assigned_to``
không tự có quyền approve và không thấy được PR khi còn đang chờ duyệt ở bất kỳ
cấp nào (chỉ thấy khi đã APPROVED — xem ``purchasing.views._pr_can_view``);
chỉ quản lý đúng phòng ban đang giữ cấp hiện tại (``can_decide_pr``) hoặc
Manager/Admin mới thật sự DUYỆT/từ chối (xem
``purchasing.services.submit_purchase_request``/``decide_purchase_request``).
"""
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.models import User


class Currency(models.TextChoices):
    VND = 'VND', 'VND'
    USD = 'USD', 'USD'
    EUR = 'EUR', 'EUR'
    JPY = 'JPY', 'JPY'
    CNY = 'CNY', 'CNY'


class ExchangeRate(models.Model):
    currency = models.CharField(max_length=3, choices=Currency.choices, verbose_name='Loại tiền')
    rate_date = models.DateField(verbose_name='Ngày áp dụng')
    rate_to_vnd = models.DecimalField(
        max_digits=14, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))],
        verbose_name='Tỷ giá quy đổi VND', help_text='1 đơn vị ngoại tệ = ? VND.')
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, verbose_name='Người nhập')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày nhập')

    class Meta:
        verbose_name = 'Tỷ giá ngoại tệ'
        verbose_name_plural = 'Tỷ giá ngoại tệ'
        ordering = ['-rate_date', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['currency', 'rate_date'], name='unique_currency_rate_date'),
            models.CheckConstraint(condition=~Q(currency='VND'), name='exchange_rate_currency_not_vnd'),
        ]

    def __str__(self):
        return f'{self.currency} @ {self.rate_date}: {self.rate_to_vnd}'


class ProcurementAllocation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Đang hiệu lực'
        RELEASED = 'RELEASED', 'Đã giải phóng'

    pr_item = models.ForeignKey(
        'PurchaseRequestItem', on_delete=models.PROTECT, related_name='allocations',
        verbose_name='Dòng yêu cầu mua hàng')
    po_item = models.ForeignKey(
        'PurchaseOrderItem', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='allocations', verbose_name='Dòng đơn mua hàng')
    po_no_snapshot = models.CharField(max_length=30, blank=True, editable=False, verbose_name='Số PO (lưu vết)')
    product_code_snapshot = models.CharField(max_length=50, blank=True, editable=False, verbose_name='Mã sản phẩm (lưu vết)')
    qty_allocated = models.PositiveIntegerField(validators=[MinValueValidator(1)], verbose_name='Số lượng phân bổ')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, verbose_name='Trạng thái')
    released_reason = models.TextField(blank=True, verbose_name='Lý do giải phóng')
    released_by = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        verbose_name='Người giải phóng')
    released_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời điểm giải phóng')
    created_by = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        verbose_name='Người tạo phân bổ')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        verbose_name = 'Phân bổ PR-PO'
        verbose_name_plural = 'Phân bổ PR-PO'
        constraints = [
            models.CheckConstraint(
                condition=Q(status='RELEASED') | Q(po_item__isnull=False),
                name='active_allocation_requires_po_item'),
            models.CheckConstraint(
                condition=(
                    Q(status='ACTIVE', released_at__isnull=True, released_by__isnull=True, released_reason='')
                    | (Q(status='RELEASED', released_at__isnull=False) & ~Q(released_reason=''))
                ),
                name='allocation_release_fields_match_status'),
            models.CheckConstraint(condition=Q(qty_allocated__gte=1), name='allocation_qty_positive'),
            models.CheckConstraint(
                condition=~Q(po_no_snapshot='') & ~Q(product_code_snapshot=''),
                name='allocation_snapshots_required'),
        ]

    def __str__(self):
        return f'{self.pr_item} -> {self.po_no_snapshot} ({self.qty_allocated})'


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

    class EmailStatus(models.TextChoices):
        """PUR-FND-02: kết quả gửi email PO cho NCC — 5 giá trị phân biệt rõ
        "chưa gửi" / "gửi nhưng NCC không có email" / "dữ liệu cũ không rõ kết
        quả" thay vì suy luận nhị phân. ``_send_po_email()`` chỉ trả về đúng 1
        trong 3 giá trị SENT/FAILED/SKIPPED_NO_EMAIL tại runtime —
        NOT_ATTEMPTED là default trước khi ``send_po()`` chạy, UNKNOWN_LEGACY
        chỉ set qua data migration cho PO đã SENT trở lên trước khi field này
        tồn tại (xem migration 0016)."""
        NOT_ATTEMPTED = 'NOT_ATTEMPTED', 'Chưa gửi'
        SKIPPED_NO_EMAIL = 'SKIPPED_NO_EMAIL', 'NCC chưa có email'
        SENT = 'SENT', 'Đã gửi'
        FAILED = 'FAILED', 'Gửi thất bại'
        UNKNOWN_LEGACY = 'UNKNOWN_LEGACY', 'Không rõ (dữ liệu trước nâng cấp)'

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
    sent_at = models.DateTimeField(
        null=True, blank=True, verbose_name='Thời điểm gửi NCC',
        help_text='Set 1 lần trong send_po() khi APPROVED -> SENT, bất kể email gửi được hay '
                   'không — là thời điểm hệ thống phát hành PO, không phải lúc NCC xác nhận đã '
                   'nhận. PO tạo trước khi có field này để trống (không suy đoán bằng created_at).')
    email_status = models.CharField(
        max_length=20, choices=EmailStatus.choices, default=EmailStatus.NOT_ATTEMPTED,
        verbose_name='Trạng thái gửi email',
        help_text='Kết quả lần gửi email PO gần nhất cho NCC (send_po()/retry_po_email()).')

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

    @property
    def grand_total(self):
        """PUR-FND-05: tổng tiền PO = Σ(qty_ordered × unit_price) các dòng hiện
        tại — property tính toán, không lưu DB (không có cột nào để sửa trực
        tiếp; tránh lệch nếu 1 dòng bị sửa mà quên đồng bộ lại tổng, cùng
        invariant "derived, never stored" như ``qty_available``/``qty_on_hand``
        ở các module khác — xem CLAUDE.md).

        Chỉ dùng ở nơi đã ``prefetch_related('items')`` (trang chi tiết 1 PO,
        N=1) — dùng ở trang liệt kê N PO sẽ gây N+1 query nếu chưa prefetch.
        ``Decimal('0.00')`` làm giá trị khởi tạo ``sum()`` để PO không có dòng
        nào (hiếm, formset ``min_num=1``) vẫn trả về ``Decimal`` thay vì ``int 0``.
        """
        return sum(
            (item.qty_ordered * item.unit_price for item in self.items.all()),
            Decimal('0.00'),
        )

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
        constraints = [
            models.UniqueConstraint(fields=['purchase_order', 'product'], name='unique_po_product'),
        ]

    def __str__(self):
        return f'{self.purchase_order.po_no} - {self.product.product_code} x{self.qty_ordered}'


class PurchaseRequest(models.Model):
    """Yêu cầu mua hàng (PR) — nhân viên phòng ban đề nghị mua, duyệt qua 2 cấp
    tuần tự (quản lý phòng ban gốc rồi mới tới quản lý phòng Mua hàng — xem
    ``purchasing.services.submit_purchase_request``/``decide_purchase_request``),
    rồi tạo PO thật từ đây (xem ``purchasing.views.po_create``).
    """

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Nháp'
        PENDING_DEPT = 'PENDING_DEPT', 'Chờ duyệt bộ phận'
        PENDING_PUR = 'PENDING_PUR', 'Chờ duyệt Mua hàng'
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
    cost_center = models.CharField(
        max_length=50, default='', verbose_name='Trung tâm chi phí',
        help_text='Bắt buộc — khoá ngân sách theo quyết định #2 (cost_center + budget_category dòng PR).')
    department_snapshot = models.CharField(
        max_length=20, choices=User.Department.choices, blank=True, default='', editable=False,
        verbose_name='Phòng ban (snapshot lúc nộp)',
        help_text='Set tự động trong submit_purchase_request() — bất biến sau khi set, không đọc lại '
                   'requested_by.department nếu người đó đổi phòng ban sau này.')
    project = models.CharField(max_length=100, blank=True, default='', verbose_name='Dự án (tuỳ chọn)')
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
        'catalog.Product', null=True, blank=True, on_delete=models.PROTECT,
        related_name='pr_items', verbose_name='Sản phẩm')
    qty_requested = models.PositiveIntegerField(validators=[MinValueValidator(1)], verbose_name='Số lượng yêu cầu')
    non_catalog_name = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Tên hàng (chưa có trong danh mục)')
    non_catalog_uom = models.CharField(
        max_length=20, blank=True, default='', verbose_name='Đơn vị tính (đề xuất)')
    non_catalog_note = models.TextField(blank=True, default='', verbose_name='Mô tả/quy cách (non-catalog)')
    required_date = models.DateField(null=True, blank=True, verbose_name='Ngày cần hàng')
    budget_category = models.CharField(
        max_length=100, null=True, blank=True, verbose_name='Nhóm ngân sách/Tài khoản')
    currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.VND, verbose_name='Loại tiền')
    estimated_unit_price = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(0)], null=True, blank=True,
        verbose_name='Đơn giá ước tính')
    qty_approved = models.PositiveIntegerField(null=True, blank=True, verbose_name='Số lượng được duyệt')
    qty_cancelled = models.PositiveIntegerField(default=0, verbose_name='Số lượng đã huỷ (phần còn mở)')

    class Meta:
        verbose_name = 'Dòng yêu cầu mua hàng'
        verbose_name_plural = 'Dòng yêu cầu mua hàng'

    def __str__(self):
        label = self.product.product_code if self.product_id else (self.non_catalog_name or 'non-catalog')
        return f'{self.purchase_request.request_no} - {label} x{self.qty_requested}'

    def clean(self):
        super().clean()
        has_product = self.product_id is not None
        has_non_catalog = bool((self.non_catalog_name or '').strip()) and bool((self.non_catalog_uom or '').strip())
        if has_product and (self.non_catalog_name or self.non_catalog_uom):
            raise ValidationError('Đã chọn sản phẩm trong danh mục thì không được điền thông tin non-catalog.')
        if not has_product and not has_non_catalog:
            raise ValidationError(
                'Phải chọn sản phẩm trong danh mục, hoặc điền đủ Tên hàng + Đơn vị tính cho hàng non-catalog.')
        if self.budget_category:
            self.budget_category = re.sub(r'\s+', ' ', self.budget_category.strip())
        if has_product and not self.budget_category:
            self.budget_category = self.product.category

    @property
    def is_non_catalog(self):
        return self.product_id is None

    @property
    def qty_allocated(self):
        from django.db.models import Sum
        return self.allocations.filter(
            status=ProcurementAllocation.Status.ACTIVE,
        ).aggregate(total=Sum('qty_allocated'))['total'] or 0

    @property
    def qty_ordered(self):
        from django.db.models import Sum
        committed_statuses = (
            PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIAL_RECEIVED,
            PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.CLOSED,
        )
        return self.allocations.filter(
            status=ProcurementAllocation.Status.ACTIVE,
            po_item__purchase_order__status__in=committed_statuses,
        ).aggregate(total=Sum('qty_allocated'))['total'] or 0

    @property
    def qty_open(self):
        return max(0, (self.qty_approved or 0) - self.qty_allocated - self.qty_cancelled)

    @property
    def qty_received(self):
        from .services import qty_received_by_allocation
        total = 0
        for allocation in self.allocations.filter(
                status=ProcurementAllocation.Status.ACTIVE, po_item__isnull=False):
            total += qty_received_by_allocation(allocation.po_item).get(allocation.pk, 0)
        return total
