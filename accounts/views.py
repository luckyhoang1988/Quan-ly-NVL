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
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.crypto import get_random_string

from .audit import client_ip, log_action
from .forms import UserCreateForm, UserUpdateForm
from .models import AuditLog
from .pagination import paginate_queryset
from .permissions import ACTIONS, MODULES

User = get_user_model()

# Các field được theo dõi để ghi before/after khi UPDATE (audit "what changed").
TRACKED_FIELDS = ['role', 'is_active', 'email', 'first_name', 'last_name']


@login_required
def dashboard(request):
    """Trang chủ sau đăng nhập (LOGIN_REDIRECT_URL).

    Tối thiểu ở Phase 1: xác nhận đã đăng nhập + hiển thị vai trò. Nội dung dashboard
    thật (cảnh báo tồn kho...) sẽ bổ sung khi làm warehouse ở mục 1b.
    """
    return render(request, 'dashboard.html')


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


def _send_temp_password(user, temp_password):
    """Gửi email mật khẩu tạm cho user mới (FR-USER-01 CREATE).

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
            f'Mật khẩu tạm thời: {temp_password}\n\n'
            f'Vui lòng đăng nhập và đổi mật khẩu ngay.'
        ),
        from_email=None,  # dùng DEFAULT_FROM_EMAIL
        recipient_list=[user.email],
        fail_silently=True,
    )


@user_admin_required
def user_list(request):
    """READ — danh sách user (kể cả đã soft-delete, có badge phân biệt)."""
    users = User.objects.order_by('username')
    role = request.GET.get('role', '')
    if role:
        users = users.filter(role=role)
    status = request.GET.get('status', '')
    if status == 'active':
        users = users.filter(is_active=True)
    elif status == 'inactive':
        users = users.filter(is_active=False)
    q = request.GET.get('q', '').strip()
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q))
    page_obj, page_size = paginate_queryset(request, users)
    return render(request, 'accounts/user_list.html', {
        'users': page_obj, 'page_obj': page_obj, 'page_size': page_size,
        'roles': User.Role.choices, 'selected_role': role,
        'selected_status': status, 'q': q,
    })


@user_admin_required
def user_detail(request, pk):
    """READ — chi tiết user + ma trận quyền hạn theo role (FR-USER-01: "quyền hạn")."""
    obj = get_object_or_404(User, pk=pk)
    # Cột đúng thứ tự ACTIONS; mỗi hàng là 1 module với danh sách bool can/không.
    action_labels = list(ACTIONS.values())
    perm_rows = [
        {'module': label,
         'cells': [obj.can(action, module) for action in ACTIONS]}
        for module, label in MODULES.items()
    ]
    return render(request, 'accounts/user_detail.html', {
        'obj': obj, 'action_labels': action_labels, 'perm_rows': perm_rows,
    })


@user_admin_required
def user_create(request):
    """CREATE — admin tạo user; sinh mật khẩu tạm + gửi email + ghi audit CREATE."""
    form = UserCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        temp_password = get_random_string(12)
        user.set_password(temp_password)
        user.save()  # kích hoạt signal đồng bộ role -> Group
        _send_temp_password(user, temp_password)
        log_action(
            request.user, AuditLog.Action.CREATE, target=user,
            description=f'Tạo user {user.username} (role {user.role or "—"})',
            ip_address=client_ip(request),
        )
        messages.success(
            request,
            f'Đã tạo user "{user.username}". Mật khẩu tạm: {temp_password} '
            f'(đã gửi email tới {user.email or "—"}).',
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
