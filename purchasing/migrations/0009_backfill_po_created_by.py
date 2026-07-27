"""Backfill ``PurchaseOrder.created_by`` cho các PO tạo trước khi field này tồn
tại (xem ``0007_purchaseorder_created_by.py``) — migration đó chỉ ``AddField``,
không có data migration đi kèm, nên PO nào ``created_by=NULL`` bị ẩn vĩnh viễn
khỏi ``po_list`` của nhân viên PURCHASING thường: filter
``orders.filter(created_by=request.user)`` (``purchasing/views.py``) không khớp
NULL trong SQL, nên không ai xem lại được các PO đó, kể cả người thực sự đã tạo.

Với PO nào có ``AuditLog`` CREATE tương ứng (đã đi qua view ``po_create`` thật,
trước khi field ``created_by`` tồn tại nên actor không được lưu vào PO), lấy
actor của log sớm nhất gán lại ``created_by`` cho đúng người tạo thật. PO không
tìm được nguồn (vd dữ liệu ``seed_demo_data`` tạo thẳng qua ORM, không qua
view/log) thì để nguyên NULL — ``po_list`` được nới để NULL luôn hiển thị cho
mọi nhân viên PURCHASING thay vì gán bừa cho một người không thực sự tạo nó
(tương tự tinh thần general-lesson cho PR ở ``0008_backfill_pr_approval.py``).
"""
from django.db import migrations


def backfill_po_created_by(apps, schema_editor):
    PurchaseOrder = apps.get_model('purchasing', 'PurchaseOrder')
    AuditLog = apps.get_model('accounts', 'AuditLog')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    content_type = ContentType.objects.get_for_model(PurchaseOrder)

    for po in PurchaseOrder.objects.filter(created_by__isnull=True):
        log = (
            AuditLog.objects
            .filter(
                target_type=content_type, target_id=str(po.pk),
                action='CREATE', actor__isnull=False,
            )
            .order_by('created_at', 'id')
            .first()
        )
        if log:
            po.created_by_id = log.actor_id
            po.save(update_fields=['created_by'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0008_backfill_pr_approval'),
        ('accounts', '0012_reseed_purchasing_pr_permissions'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(backfill_po_created_by, noop_reverse),
    ]
