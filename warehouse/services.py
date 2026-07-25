"""Service layer app warehouse: singleton Kho chờ/Kho phế + BR-WM-006.

``inventory.models`` không import ngược lại ``warehouse`` (dùng string FK
``'warehouse.Warehouse'``/``'warehouse.Location'``) nên import trực tiếp ở đây
không tạo vòng lặp.
"""
from django.core.exceptions import ValidationError

from accounts.audit import log_action
from accounts.models import AuditLog
from inventory.models import Inventory

from .models import Warehouse


def _get_singleton(warehouse_type, label):
    """Trả về kho duy nhất đang hoạt động của ``warehouse_type``.

    Ràng buộc DB (``unique_active_staging_scrap_warehouse``) đã đảm bảo tối đa
    1 kho active/loại — hàm này chỉ dịch trường hợp thiếu/0 kho thành lỗi rõ
    ràng cho người dùng thay vì để QC/GRN văng lỗi khó hiểu.
    """
    qs = Warehouse.objects.filter(warehouse_type=warehouse_type, is_active=True)
    count = qs.count()
    if count == 0:
        raise ValidationError(f'Chưa cấu hình kho loại "{label}" — tạo 1 kho loại này trước.')
    if count > 1:
        raise ValidationError(f'Có nhiều hơn 1 kho loại "{label}" đang hoạt động — vi phạm ràng buộc duy nhất.')
    return qs.first()


def get_staging_warehouse():
    """Kho Chờ duy nhất đang hoạt động (dùng bởi ``quality.services.start_qc``)."""
    return _get_singleton(Warehouse.WarehouseType.STAGING, 'Kho chờ')


def get_scrap_warehouse():
    """Kho Phế duy nhất đang hoạt động (dùng bởi QC FAIL/PARTIAL_PASS)."""
    return _get_singleton(Warehouse.WarehouseType.SCRAP, 'Kho phế')


def get_default_location(warehouse):
    """Vị trí active đầu tiên (theo mã) của 1 kho — đủ dùng cho kho hệ thống
    mục đích đơn (Kho chờ/Kho phế), không cần độ chi tiết bin-level.
    """
    location = warehouse.locations.filter(is_active=True).order_by('code').first()
    if location is None:
        raise ValidationError(f'Kho "{warehouse.code}" chưa có vị trí lưu trữ nào đang hoạt động.')
    return location


def deactivate_warehouse(warehouse, actor=None, ip_address=None):
    """BR-WM-006: không cho khoá kho khi còn tồn kho (``qty_on_hand > 0``)."""
    if Inventory.objects.filter(warehouse=warehouse, qty_on_hand__gt=0).exists():
        raise ValidationError(f'Không thể khoá kho "{warehouse.code}" khi còn tồn kho (qty_on_hand > 0).')
    warehouse.is_active = False
    warehouse.save(update_fields=['is_active'])
    log_action(actor, AuditLog.Action.DELETE, target=warehouse,
               description=f'Khoá hoạt động kho {warehouse.code}', ip_address=ip_address)
    return warehouse
