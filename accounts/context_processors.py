"""Context processor: badge số thông báo chưa đọc + flag quyền cho sidebar,
hiển thị ở mọi trang (navbar/`base.html`).

Đăng ký trong ``config/settings.py`` TEMPLATES/context_processors — tránh phải
truyền thủ công ``unread_notification_count``/các flag quyền ở từng view.
"""
from .models import Notification


def notifications(request):
    if not request.user.is_authenticated:
        return {}
    # Chỉ 1 query: badge chỉ cần đếm, chuông thông báo link sang
    # `notification_list` (trang riêng, tự query) — không có template nào
    # dùng `recent_notifications` nên bỏ hẳn thay vì query rồi không dùng.
    return {
        'unread_notification_count': Notification.objects.filter(
            recipient=request.user, is_read=False).count(),
    }


def sidebar_permissions(request):
    """Flag `user.can('read', <module>)` / `user.can_view_menu(<key>)` dùng để gate
    link sidebar (`base.html`).

    Trước đây các gate này viết bằng role cứng (vd ``role != 'STAFF'``) — trùng
    kết quả với ma trận mặc định ở ``accounts/permissions.py`` nhưng không phản
    ánh quyền chi tiết theo user (trang "Phân quyền chi tiết",
    ``views.user_permission_edit``). Dùng ``user.can()`` ở đây để sidebar luôn
    khớp với quyền hiệu lực thật của user, không chỉ role mặc định.

    ``can_view_menu_*`` (thêm cùng lúc với ``MENU_ITEMS``) phủ các mục sidebar
    KHÔNG có ma trận CRUD (warehouse/catalog/partners/inventory/handoff/
    user_mgmt/audit_log) — xem `.claude/skills/wms-conventions/SKILL.md` §6.
    """
    if not request.user.is_authenticated:
        return {}
    user = request.user
    return {
        'can_read_qc': user.can('read', 'qc'),
        'can_read_opname': user.can('read', 'opname'),
        'can_read_pr': user.can('read', 'pr'),
        'can_read_grn': user.can('read', 'grn'),
        'can_read_gin': user.can('read', 'gin'),
        'can_read_po': user.can('read', 'po'),
        'can_read_reports': user.can('read', 'reports'),
        'can_view_menu_warehouse': user.can_view_menu('warehouse'),
        'can_view_menu_catalog': user.can_view_menu('catalog'),
        'can_view_menu_partners': user.can_view_menu('partners'),
        'can_view_menu_inventory': user.can_view_menu('inventory'),
        'can_view_menu_handoff': user.can_view_menu('handoff'),
        'can_view_menu_user_mgmt': user.can_view_menu('user_mgmt'),
        'can_view_menu_audit_log': user.can_view_menu('audit_log'),
    }
