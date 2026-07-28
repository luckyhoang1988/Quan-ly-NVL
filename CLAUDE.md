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
- **User identity/actions live in a top-right gear dropdown, not a sidebar footer block** (2026-07-28): the
  username/role + "Đổi mật khẩu"/"Đăng xuất" block moved out of the sidebar into a sticky `.app-topbar` at
  the top of `<main>` (`base.html`), collapsed behind a `bi-gear` icon button (`.user-menu-toggle`) to keep
  the sidebar to nav links only. See §7 of `.claude/skills/wms-conventions/SKILL.md` before adding another
  user-facing action (add it to the dropdown, don't recreate a sidebar user block).

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
  `is_superuser`/`role==ADMIN` (system-wide oversight, added in the bug-fix bullet below) OR
  `is_department_manager('WAREHOUSE')` (oversight, sees/decides everything) OR the exact `assigned_to` user
  OR (if unassigned) anyone in `destination_warehouse.staff`, falling back to any `department=WAREHOUSE` user
  if that kho has no staff assigned — same three-tier department resolution as who gets *notified*, so "who
  sees it in their queue" and "who was told about it" never disagree (the ADMIN/superuser tier is oversight
  only, not part of that notification fan-out). Queue page: `inventory:handoff_list` ("Phiếu chờ nhận hàng"
  in the sidebar, gated to `department=WAREHOUSE`/ADMIN/superuser). Unlike GRN/GIN's
  Approval-based gates, this flow does **not** use the generic `Approval` model — a handoff is a hand-off to
  a *specific person or team*, not a request that bubbles up to one department manager's decision, so it has
  its own lighter PENDING/ACCEPTED/REJECTED model instead.
- **PR (Yêu cầu mua hàng) routed through `Approval` + visibility scoping (Phase E, 2026-07-26)**:
  `PurchaseRequest` gained `assigned_to` (FK User, optional — requester picks 1 PURCHASING-department staff to
  handle the request; left blank, `create_approval` notifies the whole department). Creating a PR now calls
  `purchasing.services.submit_purchase_request()` right away (no separate DRAFT/"submit" step — creating a PR
  *is* submitting it), which wraps `accounts.approvals.create_approval(pr, department=PURCHASING, ...)` — same
  mechanism as GRN submit/GIN confirm (§4 in the skill file). `ROLE_PERMISSIONS['PURCHASING']['pr']` had
  `approve` removed (migration `accounts/migrations/0012_reseed_purchasing_pr_permissions.py` re-seeds
  existing users) — a plain PURCHASING-role user, even one named as `assigned_to`, can no longer decide a PR
  themselves; only `user.is_department_manager('PURCHASING')` or the Manager/Admin `can('approve','pr')`
  fallback can, via `purchasing.services.decide_purchase_request()`. `assigned_to` is notification/display
  only, never a decision right — mirrors how `WarehouseHandoff.assigned_to` (Phase D) is *informational*
  while `is_department_manager` still has oversight. Visibility (`purchasing.views.pr_list`/`pr_detail`,
  `purchasing.views._pr_can_view_all`) — a requester outside the Purchasing department only sees PRs they
  created themselves (`requested_by`); a **plain PURCHASING-role staff member (not a department manager) only
  sees PRs where they are `assigned_to`** — being in the PURCHASING role alone no longer grants full
  visibility (tightened same day as the forward feature below, since "see everything" defeated the point of
  routing specific PRs to specific staff); `is_department_manager('PURCHASING')` and Manager/Admin still see
  every PR (need the full picture to triage/decide/forward). `pr_detail` enforces the same scoping against
  direct URL access, not just the list filter. Kept separate from `Supplier.managed_by` (added same day): role
  PURCHASING can now create `Supplier` rows (previously Manager/Admin only) via
  `partners.views.can_create_supplier`; the row auto-gets `managed_by = creator`, and
  `partners.views.can_edit_supplier` limits a PURCHASING user to editing only the suppliers they created —
  Manager/Admin are unaffected (still edit any Supplier). Also `PurchaseOrder.po_no` is no longer typed in by
  hand: `PurchaseOrder.generate_po_no()` auto-assigns `PO-XXXX` (a global incrementing sequence, *not*
  month-scoped like `PR-YYYYMM-XXX` — POs don't reset per cycle) in `save()` when `po_no` is blank; the field
  is now `editable=False` and dropped from `PurchaseOrderForm` so the create/update form no longer exposes it.
- **PR forward-to-staff after approval, added same day as the visibility tightening above**: an APPROVED PR
  with no `linked_po` yet can be forwarded to a specific PURCHASING-department staff member via
  `purchasing.services.forward_purchase_request(pr, staff, actor, ip_address=None)` — it simply (re)assigns
  `PurchaseRequest.assigned_to` (the same field the original requester can optionally set at creation time;
  forwarding just overwrites it later) and `notify()`s that staff member, so no new field/model was needed.
  Gated to `purchasing.views.can_decide_pr` (department manager or Manager/Admin `approve` fallback) via the
  `pr_forward` view/URL — a plain PURCHASING staff member cannot forward to a colleague themselves, only the
  manager routes work. Once forwarded, the assignee's `assigned_to == them` is exactly what the tightened
  visibility rule above checks, so they immediately see the PR in their own `pr_list`/`pr_detail` and — since
  PURCHASING already has `create` on `po` — can create the PO themselves via the existing "Tạo PO từ yêu cầu
  này" button, no extra permission needed. Raises `ValidationError` if the PR isn't `APPROVED` yet or already
  has a `linked_po` (mirrors the guard conditions on the "Tạo PO" button itself).
  **Hardened 2026-07-27**: `purchasing.views.po_create`'s `?from_pr=<pk>` handling used to trust generic
  `create`-on-`po` permission alone — any MANAGER/PURCHASING/ADMIN user could convert *any* `APPROVED` PR
  into a PO by guessing its (sequential, guessable) pk directly, bypassing the forward/visibility
  restriction entirely, and could even convert an already-`linked_po` PR a second time since `PurchaseRequest.status`
  never leaves `APPROVED` after conversion (no dedicated `CONVERTED` state) and the old query only filtered
  on `status`. Fixed by mirroring `pr_detail`'s exact visibility check (`_pr_can_view_all(user) or
  requested_by_id == user.id or assigned_to_id == user.id`, else `PermissionDenied`) plus adding
  `linked_po__isnull=True` to the initial `get_object_or_404` filter, and — since that initial check still
  has a TOCTOU gap between two concurrent submits — re-validating `status`/`linked_po_id` a second time
  inside the POST transaction after `PurchaseRequest.objects.select_for_update()`, raising `ValidationError`
  (same "not APPROVED anymore" catch-and-`messages.error` pattern used by `po_approve`/`po_send`/`po_close`)
  if a concurrent request won the race. Applies the same pattern already used by
  `forward_purchase_request`/`decide_purchase_request` (`select_for_update` + re-check inside the atomic
  block) — any future PR/PO state-transition view taking a target pk from the querystring/POST body should
  follow this template rather than trusting a pre-transaction `get_object_or_404` alone.
- **PO list visibility scoping, added same day (2026-07-26), asymmetric with the PR rule above on purpose**:
  `PurchaseOrder` gained `created_by` (nullable FK to User, `SET_NULL`, set automatically in `po_create` —
  not an exposed form field). `purchasing.views._po_can_view_all(user)` restricts **only** a plain
  PURCHASING-role user who is *not* `is_department_manager('PURCHASING')` to their own `created_by` POs in
  `po_list`; every other case — STAFF/QC/ACCOUNTANT (need PO visibility to cross-check GRN/công nợ),
  MANAGER/ADMIN, and the PURCHASING department manager (oversight) — still sees every PO. This is
  deliberately **not** a mirror of `_pr_can_view_all`: PR visibility restricts everyone who isn't a
  manager/admin (every requester only sees their own), whereas PO read access (`ROLE_PERMISSIONS[...]['po']`)
  is already broadly granted to every role, and multiple people legitimately act on the *same* PO across its
  lifecycle (Manager approves, a different PURCHASING staff member may send it to the supplier) — narrowing
  that down to "creator only" would have broken cross-role collaboration. For the same reason,
  `purchasing.views.po_detail` (unlike `pr_detail`) applies **no** `created_by` check — direct/redirected
  access (after `po_approve`/`po_send`, or via the "PO liên kết" link from `pr_detail`/GRN) stays open to
  anyone with base `read` on `po`; only `po_list` (the browsing/queue view) is scoped down, so a PURCHASING
  staff member's list isn't cluttered with every colleague's PO but they can still open one directly when
  their job requires it (e.g. sending a PO a manager created).
- **Bug fix 2026-07-27: backfill migration for pre-existing `PENDING` PRs stranded by the Approval rewire
  above**. When `491a017` rewired `pr_approve`/`pr_reject` to require a PENDING `Approval` row
  (`latest_approval_for(obj)`), it shipped no data migration — any `PurchaseRequest` created and left
  `PENDING` *before* that commit had no `Approval` at all, so it stayed visibly "Chờ duyệt" in the UI but
  `pr_approve`/`pr_reject` would raise `ValidationError('Yêu cầu này không có phiếu duyệt nào đang chờ xử lý.')`
  forever — un-actionable by anyone, including Manager/Admin. Fixed by
  `purchasing/migrations/0008_backfill_pr_approval.py` (data migration, `RunPython`): for every
  `PurchaseRequest.status == PENDING` lacking an `Approval`, create one
  (`status=PENDING, department=PURCHASING, submitted_by=requested_by`), then `.update(submitted_at=...)` to
  the PR's own `created_at` (can't pass `submitted_at` at `.create()` time — it's `auto_now_add`). **General
  lesson for any future retrofit of the `Approval` pattern (§4 of the skill file) onto an existing flat-
  permission workflow**: introducing the `Approval` gate on an app that already has live in-flight rows in
  the pre-gate "pending" status always needs a paired backfill data migration in the same change — the
  schema migration that adds `Approval` (`accounts/migrations/0011_approval.py`) and a permission-reseed
  migration (`0012_reseed_purchasing_pr_permissions.py`) are not enough on their own to cover objects that
  predate the rewire.
- **Bug fix 2026-07-27: race condition in every auto-generated sequential number (`po_no`, `request_no`,
  `grn_no`, `gin_no`, `transfer_no`) fixed with retry-on-collision, not tighter locking**. All five
  `generate_*_no()` classmethods (`PurchaseOrder`/`PurchaseRequest` in `purchasing/models.py`,
  `Grn.generate_grn_no`, `Gin.generate_gin_no`, `StockTransfer.generate_transfer_no` in `inventory/models.py`)
  share the same shape: open their own `transaction.atomic()`, `select_for_update()` over rows matching the
  prefix, compute `max(existing) + 1`, then return — but that block (and its locks) closes *before* `save()`
  does the actual `INSERT`. `select_for_update()` can only lock rows that already exist; it can't lock against
  a sequence number that doesn't have a row yet, so two concurrent creates can compute the same next number
  and both attempt to insert it, raising `IntegrityError` on the `unique=True` constraint. Widening the
  `select_for_update()` scope does not fix this — it is the classic "MAX+1 under concurrency" problem, not a
  lock-granularity problem. Fixed identically in all five models' `save()`: skip regeneration if the number
  field is already set (plain update), otherwise loop up to 5 attempts — call `generate_*_no()`, attempt
  `super().save()` inside its own `transaction.atomic()` (a savepoint, so it doesn't poison an outer
  transaction if `save()` was itself invoked from inside one), and on `IntegrityError` clear the field and
  retry with a freshly generated number. The DB's `unique=True` constraint is the actual correctness
  guarantee now; `select_for_update()` in `generate_*_no()` is kept as-is since it still reduces (does not
  eliminate) collision likelihood under low concurrency, so it's not dead code, just no longer load-bearing.
  **Apply the same retry-on-`IntegrityError` pattern to any future `generate_*_no()`-style sequential field**
  instead of trying to make the locking airtight — a real DB sequence (`nextval`) or a dedicated counter row
  would also work but weren't introduced here to avoid a new migration/model for a problem the retry loop
  already solves at the `save()` layer.
- **Bug fix 2026-07-27: `seed_demo_data --flush` was wiping the entire `Notification`/`Approval`/`AuditLog`
  tables, not just demo-related rows**. `accounts/management/commands/seed_demo_data.py::_flush_demo_data()`
  deletes demo `Warehouse`/`Supplier`/`Product`/`Grn`/`Gin`/etc. (identified by their `DEMO-` prefix), then
  needs to clean up the `Notification`/`Approval`/`AuditLog` entries that pointed at those now-deleted objects
  via `GenericForeignKey` (`target_type` + `target_id` — these don't cascade automatically when the target
  row is deleted). The old code assumed a GenericFK "can't be filtered by prefix" and called
  `Notification.objects.all().delete()` / `Approval.objects.all().delete()` / `AuditLog.objects.all().delete()`
  unconditionally whenever any demo data existed — correct only if demo data is the *only* data in the DB;
  on any environment that mixes seeded demo data with real usage (real GRN/GIN/PR approvals, real audit
  trail), running `--flush` silently destroyed all of it. Fixed by collecting `(ContentType, pk)` for every
  demo object **before** deleting it (`Warehouse`, `Location`, `Product`, `Supplier`, `PurchaseOrder`,
  `PurchaseRequest`, `Grn`, `GrnReturn`, `Gin`, `StocktakeSession`, `WarehouseHandoff`, `StockTransfer`,
  `QcCriteria` — every model actually used as a `target=` argument to `log_action()`/`notify()`/
  `create_approval()` anywhere in the codebase, cross-checked via grep), building one `Q(target_type=ct,
  target_id__in=[...])` clause per model, OR-ing them together, and using that combined `Q` to scope the three
  `.delete()` calls instead of `.objects.all()`. **General lesson**: a `GenericForeignKey` target *can* be
  filtered to a subset — grab the `ContentType` + id list of the objects you're about to delete first (before
  they're gone), then filter the log/notification table by that; never fall back to "can't scope it, wipe the
  whole table" for a table that a real environment might also be writing to.
- **Bug fix 2026-07-27: ADMIN saw an empty "Phiếu chờ nhận hàng" queue and got 403 on Nhận/Từ chối, despite
  the sidebar showing the link to them**. `accounts/templates/base.html` gates the sidebar entry on
  `user.is_superuser or user.role == 'ADMIN' or user.department == 'WAREHOUSE'`, but
  `inventory.views.can_decide_handoff()` only had the department-based three-tier resolution described above
  (`is_department_manager('WAREHOUSE')` / exact `assigned_to` / `destination_warehouse.staff` /
  `department=WAREHOUSE` fallback) — an ADMIN user (who conventionally has `department` left blank, per
  `User.department`'s own help text: "Bỏ trống cho Admin") matched none of those branches, so
  `handoff_list` filtered every pending handoff out of their view and `handoff_accept`/`handoff_reject` raised
  `PermissionDenied`. Unlike GRN/GIN/PR (`is_department_manager(dept) or user.can('approve', <module>)`),
  handoff has no dedicated module in `accounts/permissions.py` `MODULES` to hang a `user.can('approve', ...)`
  fallback off, so the fix instead adds an explicit `user.is_superuser or user.role == User.Role.ADMIN` tier
  (same pattern already used for oversight checks in `accounts/views.py`, `catalog/views.py`,
  `partners/views.py`, `warehouse/views.py`) — first in `can_decide_handoff()` (covers `handoff_accept`/
  `handoff_reject`, both of which already delegated to it) and mirrored in `handoff_list()`'s "sees everything"
  branch instead of falling through to the per-item `can_decide_handoff` filter for admins. **General lesson**:
  whenever a sidebar/nav visibility check and the view/permission check it links to are written as two
  separate conditions (as here, and as the Phase A/B/C/D approval-gate work generally does), a role added to
  one but not the other is a standing trap — new oversight roles (ADMIN, superuser) need to be added to both,
  and it's worth grepping the target view's permission helper whenever a sidebar condition is touched.
- **Bug fix 2026-07-27: PO rows with `created_by=NULL` were permanently invisible to every plain PURCHASING
  staff member's `po_list`**. `purchasing/migrations/0007_purchaseorder_created_by.py` only did `AddField`
  with no accompanying data migration, so every `PurchaseOrder` created before that migration landed with
  `created_by=NULL` forever. `purchasing.views.po_list`'s visibility filter
  (`orders.filter(created_by=request.user)`, gated behind `_po_can_view_all`) doesn't match `NULL` in SQL, so
  those rows vanished from the list for anyone without the "see all" tier — including whoever actually
  created them — with no way to get them back short of a Manager/Admin or the PURCHASING department manager
  looking them up directly. Fixed two ways together: (1) `purchasing/migrations/0009_backfill_po_created_by.py`
  (data migration) recovers the real creator where possible — for each `created_by__isnull=True` PO, find the
  earliest `AuditLog(action='CREATE')` row targeting it via `GenericForeignKey` and copy that log's `actor` back
  onto `created_by`; POs with no such log (e.g. rows inserted directly via `seed_demo_data`'s ORM calls, never
  through the `po_create` view) are left `NULL` since guessing a creator would be worse than leaving it unknown.
  (2) `purchasing.views.po_list`'s filter was widened to
  `Q(created_by=request.user) | Q(created_by__isnull=True)` — a still-`NULL` PO now shows to every plain
  PURCHASING user rather than being hidden from all of them, same "don't guess, but don't hide it forever
  either" reasoning already used for `PurchaseRequest` backfill in `0008_backfill_pr_approval.py`. **General
  lesson**: adding a nullable `created_by`/`assigned_to`-style FK to gate visibility on an existing table with
  live rows needs a paired backfill migration in the same change (recover what's recoverable via `AuditLog`),
  and the filter itself should treat "unknown owner" as visible-to-all rather than invisible-to-all — the same
  class of gap as the `Approval`-retrofit lesson two bullets up, just for a plain nullable FK instead of a new
  gating model.
- **Bug fix 2026-07-27: `cancel_grn` allowed cancelling a GRN mid-`QC_IN_PROGRESS` without reversing the
  staging Batch/Inventory that `start_qc` had already created**. `receiving.services.cancel_grn`
  (`receiving/services.py`) blocks cancellation only for `RECEIVED`/`REJECTED`/`CANCELLED`/`CLOSED` — every
  other status, including `QC_IN_PROGRESS`, was cancellable. But by the time a GRN reaches `QC_IN_PROGRESS`,
  `quality.services.start_qc` has already created a real `Batch` (`ACTIVE`) per item in the `STAGING`
  warehouse and credited `Inventory.qty_on_hand` there (Phase-D "Kho chờ" design — goods are physically
  present from the moment Qty thực nhận is confirmed, not just recorded on paper). `cancel_grn` only flipped
  `grn.status` to `CANCELLED` and cleaned up any pending `Approval` — it never touched that staging
  Batch/Inventory, so the goods stayed permanently "stuck" in STAGING stock with no GRN referencing them
  anymore. The existing test (`TC_GRN_VIEW_007_004`) didn't catch this because it constructed the GRN with
  `status=QC_IN_PROGRESS` set directly on the model rather than driving it through the real
  `grn_receive_qty` → `start_qc` flow, so no Batch/Inventory ever existed for it to check. Fixed by adding
  `quality.services.cancel_qc_inspection(grn, actor=None, ip_address=None)` — finds the GRN's `PENDING_QC`
  `QcInspection`, closes every staging `Batch` tied to it (`qty_used = qty_received`, `status = CLOSED`),
  decrements `Inventory.qty_on_hand` at the staging warehouse by each batch's `qty_available` with a
  `StockMovement` `ADJUSTMENT` entry, and marks the inspection `CANCELLED` (new
  `QcInspection.Result.CANCELLED` choice, migration `quality/migrations/0006_alter_qcinspection_status.py`).
  `receiving.services.cancel_grn` calls this (local import, to avoid a module-level `receiving`↔`quality`
  cycle) when `grn.status == QC_IN_PROGRESS`, before flipping the GRN itself to `CANCELLED`. This is
  deliberately **not** the same boundary as the QC-override rule ("annotation only, never reverse a completed
  transaction automatically" — see `QcInspection.override_note` and the Phase-D `reject_handoff(...,
  BACK_TO_QC)` branch): those apply *after* QC has rendered a PASS/FAIL/PARTIAL_PASS decision, which is a
  completed business transaction. A staging batch under `QC_IN_PROGRESS` has no decision yet — it is
  provisional, created solely so goods aren't invisible between receipt and QC, so reversing it on cancel is
  undoing an in-flight step, not rewriting history. **General lesson**: whenever a `cancel_x`/`reject_x`
  function is allowed at an intermediate status, check whether *any* side effect (Batch, Inventory, a row in
  another app) was already created to reach that status — a cancel that only flips the owning object's status
  back is incomplete if a side effect from getting *into* that status was never undone. Tests that assert a
  cancel/reject transition by constructing the object directly at the target status (`Model(status=X)`)
  instead of driving it through the real service call that produces that status will not catch this class of
  bug — this one specifically needs a regression test that goes through the real view/service flow, see
  `test_TC_GRN_VIEW_007_005_cancel_during_qc_in_progress_reverses_staging_batch` in `receiving/tests.py`.
- **Bug fix 2026-07-27: `PARTIAL_USED` batches were permanently excluded from FIFO once a GIN issued part of
  them, stranding the remaining stock forever**. `shipping.services.issue_gin` sets
  `batch.status = Batch.Status.PARTIAL_USED` (not `ACTIVE`) whenever a batch still has `qty_available > 0`
  after an issue (BR-GIN-006). But `inventory.services.suggest_fifo_batches` — the query FIFO/GIN actually
  runs — filtered `status=Batch.Status.ACTIVE` only, with no `PARTIAL_USED` branch, so a batch that had ever
  been partially issued could never be selected by FIFO again even though it still had real stock:
  `Inventory.qty_on_hand` kept the qty, but no GIN could ever draw it down through the normal flow, only via
  manual override — and even `shipping.services.override_allocation`/`shipping.forms.GinAllocationOverrideForm`
  had the identical `status=ACTIVE`-only restriction, so there was no way to select a `PARTIAL_USED` batch at
  all short of an admin DB edit. `inventory.services.sync_expired_batches`/`expiring_soon_batches` had the same
  narrow `status=ACTIVE` filter, so a `PARTIAL_USED` batch whose `exp_date` had passed never flipped to
  `EXPIRED` and never showed up in the "sắp hết hạn" dashboard warning either — expired stock with leftover
  qty sat invisibly forever instead of being correctly excluded. Fixed by widening all four call sites to
  `status__in=[Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED]` (`inventory/services.py`:
  `sync_expired_batches`, `expiring_soon_batches`, `suggest_fifo_batches`; `shipping/services.py`:
  `override_allocation`; `shipping/forms.py`: `GinAllocationOverrideForm`) — `QUARANTINE`/`EXPIRED`/
  `PENDING_RECEIPT` are still excluded everywhere, unchanged. `inventory.forms.StockTransferForm` already had
  the correct `status__in=[ACTIVE, PARTIAL_USED]` filter before this fix (manual stock-transfer form was never
  broken) — that inconsistency between it and the FIFO/override code paths is what made the bug detectable by
  inspection alone, without even needing to reproduce it. **General lesson**: any place that filters
  `Batch.objects` by `status` for "is this batch usable" should default to
  `status__in=[ACTIVE, PARTIAL_USED]`, not `status=ACTIVE` — `PARTIAL_USED` is not a terminal/excluded state
  like `QUARANTINE`/`EXPIRED`/`CLOSED`, it is a normal mid-life state of an otherwise-active batch, and a
  single-status filter is fine only where the whole point is to exclude everything but a genuinely fresh batch
  (there is no such case in this codebase today).
- **Bug fix 2026-07-27: three unrelated gaps found in manual review, all the same shape — a service silently
  relied on a form's queryset filter instead of re-checking the constraint itself**:
  1. `inventory.forms.StockTransferForm`'s batch queryset only allowed `ACTIVE`/`PARTIAL_USED`, excluding
     `PENDING_RECEIPT` — so the manual-transfer path this doc already promises for a batch left
     `PENDING_RECEIPT` after `reject_handoff(..., BACK_TO_QC)` (see the Phase-D bullet above) was unreachable
     from the UI even though `transfer_stock`/`move_batch_qty`'s status guard already accepted
     `PENDING_RECEIPT` as a source. Fixed by widening the form's `status__in` to include `PENDING_RECEIPT`;
     `transfer_stock`'s own guards (blocks a STAGING source, blocks `PENDING_RECEIPT` with a still-`PENDING`
     handoff) remain the real gatekeeper, unchanged.
  2. `quality.services.qc_partial_pass` accepted an `item_results` where every item's `qty_pass` was `0` —
     functionally identical to `qc_fail` (every item ends `REJECTED`, all qty moved to `QUARANTINE`) but the
     GRN was left `RECEIVED` with no `GrnReturn` created, unlike `qc_fail`'s `REJECTED` + `GrnReturn`. Fixed
     by raising `ValidationError` up front when `sum(item_results.values()) == 0`, forcing the caller to use
     the `fail` action instead of disguising a full fail as a partial pass — a deliberate choice over
     auto-converting to the `qc_fail` ending, to keep "which action produced this outcome" unambiguous.
  3. `shipping.services.override_allocation` validated `new_batch` against product/status/qty but never
     checked `new_batch.location.warehouse_id == gin.warehouse_id`. `GinAllocationOverrideForm` already
     filters its queryset by `location__warehouse=gin.warehouse`, so the UI path (`shipping.views.
     gin_allocation_override`) was safe, but the service itself — callable directly, same "form only collects
     input, service re-validates" boundary both files' own docstrings already claimed — could be handed a
     batch from a different warehouse, deducting `Inventory` at the GIN's warehouse while actually consuming
     stock physically sitting in another one. Fixed by adding the same warehouse check alongside the existing
     product/status/qty checks.
  **General lesson**: when adding a new form-filtered `ModelChoiceField` (or reviewing one that already
  exists), check that the paired service function actually re-asserts *every* constraint the queryset
  encodes — a docstring claiming "service re-validates, doesn't fully trust the form" is not proof it does;
  grep the service body for each filter clause in the form's queryset and confirm a matching check exists.
- **Bug fix 2026-07-27: two Stock Opname (stocktake) gaps, both "Inventory updated but Batch left behind"**:
  1. `stocktake.services.apply_adjustment` (FR-SO-05) only adjusted `Inventory.qty_on_hand` +
     `StockMovement` — it never touched `Batch`, so after an adjustment `Inventory`/KPI and FIFO/`Batch`
     disagreed: a surplus increased `Inventory.qty_on_hand` but created no batch, so FIFO/GIN had no lot to
     issue the extra qty from; a shortage decreased `Inventory.qty_on_hand` but left every existing batch's
     `qty_available` unchanged, so FIFO/GIN could still over-issue qty that Inventory no longer actually had.
     Fixed by syncing `Batch` in the same transaction, same direction as the variance: surplus (`variance >
     0`) creates one new `ACTIVE` batch (`_create_surplus_batch`) at `session.location` (FR-SO-07) or the
     warehouse's default location if kiểm toàn kho; shortage (`variance < 0`) consumes existing `ACTIVE`/
     `PARTIAL_USED` batches FIFO-order (`_consume_shortage_batches`, same `exp_date`/`created_at` ordering as
     `suggest_fifo_batches`), closing/partial-using them exactly like `shipping.services.issue_gin` does. A
     surplus batch has no GRN to inherit a supplier from — there is no such thing as "NCC of stock found lying
     around" — so `_infer_supplier` best-effort reuses the most recent batch's supplier for that same SKU
     (any warehouse); a SKU with **no** batch history at all (only possible with hand-built data, e.g. an
     `Inventory` row created directly without ever going through GRN/QC) raises `ValidationError` telling the
     approver to create a GRN/Batch manually first rather than guessing a supplier. Symmetrically, a shortage
     that exceeds the qty actually sitting in `ACTIVE`/`PARTIAL_USED` batches also raises `ValidationError`
     instead of silently letting `Inventory` drift below the sum of `Batch.qty_available` — this doubles as a
     data-integrity check (in normal operation `Inventory` is always batch-backed, so this should never fire).
  2. `stocktake.services.create_session` (FR-SO-07): when a session is scoped to one `location`, it correctly
     filtered which SKUs to list (via `Batch.location`) but still snapshotted `qty_system` from
     `Inventory.qty_on_hand` — the **whole warehouse's** qty, not the counted location's. A warehouse with
     location A=60/B=40 (`Inventory.qty_on_hand`=100) and a session scoped to A: staff counts A correctly
     (60), but `variance = 60 - 100 = -40` — a phantom 40-unit shortage for stock that was never even in scope
     to be counted, and `apply_adjustment` would then wrongly deduct 40 from the whole-warehouse `Inventory`.
     Fixed by computing `qty_system` from `Sum(Batch.qty_received - Batch.qty_used)` grouped by product **at
     that location** instead of `Inventory.qty_on_hand`, whenever `location` is given; a session with no
     `location` (kiểm toàn kho) is unaffected — `Inventory.qty_on_hand` is still correct and simpler for that
     case, since there's no location to disagree with.
  **General lesson**: this is the same class of bug as the `PARTIAL_USED`-excluded-from-FIFO fix above —
  `Batch` and `Inventory` are two views of the same physical stock that must be kept in lockstep by every
  write path, not just the GRN/QC/GIN paths that existed when that invariant was first established. Any new
  code that mutates `Inventory.qty_on_hand` directly (as opposed to going through `move_batch_qty`/
  `issue_gin`-style helpers) needs to ask whether it also owes `Batch` a matching update — grep for direct
  `Inventory.objects...qty_on_hand` mutations when auditing a new module for this pattern. Tests:
  `TC-SO-05-009` through `013` (`stocktake/tests.py::ApplyAdjustmentServiceTest`) and `TC-SO-07-003` through
  `005` (`CreateSessionServiceTest`).
- **Bug fix 2026-07-27: `dashboard_kpis()`'s `near_expiry_count` leaked Kho chờ/Kho phế batches into the
  "sắp hết hạn" KPI, breaking the very invariant this file already documents for that same function**. The
  M6 filtering rule ("reports.services... filter `warehouse__warehouse_type=MAIN` so STAGING/SCRAP stock
  never inflates those numbers," see the Phase-D bullet above) was correctly applied to `total_inventory_value`
  and `low_stock_count` inside `dashboard_kpis()` — but `near_expiry_count` called
  `inventory.services.expiring_soon_batches(days=30)` with no warehouse filter at all. A batch sitting in a
  `STAGING` warehouse is `ACTIVE` while it waits for a QC decision (Phase D: "goods aren't invisible between
  receipt and QC"), so it matches `expiring_soon_batches`'s `status__in=[ACTIVE, PARTIAL_USED]` filter just as
  readily as real MAIN-warehouse stock — a batch awaiting QC with a near expiry date inflated the KPI even
  though it isn't usable/sellable inventory yet. `reports.tests.DashboardKpisTest.test_TC_RPT_01_007_
  excludes_staging_and_scrap_inventory` covers this exact M6 rule for the other two KPIs in the same function
  but had no assertion for `near_expiry_count`, so the gap went unnoticed. Fixed by adding an optional
  `warehouse_type` kwarg to `inventory.services.expiring_soon_batches(days=30, warehouse=None,
  warehouse_type=None)` — filters `location__warehouse__warehouse_type=warehouse_type` when given, alongside
  (not instead of) the existing single-`warehouse` filter — and calling it from `dashboard_kpis()` as
  `expiring_soon_batches(days=30, warehouse_type=Warehouse.WarehouseType.MAIN)`. `inventory.views.batch_list`/
  `batch_detail` (FR-INV-01/02, "quản lý lô hàng") deliberately keep calling `expiring_soon_batches()` with no
  `warehouse_type` — that page is a company-wide batch register and must still flag a near-expiry batch sitting
  in Kho chờ/Kho phế so someone acts on it; only the MAIN-only *KPI* needed the extra filter, not the function's
  default behavior. Test: `TC-RPT-01-008` (`reports/tests.py::DashboardKpisTest`).
  **General lesson**: when a function in this codebase documents/enforces a "filter to MAIN only" invariant for
  a *set* of KPIs computed together (as `dashboard_kpis()`'s docstring/tests already claimed), a new metric
  added to that same set inherits the invariant by convention, not by the language — it has to be filtered
  explicitly, and a test asserting the invariant for the *other* metrics in the function is not evidence it
  holds for a metric added later. When auditing a function like this, check every `return`/dict-key line against
  the filter the function's own docstring or existing sibling tests claim to enforce, not just the lines a
  recent diff touched.
- **Bug fix 2026-07-27: `accounts.views.user_update` let an ADMIN deactivate their own account through the
  general edit form, bypassing the self-lock guard that already existed on the quick-toggle button**.
  `user_toggle_active` explicitly blocks `obj.pk == request.user.pk` ("tự khoá sẽ tự đăng xuất khỏi hệ thống")
  before flipping `is_active`, but `UserUpdateForm` (used by `user_update`) also exposes `is_active` as a
  plain checkbox with no equivalent check — unchecking it while editing your own account (or simply POSTing
  the form with the checkbox omitted, since an unchecked HTML checkbox submits nothing) deactivated the admin
  immediately, with no other admin left to undo it since the account it just logged out was the one editing
  it. Fixed by adding the same guard inline in `user_update`: after `form.is_valid()`, if
  `obj.pk == request.user.pk and not form.cleaned_data['is_active']`, show the identical error message
  ('Không thể tự khoá tài khoản của chính bạn.') and re-render the form without saving, instead of duplicating
  the check into the form class itself (the form has no access to `request.user`, and this view is the only
  caller). Test: `TC-USER-01-009` (`accounts/tests.py::UserCrudTest`). **General lesson**: when a quick-action
  view (toggle/delete button) and a general edit form both expose the same mutable field, a guard added to one
  doesn't automatically cover the other — grep for every view that can reach a guarded field's `save()` path,
  not just the view the original bug report was filed against.
- **Bug fix 2026-07-27: `StocktakeSessionForm`'s `location` field let a user pick a location belonging to a
  different warehouse than the one selected in the same form**. `stocktake.forms.StocktakeSessionForm.location`
  queried `Location.objects.filter(is_active=True)` with no scoping to the chosen `warehouse` — this project has
  no HTMX/JS cascading-dropdown wiring anywhere yet to filter it client-side (checked: no `hx-get`/`hx-trigger`
  usage exists in the codebase), so the raw `<select>` listed every active location company-wide regardless of
  warehouse. Picking kho A + a location that actually belongs to kho B fed
  `stocktake.services.create_session()` a mismatched pair: it intersects "products with `Inventory` at
  `warehouse` A" against "products with a `Batch` at `location`" (which is physically in warehouse B) — either
  an empty SKU list (no overlap) or, worse, a wrong `qty_system` silently sourced from warehouse B's batch qty
  for a session nominally scoped to warehouse A. Fixed two ways together, mirroring the existing
  `StockTransferForm`/`GinAllocationOverrideForm` "form collects, service re-validates" boundary (see the
  "three unrelated gaps" bug-fix bullet above): (1) `StocktakeSessionForm.clean()` now rejects the combination
  via `location.warehouse_id != warehouse.id` (`add_error` on `location`); (2)
  `stocktake.services.create_session()` re-checks the identical constraint itself and raises `ValidationError`
  — the service doesn't just trust the form, same reasoning as every other guard listed in that bullet. No
  dynamic dropdown filtering was added (would be a UI feature beyond this bug's scope) — the fix only rejects
  the invalid combination instead of preventing it from being selectable. Test:
  `test_TC_SO_07_006_rejects_location_from_different_warehouse`
  (`stocktake/tests.py::CreateSessionServiceTest`). **General lesson**: any form with two `ModelChoiceField`s
  where one is conceptually scoped inside the other (a location within a warehouse, a sub-resource within a
  parent) needs either client-side cascading filtering or a `clean()` cross-field check — an unscoped queryset
  alone will silently accept mismatched combinations, and per the pattern already established in this file, the
  service layer must re-check it too, not just the form.
- **Bug fix 2026-07-27: `StocktakeSession.save()` had no retry-on-collision for `so_no`, unlike every other
  auto-generated sequential number in the codebase**. The "MAX+1 under concurrency" race condition already
  documented above for `po_no`/`request_no`/`grn_no`/`gin_no`/`transfer_no` — `select_for_update()` inside
  `generate_*_no()` can only lock rows that already exist, so it can't prevent two concurrent creates from
  computing the same next number before either has actually `INSERT`ed — was fixed for all five of those
  models but `stocktake.models.StocktakeSession.generate_so_no()`/`save()` was never given the same treatment,
  so two stocktake sessions created concurrently could still collide on `so_no` and raise an unhandled
  `IntegrityError`. Fixed identically to `StockTransfer.save()` (`inventory/models.py`): `save()` skips
  regeneration if `so_no` is already set (plain update), otherwise loops up to 5 attempts — calls
  `generate_so_no()`, attempts `super().save()` inside its own `transaction.atomic()` savepoint, and on
  `IntegrityError` clears `so_no` and retries with a freshly generated number. Test:
  `test_TC_SO_MODEL_004_so_no_retries_on_integrity_error_collision`
  (`stocktake/tests.py::StocktakeSessionModelTest`) — mocks `generate_so_no()` with `side_effect` to return a
  colliding number on the first call and a fresh one on the second, since reproducing the race for real would
  need actual concurrent threads/connections. **General lesson**: when the retry-on-`IntegrityError` pattern
  was introduced (see the earlier bullet), it should have been swept across every `generate_*_no()`-style
  field in the codebase at once — `StocktakeSession.so_no` was added before that sweep and got missed; any
  future audit for this pattern should grep for every model with a `unique=True` auto-generated code field,
  not just the ones the original bug report happened to name.
- **Bug fix 2026-07-27: a soft-deleted user still showed an "Edit" button and could have `is_active` bounced
  back to `True` while `is_deleted` stayed `True`, letting a "deleted" account log back in**.
  `accounts.templates.accounts.user_detail.html`/`user_list.html` rendered the "Sửa" link unconditionally
  (no `is_deleted` guard, unlike the adjacent "Đặt mật khẩu"/"Khoá"/"Xoá" actions which already check
  `not obj.is_deleted`), and `accounts.views.user_update` had no server-side guard either — `UserUpdateForm`
  exposes `is_active` as a plain checkbox, so an admin (or a direct POST to the URL, bypassing the hidden
  button entirely) could re-check it on a soft-deleted user. `accounts.backends.DirectPermissionsBackend`
  extends `ModelBackend` with no override of `user_can_authenticate` (only `get_group_permissions`, see its
  own docstring), so Django's default login check only looks at `is_active` — it has no idea `is_deleted`
  exists, meaning any path that leaves `is_active=True` on a `is_deleted=True` row makes that account log in
  again. Fixed at three layers: (1) `User.save()` (`accounts/models.py`) now enforces the invariant
  `is_deleted=True ⇒ is_active=False` unconditionally, before every `super().save()` — this is the actual
  fix, since it closes the hole for **every** write path (`UserUpdateForm`, Django admin's own `is_active`
  field, which sits in a separate fieldset from `is_deleted` per `accounts/admin.py`, or any future direct
  ORM `.save()`), not just the one view the bug was originally filed against; (2) `accounts.views.user_update`
  now rejects editing a soft-deleted user outright (`messages.error('User đã bị xoá, không thể sửa.')` +
  redirect to `user_detail`), mirroring the exact guard shape already used by `user_password_set`/
  `user_toggle_active` for the same "no restore flow exists yet" reason — a deleted user is treated as frozen,
  not partially editable; (3) `user_detail.html`/`user_list.html` now wrap the "Sửa" link in
  `{% if not obj.is_deleted %}` (matching the pattern the other action buttons already followed) so the
  now-guaranteed-403-via-redirect action isn't offered as a live button in the first place. Tests:
  `test_TC_USER_01_010_cannot_update_deleted_user` (view guard) and
  `test_TC_USER_01_011_save_invariant_forces_inactive_when_deleted` (model invariant, sets `is_active=True`
  directly on a soft-deleted instance and asserts `save()` forces it back to `False`) in
  `accounts/tests.py::UserCrudTest`. **General lesson**: when a soft-delete flag and a separate "active" flag
  both exist on the same model, the flag combination that must never occur (`is_deleted ∧ is_active`) should
  be enforced in `save()` itself, not only in the one view/form that was known to violate it — a view-level
  guard alone leaves every other write path (admin panel, management command, future view) free to
  reintroduce the same inconsistency; the model invariant is what makes a plain `is_active`-only login check
  (the Django default) safe to leave unmodified.
- **Bug fix 2026-07-27: `dashboard_kpis()`'s `low_stock_count` compared `min_level` against each per-warehouse
  `Inventory` row individually instead of the SKU's combined stock across MAIN warehouses**.
  `reports.services.dashboard_kpis()` looped `Inventory.objects.filter(warehouse__warehouse_type=MAIN)` and
  incremented `low_stock_count` once per row where `inv.qty_on_hand < inv.product.min_level` — but `min_level`
  is a per-SKU setting (`Product.min_level`), not a per-warehouse one, so a SKU stocked in 2 MAIN warehouses
  was checked twice against the same threshold: individually-low rows summing to a healthy combined total
  still (wrongly) counted as low-stock 1-2 times, and there was no way for a SKU to be correctly judged
  "sufficient in aggregate but thin per-location." Fixed by aggregating `qty_on_hand` per `product_id` in a
  dict while iterating the same queryset (no second DB round-trip — `total_inventory_value`'s per-row loop
  was kept as-is and simply extended to also accumulate into `qty_by_product`/`products_by_id`), then counting
  low-stock once per SKU by comparing the aggregated total against `min_level` after the loop. Tests:
  `test_TC_RPT_01_009_low_stock_not_double_counted_across_warehouses` (same SKU under-min in 2 warehouses,
  combined still under min → counts once, not twice) and
  `test_TC_RPT_01_010_low_stock_uses_combined_qty_not_per_warehouse` (same SKU under-min in each of 2
  warehouses individually, but combined total meets `min_level` → not counted at all) in
  `reports/tests.py::DashboardKpisTest`. **General lesson**: any KPI/threshold defined per-SKU (`min_level`,
  `max_level`, reorder point, etc.) must be compared against a SKU's *aggregated* quantity across whatever
  scope the KPI claims to cover (here: all MAIN warehouses) — iterating per-row and checking the threshold on
  each row independently silently turns a per-SKU rule into a per-(SKU, warehouse) rule, which both
  double-counts split-stock SKUs that are actually fine and can also miss the reverse case a reviewer might
  expect (a SKU thin everywhere still only counts once, which is correct — the bug was always in the
  direction of over-counting/wrongly-flagging, not under-flagging).
- **Bug fix 2026-07-27: `dashboard_kpis()`'s `pending_grn_count` only counted `Grn.Status.PENDING_QC`,
  undercounting the "GRN chờ" KPI**. `Grn.Status` (`receiving/models.py`) has three distinct in-flight,
  not-yet-`RECEIVED` states — `PENDING_APPROVAL` (Phase B: staff submitted, waiting on a WAREHOUSE department
  manager's `Approval` decision), `PENDING_QC` (approved, waiting for QC to start), and `QC_IN_PROGRESS`
  (QC actively inspecting, staging `Batch`/`Inventory` already created per the Phase-D "Kho chờ" design) —
  but `dashboard_kpis()` only queried `status=PENDING_QC`, so any GRN sitting in `PENDING_APPROVAL` or
  `QC_IN_PROGRESS` at the moment the dashboard loaded was invisible to the "GRN chờ xử lý" KPI even though
  it is, by every other part of this codebase's own vocabulary (`Grn.current_department`, the cancel-
  permission logic), still a pending ticket someone needs to act on. Fixed by widening the filter to
  `status__in=[PENDING_APPROVAL, PENDING_QC, QC_IN_PROGRESS]` — `RECEIVED`/`REJECTED`/`CANCELLED`/`CLOSED`
  remain excluded, unchanged (all four are terminal, nothing left to act on). Tests:
  `test_TC_RPT_01_011_pending_grn_count_includes_pending_approval_and_qc_in_progress` (asserts all three
  statuses are counted together) and `test_TC_RPT_01_012_pending_grn_count_excludes_received_and_cancelled`
  (asserts `RECEIVED`/`CANCELLED` are still excluded) in `reports/tests.py::DashboardKpisTest`. **General
  lesson**: a KPI/count named after a business concept ("pending", "in progress", "open") that maps to a
  `TextChoices` enum with more than two values needs to enumerate every status that concept actually covers,
  not just the one status that happened to exist when the KPI was first written — `Grn.Status` gained
  `PENDING_APPROVAL` in Phase B, after `dashboard_kpis()`'s original `pending_grn_count` line was written for
  the simpler pre-Phase-B status set, and nothing forced that call site to be revisited when the enum grew.
  When adding a new intermediate status to an existing workflow enum, grep every `status=`/`status__in=`
  call site that already filters that model for the concept the new status also belongs to.
- **Bug fix 2026-07-27: `reports.views.slow_moving_view`'s `?days=` query param crashed with an unhandled
  `ValueError` (HTTP 500) for any non-numeric value**. `days = int(request.GET.get('days', 180))` only
  handles the *missing* case (the `180` default) — a query string like `?days=abc` still reaches `int(...)`
  and raises. Fixed by wrapping the parse in `try/except (TypeError, ValueError)`, falling back to the same
  `180` default on failure, mirroring how every other user-controlled numeric/enum input in this codebase is
  validated rather than trusted raw. Tests: `test_TC_RPT_05_007_slow_moving_invalid_days_falls_back_to_default`
  (`?days=abc` → 200, `response.context['days'] == 180`) and
  `test_TC_RPT_05_008_slow_moving_valid_days_still_respected` (`?days=90` → 200, `days == 90`, proving the
  fix doesn't clobber a legitimately-provided value) in `reports/tests.py::ReportsPermissionAndExportTest`.
  **General lesson**: any `int(request.GET.get(...))`/`request.GET[...]`-style read of a query/form param
  that isn't run through a Django `Form`/`ModelForm` (which validates for free) needs its own explicit
  try/except or a safe-parse helper — Django never validates raw `request.GET` access, so a bare `int()` call
  on it is a standing crash-on-bad-input bug, not just a style nitpick.
- **Bug fix 2026-07-27 (second pass): four more gaps found via a second manual audit of the same "form
  queryset vs. service re-validation" and "singleton/last-active" classes of bug already documented above**:
  1. `warehouse.views.warehouse_activate` set `is_active=True` directly with no check at all — STAGING/SCRAP
     have a DB-level `unique_active_staging_scrap_warehouse` constraint (max 1 active/type, see the Phase-A
     `Warehouse` docstring), so re-activating an old STAGING/SCRAP warehouse while another of the same type
     was already active raised an unhandled `IntegrityError` (500) instead of a friendly message — the
     mirror-image gap of `warehouse.services.deactivate_warehouse` (BR-WM-006), which already existed and
     was tested, but had no `activate_warehouse` counterpart. Fixed by adding
     `warehouse.services.activate_warehouse(warehouse, actor=None, ip_address=None)` — checks for another
     active warehouse of the same STAGING/SCRAP type first and raises `ValidationError` (caught by the view
     and shown via `messages.error`, same pattern as `warehouse_deactivate`) before setting `is_active=True`.
     Tests: `test_activate_second_staging_rejected_by_service`/`_by_view_no_500`/
     `test_activate_ok_when_no_other_active_of_same_type` in `warehouse/tests.py::WarehouseTypeSingletonTest`.
  2. `inventory.services.transfer_stock` blocked STAGING as a **source** (hàng ở Kho chờ phải qua QC) but had
     no equivalent check on the **destination** — a manual transfer could move a batch into a STAGING or
     SCRAP location, landing it there with `status=ACTIVE` (since `transfer_stock` always sets
     `new_status=Batch.Status.ACTIVE`), which violates the core invariant this file documents repeatedly:
     "hàng ở STAGING/SCRAP luôn phải qua QC quyết định trước" — STAGING is only meant to be populated by
     `start_qc()`, and SCRAP only by `qc_fail`/`qc_partial_pass`/`reject_handoff(..., TO_SCRAP)`. Fixed by
     adding a symmetric check — `to_location.warehouse.warehouse_type != Warehouse.WarehouseType.MAIN` raises
     `ValidationError` — right after the existing source-STAGING check in `transfer_stock`, and narrowing
     `inventory.forms.StockTransferForm.to_location`'s queryset to
     `warehouse__warehouse_type=Warehouse.WarehouseType.MAIN` (same "form filters, service re-validates"
     pairing used everywhere else in this doc). Tests:
     `test_TC_INV_TRF_016_staging_destination_rejected`/`test_TC_INV_TRF_017_scrap_destination_rejected` in
     `inventory/tests.py::StockTransferServiceTest`.
  3. `PurchaseOrderForm.supplier`, `PurchaseOrderItemForm`/`PurchaseRequestItemForm`/`GrnItemForm`/
     `GinItemForm`'s `product` fields all used the Django-default unfiltered queryset (`fields = [...]` with
     no `__init__` override), so a `Supplier` with `status` `INACTIVE`/`SUSPENDED` or a `Product` with
     `is_active=False` could still be picked when creating a **new** PO/PR/GRN/GIN — none of these four forms
     had ever been given the `is_active=True` filtering that e.g. `GrnForm.po` (valid PO statuses) or
     `PurchaseRequestForm.warehouse`/`assigned_to` already had. Fixed identically in all four: restrict the
     queryset to active (`Supplier.Status.ACTIVE` / `Product.is_active=True`) **OR** the instance's current
     FK value (`Q(...) | Q(pk=self.instance.supplier_id)`, mirroring `GrnForm.po`'s exact pattern) so editing
     an existing PO/PR/GRN/GIN whose supplier/product went inactive *after* creation still round-trips
     correctly instead of silently dropping the field's initial value. Tests:
     `test_TC_PUR_001_010_inactive_supplier_rejected_on_create`/`_011_inactive_product_rejected_on_create_item`/
     `_012_update_keeps_existing_inactive_supplier_selectable` (`purchasing/tests.py::PurchaseOrderCrudTest`),
     `test_TC_PR_001_003b_inactive_product_rejected_in_item_form` (`PurchaseRequestCrudTest`),
     `test_TC_GRN_VIEW_001_003b_inactive_product_rejected_in_item_form` (`receiving/tests.py::GrnViewTest`),
     `test_TC_GIN_VIEW_001_002b_inactive_product_rejected_in_item_form` (`shipping/tests.py::GinViewTest`).
  4. `warehouse.views.location_toggle_active` allowed deactivating every `Location` in a warehouse down to
     zero active ones — `warehouse.services.get_default_location()` (used by `start_qc`/QC FAIL/PARTIAL_PASS
     for Kho chờ/Kho phế, and by `stocktake.services.apply_adjustment` for a kiểm-toàn-kho session on *any*
     MAIN warehouse) requires at least 1 active location and already raised a clear `ValidationError` when
     none exists — but that error only surfaced later, inside an unrelated QC/stocktake flow, instead of being
     prevented at the point someone actually clicked "khoá" on the last one. Fixed by adding a check in
     `location_toggle_active`: before flipping an active location to inactive, query
     `warehouse.locations.filter(is_active=True).exclude(pk=loc.pk).exists()` — if nothing else is active,
     reject with `messages.error` instead of saving. Only guards the deactivate direction (reactivating is
     always safe). Tests: `test_TC_WM_02_005_cannot_deactivate_last_active_location`/
     `test_TC_WM_02_006_can_deactivate_when_another_active_location_remains`
     (`warehouse/tests.py::LocationCrudTest`) — `test_TC_WM_02_003_toggle_active_flips_flag_and_audits` also
     needed a second `Location` added to its setup, since it previously (accidentally) relied on being able to
     deactivate a warehouse's *only* location, which the fix now blocks.
  **General lesson carried forward from both audit passes**: the recurring bug shapes in this codebase are (a)
  a service function trusting a form's queryset filter instead of re-asserting the same constraint itself, and
  (b) a "there can be at most/at least N of X" invariant enforced in one direction (create) but not its mirror
  (reactivate / deactivate-the-last-one) — when reviewing any new form or toggle-style view, check both
  directions and check whether the service layer re-validates what the form merely filters.
- **Bug fix 2026-07-27: two Low-severity gaps from the same review pass, both missing bounds on a numeric
  input**:
  1. `catalog.Product.holding_cost_rate` (% chi phí lưu kho/năm, dùng tính EOQ — FR-INV-05) had no upper-bound
     validator — `ProductForm` exposes it as a plain `DecimalField`, so a value like `150.00` (150%) or a
     negative rate was accepted at face value and would silently distort the EOQ formula's `H` term for any
     SKU with a nonsensical rate. Fixed by adding `validators=[MinValueValidator(0), MaxValueValidator(100)]`
     to the model field (`django.core.validators`) — `ModelForm` validation rejects out-of-range values before
     `save()`, no view/service change needed since `ProductForm` already just calls `is_valid()`/`save()`.
     Migration `catalog/migrations/0005_alter_product_holding_cost_rate.py` (state-only, no `ALTER TABLE` —
     same shape as the `verbose_name`-only migrations described in the Frontend language convention section).
     Tests: `test_TC_CAT_001_008_holding_cost_rate_over_100_rejected`/
     `_009_holding_cost_rate_negative_rejected`/`_010_holding_cost_rate_100_accepted` (boundary value itself
     must still be accepted) in `catalog/tests.py::ProductCrudTest`.
  2. `quality.services.suggested_sample_qty`'s `FIXED`-sampling-method branch
     (`return min(product.qc_sampling_value, qty_received)`) had no floor, unlike the `PERCENT` branch
     (`max(1, min(...))`) — a `Product` configured with `qc_sampling_method=FIXED, qc_sampling_value=0`
     produced a suggested sample size of 0 even when `qty_received > 0`, inconsistent with `PERCENT`'s
     guarantee that any nonzero receipt always suggests sampling at least 1 unit. Display-only/non-blocking
     per the function's own docstring (QC can still PASS/FAIL/PARTIAL without hitting the suggested sample
     size), so this was a suggestion-quality gap, not a data-integrity one. Fixed by wrapping the `FIXED`
     branch in the same `max(1, ...)` floor as `PERCENT`. Test:
     `test_TC_QC_SAMPLE_004_fixed_zero_value_floors_at_1` in `quality/tests.py::SuggestedSampleQtyTest`.
  **General lesson**: a numeric config field or derived suggestion that has a sibling code path with a bound
  (another field's validator, another branch's floor/cap) should get the same bound by default unless there's
  a stated reason it shouldn't — the asymmetry itself (one branch floors at 1, the other doesn't; one rate
  field has no cap while similar percentage-shaped fields elsewhere do) is usually the tell that the bound was
  simply never added, not that it was deliberately omitted.
- **Bug fix 2026-07-27: sidebar links in `base.html` were gated by hardcoded role checks
  (`user.role != 'STAFF'`, `user.role == 'ADMIN' or ... or user.role == 'STAFF'`) instead of `user.can()`,
  and Purchase Request had no sidebar link at all**. `base.html` renders on every page (it's the layout shell,
  not a per-view template), so a view can't just pass a permission flag into its own context the way other
  pages do — added `accounts.context_processors.sidebar_permissions` (registered in `config/settings.py`
  TEMPLATES, same pattern as the existing `accounts.context_processors.notifications` badge-count processor)
  supplying `can_read_qc`/`can_read_opname`/`can_read_pr` via `user.can('read', <module>)` for every module
  that exists in `accounts/permissions.py` `MODULES`. The old role-hardcoded checks happened to produce the
  same result as `user.can()` for every role under the *default* `ROLE_PERMISSIONS` matrix — the bug only
  surfaces once an admin uses "Phân quyền chi tiết" (`views.user_permission_edit`) to grant or revoke a
  permission for one specific user: the sidebar, having no idea that override exists, still shows/hides the
  link based on raw `role` alone, either exposing a dead link (403 on click) or hiding a link the user
  actually has access to. Also added a "Yêu cầu mua hàng" sidebar link (`purchasing:pr_list`, gated
  `can_read_pr`) — the route already existed but was previously only reachable via a tab inside `po_list.html`.
  Left three sidebar gates **unchanged** on purpose: the "Phiếu chờ nhận hàng" (handoff) gate and the
  "Quản trị" (user management/audit log) gate are role/department checks by design, not module checks —
  `WarehouseHandoff` has no entry in `MODULES` (see §5 of the skill file: it deliberately uses a lighter
  PENDING/ACCEPTED/REJECTED model, not `Approval`, so there's no `user.can('approve', ...)` fallback to hang
  a context-processor flag off), and `accounts`'s own admin pages aren't part of the `MODULES` permission
  matrix either. The "Tồn kho"/GRN/GIN links also stayed unconditional — every role has `read` on
  `inventory`... except `inventory` isn't in `MODULES` at all (see the `inventory/views.py` docstring:
  no dedicated Permission Matrix column, same as `warehouse`/`catalog`), and every role already has
  `grn`/`gin` read by default, so gating them would add a flag with no actual behavior change. **General
  lesson**: any permission check baked directly into `base.html` (or any template rendered outside a specific
  view's own context) needs a context processor, not a per-view context variable — and it should call
  `user.can()`/module-based logic wherever a `MODULES` entry exists for that link, falling back to
  role/department checks only for the handful of things (handoff, admin pages, non-`MODULES` apps) that were
  never given a `MODULES` entry in the first place. See §6 of `.claude/skills/wms-conventions/SKILL.md` for
  the reusable pattern before adding the next gated sidebar link.
- **Perf/SEC sweep 2026-07-27, from a pasted code-review list — 5 perf fixes + 2 SEC hardening items**:
  1. `purchasing.views.po_list`'s `overdue_count` looped `sum(1 for po in orders if ...)` over the FULL
     `orders` queryset **before** `paginate_queryset` sliced it — every page load fetched and iterated every
     matching `PurchaseOrder` row in Python just to count `DELAYED` ones. `PurchaseOrder.delivery_status()`'s
     logic (pure Python, no query) was ported to a DB-level `Q`/`F` filter (`orders.filter(Q(status=SENT,
     expected_delivery_date__lt=today) | Q(status__in=[RECEIVED, CLOSED], received_at__gt=F(
     'expected_delivery_date'))).count()`) so only a `COUNT(*)` runs, not a full row fetch — the two branches
     must stay in sync with `delivery_status()` if that method's logic ever changes.
  2. `receiving.views.grn_detail` called `latest_approval_for(ret)` (1 query each, `accounts/approvals.py`)
     inside a `for ret in returns` loop — N+1 for GRNs with multiple `GrnReturn`s. Added
     `accounts.approvals.latest_approvals_for(model_class, pks)` — same lookup batched into 1 query
     (`target_id__in=[...]`, then picks the first/latest per `target_id` in Python since `Approval` has no
     DISTINCT ON in a portable way) — returns `{str(pk): Approval}`, use `.get(str(pk))` at the call site. Use
     this batch version instead of looping `latest_approval_for()` any time a list of objects (not a single
     detail page) needs its latest `Approval` displayed.
  3. `accounts.context_processors.notifications` ran 2 queries every page load (`unread_notification_count`
     count + `recent_notifications` slice) — but `recent_notifications` was never actually used in any
     template (the navbar bell only links to `notification_list`, a separate page with its own query). Deleted
     `recent_notifications` entirely rather than optimizing it into 1 query — a query nobody reads is waste at
     any query count. Don't reintroduce it without also wiring it into `base.html`'s bell dropdown.
  4. `accounts.views.audit_log_list` rebuilt `module_choices`/`actor_choices` (both `AuditLog.objects...
     .distinct()` over the **entire**, ever-growing `AuditLog` table) on every request just to populate 2
     filter `<select>`s. Cached both for 300s via Django's low-level cache API (`django.core.cache.cache`) —
     this is the project's **first use of Django's cache framework**; it works with zero config because
     Django's default backend (no `CACHES` setting in `config/settings.py`) is `LocMemCache`, an in-process
     memory cache with no Redis/Memcached dependency, consistent with the "no Celery/Redis until a backlog
     item needs it" stance. A stale filter dropdown for up to 5 minutes is acceptable for an admin lookup page;
     don't reach for this pattern on data where staleness would be incorrect, only for expensive-but-cosmetic
     aggregates like this.
  5. `quality.services.overdue_inspections()` (`WHERE status='PENDING_QC' AND started_at < threshold`, called
     fresh on every `receiving.views.grn_list` page load) had no supporting index — `QcInspection` had no
     index on `status`/`started_at` at all, so this was a full table scan every time, and the table only grows
     (QC inspections are never deleted). Fixed at the DB layer, not with caching (this data changes on every
     QC decision, so caching would trade correctness for speed unnecessarily): added
     `models.Index(fields=['status', 'started_at'])` to `QcInspection.Meta` (migration
     `quality/migrations/0007_qcinspection_quality_qci_status_bdb30c_idx.py`), matching the exact WHERE clause.
     **General lesson for all 5**: don't reach for caching as a default fix — check first whether the query is
     simply missing an index (fix #5, a correctness-preserving DB fix) or is fetching more than the caller
     needs (fixes #1/#2/#3, app-level waste with no caching required at all); reserve caching (fix #4) for
     values that are expensive to compute *and* tolerate staleness.
  6. **[SEC] `quality.models.QcCriteria.reference_image`/`QcInspectionItem.image`** (`ImageField`) had no
     explicit size or extension limit — Django's `ImageField` only verifies the upload is a real image via
     Pillow, it doesn't cap file size or whitelist extensions, so a huge (but valid) image could be uploaded
     unchecked. Added `quality.models.validate_image_upload` (shared model validator, 5MB cap +
     `.jpg/.jpeg/.png/.webp/.gif` whitelist — GIF included because the existing test suite's image fixtures
     use a minimal 1x1 GIF, not because GIF was a specific requirement) applied via `validators=[...]` on both
     fields (migration `quality/migrations/0008_alter_qccriteria_reference_image_and_more.py`). Any future
     `ImageField`/`FileField` added anywhere in the project should reuse or mirror this validator rather than
     relying on `ImageField`'s bare Pillow-only check.
  7. **[SEC] no login rate-limiting** — `accounts.forms.LoginForm` (used by `django.contrib.auth.views.
     LoginView` at `/login/`) had no brute-force protection. Added an IP-keyed counter using the same
     zero-config `LocMemCache` from fix #4 (no new dependency like `django-ratelimit`/`django-axes`):
     `LoginForm.clean()` checks `cache.get(f'login_rate_limit:{ip}')` against
     `LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5` *before* calling `super().clean()` (which does the real
     `authenticate()`); a failed attempt increments the counter with a `LOGIN_RATE_LIMIT_WINDOW_SECONDS = 900`
     (15 min) TTL, a successful login calls `cache.delete(...)` to reset it. `registration/login.html`
     previously showed one hardcoded generic message ("Tên đăng nhập hoặc mật khẩu không đúng.") for **any**
     form error, by design (avoids leaking which of username/password was wrong) — rather than reusing that
     path for the rate-limit case too (which would confuse a user with the *correct* password into thinking
     they mistyped it), added a `LoginForm.rate_limited` boolean flag (set in `clean()`, not string-matched
     from the error message) that the template checks to show a distinct "thử lại sau 15 phút" message instead.
     **Test-isolation trap**: `LocMemCache` is a process-global singleton, not reset between Django test
     methods the way the DB is (transaction rollback) — `accounts.tests.LoginAuthTest` (which posts wrong
     passwords in several test methods, all from the test client's fixed `127.0.0.1`) needed an explicit
     `cache.clear()` in `setUp()`, otherwise failed-login counts silently accumulate *across* test methods (and
     would eventually trip the rate limit on an unrelated test, order-dependently) since nothing else clears
     that cache between tests. Any future test touching a view gated by this cache-counter pattern needs the
     same `setUp()` guard. Tests: `TC-USER-03-007`/`008` (`accounts/tests.py::LoginAuthTest`).
  Deliberately left alone in this same pass: `config/settings.py` missing `SECURE_SSL_REDIRECT`/
  `SESSION_COOKIE_SECURE`/`CSRF_TRUSTED_ORIGINS`/etc. and `/media/` being unauthenticated when `DEBUG=True` —
  already reviewed and intentionally deferred (see the memory note from that review), since there is still no
  Docker/deploy target yet for those settings to protect; re-confirmed still true on 2026-07-27 (no
  `Dockerfile` in the repo) rather than re-fixing speculatively. Load/stress testing was also explicitly
  scoped out of this pass (needs real infrastructure to be meaningful, not a code change).
