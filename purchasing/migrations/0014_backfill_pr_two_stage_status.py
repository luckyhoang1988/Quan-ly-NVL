"""Backfill các ``PurchaseRequest`` đang ở status='PENDING' (giá trị cũ, đã bị
xoá khỏi ``Status`` choices ở migration 0013) sang đúng PENDING_DEPT/PENDING_PUR
theo thiết kế duyệt 2 cấp (xem CLAUDE.md "Purchase Request (PR)" và
purchasing.services.submit_purchase_request).

Trước đây MỌI PR nộp đều tạo 1 Approval(department=PURCHASING) — bất kể người
tạo thuộc phòng ban nào. Từ nay: PR của phòng khác PURCHASING phải qua
PENDING_DEPT (Approval.department = phòng gốc) trước, PR của phòng PURCHASING
(hoặc không có department) đi thẳng PENDING_PUR (Approval.department =
PURCHASING, không đổi). Với mỗi PR PENDING cũ: xác định đúng status/department
mới theo requested_by.department, rồi sửa Approval PENDING hiện có (nếu có,
department từng bị hard-code sai) hoặc tạo mới nếu thiếu hẳn (mirror phòng vệ
của 0008_backfill_pr_approval.py cho trường hợp không có Approval nào).
"""
from django.db import migrations


def backfill_two_stage_status(apps, schema_editor):
    PurchaseRequest = apps.get_model('purchasing', 'PurchaseRequest')
    Approval = apps.get_model('accounts', 'Approval')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    content_type = ContentType.objects.get_for_model(PurchaseRequest)

    for pr in PurchaseRequest.objects.filter(status='PENDING').select_related('requested_by'):
        origin_department = pr.requested_by.department if pr.requested_by_id else ''
        if origin_department and origin_department != 'PURCHASING':
            new_status = 'PENDING_DEPT'
            correct_department = origin_department
        else:
            new_status = 'PENDING_PUR'
            correct_department = 'PURCHASING'

        approval = Approval.objects.filter(
            target_type=content_type, target_id=str(pr.pk), status='PENDING',
        ).first()
        if approval is None:
            approval = Approval.objects.create(
                target_type=content_type, target_id=str(pr.pk),
                department=correct_department, action_label=f'Yêu cầu mua hàng {pr.request_no}',
                status='PENDING', submitted_by=pr.requested_by,
            )
            Approval.objects.filter(pk=approval.pk).update(submitted_at=pr.created_at)
        elif approval.department != correct_department:
            Approval.objects.filter(pk=approval.pk).update(department=correct_department)

        PurchaseRequest.objects.filter(pk=pr.pk).update(status=new_status)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0013_alter_purchaserequest_status'),
        ('accounts', '0016_reseed_pr_create_update_permissions'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(backfill_two_stage_status, noop_reverse),
    ]
