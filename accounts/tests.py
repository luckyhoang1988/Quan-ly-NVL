"""Test RBAC cho app accounts (FR-USER-02 RBAC, FR-USER-04 permission matrix).

Tập trung vào các nhánh dễ sai:
- Đồng bộ user.role -> Group membership (kể cả khi ĐỔI role).
- Cột Approve (không có trong bảng gốc, chốt theo "Theo nghiệp vụ"): ai được / không
  được approve — điểm dễ gán nhầm nhất.
- Các ô "full CRUD nhưng KHÔNG approve" (Purchasing/PO, Accountant/Reports).
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from accounts.approvals import create_approval
from accounts.audit import client_ip, log_action
from accounts.models import Approval, AuditLog, Notification
from accounts.pagination import DEFAULT_PAGE_SIZE, paginate_queryset
from accounts.permissions import ACTIONS, MODULES, all_permission_codenames
from accounts.rbac import sync_roles

User = get_user_model()


class RBACMatrixTest(TestCase):
    def setUp(self):
        # Idempotent — signal post_migrate đã chạy, gọi lại cho chắc chắn & rõ ràng.
        sync_roles()

    def make(self, role):
        return User.objects.create_user(username=f'u_{role}', password='x', role=role)

    # --- FR-USER-02: RBAC (6 role -> Group, đồng bộ membership) ---

    def test_TC_USER_02_001_six_role_groups_exist(self):
        names = set(Group.objects.values_list('name', flat=True))
        self.assertTrue(
            {'MANAGER', 'STAFF', 'QC', 'PURCHASING', 'ACCOUNTANT', 'ADMIN'} <= names
        )

    def test_TC_USER_02_002_role_assigns_group_membership(self):
        u = self.make('STAFF')
        self.assertEqual(list(u.groups.values_list('name', flat=True)), ['STAFF'])

    def test_TC_USER_02_003_change_role_resyncs_membership_and_perms(self):
        u = self.make('STAFF')
        self.assertTrue(u.can('create', 'grn'))   # quyền của STAFF

        u.role = 'QC'
        u.save()
        u = User.objects.get(pk=u.pk)  # nạp lại để xoá cache quyền trên instance cũ

        self.assertEqual(list(u.groups.values_list('name', flat=True)), ['QC'])
        self.assertFalse(u.can('create', 'grn'))  # quyền STAFF cũ đã mất
        self.assertTrue(u.can('create', 'qc'))    # quyền QC mới đã có

    # --- FR-USER-04: permission matrix từng role ---

    def test_TC_USER_04_001_staff_grn_cru_only(self):
        u = self.make('STAFF')
        self.assertTrue(u.can('create', 'grn'))
        self.assertTrue(u.can('read', 'grn'))
        # 'update' cần cho bước PENDING_QC: nhân viên Kho nhập Qty thực nhận +
        # bấm Submit to QC (mục 2a Workflow States) — xem accounts/permissions.py.
        self.assertTrue(u.can('update', 'grn'))
        self.assertFalse(u.can('delete', 'grn'))
        self.assertFalse(u.can('approve', 'grn'))

    def test_TC_USER_04_002_qc_approve_scope(self):
        u = self.make('QC')
        self.assertTrue(u.can('approve', 'qc'))    # QC duyệt kết quả QC
        self.assertTrue(u.can('create', 'qc'))
        self.assertFalse(u.can('delete', 'qc'))    # QC chỉ CRU, không có Delete
        self.assertFalse(u.can('override', 'qc'))  # override -> Manager/Admin, không phải QC Inspector
        self.assertFalse(u.can('approve', 'grn'))  # KHÔNG duyệt GRN
        self.assertFalse(u.can('create', 'grn'))   # trên GRN chỉ Read

    def test_TC_USER_04_003_purchasing_po_crud_no_approve(self):
        u = self.make('PURCHASING')
        for a in ('create', 'read', 'update', 'delete'):
            self.assertTrue(u.can(a, 'po'), f'PURCHASING thiếu {a}_po')
        self.assertFalse(u.can('approve', 'po'))   # approve PO -> Manager/Admin

    def test_TC_USER_04_004_accountant_reports_crud_no_approve(self):
        u = self.make('ACCOUNTANT')
        for a in ('create', 'read', 'update', 'delete'):
            self.assertTrue(u.can(a, 'reports'), f'ACCOUNTANT thiếu {a}_reports')
        self.assertFalse(u.can('approve', 'reports'))  # approve Reports -> Manager/Admin
        self.assertTrue(u.can('read', 'po'))
        self.assertFalse(u.can('create', 'po'))        # module khác chỉ Read

    def test_TC_USER_04_005_manager_scope(self):
        u = self.make('MANAGER')
        for m in ('grn', 'gin', 'opname', 'po'):
            self.assertTrue(u.can('approve', m), f'MANAGER thiếu approve_{m}')
            self.assertTrue(u.can('delete', m), f'MANAGER thiếu delete_{m}')
        self.assertTrue(u.can('approve', 'qc'))
        self.assertFalse(u.can('create', 'qc'))       # trên QC chỉ R + approve + override
        self.assertTrue(u.can('override', 'qc'))      # QC approval override (mục 2b)
        self.assertFalse(u.can('override', 'grn'))    # override chỉ áp dụng cho QC
        self.assertTrue(u.can('approve', 'reports'))
        self.assertFalse(u.can('create', 'reports'))  # trên Reports chỉ R + approve

    def test_TC_USER_04_006_admin_full_matrix(self):
        u = self.make('ADMIN')  # role ADMIN (KHÔNG phải superuser) -> quyền lấy từ Group
        for m in MODULES:
            for a in ACTIONS:
                self.assertTrue(u.can(a, m), f'ADMIN thiếu {a}_{m}')


class AuditLogTest(TestCase):
    """Test hạ tầng audit dùng chung (FR-USER-05): who / what / when / why.

    Nhánh dễ sai:
    - GenericFK target phải trỏ đúng đối tượng (bất kỳ model nào).
    - Log phải SỐNG LÂU HƠN actor: xoá cứng user -> log còn, actor=None (SET_NULL).
    - Hành động hệ thống (actor=None) vẫn ghi được.
    """

    def make(self, username='u_actor', role='STAFF'):
        return User.objects.create_user(username=username, password='x', role=role)

    def test_TC_USER_05_001_log_records_who_what_when(self):
        actor = self.make()
        target = self.make(username='u_target', role='QC')

        entry = log_action(actor, AuditLog.Action.UPDATE, target=target,
                           description='Đổi role STAFF -> QC')

        self.assertEqual(entry.actor, actor)              # WHO
        self.assertEqual(entry.action, 'UPDATE')          # WHAT
        self.assertIsNotNone(entry.created_at)            # WHEN (auto_now_add)
        self.assertEqual(entry.description, 'Đổi role STAFF -> QC')

    def test_TC_USER_05_002_target_generic_fk_resolves(self):
        actor = self.make()
        target = self.make(username='u_target')

        entry = log_action(actor, AuditLog.Action.DELETE, target=target)
        entry = AuditLog.objects.get(pk=entry.pk)  # nạp lại từ DB, không dùng cache

        self.assertEqual(entry.target, target)  # GenericFK giải đúng đối tượng
        self.assertEqual(entry.target_type,
                         ContentType.objects.get_for_model(User))
        self.assertEqual(entry.target_id, str(target.pk))

    def test_TC_USER_05_003_log_outlives_actor_on_hard_delete(self):
        actor = self.make()
        entry = log_action(actor, AuditLog.Action.LOGIN)

        actor.delete()  # xoá CỨNG user
        entry = AuditLog.objects.get(pk=entry.pk)

        self.assertIsNone(entry.actor)          # SET_NULL: log không bị xoá theo
        self.assertEqual(entry.action, 'LOGIN')  # nội dung log vẫn nguyên

    def test_TC_USER_05_004_system_action_allows_null_actor(self):
        entry = log_action(None, AuditLog.Action.LOGIN_FAILED,
                          description='sai mật khẩu', ip_address='10.0.0.9')

        self.assertIsNone(entry.actor)
        self.assertEqual(entry.ip_address, '10.0.0.9')

    def test_TC_USER_05_005_reason_and_changes_stored(self):
        actor = self.make()
        target = self.make(username='u_target')

        entry = log_action(actor, AuditLog.Action.UPDATE, target=target,
                           reason='nhân sự chuyển bộ phận',
                           changes={'role': ['STAFF', 'QC']})
        entry = AuditLog.objects.get(pk=entry.pk)

        self.assertEqual(entry.reason, 'nhân sự chuyển bộ phận')
        self.assertEqual(entry.changes, {'role': ['STAFF', 'QC']})  # JSON round-trip

    def test_TC_USER_05_006_ordering_newest_first(self):
        actor = self.make()
        first = log_action(actor, AuditLog.Action.LOGIN)
        second = log_action(actor, AuditLog.Action.LOGOUT)

        self.assertEqual(list(AuditLog.objects.all()), [second, first])


class LoginAuthTest(TestCase):
    """Đăng nhập/đăng xuất (FR-USER-03) + audit qua signal (FR-USER-05).

    Nhánh dễ sai:
    - Sai mật khẩu -> LOGIN_FAILED (actor=None, giữ username đã thử), KHÔNG có LOGIN.
    - User đã soft-delete (is_active=False) tuyệt đối KHÔNG đăng nhập được.
    - Trang cần đăng nhập phải chuyển hướng về login khi chưa auth.
    """

    PASSWORD = 'secret-pass-123'

    def setUp(self):
        # LocMemCache không tự reset giữa các test method — xoá counter
        # rate-limit (accounts.forms.LoginForm) để test này không bị ảnh
        # hưởng bởi lần đăng nhập sai của test khác chạy trước (cùng IP 127.0.0.1).
        cache.clear()

    def make(self, username='u_login', role='STAFF'):
        return User.objects.create_user(
            username=username, password=self.PASSWORD, role=role)

    def test_TC_USER_03_001_login_success_audits_login(self):
        user = self.make()
        resp = self.client.post(
            reverse('login'), {'username': user.username, 'password': self.PASSWORD})

        self.assertEqual(resp.status_code, 302)            # đăng nhập -> redirect
        self.assertEqual(resp.url, reverse('dashboard'))
        entry = AuditLog.objects.filter(action='LOGIN', actor=user).first()
        self.assertIsNotNone(entry)                        # WHO/WHAT/WHEN được ghi
        self.assertEqual(entry.ip_address, '127.0.0.1')    # IP từ test client

    def test_TC_USER_03_002_wrong_password_audits_login_failed(self):
        self.make()
        resp = self.client.post(
            reverse('login'), {'username': 'u_login', 'password': 'sai-mat-khau'})

        self.assertEqual(resp.status_code, 200)            # form render lại, không login
        failed = AuditLog.objects.filter(action='LOGIN_FAILED')
        self.assertEqual(failed.count(), 1)
        self.assertIsNone(failed.first().actor)            # hành động hệ thống
        self.assertIn('u_login', failed.first().description)  # giữ username đã thử
        self.assertFalse(AuditLog.objects.filter(action='LOGIN').exists())

    def test_TC_USER_03_003_logout_audits_logout(self):
        user = self.make()
        self.client.post(
            reverse('login'), {'username': user.username, 'password': self.PASSWORD})

        resp = self.client.post(reverse('logout'))

        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            AuditLog.objects.filter(action='LOGOUT', actor=user).exists())

    def test_TC_USER_03_004_soft_deleted_user_cannot_login(self):
        user = self.make(username='u_gone')
        user.soft_delete()   # is_active=False

        resp = self.client.post(
            reverse('login'), {'username': 'u_gone', 'password': self.PASSWORD})

        self.assertEqual(resp.status_code, 200)                 # không vào được
        self.assertNotIn('_auth_user_id', self.client.session)  # chưa auth
        self.assertFalse(AuditLog.objects.filter(action='LOGIN').exists())
        self.assertTrue(AuditLog.objects.filter(action='LOGIN_FAILED').exists())

    def test_TC_USER_03_004b_deleted_user_cannot_login_even_if_active_bypassed(self):
        """M2: ``QuerySet.update()``/raw SQL bypass hẳn ``User.save()`` (nơi M1
        ép is_active=False khi is_deleted=True) nên vẫn có thể để lại
        is_deleted=True + is_active=True trong DB. Backend phải tự chặn
        ``is_deleted`` độc lập với ``is_active``, không chỉ trông chờ invariant
        ở save()."""
        user = self.make(username='u_gone2')
        User.objects.filter(pk=user.pk).update(is_deleted=True, is_active=True)

        resp = self.client.post(
            reverse('login'), {'username': 'u_gone2', 'password': self.PASSWORD})

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_TC_USER_03_005_login_page_renders(self):
        resp = self.client.get(reverse('login'))

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'registration/login.html')

    def test_TC_USER_03_006_dashboard_requires_login(self):
        resp = self.client.get(reverse('dashboard'))

        self.assertEqual(resp.status_code, 302)             # @login_required chặn
        self.assertIn(reverse('login'), resp.url)

    def test_TC_USER_03_007_login_blocked_after_max_failed_attempts(self):
        self.make()
        for _ in range(5):
            self.client.post(
                reverse('login'), {'username': 'u_login', 'password': 'sai-mat-khau'})

        # Lần thứ 6 dù đúng mật khẩu vẫn bị chặn — rate-limit theo IP, không
        # phải theo tài khoản.
        resp = self.client.post(
            reverse('login'), {'username': 'u_login', 'password': self.PASSWORD})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Đăng nhập sai quá nhiều lần')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_TC_USER_03_008_successful_login_resets_rate_limit_counter(self):
        self.make()
        for _ in range(3):
            self.client.post(
                reverse('login'), {'username': 'u_login', 'password': 'sai-mat-khau'})

        resp = self.client.post(
            reverse('login'), {'username': 'u_login', 'password': self.PASSWORD})
        self.assertEqual(resp.status_code, 302)  # đăng nhập thành công -> reset counter

        self.client.logout()
        resp2 = None
        for _ in range(3):
            resp2 = self.client.post(
                reverse('login'), {'username': 'u_login', 'password': 'sai-mat-khau'})

        self.assertEqual(resp2.status_code, 200)
        self.assertNotContains(resp2, 'Đăng nhập sai quá nhiều lần')

    def test_TC_USER_03_009_xff_spoofing_does_not_bypass_rate_limit(self):
        # H1: chưa có reverse proxy tin cậy nào đứng trước app (TRUST_X_FORWARDED_FOR
        # mặc định False) — client tự set X-Forwarded-For khác nhau mỗi request để giả
        # làm nhiều IP khác nhau vẫn phải bị khoá theo REMOTE_ADDR thật (127.0.0.1).
        self.make()
        for i in range(5):
            self.client.post(
                reverse('login'), {'username': 'u_login', 'password': 'sai-mat-khau'},
                HTTP_X_FORWARDED_FOR=f'10.0.0.{i}')

        resp = self.client.post(
            reverse('login'), {'username': 'u_login', 'password': self.PASSWORD},
            HTTP_X_FORWARDED_FOR='10.0.0.99')

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Đăng nhập sai quá nhiều lần')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_TC_USER_03_010_blank_password_does_not_reset_rate_limit_counter(self):
        # H2: AuthenticationForm.clean() không raise khi password rỗng (không hề
        # gọi authenticate()) — submit form thiếu mật khẩu không được phép xoá
        # counter rate-limit của IP, kẻ tấn công không thể lợi dụng việc này để
        # né rate-limit giữa các lần thử sai mật khẩu thật.
        self.make()
        for _ in range(4):
            self.client.post(
                reverse('login'), {'username': 'u_login', 'password': 'sai-mat-khau'})

        # Submit thiếu password: super().clean() không raise (username/password rỗng
        # bị AuthenticationForm bỏ qua, không gọi authenticate()) -> không được reset.
        resp_blank = self.client.post(reverse('login'), {'username': 'u_login', 'password': ''})
        self.assertEqual(resp_blank.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

        # Lần sai mật khẩu thứ 5 (tính cả 4 lần đầu, request blank không tính) — việc
        # kiểm tra rate-limit xảy ra TRƯỚC khi tăng counter (giống test_007) nên request
        # này vẫn được xử lý bình thường, đưa counter lên đúng 5.
        resp5 = self.client.post(
            reverse('login'), {'username': 'u_login', 'password': 'sai-mat-khau'})
        self.assertEqual(resp5.status_code, 200)

        # Request kế tiếp (dù đúng mật khẩu) phải bị chặn ngay vì counter đã đạt 5 —
        # chứng tỏ request thiếu mật khẩu ở trên không hề xoá counter.
        resp6 = self.client.post(
            reverse('login'), {'username': 'u_login', 'password': self.PASSWORD})

        self.assertEqual(resp6.status_code, 200)
        self.assertContains(resp6, 'Đăng nhập sai quá nhiều lần')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_TC_USER_03_011_rate_limited_block_audits_login_failed(self):
        # L5: request bị chặn bởi rate-limit không bao giờ chạm authenticate() thật
        # của Django -> signal user_login_failed (accounts/signals.py) không phát,
        # nên trước đây các lượt bị BLOCK hoàn toàn vô hình trên audit trail (chỉ
        # thấy đúng 5 lượt sai mật khẩu thật, không thấy lượt thứ 6 bị chặn).
        self.make()
        for _ in range(5):
            self.client.post(
                reverse('login'), {'username': 'u_login', 'password': 'sai-mat-khau'})
        self.assertEqual(AuditLog.objects.filter(action='LOGIN_FAILED').count(), 5)

        resp = self.client.post(
            reverse('login'), {'username': 'u_login', 'password': self.PASSWORD})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Đăng nhập sai quá nhiều lần')
        failed = AuditLog.objects.filter(action='LOGIN_FAILED')
        self.assertEqual(failed.count(), 6)  # 5 lần sai thật + 1 lần bị chặn
        blocked_entry = failed.order_by('-created_at').first()
        self.assertIsNone(blocked_entry.actor)
        self.assertIn('u_login', blocked_entry.description)
        self.assertIn('rate-limit', blocked_entry.description.lower())
        self.assertEqual(blocked_entry.ip_address, '127.0.0.1')


class ClientIpTest(TestCase):
    """H1: ``client_ip()`` không được tin ``X-Forwarded-For`` khi chưa cấu hình proxy
    tin cậy (``TRUST_X_FORWARDED_FOR``, mặc định False — xem CLAUDE.md)."""

    def test_ignores_xff_by_default(self):
        req = RequestFactory().get('/', HTTP_X_FORWARDED_FOR='1.2.3.4', REMOTE_ADDR='9.9.9.9')
        self.assertEqual(client_ip(req), '9.9.9.9')

    @override_settings(TRUST_X_FORWARDED_FOR=True)
    def test_uses_xff_when_trusted_proxy_configured(self):
        req = RequestFactory().get('/', HTTP_X_FORWARDED_FOR='1.2.3.4', REMOTE_ADDR='9.9.9.9')
        self.assertEqual(client_ip(req), '1.2.3.4')


class UserAdminSaveModelTest(TestCase):
    """M4: Django Admin đánh dấu ``is_deleted`` trực tiếp qua form (không đi qua
    ``User.soft_delete()``) phải vẫn backfill ``deleted_at`` — trước đây field này
    readonly nên không có trong dữ liệu POST, mãi None dù user đã "đã xoá"."""

    def test_TC_USER_ADMIN_001_marking_deleted_backfills_deleted_at_and_forces_inactive(self):
        from django.contrib import admin as django_admin

        from accounts.admin import CustomUserAdmin

        user = User.objects.create_user(
            username='admin_target', password='x', role='STAFF', is_active=True)
        model_admin = CustomUserAdmin(User, django_admin.site)

        user.is_deleted = True
        model_admin.save_model(request=None, obj=user, form=None, change=True)

        user.refresh_from_db()
        self.assertTrue(user.is_deleted)
        self.assertFalse(user.is_active)          # bất biến M1 vẫn giữ
        self.assertIsNotNone(user.deleted_at)     # M4: không còn bị bỏ quên

    def test_TC_USER_ADMIN_002_save_without_is_deleted_change_does_not_touch_deleted_at(self):
        from django.contrib import admin as django_admin

        from accounts.admin import CustomUserAdmin

        user = User.objects.create_user(username='admin_target2', password='x', role='STAFF')
        model_admin = CustomUserAdmin(User, django_admin.site)

        user.first_name = 'Ai'
        model_admin.save_model(request=None, obj=user, form=None, change=True)

        user.refresh_from_db()
        self.assertEqual(user.first_name, 'Ai')
        self.assertIsNone(user.deleted_at)


class UserCrudTest(TestCase):
    """CRUD user (FR-USER-01) — Admin quản lý, audit CREATE/UPDATE/DELETE (FR-USER-05).

    Nhánh dễ sai:
    - Chỉ Admin (role ADMIN / superuser) được quản lý user; role khác -> 403,
      ẩn danh -> redirect login. Không đi qua ma trận can(action, module).
    - CREATE: sinh mật khẩu tạm + gửi email + ghi audit CREATE (không để admin tự đặt pass).
    - UPDATE đổi role -> signal resync Group + audit changes before/after.
    - DELETE là SOFT delete (giữ bản ghi cho audit), KHÔNG xoá cứng; chặn tự xoá mình.
    """

    PASSWORD = 'admin-pass-123'

    def setUp(self):
        sync_roles()
        self.admin = User.objects.create_user(
            username='boss', password=self.PASSWORD, role='ADMIN')
        self.client.force_login(self.admin)  # force_login: không phát signal LOGIN

    # --- Phân quyền truy cập ---

    def test_TC_USER_01_001_non_admin_forbidden(self):
        staff = User.objects.create_user(username='staff1', password='x', role='STAFF')
        self.client.force_login(staff)
        resp = self.client.get(reverse('user_list'))
        self.assertEqual(resp.status_code, 403)   # đã đăng nhập nhưng không phải admin

    def test_TC_USER_01_002_anonymous_redirected_to_login(self):
        self.client.logout()
        resp = self.client.get(reverse('user_list'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('login'), resp.url)

    # --- CREATE ---

    def test_TC_USER_01_003_create_with_admin_chosen_password_emails_and_audits(self):
        resp = self.client.post(reverse('user_create'), {
            'username': 'newbie', 'email': 'newbie@example.com',
            'first_name': 'New', 'last_name': 'Bie', 'role': 'STAFF',
            'password1': 'CorrectHorse9', 'password2': 'CorrectHorse9'})

        created = User.objects.get(username='newbie')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('user_detail', args=[created.pk]))
        self.assertTrue(created.check_password('CorrectHorse9'))  # đúng mật khẩu admin đặt
        self.assertTrue(created.is_active)
        self.assertEqual(                                # signal đồng bộ role -> Group
            list(created.groups.values_list('name', flat=True)), ['STAFF'])
        self.assertEqual(len(mail.outbox), 1)            # đã gửi email thông báo tài khoản
        self.assertIn('newbie@example.com', mail.outbox[0].to)
        # M3: email KHÔNG được chứa mật khẩu plaintext — admin đặt mật khẩu và
        # báo trực tiếp cho user qua kênh riêng, không phải qua email.
        self.assertNotIn('CorrectHorse9', mail.outbox[0].body)

        entry = AuditLog.objects.filter(action='CREATE').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.admin)        # WHO = admin đang đăng nhập
        self.assertEqual(entry.target, created)          # WHAT trên đối tượng nào
        self.assertEqual(entry.ip_address, '127.0.0.1')  # IP client

    def test_TC_USER_01_003c_create_rejects_is_manager_without_department(self):
        """M6: is_manager=True mà department bỏ trống -> is_department_manager()
        không bao giờ khớp phòng ban nào (quản lý "ma") — form phải chặn ngay
        lúc tạo, không để lọt xuống DB."""
        resp = self.client.post(reverse('user_create'), {
            'username': 'ghostmgr', 'email': 'ghostmgr@example.com',
            'first_name': 'Ghost', 'last_name': 'Mgr', 'role': 'STAFF',
            'is_manager': 'on', 'department': '',
            'password1': 'CorrectHorse9', 'password2': 'CorrectHorse9'})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Phải chọn phòng ban khi đánh dấu là quản lý phòng ban.')
        self.assertFalse(User.objects.filter(username='ghostmgr').exists())

    def test_TC_USER_01_003b_create_rejects_mismatched_passwords(self):
        resp = self.client.post(reverse('user_create'), {
            'username': 'newbie2', 'email': 'newbie2@example.com',
            'first_name': 'New', 'last_name': 'Bie', 'role': 'STAFF',
            'password1': 'CorrectHorse9', 'password2': 'Different9'})

        self.assertEqual(resp.status_code, 200)          # ở lại form, không tạo user
        self.assertFalse(User.objects.filter(username='newbie2').exists())
        self.assertContains(resp, 'Hai mật khẩu không khớp nhau.')

    # --- READ ---

    def test_TC_USER_01_004_detail_shows_permissions(self):
        staff = User.objects.create_user(username='viewme', password='x', role='STAFF')
        resp = self.client.get(reverse('user_detail', args=[staff.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'accounts/user_detail.html')
        self.assertContains(resp, 'Quyền hạn')           # có bảng quyền hạn

    # --- UPDATE ---

    def test_TC_USER_01_005_update_role_resyncs_group_and_audits_changes(self):
        u = User.objects.create_user(username='mover', password='x', role='STAFF')
        resp = self.client.post(reverse('user_update', args=[u.pk]), {
            'email': '', 'first_name': '', 'last_name': '',
            'role': 'QC', 'is_active': 'on'})

        self.assertEqual(resp.status_code, 302)
        u.refresh_from_db()
        self.assertEqual(u.role, 'QC')
        self.assertEqual(list(u.groups.values_list('name', flat=True)), ['QC'])

        entry = AuditLog.objects.filter(action='UPDATE', target_id=str(u.pk)).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.changes.get('role'), ['STAFF', 'QC'])  # before/after

    def test_TC_USER_01_006_deactivate_via_update(self):
        u = User.objects.create_user(username='lockme', password='x', role='STAFF')
        # Bỏ trống checkbox is_active -> deactivate (khoá đăng nhập).
        resp = self.client.post(reverse('user_update', args=[u.pk]), {
            'email': '', 'first_name': '', 'last_name': '', 'role': 'STAFF'})

        self.assertEqual(resp.status_code, 302)
        u.refresh_from_db()
        self.assertFalse(u.is_active)
        entry = AuditLog.objects.filter(action='UPDATE', target_id=str(u.pk)).first()
        self.assertIn('is_active', entry.changes)

    def test_TC_USER_01_006b_update_rejects_blank_role(self):
        """L6: ``UserUpdateForm`` không required=True cho ``role`` như
        ``UserCreateForm`` — admin có thể bỏ trống role của 1 user đang tồn tại,
        khiến ``codenames_for_role('')`` trả về rỗng (mất hết quyền CRUD, chỉ
        còn quyền xem menu). Form phải chặn, không cho lưu role rỗng."""
        u = User.objects.create_user(username='norole', password='x', role='STAFF')
        resp = self.client.post(reverse('user_update', args=[u.pk]), {
            'email': '', 'first_name': '', 'last_name': '', 'role': '', 'is_active': 'on'})

        self.assertEqual(resp.status_code, 200)  # re-render form, không redirect
        self.assertContains(resp, 'Trường này là bắt buộc.')
        u.refresh_from_db()
        self.assertEqual(u.role, 'STAFF')  # không bị đổi

    # --- DELETE ---

    def test_TC_USER_01_007_delete_is_soft_and_audited(self):
        u = User.objects.create_user(username='byebye', password='x', role='STAFF')
        resp = self.client.post(reverse('user_delete', args=[u.pk]))

        self.assertEqual(resp.status_code, 302)
        u.refresh_from_db()                     # bản ghi VẪN còn (không xoá cứng)
        self.assertTrue(u.is_deleted)
        self.assertFalse(u.is_active)
        self.assertIsNotNone(u.deleted_at)
        self.assertTrue(
            AuditLog.objects.filter(action='DELETE', target_id=str(u.pk)).exists())

    def test_TC_USER_01_008_cannot_delete_self(self):
        resp = self.client.post(reverse('user_delete', args=[self.admin.pk]))
        self.assertEqual(resp.status_code, 302)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_deleted)  # tài khoản của mình còn nguyên
        self.assertFalse(
            AuditLog.objects.filter(
                action='DELETE', target_id=str(self.admin.pk)).exists())

    def test_TC_USER_01_008b_cannot_delete_already_deleted_user(self):
        """L4: xoá lại 1 user đã ``is_deleted`` (vd. bấm F5/quay lại trình duyệt
        vào thẳng URL) không được phép gọi lại ``soft_delete()`` — nếu không,
        ``deleted_at`` bị ghi đè bằng thời điểm mới, làm sai audit trail thời
        điểm xoá THẬT. Chặn cả GET (trang xác nhận) lẫn POST."""
        u = User.objects.create_user(username='byebye2', password='x', role='STAFF')
        u.soft_delete()
        first_deleted_at = u.deleted_at

        get_resp = self.client.get(reverse('user_delete', args=[u.pk]))
        post_resp = self.client.post(reverse('user_delete', args=[u.pk]))

        self.assertEqual(get_resp.status_code, 302)
        self.assertEqual(get_resp.url, reverse('user_detail', args=[u.pk]))
        self.assertEqual(post_resp.status_code, 302)
        self.assertEqual(post_resp.url, reverse('user_detail', args=[u.pk]))
        u.refresh_from_db()
        self.assertEqual(u.deleted_at, first_deleted_at)  # không bị ghi đè
        self.assertFalse(
            AuditLog.objects.filter(action='DELETE', target_id=str(u.pk)).count() > 1)

    def test_TC_USER_01_009_cannot_deactivate_self_via_update_form(self):
        """Guard tự-khoá phải áp dụng ở CẢ form sửa chung (bỏ tick is_active), không
        chỉ nút "Khoá nhanh" (``user_toggle_active``) — cùng 1 hệ quả (tự đăng xuất
        khỏi hệ thống, không ai còn quyền mở khoá lại) nên phải chặn giống nhau."""
        resp = self.client.post(reverse('user_update', args=[self.admin.pk]), {
            'email': '', 'first_name': '', 'last_name': '', 'role': 'ADMIN'})
        # Bỏ trống checkbox is_active -> đáng lẽ deactivate, nhưng đây là tự khoá mình.

        self.assertEqual(resp.status_code, 200)  # ở lại form, KHÔNG lưu
        self.assertContains(resp, 'Không thể tự khoá tài khoản của chính bạn.')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertFalse(
            AuditLog.objects.filter(
                action='UPDATE', target_id=str(self.admin.pk)).exists())

    def test_TC_USER_01_009b_cannot_change_own_role_via_update_form(self):
        """H3: guard tự-khoá is_active (test_009) chỉ chặn is_active, không chặn
        đổi role — ADMIN vẫn có thể tự hạ role (vd ADMIN -> STAFF) rồi mất luôn
        quyền quản trị, không còn ai (kể cả chính mình) đổi lại được. Mirror guard
        is_active, so sánh role mới với ``before['role']`` thay vì is_active."""
        resp = self.client.post(reverse('user_update', args=[self.admin.pk]), {
            'email': '', 'first_name': '', 'last_name': '',
            'role': 'STAFF', 'is_active': 'on'})

        self.assertEqual(resp.status_code, 200)  # ở lại form, KHÔNG lưu
        self.assertContains(resp, 'Không thể tự đổi vai trò (role) của chính bạn.')
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, 'ADMIN')
        self.assertFalse(
            AuditLog.objects.filter(
                action='UPDATE', target_id=str(self.admin.pk)).exists())

    def test_TC_USER_01_009c_can_still_update_own_other_fields(self):
        """Guard H3 chỉ chặn đổi role, không được chặn nhầm việc admin tự sửa các
        field khác của chính mình (email, tên...) — vẫn phải lưu bình thường khi
        role giữ nguyên."""
        resp = self.client.post(reverse('user_update', args=[self.admin.pk]), {
            'email': 'boss@example.com', 'first_name': 'Boss', 'last_name': '',
            'role': 'ADMIN', 'is_active': 'on'})

        self.assertEqual(resp.status_code, 302)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.email, 'boss@example.com')
        self.assertEqual(self.admin.role, 'ADMIN')

    def test_TC_USER_01_010_cannot_update_deleted_user(self):
        """Bug fix 2026-07-27: user đã xoá mềm không cho sửa qua form chung — nếu
        mở view này ra, ``UserUpdateForm`` có thể bật lại ``is_active=True`` trong
        khi ``is_deleted`` vẫn True, và login (chỉ check ``is_active``) sẽ cho
        đăng nhập lại một tài khoản coi như đã xoá. Mirror guard đã có ở
        ``user_password_set``/``user_toggle_active``."""
        u = User.objects.create_user(username='ghost', password='x', role='STAFF')
        u.soft_delete()

        resp = self.client.post(reverse('user_update', args=[u.pk]), {
            'email': '', 'first_name': '', 'last_name': '', 'role': 'STAFF', 'is_active': 'on'})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('user_detail', args=[u.pk]))
        u.refresh_from_db()
        self.assertFalse(u.is_active)   # không bị bật lại
        self.assertFalse(
            AuditLog.objects.filter(action='UPDATE', target_id=str(u.pk)).exists())

    def test_TC_USER_01_011_save_invariant_forces_inactive_when_deleted(self):
        """Bug fix 2026-07-27: is_deleted=True phải kéo theo is_active=False ở MỌI
        đường ghi (``User.save()``), không chỉ qua ``user_update`` view/form — kể
        cả gán trực tiếp qua ORM (vd Django admin, nơi is_active/is_deleted là 2
        field tách biệt, không đi qua view guard ở trên)."""
        u = User.objects.create_user(username='ghost2', password='x', role='STAFF')
        u.soft_delete()

        u.is_active = True
        u.save()

        u.refresh_from_db()
        self.assertFalse(u.is_active)

    def test_TC_USER_01_012_save_invariant_holds_with_narrow_update_fields(self):
        """M1: ``save(update_fields=[...])`` mà danh sách đó KHÔNG có 'is_active'
        (vd ``save(update_fields=['is_deleted'])``) trước đây vẫn gán
        ``self.is_active = False`` trong bộ nhớ nhưng Django chỉ UPDATE đúng cột
        trong update_fields nên DB không thực sự đổi is_active — save() phải tự
        thêm 'is_active' vào update_fields khi ép field này."""
        u = User.objects.create_user(username='ghost3', password='x', role='STAFF', is_active=True)

        u.is_deleted = True
        u.save(update_fields=['is_deleted'])

        u.refresh_from_db()
        self.assertTrue(u.is_deleted)
        self.assertFalse(u.is_active)

    def test_TC_USER_01_012b_update_rejects_is_manager_without_department(self):
        """M6: mirror test create — sửa user để tick is_manager mà không chọn
        department cũng phải bị chặn."""
        u = User.objects.create_user(username='mgr_target', password='x', role='STAFF')

        resp = self.client.post(reverse('user_update', args=[u.pk]), {
            'email': '', 'first_name': '', 'last_name': '', 'role': 'STAFF',
            'is_active': 'on', 'is_manager': 'on', 'department': ''})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Phải chọn phòng ban khi đánh dấu là quản lý phòng ban.')
        u.refresh_from_db()
        self.assertFalse(u.is_manager)

    def test_TC_USER_01_013_cannot_edit_permissions_of_deleted_user(self):
        """M5: user_permission_edit không được kiểm tra is_deleted — có thể mở
        trang này cho user đã xoá mềm để cấp/thu hồi quyền của một tài khoản
        không còn dùng được, mirror guard đã có ở user_update/user_password_set."""
        u = User.objects.create_user(username='ghost4', password='x', role='STAFF')
        u.soft_delete()
        before_perms = set(u.user_permissions.values_list('codename', flat=True))

        get_resp = self.client.get(reverse('user_permission_edit', args=[u.pk]))
        post_resp = self.client.post(
            reverse('user_permission_edit', args=[u.pk]),
            {'perm': ['can_read_grn']})

        self.assertEqual(get_resp.status_code, 302)
        self.assertEqual(get_resp.url, reverse('user_detail', args=[u.pk]))
        self.assertEqual(post_resp.status_code, 302)
        self.assertEqual(post_resp.url, reverse('user_detail', args=[u.pk]))
        self.assertEqual(
            set(u.user_permissions.values_list('codename', flat=True)), before_perms)
        self.assertFalse(
            AuditLog.objects.filter(action='UPDATE', target_id=str(u.pk)).exists())


class PasswordChangeTest(TestCase):
    """Đổi mật khẩu: tự phục vụ (mọi user) + admin đặt lại cho user khác.

    Nhánh dễ sai:
    - Tự đổi mật khẩu phải giữ nguyên session (không tự đăng xuất) — cần
      ``update_session_auth_hash``.
    - Sai mật khẩu hiện tại -> báo lỗi, mật khẩu KHÔNG đổi.
    - Admin đặt mật khẩu cho user khác không cần mật khẩu cũ.
    - Admin không tự đặt mật khẩu cho chính mình qua route admin (phải dùng tự đổi).
    - Chỉ Admin được vào route đặt mật khẩu cho user khác; ẩn danh -> redirect login.
    """

    OLD_PASSWORD = 'old-pass-123'
    NEW_PASSWORD = 'brand-new-pass-456'

    def setUp(self):
        sync_roles()
        self.admin = User.objects.create_user(
            username='pwadmin', password='admin-pass-123', role='ADMIN')
        self.staff = User.objects.create_user(
            username='pwstaff', password=self.OLD_PASSWORD, role='STAFF')

    # --- Tự đổi mật khẩu (mọi user) ---

    def test_self_change_success_keeps_session_and_audits(self):
        self.client.force_login(self.staff)

        resp = self.client.post(reverse('password_change'), {
            'old_password': self.OLD_PASSWORD,
            'new_password1': self.NEW_PASSWORD,
            'new_password2': self.NEW_PASSWORD,
        })

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('dashboard'))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(self.NEW_PASSWORD))
        # Vẫn đang đăng nhập (update_session_auth_hash chống session bị invalidate
        # do đổi password hash — session_key có thể đổi/cycle, nhưng KHÔNG bị đăng xuất).
        self.assertIn('_auth_user_id', self.client.session)
        dash_resp = self.client.get(reverse('dashboard'))
        self.assertEqual(dash_resp.status_code, 200)  # vẫn còn phiên hợp lệ, không bị đá về login

        entry = AuditLog.objects.filter(action='UPDATE', target_id=str(self.staff.pk)).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.staff)  # tự thực hiện

    def test_self_change_wrong_old_password_rejected(self):
        self.client.force_login(self.staff)
        resp = self.client.post(reverse('password_change'), {
            'old_password': 'sai-mat-khau',
            'new_password1': self.NEW_PASSWORD,
            'new_password2': self.NEW_PASSWORD,
        })

        self.assertEqual(resp.status_code, 200)  # render lại form với lỗi
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(self.OLD_PASSWORD))  # chưa đổi

    def test_self_change_requires_login(self):
        resp = self.client.get(reverse('password_change'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('login'), resp.url)

    # --- Admin đặt mật khẩu cho user khác ---

    def test_admin_sets_password_for_other_user(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse('user_password_set', args=[self.staff.pk]),
            {'new_password1': self.NEW_PASSWORD, 'new_password2': self.NEW_PASSWORD},
        )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('user_detail', args=[self.staff.pk]))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password(self.NEW_PASSWORD))

        entry = AuditLog.objects.filter(action='UPDATE', target_id=str(self.staff.pk)).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.admin)  # admin là người thực hiện, không phải staff

    def test_admin_cannot_use_admin_route_on_self(self):
        self.client.force_login(self.admin)
        old_hash = self.admin.password
        resp = self.client.post(
            reverse('user_password_set', args=[self.admin.pk]),
            {'new_password1': self.NEW_PASSWORD, 'new_password2': self.NEW_PASSWORD},
        )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('user_detail', args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.password, old_hash)  # không đổi

    def test_admin_cannot_set_password_of_deleted_user(self):
        self.staff.soft_delete()
        self.client.force_login(self.admin)
        old_hash = self.staff.password
        resp = self.client.post(
            reverse('user_password_set', args=[self.staff.pk]),
            {'new_password1': self.NEW_PASSWORD, 'new_password2': self.NEW_PASSWORD},
        )

        self.assertEqual(resp.status_code, 302)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.password, old_hash)

    def test_non_admin_forbidden_from_setting_others_password(self):
        other = User.objects.create_user(username='pwother', password='x', role='STAFF')
        self.client.force_login(other)
        resp = self.client.get(reverse('user_password_set', args=[self.staff.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse('user_password_set', args=[self.staff.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('login'), resp.url)


class UserPermissionOverrideTest(TestCase):
    """Phân quyền chi tiết cho từng user (mở rộng FR-USER-04): admin ghi đè ma
    trận mặc định của role — có thể THU HỒI (không chỉ thêm) một quyền cụ thể.

    Nhánh dễ sai:
    - Thu hồi quyền phải có hiệu lực thật (không bị Group của role "cứu" lại
      qua union — xem accounts/backends.py::DirectPermissionsBackend).
    - Sửa field khác (không đổi role) KHÔNG được xoá mất phân quyền đã tuỳ chỉnh.
    - Đổi role THÌ reset về đúng mặc định role mới (khớp hành vi cũ đã test ở
      RBACMatrixTest.test_TC_USER_02_003).
    - Chỉ Admin được vào trang phân quyền.
    """

    PASSWORD = 'admin-pass-123'

    def setUp(self):
        sync_roles()
        self.admin = User.objects.create_user(
            username='boss2', password=self.PASSWORD, role='ADMIN')
        self.client.force_login(self.admin)
        self.staff = User.objects.create_user(
            username='staffer', password='x', role='STAFF')

    def test_revoke_a_role_granted_permission_takes_effect(self):
        self.assertTrue(self.staff.can('update', 'grn'))  # STAFF mặc định có update grn

        resp = self.client.post(
            reverse('user_permission_edit', args=[self.staff.pk]),
            {'perm': ['can_create_grn', 'can_read_grn']},  # bỏ can_update_grn
        )
        self.assertEqual(resp.status_code, 302)
        # Nạp lại instance MỚI (không dùng refresh_from_db) để xoá cache quyền
        # Django giữ trên instance cũ — giống pattern ở RBACMatrixTest.
        staff = User.objects.get(pk=self.staff.pk)
        self.assertFalse(staff.can('update', 'grn'))  # đã bị thu hồi
        self.assertTrue(staff.can('create', 'grn'))   # các quyền khác vẫn còn

        entry = AuditLog.objects.filter(
            action='UPDATE', target_id=str(self.staff.pk)).first()
        self.assertIn('can_update_grn', entry.changes.get('revoked', []))

    def test_revoking_one_user_does_not_affect_another_same_role_user(self):
        other_staff = User.objects.create_user(username='staffer2', password='x', role='STAFF')
        self.client.post(
            reverse('user_permission_edit', args=[self.staff.pk]),
            {'perm': ['can_create_grn', 'can_read_grn']},
        )
        other_staff = User.objects.get(pk=other_staff.pk)
        self.assertTrue(other_staff.can('update', 'grn'))  # user khác không bị ảnh hưởng

    def test_grant_extra_permission_beyond_role(self):
        self.assertFalse(self.staff.can('approve', 'reports'))  # STAFF mặc định không có

        codenames = [f'can_{a}_{m}' for m in MODULES for a in ACTIONS
                     if self.staff.can(a, m)]
        codenames.append('can_approve_reports')
        self.client.post(
            reverse('user_permission_edit', args=[self.staff.pk]), {'perm': codenames})

        staff = User.objects.get(pk=self.staff.pk)
        self.assertTrue(staff.can('approve', 'reports'))  # đã được cấp thêm

    def test_reset_restores_role_defaults(self):
        self.client.post(
            reverse('user_permission_edit', args=[self.staff.pk]),
            {'perm': ['can_create_grn']},  # thu hẹp rất nhiều so với mặc định STAFF
        )
        staff = User.objects.get(pk=self.staff.pk)
        self.assertFalse(staff.can('update', 'grn'))

        self.client.post(
            reverse('user_permission_edit', args=[self.staff.pk]), {'reset': '1'})
        staff = User.objects.get(pk=self.staff.pk)
        self.assertTrue(staff.can('update', 'grn'))   # về lại đúng mặc định STAFF
        self.assertTrue(staff.can('create', 'grn'))

    def test_editing_other_field_preserves_customization(self):
        self.client.post(
            reverse('user_permission_edit', args=[self.staff.pk]),
            {'perm': ['can_create_grn', 'can_read_grn']},  # thu hồi update
        )
        # Sửa email qua user_update — KHÔNG đổi role.
        self.client.post(reverse('user_update', args=[self.staff.pk]), {
            'email': 'staffer@example.com', 'first_name': '', 'last_name': '',
            'role': 'STAFF', 'is_active': 'on'})

        staff = User.objects.get(pk=self.staff.pk)
        self.assertEqual(staff.email, 'staffer@example.com')
        self.assertFalse(staff.can('update', 'grn'))  # tuỳ chỉnh vẫn giữ nguyên

    def test_changing_role_resets_customization_to_new_role_defaults(self):
        self.client.post(
            reverse('user_permission_edit', args=[self.staff.pk]),
            {'perm': ['can_create_grn', 'can_read_grn']},
        )
        self.client.post(reverse('user_update', args=[self.staff.pk]), {
            'email': '', 'first_name': '', 'last_name': '',
            'role': 'QC', 'is_active': 'on'})

        staff = User.objects.get(pk=self.staff.pk)
        self.assertTrue(staff.can('create', 'qc'))    # đúng mặc định QC mới
        self.assertFalse(staff.can('create', 'grn'))  # không còn dính tuỳ chỉnh cũ

    def test_non_admin_forbidden(self):
        qc = User.objects.create_user(username='qc1', password='x', role='QC')
        self.client.force_login(qc)
        resp = self.client.get(reverse('user_permission_edit', args=[self.staff.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_menu_access_default_granted_and_revocable(self):
        """Khối "Truy cập menu" (MENU_ITEMS) mặc định cấp cho mọi role — admin thu hồi
        riêng 1 mục (vd ``warehouse``) của 1 user phải có hiệu lực, không đụng các mục
        khác/không đụng ma trận CRUD."""
        self.assertTrue(self.staff.can_view_menu('warehouse'))  # mặc định luôn cấp

        codenames = [c for c in all_permission_codenames() if self.staff.has_perm(f'accounts.{c}')]
        codenames.remove('can_view_menu_warehouse')
        self.client.post(
            reverse('user_permission_edit', args=[self.staff.pk]), {'perm': codenames})

        staff = User.objects.get(pk=self.staff.pk)
        self.assertFalse(staff.can_view_menu('warehouse'))
        self.assertTrue(staff.can_view_menu('catalog'))  # mục khác không bị ảnh hưởng
        self.assertTrue(staff.can('update', 'grn'))       # ma trận CRUD không bị ảnh hưởng

    def test_admin_cannot_self_revoke_user_mgmt_menu_access(self):
        """Guard tự khoá: admin không được tự bỏ tick "Quản lý user" của chính mình qua
        trang Phân quyền — nếu không sẽ không còn cách nào tự khôi phục qua UI (mirror
        guard is_active tự khoá ở user_toggle_active/user_update)."""
        codenames = [c for c in all_permission_codenames() if self.admin.has_perm(f'accounts.{c}')]
        codenames.remove('can_view_menu_user_mgmt')
        resp = self.client.post(
            reverse('user_permission_edit', args=[self.admin.pk]), {'perm': codenames})
        self.assertEqual(resp.status_code, 200)  # render lại form, không redirect

        admin = User.objects.get(pk=self.admin.pk)
        self.assertTrue(admin.can_view_menu('user_mgmt'))  # vẫn giữ nguyên, chưa bị thu hồi


class UserListPaginationFilterTest(TestCase):
    """Phân trang + bộ lọc (role/status/tìm kiếm) trên user_list."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username='boss', password='admin-pass-123', role='ADMIN')
        self.client.force_login(self.admin)
        User.objects.bulk_create([
            User(
                username=f'user{i:04d}', email=f'user{i:04d}@example.com',
                role=User.Role.STAFF if i % 2 == 0 else User.Role.MANAGER,
                is_active=(i % 2 == 0),
            )
            for i in range(1, 36)
        ])

    def test_default_page_size_30(self):
        response = self.client.get(reverse('user_list'))
        self.assertEqual(len(response.context['users']), 30)

    def test_page_size_50_shows_all(self):
        response = self.client.get(reverse('user_list'), {'page_size': 50})
        self.assertEqual(len(response.context['users']), 36)  # 35 seed + self.admin

    def test_filter_role(self):
        response = self.client.get(
            reverse('user_list'), {'role': User.Role.MANAGER, 'page_size': 50})
        self.assertTrue(all(u.role == User.Role.MANAGER for u in response.context['users']))

    def test_filter_status_inactive(self):
        response = self.client.get(
            reverse('user_list'), {'status': 'inactive', 'page_size': 50})
        self.assertTrue(all(not u.is_active for u in response.context['users']))

    def test_deleted_user_hidden_from_default_list(self):
        deleted = User.objects.get(username='user0001')
        deleted.soft_delete()

        response = self.client.get(reverse('user_list'), {'page_size': 50})
        self.assertNotIn(deleted.pk, [u.pk for u in response.context['users']])

    def test_deleted_user_visible_with_status_deleted_filter(self):
        deleted = User.objects.get(username='user0001')
        deleted.soft_delete()

        response = self.client.get(
            reverse('user_list'), {'status': 'deleted', 'page_size': 50})
        self.assertEqual([u.pk for u in response.context['users']], [deleted.pk])

    def test_filter_search_by_username(self):
        response = self.client.get(reverse('user_list'), {'q': 'user0001'})
        usernames = [u.username for u in response.context['users']]
        self.assertEqual(usernames, ['user0001'])


class PaginationHelperTest(TestCase):
    """``paginate_queryset`` (hạ tầng phân trang dùng chung, áp dụng mọi list view)."""

    def setUp(self):
        self.factory = RequestFactory()
        # 45 user (STAFF) đủ cho page_size 30 tách thành 2 trang.
        User.objects.bulk_create([
            User(username=f'pgtest{i:03d}', role='STAFF') for i in range(45)
        ])
        self.queryset = User.objects.filter(username__startswith='pgtest').order_by('username')

    def test_default_page_size_when_missing(self):
        request = self.factory.get('/')
        page_obj, page_size = paginate_queryset(request, self.queryset)
        self.assertEqual(page_size, DEFAULT_PAGE_SIZE)
        self.assertEqual(len(page_obj.object_list), 30)

    def test_invalid_page_size_falls_back_to_default(self):
        request = self.factory.get('/', {'page_size': 'abc'})
        page_obj, page_size = paginate_queryset(request, self.queryset)
        self.assertEqual(page_size, DEFAULT_PAGE_SIZE)

    def test_page_size_outside_allowed_options_falls_back_to_default(self):
        request = self.factory.get('/', {'page_size': '999'})
        page_obj, page_size = paginate_queryset(request, self.queryset)
        self.assertEqual(page_size, DEFAULT_PAGE_SIZE)

    def test_allowed_page_size_is_respected(self):
        request = self.factory.get('/', {'page_size': '40'})
        page_obj, page_size = paginate_queryset(request, self.queryset)
        self.assertEqual(page_size, 40)
        self.assertEqual(len(page_obj.object_list), 40)

    def test_page_beyond_range_returns_last_page(self):
        request = self.factory.get('/', {'page_size': '30', 'page': '999'})
        page_obj, _ = paginate_queryset(request, self.queryset)
        self.assertEqual(page_obj.number, page_obj.paginator.num_pages)

    def test_non_numeric_page_returns_first_page(self):
        request = self.factory.get('/', {'page': 'abc'})
        page_obj, _ = paginate_queryset(request, self.queryset)
        self.assertEqual(page_obj.number, 1)

    def test_elided_page_range_attached_to_page_obj(self):
        request = self.factory.get('/', {'page_size': '30'})
        page_obj, _ = paginate_queryset(request, self.queryset)
        self.assertEqual(list(page_obj.elided_page_range), list(page_obj.paginator.get_elided_page_range(1)))


class ApprovalUniqueConstraintTest(TestCase):
    """H4: 2 Approval PENDING không được cùng trỏ tới 1 target — trước đây
    create_approval() chỉ chặn trùng bằng .exists() rồi mới .create(), có race
    window giữa 2 request đồng thời (cùng qua được .exists() trước khi request nào
    kịp insert). Giờ có UniqueConstraint (unique_pending_approval_per_target) ở DB
    làm chốt chặn thật; create_approval() dịch IntegrityError từ đó thành
    ValidationError quen thuộc thay vì để lộ 500."""

    def setUp(self):
        sync_roles()
        self.staff = User.objects.create_user(username='u_staff', password='x', role='STAFF')
        self.target = User.objects.create_user(username='u_target', password='x', role='STAFF')
        self.content_type = ContentType.objects.get_for_model(User)

    def test_TC_APPROVAL_001_db_constraint_rejects_duplicate_pending_direct_create(self):
        """Chốt chặn thật ở DB, độc lập với create_approval() — kể cả gọi thẳng
        .create() (bypass service) cũng phải bị chặn."""
        Approval.objects.create(
            target_type=self.content_type, target_id=str(self.target.pk),
            department='WAREHOUSE', action_label='Nộp GRN X', submitted_by=self.staff,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Approval.objects.create(
                    target_type=self.content_type, target_id=str(self.target.pk),
                    department='WAREHOUSE', action_label='Nộp GRN Y', submitted_by=self.staff,
                )

    def test_TC_APPROVAL_002_create_approval_translates_race_into_validation_error(self):
        """Giả lập đúng race: mock .exists() (check nhanh) trả False dù thực tế đã
        có 1 PENDING approval khác — mô phỏng 2 request đồng thời cùng qua được check
        trước khi request nào kịp insert (không dựng thread thật được, cùng cách mock
        đã dùng cho test collision của so_no — xem CLAUDE.md). create_approval() phải
        bắt IntegrityError từ UniqueConstraint và raise lại ValidationError."""
        create_approval(self.target, 'WAREHOUSE', 'Nộp GRN X', self.staff)

        mock_qs = MagicMock()
        mock_qs.exists.return_value = False
        with patch('accounts.approvals.Approval.objects.filter', return_value=mock_qs):
            with self.assertRaises(ValidationError):
                create_approval(self.target, 'WAREHOUSE', 'Nộp GRN X (trùng)', self.staff)

        pending_count = Approval.objects.filter(
            target_type=self.content_type, target_id=str(self.target.pk),
            status=Approval.Status.PENDING,
        ).count()
        self.assertEqual(pending_count, 1)

    def test_TC_APPROVAL_003_normal_duplicate_blocked_by_exists_check(self):
        """Trường hợp thường (không race): .exists() tự chặn ngay bằng
        ValidationError, không cần đụng tới DB constraint."""
        create_approval(self.target, 'WAREHOUSE', 'Nộp GRN X', self.staff)
        with self.assertRaises(ValidationError):
            create_approval(self.target, 'WAREHOUSE', 'Nộp GRN X (trùng)', self.staff)


class AuditLogListViewTest(TestCase):
    """H5: query param xấu (?module=abc, ?actor=abc, ?date_from=abc, ?date_to=abc)
    không được làm trang tra cứu audit log crash 500 — trước đây filter thẳng giá
    trị GET thô vào FK/DateField nên int()/parse ngày lỗi bay thẳng thành 500.
    Fix: parse an toàn, filter sai thì bỏ qua (coi như không lọc)."""

    def setUp(self):
        sync_roles()
        cache.clear()  # module_choices/actor_choices cache dùng chung LocMemCache
        self.admin = User.objects.create_user(
            username='u_admin', password='x', role='ADMIN', is_superuser=True,
        )
        self.client.force_login(self.admin)
        self.url = reverse('audit_log_list')

    def test_TC_AUDITLOG_001_invalid_module_param_does_not_crash(self):
        response = self.client.get(self.url, {'module': 'abc'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_module'], '')

    def test_TC_AUDITLOG_002_invalid_actor_param_does_not_crash(self):
        response = self.client.get(self.url, {'actor': 'abc'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_actor'], '')

    def test_TC_AUDITLOG_003_invalid_date_from_param_does_not_crash(self):
        response = self.client.get(self.url, {'date_from': 'not-a-date'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['date_from'], '')

    def test_TC_AUDITLOG_004_invalid_date_to_param_does_not_crash(self):
        response = self.client.get(self.url, {'date_to': '2026-13-99'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['date_to'], '')

    def test_TC_AUDITLOG_005_valid_params_still_filter_correctly(self):
        target = User.objects.create_user(username='u_target', password='x', role='STAFF')
        entry = log_action(self.admin, AuditLog.Action.UPDATE, target=target,
                           description='Test entry cho valid filter')
        content_type = ContentType.objects.get_for_model(User)

        response = self.client.get(self.url, {
            'module': content_type.pk, 'actor': self.admin.pk, 'date_from': '2020-01-01',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_module'], str(content_type.pk))
        self.assertEqual(response.context['selected_actor'], str(self.admin.pk))
        self.assertIn(entry, list(response.context['logs']))

    def test_TC_AUDITLOG_006_action_column_shows_vietnamese_label(self):
        """M10: cột "Hành động" phải hiện nhãn tiếng Việt (Tạo mới/Cập nhật/...),
        không phải mã tiếng Anh thô (CREATE/UPDATE/...) — trước đây field
        ``action`` thiếu ``choices=`` nên ``get_action_display()`` không tồn
        tại, template rơi vào fallback hiện mã thô."""
        target = User.objects.create_user(username='u_target2', password='x', role='STAFF')
        entry = log_action(self.admin, AuditLog.Action.CREATE, target=target,
                           description='Test entry cho nhãn hành động')

        self.assertEqual(entry.get_action_display(), 'Tạo mới')

        response = self.client.get(self.url)
        self.assertContains(response, 'Tạo mới')
        self.assertNotContains(response, '>CREATE<')


class NotificationMarkReadTest(TestCase):
    """M7: đánh dấu đã đọc phải là POST (có CSRF), không còn side-effect qua GET."""

    def setUp(self):
        sync_roles()
        self.user = User.objects.create_user(username='u_notif', password='x', role='STAFF')
        self.client.force_login(self.user)
        self.notif = Notification.objects.create(recipient=self.user, verb='Có phiếu mới cần xử lý')

    def test_TC_NOTIF_001_get_does_not_mark_read(self):
        """M7: GET không còn side-effect — trước đây <a href> GET là đủ để
        mark-read, dễ bị prefetch/bot click nhầm hàng loạt."""
        resp = self.client.get(reverse('notification_mark_read', args=[self.notif.pk]))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('notification_list'))
        self.notif.refresh_from_db()
        self.assertFalse(self.notif.is_read)

    def test_TC_NOTIF_002_post_marks_read_and_redirects_to_list_without_target(self):
        resp = self.client.post(reverse('notification_mark_read', args=[self.notif.pk]))

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('notification_list'))
        self.notif.refresh_from_db()
        self.assertTrue(self.notif.is_read)

    def test_TC_NOTIF_003_cannot_mark_read_another_users_notification(self):
        other = User.objects.create_user(username='u_other', password='x', role='STAFF')
        theirs = Notification.objects.create(recipient=other, verb='Không phải của bạn')

        resp = self.client.post(reverse('notification_mark_read', args=[theirs.pk]))

        self.assertEqual(resp.status_code, 404)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)

    def test_TC_NOTIF_004_mark_all_read_ignores_external_next(self):
        """M8: ``next`` trỏ ra domain ngoài phải bị bỏ qua (open redirect), chỉ
        chấp nhận URL nội bộ."""
        resp = self.client.post(
            reverse('notification_mark_all_read'), {'next': 'https://evil.example.com/phish'})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('notification_list'))

    def test_TC_NOTIF_005_mark_all_read_accepts_internal_next(self):
        internal_url = reverse('dashboard')
        resp = self.client.post(reverse('notification_mark_all_read'), {'next': internal_url})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, internal_url)

    def test_TC_NOTIF_006_mark_all_read_marks_everything_unread_as_read(self):
        Notification.objects.create(recipient=self.user, verb='Thông báo 2')

        resp = self.client.post(reverse('notification_mark_all_read'))

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            Notification.objects.filter(recipient=self.user, is_read=False).exists())
