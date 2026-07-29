"""View app accounts: dashboard + CRUD user (FR-USER-01).

Phân quyền quản lý user: bảng Permission Matrix (BACKLOG mục 1a) KHÔNG có cột
"Users", và yêu cầu CRUD ghi rõ "admin tạo user" — nên quản lý user là việc của
**Admin** (role ADMIN hoặc Django superuser), không đi qua ``user.can(action, module)``
(vốn chỉ dành cho các module nghiệp vụ grn/gin/qc/...).

Mọi thao tác ghi/đổi trạng thái đều ghi audit qua ``log_action`` (FR-USER-05) với
actor = người đang đăng nhập + IP client.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme

from django.contrib.contenttypes.models import ContentType

from .audit import client_ip, log_action
from .forms import UserCreateForm, UserUpdateForm, WmsPasswordChangeForm, WmsSetPasswordForm
from .models import AuditLog, Notification
from .pagination import paginate_queryset
from .permissions import (
    ACTIONS, MENU_ITEMS, MODULES, all_permission_codenames, can_view_audit_log, codenames_for_role,
)
from .rbac import sync_user_permissions

User = get_user_model()

# Các field được theo dõi để ghi before/after khi UPDATE (audit "what changed").
TRACKED_FIELDS = ['role', 'department', 'is_manager', 'is_active', 'email', 'first_name', 'last_name']


@login_required
def dashboard(request):
    """Trang chủ sau đăng nhập (LOGIN_REDIRECT_URL).

    Tối thiểu ở Phase 1: xác nhận đã đăng nhập + hiển thị vai trò. Nội dung dashboard
    thật (cảnh báo tồn kho...) sẽ bổ sung khi làm warehouse ở mục 1b.
    """
    return render(request, 'dashboard.html')


@login_required
def password_change(request):
    """Tự đổi mật khẩu của chính mình — mọi user đã đăng nhập đều dùng được, không
    riêng admin. Cần ``update_session_auth_hash`` để không tự đăng xuất sau khi đổi."""
    form = WmsPasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        log_action(
            request.user, AuditLog.Action.UPDATE, target=user,
            description=f'{user.username} tự đổi mật khẩu',
            ip_address=client_ip(request),
        )
        messages.success(request, 'Đã đổi mật khẩu thành công.')
        return redirect('dashboard')
    return render(request, 'accounts/password_change_form.html', {'form': form})


# --- Phân quyền quản lý user (Admin-only) ---

def can_manage_users(user):
    """Chỉ Admin (role ADMIN) hoặc Django superuser được quản lý user."""
    return user.is_superuser or user.role == User.Role.ADMIN


def user_admin_required(view):
    """Decorator: chưa đăng nhập -> về login; đã đăng nhập nhưng không phải admin -> 403;
    admin nhưng bị thu hồi quyền "Truy cập menu" ``user_mgmt`` (trang Phân quyền chi tiết,
    xem ``MENU_ITEMS``) -> 403."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not can_manage_users(request.user):
            raise PermissionDenied('Chỉ Admin được quản lý người dùng.')
        if not request.user.can_view_menu('user_mgmt'):
            raise PermissionDenied('Bạn không có quyền truy cập mục "Quản lý user".')
        return view(request, *args, **kwargs)

    return wrapper


def _send_account_created_email(user):
    """Gửi email thông báo tài khoản mới cho user (FR-USER-01 CREATE).

    M3: KHÔNG gửi mật khẩu (plaintext) qua email — admin là người trực tiếp đặt
    mật khẩu ban đầu (``UserCreateForm``) và báo trực tiếp cho user (không có
    luồng "quên mật khẩu" tự phục vụ trong hệ thống); email chỉ báo tài khoản đã
    được tạo. Nếu user quên mật khẩu, admin đặt lại qua ``user_password_set``
    (nút "Đặt mật khẩu" ở trang chi tiết user), không phải qua email.

    Dev dùng backend console (in ra terminal); test tự dùng locmem (mail.outbox).
    ``fail_silently=True`` để việc tạo user không đổ vỡ khi chưa cấu hình SMTP thật.
    """
    if not user.email:
        return
    send_mail(
        subject='[NVL/WMS] Tài khoản mới của bạn',
        message=(
            f'Xin chào {user.username},\n\n'
            f'Tài khoản NVL/WMS của bạn đã được tạo.\n'
            f'Tên đăng nhập: {user.username}\n\n'
            f'Mật khẩu đăng nhập đã được quản trị viên cấp cho bạn qua kênh riêng '
            f'(không gửi qua email). Nếu quên mật khẩu, vui lòng liên hệ quản trị '
            f'viên để được đặt lại.'
        ),
        from_email=None,  # dùng DEFAULT_FROM_EMAIL
        recipient_list=[user.email],
        fail_silently=True,
    )


@user_admin_required
def user_list(request):
    """READ — danh sách user. Mặc định ẩn user đã soft-delete (chỉ hiện khi lọc
    status=deleted) để danh sách không bị "rác" bởi tài khoản đã xoá."""
    users = User.objects.order_by('username')
    role = request.GET.get('role', '')
    if role:
        users = users.filter(role=role)
    status = request.GET.get('status', '')
    if status == 'active':
        users = users.filter(is_active=True, is_deleted=False)
    elif status == 'inactive':
        users = users.filter(is_active=False, is_deleted=False)
    elif status == 'deleted':
        users = users.filter(is_deleted=True)
    else:
        users = users.filter(is_deleted=False)
    q = request.GET.get('q', '').strip()
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q))
    page_obj, page_size = paginate_queryset(request, users)
    return render(request, 'accounts/user_list.html', {
        'users': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'roles': User.Role.choices, 'selected_role': role,
        'selected_status': status, 'q': q,
    })


def _effective_codenames(user):
    """Tập codename ``can_<action>_<module>`` mà user hiện có (quyền trực tiếp,
    không cộng dồn từ Group — xem accounts/backends.py)."""
    return set(
        user.user_permissions.filter(content_type__app_label='accounts')
        .values_list('codename', flat=True)
    )


@user_admin_required
def user_detail(request, pk):
    """READ — chi tiết user + ma trận quyền hạn hiệu lực (FR-USER-01: "quyền hạn")."""
    obj = get_object_or_404(User, pk=pk)
    # Cột đúng thứ tự ACTIONS; mỗi hàng là 1 module với danh sách bool can/không.
    action_labels = list(ACTIONS.values())
    perm_rows = [
        {'module': label,
         'cells': [obj.can(action, module) for action in ACTIONS]}
        for module, label in MODULES.items()
    ]
    is_customized = _effective_codenames(obj) != set(codenames_for_role(obj.role))
    return render(request, 'accounts/user_detail.html', {
        'obj': obj, 'action_labels': action_labels, 'perm_rows': perm_rows,
        'is_customized': is_customized,
    })


@user_admin_required
def user_permission_edit(request, pk):
    """UPDATE — phân quyền chi tiết cho từng user: admin tick/bỏ tick từng ô
    Module x Action, cộng khối "Ứng dụng được phép truy cập" (``MENU_ITEMS`` — các mục
    sidebar không có ma trận CRUD). Có thể THU HỒI quyền mặc định của role (không chỉ
    thêm), hoặc bấm "Đặt lại theo vai trò" để quay về đúng mặc định của role.
    """
    obj = get_object_or_404(User, pk=pk)
    if obj.is_deleted:
        messages.error(request, 'User đã bị xoá, không thể sửa phân quyền.')
        return redirect('user_detail', pk=obj.pk)
    all_codenames = all_permission_codenames()

    if request.method == 'POST':
        before = _effective_codenames(obj)
        if 'reset' in request.POST:
            sync_user_permissions(obj)
            after = set(codenames_for_role(obj.role))
            description = f'Đặt lại phân quyền của {obj.username} theo mặc định vai trò'
        else:
            selected = set(request.POST.getlist('perm')) & set(all_codenames)
            # Tự khoá: admin đang tự sửa quyền của chính mình mà bỏ tick menu "Quản lý
            # user" sẽ không còn cách nào quay lại trang này qua UI để tự khôi phục (mirror
            # guard is_active tự khoá ở user_toggle_active/user_update) — chặn trước khi lưu.
            if obj.pk == request.user.pk and 'can_view_menu_user_mgmt' not in selected:
                messages.error(
                    request, 'Không thể tự thu hồi quyền truy cập "Quản lý user" của chính bạn.')
                return _render_permission_form(request, obj)
            perms = Permission.objects.filter(
                content_type__app_label='accounts', codename__in=selected,
            )
            obj.user_permissions.set(perms)
            after = selected
            description = f'Cập nhật phân quyền chi tiết của {obj.username}'
        changes = {}
        added = sorted(after - before)
        removed = sorted(before - after)
        if added:
            changes['granted'] = added
        if removed:
            changes['revoked'] = removed
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=description, changes=changes or None,
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã cập nhật phân quyền của "{obj.username}".')
        return redirect('user_detail', pk=obj.pk)

    return _render_permission_form(request, obj)


def _render_permission_form(request, obj):
    """Dựng context cho form Phân quyền chi tiết — dùng chung cho GET và khi POST bị
    chặn bởi guard tự khoá (xem ``user_permission_edit``)."""
    action_labels = list(ACTIONS.values())
    effective = _effective_codenames(obj)
    perm_rows = [
        {'module_label': module_label,
         'cells': [
             {'codename': f'can_{action}_{module}', 'checked': f'can_{action}_{module}' in effective}
             for action in ACTIONS
         ]}
        for module, module_label in MODULES.items()
    ]
    menu_rows = [
        {'codename': f'can_view_menu_{key}', 'label': label,
         'checked': f'can_view_menu_{key}' in effective}
        for key, label in MENU_ITEMS.items()
    ]
    return render(request, 'accounts/user_permission_form.html', {
        'obj': obj, 'action_labels': action_labels, 'perm_rows': perm_rows, 'menu_rows': menu_rows,
    })


@user_admin_required
def user_create(request):
    """CREATE — admin tạo user với mật khẩu tự chọn; gửi email + ghi audit CREATE."""
    form = UserCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()  # kích hoạt signal đồng bộ role -> Group
        _send_account_created_email(user)
        log_action(
            request.user, AuditLog.Action.CREATE, target=user,
            description=f'Tạo user {user.username} (role {user.role or "—"})',
            ip_address=client_ip(request),
        )
        messages.success(
            request,
            f'Đã tạo user "{user.username}" (đã gửi email thông báo tới {user.email or "—"}).',
        )
        return redirect('user_detail', pk=user.pk)
    return render(request, 'accounts/user_form.html', {'form': form, 'mode': 'create'})


@user_admin_required
def user_update(request, pk):
    """UPDATE — đổi role / deactivate / thông tin; ghi audit UPDATE với changes.

    User đã xoá mềm không cho sửa ở đây (chưa có luồng "khôi phục" riêng — mở
    view này ra sẽ để lộ khả năng bật lại ``is_active`` qua ``UserUpdateForm``
    trong khi ``is_deleted`` vẫn True, bug fix 2026-07-27, xem CLAUDE.md); mirror
    guard đã có ở ``user_password_set``/``user_toggle_active``.
    """
    obj = get_object_or_404(User, pk=pk)
    if obj.is_deleted:
        messages.error(request, 'User đã bị xoá, không thể sửa.')
        return redirect('user_detail', pk=obj.pk)
    before = {f: getattr(obj, f) for f in TRACKED_FIELDS}
    form = UserUpdateForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        if obj.pk == request.user.pk and not form.cleaned_data['is_active']:
            messages.error(request, 'Không thể tự khoá tài khoản của chính bạn.')
            return render(request, 'accounts/user_form.html',
                          {'form': form, 'mode': 'update', 'obj': obj})
        # H3: tương tự guard is_active ở trên — tự đổi role của chính mình có thể
        # tự hạ quyền/khoá luôn quyền quản trị (vd ADMIN tự đổi thành STAFF) mà
        # không còn ai khác có quyền đổi lại, khác với việc đổi role cho user khác.
        if obj.pk == request.user.pk and form.cleaned_data['role'] != before['role']:
            messages.error(request, 'Không thể tự đổi vai trò (role) của chính bạn.')
            return render(request, 'accounts/user_form.html',
                          {'form': form, 'mode': 'update', 'obj': obj})
        user = form.save()  # kích hoạt signal đồng bộ role -> Group nếu đổi role
        changes = {
            f: [before[f], getattr(user, f)]
            for f in TRACKED_FIELDS if before[f] != getattr(user, f)
        }
        log_action(
            request.user, AuditLog.Action.UPDATE, target=user,
            description=f'Cập nhật user {user.username}',
            changes=changes or None,
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã cập nhật user "{user.username}".')
        return redirect('user_detail', pk=user.pk)
    return render(request, 'accounts/user_form.html',
                  {'form': form, 'mode': 'update', 'obj': obj})


@user_admin_required
def user_toggle_active(request, pk):
    """Khoá/Mở khoá nhanh 1 user (POST-only, không cần trang xác nhận) — tương tự
    ``warehouse.location_toggle_active``. Không áp dụng cho chính mình (tự khoá
    sẽ tự đăng xuất khỏi hệ thống) hoặc user đã xoá mềm (đã bị khoá sẵn)."""
    obj = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        if obj.pk == request.user.pk:
            messages.error(request, 'Không thể tự khoá tài khoản của chính bạn.')
        elif obj.is_deleted:
            messages.error(request, 'User đã bị xoá, không thể khoá/mở khoá.')
        else:
            before = obj.is_active
            obj.is_active = not obj.is_active
            obj.save(update_fields=['is_active'])
            log_action(
                request.user, AuditLog.Action.UPDATE, target=obj,
                description=f'{"Mở khoá" if obj.is_active else "Khoá"} user {obj.username}',
                changes={'is_active': [before, obj.is_active]},
                ip_address=client_ip(request),
            )
            messages.success(request, f'Đã {"mở khoá" if obj.is_active else "khoá"} user "{obj.username}".')
    if request.POST.get('next') == 'detail':
        return redirect('user_detail', pk=obj.pk)
    return redirect('user_list')


@user_admin_required
def user_password_set(request, pk):
    """Admin đặt mật khẩu mới cho user khác (không cần mật khẩu cũ). Không áp dụng
    cho chính mình (dùng ``password_change`` tự phục vụ) hay user đã xoá mềm."""
    obj = get_object_or_404(User, pk=pk)
    if obj.pk == request.user.pk:
        messages.error(request, 'Vui lòng dùng chức năng "Đổi mật khẩu" của chính bạn.')
        return redirect('user_detail', pk=obj.pk)
    if obj.is_deleted:
        messages.error(request, 'User đã bị xoá, không thể đặt mật khẩu.')
        return redirect('user_detail', pk=obj.pk)

    form = WmsSetPasswordForm(obj, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj,
            description=f'Đặt lại mật khẩu cho user {obj.username}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã đặt mật khẩu mới cho user "{obj.username}".')
        return redirect('user_detail', pk=obj.pk)
    return render(request, 'accounts/user_password_set_form.html', {'form': form, 'obj': obj})


@user_admin_required
def user_delete(request, pk):
    """DELETE — soft delete (giữ bản ghi cho audit); chặn tự xoá chính mình,
    chặn xoá lại user đã xoá mềm sẵn (L4, 2026-07-28).

    Nút "Xoá" đã bị ẩn ở template cho user ``is_deleted`` (giống Sửa/Đặt mật
    khẩu/Khoá), nhưng view chưa tự kiểm tra — truy cập thẳng URL vẫn gọi lại
    được ``soft_delete()``, ghi đè ``deleted_at`` bằng thời điểm mới và làm
    sai lệch audit trail thời điểm xoá THẬT, không đơn thuần "vô hại vì
    idempotent". Check trước cả nhánh GET (trang xác nhận) lẫn POST, mirror
    guard đã có ở ``user_password_set``.
    """
    obj = get_object_or_404(User, pk=pk)
    if obj.is_deleted:
        messages.error(request, 'User đã bị xoá, không thể xoá lại.')
        return redirect('user_detail', pk=obj.pk)
    if request.method == 'POST':
        if obj.pk == request.user.pk:
            messages.error(request, 'Không thể tự xoá tài khoản của chính bạn.')
            return redirect('user_detail', pk=obj.pk)
        obj.soft_delete()
        log_action(
            request.user, AuditLog.Action.DELETE, target=obj,
            description=f'Xoá mềm user {obj.username}',
            ip_address=client_ip(request),
        )
        messages.success(request, f'Đã xoá (mềm) user "{obj.username}".')
        return redirect('user_list')
    return render(request, 'accounts/user_confirm_delete.html', {'obj': obj})


# --- Thông báo trong app (Phase A nền tảng cho luồng duyệt/bàn giao) ---

@login_required
def notification_list(request):
    """READ — toàn bộ thông báo của user đang đăng nhập, mới nhất trước."""
    qs = Notification.objects.filter(recipient=request.user).select_related('target_type')
    page_obj, page_size = paginate_queryset(request, qs)
    return render(request, 'accounts/notification_list.html', {
        'notifications': page_obj, 'page_obj': page_obj, 'page_size': page_size,
    })


@login_required
def notification_mark_read(request, pk):
    """POST-only: đánh dấu 1 thông báo đã đọc rồi chuyển tới đối tượng liên quan
    (nếu có ``get_absolute_url``) hoặc quay lại danh sách thông báo.

    M7: trước đây là GET-only (đi qua thẳng ``<a href>``) — đánh dấu-đã-đọc là 1
    side-effect ghi DB, không nên làm qua GET: trình duyệt/extension prefetch
    link, hay bot quét trang, có thể âm thầm mark-read hàng loạt thông báo mà
    user chưa từng thực sự xem, và GET không có CSRF token bảo vệ. Đổi sang
    POST (mirror ``notification_mark_all_read``/``user_toggle_active`` — no-op
    im lặng nếu lỡ GET, không 405, đúng convention của các view POST-only khác
    trong file này)."""
    obj = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if request.method == 'POST':
        if not obj.is_read:
            obj.is_read = True
            obj.save(update_fields=['is_read'])
        target = obj.target
        if target is not None and hasattr(target, 'get_absolute_url'):
            return redirect(target.get_absolute_url())
    return redirect('notification_list')


@login_required
def notification_mark_all_read(request):
    """POST-only: đánh dấu toàn bộ thông báo của user hiện tại là đã đọc.

    M8: ``next`` lấy thẳng từ POST body rồi redirect thẳng trước đây là open
    redirect — ai đó có thể dựng 1 form ẩn submit tới URL này với
    ``next=https://trang-gia-mao`` để lợi dụng domain NVL/WMS đáng tin cậy dẫn
    người dùng sang trang giả mạo (phishing). Validate qua
    ``url_has_allowed_host_and_scheme`` (cùng cơ chế Django dùng cho ``next`` của
    ``LoginView``) trước khi chấp nhận; không hợp lệ thì coalesce về
    ``notification_list`` thay vì tin thẳng giá trị client gửi lên."""
    if request.method == 'POST':
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(
            url=next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(next_url)
    return redirect('notification_list')


# --- Tra cứu Audit Log (FR-USER-05) — chỉ Admin hệ thống ---

def audit_log_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not can_view_audit_log(request.user):
            raise PermissionDenied('Chỉ Admin hệ thống được tra cứu nhật ký hành động.')
        if not request.user.can_view_menu('audit_log'):
            raise PermissionDenied('Bạn không có quyền truy cập mục "Nhật ký hành động".')
        return view(request, *args, **kwargs)

    return wrapper


@audit_log_required
def audit_log_list(request):
    """READ — tra cứu ``AuditLog`` toàn hệ thống, lọc theo module/actor/phòng ban/
    hành động/khoảng ngày (FR-USER-05: "quản lý trở lên tới admin đều tra cứu được").
    """
    logs = AuditLog.objects.select_related('actor', 'target_type')

    # H5: module/actor là FK PK (int) và date_from/date_to phải đúng định dạng
    # ngày — trước đây filter thẳng bằng giá trị GET thô nên 1 query param sai
    # (?module=abc, ?actor=abc, ?date_from=abc) làm ValueError/ValidationError
    # bay thẳng lên thành lỗi 500 thay vì chỉ bỏ qua filter đó. Parse an toàn,
    # sai thì reset về '' (không lọc) thay vì crash trang.
    module = request.GET.get('module', '')
    if module:
        try:
            module_id = int(module)
        except (TypeError, ValueError):
            module = ''
        else:
            logs = logs.filter(target_type_id=module_id)

    actor_id = request.GET.get('actor', '')
    if actor_id:
        try:
            actor_pk = int(actor_id)
        except (TypeError, ValueError):
            actor_id = ''
        else:
            logs = logs.filter(actor_id=actor_pk)

    department = request.GET.get('department', '')
    if department:
        logs = logs.filter(actor__department=department)

    action = request.GET.get('action', '')
    if action:
        logs = logs.filter(action=action)

    date_from = request.GET.get('date_from', '')
    if date_from:
        # parse_date() trả None nếu chuỗi không đúng định dạng YYYY-MM-DD, nhưng
        # 1 chuỗi ĐÚNG định dạng mà sai lịch (vd. "2026-13-99") lại làm nó raise
        # ValueError thẳng từ datetime.date(...) — phải bắt cả 2 trường hợp.
        try:
            parsed_date_from = parse_date(date_from)
        except ValueError:
            parsed_date_from = None
        if parsed_date_from is None:
            date_from = ''
        else:
            logs = logs.filter(created_at__date__gte=parsed_date_from)
    date_to = request.GET.get('date_to', '')
    if date_to:
        try:
            parsed_date_to = parse_date(date_to)
        except ValueError:
            parsed_date_to = None
        if parsed_date_to is None:
            date_to = ''
        else:
            logs = logs.filter(created_at__date__lte=parsed_date_to)

    q = request.GET.get('q', '').strip()
    if q:
        logs = logs.filter(Q(description__icontains=q) | Q(reason__icontains=q))

    page_obj, page_size = paginate_queryset(request, logs)

    # 2 lựa chọn filter dropdown này quét distinct trên TOÀN BỘ bảng AuditLog
    # (ngày càng lớn vì mọi action đều ghi log) — cache ngắn hạn (LocMemCache,
    # mặc định của Django, không cần Redis) để tránh full scan lại mỗi request;
    # dropdown "trễ" tối đa 5 phút là chấp nhận được cho 1 trang tra cứu.
    module_choices = cache.get('audit_log_module_choices')
    if module_choices is None:
        module_choices = [
            (ct.pk, str(ct)) for ct in
            ContentType.objects.filter(pk__in=AuditLog.objects.values_list('target_type_id', flat=True).distinct())
        ]
        cache.set('audit_log_module_choices', module_choices, 300)
    actor_choices = cache.get('audit_log_actor_choices')
    if actor_choices is None:
        actor_choices = list(User.objects.filter(
            pk__in=AuditLog.objects.exclude(actor__isnull=True).values_list('actor_id', flat=True).distinct(),
        ).order_by('username'))
        cache.set('audit_log_actor_choices', actor_choices, 300)

    return render(request, 'accounts/audit_log_list.html', {
        'logs': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'module_choices': module_choices, 'actor_choices': actor_choices,
        'department_choices': User.Department.choices, 'action_choices': AuditLog.Action.choices,
        'selected_module': module, 'selected_actor': actor_id, 'selected_department': department,
        'selected_action': action, 'date_from': date_from, 'date_to': date_to, 'q': q,
    })
