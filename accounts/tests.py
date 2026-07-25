"""Test RBAC cho app accounts (FR-USER-02 RBAC, FR-USER-04 permission matrix).

Tập trung vào các nhánh dễ sai:
- Đồng bộ user.role -> Group membership (kể cả khi ĐỔI role).
- Cột Approve (không có trong bảng gốc, chốt theo "Theo nghiệp vụ"): ai được / không
  được approve — điểm dễ gán nhầm nhất.
- Các ô "full CRUD nhưng KHÔNG approve" (Purchasing/PO, Accountant/Reports).
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.audit import log_action
from accounts.models import AuditLog
from accounts.pagination import DEFAULT_PAGE_SIZE, paginate_queryset
from accounts.permissions import ACTIONS, MODULES
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

    def test_TC_USER_03_005_login_page_renders(self):
        resp = self.client.get(reverse('login'))

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'registration/login.html')

    def test_TC_USER_03_006_dashboard_requires_login(self):
        resp = self.client.get(reverse('dashboard'))

        self.assertEqual(resp.status_code, 302)             # @login_required chặn
        self.assertIn(reverse('login'), resp.url)


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

    def test_TC_USER_01_003_create_generates_temp_password_emails_and_audits(self):
        resp = self.client.post(reverse('user_create'), {
            'username': 'newbie', 'email': 'newbie@example.com',
            'first_name': 'New', 'last_name': 'Bie', 'role': 'STAFF'})

        created = User.objects.get(username='newbie')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('user_detail', args=[created.pk]))
        self.assertTrue(created.has_usable_password())  # có mật khẩu tạm dùng được
        self.assertTrue(created.is_active)
        self.assertEqual(                                # signal đồng bộ role -> Group
            list(created.groups.values_list('name', flat=True)), ['STAFF'])
        self.assertEqual(len(mail.outbox), 1)            # đã gửi email mật khẩu tạm
        self.assertIn('newbie@example.com', mail.outbox[0].to)

        entry = AuditLog.objects.filter(action='CREATE').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.admin)        # WHO = admin đang đăng nhập
        self.assertEqual(entry.target, created)          # WHAT trên đối tượng nào
        self.assertEqual(entry.ip_address, '127.0.0.1')  # IP client

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
