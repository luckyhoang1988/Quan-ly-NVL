from django.core.management.base import BaseCommand

from purchasing.services import find_allocation_migration_exceptions


class Command(BaseCommand):
    help = ('Liệt kê PurchaseRequestItem có linked_po nhưng chưa có ProcurementAllocation nào '
            '(ngoại lệ migration 0018 — cần xử lý qua reconcile_legacy_po_item_allocations).')

    def handle(self, *args, **options):
        exceptions = find_allocation_migration_exceptions()
        if not exceptions:
            self.stdout.write(self.style.SUCCESS('Không có ngoại lệ nào.'))
            return
        for item in exceptions:
            self.stdout.write(
                f'PR {item.purchase_request.request_no} — dòng "{item}" — linked_po '
                f'{item.purchase_request.linked_po.po_no} — chưa có allocation nào.'
            )
        self.stdout.write(self.style.WARNING(f'Tổng: {len(exceptions)} dòng cần xử lý.'))
