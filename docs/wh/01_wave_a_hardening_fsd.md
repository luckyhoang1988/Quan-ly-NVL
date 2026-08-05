# Warehouse Expansion — 01. FSD Wave A: Hardening

> Trạng thái: **Đang thiết kế — chờ duyệt**
> Nguồn: [`wh_plan.md`](../../wh_plan.md) §"Wave A — Hardening (chi tiết)" + §"Quyết định đã chốt (pre-FSD)"
> Phạm vi: A1 Inventory theo vị trí trên `warehouse_detail`, A2 Capacity soft-warn (không chặn),
> A3 Ops snapshot theo loại kho. A4 (test gap có sẵn + doc sync) không phải tính năng mới — liệt kê
> ở mục 8 để implementation plan Phase 4 bám vào, không có AC/TC riêng.
> **Ngoài phạm vi Wave A**: zone/aisle, capacity hard-block, putaway suggestion, nhãn vị trí (dời
> Wave B — chỉ viết `docs/wh/03_*`/`04_*` sau khi xác nhận vận hành thật nghẽn), Auto-PO/Celery,
> REWORK QC, scanner hardware, đổi grain model của `Inventory` sang theo vị trí.

## 0. Tóm tắt vấn đề

App `warehouse/` hiện chỉ có CRUD kho/vị trí (FR-WM-01/02) + singleton STAGING/SCRAP. 4 gap vận
hành đã xác định (BA review, xem `wh_plan.md`):

1. Không nhìn được tồn theo vị trí ngay trên trang kho — phải mở từng `batch_detail` hoặc tra DB.
2. `Warehouse.capacity`/`Location.capacity` chỉ là field metadata, chưa từng được đọc lại ở đâu —
   không cảnh báo khi kho/vị trí gần đầy hoặc đã vượt.
3. Không có snapshot vận hành: không biết STAGING đang tồn đọng bao lâu (hàng chờ QC), MAIN có bao
   nhiêu phiếu bàn giao đang chờ nhận, SCRAP đang giữ bao nhiêu hàng quarantine.
4. (A4, không phải tính năng) Một số luồng ghi đã có (staff M2M, `location_update`,
   `get_scrap_warehouse` khi thiếu kho) chưa có test — liệt kê ở mục 8, đóng trong implementation
   plan Phase 4 cùng lúc với A1-A3, không cần AC riêng vì không đổi hành vi.

## 1. Actor & quyền

Không thêm quyền mới. A1/A3 là mở rộng READ trên `warehouse_detail` — vẫn gate
`can_view_menu('warehouse')` như hiện tại (`warehouse/views.py:83-84`), không có actor mới nào
được xem thứ trước đây không xem được (mọi query A1/A3 chỉ tổng hợp lại dữ liệu Batch/Inventory/
WarehouseHandoff mà các app khác đã hiển thị rải rác). A2 là cảnh báo chèn vào 3 luồng ghi đã có
sẵn permission gate riêng (`transfer_create`, `qc_result` action=pass/partial, `grn_receive_qty`) —
không đổi ai được thực hiện hành động đó, chỉ thêm `messages.warning` sau khi hành động thành công.

## 2. A1 — Inventory theo vị trí trên `warehouse_detail`

### 2.1 Refactor `PHYSICAL_BATCH_STATUSES` — đích cuối là `inventory.models`, không phải `inventory.services`

Quyết định #4 (pre-FSD) nói chuyển hằng số này từ `stocktake.services` sang `inventory.services` để
`warehouse` không phải import ngược từ `stocktake` (app Phase 4). Xác minh lại lúc viết FSD phát
hiện việc đó tạo **circular import thật**, không chỉ là layering chưa đẹp: `inventory/services.py:22`
đã import module-level `from warehouse.services import get_default_location, get_scrap_warehouse`
— nếu `warehouse.services` cũng import module-level ngược lại `inventory.services`, kết quả phụ
thuộc module nào được import trước (`ImportError`/`AttributeError` tuỳ thứ tự, không phải lỗi ổn
định để test bắt được).

**Chốt lại**: đặt `PHYSICAL_BATCH_STATUSES` tại **`inventory/models.py`**, ngay dưới định nghĩa
`Batch.Status` (nơi hợp lý hơn cho một hằng số chỉ là "tập con của 1 enum đã có sẵn ở đó") —
`warehouse.services` đã import an toàn từ `inventory.models` từ trước (`Inventory`, docstring dòng
1-6 giải thích rõ chiều một-hướng này), không có rủi ro vòng lặp. Chỉ `stocktake.services` cần sửa
import (đổi nguồn, xem 2.1→7); `inventory.services` **không** đổi gì — module này hiện không
tham chiếu `PHYSICAL_BATCH_STATUSES` ở đâu cả (đã grep xác nhận), nên không có dòng import nào để
sửa ở đó. Vẫn giữ đúng tinh thần quyết định #4 (hằng số rời khỏi `stocktake`, `warehouse` không
phải đụng tới app Phase 4) — chỉ đổi module đích vì lý do circular-import cụ thể, không đổi ý định
nghiệp vụ.

### 2.2 `warehouse.services.location_occupancy(warehouse)`

```python
def location_occupancy(warehouse):
    return (
        Batch.objects
        .filter(location__warehouse=warehouse, status__in=PHYSICAL_BATCH_STATUSES)
        .select_related('product', 'location')
        .order_by('location__code', 'batch_code')
    )
```

Trả về `QuerySet[Batch]` (không phải list) để view tự `paginate_queryset()` — mỗi dòng UI đọc
`batch.location.code`, `batch.product`, `batch.batch_code`, `batch.get_status_display`,
`batch.qty_available` (property có sẵn), link `batch_detail` qua
`{% url 'inventory:batch_detail' batch.pk %}` (`Batch` không có `get_absolute_url()` — mọi template
khác trong dự án, vd `batch_list.html`/`transfer_list.html`/`handoff_list.html`, đều link trực tiếp
kiểu này, không qua `get_absolute_url`). Áp dụng cho cả 3 loại kho
(MAIN/STAGING/SCRAP) — không lọc theo `warehouse_type`, vì cả 3 loại đều có thể có batch đang giữ
tồn vật lý (STAGING: `ACTIVE` chờ QC; SCRAP: `QUARANTINE`; MAIN: mọi status vật lý trừ `CLOSED`).

**Có chủ đích không thêm predicate `qty_received > qty_used`** dù `wh_plan.md` A1 liệt kê điều kiện
này riêng với điều kiện status: `CLOSED` đã bao phủ đúng trường hợp qty cạn — mọi điểm trừ
`qty_used` trong dự án (`inventory.services.move_batch_qty`/`transfer_stock`,
`shipping.services.issue_gin`, `stocktake.services._consume_shortage_batches`,
`quality.services.cancel_qc_inspection`) đều đóng batch về `CLOSED` ngay khi `qty_available <= 0`,
bất kể status trước đó (đã grep xác nhận cả 4 điểm). Vì vậy một batch không `CLOSED` luôn có
`qty_available > 0` — thêm predicate qty là kiểm tra một điều kiện `PHYSICAL_BATCH_STATUSES` đã
đảm bảo sẵn, thuộc loại validation cho tình huống không thể xảy ra mà `CLAUDE.md` khuyến cáo không
thêm. Áp dụng thống nhất cho cả `location_occupancy` (2.2) lẫn `location_occupied_qty` (3.1) —
không lọc thêm ở hàm nào.

### 2.3 UI — card mới "Tồn kho theo vị trí" trên `warehouse_detail`

Card riêng, đặt dưới card "Vị trí lưu trữ" hiện có (không gộp — bảng vị trí hiện tại là *metadata*
1 dòng/vị trí, bảng mới là *tồn kho* nhiều dòng/vị trí). Cột: Vị trí, SKU, Mã lô (link
`batch_detail`), Trạng thái, Số lượng khả dụng. **Giới hạn hiển thị**: dùng
`accounts.pagination.paginate_queryset()` (cùng tiện ích mọi list view khác trong dự án đã dùng,
mặc định 30 dòng/trang) trên `QuerySet[Batch]` trả về từ 2.2 — chọn phân trang phẳng thay vì gom
nhóm theo vị trí rồi mới xổ chi tiết, vì đây là cách đơn giản nhất tái dùng đúng hạ tầng sẵn có,
không cần component UI mới (accordion/collapse) chỉ cho 1 trang.

## 3. A2 — Capacity soft-warn (không chặn)

### 3.1 Capacity basis (đã chốt, quyết định #1)

Occupied qty tại 1 `Location` = tổng `qty_available` (= `qty_received - qty_used`) của mọi `Batch`
đang ở `PHYSICAL_BATCH_STATUSES` tại vị trí đó:

```python
def location_occupied_qty(location):
    return Batch.objects.filter(
        location=location, status__in=PHYSICAL_BATCH_STATUSES,
    ).aggregate(total=Sum(F('qty_received') - F('qty_used')))['total'] or 0
```

Không thêm predicate qty riêng ở đây — lý do giống hệt `location_occupancy` (2.2): `CLOSED` đã loại
mọi batch qty=0 ra khỏi `PHYSICAL_BATCH_STATUSES` rồi, `Sum(...)` trên tập còn lại không cần lọc
thêm.

Occupied qty cấp kho = tổng occupied qty của mọi `Location` thuộc kho đó. So với `capacity` tương
ứng (`Location.capacity`/`Warehouse.capacity`) **chỉ khi field đó khác `None`** — kho/vị trí chưa
khai capacity thì không có gì để so, không hiện badge/cảnh báo. **Xấp xỉ có chủ đích**: nếu nhiều
SKU khác `Product.uom` cùng chia sẻ 1 vị trí, tổng "qty" cộng dồn thô không phải cùng đơn vị đo —
chấp nhận sai số này ở Wave A (chuẩn hoá đơn vị capacity là việc của Wave B nếu vận hành thật cần).

### 3.2 Ngưỡng cảnh báo — hằng số mới `CAPACITY_WARN_RATIO`

```python
CAPACITY_WARN_RATIO = 0.9  # occupied / capacity >= 0.9 -> "gần đầy"
```

đặt trong `warehouse/models.py` cạnh `MIN_LOCATIONS_PER_WAREHOUSE` (cùng chỗ các hằng số nghiệp vụ
khác của app này). 3 mức, dùng cả cho badge (3.3.a) lẫn message cảnh báo (3.3.b/c):

| Ratio | Mức | Badge |
|---|---|---|
| `capacity is None` | Chưa khai dung tích | không hiện badge |
| `< 0.9` | Bình thường | `bg-success` |
| `0.9 ≤ ratio < 1.0` | Gần đầy | `bg-warning text-dark` |
| `≥ 1.0` | Đã vượt dung tích | `bg-danger` |

### 3.3 3 điểm cảnh báo (quyết định #2) — cơ chế khác nhau theo loại trang

**(a) `warehouse_detail` — badge server-render, không cần JS.** Row "Dung tích sử dụng" mới trong
panel key/value đầu trang (badge cấp kho, ẩn nếu `obj.capacity` là `None`); cột "Dung tích" trong
bảng "Vị trí lưu trữ" hiện có (`warehouse_detail.html:94`) nối thêm badge cấp vị trí (ẩn nếu
`loc.capacity` là `None`). Đây là "badge" duy nhất trong phạm vi Wave A — 2 điểm còn lại (b), (c)
**không** có badge trước khi submit (tránh việc phải làm JS live-preview theo lựa chọn dropdown,
ngoài phạm vi cần thiết của Wave A theo YAGNI); chỉ cảnh báo **sau khi hành động đã thành công**,
cùng cơ chế đã có sẵn.

**(b) `transfer_create` / `qc_result` (action=pass|partial) — đích MAIN.** Mirror chính xác pattern
`receiving.services.tolerance_alerts()` (trả `list[str]`, view lặp `messages.warning`) đã dùng ở
`grn_receive_qty` (`receiving/views.py:338-339`):

```python
def location_capacity_alerts(location):
    """list[str] — 0, 1 (chỉ location) hoặc 2 (location + warehouse) message,
    gọi SAU khi transfer_stock()/qc_pass()/qc_partial_pass() đã commit."""
```

- `transfer_create` (`inventory/views.py:350-358`): sau `transfer_stock(...)` thành công, trước
  `redirect`, thêm `for alert in location_capacity_alerts(form.cleaned_data['to_location']):
  messages.warning(request, alert)`.
- `qc_result` action `pass`/`partial` (`quality/views.py:106-122`): cùng vị trí, dùng
  `location_capacity_alerts(location)` với `location` đã lấy từ `result_form.cleaned_data['location']`.
  Action `fail` không cần — đích là Kho phế, không đi qua điểm cảnh báo này (Kho phế không nằm
  trong phạm vi cảnh báo Wave A, xem 3.3.c).

**(c) `grn_receive_qty` (`start_qc` → STAGING) — điểm nghẽn vận hành, không được bỏ sót.** Cùng
cơ chế (b), nhưng đích luôn là `get_staging_warehouse()` + `get_default_location(...)` (không phải
lựa chọn của người dùng — GRN luôn nhận vào Kho chờ). Thêm ngay sau khối `for alert in
tolerance_alerts(obj): messages.warning(...)` đã có ở `receiving/views.py:338-339` — cùng 1 vòng
lặp `messages.warning`, chỉ khác nguồn `alerts`, giữ nguyên thứ tự hiển thị (tolerance trước,
capacity sau) vì cả hai đều là cảnh báo phụ, không có thứ tự ưu tiên nghiệp vụ giữa chúng.

## 4. A3 — Ops snapshot theo loại kho

Card mới "Snapshot vận hành" trên `warehouse_detail`, đặt **dưới** card "Tồn kho theo vị trí" (2.3)
— thứ tự trên trang từ trên xuống: panel key/value (badge dung tích, 3.3.a) → "Vị trí lưu trữ" →
"Tồn kho theo vị trí" (A1) → "Snapshot vận hành" (A3). Nội dung theo `obj.warehouse_type` — chỉ 1
trong 3 khối sau hiện ra tuỳ loại kho đang xem (không phải cả 3 luôn hiện):

| Loại kho | KPI | Nguồn dữ liệu |
|---|---|---|
| STAGING | Số batch `ACTIVE` + tổng qty; số batch "tồn quá `STAGING_AGING_DAYS` ngày" (subset của cùng tập) | `Batch.objects.filter(location__warehouse=obj, status=ACTIVE)` |
| MAIN | Số `WarehouseHandoff` đang `PENDING` hướng tới kho này, kèm link `inventory:handoff_list` | `WarehouseHandoff.objects.filter(destination_warehouse=obj, status=PENDING).count()` |
| SCRAP | Tổng qty khả dụng của mọi batch `QUARANTINE` tại kho này | `Batch.objects.filter(location__warehouse=obj, status=QUARANTINE)` aggregate |

Hằng số `STAGING_AGING_DAYS = 3` đặt cạnh `CAPACITY_WARN_RATIO` (`warehouse/models.py`). Tính
on-the-fly tại thời điểm render, không Celery/cache, theo đúng pattern `⏸️` sẵn có. **Cutoff aging
— công thức chính xác** (tránh lệch UTC như invariant đã chốt toàn dự án về
`timezone.now().date()`):

```python
cutoff_date = timezone.localdate() - timedelta(days=STAGING_AGING_DAYS)
cutoff_dt = timezone.make_aware(datetime.combine(cutoff_date, time.min))
aged_count = staging_active_batches.filter(created_at__lt=cutoff_dt).count()
```

So sánh bằng datetime mốc 00:00 giờ VN của ngày cutoff (`created_at__lt=cutoff_dt`), không dùng
`created_at__date__lt=cutoff_date` — tránh phụ thuộc vào việc Django/Postgres có tự quy đổi
timezone đúng cho lookup `__date` hay không, nhất quán với cách dự án đã xử lý các so sánh
business-date khác (luôn tính tường minh bằng `timezone.localdate()`/`timezone.localtime()`, không
tin tưởng ngầm định của ORM). Link `handoff_list` ở MAIN **không** truyền filter `?warehouse=` —
`handoff_list` hiện chưa hỗ trợ lọc theo kho đích, thêm filter đó là mở rộng ngoài phạm vi con số
KPI (YAGNI); số đếm hiển thị tại đây vẫn đúng dù link đích chưa lọc sẵn.

## 5. Acceptance Criteria

| ID | AC |
|---|---|
| AC-WH-LOC-01 | `location_occupancy(warehouse)` chỉ trả batch có `status in PHYSICAL_BATCH_STATUSES` tại đúng `warehouse` — không có `CLOSED`, không lẫn batch kho khác |
| AC-WH-LOC-02 | `warehouse_detail` hiện card "Tồn kho theo vị trí" phân trang (`page_size` mặc định 30), không crash N+1 (đã `select_related`) |
| AC-WH-CAP-01 | `location_capacity_alerts(location)` trả rỗng khi `location.capacity is None` và `location.warehouse.capacity is None` |
| AC-WH-CAP-02 | Chỉ **1 cấp** (location hoặc warehouse) đạt ngưỡng gần đầy (0.9 ≤ ratio < 1.0), cấp còn lại `capacity is None` hoặc ratio < 0.9 → trả đúng 1 message "gần đầy" cho đúng cấp đó (không chặn hành động) |
| AC-WH-CAP-03 | Chỉ **1 cấp** đạt ngưỡng vượt dung tích (ratio ≥ 1.0), cấp còn lại `capacity is None` hoặc ratio < 0.9 → trả đúng 1 message "vượt dung tích" cho đúng cấp đó (không chặn hành động) |
| AC-WH-CAP-04 | `transfer_create` thành công + đích gần/vượt dung tích → response chứa `messages.warning` tương ứng, transfer vẫn được tạo |
| AC-WH-CAP-05 | `qc_result` action=pass/partial thành công + đích gần/vượt dung tích → `messages.warning`, batch/inventory vẫn được tạo bình thường |
| AC-WH-CAP-06 | `grn_receive_qty` (start_qc vào STAGING) khi Kho chờ gần/vượt dung tích → `messages.warning` cùng lượt redirect với `tolerance_alerts` hiện có, không chặn submit |
| AC-WH-CAP-07 | `warehouse_detail`: badge cấp kho/vị trí đúng 1 trong 3 mức (OK/Gần đầy/Vượt) theo bảng ngưỡng, ẩn hoàn toàn khi `capacity is None` |
| AC-WH-CAP-08 | **Cả 2 cấp** (location và warehouse) cùng đạt/vượt ngưỡng (mọi tổ hợp gần-đầy/vượt) → `location_capacity_alerts` trả đúng **2** message, 1 cho mỗi cấp, không chặn hành động |
| AC-WH-OPS-01 | Kho STAGING: đúng số batch `ACTIVE` + tổng qty + số batch quá `STAGING_AGING_DAYS` ngày |
| AC-WH-OPS-02 | Kho MAIN: đúng số `WarehouseHandoff` `PENDING` hướng tới kho, có link `handoff_list` |
| AC-WH-OPS-03 | Kho SCRAP: đúng tổng qty khả dụng batch `QUARANTINE` tại kho |
| AC-WH-OPS-04 | Card snapshot chỉ hiện đúng 1 khối tương ứng `warehouse_type` đang xem, không hiện cả 3 |

## 6. Test Cases

| TC | Map AC | Mô tả ngắn |
|---|---|---|
| TC-WH-LOC-001 | 01 | `location_occupancy` loại `CLOSED`, loại batch kho khác |
| TC-WH-LOC-002 | 01 | `location_occupancy` gồm đủ `PENDING_RECEIPT`/`EXPIRED`/`QUARANTINE` (không chỉ `ACTIVE`/`PARTIAL_USED`) |
| TC-WH-LOC-003 | 02 | `warehouse_detail` render card tồn kho theo vị trí, phân trang đúng khi > 30 batch, không N+1 query bùng nổ |
| TC-WH-LOC-004 | 01 | Batch bị trừ hết qty qua `move_batch_qty`/`issue_gin` (status chuyển `CLOSED`) không xuất hiện trong `location_occupancy` dù không lọc predicate qty riêng — xác nhận invariant "CLOSED ⇒ qty=0" đủ để loại, không cần `qty_received > qty_used` |
| TC-WH-CAP-001 | 01 | `location_capacity_alerts` rỗng khi cả 2 capacity đều `None` |
| TC-WH-CAP-002 | 02 | Location ratio 0.9–0.99, warehouse `capacity is None` → đúng 1 message "gần đầy" (cấp location) |
| TC-WH-CAP-003 | 03 | Location ratio ≥ 1.0, warehouse ratio < 0.9 → đúng 1 message "vượt dung tích" (cấp location) |
| TC-WH-CAP-004 | 04 | `transfer_create` POST tới vị trí gần đầy → transfer tạo thành công + warning trong response |
| TC-WH-CAP-005 | 05 | `qc_result` action=pass tới vị trí MAIN gần đầy → QC pass thành công + warning |
| TC-WH-CAP-006 | 05 | `qc_result` action=partial tương tự TC-WH-CAP-005 |
| TC-WH-CAP-007 | 06 | `grn_receive_qty` khi Kho chờ vượt dung tích → submit QC thành công + warning, cùng response với tolerance alert nếu có |
| TC-WH-CAP-008 | 07 | Badge `warehouse_detail` đúng 3 mức OK/Gần đầy/Vượt theo dữ liệu dựng sẵn |
| TC-WH-CAP-009 | 08 | Location ratio ≥ 1.0 **và** warehouse ratio (cộng dồn mọi location của kho, gồm cả location đang xét) cũng ≥ 0.9 → `location_capacity_alerts` trả đúng 2 message (location + warehouse), không chặn hành động |
| TC-WH-OPS-001 | 01 | Snapshot STAGING: số ACTIVE + qty + số quá hạn 3 ngày đúng với dữ liệu dựng sẵn (`created_at` giả lập quá/chưa quá ngưỡng) |
| TC-WH-OPS-002 | 02 | Snapshot MAIN: đếm đúng `WarehouseHandoff` PENDING, không đếm ACCEPTED/REJECTED/CANCELLED |
| TC-WH-OPS-003 | 03 | Snapshot SCRAP: tổng qty QUARANTINE đúng, không cộng batch status khác |
| TC-WH-OPS-004 | 04 | Kho MAIN không hiện khối STAGING/SCRAP; tương tự cho STAGING/SCRAP |

## 7. Data & code map

| File | Thay đổi |
|---|---|
| `inventory/models.py` | Thêm `PHYSICAL_BATCH_STATUSES` (dưới `Batch.Status`) — nơi định nghĩa duy nhất |
| `stocktake/services.py` | Xoá định nghĩa `PHYSICAL_BATCH_STATUSES` cục bộ (dòng 52-58), đổi sang `from inventory.models import PHYSICAL_BATCH_STATUSES` (chỉ đổi nguồn import, code dùng hằng số này ở dòng 307-328 không đổi) |
| `warehouse/models.py` | Thêm hằng số `CAPACITY_WARN_RATIO = 0.9`, `STAGING_AGING_DAYS = 3` |
| `warehouse/services.py` | Thêm `location_occupancy`, `location_occupied_qty`, `warehouse_occupied_qty`, `location_capacity_alerts`, `staging_snapshot`/`main_snapshot`/`scrap_snapshot` (hoặc 1 hàm `ops_snapshot(warehouse)` chọn nhánh theo `warehouse_type` — quyết định cụ thể ở implementation plan) |
| `warehouse/views.py` | `warehouse_detail`: bổ sung context (occupancy page, capacity badge data, ops snapshot) |
| `warehouse/templates/warehouse/warehouse_detail.html` | Badge dung tích ở panel đầu + cột "Vị trí lưu trữ"; card mới "Tồn kho theo vị trí" (phân trang); card mới "Snapshot vận hành" |
| `inventory/views.py` | `transfer_create`: thêm vòng lặp `location_capacity_alerts` sau `transfer_stock()` thành công |
| `quality/views.py` | `qc_result`: thêm vòng lặp tương tự sau `qc_pass`/`qc_partial_pass` thành công |
| `receiving/views.py` | `grn_receive_qty`: thêm vòng lặp tương tự cạnh `tolerance_alerts` hiện có |

## 8. A4 — Test gap có sẵn (đóng cùng đợt, không phải AC mới)

Không đổi hành vi — chỉ bổ sung test còn thiếu cho code đã tồn tại, gộp vào cùng Phase 4 của
implementation plan vì đụng cùng file `warehouse/tests.py`:

- Form/luồng gán `Warehouse.staff` (M2M) chưa có test.
- `location_update` (`warehouse/views.py:184-199`) chưa có test.
- Nhánh thiếu Kho phế của `get_scrap_warehouse()` (`_get_singleton` raise `ValidationError` khi
  `count == 0`) chưa có test — đã xác minh: `warehouse/tests.py:339`
  (`test_get_staging_warehouse_raises_when_missing`) đã cover nhánh này cho **STAGING**, nhưng
  không có bản tương ứng cho **SCRAP** (`get_scrap_warehouse()` khi thiếu Kho phế). Cần thêm 1 test
  mới, không phải "xác minh lại" — gap đã confirm rõ, không mơ hồ.

## 9. Ngoài phạm vi

- Zone/aisle field, filter theo khu vực (Wave B).
- Capacity **hard-block** (`ValidationError`) trong `move_batch_qty`/`transfer_stock`/`qc_pass` —
  Wave A chỉ cảnh báo, không chặn giao dịch nào.
- Putaway suggestion (gợi ý vị trí còn chỗ khi QC PASS/transfer).
- In nhãn vị trí (label printing).
- Chuẩn hoá đơn vị đo khi tính capacity (UOM mismatch) — chấp nhận xấp xỉ ở Wave A (mục 3.1).
- Auto-PO dưới Min Level (Celery), REWORK QC, scanner hardware, slotting AI kiểu SAP EWM.
- Đổi `Inventory` sang lưu tồn theo từng vị trí (grain hiện tại vẫn là product × warehouse).

## 10. Doc sync kèm Wave A

- `wh_plan.md` §Todos: tick "Wave A — Hardening" khi triển khai xong; cập nhật bảng "Hiện trạng
  (BA)" (dòng "Inventory theo vị trí"/"Capacity kho/vị trí").
- `wh_plan.md` §"Quyết định đã chốt (pre-FSD)" dòng #4 và §"Phases triển khai Wave A" bước 1: sửa
  đích `PHYSICAL_BATCH_STATUSES` từ "`inventory.services`" thành "`inventory.models`" — FSD này đã
  chốt lại đích thật (mục 2.1) nhưng file gốc `wh_plan.md` vẫn còn ghi đích cũ; sửa ngay khi FSD
  được duyệt, tránh implementation plan tham chiếu nhầm file nguồn.
- `CLAUDE.md`: nếu phát sinh invariant mới đáng ghi (ví dụ bug thật gặp lúc code, theo mục
  "Established patterns to apply proactively") — chỉ thêm nếu có bài học tổng quát, không chép lại
  mô tả tính năng.
- `.claude/skills/wms-conventions/SKILL.md`: nếu badge 3-mức (OK/Gần đầy/Vượt) trở thành pattern
  tái dùng ở module khác sau này, ghi thành 1 mục riêng.
- `BACKLOG.md`: Wave A không có FR-code mới (đây là PUR-Expansion-style hardening ngoài 60-FR gốc,
  giống cách PUR Expansion không đụng bộ đếm FR) — không có checkbox nào cần tick ở đây.
