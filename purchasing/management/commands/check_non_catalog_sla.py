from django.core.management.base import BaseCommand

from accounts.models import User
from accounts.notifications import notify
from purchasing.services import overdue_non_catalog_items


class Command(BaseCommand):
    help = ('Thông báo PUR manager + assigned_to cho dòng non-catalog quá hạn SLA 3 ngày làm việc '
            '(⏸️ — chạy qua cron, không phải Celery, mục 6 FSD Stage 2).')

    def handle(self, *args, **options):
        items = overdue_non_catalog_items()
        pur_managers = list(User.objects.filter(
            department=User.Department.PURCHASING, is_manager=True, is_active=True))
        for item in items:
            pr = item.purchase_request
            recipients = list(pur_managers)
            if pr.assigned_to_id:
                recipients.append(pr.assigned_to)
            notify(
                recipients,
                f'Dòng non-catalog "{item.non_catalog_name}" của yêu cầu {pr.request_no} đã quá '
                f'3 ngày làm việc chưa map sản phẩm.',
                target=pr,
            )
        self.stdout.write(self.style.SUCCESS(f'Đã thông báo cho {len(items)} dòng quá hạn.'))
