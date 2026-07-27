"""Backfill ``Approval`` cho các ``PurchaseRequest`` PENDING tạo TRƯỚC khi PR
approval được route qua ``Approval`` (xem accounts/migrations/0011_approval.py,
0012_reseed_purchasing_pr_permissions.py và purchasing.services.submit_purchase_request).

Không có migration nào tạo ``Approval`` cho các PR PENDING cũ này -> UI vẫn hiện
"Chờ duyệt" nhưng ``pr_approve``/``pr_reject`` luôn báo lỗi "không có phiếu duyệt
nào đang chờ xử lý" (``latest_approval_for`` trả None), kẹt vĩnh viễn. Tạo 1
Approval PENDING/department=PURCHASING cho mỗi PR như vậy, ``submitted_by`` =
``requested_by`` (người tạo PR cũng chính là người "nộp" theo luồng hiện tại),
``submitted_at`` set lại = ``created_at`` của PR cho đúng mốc thời gian thực tế
(auto_now_add ghi đè lúc create nên phải update lại bằng queryset sau).
"""
from django.db import migrations


def backfill_pr_approval(apps, schema_editor):
    PurchaseRequest = apps.get_model('purchasing', 'PurchaseRequest')
    Approval = apps.get_model('accounts', 'Approval')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    content_type = ContentType.objects.get_for_model(PurchaseRequest)
    existing_target_ids = set(
        Approval.objects.filter(target_type=content_type).values_list('target_id', flat=True)
    )

    for pr in PurchaseRequest.objects.filter(status='PENDING'):
        if str(pr.pk) in existing_target_ids:
            continue
        approval = Approval.objects.create(
            target_type=content_type, target_id=str(pr.pk),
            department='PURCHASING', action_label=f'Yêu cầu mua hàng {pr.request_no}',
            status='PENDING', submitted_by=pr.requested_by,
        )
        Approval.objects.filter(pk=approval.pk).update(submitted_at=pr.created_at)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0007_purchaseorder_created_by'),
        ('accounts', '0012_reseed_purchasing_pr_permissions'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(backfill_pr_approval, noop_reverse),
    ]
