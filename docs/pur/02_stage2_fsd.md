# PUR Expansion — 02. FSD Stage 2: PR 2.0 và Allocation (Epic B, PUR-PR-01..07)

> Trạng thái: **Draft — v1, chưa review**
> Trạng thái triển khai: **Chưa viết code** — đây vẫn là mô tả backlog, chờ review + approve trước
> khi vào TDD (giống quy trình đã áp dụng cho `01_foundation_fsd.md`).
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
   department/cost center bất biến tại thời điểm nộp.
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
| PUR Staff (`role` có `update` trên `pr`/`po`, thuộc `department=PURCHASING`) | **Mới**: map non-catalog line sang Product (quyết định #9), tạo Allocation/PO từ PR line đã duyệt, huỷ phần open của 1 dòng PR (`PUR-PR-07`). |
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
  3 field non-catalog dưới đây để trống) hoặc **non-catalog** (`product=None`, 3 field dưới đây bắt
  buộc) — validate ở `PurchaseRequestItem.clean()` (mục 8).
- `non_catalog_name` — `CharField(max_length=200, blank=True)`, verbose_name `'Tên hàng (chưa có
  trong danh mục)'`.
- `non_catalog_uom` — `CharField(max_length=20, blank=True)`, verbose_name `'Đơn vị tính (đề
  xuất)'`.
- `non_catalog_note` — `TextField(blank=True)`, verbose_name `'Mô tả/quy cách (non-catalog)'`.
- `required_date` — `DateField(null=True, blank=True)`, verbose_name `'Ngày cần hàng'`. Bắt buộc ở
  **tầng form** cho mọi dòng tạo/sửa từ Stage 2 trở đi (DRAFT); để `null` ở tầng DB vì dòng PR đã
  tồn tại trước migration không có dữ liệu thật — không suy đoán bằng `created_at` (theo nguyên tắc
  backfill đã thống nhất trong CLAUDE.md — "không suy đoán thành sự kiện thật").
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

Không có field `currency=VND` (VND là đơn vị gốc, không cần tỷ giá quy đổi chính nó — service nào
quy đổi tiền tệ phải short-circuit `currency == VND ⇒ rate = 1`, không query bảng này). Không cho
sửa (`edit`)/xoá qua UI sau khi tạo trong Stage 2 — nếu nhập sai, tạo dòng mới đè `rate_date` khác
hoặc xoá thẳng qua Django Admin (chỉ Admin có quyền); vì **chưa có consumer nào** đọc bảng này
trong Stage 2 (mục 0), rủi ro sửa/xoá nhầm ảnh hưởng dữ liệu đã snapshot chỉ phát sinh từ Stage 3
trở đi — quy tắc "không sửa được sau khi đã bị snapshot dùng" để lại cho FSD Stage 3 thiết kế cụ
thể (cần biết chính xác Stage 3 snapshot ra sao trước khi khoá).

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
        'PurchaseOrderItem', on_delete=models.PROTECT, related_name='allocations',
        verbose_name='Dòng đơn mua hàng')
    qty_allocated = models.PositiveIntegerField(validators=[MinValueValidator(1)], verbose_name='Số lượng phân bổ')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, verbose_name='Trạng thái')
    released_reason = models.TextField(blank=True, verbose_name='Lý do giải phóng')
    released_by = models.ForeignKey(
        'accounts.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        verbose_name='Người giải phóng')
    released_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời điểm giải phóng')
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.PROTECT, related_name='+', verbose_name='Người tạo phân bổ')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Ngày tạo')

    class Meta:
        verbose_name = 'Phân bổ PR-PO'
        verbose_name_plural = 'Phân bổ PR-PO'
```

`pr_item.product_id == po_item.product_id` là bất biến bắt buộc nhưng **không** khả thi thành DB
constraint 2 FK khác bảng — validate ở tầng service (`create_allocation()`, mục 4). Không
`UniqueConstraint(pr_item, po_item)` — 1 cặp có thể lý thuyết lặp lại sau khi 1 allocation cũ bị
`RELEASED` và tạo allocation mới thay thế; ràng buộc thật sự cần là tổng `qty_allocated` (mục 4
điểm 2), không phải số dòng.

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
lại `ACTIVE` — muốn phân bổ lại thì tạo allocation mới). Chuyển `RELEASED` khi: PO/PO-item chứa nó
bị xoá/huỷ trước khi `SENT` (mục 4 điểm 3), hoặc PUR Staff chủ động release thủ công (đổi ý trước
khi gửi NCC, cần `released_reason`).

## 4. Business rules

1. **`PUR-PR-01`** — không đổi so với hiện tại: PR draft cần ≥1 dòng hợp lệ mới nộp được (dòng hợp
   lệ = có `product` **hoặc** đủ 3 field non-catalog, có `qty_requested`, `required_date`,
   `currency`, `estimated_unit_price`).
2. **`PUR-PR-04`** — `create_allocation(pr_item, po_item, qty, actor)` chặn nếu
   `qty > pr_item.qty_open` **tính ngay trước khi tạo, dưới khoá** (`select_for_update()` trên
   `pr_item`, cùng nhóm lock order `Grn → QcInspection → Inventory → Batch → WarehouseHandoff` đã
   chốt cho các module khác — `PurchaseRequestItem`/`ProcurementAllocation` là nhóm lock **riêng**,
   không giao với nhóm đó, nhưng áp dụng cùng nguyên tắc: khoá `pr_item` trước khi đọc
   `qty_allocated`/`qty_cancelled` để tính `qty_open`, tránh 2 request tạo allocation song song
   cùng vượt `qty_approved` — đúng lớp lỗi "per-target quantity check must sum every existing claim"
   đã ghi trong CLAUDE.md).
3. **`PUR-PR-05`** — 1 `PurchaseOrderItem` có thể nhận nhiều `ProcurementAllocation` từ nhiều
   `PurchaseRequestItem` khác nhau (kể cả khác PR), miễn cùng `product`; khi build PO
   (`build_po_from_allocations()`, mục 5), nếu 2+ dòng PR được chọn cùng trỏ 1 product cho cùng 1
   PO đang tạo, hệ thống tự **gộp** vào đúng 1 `PurchaseOrderItem.qty_ordered` (tổng qty các dòng đã
   chọn) — không tạo 2 dòng PO trùng product (giữ `PUR-FND-06`).
4. Khi PO/PO-item còn `DRAFT`/`APPROVED` (chưa `SENT`) bị xoá hoặc bị bớt `qty_ordered` xuống dưới
   tổng `qty_allocated` hiện có, mọi `ProcurementAllocation` `ACTIVE` trỏ tới nó phải `RELEASED`
   trong cùng transaction (`released_reason` tự sinh, vd `"PO {po_no} bị xoá trước khi gửi NCC"`) —
   trả `qty_open` về cho các `pr_item` liên quan, không để phân bổ mồ côi.
5. PO đã `SENT` trở lên: allocation trỏ vào nó **không** tự động `RELEASED` nữa (đã là cam kết thật
   với NCC) — muốn huỷ phần đó phải qua quy trình đóng sớm PO (`close_po()` hiện có) hoặc GIN/GRN
   thực tế, ngoài phạm vi Allocation.
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
10. Map non-catalog (`map_non_catalog_item()`) chỉ gọi được khi PR đã rời `DRAFT` (tức đã tới ít
    nhất `PENDING_DEPT`) — không cho map trước khi nộp, vì lúc `DRAFT` Requester có thể còn đang sửa
    tự do (kể cả xoá hẳn dòng đó), map sớm chỉ tạo `Product` rác nếu Requester đổi ý.

## 5. Màn hình

- **`pr_create`/`pr_update` (sửa màn hình có sẵn)**: formset dòng PR thêm `required_date`,
  `currency`, `estimated_unit_price`, và 1 toggle "Hàng chưa có trong danh mục" — bật thì ẩn ô chọn
  `product`, hiện 3 ô `non_catalog_name/uom/note`; tắt thì ngược lại. Header thêm `cost_center`
  (bắt buộc), `project` (tuỳ chọn).
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
- **`exchange_rate_list`/`exchange_rate_create` (mới, Admin only)**: danh sách + form tạo 1 dòng
  `ExchangeRate` (`currency`, `rate_date`, `rate_to_vnd`); không có sửa/xoá qua UI (mục 2.3).

## 6. Notification

- Map non-catalog quá hạn: management command mới (`manage.py check_non_catalog_sla`, chạy qua cron
  — đúng pattern `⏸️`) — tìm `PurchaseRequestItem` có `product__isnull=True`, thuộc PR đã có
  `Approval(department=PURCHASING)` (mốc "PUR tiếp nhận", dùng
  `accounts.approvals.latest_approvals_for`/truy `Approval.created_at`) quá **3 ngày làm việc**
  (tính lùi bỏ Thứ 7/CN, chưa tính lịch nghỉ lễ — đủ cho MVP, đúng cách đơn giản hoá `⏸️`), gửi
  `Notification` cho PUR department manager + `pr.assigned_to` (nếu có).
- Allocation vừa `RELEASED` do PO bị xoá/huỷ trước khi gửi: notify `pr_item.purchase_request.
  requested_by` — dòng của họ vừa mất phân bổ, `qty_open` tăng trở lại.

## 7. Audit log

Log qua `accounts.AuditLog`/`log_action()` cho mọi transition mới: `create_allocation`,
`release_allocation` (kể cả tự động do xoá PO — actor `None`, cùng pattern "system-triggered" đã
dùng cho `sync_expired_batches`), `cancel_pr_item_open_qty`, `map_non_catalog_item`,
`ExchangeRate` tạo mới, `decide_purchase_request` nhánh approve ghi thêm `qty_approved` từng dòng
vào `description` nếu khác `qty_requested` (để audit trail thấy rõ dòng nào bị duyệt giảm).

## 8. Validation

- `PurchaseRequestItem.clean()`: đúng 1 trong 2 — `product` có giá trị **XOR** cả 3
  (`non_catalog_name`, `non_catalog_uom`) khác rỗng; không cho vừa có `product` vừa có
  `non_catalog_*`, không cho cả hai đều rỗng.
- `create_allocation()`: `pr_item.product_id == po_item.product_id` (mục 2.4), `qty <=
  pr_item.qty_open` dưới khoá (mục 4 điểm 2), `pr_item.purchase_request.status == APPROVED`,
  `po_item.purchase_order.status in (DRAFT, APPROVED)` (không tạo allocation mới thẳng vào PO đã
  `SENT` — muốn thêm hàng vào PO đã gửi phải qua revision, ngoài phạm vi Stage 2).
- `ExchangeRateForm`: `rate_date` không được là ngày tương lai (nhập tỷ giá cho ngày chưa tới là vô
  nghĩa — tỷ giá thực tế chưa biết).
- Form PR: `required_date` không được là ngày trong quá khứ so với ngày nộp.

## 9. Migration dữ liệu cũ

**Schema** (migration `purchasing/0017_...`): thêm field mục 2.1/2.2 (toàn bộ nullable/có default —
không cần `RunPython` guard kiểu `PUR-FND-06`, vì không có `UniqueConstraint`/`NOT NULL` mới nào có
thể bị dữ liệu cũ vi phạm), tạo bảng `ExchangeRate`, tạo bảng `ProcurementAllocation`.

**Backfill `ProcurementAllocation` từ `linked_po`** (`purchasing/0018_...`, `RunPython`, dùng
`apps.get_model()` — không import `purchasing.models`/`purchasing.services`, đúng nguyên tắc đã
chốt ở `PUR-FND-06`):

1. Với mỗi `PurchaseRequest` có `linked_po` khác `None`: lấy toàn bộ `items` của PR và toàn bộ
   `items` của `linked_po`, map theo `product_id`.
2. **Khớp rõ ràng** (đúng 1 `PurchaseRequestItem` và đúng 1 `PurchaseOrderItem` cùng `product` cho
   cặp PR/PO đang xét): tạo 1 `ProcurementAllocation(status=ACTIVE, qty_allocated=min(pr_item.
   qty_requested, po_item.qty_ordered), created_by=None)`; nếu `pr_item.qty_requested !=
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
   buộc xử lý báo cáo ngoại lệ, khác SLA non-catalog).

**`linked_po` sau migration**: giữ nguyên cột, **không xoá, không còn được ghi mới** — `build_po_
from_allocations()` không set `linked_po` (PO mới từ Stage 2 trở đi trace qua
`ProcurementAllocation` duy nhất). Đánh dấu deprecated trong docstring model, việc gỡ cột hẳn để
lại cho 1 Stage sau khi đã xác nhận không còn UI nào đọc nó nữa.

Guard bắt buộc theo đúng pattern đã chốt (migration thêm `UniqueConstraint`/backfill dữ liệu quan
trọng cần idempotent-check): chạy lại migration lần 2 trên DB đã backfill rồi phải là no-op (kiểm
tra `ProcurementAllocation` đã tồn tại cho cặp `pr_item`/`po_item` trước khi tạo lại).

## 10. Acceptance criteria

1. Tạo PR mới thiếu `cost_center`/`required_date`/`currency`/`estimated_unit_price` ở 1 dòng ⇒
   form/formset báo lỗi đúng field, không lưu được.
2. Bật toggle non-catalog trên 1 dòng, để trống `product` ⇒ lưu được (không đòi `product`); tắt
   toggle mà không chọn `product` ⇒ báo lỗi.
3. PUR Manager duyệt PR ở `PENDING_PUR`, sửa `qty_approved` 1 dòng xuống thấp hơn `qty_requested`
   ⇒ sau approve, `pr_item.qty_approved` đúng giá trị đã sửa, `qty_open` tính đúng theo giá trị đó
   (không phải `qty_requested`).
4. `create_allocation()` gọi với `qty > qty_open` ⇒ `ValidationError`, không tạo row.
5. 2 dòng PR (từ 2 PR khác nhau) cùng `product`, cùng chọn vào 1 lần build PO ⇒ kết quả có đúng
   **1** `PurchaseOrderItem` (không phải 2), `qty_ordered` = tổng 2 dòng, và đúng **2**
   `ProcurementAllocation` trỏ vào PO item đó.
6. Xoá 1 PO còn `DRAFT` có allocation ⇒ mọi allocation liên quan chuyển `RELEASED` trong cùng
   transaction, `pr_item.qty_open` tăng lại đúng bằng phần vừa release.
7. `create_allocation()` gọi với `pr_item.product_id is None` (chưa map) ⇒ `ValidationError`.
8. `map_non_catalog_item()` gán `product` cho `pr_item` ⇒ dòng đó xuất hiện trong danh sách chọn ở
   `po_build_from_pr_lines` ngay sau đó (không cần refresh gì đặc biệt).
9. `cancel_pr_item_open_qty()` gọi với `qty > qty_open` ⇒ chặn; gọi hợp lệ ⇒ `qty_cancelled` tăng
   đúng, `qty_open` giảm đúng, có `AuditLog` chứa `reason`.
10. 1 `po_item` nhận allocation từ 2 `pr_item` (10 và 5, tổng `qty_ordered=15`), GRN ghi nhận
    `qty_received=9` cho product đó trên PO ⇒ `qty_received` của 2 `pr_item` cộng lại đúng bằng 9,
    không thừa/thiếu do làm tròn (mục 4 điểm 6).
11. Migration backfill trên fixture có 1 PR/PO khớp rõ ràng 1-1 và 1 PR/PO có 2 dòng cùng product
    (khớp mơ hồ) ⇒ trường hợp đầu tạo đúng 1 allocation; trường hợp sau **không** tạo allocation,
    xuất hiện trong báo cáo ngoại lệ.
12. Chạy lại migration backfill (hoặc gọi lại `report_allocation_migration_exceptions`) lần 2 trên
    dữ liệu đã backfill ⇒ không tạo thêm allocation trùng.
13. User không phải Admin/superuser gọi `exchange_rate_create` ⇒ `403`.
14. `check_non_catalog_sla` chạy trên fixture có 1 dòng non-catalog PUR tiếp nhận cách đây 4 ngày
    làm việc (chưa map) ⇒ tạo `Notification` cho PUR manager; dòng mới tiếp nhận 1 ngày ⇒ không tạo.

## 11. Test case

Convention `TC-PUR-PR-0X-00Y` (theo `FR-XX-##`, cùng tiền tố `PUR` như `TC-PUR-FND-*` để phân biệt
sáng kiến ngoài 60-FR — xem `01_foundation_fsd.md` mục 11):

- `TC-PUR-PR-01-001`: PR draft 1 dòng thiếu `required_date` → `is_valid() == False`.
- `TC-PUR-PR-01-002`: PR draft 1 dòng non-catalog đủ 3 field, không chọn `product` → lưu thành công.
- `TC-PUR-PR-01-003`: PR draft 1 dòng vừa chọn `product` vừa điền `non_catalog_name` → `clean()`
  raise `ValidationError`.
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
- `TC-PUR-PR-05-001`: build PO từ 2 dòng PR (2 PR khác nhau) cùng `product` → đúng 1
  `PurchaseOrderItem`, `qty_ordered` = tổng, đúng 2 `ProcurementAllocation`.
- `TC-PUR-PR-05-002`: build PO từ 2 dòng PR khác `product` → 2 `PurchaseOrderItem` riêng, mỗi cái 1
  allocation.
- `TC-PUR-PR-05-003`: PO `DRAFT` có 2 allocation trỏ 1 `po_item`, xoá PO → cả 2 allocation
  `RELEASED`, cả 2 `pr_item.qty_open` tăng lại đúng phần của mình.
- `TC-PUR-PR-05-004`: `qty_received` chia tỷ lệ đúng theo thuật toán mục 4 điểm 6 (case tổng chia
  không hết, kiểm tra phần dư cộng vào allocation cuối, tổng 2 `pr_item.qty_received` khớp đúng
  `total_received`).
- `TC-PUR-PR-06-001`: `create_allocation()` với `pr_item.product_id is None` → `ValidationError`.
- `TC-PUR-PR-06-002`: `map_non_catalog_item()` tạo `Product` mới + gán vào `pr_item` → `pr_item.
  product_id` đúng Product vừa tạo, `is_non_catalog == False` sau đó.
- `TC-PUR-PR-06-003`: `map_non_catalog_item()` gọi khi PR còn `DRAFT` → chặn (mục 4 điểm 10).
- `TC-PUR-PR-07-001`: `cancel_pr_item_open_qty(qty=qty_open+1)` → chặn.
- `TC-PUR-PR-07-002`: `cancel_pr_item_open_qty()` hợp lệ → `qty_cancelled` tăng đúng, `AuditLog`
  chứa `reason`.
- `TC-PUR-PR-07-003`: PR `DRAFT` không có allocation → `delete_purchase_request()` vẫn xoá được
  (không đổi so với hiện tại — regression, không phải case mới).
- `TC-PUR-MIG-001`: fixture PR/PO khớp rõ ràng 1-1 → backfill tạo đúng 1 allocation,
  `qty_allocated == min(qty_requested, qty_ordered)`.
- `TC-PUR-MIG-002`: fixture PR có 2 dòng cùng product trỏ 1 PO cùng product → backfill không tạo
  allocation cho cặp này, xuất hiện trong báo cáo ngoại lệ.
- `TC-PUR-MIG-003`: chạy lại backfill lần 2 trên DB đã chạy → không tạo thêm allocation nào (đếm số
  row trước/sau bằng nhau).
- `TC-PUR-XR-001`: user role `STAFF` gọi `exchange_rate_create` → `403`.
- `TC-PUR-XR-002`: Admin tạo `ExchangeRate(currency=USD, rate_date=hôm nay)` 2 lần → lần 2 vi phạm
  `unique_currency_rate_date`, `IntegrityError`/form error, không tạo row trùng.
- `TC-PUR-SLA-001`: `check_non_catalog_sla` — dòng non-catalog PUR tiếp nhận 4 ngày làm việc trước
  (bỏ qua T7/CN trong khoảng đó), chưa map → tạo đúng 1 `Notification`.
- `TC-PUR-SLA-002`: dòng tương tự nhưng mới tiếp nhận 1 ngày làm việc → không tạo `Notification`.
- `TC-PUR-SLA-003`: dòng đã được map (`product` không rỗng) dù quá 3 ngày → không tạo `Notification`
  (đã xử lý xong, không còn vi phạm SLA).

## 12. Backlog kỹ thuật (Stage 2)

| Ticket | Mã | Việc cần làm | File chính |
|---|---|---|---|
| T1 | `PUR-PR-01/02` | Migration `0017` thêm field mục 2.1/2.2; form/formset PR thêm field mới + toggle non-catalog | `purchasing/models.py`, `purchasing/forms.py`, `purchasing/migrations/0017_*.py` |
| T2 | quyết định #8 | Model `ExchangeRate` + `Currency` choices + migration + `exchange_rate_list`/`_create` view (Admin-only) + menu item `exchange_rate` | `purchasing/models.py`, `purchasing/views.py`, `purchasing/urls.py`, `accounts/permissions.py` |
| T3 | `PUR-PR-03/04/05` | Model `ProcurementAllocation` + migration + `create_allocation()`/`release_allocation()` + 5 property qty trên `PurchaseRequestItem` (mục 2.2, mục 4 điểm 6) | `purchasing/models.py`, `purchasing/services.py` |
| T4 | `PUR-PR-06`, quyết định #9 | `product` nullable trên `PurchaseRequestItem` + `clean()` XOR non-catalog; `map_non_catalog_item()` + view `pr_item_map_product` | `purchasing/models.py`, `purchasing/views.py`, `purchasing/forms.py` |
| T5 | `PUR-PR-07` | `cancel_pr_item_open_qty()` + nút trên `pr_detail` | `purchasing/services.py`, `purchasing/views.py`, `templates/purchasing/pr_detail.html` |
| T6 | `PUR-PR-05` | View `po_build_from_pr_lines` (2 bước) + `build_po_from_allocations()`, thay link `po_create?from_pr=` ở `pr_detail` | `purchasing/views.py`, `purchasing/services.py`, `purchasing/forms.py` |
| T7 | `PUR-PR-07` (migration) | Data migration `0018` backfill `ProcurementAllocation` từ `linked_po` + management command `report_allocation_migration_exceptions` | `purchasing/migrations/0018_*.py`, `purchasing/management/commands/report_allocation_migration_exceptions.py` |
| T8 | quyết định #9 | Management command `check_non_catalog_sla` (business-day, bỏ T7/CN) + notify | `purchasing/management/commands/check_non_catalog_sla.py` |

Phụ thuộc thứ tự: T1 trước tất cả (field nền tảng). T2 độc lập, làm song song được. T3 phụ thuộc T1.
T4 phụ thuộc T1 (field non-catalog). T5 phụ thuộc T3 (cần `qty_open`). T6 phụ thuộc T3+T4 (cần
allocation + gate non-catalog). T7 phụ thuộc T3 (cần model `ProcurementAllocation` tồn tại), làm
sau T3 nhưng độc lập với T4/T5/T6. T8 phụ thuộc T4 (cần field non-catalog + mốc `Approval`).

## 13. Lịch sử review

| Version | Ngày | Nội dung |
|---|---|---|
| v1 | 03/08/2026 | Bản nháp đầu tiên, đối chiếu Epic B (`PUR-PR-01..07`) + quyết định #2/#8/#9 với code thật (`purchasing/models.py`, `catalog/models.py`, `accounts/models.py`) ngày 03/08/2026. Chưa qua review. |
