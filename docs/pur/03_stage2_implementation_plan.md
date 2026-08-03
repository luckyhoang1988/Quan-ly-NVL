# PUR Expansion — 03. Implementation Plan Stage 2 (Epic B, PUR-PR-01..07)

> Nguồn: `docs/pur/02_stage2_fsd.md` (**Approved v6**, duyệt bởi luckyhoang1988 ngày 03/08/2026).
> Trạng thái: **Approved — v3**.
> Người duyệt: **luckyhoang1988** | Ngày duyệt: **03/08/2026**.
> Được phép bắt đầu triển khai tuần tự theo TDD và các gate trong plan này.
> Quy ước: mọi tham chiếu `mục X` trong file này là mục của `02_stage2_fsd.md`, trừ khi ghi rõ khác.

**Mục tiêu**: hiện thực đầy đủ Stage 2 (PR 2.0 + Allocation) đúng theo FSD v6 — field PR mới,
`ExchangeRate`, `ProcurementAllocation`, non-catalog + map Product, build PO từ nhiều dòng PR,
migrate `linked_po` cũ, reconciliation legacy — theo trình tự migration → service → form/view →
management command → test, mỗi đơn vị đi qua TDD (test FAIL trước, code sau).

**Kiến trúc**: không đổi kiến trúc tổng thể (Django Template + Bootstrap 5 + HTMX, monolith, không
Celery/Redis). Toàn bộ field/model/service mới nằm trong app `purchasing` đã có sẵn — không tạo
app mới. `ProcurementAllocation` là model trung tâm mới, nối `PurchaseRequestItem` với
`PurchaseOrderItem` qua quan hệ nhiều-nhiều có ngữ nghĩa (nhiều allocation cho 1 cặp qua thời gian,
nhưng tại một thời điểm tổng `qty_allocated` các allocation `ACTIVE` là nguồn sự thật cho
`qty_ordered` của PO nguồn `FROM_PR`).

**Công nghệ**: không thêm thư viện mới. Django ORM (`F()`, `select_for_update()`,
`CheckConstraint`), `django.core.management.BaseCommand`, `threading.Barrier` +
`TransactionTestCase` cho test deadlock (mirror `stocktake.tests`).

## Ràng buộc chung (Global Constraints)

- Toàn bộ transition/tạo dữ liệu nghiệp vụ nằm trong `@transaction.atomic` — không có
  service function nào ghi DB ngoài transaction.
- **Lock order bắt buộc cho mọi hàm đụng ≥2 trong 4 model sau** (mục 4 điểm 2, nhắc lại ở mục 4
  điểm 4/mục 8 cho hàm batch): `PurchaseOrder → PurchaseOrderItem → PurchaseRequestItem →
  ProcurementAllocation`. Khoá bằng các lệnh `select_for_update().get(pk=...)` **tuần tự, tách rời**
  theo đúng thứ tự trên (không dùng `select_related(...).select_for_update()` một lệnh duy nhất cho
  nhiều bảng — cách đó không đảm bảo thứ tự lock giữa các bảng, mirror cách
  `inventory.services.lock_inventories` đã làm). Khi có nhiều dòng cùng loại (nhiều `pr_item`/nhiều
  `ProcurementAllocation` trong 1 lần gọi), khoá theo `order_by('pk')` tăng dần.
- **Quy ước F()/refresh_from_db() (lưu ý kỹ thuật #1)**: bất kỳ chỗ nào dùng
  `obj.field = F('field') ± value; obj.save(update_fields=['field'])` để tăng/giảm nguyên tử dưới
  khoá, `obj.field` sau lệnh `save()` vẫn là một `CombinedExpression` chưa evaluate, **không phải**
  giá trị số thật — mọi chỗ cần dùng lại giá trị đó ngay sau đó (ghi vào `AuditLog.description`,
  trả về cho caller, so sánh lại để re-assert bất biến) **bắt buộc** gọi
  `obj.refresh_from_db(fields=['field'])` ngay sau `save()`, trước khi đọc lại `obj.field`. Thiếu
  bước này sẽ in ra chuỗi biểu diễn expression (vd `F(field) + Value(5)`) thay vì số, hoặc so sánh
  luôn sai khi re-assert bất biến. Áp dụng ở `create_allocation()`, `release_allocation()`,
  `reconcile_legacy_po_item_allocations()` — mọi nơi có `F()` trên `qty_ordered`.
- **Quy ước chuẩn hoá so sánh raw POST (lưu ý kỹ thuật #2)**: `po_update` so giá trị POST thô của 1
  field `disabled=True` với giá trị đang lưu trong DB qua đúng 1 helper dùng chung
  `_raw_disabled_field_tampered(request.POST, form, field_name, db_value)` (Task 3.7) — không viết
  logic so sánh riêng lẻ ở 2 chỗ (formset cấp `po_update`, và guard mô tả lại ở mục 4 điểm 4/mục 8 —
  cả 2 đều là cùng 1 hàm `po_update`, không phải 2 call site độc lập). Chuẩn hoá cụ thể: field
  `product` (FK, render `<select>`) — key vắng mặt trong `request.POST` ⇒ không tamper; có mặt ⇒
  `str(db_value.pk) != post_value.strip()` (so sánh chuỗi PK, không so `None`/rỗng lẫn lộn). Field
  `qty_ordered` (số nguyên) — key có mặt ⇒ thử `int(post_value)`; **không parse được** (chuỗi rác)
  coi là tampering ngay (không crash 500); parse được ⇒ so **số nguyên** `int(post_value) !=
  db_value` (không so chuỗi — `"007" != "7"` sẽ sai nếu so chuỗi).
- **Quy ước rollback `--dry-run` bằng savepoint thật (lưu ý kỹ thuật #3)**: management command
  `reconcile_legacy_po_item_allocations` (Task 4.3) **không** viết 2 đường code riêng cho dry-run và
  chạy thật. Cả 2 đều gọi đúng 1 hàm service `reconcile_legacy_po_item_allocations()` bên trong
  `transaction.atomic()`; khi `--dry-run`, sau khi hàm service trả về thành công, command chủ động
  `raise _DryRunRollback(...)` (exception nội bộ, không kế thừa `ValidationError`) ngay trước khi
  thoát khối `atomic()` để buộc Postgres `ROLLBACK` toàn bộ savepoint — rồi bắt đúng exception đó
  ngay bên ngoài khối `atomic()` để in báo cáo before/after. Cách này đảm bảo dry-run chạy **đúng**
  connect toàn bộ validation/constraint DB thật (không phải bản rút gọn), tuyệt đối không để lọt 1
  dòng ghi nào xuống DB.
- Test mới thêm vào **`purchasing/tests.py`** (file hiện có, không tách package `tests/` — ngoài
  phạm vi plan này, xem ghi chú cuối). Nhóm theo class mới, đặt tên theo đúng convention TC đã có
  (`TC-PUR-PR-0X-0YY`). Import thêm ở đầu file khi cần (`ProcurementAllocation`, `ExchangeRate`,
  các service function mới) — không import `*`.
- Toàn bộ `verbose_name`/message lỗi/label mới phải tiếng Việt (quy ước UI toàn dự án, xem
  CLAUDE.md).
- Sau khi 1 Task đổi field/`Meta`, chạy `manage.py makemigrations purchasing` — không tự tay viết
  migration trừ khi ghi rõ (2 migration data/schema ở Phase 1 viết tay 1 phần theo mẫu bên dưới).

## Bản đồ file

| File | Vai trò trong Stage 2 |
|---|---|
| `purchasing/models.py` | Field mới trên `PurchaseRequest`/`PurchaseRequestItem`, model `Currency`/`ExchangeRate`/`ProcurementAllocation`, property `qty_*`/`is_non_catalog` |
| `purchasing/services.py` | Toàn bộ service function mới (Phase 2) + guard mới trong `send_po()` |
| `purchasing/forms.py` | Form/formset PR, form PO-item khoá field cho nguồn `FROM_PR`, form build-PO, form map non-catalog, form `ExchangeRate` |
| `purchasing/views.py` | View mới + rewrite `po_update` |
| `purchasing/urls.py` | Route cho view mới |
| `purchasing/admin.py` | `ServiceManagedAdminMixin` cho `ProcurementAllocation` (không cho sửa qua Admin) |
| `purchasing/migrations/0017_*.py` | Schema: field mới + bảng `ExchangeRate`/`ProcurementAllocation` |
| `purchasing/migrations/0018_*.py` | Data: backfill `ProcurementAllocation` từ `linked_po` |
| `purchasing/management/commands/report_allocation_migration_exceptions.py` | Xem lại báo cáo ngoại lệ migration bất kỳ lúc nào |
| `purchasing/management/commands/check_non_catalog_sla.py` | Cron SLA 3 ngày làm việc non-catalog |
| `purchasing/management/commands/reconcile_legacy_po_item_allocations.py` | Wrap hàm batch reconcile, có `--dry-run` |
| `accounts/permissions.py` | Thêm `MENU_ITEMS['exchange_rate']`, sửa `codenames_for_role` để field này **không** default-grant mọi role |
| `purchasing/templates/purchasing/*.html` | Template mới/sửa cho các view trên |
| `purchasing/tests.py` | Toàn bộ test mới (Phase 1-5 đều có phần test riêng, Phase 5 gom các TC không thuộc 1 unit cụ thể) |

Thứ tự thực thi tổng quát: **Phase 1 (Migration) → Phase 2 (Service) → Phase 3 (Form/View) →
Phase 4 (Management command) → Phase 5 (Test bổ sung/regression)**. Trong mỗi Task ở Phase 1-4 vẫn
áp dụng TDD đầy đủ (test FAIL trước — code sau) cho đơn vị của Task đó; Phase 5 gom các TC rộng hơn
1 unit (constraint DB, concurrency, migration idempotency, permission) chưa được viết inline ở
Phase 1-4.

---

# Phase 1 — Migration (schema + backfill)

## Task 1.1: Toàn bộ schema Stage 2 — field PR mới, `Currency`, `ExchangeRate`, `ProcurementAllocation` + migration `0017`

**Ghi chú cấu trúc task (v2 — gộp từ 5 task riêng ở v1 sau khi review phát hiện lỗi trình tự)**:
`manage.py test` build DB test **từ migration**, không đọc trực tiếp `models.py` — nếu tách nhỏ
theo từng model rồi hoãn `makemigrations` đến task cuối (như v1 từng làm), "Bước 4: chạy test xác
nhận PASS" của mọi task con ở giữa sẽ FAIL với lỗi `column ... does not exist`, vì DB test không có
cột model vừa thêm cho tới khi migration được sinh. 4 model ở đây cũng phụ thuộc lẫn nhau
(`ProcurementAllocation` cần field mới trên `PurchaseRequestItem`/`PurchaseOrderItem` đã tồn tại),
nên nguyên tử-hoá đúng cách là: viết đủ toàn bộ test trước → đổi đủ toàn bộ `models.py` → sinh
**1** migration `0017` → migrate → chạy test — một lần, một task, một commit.

**File:**
- Sửa: `purchasing/models.py` (class `PurchaseRequest`, class `PurchaseRequestItem`, thêm class
  `Currency`, `ExchangeRate`, `ProcurementAllocation`)
- Sửa: `purchasing/admin.py` (đăng ký `ProcurementAllocationAdmin`)
- Tạo: `purchasing/migrations/0017_pr_stage2_fields_exchangerate_allocation.py`
- Test: `purchasing/tests.py` (class mới `PurchaseRequestFieldsTest`, `PurchaseRequestItemFieldsTest`,
  `ExchangeRateModelTest`, `ProcurementAllocationModelTest`)

**Giao diện:**
- Cung cấp: `Currency` (dùng bởi `ExchangeRate`, Task 3.2/3.8 form), `PurchaseRequest.cost_center`/
  `.department_snapshot`/`.project` (dùng bởi Task 2.9/2.12, Task 3.1), toàn bộ field mục 2.2 trên
  `PurchaseRequestItem` + property `is_non_catalog`/`qty_allocated`/`qty_ordered`/`qty_open`/
  `qty_received`, `ExchangeRate`, `ProcurementAllocation` (dùng khắp Phase 2/3).
- Sử dụng: `purchasing.services.qty_received_by_allocation` (Task 2.11) — forward reference hợp lệ:
  hàm chỉ được import cục bộ **bên trong** property `qty_received`, Python chỉ resolve tên khi
  property thực sự được GỌI, không phải lúc định nghĩa class hay lúc chạy test Task này. Property
  `qty_received` chưa có test riêng ở Task này — test của nó nằm ở Task 2.11 sau khi hàm thật tồn tại.

**Quyết định cụ thể hoá** (FSD mục 2.1 không ghi rõ `default=` cho 3 field `PurchaseRequest`, chỉ
ghi mục 9 "toàn bộ nullable/có default" áp dụng cho migration): cả 3 field dùng `default=''` (không
`null=True`) — `cost_center` bắt buộc ở tầng form (`CharField` mặc định `blank=False` ⇒ `ModelForm`
field `required=True`), `project` optional (`blank=True`); migration áp dụng `default=''` cho các
dòng `PurchaseRequest` cũ hiện có (không suy đoán giá trị thật — dòng cũ hiển thị rỗng cho tới khi
có ai sửa). Riêng `ExchangeRate` thêm 1 `CheckConstraint` `currency != 'VND'` bên cạnh validate ở
form (Task 3.8) — chặn đường ghi trực tiếp qua service/shell/Admin, không chỉ qua form.

- [ ] **Bước 1: Viết toàn bộ test đang FAIL**
```python
class PurchaseRequestFieldsTest(TestCase):
    def test_TC_PUR_PR_new_fields_exist_with_correct_defaults(self):
        user = User.objects.create_user(username='rq1', password='rq-pass-123', role=User.Role.STAFF)
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        pr = PurchaseRequest.objects.create(
            requested_by=user, warehouse=warehouse, cost_center='CC-001')
        self.assertEqual(pr.cost_center, 'CC-001')
        self.assertEqual(pr.department_snapshot, '')
        self.assertEqual(pr.project, '')


class PurchaseRequestItemFieldsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='rq1', password='rq-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg', category='Nguyên liệu')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.user, warehouse=self.warehouse, cost_center='CC-001')

    def test_TC_PUR_PR_01_catalog_item_default_qty_properties_zero(self):
        item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'),
            budget_category='Nguyên liệu')
        self.assertFalse(item.is_non_catalog)
        self.assertEqual(item.qty_allocated, 0)
        self.assertEqual(item.qty_ordered, 0)
        self.assertEqual(item.qty_open, 0)  # qty_approved=None -> qty_open=0 (mục 2.2)

    def test_TC_PUR_PR_01_non_catalog_item_is_non_catalog_true(self):
        item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=None, qty_requested=5,
            non_catalog_name='Ống nhựa PVC', non_catalog_uom='cây',
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('50000'),
            budget_category='Vật tư')
        self.assertTrue(item.is_non_catalog)


class ExchangeRateModelTest(TestCase):
    def test_TC_PUR_XR_unique_currency_rate_date(self):
        admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        ExchangeRate.objects.create(
            currency='USD', rate_date=timezone.localdate(), rate_to_vnd=Decimal('25000'),
            created_by=admin_user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExchangeRate.objects.create(
                    currency='USD', rate_date=timezone.localdate(), rate_to_vnd=Decimal('25100'),
                    created_by=admin_user)

    def test_TC_PUR_XR_currency_cannot_be_vnd(self):
        admin_user = User.objects.create_user(username='admin2', password='admin-pass-123', role=User.Role.ADMIN)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExchangeRate.objects.create(
                    currency='VND', rate_date=timezone.localdate(), rate_to_vnd=Decimal('1'),
                    created_by=admin_user)


class ProcurementAllocationModelTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.user = User.objects.create_user(username='rq1', password='rq-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.user, warehouse=self.warehouse, cost_center='CC-001', status=PurchaseRequest.Status.APPROVED)
        self.pr_item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'),
            budget_category='Nguyên liệu')
        self.po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        self.po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('1000'))

    def test_TC_PUR_PR_05_009_active_allocation_requires_po_item(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProcurementAllocation.objects.create(
                    pr_item=self.pr_item, po_item=None, qty_allocated=5,
                    status=ProcurementAllocation.Status.ACTIVE,
                    po_no_snapshot=self.po.po_no, product_code_snapshot=self.product.product_code,
                )

    def test_TC_PUR_PR_05_011_allocation_qty_positive(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProcurementAllocation.objects.create(
                    pr_item=self.pr_item, po_item=self.po_item, qty_allocated=0,
                    po_no_snapshot=self.po.po_no, product_code_snapshot=self.product.product_code,
                )
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** —
  `manage.py test purchasing.tests.PurchaseRequestFieldsTest purchasing.tests.PurchaseRequestItemFieldsTest purchasing.tests.ExchangeRateModelTest purchasing.tests.ProcurementAllocationModelTest -v 2`,
  kỳ vọng: `TypeError: ... unexpected keyword argument 'cost_center'` và
  `NameError: name 'ExchangeRate'/'ProcurementAllocation' is not defined` (field/model chưa tồn tại).
- [ ] **Bước 3: Viết toàn bộ code model tối thiểu để PASS**

  3a. Thêm import ở đầu `purchasing/models.py` (kiểm tra trước, tránh trùng):
  `from django.db.models import Q` và `from accounts.models import User` nếu chưa có.

  3b. Thêm module-level, trước class `PurchaseOrder`:
```python
class Currency(models.TextChoices):
    VND = 'VND', 'VND'
    USD = 'USD', 'USD'
    EUR = 'EUR', 'EUR'
    JPY = 'JPY', 'JPY'
    CNY = 'CNY', 'CNY'


class ExchangeRate(models.Model):
    currency = models.CharField(max_length=3, choices=Currency.choices, verbose_name='Loại tiền')
    rate_date = models.DateField(verbose_name='Ngày áp dụng')
    rate_to_vnd = models.DecimalField(
        max_digits=14, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))],
        verbose_name='Tỷ giá quy đổi VND', help_text='1 đơn vị ngoại tệ = ? VND.')
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, verbose_name='Người nhập')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày nhập')

    class Meta:
        verbose_name = 'Tỷ giá ngoại tệ'
        verbose_name_plural = 'Tỷ giá ngoại tệ'
        ordering = ['-rate_date', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['currency', 'rate_date'], name='unique_currency_rate_date'),
            models.CheckConstraint(condition=~Q(currency='VND'), name='exchange_rate_currency_not_vnd'),
        ]

    def __str__(self):
        return f'{self.currency} @ {self.rate_date}: {self.rate_to_vnd}'


class ProcurementAllocation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Đang hiệu lực'
        RELEASED = 'RELEASED', 'Đã giải phóng'

    pr_item = models.ForeignKey(
        'PurchaseRequestItem', on_delete=models.PROTECT, related_name='allocations',
        verbose_name='Dòng yêu cầu mua hàng')
    po_item = models.ForeignKey(
        'PurchaseOrderItem', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='allocations', verbose_name='Dòng đơn mua hàng')
    po_no_snapshot = models.CharField(max_length=30, blank=True, editable=False, verbose_name='Số PO (lưu vết)')
    product_code_snapshot = models.CharField(max_length=50, blank=True, editable=False, verbose_name='Mã sản phẩm (lưu vết)')
    qty_allocated = models.PositiveIntegerField(validators=[MinValueValidator(1)], verbose_name='Số lượng phân bổ')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, verbose_name='Trạng thái')
    released_reason = models.TextField(blank=True, verbose_name='Lý do giải phóng')
    released_by = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        verbose_name='Người giải phóng')
    released_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời điểm giải phóng')
    created_by = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        verbose_name='Người tạo phân bổ')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        verbose_name = 'Phân bổ PR-PO'
        verbose_name_plural = 'Phân bổ PR-PO'
        constraints = [
            models.CheckConstraint(
                condition=Q(status='RELEASED') | Q(po_item__isnull=False),
                name='active_allocation_requires_po_item'),
            models.CheckConstraint(
                condition=(
                    Q(status='ACTIVE', released_at__isnull=True, released_by__isnull=True, released_reason='')
                    | (Q(status='RELEASED', released_at__isnull=False) & ~Q(released_reason=''))
                ),
                name='allocation_release_fields_match_status'),
            models.CheckConstraint(condition=Q(qty_allocated__gte=1), name='allocation_qty_positive'),
            models.CheckConstraint(
                condition=~Q(po_no_snapshot='') & ~Q(product_code_snapshot=''),
                name='allocation_snapshots_required'),
        ]

    def __str__(self):
        return f'{self.pr_item} -> {self.po_no_snapshot} ({self.qty_allocated})'
```

  3c. Sửa `class PurchaseRequest` — thêm 3 field mới (sau field `note`, trước `status`):
```python
    cost_center = models.CharField(
        max_length=50, default='', verbose_name='Trung tâm chi phí',
        help_text='Bắt buộc — khoá ngân sách theo quyết định #2 (cost_center + budget_category dòng PR).')
    department_snapshot = models.CharField(
        max_length=20, choices=User.Department.choices, blank=True, default='', editable=False,
        verbose_name='Phòng ban (snapshot lúc nộp)',
        help_text='Set tự động trong submit_purchase_request() — bất biến sau khi set, không đọc lại '
                   'requested_by.department nếu người đó đổi phòng ban sau này.')
    project = models.CharField(max_length=100, blank=True, default='', verbose_name='Dự án (tuỳ chọn)')
```

  3d. Sửa `class PurchaseRequestItem` — sửa field `product` hiện có (thêm `null=True, blank=True`,
  giữ nguyên `on_delete=models.PROTECT`), thêm field mới ngay sau `qty_requested`, thêm property ở
  cuối class (sau `__str__`):
```python
    non_catalog_name = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Tên hàng (chưa có trong danh mục)')
    non_catalog_uom = models.CharField(
        max_length=20, blank=True, default='', verbose_name='Đơn vị tính (đề xuất)')
    non_catalog_note = models.TextField(blank=True, default='', verbose_name='Mô tả/quy cách (non-catalog)')
    required_date = models.DateField(null=True, blank=True, verbose_name='Ngày cần hàng')
    budget_category = models.CharField(
        max_length=100, null=True, blank=True, verbose_name='Nhóm ngân sách/Tài khoản')
    currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.VND, verbose_name='Loại tiền')
    estimated_unit_price = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(0)], null=True, blank=True,
        verbose_name='Đơn giá ước tính')
    qty_approved = models.PositiveIntegerField(null=True, blank=True, verbose_name='Số lượng được duyệt')
    qty_cancelled = models.PositiveIntegerField(default=0, verbose_name='Số lượng đã huỷ (phần còn mở)')
```
```python
    @property
    def is_non_catalog(self):
        return self.product_id is None

    @property
    def qty_allocated(self):
        from django.db.models import Sum
        return self.allocations.filter(
            status=ProcurementAllocation.Status.ACTIVE,
        ).aggregate(total=Sum('qty_allocated'))['total'] or 0

    @property
    def qty_ordered(self):
        from django.db.models import Sum
        committed_statuses = (
            PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIAL_RECEIVED,
            PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.CLOSED,
        )
        return self.allocations.filter(
            status=ProcurementAllocation.Status.ACTIVE,
            po_item__purchase_order__status__in=committed_statuses,
        ).aggregate(total=Sum('qty_allocated'))['total'] or 0

    @property
    def qty_open(self):
        return max(0, (self.qty_approved or 0) - self.qty_allocated - self.qty_cancelled)

    @property
    def qty_received(self):
        from .services import qty_received_by_allocation
        total = 0
        for allocation in self.allocations.filter(
                status=ProcurementAllocation.Status.ACTIVE, po_item__isnull=False):
            total += qty_received_by_allocation(allocation.po_item).get(allocation.pk, 0)
        return total
```

  3e. Đăng ký Admin (`purchasing/admin.py`, thêm sau `PurchaseRequestAdmin`, mở rộng import hiện có
  từ `.models`):
```python
from .models import PurchaseOrder, PurchaseOrderItem, PurchaseRequest, PurchaseRequestItem, ProcurementAllocation

@admin.register(ProcurementAllocation)
class ProcurementAllocationAdmin(ServiceManagedAdminMixin, admin.ModelAdmin):
    list_display = ('pr_item', 'po_no_snapshot', 'product_code_snapshot', 'qty_allocated', 'status', 'created_at')
    list_filter = ('status',)
```
- [ ] **Bước 4: Sinh + áp migration**
  - `manage.py makemigrations purchasing` — xác nhận Django sinh đúng **1** file mới, nội dung gồm:
    `AlterField('product')` (thêm `null=True`), `AddField` cho 3 field `PurchaseRequest` + **9**
    field `PurchaseRequestItem` (đếm đúng: `non_catalog_name`, `non_catalog_uom`, `non_catalog_note`,
    `required_date`, `budget_category`, `currency`, `estimated_unit_price`, `qty_approved`,
    `qty_cancelled`), `CreateModel` cho `ExchangeRate` (1 `UniqueConstraint` unique_currency_rate_date + 1
    `CheckConstraint` exchange_rate_currency_not_vnd) và `ProcurementAllocation` (4 `CheckConstraint`). Không có `RunPython` nào trong migration
    này (đúng mục 9 — toàn bộ nullable/có default, không `UniqueConstraint`/`NOT NULL` mới nào có
    thể bị dữ liệu cũ vi phạm).
  - Đổi tên file thành `0017_pr_stage2_fields_exchangerate_allocation.py` (mirror cách đặt tên
    `0014_backfill_pr_two_stage_status.py`).
  - `manage.py migrate purchasing` trên DB dev — xác nhận chạy sạch, không lỗi.
- [ ] **Bước 5: Chạy test, xác nhận PASS** —
  `manage.py test purchasing.tests.PurchaseRequestFieldsTest purchasing.tests.PurchaseRequestItemFieldsTest purchasing.tests.ExchangeRateModelTest purchasing.tests.ProcurementAllocationModelTest -v 2`
  (Django tự build DB test từ migration `0017` vừa tạo nên PASS ngay lần chạy đầu — không còn lỗi
  "column does not exist" như cấu trúc tách-nhỏ ở v1).
- [ ] **Bước 6: Commit**
```bash
git add purchasing/models.py purchasing/admin.py purchasing/migrations/0017_pr_stage2_fields_exchangerate_allocation.py purchasing/tests.py
git commit -m "feat(pur): stage2 schema - PR/PRItem fields, Currency, ExchangeRate, ProcurementAllocation (migration 0017)"
```

## Task 1.2: Data migration `0018` — backfill `ProcurementAllocation` từ `linked_po`

**File:**
- Tạo: `purchasing/migrations/0018_backfill_procurement_allocation_from_linked_po.py`
- Test: `purchasing/tests.py` (class mới `AllocationBackfillMigrationTest`) — dùng
  `django.test.migrations.MigratorTestCase`-style thủ công KHÔNG khả dụng ở bản Django hiện tại của
  dự án (không thêm dependency `django-test-migrations`); thay vào đó test gọi thẳng hàm
  `apps.get_model()`-based logic đã tách thành 1 hàm module-level thuần (không phụ thuộc
  `RunPython`/`apps` cụ thể) để có thể import và gọi trực tiếp trong `TestCase` thường, đồng thời
  vẫn dùng `apps.get_model()` bên trong `RunPython` thật (đúng nguyên tắc PUR-FND-06 — không import
  `purchasing.models`/`purchasing.services` bên trong migration).

**Giao diện:**
- Cung cấp: hàm thuần `_backfill_allocations_from_linked_po(PurchaseRequestModel,
  PurchaseRequestItemModel, PurchaseOrderItemModel, ProcurementAllocationModel)` — nhận model
  classes làm tham số (không import cứng) để vừa dùng được trong `RunPython` (truyền historical
  model qua `apps.get_model()`) vừa test được trực tiếp (truyền model thật qua `apps.get_model()`
  trong test, xem mẫu dưới).

- [ ] **Bước 1: Viết test đang FAIL**
```python
from django.apps import apps as django_apps

class AllocationBackfillMigrationTest(TestCase):
    """TC-PUR-MIG-001/002: backfill dùng hàm thuần tách khỏi RunPython (import trực tiếp
    module migration bằng importlib, đúng cách test 1 RunPython function mà không cần chạy
    lại toàn bộ chuỗi migration — mirror cách dự án test các RunPython khác nếu có, nếu chưa
    có tiền lệ thì đây là ca đầu, giữ pattern đơn giản: import module bằng importlib.import_module
    theo đường dẫn file, gọi thẳng hàm nội bộ)."""

    def setUp(self):
        self.user = User.objects.create_user(username='rq1', password='rq-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        self.po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('1000'))
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.user, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED, linked_po=self.po)
        self.pr_item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'),
            budget_category='Nguyên liệu')

    def _run_backfill(self):
        migration_module = import_module('purchasing.migrations.0018_backfill_procurement_allocation_from_linked_po')
        migration_module.backfill_allocations(django_apps, None)

    def test_TC_PUR_MIG_001_clear_match_creates_one_allocation(self):
        self._run_backfill()
        allocation = ProcurementAllocation.objects.get(pr_item=self.pr_item)
        self.assertEqual(allocation.qty_allocated, 10)
        self.assertIsNone(allocation.created_by)
        self.pr_item.refresh_from_db()
        self.assertEqual(self.pr_item.qty_approved, 10)

    def test_TC_PUR_MIG_002_ambiguous_match_creates_no_allocation(self):
        # 2 dòng PR cùng product trỏ 1 PO cùng product -> không khớp rõ ràng
        PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=3,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'),
            budget_category='Nguyên liệu')
        self._run_backfill()
        self.assertEqual(ProcurementAllocation.objects.filter(pr_item__purchase_request=self.pr).count(), 0)
```
  (`import_module('purchasing.migrations.0018_...')` — tên module bắt đầu bằng số nên Python
  `import_module` với chuỗi vẫn hoạt động dù cú pháp `import` thường không cho phép tên bắt đầu
  bằng số; dự án đã dùng đúng cách này ở test khác — xem `from importlib import import_module` đã
  có sẵn ở đầu `purchasing/tests.py`.)
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ModuleNotFoundError` (file migration chưa tồn tại).
- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
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
        print(f'[0018] {len(exceptions)} ngoại lệ migration allocation — xem chi tiết qua '
              f'`manage.py report_allocation_migration_exceptions`.')


class Migration(migrations.Migration):
    dependencies = [('purchasing', '0017_pr_stage2_fields_exchangerate_allocation')]
    operations = [migrations.RunPython(backfill_allocations, migrations.RunPython.noop)]
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5**: viết thêm `test_TC_PUR_MIG_003_rerun_is_idempotent` (gọi `_run_backfill()` 2 lần
  liên tiếp, đếm `ProcurementAllocation.objects.count()` không đổi giữa 2 lần) — chạy FAIL trước
  (nếu thiếu check `.exists()` ở trên sẽ tạo trùng), xác nhận code Bước 3 đã có guard
  `if ... .exists(): continue` nên PASS ngay không cần sửa thêm.
- [ ] **Bước 6: Commit**
```bash
git add purchasing/migrations/0018_backfill_procurement_allocation_from_linked_po.py purchasing/tests.py
git commit -m "feat(pur): migration 0018 - backfill ProcurementAllocation from linked_po (idempotent)"
```

---

# Phase 2 — Service

Toàn bộ hàm dưới đây thêm vào `purchasing/services.py`, import `ProcurementAllocation`,
`PurchaseRequestItem`, `PurchaseOrderItem`, `Q`, `F` khi cần (kiểm tra import hiện có trước khi
thêm trùng — file đã import `transaction`, `ValidationError`, `log_action`, `AuditLog`, `User`,
`notify`).

## Task 2.1: `create_allocation(pr_item, po_item, qty, actor, ip_address=None)`

**File:**
- Sửa: `purchasing/services.py`
- Test: `purchasing/tests.py` (class mới `CreateAllocationTest`)

**Giao diện:**
- Sử dụng: `ProcurementAllocation`, `PurchaseOrder.Status.DRAFT`, `PurchaseOrder.Source.FROM_PR`,
  `PurchaseRequest.Status.APPROVED`, lock order chung (Ràng buộc chung).
- Cung cấp: `create_allocation(pr_item, po_item, qty, actor, ip_address=None) -> ProcurementAllocation`
  — dùng bởi Task 3.6 (view build PO đơn lẻ), Task 5.2 (concurrency test); và hàm nội bộ
  `_create_allocation_locked(pr_item, po, po_item, qty, actor, ip_address=None)` (không tự khoá) —
  dùng bởi Task 2.5 (`build_po_from_allocations`, khoá nhiều `PurchaseRequestItem` theo pk 1 lần
  trước khi gọi lần lượt, xem Task 2.5).

- [ ] **Bước 1: Viết test đang FAIL (AC #4 — chặn `qty > qty_open`)**
```python
class CreateAllocationTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.admin_user, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED)
        self.pr_item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'),
            budget_category='Nguyên liệu')
        self.po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        self.po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=0, unit_price=Decimal('1000'))

    def test_TC_PUR_PR_04_001_qty_exceeds_open_rejected(self):
        with self.assertRaises(ValidationError):
            create_allocation(self.pr_item, self.po_item, qty=11, actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.count(), 0)

    def test_create_allocation_increments_qty_ordered_immediately(self):
        allocation = create_allocation(self.pr_item, self.po_item, qty=10, actor=self.admin_user)
        self.po_item.refresh_from_db()
        self.assertEqual(self.po_item.qty_ordered, 10)
        self.assertEqual(allocation.qty_allocated, 10)
        self.assertEqual(allocation.po_no_snapshot, 'PO-9001')
        self.assertEqual(allocation.product_code_snapshot, 'NVL-0001')
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError: cannot import name 'create_allocation'`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa 2 dòng import đầu `purchasing/services.py`
  (KHÔNG thêm dòng import mới trùng module — gộp vào 2 dòng đã có sẵn):
```python
from django.db.models import Avg, Count, F, Q, Sum  # thêm F, Q vào dòng import Avg, Count, Sum có sẵn
...
from .models import (  # mở rộng tuple import PurchaseOrder, PurchaseOrderItem, PurchaseRequest có sẵn
    PurchaseOrder, PurchaseOrderItem, PurchaseRequest, PurchaseRequestItem, ProcurementAllocation,
)
```
  rồi thêm 2 hàm mới (sau `find_duplicate_po_products`, trước `sync_po_status` — nhóm cùng các hàm
  PO-level): 1 hàm nội bộ `_create_allocation_locked` **giả định caller đã khoá `po`/`po_item`/
  `pr_item` theo đúng thứ tự chung** (không tự `select_for_update()`), và `create_allocation` công
  khai — chỉ khoá rồi gọi hàm nội bộ. Tách riêng phần validate/ghi dữ liệu khỏi phần khoá là để Task
  2.5 (`build_po_from_allocations`) gọi lại đúng phần validate/ghi này cho **nhiều cặp** mà không
  phải khoá lại `PurchaseRequestItem` xen kẽ từng cặp một (xem lý do đầy đủ ở Task 2.5):
```python
def _create_allocation_locked(pr_item, po, po_item, qty, actor, ip_address=None):
    """Phần validate + ghi dữ liệu của PUR-PR-04, KHÔNG tự khoá — giả định `po`/`po_item`/`pr_item`
    caller truyền vào đã là bản `select_for_update()` mới nhất (create_allocation() khoá 1 cặp;
    build_po_from_allocations() khoá nhiều PurchaseRequestItem theo pk trước khi gọi hàm này lần
    lượt cho từng cặp — xem Task 2.5)."""
    if po.source != PurchaseOrder.Source.FROM_PR:
        raise ValidationError('Chỉ tạo được phân bổ cho PO nguồn Từ yêu cầu mua hàng.')
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError(
            f'Chỉ tạo được phân bổ khi PO đang ở trạng thái Nháp (hiện tại: {po.get_status_display()}).')
    if pr_item.purchase_request.status != PurchaseRequest.Status.APPROVED:
        raise ValidationError('Chỉ tạo được phân bổ cho dòng PR đã duyệt.')
    if pr_item.product_id is None:
        raise ValidationError('Dòng yêu cầu mua hàng này chưa được map sang sản phẩm trong danh mục.')
    if pr_item.product_id != po_item.product_id:
        raise ValidationError('Sản phẩm của dòng PR và dòng PO không khớp.')
    if qty < 1:
        raise ValidationError('Số lượng phân bổ phải lớn hơn 0.')
    if qty > pr_item.qty_open:
        raise ValidationError(f'Số lượng phân bổ ({qty}) vượt quá số lượng còn mở ({pr_item.qty_open}).')

    allocation = ProcurementAllocation.objects.create(
        pr_item=pr_item, po_item=po_item, qty_allocated=qty,
        po_no_snapshot=po.po_no, product_code_snapshot=po_item.product.product_code,
        created_by=actor,
    )
    po_item.qty_ordered = F('qty_ordered') + qty
    po_item.save(update_fields=['qty_ordered'])
    po_item.refresh_from_db(fields=['qty_ordered'])  # F() -> phải refresh trước khi dùng lại giá trị số

    log_action(
        actor, AuditLog.Action.CREATE, target=allocation,
        description=(
            f'Phân bổ {qty} từ dòng PR "{pr_item}" sang PO "{po.po_no}" '
            f'(sản phẩm {po_item.product.product_code}) — qty_ordered mới: {po_item.qty_ordered}.'
        ),
        ip_address=ip_address,
    )
    return allocation


@transaction.atomic
def create_allocation(pr_item, po_item, qty, actor, ip_address=None):
    """PUR-PR-04: tạo 1 ProcurementAllocation, đồng thời tăng po_item.qty_ordered
    đúng bằng qty (mục 4 điểm 4 FSD Stage 2 — điểm DUY NHẤT được phép tăng qty_ordered
    của PO nguồn FROM_PR). Lock order: PurchaseOrder -> PurchaseOrderItem ->
    PurchaseRequestItem -> ProcurementAllocation (mục 4 điểm 2).
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po_item.purchase_order_id)
    po_item = PurchaseOrderItem.objects.select_for_update().select_related('product').get(pk=po_item.pk)
    pr_item = PurchaseRequestItem.objects.select_for_update().get(pk=pr_item.pk)
    return _create_allocation_locked(pr_item, po, po_item, qty, actor, ip_address=ip_address)
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Viết tiếp test cho AC #6/#7/#15 (TDD lặp lại — mỗi test 1 chu trình Đỏ→Xanh riêng,
  code Bước 3 ở trên đã đủ để cả 3 PASS ngay vì mọi nhánh validate đã có sẵn, không cần sửa thêm)**:
```python
    def test_TC_PUR_PR_04_004_create_allocation_on_existing_po_item_increments_further(self):
        create_allocation(self.pr_item, self.po_item, qty=4, actor=self.admin_user)
        pr_item2 = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=6, qty_approved=6,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'),
            budget_category='Nguyên liệu')
        create_allocation(pr_item2, self.po_item, qty=6, actor=self.admin_user)
        self.po_item.refresh_from_db()
        self.assertEqual(self.po_item.qty_ordered, 10)

    def test_TC_PUR_PR_04_005_create_allocation_rejected_when_po_approved(self):
        self.po.status = PurchaseOrder.Status.APPROVED
        self.po.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            create_allocation(self.pr_item, self.po_item, qty=5, actor=self.admin_user)

    def test_TC_PUR_PR_06_001_create_allocation_rejects_unmapped_non_catalog(self):
        non_catalog_item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=None, qty_requested=3, qty_approved=3,
            non_catalog_name='Ống nhựa', non_catalog_uom='cây',
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('5000'),
            budget_category='Vật tư')
        with self.assertRaises(ValidationError):
            create_allocation(non_catalog_item, self.po_item, qty=1, actor=self.admin_user)
```
- [ ] **Bước 6: Chạy test, xác nhận PASS**
- [ ] **Bước 7: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): add create_allocation() service (PUR-PR-04)"
```

## Task 2.2: `release_allocation(allocation, reason, actor, *, delete_empty_po_item=True, ip_address=None)`

**File:**
- Sửa: `purchasing/services.py`
- Test: `purchasing/tests.py` (class mới `ReleaseAllocationTest`)

**Giao diện:**
- Sử dụng: `create_allocation` (Task 2.1, chỉ để dựng fixture trong test).
- Cung cấp: `release_allocation(...) -> (ProcurementAllocation, po_item_deleted: bool)` — dùng bởi
  Task 3.7 (`po_update`); và hàm nội bộ `_release_allocation_locked(allocation, po, po_item,
  pr_item, reason, actor, ip_address=None) -> int` (không tự khoá, trả về `qty_released`) — dùng bởi
  Task 2.3 (`delete_draft_po_item_with_allocations`, khoá nhiều `PurchaseRequestItem`/
  `ProcurementAllocation` theo pk 1 lần trước khi gọi lần lượt, xem Task 2.3).

- [ ] **Bước 1: Viết test đang FAIL (AC #13 — release 1 phần, dòng PO-item không bị xoá)**
```python
class ReleaseAllocationTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.admin_user, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED)
        self.po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        self.po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=0, unit_price=Decimal('1000'))
        self.pr_item_a = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        self.pr_item_b = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=5, qty_approved=5,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        self.allocation_a = create_allocation(self.pr_item_a, self.po_item, qty=10, actor=self.admin_user)
        self.allocation_b = create_allocation(self.pr_item_b, self.po_item, qty=5, actor=self.admin_user)

    def test_TC_PUR_PR_05_007_partial_release_keeps_po_item(self):
        release_allocation(self.allocation_b, reason='Đổi ý giảm số lượng', actor=self.admin_user)
        self.po_item.refresh_from_db()
        self.assertEqual(self.po_item.qty_ordered, 10)
        self.assertTrue(PurchaseOrderItem.objects.filter(pk=self.po_item.pk).exists())
        self.allocation_b.refresh_from_db()
        self.assertEqual(self.allocation_b.status, ProcurementAllocation.Status.RELEASED)

    def test_release_allocation_requires_reason(self):
        with self.assertRaises(ValidationError):
            release_allocation(self.allocation_b, reason='  ', actor=self.admin_user)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — cùng lý do tách hàm nội bộ như Task 2.1 (Task 2.3
  `delete_draft_po_item_with_allocations` cần khoá TOÀN BỘ `PurchaseRequestItem` liên quan theo pk
  TRƯỚC, rồi TOÀN BỘ `ProcurementAllocation` theo pk, thay vì xen kẽ PRItem→Allocation→PRItem→
  Allocation cho từng allocation một — xen kẽ là lỗi thứ tự khoá đã phát hiện ở review, xem Task
  2.3), thêm sau `_create_allocation_locked`/`create_allocation`:
```python
def _release_allocation_locked(allocation, po, po_item, pr_item, reason, actor, ip_address=None):
    """Phần validate + ghi dữ liệu của việc giải phóng 1 allocation, KHÔNG tự khoá — giả định
    `allocation`/`po`/`po_item`/`pr_item` caller truyền vào đã là bản `select_for_update()` mới
    nhất. Trả về `qty_released` (int thật, đã refresh) để caller tự quyết định có xoá po_item
    rỗng hay không (release_allocation() xoá ngay; delete_draft_po_item_with_allocations() gọi
    với ý định xoá po_item đúng 1 lần ở cuối vòng lặp, không xoá lặp lại ở đây)."""
    if allocation.status != ProcurementAllocation.Status.ACTIVE:
        raise ValidationError('Chỉ giải phóng được phân bổ đang hiệu lực.')
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError(
            f'Chỉ giải phóng được phân bổ khi PO đang ở trạng thái Nháp (hiện tại: {po.get_status_display()}).')

    qty_released = allocation.qty_allocated
    allocation.status = ProcurementAllocation.Status.RELEASED
    allocation.released_reason = reason
    allocation.released_by = actor
    allocation.released_at = timezone.now()
    allocation.save(update_fields=['status', 'released_reason', 'released_by', 'released_at'])

    po_item.qty_ordered = F('qty_ordered') - qty_released
    po_item.save(update_fields=['qty_ordered'])
    po_item.refresh_from_db(fields=['qty_ordered'])

    log_action(
        actor, AuditLog.Action.UPDATE, target=allocation,
        description=(
            f'Giải phóng phân bổ {qty_released} của dòng PR "{pr_item}" khỏi PO "{po.po_no}" '
            f'— lý do: {reason}. qty_ordered mới: {po_item.qty_ordered}.'
        ),
        ip_address=ip_address,
    )
    notify(pr_item.purchase_request.requested_by, (
        f'Phân bổ {qty_released} của dòng yêu cầu mua hàng "{pr_item}" vừa được giải phóng khỏi '
        f'PO "{po.po_no}" — số lượng còn mở đã tăng trở lại.'
    ), target=pr_item.purchase_request)
    return qty_released


@transaction.atomic
def release_allocation(allocation, reason, actor, *, delete_empty_po_item=True, ip_address=None):
    """Chuyển 1 ProcurementAllocation ACTIVE -> RELEASED, trừ po_item.qty_ordered
    tương ứng trong cùng transaction (mục 3/mục 4 điểm 4). ``delete_empty_po_item=False``
    dùng bởi delete_draft_po_item_with_allocations() để tự quản lý xoá po_item đúng 1 lần
    (Nghiêm trọng #4, review lần 3).
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Bắt buộc nhập lý do khi giải phóng phân bổ.')
    allocation = ProcurementAllocation.objects.select_related('po_item', 'pr_item').get(pk=allocation.pk)
    if allocation.status != ProcurementAllocation.Status.ACTIVE:
        raise ValidationError('Chỉ giải phóng được phân bổ đang hiệu lực.')
    if allocation.po_item_id is None:
        raise ValidationError('Phân bổ này không còn gắn với dòng PO nào.')

    po = PurchaseOrder.objects.select_for_update().get(pk=allocation.po_item.purchase_order_id)
    po_item = PurchaseOrderItem.objects.select_for_update().select_related('product').get(pk=allocation.po_item_id)
    pr_item = PurchaseRequestItem.objects.select_for_update().get(pk=allocation.pr_item_id)
    allocation = ProcurementAllocation.objects.select_for_update().get(pk=allocation.pk)

    _release_allocation_locked(allocation, po, po_item, pr_item, reason, actor, ip_address=ip_address)

    deleted = False
    if delete_empty_po_item and po_item.qty_ordered == 0:
        po_item_repr = str(po_item)
        po_item.delete()
        deleted = True
        log_action(
            actor, AuditLog.Action.DELETE, target=po,
            description=f'Xoá dòng PO-item "{po_item_repr}" khỏi PO "{po.po_no}" — hết số lượng sau khi giải phóng.',
            ip_address=ip_address,
        )
    return allocation, deleted
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Thêm test AC #7 nhánh `release_allocation` (PO `APPROVED` chặn) + AC #13 dòng
  full-release (dùng `delete_empty_po_item=True` mặc định, xoá hẳn dòng)**:
```python
    def test_TC_PUR_PR_04_005_release_allocation_rejected_when_po_approved(self):
        self.po.status = PurchaseOrder.Status.APPROVED
        self.po.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            release_allocation(self.allocation_a, reason='test', actor=self.admin_user)

    def test_release_last_allocation_deletes_po_item(self):
        release_allocation(self.allocation_a, reason='r1', actor=self.admin_user)
        _, deleted = release_allocation(self.allocation_b, reason='r2', actor=self.admin_user)
        self.assertTrue(deleted)
        self.assertFalse(PurchaseOrderItem.objects.filter(pk=self.po_item.pk).exists())
```
- [ ] **Bước 6: Chạy test, xác nhận PASS**
- [ ] **Bước 7: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): add release_allocation() service"
```

## Task 2.3: `delete_draft_po_item_with_allocations(po_item, actor, ip_address=None)`

**File:**
- Sửa: `purchasing/services.py`
- Test: `purchasing/tests.py` (class mới `DeleteDraftPoItemTest`)

**Giao diện:**
- Sử dụng: `_release_allocation_locked` (Task 2.2, hàm nội bộ không tự khoá — KHÔNG gọi
  `release_allocation()` công khai trong vòng lặp, vì hàm công khai tự khoá lại từ đầu và sẽ phá vỡ
  đúng thứ tự khoá "toàn bộ PRItem theo pk rồi toàn bộ Allocation theo pk" mà Task này tự dựng
  trước khi lặp — xem phần "Lỗi thứ tự khoá đã sửa" trong Bước 3).
- Cung cấp: `delete_draft_po_item_with_allocations(po_item, actor, ip_address=None) -> str` (trả về
  chuỗi mô tả dòng vừa xoá, dùng cho message flash) — dùng bởi Task 3.7 (`po_update`).

- [ ] **Bước 1: Viết test đang FAIL (AC #11/#12)**
```python
class DeleteDraftPoItemTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)

    def test_TC_PUR_PR_05_003_delete_po_item_releases_all_allocations_once(self):
        po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=0, unit_price=Decimal('1000'))
        pr = PurchaseRequest.objects.create(
            requested_by=self.admin_user, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED)
        pr_item_a = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        pr_item_b = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=self.product, qty_requested=5, qty_approved=5,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        create_allocation(pr_item_a, po_item, qty=10, actor=self.admin_user)
        create_allocation(pr_item_b, po_item, qty=5, actor=self.admin_user)

        delete_draft_po_item_with_allocations(po_item, actor=self.admin_user)

        self.assertFalse(PurchaseOrderItem.objects.filter(pk=po_item.pk).exists())
        self.assertEqual(
            ProcurementAllocation.objects.filter(
                pr_item__in=[pr_item_a, pr_item_b], status=ProcurementAllocation.Status.RELEASED,
            ).count(), 2)
        pr_item_a.refresh_from_db()
        self.assertEqual(pr_item_a.qty_open, 10)

    def test_TC_PUR_PR_05_010_delete_legacy_po_item_no_allocation(self):
        po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('1000'))
        delete_draft_po_item_with_allocations(po_item, actor=self.admin_user)
        self.assertFalse(PurchaseOrderItem.objects.filter(pk=po_item.pk).exists())
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm sau `release_allocation`.

  **Lỗi thứ tự khoá đã sửa so với v1 (review phát hiện)**: bản v1 từng gọi `release_allocation()`
  công khai lần lượt cho từng allocation trong vòng lặp — vì `release_allocation()` tự khoá
  `PurchaseRequestItem` rồi `ProcurementAllocation` bên trong nó, vòng lặp đó tạo ra thứ tự khoá xen
  kẽ **PRItem(alloc1) → Allocation(alloc1) → PRItem(alloc2) → Allocation(alloc2)** thay vì "toàn bộ
  PRItem theo pk rồi toàn bộ Allocation theo pk" như Ràng buộc chung yêu cầu. Nếu 2 lời gọi hàm này
  chạy song song trên 2 `po_item` khác nhau nhưng có tập `pr_item` chung theo thứ tự tương đối khác
  nhau (thứ tự `allocation.pk` không nhất thiết khớp thứ tự `pr_item.pk`), có thể tạo chu trình khoá
  ngược chiều trên chính bảng `PurchaseRequestItem` (Txn1 giữ PRItem-A chờ PRItem-B, Txn2 giữ
  PRItem-B chờ PRItem-A) — deadlock thật. Cách sửa: khoá TOÀN BỘ `PurchaseRequestItem` liên quan
  theo `pk` tăng dần **một lần duy nhất** trước khi xử lý bất kỳ allocation nào, rồi khoá toàn bộ
  `ProcurementAllocation` liên quan theo `pk`, sau đó gọi hàm nội bộ `_release_allocation_locked`
  (không tự khoá) cho từng cặp đã khoá sẵn — không gọi lại `release_allocation()` công khai (nó sẽ
  tự khoá lại từ đầu, phá vỡ đúng thứ tự đã dựng).
```python
@transaction.atomic
def delete_draft_po_item_with_allocations(po_item, actor, ip_address=None):
    """Nghiêm trọng #4 review lần 3: điều phối release TOÀN BỘ allocation ACTIVE
    của po_item rồi xoá hẳn po_item ĐÚNG 1 LẦN — tránh 2 tầng (release_allocation
    tự xoá khi về 0, formset.save() xoá lại) cùng xoá 1 row.
    """
    po = PurchaseOrder.objects.select_for_update().get(pk=po_item.purchase_order_id)
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError('Chỉ xoá được dòng PO-item khi PO đang ở trạng thái Nháp.')
    po_item = PurchaseOrderItem.objects.select_for_update().select_related('product').get(pk=po_item.pk)

    pr_item_ids_sorted = sorted(
        ProcurementAllocation.objects.filter(po_item=po_item, status=ProcurementAllocation.Status.ACTIVE)
        .values_list('pr_item_id', flat=True).distinct()
    )
    locked_pr_items = {
        obj.pk: obj
        for obj in PurchaseRequestItem.objects.select_for_update()
        .filter(pk__in=pr_item_ids_sorted).order_by('pk')
    }
    active_allocations = list(
        ProcurementAllocation.objects.select_for_update()
        .filter(po_item=po_item, status=ProcurementAllocation.Status.ACTIVE)
        .order_by('pk')
    )
    for allocation in active_allocations:
        _release_allocation_locked(
            allocation, po, po_item, locked_pr_items[allocation.pr_item_id],
            reason=f'PO-item bị xoá khỏi PO {po.po_no} khi còn Nháp.', actor=actor, ip_address=ip_address,
        )

    po_item_repr = str(po_item)
    po_item.refresh_from_db()  # qty_ordered đã về đúng 0 sau vòng lặp release ở trên (nếu có allocation)
    po_item.delete()
    log_action(
        actor, AuditLog.Action.DELETE, target=po,
        description=f'Xoá dòng PO-item "{po_item_repr}" khỏi PO "{po.po_no}".',
        ip_address=ip_address,
    )
    return po_item_repr
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): add delete_draft_po_item_with_allocations() service"
```

## Task 2.4: `send_po()` — thêm guard bất biến `qty_ordered == tổng allocation ACTIVE`

**File:**
- Sửa: `purchasing/services.py` (hàm `send_po` đã có, KHÔNG viết hàm mới)
- Test: `purchasing/tests.py` (thêm test vào class `PurchaseOrderWorkflowTest` đã có, hoặc class mới
  `SendPoAllocationGuardTest` nếu class cũ đã dài — khuyến nghị class mới để không làm phình class
  cũ đang test luồng gửi PO cơ bản)

**Giao diện:**
- Sử dụng: `ProcurementAllocation.Status.ACTIVE`, `Sum` (đã import sẵn ở đầu file).

**⚠️ Lưu ý bắt buộc khi viết test (Nghiêm trọng #3, review lần 3 — xem mục 4 điểm 4 FSD):** fixture
PHẢI dựng PO ở trạng thái `APPROVED`, không phải `DRAFT` — dựng `DRAFT` là false positive vì điều
kiện cũ `po.status != APPROVED` đã chặn trước, chưa từng chạm guard mới.

- [ ] **Bước 1: Viết test đang FAIL (AC #14 / TC-PUR-PR-05-008)**
```python
class SendPoAllocationGuardTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC', contact_email='')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        # PO APPROVED (không phải DRAFT) — bắt buộc để thực sự chạm guard mới, không bị chặn bởi
        # điều kiện cũ "chỉ gửi PO APPROVED".
        self.po = PurchaseOrder.objects.create(
            po_no='PO-9001', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR,
            status=PurchaseOrder.Status.APPROVED)
        # Legacy: qty_ordered=10 nhưng KHÔNG có allocation nào trỏ tới (mô phỏng dữ liệu cũ chưa reconcile).
        self.po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('1000'))

    def test_TC_PUR_PR_05_008_send_po_blocked_when_qty_ordered_mismatches_allocation(self):
        audit_count_before = AuditLog.objects.count()
        with self.assertRaises(ValidationError):
            send_po(self.po, actor=self.admin_user)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.APPROVED)
        self.assertEqual(AuditLog.objects.count(), audit_count_before)
        self.assertEqual(len(mail.outbox), 0)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `send_po()` hiện tại không raise gì (không có guard),
  PO chuyển `SENT` thành công, test fail ở `assertRaises`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa hàm `send_po()` hiện có, thêm guard NGAY SAU
  dòng `if po.status != PurchaseOrder.Status.APPROVED: raise ...` và TRƯỚC dòng
  `po.status = PurchaseOrder.Status.SENT`:
```python
    if po.source == PurchaseOrder.Source.FROM_PR:
        mismatched_lines = []
        for po_item in po.items.select_related('product'):
            total_allocated = po_item.allocations.filter(
                status=ProcurementAllocation.Status.ACTIVE,
            ).aggregate(total=Sum('qty_allocated'))['total'] or 0
            if po_item.qty_ordered != total_allocated:
                mismatched_lines.append(
                    f'{po_item.product.product_code} (đặt: {po_item.qty_ordered}, đã phân bổ: {total_allocated})')
        if mismatched_lines:
            raise ValidationError(
                'PO có dòng chưa khớp số lượng phân bổ, không thể gửi NCC: ' + '; '.join(mismatched_lines))
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): send_po() guard - qty_ordered must match ACTIVE allocation total for FROM_PR"
```

## Task 2.5: `build_po_from_allocations(supplier, allocation_requests, unit_price_by_product, actor, expected_delivery_date=None, ip_address=None)`

**File:**
- Sửa: `purchasing/services.py`
- Test: `purchasing/tests.py` (class mới `BuildPoFromAllocationsTest`)

**Giao diện:**
- Sử dụng: `_create_allocation_locked` (Task 2.1, hàm nội bộ không tự khoá — KHÔNG gọi
  `create_allocation()` công khai trong vòng lặp, cùng lý do như Task 2.3: hàm công khai tự khoá lại
  `PurchaseRequestItem` theo thứ tự caller truyền vào, phá vỡ đúng thứ tự "toàn bộ PRItem theo pk"
  Task này tự dựng trước khi lặp).
- Cung cấp: `build_po_from_allocations(...) -> PurchaseOrder` — dùng bởi Task 3.6
  (`po_build_from_pr_lines` view).

- [ ] **Bước 1: Viết test đang FAIL (AC #5 — gộp 2 dòng PR cùng product vào 1 PurchaseOrderItem)**
```python
class BuildPoFromAllocationsTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.admin_user, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED)
        self.pr_item_a = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        self.pr_item_b = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=5, qty_approved=5,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')

    def test_TC_PUR_PR_05_001_two_pr_items_same_product_merge_into_one_po_item(self):
        po = build_po_from_allocations(
            self.supplier,
            allocation_requests=[(self.pr_item_a, 10), (self.pr_item_b, 5)],
            unit_price_by_product={self.product.pk: Decimal('1200')},
            actor=self.admin_user,
        )
        self.assertEqual(po.source, PurchaseOrder.Source.FROM_PR)
        self.assertEqual(po.items.count(), 1)
        po_item = po.items.first()
        self.assertEqual(po_item.qty_ordered, 15)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=po_item).count(), 2)

    def test_build_po_from_allocations_rejects_empty(self):
        with self.assertRaises(ValidationError):
            build_po_from_allocations(self.supplier, [], {}, actor=self.admin_user)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS**.

  **Lỗi thứ tự khoá đã sửa so với v1 (review phát hiện)**: bản v1 gọi `create_allocation()` công
  khai lần lượt theo đúng thứ tự `allocation_requests` do caller truyền vào (thứ tự người dùng chọn
  dòng trên UI, không phải thứ tự `pr_item.pk`). Vì `create_allocation()` tự khoá
  `PurchaseRequestItem` bên trong, 2 lời gọi `build_po_from_allocations()` song song cùng tập
  `pr_item` nhưng khác thứ tự chọn (rất dễ xảy ra — 2 người dùng khác nhau tick chọn dòng theo thứ
  tự khác nhau trên form) có thể khoá `PurchaseRequestItem` ngược chiều nhau → deadlock thật (Txn1
  giữ PRItem-A chờ PRItem-B, Txn2 giữ PRItem-B chờ PRItem-A). Cách sửa — giống Task 2.3: khoá TOÀN
  BỘ `PurchaseRequestItem` liên quan theo `pk` tăng dần **một lần duy nhất** trước, rồi mới lặp qua
  `allocation_requests` gọi hàm nội bộ `_create_allocation_locked` (không tự khoá) cho từng cặp —
  không gọi lại `create_allocation()` công khai. `po`/`po_item` không cần khoá thêm ở đây: `po` vừa
  được tạo trong chính transaction này (`PurchaseOrder.objects.create()`), chưa transaction nào khác
  có thể tham chiếu tới pk của nó trước khi commit; `po_item` cũng vậy (tạo mới trong vòng lặp).
```python
@transaction.atomic
def build_po_from_allocations(supplier, allocation_requests, unit_price_by_product, actor,
                               expected_delivery_date=None, ip_address=None):
    """PUR-PR-05 (po_build_from_pr_lines): tạo PurchaseOrder(DRAFT, FROM_PR) từ nhiều dòng PR,
    gộp theo product thành đúng 1 PurchaseOrderItem/product (PUR-FND-06). ``qty_ordered=0`` khởi
    tạo là giá trị TẠM trong transaction chưa commit (mục 4 điểm 4) — _create_allocation_locked()
    tự cộng dồn tới giá trị thật cho từng cặp, hàm này không tự tính tổng.

    allocation_requests: list[(pr_item, qty)]. unit_price_by_product: {product_id: Decimal}.
    """
    if not allocation_requests:
        raise ValidationError('Phải chọn ít nhất 1 dòng yêu cầu mua hàng để tạo PO.')

    po = PurchaseOrder.objects.create(
        supplier=supplier, source=PurchaseOrder.Source.FROM_PR, created_by=actor,
        expected_delivery_date=expected_delivery_date,
    )

    pr_item_ids_sorted = sorted({pr_item.pk for pr_item, _qty in allocation_requests})
    locked_pr_items = {
        obj.pk: obj
        for obj in PurchaseRequestItem.objects.select_for_update()
        .filter(pk__in=pr_item_ids_sorted).order_by('pk')
    }

    po_item_by_product = {}
    for pr_item, qty in allocation_requests:
        pr_item = locked_pr_items[pr_item.pk]  # dùng bản đã khoá — không dùng object caller truyền vào
        if pr_item.product_id is None:
            raise ValidationError(f'Dòng yêu cầu mua hàng "{pr_item}" chưa được map sang sản phẩm.')
        product_id = pr_item.product_id
        if product_id not in po_item_by_product:
            unit_price = unit_price_by_product.get(product_id)
            if unit_price is None:
                raise ValidationError(f'Thiếu đơn giá cho sản phẩm "{pr_item.product.product_code}".')
            po_item_by_product[product_id] = PurchaseOrderItem.objects.create(
                purchase_order=po, product=pr_item.product, qty_ordered=0, unit_price=unit_price)
        _create_allocation_locked(pr_item, po, po_item_by_product[product_id], qty, actor, ip_address=ip_address)

    log_action(
        actor, AuditLog.Action.CREATE, target=po,
        description=f'Tạo PO {po.po_no} từ {len(allocation_requests)} dòng yêu cầu mua hàng.',
        ip_address=ip_address,
    )
    return po
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Thêm test AC #19 (case "1 PR → n PO")**
```python
    def test_TC_PUR_PR_05_006_one_pr_item_split_into_two_pos(self):
        pr_item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=100, qty_approved=100,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        po_a = build_po_from_allocations(
            self.supplier, [(pr_item, 40)], {self.product.pk: Decimal('1000')}, actor=self.admin_user)
        po_b = build_po_from_allocations(
            self.supplier, [(pr_item, 60)], {self.product.pk: Decimal('1000')}, actor=self.admin_user)
        pr_item.refresh_from_db()
        self.assertEqual(pr_item.qty_allocated, 100)
        self.assertEqual(pr_item.qty_open, 0)
        with self.assertRaises(ValidationError):
            create_allocation(pr_item, po_a.items.first(), qty=1, actor=self.admin_user)
        allocation_a = ProcurementAllocation.objects.get(pr_item=pr_item, po_item__purchase_order=po_a)
        release_allocation(allocation_a, reason='test', actor=self.admin_user)
        pr_item.refresh_from_db()
        self.assertEqual(pr_item.qty_open, 40)
        self.assertFalse(PurchaseOrderItem.objects.filter(purchase_order=po_a).exists())
```
- [ ] **Bước 6: Chạy test, xác nhận PASS**
- [ ] **Bước 7: Viết test đang FAIL (Review lần 3, mục phụ #1 — TC-PUR-PR-05-028: chặn supplier
  không ACTIVE)**. Form-level (`PurchaseOrderForm`, `purchasing/forms.py`) đã lọc queryset
  `status=ACTIVE` cho luồng `po_create` thường, nhưng `build_po_from_allocations` nhận `supplier`
  thẳng làm tham số — không đi qua form đó — nên phải tự re-validate độc lập, đúng convention
  "form lọc, service phải tự kiểm tra lại" đã áp dụng cho các constraint khác trong file này.
```python
    def test_TC_PUR_PR_05_028_rejects_inactive_supplier(self):
        self.supplier.status = Supplier.Status.INACTIVE
        self.supplier.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            build_po_from_allocations(
                self.supplier, [(self.pr_item_a, 10)],
                {self.product.pk: Decimal('1000')}, actor=self.admin_user)
        self.assertFalse(PurchaseOrder.objects.filter(supplier=self.supplier).exists())
```
- [ ] **Bước 8: Chạy test, xác nhận FAIL** — supplier `INACTIVE` vẫn tạo PO bình thường vì hàm
  chưa kiểm tra `status`.
- [ ] **Bước 9: Sửa code tối thiểu để PASS** — thêm ngay sau check `allocation_requests` rỗng ở
  Bước 3 (trước dòng `po = PurchaseOrder.objects.create(...)`):
```python
    if supplier.status != Supplier.Status.ACTIVE:
        raise ValidationError(
            f'Nhà cung cấp "{supplier.name}" đã ngừng giao dịch hoặc bị tạm khóa, không thể tạo PO.')
```
- [ ] **Bước 10: Chạy lại toàn bộ test của class, xác nhận PASS**
- [ ] **Bước 11: Viết test đang FAIL (Review lần 3, mục phụ #2 — chặn `unit_price` âm)**.
  `PurchaseOrderItem.unit_price` đã có `MinValueValidator(0)` ở tầng model
  (`purchasing/models.py:208-209`), nhưng validator model chỉ chạy khi có `full_clean()` (ví dụ
  qua `ModelForm.is_valid()`) — `PurchaseOrderItem.objects.create(...)` ở Bước 3 gọi thẳng, bỏ qua
  validator hoàn toàn. `unit_price_by_product` trong hàm này tới từ giá trị người dùng nhập tay ở
  view Task 3.6 (`Decimal(request.POST.get(f'unit_price_{product_id}', ''))`), nên một chuỗi
  `"-100"` sẽ lọt qua thẳng xuống DB nếu không tự kiểm tra lại — cùng dạng "field có sibling bị
  chặn ở nơi khác" như `PurchaseRequestItem.estimated_unit_price` (`MinValueValidator(0)`,
  `purchasing/models.py:350-352`) đã có từ trước.
```python
    def test_build_po_from_allocations_rejects_negative_unit_price(self):
        with self.assertRaises(ValidationError):
            build_po_from_allocations(
                self.supplier, [(self.pr_item_a, 10)],
                {self.product.pk: Decimal('-100')}, actor=self.admin_user)
        self.assertFalse(PurchaseOrder.objects.filter(supplier=self.supplier).exists())
```
- [ ] **Bước 12: Chạy test, xác nhận FAIL** — `unit_price=-100` vẫn tạo `PurchaseOrderItem`/`PO`
  bình thường vì hàm chưa kiểm tra dấu.
- [ ] **Bước 13: Sửa code tối thiểu để PASS** — thêm ngay sau check `unit_price is None` ở Bước 3
  (trước dòng `po_item_by_product[product_id] = PurchaseOrderItem.objects.create(...)`):
```python
        if product_id not in po_item_by_product:
            unit_price = unit_price_by_product.get(product_id)
            if unit_price is None:
                raise ValidationError(f'Thiếu đơn giá cho sản phẩm "{pr_item.product.product_code}".')
            if unit_price < 0:
                raise ValidationError(
                    f'Đơn giá cho sản phẩm "{pr_item.product.product_code}" không được âm.')
            po_item_by_product[product_id] = PurchaseOrderItem.objects.create(
                purchase_order=po, product=pr_item.product, qty_ordered=0, unit_price=unit_price)
```
- [ ] **Bước 14: Chạy lại toàn bộ test của class, xác nhận PASS**
- [ ] **Bước 15: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): add build_po_from_allocations() service (PUR-PR-05)"
```

## Task 2.6: `PurchaseRequestItem.clean()` (XOR non-catalog/product + budget_category fallback) + fix `__str__`

**File:**
- Sửa: `purchasing/models.py` (method `clean()` mới + sửa `__str__` trên `PurchaseRequestItem`)
- Test: `purchasing/tests.py` (class mới `PurchaseRequestItemCleanTest`)

**Giao diện:**
- Cung cấp: `PurchaseRequestItem.clean()` — được `ModelForm.full_clean()` gọi tự động qua
  `PurchaseRequestItemForm` (Task 3.2), không gọi trực tiếp từ view.

- [ ] **Bước 1: Viết test đang FAIL (AC #2, TC-PUR-PR-01-003/005/006)**
```python
class PurchaseRequestItemCleanTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='rq1', password='rq-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg', category='Nguyên liệu')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.user, warehouse=self.warehouse, cost_center='CC-001')

    def test_TC_PUR_PR_01_003_product_and_non_catalog_both_set_rejected(self):
        item = PurchaseRequestItem(
            purchase_request=self.pr, product=self.product, qty_requested=1,
            non_catalog_name='Ống nhựa', non_catalog_uom='cây',
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'))
        with self.assertRaises(ValidationError):
            item.clean()

    def test_TC_PUR_PR_01_005_non_catalog_missing_uom_rejected(self):
        item = PurchaseRequestItem(
            purchase_request=self.pr, product=None, qty_requested=1,
            non_catalog_name='Ống nhựa', non_catalog_uom='',
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'))
        with self.assertRaises(ValidationError):
            item.clean()

    def test_TC_PUR_PR_01_006_budget_category_fallback_to_product_category(self):
        item = PurchaseRequestItem(
            purchase_request=self.pr, product=self.product, qty_requested=1, budget_category='',
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'))
        item.clean()
        self.assertEqual(item.budget_category, 'Nguyên liệu')

    def test_budget_category_normalized_strip_and_collapse_spaces(self):
        item = PurchaseRequestItem(
            purchase_request=self.pr, product=self.product, qty_requested=1,
            budget_category='  Nguyên   liệu  ',
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'))
        item.clean()
        self.assertEqual(item.budget_category, 'Nguyên liệu')

    def test_TC_PUR_PR_06_004_str_does_not_crash_on_non_catalog(self):
        item = PurchaseRequestItem(
            purchase_request=self.pr, product=None, qty_requested=1,
            non_catalog_name='Ống nhựa PVC', non_catalog_uom='cây')
        self.assertIn('Ống nhựa PVC', str(item))
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `clean()` hiện tại là `Model.clean()` mặc định (no-op),
  `__str__` hiện tại crash `AttributeError` khi `product is None` (`self.product.product_code`).
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thay `__str__` và thêm `clean()` trên
  `PurchaseRequestItem`:
```python
    def __str__(self):
        label = self.product.product_code if self.product_id else (self.non_catalog_name or 'non-catalog')
        return f'{self.purchase_request.request_no} - {label} x{self.qty_requested}'

    def clean(self):
        super().clean()
        import re
        has_product = self.product_id is not None
        has_non_catalog = bool((self.non_catalog_name or '').strip()) and bool((self.non_catalog_uom or '').strip())
        if has_product and (self.non_catalog_name or self.non_catalog_uom):
            raise ValidationError('Đã chọn sản phẩm trong danh mục thì không được điền thông tin non-catalog.')
        if not has_product and not has_non_catalog:
            raise ValidationError(
                'Phải chọn sản phẩm trong danh mục, hoặc điền đủ Tên hàng + Đơn vị tính cho hàng non-catalog.')
        if self.budget_category:
            normalized = re.sub(r'\s+', ' ', self.budget_category.strip())
            self.budget_category = normalized
        if has_product and not self.budget_category:
            self.budget_category = self.product.category
```
  (Import `re` ở đầu file thay vì trong hàm — dọn lại khi hoàn thiện, đặt tạm trong hàm ở bước này
  chỉ để tối thiểu hoá diff, sửa lại vị trí import trước khi commit ở Bước 4.)
- [ ] **Bước 4: Dọn import `re` lên đầu `purchasing/models.py`, chạy lại test, xác nhận PASS**
- [ ] **Bước 5: Commit**
```bash
git add purchasing/models.py purchasing/tests.py
git commit -m "feat(pur): PurchaseRequestItem.clean() - non-catalog XOR + budget_category fallback; fix __str__ crash on non-catalog"
```

## Task 2.7: `map_non_catalog_item(pr_item, product, actor, ip_address=None)`

**File:**
- Sửa: `purchasing/services.py`
- Test: `purchasing/tests.py` (class mới `MapNonCatalogItemTest`)

**Giao diện:**
- Cung cấp: `map_non_catalog_item(...) -> PurchaseRequestItem` — dùng bởi Task 3.5
  (`pr_item_map_product` view, sau khi view đã resolve/tạo `Product`).

- [ ] **Bước 1: Viết test đang FAIL (mục 4 điểm 10, TC-PUR-PR-06-002/003)**
```python
class MapNonCatalogItemTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='pur1', password='pur-pass-123', role=User.Role.PURCHASING)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0002', name='Ống nhựa PVC', uom='cây')

    def _make_non_catalog_item(self, pr_status):
        pr = PurchaseRequest.objects.create(
            requested_by=self.user, warehouse=self.warehouse, cost_center='CC-001', status=pr_status)
        return PurchaseRequestItem.objects.create(
            purchase_request=pr, product=None, qty_requested=3,
            non_catalog_name='Ống nhựa PVC', non_catalog_uom='cây',
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('5000'), budget_category='VT')

    def test_TC_PUR_PR_06_002_map_assigns_product(self):
        item = self._make_non_catalog_item(PurchaseRequest.Status.PENDING_DEPT)
        map_non_catalog_item(item, self.product, actor=self.user)
        item.refresh_from_db()
        self.assertEqual(item.product_id, self.product.pk)
        self.assertFalse(item.is_non_catalog)
        self.assertEqual(item.non_catalog_name, '')
        self.assertEqual(item.non_catalog_uom, '')
        self.assertEqual(item.non_catalog_note, '')
        item.full_clean()  # không được raise — clean() (Task 2.6) cấm vừa có product vừa có non-catalog

    def test_TC_PUR_PR_06_003_map_blocked_while_draft(self):
        item = self._make_non_catalog_item(PurchaseRequest.Status.DRAFT)
        with self.assertRaises(ValidationError):
            map_non_catalog_item(item, self.product, actor=self.user)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm vào `purchasing/services.py` (nhóm cùng các
  hàm PR-level, sau `forward_purchase_request`).

  **Lỗi đã sửa so với v1 (review phát hiện)**: v1 chỉ set `pr_item.product = product` rồi
  `save(update_fields=['product'])`, không xoá `non_catalog_name`/`non_catalog_uom`/
  `non_catalog_note` — sau khi map, dòng có ĐỒNG THỜI `product` khác `None` VÀ 3 field non-catalog
  vẫn còn giá trị, vi phạm chính `PurchaseRequestItem.clean()` (Task 2.6, cấm XOR). `Model.save()`
  không tự gọi `full_clean()` nên lỗi không lộ ra ngay khi map, nhưng bất kỳ lần sửa sau qua
  `ModelForm` (gọi `full_clean()`) sẽ FAIL khó hiểu. Cách sửa: xoá 3 field non-catalog về giá trị
  rỗng trong CÙNG 1 lệnh `save(update_fields=[...])` với việc set `product` (ghi vào audit log
  trước khi xoá, để giữ lại tên hàng non-catalog gốc trong mô tả).
```python
@transaction.atomic
def map_non_catalog_item(pr_item, product, actor, ip_address=None):
    """PUR-PR-06 (quyết định #9): gán Product cho 1 dòng PR non-catalog. Chỉ gọi được sau khi PR
    đã rời DRAFT (mục 4 điểm 10) — map sớm lúc còn DRAFT chỉ tạo Product rác nếu Requester đổi ý.
    """
    pr_item = PurchaseRequestItem.objects.select_related('purchase_request').select_for_update().get(pk=pr_item.pk)
    if pr_item.product_id is not None:
        raise ValidationError('Dòng này đã có sản phẩm, không cần map lại.')
    if pr_item.purchase_request.status == PurchaseRequest.Status.DRAFT:
        raise ValidationError('Chỉ map sản phẩm được sau khi yêu cầu mua hàng đã được nộp.')

    old_non_catalog_name = pr_item.non_catalog_name
    pr_item.product = product
    pr_item.non_catalog_name = ''
    pr_item.non_catalog_uom = ''
    pr_item.non_catalog_note = ''
    pr_item.save(update_fields=['product', 'non_catalog_name', 'non_catalog_uom', 'non_catalog_note'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=pr_item.purchase_request,
        description=f'Map dòng non-catalog "{old_non_catalog_name}" sang sản phẩm "{product.product_code}".',
        ip_address=ip_address,
    )
    return pr_item
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): add map_non_catalog_item() service (PUR-PR-06)"
```

## Task 2.8: `cancel_pr_item_open_qty(pr_item, qty, reason, actor, ip_address=None)`

**File:**
- Sửa: `purchasing/services.py`
- Test: `purchasing/tests.py` (class mới `CancelPrItemOpenQtyTest`)

**Giao diện:**
- Cung cấp: `cancel_pr_item_open_qty(...) -> PurchaseRequestItem` — dùng bởi Task 3.3 (nút "Huỷ
  phần còn mở" ở `pr_detail`). Hàm KHÔNG tự kiểm quyền actor (ai được gọi) — quyền kiểm ở view
  (mục 1/mục 4 điểm 9: `update` trên `pr` + (`is_department_manager('PURCHASING')` hoặc đúng
  `assigned_to`)), hàm chỉ validate bất biến số lượng.

- [ ] **Bước 1: Viết test đang FAIL (AC #17)**
```python
class CancelPrItemOpenQtyTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='pur1', password='pur-pass-123', role=User.Role.PURCHASING)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        pr = PurchaseRequest.objects.create(
            requested_by=self.user, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED)
        self.pr_item = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')

    def test_TC_PUR_PR_07_001_cancel_more_than_open_rejected(self):
        with self.assertRaises(ValidationError):
            cancel_pr_item_open_qty(self.pr_item, qty=11, reason='Không cần nữa', actor=self.user)

    def test_TC_PUR_PR_07_002_cancel_valid_increments_qty_cancelled(self):
        cancel_pr_item_open_qty(self.pr_item, qty=4, reason='Giảm nhu cầu', actor=self.user)
        self.pr_item.refresh_from_db()
        self.assertEqual(self.pr_item.qty_cancelled, 4)
        self.assertEqual(self.pr_item.qty_open, 6)
        self.assertTrue(AuditLog.objects.filter(description__icontains='Giảm nhu cầu').exists())
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS**:
```python
@transaction.atomic
def cancel_pr_item_open_qty(pr_item, qty, reason, actor, ip_address=None):
    """PUR-PR-07: huỷ 1 phần qty_open của dòng PR (không xoá dòng, không đổi qty_requested/
    qty_approved). Không có giới hạn "chỉ PR APPROVED" — dòng đã APPROVED mới có qty_open > 0 để
    huỷ, điều kiện tự nhiên đã chặn (mục 4 điểm 9).
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Bắt buộc nhập lý do khi huỷ phần còn mở.')
    pr_item = PurchaseRequestItem.objects.select_related('purchase_request').select_for_update().get(pk=pr_item.pk)
    if qty < 1:
        raise ValidationError('Số lượng huỷ phải lớn hơn 0.')
    if qty > pr_item.qty_open:
        raise ValidationError(f'Số lượng huỷ ({qty}) vượt quá số lượng còn mở ({pr_item.qty_open}).')

    pr_item.qty_cancelled = F('qty_cancelled') + qty
    pr_item.save(update_fields=['qty_cancelled'])
    pr_item.refresh_from_db(fields=['qty_cancelled'])

    log_action(
        actor, AuditLog.Action.CANCEL, target=pr_item.purchase_request,
        description=f'Huỷ {qty} số lượng còn mở của dòng "{pr_item}" — lý do: {reason}.',
        ip_address=ip_address,
    )
    return pr_item
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): add cancel_pr_item_open_qty() service (PUR-PR-07)"
```

## Task 2.9: `decide_purchase_request()` — mở rộng set `qty_approved` khi duyệt cấp `PENDING_PUR`

**File:**
- Sửa: `purchasing/services.py` (hàm `decide_purchase_request` đã có — thêm tham số, KHÔNG viết
  hàm mới)
- Test: `purchasing/tests.py` (thêm test vào class hiện có test `decide_purchase_request`, hoặc
  class mới `DecidePurchaseRequestQtyApprovedTest` nếu tách riêng dễ đọc hơn)

**Giao diện:**
- Sửa chữ ký hàm: `decide_purchase_request(approval, approved, actor, note='', ip_address=None,
  qty_approved_overrides=None)` — `qty_approved_overrides`: dict `{pr_item_id: int}`, chỉ áp dụng ở
  nhánh approve cấp `PENDING_PUR`; dòng không có trong dict giữ mặc định `qty_approved =
  qty_requested`. Dùng bởi Task 3.4 (view duyệt PR — đọc từ POST khi ở `PENDING_PUR`).

- [ ] **Bước 1: Viết test đang FAIL (AC #3, TC-PUR-PR-03-001/002/003)**
```python
class DecidePurchaseRequestQtyApprovedTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='staff1', password='staff-pass-123', role=User.Role.STAFF)
        self.pur_manager = User.objects.create_user(
            username='purm', password='purm-pass-123', role=User.Role.MANAGER,
            department=User.Department.PURCHASING, is_manager=True)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001')
        self.item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        submit_purchase_request(self.pr, actor=self.staff)  # -> PENDING_PUR (staff không thuộc PURCHASING)

    def _approval(self):
        from accounts.approvals import latest_approval_for
        return latest_approval_for(self.pr)

    def test_TC_PUR_PR_03_001_approve_without_override_keeps_qty_requested(self):
        decide_purchase_request(self._approval(), approved=True, actor=self.pur_manager)
        self.item.refresh_from_db()
        self.assertEqual(self.item.qty_approved, 10)

    def test_TC_PUR_PR_03_002_approve_with_override_lower(self):
        decide_purchase_request(
            self._approval(), approved=True, actor=self.pur_manager,
            qty_approved_overrides={self.item.pk: 6})
        self.item.refresh_from_db()
        self.assertEqual(self.item.qty_approved, 6)

    def test_TC_PUR_PR_03_003_approve_all_zero_rejected(self):
        with self.assertRaises(ValidationError):
            decide_purchase_request(
                self._approval(), approved=True, actor=self.pur_manager,
                qty_approved_overrides={self.item.pk: 0})
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PurchaseRequest.Status.PENDING_PUR)

    def test_override_above_qty_requested_rejected(self):
        with self.assertRaises(ValidationError):
            decide_purchase_request(
                self._approval(), approved=True, actor=self.pur_manager,
                qty_approved_overrides={self.item.pk: 11})
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `TypeError: unexpected keyword argument
  'qty_approved_overrides'`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa chữ ký hàm và nội dung `on_approve()` nhánh
  `else` (cấp `PENDING_PUR`) của `decide_purchase_request` hiện có:
```python
def decide_purchase_request(approval, approved, actor, note='', ip_address=None, qty_approved_overrides=None):
    pr = PurchaseRequest.objects.select_for_update().get(pk=approval.target_id)
    stage = pr.status
    if stage not in (PurchaseRequest.Status.PENDING_DEPT, PurchaseRequest.Status.PENDING_PUR):
        raise ValidationError(f'Yêu cầu "{pr.request_no}" không ở trạng thái chờ duyệt.')

    qty_approved_overrides = qty_approved_overrides or {}
    advance_to_pur = False

    def on_approve():
        nonlocal advance_to_pur
        if stage == PurchaseRequest.Status.PENDING_DEPT:
            pr.status = PurchaseRequest.Status.PENDING_PUR
            pr.save(update_fields=['status'])
            advance_to_pur = True
        else:
            items = list(pr.items.select_for_update().order_by('pk'))
            changed_lines = []
            for item in items:
                requested = item.qty_requested
                approved_qty = qty_approved_overrides.get(item.pk, requested)
                if approved_qty > requested:
                    raise ValidationError(f'Không được duyệt tăng số lượng dòng "{item}" (yêu cầu: {requested}).')
                if approved_qty < 0:
                    raise ValidationError(f'Số lượng duyệt của dòng "{item}" không được âm.')
                if approved_qty != requested:
                    changed_lines.append(f'{item}: {requested} -> {approved_qty}')
                item.qty_approved = approved_qty
                item.save(update_fields=['qty_approved'])
            if items and all(item.qty_approved == 0 for item in items):
                raise ValidationError(
                    'Không thể duyệt yêu cầu với toàn bộ dòng có số lượng duyệt = 0 — dùng "Từ chối" thay thế.')
            pr.status = PurchaseRequest.Status.APPROVED
            pr.decided_by = actor
            pr.decided_at = timezone.now()
            pr.save(update_fields=['status', 'decided_by', 'decided_at'])
            if changed_lines:
                log_action(
                    actor, AuditLog.Action.APPROVE, target=pr,
                    description=f'Duyệt yêu cầu {pr.request_no} — điều chỉnh số lượng duyệt: ' + '; '.join(changed_lines),
                    ip_address=ip_address,
                )
            if pr.assigned_to_id:
                notify(pr.assigned_to, f'Yêu cầu mua hàng {pr.request_no} đã được duyệt — hãy tạo PO.', target=pr)

    def on_reject():
        pr.status = PurchaseRequest.Status.REJECTED
        pr.decided_by = actor
        pr.decided_at = timezone.now()
        pr.reject_reason = note
        pr.save(update_fields=['status', 'decided_by', 'decided_at', 'reject_reason'])

    decide_approval(
        approval, approved, actor=actor, note=note,
        on_approve=on_approve, on_reject=on_reject, ip_address=ip_address,
    )
    if advance_to_pur:
        create_approval(
            pr, department=User.Department.PURCHASING,
            action_label=f'Yêu cầu mua hàng {pr.request_no}', submitted_by=pr.requested_by, ip_address=ip_address,
        )
    return pr
```
  (Toàn bộ phần khác của hàm giữ nguyên — chỉ thay chữ ký + nội dung nhánh `else` trong
  `on_approve()`; nhánh `PENDING_DEPT` và `on_reject()`/phần gọi `decide_approval()` không đổi.)
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5**: chạy lại TOÀN BỘ test cũ của `decide_purchase_request` đã có trong `tests.py`
  trước Stage 2 (không được đổi hành vi mặc định khi không truyền `qty_approved_overrides`) —
  xác nhận vẫn PASS nguyên trạng (regression).
- [ ] **Bước 6: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): decide_purchase_request() supports qty_approved override at PENDING_PUR stage"
```

## Task 2.10: `reconcile_legacy_po_item_allocations(po_item, allocations, actor, ip_address=None)` — batch, 14 điều kiện (T9)

Hàm phức tạp nhất Stage 2 (mục 4 điểm 4 "Ghi chú dữ liệu cũ"/mục 8, đã qua review lần 4 + lần 5).
Vì có 14 điều kiện validate cùng lúc và FSD đã pin cứng từng kịch bản, task này KHÔNG chia 14 chu
trình Đỏ→Xanh riêng lẻ (sẽ vụn vặt, khó theo dõi) — thay vào đó: viết 1 batch test đầu tiên gồm
happy-path + 2 case atomicity/mới nhất (review lần 5) trước, hiện thực hàm ĐẦY ĐỦ ngay (không phải
hàm rút gọn rồi vá dần — 14 điều kiện phụ thuộc lẫn nhau về thứ tự, viết nửa vời dễ sai lock order),
rồi bổ sung tiếp các test còn lại theo checklist bên dưới để phủ hết AC #28-38/TC-PUR-PR-05-014,
018-020, 023-027 — chạy lại toàn bộ sau mỗi lần thêm, không sửa hàm nữa nếu đã PASS hết Bước 1.

**File:**
- Sửa: `purchasing/services.py`
- Test: `purchasing/tests.py` (class mới `ReconcileLegacyPoItemAllocationsTest`)

**Giao diện:**
- Cung cấp: `reconcile_legacy_po_item_allocations(po_item, allocations, actor, ip_address=None) ->
  list[ProcurementAllocation]` — dùng bởi Task 4.3 (management command), Task 5.2 (concurrency test
  với `send_po()`).
- Sử dụng: lock order chung (Ràng buộc chung), `User.Role.ADMIN`.

- [ ] **Bước 1: Viết test đang FAIL** (happy-path 1 dòng, happy-path 2 dòng khớp tổng, atomicity
  khi 1 dòng sai, + 3 case mới của review lần 5: batch rỗng/trùng pr_item/`linked_po_id` rỗng)
```python
class ReconcileLegacyPoItemAllocationsTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.staff = User.objects.create_user(username='staff1', password='staff-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        # PO legacy: qty_ordered=10, 0 allocation, source=FROM_PR, DRAFT (đủ điều kiện reconcile).
        self.po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        self.po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('1000'))

    def _pr_item(self, qty_requested, qty_approved, linked_po=None):
        pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED, linked_po=linked_po)
        return PurchaseRequestItem.objects.create(
            purchase_request=pr, product=self.product, qty_requested=qty_requested, qty_approved=qty_approved,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')

    def test_TC_PUR_PR_05_014_single_pr_item_exact_match(self):
        pr_item = self._pr_item(10, 10, linked_po=self.po)
        reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.admin_user)
        self.po_item.refresh_from_db()
        self.assertEqual(self.po_item.qty_ordered, 10)  # KHÔNG cộng thêm
        self.assertEqual(
            ProcurementAllocation.objects.filter(po_item=self.po_item, status='ACTIVE').count(), 1)
        pr_item_b = self._pr_item(1, 1, linked_po=self.po)
        with self.assertRaises(ValidationError):  # đã khớp đủ, không còn chỗ
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item_b, 1)], actor=self.admin_user)

    def test_TC_PUR_PR_05_018_two_pr_items_exact_match_in_one_call(self):
        pr_item_1 = self._pr_item(4, 4, linked_po=self.po)
        pr_item_2 = self._pr_item(6, 6, linked_po=self.po)
        reconcile_legacy_po_item_allocations(
            self.po_item, [(pr_item_1, 4), (pr_item_2, 6)], actor=self.admin_user)
        self.assertEqual(
            ProcurementAllocation.objects.filter(po_item=self.po_item, status='ACTIVE').count(), 2)

    def test_TC_PUR_PR_05_019_batch_rolls_back_entirely_on_one_invalid_line(self):
        pr_item_1 = self._pr_item(4, 4, linked_po=self.po)
        pr_item_2 = self._pr_item(6, 3, linked_po=self.po)  # qty_open chỉ còn 3
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(
                self.po_item, [(pr_item_1, 4), (pr_item_2, 6)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_024_duplicate_pr_item_in_same_batch_rejected(self):
        pr_item = self._pr_item(10, 10, linked_po=self.po)
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(
                self.po_item, [(pr_item, 4), (pr_item, 6)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_025_linked_po_none_rejected(self):
        pr_item = self._pr_item(10, 10, linked_po=None)  # chưa từng liên kết PO nào
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_026_empty_batch_rejected_no_audit_log(self):
        audit_count_before = AuditLog.objects.count()
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [], actor=self.admin_user)
        self.assertEqual(AuditLog.objects.count(), audit_count_before)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code ĐẦY ĐỦ để PASS cả 6 test trên cùng lúc** — thêm vào cuối nhóm hàm
  allocation trong `purchasing/services.py`:
```python
@transaction.atomic
def reconcile_legacy_po_item_allocations(po_item, allocations, actor, ip_address=None):
    """T9 (review lần 4/5): recovery procedure MỘT LẦN cho PO legacy backfill từ linked_po
    (mục 9 migration 0018) không khớp allocation tự động được. TẠO THÊM allocation khớp CHÍNH
    XÁC qty_ordered hiện có — KHÔNG cộng thêm (ngoại lệ duy nhất so với create_allocation()).
    Chỉ gọi qua management command `reconcile_legacy_po_item_allocations` (Task 4.3), không lộ
    ra UI/luồng tạo PO thông thường.

    ``allocations``: list[(pr_item, qty)] — không rỗng, mỗi pr_item chỉ 1 lần.

    Lock order (đặc biệt quan trọng — đường DUY NHẤT tạo allocation cho PO legacy đã APPROVED,
    có thể chạy đồng thời với send_po() trên cùng PO):
    PurchaseOrder -> PurchaseOrderItem -> PurchaseRequestItem (pk asc) -> ProcurementAllocation (pk asc).

    Thiết kế 2 lượt (validate-toàn-bộ-trước, tạo-toàn-bộ-sau) — không tạo allocation nào trong
    lượt validate, nên 1 dòng sai ở giữa batch không để lại allocation dở dang của các dòng hợp
    lệ đứng trước nó (AC #30), không chỉ dựa vào rollback transaction.
    """
    # (1) actor — kiểm trước khi khoá gì (thuần thuộc tính actor, không cần DB lock).
    if not (actor.role == User.Role.ADMIN or actor.is_superuser):
        raise ValidationError('Chỉ Admin/superuser mới chạy được reconciliation.')
    if not actor.is_active or actor.is_deleted:
        raise ValidationError('Tài khoản actor phải đang hoạt động (không bị khoá/xoá mềm).')
    # (4) batch không rỗng.
    if not allocations:
        raise ValidationError('Danh sách allocation không được rỗng.')
    # (5) không trùng pr_item trong input — kiểm trước khi khoá, thuần trên list truyền vào.
    pr_item_ids = [pr_item.pk for pr_item, _qty in allocations]
    if len(pr_item_ids) != len(set(pr_item_ids)):
        raise ValidationError('Một dòng yêu cầu mua hàng không được xuất hiện quá 1 lần trong batch.')

    # Lock order: PurchaseOrder -> PurchaseOrderItem -> PurchaseRequestItem (pk asc) -> ProcurementAllocation (pk asc)
    po = PurchaseOrder.objects.select_for_update().get(pk=po_item.purchase_order_id)
    po_item = PurchaseOrderItem.objects.select_for_update().select_related('product').get(pk=po_item.pk)
    locked_pr_items = {
        item.pk: item
        for item in PurchaseRequestItem.objects.select_for_update()
        .select_related('purchase_request', 'product').filter(pk__in=pr_item_ids).order_by('pk')
    }
    existing_allocations = list(
        ProcurementAllocation.objects.select_for_update()
        .filter(po_item=po_item, status=ProcurementAllocation.Status.ACTIVE).order_by('pk')
    )

    # (2)(3) PO nguồn + trạng thái.
    if po.source != PurchaseOrder.Source.FROM_PR:
        raise ValidationError('Chỉ reconcile được PO nguồn Từ yêu cầu mua hàng.')
    if po.status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.APPROVED):
        raise ValidationError(f'Không thể reconcile khi PO đang ở trạng thái {po.get_status_display()}.')

    existing_total = sum(a.qty_allocated for a in existing_allocations)
    existing_pr_item_ids = {a.pr_item_id for a in existing_allocations}
    batch_total = 0
    for pr_item, qty in allocations:
        locked_pr_item = locked_pr_items[pr_item.pk]
        # (6) PR đã duyệt.
        if locked_pr_item.purchase_request.status != PurchaseRequest.Status.APPROVED:
            raise ValidationError(f'Dòng PR "{locked_pr_item}" chưa ở trạng thái Đã duyệt.')
        # (7) product khớp.
        if locked_pr_item.product_id != po_item.product_id:
            raise ValidationError(f'Sản phẩm của dòng PR "{locked_pr_item}" không khớp dòng PO.')
        # (8) qty >= 1.
        if qty < 1:
            raise ValidationError(f'Số lượng của dòng "{locked_pr_item}" phải lớn hơn 0.')
        # (9) linked_po BẮT BUỘC khớp — rỗng cũng reject (review lần 5 điểm 3).
        if locked_pr_item.purchase_request.linked_po_id != po_item.purchase_order_id:
            raise ValidationError(
                f'Dòng PR "{locked_pr_item}" chưa từng liên kết đúng PO này qua linked_po — '
                f'ngoài phạm vi recovery procedure này, cần điều tra riêng.')
        # (11) không trùng allocation ACTIVE đã có cho đúng cặp.
        if locked_pr_item.pk in existing_pr_item_ids:
            raise ValidationError(f'Đã tồn tại allocation đang hiệu lực cho dòng PR "{locked_pr_item}".')
        # (10) qty <= qty_open, tính SAU khi đã khoá toàn bộ pr_item trong batch (review lần 5 điểm 4).
        if qty > locked_pr_item.qty_open:
            raise ValidationError(
                f'Số lượng ({qty}) vượt quá số lượng còn mở ({locked_pr_item.qty_open}) của dòng "{locked_pr_item}".')
        batch_total += qty

    # Rule tổng: existing + batch phải khớp CHÍNH XÁC qty_ordered (==, không phải <=).
    if existing_total + batch_total != po_item.qty_ordered:
        raise ValidationError(
            f'Tổng allocation sau khi reconcile ({existing_total + batch_total}) không khớp chính xác '
            f'qty_ordered ({po_item.qty_ordered}).')

    # (12) tạo toàn bộ — chỉ chạy sau khi TOÀN BỘ batch đã validate sạch ở trên.
    created = []
    for pr_item, qty in allocations:
        locked_pr_item = locked_pr_items[pr_item.pk]
        created.append(ProcurementAllocation.objects.create(
            pr_item=locked_pr_item, po_item=po_item, qty_allocated=qty,
            po_no_snapshot=po.po_no, product_code_snapshot=po_item.product.product_code,
            created_by=actor,
        ))

    # (14) re-assert lần cuối trước khi commit.
    final_total = (
        ProcurementAllocation.objects.filter(po_item=po_item, status=ProcurementAllocation.Status.ACTIVE)
        .aggregate(total=Sum('qty_allocated'))['total'] or 0
    )
    if final_total != po_item.qty_ordered:
        raise ValidationError('Re-assert thất bại: tổng allocation cuối cùng không khớp qty_ordered — rollback.')

    # (13) 1 dòng AuditLog cho cả batch.
    detail = '; '.join(f'{locked_pr_items[pr_item.pk]}: {qty}' for pr_item, qty in allocations)
    log_action(
        actor, AuditLog.Action.CREATE, target=po,
        description=f'Reconcile legacy allocation cho dòng PO "{po_item}" (PO {po.po_no}) — {detail}.',
        ip_address=ip_address,
    )
    return created
```
- [ ] **Bước 4: Chạy test, xác nhận PASS cả 6 test ở Bước 1**
- [ ] **Bước 5**: 9 kịch bản vi phạm còn lại (TC-PUR-PR-05-020, AC #31) + case PO `APPROVED`
  (TC-PUR-PR-05-021, AC #32) viết đầy đủ ở **Task 5.3** (Phase 5) thay vì lặp lại ở đây — cùng class
  `ReconcileLegacyPoItemAllocationsTest` này (Task 5.3 bổ sung method vào đúng class đã tạo ở Bước 1,
  không tạo class trùng). Xác nhận `test_TC_PUR_PR_05_014` (Bước 1) đã phủ AC #28/#29;
  `test_TC_PUR_PR_05_019` đã phủ AC #30; `test_TC_PUR_PR_05_024/025/026` đã phủ AC #35/#36/#37.
  **TC-PUR-PR-05-027** (concurrency `reconcile` vs `send_po()`) ở **Task 5.2** — cần
  `TransactionTestCase` + `threading.Barrier`, khác `TestCase` thường của class này.
- [ ] **Bước 6: Sau khi hoàn thành Task 5.3, quay lại chạy toàn bộ `ReconcileLegacyPoItemAllocationsTest`, xác nhận PASS**
- [ ] **Bước 7: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): add reconcile_legacy_po_item_allocations() batch service (T9, review lan 4/5)"
```

## Task 2.11: `qty_received_by_allocation(po_item)` — thuật toán chia tỷ lệ

**File:**
- Sửa: `purchasing/services.py`
- Test: `purchasing/tests.py` (class mới `QtyReceivedByAllocationTest`)

**Giao diện:**
- Cung cấp: `qty_received_by_allocation(po_item) -> dict[int, int]` (khoá là `ProcurementAllocation.pk`)
  — dùng bởi property `PurchaseRequestItem.qty_received` (Task 1.1, import cục bộ).
- Sử dụng: `received_qty_by_product` (đã có sẵn trong `services.py`).

- [ ] **Bước 1: Viết test đang FAIL (AC #18, mục 4 điểm 6 — chia không hết, phần dư dồn vào
  allocation cuối theo `pk` tăng dần)**
```python
class QtyReceivedByAllocationTest(TestCase):
    def test_TC_PUR_PR_05_004_remainder_goes_to_last_allocation_by_pk(self):
        admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        staff = User.objects.create_user(username='staff1', password='staff-pass-123', role=User.Role.STAFF)
        qc_user = User.objects.create_user(username='qc1', password='qc-pass-123', role=User.Role.QC)
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=supplier, source=PurchaseOrder.Source.FROM_PR)
        po_item = PurchaseOrderItem.objects.create(
            purchase_order=po, product=product, qty_ordered=0, unit_price=Decimal('1000'))
        pr = PurchaseRequest.objects.create(
            requested_by=staff, warehouse=warehouse, cost_center='CC-001', status=PurchaseRequest.Status.APPROVED)
        pr_item_a = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        pr_item_b = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=product, qty_requested=5, qty_approved=5,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        allocation_a = create_allocation(pr_item_a, po_item, qty=10, actor=admin_user)  # pk nhỏ hơn
        allocation_b = create_allocation(pr_item_b, po_item, qty=5, actor=admin_user)   # pk lớn hơn -> nhận phần dư
        grn = Grn.objects.create(po=po, supplier=supplier, created_by=qc_user)
        GrnItem.objects.create(grn=grn, product=product, qty_received=9, unit_price=Decimal('1000'))

        result = qty_received_by_allocation(po_item)
        self.assertEqual(result[allocation_a.pk] + result[allocation_b.pk], 9)
        # 9 * 10 // 15 = 6 (allocation_a, pk nhỏ hơn, không nhận dư); phần dư 3 dồn vào allocation_b.
        self.assertEqual(result[allocation_a.pk], 6)
        self.assertEqual(result[allocation_b.pk], 3)
        pr_item_a.refresh_from_db()
        pr_item_b.refresh_from_db()
        self.assertEqual(pr_item_a.qty_received, 6)
        self.assertEqual(pr_item_b.qty_received, 3)
```
  (Cần import `Grn`, `GrnItem` đã có sẵn ở đầu `purchasing/tests.py`.)
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm vào `purchasing/services.py` (ngay sau
  `received_qty_by_product`, vì cùng nhóm "tính received"):
```python
def qty_received_by_allocation(po_item):
    """Mục 4 điểm 6: chia total_received (GRN đã ghi nhận cho product của po_item) cho các
    ProcurementAllocation ACTIVE trỏ tới po_item, theo tỷ lệ qty_allocated/qty_ordered — phần dư
    do làm tròn xuống dồn hết vào allocation CUỐI CÙNG theo pk tăng dần, đảm bảo tổng luôn khớp
    chính xác total_received (không thừa/thiếu). Trả dict {allocation_id: qty_received}.
    """
    total_received = received_qty_by_product(po_item.purchase_order).get(po_item.product_id, 0)
    allocations = list(
        po_item.allocations.filter(status=ProcurementAllocation.Status.ACTIVE).order_by('pk')
    )
    if not allocations or po_item.qty_ordered == 0:
        return {}
    result = {}
    distributed = 0
    for allocation in allocations[:-1]:
        share = (total_received * allocation.qty_allocated) // po_item.qty_ordered
        result[allocation.pk] = share
        distributed += share
    last = allocations[-1]
    result[last.pk] = total_received - distributed
    return result
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): add qty_received_by_allocation() - proportional split algorithm (mục 4 điểm 6)"
```

## Task 2.12: `submit_purchase_request()` — set `department_snapshot`

**File:**
- Sửa: `purchasing/services.py` (hàm `submit_purchase_request` đã có — thêm 1 dòng, KHÔNG viết
  hàm mới)
- Test: `purchasing/tests.py` (class mới `SubmitPurchaseRequestDepartmentSnapshotTest`)

**Giao diện:**
- Không đổi chữ ký hàm — chỉ thêm side-effect set `pr.department_snapshot`.

- [ ] **Bước 1: Viết test đang FAIL (TC-PUR-PR-02-001/002)**
```python
class SubmitPurchaseRequestDepartmentSnapshotTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff1', password='staff-pass-123', role=User.Role.STAFF, department=User.Department.WAREHOUSE)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001')
        PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg'),
            qty_requested=1, required_date=timezone.localdate(), currency='VND',
            estimated_unit_price=Decimal('1000'), budget_category='NL')

    def test_TC_PUR_PR_02_001_submit_sets_department_snapshot(self):
        submit_purchase_request(self.pr, actor=self.staff)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.department_snapshot, User.Department.WAREHOUSE)

    def test_TC_PUR_PR_02_002_snapshot_immutable_after_requester_department_changes(self):
        submit_purchase_request(self.pr, actor=self.staff)
        self.staff.department = User.Department.QC
        self.staff.save(update_fields=['department'])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.department_snapshot, User.Department.WAREHOUSE)  # không đọc lại
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `department_snapshot` vẫn rỗng sau submit.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm 2 dòng vào `submit_purchase_request()` hiện
  có, ngay sau khối `if origin_department and ...: ... else: ...` (trước
  `pr.save(update_fields=['status'])`):
```python
    pr.department_snapshot = origin_department
    pr.save(update_fields=['status', 'department_snapshot'])
```
  (Thay dòng `pr.save(update_fields=['status'])` hiện có bằng dòng trên — gộp 2 field cùng 1 lần
  save, không thêm lệnh `save()` thứ hai.)
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**
```bash
git add purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): submit_purchase_request() sets department_snapshot (immutable after submit)"
```

---

# Phase 3 — Form/View

## Task 3.1: `PurchaseRequestForm` — thêm `cost_center`/`project`

**File:**
- Sửa: `purchasing/forms.py` (class `PurchaseRequestForm`)
- Sửa: `purchasing/templates/purchasing/pr_form.html` (không cần sửa nếu template render bằng
  `{{ form }}`/`{% for field in form %}` chung — kiểm tra template hiện có trước khi giả định)
- Test: `purchasing/tests.py` (thêm vào test form hiện có nếu có, hoặc test trực tiếp qua
  `pr_create` view test đã có sẵn trong `PurchaseRequestCrudTest`-tương-đương)

**Giao diện:**
- Cung cấp: `PurchaseRequestForm.fields` gồm thêm `cost_center`, `project`.

- [ ] **Bước 1: Viết test đang FAIL (AC #1 — thiếu `cost_center` không lưu được)**
```python
class PurchaseRequestFormFieldsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='rq1', password='rq-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')

    def test_AC1_missing_cost_center_invalid(self):
        form = PurchaseRequestForm(data={'warehouse': self.warehouse.pk, 'note': '', 'project': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('cost_center', form.errors)

    def test_cost_center_and_project_accepted(self):
        form = PurchaseRequestForm(data={
            'warehouse': self.warehouse.pk, 'note': '', 'cost_center': 'CC-001', 'project': 'Dự án A'})
        self.assertTrue(form.is_valid(), form.errors)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `cost_center` không nằm trong `Meta.fields` nên
  `form.errors` không có key đó (form coi như thừa field, không báo lỗi thiếu).
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa `Meta.fields` của `PurchaseRequestForm`:
```python
class Meta:
    model = PurchaseRequest
    fields = ['warehouse', 'assigned_to', 'cost_center', 'project', 'note']
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5**: mở `purchasing/templates/purchasing/pr_form.html`, xác nhận template render form
  bằng vòng lặp field tổng quát (không liệt kê tay từng field) — nếu template LIỆT KÊ TAY từng
  field (kiểm tra thực tế trước khi giả định), thêm 2 dòng `{{ form.cost_center }}`/
  `{{ form.project }}` vào đúng vị trí cạnh `warehouse`/`note`.
- [ ] **Bước 6: Commit**
```bash
git add purchasing/forms.py purchasing/templates/purchasing/pr_form.html purchasing/tests.py
git commit -m "feat(pur): PurchaseRequestForm - add cost_center/project fields"
```

## Task 3.2: `PurchaseRequestItemForm` — non-catalog + budget/currency/estimate + JS prefill

**File:**
- Sửa: `purchasing/forms.py` (class `PurchaseRequestItemForm`)
- Sửa: `purchasing/templates/purchasing/pr_form.html` (formset item template — thêm toggle
  non-catalog + `data-category` trên `<option>` + script prefill)
- Test: `purchasing/tests.py` (class mới `PurchaseRequestItemFormTest`)

**Giao diện:**
- Sử dụng: `PurchaseRequestItem.clean()` (Task 2.6, chạy tự động qua `ModelForm.full_clean()` —
  KHÔNG viết lại logic XOR ở tầng form).
- Cung cấp: `PurchaseRequestItemForm` với đủ field mục 2.2 — dùng bởi `PurchaseRequestItemFormSet`
  (factory hiện có, chỉ cần đổi `form=` nếu tên class không đổi thì không cần sửa dòng
  `inlineformset_factory`).

- [ ] **Bước 1: Viết test đang FAIL (form-level integration của Model.clean() qua ModelForm)**
```python
class PurchaseRequestItemFormTest(TestCase):
    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg', category='Nguyên liệu')

    def _base_data(self, **overrides):
        data = {
            'product': '', 'qty_requested': 5, 'required_date': timezone.localdate().isoformat(),
            'currency': 'VND', 'estimated_unit_price': '1000', 'budget_category': '',
            'non_catalog_name': '', 'non_catalog_uom': '', 'non_catalog_note': '',
        }
        data.update(overrides)
        return data

    def test_catalog_line_valid_without_non_catalog_fields(self):
        form = PurchaseRequestItemForm(data=self._base_data(product=self.product.pk))
        self.assertTrue(form.is_valid(), form.errors)

    def test_non_catalog_line_valid_without_product(self):
        form = PurchaseRequestItemForm(data=self._base_data(non_catalog_name='Ống nhựa', non_catalog_uom='cây'))
        self.assertTrue(form.is_valid(), form.errors)

    def test_neither_product_nor_non_catalog_invalid(self):
        form = PurchaseRequestItemForm(data=self._base_data())
        self.assertFalse(form.is_valid())

    def test_budget_category_fallback_applied_through_form(self):
        form = PurchaseRequestItemForm(data=self._base_data(product=self.product.pk, budget_category=''))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.instance.budget_category, 'Nguyên liệu')
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `TypeError`/`ValueError` do `Meta.fields` chưa có các
  field mới, hoặc `product` vẫn bị coi bắt buộc (form hiện tại không cho `product=''`).
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa `PurchaseRequestItemForm`:
```python
class PurchaseRequestItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseRequestItem
        fields = [
            'product', 'qty_requested', 'required_date', 'currency', 'estimated_unit_price',
            'budget_category', 'non_catalog_name', 'non_catalog_uom', 'non_catalog_note',
        ]
        widgets = {
            'required_date': forms.DateInput(attrs={'type': 'date'}),
            'non_catalog_note': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.fields['product'].required đã tự động False nhờ model field null=True/blank=True
        # (ModelForm suy required từ blank của model field, không cần set tay).
        queryset = Q(is_active=True)
        if self.instance.pk and self.instance.product_id:
            queryset |= Q(pk=self.instance.product_id)
        self.fields['product'].queryset = Product.objects.filter(queryset).distinct()
        _bootstrapify(self.fields)
```
  (Dòng `PurchaseRequestItemFormSet = inlineformset_factory(...)` hiện có KHÔNG cần sửa — vẫn
  tham chiếu đúng class `PurchaseRequestItemForm` vừa đổi nội dung.)
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Sửa template `pr_form.html`** — thêm `data-category="{{ product.category }}"` vào
  mỗi `<option>` của `product` (Django `ModelChoiceField` không tự thêm `data-*`, cần widget tuỳ
  chỉnh HOẶC vòng lặp tay; đơn giản nhất: render 1 `<select>` tay bằng vòng lặp `Product.objects
  .filter(is_active=True)` thay vì `{{ form.product }}` mặc định — hoặc dùng
  `forms.Select(attrs=...)` kết hợp `TypedChoiceField` tự set `data-category` qua
  `choice_attrs`/`create_option()` override. **Quyết định cụ thể hoá** (FSD không ghi rõ cơ chế
  Django, chỉ ghi rõ hành vi mong muốn): override
  `forms.Select` thành `ProductSelectWithCategory(forms.Select)`:
```python
class ProductSelectWithCategory(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            try:
                option['attrs']['data-category'] = Product.objects.get(pk=value).category
            except Product.DoesNotExist:
                pass
        return option
```
  gán `widgets = {'product': ProductSelectWithCategory, ...}` trong `Meta` của
  `PurchaseRequestItemForm`. Thêm script cuối `pr_form.html` (mirror cách project đã tự viết JS
  nhỏ cho formset thêm dòng động, không dùng thư viện ngoài).

  **Lỗi đã sửa so với v1 (review phát hiện)**: `document.querySelectorAll(...).forEach(el =>
  el.addEventListener(...))` chỉ gắn listener cho các `<select>` đã tồn tại SẴN trong DOM tại thời
  điểm script chạy (lúc tải trang) — dòng formset THÊM ĐỘNG sau đó (form "Thêm dòng" tiêu chuẩn của
  Django formset, clone template `empty_form` bằng JS) sẽ KHÔNG có listener này, vì phần tử của nó
  chưa tồn tại khi `querySelectorAll` chạy. Sửa bằng **event delegation**: gắn đúng 1 listener
  `change` lên `document` (bắt sự kiện nổi bọt/bubbling từ bất kỳ `<select>` con nào, kể cả phần tử
  được thêm vào DOM sau này), rồi lọc đúng phần tử bằng `event.target.closest(...)` tại thời điểm sự
  kiện xảy ra — không cần biết trước tập phần tử lúc gắn listener:
```html
<script>
document.addEventListener('change', function (event) {
  var select = event.target.closest('select[id$="-product"]');
  if (!select) return;
  var opt = select.options[select.selectedIndex];
  var category = opt.getAttribute('data-category');
  var row = select.closest('tr') || select.closest('.formset-row');
  var budgetInput = row ? row.querySelector('input[id$="-budget_category"]') : null;
  if (budgetInput && category && !budgetInput.value) {
    budgetInput.value = category;
  }
});
</script>
```
  (`'change'` bubble lên tới `document` bình thường ở mọi trình duyệt hiện đại — không cần
  `capture: true`. Điều chỉnh selector `row`/`closest(...)` cho khớp cấu trúc HTML thật của
  `pr_form.html` sau khi mở file — đây là điểm engineer cần xác nhận lại DOM thật trước khi ráp,
  không đoán mù; cách delegation này không đổi dù cấu trúc DOM khác dự đoán, miễn `row`/`budgetInput`
  selector đúng.)
- [ ] **Bước 6: Test thủ công trên trình duyệt** (theo quy ước "test UI trước khi báo hoàn thành"):
  mở `pr_create`, chọn 1 sản phẩm có `category` khác rỗng cho 1 dòng mới thêm bằng JS (chưa
  round-trip server) — xác nhận `budget_category` tự điền, sửa lại được, và submit thiếu field
  bắt buộc báo lỗi đúng field.

  **Lỗi đã sửa so với v2 (review phát hiện — N+1 query trong `ProductSelectWithCategory
  .create_option()`, Bước 5 ở trên)**: bản gốc dùng `Product.objects.get(pk=value)` để lấy
  `category` cho từng `<option>`. Đã xác minh thực nghiệm trong đúng môi trường Django của repo
  này (`python manage.py shell`, Django 5.2.16) rằng đây **không chỉ là N+1 (chậm) mà thực sự
  CRASH**: kể từ Django 3.1, `value` mà `create_option()` nhận được cho một `ModelChoiceField`
  không phải là pk thô, mà là instance `django.forms.models.ModelChoiceIteratorValue` (bọc pk +
  giữ sẵn `.instance` — chính là object đã fetch từ vòng lặp queryset). Gọi
  `Product.objects.get(pk=value)` khiến Django cố `int(value)` để chuẩn bị SQL param, và
  `ModelChoiceIteratorValue` không định nghĩa `__int__`, nên raise:
  ```
  TypeError: Field 'id' expected a number but got <django.forms.models.ModelChoiceIteratorValue object at 0x...>.
  ```
  Đây là `TypeError`, không phải `Product.DoesNotExist`, nên khối `except Product.DoesNotExist:
  pass` hiện có KHÔNG bắt được — lỗi văng thẳng lên, render form `product` (bất kỳ field nào dùng
  `ProductSelectWithCategory` với ≥1 lựa chọn thật) sẽ crash toàn bộ trang, không chỉ chậm.
  Sửa bằng TDD:
  - [ ] **Bước 7: Viết test đang FAIL**
```python
    def test_product_select_with_category_widget_renders_without_error(self):
        Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg', category='Phụ gia')
        form = PurchaseRequestItemForm()
        with self.assertNumQueries(1):
            rendered = str(form['product'])
        self.assertIn('data-category="Nguyên liệu"', rendered)
        self.assertIn('data-category="Phụ gia"', rendered)
```
  - [ ] **Bước 8: Chạy test, xác nhận FAIL** — chạy
    `python manage.py test purchasing.tests.PurchaseRequestItemFormTest.test_product_select_with_category_widget_renders_without_error -v 2`,
    kỳ vọng: `TypeError: Field 'id' expected a number but got <django.forms.models.ModelChoiceIteratorValue ...>`
    (văng ra từ bên trong `create_option()`, không phải một `AssertionError` thông thường).
  - [ ] **Bước 9: Sửa code tối thiểu để PASS** — thay `ProductSelectWithCategory` ở Bước 5 bằng:
```python
class ProductSelectWithCategory(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, 'instance', None)
        if instance is not None:
            option['attrs']['data-category'] = instance.category
        return option
```
    (`value.instance` là object `Product` Django đã fetch sẵn khi lặp qua
    `self.fields['product'].queryset` để dựng danh sách `<option>` — không cần, và không được,
    query lại. `value` cho lựa chọn "---------" mặc định là chuỗi rỗng `''`, không có thuộc tính
    `instance`, nên `getattr(..., None)` trả `None` và bị bỏ qua đúng như mong muốn — không cần
    try/except nữa.)
  - [ ] **Bước 10: Chạy lại test, xác nhận PASS** — đúng 1 query (fetch `queryset.all()` một lần
    khi dựng choices), không phát sinh thêm query nào theo số `<option>`, và không còn crash dù có
    bao nhiêu sản phẩm.
- [ ] **Bước 11: Commit**
```bash
git add purchasing/forms.py purchasing/templates/purchasing/pr_form.html purchasing/tests.py
git commit -m "feat(pur): PurchaseRequestItemForm - non-catalog toggle, budget_category JS prefill"
```

## Task 3.3: `pr_detail` — hàng số liệu mới + nút Huỷ phần còn mở + badge/nút Map non-catalog

**File:**
- Sửa: `purchasing/views.py` (view `pr_detail` — thêm context; view mới `pr_item_cancel_open_qty`)
- Sửa: `purchasing/urls.py` (route `pr_item_cancel_open_qty`)
- Sửa: `purchasing/templates/purchasing/pr_detail.html` (bảng "Chi tiết yêu cầu")
- Test: `purchasing/tests.py` (class mới `PrItemCancelOpenQtyViewTest`)

**Giao diện:**
- Sử dụng: `cancel_pr_item_open_qty` (Task 2.8).
- Cung cấp: helper `can_cancel_pr_item_open_qty(user, pr)` (mục 1/mục 4 điểm 9) — dùng bởi
  `pr_detail` (hiện nút) và `pr_item_cancel_open_qty` (gate quyền thật).

- [ ] **Bước 1: Viết test đang FAIL (AC #17 — chặn PUR Staff không phải `assigned_to`)**
```python
class PrItemCancelOpenQtyViewTest(TestCase):
    def setUp(self):
        self.requester = User.objects.create_user(username='rq1', password='rq-pass-123', role=User.Role.STAFF)
        self.pur_staff = User.objects.create_user(
            username='pur1', password='pur-pass-123', role=User.Role.PURCHASING, department=User.Department.PURCHASING)
        self.other_pur_staff = User.objects.create_user(
            username='pur2', password='pur-pass-123', role=User.Role.PURCHASING, department=User.Department.PURCHASING)
        self.pur_manager = User.objects.create_user(
            username='purm', password='purm-pass-123', role=User.Role.MANAGER,
            department=User.Department.PURCHASING, is_manager=True)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.requester, warehouse=self.warehouse, cost_center='CC-001',
            assigned_to=self.pur_staff, status=PurchaseRequest.Status.APPROVED)
        self.item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')

    def test_TC_PUR_PR_07_004_non_assigned_staff_forbidden(self):
        self.client.login(username='pur2', password='pur-pass-123')
        response = self.client.post(
            reverse('purchasing:pr_item_cancel_open_qty', args=[self.item.pk]),
            {'qty': 3, 'reason': 'test'})
        self.assertEqual(response.status_code, 403)

    def test_assigned_staff_allowed(self):
        self.client.login(username='pur1', password='pur-pass-123')
        response = self.client.post(
            reverse('purchasing:pr_item_cancel_open_qty', args=[self.item.pk]),
            {'qty': 3, 'reason': 'test'})
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.qty_cancelled, 3)

    def test_department_manager_purchasing_allowed_regardless_of_assigned_to(self):
        self.client.login(username='purm', password='purm-pass-123')
        response = self.client.post(
            reverse('purchasing:pr_item_cancel_open_qty', args=[self.item.pk]),
            {'qty': 2, 'reason': 'test'})
        self.assertEqual(response.status_code, 302)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `NoReverseMatch` (route chưa tồn tại).
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm vào `purchasing/views.py` (sau
  `can_manage_pur_pr`):
```python
def can_cancel_pr_item_open_qty(user, pr):
    """Mục 1/mục 4 điểm 9: update trên 'pr' + (quản lý phòng Mua hàng HOẶC đúng assigned_to của
    PR đó) — PUR Staff KHÁC (dù cùng phòng ban) không được, kể cả có quyền update trên 'pr'.
    """
    if not user.can('update', 'pr'):
        return False
    return user.is_department_manager(User.Department.PURCHASING) or pr.assigned_to_id == user.id
```
  Thêm view (sau `pr_delete` hoặc cuối file, nhóm cùng các view PR-item-level):
```python
@login_required
def pr_item_cancel_open_qty(request, pk):
    """POST-only: huỷ 1 phần qty_open của 1 dòng PR (PUR-PR-07). Quyền: mục 1/mục 4 điểm 9."""
    item = get_object_or_404(PurchaseRequestItem.objects.select_related('purchase_request'), pk=pk)
    if not can_cancel_pr_item_open_qty(request.user, item.purchase_request):
        raise PermissionDenied('Không có quyền huỷ phần còn mở của dòng yêu cầu mua hàng này.')
    if request.method == 'POST':
        try:
            qty = int(request.POST.get('qty', ''))
        except ValueError:
            messages.error(request, 'Số lượng huỷ không hợp lệ.')
            return redirect('purchasing:pr_detail', pk=item.purchase_request_id)
        reason = request.POST.get('reason', '')
        try:
            cancel_pr_item_open_qty(item, qty, reason, actor=request.user, ip_address=client_ip(request))
            messages.success(request, f'Đã huỷ {qty} số lượng còn mở của dòng "{item}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:pr_detail', pk=item.purchase_request_id)
```
  Thêm import `cancel_pr_item_open_qty`, `PurchaseRequestItem` (nếu `PurchaseRequestItem` chưa
  import trực tiếp ở đầu `views.py` — hiện chỉ import `PurchaseOrder`, `PurchaseRequest`, cần mở
  rộng dòng `from .models import PurchaseOrder, PurchaseRequest` thành có thêm
  `PurchaseRequestItem`).
- [ ] **Bước 4**: Thêm route vào `purchasing/urls.py`:
```python
path('pr-item/<int:pk>/cancel-open-qty/', views.pr_item_cancel_open_qty, name='pr_item_cancel_open_qty'),
```
- [ ] **Bước 5: Chạy test, xác nhận PASS**
- [ ] **Bước 6: Sửa `pr_detail.html`** — mở rộng bảng "Chi tiết yêu cầu" (dòng 125-142 hiện tại)
  thêm 6 cột số liệu mới (`qty_approved`/`qty_allocated`/`qty_ordered`/`qty_received`/
  `qty_cancelled`/`qty_open` — cộng với cột `qty_requested` sẵn có thành đủ 7 số của mục 5) + cột
  badge/nút map non-catalog + cột nút huỷ:
```html
<thead>
  <tr>
    <th>Sản phẩm</th>
    <th>Yêu cầu</th>
    <th>Đã duyệt</th>
    <th>Đã phân bổ</th>
    <th>Đã đặt</th>
    <th>Đã nhận</th>
    <th>Đã huỷ</th>
    <th>Còn mở</th>
    <th></th>
  </tr>
</thead>
<tbody>
  {% for item in obj.items.all %}
  <tr>
    <td>
      {% if item.is_non_catalog %}
        {{ item.non_catalog_name }} <span class="badge bg-warning text-dark">Chưa map Product</span>
      {% else %}
        {{ item.product.product_code }} — {{ item.product.name }}
      {% endif %}
    </td>
    <td>{{ item.qty_requested }}</td>
    <td>{{ item.qty_approved|default:"—" }}</td>
    <td>{{ item.qty_allocated }}</td>
    <td>{{ item.qty_ordered }}</td>
    <td>{{ item.qty_received }}</td>
    <td>{{ item.qty_cancelled }}</td>
    <td>{{ item.qty_open }}</td>
    <td>
      {% if item.is_non_catalog and can_map_non_catalog %}
        <a href="{% url 'purchasing:pr_item_map_product' item.pk %}" class="btn btn-outline-secondary btn-sm">Map sang Product</a>
      {% endif %}
      {% if item.qty_open > 0 and obj.status == 'APPROVED' and can_cancel_pr_item %}
        <form method="post" action="{% url 'purchasing:pr_item_cancel_open_qty' item.pk %}" class="d-inline">
          {% csrf_token %}
          <input type="hidden" name="qty" value="{{ item.qty_open }}">
          <input type="hidden" name="reason" value="Huỷ toàn bộ phần còn mở">
          <button type="submit" class="btn btn-outline-danger btn-sm">Huỷ phần còn mở</button>
        </form>
      {% endif %}
    </td>
  </tr>
  {% empty %}
  <tr><td colspan="9" class="text-center text-muted">Chưa có dòng hàng.</td></tr>
  {% endfor %}
</tbody>
```
  (Nút "Huỷ phần còn mở" ở bản tối thiểu này huỷ TOÀN BỘ `qty_open` với lý do cố định — nếu cần
  huỷ 1 phần + lý do tự nhập, thay bằng modal/form riêng có input `qty`/`reason` — ghi chú lại đây
  làm quyết định UX tối thiểu cho Task này, có thể mở rộng sau không phải blocker.)
  Thêm helper quyền ngay trong **Task 3.3** (không chờ Task 3.5, vì Task 3.3 phải test/commit PASS
  độc lập trước khi sang Task kế tiếp), đặt cạnh `can_decide_pr`/`can_manage_pur_pr` trong
  `purchasing/views.py`:
```python
def can_map_non_catalog(user):
    if not (user.can('update', 'pr') and user.can_view_menu('catalog')):
        return False
    if user.is_superuser or user.role == User.Role.ADMIN:
        return True
    if user.role == User.Role.MANAGER:
        return user.is_department_manager(User.Department.PURCHASING)
    return user.department == User.Department.PURCHASING
```
  Sau đó thêm context vào view `pr_detail` (mục `return render(...)`, thêm 2 key mới). **Sửa theo
  review lần 3**: dùng chung `can_map_non_catalog()` thay vì lặp lại điều kiện
  role/permission rời rạc — tránh 2 nơi cùng biểu diễn một rule mà lệch nhau (đúng lỗi bị review lần
  3 phát hiện: bản v2 viết riêng ở đây, thiếu `can_view_menu('catalog')` và ràng buộc phòng ban):
```python
'can_map_non_catalog': can_map_non_catalog(request.user),
'can_cancel_pr_item': can_cancel_pr_item_open_qty(request.user, obj),
```
- [ ] **Bước 7: Test thủ công trên trình duyệt** — xác nhận 7 số hiển thị đúng, nút chỉ hiện đúng
  người có quyền.
- [ ] **Bước 8: Commit**
```bash
git add purchasing/views.py purchasing/urls.py purchasing/templates/purchasing/pr_detail.html purchasing/tests.py
git commit -m "feat(pur): pr_detail - qty breakdown row, cancel open qty button, map non-catalog badge"
```

## Task 3.4: `pr_approve` — sửa `qty_approved` từng dòng khi duyệt cấp `PENDING_PUR`

**File:**
- Sửa: `purchasing/views.py` (view `pr_approve` đã có)
- Sửa: `purchasing/templates/purchasing/pr_detail.html` (form Duyệt dòng 49-54 + thêm cột trong
  bảng item đã sửa ở Task 3.3)
- Test: `purchasing/tests.py` (class mới `PrApproveQtyOverrideViewTest`)

**Giao diện:**
- Sử dụng: `decide_purchase_request(..., qty_approved_overrides=...)` (Task 2.9).

- [ ] **Bước 1: Viết test đang FAIL**
```python
class PrApproveQtyOverrideViewTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='staff1', password='staff-pass-123', role=User.Role.STAFF)
        self.pur_manager = User.objects.create_user(
            username='purm', password='purm-pass-123', role=User.Role.MANAGER,
            department=User.Department.PURCHASING, is_manager=True)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001')
        self.item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        submit_purchase_request(self.pr, actor=self.staff)  # -> PENDING_PUR

    def test_approve_view_applies_qty_approved_override_from_post(self):
        self.client.login(username='purm', password='purm-pass-123')
        response = self.client.post(
            reverse('purchasing:pr_approve', args=[self.pr.pk]),
            {f'qty_approved_{self.item.pk}': '6'})
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.qty_approved, 6)

    def test_approve_view_invalid_qty_input_shows_error_no_transition(self):
        self.client.login(username='purm', password='purm-pass-123')
        self.client.post(reverse('purchasing:pr_approve', args=[self.pr.pk]), {f'qty_approved_{self.item.pk}': 'abc'})
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PurchaseRequest.Status.PENDING_PUR)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `qty_approved` giữ nguyên `None`/mặc định thay vì 6
  (view hiện tại không đọc field POST mới).
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa view `pr_approve` hiện có, chèn logic đọc
  `qty_approved_overrides` ngay trước khối `try:`:
```python
@login_required
def pr_approve(request, pk):
    obj = get_object_or_404(PurchaseRequest, pk=pk)
    if not can_decide_pr(request.user, obj):
        raise PermissionDenied('Không có quyền duyệt yêu cầu mua hàng.')
    if request.method == 'POST':
        approval = latest_approval_for(obj)
        qty_approved_overrides = {}
        if obj.status == PurchaseRequest.Status.PENDING_PUR:
            for item in obj.items.all():
                raw = request.POST.get(f'qty_approved_{item.pk}', '').strip()
                if raw:
                    try:
                        qty_approved_overrides[item.pk] = int(raw)
                    except ValueError:
                        messages.error(request, f'Số lượng duyệt không hợp lệ cho dòng "{item}".')
                        return redirect('purchasing:pr_detail', pk=obj.pk)
        try:
            if approval is None or approval.status != Approval.Status.PENDING:
                raise ValidationError('Yêu cầu này không có phiếu duyệt nào đang chờ xử lý.')
            decide_purchase_request(
                approval, True, actor=request.user, ip_address=client_ip(request),
                qty_approved_overrides=qty_approved_overrides,
            )
            messages.success(request, f'Đã duyệt yêu cầu "{obj.request_no}".')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return redirect('purchasing:pr_detail', pk=obj.pk)
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Sửa template** — thêm `id="pr-approve-form"` vào `<form>` ở dòng 50, để input số
  lượng nằm TRONG bảng item (Task 3.3) vẫn liên kết được với form này qua thuộc tính HTML5 `form=`
  (không cần lồng bảng vào trong `<form>`):
```html
{% if can_approve %}
  <form method="post" id="pr-approve-form" action="{% url 'purchasing:pr_approve' obj.pk %}" class="d-inline">
    {% csrf_token %}
    <button type="submit" class="btn btn-success btn-sm"><i class="bi bi-check-lg"></i> Duyệt</button>
  </form>
{% endif %}
```
  Thêm 1 cột vào bảng item (nối tiếp cột "Đã duyệt" đã có ở Task 3.3, CHỈ hiện khi
  `obj.status == 'PENDING_PUR' and can_approve` — thay `<td>{{ item.qty_approved|default:"—" }}</td>`
  bằng:
```html
<td>
  {% if obj.status == 'PENDING_PUR' and can_approve %}
    <input type="number" name="qty_approved_{{ item.pk }}" value="{{ item.qty_requested }}"
           min="0" max="{{ item.qty_requested }}" form="pr-approve-form" class="form-control form-control-sm" style="width: 6rem;">
  {% else %}
    {{ item.qty_approved|default:"—" }}
  {% endif %}
</td>
```
- [ ] **Bước 6: Test thủ công trên trình duyệt** — xác nhận sửa số ở `PENDING_PUR` rồi bấm "Duyệt"
  áp dụng đúng giá trị đã sửa; ở `PENDING_DEPT` không hiện input (chỉ hiện số `—`).
- [ ] **Bước 7: Commit**
```bash
git add purchasing/views.py purchasing/templates/purchasing/pr_detail.html purchasing/tests.py
git commit -m "feat(pur): pr_approve - editable qty_approved per line at PENDING_PUR stage"
```

## Task 3.5: `pr_item_map_product` — map non-catalog sang Product

**File:**
- Sửa: `purchasing/forms.py` (form mới `PrItemMapProductForm`)
- Sửa: `purchasing/views.py` (view mới `pr_item_map_product`)
- Sửa: `purchasing/urls.py`
- Tạo: `purchasing/templates/purchasing/pr_item_map_product.html`
- Test: `purchasing/tests.py` (class mới `PrItemMapProductViewTest`)

**Giao diện:**
- Sử dụng: `map_non_catalog_item` (Task 2.7).

**Quyết định cụ thể hoá** (FSD mục 5 mô tả "chọn Product có sẵn HOẶC tạo mới (form rút gọn của
catalog create)" — không pin cứng cơ chế Django): dùng 1 `forms.Form` (không phải `ModelForm`) với
2 field tuỳ chọn loại trừ nhau — `existing_product` (ModelChoiceField, không bắt buộc) và
`new_product_name`/`new_product_uom`/`new_product_category` (bắt buộc nếu không chọn
`existing_product`) — validate XOR trong `clean()`. View tự tạo `Product` mới trong cùng
transaction nếu chọn nhánh "tạo mới", rồi gọi `map_non_catalog_item()` — KHÔNG tái sử dụng
`catalog.forms.ProductForm` đầy đủ (nhiều field không liên quan, vd `preferred_supplier`/
`min_level` không cần thiết ở bước map nhanh này) — chỉ 3 field tối thiểu.

- [ ] **Bước 1: Viết test đang FAIL (TC-PUR-PR-06-002, mục 5)**
```python
class PrItemMapProductViewTest(TestCase):
    def setUp(self):
        self.pur_staff = User.objects.create_user(
            username='pur1', password='pur-pass-123', role=User.Role.PURCHASING, department=User.Department.PURCHASING)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.pur_staff, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.PENDING_PUR)
        self.item = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=None, qty_requested=3,
            non_catalog_name='Ống nhựa PVC', non_catalog_uom='cây',
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('5000'), budget_category='VT')

    def test_map_to_existing_product(self):
        existing = Product.objects.create(product_code='NVL-0002', name='Ống nhựa PVC', uom='cây')
        self.client.login(username='pur1', password='pur-pass-123')
        response = self.client.post(
            reverse('purchasing:pr_item_map_product', args=[self.item.pk]),
            {'existing_product': existing.pk})
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.product_id, existing.pk)

    def test_map_creates_new_product(self):
        self.client.login(username='pur1', password='pur-pass-123')
        response = self.client.post(
            reverse('purchasing:pr_item_map_product', args=[self.item.pk]),
            {'new_product_code': 'NVL-0099', 'new_product_name': 'Ống nhựa PVC', 'new_product_uom': 'cây',
             'new_product_category': 'Vật tư'})
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.product_id)
        self.assertEqual(self.item.product.product_code, 'NVL-0099')
        self.assertEqual(self.item.product.category, 'Vật tư')

    def test_catalog_menu_revoked_forbids_map(self):
        """Review lần 3, blocker #3: FSD mục 1 ghi map-non-catalog đi theo `pr` + `catalog` — thu
        hồi riêng `can_view_menu_catalog` của PUR Staff (dù vẫn còn `update` trên `pr`) phải chặn
        được, không chỉ kiểm role/`can('update','pr')`."""
        from django.contrib.auth.models import Permission
        perm = Permission.objects.get(content_type__app_label='accounts', codename='can_view_menu_catalog')
        self.pur_staff.user_permissions.remove(perm)
        self.client.login(username='pur1', password='pur-pass-123')
        response = self.client.post(
            reverse('purchasing:pr_item_map_product', args=[self.item.pk]),
            {'existing_product': ''})
        self.assertEqual(response.status_code, 403)

    def test_manager_wrong_department_forbidden(self):
        """Review lần 3, blocker #3: FSD mục 5 ghi 'chỉ PUR Staff/Manager thấy nút' — "Manager" ở
        đây là PUR Manager (`is_department_manager('PURCHASING')`), không phải bất kỳ Manager nào.
        Bản v2 cho MANAGER role đi qua bất kể phòng ban."""
        qc_manager = User.objects.create_user(
            username='qcm', password='qcm-pass-123', role=User.Role.MANAGER,
            department=User.Department.QC, is_manager=True)
        self.client.login(username='qcm', password='qcm-pass-123')
        response = self.client.post(
            reverse('purchasing:pr_item_map_product', args=[self.item.pk]),
            {'existing_product': ''})
        self.assertEqual(response.status_code, 403)

    def test_duplicate_new_product_code_renders_form_error_without_partial_map(self):
        Product.objects.create(product_code='NVL-0099', name='Đã tồn tại', uom='kg')
        self.client.login(username='pur1', password='pur-pass-123')
        response = self.client.post(
            reverse('purchasing:pr_item_map_product', args=[self.item.pk]), {
                'new_product_code': 'NVL-0099', 'new_product_name': 'Ống nhựa PVC',
                'new_product_uom': 'cây', 'new_product_category': 'Vật tư',
            })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Mã sản phẩm đã tồn tại')
        self.item.refresh_from_db()
        self.assertIsNone(self.item.product_id)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `NoReverseMatch`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm form (`purchasing/forms.py`, cuối file):
```python
class PrItemMapProductForm(forms.Form):
    existing_product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True), required=False, label='Chọn sản phẩm có sẵn')
    new_product_code = forms.CharField(max_length=50, required=False, label='Mã sản phẩm mới')
    new_product_name = forms.CharField(max_length=200, required=False, label='Tên sản phẩm mới')
    new_product_uom = forms.CharField(max_length=20, required=False, label='Đơn vị tính')
    new_product_category = forms.CharField(max_length=100, required=False, label='Danh mục sản phẩm mới')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)

    def clean(self):
        cleaned_data = super().clean()
        existing = cleaned_data.get('existing_product')
        # Đã sửa so với v1 (review phát hiện): "Quyết định cụ thể hoá" ở trên ghi rõ 4 field
        # (kể cả new_product_category) bắt buộc cùng nhau khi tạo sản phẩm mới, nhưng code v1 chỉ
        # khai báo VÀ kiểm tra 3 field, thiếu new_product_category — Product.objects.create() sau
        # đó tạo sản phẩm với category rỗng, khiến PurchaseRequestItem.clean() (Task 2.6) không có
        # gì để fallback budget_category cho các dòng PR sau này dùng sản phẩm mới này.
        new_fields_filled = all([
            cleaned_data.get('new_product_code'), cleaned_data.get('new_product_name'),
            cleaned_data.get('new_product_uom'), cleaned_data.get('new_product_category'),
        ])
        if existing and new_fields_filled:
            raise forms.ValidationError('Chỉ chọn 1 trong 2: sản phẩm có sẵn HOẶC tạo sản phẩm mới.')
        if not existing and not new_fields_filled:
            raise forms.ValidationError(
                'Phải chọn 1 sản phẩm có sẵn, hoặc điền đủ Mã/Tên/Đơn vị tính/Danh mục để tạo mới.')
        new_code = cleaned_data.get('new_product_code')
        if not existing and new_code and Product.objects.filter(product_code=new_code).exists():
            self.add_error('new_product_code', 'Mã sản phẩm đã tồn tại.')
        return cleaned_data
```
  **Sửa theo review lần 3 (blocker #3)**: FSD mục 1 dòng "PUR Staff" ghi rõ actor là `role` có
  `update` trên `pr` **và** `department=PURCHASING`; mục 1 dòng cuối ghi rõ "map-non-catalog đi
  theo `pr` + `catalog`" (2 permission, không phải chỉ `pr`); mục 5 ghi "chỉ PUR Staff/Manager thấy
  nút" — "Manager" ở đây là PUR Manager (`is_department_manager('PURCHASING')`), không phải bất kỳ
  Manager nào. Code v2 chỉ kiểm `can('update','pr')` + `role in (PURCHASING, MANAGER, ADMIN)` — bỏ
  sót cả điều kiện `can_view_menu('catalog')` lẫn ràng buộc phòng ban cho `MANAGER` (một Manager
  phòng khác, ví dụ QC, vẫn map được). Dùng helper `can_map_non_catalog()` **đã tạo và test ở Task
  3.3** (không định nghĩa lại tại đây), cùng convention actor-gate của dự án — xem CLAUDE.md mục
  "can_view_menu(key) alone only gates 'view'...".
  Thêm view (`purchasing/views.py`, sau `pr_item_cancel_open_qty`):
```python
@login_required
def pr_item_map_product(request, pk):
    item = get_object_or_404(PurchaseRequestItem.objects.select_related('purchase_request'), pk=pk)
    if not can_map_non_catalog(request.user):
        raise PermissionDenied('Không có quyền map sản phẩm cho dòng yêu cầu mua hàng này.')
    if not item.is_non_catalog:
        messages.error(request, 'Dòng này đã có sản phẩm.')
        return redirect('purchasing:pr_detail', pk=item.purchase_request_id)

    form = PrItemMapProductForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                original_non_catalog_name = item.non_catalog_name
                product = form.cleaned_data.get('existing_product')
                if not product:
                    product = Product.objects.create(
                        product_code=form.cleaned_data['new_product_code'],
                        name=form.cleaned_data['new_product_name'],
                        uom=form.cleaned_data['new_product_uom'],
                        category=form.cleaned_data['new_product_category'],
                    )
                map_non_catalog_item(item, product, actor=request.user, ip_address=client_ip(request))
            messages.success(
                request,
                f'Đã map dòng "{original_non_catalog_name}" sang sản phẩm "{product.product_code}".')
            return redirect('purchasing:pr_detail', pk=item.purchase_request_id)
        except IntegrityError:
            # Chặn race: mã chưa tồn tại lúc form validate nhưng transaction khác vừa tạo trước
            # INSERT của transaction này. Exception thoát khỏi atomic nên transaction đã rollback
            # sạch trước khi render lại form.
            form.add_error('new_product_code', 'Mã sản phẩm đã tồn tại.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
    return render(request, 'purchasing/pr_item_map_product.html', {'item': item, 'form': form})
```
  Import thêm `IntegrityError` từ `django.db`. Việc lưu `original_non_catalog_name` trước khi gọi
  service là bắt buộc vì `map_non_catalog_item()` xoá các field non-catalog trong cùng transaction;
  đọc `item.non_catalog_name` sau lời gọi sẽ chỉ còn chuỗi rỗng trong success message.
  Template `pr_item_map_product.html` (form đơn giản, mirror bố cục `pr_form.html` — 1 card,
  `{{ form.as_p }}` hoặc render tay từng field theo pattern Bootstrap đã dùng khắp dự án, nút Lưu +
  Huỷ quay lại `pr_detail`).
- [ ] **Bước 4**: Thêm route:
```python
path('pr-item/<int:pk>/map-product/', views.pr_item_map_product, name='pr_item_map_product'),
```
- [ ] **Bước 5: Chạy test, xác nhận PASS**
- [ ] **Bước 6: Commit**
```bash
git add purchasing/forms.py purchasing/views.py purchasing/urls.py purchasing/templates/purchasing/pr_item_map_product.html purchasing/tests.py
git commit -m "feat(pur): add pr_item_map_product view (PUR-PR-06)"
```

## Task 3.6: `po_build_from_pr_lines` — thay `po_create?from_pr=<pk>`

**File:**
- Sửa: `purchasing/views.py` (view mới)
- Sửa: `purchasing/urls.py`
- Sửa: `purchasing/templates/purchasing/pr_detail.html` (đổi link `can_create_po` sang view mới)
- Tạo: `purchasing/templates/purchasing/po_build_from_pr_lines.html`
- Test: `purchasing/tests.py` (class mới `PoBuildFromPrLinesViewTest`)

**Giao diện:**
- Sử dụng: `build_po_from_allocations` (Task 2.5).

**Quyết định cụ thể hoá** (FSD mô tả "bước 1 chọn supplier; bước 2 liệt kê dòng PR" như 2 bước UX,
không pin cứng đây phải là 2 request/2 view riêng): hiện thực thành **1 view, 1 trang** — GET render
đồng thời cả ô chọn supplier lẫn danh sách dòng PR đủ điều kiện (đã có sẵn toàn bộ dữ liệu cần thiết
ngay từ đầu, không cần round-trip riêng để "biết supplier rồi mới hiện danh sách dòng") — mirror
đúng tiền lệ `po_create?from_pr=` hiện tại (1 view, prefill rồi submit 1 lần), tránh phát sinh state
tạm giữa 2 request (session/hidden field) không cần thiết. `qty_open` là **property** (không phải
DB field) nên lọc `qty_open > 0` phải làm ở Python sau khi query, không viết được thành
`.filter(qty_open__gt=0)` ở tầng ORM.

- [ ] **Bước 1: Viết test đang FAIL (AC #5, mục 5)**
```python
class PoBuildFromPrLinesViewTest(TestCase):
    def setUp(self):
        self.pur_staff = User.objects.create_user(
            username='pur1', password='pur-pass-123', role=User.Role.PURCHASING, department=User.Department.PURCHASING)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.pr = PurchaseRequest.objects.create(
            requested_by=self.pur_staff, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED)
        self.pr_item_a = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        self.pr_item_b = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=self.product, qty_requested=5, qty_approved=5,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')

    def test_post_creates_po_merging_two_lines_same_product(self):
        self.client.login(username='pur1', password='pur-pass-123')
        response = self.client.post(reverse('purchasing:po_build_from_pr_lines'), {
            'supplier': self.supplier.pk,
            'selected_items': [str(self.pr_item_a.pk), str(self.pr_item_b.pk)],
            f'qty_{self.pr_item_a.pk}': '10',
            f'qty_{self.pr_item_b.pk}': '5',
            f'unit_price_{self.product.pk}': '1200',
        })
        self.assertEqual(response.status_code, 302)
        po = PurchaseOrder.objects.get(source=PurchaseOrder.Source.FROM_PR)
        self.assertEqual(po.items.count(), 1)
        self.assertEqual(po.items.first().qty_ordered, 15)

    def test_post_no_selection_shows_error_no_po_created(self):
        self.client.login(username='pur1', password='pur-pass-123')
        self.client.post(reverse('purchasing:po_build_from_pr_lines'), {'supplier': self.supplier.pk})
        self.assertEqual(PurchaseOrder.objects.count(), 0)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `NoReverseMatch`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm vào `purchasing/views.py` (đầu file thêm
  `from decimal import Decimal, InvalidOperation`; view đặt sau `po_create`):
```python
@po_permission_required('create')
def po_build_from_pr_lines(request):
    """PUR-PR-05 — thay po_create?from_pr=<pk>: chọn nhiều dòng PR (từ 1 hoặc nhiều PR) để gộp/
    tách vào 1 PO mới, qua build_po_from_allocations(). ``qty_open`` là property nên lọc bằng
    Python sau khi query, không lọc được ở tầng ORM.
    """
    from_pr_id = request.GET.get('from_pr') or request.POST.get('from_pr')
    eligible_items = [
        item for item in PurchaseRequestItem.objects.filter(
            product__isnull=False, purchase_request__status=PurchaseRequest.Status.APPROVED,
        ).select_related('product', 'purchase_request').order_by('purchase_request_id', 'pk')
        if item.qty_open > 0
    ]
    suppliers = Supplier.objects.filter(status=Supplier.Status.ACTIVE)
    prefill_supplier_id = None
    if from_pr_id:
        first_item = next(
            (item for item in eligible_items if str(item.purchase_request_id) == str(from_pr_id)), None)
        if first_item and first_item.product.preferred_supplier_id:
            prefill_supplier_id = first_item.product.preferred_supplier_id

    if request.method == 'POST':
        supplier = get_object_or_404(Supplier, pk=request.POST.get('supplier'))
        selected_ids = set(request.POST.getlist('selected_items'))
        allocation_requests = []
        unit_price_by_product = {}
        error = None
        for item in eligible_items:
            if str(item.pk) not in selected_ids:
                continue
            try:
                qty = int(request.POST.get(f'qty_{item.pk}', ''))
            except ValueError:
                error = f'Số lượng không hợp lệ cho dòng "{item}".'
                break
            allocation_requests.append((item, qty))
            if item.product_id not in unit_price_by_product:
                try:
                    unit_price_by_product[item.product_id] = Decimal(
                        request.POST.get(f'unit_price_{item.product_id}', ''))
                except (InvalidOperation, TypeError):
                    error = f'Đơn giá không hợp lệ cho sản phẩm "{item.product.product_code}".'
                    break
        if error:
            messages.error(request, error)
        else:
            try:
                po = build_po_from_allocations(
                    supplier, allocation_requests, unit_price_by_product, actor=request.user,
                    ip_address=client_ip(request))
                messages.success(request, f'Đã tạo PO "{po.po_no}".')
                return redirect('purchasing:po_detail', pk=po.pk)
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))

    return render(request, 'purchasing/po_build_from_pr_lines.html', {
        'eligible_items': eligible_items, 'suppliers': suppliers,
        'prefill_supplier_id': prefill_supplier_id, 'from_pr_id': from_pr_id,
    })
```
  Thêm import ở đầu `purchasing/views.py`: mở rộng `from .models import PurchaseOrder,
  PurchaseRequest` thành thêm `PurchaseRequestItem`; mở rộng
  `from .services import (...)` thêm `build_po_from_allocations`.
- [ ] **Bước 4**: Thêm route (`purchasing/urls.py`):
```python
path('po/build-from-pr-lines/', views.po_build_from_pr_lines, name='po_build_from_pr_lines'),
```
  Template `po_build_from_pr_lines.html`: 1 form — dropdown `supplier` (prefill
  `prefill_supplier_id`), bảng checkbox mỗi dòng `eligible_items` (`checked` mặc định nếu
  `item.purchase_request_id == from_pr_id`), input `qty_<pk>` (giá trị mặc định `item.qty_open`),
  input `unit_price_<product_id>` (1 ô mỗi product xuất hiện — dùng `{% ifchanged
  item.product_id %}` trong vòng lặp `{% for item in eligible_items %}` đã `order_by
  ('purchase_request_id', 'pk')`... **lưu ý**: `{% ifchanged %}` chỉ phát hiện đúng khi list đã sort
  theo `product_id` liền kề — danh sách hiện sort theo `purchase_request_id` nên 2 dòng cùng
  product ở 2 PR khác nhau có thể KHÔNG liền kề; giải pháp đơn giản hơn: JS ẩn input `unit_price`
  trùng lặp theo `product_id` bằng `data-product-id`, hoặc server-side gom `unit_price` input
  thành 1 danh sách riêng (`{% regroup eligible_items by product as product_groups %}`) render
  TÁCH RIÊNG khỏi bảng chọn dòng — chọn cách `{% regroup %}` (không cần JS, đơn giản hơn).
- [ ] **Bước 5**: sửa `pr_detail.html` dòng 44-47 (`can_create_po` link) từ
  `{% url 'purchasing:po_create' %}?from_pr={{ obj.pk }}` sang
  `{% url 'purchasing:po_build_from_pr_lines' %}?from_pr={{ obj.pk }}`.
- [ ] **Bước 6: Chạy test, xác nhận PASS**
- [ ] **Bước 7: Viết test đang FAIL (Review lần 3, mục phụ #1 — chặn supplier không ACTIVE ở
  tầng view, đối xứng với Bước 7-11 của Task 2.5 ở tầng service)**
```python
    def test_post_inactive_supplier_rejected_no_po_created(self):
        from django.contrib.messages import get_messages
        self.supplier.status = Supplier.Status.INACTIVE
        self.supplier.save(update_fields=['status'])
        self.client.login(username='pur1', password='pur-pass-123')
        response = self.client.post(reverse('purchasing:po_build_from_pr_lines'), {
            'supplier': self.supplier.pk,
            'selected_items': [str(self.pr_item_a.pk)],
            f'qty_{self.pr_item_a.pk}': '10',
            f'unit_price_{self.product.pk}': '1200',
        })
        self.assertEqual(PurchaseOrder.objects.count(), 0)
        messages_list = list(get_messages(response.wsgi_request))
        self.assertEqual(
            str(messages_list[-1]),
            'Nhà cung cấp đã ngừng giao dịch hoặc bị tạm khóa, vui lòng chọn nhà cung cấp khác.')
```
- [ ] **Bước 8: Chạy test, xác nhận FAIL** — supplier `INACTIVE` vẫn tạo PO bình thường (view
  chưa kiểm tra `status`, chỉ có `get_object_or_404` tra theo `pk`).
- [ ] **Bước 9: Sửa code tối thiểu để PASS** — thay đoạn code ở Bước 3 (từ dòng `error = None` tới
  hết vòng lặp `for item in eligible_items:`) bằng:
```python
        error = None
        # `suppliers` ở GET (trên) đã lọc status=ACTIVE cho dropdown, nhưng đây là field POST thô
        # (không phải ModelForm có queryset ràng buộc) nên vẫn phải tự re-validate ở POST — cùng
        # convention "form lọc, code xử lý phải tự kiểm tra lại" (chặn cả tampering lẫn trường hợp
        # supplier bị chuyển INACTIVE/SUSPENDED giữa lúc GET và lúc submit).
        if supplier.status != Supplier.Status.ACTIVE:
            error = 'Nhà cung cấp đã ngừng giao dịch hoặc bị tạm khóa, vui lòng chọn nhà cung cấp khác.'
        else:
            for item in eligible_items:
                if str(item.pk) not in selected_ids:
                    continue
                try:
                    qty = int(request.POST.get(f'qty_{item.pk}', ''))
                except ValueError:
                    error = f'Số lượng không hợp lệ cho dòng "{item}".'
                    break
                allocation_requests.append((item, qty))
                if item.product_id not in unit_price_by_product:
                    try:
                        unit_price_by_product[item.product_id] = Decimal(
                            request.POST.get(f'unit_price_{item.product_id}', ''))
                    except (InvalidOperation, TypeError):
                        error = f'Đơn giá không hợp lệ cho sản phẩm "{item.product.product_code}".'
                        break
```
- [ ] **Bước 10: Chạy lại toàn bộ test của class, xác nhận PASS**
- [ ] **Bước 11: Test thủ công trên trình duyệt** — từ `pr_detail`, bấm "Tạo PO từ yêu cầu này",
  xác nhận dòng của PR đó được pre-check, chọn thêm dòng từ PR khác cùng lúc, submit tạo PO đúng.
- [ ] **Bước 12: Commit**
```bash
git add purchasing/views.py purchasing/urls.py purchasing/templates/purchasing/po_build_from_pr_lines.html purchasing/templates/purchasing/pr_detail.html purchasing/tests.py
git commit -m "feat(pur): add po_build_from_pr_lines view, replaces po_create?from_pr= (PUR-PR-05)"
```

## Task 3.7: `po_update` — rewrite cho PO nguồn `FROM_PR` (khoá field, chặn `pk` trùng, phát hiện tampering)

Task quan trọng và rủi ro nhất Phase 3 — nhắc lại: PO nguồn `MANUAL` **không đổi hành vi**, toàn bộ
guard dưới đây chỉ áp dụng khi `obj.source == FROM_PR`.

**File:**
- Sửa: `purchasing/forms.py` (form mới `PurchaseOrderItemFromPrForm` + formset factory
  `PurchaseOrderItemFromPrFormSet`)
- Sửa: `purchasing/views.py` (viết lại `po_update`, thêm helper `_raw_disabled_field_tampered`)
- Test: `purchasing/tests.py` (class mới `PoUpdateFromPrGuardTest`)

**Giao diện:**
- Sử dụng: `delete_draft_po_item_with_allocations` (Task 2.3).
- Cung cấp: `_raw_disabled_field_tampered(post_data, form, field_name, db_value) -> bool` (helper
  dùng chung — xem Ràng buộc chung, lưu ý kỹ thuật #2).

- [ ] **Bước 1: Viết test đang FAIL** (AC #8/#9/#10/#34 — tamper `qty_ordered`, POST thêm form giả,
  duplicate `pk`, sửa `unit_price` tự do vẫn được)
```python
class PoUpdateFromPrGuardTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        self.po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('1000'))
        self.client.login(username='admin1', password='admin-pass-123')

    def _post_data(self, **item_overrides):
        data = {
            'supplier': self.supplier.pk,
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1', 'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-id': str(self.po_item.pk),
            'items-0-product': str(self.product.pk),
            'items-0-qty_ordered': '10',
            'items-0-unit_price': '1500',
        }
        data.update(item_overrides)
        return data

    def test_TC_PUR_PR_05_005_tamper_qty_ordered_rejected(self):
        response = self.client.post(
            reverse('purchasing:po_update', args=[self.po.pk]), self._post_data(**{'items-0-qty_ordered': '5'}))
        self.assertEqual(response.status_code, 302)
        self.po_item.refresh_from_db()
        self.assertEqual(self.po_item.qty_ordered, 10)  # không đổi

    def test_TC_PUR_PR_05_017_unit_price_editable_freely(self):
        self.client.post(reverse('purchasing:po_update', args=[self.po.pk]), self._post_data())
        self.po_item.refresh_from_db()
        self.assertEqual(self.po_item.unit_price, Decimal('1500'))

    def test_TC_PUR_PR_05_016_extra_fake_form_rejected(self):
        data = self._post_data()
        data['items-TOTAL_FORMS'] = '2'
        data['items-1-id'] = ''
        data['items-1-product'] = str(self.product.pk)
        data['items-1-qty_ordered'] = '99'
        data['items-1-unit_price'] = '100'
        response = self.client.post(reverse('purchasing:po_update', args=[self.po.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PurchaseOrderItem.objects.filter(purchase_order=self.po).count(), 1)

    def test_TC_PUR_PR_05_023_duplicate_pk_two_forms_rejected(self):
        from django.contrib.messages import get_messages
        data = self._post_data()
        data['items-TOTAL_FORMS'] = '2'
        # Review lần 3, blocker #4: phải nâng INITIAL_FORMS lên '2' — nếu để nguyên '1' (giá trị kế
        # thừa từ _post_data()), Django coi form index 1 là form "extra" (i >= initial_form_count()),
        # nên KHÔNG gán instance theo pk submit (BaseModelFormSet._construct_form chỉ tra pk khi
        # i < initial_form_count()); form 1 sẽ có instance.pk=None và test PASS nhầm qua nhánh lỗi
        # "dòng PO-item lạ" thay vì nhánh duplicate-pk (`submitted_pks`) thật sự cần kiểm ở đây.
        data['items-INITIAL_FORMS'] = '2'
        data['items-1-id'] = str(self.po_item.pk)
        data['items-1-product'] = str(self.product.pk)
        data['items-1-qty_ordered'] = '10'
        data['items-1-unit_price'] = '200'
        response = self.client.post(reverse('purchasing:po_update', args=[self.po.pk]), data)
        self.assertEqual(response.status_code, 302)
        messages_list = list(get_messages(response.wsgi_request))
        self.assertEqual(
            str(messages_list[-1]), 'Một dòng PO không được xuất hiện nhiều lần trong formset.')
        self.po_item.refresh_from_db()
        self.assertEqual(self.po_item.unit_price, Decimal('1000'))  # giữ nguyên, không phải 1500 hay 200

    def test_TC_PUR_PR_05_015_legacy_line_zero_allocation_still_locked(self):
        # po_item hiện tại chính là dòng legacy (qty_ordered=10, 0 allocation) — field vẫn disabled.
        response = self.client.post(
            reverse('purchasing:po_update', args=[self.po.pk]), self._post_data(**{'items-0-qty_ordered': '3'}))
        self.po_item.refresh_from_db()
        self.assertEqual(self.po_item.qty_ordered, 10)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `po_update` hiện tại dùng `PurchaseOrderItemFormSet`
  (không `disabled`), mọi test tamper đều PASS sai (giá trị bị ghi đè) hoặc tạo dòng mới thành công.
- [ ] **Bước 3: Viết code tối thiểu để PASS**. Thêm form/formset (`purchasing/forms.py`, sau
  `PurchaseOrderItemFormSet` hiện có):
```python
class PurchaseOrderItemFromPrForm(forms.ModelForm):
    """PO nguồn FROM_PR: product/qty_ordered chỉ đổi được qua create_allocation()/
    release_allocation() (mục 4 điểm 4) — disabled=True ở tầng Form (không chỉ HTML readonly)
    khiến Django luôn dùng giá trị initial trong cleaned_data, bỏ qua hoàn toàn giá trị POST.
    """
    product = forms.ModelChoiceField(queryset=Product.objects.all(), disabled=True, required=False)
    qty_ordered = forms.IntegerField(disabled=True, required=False)

    class Meta:
        model = PurchaseOrderItem
        fields = ['product', 'qty_ordered', 'unit_price']


PurchaseOrderItemFromPrFormSet = inlineformset_factory(
    PurchaseOrder, PurchaseOrderItem, form=PurchaseOrderItemFromPrForm,
    extra=0, can_delete=True,
)
```
  Thêm helper + viết lại `po_update` (`purchasing/views.py`):
```python
def _raw_disabled_field_tampered(post_data, form, field_name, db_value):
    """Lưu ý kỹ thuật #2 (docs/pur/03_stage2_implementation_plan.md, Ràng buộc chung): field
    disabled=True bị Django bỏ qua trong cleaned_data — phải tự so giá trị POST thô với DB.
    """
    key = form.add_prefix(field_name)
    if key not in post_data:
        return False
    raw_value = post_data[key].strip()
    if field_name == 'qty_ordered':
        try:
            return int(raw_value) != db_value
        except ValueError:
            return True
    return str(db_value) != raw_value


@po_permission_required('update')
def po_update(request, pk):
    obj = get_object_or_404(PurchaseOrder, pk=pk)
    if obj.status != PurchaseOrder.Status.DRAFT:
        messages.error(request, f'Không thể sửa PO "{obj.po_no}" khi đã qua state DRAFT.')
        return redirect('purchasing:po_detail', pk=obj.pk)

    formset_class = (
        PurchaseOrderItemFromPrFormSet if obj.source == PurchaseOrder.Source.FROM_PR
        else PurchaseOrderItemFormSet
    )

    if request.method == 'POST':
        with transaction.atomic():
            locked_obj = get_object_or_404(PurchaseOrder.objects.select_for_update(), pk=pk)
            if locked_obj.status != PurchaseOrder.Status.DRAFT:
                messages.error(request, f'Không thể sửa PO "{locked_obj.po_no}" khi đã qua state DRAFT.')
                return redirect('purchasing:po_detail', pk=pk)
            locked_items_by_pk = {
                item.pk: item for item in
                PurchaseOrderItem.objects.select_for_update().filter(purchase_order=locked_obj).order_by('pk')
            }
            form = PurchaseOrderForm(request.POST, instance=locked_obj)
            formset = formset_class(
                request.POST, instance=locked_obj, prefix='items',
                queryset=PurchaseOrderItem.objects.filter(pk__in=locked_items_by_pk.keys()),
            )
            if form.is_valid() and formset.is_valid():
                # is_valid() == True từ đây trở xuống — is_valid() == False rơi thẳng xuống
                # render() cuối hàm, KHÔNG chạy guard nào (review lần 5 điểm 2).
                if locked_obj.source == PurchaseOrder.Source.FROM_PR:
                    # Lỗi đã sửa so với v1 (review implementation plan): pass đầu tiên PHẢI quét
                    # TOÀN BỘ form đã submit — kể cả form bị đánh dấu xoá (`formset.deleted_forms`)
                    # — TRƯỚC khi tách nhánh xoá/sửa. v1 chỉ kiểm tra pk trùng trên các form KHÔNG
                    # bị đánh dấu xoá, nên submit cùng 1 pk ở 2 form (1 giữ lại, 1 đánh dấu xoá) lọt
                    # qua check — nhánh xoá chạy trước xoá thật po_item đó, rồi nhánh sửa lại gọi
                    # `.save(update_fields=['unit_price'])` trên đúng pk vừa xoá: UPDATE khớp 0 dòng,
                    # âm thầm không lỗi, không đúng ý người dùng dù không mất dữ liệu người khác.
                    submitted_pks = []
                    for item_form in formset.forms:
                        item_pk = item_form.instance.pk
                        if item_pk is None or item_pk not in locked_items_by_pk:
                            messages.error(request, 'Dữ liệu gửi lên không hợp lệ (dòng PO-item lạ).')
                            return redirect('purchasing:po_update', pk=pk)
                        if item_pk in submitted_pks:
                            messages.error(request, 'Một dòng PO không được xuất hiện nhiều lần trong formset.')
                            return redirect('purchasing:po_update', pk=pk)
                        submitted_pks.append(item_pk)

                    # Pass thứ 2 — CHỈ form không bị đánh dấu xoá — kiểm tra field khoá bị sửa qua raw
                    # POST (mọi pk ở đây đã được xác nhận hợp lệ + không trùng ở pass đầu, không cần
                    # kiểm tra lại `item_pk in locked_items_by_pk`).
                    for item_form in formset.forms:
                        if item_form in formset.deleted_forms:
                            continue
                        db_item = locked_items_by_pk[item_form.instance.pk]
                        if (_raw_disabled_field_tampered(request.POST, item_form, 'product', db_item.product_id)
                                or _raw_disabled_field_tampered(
                                    request.POST, item_form, 'qty_ordered', db_item.qty_ordered)):
                            messages.error(request, f'Không được sửa sản phẩm/số lượng của dòng "{db_item}".')
                            return redirect('purchasing:po_update', pk=pk)

                    for item_form in formset.deleted_forms:
                        if item_form.instance.pk:
                            delete_draft_po_item_with_allocations(
                                item_form.instance, actor=request.user, ip_address=client_ip(request))
                    obj = form.save()
                    for item_form in formset.forms:
                        if item_form in formset.deleted_forms or item_form.instance.pk is None:
                            continue
                        item_form.instance.save(update_fields=['unit_price'])
                else:
                    obj = form.save()
                    formset.save()

                log_action(
                    request.user, AuditLog.Action.UPDATE, target=obj,
                    description=f'Cập nhật PO {obj.po_no}', ip_address=client_ip(request),
                )
                messages.success(request, f'Đã cập nhật PO "{obj.po_no}".')
                return redirect('purchasing:po_detail', pk=obj.pk)
    else:
        form = PurchaseOrderForm(instance=obj)
        formset = formset_class(instance=obj, prefix='items')
    return render(request, 'purchasing/po_form.html', {'form': form, 'formset': formset, 'mode': 'update', 'obj': obj})
```
  (`item_form.instance.save(update_fields=['unit_price'])` — giới hạn UPDATE thật xuống đúng 1 cột
  là lớp phòng vệ THỨ 3, cộng thêm `disabled=True` (lớp 1, dữ liệu) và so raw POST (lớp 2, phát
  hiện) — dù `disabled=True` đã đủ để `cleaned_data['product']/['qty_ordered']` giữ nguyên giá trị
  cũ, giới hạn `update_fields` đảm bảo câu SQL UPDATE vật lý không đụng 2 cột đó bất kể chuyện gì
  xảy ra ở tầng Python phía trên.)
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5**: bổ sung các test còn lại của nhóm này theo checklist (mỗi dòng 1 test, dựng theo
  đúng kịch bản đã ghi ở FSD mục 10/11 — không sửa code thêm nếu đã PASS ngay từ Bước 3):
  `TC-PUR-PR-05-016` (biến thể `pk` thuộc PO khác — dựng PO thứ 2, POST `items-0-id` trỏ
  `po_item` của PO đó), test guard `product` tamper song song `qty_ordered` (2 trường hợp riêng), và
  test riêng cho lỗi đã sửa ở Bước 3 (pk trùng giữa 1 form giữ lại và 1 form đánh dấu xoá):
```python
    def test_TC_PUR_PR_05_017_duplicate_pk_across_kept_and_deleted_form_rejected(self):
        from django.contrib.messages import get_messages
        payload = {
            'po_no': self.po.po_no, 'supplier': self.supplier.pk,
            # Review lần 3, blocker #4: INITIAL_FORMS phải là '2', không phải '1' — cùng lý do đã
            # sửa ở test_TC_PUR_PR_05_023 phía trên. Để '1' thì form index 1 (form đánh dấu DELETE)
            # là form "extra": Django không gán instance theo pk submit nên instance.pk=None, và
            # request rơi vào nhánh lỗi "dòng PO-item lạ" ở PASS ĐẦU TIÊN — response vẫn là 302 và
            # po_item vẫn giữ nguyên, nên 2 assert bên dưới PASS "nhầm", không thực sự chứng minh
            # guard duplicate-pk-giữa-form-giữ-và-form-xoá (lý do bug được sửa ở Bước 3) hoạt động.
            'items-TOTAL_FORMS': '2', 'items-INITIAL_FORMS': '2', 'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-id': str(self.po_item.pk), 'items-0-product': str(self.product.pk),
            'items-0-qty_ordered': str(self.po_item.qty_ordered), 'items-0-unit_price': '1000',
            # Form thứ 2 trỏ ĐÚNG cùng pk với form 0, nhưng đánh dấu DELETE — trước khi sửa, guard
            # trùng pk chỉ soi form KHÔNG đánh dấu xoá nên bỏ lọt trường hợp này.
            'items-1-id': str(self.po_item.pk), 'items-1-product': str(self.product.pk),
            'items-1-qty_ordered': str(self.po_item.qty_ordered), 'items-1-unit_price': '1000',
            'items-1-DELETE': 'on',
        }
        response = self.client.post(reverse('purchasing:po_update', args=[self.po.pk]), payload)
        self.assertEqual(response.status_code, 302)
        messages_list = list(get_messages(response.wsgi_request))
        self.assertEqual(
            str(messages_list[-1]), 'Một dòng PO không được xuất hiện nhiều lần trong formset.')
        self.po_item.refresh_from_db()
        self.assertTrue(PurchaseOrderItem.objects.filter(pk=self.po_item.pk).exists())  # KHÔNG bị xoá
        self.assertEqual(self.po_item.unit_price, Decimal('1000'))  # KHÔNG bị "sửa" trên dòng đã lẽ ra bị xoá
```
- [ ] **Bước 6: Test thủ công trên trình duyệt** — mở `po_update` của 1 PO `FROM_PR` `DRAFT`,
  xác nhận field `product`/`qty_ordered` hiển thị disabled (không sửa được qua UI thường), sửa
  `unit_price` lưu được bình thường, xoá 1 dòng hoạt động đúng qua
  `delete_draft_po_item_with_allocations`.
- [ ] **Bước 7: Commit**
```bash
git add purchasing/forms.py purchasing/views.py purchasing/tests.py
git commit -m "feat(pur): rewrite po_update for FROM_PR - disabled fields, duplicate-pk guard, raw POST tamper detection"
```

## Task 3.8: `ExchangeRate` CRUD (Admin-only) + `MENU_ITEMS['exchange_rate']` (mặc định KHÔNG cấp mọi role)

**File:**
- Sửa: `accounts/permissions.py` (`MENU_ITEMS`, `DEFAULT_RESTRICTED_MENU_ITEMS`, `codenames_for_role`)
- Sửa: `accounts/context_processors.py` (`sidebar_permissions` — thêm flag `can_view_menu_exchange_rate`)
- Tạo: `accounts/migrations/0017_exchange_rate_menu_permission.py` (permission mới + backfill cho
  Admin hiện có — xem lý do bắt buộc tách migration riêng ở Bước 6)
- Sửa: `purchasing/forms.py` (form mới `ExchangeRateForm`)
- Sửa: `purchasing/views.py` (4 view mới)
- Sửa: `purchasing/urls.py`
- Sửa: `accounts/templates/base.html` (hoặc file sidebar riêng — thêm mục "Tỷ giá ngoại tệ")
- Tạo: `purchasing/templates/purchasing/exchange_rate_list.html`,
  `exchange_rate_form.html`, `exchange_rate_confirm_delete.html`
- Test: `purchasing/tests.py` (class mới `ExchangeRateViewTest`) — test cho `codenames_for_role`
  gọi thẳng `accounts.permissions.codenames_for_role`, không cần file `accounts/tests.py` riêng.

**Giao diện:**
- Cung cấp: `ExchangeRateForm`, 4 view `exchange_rate_list/_create/_update/_delete`,
  `can_view_menu_exchange_rate` (context variable, mọi template).

**Quyết định cụ thể hoá** (FSD mục 1: "không dùng default-granted-mọi-role như các menu-only khác
vì đây là ngoại lệ cần khoá chặt từ đầu" — không pin cứng cơ chế code): thêm tập hằng
`DEFAULT_RESTRICTED_MENU_ITEMS = {'exchange_rate'}` trong `accounts/permissions.py`, loại các key
trong tập này khỏi phần cấp mặc định của `codenames_for_role()`, rồi cấp lại **chỉ cho role
`ADMIN`**. `all_menu_codenames()` (dùng để sinh `Meta.permissions` — danh sách toàn bộ codename khả
dĩ) **giữ nguyên**, vẫn liệt kê `can_view_menu_exchange_rate` (permission phải tồn tại trong DB dù
mặc định không role nào khác được cấp).

- [ ] **Bước 1: Viết test đang FAIL (AC #22, TC-PUR-XR-001/002/003)**
```python
class ExchangeRateViewTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.staff = User.objects.create_user(username='staff1', password='staff-pass-123', role=User.Role.STAFF)

    def test_TC_PUR_XR_001_staff_forbidden_create(self):
        self.client.login(username='staff1', password='staff-pass-123')
        response = self.client.post(reverse('purchasing:exchange_rate_create'), {
            'currency': 'USD', 'rate_date': timezone.localdate().isoformat(), 'rate_to_vnd': '25000'})
        self.assertEqual(response.status_code, 403)

    def test_TC_PUR_XR_002_duplicate_currency_rate_date_rejected(self):
        self.client.login(username='admin1', password='admin-pass-123')
        payload = {'currency': 'USD', 'rate_date': timezone.localdate().isoformat(), 'rate_to_vnd': '25000'}
        self.client.post(reverse('purchasing:exchange_rate_create'), payload)
        response = self.client.post(reverse('purchasing:exchange_rate_create'), payload)
        self.assertEqual(ExchangeRate.objects.count(), 1)
        self.assertEqual(response.status_code, 200)  # render lại kèm lỗi form, không redirect

    def test_TC_PUR_XR_003_staff_forbidden_update_delete_admin_allowed(self):
        rate = ExchangeRate.objects.create(
            currency='USD', rate_date=timezone.localdate(), rate_to_vnd=Decimal('25000'), created_by=self.admin_user)
        self.client.login(username='staff1', password='staff-pass-123')
        self.assertEqual(self.client.post(reverse('purchasing:exchange_rate_update', args=[rate.pk]), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse('purchasing:exchange_rate_delete', args=[rate.pk])).status_code, 403)
        self.client.login(username='admin1', password='admin-pass-123')
        response = self.client.post(reverse('purchasing:exchange_rate_delete', args=[rate.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ExchangeRate.objects.filter(pk=rate.pk).exists())

    def test_default_role_does_not_get_exchange_rate_menu_but_admin_does(self):
        from accounts.permissions import codenames_for_role
        self.assertNotIn('can_view_menu_exchange_rate', codenames_for_role('STAFF'))
        self.assertIn('can_view_menu_exchange_rate', codenames_for_role('ADMIN'))

    def test_TC_PUR_XR_005_admin_with_menu_permission_revoked_gets_403(self):
        """Nghiêm trọng (review implementation plan): Admin có role đúng nhưng bị thu hồi RIÊNG
        quyền `can_view_menu_exchange_rate` qua trang "Phân quyền chi tiết" vẫn phải bị chặn — view
        không được chỉ kiểm `role`/`is_superuser` mà bỏ qua `can_view_menu`, giống pattern
        `can_transfer_inventory` AND `can_view_menu('inventory')` đã áp dụng ở module `inventory`."""
        from django.contrib.auth.models import Permission
        perm = Permission.objects.get(content_type__app_label='accounts', codename='can_view_menu_exchange_rate')
        self.admin_user.user_permissions.remove(perm)
        self.client.login(username='admin1', password='admin-pass-123')
        response = self.client.post(reverse('purchasing:exchange_rate_create'), {
            'currency': 'USD', 'rate_date': timezone.localdate().isoformat(), 'rate_to_vnd': '25000'})
        self.assertEqual(response.status_code, 403)

    def test_TC_PUR_XR_006_form_rejects_vnd_currency(self):
        self.client.login(username='admin1', password='admin-pass-123')
        response = self.client.post(reverse('purchasing:exchange_rate_create'), {
            'currency': 'VND', 'rate_date': timezone.localdate().isoformat(), 'rate_to_vnd': '1'})
        self.assertEqual(response.status_code, 200)  # render lại kèm lỗi form, không redirect
        self.assertEqual(ExchangeRate.objects.count(), 0)
```
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `NoReverseMatch`.
- [ ] **Bước 3: Sửa permission/form/view — viết code tối thiểu để PASS.** Sửa
  `accounts/permissions.py`:
```python
MENU_ITEMS = {
    'warehouse': 'Kho hàng',
    'catalog': 'Sản phẩm (Danh mục)',
    'partners': 'Nhà cung cấp',
    'inventory': 'Tồn kho',
    'handoff': 'Phiếu chờ nhận hàng',
    'user_mgmt': 'Quản lý user',
    'audit_log': 'Nhật ký hành động',
    'exchange_rate': 'Tỷ giá ngoại tệ',
}

# Ngoại lệ: menu KHÔNG mặc định cấp cho mọi role (khác toàn bộ MENU_ITEMS còn lại) — dữ liệu tài
# chính nhạy cảm dùng chung toàn hệ thống, chỉ Admin thấy mặc định (mục 1 FSD Stage 2).
DEFAULT_RESTRICTED_MENU_ITEMS = {'exchange_rate'}


def codenames_for_role(role):
    module_actions = ROLE_PERMISSIONS.get(role, {})
    crud = [
        f'can_{action}_{module}'
        for module, actions in module_actions.items()
        for action in actions
    ]
    default_menu = [
        f'can_view_menu_{key}' for key in MENU_ITEMS if key not in DEFAULT_RESTRICTED_MENU_ITEMS
    ]
    restricted_menu = ['can_view_menu_exchange_rate'] if role == 'ADMIN' else []
    return crud + default_menu + restricted_menu
```
  (Không sửa `all_menu_codenames()` — vẫn liệt kê đủ mọi codename kể cả `exchange_rate`, chỉ
  `codenames_for_role` đổi ai được CẤP mặc định.)

  Thêm form (`purchasing/forms.py`, cuối file — `Currency` import từ `.models` nếu chưa có sẵn từ
  form PR ở Task 3.2):
```python
class ExchangeRateForm(forms.ModelForm):
    class Meta:
        model = ExchangeRate
        fields = ['currency', 'rate_date', 'rate_to_vnd']
        widgets = {'rate_date': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)

    def clean_currency(self):
        currency = self.cleaned_data['currency']
        if currency == Currency.VND:
            raise forms.ValidationError('Không nhập tỷ giá cho VND — VND là đơn vị gốc quy đổi.')
        return currency

    def clean_rate_date(self):
        rate_date = self.cleaned_data['rate_date']
        if rate_date > timezone.localdate():
            raise forms.ValidationError('Ngày áp dụng không được là ngày trong tương lai.')
        return rate_date
```
  (Validate ở form đây là lớp UX — `CheckConstraint('exchange_rate_currency_not_vnd')` đã thêm ở
  Task 1.1 là lớp chặn thật ở DB, phòng đường ghi trực tiếp qua service/shell/Admin.)

  Thêm view (`purchasing/views.py`, nhóm riêng cuối file). **Quyết định cụ thể hoá (review phát
  hiện thiếu)**: decorator PHẢI kiểm tra CẢ role/superuser LẪN `can_view_menu('exchange_rate')` —
  chỉ kiểm role/superuser thì một Admin bị thu hồi riêng quyền `can_view_menu_exchange_rate` qua
  trang "Phân quyền chi tiết" (`user_permission_edit`) vẫn thao tác được, cùng lỗi dạng
  `can_transfer_inventory` từng gặp ở module `inventory` (xem CLAUDE.md mục "can_view_menu(key) alone
  only gates 'view'..."):
```python
def _exchange_rate_admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        is_admin = request.user.role == User.Role.ADMIN or request.user.is_superuser
        if not (is_admin and request.user.can_view_menu('exchange_rate')):
            raise PermissionDenied('Chỉ Admin mới quản lý được tỷ giá ngoại tệ.')
        return view(request, *args, **kwargs)
    return wrapper


@_exchange_rate_admin_required
def exchange_rate_list(request):
    rates = ExchangeRate.objects.select_related('created_by').all()
    page_obj, page_size = paginate_queryset(request, rates)
    return render(request, 'purchasing/exchange_rate_list.html', {
        'rates': page_obj, 'page_obj': page_obj, 'page_size': page_size,
    })


@_exchange_rate_admin_required
def exchange_rate_create(request):
    form = ExchangeRateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        log_action(
            request.user, AuditLog.Action.CREATE, target=obj, description=f'Tạo tỷ giá {obj}.',
            ip_address=client_ip(request))
        messages.success(request, f'Đã tạo tỷ giá "{obj}".')
        return redirect('purchasing:exchange_rate_list')
    return render(request, 'purchasing/exchange_rate_form.html', {'form': form, 'mode': 'create'})


@_exchange_rate_admin_required
def exchange_rate_update(request, pk):
    obj = get_object_or_404(ExchangeRate, pk=pk)
    form = ExchangeRateForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        log_action(
            request.user, AuditLog.Action.UPDATE, target=obj, description=f'Cập nhật tỷ giá {obj}.',
            ip_address=client_ip(request))
        messages.success(request, f'Đã cập nhật tỷ giá "{obj}".')
        return redirect('purchasing:exchange_rate_list')
    return render(request, 'purchasing/exchange_rate_form.html', {'form': form, 'mode': 'update', 'obj': obj})


@_exchange_rate_admin_required
def exchange_rate_delete(request, pk):
    obj = get_object_or_404(ExchangeRate, pk=pk)
    if request.method == 'POST':
        description = f'Xoá tỷ giá {obj}.'
        log_action(request.user, AuditLog.Action.DELETE, target=obj, description=description, ip_address=client_ip(request))
        obj.delete()
        messages.success(request, 'Đã xoá tỷ giá.')
        return redirect('purchasing:exchange_rate_list')
    return render(request, 'purchasing/exchange_rate_confirm_delete.html', {'obj': obj})
```
  Import thêm ở đầu `purchasing/views.py`: `ExchangeRate`, `ExchangeRateForm`.
- [ ] **Bước 4: Tạo migration `accounts` — permission MỚI + backfill cho Admin hiện có** (viết
  file migration ở bước này; KHÔNG chạy `manage.py migrate` ngay — dời sang Bước 6, sau khi
  route/template/context đã có ở Bước 5, để đúng thứ tự TDD: test chỉ được chạy xác nhận PASS ở
  Bước 7, sau khi migration đã thực sự áp dụng). Lý do bắt buộc tách migration riêng — `manage.py
  migrate` một mình không đủ, và `manage.py sync_roles` KHÔNG cấp quyền hiệu lực cho user hiện có —
  2 lý do độc lập, cả hai đều phải xử lý ở đây:

  **Lý do #1 — `sync_roles` không đủ**: `sync_roles()` (`accounts/rbac.py`) chỉ set lại
  `Group.permissions` — "mẫu tham chiếu", không phải quyền hiệu lực. `DirectPermissionsBackend`
  (`accounts/backends.py`) ghi đè `get_group_permissions()` trả về `set()` rỗng vô điều kiện, nên
  Group KHÔNG cộng bất kỳ quyền nào vào `has_perm()` — quyền hiệu lực của 1 user CHỈ đến từ
  `user.user_permissions` trực tiếp, và cột đó chỉ được ghi lại (`sync_user_permissions()`) khi user
  được TẠO MỚI hoặc RENAME role, không phải mỗi lần `sync_roles()` chạy. Vậy Admin đã tồn tại từ
  trước Task này sẽ KHÔNG tự có `can_view_menu_exchange_rate` dù `sync_roles()` đã chạy.

  **Lý do #2 — permission mới không tồn tại ngay khi `RunPython` cần nó**: Django chỉ thật sự tạo
  `Permission` row cho `Meta.permissions` mới qua signal `post_migrate`, phát **1 lần duy nhất ở
  cuối toàn bộ lệnh `migrate`** (sau khi MỌI migration, kể cả `RunPython` trong chính migration vừa
  thêm field, đã chạy xong) — nên 1 `RunPython` backfill nằm cùng migration với `AlterModelOptions`
  thêm permission mới KHÔNG thể `Permission.objects.get(codename='can_view_menu_exchange_rate')`
  ngay được (permission đó chưa tồn tại tại thời điểm đó). Cách sửa (cách xử lý chuẩn khi 1
  migration cần dùng ngay permission mới do chính nó tạo ra): gọi thẳng
  `django.contrib.auth.management.create_permissions()` ngay trong `RunPython` đầu tiên, set tạm
  `app_config.models_module = app_config.models_module or True` (bản `apps.get_app_config()` lấy từ
  registry lịch sử trong migration không có `models_module` thật nên `create_permissions()` mặc
  định bỏ qua app đó — set tạm về giá trị truthy bất kỳ để nó không bị bỏ qua), rồi khôi phục lại
  giá trị GỐC (không phải gán cứng `None`) ngay sau đó trong `finally`.

  Chạy `manage.py makemigrations accounts` trước (Django tự sinh `AlterModelOptions` cho
  `Meta.permissions` mới), đổi tên file thành `0017_exchange_rate_menu_permission.py`, rồi CHÈN
  THÊM 2 `RunPython` vào cuối `operations` (giữ nguyên `AlterModelOptions` Django tự sinh ở đầu):
```python
from django.db import migrations


def create_permissions_now(apps, schema_editor):
    from django.contrib.auth.management import create_permissions
    app_config = apps.get_app_config('accounts')
    # Lưu/khôi phục trong finally — KHÔNG gán cứng None ở cuối (bug review phát hiện): nếu hàm này
    # được gọi lại trực tiếp từ test (import_module, xem cuối Bước 4) khi models_module gốc đã có
    # giá trị thật (khác None), gán cứng None sẽ xoá mất trạng thái registry thật của app
    # 'accounts' sau khi chạy xong.
    original_models_module = app_config.models_module
    try:
        app_config.models_module = original_models_module or True
        create_permissions(
            app_config, apps=apps, using=schema_editor.connection.alias, verbosity=0)
    finally:
        app_config.models_module = original_models_module


def grant_exchange_rate_menu_to_existing_admins(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    Permission = apps.get_model('auth', 'Permission')
    db_alias = schema_editor.connection.alias
    perm = Permission.objects.using(db_alias).get(
        content_type__app_label='accounts', codename='can_view_menu_exchange_rate')
    for user in User.objects.using(db_alias).filter(role='ADMIN'):
        user.user_permissions.add(perm)  # .add() — KHÔNG .set(), giữ nguyên phân quyền chi tiết khác


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [('accounts', '0016_reseed_pr_create_update_permissions')]
    operations = [
        migrations.AlterModelOptions(
            name='user',
            options={'permissions': [...]},  # giữ nguyên đúng danh sách Django tự sinh — không gõ tay
        ),
        migrations.RunPython(create_permissions_now, noop_reverse),
        migrations.RunPython(grant_exchange_rate_menu_to_existing_admins, noop_reverse),
    ]
```
  Viết test riêng cho migration này trong `accounts`-side hoặc tái dùng
  `ProcurementAllocationModelTest`-style: tạo 1 Admin TRƯỚC khi gọi
  `create_permissions_now`/`grant_exchange_rate_menu_to_existing_admins` (gọi trực tiếp 2 hàm qua
  `import_module`, cùng kỹ thuật `importlib.import_module` đã dùng ở Task 1.2/migration `0018`),
  xác nhận `admin_user.has_perm('accounts.can_view_menu_exchange_rate')` trả `True` sau khi chạy —
  đây là bằng chứng thực sự bám đúng bug, không chỉ tin vào code đọc mắt thường. **Không truyền
  `schema_editor=None` như migration `0018`**, vì 2 hàm này cần `schema_editor.connection.alias`;
  test truyền một stub chỉ-đọc kết nối (không mở schema editor thật trong `TestCase`):
```python
from types import SimpleNamespace
from django.db import connection

schema_editor_stub = SimpleNamespace(connection=connection)
migration_module.create_permissions_now(django_apps, schema_editor_stub)
migration_module.grant_exchange_rate_menu_to_existing_admins(django_apps, schema_editor_stub)
```
- [ ] **Bước 5: Thêm route/template/context.** Route (`purchasing/urls.py`):
```python
path('exchange-rate/', views.exchange_rate_list, name='exchange_rate_list'),
path('exchange-rate/create/', views.exchange_rate_create, name='exchange_rate_create'),
path('exchange-rate/<int:pk>/update/', views.exchange_rate_update, name='exchange_rate_update'),
path('exchange-rate/<int:pk>/delete/', views.exchange_rate_delete, name='exchange_rate_delete'),
```
  Tạo 3 template tối thiểu (mirror bố cục các trang list/form/confirm-delete đã có trong
  `purchasing/templates/purchasing/` — vd `po_price_comparison.html` cho list, `pr_form.html` cho
  form đơn giản).

  Thêm flag context processor (`accounts/context_processors.py`, hàm `sidebar_permissions`, thêm 1
  dòng vào dict trả về — theo đúng pattern đã có cho 7 `MENU_ITEMS` còn lại, KHÔNG gọi method có
  tham số trực tiếp trong template vì Django template không hỗ trợ truyền literal argument khi
  resolve biến — đây chính là lỗi cú pháp review phát hiện ở bản v1):
```python
        'can_view_menu_exchange_rate': user.can_view_menu('exchange_rate'),
```
  Thêm mục "Tỷ giá ngoại tệ" vào sidebar (`accounts/templates/base.html` hoặc file sidebar riêng —
  xác định đúng vị trí bằng cách grep `can_view_menu_` trong template hiện có), gate bằng
  `{% if can_view_menu_exchange_rate %}` (biến context có sẵn, không phải gọi method) — theo đúng
  pattern §6.1 skill `wms-conventions` (không cần role oversight bổ sung vì `exchange_rate` không có
  ngoại lệ ADMIN/superuser bypass nào khác ngoài chính permission này).
- [ ] **Bước 6: Chạy `manage.py migrate`, xác nhận sạch** — áp dụng migration `0017` viết ở
  Bước 4. Bắt buộc chạy TRƯỚC Bước 7: nếu chưa chạy, permission `can_view_menu_exchange_rate` chưa
  tồn tại/chưa được backfill cho Admin hiện có, khiến test Admin hợp lệ ở Bước 7 nhận nhầm 403.
  (`manage.py sync_roles` vẫn nên chạy sau đó — không sai, chỉ là không ĐỦ — để Group "mẫu tham
  chiếu" cũng khớp ma trận mới cho mục đích tham chiếu/test.)
- [ ] **Bước 7: Chạy test, xác nhận PASS**
- [ ] **Bước 8: Commit**
```bash
git add accounts/permissions.py accounts/context_processors.py accounts/migrations/0017_exchange_rate_menu_permission.py accounts/templates/base.html purchasing/forms.py purchasing/views.py purchasing/urls.py purchasing/templates/purchasing/exchange_rate_*.html purchasing/tests.py
git commit -m "feat(pur): ExchangeRate CRUD (Admin-only), exchange_rate menu item restricted by default"
```

## Task 3.9: Rà soát cuối Phase 3 — route/template/sidebar không thiếu

Task xác minh, không thêm tính năng mới — chạy sau khi Task 3.1-3.8 đã commit xong.

- [ ] **Bước 1**: `manage.py check` — xác nhận không lỗi cấu hình URL/template.
- [ ] **Bước 2**: Grep `{% url 'purchasing:` trong toàn bộ `purchasing/templates/` — xác nhận mọi
  tên route được gọi đều có trong `purchasing/urls.py` (không có `NoReverseMatch` tiềm ẩn ở template
  chưa từng được test tự động chạm tới).
- [ ] **Bước 3**: Xác nhận `pr_detail.html` không còn tham chiếu `po_create?from_pr=` ở bất kỳ đâu
  khác ngoài chỗ đã sửa ở Task 3.6 (grep `po_create` trong `templates/purchasing/`).
- [ ] **Bước 4**: Test thủ công trên trình duyệt luồng đầy đủ: tạo PR (có dòng non-catalog) → nộp
  → duyệt 2 cấp (sửa `qty_approved` ở cấp Mua hàng) → map non-catalog → build PO từ PR line → sửa
  PO (`po_update`, xác nhận field khoá) → gửi PO. Ghi nhận lỗi UI nếu có, sửa ngay trong Task tương
  ứng ở trên (không tạo Task mới cho lỗi thuộc phạm vi Task đã có).
- [ ] **Bước 5: Commit** (chỉ nếu Bước 4 phát sinh sửa lỗi nhỏ; nếu không có gì sửa thì bỏ qua bước
  này, Task 3.9 không tạo commit riêng).

---

# Phase 4 — Management command

`purchasing/management/commands/` chưa tồn tại — tạo cả thư mục (`__init__.py` rỗng) ở Task 4.1.

## Task 4.1: `report_allocation_migration_exceptions`

**File:**
- Tạo: `purchasing/management/__init__.py`, `purchasing/management/commands/__init__.py` (rỗng)
- Tạo: `purchasing/management/commands/report_allocation_migration_exceptions.py`
- Sửa: `purchasing/services.py` (hàm mới `find_allocation_migration_exceptions`)
- Test: `purchasing/tests.py` (class mới `ReportAllocationMigrationExceptionsCommandTest`)

**Giao diện:**
- Cung cấp: `find_allocation_migration_exceptions() -> list[PurchaseRequestItem]` (service, gọi
  bởi command) — định nghĩa lại "ngoại lệ" bằng dữ liệu THẬT tại thời điểm gọi (không phải nhật ký
  cố định từ lúc chạy migration 0018): mọi `PurchaseRequestItem` có `purchase_request.linked_po`
  nhưng **chưa có** `ProcurementAllocation` nào — đúng đối tượng "chưa reconcile" mà mục 9 mô tả
  cần Admin xử lý qua `reconcile_legacy_po_item_allocations` command (Task 4.3).

- [ ] **Bước 1: Viết test đang FAIL**
```python
class ReportAllocationMigrationExceptionsCommandTest(TestCase):
    def test_command_lists_pr_items_with_linked_po_but_no_allocation(self):
        staff = User.objects.create_user(username='staff1', password='staff-pass-123', role=User.Role.STAFF)
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=supplier, source=PurchaseOrder.Source.FROM_PR)
        PurchaseOrderItem.objects.create(purchase_order=po, product=product, qty_ordered=10, unit_price=Decimal('1000'))
        pr = PurchaseRequest.objects.create(
            requested_by=staff, warehouse=warehouse, cost_center='CC-001', linked_po=po,
            status=PurchaseRequest.Status.APPROVED)
        PurchaseRequestItem.objects.create(
            purchase_request=pr, product=product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')

        out = StringIO()
        call_command('report_allocation_migration_exceptions', stdout=out)
        self.assertIn(pr.request_no, out.getvalue())
```
  (Thêm `from io import StringIO` và `from django.core.management import call_command` ở đầu
  `purchasing/tests.py` nếu chưa có.)
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `management.commands` không tồn tại,
  `django.core.management.CommandError: Unknown command`.
- [ ] **Bước 3: Viết code tối thiểu để PASS.** Thêm hàm (`purchasing/services.py`, gần
  `find_duplicate_po_products`):
```python
def find_allocation_migration_exceptions():
    """Đọc lại (bất kỳ lúc nào, không chỉ lúc migrate) danh sách PurchaseRequestItem có
    linked_po nhưng CHƯA có ProcurementAllocation nào — dùng model thật (không phải
    apps.get_model(), vì đây không phải RunPython bên trong migration).
    """
    return list(
        PurchaseRequestItem.objects
        .filter(purchase_request__linked_po__isnull=False, allocations__isnull=True)
        .select_related('purchase_request', 'purchase_request__linked_po', 'product')
        .distinct()
    )
```
  Tạo `purchasing/management/__init__.py` và `purchasing/management/commands/__init__.py` (rỗng),
  rồi `purchasing/management/commands/report_allocation_migration_exceptions.py`:
```python
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
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**
```bash
git add purchasing/management purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): add report_allocation_migration_exceptions management command"
```

## Task 4.2: `check_non_catalog_sla`

**File:**
- Tạo: `purchasing/management/commands/check_non_catalog_sla.py`
- Sửa: `purchasing/services.py` (hàm mới `overdue_non_catalog_items`, helper `_business_days_before`)
- Test: `purchasing/tests.py` (class mới `CheckNonCatalogSlaCommandTest`)

**Giao diện:**
- Cung cấp: `overdue_non_catalog_items(reference_date=None, business_days=3) ->
  list[PurchaseRequestItem]`.

**Lỗi đã sửa so với v1 (review phát hiện, 3 lỗi độc lập trong cùng fixture test)**:
1. `Approval` dùng field `target_type`/`target_id` (xem `accounts/models.py`), KHÔNG phải
   `content_type`/`object_id` như v1 — sai tên field sẽ raise `TypeError` ngay khi tạo fixture.
2. `Approval.submitted_at` là `auto_now_add=True` — Django **luôn ghi đè** giá trị này bằng
   `timezone.now()` ngay trong `save()`, bất kể `.objects.create(submitted_at=...)` truyền gì vào;
   v1 tưởng đã "giả lập quá khứ" nhưng thực chất mọi Approval đều có `submitted_at` = thời điểm test
   chạy thật, khiến tham số `submitted_days_ago_business` hoàn toàn không có tác dụng. Cách backdate
   đúng: tạo trước rồi `Approval.objects.filter(pk=...).update(submitted_at=...)` —
   `QuerySet.update()` không đi qua `save()`/`pre_save()` nên `auto_now_add` không can thiệp.
3. Tính "N ngày trước" bằng `timezone.now() - timedelta(days=N)` là ngày LỊCH, không phải ngày LÀM
   VIỆC — kết quả test phụ thuộc đúng vào thứ trong tuần lúc CI/dev chạy test (có thể tình cờ PASS
   hôm nay nhưng FAIL tuần sau nếu chạy đúng lúc bắc qua cuối tuần khác). Sửa: dùng 1 `reference_date`
   **cố định** (truyền tường minh vào `overdue_non_catalog_items(reference_date=...)`, hàm đã có sẵn
   tham số này), và dựng 2 ca test cố tình bắc qua cuối tuần thật (Thứ 6 → Thứ 2) để tự chứng minh
   `_business_days_before` bỏ qua đúng Thứ 7/CN, không phụ thuộc ngày chạy test.

- [ ] **Bước 1: Viết test đang FAIL (TC-PUR-SLA-001/002/003)**
```python
from datetime import date, datetime

REFERENCE_DATE = date(2026, 8, 10)  # Thứ Hai cố định — KHÔNG dùng timezone.localdate() (ngày chạy
                                     # test thật), để kết quả PASS/FAIL không phụ thuộc thứ trong
                                     # tuần lúc test được chạy.


class CheckNonCatalogSlaCommandTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='staff1', password='staff-pass-123', role=User.Role.STAFF)
        self.pur_manager = User.objects.create_user(
            username='purm', password='purm-pass-123', role=User.Role.MANAGER,
            department=User.Department.PURCHASING, is_manager=True)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')

    def _make_pr_with_non_catalog_item(self, submitted_date, mapped=False):
        """``submitted_date``: 1 đối tượng ``date`` VN-local cụ thể (không phải "N ngày trước") —
        hàm tự dựng datetime 10:00 sáng VN-local rồi backdate qua ``.update()`` (auto_now_add
        không cho set qua ``.create()``, xem lỗi #2 ở trên)."""
        pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.PENDING_PUR)
        product = None
        if mapped:
            product = Product.objects.create(product_code=f'NVL-{pr.pk:04d}', name='Ống nhựa', uom='cây')
        item = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=product, qty_requested=3,
            non_catalog_name='Ống nhựa PVC', non_catalog_uom='cây',
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('5000'), budget_category='VT')
        approval = Approval.objects.create(
            target_type=ContentType.objects.get_for_model(PurchaseRequest), target_id=str(pr.pk),
            department=User.Department.PURCHASING, status=Approval.Status.APPROVED, submitted_by=self.staff,
        )
        aware_submitted_at = timezone.make_aware(
            datetime(submitted_date.year, submitted_date.month, submitted_date.day, 10, 0))
        Approval.objects.filter(pk=approval.pk).update(submitted_at=aware_submitted_at)
        return item

    def test_TC_PUR_SLA_001_overdue_4_business_days_included(self):
        # Nộp Thứ 3 04/08/2026, mốc kiểm tra Thứ 2 10/08/2026 -> 4 ngày LÀM VIỆC đã qua
        # (05, 06, 07/08, 10/08 — cuối tuần 08-09/08 bị bỏ qua đúng), vượt ngưỡng 3.
        item = self._make_pr_with_non_catalog_item(submitted_date=date(2026, 8, 4))
        self.assertIn(item, overdue_non_catalog_items(reference_date=REFERENCE_DATE))

    def test_TC_PUR_SLA_002_only_1_business_day_across_weekend_not_overdue(self):
        # Nộp Thứ 6 07/08/2026, mốc kiểm tra Thứ 2 10/08/2026 -> chỉ 1 ngày LÀM VIỆC đã qua (10/08),
        # dù cách 3 ngày LỊCH (bắc qua trọn 1 cuối tuần) -> KHÔNG quá hạn. Ca này cố tình bắc qua
        # cuối tuần thật để chứng minh _business_days_before không đếm nhầm Thứ 7/CN thành ngày làm việc.
        item = self._make_pr_with_non_catalog_item(submitted_date=date(2026, 8, 7))
        self.assertNotIn(item, overdue_non_catalog_items(reference_date=REFERENCE_DATE))

    def test_TC_PUR_SLA_003_already_mapped_excluded(self):
        item = self._make_pr_with_non_catalog_item(submitted_date=date(2026, 7, 20), mapped=True)
        self.assertNotIn(item, overdue_non_catalog_items(reference_date=REFERENCE_DATE))

    def test_command_notifies_pur_manager(self):
        self._make_pr_with_non_catalog_item(submitted_date=date(2026, 8, 4))
        with patch('purchasing.services.timezone.localdate', return_value=REFERENCE_DATE):
            call_command('check_non_catalog_sla')
        self.assertTrue(Notification.objects.filter(recipient=self.pur_manager).exists())
```
  (Cần import `Approval`, `Notification`, `ContentType`, `date`, `datetime` từ `datetime`, và
  `from unittest.mock import patch` — kiểm tra đã có sẵn ở đầu `purchasing/tests.py` hay chưa, thêm
  nếu thiếu. Patch đúng `purchasing.services.timezone.localdate` — namespace của module GỌI hàm,
  không phải `django.utils.timezone.localdate` — cùng quy ước đã ghi ở CLAUDE.md mục "grep bất kỳ
  `.date()` trần...".)
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS.** Thêm vào `purchasing/services.py` (đầu file thêm
  `from datetime import timedelta`; import thêm `Approval`, `ContentType` nếu chưa có):
```python
def _business_days_before(reference_date, business_days):
    """Trừ lùi N ngày làm việc (bỏ qua Thứ 7/CN, chưa tính lịch nghỉ lễ — đủ cho MVP theo pattern
    ⏸️ đơn giản hoá, mục 6 FSD Stage 2)."""
    current = reference_date
    counted = 0
    while counted < business_days:
        current -= timedelta(days=1)
        if current.weekday() < 5:  # 0=Thứ 2 .. 4=Thứ 6
            counted += 1
    return current


def overdue_non_catalog_items(reference_date=None, business_days=3):
    """Mục 6: dòng non-catalog (product__isnull=True) mà PR đã có Approval(department=
    PURCHASING) — mốc "PUR tiếp nhận" — quá ``business_days`` ngày làm việc mà vẫn chưa map.
    """
    reference_date = reference_date or timezone.localdate()
    threshold = _business_days_before(reference_date, business_days)
    pr_content_type = ContentType.objects.get_for_model(PurchaseRequest)
    overdue = []
    for item in PurchaseRequestItem.objects.filter(product__isnull=True).select_related('purchase_request'):
        first_pur_approval = (
            Approval.objects.filter(
                target_type=pr_content_type, target_id=str(item.purchase_request_id),
                department=User.Department.PURCHASING,
            ).order_by('submitted_at').first()
        )
        # timezone.localtime(...).date() — KHÔNG bare .date() trên datetime UTC aware (CLAUDE.md:
        # so sánh ngày nghiệp vụ phải quy đổi VN-local trước, .date() trần luôn là ngày UTC).
        if first_pur_approval and timezone.localtime(first_pur_approval.submitted_at).date() <= threshold:
            overdue.append(item)
    return overdue
```
  Tạo `purchasing/management/commands/check_non_catalog_sla.py`:
```python
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
```
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**
```bash
git add purchasing/management/commands/check_non_catalog_sla.py purchasing/services.py purchasing/tests.py
git commit -m "feat(pur): add check_non_catalog_sla management command (quyết định #9)"
```

## Task 4.3: `reconcile_legacy_po_item_allocations` command — `--dry-run` rollback bằng savepoint thật

**File:**
- Tạo: `purchasing/management/commands/reconcile_legacy_po_item_allocations.py`
- Test: `purchasing/tests.py` (class mới `ReconcileLegacyCommandTest`)

**Giao diện:**
- Sử dụng: `reconcile_legacy_po_item_allocations` (Task 2.10). Không viết logic reconcile lần 2
  trong command — command chỉ parse tham số CLI rồi gọi đúng 1 hàm service, `--dry-run` chỉ khác ở
  chỗ CÓ rollback cuối cùng hay không (lưu ý kỹ thuật #3, xem Ràng buộc chung).

- [ ] **Bước 1: Viết test đang FAIL (AC #33, TC-PUR-PR-05-022)**
```python
class ReconcileLegacyCommandTest(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.staff = User.objects.create_user(username='staff1', password='staff-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        self.po_item = PurchaseOrderItem.objects.create(
            purchase_order=self.po, product=self.product, qty_ordered=10, unit_price=Decimal('1000'))
        pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED, linked_po=self.po)
        self.pr_item = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')

    def test_TC_PUR_PR_05_022_dry_run_does_not_commit(self):
        out = StringIO()
        call_command(
            'reconcile_legacy_po_item_allocations', po_item=self.po_item.pk,
            allocation=[f'{self.pr_item.pk}:10'], actor='admin1', dry_run=True, stdout=out,
        )
        self.assertEqual(ProcurementAllocation.objects.count(), 0)
        self.assertIn('DRY-RUN', out.getvalue())

    def test_TC_PUR_PR_05_022_real_run_commits(self):
        call_command(
            'reconcile_legacy_po_item_allocations', po_item=self.po_item.pk,
            allocation=[f'{self.pr_item.pk}:10'], actor='admin1', dry_run=False,
        )
        self.assertEqual(ProcurementAllocation.objects.count(), 1)

    def test_TC_PUR_PR_05_022_invalid_pr_item_id_aborts_no_partial(self):
        with self.assertRaises(CommandError):
            call_command(
                'reconcile_legacy_po_item_allocations', po_item=self.po_item.pk,
                allocation=['999999:10'], actor='admin1', dry_run=False)
        self.assertEqual(ProcurementAllocation.objects.count(), 0)

    def test_actor_username_not_found_raises_clear_error(self):
        with self.assertRaises(CommandError):
            call_command(
                'reconcile_legacy_po_item_allocations', po_item=self.po_item.pk,
                allocation=[f'{self.pr_item.pk}:10'], actor='khong_ton_tai', dry_run=False)
```
  (Thêm `from django.core.management.base import CommandError` ở đầu `purchasing/tests.py` nếu
  chưa có — `call_command` đã có sẵn nhờ Task 4.1.)
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — command chưa tồn tại.
- [ ] **Bước 3: Viết code tối thiểu để PASS.** Tạo
  `purchasing/management/commands/reconcile_legacy_po_item_allocations.py`:
```python
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
```
  (Không viết `except User.MultipleObjectsReturned` — `User.username` đã `unique=True` ở tầng DB
  từ `AbstractUser`, tình huống đó không thể xảy ra thật, thêm nhánh xử lý cho nó là error handling
  cho kịch bản không thể xảy ra, vi phạm nguyên tắc "chỉ validate ở ranh giới thật cần" — CLAUDE.md.)
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**
```bash
git add purchasing/management/commands/reconcile_legacy_po_item_allocations.py purchasing/tests.py
git commit -m "feat(pur): add reconcile_legacy_po_item_allocations management command with real --dry-run rollback (T9)"
```

---

# Phase 5 — Test bổ sung / regression

Gom các TC không thuộc gọn 1 unit của Phase 1-4: quét `CheckConstraint` còn thiếu, concurrency
(`TransactionTestCase`), 9 kịch bản còn lại của hàm batch reconcile (đã hoãn từ Task 2.10), và rà
soát chéo cuối cùng so với FSD mục 10/11.

## Task 5.1: Quét `CheckConstraint` còn thiếu trên `ProcurementAllocation`

**File:**
- Test: `purchasing/tests.py` (thêm method vào class `ProcurementAllocationModelTest` đã tạo ở
  Task 1.1 — KHÔNG tạo class trùng)

**Giao diện:** không đổi code sản phẩm — Task này chỉ bổ sung test cho 2 `CheckConstraint` đã tồn
tại từ Task 1.1 nhưng chưa có test tường minh (TC-PUR-PR-05-012/013).

- [ ] **Bước 1: Viết test (đã PASS ngay vì constraint đã tồn tại từ Task 1.1 — mục đích Task này
  là PHỦ test, không phải sửa code)**
```python
    def test_TC_PUR_PR_05_012_snapshot_fields_cannot_be_empty(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProcurementAllocation.objects.create(
                    pr_item=self.pr_item, po_item=self.po_item, qty_allocated=5,
                    po_no_snapshot='', product_code_snapshot=self.product.product_code,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProcurementAllocation.objects.create(
                    pr_item=self.pr_item, po_item=self.po_item, qty_allocated=5,
                    po_no_snapshot=self.po.po_no, product_code_snapshot='',
                )

    def test_TC_PUR_PR_05_013_release_fields_match_status_constraint(self):
        # (a) ACTIVE nhưng released_at khác None -> IntegrityError.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProcurementAllocation.objects.create(
                    pr_item=self.pr_item, po_item=self.po_item, qty_allocated=5,
                    po_no_snapshot=self.po.po_no, product_code_snapshot=self.product.product_code,
                    status=ProcurementAllocation.Status.ACTIVE, released_at=timezone.now(),
                )
        # (b) RELEASED nhưng released_at=None -> IntegrityError.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProcurementAllocation.objects.create(
                    pr_item=self.pr_item, po_item=self.po_item, qty_allocated=5,
                    po_no_snapshot=self.po.po_no, product_code_snapshot=self.product.product_code,
                    status=ProcurementAllocation.Status.RELEASED, released_reason='huỷ',
                )
        # (c) RELEASED nhưng released_reason='' -> IntegrityError.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProcurementAllocation.objects.create(
                    pr_item=self.pr_item, po_item=self.po_item, qty_allocated=5,
                    po_no_snapshot=self.po.po_no, product_code_snapshot=self.product.product_code,
                    status=ProcurementAllocation.Status.RELEASED, released_at=timezone.now(), released_reason='',
                )
        # (d) RELEASED, po_item=None, released_at/released_reason hợp lệ, released_by=None -> tạo được
        #     (released_by rỗng vẫn hợp lệ cho ca release tự động do hệ thống, mục 2.4).
        ProcurementAllocation.objects.create(
            pr_item=self.pr_item, po_item=None, qty_allocated=5,
            po_no_snapshot=self.po.po_no, product_code_snapshot=self.product.product_code,
            status=ProcurementAllocation.Status.RELEASED, released_at=timezone.now(),
            released_reason='huỷ', released_by=None,
        )
```
- [ ] **Bước 2: Chạy test, xác nhận PASS ngay** (nếu FAIL, nghĩa là constraint ở Task 1.1 sai —
  quay lại sửa `Meta.constraints` của `ProcurementAllocation`, không sửa test để né).
- [ ] **Bước 3: Commit**
```bash
git add purchasing/tests.py
git commit -m "test(pur): cover remaining ProcurementAllocation CheckConstraint scenarios (TC-05-012/013)"
```

## Task 5.2: Concurrency — `TransactionTestCase` + `threading.Barrier` (TC-04-002/003, TC-05-027)

**File:**
- Test: `purchasing/tests.py` (class mới `AllocationConcurrencyDeadlockTests(TransactionTestCase)`)

Mirror đúng pattern `stocktake.tests.MultiSkuLockOrderDeadlockTests`/`HandoffStocktakeDeadlockTests`
(CLAUDE.md — "first threading precedent", giờ áp dụng cho nhóm Allocation): `TransactionTestCase`
(không phải `TestCase`) vì cần 2 transaction/kết nối DB thật để tạo tranh chấp khoá thật.

- [ ] **Bước 1: Viết test**
```python
class AllocationConcurrencyDeadlockTests(TransactionTestCase):
    """TC-PUR-PR-04-002/003, TC-PUR-PR-05-027 — regression cho lock order mục 4 điểm 2/mục 4
    điểm 4 (nhóm Allocation): PurchaseOrder -> PurchaseOrderItem -> PurchaseRequestItem ->
    ProcurementAllocation. Cùng kỹ thuật threading.Barrier + TransactionTestCase với
    stocktake.tests (CLAUDE.md).
    """

    def setUp(self):
        self.admin_user = User.objects.create_user(username='admin1', password='admin-pass-123', role=User.Role.ADMIN)
        self.staff = User.objects.create_user(username='staff1', password='staff-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')

    def _run_concurrently(self, fn_a, fn_b):
        barrier = threading.Barrier(2)
        errors = {}

        def wrap(name, fn):
            try:
                barrier.wait(timeout=5)
                fn()
            except Exception as exc:  # noqa: BLE001 - ghi lại để assert bên ngoài thread
                errors[name] = exc
            finally:
                connection.close()

        t1 = threading.Thread(target=wrap, args=('a', fn_a))
        t2 = threading.Thread(target=wrap, args=('b', fn_b))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        self.assertFalse(t1.is_alive(), 'thread a bị treo quá 10s (nghi deadlock)')
        self.assertFalse(t2.is_alive(), 'thread b bị treo quá 10s (nghi deadlock)')
        for name, exc in errors.items():
            self.assertNotIsInstance(exc, OperationalError, f'{name} raised {exc!r} — nghi deadlock')
        return errors

    def test_TC_PUR_PR_04_002_concurrent_create_allocation_exceeding_qty_open(self):
        pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED)
        pr_item = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        po_a = PurchaseOrder.objects.create(po_no='PO-A', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        po_item_a = PurchaseOrderItem.objects.create(
            purchase_order=po_a, product=self.product, qty_ordered=0, unit_price=Decimal('1000'))
        po_b = PurchaseOrder.objects.create(po_no='PO-B', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        po_item_b = PurchaseOrderItem.objects.create(
            purchase_order=po_b, product=self.product, qty_ordered=0, unit_price=Decimal('1000'))

        errors = self._run_concurrently(
            lambda: create_allocation(pr_item, po_item_a, qty=6, actor=self.admin_user),
            lambda: create_allocation(pr_item, po_item_b, qty=6, actor=self.admin_user),
        )
        self.assertEqual(len(errors), 1, 'đúng 1 trong 2 lần gọi phải thất bại (tổng 6+6=12 > qty_open=10)')
        self.assertIsInstance(next(iter(errors.values())), ValidationError)
        pr_item.refresh_from_db()
        self.assertLessEqual(pr_item.qty_allocated, 10)

    def test_TC_PUR_PR_04_003_concurrent_delete_po_item_and_create_allocation(self):
        pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED)
        pr_item_existing = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=self.product, qty_requested=5, qty_approved=5,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        pr_item_new = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=self.product, qty_requested=5, qty_approved=5,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        po = PurchaseOrder.objects.create(po_no='PO-9001', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        po_item = PurchaseOrderItem.objects.create(
            purchase_order=po, product=self.product, qty_ordered=0, unit_price=Decimal('1000'))
        create_allocation(pr_item_existing, po_item, qty=5, actor=self.admin_user)

        errors = self._run_concurrently(
            lambda: delete_draft_po_item_with_allocations(po_item, actor=self.admin_user),
            lambda: create_allocation(pr_item_new, po_item, qty=5, actor=self.admin_user),
        )
        # Lỗi đã sửa so với v1 (review phát hiện): 2 nhánh thắng-thua khác nhau tạo ra 2 LOẠI lỗi
        # khác nhau, không chỉ 1. Cả 2 hàm đều khoá PurchaseOrder (cùng po_item.purchase_order_id)
        # TRƯỚC TIÊN — ai thắng lock PO chạy trọn transaction, commit, rồi bên thua mới được chạy
        # tiếp: (a) delete_draft_po_item_with_allocations() thắng trước -> xoá hẳn po_item -> khi
        # create_allocation() (thua, chạy sau) tới lượt `PurchaseOrderItem.objects
        # .select_for_update().get(pk=po_item.pk)`, row đã KHÔNG CÒN tồn tại -> raise
        # `PurchaseOrderItem.DoesNotExist`, KHÔNG PHẢI `ValidationError`; (b) create_allocation()
        # thắng trước -> tạo allocation thành công, po_item.qty_ordered tăng lên -> khi
        # delete_draft_po_item_with_allocations() (thua, chạy sau) tới lượt, nó thấy CẢ 2 allocation
        # (kể cả cái vừa tạo) và release/xoá sạch bình thường -> KHÔNG raise gì cả (`errors` rỗng ở
        # nhánh này). Assertion phải chấp nhận cả 2 loại lỗi khả dĩ, không chỉ `ValidationError`
        # (v1 chỉ assert `ValidationError`, flaky theo thứ tự thắng-thua thật của OS scheduler).
        for exc in errors.values():
            self.assertIsInstance(exc, (ValidationError, PurchaseOrderItem.DoesNotExist))
        # Không assert cụ thể bên nào thắng — chỉ cần bất biến mục 4 điểm 4 vẫn đúng nếu po_item
        # còn tồn tại sau cùng.
        if PurchaseOrderItem.objects.filter(pk=po_item.pk).exists():
            po_item.refresh_from_db()
            total_active = ProcurementAllocation.objects.filter(
                po_item=po_item, status=ProcurementAllocation.Status.ACTIVE,
            ).aggregate(total=Sum('qty_allocated'))['total'] or 0
            self.assertEqual(total_active, po_item.qty_ordered)

    def test_TC_PUR_PR_05_027_concurrent_reconcile_and_send_po(self):
        po = PurchaseOrder.objects.create(
            po_no='PO-9002', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR,
            status=PurchaseOrder.Status.APPROVED)
        po_item = PurchaseOrderItem.objects.create(
            purchase_order=po, product=self.product, qty_ordered=10, unit_price=Decimal('1000'))
        pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED, linked_po=po)
        pr_item = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')

        # Cả 2 hàm đều khoá PurchaseOrder TRƯỚC (đúng lock order mục 4 điểm 4) — chỉ có 1 resource
        # tranh chấp thật (chính row PurchaseOrder), send_po() không tự khoá PurchaseOrderItem nên
        # không đọc được dữ liệu "đang chờ" của reconcile — vì vậy reconcile ('a') KHÔNG BAO GIỜ
        # được phép lỗi ở kịch bản này; chỉ send_po ('b') có thể lỗi, khi nó thắng lock PO trước
        # lúc allocation chưa tồn tại (existing_total=0 != qty_ordered=10).
        errors = self._run_concurrently(
            lambda: reconcile_legacy_po_item_allocations(po_item, [(pr_item, 10)], actor=self.admin_user),
            lambda: send_po(po, actor=self.admin_user),
        )
        self.assertNotIn('a', errors, 'reconcile không được lỗi trong kịch bản này')
        if errors:
            self.assertEqual(set(errors.keys()), {'b'})
            self.assertIsInstance(errors['b'], ValidationError)
        self.assertEqual(
            ProcurementAllocation.objects.filter(
                po_item=po_item, status=ProcurementAllocation.Status.ACTIVE).count(),
            1, 'reconcile phải luôn thành công dù thắng hay thua tranh chấp khoá PurchaseOrder')
        po.refresh_from_db()
        if errors:
            self.assertEqual(po.status, PurchaseOrder.Status.APPROVED)
        else:
            self.assertEqual(po.status, PurchaseOrder.Status.SENT)
```
  (Cần `import threading`, `from django.db import connection`, `from django.db.utils import
  OperationalError` ở đầu `purchasing/tests.py` nếu chưa có — `stocktake.tests` đã dùng pattern
  này, kiểm tra import tương đương.)
- [ ] **Bước 2: Chạy test, xác nhận PASS** (nếu FAIL với `OperationalError` thật — nghĩa là lock
  order ở Task 2.1/2.3/2.10 sai thứ tự so với Ràng buộc chung, quay lại sửa service, không sửa
  test để né).
- [ ] **Bước 3: Commit**
```bash
git add purchasing/tests.py
git commit -m "test(pur): concurrency/deadlock regression for Allocation lock order (TC-04-002/003, TC-05-027)"
```

## Task 5.3: 9 kịch bản còn lại của `reconcile_legacy_po_item_allocations` (TC-05-020) + PO `APPROVED` (TC-05-021)

**File:**
- Test: `purchasing/tests.py` (thêm method vào class `ReconcileLegacyPoItemAllocationsTest` đã tạo
  ở Task 2.10 — KHÔNG tạo class trùng)

- [ ] **Bước 1: Viết đủ 10 test (9 kịch bản vi phạm + 1 case PO `APPROVED` thành công)**
```python
    def test_TC_PUR_PR_05_020_a_actor_not_admin_rejected(self):
        pr_item = self._pr_item(10, 10, linked_po=self.po)
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.staff)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_020_b_actor_inactive_rejected(self):
        pr_item = self._pr_item(10, 10, linked_po=self.po)
        self.admin_user.is_active = False
        self.admin_user.save(update_fields=['is_active'])
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_020_c_po_source_manual_rejected(self):
        pr_item = self._pr_item(10, 10, linked_po=self.po)
        self.po.source = PurchaseOrder.Source.MANUAL
        self.po.save(update_fields=['source'])
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_020_d_pr_item_not_approved_rejected(self):
        pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.PENDING_PUR, linked_po=self.po)
        pr_item = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=self.product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_020_e_product_mismatch_rejected(self):
        other_product = Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg')
        pr = PurchaseRequest.objects.create(
            requested_by=self.staff, warehouse=self.warehouse, cost_center='CC-001',
            status=PurchaseRequest.Status.APPROVED, linked_po=self.po)
        pr_item = PurchaseRequestItem.objects.create(
            purchase_request=pr, product=other_product, qty_requested=10, qty_approved=10,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('1000'), budget_category='NL')
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_020_f_qty_below_one_rejected(self):
        pr_item = self._pr_item(10, 10, linked_po=self.po)
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 0)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_020_g_qty_exceeds_qty_open_rejected(self):
        pr_item = self._pr_item(qty_requested=10, qty_approved=3, linked_po=self.po)  # qty_open=3
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_020_h_linked_po_mismatch_rejected(self):
        other_po = PurchaseOrder.objects.create(
            po_no='PO-OTHER', supplier=self.supplier, source=PurchaseOrder.Source.FROM_PR)
        pr_item = self._pr_item(10, 10, linked_po=other_po)  # trỏ PO KHÁC, không phải None
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 0)

    def test_TC_PUR_PR_05_020_i_existing_active_allocation_rejected(self):
        pr_item = self._pr_item(10, 10, linked_po=self.po)
        ProcurementAllocation.objects.create(
            pr_item=pr_item, po_item=self.po_item, qty_allocated=10,
            po_no_snapshot=self.po.po_no, product_code_snapshot=self.product.product_code,
        )
        with self.assertRaises(ValidationError):
            reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.admin_user)
        self.assertEqual(ProcurementAllocation.objects.filter(po_item=self.po_item).count(), 1)  # không tăng thêm

    def test_TC_PUR_PR_05_021_po_approved_not_draft_allowed_then_send_po_succeeds(self):
        self.po.status = PurchaseOrder.Status.APPROVED
        self.po.save(update_fields=['status'])
        pr_item = self._pr_item(10, 10, linked_po=self.po)
        reconcile_legacy_po_item_allocations(self.po_item, [(pr_item, 10)], actor=self.admin_user)
        self.po_item.refresh_from_db()
        self.assertEqual(self.po_item.qty_ordered, 10)
        self.assertEqual(self.po_item.product_id, self.product.pk)
        self.assertEqual(self.po_item.unit_price, Decimal('1000'))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.APPROVED)  # chưa đổi gì khác
        send_po(self.po, actor=self.admin_user)  # không còn bị guard Task 2.4 chặn
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.Status.SENT)
```
- [ ] **Bước 2: Chạy toàn bộ class `ReconcileLegacyPoItemAllocationsTest` (Task 2.10 + Task 5.3),
  xác nhận PASS hết** (nếu 1 trong 9 kịch bản FAIL, quay lại sửa thứ tự/điều kiện trong hàm ở Task
  2.10 — hàm đã viết đủ 14 điều kiện ở đó nên về lý thuyết không cần sửa gì thêm, Bước này chỉ để
  xác nhận thật).
- [ ] **Bước 3: Commit**
```bash
git add purchasing/tests.py
git commit -m "test(pur): cover remaining 9 rejection scenarios + APPROVED-PO case for reconcile batch (TC-05-020/021)"
```

## Task 5.4: Rà soát chéo cuối cùng — khoảng trống nhỏ còn lại + regression không đổi hành vi cũ

**File:**
- Test: `purchasing/tests.py` (thêm rải rác vào các class đã có ở trên, không tạo class mới trừ
  khi ghi rõ)

- [ ] **Bước 1**: `TC-PUR-PR-01-001` — thêm vào `PurchaseRequestItemFormTest` (Task 3.2):
```python
    def test_TC_PUR_PR_01_001_missing_required_date_invalid(self):
        data = self._base_data(product=self.product.pk)
        data['required_date'] = ''
        form = PurchaseRequestItemForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('required_date', form.errors)
```
- [ ] **Bước 2**: `TC-PUR-PR-05-002` (2 dòng PR khác product → 2 `PurchaseOrderItem` riêng) — thêm
  vào `BuildPoFromAllocationsTest` (Task 2.5):
```python
    def test_TC_PUR_PR_05_002_different_products_create_separate_po_items(self):
        other_product = Product.objects.create(product_code='NVL-0002', name='Đường', uom='kg')
        pr_item_c = PurchaseRequestItem.objects.create(
            purchase_request=self.pr, product=other_product, qty_requested=3, qty_approved=3,
            required_date=timezone.localdate(), currency='VND', estimated_unit_price=Decimal('2000'), budget_category='NL')
        po = build_po_from_allocations(
            self.supplier, [(self.pr_item_a, 10), (pr_item_c, 3)],
            {self.product.pk: Decimal('1200'), other_product.pk: Decimal('2500')}, actor=self.admin_user)
        self.assertEqual(po.items.count(), 2)
```
- [ ] **Bước 3**: `TC-PUR-PR-07-003` (regression — không đổi so với hiện tại, xác nhận Stage 2
  không phá `delete_purchase_request`) — thêm vào `CancelPrItemOpenQtyTest` (Task 2.8) hoặc test
  class PR đã có sẵn từ trước Stage 2:
```python
    def test_TC_PUR_PR_07_003_delete_draft_pr_unaffected_by_stage2(self):
        pr = PurchaseRequest.objects.create(
            requested_by=self.user, warehouse=self.warehouse, cost_center='CC-001')
        request_no = delete_purchase_request(pr, actor=self.user)
        self.assertEqual(request_no, pr.request_no)
        self.assertFalse(PurchaseRequest.objects.filter(pk=pr.pk).exists())
```
  (Cần import `delete_purchase_request` ở đầu `purchasing/tests.py` — đã có sẵn từ trước Stage 2,
  chỉ cần gọi lại trong class mới nếu chưa có test tương đương.)
- [ ] **Bước 4**: Chạy **toàn bộ test project** (`manage.py test`), KHÔNG chỉ `manage.py test
  purchasing` — Stage 2 sửa cả `accounts/permissions.py`/`accounts/context_processors.py` + thêm
  migration `accounts/migrations/0017_exchange_rate_menu_permission.py` (Task 3.8), nên riêng
  `manage.py test purchasing` không chạm tới `accounts/tests.py` hay bất kỳ app nào khác đọc
  `codenames_for_role()`/`sidebar_permissions` (vd `catalog`/`warehouse`/`inventory` nếu có test
  dựng sidebar/permission dùng chung). Xác nhận 100% PASS toàn bộ, không chỉ các class mới, cả toàn
  bộ test Phase 5/Foundation cũ VÀ toàn bộ app khác (regression thật, đúng phạm vi thay đổi thực tế
  của Stage 2 — không chỉ phạm vi file `purchasing/`).
- [ ] **Bước 5**: Đối chiếu tay 1 lượt: mọi AC (1-38, mục 10 FSD) và mọi TC (mục 11 FSD) đã có ít
  nhất 1 test tương ứng trong `purchasing/tests.py` — lập bảng chéo AC/TC → tên method test tương
  ứng (không cần commit bảng này, chỉ dùng nội bộ để xác nhận không sót; nếu phát hiện thiếu, quay
  lại Task tương ứng ở Phase 1-4 bổ sung, không thêm test rời rạc ở đây).
- [ ] **Bước 6: Commit**
```bash
git add purchasing/tests.py
git commit -m "test(pur): final cross-check gaps - required_date, multi-product PO build, PR delete regression"
```

---

# Lịch sử review kế hoạch

## v1 → v2 (review lần 1, trước khi bắt đầu TDD)

6 vấn đề chặn (NO-GO) + nhiều điểm phụ được phát hiện khi đối chiếu plan v1 với codebase thật, đã
sửa hết trong v2:

1. **Trình tự Phase 1 không chạy được** — Task 1.1-1.5 (v1) tách nhỏ theo model nhưng hoãn
   `makemigrations` đến task cuối; `manage.py test` build DB test từ migration nên mọi "Bước 4: xác
   nhận PASS" ở giữa sẽ FAIL vì thiếu cột. Sửa: gộp thành 1 Task 1.1 nguyên tử (test → model → 1
   migration `0017` → migrate → test PASS → 1 commit); Task 1.6 cũ đổi số thành Task 1.2.
2. **Quyền `ExchangeRate` sai kiến trúc RBAC 2 lớp**: cú pháp template gọi method có tham số không
   hợp lệ (Django template không hỗ trợ) — sửa dùng context-processor flag
   `can_view_menu_exchange_rate`; view chỉ kiểm role/superuser, thiếu AND với `can_view_menu` — sửa
   decorator; `sync_roles()` không cấp quyền hiệu lực cho user hiện có (`DirectPermissionsBackend`
   không cộng dồn quyền từ Group) VÀ permission mới không tồn tại kịp lúc `RunPython` cần nó (thời
   điểm `post_migrate` tạo Permission) — sửa bằng migration `accounts/migrations/
   0017_exchange_rate_menu_permission.py` gọi `create_permissions()` thủ công (cách xử lý khi
   migration phải dùng permission mới trước thời điểm `post_migrate`) rồi `.add()` permission cho
   Admin hiện có; thêm test Admin bị thu hồi quyền phải nhận 403 (Task 3.8).
3. **Thiếu chặn `currency=VND` trên `ExchangeRate`** — chỉ có validate ngày tương lai. Sửa: thêm
   `CheckConstraint('exchange_rate_currency_not_vnd')` (Task 1.1) + `clean_currency()` ở form (Task
   3.8), 2 lớp.
4. **`map_non_catalog_item()` tạo dữ liệu tự vi phạm `clean()`** — set `product` nhưng không xoá 3
   field non-catalog, vi phạm chính ràng buộc XOR của `PurchaseRequestItem.clean()` (Task 2.6). Sửa:
   xoá cả 3 field non-catalog trong cùng `save(update_fields=[...])` (Task 2.7).
5. **Lock order xen kẽ ở 2 hàm batch allocation** — `build_po_from_allocations()` (Task 2.5) và
   `delete_draft_po_item_with_allocations()` (Task 2.3) gọi lại `create_allocation()`/
   `release_allocation()` công khai trong vòng lặp, mỗi lần gọi tự khoá `PurchaseRequestItem` theo
   thứ tự caller/allocation-pk thay vì "toàn bộ theo pk PR-item tăng dần" — 2 giao dịch song song xử
   lý cùng tập PR-item khác thứ tự tương đối có thể deadlock thật trên chính bảng
   `PurchaseRequestItem`. Sửa: tách `_create_allocation_locked`/`_release_allocation_locked` (hàm
   nội bộ không tự khoá) — 2 hàm batch khoá TOÀN BỘ `PurchaseRequestItem` liên quan theo pk 1 lần
   trước, rồi gọi hàm nội bộ lần lượt.
6. **Test fixture `check_non_catalog_sla`** (Task 4.2) sai 3 chỗ độc lập: field `Approval` dùng sai
   tên (`content_type`/`object_id` thay vì `target_type`/`target_id`); backdate `submitted_at`
   (auto_now_add=True) qua `.create()` không có tác dụng (Django luôn ghi đè bằng `now()` trong
   `save()`) — phải backdate qua `.update()`; tính "N ngày trước" bằng ngày lịch thay vì ngày làm
   việc khiến kết quả test phụ thuộc đúng ngày chạy — sửa dùng `reference_date` cố định + 2 ca cố
   tình bắc qua cuối tuần thật. Nhân tiện sửa luôn bare `.date()` trên `submitted_at` (aware UTC)
   trong `overdue_non_catalog_items()` thành `timezone.localtime(...).date()`.

Điểm phụ (không chặn nhưng đã sửa cùng đợt): JS prefill `budget_category` (Task 3.2) chỉ bind
`<select>` có sẵn lúc tải trang, không hoạt động với dòng formset thêm động — sửa dùng event
delegation trên `document`; guard PK trùng ở `po_update` (Task 3.7) bỏ qua `deleted_forms`, lọt
trường hợp 1 pk xuất hiện ở cả form giữ lại lẫn form đánh dấu xoá — sửa quét toàn bộ form trước khi
tách nhánh; `PrItemMapProductForm` (Task 3.5) thiếu field `new_product_category` dù "Quyết định cụ
thể hoá" đã ghi rõ yêu cầu — thêm field + bắt buộc cùng nhóm; test concurrency delete-vs-create
(Task 5.2, TC-04-003) chỉ assert `ValidationError`, bỏ sót nhánh thắng-thua tạo ra
`PurchaseOrderItem.DoesNotExist` — sửa assert chấp nhận cả 2; Bước 4 Task 5.4 đổi từ `manage.py test
purchasing` sang `manage.py test` (toàn bộ project) vì Stage 2 sửa cả `accounts/`.

Không có thay đổi nào ở review lần 1 đụng tới `docs/pur/02_stage2_fsd.md` (đã Approved v6) — toàn bộ
là quyết định cụ thể hoá ở tầng implementation, đúng như user đã chốt khi giao review lần này.

## v2 → v3 (review chốt, trước khi bắt đầu TDD)

Đã xử lý đủ 9 mục của review lần 2: sửa thứ tự TDD/migration `ExchangeRate`; khôi phục an toàn
`app_config.models_module` và dùng đúng database alias; đồng bộ quyền map non-catalog theo `pr +
catalog`/đúng phòng ban; làm test duplicate-PK thực sự đi qua nhánh duplicate; chặn supplier không
ACTIVE và `unit_price` âm; bỏ query/crash trong `ProductSelectWithCategory`; sửa đúng đường dẫn base
template; và mô tả đúng `UniqueConstraint`/`CheckConstraint`.

Review chốt v3 bổ sung ba hiệu chỉnh cục bộ, không đổi phạm vi FSD: đưa
`can_map_non_catalog()` về Task 3.3 để từng Task vẫn PASS độc lập khi triển khai tuần tự; ghi rõ stub
`schema_editor` cho test trực tiếp migration permission; và chặn mã Product trùng/race đồng thời giữ
lại tên non-catalog trước khi service xoá snapshot nhập tay để success message không bị rỗng.

**Kết quả review chốt: GO — Approved v3 bởi luckyhoang1988 ngày 03/08/2026.** Không thay đổi
`docs/pur/02_stage2_fsd.md` (Approved v6); từ đây được phép bắt đầu Phase 1/Task 1.1 theo TDD.

---

# Bàn giao

Kế hoạch có **30 Task** (Phase 1: 2 — gộp từ 5 task riêng ở v1 thành 1 task schema nguyên tử sau khi
review phát hiện lỗi trình tự migration, xem Task 1.1 — cộng 1 task backfill; Phase 2: 12, Phase 3:
9, Phase 4: 3, Phase 5: 4 — kể cả Task 3.9 xác minh không sinh code mới), mỗi Task tự chứa đủ
file/giao diện/bước TDD để 1 kỹ sư không biết gì về codebase vẫn triển khai được. Thứ tự bắt buộc
theo dependency: Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 (trong mỗi Phase, thứ tự Task đã
tính theo phụ thuộc — vd Task 2.3 cần Task 2.2, Task 3.7 cần Task 2.1-2.3).

**Không thực thi Task nào cho tới khi kế hoạch này được duyệt** (giữ đúng gate của skill
`viet-ke-hoach-trien-khai`/`brainstorming-thiet-ke`) — sau khi duyệt, 2 lựa chọn:

1. **Thực thi tuần tự trong phiên hiện tại** (`thuc-thi-va-tdd`) — làm từng Task, dừng lại review
   sau mỗi Task hoặc mỗi vài Task liền mạch (theo yêu cầu "break into steps" đã có từ trước).
2. **Dispatch sub-agent cho từng Task/nhóm Task độc lập** (`phan-viec-song-song-agent`) — chỉ hợp
   lý cho các Task KHÔNG phụ thuộc nhau trong cùng 1 Phase (vd Task 4.1/4.2 độc lập nhau; Task
   3.1/3.8 độc lập nhau) — đa số Task còn lại phụ thuộc tuần tự chặt nên ít cơ hội song song thật
   sự trong kế hoạch này.

Trước khi thực thi trên nhánh chính: cân nhắc `cach-ly-khong-gian-lam-viec` (nhánh/worktree riêng)
nếu muốn tách khỏi `docs/pur-stage2-fsd` hiện tại — nhánh này đã chứa 2 commit FSD (approve v6 +
sửa quyết định #18), có thể tiếp tục dùng cho toàn bộ Stage 2 hoặc tách nhánh code riêng tuỳ ý
người dùng.
