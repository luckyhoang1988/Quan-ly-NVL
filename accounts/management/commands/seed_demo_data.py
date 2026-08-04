"""Tạo dữ liệu mẫu (demo) đầy đủ mọi khâu nghiệp vụ để test thủ công qua UI:

    python manage.py seed_demo_data            # tạo mới (bỏ qua nếu đã có dữ liệu demo)
    python manage.py seed_demo_data --flush     # xoá sạch dữ liệu demo cũ rồi tạo lại

Đi qua đúng chuỗi service thật (không insert thẳng Batch/Inventory/StockMovement)
để dữ liệu phản ánh đúng workflow: PO -> GRN -> QC (PASS/FAIL/PARTIAL_PASS) ->
Batch/Inventory -> GIN (FIFO) -> Stocktake — mirror cách các *_test.py trong từng
app dựng fixture (xem CLAUDE.md: GRN/QC/Batch/Inventory là 1 transaction).

Mọi bản ghi demo dùng prefix ``DEMO-`` trên mã (code/supplier_code/product_code/
po_no) để ``--flush`` xoá đúng phạm vi, không đụng dữ liệu thật (kho/NCC/SP khác
DEMO- code). Khi có dữ liệu demo bị xoá, ``--flush`` cũng dọn AuditLog/
Notification/Approval trỏ tới các đối tượng demo đó (log sẽ thành rác — không
còn target sau khi xoá) — nhưng CHỈ đúng phạm vi demo: thu thập
(ContentType, object_id) của từng đối tượng demo TRƯỚC khi xoá rồi lọc theo đó,
không đụng tới log/thông báo/phiếu duyệt của dữ liệu thật dù môi trường có lẫn
cả hai. Nếu không có dữ liệu demo nào để xoá, ``--flush`` sẽ không đụng tới
AuditLog/Notification/Approval.

KHÔNG tạo tài khoản demo_* riêng nữa — script này dùng thẳng các tài khoản THẬT
đã được tạo sẵn trong Quản lý User (mỗi phòng ban 1 quản lý + 1 nhân viên, xem
``REQUIRED_USERS`` bên dưới), để Approval/Notification phát sinh trong lúc seed
rơi đúng vào tài khoản người dùng thực sự đang test, không phải tài khoản
demo_* dùng-rồi-bỏ. Nếu thiếu tài khoản nào, lệnh sẽ báo lỗi rõ cần tạo trước.
"""
import datetime
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.approvals import latest_approval_for
from accounts.models import Approval, AuditLog, Notification, User
from catalog.models import Product
from inventory.models import Batch, Inventory, StockMovement, StockTransfer, WarehouseHandoff
from inventory.services import accept_handoff, sync_expired_batches
from partners.models import Supplier
from purchasing.models import PurchaseOrder, PurchaseOrderItem, PurchaseRequest, PurchaseRequestItem
from purchasing.services import (
    approve_po, close_po, decide_purchase_request, send_po, submit_purchase_request, sync_po_status,
)
from quality.models import QcCriteria, QcInspection, QcInspectionItem
from quality.services import qc_fail, qc_partial_pass, qc_pass, start_qc
from receiving.models import Grn, GrnItem, GrnReturn
from receiving.services import close_grn, submit_to_pending_qc
from shipping.models import Gin, GinBatchAllocation, GinItem
from shipping.services import close_gin, issue_gin, start_picking
from stocktake.models import StocktakeItem, StocktakeSession
from stocktake.services import (
    apply_adjustment, create_session, record_count, start_execution, submit_reconciliation,
)
from warehouse.models import Location, Warehouse

# Tài khoản THẬT đã tạo sẵn trong Quản lý User, theo phòng ban (xem CLAUDE.md
# "Department-manager approval axis") — key -> (username, mô tả cho thông báo lỗi).
# Không tạo mới ở đây; script chỉ fetch và dùng làm actor cho các thao tác demo.
REQUIRED_USERS = [
    ('warehouse_manager', 'khoadmin', 'Quản lý kho (WAREHOUSE, is_manager)'),
    ('warehouse_staff', 'nvkho1', 'Nhân viên kho (STAFF, dept WAREHOUSE)'),
    ('purchasing_manager', 'puradmin', 'Quản lý mua hàng (dept PURCHASING, is_manager)'),
    ('purchasing_staff', 'pur1', 'Nhân viên mua hàng (PURCHASING, dept PURCHASING)'),
    ('qc_staff', 'qc1', 'Nhân viên QC (QC, dept QC)'),
]

# Tài khoản demo_* cũ (tạo bởi phiên bản trước của script này) — không còn được
# seed script tham chiếu tới nữa kể từ khi chuyển sang dùng REQUIRED_USERS ở trên;
# --flush xoá cứng luôn cho gọn (đã được người dùng xác nhận, không phải đoán).
OBSOLETE_DEMO_USERNAMES = [
    'demo_admin', 'demo_manager', 'demo_purchasing', 'demo_qc', 'demo_staff', 'demo_accountant',
]

WAREHOUSES = [
    ('DEMO-KHO-01', 'Kho Hà Nội (Demo)'),
    ('DEMO-KHO-02', 'Kho Hải Phòng (Demo)'),
    ('DEMO-KHO-03', 'Kho Đà Nẵng (Demo)'),
    ('DEMO-KHO-04', 'Kho Hồ Chí Minh (Demo)'),
    ('DEMO-KHO-05', 'Kho Cần Thơ (Demo)'),
]

# Kho hệ thống singleton (BR mới, xem BACKLOG.md 1b) — bắt buộc phải tồn tại
# trước khi _run_po_to_qc_workflow gọi start_qc/qc_pass/qc_fail/qc_partial_pass,
# nếu không warehouse.services.get_staging_warehouse()/get_scrap_warehouse() raise.
STAGING_WAREHOUSE = ('DEMO-KHO-STG', 'Kho chờ (Demo)')
SCRAP_WAREHOUSE = ('DEMO-KHO-SCR', 'Kho phế (Demo)')

SUPPLIERS = [
    ('DEMO-NCC-01', 'Công ty TNHH Bột mì Miền Bắc', 7),
    ('DEMO-NCC-02', 'Công ty CP Đường Biên Hòa', 5),
    ('DEMO-NCC-03', 'Công ty TNHH Gia vị Việt', 10),
    ('DEMO-NCC-04', 'Công ty TNHH Hương liệu Á Châu', 14),
    ('DEMO-NCC-05', 'Công ty CP Phụ gia Thực phẩm VN', 10),
    ('DEMO-NCC-06', 'Công ty TNHH Dầu thực vật Tường An', 7),
    ('DEMO-NCC-07', 'Công ty CP Sữa Mộc Châu', 5),
    ('DEMO-NCC-08', 'Công ty TNHH Bao bì Đồng Tiến', 12),
    ('DEMO-NCC-09', 'Công ty TNHH Hóa chất Thực phẩm', 15),
    ('DEMO-NCC-10', 'Công ty CP Xuất nhập khẩu NVL', 9),
    ('DEMO-NCC-11', 'Công ty TNHH Thương mại Phú Thái', 6),
    ('DEMO-NCC-12', 'Công ty CP Nguyên liệu Sạch', 8),
]

# (name, category, uom, min_level, max_level, ordering_cost, holding_cost_rate)
PRODUCTS = [
    ('Bột mì số 8', 'Bột', 'kg', 200, 2000, Decimal('150000'), Decimal('12')),
    ('Bột mì số 11', 'Bột', 'kg', 200, 2000, Decimal('150000'), Decimal('12')),
    ('Bột gạo', 'Bột', 'kg', 100, 1000, Decimal('120000'), Decimal('10')),
    ('Bột năng', 'Bột', 'kg', 100, 1000, None, None),
    ('Đường tinh luyện', 'Đường', 'kg', 300, 3000, Decimal('100000'), Decimal('8')),
    ('Đường phèn', 'Đường', 'kg', 100, 800, None, None),
    ('Đường nâu', 'Đường', 'kg', 100, 800, Decimal('100000'), Decimal('8')),
    ('Muối tinh', 'Gia vị', 'kg', 150, 1200, Decimal('80000'), Decimal('6')),
    ('Muối i-ốt', 'Gia vị', 'kg', 150, 1200, None, None),
    ('Bột ngọt', 'Gia vị', 'kg', 80, 600, Decimal('90000'), Decimal('7')),
    ('Hương vani', 'Hương liệu', 'lít', 20, 150, Decimal('200000'), Decimal('15')),
    ('Hương dâu', 'Hương liệu', 'lít', 20, 150, None, None),
    ('Màu thực phẩm đỏ', 'Phụ gia', 'lít', 15, 100, Decimal('180000'), Decimal('15')),
    ('Màu thực phẩm vàng', 'Phụ gia', 'lít', 15, 100, None, None),
    ('Men nở', 'Phụ gia', 'kg', 30, 300, Decimal('130000'), Decimal('10')),
    ('Bột nở', 'Phụ gia', 'kg', 30, 300, Decimal('130000'), Decimal('10')),
    ('Dầu ăn', 'Dầu mỡ', 'lít', 100, 900, None, None),
    ('Bơ lạt', 'Dầu mỡ', 'kg', 50, 400, Decimal('160000'), Decimal('12')),
    ('Sữa bột', 'Sữa', 'kg', 60, 500, Decimal('220000'), Decimal('14')),
    ('Sữa đặc', 'Sữa', 'thùng', 40, 300, None, None),
    ('Bao bì PE 25kg', 'Bao bì', 'cái', 500, 5000, Decimal('50000'), Decimal('5')),
    ('Bao bì giấy Kraft', 'Bao bì', 'cái', 500, 5000, None, None),
    ('Thùng carton 5kg', 'Bao bì', 'cái', 300, 3000, Decimal('50000'), Decimal('5')),
    ('Nhãn dán sản phẩm', 'Bao bì', 'cuộn', 100, 1000, None, None),
    ('Chất bảo quản', 'Phụ gia', 'kg', 9999, 20000, Decimal('170000'), Decimal('15')),
    ('Chất tạo đặc', 'Phụ gia', 'kg', 9999, 20000, None, None),
    ('Vitamin premix', 'Phụ gia', 'kg', 20, 150, Decimal('250000'), Decimal('18')),
    ('Khoáng chất premix', 'Phụ gia', 'kg', 20, 150, None, None),
]

# i (PO index trong chuỗi PASS) -> product index bị ép buộc, để đảm bảo các
# sản phẩm demo tồn lỏng/near-expiry/expired rơi đúng vào nhánh QC PASS.
FORCED_PRODUCT_FOR_PO_INDEX = {15: 7}  # PO#15 (PASS) dùng Muối tinh (issue lâu rồi -> tồn lỏng)
NEAR_EXPIRY_PO_INDEXES = {16: 15, 17: 20}  # PO index -> số ngày tới hạn (trong 30 ngày)
EXPIRED_PO_INDEX = 18  # hạn đã qua -> sync_expired_batches() sẽ chuyển EXPIRED
NEVER_ISSUED_PRODUCT_INDEX = 19  # Sữa đặc: có tồn nhưng không GIN nào xuất -> tồn lỏng
ISSUED_LONG_AGO_PRODUCT_INDEX = 7  # Muối tinh: GIN xuất rồi nhưng backdate StockMovement > 180 ngày

PR_NOTES = [
    'Sắp hết hàng, cần bổ sung gấp.',
    'Chuẩn bị cho đơn hàng lớn tháng tới.',
    'Bổ sung tồn kho định kỳ.',
    'Khách yêu cầu gấp, cần nhập sớm.',
    '',
]

PR_REJECT_REASONS = [
    'Ngân sách quý này đã hết.',
    'Tồn kho hiện tại vẫn đủ dùng.',
    'Chờ đàm phán lại giá với NCC.',
    'Trùng với yêu cầu đã duyệt trước đó.',
]


class Command(BaseCommand):
    help = 'Tạo dữ liệu mẫu đầy đủ workflow (PO/GRN/QC/Inventory/GIN/Stocktake) để test UI thủ công.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--flush', action='store_true',
            help='Xoá sạch dữ liệu demo cũ (prefix DEMO-) trước khi tạo lại.',
        )

    def handle(self, *args, **options):
        if options['flush']:
            self._flush_demo_data()
        elif Warehouse.objects.filter(code__startswith='DEMO-KHO-').exists():
            self.stdout.write(self.style.WARNING(
                'Dữ liệu demo đã tồn tại. Chạy lại với --flush để xoá và tạo mới.'
            ))
            return

        with transaction.atomic():
            users = self._create_users()
            warehouses, locations = self._create_warehouses()
            suppliers = self._create_suppliers()
            products = self._create_products()
            self._create_qc_criteria()
            self._create_purchase_requests(users, warehouses, products, suppliers)
            available_batches = self._run_po_to_qc_workflow(
                users, warehouses, locations, suppliers, products,
            )
            self._create_gins(users, available_batches, products)
            self._create_stocktake(users, warehouses)
            sync_expired_batches()

        self.stdout.write(self.style.SUCCESS(
            'Đã tạo dữ liệu mẫu: 5 kho thành phẩm + Kho chờ + Kho phế, 12 NCC, 28 sản phẩm, '
            '45 PO, 20 yêu cầu mua hàng (PR), GIN, kiểm kê.'
        ))
        self.stdout.write('Dữ liệu demo được gắn với các tài khoản thật đã có sẵn (đăng nhập bằng mật khẩu bạn đã đặt):')
        for key, username, label in REQUIRED_USERS:
            self.stdout.write(f'  {username} — {label}')

    # ------------------------------------------------------------------ users
    def _create_users(self):
        users = {}
        missing = []
        for key, username, label in REQUIRED_USERS:
            user = User.objects.filter(username=username, is_deleted=False, is_active=True).first()
            if user is None:
                missing.append(f'{username} ({label})')
            else:
                users[key] = user
        if missing:
            raise CommandError(
                'Thiếu tài khoản thật cần thiết trong Quản lý User (chưa tạo, hoặc đã bị khoá/xoá): '
                + '; '.join(missing)
                + '. Hãy tạo/kích hoạt các tài khoản này (đúng role + phòng ban + is_manager) '
                  'trước khi chạy lại seed_demo_data.'
            )
        self.stdout.write('Users: dùng ' + ', '.join(u.username for u in users.values()) + ' (tài khoản thật có sẵn).')
        return users

    # ------------------------------------------------------------- warehouses
    def _create_warehouses(self):
        warehouses = []
        locations = {}
        for code, name in WAREHOUSES:
            wh = Warehouse.objects.create(code=code, name=name, warehouse_type=Warehouse.WarehouseType.MAIN)
            warehouses.append(wh)
            locations[wh.pk] = [
                Location.objects.create(warehouse=wh, code='A-01'),
                Location.objects.create(warehouse=wh, code='A-02'),
            ]

        # Kho chờ QC + Kho phế (singleton) — tạo trước khi chuỗi PO->GRN->QC chạy.
        for (code, name), warehouse_type in (
            (STAGING_WAREHOUSE, Warehouse.WarehouseType.STAGING),
            (SCRAP_WAREHOUSE, Warehouse.WarehouseType.SCRAP),
        ):
            wh = Warehouse.objects.create(code=code, name=name, warehouse_type=warehouse_type)
            Location.objects.create(warehouse=wh, code='A-01')

        self.stdout.write(
            f'Kho: {len(warehouses)} kho thành phẩm, {sum(len(v) for v in locations.values())} vị trí, '
            f'+ 1 Kho chờ, 1 Kho phế.'
        )
        return warehouses, locations

    # -------------------------------------------------------------- suppliers
    def _create_suppliers(self):
        suppliers = [
            Supplier.objects.create(supplier_code=code, name=name, lead_time_days=lead_time)
            for code, name, lead_time in SUPPLIERS
        ]
        self.stdout.write(f'Nhà cung cấp: {len(suppliers)}.')
        return suppliers

    # --------------------------------------------------------------- products
    def _create_products(self):
        products = []
        for idx, (name, category, uom, min_level, max_level, ordering_cost, holding_cost_rate) in enumerate(PRODUCTS):
            products.append(Product.objects.create(
                product_code=f'DEMO-SP-{idx + 1:03d}', name=name, category=category, uom=uom,
                min_level=min_level, max_level=max_level,
                ordering_cost=ordering_cost, holding_cost_rate=holding_cost_rate,
            ))
        self.stdout.write(f'Sản phẩm: {len(products)}.')
        return products

    # ------------------------------------------------------------ qc criteria
    def _create_qc_criteria(self):
        criteria = [
            ('DEMO-Bột', 'Ngoại hình', 'Màu trắng đều, không vón cục', 'Vón cục/lẫn tạp chất'),
            ('DEMO-Bột', 'Độ ẩm', '<= 13%', '> 13%'),
            ('DEMO-Đường', 'Ngoại hình', 'Tinh thể trắng, khô ráo', 'Ẩm/vón cục'),
            ('DEMO-Gia vị', 'Độ tinh khiết', '>= 99%', '< 99%'),
            ('DEMO-Hương liệu', 'Seal integrity', 'Nắp kín, không rò rỉ', 'Rò rỉ/hở nắp'),
            ('DEMO-Bao bì', 'Ngoại hình', 'Không rách, không ẩm mốc', 'Rách/ẩm mốc'),
        ]
        for category, name, pass_rule, fail_rule in criteria:
            QcCriteria.objects.create(category=category, name=name, pass_rule=pass_rule, fail_rule=fail_rule)
        self.stdout.write(f'Tiêu chuẩn QC: {len(criteria)}.')

    # -------------------------------------------------------- purchase requests
    def _create_purchase_requests(self, users, warehouses, products, suppliers):
        staff = users['warehouse_staff']
        purchasing_manager = users['purchasing_manager']
        manager = users['warehouse_manager']
        now = timezone.now()

        # 20 PR: 10 chờ duyệt, 6 đã duyệt (4 chưa tạo PO, 2 đã tạo PO), 4 từ chối.
        plan = (
            ['PENDING'] * 10 + ['APPROVED_NOT_CONVERTED'] * 4
            + ['APPROVED_CONVERTED'] * 2 + ['REJECTED'] * 4
        )
        prs = []
        for i, state in enumerate(plan):
            warehouse = warehouses[i % len(warehouses)]
            pr = PurchaseRequest.objects.create(
                requested_by=staff, warehouse=warehouse, note=PR_NOTES[i % len(PR_NOTES)],
            )
            item_count = (i % 3) + 1
            for j in range(item_count):
                product = products[(i * 3 + j) % len(products)]
                PurchaseRequestItem.objects.create(
                    purchase_request=pr, product=product, qty_requested=20 + (i * 7 + j * 11) % 180,
                )
            # Tạo PR tức là nộp (giống pr_create thật) -> luôn có Approval PENDING
            # đi kèm, nếu không thì "Duyệt"/"Từ chối" ở UI sẽ báo lỗi thiếu phiếu duyệt.
            submit_purchase_request(pr, actor=staff)

            if state == 'PENDING':
                prs.append(pr)
                continue

            approval = latest_approval_for(pr)
            decided_at = now - datetime.timedelta(days=(i % 10) + 1)

            if state == 'REJECTED':
                pr = decide_purchase_request(
                    approval, False, actor=manager,
                    note=PR_REJECT_REASONS[i % len(PR_REJECT_REASONS)],
                )
                pr.decided_at = decided_at
                pr.save(update_fields=['decided_at'])
                approval.refresh_from_db()
                approval.decided_at = decided_at
                approval.save(update_fields=['decided_at'])
                prs.append(pr)
                continue

            pr = decide_purchase_request(approval, True, actor=purchasing_manager)
            pr.decided_at = decided_at
            pr.save(update_fields=['decided_at'])
            approval.refresh_from_db()
            approval.decided_at = decided_at
            approval.save(update_fields=['decided_at'])

            if state == 'APPROVED_CONVERTED':
                po = PurchaseOrder.objects.create(
                    po_no=f'DEMO-PR-PO-{i + 1:03d}', supplier=suppliers[i % len(suppliers)],
                )
                for item in pr.items.all():
                    PurchaseOrderItem.objects.create(
                        purchase_order=po, product=item.product, qty_ordered=item.qty_requested,
                        unit_price=Decimal('15000'),
                    )
                pr.linked_po = po
                pr.save(update_fields=['linked_po'])

            prs.append(pr)

        self.stdout.write(
            f'Yêu cầu mua hàng (PR): {len(prs)} '
            f'({plan.count("PENDING")} chờ duyệt, '
            f'{plan.count("APPROVED_NOT_CONVERTED") + plan.count("APPROVED_CONVERTED")} đã duyệt, '
            f'{plan.count("REJECTED")} từ chối).'
        )
        return prs

    # ------------------------------------------------------- PO -> GRN -> QC
    def _run_po_to_qc_workflow(self, users, warehouses, locations, suppliers, products):
        manager = users['warehouse_manager']
        purchasing_staff = users['purchasing_staff']
        qc_user = users['qc_staff']
        today = timezone.localdate()

        # 45 PO: 3 DRAFT, 2 APPROVED, 10 SENT (chưa có GRN), 30 đã qua GRN/QC
        # (19 PASS, 5 FAIL, 4 PARTIAL_PASS, 2 nhận một phần + PASS).
        plan = (
            ['DRAFT'] * 3 + ['APPROVED'] * 2 + ['SENT_ONLY'] * 10
            + ['PASS'] * 19 + ['FAIL'] * 5 + ['PARTIAL'] * 4 + ['SPLIT_RECEIVE'] * 2
        )
        available_batches = []
        pass_seq = 0

        for i, group in enumerate(plan):
            supplier = suppliers[i % len(suppliers)]
            product_idx = FORCED_PRODUCT_FOR_PO_INDEX.get(i, i % len(products))
            product = products[product_idx]
            warehouse = warehouses[i % len(warehouses)]
            location = locations[warehouse.pk][i % 2]

            qty_ordered = 50 + (i * 13) % 150
            unit_price = Decimal(8000 + product_idx * 350 + (i % len(suppliers)) * 120)
            expected_delivery = today + datetime.timedelta(days=(5 if i % 2 == 0 else -5))

            po = PurchaseOrder.objects.create(
                po_no=f'DEMO-PO-{i + 1:04d}', supplier=supplier, expected_delivery_date=expected_delivery,
            )
            PurchaseOrderItem.objects.create(
                purchase_order=po, product=product, qty_ordered=qty_ordered, unit_price=unit_price,
            )
            if group == 'DRAFT':
                continue

            approve_po(po, actor=manager)
            if group == 'APPROVED':
                continue

            send_po(po, actor=purchasing_staff)
            if group == 'SENT_ONLY':
                continue

            qty_received = qty_ordered if group != 'SPLIT_RECEIVE' else int(qty_ordered * 0.6)
            if i in NEAR_EXPIRY_PO_INDEXES:
                exp_date = today + datetime.timedelta(days=NEAR_EXPIRY_PO_INDEXES[i])
            elif i == EXPIRED_PO_INDEX:
                exp_date = today - datetime.timedelta(days=10)
            else:
                exp_date = today + datetime.timedelta(days=365)

            grn = Grn.objects.create(po=po, supplier=supplier, created_by=purchasing_staff)
            grn_item = GrnItem.objects.create(
                grn=grn, product=product, qty_ordered=qty_ordered, qty_received=qty_received,
                unit_price=unit_price, mfg_date=today - datetime.timedelta(days=30), exp_date=exp_date,
            )
            submit_to_pending_qc(grn, actor=purchasing_staff)
            inspection = start_qc(grn, qc_user, actor=qc_user)
            QcInspectionItem.objects.create(
                inspection=inspection, grn_item=grn_item, criteria_name='Ngoại hình',
                result=QcInspectionItem.Result.FAIL if group == 'FAIL' else QcInspectionItem.Result.PASS,
            )

            if group == 'FAIL':
                qc_fail(inspection, actor=qc_user, reason='Ngoại hình không đạt tiêu chuẩn QC.')
            elif group == 'PARTIAL':
                qty_pass = int(qty_received * 0.6)
                qc_partial_pass(inspection, {grn_item.pk: qty_pass}, actor=qc_user, location=location)
                # Phase D: batch phần PASS dừng ở PENDING_RECEIPT chờ kho xác nhận —
                # demo data giả lập kho đã Nhận ngay để available_batches dùng được cho GIN bên dưới.
                for handoff in inspection.handoffs.filter(status='PENDING'):
                    accept_handoff(handoff, actor=manager)
                available_batches.append({
                    'product': product, 'warehouse': warehouse, 'qty': qty_pass,
                })
            else:  # PASS hoặc SPLIT_RECEIVE
                qc_pass(inspection, actor=qc_user, location=location)
                for handoff in inspection.handoffs.filter(status='PENDING'):
                    accept_handoff(handoff, actor=manager)
                # Batch hết hạn (EXPIRED_PO_INDEX) không đưa vào kế hoạch GIN — FIFO
                # sẽ tự loại nó (BR-GIN-007), cố xuất sẽ báo lỗi "không đủ tồn kho".
                if i != EXPIRED_PO_INDEX:
                    available_batches.append({
                        'product': product, 'warehouse': warehouse, 'qty': qty_received,
                    })

            sync_po_status(po)

            if group == 'PASS':
                pass_seq += 1
                if pass_seq <= 3:  # đóng vài PO/GRN đã RECEIVED để có ví dụ CLOSED
                    close_po(po, actor=manager)
                    close_grn(grn, actor=manager)

        # Sản phẩm "tồn lỏng chưa từng xuất": có tồn nhưng loại khỏi kế hoạch GIN,
        # rồi backdate created_at để days_idle > 180 (xem reports.services.slow_moving_items).
        never_issued_product = products[NEVER_ISSUED_PRODUCT_INDEX]
        Product.objects.filter(pk=never_issued_product.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=250),
        )

        self.stdout.write(
            f'PO/GRN/QC: {len(plan)} PO ({plan.count("PASS")} PASS, {plan.count("FAIL")} FAIL, '
            f'{plan.count("PARTIAL")} PARTIAL_PASS, {plan.count("SPLIT_RECEIVE")} nhận một phần).'
        )
        return available_batches

    # -------------------------------------------------------------------- GIN
    def _create_gins(self, users, available_batches, products):
        manager = users['warehouse_manager']
        staff = users['warehouse_staff']
        never_issued_code = products[NEVER_ISSUED_PRODUCT_INDEX].product_code
        issued_long_ago_code = products[ISSUED_LONG_AGO_PRODUCT_INDEX].product_code

        remaining = [b for b in available_batches if b['product'].product_code != never_issued_code]
        # Tách riêng batch "issue lâu rồi" — LUÔN đi qua nhánh ISSUED (không phải
        # DRAFT/PICKING), nếu không việc backdate StockMovement bên dưới vô nghĩa.
        long_ago_entry = next((b for b in remaining if b['product'].product_code == issued_long_ago_code), None)
        if long_ago_entry is not None:
            remaining.remove(long_ago_entry)

        plans = (
            [('DRAFT', 0.3)] * 2 + [('PICKING', 0.3)] * 2
            + [('ISSUED', 0.4)] * 3 + [('ISSUED_FULL', 1.0)] * 1 + [('ISSUED_CLOSED', 0.5)] * 2
        )
        gin_count = {'DRAFT': 0, 'PICKING': 0, 'ISSUED': 0, 'CLOSED': 0}
        long_ago_gin = None

        entries_and_plans = list(zip(remaining, plans))
        if long_ago_entry is not None:
            entries_and_plans.append((long_ago_entry, ('ISSUED_CLOSED', 0.5)))

        for entry, (state, ratio) in entries_and_plans:
            qty = max(1, int(entry['qty'] * ratio))
            gin = Gin.objects.create(
                warehouse=entry['warehouse'], reference_type=Gin.ReferenceType.SALES, requested_by=staff,
            )
            GinItem.objects.create(gin=gin, product=entry['product'], qty_requested=qty)

            if state == 'DRAFT':
                gin_count['DRAFT'] += 1
                continue
            start_picking(gin, actor=manager)
            if state == 'PICKING':
                gin_count['PICKING'] += 1
                continue
            issue_gin(gin, actor=manager)
            if entry['product'].product_code == issued_long_ago_code:
                long_ago_gin = gin
            if state in ('ISSUED', 'ISSUED_FULL'):
                gin_count['ISSUED'] += 1
                continue
            close_gin(gin, actor=manager)
            gin_count['CLOSED'] += 1

        # Backdate StockMovement ISSUE của GIN "issue lâu rồi" -> days_idle > 180.
        if long_ago_gin is not None:
            StockMovement.objects.filter(
                reference=long_ago_gin.gin_no, movement_type=StockMovement.MovementType.ISSUE,
            ).update(created_at=timezone.now() - datetime.timedelta(days=220))

        self.stdout.write(
            f'GIN: {gin_count["DRAFT"]} DRAFT, {gin_count["PICKING"]} PICKING, '
            f'{gin_count["ISSUED"]} ISSUED, {gin_count["CLOSED"]} CLOSED.'
        )

    # --------------------------------------------------------------- stocktake
    def _create_stocktake(self, users, warehouses):
        manager = users['warehouse_manager']
        staff = users['warehouse_staff']

        # Phiên 1 (kho #1): chạy hết vòng đời tới ADJUSTMENT.
        session1 = create_session(warehouse=warehouses[0], created_by=manager, actor=manager)
        start_execution(session1, actor=manager)
        items1 = list(session1.items.all())
        for idx, item in enumerate(items1):
            if idx == 0:
                record_count(item, item.qty_system - 3, counted_by=staff, reason=StocktakeItem.Reason.DAMAGE, actor=staff)
            elif idx == 1:
                record_count(item, item.qty_system + 2, counted_by=staff, reason=StocktakeItem.Reason.COUNTING_ERROR, actor=staff)
            else:
                record_count(item, item.qty_system, counted_by=staff, actor=staff)
        submit_reconciliation(session1, actor=manager)
        apply_adjustment(session1, actor=manager)

        # Phiên 2 (kho #2): dừng giữa chừng ở EXECUTION (mới đếm được một nửa).
        session2 = create_session(warehouse=warehouses[1], created_by=manager, actor=manager)
        start_execution(session2, actor=manager)
        items2 = list(session2.items.all())
        for item in items2[:max(1, len(items2) // 2)]:
            record_count(item, item.qty_system, counted_by=staff, actor=staff)

        self.stdout.write(
            f'Kiểm kê: {session1.so_no} (ADJUSTMENT, {len(items1)} SKU), '
            f'{session2.so_no} (EXECUTION, {len(items2)} SKU, mới đếm 1 phần).'
        )

    # -------------------------------------------------------------------- flush
    def _flush_demo_data(self):
        demo_warehouses = Warehouse.objects.filter(code__startswith='DEMO-KHO-')
        demo_suppliers = Supplier.objects.filter(supplier_code__startswith='DEMO-NCC-')
        demo_products = Product.objects.filter(product_code__startswith='DEMO-SP-')
        demo_pos = PurchaseOrder.objects.filter(supplier__in=demo_suppliers)

        if not (demo_warehouses.exists() or demo_suppliers.exists() or demo_products.exists()):
            self.stdout.write('Không có dữ liệu demo để xoá.')
        else:
            demo_locations = Location.objects.filter(warehouse__in=demo_warehouses)
            demo_grns = Grn.objects.filter(po__in=demo_pos)
            demo_grn_returns = GrnReturn.objects.filter(grn__po__in=demo_pos)
            demo_gins = Gin.objects.filter(warehouse__in=demo_warehouses)
            demo_sessions = StocktakeSession.objects.filter(warehouse__in=demo_warehouses)
            demo_prs = PurchaseRequest.objects.filter(warehouse__in=demo_warehouses)
            demo_handoffs = WarehouseHandoff.objects.filter(batch__product__in=demo_products)
            demo_transfers = StockTransfer.objects.filter(batch__product__in=demo_products)
            demo_qc_criteria = QcCriteria.objects.filter(category__startswith='DEMO-')

            # Notification/Approval/AuditLog.target là GenericFK (target_type +
            # target_id) -> không cascade tự động khi xoá đối tượng đích, và không
            # lọc được theo prefix DEMO- ngay trên chính 3 bảng đó. Thay vào đó thu
            # thập (ContentType, pk) của MỌI model từng được dùng làm target=... ở
            # log_action()/notify()/create_approval() (xem accounts.audit/
            # accounts.notifications/accounts.approvals + các *.services đang gọi
            # chúng) cho đúng các bản ghi demo sắp xoá bên dưới — làm TRƯỚC khi xoá,
            # để không lọc trên dữ liệu đã biến mất. Danh sách model này phải cập
            # nhật nếu sau này có model demo mới trở thành target= ở nơi khác.
            demo_target_querysets = [
                (Warehouse, demo_warehouses), (Location, demo_locations),
                (Product, demo_products), (Supplier, demo_suppliers),
                (PurchaseOrder, demo_pos), (PurchaseRequest, demo_prs),
                (Grn, demo_grns), (GrnReturn, demo_grn_returns),
                (Gin, demo_gins), (StocktakeSession, demo_sessions),
                (WarehouseHandoff, demo_handoffs), (StockTransfer, demo_transfers),
                (QcCriteria, demo_qc_criteria),
            ]
            target_q = None
            for model, qs in demo_target_querysets:
                ids = [str(pk) for pk in qs.values_list('pk', flat=True)]
                if not ids:
                    continue
                clause = Q(target_type=ContentType.objects.get_for_model(model), target_id__in=ids)
                target_q = clause if target_q is None else (target_q | clause)

            StocktakeItem.objects.filter(session__warehouse__in=demo_warehouses).delete()
            demo_sessions.delete()

            GinBatchAllocation.objects.filter(gin_item__gin__warehouse__in=demo_warehouses).delete()
            GinItem.objects.filter(gin__warehouse__in=demo_warehouses).delete()
            demo_gins.delete()

            StockMovement.objects.filter(warehouse__in=demo_warehouses).delete()
            demo_transfers.delete()

            # WarehouseHandoff.batch là OneToOne PROTECT (Phase D) -> phải xoá handoff
            # trước Batch, nếu không .delete() bên dưới raise ProtectedError (bug cũ:
            # hàm này chưa được cập nhật khi WarehouseHandoff ra đời).
            demo_handoffs.delete()

            # Batch.grn_item là PROTECT -> phải xoá Batch trước GrnItem (batch ở Kho
            # chờ/Kho phế đều trace về grn_item nguồn qua move_batch_qty, xem M4).
            Batch.objects.filter(product__in=demo_products).delete()
            Inventory.objects.filter(product__in=demo_products).delete()

            QcInspectionItem.objects.filter(inspection__grn__po__in=demo_pos).delete()
            QcInspection.objects.filter(grn__po__in=demo_pos).delete()

            demo_grn_returns.delete()
            GrnItem.objects.filter(grn__po__in=demo_pos).delete()
            demo_grns.delete()

            PurchaseOrderItem.objects.filter(purchase_order__in=demo_pos).delete()
            demo_pos.delete()

            # PurchaseRequest.warehouse la PROTECT -> phai xoa truoc demo_warehouses;
            # linked_po la SET_NULL nen khong phu thuoc thu tu voi demo_pos.delete() o tren.
            PurchaseRequestItem.objects.filter(purchase_request__warehouse__in=demo_warehouses).delete()
            demo_prs.delete()

            demo_locations.delete()
            demo_warehouses.delete()
            demo_products.delete()
            demo_suppliers.delete()
            demo_qc_criteria.delete()

            # Chỉ xoá đúng phạm vi demo đã thu thập ở target_q phía trên — không đụng
            # tới Notification/Approval/AuditLog của dữ liệu thật dù môi trường có
            # lẫn cả hai (khác hành vi cũ là xoá sạch cả bảng).
            if target_q is not None:
                notif_count, _ = Notification.objects.filter(target_q).delete()
                approval_count, _ = Approval.objects.filter(target_q).delete()
                audit_count, _ = AuditLog.objects.filter(target_q).delete()
            else:
                notif_count = approval_count = audit_count = 0
            self.stdout.write(
                f'Đã xoá {notif_count} notification, {approval_count} approval, {audit_count} audit log '
                f'(chỉ của dữ liệu demo).'
            )

            self.stdout.write(self.style.WARNING('Đã xoá toàn bộ dữ liệu demo cũ.'))

        # Tài khoản demo_* của phiên bản script cũ (đã bị thay bằng REQUIRED_USERS ở
        # trên) -> xoá cứng luôn cho gọn, theo xác nhận của người dùng.
        obsolete_users = User.objects.filter(username__in=OBSOLETE_DEMO_USERNAMES)
        deleted_usernames = list(obsolete_users.values_list('username', flat=True))
        if deleted_usernames:
            obsolete_users.delete()
            self.stdout.write(f'Đã xoá tài khoản demo cũ không còn dùng: {", ".join(deleted_usernames)}.')
