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
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from django.contrib.contenttypes.models import ContentType

from .audit import client_ip, log_action
from .forms import UserCreateForm, UserUpdateForm, WmsPasswordChangeForm, WmsSetPasswordForm
from .models import AuditLog, Notification
from .pagination import paginate_queryset
from .permissions import ACTIONS, MODULES, codenames_for_role
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
    """Decorator: chưa đăng nhập -> về login; đã đăng nhập nhưng không phải admin -> 403."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not can_manage_users(request.user):
            raise PermissionDenied('Chỉ Admin được quản lý người dùng.')
        return view(request, *args, **kwargs)

    return wrapper


def _send_account_created_email(user, password):
    """Gửi email thông báo tài khoản mới cho user (FR-USER-01 CREATE).

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
            f'Tên đăng nhập: {user.username}\n'
            f'Mật khẩu: {password}\n\n'
            f'Vui lòng đăng nhập và đổi mật khẩu nếu cần.'
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
    Module x Action, có thể THU HỒI quyền mặc định của role (không chỉ thêm),
    hoặc bấm "Đặt lại theo vai trò" để quay về đúng mặc định của role.
    """
    obj = get_object_or_404(User, pk=pk)
    all_codenames = [f'can_{action}_{module}' for module in MODULES for action in ACTIONS]

    if request.method == 'POST':
        before = _effective_codenames(obj)
        if 'reset' in request.POST:
            sync_user_permissions(obj)
            after = set(codenames_for_role(obj.role))
            description = f'Đặt lại phân quyền của {obj.username} theo mặc định vai trò'
        else:
            selected = set(request.POST.getlist('perm')) & set(all_codenames)
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
    return render(request, 'accounts/user_permission_form.html', {
        'obj': obj, 'action_labels': action_labels, 'perm_rows': perm_rows,
    })


@user_admin_required
def user_create(request):
    """CREATE — admin tạo user với mật khẩu tự chọn; gửi email + ghi audit CREATE."""
    form = UserCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        password = form.cleaned_data['password1']
        user = form.save()  # kích hoạt signal đồng bộ role -> Group
        _send_account_created_email(user, password)
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
    """UPDATE — đổi role / deactivate / thông tin; ghi audit UPDATE với changes."""
    obj = get_object_or_404(User, pk=pk)
    before = {f: getattr(obj, f) for f in TRACKED_FIELDS}
    form = UserUpdateForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
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
    """DELETE — soft delete (giữ bản ghi cho audit); chặn tự xoá chính mình."""
    obj = get_object_or_404(User, pk=pk)
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
    """Đánh dấu 1 thông báo đã đọc rồi chuyển tới đối tượng liên quan (nếu có
    ``get_absolute_url``) hoặc quay lại danh sách thông báo."""
    obj = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if not obj.is_read:
        obj.is_read = True
        obj.save(update_fields=['is_read'])
    target = obj.target
    if target is not None and hasattr(target, 'get_absolute_url'):
        return redirect(target.get_absolute_url())
    return redirect('notification_list')


@login_required
def notification_mark_all_read(request):
    """POST-only: đánh dấu toàn bộ thông báo của user hiện tại là đã đọc."""
    if request.method == 'POST':
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return redirect(request.POST.get('next') or 'notification_list')


# --- Tra cứu Audit Log (FR-USER-05) — quản lý phòng ban trở lên + Admin ---

def can_view_audit_log(user):
    """Quản lý phòng ban (``is_manager``) trở lên, hoặc Admin/superuser."""
    return user.is_superuser or user.role == User.Role.ADMIN or user.is_manager


def audit_log_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not can_view_audit_log(request.user):
            raise PermissionDenied('Chỉ quản lý phòng ban hoặc Admin được tra cứu nhật ký hành động.')
        return view(request, *args, **kwargs)

    return wrapper


@audit_log_required
def audit_log_list(request):
    """READ — tra cứu ``AuditLog`` toàn hệ thống, lọc theo module/actor/phòng ban/
    hành động/khoảng ngày (FR-USER-05: "quản lý trở lên tới admin đều tra cứu được").
    """
    logs = AuditLog.objects.select_related('actor', 'target_type')

    module = request.GET.get('module', '')
    if module:
        logs = logs.filter(target_type_id=module)

    actor_id = request.GET.get('actor', '')
    if actor_id:
        logs = logs.filter(actor_id=actor_id)

    department = request.GET.get('department', '')
    if department:
        logs = logs.filter(actor__department=department)

    action = request.GET.get('action', '')
    if action:
        logs = logs.filter(action=action)

    date_from = request.GET.get('date_from', '')
    if date_from:
        logs = logs.filter(created_at__date__gte=date_from)
    date_to = request.GET.get('date_to', '')
    if date_to:
        logs = logs.filter(created_at__date__lte=date_to)

    q = request.GET.get('q', '').strip()
    if q:
        logs = logs.filter(Q(description__icontains=q) | Q(reason__icontains=q))

    page_obj, page_size = paginate_queryset(request, logs)

    module_choices = [
        (ct.pk, str(ct)) for ct in
        ContentType.objects.filter(pk__in=AuditLog.objects.values_list('target_type_id', flat=True).distinct())
    ]
    actor_choices = User.objects.filter(
        pk__in=AuditLog.objects.exclude(actor__isnull=True).values_list('actor_id', flat=True).distinct(),
    ).order_by('username')

    return render(request, 'accounts/audit_log_list.html', {
        'logs': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'module_choices': module_choices, 'actor_choices': actor_choices,
        'department_choices': User.Department.choices, 'action_choices': AuditLog.Action.choices,
        'selected_module': module, 'selected_actor': actor_id, 'selected_department': department,
        'selected_action': action, 'date_from': date_from, 'date_to': date_to, 'q': q,
    })
