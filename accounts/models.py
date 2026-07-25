from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from .permissions import ACTIONS, MODULES


class User(AbstractUser):
    """Custom user cho NVL/WMS.

    Kế thừa ``AbstractUser`` (đã có sẵn username/password/email/is_active/...)
    và bổ sung phần đặc thù của hệ thống:

    - ``role``: 1 trong 6 vai trò RBAC — là nguồn cho permission matrix
      (FR-USER-02, FR-USER-04). Ở bước A2 role sẽ được đồng bộ sang Django Group.
    - Soft delete (``is_deleted`` + ``deleted_at``): FR-USER-01 quy định DELETE là
      *xoá mềm* để giữ audit trail, không xoá cứng.

    Phân biệt 2 khái niệm:
    - ``is_active`` (có sẵn) = *deactivate* (khoá đăng nhập) — dùng cho bước UPDATE.
    - ``is_deleted``       = *soft delete* (đánh dấu đã xoá, vẫn giữ bản ghi).
    """

    class Role(models.TextChoices):
        MANAGER = 'MANAGER', 'Quản lý kho'
        STAFF = 'STAFF', 'Nhân viên kho'
        QC = 'QC', 'Nhân viên QC'
        PURCHASING = 'PURCHASING', 'Nhân viên mua hàng'
        ACCOUNTANT = 'ACCOUNTANT', 'Kế toán'
        ADMIN = 'ADMIN', 'Quản trị viên'

    class Department(models.TextChoices):
        """Trục phân quyền MỚI, song song với ``role`` (không thay thế).

        ``role`` tiếp tục quyết định ma trận CRUD (``ROLE_PERMISSIONS``) như cũ.
        ``department`` + ``is_manager`` chỉ phục vụ luồng duyệt/hủy/thông báo
        theo phòng ban (mỗi phòng ban có thể có quản lý riêng, không chỉ Kho).
        """
        WAREHOUSE = 'WAREHOUSE', 'Kho'
        QC = 'QC', 'QC'
        PURCHASING = 'PURCHASING', 'Mua hàng'
        ACCOUNTING = 'ACCOUNTING', 'Kế toán'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        blank=True,
        verbose_name='Vai trò',
        help_text='Vai trò RBAC — quyết định permission matrix.',
    )
    department = models.CharField(
        max_length=20,
        choices=Department.choices,
        blank=True,
        verbose_name='Phòng ban',
        help_text='Phòng ban để xác định người nộp/người duyệt trong luồng phê duyệt. '
                   'Bỏ trống cho Admin (không thuộc phòng ban cụ thể).',
    )
    is_manager = models.BooleanField(
        default=False,
        verbose_name='Là quản lý phòng ban',
        help_text='True nếu user là quản lý của "department" — được duyệt/hủy phiếu của '
                   'nhân viên cùng phòng ban.',
    )
    is_deleted = models.BooleanField(
        default=False,
        verbose_name='Đã xoá',
        help_text='Soft delete (FR-USER-01): đánh dấu đã xoá nhưng giữ lại để audit.',
    )
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời điểm xoá')

    # LƯU Ý: FK tới warehouse ("gán warehouse" ở bước UPDATE) sẽ được thêm ở mục 1b,
    # sau khi app `warehouse` tồn tại — thêm qua migration lúc đó (đúng thứ tự build).

    class Meta:
        # 30 custom permission cấp module (MODULES × ACTIONS) làm vocabulary RBAC.
        # rbac.sync_roles() gán chúng cho 6 Group theo ROLE_PERMISSIONS.
        permissions = [
            (f'can_{action}_{module}', f'{ACTIONS[action]} {label}')
            for module, label in MODULES.items()
            for action in ACTIONS
        ]

    def can(self, action, module):
        """Tiện ích: user có được thực hiện ``action`` trên ``module`` không?

        Vd ``user.can('approve', 'grn')`` -> kiểm tra perm ``accounts.can_approve_grn``.
        Superuser luôn trả về True (hành vi has_perm mặc định của Django).
        """
        return self.has_perm(f'accounts.can_{action}_{module}')

    def is_department_manager(self, department):
        """True nếu user là quản lý của đúng ``department`` được truyền vào.

        Dùng cho luồng duyệt/hủy phiếu theo phòng ban (song song với ``can()``
        — không thay thế permission matrix hiện có).
        """
        return self.is_manager and self.department == department

    def soft_delete(self):
        """Xoá mềm: đánh dấu đã xoá + khoá đăng nhập, giữ nguyên bản ghi cho audit."""
        self.is_deleted = True
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'is_active', 'deleted_at'])

    def __str__(self):
        return f'{self.username} ({self.get_role_display() or "chưa gán role"})'


class AuditLog(models.Model):
    """Nhật ký hành động (FR-USER-05): who / what / when (+ why).

    Thiết kế DÙNG CHUNG toàn hệ thống: ``target`` là GenericForeignKey nên một
    bảng log duy nhất trỏ được tới BẤT KỲ model nào — bây giờ là ``User``, sau
    này là GRN/QC/Batch ở Phase 2 (audit state-transition mà CLAUDE.md gọi là
    "khó thêm về sau", nên hạ tầng phải sẵn từ Phase 1).

    Append-only: chỉ tạo mới, không sửa/không xoá bản ghi sau khi ghi.
    Đừng tạo ``AuditLog`` trực tiếp — dùng ``accounts.audit.log_action()`` để
    thống nhất cách ghi who/what/when/why.
    """

    class Action(models.TextChoices):
        # Gợi ý mã hành động phổ biến; ``action`` KHÔNG ràng buộc choices để
        # module sau (GRN/QC...) tự mở rộng động từ nghiệp vụ riêng.
        CREATE = 'CREATE', 'Tạo mới'
        UPDATE = 'UPDATE', 'Cập nhật'
        DELETE = 'DELETE', 'Xoá'
        APPROVE = 'APPROVE', 'Duyệt'
        REJECT = 'REJECT', 'Từ chối'
        CANCEL = 'CANCEL', 'Hủy'
        OVERRIDE = 'OVERRIDE', 'Override'
        LOGIN = 'LOGIN', 'Đăng nhập'
        LOGOUT = 'LOGOUT', 'Đăng xuất'
        LOGIN_FAILED = 'LOGIN_FAILED', 'Đăng nhập thất bại'

    # WHO — SET_NULL để log SỐNG LÂU HƠN người thực hiện (xoá cứng user vẫn giữ log).
    # null cũng dùng cho hành động hệ thống (cron, migration).
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        verbose_name='Người thực hiện',
    )
    # WHAT — mã hành động ngắn (free text) + mô tả người-đọc-được.
    action = models.CharField(max_length=30, verbose_name='Hành động')
    description = models.CharField(max_length=255, blank=True, verbose_name='Mô tả')

    # Đối tượng bị tác động — GenericFK, có thể null (vd sự kiện LOGIN không có target).
    target_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Loại đối tượng',
    )
    target_id = models.CharField(max_length=64, null=True, blank=True, verbose_name='Mã đối tượng')
    target = GenericForeignKey('target_type', 'target_id')

    # WHY + dữ liệu thay đổi (before/after) — tuỳ chọn.
    reason = models.TextField(blank=True, verbose_name='Lý do')
    changes = models.JSONField(null=True, blank=True, verbose_name='Thay đổi')
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name='Địa chỉ IP')

    # WHEN
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Thời gian')

    class Meta:
        # -id làm tiebreaker: khi hai bản ghi trùng created_at (cùng micro-giây),
        # vẫn xác định "mới nhất trước" theo thứ tự chèn (pk tăng dần) — tránh
        # thứ tự nhập nhằng cho log append-only.
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['target_type', 'target_id']),
            models.Index(fields=['actor', '-created_at']),
        ]
        verbose_name = 'Nhật ký hành động'
        verbose_name_plural = 'Nhật ký hành động'

    def __str__(self):
        who = self.actor.username if self.actor else 'system'
        return f'[{self.created_at:%Y-%m-%d %H:%M}] {who} {self.action} {self.description}'.strip()


class Notification(models.Model):
    """Thông báo trong app cho luồng duyệt/bàn giao theo phòng ban.

    Poll khi load trang (badge số chưa đọc trong navbar) — KHÔNG dùng
    Celery/websocket, đúng convention ``⏸️`` của CLAUDE.md. Đừng tạo trực tiếp
    — dùng ``accounts.notifications.notify()`` để gửi hàng loạt cho nhiều
    người nhận cùng lúc.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications',
        verbose_name='Người nhận',
    )
    verb = models.CharField(max_length=255, verbose_name='Nội dung thông báo')

    # Đối tượng liên quan (GRN/GIN/GrnReturn/WarehouseHandoff...) — nullable,
    # cùng pattern GenericFK với AuditLog.target ở trên.
    target_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Loại đối tượng',
    )
    target_id = models.CharField(max_length=64, null=True, blank=True, verbose_name='Mã đối tượng')
    target = GenericForeignKey('target_type', 'target_id')

    is_read = models.BooleanField(default=False, verbose_name='Đã đọc')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Thời gian')

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
        ]
        verbose_name = 'Thông báo'
        verbose_name_plural = 'Thông báo'

    def __str__(self):
        return f'{self.recipient.username}: {self.verb}'


class Approval(models.Model):
    """Yêu cầu duyệt "nhân viên nộp -> quản lý phòng ban duyệt" (Phase B/C).

    Dùng chung cho GRN submit / GIN confirm / GrnReturn QC-confirm — ``target`` là
    GenericFK trỏ tới đối tượng cần duyệt. Bản thân ``Approval`` KHÔNG biết và
    không thực thi transition thật của đối tượng đó — transition thật truyền vào
    qua callback ``on_approve``/``on_reject`` do app gọi cung cấp, xem
    ``accounts.approvals.decide_approval()``. Đừng tạo/sửa trực tiếp — dùng
    ``accounts.approvals.create_approval()``/``decide_approval()``.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Đang chờ duyệt'
        APPROVED = 'APPROVED', 'Đã duyệt'
        REJECTED = 'REJECTED', 'Đã từ chối'

    target_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Loại đối tượng',
    )
    target_id = models.CharField(max_length=64, verbose_name='Mã đối tượng')
    target = GenericForeignKey('target_type', 'target_id')

    department = models.CharField(
        max_length=20, choices=User.Department.choices, verbose_name='Phòng ban duyệt',
        help_text='Chỉ quản lý (is_manager=True) của phòng ban này được quyết định.',
    )
    action_label = models.CharField(max_length=255, verbose_name='Nội dung yêu cầu duyệt')
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, verbose_name='Trạng thái')

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='approvals_submitted',
        verbose_name='Người nộp',
    )
    submitted_at = models.DateTimeField(auto_now_add=True, verbose_name='Thời gian nộp')

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='approvals_decided', verbose_name='Người duyệt',
    )
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời gian duyệt')
    decision_note = models.TextField(blank=True, verbose_name='Ghi chú quyết định')

    class Meta:
        ordering = ['-submitted_at', '-id']
        indexes = [
            models.Index(fields=['target_type', 'target_id']),
            models.Index(fields=['department', 'status']),
        ]
        verbose_name = 'Yêu cầu duyệt'
        verbose_name_plural = 'Yêu cầu duyệt'

    def __str__(self):
        return f'{self.action_label} — {self.get_status_display()}'
