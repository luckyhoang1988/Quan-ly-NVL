"""Context processor: badge số thông báo chưa đọc hiển thị ở mọi trang (navbar).

Đăng ký trong ``config/settings.py`` TEMPLATES/context_processors — tránh phải
truyền thủ công ``unread_notification_count`` ở từng view.
"""
from .models import Notification


def notifications(request):
    if not request.user.is_authenticated:
        return {}
    qs = Notification.objects.filter(recipient=request.user)
    return {
        'unread_notification_count': qs.filter(is_read=False).count(),
        'recent_notifications': qs.select_related('target_type')[:8],
    }
