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

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        blank=True,
        verbose_name='Vai trò',
        help_text='Vai trò RBAC — quyết định permission matrix.',
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
