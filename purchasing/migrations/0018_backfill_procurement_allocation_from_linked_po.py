from django.db import migrations


def backfill_allocations(apps, schema_editor):
    PurchaseRequest = apps.get_model('purchasing', 'PurchaseRequest')
    PurchaseRequestItem = apps.get_model('purchasing', 'PurchaseRequestItem')
    PurchaseOrderItem = apps.get_model('purchasing', 'PurchaseOrderItem')
    ProcurementAllocation = apps.get_model('purchasing', 'ProcurementAllocation')

    exceptions = []
    for pr in PurchaseRequest.objects.filter(linked_po__isnull=False):
        pr_items_by_product = {}
        for item in pr.items.all():
            pr_items_by_product.setdefault(item.product_id, []).append(item)
        po_items_by_product = {}
        for item in PurchaseOrderItem.objects.filter(purchase_order_id=pr.linked_po_id):
            po_items_by_product.setdefault(item.product_id, []).append(item)

        for product_id, pr_items in pr_items_by_product.items():
            po_items = po_items_by_product.get(product_id, [])
            if len(pr_items) == 1 and len(po_items) == 1:
                pr_item, po_item = pr_items[0], po_items[0]
                qty = min(pr_item.qty_requested, po_item.qty_ordered)
                if ProcurementAllocation.objects.filter(pr_item=pr_item, po_item=po_item).exists():
                    continue  # idempotent: đã backfill lần trước
                ProcurementAllocation.objects.create(
                    pr_item=pr_item, po_item=po_item, qty_allocated=qty,
                    status='ACTIVE', created_by=None,
                    po_no_snapshot=po_item.purchase_order.po_no,
                    product_code_snapshot=po_item.product.product_code,
                )
                pr_item.qty_approved = pr_item.qty_requested
                pr_item.save(update_fields=['qty_approved'])
                if pr_item.qty_requested != po_item.qty_ordered:
                    exceptions.append((pr.pk, pr_item.pk, po_item.pk, 'qty_mismatch'))
            else:
                for pr_item in pr_items:
                    pr_item.qty_approved = pr_item.qty_requested
                    pr_item.save(update_fields=['qty_approved'])
                exceptions.append((pr.pk, product_id, pr.linked_po_id, 'ambiguous_match'))

    if exceptions:
        # ASCII-only: print() ra console migrate co the chay tren terminal dung
        # codepage khong ho tro tieng Viet co dau (vd Windows cp1252) - se
        # UnicodeEncodeError neu dung ky tu co dau/em-dash o day.
        print(f'[0018] {len(exceptions)} exception(s) trong migration allocation - '
              f'chay `manage.py report_allocation_migration_exceptions` de xem chi tiet.')


class Migration(migrations.Migration):
    dependencies = [('purchasing', '0017_pr_stage2_fields_exchangerate_allocation')]
    operations = [migrations.RunPython(backfill_allocations, migrations.RunPython.noop)]
