# PUR Expansion — 03. Implementation Plan Stage 2 (Epic B, PUR-PR-01..07)

> Nguồn: `docs/pur/02_stage2_fsd.md` (**Approved v6**, duyệt bởi luckyhoang1988 ngày 03/08/2026).
> Trạng thái: **Approved — v3**.
> Trạng thái triển khai: **Hoàn thành cả 5 Phase** (xem `docs/pur/02_stage2_fsd.md` để biết chi
> tiết kết quả) — checkbox Task trong file này giữ nguyên `- [ ]` theo đúng convention đã chốt
> (không tick lại sau khi xong, xem git log/`purchasing/tests.py` là nguồn xác nhận thật).
> Người duyệt: **luckyhoang1988** | Ngày duyệt: **03/08/2026**.
> Được phép bắt đầu triển khai tuần tự theo TDD và các gate trong plan này.
> Quy ước: mọi tham chiếu `mục X` trong file này là mục của `02_stage2_fsd.md`, trừ khi ghi rõ khác.
> **Bản rút gọn** (2026-08-04): đã bỏ toàn bộ code snippet RED/GREEN của 30 Task (code thật
> xem file liệt kê ở mục **File:** của từng Task) để giảm chi phí đọc — bản đầy đủ (nguyên bản
> trước khi rút gọn, dùng khi cần tra lại đúng snippet TDD từng bước) lưu tại
> `docs/pur/archive/03_stage2_implementation_plan.md`.

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** —
  `manage.py test purchasing.tests.PurchaseRequestFieldsTest purchasing.tests.PurchaseRequestItemFieldsTest purchasing.tests.ExchangeRateModelTest purchasing.tests.ProcurementAllocationModelTest -v 2`,
  kỳ vọng: `TypeError: ... unexpected keyword argument 'cost_center'` và
  `NameError: name 'ExchangeRate'/'ProcurementAllocation' is not defined` (field/model chưa tồn tại).
- [ ] **Bước 3: Viết toàn bộ code model tối thiểu để PASS**

  3a. Thêm import ở đầu `purchasing/models.py` (kiểm tra trước, tránh trùng):
  `from django.db.models import Q` và `from accounts.models import User` nếu chưa có.

  3b. Thêm module-level, trước class `PurchaseOrder`:

  3c. Sửa `class PurchaseRequest` — thêm 3 field mới (sau field `note`, trước `status`):

  3d. Sửa `class PurchaseRequestItem` — sửa field `product` hiện có (thêm `null=True, blank=True`,
  giữ nguyên `on_delete=models.PROTECT`), thêm field mới ngay sau `qty_requested`, thêm property ở
  cuối class (sau `__str__`):

  3e. Đăng ký Admin (`purchasing/admin.py`, thêm sau `PurchaseRequestAdmin`, mở rộng import hiện có
  từ `.models`):
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
  (`import_module('purchasing.migrations.0018_...')` — tên module bắt đầu bằng số nên Python
  `import_module` với chuỗi vẫn hoạt động dù cú pháp `import` thường không cho phép tên bắt đầu
  bằng số; dự án đã dùng đúng cách này ở test khác — xem `from importlib import import_module` đã
  có sẵn ở đầu `purchasing/tests.py`.)
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ModuleNotFoundError` (file migration chưa tồn tại).
- [ ] **Bước 3: Viết code tối thiểu để PASS**
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5**: viết thêm `test_TC_PUR_MIG_003_rerun_is_idempotent` (gọi `_run_backfill()` 2 lần
  liên tiếp, đếm `ProcurementAllocation.objects.count()` không đổi giữa 2 lần) — chạy FAIL trước
  (nếu thiếu check `.exists()` ở trên sẽ tạo trùng), xác nhận code Bước 3 đã có guard
  `if ... .exists(): continue` nên PASS ngay không cần sửa thêm.
- [ ] **Bước 6: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError: cannot import name 'create_allocation'`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa 2 dòng import đầu `purchasing/services.py`
  (KHÔNG thêm dòng import mới trùng module — gộp vào 2 dòng đã có sẵn):
  rồi thêm 2 hàm mới (sau `find_duplicate_po_products`, trước `sync_po_status` — nhóm cùng các hàm
  PO-level): 1 hàm nội bộ `_create_allocation_locked` **giả định caller đã khoá `po`/`po_item`/
  `pr_item` theo đúng thứ tự chung** (không tự `select_for_update()`), và `create_allocation` công
  khai — chỉ khoá rồi gọi hàm nội bộ. Tách riêng phần validate/ghi dữ liệu khỏi phần khoá là để Task
  2.5 (`build_po_from_allocations`) gọi lại đúng phần validate/ghi này cho **nhiều cặp** mà không
  phải khoá lại `PurchaseRequestItem` xen kẽ từng cặp một (xem lý do đầy đủ ở Task 2.5):
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Viết tiếp test cho AC #6/#7/#15 (TDD lặp lại — mỗi test 1 chu trình Đỏ→Xanh riêng,
  code Bước 3 ở trên đã đủ để cả 3 PASS ngay vì mọi nhánh validate đã có sẵn, không cần sửa thêm)**:
- [ ] **Bước 6: Chạy test, xác nhận PASS**
- [ ] **Bước 7: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — cùng lý do tách hàm nội bộ như Task 2.1 (Task 2.3
  `delete_draft_po_item_with_allocations` cần khoá TOÀN BỘ `PurchaseRequestItem` liên quan theo pk
  TRƯỚC, rồi TOÀN BỘ `ProcurementAllocation` theo pk, thay vì xen kẽ PRItem→Allocation→PRItem→
  Allocation cho từng allocation một — xen kẽ là lỗi thứ tự khoá đã phát hiện ở review, xem Task
  2.3), thêm sau `_create_allocation_locked`/`create_allocation`:
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Thêm test AC #7 nhánh `release_allocation` (PO `APPROVED` chặn) + AC #13 dòng
  full-release (dùng `delete_empty_po_item=True` mặc định, xoá hẳn dòng)**:
- [ ] **Bước 6: Chạy test, xác nhận PASS**
- [ ] **Bước 7: Commit**

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
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `send_po()` hiện tại không raise gì (không có guard),
  PO chuyển `SENT` thành công, test fail ở `assertRaises`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa hàm `send_po()` hiện có, thêm guard NGAY SAU
  dòng `if po.status != PurchaseOrder.Status.APPROVED: raise ...` và TRƯỚC dòng
  `po.status = PurchaseOrder.Status.SENT`:
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**

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
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Thêm test AC #19 (case "1 PR → n PO")**
- [ ] **Bước 6: Chạy test, xác nhận PASS**
- [ ] **Bước 7: Viết test đang FAIL (Review lần 3, mục phụ #1 — TC-PUR-PR-05-028: chặn supplier
  không ACTIVE)**. Form-level (`PurchaseOrderForm`, `purchasing/forms.py`) đã lọc queryset
  `status=ACTIVE` cho luồng `po_create` thường, nhưng `build_po_from_allocations` nhận `supplier`
  thẳng làm tham số — không đi qua form đó — nên phải tự re-validate độc lập, đúng convention
  "form lọc, service phải tự kiểm tra lại" đã áp dụng cho các constraint khác trong file này.
- [ ] **Bước 8: Chạy test, xác nhận FAIL** — supplier `INACTIVE` vẫn tạo PO bình thường vì hàm
  chưa kiểm tra `status`.
- [ ] **Bước 9: Sửa code tối thiểu để PASS** — thêm ngay sau check `allocation_requests` rỗng ở
  Bước 3 (trước dòng `po = PurchaseOrder.objects.create(...)`):
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
- [ ] **Bước 12: Chạy test, xác nhận FAIL** — `unit_price=-100` vẫn tạo `PurchaseOrderItem`/`PO`
  bình thường vì hàm chưa kiểm tra dấu.
- [ ] **Bước 13: Sửa code tối thiểu để PASS** — thêm ngay sau check `unit_price is None` ở Bước 3
  (trước dòng `po_item_by_product[product_id] = PurchaseOrderItem.objects.create(...)`):
- [ ] **Bước 14: Chạy lại toàn bộ test của class, xác nhận PASS**
- [ ] **Bước 15: Commit**

## Task 2.6: `PurchaseRequestItem.clean()` (XOR non-catalog/product + budget_category fallback) + fix `__str__`

**File:**
- Sửa: `purchasing/models.py` (method `clean()` mới + sửa `__str__` trên `PurchaseRequestItem`)
- Test: `purchasing/tests.py` (class mới `PurchaseRequestItemCleanTest`)

**Giao diện:**
- Cung cấp: `PurchaseRequestItem.clean()` — được `ModelForm.full_clean()` gọi tự động qua
  `PurchaseRequestItemForm` (Task 3.2), không gọi trực tiếp từ view.

- [ ] **Bước 1: Viết test đang FAIL (AC #2, TC-PUR-PR-01-003/005/006)**
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `clean()` hiện tại là `Model.clean()` mặc định (no-op),
  `__str__` hiện tại crash `AttributeError` khi `product is None` (`self.product.product_code`).
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thay `__str__` và thêm `clean()` trên
  `PurchaseRequestItem`:
  (Import `re` ở đầu file thay vì trong hàm — dọn lại khi hoàn thiện, đặt tạm trong hàm ở bước này
  chỉ để tối thiểu hoá diff, sửa lại vị trí import trước khi commit ở Bước 4.)
- [ ] **Bước 4: Dọn import `re` lên đầu `purchasing/models.py`, chạy lại test, xác nhận PASS**
- [ ] **Bước 5: Commit**

## Task 2.7: `map_non_catalog_item(pr_item, product, actor, ip_address=None)`

**File:**
- Sửa: `purchasing/services.py`
- Test: `purchasing/tests.py` (class mới `MapNonCatalogItemTest`)

**Giao diện:**
- Cung cấp: `map_non_catalog_item(...) -> PurchaseRequestItem` — dùng bởi Task 3.5
  (`pr_item_map_product` view, sau khi view đã resolve/tạo `Product`).

- [ ] **Bước 1: Viết test đang FAIL (mục 4 điểm 10, TC-PUR-PR-06-002/003)**
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bổ sung sau review code (thực hiện sau khi triển khai, không nằm trong RED ban đầu)**:
  `test_TC_PUR_PR_06_005/006/007` ở trên được thêm ở 1 vòng review riêng, sau khi `map_non_catalog_item()`
  đã commit — xem "Lỗi đã sửa (review sau khi triển khai)" bên dưới.
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
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Lỗi đã sửa (review sau khi triển khai, code ở Bước 3 trên đã chốt commit
  `e2c767d`/`35aa449` — 2 vấn đề Quan trọng phát hiện lúc review Task 3.5)**:
  1. Guard `if pr_item.purchase_request.status == PurchaseRequest.Status.DRAFT` chỉ chặn đúng
     `DRAFT` — `REJECTED` không phải `DRAFT` nên vẫn map thành công, trong khi hệ thống cho phép
     `REJECTED → DRAFT` (`reopen_purchase_request()`), sau đó Requester lại sửa/xoá dòng tự do,
     tái tạo đúng nguy cơ "Product rác" mà rule mục 4 điểm 10 muốn tránh. Sửa bằng **allow-list**
     thay vì deny-list:
  2. Hàm không tái kiểm `product.is_active` — form ở Task 3.5 chỉ hiển thị Product đang hoạt động,
     nhưng đó là lớp UX (đúng pattern "Form querysets filter, services must re-validate
     independently" trong CLAUDE.md), không chặn được caller khác truyền Product inactive, hoặc
     Product bị deactivate (qua `QuerySet.update()`) sau khi form validate nhưng trước khi service
     chạy (TOCTOU). Sửa bằng cách khoá và đọc lại `product` từ DB ngay trong service, trước khi
     gán:
  3 test mới ở Bước 1 (`TC-PUR-PR-06-005/006/007`) đi kèm 2 sửa trên — RED trước (guard cũ cho map
  qua, product cũ vẫn active-check thiếu) rồi GREEN sau khi áp 2 đoạn code trên vào đúng vị trí
  trong thân hàm ở Bước 3.
- [ ] **Bước 5: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS**:
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `TypeError: unexpected keyword argument
  'qty_approved_overrides'` (4 test đầu); test cuối (`test_override_with_long_non_catalog_name_...`)
  báo lỗi tương tự trước khi tới được assertion — không tự động chứng minh gì về overflow ở bước
  này, giá trị của nó chỉ phát huy ở Bước 4 sau khi code đã tồn tại.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa chữ ký hàm và nội dung `on_approve()` nhánh
  `else` (cấp `PENDING_PUR`) của `decide_purchase_request` hiện có. **Lưu ý bắt buộc:** description
  KHÔNG được nhúng `str(item)`/giá trị tự do trực tiếp — `non_catalog_name` dài tới 200 ký tự có thể
  vượt `AuditLog.description` (`max_length=255`) và gây `StringDataRightTruncation` rollback cả
  transaction duyệt (cùng lớp lỗi đã sửa ở Task 2.8 `cancel_pr_item_open_qty`) — dùng `changes=`
  (JSONField, không giới hạn) cho chi tiết từng dòng thay vì nối chuỗi vào `description`:
  (Toàn bộ phần khác của hàm giữ nguyên — chỉ thay chữ ký + nội dung nhánh `else` trong
  `on_approve()`; nhánh `PENDING_DEPT` và `on_reject()`/phần gọi `decide_approval()` không đổi.)
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5**: chạy lại TOÀN BỘ test cũ của `decide_purchase_request` đã có trong `tests.py`
  trước Stage 2 (không được đổi hành vi mặc định khi không truyền `qty_approved_overrides`) —
  xác nhận vẫn PASS nguyên trạng (regression).
- [ ] **Bước 6: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code ĐẦY ĐỦ để PASS cả 6 test trên cùng lúc** — thêm vào cuối nhóm hàm
  allocation trong `purchasing/services.py`:
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
  (Cần import `Grn`, `GrnItem` đã có sẵn ở đầu `purchasing/tests.py`.)
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm vào `purchasing/services.py` (ngay sau
  `received_qty_by_product`, vì cùng nhóm "tính received"):
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**

## Task 2.12: `submit_purchase_request()` — set `department_snapshot`

**File:**
- Sửa: `purchasing/services.py` (hàm `submit_purchase_request` đã có — thêm 1 dòng, KHÔNG viết
  hàm mới)
- Test: `purchasing/tests.py` (class mới `SubmitPurchaseRequestDepartmentSnapshotTest`)

**Giao diện:**
- Không đổi chữ ký hàm — chỉ thêm side-effect set `pr.department_snapshot`.

- [x] **Bước 1: Viết test đang FAIL (TC-PUR-PR-02-001/002)**
- [x] **Bước 2: Chạy test, xác nhận FAIL** — `department_snapshot` vẫn rỗng sau submit.
- [x] **Bước 3: Viết code tối thiểu để PASS** — thêm 2 dòng vào `submit_purchase_request()` hiện
  có, ngay sau khối `if origin_department and ...: ... else: ...` (trước
  `pr.save(update_fields=['status'])`):
  (Thay dòng `pr.save(update_fields=['status'])` hiện có bằng dòng trên — gộp 2 field cùng 1 lần
  save, không thêm lệnh `save()` thứ hai.)
- [x] **Bước 4: Chạy test, xác nhận PASS**
- [x] **Bước 5: Commit**

**Lỗi đã sửa (review phát hiện, sau khi commit ở trên)**: code ban đầu gán vô điều kiện
`pr.department_snapshot = origin_department` mỗi lần gọi — đúng cho lần nộp đầu, nhưng PR có thể
đi `REJECTED -> reopen_purchase_request() -> DRAFT -> submit_purchase_request()` lần 2; nếu
`requested_by.department` đổi giữa 2 lần nộp, lần nộp lại sẽ ghi đè snapshot, trái với chính
help_text của field ("bất biến sau khi set"). `TC-PUR-PR-02-002` không bắt được lỗi này vì nó chỉ
đổi `department` rồi đọc lại PR mà không nộp lại thật. Thêm `test_TC_PUR_PR_02_003` (submit → reject
→ reopen → đổi department → submit lại → snapshot vẫn giữ giá trị lần đầu), xác nhận FAIL đúng lý
do (`'QC' != WAREHOUSE`), rồi sửa thành `if not pr.department_snapshot: pr.department_snapshot =
origin_department` (set-once, không set lại nếu đã có giá trị).

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `cost_center` không nằm trong `Meta.fields` nên
  `form.errors` không có key đó (form coi như thừa field, không báo lỗi thiếu).
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa `Meta.fields` của `PurchaseRequestForm`:
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5**: mở `purchasing/templates/purchasing/pr_form.html`, xác nhận template render form
  bằng vòng lặp field tổng quát (không liệt kê tay từng field) — nếu template LIỆT KÊ TAY từng
  field (kiểm tra thực tế trước khi giả định), thêm 2 dòng `{{ form.cost_center }}`/
  `{{ form.project }}` vào đúng vị trí cạnh `warehouse`/`note`.
- [ ] **Bước 6: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `TypeError`/`ValueError` do `Meta.fields` chưa có các
  field mới, hoặc `product` vẫn bị coi bắt buộc (form hiện tại không cho `product=''`).
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa `PurchaseRequestItemForm`:
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
  Đây là `TypeError`, không phải `Product.DoesNotExist`, nên khối `except Product.DoesNotExist:
  pass` hiện có KHÔNG bắt được — lỗi văng thẳng lên, render form `product` (bất kỳ field nào dùng
  `ProductSelectWithCategory` với ≥1 lựa chọn thật) sẽ crash toàn bộ trang, không chỉ chậm.
  Sửa bằng TDD:
  - [ ] **Bước 7: Viết test đang FAIL**
  - [ ] **Bước 8: Chạy test, xác nhận FAIL** — chạy
    `python manage.py test purchasing.tests.PurchaseRequestItemFormTest.test_product_select_with_category_widget_renders_without_error -v 2`,
    kỳ vọng: `TypeError: Field 'id' expected a number but got <django.forms.models.ModelChoiceIteratorValue ...>`
    (văng ra từ bên trong `create_option()`, không phải một `AssertionError` thông thường).
  - [ ] **Bước 9: Sửa code tối thiểu để PASS** — thay `ProductSelectWithCategory` ở Bước 5 bằng:
    (`value.instance` là object `Product` Django đã fetch sẵn khi lặp qua
    `self.fields['product'].queryset` để dựng danh sách `<option>` — không cần, và không được,
    query lại. `value` cho lựa chọn "---------" mặc định là chuỗi rỗng `''`, không có thuộc tính
    `instance`, nên `getattr(..., None)` trả `None` và bị bỏ qua đúng như mong muốn — không cần
    try/except nữa.)
  - [ ] **Bước 10: Chạy lại test, xác nhận PASS** — đúng 1 query (fetch `queryset.all()` một lần
    khi dựng choices), không phát sinh thêm query nào theo số `<option>`, và không còn crash dù có
    bao nhiêu sản phẩm.
- [ ] **Bước 11: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `NoReverseMatch` (route chưa tồn tại).
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm vào `purchasing/views.py` (sau
  `can_manage_pur_pr`):
  Thêm view (sau `pr_delete` hoặc cuối file, nhóm cùng các view PR-item-level):
  Thêm import `cancel_pr_item_open_qty`, `PurchaseRequestItem` (nếu `PurchaseRequestItem` chưa
  import trực tiếp ở đầu `views.py` — hiện chỉ import `PurchaseOrder`, `PurchaseRequest`, cần mở
  rộng dòng `from .models import PurchaseOrder, PurchaseRequest` thành có thêm
  `PurchaseRequestItem`).
- [ ] **Bước 4**: Thêm route vào `purchasing/urls.py`:
- [ ] **Bước 5: Chạy test, xác nhận PASS**
- [ ] **Bước 6: Sửa `pr_detail.html`** — mở rộng bảng "Chi tiết yêu cầu" (dòng 125-142 hiện tại)
  thêm 6 cột số liệu mới (`qty_approved`/`qty_allocated`/`qty_ordered`/`qty_received`/
  `qty_cancelled`/`qty_open` — cộng với cột `qty_requested` sẵn có thành đủ 7 số của mục 5) + cột
  badge/nút map non-catalog + cột nút huỷ:
  (Nút "Huỷ phần còn mở" ở bản tối thiểu này huỷ TOÀN BỘ `qty_open` với lý do cố định — nếu cần
  huỷ 1 phần + lý do tự nhập, thay bằng modal/form riêng có input `qty`/`reason` — ghi chú lại đây
  làm quyết định UX tối thiểu cho Task này, có thể mở rộng sau không phải blocker.)
  Thêm helper quyền ngay trong **Task 3.3** (không chờ Task 3.5, vì Task 3.3 phải test/commit PASS
  độc lập trước khi sang Task kế tiếp), đặt cạnh `can_decide_pr`/`can_manage_pur_pr` trong
  `purchasing/views.py`:
  Sau đó thêm context vào view `pr_detail` (mục `return render(...)`, thêm 2 key mới). **Sửa theo
  review lần 3**: dùng chung `can_map_non_catalog()` thay vì lặp lại điều kiện
  role/permission rời rạc — tránh 2 nơi cùng biểu diễn một rule mà lệch nhau (đúng lỗi bị review lần
  3 phát hiện: bản v2 viết riêng ở đây, thiếu `can_view_menu('catalog')` và ràng buộc phòng ban):
- [ ] **Bước 7: Test thủ công trên trình duyệt** — xác nhận 7 số hiển thị đúng, nút chỉ hiện đúng
  người có quyền.
- [ ] **Bước 8: Commit**

## Task 3.4: `pr_approve` — sửa `qty_approved` từng dòng khi duyệt cấp `PENDING_PUR`

**File:**
- Sửa: `purchasing/views.py` (view `pr_approve` đã có)
- Sửa: `purchasing/templates/purchasing/pr_detail.html` (form Duyệt dòng 49-54 + thêm cột trong
  bảng item đã sửa ở Task 3.3)
- Test: `purchasing/tests.py` (class mới `PrApproveQtyOverrideViewTest`)

**Giao diện:**
- Sử dụng: `decide_purchase_request(..., qty_approved_overrides=...)` (Task 2.9).

- [ ] **Bước 1: Viết test đang FAIL**
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `qty_approved` giữ nguyên `None`/mặc định thay vì 6
  (view hiện tại không đọc field POST mới).
- [ ] **Bước 3: Viết code tối thiểu để PASS** — sửa view `pr_approve` hiện có, chèn logic đọc
  `qty_approved_overrides` ngay trước khối `try:`:
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Sửa template** — thêm `id="pr-approve-form"` vào `<form>` ở dòng 50, để input số
  lượng nằm TRONG bảng item (Task 3.3) vẫn liên kết được với form này qua thuộc tính HTML5 `form=`
  (không cần lồng bảng vào trong `<form>`):
  Thêm 1 cột vào bảng item (nối tiếp cột "Đã duyệt" đã có ở Task 3.3, CHỈ hiện khi
  `obj.status == 'PENDING_PUR' and can_approve` — thay `<td>{{ item.qty_approved|default:"—" }}</td>`
  bằng:
- [ ] **Bước 6: Test thủ công trên trình duyệt** — xác nhận sửa số ở `PENDING_PUR` rồi bấm "Duyệt"
  áp dụng đúng giá trị đã sửa; ở `PENDING_DEPT` không hiện input (chỉ hiện số `—`).
- [ ] **Bước 7: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `NoReverseMatch`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm form (`purchasing/forms.py`, cuối file):
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
  Import thêm `IntegrityError` từ `django.db`. Việc lưu `original_non_catalog_name` trước khi gọi
  service là bắt buộc vì `map_non_catalog_item()` xoá các field non-catalog trong cùng transaction;
  đọc `item.non_catalog_name` sau lời gọi sẽ chỉ còn chuỗi rỗng trong success message.
  Template `pr_item_map_product.html` (form đơn giản, mirror bố cục `pr_form.html` — 1 card,
  `{{ form.as_p }}` hoặc render tay từng field theo pattern Bootstrap đã dùng khắp dự án, nút Lưu +
  Huỷ quay lại `pr_detail`).
- [ ] **Bước 4**: Thêm route:
- [ ] **Bước 5: Chạy test, xác nhận PASS**
- [ ] **Bước 6: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `NoReverseMatch`.
- [ ] **Bước 3: Viết code tối thiểu để PASS** — thêm vào `purchasing/views.py` (đầu file thêm
  `from decimal import Decimal, InvalidOperation`; view đặt sau `po_create`):
  Thêm import ở đầu `purchasing/views.py`: mở rộng `from .models import PurchaseOrder,
  PurchaseRequest` thành thêm `PurchaseRequestItem`; mở rộng
  `from .services import (...)` thêm `build_po_from_allocations`.
- [ ] **Bước 4**: Thêm route (`purchasing/urls.py`):
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
- [ ] **Bước 8: Chạy test, xác nhận FAIL** — supplier `INACTIVE` vẫn tạo PO bình thường (view
  chưa kiểm tra `status`, chỉ có `get_object_or_404` tra theo `pk`).
- [ ] **Bước 9: Sửa code tối thiểu để PASS** — thay đoạn code ở Bước 3 (từ dòng `error = None` tới
  hết vòng lặp `for item in eligible_items:`) bằng:
- [ ] **Bước 10: Chạy lại toàn bộ test của class, xác nhận PASS**
- [ ] **Bước 11: Test thủ công trên trình duyệt** — từ `pr_detail`, bấm "Tạo PO từ yêu cầu này",
  xác nhận dòng của PR đó được pre-check, chọn thêm dòng từ PR khác cùng lúc, submit tạo PO đúng.
- [ ] **Bước 12: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `po_update` hiện tại dùng `PurchaseOrderItemFormSet`
  (không `disabled`), mọi test tamper đều PASS sai (giá trị bị ghi đè) hoặc tạo dòng mới thành công.
- [ ] **Bước 3: Viết code tối thiểu để PASS**. Thêm form/formset (`purchasing/forms.py`, sau
  `PurchaseOrderItemFormSet` hiện có):
  Thêm helper + viết lại `po_update` (`purchasing/views.py`):
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
- [ ] **Bước 6: Test thủ công trên trình duyệt** — mở `po_update` của 1 PO `FROM_PR` `DRAFT`,
  xác nhận field `product`/`qty_ordered` hiển thị disabled (không sửa được qua UI thường), sửa
  `unit_price` lưu được bình thường, xoá 1 dòng hoạt động đúng qua
  `delete_draft_po_item_with_allocations`.
- [ ] **Bước 7: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `NoReverseMatch`.
- [ ] **Bước 3: Sửa permission/form/view — viết code tối thiểu để PASS.** Sửa
  `accounts/permissions.py`:
  (Không sửa `all_menu_codenames()` — vẫn liệt kê đủ mọi codename kể cả `exchange_rate`, chỉ
  `codenames_for_role` đổi ai được CẤP mặc định.)

  Thêm form (`purchasing/forms.py`, cuối file — `Currency` import từ `.models` nếu chưa có sẵn từ
  form PR ở Task 3.2):
  (Validate ở form đây là lớp UX — `CheckConstraint('exchange_rate_currency_not_vnd')` đã thêm ở
  Task 1.1 là lớp chặn thật ở DB, phòng đường ghi trực tiếp qua service/shell/Admin.)

  Thêm view (`purchasing/views.py`, nhóm riêng cuối file). **Quyết định cụ thể hoá (review phát
  hiện thiếu)**: decorator PHẢI kiểm tra CẢ role/superuser LẪN `can_view_menu('exchange_rate')` —
  chỉ kiểm role/superuser thì một Admin bị thu hồi riêng quyền `can_view_menu_exchange_rate` qua
  trang "Phân quyền chi tiết" (`user_permission_edit`) vẫn thao tác được, cùng lỗi dạng
  `can_transfer_inventory` từng gặp ở module `inventory` (xem CLAUDE.md mục "can_view_menu(key) alone
  only gates 'view'..."):
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
  Viết test riêng cho migration này trong `accounts`-side hoặc tái dùng
  `ProcurementAllocationModelTest`-style: tạo 1 Admin TRƯỚC khi gọi
  `create_permissions_now`/`grant_exchange_rate_menu_to_existing_admins` (gọi trực tiếp 2 hàm qua
  `import_module`, cùng kỹ thuật `importlib.import_module` đã dùng ở Task 1.2/migration `0018`),
  xác nhận `admin_user.has_perm('accounts.can_view_menu_exchange_rate')` trả `True` sau khi chạy —
  đây là bằng chứng thực sự bám đúng bug, không chỉ tin vào code đọc mắt thường. **Không truyền
  `schema_editor=None` như migration `0018`**, vì 2 hàm này cần `schema_editor.connection.alias`;
  test truyền một stub chỉ-đọc kết nối (không mở schema editor thật trong `TestCase`):
- [ ] **Bước 5: Thêm route/template/context.** Route (`purchasing/urls.py`):
  Tạo 3 template tối thiểu (mirror bố cục các trang list/form/confirm-delete đã có trong
  `purchasing/templates/purchasing/` — vd `po_price_comparison.html` cho list, `pr_form.html` cho
  form đơn giản).

  Thêm flag context processor (`accounts/context_processors.py`, hàm `sidebar_permissions`, thêm 1
  dòng vào dict trả về — theo đúng pattern đã có cho 7 `MENU_ITEMS` còn lại, KHÔNG gọi method có
  tham số trực tiếp trong template vì Django template không hỗ trợ truyền literal argument khi
  resolve biến — đây chính là lỗi cú pháp review phát hiện ở bản v1):
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
  (Thêm `from io import StringIO` và `from django.core.management import call_command` ở đầu
  `purchasing/tests.py` nếu chưa có.)
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `management.commands` không tồn tại,
  `django.core.management.CommandError: Unknown command`.
- [ ] **Bước 3: Viết code tối thiểu để PASS.** Thêm hàm (`purchasing/services.py`, gần
  `find_duplicate_po_products`):
  Tạo `purchasing/management/__init__.py` và `purchasing/management/commands/__init__.py` (rỗng),
  rồi `purchasing/management/commands/report_allocation_migration_exceptions.py`:
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**

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
  (Cần import `Approval`, `Notification`, `ContentType`, `date`, `datetime` từ `datetime`, và
  `from unittest.mock import patch` — kiểm tra đã có sẵn ở đầu `purchasing/tests.py` hay chưa, thêm
  nếu thiếu. Patch đúng `purchasing.services.timezone.localdate` — namespace của module GỌI hàm,
  không phải `django.utils.timezone.localdate` — cùng quy ước đã ghi ở CLAUDE.md mục "grep bất kỳ
  `.date()` trần...".)
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError`.
- [ ] **Bước 3: Viết code tối thiểu để PASS.** Thêm vào `purchasing/services.py` (đầu file thêm
  `from datetime import timedelta`; import thêm `Approval`, `ContentType` nếu chưa có):
  Tạo `purchasing/management/commands/check_non_catalog_sla.py`:
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**

## Task 4.3: `reconcile_legacy_po_item_allocations` command — `--dry-run` rollback bằng savepoint thật

**File:**
- Tạo: `purchasing/management/commands/reconcile_legacy_po_item_allocations.py`
- Test: `purchasing/tests.py` (class mới `ReconcileLegacyCommandTest`)

**Giao diện:**
- Sử dụng: `reconcile_legacy_po_item_allocations` (Task 2.10). Không viết logic reconcile lần 2
  trong command — command chỉ parse tham số CLI rồi gọi đúng 1 hàm service, `--dry-run` chỉ khác ở
  chỗ CÓ rollback cuối cùng hay không (lưu ý kỹ thuật #3, xem Ràng buộc chung).

- [ ] **Bước 1: Viết test đang FAIL (AC #33, TC-PUR-PR-05-022)**
  (Thêm `from django.core.management.base import CommandError` ở đầu `purchasing/tests.py` nếu
  chưa có — `call_command` đã có sẵn nhờ Task 4.1.)
- [ ] **Bước 2: Chạy test, xác nhận FAIL** — command chưa tồn tại.
- [ ] **Bước 3: Viết code tối thiểu để PASS.** Tạo
  `purchasing/management/commands/reconcile_legacy_po_item_allocations.py`:
  (Không viết `except User.MultipleObjectsReturned` — `User.username` đã `unique=True` ở tầng DB
  từ `AbstractUser`, tình huống đó không thể xảy ra thật, thêm nhánh xử lý cho nó là error handling
  cho kịch bản không thể xảy ra, vi phạm nguyên tắc "chỉ validate ở ranh giới thật cần" — CLAUDE.md.)
- [ ] **Bước 4: Chạy test, xác nhận PASS**
- [ ] **Bước 5: Commit**

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
- [ ] **Bước 2: Chạy test, xác nhận PASS ngay** (nếu FAIL, nghĩa là constraint ở Task 1.1 sai —
  quay lại sửa `Meta.constraints` của `ProcurementAllocation`, không sửa test để né).
- [ ] **Bước 3: Commit**

## Task 5.2: Concurrency — `TransactionTestCase` + `threading.Barrier` (TC-04-002/003, TC-05-027)

**File:**
- Test: `purchasing/tests.py` (class mới `AllocationConcurrencyDeadlockTests(TransactionTestCase)`)

Mirror đúng pattern `stocktake.tests.MultiSkuLockOrderDeadlockTests`/`HandoffStocktakeDeadlockTests`
(CLAUDE.md — "first threading precedent", giờ áp dụng cho nhóm Allocation): `TransactionTestCase`
(không phải `TestCase`) vì cần 2 transaction/kết nối DB thật để tạo tranh chấp khoá thật.

- [ ] **Bước 1: Viết test**
  (Cần `import threading`, `from django.db import connection`, `from django.db.utils import
  OperationalError` ở đầu `purchasing/tests.py` nếu chưa có — `stocktake.tests` đã dùng pattern
  này, kiểm tra import tương đương.)
- [ ] **Bước 2: Chạy test, xác nhận PASS** (nếu FAIL với `OperationalError` thật — nghĩa là lock
  order ở Task 2.1/2.3/2.10 sai thứ tự so với Ràng buộc chung, quay lại sửa service, không sửa
  test để né).
- [ ] **Bước 3: Commit**

## Task 5.3: 9 kịch bản còn lại của `reconcile_legacy_po_item_allocations` (TC-05-020) + PO `APPROVED` (TC-05-021)

**File:**
- Test: `purchasing/tests.py` (thêm method vào class `ReconcileLegacyPoItemAllocationsTest` đã tạo
  ở Task 2.10 — KHÔNG tạo class trùng)

- [ ] **Bước 1: Viết đủ 10 test (9 kịch bản vi phạm + 1 case PO `APPROVED` thành công)**
- [ ] **Bước 2: Chạy toàn bộ class `ReconcileLegacyPoItemAllocationsTest` (Task 2.10 + Task 5.3),
  xác nhận PASS hết** (nếu 1 trong 9 kịch bản FAIL, quay lại sửa thứ tự/điều kiện trong hàm ở Task
  2.10 — hàm đã viết đủ 14 điều kiện ở đó nên về lý thuyết không cần sửa gì thêm, Bước này chỉ để
  xác nhận thật).
- [ ] **Bước 3: Commit**

## Task 5.4: Rà soát chéo cuối cùng — khoảng trống nhỏ còn lại + regression không đổi hành vi cũ

**File:**
- Test: `purchasing/tests.py` (thêm rải rác vào các class đã có ở trên, không tạo class mới trừ
  khi ghi rõ)

- [ ] **Bước 1**: `TC-PUR-PR-01-001` — thêm vào `PurchaseRequestItemFormTest` (Task 3.2):
- [ ] **Bước 2**: `TC-PUR-PR-05-002` (2 dòng PR khác product → 2 `PurchaseOrderItem` riêng) — thêm
  vào `BuildPoFromAllocationsTest` (Task 2.5):
- [ ] **Bước 3**: `TC-PUR-PR-07-003` (regression — không đổi so với hiện tại, xác nhận Stage 2
  không phá `delete_purchase_request`) — thêm vào `CancelPrItemOpenQtyTest` (Task 2.8) hoặc test
  class PR đã có sẵn từ trước Stage 2:
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
