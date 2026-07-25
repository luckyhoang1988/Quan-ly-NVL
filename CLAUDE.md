# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

Phase 0 scaffolding is done and well past it: this is a working Django project (`manage.py`, `config/`
settings, `requirements.txt` pinned deps) with all 10 apps from the module map below already created —
`accounts`, `warehouse`, `catalog`, `partners`, `purchasing`, `inventory`, `receiving`, `quality`,
`shipping`, `stocktake` (plus `reports`; `api` not started). Standard Django commands apply:
`manage.py runserver`, `manage.py test <app>`, `manage.py makemigrations`/`migrate`, `manage.py check`.
DB is PostgreSQL (`psycopg[binary]`), configured via `.env` (see `config/settings.py`) — not SQLite, don't
assume a fallback exists. No Dockerfile/compose yet — still pre-Docker per the "Docker deferred to end of
Phase 1" decision below. Progress against the 60-FR checklist is tracked at the top of `BACKLOG.md`
(currently 55/60) — check there for what's actually implemented per module rather than assuming from the
phase table alone, since phases can be partially done.

## What this project is

**NVL/WMS** — a warehouse/raw-materials management system (Nhà cung cấp → GRN → QC → Inventory → GIN →
Reporting) being built by a solo developer using Claude Code as the primary implementation partner. Source
documents are in Vietnamese; code and identifiers should be English as usual.

## Source-of-truth documents

- `BACKLOG.md` (repo root) — **the operative spec**. It distills the BRD/SRS/FSD into a phase-by-phase
  checklist with FR codes, business rules, workflow states, and data models, adjusted for solo-dev
  constraints. Treat this as more current and more actionable than the raw docs in `Tai_lieu/`.
- `Tai_lieu/1_BRD_NVL_System.docx`, `2_SRS_NVL_System.docx`, `3_FSD_NVL_System.docx` — the original
  Business Requirements / Software Requirements / Functional Specification documents. **These are binary
  `.docx` files** — the `Read` tool cannot open them directly; extract text first (e.g. unzip and strip
  `word/document.xml`) if detail beyond what's summarized in `BACKLOG.md` is needed. The FSD is the most
  detailed for the GRN and GIN modules specifically.
- `Tai_lieu/quy_trinh_mua_hang_qc_nhap_kho.pdf` — the real-world purchasing → QC → warehouse-receipt process
  the system is modeling.
- `Tai_lieu/Ke_Hoach_Trien_Khai_NVL_Solo.pdf` — the solo-dev delivery plan; explains *why* the tech stack and
  module order deviate from the original BRD/SRS (written for a 6-8 person team).
- `Tai_lieu/GIAI_THICH_BRD_SRS_FSD_FULL.pdf` — a conceptual explainer of what BRD/SRS/FSD are, illustrated
  with this project's GRN/GIN modules as examples.

**Session hygiene**: don't load all of `Tai_lieu/` into context at once. Per the delivery plan, read only the
FSD section for the module currently being implemented — dumping all three documents dilutes context and
leads to unfocused code.

## Working with BACKLOG.md

- Only lines with a bold `FR-XX-##` code count toward the 60-FR progress total (tracked at the top of the
  file: "Tổng tiến độ: X / 60 FR"). Business Rules, Workflow States, Data Model, and Algorithm notes under
  each module are supporting technical detail for implementation, not counted requirements — check those off
  freely.
- The `⏸️` marker means a feature conceptually needs Celery/Redis (background jobs) but that's deferred
  per the solo-dev plan. The standard substitute pattern is: compute on-the-fly at page load (e.g.
  `WHERE exp_date < today + 30`) or use a Django management command invoked by cron, instead of a task queue.
  Don't introduce Celery/Redis unless a backlog item explicitly graduates out of this deferral (Phase 5+).
  Preserve this pattern when implementing anything marked `⏸️`.
- When a module's FRs are completed, tick the checkboxes in `BACKLOG.md` — it's the single tracked backlog,
  not a Jira/Trello substitute.
- Phụ Lục B test case naming convention: `TC-<MODULE>-<FR#>-<seq>` (e.g. `TC-GIN-002-001`).

## Keeping documentation in sync

After finishing any code change, check whether it needs to be reflected in:

- **`.claude/skills/wms-conventions/SKILL.md`** — if the change establishes or modifies a reusable
  convention (UI pattern, code organization, workflow). Update the relevant entry there so the next session
  doesn't have to re-derive it.
- **This file (`CLAUDE.md`)** — if the change is a cross-cutting design decision, a "chốt" (settled)
  architecture choice, or a project-wide convention. Keep the "Non-obvious cross-cutting design decisions"
  and "UI conventions" sections matching what the code actually does.
- **`BACKLOG.md`** — if the change completes or touches a tracked `FR-XX-##`: tick the checkbox and update
  the "Tổng tiến độ: X / 60 FR" line at the top of the file.

Not every change touches all three — a pure UI/refactor pass with no FR attached only needs the skill file
and/or this file; there's nothing to tick in `BACKLOG.md` for it.

## Frontend language convention

**The entire UI is Vietnamese — always, by default, no exceptions to call out per task.** This was fixed
project-wide on 2026-07-25 (every model field across all 10 apps was missing an explicit `verbose_name`, so
Django auto-generated English labels like "Code"/"Name"/"Warehouse type" from the field name — visible on
every create/edit form even though surrounding template text was already Vietnamese). Apply automatically to
all new/changed code, without being asked again:

- `LANGUAGE_CODE = 'vi'` in `config/settings.py` (not `'en-us'`) — this makes Django's own built-in strings
  Vietnamese for free: `AbstractUser` field labels (username, email, first/last name, is_active), built-in
  validation messages ("This field is required." → "Trường này là bắt buộc."), and Django admin chrome.
  It does **not** translate custom field labels — those still need an explicit `verbose_name`.
- **Every model field must have an explicit Vietnamese `verbose_name`.** Django auto-generates a label from
  the field name (`warehouse_type` → "Warehouse type") when `verbose_name` is omitted, and that auto-label is
  plain string formatting, not run through gettext — `LANGUAGE_CODE` alone does not fix it. Add
  `verbose_name='...'` to every `CharField`/`ForeignKey`/etc. when writing a new model or field, and add
  `Meta.verbose_name` / `verbose_name_plural` too (both in Vietnamese).
- **`TextChoices` display labels** (the second element of each choice tuple) must be Vietnamese
  (`PASS = 'PASS', 'Đạt'`, not `'Pass'`) — this is already the convention everywhere except it was missed for
  `accounts.User.Role` (fixed: `'Warehouse Manager'/'Staff'/'QC Inspector'/...` → `'Quản lý kho'/'Nhân viên
  kho'/'Nhân viên QC'/...`).
- **Standalone form fields, template text, button labels, table headers, flash messages
  (`messages.success/error`), and `AuditLog.description` strings** must be Vietnamese — these don't come from
  `verbose_name` and are easy to leave in English by habit (e.g. `f'Đã tạo Supplier "{code}"'` should say
  `f'Đã tạo Nhà cung cấp "{code}"'`).
- **Established exceptions — keep these as-is, they are not violations**: domain/document abbreviations
  already used throughout `BACKLOG.md` and the BRD/SRS/FSD source docs — `GRN`, `GIN`, `PO`, `PR`, `SO`,
  `QC`, `SKU`, `FIFO`, `EOQ`, `ABC Analysis`, `Dashboard`, `Override` — and common IT loanwords already
  written untranslated elsewhere in the project's own Vietnamese text (`Email`, `Website`). Don't force these
  into stiffer pure-Vietnamese phrasing; match the terminology `BACKLOG.md` already uses.
- After adding/changing any `verbose_name` or `Meta` options, run `manage.py makemigrations` — this generates
  a state-only migration (no `ALTER TABLE`, since `verbose_name` isn't a DB column property) but Django still
  needs it to keep migration history in sync. Run `manage.py migrate` after.

## Planned architecture (not yet implemented)

Tech stack deliberately deviates from the original SRS (React 18 + DRF + Celery/Redis + Docker-from-day-1,
written for a full team) toward a solo-maintainable stack:

- **Django Template + Bootstrap 5 + HTMX**, monolith — not a separate React SPA + DRF API. Avoids
  maintaining CORS/token auth and two codebases. DRF/JWT (`FR-API-*`) is explicitly low priority — build a
  plain JSON Django view only if HTMX needs one async endpoint (e.g. FIFO batch suggestion), not a full API
  layer.
- **PostgreSQL**, no Redis/Celery until a backlog item genuinely requires it (see `⏸️` pattern above).
- **Docker** deferred to the end of Phase 1, not set up in Phase 0 — get something running locally first.

### Module → Phase → Django app map

| Phase | Module | Django app |
|---|---|---|
| 1 | User & Permission | `accounts` |
| 1 | Warehouse Management | `warehouse` |
| 1 | Product/SKU (master data, no FR code) | `catalog` |
| 1 | Supplier (master data, no FR code) | `partners` |
| 1 | PO stub (minimal, no FR code) | `purchasing` |
| 1 | Inventory/Batch (schema only) | `inventory` |
| 2 | GRN (Goods Receipt Note) | `receiving` |
| 2 | Quality Control (QC) | `quality` |
| 3 | Inventory Management (full logic) | `inventory` |
| 3 | GIN (Goods Issue Note) | `shipping` |
| 4 | Stock Opname (kiểm kê) | `stocktake` |
| 5 | Purchase Order (full) | `purchasing` |
| 6 | Reporting & Analytics | `reports` |
| 7 | API & Integration (optional) | `api` |

Build order follows this dependency chain, not the BRD's business-narrative order (User/Permission was moved
from position #9 to Phase 1 because every audit trail and RBAC check needs the `User` model to exist first).

## UI conventions

- **Detail-page key/value panels use `<table class="table-accent">`, not `dl.row`.** Bootstrap's
  `dl.row`/`dt`/`dd` grid only renders reliable borders when the `dt`+`dd` column widths sum to exactly 12,
  and a 2-column split (`col-md-6`) only gets a divider border at the `md` breakpoint and up — both broke in
  practice and were rejected twice before landing on the table version (see `grn_detail.html` as the
  reference page). Full pattern, density rules, and the list of pages already converted are in
  `.claude/skills/wms-conventions/SKILL.md` — follow it for any new detail page instead of reinventing the
  layout.

## Non-obvious cross-cutting design decisions

These span multiple modules and are easy to get wrong if implemented module-by-module without the full
picture — read `BACKLOG.md` Phase 2/3 in full before touching GRN, QC, or GIN:

- **PO ↔ GRN circular dependency**: `FR-GRN-04` requires GRN to reference a PO, but the full PO workflow
  (approval, supplier price comparison) isn't built until Phase 5. Resolved with a **PO stub** in Phase 1
  (just `po_no`, `supplier_id` FK, `status` defaulted to `SENT`, line items) — enough for GRN to have a valid
  FK. Don't build PO approval workflow while implementing the stub.
- **GRN/QC/Batch/Inventory is one transaction, not three**: QC result determines what happens atomically —
  PASS creates a Batch (`PENDING_RECEIPT` since Phase D, see below) and increases inventory; FAIL creates a
  `GRN_RETURN` and must **not** touch inventory; PARTIAL_PASS splits into two batches (`PENDING_RECEIPT` for
  the passed qty, `QUARANTINE` for the failed qty) and only credits inventory for the passed portion. Each
  path needs its own test — the backlog calls out the FAIL and PARTIAL_PASS paths as the easiest to get
  wrong.
- **Batch status enum** (`PENDING_RECEIPT`, `ACTIVE`, `PARTIAL_USED`, `QUARANTINE`, `EXPIRED`, `CLOSED`) gates
  what GIN can select: only `ACTIVE` batches are eligible for FIFO issue; `PENDING_RECEIPT` (chờ kho xác
  nhận nhận hàng, Phase D)/`QUARANTINE`/`EXPIRED` must all be rejected even if `qty_available > 0`.
- **FIFO issue algorithm** (GIN): select from `batch WHERE product_id=? AND qty_available>0 AND status='ACTIVE'
  ORDER BY exp_date ASC, created_at ASC`, and allow splitting the requested qty across multiple batches if
  one batch isn't enough. This is flagged in the backlog as the most error-prone piece of logic and requires
  dedicated unit tests (single batch, multi-batch split, insufficient total stock).
- **Three warehouse types, one mandatory QC staging step**: `Warehouse.warehouse_type` is `MAIN` (normal,
  GIN/FIFO-eligible), `STAGING` ("Kho chờ" — goods awaiting QC), or `SCRAP` ("Kho phế" — QC-rejected goods).
  At most one active `STAGING` warehouse and one active `SCRAP` warehouse company-wide, enforced by a DB-level
  `UniqueConstraint`; `warehouse_type` is locked (disabled in the form) after creation — changing type means
  creating a new warehouse and deactivating the old one via `warehouse.services.deactivate_warehouse()`.
  Confirmed qty received on a GRN goes straight into a full `Batch` (`ACTIVE`) + `Inventory` record in
  `STAGING` via `start_qc()` — it no longer "disappears" from the system until QC decides, the way it used
  to. `qc_pass`/`qc_fail`/`qc_partial_pass` then consume that staging batch via
  `inventory.services.move_batch_qty()` (the shared split primitive also used by `transfer_stock`, recording
  `TRANSFER_OUT`/`TRANSFER_IN` rather than `RECEIPT`): PASS moves 100% to a new `PENDING_RECEIPT` batch in the
  chosen `MAIN` warehouse (see Phase D bullet below — no longer `ACTIVE` immediately); FAIL moves 100% to a
  new `QUARANTINE` batch in `SCRAP`; PARTIAL_PASS splits into both (`PENDING_RECEIPT` + `QUARANTINE`) in the
  same transaction. `Batch.grn_item` (nullable `PROTECT` FK) tracks lineage back to the source `GrnItem`
  across every split. Two guards keep the "must go through QC" invariant: `qc_pass`/`qc_partial_pass` reject
  any destination location whose warehouse isn't `MAIN`, and `transfer_stock` rejects any source batch
  currently sitting in a `STAGING` warehouse. `GinForm`/`Gin.clean()` restrict GIN to `MAIN` warehouses only —
  necessary because the FIFO query's `status='ACTIVE'` filter alone doesn't exclude staging batches (they're
  `ACTIVE` too); it's the warehouse restriction that does the excluding. `reports.services` (dashboard KPI,
  ABC Analysis, slow-moving) and the Min/Max banner in `inventory_list` all filter
  `warehouse__warehouse_type=MAIN` so `STAGING`/`SCRAP` stock never inflates those numbers. Singleton lookup
  helpers (`get_staging_warehouse()`, `get_scrap_warehouse()`, `get_default_location()`) live in
  `warehouse/services.py` (new file — this app previously had no service layer).
- **Audit trail** (who/what/when/why) is required on every state transition of GRN, QC, and Batch from Phase
  2 onward — it's called out as "hard to add later," so don't defer it while building the workflow states.
- `qty_available = qty_on_hand - qty_reserved` is computed, not stored input — keep it derived wherever it's
  used (Inventory, Batch).
- **Department-manager approval axis, added 2026-07-25, parallel to `role`, not a replacement**: `User.role`
  keeps deciding the CRUD permission matrix (`user.can(action, module)`) exactly as before — don't touch
  `ROLE_PERMISSIONS`/`rbac.py` for the new approval-hierarchy work. A second, independent axis —
  `User.department` (`WAREHOUSE`/`QC`/`PURCHASING`/`ACCOUNTING`) + `User.is_manager` (bool) — was added
  specifically so every department (not just the warehouse `MANAGER` role) can have its own manager who
  approves/cancels tickets currently in their department's stage. Check it via
  `user.is_department_manager(department)`, never via `user.role == 'MANAGER'` (that check now only means
  "has warehouse CRUD/approve rights," not "is anyone's manager"). In-app notifications
  (`accounts.models.Notification`, sent via `accounts.notifications.notify()`) and the audit-log search page
  (`/audit-log/`, gated to `is_manager or role==ADMIN or is_superuser`) were built on top of this axis — both
  are plain DB-polled, no Celery/websocket, per the `⏸️` convention. `Warehouse.staff` (M2M→User, limited to
  `department=WAREHOUSE`) lets a GRN/QC handoff target a specific warehouse's assigned staff instead of every
  `STAFF`-role user company-wide; empty assignment falls back to notifying the whole `WAREHOUSE` department.
  A staff→department-manager "submit for approval" pattern (GRN submit, GrnReturn QC sign-off, GIN confirm,
  and the Phase D warehouse handoff below are all done) is built on top via a generic `Approval` model
  (`accounts.models.Approval`, GenericFK `target`) + `accounts.approvals.create_approval()`/
  `decide_approval()`/`latest_approval_for()` — see
  `.claude/skills/wms-conventions/SKILL.md` §4 for the reusable helpers before wiring a new approval step.
- **GRN submit is now itself gated by `Approval` (added Phase B, 2026-07-26)**: `Grn.Status` gained
  `PENDING_APPROVAL` (between `DRAFT` and `PENDING_QC`) and `CANCELLED`. Staff "Nộp" no longer calls
  `submit_to_pending_qc()` directly — `receiving.services.request_submission()` moves the GRN to
  `PENDING_APPROVAL` and creates an `Approval` for `department=WAREHOUSE`; only a WAREHOUSE
  `is_department_manager` (or the existing `can('approve','grn')` Manager/Admin fallback) can decide it via
  `decide_grn_submission()`, which calls the real `submit_to_pending_qc()` on approve or reverts to `DRAFT`
  on reject. `submit_to_pending_qc()` itself still accepts `DRAFT` as a source status too (not just
  `PENDING_APPROVAL`) — kept so `seed_demo_data.py` and other programmatic callers can skip the approval gate
  entirely; only the UI submit path goes through `request_submission()`. `Grn.current_department` (property:
  `WAREHOUSE` for `DRAFT`/`PENDING_APPROVAL`/`PENDING_QC`, `QC` for `QC_IN_PROGRESS`, else `None`) drives who
  can `grn_cancel` a ticket at its current stage (e.g. a QC department manager can cancel a GRN that's
  `QC_IN_PROGRESS`, per `receiving.views.can_cancel_grn`) — this property intentionally hardcodes the
  department string constants instead of importing `accounts.models.User` to avoid a cross-app model
  dependency at the model layer; keep that pattern for any similar property added later.
- **`GrnReturn` return-to-supplier flow is now department-routed, not Manager-only (Phase B)**:
  `approve_return()` (PENDING→APPROVED) now accepts `department=PURCHASING` in addition to the existing
  `can('approve','grn')` Manager/Admin path, and fans out a `notify()` to both QC and WAREHOUSE departments.
  `mark_return_returned()` (APPROVED→RETURNED) is no longer exposed directly to any view — it only runs
  inside `decide_return_confirmation()`'s `on_approve` callback. The flow is: QC calls
  `request_return_confirmation()` (creates an `Approval` for `department=QC`) after physically checking the
  returned goods; a QC `is_department_manager` decides it via `decide_return_confirmation()`, which on
  approve calls `mark_return_returned()` for real and `notify()`s the WAREHOUSE department (the "auto-forward
  to warehouse" behavior) — on reject the `GrnReturn` simply stays `APPROVED` so QC can re-request later.
- **GIN start-picking is approval-gated too (Phase C, mirrors GRN submit)**: `Gin.Status` gained
  `PENDING_APPROVAL` (between `DRAFT` and `PICKING`) and `CANCELLED`. STAFF has no `update` permission on
  GIN (only `create`+`read`), so they use a *new*, separate view (`shipping.views.gin_confirm_request`, gated
  by `user.can('create', 'gin')`) to move DRAFT→PENDING_APPROVAL and create an `Approval` for
  `department=WAREHOUSE` — this is deliberately a different view from the pre-existing `gin_start_picking`
  (kept unchanged, still gated by `update`), not a rewire of one shared button like GRN's `grn_submit`: a
  Manager/Admin (who already has `update`) still starts picking directly with no self-approval detour, while
  STAFF's request must go through `shipping.views.gin_approve_confirmation`/`gin_reject_confirmation`
  (`user.is_department_manager('WAREHOUSE') or user.can('approve', 'gin')`) →
  `shipping.services.decide_gin_confirmation()`, which on approve calls the real `start_picking()` (now
  accepts `DRAFT` *or* `PENDING_APPROVAL` as source status, same dual-entry reasoning as
  `submit_to_pending_qc`) or on reject reverts to `DRAFT`. `gin_cancel` mirrors `grn_cancel` but is simpler —
  GIN only ever belongs to one department (`WAREHOUSE`), so there's no `current_department`-style property;
  the permission check is a flat `user.is_department_manager('WAREHOUSE') or user.can('delete', 'gin')`, and
  cancel is blocked once `ISSUED` (inventory has already been deducted for real by then).
- **QC PASS → warehouse handoff (Phase D)**: `Batch.Status` gained `PENDING_RECEIPT` ("Chờ kho xác nhận"),
  placed *before* `ACTIVE` in the enum. `qc_pass`/`qc_partial_pass` (`quality/services.py`) now create the
  passed-qty destination batch as `PENDING_RECEIPT` instead of `ACTIVE` — `Inventory.qty_on_hand` at the
  destination `MAIN` warehouse is still credited immediately (physical goods really are there), only the
  per-batch `status` gates FIFO eligibility, so `suggest_fifo_batches`'s existing `status=ACTIVE` filter
  needed no changes to exclude it. Each such batch gets one `inventory.models.WarehouseHandoff`
  (`inventory.services.create_handoff()`) — an optional `assigned_to` picked on the QC PASS/PARTIAL_PASS form
  (`quality.forms.QcResultForm.assigned_to`, queryset `department=WAREHOUSE`) notifies just that person;
  left blank, `create_handoff()` notifies `destination_warehouse.staff` (falls back to the whole
  `department=WAREHOUSE` if that kho has no staff assigned — same fallback rule as the Phase A
  `Warehouse.staff` design). `inventory.services.accept_handoff()` flips the batch to `ACTIVE` (now real FIFO
  stock) and notifies the QC inspector back; `reject_handoff()` requires a `reason` and a
  `WarehouseHandoff.RejectDestination` choice: `TO_SCRAP` calls `move_batch_qty()` to split the batch into a
  new `QUARANTINE` batch in `SCRAP` (reuses the same primitive as `qc_fail`/`transfer_stock`); `BACK_TO_QC`
  deliberately does **not** touch Batch/Inventory (mirrors the QC-override boundary: "annotation only, never
  reverse a completed transaction automatically") — it just flips `WarehouseHandoff.status` to `REJECTED` and
  notifies `department=QC` to resolve it manually (e.g. a manual `transfer_stock` to `SCRAP` afterward, which
  is why `move_batch_qty`'s source-status guard was widened to accept `PENDING_RECEIPT` as a valid source,
  not just `ACTIVE`/`PARTIAL_USED`). Who may decide a handoff — `inventory.views.can_decide_handoff()` — is
  `is_department_manager('WAREHOUSE')` (oversight, sees/decides everything) OR the exact `assigned_to` user
  OR (if unassigned) anyone in `destination_warehouse.staff`, falling back to any `department=WAREHOUSE` user
  if that kho has no staff assigned — same three-tier resolution as who gets *notified*, so "who sees it in
  their queue" and "who was told about it" never disagree. Queue page: `inventory:handoff_list`
  ("Phiếu chờ nhận hàng" in the sidebar, gated to `department=WAREHOUSE`/ADMIN/superuser). Unlike GRN/GIN's
  Approval-based gates, this flow does **not** use the generic `Approval` model — a handoff is a hand-off to
  a *specific person or team*, not a request that bubbles up to one department manager's decision, so it has
  its own lighter PENDING/ACCEPTED/REJECTED model instead.
