"""Helper ghi audit dùng chung (FR-USER-05).

Mọi nơi cần ghi nhật ký (login, CRUD user, sau này là state-transition của
GRN/QC/Batch) đều gọi ``log_action(...)`` thay vì tạo ``AuditLog`` trực tiếp —
để cách ghi who/what/when/why nhất quán toàn hệ thống.

Ví dụ::

    from accounts.audit import log_action
    log_action(request.user, AuditLog.Action.UPDATE, target=user_obj,
               description='Đổi role STAFF -> QC',
               changes={'role': ['STAFF', 'QC']}, ip_address=get_client_ip(request))
"""
from django.contrib.contenttypes.models import ContentType

from .models import AuditLog


def log_action(actor, action, target=None, description='', reason='',
               changes=None, ip_address=None):
    """Ghi một dòng ``AuditLog`` và trả về bản ghi vừa tạo.

    - ``actor``: User thực hiện, hoặc ``None`` cho hành động hệ thống. Nếu truyền
      một user chưa lưu (không có ``pk``) thì coi như hệ thống (actor=None).
    - ``action``: mã hành động — nên dùng ``AuditLog.Action.*`` nhưng chấp nhận
      chuỗi tự do để module sau mở rộng.
    - ``target``: đối tượng bị tác động (bất kỳ model nào), lưu qua GenericFK.
    """
    target_type = None
    target_id = None
    if target is not None:
        target_type = ContentType.objects.get_for_model(target.__class__)
        target_id = str(target.pk)

    return AuditLog.objects.create(
        actor=actor if getattr(actor, 'pk', None) else None,
        action=str(action),
        description=description,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        changes=changes,
        ip_address=ip_address,
    )


def client_ip(request):
    """Lấy IP client từ ``request`` để ghi audit (WHO ở đâu).

    Ưu tiên ``X-Forwarded-For`` (khi chạy sau proxy/nginx) rồi mới ``REMOTE_ADDR``.
    Trả ``None`` nếu không có request (vd hành động hệ thống) — an toàn cho
    ``GenericIPAddressField(null=True)``.
    """
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
