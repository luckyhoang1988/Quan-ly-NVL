from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from accounts.models import User
from purchasing.models import ProcurementAllocation, PurchaseOrderItem, PurchaseRequestItem
from purchasing.services import reconcile_legacy_po_item_allocations


class _DryRunRollback(Exception):
    """Buộc rollback savepoint thật cho --dry-run (lưu ý kỹ thuật #3, xem Ràng buộc chung của
    docs/pur/03_stage2_implementation_plan.md) — không phải lỗi nghiệp vụ, không hiển thị traceback.
    """


class Command(BaseCommand):
    help = ('Reconcile allocation cho 1 PO-item legacy backfill từ linked_po (T9) — recovery '
            'procedure Admin-only, xem mục 4 điểm 4/mục 9 FSD Stage 2.')

    def add_arguments(self, parser):
        parser.add_argument('--po-item', type=int, required=True, dest='po_item')
        parser.add_argument(
            '--allocation', action='append', required=True,
            help='Định dạng pr_item_id:qty — lặp lại nhiều lần cho batch nhiều pr_item.')
        parser.add_argument('--actor', type=str, required=True, help='Username duy nhất — chỉ dùng để ghi audit.')
        parser.add_argument('--dry-run', action='store_true', dest='dry_run')

    def handle(self, *args, **options):
        try:
            po_item = PurchaseOrderItem.objects.get(pk=options['po_item'])
        except PurchaseOrderItem.DoesNotExist:
            raise CommandError(f'Không tìm thấy PurchaseOrderItem pk={options["po_item"]}.')

        try:
            actor = User.objects.get(username=options['actor'])
        except User.DoesNotExist:
            raise CommandError(f'Không tìm thấy user với username="{options["actor"]}".')

        allocations = []
        for raw in options['allocation']:
            try:
                pr_item_id_str, qty_str = raw.split(':')
                pr_item = PurchaseRequestItem.objects.get(pk=int(pr_item_id_str))
                qty = int(qty_str)
            except (ValueError, PurchaseRequestItem.DoesNotExist) as exc:
                raise CommandError(f'--allocation "{raw}" không hợp lệ: {exc}')
            allocations.append((pr_item, qty))

        existing_total_before = (
            ProcurementAllocation.objects.filter(po_item=po_item, status=ProcurementAllocation.Status.ACTIVE)
            .aggregate(total=Sum('qty_allocated'))['total'] or 0
        )
        self.stdout.write(
            f'Trước khi chạy: qty_ordered={po_item.qty_ordered}, tổng allocation hiện có={existing_total_before}.')
        for pr_item, qty in allocations:
            self.stdout.write(f'  Sẽ tạo: pr_item={pr_item.pk} ("{pr_item}") qty={qty}.')

        try:
            with transaction.atomic():
                created = reconcile_legacy_po_item_allocations(po_item, allocations, actor=actor)
                if options['dry_run']:
                    raise _DryRunRollback()
        except _DryRunRollback:
            would_be_total = existing_total_before + sum(qty for _pr_item, qty in allocations)
            self.stdout.write(self.style.WARNING(
                f'[DRY-RUN] Không commit gì. Nếu chạy thật: tổng allocation sẽ = {would_be_total} '
                f'(khớp qty_ordered={po_item.qty_ordered} — đã validate ở trên).'
            ))
            return
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages))

        po_item.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(
            f'Đã tạo {len(created)} allocation. qty_ordered={po_item.qty_ordered} — khớp tổng allocation.'))
