# PROMPT_TEMPLATE — NVL/WMS

Mẫu prompt để mỗi phiên làm việc với Claude Code bám đúng `BACKLOG.md` và quy ước dự án.
Copy khối "PROMPT BẮT ĐẦU PHIÊN" bên dưới, điền chỗ `«...»`, rồi dán vào Claude Code.

> Nguyên tắc bất di bất dịch của dự án này: **chia nhỏ ra làm — xong MỖI nhiệm vụ phải DỪNG và hỏi
> tôi có muốn tiếp tục nhiệm vụ tiếp theo không.** Không tự động nối nhiều việc.

---

## PROMPT BẮT ĐẦU PHIÊN (copy phần dưới)

```
Bối cảnh: dự án NVL/WMS (kho nguyên vật liệu). Đọc CLAUDE.md và BACKLOG.md trước khi làm.

Nhiệm vụ hôm nay:
- Phase:   «vd: Phase 2 — GRN»
- Module / app Django:  «vd: receiving»
- FR cần làm:  «vd: FR-GRN-04, FR-GRN-05»
- Tài liệu FSD cần đọc (CHỈ đúng phần này, đừng nạp cả Tai_lieu/):  «vd: FSD mục GRN»

Yêu cầu cách làm:
1. Tóm tắt cách hiểu của bạn về FR + business rule liên quan (trích từ BACKLOG.md) rồi ĐỢI tôi xác nhận.
2. Chia thành các bước nhỏ. Làm 1 bước → dừng → hỏi tôi mới làm tiếp.
3. Viết test cho các nhánh dễ sai (xem checklist dưới) TRƯỚC hoặc song song với code.
4. Xong FR nào thì tick checkbox trong BACKLOG.md và cập nhật "Tổng tiến độ: X / 60 FR".
```

---

## Ràng buộc kỹ thuật (Claude phải tự tuân, không cần nhắc lại mỗi lần)

**Stack** (cố ý khác SRS gốc — xem `CLAUDE.md`):
- Django Template + Bootstrap 5 + **HTMX**, monolith. KHÔNG dựng React SPA + DRF.
- PostgreSQL. **KHÔNG** thêm Redis/Celery cho tới khi một backlog item ở Phase 5+ thực sự cần.
- Mục `⏸️` trong BACKLOG = thay bằng: tính on-the-fly lúc load trang (vd `WHERE exp_date < today + 30`)
  hoặc management command chạy bằng cron. Không dựng task queue.
- DRF/JWT (`FR-API-*`) ưu tiên thấp — chỉ viết 1 JSON view thuần nếu HTMX cần 1 endpoint async
  (vd gợi ý batch FIFO), không dựng cả tầng API.
- Docker để cuối Phase 1, không phải Phase 0.

**Quy ước code:**
- Tài liệu nguồn tiếng Việt, nhưng **code + identifier tiếng Anh**. Comment có thể tiếng Việt.
- Migration **PHẢI commit** (không gitignore).
- Secret/DB đọc từ `.env` qua `os.getenv` — KHÔNG hardcode. `.env` bị gitignore; cập nhật `.env.example`
  khi thêm biến mới.

**Bẫy logic xuyên-module (đọc kỹ BACKLOG Phase 2/3 trước khi động vào GRN/QC/GIN):**
- **PO ↔ GRN**: Phase 1 chỉ làm PO *stub* (`po_no`, `supplier_id` FK, `status='SENT'`, line items) đủ để GRN
  có FK hợp lệ. KHÔNG dựng workflow duyệt PO ở giai đoạn stub.
- **GRN/QC/Batch/Inventory = MỘT transaction**, không phải ba:
  - `PASS` → tạo Batch `ACTIVE` + tăng tồn kho.
  - `FAIL` → tạo `GRN_RETURN`, **KHÔNG** đụng tồn kho.
  - `PARTIAL_PASS` → tách 2 batch (`ACTIVE` cho phần đạt, `QUARANTINE` cho phần rớt), chỉ cộng tồn phần đạt.
  - Mỗi nhánh 1 test riêng; FAIL và PARTIAL_PASS là dễ sai nhất.
- **Batch status** (`ACTIVE / PARTIAL_USED / QUARANTINE / EXPIRED / CLOSED`): chỉ `ACTIVE` mới được GIN chọn.
  `QUARANTINE`/`EXPIRED` phải bị từ chối kể cả khi `qty_available > 0`.
- **FIFO (GIN)**: `batch WHERE product_id=? AND qty_available>0 AND status='ACTIVE' ORDER BY exp_date ASC,
  created_at ASC`; cho phép tách 1 yêu cầu qua nhiều batch. Test bắt buộc: 1 batch / tách nhiều batch /
  thiếu tổng tồn.
- **Audit trail** (ai / cái gì / khi nào / vì sao) bắt buộc trên MỌI chuyển trạng thái của GRN, QC, Batch
  từ Phase 2 — làm ngay, đừng để sau ("hard to add later").
- `qty_available = qty_on_hand - qty_reserved` là **giá trị tính ra**, không lưu như input.

**Định nghĩa HOÀN THÀNH một FR:**
- [ ] Code chạy, `manage.py check` sạch, `manage.py test «app»` xanh.
- [ ] Có test cho các nhánh dễ sai ở trên.
- [ ] Test đặt tên theo quy ước: `TC-<MODULE>-<FR#>-<seq>` (vd `TC-GIN-002-001`).
- [ ] Đã tick checkbox FR trong `BACKLOG.md` + cập nhật "Tổng tiến độ: X / 60 FR".
- [ ] Migration đã tạo và commit.

---

## Bản đồ Module → Phase → app Django (tham chiếu nhanh)

| Phase | Module | app |
|---|---|---|
| 1 | User & Permission | `accounts` |
| 1 | Warehouse | `warehouse` |
| 1 | Product/SKU (master data) | `catalog` |
| 1 | Supplier (master data) | `partners` |
| 1 | PO stub | `purchasing` |
| 1 | Inventory/Batch (schema) | `inventory` |
| 2 | GRN | `receiving` |
| 2 | Quality Control | `quality` |
| 3 | Inventory (logic đầy đủ) | `inventory` |
| 3 | GIN | `shipping` |
| 4 | Stock Opname | `stocktake` |
| 5 | Purchase Order (đầy đủ) | `purchasing` |
| 6 | Reporting & Analytics | `reports` |
| 7 | API & Integration (tuỳ chọn) | `api` |
