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
  and "UI conventions" sections matching what the code actually does. Record the resulting *invariant* and
  *general lesson*, not a blow-by-blow narrative — exact migration numbers, line numbers, and test names are
  already preserved in `git log`/the code itself, so they don't need to be re-typed here.
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
picture — read `BACKLOG.md` Phase 2/3 in full before touching GRN, QC, or GIN. This section tracks current
invariants and reusable lessons only; detailed bug-fix narratives (migration numbers, line numbers, test
names, exact dates) live in `git log`, not here.

### Core domain: GRN → QC → Batch → Inventory

- **PO ↔ GRN circular dependency**: resolved with a **PO stub** in Phase 1 (just `po_no`, `supplier_id` FK,
  `status` defaulted to `SENT`, line items) so GRN has a valid FK before the full PO workflow (Phase 5)
  exists.
- **GRN/QC/Batch/Inventory is one transaction, not three**: QC result decides atomically — PASS creates a
  `PENDING_RECEIPT` batch and credits inventory; FAIL creates a `GRN_RETURN` and must **not** touch
  inventory; PARTIAL_PASS splits into two batches (`PENDING_RECEIPT` for the passed qty, `QUARANTINE` for
  the failed qty), crediting inventory only for the passed portion. Each path needs its own test.
- **Batch status enum**: `PENDING_RECEIPT`, `ACTIVE`, `PARTIAL_USED`, `QUARANTINE`, `EXPIRED`, `CLOSED`.
  FIFO/GIN and any "is this batch usable" query must use `status__in=[ACTIVE, PARTIAL_USED]` —
  `PARTIAL_USED` is a normal mid-life state, not terminal, so filtering to `status='ACTIVE'` alone silently
  strands partially-issued batches. `PENDING_RECEIPT`/`QUARANTINE`/`EXPIRED` are always excluded regardless
  of `qty_available`.
- **FIFO issue algorithm** (GIN): `batch WHERE product_id=? AND qty_available>0 AND status IN
  ('ACTIVE','PARTIAL_USED') ORDER BY exp_date ASC, created_at ASC`, splitting the requested qty across
  multiple batches if needed. The most error-prone piece of logic in the app — keep dedicated unit tests
  (single batch, multi-batch split, insufficient total stock).
- **Three warehouse types, one mandatory QC staging step**: `Warehouse.warehouse_type` is `MAIN`
  (GIN/FIFO-eligible), `STAGING` ("Kho chờ"), or `SCRAP` ("Kho phế") — at most one active STAGING and one
  active SCRAP warehouse company-wide (DB `UniqueConstraint`); `warehouse_type` is locked after creation
  (create a new warehouse + `deactivate_warehouse()`/`activate_warehouse()` to change it). Confirmed GRN
  receipt goes straight into a real `Batch`+`Inventory` in STAGING via `start_qc()` — goods are never
  invisible while awaiting QC. `qc_pass`/`qc_fail`/`qc_partial_pass` consume that staging batch via
  `inventory.services.move_batch_qty()` (shared split primitive, also used by `transfer_stock`) to move it
  into MAIN (`PENDING_RECEIPT`) and/or SCRAP (`QUARANTINE`). Guards enforcing "must go through QC":
  `qc_pass`/`qc_partial_pass` reject non-MAIN destinations; `transfer_stock` rejects a STAGING source *and*
  a non-MAIN destination; `GinForm`/`Gin.clean()` restrict GIN to MAIN warehouses (FIFO's status filter
  alone doesn't exclude staging batches, since they're `ACTIVE` too). `reports.services` and the Min/Max
  banner filter `warehouse__warehouse_type=MAIN` so STAGING/SCRAP never inflate KPIs. Singleton lookups
  (`get_staging_warehouse()`, `get_scrap_warehouse()`, `get_default_location()`) live in
  `warehouse/services.py`.
- **QC PASS → warehouse handoff**: the passed-qty destination batch is created `PENDING_RECEIPT`, not
  `ACTIVE` — `Inventory.qty_on_hand` is credited immediately, but FIFO eligibility waits for a real person
  to confirm. Each such batch gets one `inventory.models.WarehouseHandoff`
  (`inventory.services.create_handoff()`, notifies an optional `assigned_to` or falls back to
  `destination_warehouse.staff`/whole WAREHOUSE department). `accept_handoff()` flips the batch to `ACTIVE`;
  `reject_handoff()` needs a reason + `RejectDestination` (`TO_SCRAP` splits into a `QUARANTINE` batch via
  `move_batch_qty`; `BACK_TO_QC` is annotation-only, mirrors the QC-override boundary below, and requires
  manual follow-up). `can_decide_handoff()` — used both to gate the action and to filter `handoff_list` — is
  `is_superuser`/`role==ADMIN` (oversight) OR `is_department_manager('WAREHOUSE')` OR the exact
  `assigned_to` user OR `destination_warehouse.staff` (falling back to the whole WAREHOUSE department).
  Unlike GRN/GIN/PR, this flow deliberately does **not** use the generic `Approval` model — a handoff
  targets a specific person/team, not a department-manager decision.
- **QC override is annotation-only**: Manager/Admin can override a QC result (`quality:qc_override`,
  permission `can_override_qc`) and it records `override_note`/`overridden_by` + audit log, but it never
  reverses the Batch/Inventory that PASS/FAIL/PARTIAL_PASS already created. The same boundary applies to
  `reject_handoff(..., BACK_TO_QC)` above — once QC/Batch effects exist, only manual follow-up (e.g. a
  manual `transfer_stock`) unwinds them; nothing auto-reverses a completed transaction.
- **`cancel_x`/`reject_x` at an intermediate workflow status must reverse any side effect already created to
  reach that status** — e.g. cancelling a GRN mid-`QC_IN_PROGRESS` must also reverse the staging
  `Batch`/`Inventory` that `start_qc()` created (`quality.services.cancel_qc_inspection()`), not just flip
  the owning object's own status. This differs from the QC-override boundary above: a staging batch under
  `QC_IN_PROGRESS` has no completed decision yet, so reversing it is undoing an in-flight step, not
  rewriting history. A regression test for this class of bug must drive the object through the real service
  flow to reach the intermediate status — constructing it directly at that status won't have created the
  side effect to check for. The side effect to reverse isn't only Batch/Inventory — it also includes any
  aggregate state on a *parent* model updated in that same original transaction: `cancel_grn` must also
  call `purchasing.services.sync_po_status(grn.po)` so `PurchaseOrder.status`/`received_at` (bumped by the
  same `grn_receive_qty` transaction that ran `start_qc()`) un-sticks from `PARTIAL_RECEIVED`/`RECEIVED`
  back down to `SENT` when the GRN carrying that qty is cancelled — and `sync_po_status`'s own aggregate
  query, plus the sibling GRN-quota check in `BaseGrnItemFormSet.clean`, must exclude `CANCELLED` GRNs
  entirely (not just QC-`REJECTED` items), or a cancelled GRN's qty permanently counts against both the
  PO's fulfillment and its re-receive quota. **Ordering hazard, same shape as the PR two-stage-approval one
  below**: a reversal that re-queries the DB for "is this GRN cancelled" must run *after* the owning
  object's own status is mutated and saved, not before — calling `sync_po_status` before `grn.status =
  CANCELLED` is persisted means its exclude-CANCELLED query still sees the old status and silently no-ops.
- Any code that mutates `Inventory.qty_on_hand` directly must also keep `Batch` in sync (create a new
  `ACTIVE` batch for a surplus, consume existing `ACTIVE`/`PARTIAL_USED` batches FIFO-order for a shortage)
  — established for GRN/QC/GIN from the start, and retrofitted onto Stock Opname adjustments
  (`stocktake.services.apply_adjustment`) after Batch and Inventory drifted out of sync. Grep for direct
  `Inventory.objects...qty_on_hand` writes when auditing a new module for this pattern.
- **Audit trail** (who/what/when/why) is required on every GRN/QC/Batch state transition via
  `accounts.AuditLog`/`log_action()` — treat this as non-negotiable on any new transition, it's much harder
  to retrofit than to add up front.
- `qty_available = qty_on_hand - qty_reserved` (and `Batch.qty_available = qty_received - qty_used`) is
  always computed, never stored — keep it derived wherever used.

### Department-manager approval axis

- `User.role` still drives the CRUD permission matrix (`user.can(action, module)`, `ROLE_PERMISSIONS`/
  `rbac.py`) unchanged. A second, independent axis — `User.department` + `User.is_manager` — lets each
  department (WAREHOUSE/QC/PURCHASING/ACCOUNTING) have its own manager who approves/cancels tickets
  currently in that department's stage. Check via `user.is_department_manager(department)`, never
  `user.role == 'MANAGER'`. `is_manager=True` requires a non-blank `department` (form-enforced), since
  `is_department_manager` can never match otherwise.
- Generic **`Approval`** model (`accounts.models.Approval`, GenericFK `target`) +
  `accounts.approvals.create_approval()`/`decide_approval()`/`latest_approval_for()` (single)/
  `latest_approvals_for()` (batched, avoids N+1 on list pages) is the shared "staff submits → department
  manager decides" primitive — see §4 of `.claude/skills/wms-conventions/SKILL.md` before wiring a new
  approval step. A partial `UniqueConstraint` on `(target_type, target_id) WHERE status='PENDING'` blocks
  two concurrent submits from creating duplicate PENDING approvals for the same target;
  `create_approval()` catches the resulting `IntegrityError` and re-raises as `ValidationError`. Every
  Approval-gated flow follows the same shape: staff action → `PENDING_APPROVAL`-style status +
  `Approval(department=X)` → department manager (or the Manager/Admin `can('approve', module)` fallback)
  decides via `decide_*()` → real transition on approve, revert on reject.
- Flows currently routed through `Approval`: **GRN submit** (`Grn.Status.PENDING_APPROVAL`,
  `receiving.services.request_submission()`/`decide_grn_submission()`, department WAREHOUSE;
  `Grn.current_department` property drives who can cancel at each stage), **GrnReturn confirmation**
  (`request_return_confirmation()`/`decide_return_confirmation()`, department QC, auto-notifies WAREHOUSE on
  approve), **GIN start-picking** (`Gin.Status.PENDING_APPROVAL`, staff use `gin_confirm_request()` +
  `gin_approve/reject_confirmation()` since STAFF has no `update` on `gin`; Manager/Admin still start
  picking directly), and **Purchase Request** (below).
- `accounts.models.Notification` + `accounts.notifications.notify()` are plain DB-polled (no
  Celery/websocket, per the `⏸️` convention). Any model used as a notification/approval target should
  implement `get_absolute_url()` so `notification_mark_read` can deep-link to it. `Warehouse.staff`
  (M2M→User, `department=WAREHOUSE`) lets a handoff/notification target a specific warehouse's staff,
  falling back to the whole department when empty.
- **Audit Log access narrowed to Admin/superuser only** (`accounts.permissions.can_view_audit_log()`,
  2026-07-29, system-wide — not PR-specific): `/audit-log/` previously let any `is_manager=True` department
  manager browse every module's history; a department manager's own approval history is already visible on
  the relevant object's detail page (PR/GRN/GIN), so a global cross-module log is now Admin/superuser-only.
  Gate at all three layers per the menu-access pattern below: `can_view_audit_log(user)` in the view,
  `sidebar_permissions` (`can_view_menu('audit_log') and can_view_audit_log(user)`, combined in Python — not
  as a compound `{% if %}` expression in the template), and the view itself.
- **Purchase Request (PR) — two-stage sequential approval** (redesigned 2026-07-29 from the original
  single-stage "always routes to PURCHASING" flow): `submit_purchase_request()` routes first to the
  *requester's own* department manager (`PENDING_DEPT`, `Approval(department=requester.department)`) —
  unless the requester belongs to PURCHASING or has no department, which skips straight to `PENDING_PUR`
  (`Approval(department=PURCHASING)`) so nobody ends up approving their own PR twice. Approving at
  `PENDING_DEPT` only *advances* the PR to `PENDING_PUR` and opens the second `Approval` — it is not a final
  decision, so `decided_by`/`decided_at` stay unset; only approving at `PENDING_PUR` sets those fields and
  flips to `APPROVED`. Rejecting at either stage ends the PR immediately (`REJECTED`). Both stages share one
  service function, `decide_purchase_request()` — it reads which stage it's in from `pr.status` before
  mutating, rather than being split into two stage-specific functions. **Ordering hazard**: the stage-2
  `Approval` must be created *after* `decide_approval()` returns, never inside its `on_approve()` callback —
  `decide_approval()` invokes the callback before persisting `approval.status=APPROVED`, so opening a second
  `Approval` while the first is still `PENDING` in the DB collides with the `unique_pending_approval_per_target`
  constraint (two PENDING rows for the same target).
  `assigned_to` (optional; staff picks it or a manager `forward_purchase_request()`s later) is still
  informational/notification only, never a decision right. Decision rights flow entirely through
  `can_decide_pr(user, pr)` (`user.can('approve','pr')` Manager/Admin fallback, OR
  `user.is_department_manager()` of whichever department currently holds the PR's `Approval`, per
  `accounts.approvals.latest_approval_for(pr)`) — `can_manage_pur_pr(user)` is the PR-independent variant
  (PURCHASING department manager, or the Manager/Admin fallback) used by `pr_forward` and the `?from_pr=`
  gate on `po_create`, both of which are always PURCHASING-stage actions regardless of which PR they touch.
  Notify `assigned_to` at **final approval** (`PENDING_PUR`→`APPROVED`), not at submit. A REJECTED PR can be
  reopened back to DRAFT (`reopen_purchase_request()`), keeping `decided_by`/`reject_reason` as history — the
  UI only shows the rejection reason while `status == REJECTED`. A DRAFT PR can be hard-deleted
  (`delete_purchase_request()`, gated by module `delete` permission **and** ownership/view-all).
  Visibility is 4 tiers, enforced identically at `pr_list` (`_pr_visible_queryset()`) and `pr_detail`
  (`_pr_can_view()`) — never just the list filter: (1) `_pr_can_view_all()` (superuser/MANAGER/ADMIN) sees
  everything — the PURCHASING department manager is **no longer** in this tier post-redesign; (2) the origin
  department's manager sees every submitted (non-DRAFT) PR from their own department, including read-only
  after it has moved on to `PENDING_PUR` (no more `can_approve`/edit once the `Approval` has left their
  department); (3) the PURCHASING department manager sees any PR that has ever reached `PENDING_PUR`,
  tracked via `Approval(department=PURCHASING)` history (`_pr_ids_with_pur_approval()`) rather than current
  status, so an already-`APPROVED`/`REJECTED` PR that passed through PURCHASING stays visible; (4) everyone
  else sees only PRs they created themselves, plus PRs where they're `assigned_to` **and** the PR is already
  `APPROVED` — never while pending at either stage, to avoid leaking a PR before it's actually approved.
  Approval history for a PR is shown in full (`accounts.approvals.approval_history_for()`, both stages in
  submission order), not just the latest record like GRN/GIN — see §4 of the skill file.
- **Supplier.managed_by**: PURCHASING role can create `Supplier` rows (not just Manager/Admin); the row
  auto-gets `managed_by=creator`, and `can_edit_supplier` limits a PURCHASING user to editing only suppliers
  they created (Manager/Admin edit any).
- **PO**: `created_by` (nullable, auto-set) scopes `po_list` to "own POs only" for a plain PURCHASING user
  (not the department manager) — every other role sees all POs (deliberately asymmetric with PR: PO read
  access is broadly granted and multiple people legitimately act on the same PO across its lifecycle).
  `po_detail` applies no such check. `PurchaseOrder.source` (`MANUAL`/`FROM_PR`) and
  `PurchaseRequest.origin` (`MANUAL`/`MIN_LEVEL`) track provenance; there is no direct "Min Level → PO"
  shortcut anymore — the below-Min-Level dashboard link goes to `pr_create` (PURCHASING can create PRs
  itself), so every PO born from low stock goes through a PR + approval first. `Product.preferred_supplier`
  (nullable FK) prefills the supplier when converting a PR to a PO. `close_po()` requires a `close_reason`
  when closing early (from `SENT`/`PARTIAL_RECEIVED`), not when closing an already-`RECEIVED` PO.
  `send_po()` best-effort emails the supplier if `contact_email` is set; never blocks the SENT transition
  either way.
- **Menu-access permission axis** (`accounts.permissions.MENU_ITEMS`/`User.can_view_menu(key)`): a second
  permission axis, parallel to the CRUD matrix, gating plain sidebar visibility for the 7 modules with no
  CRUD concept (`warehouse`, `catalog`, `partners`, `inventory`, `handoff`, `user_mgmt`, `audit_log`) —
  default-granted to every role, used only to narrow a specific user via "Phân quyền chi tiết". Enforce at
  all three layers for any new menu item: the sidebar link (via
  `accounts.context_processors.sidebar_permissions`), the real view
  (`if not request.user.can_view_menu(key): raise PermissionDenied`), and — critical Django template
  gotcha — never combine an oversight-role check with a `can_view_menu`/`can_read_*` check on one
  `{% if a or b and c %}` line (`and` binds tighter than `or`, silently letting the role check bypass the
  menu check); always nest `{% if a %}{% if c %}...{% endif %}{% endif %}`. Full pattern in §6.1 of the
  skill file.
- **Sidebar-link visibility and the view/permission check it links to are separate conditions** — adding a
  new oversight role (ADMIN/superuser) to one without the other is a standing trap (e.g. the handoff
  sidebar link and `can_decide_handoff()` originally disagreed on whether ADMIN counted). Grep the target
  view's permission helper whenever a sidebar visibility condition is touched.

### Established patterns to apply proactively (from accumulated bug fixes)

Each of these was a real bug found in review; the fix is now the standing convention — apply it by default
rather than rediscovering the failure.

- **Sequential number fields** (`po_no`, `request_no`, `grn_no`, `gin_no`, `transfer_no`, `so_no`):
  `select_for_update()` inside `generate_*_no()` cannot prevent two concurrent creates from computing the
  same next number (it can't lock a row that doesn't exist yet). Every model's `save()` retries up to 5
  times on `IntegrityError` (regenerate the number, retry inside a savepoint) — the DB `unique=True`
  constraint is the real correctness guarantee. Apply this to any future auto-generated sequential code
  field.
- **Form querysets filter, services must re-validate independently** — a `ModelChoiceField` queryset
  restricting to e.g. active suppliers/valid statuses/same-warehouse locations is a UX convenience only;
  the paired service function must re-assert every one of those constraints itself (grep the form's
  queryset filters and confirm a matching check exists in the service). The idiom
  `Q(is_active=True) | Q(pk=self.instance.fk_id)` keeps a since-deactivated value selectable when editing an
  existing record, without allowing it on create.
- **"At most/at least N of X" invariants need both directions guarded** — e.g. the warehouse-type singleton
  (STAGING/SCRAP) needed both `deactivate_warehouse()` *and* `activate_warehouse()` to check the other side;
  "a warehouse needs ≥1 active location" needed a guard on deactivating a `Location` too, not just at
  creation. When adding a create-side guard, check whether the delete/deactivate/reactivate side needs the
  mirror check.
- **Soft-delete invariant**: `is_deleted=True ⇒ is_active=False` is enforced inside `User.save()` itself
  (appending `is_active` to `update_fields` if narrowed) and independently in
  `DirectPermissionsBackend.user_can_authenticate()` (defense in depth against `.update()`/admin bypassing
  `.save()`). Any mutating view on a soft-deletable model should also reject editing an already-deleted row
  outright, and a self-edit guard on one mutable field (e.g. `is_active`) doesn't cover a sibling field on
  the same form (e.g. `role`) — check every field on a self-edit form that could lock an admin out.
- **`GenericForeignKey` bulk cleanup must be scoped, never blanket-deleted** — when cleaning up
  `Notification`/`Approval`/`AuditLog` rows tied to deleted objects (e.g. `seed_demo_data --flush`), collect
  `(ContentType, pk)` for every object *before* deleting it and filter the log/notification table by that
  combined `Q`; never fall back to `.objects.all().delete()` on a table a real environment might also be
  writing to.
- **Nullable FK added for visibility/assignment on a table with live rows** (`created_by`,
  `assigned_to`-style) needs a paired backfill migration (recover from `AuditLog` where possible), and the
  filter should treat "unknown owner" as visible-to-everyone, not invisible-to-everyone.
- **Retrofitting the `Approval` pattern onto an existing flat-permission workflow** needs a backfill data
  migration for any row already sitting in the pre-gate "pending" status, in the same change that adds the
  gate — otherwise those rows become permanently un-actionable.
- **State-transition views that take a target pk from the querystring/POST body** (not just a URL path
  segment already scoped by permission) must re-check ownership/visibility and status inside the
  transaction (`select_for_update()` + re-validate), not only via a pre-transaction `get_object_or_404` —
  closes both "guess another user's pk" and the TOCTOU race between two concurrent submits.
- **Login rate limiting**: IP-keyed counter in Django's default `LocMemCache` (`accounts.forms.LoginForm`,
  5 attempts / 15 min). `client_ip()` only trusts `X-Forwarded-For` when `settings.TRUST_X_FORWARDED_FOR` is
  explicitly `True` (no reverse proxy yet — an unconditionally-trusted XFF header is a rate-limit bypass).
  The counter only resets on an actual successful `authenticate()` (via `form.get_user()`), not merely "no
  exception raised" — a blank-password submit doesn't raise but also isn't a real attempt. `LocMemCache` is
  process-global and does **not** reset between Django test methods — tests touching this need an explicit
  `cache.clear()` in `setUp()`.
- **`ImageField`/`FileField` uploads** need an explicit size cap + extension whitelist validator
  (`quality.models.validate_image_upload` is the existing example) — Django's `ImageField` only verifies
  it's a valid image via Pillow, it doesn't bound size or extension.
- **Raw `request.GET`/`request.POST` values feeding `int()`/`parse_date()`** must be wrapped in try/except
  with a safe fallback — Django never validates raw querystring access, and `parse_date()` specifically can
  both return `None` (malformed shape) *and* raise `ValueError` (valid shape, invalid calendar date), so
  both failure modes need catching.
- **Per-SKU thresholds/KPIs must aggregate across every row in scope before comparing**, not check
  row-by-row — e.g. `low_stock_count` must sum a SKU's qty across all MAIN warehouses before comparing to
  `min_level`, or a split-stock SKU gets double-counted/miscounted. Similarly, a KPI naming a business
  concept that maps to a multi-value status enum (e.g. "pending GRN") must enumerate every status belonging
  to that concept, not just the one that existed when the KPI was first written.
- **A numeric field/derived value with a sibling that has a bound** (a percentage field capped elsewhere, a
  sample-size floor on one sampling method but not another) should get the same bound by default — the
  asymmetry itself is usually evidence the bound was simply never added.
- **Performance**: prefer fixing a missing index or reducing per-request query count over reaching for
  caching. Reserve caching for values that are both expensive to compute *and* tolerate staleness (e.g. the
  audit-log filter dropdowns are cached 300s via Django's default `LocMemCache` — the first use of the cache
  framework in this repo; zero-config, no Redis).
- **Pre-deploy hardening deferred, not forgotten**: `SECURE_SSL_REDIRECT`/`SESSION_COOKIE_SECURE`/
  `CSRF_TRUSTED_ORIGINS`/media-file auth are intentionally left unset — no Docker/deploy target exists yet
  for those settings to protect (see repo-state note at top of this file). Revisit at Phase 9
  (Docker/deploy), not before.
