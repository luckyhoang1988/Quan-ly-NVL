# Warehouse Expansion — Bản thống nhất (Wave C)

> Mục đích: lộ trình hoàn thiện module Kho sau khi 6 FR-WM đã tick trên `BACKLOG.md`.
> App `warehouse/` = master data + singleton STAGING/SCRAP + staff M2M.
> Nghiệp vụ tồn / Min-Max / transfer / handoff sống ở `inventory/` (không làm lại FR-WM).
> Mirror cách làm QC/PUR: plan gốc ở đây → FSD/implementation plan trong `docs/wh/`.

## Todos

- [x] **Ngay (độc lập Wave A):** xác minh test cover BR-WM-003/004 rồi tick checkbox trên `BACKLOG.md` — xong, xem chi tiết dưới mục A4
- [x] Wave A — Hardening (inventory theo vị trí, capacity soft-warn gồm STAGING receipt, ops snapshot, test/doc)
- [ ] Wave B — Mở rộng vận hành (zone/aisle, capacity hard-block, putaway suggest, nhãn vị trí) — chỉ khi vận hành thật nghẽn sau Wave A
- [ ] Viết FSD + implementation plan Wave A tại `docs/wh/01_*.md` / `02_*.md` trước khi code
- [ ] Wave B: chỉ viết `docs/wh/03_*.md` / `04_*.md` sau khi Ryan xác nhận cần

## Hiện trạng (BA)

| Lớp | Trạng thái |
|---|---|
| FR-WM-01..06 | Đã tick — CRUD kho/vị trí; tồn/Min-Max/transfer ở `inventory` |
| Singleton STAGING/SCRAP + type lock | Đã có (DB + form + activate service) |
| `Warehouse.staff` M2M | Có UI + test (`WarehouseStaffAssignmentTest`, Task 11) |
| Capacity kho/vị trí | Wave A: soft-warn 3 điểm (detail badge, transfer/QC đích, GRN→STAGING) — hard-block vẫn Wave B |
| Inventory theo vị trí | Wave A: card "Tồn kho theo vị trí" trên `warehouse_detail`, phân trang 30/trang |
| Auto-PO khi dưới Min | Còn `⏸️` (Phase 5 / Celery) — **ngoài phạm vi** roadmap này |
| BR-WM-003 / BR-WM-004 | **Đã tick** trên BACKLOG — test `TC_GIN_ISSUE_001` / `TC_QC_START_002_001` xác nhận |

> Lưu ý kỹ thuật (Wave A): `capacity == 0` được xử lý giống hệt `capacity is None` (không hiện
> badge/cảnh báo) — tránh `ZeroDivisionError`, xem "Ràng buộc chung" trong
> `docs/wh/02_wave_a_implementation_plan.md`.

## Gap vận hành đáng làm

1. Không nhìn được tồn theo vị trí ngay trên trang kho.
2. Capacity không cảnh báo khi gần/đầy.
3. Không có snapshot vận hành (STAGING aging, handoff PENDING, SCRAP quarantine).
4. Test/doc lệch code (staff, `location_update`, BR chưa tick).

## Lộ trình Wave C (A → B)

```
Wave A Hardening  →  (vận hành ổn)  →  Wave B Ops expand (khi cần)
```

| Wave | Mục tiêu | Tiêu chí xong |
|---|---|---|
| **A** | Nhìn + cảnh báo trên nền hiện có | FSD A + code + test xanh + sync doc/BACKLOG |
| **B** | Putaway / zone / chặn cứng capacity / nhãn | FSD B riêng + code; chỉ khi Ryan xác nhận nghẽn |

### Ngoài phạm vi toàn lộ trình

- Auto-PO dưới Min (Celery)
- REWORK QC, scanner hardware, slotting AI kiểu SAP EWM
- Đổi model Inventory sang grain theo location (giữ aggregate theo warehouse như hiện tại)

---

## Wave A — Hardening (chi tiết)

### A1. Inventory theo vị trí trên `warehouse_detail`

- Helper `warehouse.services.location_occupancy(warehouse)`: status ∈ tập "còn vật lý"
  (`PHYSICAL_BATCH_STATUSES`, không `CLOSED`) — **không** lọc thêm predicate riêng
  `qty_received - qty_used > 0`: `CLOSED` đã bao phủ đúng trường hợp qty cạn, mọi điểm trừ qty
  trong dự án đều đóng batch về `CLOSED` ngay khi hết qty (xem FSD 2.2).
- **Đã chốt (Refactor constant):** trước khi A1 dùng tập status đó — **chuyển `PHYSICAL_BATCH_STATUSES` từ `stocktake.services` sang `inventory.models`** (đích cuối — không phải `inventory.services`, circular import thật, xem FSD 2.1). Tránh `warehouse` (Phase 1) import ngược từ `stocktake` (Phase 4).
- UI bảng trên `warehouse_detail`: vị trí / SKU / batch / status / qty available / link `batch_detail`.
- Áp dụng cả MAIN / STAGING / SCRAP. Tránh N+1 (`select_related`).
- Hằng `STAGING_AGING_DAYS = 3` đặt trong `warehouse` (dùng ở A3).
- **Ràng buộc UI bổ sung:** kho MAIN nhiều batch lâu ngày có thể khiến bảng vị trí/batch phình
  to trên `warehouse_detail` — FSD phải chốt cách giới hạn hiển thị (phân trang, hoặc gom nhóm
  theo location rồi mới xổ chi tiết batch), không hiện toàn bộ không giới hạn.

### A2. Capacity soft-warn (không chặn)

**Đã chốt (Capacity basis):** occupancy = **cộng dồn `qty_available`** của mọi batch còn qty tại location/kho, so với `capacity` nếu không null — **xấp xỉ**, chấp nhận sai số khi nhiều SKU khác `Product.uom` cùng một vị trí. FSD Wave A phải ghi rõ giới hạn này; chuẩn hóa đơn vị capacity → Wave B nếu cần.

**Đã chốt (Phạm vi warn):** soft-warn (UI badge + `messages.warning` ở view, **không** `ValidationError` ở service) khi đích gần/đầy tại:
1. `warehouse_detail` (badge theo location/kho),
2. transfer / QC PASS|PARTIAL đích (MAIN),
3. **GRN receipt → STAGING** (`start_qc` / nhận qty) — điểm nghẽn vận hành; bỏ sót thì A2 lệch Gap #3.

- Hard-block → Wave B tại `move_batch_qty`.

### A3. Ops snapshot

| Loại kho | KPI |
|---|---|
| STAGING | Số batch ACTIVE + tổng qty; aging = `created_at` > **3 ngày** (on-the-fly, không Celery) |
| MAIN | Số `WarehouseHandoff` PENDING tới kho; link `handoff_list` |
| SCRAP | Tổng qty batch `QUARANTINE` |

### A4. Hardening test + doc

- Test: staff M2M form, `location_update`, `get_scrap_warehouse` missing, occupancy/capacity/ops context.
- Sync `CLAUDE.md` / skill nếu có invariant mới.

**Đã chốt (BR tick timing):** BR-WM-003 / BR-WM-004 **tách làm ngay, độc lập Wave A** — xác minh test hiện có (shipping/receiving/quality) cover đúng rule rồi tick trên `BACKLOG.md`; không treo tới cuối Phase A4.
**Đã xong (2026-08-05):** chạy `shipping.tests.GinIssueServiceTest.test_TC_GIN_ISSUE_001_deducts_inventory_and_sets_issued`
(qty_on_hand 100→70 khi GIN issue) và `quality.tests.StartQcTest.test_TC_QC_START_002_001_creates_active_batch_at_staging_and_credits_inventory`
(qty_on_hand 0→10 tại Kho chờ khi GRN receive qua `start_qc`) — cả hai xanh. `qty_available` là property suy ra
`qty_on_hand - qty_reserved` (`inventory/models.py:51-53`), `qty_reserved` không đổi trong 2 luồng này nên không cần
assertion riêng. Đã tick BR-WM-003/004 trên `BACKLOG.md` kèm tham chiếu test.

### AC / TC gợi ý (chi tiết hóa trong FSD)

- `TC-WH-LOC-*` — occupancy đúng location, loại CLOSED, không N+1 crash
- `TC-WH-CAP-*` — badge khi đầy; service vẫn nhận được khi vượt (soft)
- `TC-WH-OPS-*` — STAGING aging / MAIN handoff pending / SCRAP qty

### Phases triển khai Wave A

0. Spec: FSD `docs/wh/01_wave_a_hardening_fsd.md` + plan `docs/wh/02_wave_a_implementation_plan.md` — **duyệt trước khi code**
1. Chuyển `PHYSICAL_BATCH_STATUSES` → `inventory.models` + `location_occupancy` + UI bảng
2. Capacity soft-warn (detail + transfer/QC đích + STAGING receipt)
3. Ops snapshot (+ badge list nếu rẻ); dùng `STAGING_AGING_DAYS`
4. Test gap (staff / location_update / …) + regression `warehouse inventory quality shipping stocktake receiving`
   (`receiving` thêm vào vì A2 điểm 3 chạm luồng GRN start-QC)

*(BR-WM-003/004 tick: task độc lập, không thuộc Phase 4.)*

---

## Quyết định đã chốt (pre-FSD)

| # | Chủ đề | Chốt |
|---|---|---|
| 1 | Capacity basis (A2) | Cộng dồn `qty_available` (xấp xỉ); ghi giới hạn khác UOM trong FSD |
| 2 | Phạm vi warn (A2) | Detail + transfer/QC đích + **STAGING receipt (`start_qc`)**; soft only |
| 3 | BR-WM-003/004 | Tick **ngay, độc lập** Wave A sau khi xác minh test cover |
| 4 | PHYSICAL_BATCH_STATUSES | **Chuyển sang `inventory.models` trong A1** trước khi warehouse dùng (đổi từ `inventory.services` — circular import thật, xem FSD 2.1) |

---

## Wave B — Roadmap only (chưa FSD)

Chỉ sau Wave A + Ryan xác nhận cần:

1. Zone/aisle (field optional trên `Location`) + filter
2. Capacity hard-block trong `inventory.services.move_batch_qty`
3. Putaway suggest: gợi ý location MAIN còn chỗ khi QC PASS / transfer
4. Nhãn vị trí: in HTML/PDF đơn giản (code + kho)

Gate: viết `docs/wh/03_*.md` / `04_*.md` **trước** khi code Wave B.

---

## Nguyên tắc

- UI tiếng Việt; không Celery; compute on-the-fly (`⏸️` pattern).
- Service = nguồn chân lý; UI chỉ UX.
- TDD; checkbox task giữ `- [ ]` sau khi xong (convention QC/PUR).
- Không trộn PUR/QC residual vào commit Wave A.

## Việc tiếp theo ngay

1. ~~Task độc lập: xác minh + tick BR-WM-003/004 trên `BACKLOG.md`.~~ **Xong (2026-08-05).**
2. Viết FSD Wave A `docs/wh/01_wave_a_hardening_fsd.md` theo 4 quyết định đã chốt → xin duyệt.
3. Viết implementation plan TDD `docs/wh/02_…` → triển khai Phase 1–4.
