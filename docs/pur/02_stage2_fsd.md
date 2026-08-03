# PUR Expansion — 02. FSD Stage 2: PR 2.0 và Allocation (Epic B, PUR-PR-01..07)

> Trạng thái: **Approved — v6, đã xử lý review lần 5 (xem mục 13)**
> Trạng thái triển khai: **Chưa viết code** — chuyển sang viết implementation plan chi tiết
> (migration → service → form/view → management command → tests) trước khi vào TDD, giống quy
> trình đã áp dụng cho `01_foundation_fsd.md`.
> Người duyệt: luckyhoang1988 (Trường Hoàng) | Ngày duyệt: 03/08/2026
> Phụ thuộc: **có** phụ thuộc `00_business_decisions.md` — khác Stage 1 Foundation (không đụng
> RFQ/Budget/currency nên miễn 19 quyết định), Stage 2 đụng trực tiếp quyết định #2 (budget key:
> cost center + category, project tuỳ chọn), #8 (ExchangeRate + `currency`/`estimated_unit_price`
> trên PR line — hạ tầng tối thiểu, **chưa** có nghiệp vụ nào tiêu thụ trong Stage 2, xem mục 0),
> #9 (owner + SLA 3 ngày làm việc cho non-catalog → Product). File này **không** đụng quyết định
> #3/#4 (budget commitment/block — Stage 3), #5/#6/#15/#16 (ngưỡng RFQ/direct-buy reason code —
> Stage 3-4), #17 (budget tolerance — Stage 3), #18 (ExchangeRate thiếu dữ liệu — tiêu thụ ở
> Stage 3), #7/#13 (RFQ evaluation/attachment — Stage 4).
> Nguồn: Epic B (`PUR-PR-01..07`, mục 7) + Stage 2 (mục 12) của `PUR_EXPANSION_MASTER_PLAN.md`,
> đối chiếu lại với code thật ngày 03/08/2026 (`purchasing/models.py`, `purchasing/services.py`,
> `catalog/models.py`, `accounts/models.py`) — không chép mô tả chung chung từ master plan.

## 0. Tóm tắt phạm vi

**Trong phạm vi** (đúng 5 gạch đầu dòng của Stage 2 trong master plan mục 12, ánh xạ sang
`PUR-PR-01..07`):

1. **Header/line lifecycle, required date, category/cost center/project** (`PUR-PR-01`,
   `PUR-PR-02`) — thêm field lên `PurchaseRequest`/`PurchaseRequestItem`, snapshot
   department/cost center bất biến tại thời điểm nộp. "category" ở đây cụ thể hoá thành field
   `budget_category` trên `PurchaseRequestItem` (mục 2.2) — đúng quyết định #2 (budget key = cost
   center + category/account); v1 review lần 1 sót field này dù đã nhắc tên trong bullet, xem mục 13.
2. **`ExchangeRate` master data + field `currency`/`estimated_unit_price` trên PR line**
   (quyết định #8) — **chỉ** xây bảng + CRUD Admin + 2 field trên PR line. **Chưa** có màn hình/
   luồng nào đọc/quy đổi tỷ giá trong Stage 2 — việc quy đổi VND để kiểm ngân sách là Stage 3, so
   sánh báo giá khác tiền tệ là Stage 4. PO tạo trong Stage 2 vẫn chỉ nhập `unit_price` thuần VND
   như hiện tại (`PurchaseOrderItem` không đổi field) — xem giới hạn đã biết ở mục 4 điểm 7.
3. **Allocation split/consolidate và open quantity** (`PUR-PR-03`, `PUR-PR-04`, `PUR-PR-05`) —
   model `ProcurementAllocation` mới, thay hẳn cách `po_create?from_pr=` hiện tại (chuyển nguyên
   1 PR thành 1 PO) bằng màn hình chọn nhiều dòng PR (từ 1 hoặc nhiều PR) để gộp/tách vào PO.
4. **Non-catalog goods intake + map Product gate** (`PUR-PR-06`, quyết định #9) — `product` trên
   `PurchaseRequestItem` chuyển thành tuỳ chọn, thêm field mô tả hàng non-catalog, chặn Allocation
   cho tới khi được map sang 1 `Product` thật.
5. **Migrate `linked_po` cũ sang allocation, giữ compatibility đọc** (`PUR-PR-07` một phần) — data
   migration backfill `ProcurementAllocation` từ `PurchaseRequest.linked_po` hiện có, giữ cột
   `linked_po` ở trạng thái deprecated/đọc-được, không xoá.

**Ngoài phạm vi** (thuộc Stage khác, không làm ở đây dù có nhắc tới trong mô tả field):

- Approval engine có version/decision-table (`PUR-APR-*`, Epic C) — **giữ nguyên luồng duyệt 2 cấp
  hiện có** (`PENDING_DEPT → PENDING_PUR → APPROVED/REJECTED`, `purchasing.services.
  submit_purchase_request`/`decide_purchase_request`), không đổi transition nào của PR header.
- Budget check/commitment/tolerance (`PUR-BUD-*`, quyết định #3/#4/#17) — Stage 3.
- RFQ/Quotation/ngưỡng bắt buộc RFQ/4 reason code direct-buy (`PUR-RFQ-*`, quyết định #5/#6/#7/
  #15/#16) — Stage 4. Trong Stage 2, **mọi** PO tạo từ PR line đều đi qua đúng 1 luồng duy nhất
  (Allocation), chưa phân biệt direct-buy hay RFQ — sự phân biệt đó chỉ có ý nghĩa từ khi Stage 3-4
  tồn tại.
- Attachment dùng chung PR/RFQ/Quotation/PO (quyết định #13) — Stage 4.
- Dashboard/KPI cho Allocation (`PUR-RPT-*`) — Stage 6/Epic F, không làm ở đây.

**Ràng buộc thiết kế bao trùm**: Stage 2 không được phá `PUR-FND-06` (1 Product tối đa 1 dòng
trong 1 PO, đã `Implemented` — xem `01_foundation_fsd.md`) — khi nhiều dòng PR cùng product được
gộp vào cùng 1 PO, chúng phải trỏ vào **cùng 1** `PurchaseOrderItem` (nhiều `ProcurementAllocation`
trỏ 1 `PurchaseOrderItem`), không được tạo 2 dòng PO trùng product.

## 1. Actor và quyền

Không thêm role/department mới. Tái dùng đúng các actor đã có:

| Actor | Việc mới trong Stage 2 |
|---|---|
| Requester (bất kỳ role nào có `create` trên `pr`) | Nhập thêm `cost_center`/`project`/dòng non-catalog/`required_date`/`currency`/`estimated_unit_price` khi tạo PR draft. |
| Quản lý phòng ban gốc (`is_department_manager`) | Không đổi — vẫn chỉ duyệt/từ chối PR ở cấp `PENDING_DEPT`, không thấy/sửa field Allocation. |
| PUR Manager (`is_department_manager('PURCHASING')` hoặc `can('approve','pr')`) | Không đổi quyền duyệt PR; **mới**: có thể chỉnh `qty_approved` từng dòng khi duyệt ở cấp `PENDING_PUR` (mục 3). |
| PUR Staff (`role` có `update` trên `pr`/`po`, thuộc `department=PURCHASING`) | **Mới**: map non-catalog line sang Product (quyết định #9), tạo Allocation/PO từ PR line đã duyệt. Huỷ phần open của 1 dòng PR (`PUR-PR-07`) **chỉ khi** đúng người được `assigned_to` trên PR đó — PUR Staff khác (dù cùng phòng ban) không được; PUR Manager (`is_department_manager('PURCHASING')`) làm được bất kể `assigned_to` (khớp đúng điều kiện chặt hơn ở mục 4 điểm 9, bảng này ở v1 diễn đạt lỏng hơn rule thật, xem mục 13). |
| Admin/Manager (`can('create','po')` trở lên, dùng chung ma trận `po` hiện có) | **Mới**: CRUD `ExchangeRate` — giới hạn thêm: chỉ Admin (`user.role == ADMIN` hoặc `is_superuser`), không mở cho Manager, vì đây là dữ liệu tài chính nhạy cảm dùng chung toàn hệ thống, khác PO/PR vốn theo phòng ban. |

`can_view_menu('pr')`/`can_view_menu('po')` hiện có (nếu có) tiếp tục áp dụng nguyên trạng cho các
màn hình PR/PO đã có; 3 màn hình mới (mục 5) dùng lại đúng permission của module gần nhất (Allocation
đi theo `po`, map-non-catalog đi theo `pr` + `catalog`, `ExchangeRate` là menu-only mới — thêm key
`exchange_rate` vào `accounts.permissions.MENU_ITEMS`, mặc định chỉ Admin nhìn thấy theo bảng trên,
không dùng default-granted-mọi-role như các menu-only khác vì đây là ngoại lệ cần khoá chặt từ đầu).

## 2. Trường dữ liệu

### 2.1 `PurchaseRequest` — thêm field

- `cost_center` — `CharField(max_length=50)`, verbose_name `'Trung tâm chi phí'`, **bắt buộc**
  (cùng mức bắt buộc như `warehouse` hiện có). Nhập tay tự do (không tạo model `CostCenter` riêng —
  YAGNI, chưa có yêu cầu nào đòi danh mục trung tâm chi phí có cấu trúc/duyệt riêng; nếu Stage 3
  cần validate theo danh sách cố định, làm lúc đó).
- `department_snapshot` — `CharField(max_length=20, choices=accounts.User.Department.choices,
  blank=True, editable=False)`, verbose_name `'Phòng ban (snapshot lúc nộp)'`. **Không** phải field
  người dùng nhập — set tự động trong `submit_purchase_request()` từ `requested_by.department` tại
  đúng thời điểm nộp, bất biến sau đó kể cả nếu `requested_by.department` đổi sau này (cùng nguyên
  tắc snapshot-bất-biến đã dùng cho `ExchangeRate`/RFQ/PO ở các quyết định #8). Rỗng khi PR còn
  `DRAFT` (chưa nộp lần nào).
- `project` — `CharField(max_length=100, blank=True)`, verbose_name `'Dự án (tuỳ chọn)'` — đúng
  quyết định #2 (field tuỳ chọn, không tham gia khoá ngân sách).

### 2.2 `PurchaseRequestItem` — thêm field, đổi `product` thành tuỳ chọn

- `product` — đổi `on_delete=models.PROTECT` giữ nguyên nhưng thêm `null=True, blank=True`
  (hiện tại bắt buộc). Một dòng PR giờ có 2 dạng loại trừ nhau: **catalog** (`product` được chọn,
  2 field non-catalog bắt buộc dưới đây để trống) hoặc **non-catalog** (`product=None`,
  `non_catalog_name`/`non_catalog_uom` bắt buộc, `non_catalog_note` tuỳ chọn) — validate ở
  `PurchaseRequestItem.clean()` (mục 8; sửa theo review lần 2 — v1/v2 viết nhầm "cả 3 field bắt
  buộc" trong khi `clean()` ở v2 chỉ XOR đúng 2 field, xem mục 13).
- `non_catalog_name` — `CharField(max_length=200, blank=True)`, verbose_name `'Tên hàng (chưa có
  trong danh mục)'` — **bắt buộc ở tầng form** cho dòng non-catalog.
- `non_catalog_uom` — `CharField(max_length=20, blank=True)`, verbose_name `'Đơn vị tính (đề
  xuất)'` — **bắt buộc ở tầng form** cho dòng non-catalog.
- `non_catalog_note` — `TextField(blank=True)`, verbose_name `'Mô tả/quy cách (non-catalog)'` —
  **tuỳ chọn**, không bắt buộc kể cả dòng non-catalog.
- `required_date` — `DateField(null=True, blank=True)`, verbose_name `'Ngày cần hàng'`. Bắt buộc ở
  **tầng form** cho mọi dòng tạo/sửa từ Stage 2 trở đi (DRAFT); để `null` ở tầng DB vì dòng PR đã
  tồn tại trước migration không có dữ liệu thật — không suy đoán bằng `created_at` (theo nguyên tắc
  backfill đã thống nhất trong CLAUDE.md — "không suy đoán thành sự kiện thật").
- `budget_category` — `CharField(max_length=100, null=True, blank=True)`, verbose_name `'Nhóm ngân
  sách/Tài khoản'` — nửa còn lại của khoá ngân sách theo quyết định #2 (`cost_center` + `category/
  account`), đặt ở cấp **dòng PR** (khác `cost_center` ở cấp header) vì các dòng trong cùng 1 PR có
  thể thuộc nhóm ngân sách khác nhau. Cùng cách xử lý YAGNI đã áp dụng cho `cost_center` (mục 2.1):
  CharField tự do, **không** tạo model danh mục/chart-of-accounts riêng — nếu sau này cần một danh
  mục chuẩn có kiểm soát, nâng cấp thành FK ở Stage 3 khi budget check thực sự cần validate theo
  danh sách cố định, không làm trước khi có yêu cầu rõ ràng. Bắt buộc ở **tầng form** cho dòng
  catalog lẫn non-catalog (giống `required_date`), `null` ở DB vì lý do backfill giống nhau.

  **Prefill dòng catalog** (sửa theo review lần 2, mục 13 — v2 chỉ nói "server-side" nhưng không đủ
  cho dòng mới thêm bằng formset phía client trước khi submit): 2 lớp, không phải chọn 1:
  1. **JS** trên `pr_create`/`pr_update` (mục 5): khi Requester chọn `product` cho 1 dòng (kể cả
     dòng vừa thêm động, chưa từng round-trip server), tự điền `budget_category` = giá trị đọc từ
     `data-category` render sẵn trên `<option>` tương ứng (không cần gọi API) — thuần UX, Requester
     vẫn sửa lại được trước khi lưu.
  2. **Server-side fallback** trong `PurchaseRequestItemForm.clean()`: nếu dòng là catalog
     (`product` khác rỗng) và `budget_category` rỗng lúc submit (JS tắt/bị chặn, hoặc form render
     lại do lỗi validation ở dòng khác) — tự lấy `product.category`. Đây là lớp **bắt buộc phải có**,
     JS chỉ là tiện ích chứ không phải điều kiện duy nhất để field này có giá trị đúng.
  Dòng non-catalog: Requester tự nhập tay, không có gợi ý ở cả 2 lớp trên.

  **Thời điểm đóng băng** (sửa lại câu "sau khi lưu" của v2 — không chính xác, vì dòng PR vẫn sửa
  được nhiều lần khi còn `DRAFT`): `budget_category` được nhập và **chỉnh sửa tự do trong khi PR còn
  `DRAFT`** (kể cả giá trị đã prefill từ `product.category`); tại đúng thời điểm
  `submit_purchase_request()` chuyển PR khỏi `DRAFT` lần đầu, giá trị hiện tại của field trở thành
  **snapshot cố định** — không đọc lại `product.category` về sau kể cả khi `Product.category` đổi
  sau này (cùng nguyên tắc bất biến đã dùng cho `department_snapshot`); dòng PR chỉ còn sửa được nếu
  PR `REJECTED` quay lại `DRAFT` (`reopen_purchase_request()`, lúc đó lại coi như đang sửa draft),
  tính bất biến tự nhiên có sẵn nhờ ràng buộc "chỉ sửa được khi `DRAFT`" áp dụng cho toàn bộ dòng
  PR, không cần cơ chế khoá field riêng.

  **Chuẩn hoá**: `clean()`/`save()` chuẩn hoá `strip()` + gộp khoảng trắng thừa trước khi lưu (tránh
  2 giá trị chỉ khác khoảng trắng bị tính là 2 nhóm ngân sách khác nhau) — **không** ép chuẩn hoá
  hoa/thường ở Stage 2, giữ nguyên casing Requester nhập để hiển thị đúng ý họ gõ. **Giới hạn đã
  biết** (review lần 2, mục 13): `'Nguyên liệu'` và `'NGUYÊN LIỆU'` vẫn là 2 giá trị khác nhau ở
  tầng lưu trữ trong Stage 2 — chấp nhận được vì **chưa có consumer nào** (giống `ExchangeRate`,
  mục 0 điểm 2) đọc field này để gộp nhóm ngân sách trong Stage 2. Khi Stage 3 bắt đầu dùng
  `budget_category` để gộp/so khớp ngân sách, **bắt buộc** đối chiếu qua 1 hàm canonical dùng chung
  toàn hệ thống (`trim` + gộp khoảng trắng + so sánh không phân biệt hoa/thường) thay vì để mỗi
  service Stage 3 tự viết logic so sánh riêng — ghi yêu cầu này vào FSD Stage 3 khi viết, không
  phải quyết định của Stage 2.
- `currency` — `CharField(max_length=3, choices=Currency.choices, default=Currency.VND)`,
  verbose_name `'Loại tiền'`. `Currency` là `TextChoices` mới (đặt ở `purchasing/models.py`, dùng
  chung với `ExchangeRate`): `VND`, `USD`, `EUR`, `JPY`, `CNY` — 5 mã đủ dùng cho MVP, thêm mã mới
  chỉ cần sửa `choices`, không đổi schema.
- `estimated_unit_price` — `DecimalField(max_digits=14, decimal_places=2,
  validators=[MinValueValidator(0)], null=True, blank=True)`, verbose_name `'Đơn giá ước tính'`.
  Bắt buộc ở **tầng form** cho dòng mới/sửa (đúng tinh thần "bắt buộc" của quyết định #8), `null`
  ở DB vì lý do backfill giống `required_date`. **Thuần tuý thông tin trong Stage 2** — chưa có
  service nào đọc field này để tính toán gì (Stage 3 mới dùng cho budget commitment).
- `qty_approved` — `PositiveIntegerField(null=True, blank=True)`, verbose_name `'Số lượng được
  duyệt'`. `null` cho tới khi PR được duyệt cuối (`PENDING_PUR → APPROVED`); set trong
  `decide_purchase_request()` nhánh approve (mục 3).
- `qty_cancelled` — `PositiveIntegerField(default=0)`, verbose_name `'Số lượng đã huỷ (phần còn
  mở)'` — tăng dần qua `cancel_pr_item_open_qty()` (`PUR-PR-07`), không giảm.

Property tính toán (không lưu cột, cùng invariant "derived, never stored" đã áp dụng cho
`qty_available`/`grand_total` toàn dự án):

- `qty_allocated` = tổng `qty_allocated` của mọi `ProcurementAllocation` **`status=ACTIVE`** trỏ
  tới dòng này (bất kể PO đích đã `SENT` hay còn `DRAFT`).
- `qty_ordered` = tổng `qty_allocated` của các allocation `ACTIVE` mà
  `po_item.purchase_order.status` thuộc `{SENT, PARTIAL_RECEIVED, RECEIVED, CLOSED}` — tức phần đã
  thật sự cam kết với NCC, không tính PO còn `DRAFT`/`APPROVED` (PO chưa gửi có thể bị xoá/sửa tự
  do, chưa phải cam kết ra ngoài).
- `qty_received` = tổng phần "nhận" quy về dòng PR này, tính theo thuật toán chia tỷ lệ ở mục 4
  điểm 6 (nhiều dòng PR có thể cùng trỏ 1 `PurchaseOrderItem`, trong khi GRN chỉ ghi nhận
  `qty_received` ở mức PO item, không tách theo từng nguồn PR — xem `PUR-PR-05`).
- `qty_open` = `max(0, (qty_approved or 0) - qty_allocated - qty_cancelled)`. `qty_approved is
  None` (PR chưa duyệt cuối) ⇒ `qty_open = 0` — không cho allocate dòng chưa duyệt (`PUR-PR-04`).
- `is_non_catalog` = `product_id is None`.

### 2.3 `ExchangeRate` — model mới

```python
class ExchangeRate(models.Model):
    currency = models.CharField(max_length=3, choices=Currency.choices, verbose_name='Loại tiền')
    rate_date = models.DateField(verbose_name='Ngày áp dụng')
    rate_to_vnd = models.DecimalField(
        max_digits=14, decimal_places=6, validators=[MinValueValidator(Decimal('0.000001'))],
        verbose_name='Tỷ giá quy đổi VND', help_text='1 đơn vị ngoại tệ = ? VND.')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, verbose_name='Người nhập')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày nhập')

    class Meta:
        verbose_name = 'Tỷ giá ngoại tệ'
        verbose_name_plural = 'Tỷ giá ngoại tệ'
        ordering = ['-rate_date', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['currency', 'rate_date'], name='unique_currency_rate_date'),
        ]
```

Không lưu bản ghi `ExchangeRate` nào có `currency=VND` (VND là đồng tiền gốc, không cần tỷ giá quy
đổi chính nó — service nào quy đổi tiền tệ phải short-circuit `currency == VND ⇒ rate = 1`, không
query bảng này).

Sửa/xoá `ExchangeRate` dùng **màn hình nghiệp vụ riêng** (`exchange_rate_update`/
`exchange_rate_delete`, mục 5), **không** dùng Django Admin — một user có `role=ADMIN` chưa chắc có
`is_staff=True` nên Django Admin không phải cổng kiểm soát đáng tin cho quyền "chỉ Admin" của bảng
này (khác cách v1 từng đề xuất). Cả `create`/`update`/`delete` đều giới hạn `user.role == ADMIN`
hoặc `is_superuser` (mục 1) và đều ghi `AuditLog` (mục 7). Vì **chưa có consumer nào** đọc bảng này
trong Stage 2 (mục 0), rủi ro sửa/xoá nhầm ảnh hưởng dữ liệu đã snapshot chỉ phát sinh từ Stage 3
trở đi — quy tắc "không sửa được sau khi đã bị snapshot dùng" để lại cho FSD Stage 3 thiết kế cụ
thể (cần biết chính xác Stage 3 snapshot ra sao trước khi khoá); Stage 2 cho sửa/xoá tự do miễn
đúng quyền Admin.

### 2.4 `ProcurementAllocation` — model mới

```python
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
                name='active_allocation_requires_po_item',
            ),
            models.CheckConstraint(
                condition=(
                    Q(status='ACTIVE', released_at__isnull=True, released_by__isnull=True,
                      released_reason='')
                    | (Q(status='RELEASED', released_at__isnull=False) & ~Q(released_reason=''))
                ),
                name='allocation_release_fields_match_status',
            ),
            models.CheckConstraint(
                condition=Q(qty_allocated__gte=1),
                name='allocation_qty_positive',
            ),
            models.CheckConstraint(
                condition=~Q(po_no_snapshot='') & ~Q(product_code_snapshot=''),
                name='allocation_snapshots_required',
            ),
        ]
```

`pr_item.product_id == po_item.product_id` là bất biến bắt buộc **tại thời điểm tạo** nhưng **không**
khả thi thành DB constraint 2 FK khác bảng — validate ở tầng service (`create_allocation()`, mục 4).
Không `UniqueConstraint(pr_item, po_item)` — 1 cặp có thể lý thuyết lặp lại sau khi 1 allocation cũ bị
`RELEASED` và tạo allocation mới thay thế; ràng buộc thật sự cần là tổng `qty_allocated` (mục 4
điểm 2), không phải số dòng.

`po_item` là `null=True`/`SET_NULL` (khác v1: ban đầu là `PROTECT`) vì `PROTECT` sẽ chặn đứng việc
xoá 1 dòng `PurchaseOrderItem` (mục 4 điểm 4) ngay cả sau khi mọi allocation trỏ tới nó đã chuyển
`RELEASED` — allocation đã `RELEASED` không còn giữ ý nghĩa "phải giữ nguyên PO item sống mãi", nó
chỉ cần giữ lại **thông tin để tra cứu lịch sử**. `create_allocation()` set `po_no_snapshot`/
`product_code_snapshot` tự động từ `po_item.purchase_order.po_no`/`po_item.product.product_code`
ngay tại thời điểm tạo — nên dù `po_item` sau đó bị xoá và FK về `NULL`, allocation vẫn tra cứu
được nó từng thuộc PO/product nào. `pr_item.product_id == po_item.product_id` ở trên do vậy chỉ
được validate lúc `create_allocation()` chạy, không phải một invariant giữ mãi trên toàn vòng đời
row (vì `po_item` có thể trở thành `None`).

**2 `CheckConstraint` mới** (review lần 2, mục 13 — v2 chưa có): `active_allocation_requires_
po_item` chặn đứng trạng thái vô nghĩa "`ACTIVE` nhưng `po_item=NULL`" — allocation chỉ được phép
có `po_item=NULL` sau khi đã `RELEASED` (khớp đúng lý do `SET_NULL` ở trên: `po_item` chỉ mất đi
sau khi dòng PO-item bị xoá, mà xoá chỉ xảy ra sau khi mọi allocation trỏ tới đã release trước, mục
4 điểm 4). `allocation_release_fields_match_status` khoá cặp field
`released_at`/`released_by`/`released_reason` đi cùng đúng `status`: `ACTIVE` bắt buộc cả 3 rỗng,
`RELEASED` bắt buộc có `released_at` và `released_reason` khác rỗng (`released_by` vẫn có thể rỗng
cho case release tự động do hệ thống — actor thật vẫn luôn được ghi ở `AuditLog`, mục 7, nên không
bắt buộc ở constraint DB này). Cả hai là defense-in-depth ở tầng DB, cùng pattern
`batch_qty_used_lte_received`/`inventory_reserved_lte_on_hand` đã có trong `inventory` app.

**2 `CheckConstraint` mới** (review lần 3, mục 13 — v3 chưa có, Quan trọng #4): `allocation_qty_
positive` (`qty_allocated >= 1`) — `PositiveIntegerField` từ Django 4.1 trở đi chỉ tự sinh DB check
đảm bảo **`>= 0`** (emulate kiểu unsigned cho Postgres), `MinValueValidator(1)` khai báo trên field
chỉ chạy ở tầng `full_clean()`/form, **không** phải constraint DB — một `INSERT` thô (bypass service)
với `qty_allocated=0` trước đây lọt qua được tầng DB, giờ bị chặn thẳng. `allocation_snapshots_
required` (`po_no_snapshot`/`product_code_snapshot` không được rỗng) — 2 cột này là **dấu vết duy
nhất còn lại** sau khi `po_item` bị xoá hẳn và FK về `NULL` (mục 4 điểm 4), nên không được phép rỗng
ngay từ lúc tạo, bất kể `po_item` còn sống hay đã mất.

**Giới hạn của `CheckConstraint`** (review lần 3, ghi rõ để tránh hiểu nhầm): 2 constraint hiện có
chỉ kiểm tra được trạng thái của **1 row `ProcurementAllocation`** tại một thời điểm — **không** thể
dùng `CheckConstraint` để bảo đảm bất biến `PurchaseOrderItem.qty_ordered == tổng qty_allocated`
(mục 4 điểm 4), vì đó là phép tổng hợp (aggregate) xuyên nhiều row/2 bảng khác nhau, ngoài khả năng
biểu diễn của 1 `CHECK` trên 1 bảng. Bất biến đó **bắt buộc** phải dựa hoàn toàn vào tầng service +
lock order (mục 4 điểm 2) + guard `send_po()` (mục 4 điểm 4) — 2 `CheckConstraint` này chỉ đóng vai
trò defense-in-depth cho những bất biến **có thể** biểu diễn ở 1 row, không thay thế được lớp bảo vệ
ở tầng service cho bất biến tổng hợp.

**`created_by` sửa từ `PROTECT` sang `null=True, blank=True, on_delete=SET_NULL`** (sửa lỗi Nghiêm
trọng #3 của review lần 2 — v2 khai báo `PROTECT` nhưng migration backfill (mục 9) lại tạo
allocation với `created_by=None`, 2 điều này mâu thuẫn nhau khiến migration crash ngay khi chạy
thật). `created_by=None` mang đúng 1 nghĩa: allocation được tạo bởi data migration/hệ thống
(backfill từ `linked_po`), không phải do 1 user thao tác qua UI — cùng quy ước "actor `None` =
system-triggered" đã dùng cho `sync_expired_batches`. Khác với v2: cascade `release_allocation()`
do Buyer xoá 1 dòng PO-item qua `po_update` (mục 4 điểm 4) **không còn** dùng `actor=None` nữa — đó
là thao tác trực tiếp của người dùng thật, phải ghi đúng `request.user` (mục 7); `actor=None` từ
nay **chỉ** dành cho data migration thật.

### 2.5 `PurchaseOrderItem`, `PurchaseOrder` — không đổi field

Không thêm `currency`/`exchange_rate_snapshot` vào 2 model này trong Stage 2 (xem mục 0 điểm 2 —
việc đó là Stage 4/5). `PurchaseOrder.source = FROM_PR` tiếp tục dùng nguyên trạng, chỉ đổi **cách
tạo** (mục 5) chứ không đổi field.

## 3. Trạng thái và transition

**PR header**: giữ nguyên 100% state machine hiện có (`DRAFT → PENDING_DEPT → PENDING_PUR →
APPROVED/REJECTED`, `REJECTED` mở lại `DRAFT` qua `reopen_purchase_request()`) — Stage 2 không sửa
transition nào của `PurchaseRequest.status`.

**PR line — mới, không phải enum, là tổ hợp qty** (đúng tinh thần `PUR-PR-03`, không thêm cột
`status` riêng cho `PurchaseRequestItem` vì trạng thái luôn suy ra được từ 5 con số
`qty_requested/qty_approved/qty_allocated/qty_ordered/qty_received/qty_cancelled/qty_open` — thêm
cột enum sẽ tạo nguồn sự thật thứ 2 dễ lệch, đúng lỗi đã tránh ở nhiều chỗ khác trong dự án này,
xem CLAUDE.md "derived, never stored"):

```
requested → (PR duyệt cuối) → approved → (tạo Allocation) → allocated
    → (PO chuyển SENT) → ordered → (GRN ghi nhận) → received (từng phần hoặc đủ)
Bất kỳ lúc nào còn qty_open > 0: PUR Staff có thể cancel một phần → qty_cancelled tăng
```

Điểm mới cần code (chưa có trong luồng approve hiện tại): **`decide_purchase_request()` nhánh
approve ở cấp `PENDING_PUR` phải set `qty_approved` cho từng `PurchaseRequestItem`** — mặc định
`qty_approved = qty_requested` nếu PUR Manager không chỉnh gì; màn hình duyệt cấp `PENDING_PUR`
(mục 5) cho phép sửa xuống (không cho sửa lên — muốn mua nhiều hơn phải qua PR/dòng mới, tránh
tăng chi tiêu âm thầm ở bước duyệt). `qty_approved = 0` hợp lệ (coi như từ chối riêng dòng đó, PR
tổng thể vẫn `APPROVED` nếu còn dòng khác >0; PR có **tất cả** dòng `qty_approved=0` là trường hợp
dị thường — chặn ở validation, yêu cầu dùng `REJECTED` cho cả PR thay vì approve rỗng). Cấp
`PENDING_DEPT` **không** set `qty_approved` (giữ đúng quyết định #3 — chỉ duyệt cuối ở
`PENDING_PUR` mới là điểm phát sinh commitment/approved qty thật).

**`ProcurementAllocation.status`**: `ACTIVE` (mặc định lúc tạo) → `RELEASED` (một chiều, không quay
lại `ACTIVE` — muốn phân bổ lại thì tạo allocation mới). Chuyển `RELEASED` khi: PUR Staff chủ động
release thủ công (`release_allocation()`, cần `released_reason`), hoặc cascade tự động khi 1 dòng
`PurchaseOrderItem` bị xoá hẳn trong khi PO còn `DRAFT` — đi qua service điều phối riêng
`delete_draft_po_item_with_allocations()` (mục 4 điểm 4, đổi từ review lần 3, thay vì `po_update`
tự lặp gọi `release_allocation()` rồi để `formset.save()` xoá lại cùng row, xem Nghiêm trọng #4 mục
13). **Cả 2 đường đều đồng thời giảm `PurchaseOrderItem.qty_ordered` đúng bằng `qty_allocated` vừa
release** (chốt từ review lần 2 — bất biến mục 4 điểm 4 đòi `qty_ordered` luôn khớp đúng tổng
allocation `ACTIVE`, nên mọi lần release đều phải giữ bất biến đó, không có ngoại lệ "release nhưng
để nguyên qty_ordered").

**Đối xứng ở chiều tạo mới** (review lần 3, Nghiêm trọng #1 — v3 chỉ nói rõ chiều *giảm* qua
`release_allocation()`, chưa nói chiều *tăng*): `create_allocation()` luôn **tăng**
`PurchaseOrderItem.qty_ordered` đúng bằng `qty_allocated` vừa tạo, trong cùng transaction với việc
tạo row `ProcurementAllocation` (mục 4 điểm 2/4) — áp dụng cho **mọi** lần gọi, kể cả khi thêm 1
allocation vào 1 `po_item` đã tồn tại từ trước (không chỉ lúc `build_po_from_allocations()` tạo PO
mới). Đây là **điểm duy nhất** được phép tăng `qty_ordered` của PO nguồn `FROM_PR`, đối xứng đúng với
`release_allocation()`/`delete_draft_po_item_with_allocations()` là điểm duy nhất được phép giảm.

## 4. Business rules

1. **`PUR-PR-01`** — không đổi so với hiện tại: PR draft cần ≥1 dòng hợp lệ mới nộp được (dòng hợp
   lệ = có `product` **hoặc** đủ 2 field non-catalog bắt buộc (`non_catalog_name`,
   `non_catalog_uom`; `non_catalog_note` tuỳ chọn — sửa theo review lần 2, xem mục 2.2/mục 13: v1/v2
   viết nhầm "đủ 3 field" trong khi `clean()` luôn chỉ XOR đúng 2 field bắt buộc), có
   `qty_requested`, `required_date`, `currency`, `estimated_unit_price`).
2. **`PUR-PR-04`** — `create_allocation(pr_item, po_item, qty, actor)` chặn nếu
   `qty > pr_item.qty_open` **tính ngay trước khi tạo, dưới khoá**, theo đúng **lock order chung
   cho toàn bộ nhóm Allocation** (chốt mới ở review lần 2, thay hẳn cách v2 mô tả — v2 chỉ khoá
   `pr_item`, không đủ vì `po_update`/`release_allocation` cũng động vào `PurchaseOrder`/
   `PurchaseOrderItem` cùng lúc, có thể deadlock hoặc TOCTOU chéo nếu thứ tự khoá không thống nhất
   giữa các service — Nghiêm trọng #2, mục 13):

   **`PurchaseOrder → PurchaseOrderItem → PurchaseRequestItem (order_by pk tăng dần nếu nhiều dòng)
   → ProcurementAllocation (order_by pk tăng dần nếu nhiều dòng)`**

   Mọi service đụng tới 2+ trong 4 model này — `create_allocation`, `release_allocation`,
   `po_update` (guard mục 4 điểm 4), `build_po_from_allocations` — phải khoá theo đúng thứ tự này,
   kể cả khi bản thân hàm đó chỉ thao tác thật sự trên 1-2 model cuối trong chuỗi: ví dụ
   `create_allocation()` (từ v4 trở đi **tự sửa** `PurchaseOrderItem.qty_ordered` qua `F()`, mục 4
   điểm 4 — sửa câu diễn đạt sai của v4 ở đây, review lần 4 điểm 7a: câu cũ nói hàm này "không sửa
   `PurchaseOrder`/`PurchaseOrderItem`", không còn đúng từ khi bất biến tăng `qty_ordered` được thêm
   vào) vẫn phải khoá `po_item.purchase_order` rồi `po_item` **trước** khi khoá `pr_item`, để nhất
   quán thứ tự toàn cục với `po_update` — tránh deadlock kiểu 2 transaction khoá 2 resource theo 2
   chiều ngược nhau, đúng
   lớp lỗi "standing lock order" đã ghi trong CLAUDE.md cho chuỗi
   `Grn → QcInspection → Inventory → Batch → WarehouseHandoff` của các module khác (đây là chuỗi
   lock **riêng** của nhóm Allocation, không giao với chuỗi đó). `create_allocation()` vẫn chặn
   `qty > pr_item.qty_open` như v2 — chỉ đổi thứ tự khoá bao quanh nó, không đổi điều kiện chặn.
3. **`PUR-PR-05`** — 1 `PurchaseOrderItem` có thể nhận nhiều `ProcurementAllocation` từ nhiều
   `PurchaseRequestItem` khác nhau (kể cả khác PR), miễn cùng `product`; khi build PO
   (`build_po_from_allocations()`, mục 5), nếu 2+ dòng PR được chọn cùng trỏ 1 product cho cùng 1
   PO đang tạo, hệ thống tự **gộp** vào đúng 1 `PurchaseOrderItem.qty_ordered` (tổng qty các dòng đã
   chọn) — không tạo 2 dòng PO trùng product (giữ `PUR-FND-06`).
4. **Bất biến bắt buộc — `qty_ordered` luôn khớp đúng tổng allocation `ACTIVE`** (chốt từ review lần
   2; sửa lỗ hổng over-order Nghiêm trọng #1 của v2 — xem mục 13): với mọi `PurchaseOrder.source ==
   FROM_PR`, tại **mọi transaction boundary/trạng thái đã commit** kể từ khi dòng
   `PurchaseOrderItem` được tạo cho tới khi PO `SENT` (sửa câu "mọi thời điểm" của v4, review lần 4
   điểm 7b — câu đó mâu thuẫn với chính `build_po_from_allocations()` tạm tạo `qty_ordered=0` trước
   khi gọi `create_allocation()` cho từng cặp, mục 4 điểm 4 dưới: trạng thái tạm **bên trong** một
   transaction chưa commit không tính là vi phạm, vì nó không bao giờ được quan sát từ bên ngoài
   transaction đó, và bắt buộc rollback toàn bộ nếu bất kỳ bước nào của quá trình build thất bại):
   `PurchaseOrderItem.qty_ordered == tổng qty_allocated của các ProcurementAllocation
   ACTIVE trỏ tới đúng dòng đó`. Không có trạng thái trung gian "`qty_ordered` cao hơn hoặc thấp hơn
   tổng allocation" được phép tồn tại đối với PO nguồn `FROM_PR` — cách v2 mô tả ("release 1 phần
   allocation nhưng giữ nguyên `qty_ordered`") tạo lỗ hổng: PR mở lại `qty_open`, nhưng PO gốc vẫn
   giữ nguyên số lượng đã đặt, cho phép allocate thêm sang PO khác và vô tình đặt mua gấp đôi nhu
   cầu thật. PO `source=MANUAL` không áp dụng bất biến này (không có allocation nào trỏ tới).

   **`create_allocation()` là điểm duy nhất được phép *tăng* `qty_ordered`** (review lần 3, Nghiêm
   trọng #1 — v3 chỉ nói rõ chiều *giảm* qua `release_allocation()`, chưa nói chiều *tăng*: gọi
   `create_allocation()` để thêm 1 allocation vào 1 `po_item` **đã tồn tại từ trước** — không chỉ lúc
   `build_po_from_allocations()` tạo PO mới, xem `TC-PUR-PR-04-003` — làm tổng allocation tăng nhưng
   `qty_ordered` đứng yên nếu hàm không tự cập nhật, phá bất biến ngay lập tức). Trong cùng
   transaction, dưới khoá theo đúng lock order (điểm 2 dưới), ngay sau khi tạo row
   `ProcurementAllocation`, `create_allocation()` luôn thực hiện `po_item.qty_ordered =
   F('qty_ordered') + qty` rồi `save(update_fields=['qty_ordered'])` — không có đường nào khác được
   phép tăng `qty_ordered` của PO nguồn `FROM_PR`. Đồng thời, **thu hẹp điều kiện trạng thái PO cho
   phép tạo allocation từ `(DRAFT, APPROVED)` xuống còn đúng `DRAFT`** (mục 8 — sửa theo review lần
   3: PO đã `APPROVED` không còn được tạo/release allocation nữa, khớp đúng quy tắc "PO `APPROVED`
   trở lên không sửa được PO-item" ở gạch đầu dòng dưới; v3 để hở 2 rule mâu thuẫn nhau khi
   `create_allocation()` vẫn nhận `APPROVED` trong khi `po_update` đã khoá cứng PO `APPROVED`).

   `build_po_from_allocations()` (mục 5) áp dụng đúng cơ chế trên, không tính tổng `qty_ordered`
   riêng: tạo `PurchaseOrder(DRAFT)` + mỗi `PurchaseOrderItem` (gộp theo product) khởi tạo
   `qty_ordered=0` (giá trị tạm thời, chỉ tồn tại trong transaction chưa commit, không lộ ra ngoài;
   tạo bằng `.objects.create()` thẳng, **không** gọi `full_clean()` trên giá trị tạm này —
   `MinValueValidator(1)` chỉ chạy khi chủ động gọi `full_clean()`/qua `ModelForm`, ghi rõ comment
   trong code để tránh ai đó sau này vô tình thêm `full_clean()` vào bước này), rồi gọi
   `create_allocation()` đúng 1 lần cho mỗi cặp (`pr_item`, `po_item`, `qty`) đã chọn. Mỗi lần gọi tự
   tăng `qty_ordered`, nên tại thời điểm transaction commit, `qty_ordered` của mọi `po_item` vừa tạo
   đã bằng đúng tổng allocation của nó — `create_allocation()` là nguồn sự thật duy nhất cho phép
   cộng, `build_po_from_allocations()` không lặp lại phép tính tổng ở tầng view.

   Hệ quả cụ thể (v1 từng nhắc tới "xoá PO" — capability đó **không** tồn tại trong code, `po_update`
   chỉ cho sửa PO khi còn `DRAFT`, không có view/route/service nào xoá cả `PurchaseOrder`; điểm 4
   chỉ nói về sửa/xoá **1 dòng `PurchaseOrderItem`**):
   - **PO `APPROVED` trở lên**: không được sửa/xoá bất kỳ `PurchaseOrderItem` nào, và **không được
     `create_allocation()`/`release_allocation()`** (mở rộng theo review lần 3 — trước đây chỉ
     `po_update` bị khoá, giờ khoá luôn ở tầng service để 2 rule nhất quán) — đúng ràng buộc
     `po_update` đã có sẵn (chỉ sửa khi còn `DRAFT`), Stage 2 không mở thêm đường nào khác để sửa PO
     đã duyệt.
   - **PO còn `DRAFT`, nguồn `FROM_PR`: mọi dòng `PurchaseOrderItem` đều bị khoá, không chỉ dòng
     đang có allocation** (mở rộng theo review lần 3, Nghiêm trọng #2 — v3 chỉ khoá dòng đang có ≥1
     allocation `ACTIVE`, để hở 3 đường: thêm dòng PO-item mới thủ công qua `po_update`, đổi
     `product`/`qty_ordered` của 1 dòng legacy chưa có allocation nào, tạo trạng thái `qty_ordered >
     0` mà tổng allocation vẫn = 0). Quy tắc đầy đủ:
     - **`extra=0` chỉ giới hạn số form hiển thị lúc GET, không chặn được POST giả** (sửa hiểu nhầm
       của v4, review lần 4 điểm 1 — v4 coi `extra=0` là đủ để "không cho thêm dòng mới"; thực tế
       client vẫn có thể tự sửa `items-TOTAL_FORMS` và gửi kèm 1 form mới, hoặc gửi 1 `pk` không
       thuộc PO đang sửa — inline formset vẫn nhận). Server phải tự guard độc lập với management
       form: sau khi `formset.is_valid()` (bước 4 dưới), loại bỏ (`ValidationError`) bất kỳ form nào
       có `form.instance.pk is None` (form mới) hoặc `pk` không nằm trong đúng tập
       `PurchaseOrderItem` của **PO này** đã khoá ở bước 2 dưới — dòng mới chỉ được tạo qua
       `po_build_from_pr_lines`/`create_allocation()`, không bao giờ qua `po_update`. Kiểm
       `TOTAL_FORMS == INITIAL_FORMS` chỉ là tín hiệu phụ, phát hiện sớm — không thay được việc
       duyệt từng instance, vì bản thân management form (`TOTAL_FORMS`/`INITIAL_FORMS`) cũng chỉ là
       dữ liệu POST, tự nó không đáng tin.
     - **`product` và `qty_ordered` khoá `disabled=True`** ở tầng `Form` (không chỉ thuộc tính
       `readonly` HTML) cho **mọi** dòng của PO nguồn `FROM_PR`, kể cả dòng chưa có allocation nào —
       `disabled=True` khiến Django luôn dùng giá trị `initial` trong `cleaned_data`, bỏ qua hoàn
       toàn giá trị POST bất kể client gửi gì; đây là **lớp bảo vệ dữ liệu** (giá trị lưu luôn
       đúng), độc lập với lớp phát hiện tampering dưới đây.
     - **Trình tự xử lý đúng** (viết lại theo review lần 4 điểm 2 — sửa thứ tự sai của v4: v4 mô tả
       so sánh raw POST **trước** `formset.is_valid()`, buộc code tự parse management form/prefix/ID
       khi chưa có gì đáng tin cậy; gọi `is_valid()` trước an toàn hơn vì bản thân nó không ghi DB):
       1. Mở `transaction.atomic()`.
       2. Lock `PurchaseOrder` và toàn bộ `PurchaseOrderItem` hiện có của đúng PO đó
          (`select_for_update()`, theo lock order mục 4 điểm 2).
       3. Khởi tạo formset **bound** với `queryset` giới hạn đúng các `PurchaseOrderItem` vừa khoá ở
          bước 2 (không phải toàn bảng).
       4. Gọi `formset.is_valid()`. **Nếu trả về `False`**: dừng lại ngay tại đây — render lại form
          kèm lỗi cho người dùng, **không** chạy bước 5-8 dưới (không kiểm `pk`, không so sánh raw
          POST, không lưu gì) — các guard ở bước 5 trở đi chỉ có ý nghĩa và chỉ được chạy sau khi
          `formset.is_valid()` đã trả về `True` (review lần 5 điểm 2 — làm rõ nhánh else còn thiếu
          của review lần 4 điểm 2, vốn chỉ mô tả nhánh hợp lệ).
       5. Với từng form trong formset, lấy `pk = form.instance.pk`: reject (`ValidationError`) nếu
          `pk is None` (form mới) hoặc `pk` không thuộc tập `PurchaseOrderItem` đã khoá ở bước 2.
          Đồng thời tích luỹ mọi `pk` đã duyệt qua vào 1 tập riêng (`submitted_pks`) và reject nếu
          `pk` đó **đã xuất hiện trước đó trong cùng formset** (mới, review lần 5 điểm 1 — 2 guard
          "có tồn tại"/"thuộc đúng PO" chỉ kiểm từng form riêng lẻ, không phát hiện 2 form khác nhau
          cùng gửi 1 `pk` giống hệt: client tăng `items-TOTAL_FORMS` rồi gửi 2 form trỏ cùng 1
          `PurchaseOrderItem.pk` hợp lệ vẫn qua lọt cả 2 guard cũ, khiến `unit_price` bị ghi 2 lần
          theo thứ tự form — "last write wins" không tường minh, không ai chủ ý tạo ra). Không bắt
          buộc mọi `pk` đã khoá ở bước 2 phải có mặt trong POST — dòng nào client không gửi kèm coi
          như không sửa; nhưng mỗi `pk` có mặt tối đa **đúng 1 lần**.
       6. Với từng form còn lại (ứng đúng 1 `PurchaseOrderItem` thật, không trùng, thuộc đúng PO): lấy
          tên field bằng
          `form.add_prefix('product')`/`form.add_prefix('qty_ordered')` (**không** tự ghép chuỗi
          kiểu `f'items-{i}-product'` — dễ sai khi Django đổi thứ tự/prefix form). Field
          `disabled=True` thường **không xuất hiện** trong `request.POST` (hành vi trình duyệt
          chuẩn) — key không có ⇒ request bình thường, không phải tampering. Key **có mặt** (client
          cố tình POST thêm field bị vô hiệu hoá) ⇒ chuẩn hoá giá trị rồi so với giá trị đã khoá
          trong DB — khác nhau ⇒ raise `ValidationError` báo tampering ngay. Đây là **lớp phát hiện +
          báo lỗi**, khác hẳn `disabled=True` (lớp bảo vệ dữ liệu) — 2 lớp độc lập, không thay thế
          nhau: mất lớp `disabled=True` thì dữ liệu sai vẫn có thể lọt nếu code quên so sánh; mất lớp
          so sánh raw POST thì tampering vẫn bị chặn (nhờ `disabled=True`) nhưng người dùng không
          được báo gì.
       7. Dòng bị đánh dấu xoá (`formset.deleted_forms`) xử lý qua
          `delete_draft_po_item_with_allocations()` (gạch đầu dòng dưới), không phải để
          `formset.save()` tự xoá.
       8. Chỉ lưu `unit_price` cho các form **không** thuộc `deleted_forms`
          (`formset.save(commit=False)` rồi tự gọi `.save(update_fields=['unit_price'])` từng
          instance còn lại).
     - Cho phép sửa `unit_price` tự do khi PO còn `DRAFT` (field này không bị khoá, không nằm trong
       bước so sánh raw POST ở trên).
     - Cho phép xoá dòng (`can_delete`) — xoá đi qua `delete_draft_po_item_with_allocations()` (gạch
       đầu dòng dưới), không phải `formset.save()` xoá trực tiếp.
     - **Dòng legacy không có allocation nào** (`qty_ordered > 0`, tổng allocation `ACTIVE = 0`, chỉ
       phát sinh từ dữ liệu backfill mơ hồ, mục 9): vì `product`/`qty_ordered` đã khoá cứng và
       `qty_allocated`/`qty_ordered` đều có `MinValueValidator(1)` (không lưu được `qty_ordered=0`
       cho 1 dòng còn giữ lại), chỉ còn đúng 2 lựa chọn — (a) **xoá dòng trực tiếp** qua
       `delete_draft_po_item_with_allocations()` (không có allocation nào để release, xoá thẳng —
       chỉ dùng được khi PO còn `DRAFT`, vì `po_update` chỉ sửa được PO `DRAFT`), hoặc (b)
       **`reconcile_legacy_po_item_allocations()`** (đổi tên số nhiều ở review lần 4 — xem "Ghi chú
       dữ liệu cũ" dưới) tạo 1 hoặc nhiều allocation khớp đúng **chính xác** `qty_ordered` hiện có mà
       **không** tăng thêm — khác hẳn `create_allocation()` thông thường (luôn tăng `qty_ordered`, sẽ
       nhân đôi giá trị legacy nếu dùng nhầm hàm này). Không giống lựa chọn (a), lựa chọn (b) chạy
       được cả khi PO đã `APPROVED`, không chỉ `DRAFT` (mở rộng ở review lần 4 điểm 5, xem "Ghi chú
       dữ liệu cũ" dưới) — nhưng chỉ qua management command riêng, không qua màn hình `po_update`.
     Muốn thay đổi *số lượng* (không phải sửa trực tiếp), đường duy nhất vẫn là **release bớt
     allocation** — `release_allocation()` tự động trừ `qty_ordered` đúng bằng `qty_allocated` vừa
     release trong cùng transaction, giữ bất biến đúng ngay lập tức, không có bước "sửa
     `qty_ordered`" riêng nào khác.
   - **Xoá hẳn 1 dòng `PurchaseOrderItem` khi PO còn `DRAFT`** (qua formset `can_delete` của
     `po_update`): đi qua service điều phối riêng **`delete_draft_po_item_with_allocations(po_item,
     actor)`** (mới ở review lần 3, sửa lỗi Nghiêm trọng #4 — v3 để `po_update` tự lặp gọi
     `release_allocation()` cho từng allocation rồi vẫn để `formset.save()` xoá lại chính dòng đó:
     `release_allocation()` đã tự xoá `po_item` khi allocation cuối cùng đưa `qty_ordered` về 0, nên
     `formset.save()` xoá thêm lần nữa là 2 tầng cùng sở hữu 1 thao tác xoá trên cùng 1 row). Hàm
     mới, trong cùng transaction/lock order (điểm 2 dưới):
     1. Lock `po_item` và các model cha theo đúng thứ tự.
     2. Gọi `release_allocation(alloc, reason=..., actor=actor, delete_empty_po_item=False)` cho
        **mọi** allocation `ACTIVE` trỏ tới `po_item` — tham số mới `delete_empty_po_item` (mặc
        định `True` cho ca release đơn lẻ ở gạch đầu dòng dưới) khi `False` tắt hẳn bước tự xoá
        `po_item` bên trong `release_allocation()`, dồn quyền xoá về đúng 1 chỗ.
     3. Xoá hẳn row `po_item` **đúng một lần**, bất kể trước đó có allocation hay không (dòng legacy
        0 allocation vẫn xoá thẳng được, bỏ qua bước 2).
     4. Ghi `AuditLog` cho từng lần release (như cũ) **và thêm 1 dòng riêng** cho chính thao tác xoá
        `po_item`, `released_reason` tự sinh (vd `"PO-item bị xoá khỏi PO {po_no} khi còn DRAFT"`),
        **actor ghi đúng người thao tác thực** — `request.user` của request đang xử lý `po_update`,
        **không phải** `actor=None` (chốt từ review lần 2 — `actor=None` từ nay **chỉ** dành cho
        data migration thật, mục 2.4/mục 7).
     `po_update` view gọi hàm này cho mỗi dòng bị đánh dấu `can_delete`, rồi **loại các dòng đó ra
     khỏi tập `formset.save()` xử lý tiếp** (ví dụ: `formset.save(commit=False)`, tự xử lý
     `formset.deleted_forms` bằng hàm mới ở trên, chỉ gọi `.save()` cho các form còn lại) — không để
     `formset.save()` mặc định tự xoá thêm lần nữa trên cùng row.
   - PUR Staff cũng có thể chủ động `release_allocation()` (`delete_empty_po_item=True` mặc định,
     review lần 3) cho 1 allocation riêng lẻ khi PO còn `DRAFT` (đổi ý trước khi gửi NCC), bắt buộc
     `released_reason`, actor là chính PUR Staff đó. Nếu đây là allocation **cuối cùng** còn `ACTIVE`
     của dòng `PurchaseOrderItem` (tức release xong `qty_ordered` về 0), cascade xoá luôn dòng
     `PurchaseOrderItem` đó ngay bên trong `release_allocation()` (khác đường xoá cả dòng ở trên —
     `delete_draft_po_item_with_allocations()` tự quản lý xoá riêng, tắt cờ này) — hành vi giống
     hệt, chỉ khác điểm khởi phát (Buyer chủ động chọn 1 allocation để release, thay vì xoá cả dòng
     PO-item trước).
   - **`send_po()` thêm guard mới**: PO `source=FROM_PR` bị chặn chuyển `SENT` (`ValidationError`,
     liệt kê rõ dòng vi phạm) nếu tồn tại bất kỳ `PurchaseOrderItem` nào có `qty_ordered != tổng
     qty_allocated (ACTIVE)` — bảo vệ trường hợp dữ liệu cũ/bất thường (xem "Ghi chú dữ liệu cũ"
     dưới) lọt qua được tới bước gửi NCC. Với PO tạo mới từ Stage 2 trở đi, guard này luôn tự động
     đúng (mọi đường tạo/release đều giữ bất biến); guard chỉ thực sự "bắt" được trường hợp dữ liệu
     legacy chưa reconcile. **Lưu ý khi viết test cho guard này** (review lần 3, Nghiêm trọng #3 —
     xem `TC-PUR-PR-05-008` mục 11): dựng PO ở trạng thái `DRAFT` sẽ bị chặn bởi điều kiện cũ đã có
     từ trước ("chỉ gửi được PO `APPROVED`"), không hề chạm tới guard mới này — fixture test **phải**
     dựng PO ở trạng thái `APPROVED` để thực sự exercise guard mới, nếu không test sẽ pass dù guard
     chưa từng chạy (false positive).
   - **Ghi chú dữ liệu cũ**: PO `source=FROM_PR` được backfill từ `linked_po` (mục 9) mà rơi vào
     nhóm "không khớp rõ ràng" (không tạo được allocation tự động) giữ nguyên `qty_ordered` cũ
     nhưng tổng allocation `ACTIVE` = 0 — vi phạm bất biến trên ngay từ đầu. Tuyệt đại đa số PO cũ
     đã `SENT` từ trước Stage 2 nên guard `send_po()` không chặn gì (PO đã qua trạng thái đó rồi,
     guard chỉ chạy tại thời điểm gọi `send_po()`); phần hiếm PO cũ còn `DRAFT` hoặc `APPROVED` rơi
     vào nhóm này phải được Admin **reconciliation thủ công** trước khi gửi, đúng 1 trong 2 cách:
     (a) `delete_draft_po_item_with_allocations()` xoá thẳng dòng — **chỉ** dùng được khi PO còn
     `DRAFT` (không có allocation nào để release), hoặc (b)
     **`reconcile_legacy_po_item_allocations(po_item, allocations, actor)`** (đổi thành hàm
     **batch** ở review lần 4, thay `reconcile_legacy_po_item_allocation()` đơn lẻ của v4 — xem lý
     do dưới; hàm one-off riêng biệt, **không** lộ ra UI/luồng tạo PO thông thường, chỉ gọi qua
     **management command** chuyên dụng, không phải Django shell tự do — xem điểm dưới) tạo **thêm**
     1 hoặc nhiều allocation (mỗi phần tử `allocations` là 1 cặp `(pr_item, qty)`) khớp với phần
     `qty_ordered` hiện có **mà không tăng `qty_ordered`** — ngoại lệ **duy nhất** được phép "tạo
     allocation nhưng không cộng thêm `qty_ordered`", vì giá trị đó đã là sự thật lịch sử cần giữ
     nguyên, không phải nhu cầu mới cần cộng dồn.

     **Vì sao phải đổi thành batch** (review lần 4 — sửa lỗi của v4: hàm đơn lẻ chỉ chặn khi tổng
     allocation *vượt quá* `qty_ordered`, dùng `<=`): gọi hàm đơn lẻ 1 lần với `qty` nhỏ hơn
     `qty_ordered` (ví dụ `qty_ordered=10`, gọi `qty=5`) **commit thành công** dù bất biến "tổng
     allocation == `qty_ordered`" (mục 4 điểm 4) vẫn sai ngay sau đó — hàm đơn lẻ không tự biết còn
     thiếu bao nhiêu. Thêm nữa, 1 `po_item` legacy có thể cần **2+ `pr_item`** khớp vào (ví dụ
     4 + 6 = 10) — gọi hàm đơn lẻ 2 lần không giữ được bất biến tại ranh giới **mỗi lần gọi** (lần
     gọi đầu commit ở trạng thái tổng=4, sai bất biến, dù lần gọi 2 sẽ sửa đúng). Đổi thành 1 hàm
     batch nhận danh sách `allocations=[(pr_item_1, 4), (pr_item_2, 6), ...]`, tạo **toàn bộ**
     allocation trong **cùng 1 transaction**: rule bắt buộc là `tổng allocation ACTIVE hiện có +
     tổng qty trong batch == po_item.qty_ordered` (dùng đúng dấu `==`, không phải `<=` như hàm đơn lẻ
     của v4) — batch reject (`ValidationError`, rollback toàn bộ) nếu tổng không khớp **chính xác**,
     và 1 dòng sai trong batch (ví dụ `qty` vượt `pr_item.qty_open`) làm rollback **toàn bộ batch**,
     không tạo dở dang 1 phần.

     **Validation đầy đủ của hàm batch** (mở rộng theo review lần 4 và review lần 5, không chỉ kiểm
     tổng): (1) actor là `role == ADMIN` hoặc `is_superuser`, **và** `actor.is_active == True` và
     chưa bị soft-delete (mới, review lần 5 điểm 5 — actor dùng để ghi `AuditLog` phải là 1 tài
     khoản Admin thật đang hoạt động, không phải username còn tồn tại trong DB nhưng đã bị
     khoá/xoá mềm); (2) `po_item.purchase_order.source == FROM_PR`; (3)
     `po_item.purchase_order.status` thuộc `{DRAFT, APPROVED}` (mở rộng từ chỉ `DRAFT`, xem điểm
     dưới); (4) `allocations` **không được rỗng** (mới, review lần 5 điểm 4 — batch rỗng reject
     ngay bằng `ValidationError`, **không** ghi `AuditLog` nào vì không có thao tác thật nào xảy
     ra); (5) mỗi `pr_item_id` chỉ được xuất hiện **đúng 1 lần** trong `allocations` (mới, review
     lần 5 điểm 4 — vì `po_item` cố định trong 1 lần gọi, tính duy nhất của `pr_item_id` chính là
     tính duy nhất của cặp (`pr_item`, `po_item`) trong batch; nếu không chặn, 2 phần tử trùng
     `pr_item` trong cùng batch — ví dụ `[(pr_item_1, 4), (pr_item_1, 6)]` — vẫn qua lọt điều kiện
     (11) dưới vì lúc kiểm chưa có allocation `ACTIVE` nào tồn tại sẵn cho cặp đó, tạo ra 2
     allocation `ACTIVE` cho cùng 1 cặp, đồng thời kiểm `qty <= pr_item.qty_open` riêng lẻ cho từng
     phần tử không phát hiện được tổng 4+6=10 có thể vượt `qty_open` thật của `pr_item_1`); (6) mọi
     `pr_item` trong `allocations` thuộc PR `status == APPROVED`; (7) `pr_item.product_id ==
     po_item.product_id` cho từng cặp; (8) mỗi `qty >= 1`; (9) **bắt buộc**
     `pr_item.purchase_request.linked_po_id == po_item.purchase_order_id` cho **mọi** cặp (sửa từ
     "ưu tiên kiểm... khi `linked_po_id` khác `None`" của review lần 4 — review lần 5 điểm 3: cho
     phép bỏ qua kiểm tra khi `linked_po_id` rỗng để hở đường Admin gắn 1 `pr_item` bất kỳ cùng
     `product` vào PO legacy dù 2 bên chưa từng có quan hệ lịch sử nào qua cột `linked_po` cũ, mục
     9; `linked_po_id` rỗng/`None` ⇒ **reject ngay**, ca đó không thuộc phạm vi recovery procedure
     này và cần điều tra riêng, không đi qua command); (10) mỗi `qty` không vượt
     `pr_item.qty_open`, tính **sau khi đã khoá toàn bộ `pr_item` xuất hiện trong batch theo `pk`
     tăng dần** (mới, review lần 5 điểm 4 — khoá xong toàn bộ rồi mới tính `qty_open`, nhất quán
     với guard (5) đã chặn trùng `pr_item` ngay trong input, thay vì chỉ kiểm từng cặp (`pr_item`,
     `qty`) độc lập); (11) không tạo trùng allocation `ACTIVE` đã tồn tại sẵn cho đúng cặp
     (`pr_item`, `po_item`); (12) set đầy đủ `po_no_snapshot`/`product_code_snapshot`/
     `created_by=actor` cho mỗi allocation tạo mới (giống `create_allocation()`, mục 2.4); (13) ghi
     `AuditLog` chứa `po_item`, toàn bộ `pr_item` và số lượng tương ứng trong batch; (14) **sau
     khi** tạo xong toàn bộ batch, re-assert lại lần cuối tổng allocation `ACTIVE` của `po_item`
     bằng đúng `qty_ordered` trước khi commit (double-check, không tin riêng phép so khớp ở điều
     kiện tổng phía trên).

     **Lock order của hàm batch** (review lần 5 điểm 5, làm rõ tường minh — trước đây chỉ tham
     chiếu chung "đúng lock order mục 4 điểm 2" ở điều kiện (7) cũ): hàm khoá đúng thứ tự
     `PurchaseOrder → PurchaseOrderItem → PurchaseRequestItem (order_by pk tăng dần) →
     ProcurementAllocation (order_by pk tăng dần)`, giống hệt chuỗi lock chung của nhóm Allocation
     ở mục 4 điểm 2 — đặc biệt quan trọng cho hàm này vì nó là đường **duy nhất** được phép tạo
     allocation cho PO legacy đã `APPROVED`, nên có thể chạy **đồng thời** với `send_po()` gọi trên
     cùng PO đó (điểm dưới): nếu 2 hàm khoá theo 2 thứ tự khác nhau, request reconcile và request
     gửi NCC có thể deadlock lẫn nhau thay vì 1 bên đợi bên kia xong rồi chạy tiếp bình thường.

     **Vì sao cho phép PO legacy `APPROVED`, không chỉ `DRAFT`** (review lần 4 điểm 5, nới ra từ v4 —
     v4 coi PO cũ đã `APPROVED` là "bế tắc hoàn toàn, phải sửa DB thủ công", không phù hợp với 1
     migration có recovery procedure): reconciliation ở đây **chỉ bổ sung truy vết** (traceability)
     cho dữ liệu lịch sử — **không** đổi `Product`, **không** đổi `qty_ordered`, **không** đổi
     `unit_price`/trạng thái PO — nên không vi phạm tinh thần "PO `APPROVED` trở lên khoá cứng
     PO-item" (gạch đầu dòng "PO `APPROVED` trở lên" ở trên, vốn nhắm tới việc **đổi số lượng/giá
     trị thật**, không nhắm tới việc thêm 1 dòng ghi chú lịch sử). Cho phép chạy trên PO legacy
     `DRAFT` hoặc `APPROVED`, nhưng **chỉ** qua management command Admin có audit (điểm dưới) —
     **PO mới tạo từ Stage 2 trở đi không bao giờ đi qua đường này** (chỉ áp dụng cho PO backfill từ
     `linked_po`, mục 9). Nhờ vậy PO `APPROVED` legacy có thể được reconcile hợp lệ rồi gọi
     `send_po()` bình thường, không cần can thiệp SQL trực tiếp nữa.

     **Management command chính thức, không dùng Django shell** (review lần 4 điểm 6 — sửa v4: "chỉ
     gọi qua management command/Django shell" đặt 2 con đường ngang hàng nhau, trong khi shell
     không có audit/dry-run/rollback nhất quán, không nên là *recovery procedure* được duyệt chính
     thức):

     ```
     python manage.py reconcile_legacy_po_item_allocations \
       --po-item 123 \
       --allocation 456:4 \
       --allocation 789:6 \
       --actor luckyhoang1988 \
       --dry-run
     ```

     Yêu cầu bắt buộc của command: `--actor` bắt buộc (username **duy nhất**, resolve sang đúng 1
     `User` — lỗi rõ ràng nếu username không tồn tại; rồi áp toàn bộ validation ở trên, kể cả
     `actor.is_active`); `--allocation pr_item_id:qty` lặp lại nhiều lần cho batch nhiều `pr_item`;
     `--dry-run` chạy toàn bộ validation + in ra **before/after** (`qty_ordered` hiện có, tổng
     allocation hiện có, danh sách allocation sẽ tạo) mà **không** commit gì; không có `--dry-run` ⇒
     chạy thật trong 1 `transaction.atomic()`, rollback toàn bộ nếu bất kỳ bước nào lỗi (không có
     trạng thái nửa vời). `--actor` chỉ dùng để **ghi audit** (ai chịu trách nhiệm quyết định
     reconcile) — command **không** tự kiểm tra quyền Django (`user.can(...)`) của actor đó, vì
     quyền **chạy được** `manage.py` trên server production vốn đã là 1 lớp kiểm soát truy cập
     riêng (SSH/quyền deploy), độc lập với RBAC trong ứng dụng (mới, review lần 5 điểm 5). Django
     shell chỉ còn là phương án **debug** (đọc dữ liệu, không sửa), không phải đường chạy
     reconciliation được duyệt.

     Chấp nhận là giới hạn đã biết, cực hiếm gặp vì migration mục 9 chỉ chạy 1 lần ngay đầu Stage 2
     khi tuyệt đại đa số PO cũ đã `SENT`; không có migration tự động nào xử lý thay, vì hệ thống
     không biết chắc phải reconcile theo hướng nào (giữ `qty_ordered` + tạo allocation, hay bỏ hẳn
     dòng) — cần quyết định của Admin theo từng ca cụ thể qua command trên.
5. PO đã `SENT` trở lên: allocation trỏ vào nó **không** tự động `RELEASED` nữa (đã là cam kết thật
   với NCC) — muốn huỷ phần đó phải qua quy trình đóng sớm PO (`close_po()` hiện có) hoặc GIN/GRN
   thực tế, ngoài phạm vi Allocation. Bất biến ở điểm 4 do vậy chỉ cần giữ đúng **cho tới thời điểm
   `SENT`**; sau đó giá trị được coi là lịch sử cố định — Stage 2 không có nghiệp vụ nào sửa
   `qty_ordered`/tạo thêm allocation cho PO đã `SENT`.
6. **Thuật toán chia `qty_received` theo tỷ lệ** (cho property `qty_received` ở mục 2.2, cần khi 1
   `po_item` nhận allocation từ 2+ `pr_item`): gọi `total_received =
   received_qty_by_product(po)[product_id]` (hàm đã có ở `purchasing/services.py`). Với mỗi
   `po_item`, liệt kê các allocation `ACTIVE` trỏ tới nó theo thứ tự `pk` tăng dần; mỗi allocation
   nhận `floor(total_received × qty_allocated / po_item.qty_ordered)`; phần dư (do làm tròn xuống)
   cộng hết vào allocation **cuối cùng** trong danh sách — bảo đảm tổng `qty_received` chia cho các
   `pr_item` **luôn khớp đúng bằng** `total_received` thật (không thừa/thiếu do làm tròn từng phần).
7. **Giới hạn đã biết (chấp nhận cho Stage 2, không phải bug)**: PR line có `currency != VND` vẫn
   tạo/duyệt/allocate được bình thường, nhưng `build_po_from_allocations()` chỉ nhận `unit_price`
   thuần VND do Buyer tự nhập tay (không tự quy đổi) — đúng như mục 0 điểm 2 đã nêu, vì
   `PurchaseOrderItem` chưa có field `currency`. Buyer phải tự quy đổi thủ công nếu cần tham khảo
   `estimated_unit_price`/`currency` của dòng PR gốc. Việc tự động hoá quy đổi thuộc Stage 3/4.
8. **`PUR-PR-06`** — `create_allocation()` reject nếu `pr_item.product_id is None` (chưa map
   non-catalog) — `ValidationError`, không tạo allocation, không lộ được ra `build_po_from_
   allocations()` (dòng non-catalog không xuất hiện trong danh sách dòng có thể chọn ở màn hình
   build PO, mục 5, cho tới khi được map).
9. **`PUR-PR-07`** — giữ nguyên bất biến hiện có "PR chỉ hard-delete được khi còn `DRAFT`"
   (`delete_purchase_request()`, không đổi — PR `APPROVED` không hard-delete được, tự nhiên thoả
   "PR đã phát sinh PO không được hard-delete" vì Allocation chỉ tồn tại từ `APPROVED` trở đi). Thêm
   mới: `cancel_pr_item_open_qty(pr_item, qty, reason, actor)` — chặn `qty > pr_item.qty_open`,
   bắt buộc `reason`, chỉ actor có `update` trên `pr` **và** (`is_department_manager('PURCHASING')`
   hoặc PUR Staff được `assigned_to`) mới gọi được; ghi `AuditLog`. Không có giới hạn "chỉ PR
   `APPROVED`" — dòng đã `APPROVED` mới có `qty_open > 0` để mà huỷ, điều kiện tự nhiên đã chặn.
10. Map non-catalog (`map_non_catalog_item()`) chỉ gọi được khi PR đang ở `PENDING_DEPT`,
    `PENDING_PUR`, hoặc `APPROVED` — **allow-list**, không phải chặn mỗi `DRAFT` (sửa sau khi triển
    khai thực tế, review code: chặn theo kiểu "khác `DRAFT`" vẫn lọt qua `REJECTED`, trong khi
    `REJECTED` mở lại được về `DRAFT` (`reopen_purchase_request`) — Requester có thể sửa/xoá dòng tự
    do sau khi reopen, mang đúng rủi ro "Product rác nếu Requester đổi ý" mà rule này muốn tránh
    ngay từ đầu). Đồng thời, `product` truyền vào phải đang `is_active=True` tại thời điểm gọi (khoá
    `select_for_update()` và đọc lại từ DB, không tin instance caller truyền vào) — form chọn Product
    ở `pr_item_map_product` (mục 5) chỉ hiển thị Product đang hoạt động, nhưng đó là lớp UX, không
    thay được việc service tự xác nhận lại.

## 5. Màn hình

- **`pr_create`/`pr_update` (sửa màn hình có sẵn)**: formset dòng PR thêm `required_date`,
  `currency`, `estimated_unit_price`, `budget_category`, và 1 toggle "Hàng chưa có trong danh mục" —
  bật thì ẩn ô chọn `product`, hiện 2 ô bắt buộc `non_catalog_name`/`non_catalog_uom` + 1 ô tuỳ chọn
  `non_catalog_note`; tắt thì ngược lại (sửa số field theo review lần 2, mục 2.2/mục 13 — v1/v2 nói
  nhầm "3 ô bắt buộc"). Ô `budget_category` của dòng catalog: (1) **JS** tự điền khi Requester chọn
  `product` cho 1 dòng — kể cả dòng vừa thêm động bằng formset phía client, chưa từng round-trip
  server (đọc `data-category` render sẵn trên `<option>`, không cần gọi API); (2) **server-side
  fallback** trong `clean()` nếu field vẫn rỗng lúc submit (mục 2.2) — sửa theo review lần 2, v2
  chỉ có prefill server-side nên không phủ được dòng mới thêm trước khi submit. Requester luôn sửa
  lại được trước khi lưu; dòng non-catalog để trống, không có gợi ý ở cả 2 lớp. Header thêm
  `cost_center` (bắt buộc), `project` (tuỳ chọn).
- **`po_update` (sửa màn hình có sẵn, mục 4 điểm 4 — viết lại theo review lần 4)**: khi PO nguồn
  `FROM_PR` còn `DRAFT` — formset **không có dòng `extra`** (`extra=0`; chỉ giới hạn số form hiển
  thị lúc GET, **không** tự chặn được POST giả — xem server guard ở bước 4 dưới, review lần 4 điểm
  1, sửa hiểu nhầm của v4); mọi dòng (kể cả dòng chưa có allocation) render field
  `product`/`qty_ordered` với **`disabled=True`** ở tầng `Form` (không chỉ HTML `readonly`; PO
  `source=MANUAL` không áp dụng gì, sửa tự do như hiện tại).

  **Trình tự xử lý** (review lần 4 — đổi thứ tự từ v4: so sánh raw POST giờ chạy **sau**
  `formset.is_valid()`, không phải trước, vì `is_valid()` không ghi DB nên gọi trước an toàn và đơn
  giản hơn việc tự parse management form/prefix khi chưa có gì đáng tin cậy):
  1. `transaction.atomic()`, lock `PurchaseOrder` + toàn bộ `PurchaseOrderItem` hiện có của đúng PO
     (`select_for_update()`, đúng lock order chung `PurchaseOrder → PurchaseOrderItem → ...`, mục 4
     điểm 2 — TOCTOU guard hiện tại).
  2. Khởi tạo formset **bound**, `queryset` giới hạn đúng các `PurchaseOrderItem` vừa khoá ở bước 1.
  3. Gọi `formset.is_valid()`. Nếu `False`: dừng lại, render lại form kèm lỗi, **không** chạy bước
     4-7 dưới (mới, review lần 5 điểm 2 — làm rõ nhánh else, các guard sau chỉ chạy khi `True`).
  4. **Server guard chặn bypass `extra=0` + chặn `pk` trùng**: reject (`ValidationError`) mọi form có
     `instance.pk is None` (form mới) hoặc `pk` không thuộc tập đã khoá ở bước 1 — đóng đường client
     tự sửa `items-TOTAL_FORMS` rồi POST thêm 1 dòng/1 `pk` lạ (review lần 4 điểm 1); đồng thời
     reject nếu cùng 1 `pk` xuất hiện ở **2+ form** trong formset (mới, review lần 5 điểm 1 — chặn
     client gửi 2 form trỏ cùng `PurchaseOrderItem.pk` để tránh `unit_price` bị ghi 2 lần theo thứ
     tự form).
  5. **Phát hiện tampering trên `product`/`qty_ordered`**: với từng form còn lại, lấy field key bằng
     `form.add_prefix('product')`/`form.add_prefix('qty_ordered')` (không tự ghép chuỗi thủ công) —
     key có mặt trong `request.POST` (field `disabled=True` bình thường không xuất hiện) ⇒ chuẩn hoá
     giá trị rồi so với DB, khác nhau ⇒ `ValidationError` ngay (lớp phát hiện + báo lỗi, độc lập với
     `disabled=True` là lớp bảo vệ dữ liệu — review lần 4 điểm 2, sửa lại thứ tự sai của v4).
  6. Với mỗi dòng PO-item bị đánh dấu xoá (`can_delete`) — gọi
     `delete_draft_po_item_with_allocations(po_item, actor=request.user)` (mục 4 điểm 4, thay cho
     việc tự lặp gọi `release_allocation()` rồi vẫn để `formset.save()` xoá lại — 2 tầng cùng xoá 1
     row, Nghiêm trọng #4 review lần 3).
  7. `formset.save(commit=False)`, chỉ lưu (`update_fields=['unit_price']`) các form **không** thuộc
     `formset.deleted_forms` (tránh Django tự xoá lại lần nữa các dòng đã xử lý ở bước 6).

  Cho phép sửa `unit_price` tự do khi PO còn `DRAFT` (không bị khoá, không nằm trong bước 5).
- **`pr_detail` (sửa)**: mỗi dòng PR hiển thị thêm 1 hàng phụ "Đã duyệt / Đã phân bổ / Đã đặt / Đã
  nhận / Đã huỷ / Còn mở" (7 số từ mục 2.2); dòng có `qty_open > 0` **và** PR `APPROVED` hiện nút
  "Huỷ phần còn mở" (gọi `cancel_pr_item_open_qty`); dòng non-catalog chưa map hiện badge "Chưa map
  Product" + nút "Map sang Product" (chỉ PUR Staff/Manager thấy nút, theo mục 1).
- **`pr_item_map_product` (mới)**: cho 1 `pr_item` non-catalog — chọn 1 Product đã có sẵn (nếu vừa
  được tạo bởi người khác) hoặc tạo mới (form rút gọn của `catalog` create, prefill `name` từ
  `non_catalog_name`, `uom` từ `non_catalog_uom`) rồi gán `product` cho `pr_item` trong cùng
  transaction.
- **`po_build_from_pr_lines` (mới, thay `po_create?from_pr=<pk>`)**: bước 1 chọn `supplier`; bước 2
  liệt kê mọi `PurchaseRequestItem` `qty_open > 0`, `product` đã map, thuộc PR `APPROVED`, lọc mặc
  định theo PR truyền vào qua `?from_pr=` (giữ tương thích link cũ từ `pr_detail`) nhưng cho phép
  bỏ chọn/thêm dòng từ PR khác cùng supplier; nhập `unit_price` theo từng product (1 giá cho các
  dòng cùng product, vì sẽ gộp — mục 4 điểm 3); submit tạo `PurchaseOrder(DRAFT, source=FROM_PR)` +
  `PurchaseOrderItem` (gộp theo product) + 1 `ProcurementAllocation` mỗi (pr_item, po_item) — tất cả
  trong 1 transaction. `po_create` (manual, không qua PR) giữ nguyên, không đổi.
- **`exchange_rate_list`/`exchange_rate_create`/`exchange_rate_update`/`exchange_rate_delete` (mới,
  Admin only)**: danh sách + form tạo/sửa/xoá 1 dòng `ExchangeRate` (`currency`, `rate_date`,
  `rate_to_vnd`) — cả 4 view đều gate `user.role == ADMIN` hoặc `is_superuser`, ghi `AuditLog` (mục
  2.3, mục 7). Không dùng Django Admin/`purchasing/admin.py` cho việc này (khác v1).

## 6. Notification

- Map non-catalog quá hạn: management command mới (`manage.py check_non_catalog_sla`, chạy qua cron
  — đúng pattern `⏸️`) — tìm `PurchaseRequestItem` có `product__isnull=True`, thuộc PR đã có
  `Approval(department=PURCHASING)` (mốc "PUR tiếp nhận", dùng
  `accounts.approvals.latest_approvals_for`/truy `Approval.created_at`) quá **3 ngày làm việc**
  (tính lùi bỏ Thứ 7/CN, chưa tính lịch nghỉ lễ — đủ cho MVP, đúng cách đơn giản hoá `⏸️`), gửi
  `Notification` cho PUR department manager + `pr.assigned_to` (nếu có).
- Allocation vừa `RELEASED` (thủ công bởi PUR Staff, hoặc cascade tự động do xoá dòng
  `PurchaseOrderItem` khi PO còn `DRAFT` — mục 4 điểm 4; sửa wording theo review lần 2, Stage 2
  không có khái niệm "xoá PO", chỉ có xoá 1 dòng PO-item): notify `pr_item.purchase_request.
  requested_by` — dòng của họ vừa mất phân bổ, `qty_open` tăng trở lại.

## 7. Audit log

Log qua `accounts.AuditLog`/`log_action()` cho mọi transition mới: `create_allocation`,
`release_allocation` (**kể cả** khi cascade tự động do xoá dòng `PurchaseOrderItem` khi PO còn
`DRAFT` — actor ghi đúng **`request.user` thật** đã bấm xoá dòng đó trên `po_update`, **không phải**
`actor=None`; sửa lỗi Quan trọng #5 của review lần 2, v2 từng coi nhầm case này là
"system-triggered" giống `sync_expired_batches`, trong khi đây là hệ quả trực tiếp của 1 thao tác
người dùng thật. `actor=None` từ nay **chỉ** dùng cho data migration backfill thật — mục 2.4/mục
9), `cancel_pr_item_open_qty`, `map_non_catalog_item`, `ExchangeRate` tạo/sửa/xoá (cả 3 thao tác,
không chỉ tạo mới — mục 2.3/mục 5), `decide_purchase_request` nhánh approve ghi thêm `qty_approved`
từng dòng vào `description` nếu khác `qty_requested` (để audit trail thấy rõ dòng nào bị duyệt
giảm), và **`reconcile_legacy_po_item_allocations`** (mới ở review lần 4, mục 4 điểm 4/mục 8/mục 9)
— 1 dòng `AuditLog` cho mỗi lần chạy command, `description` liệt kê `po_item`, toàn bộ `pr_item` và
`qty` tương ứng trong batch, actor luôn là Admin thật truyền qua `--actor`, không phải actor rỗng.

## 8. Validation

- `PurchaseRequestItem.clean()`: đúng 1 trong 2 — `product` có giá trị **XOR** cả 2 field bắt buộc
  (`non_catalog_name`, `non_catalog_uom`) khác rỗng (`non_catalog_note` không tham gia điều kiện
  XOR vì luôn tuỳ chọn — sửa số field theo review lần 2, mục 2.2/mục 13); không cho vừa có `product`
  vừa có `non_catalog_name`/`non_catalog_uom`, không cho cả hai đều rỗng. `budget_category`: nếu
  rỗng và dòng là catalog (`product` khác rỗng) ⇒ tự lấy `product.category` (fallback bắt buộc, mục
  2.2 — không chỉ dựa vào JS phía client); sau đó chuẩn hoá `strip()` + gộp khoảng trắng thừa trước
  khi so sánh rỗng/lưu.
- `create_allocation()`: `pr_item.product_id == po_item.product_id` (mục 2.4), `qty <=
  pr_item.qty_open` dưới khoá theo đúng **lock order chung**
  `PurchaseOrder → PurchaseOrderItem → PurchaseRequestItem → ProcurementAllocation` (mục 4 điểm 2),
  `pr_item.purchase_request.status == APPROVED`, `po_item.purchase_order.status == DRAFT` (sửa theo
  review lần 3, Nghiêm trọng #1 — thu hẹp từ `(DRAFT, APPROVED)`: PO `APPROVED` không còn được tạo
  allocation nữa, khớp quy tắc mục 4 điểm 4; không tạo allocation mới thẳng vào PO đã `SENT` — muốn
  thêm hàng vào PO đã gửi phải qua revision, ngoài phạm vi Stage 2). Set `po_no_snapshot`/
  `product_code_snapshot` từ `po_item` ngay khi tạo (mục 2.4). **Đồng thời tăng `po_item.qty_ordered`
  đúng bằng `qty` vừa allocate** (`F('qty_ordered') + qty`, cùng transaction — mới ở review lần 3,
  Nghiêm trọng #1, mục 4 điểm 4: đây là điểm duy nhất được tăng `qty_ordered`, kể cả khi thêm
  allocation vào 1 `po_item` đã tồn tại từ trước, không chỉ lúc `build_po_from_allocations()`).
- `release_allocation(allocation, reason, actor, *, delete_empty_po_item=True)`: khoá theo đúng lock
  order như `create_allocation()`; chuyển `status=RELEASED`, set `released_reason`/`released_by`/
  `released_at`, **đồng thời trừ `po_item.qty_ordered` đúng bằng `qty_allocated` vừa release trong
  cùng transaction** (mục 3, mục 4 điểm 4 — bất biến của review lần 2); nếu `qty_ordered` về 0 **và**
  `delete_empty_po_item=True` (mặc định, dùng cho ca release đơn lẻ) ⇒ xoá hẳn `po_item`; nếu
  `delete_empty_po_item=False` (dùng bởi `delete_draft_po_item_with_allocations()`, dưới) ⇒ **không**
  tự xoá, để lời gọi bên ngoài tự quản lý xoá đúng 1 lần (tham số mới ở review lần 3, Nghiêm trọng
  #4 — tránh 2 tầng cùng xoá 1 row).
- **`delete_draft_po_item_with_allocations(po_item, actor)`** (mới ở review lần 3, Nghiêm trọng #4,
  mục 4 điểm 4): khoá theo đúng lock order; chỉ áp dụng khi `po_item.purchase_order.status ==
  DRAFT`; gọi `release_allocation(..., delete_empty_po_item=False)` cho mọi allocation `ACTIVE` trỏ
  tới `po_item`, rồi tự xoá `po_item` đúng 1 lần (kể cả khi không có allocation nào — dòng legacy);
  ghi `AuditLog` cho từng release **và** cho chính thao tác xoá, actor luôn là `request.user` thật
  truyền vào, không phải `actor=None`.
- **`reconcile_legacy_po_item_allocations(po_item, allocations, actor)`** (đổi thành **batch** ở
  review lần 4, thay hàm đơn lẻ `reconcile_legacy_po_item_allocation()` của v4 — mục 4 điểm 4 "Ghi
  chú dữ liệu cũ" giải thích lý do đổi; hàm one-off, không lộ qua UI/luồng tạo PO thông thường, chỉ
  gọi qua management command `reconcile_legacy_po_item_allocations` chuyên dụng, không dùng Django
  shell làm recovery procedure): `allocations` là danh sách cặp `(pr_item, qty)`. Khoá đúng thứ tự
  `PurchaseOrder → PurchaseOrderItem → PurchaseRequestItem (pk tăng dần) → ProcurementAllocation (pk
  tăng dần)` (giống mục 4 điểm 2 — đặc biệt quan trọng vì đây là đường duy nhất tạo allocation được
  cho PO legacy đã `APPROVED`, có thể chạy đồng thời với `send_po()` trên cùng PO, review lần 5 điểm
  5). Validate đầy đủ (mục 4 điểm 4, không chỉ kiểm tổng): actor `role == ADMIN`/`is_superuser` **và**
  `actor.is_active == True`, chưa soft-delete (review lần 5 điểm 5); `po_item.purchase_order.
  source == FROM_PR`; `po_item.purchase_order.status` thuộc `{DRAFT, APPROVED}`; `allocations`
  **không rỗng** (review lần 5 điểm 4); mỗi `pr_item_id` chỉ xuất hiện **đúng 1 lần** trong
  `allocations` (review lần 5 điểm 4 — chặn 2 phần tử trùng `pr_item` ngay trong input); mọi `pr_item`
  thuộc PR `APPROVED`; `pr_item.product_id == po_item.product_id` từng cặp; mỗi `qty >= 1`; mỗi `qty`
  không vượt `pr_item.qty_open` (tính sau khi đã khoá toàn bộ `pr_item` trong batch); **bắt buộc**
  `pr_item.purchase_request.linked_po_id == po_item.purchase_order_id` cho mọi cặp — `linked_po_id`
  rỗng ⇒ reject (sửa từ "ưu tiên kiểm... khi khác `None`", review lần 5 điểm 3); không tạo trùng
  allocation `ACTIVE` đã có cho đúng cặp. Tạo **toàn bộ** allocation trong batch (mỗi allocation set
  `po_no_snapshot`/`product_code_snapshot`/`created_by=actor` như `create_allocation()`) trong
  **cùng 1 transaction**, **không** cộng bất kỳ `qty` nào vào `po_item.qty_ordered` (ngoại lệ duy
  nhất — giá trị đó đã là dữ liệu lịch sử cố định từ trước Stage 2); chặn (`ValidationError`,
  rollback toàn bộ batch) nếu `tổng allocation ACTIVE hiện có + tổng qty trong batch !=
  po_item.qty_ordered` (dùng đúng dấu `==` để so khớp, không phải `<=`) — 1 dòng sai trong batch làm
  rollback hết, không tạo dở dang. Ghi `AuditLog` chứa `po_item`, toàn bộ `pr_item`/`qty` trong
  batch. Sau khi tạo xong, re-assert lại lần cuối tổng allocation bằng `qty_ordered` trước khi
  commit.
- `po_update` (`PurchaseOrderItemFormSet`, mục 5, viết lại theo review lần 4): formset `extra=0` cho
  PO nguồn `FROM_PR` — chỉ giới hạn hiển thị lúc GET, **không** tự chặn POST giả (review lần 4 điểm
  1); field `product`/`qty_ordered` của **mọi** dòng PO nguồn `FROM_PR` khai báo `disabled=True` ở
  `Form` (không chỉ HTML `readonly`). Trình tự đúng (review lần 4 điểm 2 — đảo lại thứ tự sai của
  v4): (1) `transaction.atomic()`, lock `PurchaseOrder` + toàn bộ `PurchaseOrderItem` hiện có; (2)
  khởi tạo formset bound với `queryset` giới hạn đúng tập đã khoá; (3) gọi `formset.is_valid()` —
  nếu `False`, dừng lại, render lỗi, không chạy (4)-(7) (review lần 5 điểm 2); (4) reject mọi form có
  `instance.pk is None` hoặc `pk` không thuộc tập đã khoá (đóng đường bypass `extra=0`), **và** reject
  nếu cùng 1 `pk` xuất hiện ở 2+ form trong formset (review lần 5 điểm 1 — chặn ghi `unit_price` 2
  lần cho cùng 1 dòng); (5) với từng form còn lại, lấy key bằng `form.add_prefix('product')`/
  `form.add_prefix('qty_ordered')`, nếu key có mặt trong `request.POST` (field `disabled=True`
  thường không xuất hiện) thì so giá trị chuẩn hoá với DB — khác nhau ⇒ `ValidationError` ngay (lớp
  phát hiện tampering, độc lập với `disabled=True` là lớp bảo vệ dữ liệu); (6) dòng bị xoá ⇒ gọi
  `delete_draft_po_item_with_allocations(po_item, actor=request.user)` (không phải để
  `formset.save()` tự xoá, Nghiêm trọng #4 review lần 3); (7) `formset.save(commit=False)` + chỉ lưu
  các form không thuộc `deleted_forms` (`unit_price` only).
- `send_po()`: thêm guard PO `source=FROM_PR` ⇒ chặn nếu tồn tại dòng `qty_ordered != tổng
  qty_allocated (ACTIVE)` (mục 4 điểm 4, chốt từ review lần 2). Test cho guard này phải dựng PO ở
  `APPROVED` — dựng ở `DRAFT` sẽ bị chặn bởi điều kiện "chỉ gửi PO `APPROVED`" có từ trước, không
  chạm được tới guard mới (review lần 3, Nghiêm trọng #3, xem `TC-PUR-PR-05-008`).
- `ExchangeRateForm` (`create`/`update`): `rate_date` không được là ngày tương lai (nhập tỷ giá cho
  ngày chưa tới là vô nghĩa — tỷ giá thực tế chưa biết); `currency != VND` (mục 2.3).
- Form PR: `required_date` không được là ngày trong quá khứ so với ngày nộp; `budget_category` bắt
  buộc cho mọi dòng catalog lẫn non-catalog, có fallback tự động cho dòng catalog nếu để trống (mục
  2.2).

## 9. Migration dữ liệu cũ

**Schema** (migration `purchasing/0017_...`): thêm field mục 2.1/2.2 — bao gồm `budget_category`
(toàn bộ nullable/có default — không cần `RunPython` guard kiểu `PUR-FND-06`, vì không có
`UniqueConstraint`/`NOT NULL` mới nào có thể bị dữ liệu cũ vi phạm), tạo bảng `ExchangeRate`, tạo
bảng `ProcurementAllocation` (`po_item` đã là `null=True`/`SET_NULL` + 2 cột snapshot ngay từ đầu,
mục 2.4 — bảng mới tạo lần đầu ở Stage 2 nên không có vấn đề tương thích ngược nào ở đây).

**Backfill `ProcurementAllocation` từ `linked_po`** (`purchasing/0018_...`, `RunPython`, dùng
`apps.get_model()` — không import `purchasing.models`/`purchasing.services`, đúng nguyên tắc đã
chốt ở `PUR-FND-06`). `created_by=None` ở bước 2 dưới đây chỉ chạy được vì `ProcurementAllocation.
created_by` đã sửa thành `null=True`/`SET_NULL` ở mục 2.4 (sửa lỗi Nghiêm trọng #3 của review lần 2
— v2 khai báo `PROTECT` sẽ khiến migration này crash ngay khi chạy thật):

1. Với mỗi `PurchaseRequest` có `linked_po` khác `None`: lấy toàn bộ `items` của PR và toàn bộ
   `items` của `linked_po`, map theo `product_id`.
2. **Khớp rõ ràng** (đúng 1 `PurchaseRequestItem` và đúng 1 `PurchaseOrderItem` cùng `product` cho
   cặp PR/PO đang xét): tạo 1 `ProcurementAllocation(status=ACTIVE, qty_allocated=min(pr_item.
   qty_requested, po_item.qty_ordered), created_by=None, po_no_snapshot=po_item.purchase_order.
   po_no, product_code_snapshot=po_item.product.product_code)` (2 cột snapshot mục 2.4 set giống
   hệt cách `create_allocation()` set, không riêng gì đường backfill); nếu `pr_item.qty_requested !=
   po_item.qty_ordered`, vẫn tạo allocation với giá trị `min(...)` nhưng ghi vào **báo cáo ngoại
   lệ** (điểm 4) vì lệch số lượng — không tự suy đoán lý do lệch.
3. Set `qty_approved = qty_requested` cho các `pr_item` được backfill (PR cũ đã `APPROVED` trước
   Stage 2 không có khái niệm duyệt-giảm-số-lượng, coi như duyệt đúng số đã xin).
4. **Không khớp rõ ràng** (PR có 2+ dòng cùng product, PO có 2+ dòng cùng product tương ứng nhiều
   PR khác nhau, hoặc product ở PR không xuất hiện trong `linked_po`): **không** tạo allocation tự
   động — ghi vào báo cáo ngoại lệ (in ra `stdout` khi chạy migration + gợi ý chạy management
   command riêng `report_allocation_migration_exceptions` để xem lại bất kỳ lúc nào sau đó, không
   chỉ lúc migrate). Các trường hợp này giữ `qty_approved = qty_requested` nhưng **không** có
   allocation ⇒ `qty_open = qty_requested` (hiện lại như "còn mở"), cần PUR Staff xử lý thủ công
   (tạo allocation đúng qua UI mới, hoặc để nguyên nếu PR/PO đó đã đóng hẳn — không có deadline bắt
   buộc xử lý báo cáo ngoại lệ, khác SLA non-catalog). Nếu `linked_po` tương ứng còn `DRAFT` hoặc
   `APPROVED` (hiếm — xem mục 4 điểm 4 "Ghi chú dữ liệu cũ"), guard mới trên `send_po()` sẽ chặn gửi
   PO đó cho tới khi được reconcile qua management command `reconcile_legacy_po_item_allocations`
   (hàm batch, chạy được cả PO `DRAFT` lẫn `APPROVED` — mở rộng ở review lần 4, mục 4 điểm 4/mục 8)
   hoặc `delete_draft_po_item_with_allocations()` (chỉ dùng được khi PO còn `DRAFT`); không ảnh
   hưởng gì tới PO cũ đã `SENT`.

**`linked_po` sau migration**: giữ nguyên cột, **không xoá, không còn được ghi mới** — `build_po_
from_allocations()` không set `linked_po` (PO mới từ Stage 2 trở đi trace qua
`ProcurementAllocation` duy nhất). Đánh dấu deprecated trong docstring model, việc gỡ cột hẳn để
lại cho 1 Stage sau khi đã xác nhận không còn UI nào đọc nó nữa.

Guard bắt buộc theo đúng pattern đã chốt (migration thêm `UniqueConstraint`/backfill dữ liệu quan
trọng cần idempotent-check): chạy lại migration lần 2 trên DB đã backfill rồi phải là no-op (kiểm
tra `ProcurementAllocation` đã tồn tại cho cặp `pr_item`/`po_item` trước khi tạo lại).

## 10. Acceptance criteria

1. Tạo PR mới thiếu `cost_center`/`required_date`/`currency`/`estimated_unit_price`/
   `budget_category` ở 1 dòng ⇒ form/formset báo lỗi đúng field, không lưu được.
2. Bật toggle non-catalog trên 1 dòng, điền đủ `non_catalog_name` + `non_catalog_uom` (để trống
   `non_catalog_note`) ⇒ lưu được; để trống 1 trong 2 field bắt buộc ⇒ báo lỗi; tắt toggle mà không
   chọn `product` ⇒ báo lỗi (sửa theo review lần 2 — đúng 2 field bắt buộc, không phải 3, mục 2.2/
   mục 13).
3. PUR Manager duyệt PR ở `PENDING_PUR`, sửa `qty_approved` 1 dòng xuống thấp hơn `qty_requested`
   ⇒ sau approve, `pr_item.qty_approved` đúng giá trị đã sửa, `qty_open` tính đúng theo giá trị đó
   (không phải `qty_requested`).
4. `create_allocation()` gọi với `qty > qty_open` ⇒ `ValidationError`, không tạo row.
5. Tạo 1 dòng PO-item bằng `build_po_from_allocations()` (2 dòng PR cùng `product` vào 1 PO) ⇒ ngay
   sau khi tạo, `qty_ordered` của `PurchaseOrderItem` đã bằng đúng tổng `qty_allocated` của 2
   `ProcurementAllocation` `ACTIVE` vừa tạo (bất biến mục 4 điểm 4 đúng ngay từ thời điểm tạo).
6. **[Mới, review lần 3, Nghiêm trọng #1]** Gọi `create_allocation()` thêm 1 lần nữa vào 1 `po_item`
   **đã tồn tại từ trước** (không phải lúc build PO mới, ví dụ PO đã có `qty_ordered=10` từ 1
   allocation trước đó) ⇒ sau khi tạo, `qty_ordered` tăng đúng bằng `qty` vừa allocate (thành 10 +
   `qty`), bất biến mục 4 điểm 4 vẫn đúng ngay sau thao tác.
7. **[Mới, review lần 3, Nghiêm trọng #1]** Gọi `create_allocation()`/`release_allocation()` trên 1
   `po_item` mà `po_item.purchase_order.status == APPROVED` ⇒ cả hai đều `ValidationError`, không
   tạo/không đổi allocation nào (thu hẹp từ `(DRAFT, APPROVED)` xuống đúng `DRAFT`).
8. **[Viết lại, review lần 4]** Mở `po_update` cho PO `FROM_PR` còn `DRAFT` ⇒ field
   `product`/`qty_ordered` của **mọi** dòng (kể cả dòng chưa có allocation nào) render `disabled`
   (không phải input sửa được); cố POST giá trị khác giá trị hiện có cho 2 field này ở bất kỳ dòng
   nào (bypass client) ⇒ `formset.is_valid()` vẫn `True` (giá trị bị `disabled=True` bỏ qua), nhưng
   bước so sánh raw POST **sau** `is_valid()` (dùng `form.add_prefix('product')`/
   `form.add_prefix('qty_ordered')` để lấy key, review lần 4 điểm 2 — sửa thứ tự sai của v4) phát
   hiện key có mặt trong `request.POST` và khác giá trị DB ⇒ `ValidationError`, không lưu gì.
9. **[Viết lại, review lần 4 điểm 1]** `po_update` cho PO `FROM_PR` ⇒ formset `extra=0` (không hiện
   form trống lúc GET), nhưng cố POST **thêm** 1 form mới bằng cách tự tăng `items-TOTAL_FORMS` và
   gửi kèm dữ liệu form đó (bypass giả định "`extra=0` là đủ" của v4) ⇒ server guard sau
   `formset.is_valid()` phát hiện `form.instance.pk is None` và reject (`ValidationError`), không
   tạo `PurchaseOrderItem` nào (chỉ tạo được qua `po_build_from_pr_lines`); tương tự, POST 1 `pk`
   thuộc `PurchaseOrderItem` của **PO khác** (không phải PO đang sửa) ⇒ cũng bị reject vì `pk` không
   thuộc tập đã khoá của PO hiện tại. **[Mới, review lần 5 điểm 1]** POST 2 form cùng trỏ 1 `pk`
   thuộc đúng PO đang sửa (tự tăng `items-TOTAL_FORMS`, lặp lại y hệt 1 form hợp lệ) ⇒ cũng bị
   reject (`ValidationError`) dù cả 2 form đều có `pk` hợp lệ và thuộc đúng PO — không lưu
   `unit_price` nào từ cả 2 form (xem AC34).
10. **[Mới, review lần 3, Nghiêm trọng #2]** Sửa `unit_price` của 1 dòng PO nguồn `FROM_PR` còn
    `DRAFT` (dòng có hoặc không có allocation đều được) ⇒ lưu thành công (field này không bị khoá,
    khác `product`/`qty_ordered`).
11. Xoá 1 dòng `PurchaseOrderItem` của PO còn `DRAFT` có allocation ⇒ mọi allocation liên quan
    chuyển `RELEASED` trong cùng transaction (giữ `po_no_snapshot`/`product_code_snapshot` đúng,
    `AuditLog` ghi đúng `request.user` đã thao tác, không phải actor rỗng), `pr_item.qty_open` tăng
    lại đúng bằng phần vừa release, và dòng `PurchaseOrderItem` bị xoá hẳn (vì `qty_ordered` về 0),
    **không phát sinh lỗi nào từ việc xoá 2 lần** (`delete_draft_po_item_with_allocations()` xoá
    đúng 1 lần, mới ở review lần 3, Nghiêm trọng #4).
12. **[Mới, review lần 3, Nghiêm trọng #4]** Xoá 1 dòng `PurchaseOrderItem` **legacy** (`qty_ordered
    > 0`, không có allocation nào trỏ tới, mô phỏng dữ liệu backfill mơ hồ) qua `po_update` ⇒
    `delete_draft_po_item_with_allocations()` xoá thẳng dòng, không gọi `release_allocation()` nào
    (không có gì để release), không lỗi.
13. `release_allocation()` release 1 phần allocation của 1 dòng PO-item còn allocation `ACTIVE` khác
    (chưa về 0) ⇒ `qty_ordered` của dòng đó **giảm đúng bằng** `qty_allocated` vừa release, dòng
    PO-item **không** bị xoá (còn allocation khác đang giữ), bất biến mục 4 điểm 4 vẫn đúng ngay sau
    thao tác.
14. **[Sửa, review lần 3, Nghiêm trọng #3]** PO `source=FROM_PR` ở trạng thái **`APPROVED`** (không
    phải `DRAFT` — dựng `DRAFT` sẽ bị chặn bởi điều kiện "chỉ gửi PO `APPROVED`" có từ trước, không
    chạm được guard mới) có 1 dòng cố tình để `qty_ordered != tổng qty_allocated (ACTIVE)` (dựng
    thẳng qua fixture, mô phỏng dữ liệu cũ) ⇒ `send_po()` chặn (`ValidationError`, liệt kê đúng dòng
    vi phạm), PO **vẫn `APPROVED`** (không chuyển `SENT`), **không** ghi `AuditLog` "gửi PO", **không**
    gọi `send_mail`.
15. `create_allocation()` gọi với `pr_item.product_id is None` (chưa map) ⇒ `ValidationError`.
16. `map_non_catalog_item()` gán `product` cho `pr_item` ⇒ dòng đó xuất hiện trong danh sách chọn ở
    `po_build_from_pr_lines` ngay sau đó (không cần refresh gì đặc biệt).
17. `cancel_pr_item_open_qty()` gọi với `qty > qty_open` ⇒ chặn; gọi hợp lệ ⇒ `qty_cancelled` tăng
    đúng, `qty_open` giảm đúng, có `AuditLog` chứa `reason`. Gọi bởi PUR Staff không phải
    `assigned_to` của PR đó ⇒ chặn (mục 1, mục 4 điểm 9), kể cả khi PUR Staff đó có quyền `update`
    trên `pr`.
18. 1 `po_item` nhận allocation từ 2 `pr_item` (10 và 5, tổng `qty_ordered=15`), GRN ghi nhận
    `qty_received=9` cho product đó trên PO ⇒ `qty_received` của 2 `pr_item` cộng lại đúng bằng 9,
    không thừa/thiếu do làm tròn (mục 4 điểm 6).
19. 1 `pr_item` có `qty_approved=100` được allocate 40 vào PO A và 60 vào PO B (2 PO khác nhau) ⇒
    tạo đúng 2 `ProcurementAllocation`, `qty_allocated=100`, `qty_open=0`; allocate thêm bất kỳ số
    dương nào nữa ⇒ chặn; release allocation ở PO A ⇒ `qty_open` quay lại 40 **và** `qty_ordered`
    của PO-item ở PO A giảm đúng 40 (case "1 PR → n PO" theo Exit Criteria Stage 2 của
    `PUR_EXPANSION_MASTER_PLAN.md`, bổ sung phần `qty_ordered` theo bất biến mới).
20. Migration backfill trên fixture có 1 PR/PO khớp rõ ràng 1-1 và 1 PR/PO có 2 dòng cùng product
    (khớp mơ hồ) ⇒ trường hợp đầu tạo đúng 1 allocation (`created_by=None`, không crash — bất biến
    field nullable mới ở mục 2.4); trường hợp sau **không** tạo allocation, xuất hiện trong báo cáo
    ngoại lệ.
21. Chạy lại migration backfill (hoặc gọi lại `report_allocation_migration_exceptions`) lần 2 trên
    dữ liệu đã backfill ⇒ không tạo thêm allocation trùng.
22. User không phải Admin/superuser gọi `exchange_rate_create`/`exchange_rate_update`/
    `exchange_rate_delete` ⇒ `403` ở cả ba view.
23. `check_non_catalog_sla` chạy trên fixture có 1 dòng non-catalog PUR tiếp nhận cách đây 4 ngày
    làm việc (chưa map) ⇒ tạo `Notification` cho PUR manager; dòng mới tiếp nhận 1 ngày ⇒ không tạo.
24. Cố `INSERT` thẳng (mô phỏng bypass tầng service) 1 `ProcurementAllocation` có `status=ACTIVE` và
    `po_item=NULL` ⇒ vi phạm `active_allocation_requires_po_item`, `IntegrityError` — DB tự chặn
    được kể cả khi tầng service có bug (mục 2.4, mới ở review lần 2).
25. **[Mới, review lần 3, Quan trọng #4]** Cố `INSERT` thẳng 1 `ProcurementAllocation` có
    `qty_allocated=0` ⇒ vi phạm `allocation_qty_positive`, `IntegrityError` (`PositiveIntegerField`
    tự sinh chỉ đảm bảo `>= 0`, không phải `>= 1`).
26. **[Mới, review lần 3, Quan trọng #4]** Cố `INSERT` thẳng 1 `ProcurementAllocation` có
    `po_no_snapshot=''` hoặc `product_code_snapshot=''` ⇒ vi phạm `allocation_snapshots_required`,
    `IntegrityError`.
27. Thêm 1 dòng PR mới bằng formset JS phía client (chưa từng round-trip server) và chọn `product`
    cho dòng đó mà **không** đụng tới ô `budget_category` ⇒ sau khi submit, giá trị được lưu đúng
    bằng `product.category` (server-side fallback vẫn đúng dù JS client không kịp/không chạy được
    trong môi trường test, mục 2.2, mới ở review lần 2).
28. **[Sửa, review lần 4]** Dựng 1 `po_item` legacy `qty_ordered=10`, 0 allocation ⇒ gọi
    `reconcile_legacy_po_item_allocations(po_item, allocations=[(pr_item, 10)], actor=admin)` ⇒ tạo
    đúng 1 allocation `ACTIVE` qty=10, **`qty_ordered` vẫn giữ nguyên 10** (không cộng thêm); gọi
    thêm lần nữa với `allocations=[(pr_item_khác, 1)]` bất kỳ ⇒ `ValidationError` (tổng allocation đã
    bằng `qty_ordered`, không còn chỗ).
29. **[Mới, review lần 4 điểm 3]** Dựng 1 `po_item` legacy `qty_ordered=10`, 0 allocation, cần 2
    `pr_item` khớp vào (4 + 6) ⇒ gọi 1 lần duy nhất
    `reconcile_legacy_po_item_allocations(po_item, allocations=[(pr_item_1, 4), (pr_item_2, 6)],
    actor=admin)` ⇒ tạo đúng 2 allocation `ACTIVE` (4 và 6), tổng khớp **chính xác** `qty_ordered=10`
    ngay sau khi commit; gọi với tổng `allocations` chỉ bằng 9 (thiếu 1) hoặc 11 (thừa 1) ⇒ cả hai
    đều `ValidationError`, không tạo allocation nào (rule dùng `==`, không phải `<=`).
30. **[Mới, review lần 4 điểm 3]** Batch `allocations=[(pr_item_1, 4), (pr_item_2, 6)]` mà
    `pr_item_2` có `qty=6` vượt `pr_item_2.qty_open` (validation không hợp lệ ở dòng thứ 2) ⇒ toàn
    bộ batch rollback — **không** allocation nào được tạo, kể cả `pr_item_1` (dòng hợp lệ đứng
    trước) — kiểm chứng tính atomic của batch.
31. **[Mới, review lần 4 điểm 4, mở rộng review lần 5]** Gọi `reconcile_legacy_po_item_allocations()`
    lần lượt vi phạm từng điều kiện validation (mỗi kịch bản dựng riêng, giữ các điều kiện khác hợp
    lệ): actor không phải Admin/superuser; **actor `is_active == False`** (mới, review lần 5 điểm 5);
    `po_item.purchase_order.source == MANUAL`; 1 `pr_item` trong batch thuộc PR chưa `APPROVED`; 1
    `pr_item.product_id != po_item.product_id`; 1 `qty < 1`; 1 `qty` vượt `pr_item.qty_open`;
    `pr_item.purchase_request.linked_po_id` khác `po_item.purchase_order_id`; đã tồn tại sẵn 1
    allocation `ACTIVE` cho đúng cặp (`pr_item`, `po_item`) đó ⇒ **mỗi** kịch bản đều
    `ValidationError`, không tạo allocation nào. (Ca `linked_po_id` rỗng/`None` và ca 2 phần tử
    trùng `pr_item` trong cùng batch tách riêng thành AC36/AC35 dưới, vì cả hai đều cần fixture
    riêng để không lẫn với các kịch bản trên.)
32. **[Mới, review lần 4 điểm 5]** Dựng PO legacy `source=FROM_PR`, `status=APPROVED` (không phải
    `DRAFT`), 1 `po_item` legacy `qty_ordered=10`, 0 allocation ⇒ gọi
    `reconcile_legacy_po_item_allocations()` với batch khớp đúng 10 ⇒ tạo allocation thành công (PO
    `APPROVED` không còn là bế tắc); `Product`/`qty_ordered`/`unit_price`/`PurchaseOrder.status`
    không đổi gì sau khi reconcile; gọi `send_po()` ngay sau đó ⇒ không bị guard mục 4 điểm 4 chặn
    nữa (bất biến đã khớp).
33. **[Mới, review lần 4 điểm 6]** `manage.py reconcile_legacy_po_item_allocations --dry-run` với
    tham số hợp lệ ⇒ in ra before/after (`qty_ordered` hiện có, tổng allocation hiện có, danh sách
    allocation sẽ tạo), **không** tạo row nào trong DB; chạy lại **không** có `--dry-run` với cùng
    tham số ⇒ tạo đúng allocation, commit thật; chạy với 1 `--allocation` sai định dạng hoặc 1
    `pr_item_id` không tồn tại ⇒ lệnh thoát lỗi, rollback toàn bộ, không tạo allocation nào.
34. **[Mới, review lần 5 điểm 1]** `po_update` cho PO `FROM_PR`, 1 `PurchaseOrderItem` có sẵn ⇒ POST
    formset với 2 form khác nhau cùng trỏ **đúng 1 `pk`** đó (client tự tăng `items-TOTAL_FORMS` và
    lặp lại y hệt dữ liệu form hợp lệ, mỗi form 1 `unit_price` khác nhau) ⇒ `ValidationError`, không
    lưu `unit_price` nào (không phải "last write wins" theo thứ tự form).
35. **[Mới, review lần 5 điểm 4]** Batch `allocations=[(pr_item_1, 4), (pr_item_1, 6)]` (cùng 1
    `pr_item` xuất hiện 2 lần) ⇒ `ValidationError` ngay từ bước kiểm trùng trong input, **không** tạo
    allocation nào — kể cả khi `pr_item_1.qty_open >= 10` (tức nếu không chặn trùng thì cả 2 dòng có
    thể qua lọt do validate riêng lẻ).
36. **[Mới, review lần 5 điểm 3]** Batch hợp lệ về số lượng (tổng khớp đúng `qty_ordered`) nhưng
    `pr_item.purchase_request.linked_po_id` là `None`/rỗng (PR chưa từng liên kết `linked_po` với PO
    nào) ⇒ `ValidationError`, không tạo allocation nào — kể cả khi `pr_item.product_id ==
    po_item.product_id` và mọi điều kiện khác đều hợp lệ.
37. **[Mới, review lần 5 điểm 4]** Gọi `reconcile_legacy_po_item_allocations(po_item, allocations=[],
    actor=admin)` (batch rỗng) ⇒ `ValidationError`, không tạo allocation nào, **không** ghi
    `AuditLog` nào (khác các ca reject khác — batch rỗng nghĩa là chưa có thao tác thật nào được yêu
    cầu).
38. **[Mới, review lần 5 điểm 5, concurrency]** 2 thread trên cùng PO legacy `source=FROM_PR`,
    `status=APPROVED`, 1 `po_item` chưa reconcile: thread A gọi
    `reconcile_legacy_po_item_allocations()` (batch khớp đúng `qty_ordered`), thread B gọi
    `send_po()` cùng lúc ⇒ không `OperationalError` (không deadlock); nếu A thắng lock trước, B chạy
    sau và thành công (bất biến đã khớp); nếu B thắng lock trước (khi bất biến còn sai), B bị guard
    mục 4 điểm 4 chặn, PO vẫn `APPROVED`, và A vẫn reconcile thành công sau đó — cả 2 khả năng thắng
    cuộc đều nhất quán, không có kết quả dở dang.

## 11. Test case

Convention `TC-PUR-PR-0X-00Y` (theo `FR-XX-##`, cùng tiền tố `PUR` như `TC-PUR-FND-*` để phân biệt
sáng kiến ngoài 60-FR — xem `01_foundation_fsd.md` mục 11):

- `TC-PUR-PR-01-001`: PR draft 1 dòng thiếu `required_date` → `is_valid() == False`.
- `TC-PUR-PR-01-002`: PR draft 1 dòng non-catalog đủ 2 field bắt buộc (`non_catalog_name`,
  `non_catalog_uom`), để trống `non_catalog_note`, không chọn `product` → lưu thành công (sửa theo
  review lần 2 — 2 field bắt buộc, không phải 3, mục 2.2/mục 13).
- `TC-PUR-PR-01-003`: PR draft 1 dòng vừa chọn `product` vừa điền `non_catalog_name` → `clean()`
  raise `ValidationError`.
- `TC-PUR-PR-01-004`: dòng catalog chọn `product` có `category='Nguyên liệu'` → form render với
  `budget_category` initial = `'Nguyên liệu'`; Requester sửa lại thành giá trị khác trước khi lưu →
  giá trị đã lưu là giá trị Requester sửa, không bị ghi đè lại bởi `product.category`.
- `TC-PUR-PR-01-005`: PR draft 1 dòng non-catalog điền `non_catalog_name`, để trống
  `non_catalog_uom` → `clean()` raise `ValidationError` (thiếu field bắt buộc, mới ở review lần 2).
- `TC-PUR-PR-01-006`: `PurchaseRequestItemForm.clean()` gọi trực tiếp (mô phỏng dòng thêm bằng JS
  phía client, không có initial từ server) với `product` đã chọn, `budget_category` để trống →
  giá trị sau `clean()` tự động bằng `product.category` (server-side fallback, mới ở review lần 2,
  AC #27).
- `TC-PUR-PR-02-001`: `submit_purchase_request()` set đúng `department_snapshot` = department hiện
  tại của `requested_by` tại thời điểm gọi.
- `TC-PUR-PR-02-002`: sau khi PR đã nộp, đổi `requested_by.department` sang phòng khác → PR đã nộp
  vẫn giữ `department_snapshot` cũ (bất biến).
- `TC-PUR-PR-03-001`: `decide_purchase_request()` approve không sửa `qty_approved` → mỗi
  `pr_item.qty_approved == qty_requested`.
- `TC-PUR-PR-03-002`: approve có sửa `qty_approved` 1 dòng thấp hơn `qty_requested` → giá trị lưu
  đúng, dòng khác không đổi.
- `TC-PUR-PR-03-003`: PR có `qty_approved=0` ở **mọi** dòng → approve bị chặn (`ValidationError`,
  yêu cầu dùng reject).
- `TC-PUR-PR-04-001`: `create_allocation(qty=qty_open+1)` → `ValidationError`, không tạo row.
- `TC-PUR-PR-04-002`: 2 thread gọi `create_allocation()` đồng thời trên cùng `pr_item` với tổng
  `qty` vượt `qty_open` (mỗi lần riêng lẻ hợp lệ) → đúng 1 thread thành công, thread còn lại nhận
  `ValidationError` — regression test khoá đúng dưới `select_for_update()` (mirror
  `MultiSkuLockOrderDeadlockTests` pattern đã có trong `stocktake.tests`).
- `TC-PUR-PR-04-003`: `TransactionTestCase` + threading — 1 thread gọi `po_update` xoá dòng
  `PurchaseOrderItem` (qua `delete_draft_po_item_with_allocations()`, mục 4 điểm 4), 1 thread khác
  gọi `create_allocation()` vào đúng dòng đó cùng lúc → không `OperationalError` (không deadlock),
  kết quả nhất quán ở cả 2 khả năng thắng cuộc (allocation tạo thành công trước khi dòng bị xoá,
  hoặc bị chặn vì dòng đã không còn tồn tại) — regression test cho lock order mới
  `PurchaseOrder → PurchaseOrderItem → PurchaseRequestItem → ProcurementAllocation` (mục 4 điểm 2,
  Nghiêm trọng #2 review lần 2).
- `TC-PUR-PR-04-004`: `po_item` đã tồn tại sẵn `qty_ordered=10` (từ 1 allocation trước đó) → gọi
  `create_allocation()` thêm 1 lần nữa với `qty=5` vào đúng `po_item` này → `qty_ordered` sau đó
  bằng 15, đúng 2 `ProcurementAllocation ACTIVE` cùng trỏ tới, tổng `qty_allocated` khớp `qty_ordered`
  (mới ở review lần 3, Nghiêm trọng #1, AC #6).
- `TC-PUR-PR-04-005`: dựng `po_item` với `purchase_order.status=APPROVED` → gọi `create_allocation()`
  → `ValidationError`; dựng 1 allocation `ACTIVE` có sẵn trên `po_item` đó rồi chuyển PO sang
  `APPROVED` → gọi `release_allocation()` → `ValidationError` (mới ở review lần 3, Nghiêm trọng #1,
  AC #7).
- `TC-PUR-PR-05-001`: build PO từ 2 dòng PR (2 PR khác nhau) cùng `product` → đúng 1
  `PurchaseOrderItem`, `qty_ordered` = tổng, đúng 2 `ProcurementAllocation`.
- `TC-PUR-PR-05-002`: build PO từ 2 dòng PR khác `product` → 2 `PurchaseOrderItem` riêng, mỗi cái 1
  allocation.
- `TC-PUR-PR-05-003`: PO `DRAFT` có 1 dòng `PurchaseOrderItem` (`qty_ordered=15`) với 2 allocation
  trỏ vào (10 + 5), xoá dòng `PurchaseOrderItem` đó qua `po_update` (`can_delete`, đi qua
  `delete_draft_po_item_with_allocations()`, actor là 1 user cụ thể) → cả 2 allocation `RELEASED`,
  giữ đúng `po_no_snapshot`/`product_code_snapshot`, `AuditLog` của cả 2 lần release **và** 1 dòng
  riêng cho thao tác xoá `po_item` đều ghi đúng user đó (không phải actor rỗng — sửa theo review lần
  2), cả 2 `pr_item.qty_open` tăng lại đúng phần của mình, dòng `PurchaseOrderItem` bị xoá hẳn khỏi
  DB đúng **1 lần** (`qty_ordered` về 0), **không** raise lỗi nào từ việc `formset.save()` cố xoá lại
  cùng row (regression cho Nghiêm trọng #4 review lần 3 — trước đây `release_allocation()` tự xoá
  `po_item` rồi `formset.save()` xoá thêm lần nữa).
- `TC-PUR-PR-05-004`: `qty_received` chia tỷ lệ đúng theo thuật toán mục 4 điểm 6 (case tổng chia
  không hết, kiểm tra phần dư cộng vào allocation cuối, tổng 2 `pr_item.qty_received` khớp đúng
  `total_received`).
- `TC-PUR-PR-05-005`: PO `DRAFT` có 1 `PurchaseOrderItem` với `qty_ordered=10`, allocation `ACTIVE`
  tổng `qty_allocated=8` → mở `po_update`, field `product`/`qty_ordered` của dòng này `disabled`;
  POST thẳng `qty_ordered=5` (bypass client, key lấy bằng `form.add_prefix('qty_ordered')`) →
  `formset.is_valid()` vẫn `True` (bước 3 mục 8), nhưng bước so sánh raw POST **sau** đó (bước 5 mục
  8) phát hiện khác DB → `ValidationError`, không lưu, `qty_ordered` và allocation giữ nguyên như
  trước request (viết lại theo review lần 4 — sửa lại thứ tự so sánh: v4 mô tả so sánh trước
  `is_valid()`, review lần 4 điểm 2 chỉ ra chạy sau `is_valid()` mới đúng và đơn giản hơn).
- `TC-PUR-PR-05-006`: case "1 PR → n PO" (AC #19) — split 1 `pr_item` (`qty_approved=100`) thành 2
  `ProcurementAllocation` ở 2 PO khác nhau (40 + 60) → cả 2 tạo thành công, `qty_allocated=100`,
  `qty_open=0`; gọi `create_allocation()` thêm 1 lần nữa với `qty=1` → `ValidationError`; release
  allocation ở PO đầu → `qty_open` quay lại 40 **và** `qty_ordered` của PO-item ở PO đầu giảm đúng
  từ 40 về 0 (dòng PO-item đó bị xoá hẳn vì về 0 — bổ sung theo review lần 2).
- `TC-PUR-PR-05-007`: PO `DRAFT` có 1 `PurchaseOrderItem` (`qty_ordered=15`) với 2 allocation `ACTIVE`
  (10 + 5) → `release_allocation()` allocation 5 (`released_reason` bắt buộc) → `qty_ordered` giảm
  còn 10, dòng `PurchaseOrderItem` **không** bị xoá (allocation 10 vẫn `ACTIVE`), bất biến mục 4
  điểm 4 đúng ngay sau thao tác (mới ở review lần 2, AC #13).
- `TC-PUR-PR-05-008`: dựng PO `source=FROM_PR` ở trạng thái **`APPROVED`** (sửa theo review lần 3,
  Nghiêm trọng #3 — dựng `DRAFT` là false positive: `send_po()` đã có điều kiện cũ "chỉ gửi PO
  `APPROVED`" chặn trước, không hề chạm tới guard mới) với 1 `PurchaseOrderItem.qty_ordered=10`
  nhưng **không** tạo allocation nào trỏ tới (mô phỏng dữ liệu cũ chưa reconcile) → gọi `send_po()`
  → `ValidationError` liệt kê đúng dòng vi phạm; sau đó khẳng định **cả 3**: PO vẫn `APPROVED`
  (không chuyển `SENT`), không có `AuditLog` nào ghi hành động gửi PO, `send_mail` không được gọi
  (mock/assert `not called`) — AC #14.
- `TC-PUR-PR-05-009`: cố tạo thẳng (bypass service, ví dụ `ProcurementAllocation.objects.create()`
  trực tiếp trong test) 1 row `status=ACTIVE, po_item=None` → `IntegrityError` do
  `active_allocation_requires_po_item` (mới ở review lần 2, AC #24, mục 2.4).
- `TC-PUR-PR-05-010`: PO `DRAFT`, nguồn `FROM_PR`, 1 dòng **legacy** (`qty_ordered=10`, 0 allocation)
  → gọi `delete_draft_po_item_with_allocations(po_item, actor)` trực tiếp → xoá thành công, không
  gọi `release_allocation()` nào (không có gì để release), đúng 1 dòng `AuditLog` cho thao tác xoá
  (mới ở review lần 3, Nghiêm trọng #4, AC #12).
- `TC-PUR-PR-05-011`: cố `INSERT` thẳng 1 `ProcurementAllocation` có `qty_allocated=0` → vi phạm
  `allocation_qty_positive`, `IntegrityError` (mới ở review lần 3, Quan trọng #4, AC #25).
- `TC-PUR-PR-05-012`: cố `INSERT` thẳng 1 `ProcurementAllocation` có `po_no_snapshot=''` (giữ
  `product_code_snapshot` hợp lệ) → vi phạm `allocation_snapshots_required`, `IntegrityError`; lặp
  lại với `product_code_snapshot=''` (giữ `po_no_snapshot` hợp lệ) → cùng kết quả (mới ở review lần
  3, Quan trọng #4, AC #26).
- `TC-PUR-PR-05-013`: 4 kịch bản trực tiếp cho `allocation_release_fields_match_status` (mới ở
  review lần 3, theo đề nghị review — kiểm chứng riêng constraint đã có từ v3, chưa từng có test
  tường minh): (a) `status=ACTIVE` nhưng `released_at` khác `None` → `IntegrityError`; (b)
  `status=RELEASED` nhưng `released_at=None` → `IntegrityError`; (c) `status=RELEASED` nhưng
  `released_reason=''` → `IntegrityError`; (d) `status=RELEASED`, `po_item=None`, `released_at`/
  `released_reason` đều hợp lệ, `released_by=None` → tạo thành công (`released_by` rỗng vẫn hợp lệ
  cho ca release tự động do hệ thống, mục 2.4).
- `TC-PUR-PR-05-014`: `po_item` legacy `qty_ordered=10`, 0 allocation → gọi
  `reconcile_legacy_po_item_allocations(po_item, allocations=[(pr_item, 10)], actor=admin)` → tạo
  đúng 1 `ProcurementAllocation ACTIVE` `qty_allocated=10`, **`po_item.qty_ordered` vẫn giữ nguyên
  10** (không cộng thêm — khác hẳn `create_allocation()`); gọi thêm 1 lần nữa với
  `allocations=[(pr_item_khác, 1)]` bất kỳ → `ValidationError` (tổng allocation đã bằng
  `qty_ordered`, không còn chỗ) (sửa theo review lần 4 — đổi tên hàm/tham số sang batch, AC #28).
- `TC-PUR-PR-05-015`: PO `DRAFT`, nguồn `FROM_PR`, có 1 dòng **legacy** (`qty_ordered=10`, **0**
  allocation) → mở `po_update`, field `product`/`qty_ordered` của dòng này **vẫn** `disabled` dù
  không có allocation nào; POST thẳng `qty_ordered=3` cho dòng này → `ValidationError` (mới ở review
  lần 3, Nghiêm trọng #2 — mở rộng phạm vi khoá ra khỏi "chỉ dòng có allocation", AC #8).
- `TC-PUR-PR-05-016`: PO `DRAFT`, nguồn `FROM_PR` → POST formset tự sửa `items-TOTAL_FORMS` (tăng
  thêm 1 so với số dòng thật) kèm 1 form mới (`product`/`qty_ordered` bất kỳ, không có `pk`) → sau
  `formset.is_valid()`, server guard phát hiện `form.instance.pk is None` và reject
  (`ValidationError`), không tạo `PurchaseOrderItem` nào (viết lại theo review lần 4 điểm 1 — sửa
  hiểu nhầm của v4 rằng `extra=0` tự nó đã chặn được POST giả; test này giờ mô phỏng đúng cách
  bypass thật — tự sửa management form — thay vì chỉ dựa vào formset không hiện `extra` lúc GET,
  AC #9). Test tương tự với `pk` thuộc `PurchaseOrderItem` của **PO khác** → cũng bị reject vì `pk`
  không thuộc tập đã khoá của PO đang sửa. Test tương tự với 2 form cùng gửi **1 `pk` hợp lệ giống
  hệt nhau** thuộc đúng PO đang sửa → cũng bị reject (mới ở review lần 5 điểm 1, AC #9/#34 — xem
  `TC-PUR-PR-05-023` cho fixture chi tiết hơn).
- `TC-PUR-PR-05-017`: PO `DRAFT`, nguồn `FROM_PR`, 1 dòng bất kỳ (có hoặc không allocation) → POST
  `unit_price` mới cho dòng đó → lưu thành công, giá trị mới đúng như đã gửi (field này không bị
  khoá, mới ở review lần 3, AC #10).
- `TC-PUR-PR-05-018`: `po_item` legacy `qty_ordered=10`, 0 allocation, cần 2 `pr_item` (4 + 6) → gọi
  1 lần `reconcile_legacy_po_item_allocations(po_item, allocations=[(pr_item_1, 4), (pr_item_2, 6)],
  actor=admin)` → tạo đúng 2 allocation `ACTIVE`, tổng khớp chính xác `qty_ordered=10`; gọi với tổng
  batch bằng 9 (thiếu) → `ValidationError`, không tạo allocation nào; gọi với tổng batch bằng 11
  (thừa) → `ValidationError`, không tạo allocation nào (mới ở review lần 4 điểm 3, AC #29).
- `TC-PUR-PR-05-019`: batch `allocations=[(pr_item_1, 4), (pr_item_2, 6)]` với `pr_item_2.qty_open`
  chỉ còn 3 (< 6, dòng thứ 2 không hợp lệ) → `ValidationError`, **0** allocation được tạo (kể cả
  `pr_item_1` dù hợp lệ riêng lẻ) — kiểm chứng batch rollback toàn bộ, không tạo dở dang (mới ở
  review lần 4 điểm 3, AC #30).
- `TC-PUR-PR-05-020`: gọi `reconcile_legacy_po_item_allocations()` lần lượt với 9 kịch bản vi phạm
  (mỗi kịch bản giữ các điều kiện khác hợp lệ): actor không phải Admin/superuser; **actor
  `is_active=False`** (mới, review lần 5); PO `source=MANUAL`; 1 `pr_item` thuộc PR chưa `APPROVED`;
  1 `pr_item.product_id != po_item.product_id`; 1 `qty < 1`; 1 `qty` vượt `pr_item.qty_open`;
  `pr_item.purchase_request.linked_po_id` khác `po_item.purchase_order_id`; đã tồn tại sẵn 1
  allocation `ACTIVE` cho đúng cặp (`pr_item`, `po_item`) → **cả 9** kịch bản đều `ValidationError`,
  không tạo allocation nào (mới ở review lần 4 điểm 4, mở rộng review lần 5 điểm 5, AC #31 — ca
  `linked_po_id` rỗng và ca trùng `pr_item` trong batch tách riêng ở `TC-PUR-PR-05-024`/`025` dưới).
- `TC-PUR-PR-05-021`: PO legacy `source=FROM_PR`, `status=APPROVED`, 1 `po_item` legacy
  `qty_ordered=10`, 0 allocation → gọi `reconcile_legacy_po_item_allocations()` batch khớp đúng 10 →
  tạo allocation thành công, `Product`/`qty_ordered`/`unit_price`/`PurchaseOrder.status` không đổi;
  gọi `send_po()` ngay sau đó → không còn bị guard mục 4 điểm 4 chặn (mới ở review lần 4 điểm 5,
  AC #32).
- `TC-PUR-PR-05-022`: `call_command('reconcile_legacy_po_item_allocations', po_item=..., allocation=
  [...], actor=..., dry_run=True)` → không tạo row nào trong DB, output chứa before/after; gọi lại
  không kèm `--dry-run` → tạo đúng allocation, commit thật; gọi với 1 `pr_item_id` không tồn tại →
  lệnh thoát lỗi, rollback toàn bộ, không tạo allocation nào (mới ở review lần 4 điểm 6, AC #33).
- `TC-PUR-PR-05-023`: PO `DRAFT`, nguồn `FROM_PR`, 1 `PurchaseOrderItem` có sẵn (`pk=X`) → POST
  formset với 2 form (tự tăng `items-TOTAL_FORMS`) cùng gán `instance.pk = X` (mô phỏng client lặp
  lại y hệt 1 form hợp lệ), mỗi form 1 `unit_price` khác nhau (ví dụ 100 và 200) → `ValidationError`
  ngay từ guard trùng `pk` (bước 5 mục 4 điểm 4), `unit_price` của dòng đó **giữ nguyên giá trị cũ**
  trước request, không phải 100 hay 200 (mới ở review lần 5 điểm 1, AC #9/#34).
- `TC-PUR-PR-05-024`: batch `allocations=[(pr_item_1, 4), (pr_item_1, 6)]` (cùng 1 `pr_item` lặp 2
  lần), `pr_item_1.qty_open=10` (đủ chỗ cho cả 2 dòng nếu tính riêng lẻ) → `ValidationError` ngay từ
  guard trùng `pr_item_id` trong input, **0** allocation được tạo — không được để lọt qua rồi mới
  chặn ở bước tổng cuối (mới ở review lần 5 điểm 4, AC #35).
- `TC-PUR-PR-05-025`: `po_item` legacy `qty_ordered=10`, batch `allocations=[(pr_item, 10)]` với
  `pr_item.purchase_request.linked_po_id is None` (PR này chưa từng gắn `linked_po`) →
  `ValidationError`, không tạo allocation nào, dù `pr_item.product_id == po_item.product_id` và
  `qty` khớp chính xác `qty_ordered` (mới ở review lần 5 điểm 3, AC #36).
- `TC-PUR-PR-05-026`: gọi `reconcile_legacy_po_item_allocations(po_item, allocations=[], actor=admin)`
  → `ValidationError`, không tạo allocation nào, **0** dòng `AuditLog` mới được ghi (đếm số `AuditLog`
  trước/sau bằng nhau — mới ở review lần 5 điểm 4, AC #37).
- `TC-PUR-PR-05-027`: `TransactionTestCase` + threading — PO legacy `source=FROM_PR`,
  `status=APPROVED`, 1 `po_item` chưa reconcile (`qty_ordered=10`, 0 allocation); dùng
  `threading.Barrier` đồng bộ 2 thread: thread A gọi `reconcile_legacy_po_item_allocations()` (batch
  khớp đúng 10), thread B gọi `send_po()` → không `OperationalError`/deadlock; đúng 1 trong 2 khả
  năng xảy ra tuỳ thread nào thắng lock trước (A trước ⇒ B thành công ngay sau; B trước ⇒ B bị chặn
  bởi guard mục 4 điểm 4, PO vẫn `APPROVED`, A vẫn reconcile thành công sau đó) — regression test cho
  lock order mới của hàm batch (mục 4 điểm 4, review lần 5 điểm 5, AC #38 — mirror
  `MultiSkuLockOrderDeadlockTests` pattern đã có trong `stocktake.tests`, theo CLAUDE.md).
- `TC-PUR-PR-06-001`: `create_allocation()` với `pr_item.product_id is None` → `ValidationError`.
- `TC-PUR-PR-06-002`: `map_non_catalog_item()` tạo `Product` mới + gán vào `pr_item` → `pr_item.
  product_id` đúng Product vừa tạo, `is_non_catalog == False` sau đó.
- `TC-PUR-PR-06-003`: `map_non_catalog_item()` gọi khi PR còn `DRAFT` → chặn (mục 4 điểm 10).
- `TC-PUR-PR-06-004`: gọi `str()` trên 1 `PurchaseRequestItem` non-catalog (`product=None`) →
  không raise `AttributeError`, trả về chuỗi có chứa `non_catalog_name` (regression cho lỗi
  `self.product.product_code` khi `product` là `None`, xem mục 13).
- `TC-PUR-PR-06-005`: `map_non_catalog_item()` gọi khi PR `REJECTED` → chặn (mục 4 điểm 10 — allow-list
  không coi `REJECTED` là trạng thái map được, dù khác `DRAFT`).
- `TC-PUR-PR-06-006`: `map_non_catalog_item()` gọi với Product `is_active=False` → chặn (mục 4 điểm
  10).
- `TC-PUR-PR-06-007`: Product bị deactivate (qua `QuerySet.update()`, không qua instance caller đang
  giữ) sau khi form đã validate nhưng trước khi service chạy → service tự đọc lại từ DB, vẫn chặn
  (mục 4 điểm 10, TOCTOU).
- `TC-PUR-PR-07-001`: `cancel_pr_item_open_qty(qty=qty_open+1)` → chặn.
- `TC-PUR-PR-07-002`: `cancel_pr_item_open_qty()` hợp lệ, gọi bởi PUR Staff đúng `assigned_to` →
  `qty_cancelled` tăng đúng, `AuditLog` chứa `reason`.
- `TC-PUR-PR-07-003`: PR `DRAFT` không có allocation → `delete_purchase_request()` vẫn xoá được
  (không đổi so với hiện tại — regression, không phải case mới).
- `TC-PUR-PR-07-004`: `cancel_pr_item_open_qty()` gọi bởi PUR Staff **không** phải `assigned_to`
  của PR → `PermissionDenied`; cùng PR, gọi bởi PUR Manager (`is_department_manager('PURCHASING')`)
  dù không phải `assigned_to` → thành công (mục 1, mục 4 điểm 9).
- `TC-PUR-MIG-001`: fixture PR/PO khớp rõ ràng 1-1 → chạy migration `0018` không crash (kiểm chứng
  `created_by=None` hợp lệ với field đã sửa `null=True`/`SET_NULL` — sửa lỗi Nghiêm trọng #3 review
  lần 2), backfill tạo đúng 1 allocation, `qty_allocated == min(qty_requested, qty_ordered)`,
  `created_by is None`.
- `TC-PUR-MIG-002`: fixture PR có 2 dòng cùng product trỏ 1 PO cùng product → backfill không tạo
  allocation cho cặp này, xuất hiện trong báo cáo ngoại lệ.
- `TC-PUR-MIG-003`: chạy lại backfill lần 2 trên DB đã chạy → không tạo thêm allocation nào (đếm số
  row trước/sau bằng nhau).
- `TC-PUR-XR-001`: user role `STAFF` gọi `exchange_rate_create` → `403`.
- `TC-PUR-XR-002`: Admin tạo `ExchangeRate(currency=USD, rate_date=hôm nay)` 2 lần → lần 2 vi phạm
  `unique_currency_rate_date`, `IntegrityError`/form error, không tạo row trùng.
- `TC-PUR-XR-003`: user role `STAFF` gọi `exchange_rate_update` hoặc `exchange_rate_delete` trên 1
  `ExchangeRate` có sẵn → `403` ở cả hai; Admin gọi cùng thao tác → thành công, có `AuditLog`.
- `TC-PUR-SLA-001`: `check_non_catalog_sla` — dòng non-catalog PUR tiếp nhận 4 ngày làm việc trước
  (bỏ qua T7/CN trong khoảng đó), chưa map → tạo đúng 1 `Notification`.
- `TC-PUR-SLA-002`: dòng tương tự nhưng mới tiếp nhận 1 ngày làm việc → không tạo `Notification`.
- `TC-PUR-SLA-003`: dòng đã được map (`product` không rỗng) dù quá 3 ngày → không tạo `Notification`
  (đã xử lý xong, không còn vi phạm SLA).

## 12. Backlog kỹ thuật (Stage 2)

| Ticket | Mã | Việc cần làm | File chính |
|---|---|---|---|
| T1 | `PUR-PR-01/02` | Migration `0017` thêm field mục 2.1/2.2; form/formset PR thêm field mới + toggle non-catalog | `purchasing/models.py`, `purchasing/forms.py`, `purchasing/migrations/0017_*.py` |
| T2 | quyết định #8 | Model `ExchangeRate` + `Currency` choices + migration + `exchange_rate_list`/`_create`/`_update`/`_delete` view (Admin-only, không dùng Django Admin) + menu item `exchange_rate` | `purchasing/models.py`, `purchasing/views.py`, `purchasing/urls.py`, `accounts/permissions.py` |
| T3 | `PUR-PR-03/04/05` | Model `ProcurementAllocation` (`po_item` `SET_NULL` + `po_no_snapshot`/`product_code_snapshot` + `created_by` `null=True`/`SET_NULL` + **4** `CheckConstraint`, mục 2.4) + migration + `create_allocation()` (chỉ nhận PO `DRAFT`, **tự tăng** `po_item.qty_ordered` bằng `F()`, lock order `PurchaseOrder → PurchaseOrderItem → PurchaseRequestItem → ProcurementAllocation`, mục 4 điểm 2/4) + `release_allocation(..., delete_empty_po_item=True)` (tự trừ `qty_ordered`, xoá `po_item` khi về 0 **chỉ nếu** `delete_empty_po_item=True`) + **`delete_draft_po_item_with_allocations()`** (điều phối release hàng loạt + xoá `po_item` đúng 1 lần, mục 4 điểm 4) + `send_po()` guard (chặn nếu `qty_ordered != tổng allocation ACTIVE`) + 5 property qty trên `PurchaseRequestItem` (mục 2.2, mục 4 điểm 6) + `po_update` (`extra=0` cho PO `FROM_PR` + server guard reject form mới/`pk` lạ/**`pk` trùng trong cùng formset** (mới review lần 5) **sau** `is_valid()` (dừng ngay nếu `is_valid()==False`, không chạy guard nào), `product`/`qty_ordered` `disabled=True` cho **mọi** dòng, so sánh raw POST bằng `form.add_prefix()` **sau** `is_valid()` — đổi thứ tự ở review lần 4, xoá dòng qua `delete_draft_po_item_with_allocations()` thay vì để `formset.save()` tự xoá, mục 4 điểm 4/mục 5/mục 7/mục 8) | `purchasing/models.py`, `purchasing/services.py`, `purchasing/views.py` |
| T4 | `PUR-PR-06`, quyết định #9 | `product` nullable trên `PurchaseRequestItem` + `budget_category` (mục 2.2, `clean()` fallback = `product.category` cho dòng catalog + JS prefill trên `pr_create`/`pr_update`) + `clean()` XOR đúng 2 field bắt buộc non-catalog (`non_catalog_name`/`non_catalog_uom`, `non_catalog_note` tuỳ chọn); `map_non_catalog_item()` + view `pr_item_map_product`; sửa `PurchaseRequestItem.__str__()` để không truy cập `self.product.product_code` khi `product is None` (fallback `non_catalog_name`) | `purchasing/models.py`, `purchasing/views.py`, `purchasing/forms.py` |
| T5 | `PUR-PR-07` | `cancel_pr_item_open_qty()` + nút trên `pr_detail` | `purchasing/services.py`, `purchasing/views.py`, `templates/purchasing/pr_detail.html` |
| T6 | `PUR-PR-05` | View `po_build_from_pr_lines` (2 bước) + `build_po_from_allocations()`, thay link `po_create?from_pr=` ở `pr_detail` | `purchasing/views.py`, `purchasing/services.py`, `purchasing/forms.py` |
| T7 | `PUR-PR-07` (migration) | Data migration `0018` backfill `ProcurementAllocation` từ `linked_po` + management command `report_allocation_migration_exceptions` | `purchasing/migrations/0018_*.py`, `purchasing/management/commands/report_allocation_migration_exceptions.py` |
| T8 | quyết định #9 | Management command `check_non_catalog_sla` (business-day, bỏ T7/CN) + notify | `purchasing/management/commands/check_non_catalog_sla.py` |
| T9 | (mới, review lần 4, mở rộng review lần 5, mục 4 điểm 4/mục 8/mục 9) | Hàm **batch** `reconcile_legacy_po_item_allocations(po_item, allocations, actor)` (validate đủ **14** điều kiện mục 4 điểm 4 — gồm `actor.is_active`, batch không rỗng, không trùng `pr_item_id` trong input, `linked_po_id` bắt buộc khớp — tổng phải khớp **chính xác** `qty_ordered`, atomic — 1 dòng sai rollback cả batch, chạy được cả PO `DRAFT`/`APPROVED`, khoá đúng lock order `PurchaseOrder → PurchaseOrderItem → PurchaseRequestItem (pk) → ProcurementAllocation (pk)`) + management command `reconcile_legacy_po_item_allocations` (`--po-item`, `--allocation pr_item_id:qty` lặp lại, `--actor` username duy nhất chỉ dùng để audit, `--dry-run`, in before/after, rollback toàn bộ khi lỗi) | `purchasing/services.py`, `purchasing/management/commands/reconcile_legacy_po_item_allocations.py` |

Phụ thuộc thứ tự: T1 trước tất cả (field nền tảng). T2 độc lập, làm song song được. T3 phụ thuộc T1.
T4 phụ thuộc T1 (field non-catalog). T5 phụ thuộc T3 (cần `qty_open`). T6 phụ thuộc T3+T4 (cần
allocation + gate non-catalog). T7 phụ thuộc T3 (cần model `ProcurementAllocation` tồn tại), làm
sau T3 nhưng độc lập với T4/T5/T6. T8 phụ thuộc T4 (cần field non-catalog + mốc `Approval`). T9 phụ
thuộc T3 (cần model `ProcurementAllocation` + `create_allocation()` tồn tại) và T7 (backfill mục 9
là nguồn dữ liệu legacy cần reconcile), độc lập với T4/T5/T6/T8.

## 13. Lịch sử review

| Version | Ngày | Nội dung |
|---|---|---|
| v1 | 03/08/2026 | Bản nháp đầu tiên, đối chiếu Epic B (`PUR-PR-01..07`) + quyết định #2/#8/#9 với code thật (`purchasing/models.py`, `catalog/models.py`, `accounts/models.py`) ngày 03/08/2026. Chưa qua review. |
| v2 | 03/08/2026 | Xử lý review lần 1 (2 Nghiêm trọng, 3 Quan trọng, 3 Nhỏ): (1) bổ sung field `budget_category` (mục 2.2) — field "category" đã nhắc tên ở mục 0 nhưng v1 quên triển khai; (2) viết lại mục 4 điểm 4 — bỏ khái niệm "xoá PO" không tồn tại trong code, thay bằng quy tắc rõ ràng cho xoá/giảm `qty_ordered` của **dòng PO-item** khi PO còn `DRAFT`, đổi `ProcurementAllocation.po_item` từ `PROTECT` sang `SET_NULL` kèm `po_no_snapshot`/`product_code_snapshot` (mục 2.4) để tránh `ProtectedError` khi xoá dòng đã release hết allocation; (3) sửa câu chữ quyết định #18 trong `00_business_decisions.md` cho rõ đây là điều kiện hoàn tất Discovery, không phải yêu cầu code ở Stage 2; (4) thêm AC #12/TC-PUR-PR-05-006 cho case "1 PR → n PO" theo đúng Exit Criteria Stage 2 của master plan; (5) đổi `ExchangeRate` sửa/xoá từ Django Admin sang view nghiệp vụ riêng (`exchange_rate_update`/`_delete`, Admin-only, có audit log) vì `is_staff` không đồng nhất với `role=ADMIN`; (6) thêm kế hoạch sửa `PurchaseRequestItem.__str__()` (T4) + `TC-PUR-PR-06-004`; (7) làm rõ câu "Không có field `currency=VND`" thành "không lưu bản ghi nào có `currency=VND`"; (8) thu hẹp mô tả quyền PUR Staff ở mục 1 khớp đúng điều kiện `assigned_to` đã có ở mục 4 điểm 9, thêm `TC-PUR-PR-07-004`. Chưa qua review lần 2. |
| v3 | 03/08/2026 | Xử lý review lần 2 (3 Nghiêm trọng, 7 Quan trọng): (1) **[Nghiêm trọng]** thêm bất biến bắt buộc "`qty_ordered` luôn khớp đúng tổng allocation `ACTIVE`" cho PO `source=FROM_PR` (mục 4 điểm 4, viết lại toàn bộ) — v2 cho phép release allocation mà không đụng `qty_ordered`, tạo lỗ hổng over-order (đặt mua gấp đôi nhu cầu thật); `qty_ordered` giờ readonly ở `po_update` cho dòng có allocation, chỉ đổi được gián tiếp qua `release_allocation()` (tự trừ `qty_ordered`, xoá dòng khi về 0), thêm guard `send_po()` chặn PO vi phạm bất biến (bảo vệ dữ liệu legacy backfill mơ hồ); (2) **[Nghiêm trọng]** chốt lock order chung cho nhóm Allocation — `PurchaseOrder → PurchaseOrderItem → PurchaseRequestItem → ProcurementAllocation` (mục 4 điểm 2) — v2 chỉ khoá `pr_item`, không đủ vì `po_update`/`release_allocation` cũng động vào `PurchaseOrder`/`PurchaseOrderItem`; (3) **[Nghiêm trọng]** sửa `ProcurementAllocation.created_by` từ `PROTECT` sang `null=True`/`SET_NULL` (mục 2.4) — v2 khai báo `PROTECT` nhưng migration backfill tạo `created_by=None`, sẽ crash khi chạy thật; (4) **[Quan trọng]** thêm 2 `CheckConstraint` (`active_allocation_requires_po_item`, `allocation_release_fields_match_status`, mục 2.4) làm defense-in-depth cho trạng thái `ACTIVE`/`po_item`/field release; (5) **[Quan trọng]** sửa actor của cascade `release_allocation()` khi xoá dòng PO-item từ `None` sang `request.user` thật (mục 4 điểm 4, mục 7) — đây là thao tác trực tiếp của Buyer, không phải system job; `actor=None` từ nay chỉ dành cho migration; (6) **[Quan trọng]** bổ sung JS prefill `budget_category` cho dòng PR thêm động phía client + giữ server-side fallback trong `clean()` làm lớp bắt buộc (mục 2.2/mục 5) — v2 chỉ có server-side prefill nên không phủ được dòng mới thêm trước khi submit; (7) **[Quan trọng]** sửa câu "snapshot sau khi lưu" thành "snapshot tại thời điểm `submit_purchase_request()`" (mục 2.2) — dòng PR vẫn sửa tự do được nhiều lần khi còn `DRAFT`; (8) **[Quan trọng]** ghi rõ giới hạn casing của `budget_category` (không chuẩn hoá hoa/thường ở Stage 2) và yêu cầu Stage 3 phải dùng 1 hàm canonical dùng chung khi so khớp (mục 2.2); (9) **[Quan trọng]** sửa mâu thuẫn "3 field non-catalog" — chỉ `non_catalog_name`/`non_catalog_uom` bắt buộc, `non_catalog_note` tuỳ chọn (mục 2.2, mục 4 điểm 1, mục 8) — `clean()` ở v2 vốn đã chỉ XOR đúng 2 field, câu chữ mô tả sai; (10) **[Quan trọng]** sửa câu Notification "PO bị xoá/huỷ trước khi gửi" thành đúng khái niệm "release allocation/xoá dòng PO-item" (mục 6) — Stage 2 không có chức năng xoá cả PO. Renumber AC (giờ 20 mục) và thêm/sửa test case tương ứng (`TC-PUR-PR-04-003`, `TC-PUR-PR-05-005..009`, `TC-PUR-PR-01-005/006`, `TC-PUR-MIG-001` cập nhật). Chưa qua review lần 3. |
| v4 | 03/08/2026 | Xử lý review lần 3 (4 Nghiêm trọng, kèm 2 `CheckConstraint` bổ sung theo đề nghị Quan trọng): (1) **[Nghiêm trọng #1]** `create_allocation()` giờ luôn **tăng** `po_item.qty_ordered` đúng bằng `qty` ngay khi tạo allocation (`F('qty_ordered') + qty`, mục 3/mục 4 điểm 4/mục 8) — v3 chỉ nói rõ chiều giảm qua `release_allocation()`, chưa nói chiều tăng, nên thêm 1 allocation vào 1 `po_item` đã tồn tại (không chỉ lúc build PO mới) làm tổng allocation tăng nhưng `qty_ordered` đứng yên, phá bất biến ngay lập tức; đồng thời thu hẹp điều kiện trạng thái PO cho `create_allocation()`/`release_allocation()` từ `(DRAFT, APPROVED)` xuống đúng `DRAFT`, khớp đúng rule "PO `APPROVED` trở lên không sửa PO-item"; `build_po_from_allocations()` viết lại để gọi `create_allocation()` cho từng cặp thay vì tự tính tổng `qty_ordered` riêng; (2) **[Nghiêm trọng #2]** mở rộng phạm vi khoá ở `po_update` ra **mọi** dòng PO nguồn `FROM_PR` (không chỉ dòng có allocation, mục 4 điểm 4/mục 5/mục 8) — `product`/`qty_ordered` khai báo `disabled=True` ở tầng `Form` (không chỉ HTML `readonly`), formset `extra=0` (không cho thêm dòng thủ công), phát hiện tampering qua so sánh raw `request.POST` (vì `disabled=True` khiến `cleaned_data` không phản ánh được giá trị giả mạo để so sánh — v3 mô tả sai cơ chế này), `unit_price` vẫn sửa tự do, dòng legacy không allocation chỉ còn 2 lối ra (xoá thẳng, hoặc `reconcile_legacy_po_item_allocation()` mới); (3) **[Nghiêm trọng #3]** sửa fixture `TC-PUR-PR-05-008` từ PO `DRAFT` sang `APPROVED` — dựng `DRAFT` là false positive vì `send_po()` đã có điều kiện cũ "chỉ gửi PO `APPROVED`" chặn trước, chưa từng chạm guard mới; bổ sung 3 assertion (PO vẫn `APPROVED`, không có `AuditLog` gửi PO, `send_mail` không được gọi); (4) **[Nghiêm trọng #4]** thêm service điều phối `delete_draft_po_item_with_allocations()` (mục 4 điểm 4/mục 8) và tham số `delete_empty_po_item` trên `release_allocation()` — sửa lỗi 2 tầng cùng xoá 1 `po_item` (`release_allocation()` tự xoá khi về 0, rồi `formset.save()` xoá lại lần nữa); `po_update` giờ gọi hàm điều phối rồi loại các dòng đã xử lý khỏi `formset.save()`. Thêm 2 `CheckConstraint` mới `allocation_qty_positive`/`allocation_snapshots_required` (mục 2.4, theo đề nghị Quan trọng của review lần 3) + ghi rõ giới hạn "constraint không kiểm được aggregate xuyên bảng". Thêm hàm one-off `reconcile_legacy_po_item_allocation()` cho ca dữ liệu cũ (mục 4 điểm 4/mục 8/mục 9). Renumber AC (giờ 28 mục) và thêm/sửa test case tương ứng (`TC-PUR-PR-04-004/005`, `TC-PUR-PR-05-003` cập nhật, `TC-PUR-PR-05-008` cập nhật, `TC-PUR-PR-05-010..017`). Chưa qua review lần 4. |
| v5 | 03/08/2026 | Xử lý review lần 4 (7 điểm, không phân hạng Nghiêm trọng/Quan trọng — review dạng kỹ thuật chi tiết): (1) sửa hiểu nhầm "`extra=0` chặn được POST giả" của v4 — `extra=0` chỉ giới hạn hiển thị lúc GET, client vẫn có thể tự sửa `items-TOTAL_FORMS`/gửi `pk` lạ; thêm server guard tường minh sau `formset.is_valid()`: reject mọi form `instance.pk is None` hoặc `pk` không thuộc đúng tập `PurchaseOrderItem` của PO đang khoá (mục 4 điểm 4/mục 5/mục 8, AC #9 viết lại); (2) đảo thứ tự so sánh raw POST từ **trước** sang **sau** `formset.is_valid()` (`is_valid()` không ghi DB nên gọi trước an toàn hơn, không cần tự parse management form/prefix khi chưa có gì đáng tin) — dùng `form.add_prefix('product')`/`form.add_prefix('qty_ordered')` để lấy key thay vì tự ghép chuỗi thủ công (mục 4 điểm 4/mục 5/mục 8, AC #8 viết lại); (3) đổi `reconcile_legacy_po_item_allocation()` đơn lẻ (v4) thành hàm **batch** `reconcile_legacy_po_item_allocations(po_item, allocations, actor)` — hàm đơn lẻ chỉ chặn khi tổng *vượt quá* `qty_ordered` (`<=`), không chặn khi tổng *thiếu*, và không giữ được bất biến khi 1 `po_item` cần 2+ `pr_item` khớp vào qua nhiều lần gọi riêng lẻ; batch tạo toàn bộ allocation trong 1 transaction, rule dùng đúng dấu `==` (tổng hiện có + tổng batch phải khớp **chính xác** `qty_ordered`), 1 dòng sai rollback cả batch (mục 4 điểm 4/mục 8, AC #29/#30 mới); (4) mở rộng validation của hàm batch từ chỉ kiểm tổng thành 12 điều kiện đầy đủ — actor Admin/superuser, PO nguồn `FROM_PR`, PR item thuộc PR `APPROVED`, product khớp, `qty>=1`, `qty<=pr_item.qty_open`, đối chiếu `linked_po_id`, không trùng allocation `ACTIVE`, set snapshot/`created_by`, ghi `AuditLog`, re-assert tổng sau khi tạo (mục 4 điểm 4/mục 8, AC #31 mới); (5) cho phép hàm batch chạy trên PO legacy `APPROVED`, không chỉ `DRAFT` (v4 coi `APPROVED` là "bế tắc hoàn toàn") — reconciliation chỉ bổ sung truy vết, không đổi `Product`/`qty_ordered`/`unit_price`/trạng thái PO nên không vi phạm rule khoá PO-item đã `APPROVED` (mục 4 điểm 4/mục 8/mục 9, AC #32 mới); (6) thay "management command/Django shell" ngang hàng nhau (v4) bằng 1 management command chính thức `reconcile_legacy_po_item_allocations` (`--po-item`, `--allocation pr_item_id:qty` lặp lại, `--actor` bắt buộc, `--dry-run` in before/after không commit, rollback toàn bộ khi lỗi) — Django shell chỉ còn là debug, không phải recovery procedure được duyệt (mục 4 điểm 4/mục 12 T9 mới, AC #33 mới); (7) 2 chỉnh câu chữ nhỏ: bỏ câu sai "`create_allocation()` không sửa `PurchaseOrder`/`PurchaseOrderItem`" ở mục 4 điểm 2 (không còn đúng từ khi hàm này tự tăng `qty_ordered`), và sửa "bất biến đúng tại **mọi thời điểm**" thành "tại **mọi transaction boundary/trạng thái đã commit**" ở mục 4 điểm 4 (khớp với việc `build_po_from_allocations()` tạm tạo `qty_ordered=0` trong 1 transaction chưa commit). Renumber AC (giờ 33 mục) và thêm/sửa test case tương ứng (`TC-PUR-PR-05-005/014/015/016` cập nhật, `TC-PUR-PR-05-018..022` mới). Chưa qua review lần 5. |
| v6 | 03/08/2026 | Xử lý review lần 5 (6 điểm, không phân hạng): (1) formset `po_update` chỉ kiểm "`pk` tồn tại/thuộc đúng PO" theo từng form riêng lẻ, không phát hiện 2 form khác nhau gửi **cùng 1 `pk`** hợp lệ — client tăng `items-TOTAL_FORMS` rồi lặp lại y hệt 1 `pk` vẫn qua lọt cả 2 guard cũ, ghi `unit_price` 2 lần theo thứ tự form ("last write wins" không tường minh); thêm guard tích luỹ `submitted_pks`, reject nếu `pk` xuất hiện 2+ lần (mục 4 điểm 4/mục 5/mục 8, AC #9/#34 mới, `TC-PUR-PR-05-023`); (2) làm rõ nhánh `formset.is_valid() == False` phải dừng ngay, không chạy bất kỳ guard nào ở bước sau (kiểm `pk`, so sánh raw POST) — review lần 4 chỉ mô tả nhánh hợp lệ, để hở câu hỏi guard có chạy khi `is_valid()` fail không (mục 4 điểm 4/mục 5/mục 8); (3) rule `linked_po_id` của hàm batch reconcile đổi từ "ưu tiên kiểm khi có giá trị" (bỏ qua khi rỗng) sang **bắt buộc khớp cho mọi cặp**, rỗng/`None` ⇒ reject — rule cũ để hở đường Admin gắn 1 `pr_item` bất kỳ cùng `product` vào PO legacy dù 2 bên chưa từng có quan hệ lịch sử `linked_po` nào (mục 4 điểm 4/mục 8, AC #31/#36 mới, `TC-PUR-PR-05-025`); (4) hàm batch reject khi `allocations` rỗng (không ghi `AuditLog`) và khi cùng 1 `pr_item_id` xuất hiện 2+ lần trong input — trước đây 1 `pr_item` lặp lại (ví dụ `[(pr_item_1,4),(pr_item_1,6)]`) có thể tạo 2 allocation `ACTIVE` cho cùng 1 cặp vì lúc kiểm chưa tồn tại allocation nào sẵn, đồng thời validate `qty<=qty_open` riêng lẻ từng dòng không phát hiện được tổng vượt ngưỡng; sửa bằng cách khoá toàn bộ `pr_item` trong batch theo `pk` tăng dần rồi mới tính tổng theo từng `pr_item` (mục 4 điểm 4/mục 8, AC #35/#37 mới, `TC-PUR-PR-05-024/026`); (5) thêm 2 validation vận hành cho hàm batch — `actor.is_active == True` và chưa soft-delete (không chỉ role Admin), và làm tường minh lại lock order `PurchaseOrder → PurchaseOrderItem → PurchaseRequestItem (pk) → ProcurementAllocation (pk)` cho riêng hàm này, vì nó là đường duy nhất tạo allocation được cho PO legacy đã `APPROVED` nên có thể chạy đồng thời với `send_po()` — lock order sai sẽ deadlock giữa 2 request; management command resolve `--actor` theo username duy nhất, actor chỉ dùng để audit chứ không tự kiểm tra quyền Django, vì quyền chạy được `manage.py` trên server đã là 1 lớp kiểm soát truy cập riêng (mục 4 điểm 4/mục 8, AC #31/#38 mới, `TC-PUR-PR-05-020/027`); (6) thêm 4 test case mới theo yêu cầu review (`TC-PUR-PR-05-023..026`) + 1 concurrency test (`TC-PUR-PR-05-027`, mirror `MultiSkuLockOrderDeadlockTests`) cho khả năng reconcile chạy đồng thời với `send_po()` trên PO `APPROVED`. Renumber AC (giờ 38 mục), cập nhật T3/T9 ở mục 12. **Approved v6** bởi luckyhoang1988 (Trường Hoàng) ngày 03/08/2026 — không yêu cầu thêm review lần 6, chuyển sang viết implementation plan chi tiết (migration → service → form/view → management command → tests); 3 lưu ý kỹ thuật nhỏ (chuẩn hoá `F()`/`refresh_from_db()` sau update, chuẩn hoá cách đọc raw POST theo prefix, `--dry-run` phải rollback thật sự bằng savepoint) đưa vào implementation plan, không phải điều kiện chặn FSD. |
| v7 | 03/08/2026 | Review code sau khi triển khai Task 2.7 (`map_non_catalog_item()` đã commit) + rà lại thiết kế Task 3.5 (2 **Quan trọng**): (1) guard chặn "PR còn `DRAFT`" (mục 4 điểm 10) chỉ chặn đúng `DRAFT`, nên `REJECTED` vẫn map thành công — trong khi hệ thống cho phép `REJECTED → DRAFT` qua `reopen_purchase_request()`, sau đó Requester lại sửa/xoá dòng tự do, tái tạo đúng nguy cơ "Product rác" mà rule này muốn tránh; đổi sang **allow-list** `{PENDING_DEPT, PENDING_PUR, APPROVED}` thay vì deny-list `{DRAFT}` (mục 4 điểm 10, `TC-PUR-PR-06-005` mới); (2) service không tái kiểm `product.is_active` — form dự kiến ở Task 3.5 chỉ hiển thị Product đang hoạt động nhưng đó là lớp UX, không chặn được caller khác truyền Product inactive, hoặc Product bị deactivate sau khi form validate nhưng trước khi service chạy (TOCTOU); service giờ tự `select_for_update()` + đọc lại `product` từ DB rồi kiểm `is_active` (mục 4 điểm 10, `TC-PUR-PR-06-006`/`TC-PUR-PR-06-007` mới, quy tắc chung "Form querysets filter, services must re-validate independently" theo CLAUDE.md). Đã sửa trong `purchasing/services.py` (`map_non_catalog_item()`, hằng số `MAPPABLE_PR_STATUSES` mới), TDD đầy đủ (RED→GREEN), không phải điều kiện chặn FSD thêm review lần nữa vì phạm vi chỉ sửa 1 hàm đã có sẵn, không đổi model/screen/AC nào khác. |
