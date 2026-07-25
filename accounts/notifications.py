"""Helper gửi thông báo trong app (Phase A nền tảng cho luồng duyệt/bàn giao).

Poll khi load trang — không dùng Celery/websocket (đúng convention ``⏸️`` của
CLAUDE.md). Dùng ``notify()`` thay vì tạo ``Notification`` trực tiếp để đảm
bảo gửi nhất quán cho 1 người hoặc nhiều người cùng lúc.
"""
from django.contrib.contenttypes.models import ContentType

from .models import Notification


def notify(recipients, verb, target=None):
    """Tạo 1 ``Notification`` cho mỗi user trong ``recipients``.

    ``recipients``: 1 user, hoặc list/queryset user (trùng lặp tự loại nhờ
    ``set()`` theo pk). ``target``: đối tượng liên quan (GRN/GIN/GrnReturn/
    WarehouseHandoff...), có thể ``None`` cho thông báo không gắn đối tượng.
    Cùng cách resolve GenericFK thủ công như ``accounts.audit.log_action()``.
    Trả về list ``Notification`` đã tạo (rỗng nếu ``recipients`` rỗng).
    """
    if recipients is None:
        return []
    if hasattr(recipients, 'pk'):
        recipients = [recipients]

    target_type = None
    target_id = None
    if target is not None:
        target_type = ContentType.objects.get_for_model(target.__class__)
        target_id = str(target.pk)

    seen_pks = set()
    to_create = []
    for user in recipients:
        if user is None or user.pk in seen_pks:
            continue
        seen_pks.add(user.pk)
        to_create.append(Notification(
            recipient=user, verb=verb, target_type=target_type, target_id=target_id,
        ))

    if not to_create:
        return []
    return Notification.objects.bulk_create(to_create)
