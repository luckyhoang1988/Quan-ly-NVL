# QC Expansion — 02. Implementation Plan Wave 1: Quarantine Disposition

> Nguồn: [`01_quarantine_disposition_fsd.md`](01_quarantine_disposition_fsd.md)
> Quy ước: checkbox Task giữ `- [ ]` sau khi xong (như `docs/pur/03_*`); xác nhận bằng `git log` / test.
> TDD: mỗi task — viết test FAIL → implement tối thiểu → PASS.

## Ràng buộc chung

- Lock order: **Inventory → Batch → (WarehouseHandoff nếu release)**; nếu đụng GrnReturn/GRN: khoá Grn trước khi trừ inventory ở `mark_return_returned` legacy path nếu cần, nhưng ưu tiên Inventory→Batch cho disposition thuần.
- `log_action(description=...)` ngắn; `reason=` cho lý do tự do.
- UI tiếng Việt; `verbose_name` tiếng Việt trên field mới.
- Không Celery.

## Bản đồ file

| File | Việc |
|---|---|
| `receiving/models.py` | `GrnReturn.batch`, `GrnReturn.qty` + docstring |
| `receiving/migrations/00xx_...` | AddField |
| `receiving/services.py` | Mở rộng `mark_return_returned` trừ SCRAP |
| `quality/services.py` | (không đổi cardinality `qc_fail`; optional comment) |
| `inventory/services.py` | `scrap_writeoff`, `return_quarantine_to_supplier`, `release_quarantine_to_main`, helper move quarantine |
| `inventory/forms.py` | `QuarantineDisposeForm` |
| `inventory/views.py` | `batch_dispose` + context flags trên `batch_detail` |
| `inventory/urls.py` | `batches/<pk>/dispose/` |
| `inventory/templates/.../batch_detail.html` | Nút disposition |
| `inventory/templates/.../batch_dispose.html` | Form |
| `inventory/tests.py` / `receiving/tests.py` | TC-QC-DISP-* |
| `BACKLOG.md`, `CLAUDE.md`, skill | Doc sync |

---

## Phase 1 — Model + migration

### Task 1.1 — GrnReturn.batch / qty

**File:** `receiving/models.py`, migration, `receiving/tests.py` (hoặc `inventory/tests.py`)

- [ ] RED: test tạo `GrnReturn(batch=..., qty=...)` lưu được; `batch=null` vẫn được (legacy)
- [ ] GREEN: thêm fields + `makemigrations`/`migrate`
- [ ] Sửa docstring GrnReturn

---

## Phase 2 — Services disposition

### Task 2.1 — `scrap_writeoff`

**File:** `inventory/services.py`, tests

- [ ] RED: TC-QC-DISP-001, 002, 010
- [ ] GREEN: implement `scrap_writeoff`

### Task 2.2 — `return_quarantine_to_supplier` + `mark_return_returned` inventory

**File:** `inventory/services.py`, `receiving/services.py`, tests

- [ ] RED: TC-QC-DISP-004, 005, 006, 009
- [ ] GREEN: create return; trừ SCRAP khi RETURNED

### Task 2.3 — `release_quarantine_to_main`

**File:** `inventory/services.py`, tests

- [ ] RED: TC-QC-DISP-007, 008
- [ ] GREEN: move QUARANTINE→MAIN PENDING_RECEIPT + handoff; chặn actor không override

---

## Phase 3 — Views / forms / UI

### Task 3.1 — Form + view `batch_dispose`

**File:** forms, views, urls, templates, tests view 403 (TC-QC-DISP-003)

- [ ] RED: POST writeoff/release permission tests
- [ ] GREEN: UI + wiring

### Task 3.2 — `batch_detail` buttons

- [ ] Hiện nút theo quyền; chỉ khi QUARANTINE

---

## Phase 4 — Doc sync

### Task 4.1

- [ ] Tick BACKLOG disposition
- [ ] `stale_quarantine` docstring; CLAUDE.md / skill nếu cần
- [ ] Chạy `manage.py test inventory receiving quality` (subset DISP + regression return/QC)

---

## Thứ tự commit gợi ý (khi Ryan yêu cầu commit)

1. model+migration+tests model
2. services+tests
3. views/ui+tests
4. docs BACKLOG/CLAUDE/skill
