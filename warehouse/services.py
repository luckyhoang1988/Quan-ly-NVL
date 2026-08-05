"""Service layer app warehouse: singleton Kho chờ/Kho phế + BR-WM-006.

``inventory.models`` không import ngược lại ``warehouse`` (dùng string FK
``'warehouse.Warehouse'``/``'warehouse.Location'``) nên import trực tiếp ở đây
không tạo vòng lặp.
"""
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db.models import Count, F, Sum
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog
from inventory.models import Batch, Inventory, PHYSICAL_BATCH_STATUSES, WarehouseHandoff

from .models import CAPACITY_WARN_RATIO, STAGING_AGING_DAYS, Warehouse


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


def activate_warehouse(warehouse, actor=None, ip_address=None):
    """Mở lại hoạt động 1 kho đã bị khoá.

    STAGING/SCRAP tối đa 1 kho active/loại (``unique_active_staging_scrap_warehouse``)
    — check trước và trả ``ValidationError`` rõ ràng thay vì để DB
    ``IntegrityError`` (500) văng ra khi đã có kho khác cùng loại đang active.
    """
    if warehouse.warehouse_type in (Warehouse.WarehouseType.STAGING, Warehouse.WarehouseType.SCRAP):
        other = Warehouse.objects.filter(
            warehouse_type=warehouse.warehouse_type, is_active=True,
        ).exclude(pk=warehouse.pk).first()
        if other is not None:
            raise ValidationError(
                f'Đã có kho "{other.code}" loại "{warehouse.get_warehouse_type_display()}" đang '
                f'hoạt động — khoá kho đó trước khi mở lại kho "{warehouse.code}".'
            )
    warehouse.is_active = True
    warehouse.save(update_fields=['is_active'])
    log_action(actor, AuditLog.Action.UPDATE, target=warehouse,
               description=f'Mở lại hoạt động kho {warehouse.code}', ip_address=ip_address)
    return warehouse


def location_occupancy(warehouse):
    """A1: tồn kho theo vị trí, mọi status vật lý, mọi loại kho (MAIN/STAGING/
    SCRAP). Không lọc thêm qty_received > qty_used — CLOSED đã loại hết batch
    qty=0 khỏi PHYSICAL_BATCH_STATUSES (xem FSD 2.2)."""
    return (
        Batch.objects
        .filter(location__warehouse=warehouse, status__in=PHYSICAL_BATCH_STATUSES)
        .select_related('product', 'location')
        .order_by('location__code', 'batch_code')
    )


def location_occupied_qty(location):
    """A2: occupied qty tại 1 vị trí = tổng qty_available mọi batch vật lý (FSD 3.1)."""
    return Batch.objects.filter(
        location=location, status__in=PHYSICAL_BATCH_STATUSES,
    ).aggregate(total=Sum(F('qty_received') - F('qty_used')))['total'] or 0


def warehouse_occupied_qty(warehouse):
    """A2: occupied qty cấp kho = tổng occupied qty mọi location thuộc kho đó
    (kể cả location đang được kiểm tra riêng — xem AC-WH-CAP-08)."""
    return Batch.objects.filter(
        location__warehouse=warehouse, status__in=PHYSICAL_BATCH_STATUSES,
    ).aggregate(total=Sum(F('qty_received') - F('qty_used')))['total'] or 0


def _capacity_level(ratio):
    """('OVER'|'WARN'|'OK', css class Bootstrap) theo ngưỡng CAPACITY_WARN_RATIO/1.0."""
    if ratio >= 1.0:
        return 'OVER', 'bg-danger'
    if ratio >= CAPACITY_WARN_RATIO:
        return 'WARN', 'bg-warning text-dark'
    return 'OK', 'bg-success'


def location_capacity_alerts(location):
    """A2(b)(c): list[str] cảnh báo (không chặn), gọi SAU khi transfer_stock()/
    qc_pass()/qc_partial_pass()/start_qc() đã commit. capacity None hoặc 0 (chưa
    khai) -> bỏ qua cấp đó. 0/1/2 message tuỳ mấy cấp đạt ngưỡng gần đầy/vượt."""
    alerts = []
    if location.capacity:
        level, _ = _capacity_level(location_occupied_qty(location) / location.capacity)
        if level == 'OVER':
            alerts.append(f'Vị trí "{location}" đã vượt dung tích.')
        elif level == 'WARN':
            alerts.append(f'Vị trí "{location}" gần đầy dung tích.')
    warehouse = location.warehouse
    if warehouse.capacity:
        level, _ = _capacity_level(warehouse_occupied_qty(warehouse) / warehouse.capacity)
        if level == 'OVER':
            alerts.append(f'Kho "{warehouse.code}" đã vượt dung tích.')
        elif level == 'WARN':
            alerts.append(f'Kho "{warehouse.code}" gần đầy dung tích.')
    return alerts


def location_occupied_qty_map(warehouse):
    """{location_id: occupied_qty} — 1 query cho toàn bộ vị trí của kho, tránh
    N+1 khi warehouse_detail tính badge dung tích cho mỗi dòng bảng "Vị trí
    lưu trữ"."""
    rows = (
        Batch.objects.filter(location__warehouse=warehouse, status__in=PHYSICAL_BATCH_STATUSES)
        .values('location_id')
        .annotate(total=Sum(F('qty_received') - F('qty_used')))
    )
    return {row['location_id']: row['total'] for row in rows}


def capacity_badge(occupied, capacity):
    """UI badge OK/Gần đầy/Vượt (3.3.a) — None nếu capacity chưa khai (None/0)."""
    if not capacity:
        return None
    level, css = _capacity_level(occupied / capacity)
    label = {'OVER': 'Vượt dung tích', 'WARN': 'Gần đầy', 'OK': 'Bình thường'}[level]
    return {'css': css, 'label': label}


def _staging_ops_snapshot(warehouse):
    batches = Batch.objects.filter(location__warehouse=warehouse, status=Batch.Status.ACTIVE)
    agg = batches.aggregate(count=Count('pk'), qty=Sum(F('qty_received') - F('qty_used')))
    cutoff_date = timezone.localdate() - timedelta(days=STAGING_AGING_DAYS)
    cutoff_dt = timezone.make_aware(datetime.combine(cutoff_date, time.min))
    return {
        'active_count': agg['count'] or 0,
        'active_qty': agg['qty'] or 0,
        'aged_count': batches.filter(created_at__lt=cutoff_dt).count(),
    }


def _main_ops_snapshot(warehouse):
    pending_handoff_count = WarehouseHandoff.objects.filter(
        destination_warehouse=warehouse, status=WarehouseHandoff.Status.PENDING,
    ).count()
    return {'pending_handoff_count': pending_handoff_count}


def _scrap_ops_snapshot(warehouse):
    qty = Batch.objects.filter(
        location__warehouse=warehouse, status=Batch.Status.QUARANTINE,
    ).aggregate(total=Sum(F('qty_received') - F('qty_used')))['total'] or 0
    return {'quarantine_qty': qty}


def ops_snapshot(warehouse):
    """A3: KPI vận hành theo warehouse_type — trả dict cho đúng 1 loại kho.
    Template chọn khối hiển thị theo obj.warehouse_type, không theo key dict."""
    if warehouse.warehouse_type == Warehouse.WarehouseType.STAGING:
        return _staging_ops_snapshot(warehouse)
    if warehouse.warehouse_type == Warehouse.WarehouseType.MAIN:
        return _main_ops_snapshot(warehouse)
    return _scrap_ops_snapshot(warehouse)
