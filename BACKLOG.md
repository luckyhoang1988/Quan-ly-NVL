# 📋 BACKLOG — NVL/WMS Implementation Checklist (Solo Dev Edition)

> **Nguồn:** `SRS` (52 FR gốc theo Section 3) + `FSD` (workflow/data model/API/UI từng module — hiện đầy đủ nhất cho GRN & GIN) + điều chỉnh theo `Ke_Hoach_Trien_Khai_NVL_Solo.pdf` (kế hoạch solo dev + Claude Code).
> Tick `[x]` khi hoàn thành. **Chỉ các dòng có mã in đậm `FR-XX-##` được tính vào bộ đếm 60 FR** — các dòng còn lại (Business Rules, Workflow States, Algorithm, Transaction...) là ghi chú kỹ thuật hỗ trợ Claude Code, tick tự do, không ảnh hưởng % tiến độ.

**Tổng tiến độ:** 55 / 60 FR
**Timeline mục tiêu:** 24 tuần (5-6 tháng) — solo dev, xem nhịp làm việc ở Phụ lục D.
**Tech stack đã chốt (solo):** Django Template + Bootstrap 5 + HTMX (monolith) · PostgreSQL · Celery/Redis **hoãn** đến khi thật cần · Docker hóa cuối Phase 1.

---

## 🔍 Ghi Chú Từ BA Review (đọc trước khi code)

Sau khi rà lại BRD/SRS/FSD + kế hoạch solo + phân tích chi tiết quy trình GRN→QC→Nhập kho, đây là những vấn đề đã sửa trong bản backlog này so với bản trước:

1. **Thứ tự module bị sai lệch so với kế hoạch solo.** Bản cũ để User & Permission ở vị trí #9 (gần cuối), nhưng mọi audit trail, mọi field `created_by`, mọi RBAC đều cần User model tồn tại **trước tiên**. Đã dời lên Phase 1 (Tuần 1-2).
2. **QC bị tách rời khỏi GRN.** Bản cũ xếp QC ở vị trí #6, sau cả GIN(#4) và Stock Opname(#5) — nhưng QC chính là 1 phần workflow của GRN (state `PENDING_QC` → `QC_IN_PROGRESS` nằm ngay trong GRN). Kế hoạch solo cũng gộp chung "Tuần 3-6: GRN + QC". Đã gộp lại thành Phase 2.
3. **Thiếu Product (SKU) & Supplier làm master data.** SRS không có FR riêng cho 2 bảng này (chúng chỉ là bảng tham chiếu), nhưng GRN/PO/Inventory đều phụ thuộc — nếu không làm trước, Claude Code sẽ không có gì để tạo FK. Đã bổ sung vào Phase 1 (đánh dấu rõ "bổ sung ngoài 60 FR").
4. **Circular dependency PO ↔ GRN.** FR-GRN-04 bắt buộc GRN tham chiếu PO, nhưng theo lịch solo, PO đầy đủ (workflow duyệt, so sánh giá NCC, auto-suggestion) chỉ làm ở Tuần 14-16 — sau GRN (Tuần 3-6) rất xa. Giải pháp: tách PO làm 2 phần — **PO stub tối thiểu** (Phase 1, chỉ đủ để GRN tham chiếu) và **PO đầy đủ** (Phase 5).
5. **Nhiều mục ngầm định cần Celery** (auto-tạo PO khi tồn thấp, gửi email NCC khi GRN reject, scheduling report, alert SLA/quarantine/near-expiry) trong khi kế hoạch solo đã quyết định **hoãn Celery+Redis**. Đã gắn nhãn ⏸️ và đề xuất thay thế tạm (tính on-the-fly khi load trang, hoặc Django management command chạy bằng cron).
6. **API/Integration bị đặt ngang hàng ưu tiên với các module UI chính.** Vì kiến trúc solo dùng Django Template + HTMX (không phải React SPA + DRF tách biệt), FR-API không còn là đường chính để dùng app — hạ xuống 1 phase riêng (optional/ongoing), chỉ làm khi thật cần.
7. **Chưa có SRS.docx/FSD.docx đầy đủ trong repo.** Trong thư mục `Tai_lieu/` hiện chỉ có 1 file giải thích khái niệm BRD/SRS/FSD (minh họa chi tiết cho GRN & GIN, các module khác chưa có workflow/data model/API/UI spec đầy đủ) và 1 file kế hoạch triển khai. **Hành động cần làm:** nếu bạn có bản SRS.docx/FSD.docx đầy đủ ở nơi khác, copy vào `docs/` trước khi bắt đầu Phase 4 trở đi. Nếu không, phần "Chi tiết kỹ thuật" trong backlog này (Business Rules, Workflow States, Data Model...) sẽ đóng vai trò FSD tạm thời cho Claude Code đọc.
8. **Số 60 FR (backlog) vs 52 FR (SRS gốc)** — chênh lệch vì backlog có thêm module API/Integration (5 FR) không nằm trong 9 module gốc của SRS. Giữ nguyên 60 làm số tổng vận hành thực tế, nên đối chiếu lại với SRS.docx gốc khi có bản đầy đủ.

---

## 🗺️ Bản Đồ Module → Phase → Django App

| Phase | Tuần | Module | FR | Django App đề xuất |
|---|---|---|---|---|
| 0 | 1 | Setup dự án | – | – |
| 1 | 1-2 | User & Permission | 5 | `accounts` |
| 1 | 1-2 | Warehouse Management | 6 | `warehouse` |
| 1 | 1-2 | *Product/SKU (bổ sung)* | – | `catalog` |
| 1 | 1-2 | *Supplier (bổ sung)* | – | `partners` |
| 1 | 1-2 | *PO stub (bổ sung, tối thiểu)* | – | `purchasing` |
| 1 | 1-2 | *Inventory/Batch (schema only)* | – | `inventory` |
| 2 | 3-6 | Phiếu Nhập (GRN) | 7 | `receiving` |
| 2 | 3-6 | Quality Control (QC) | 6 | `quality` |
| 3 | 7-10 | Inventory Management (logic đầy đủ) | 5 | `inventory` |
| 3 | 7-10 | Phiếu Xuất (GIN) | 7 | `shipping` |
| 4 | 11-13 | Kiểm Kê (Stock Opname) | 7 | `stocktake` |
| 5 | 14-16 | Purchase Order (đầy đủ) | 6 | `purchasing` |
| 6 | 17-19 | Reporting & Analytics | 6 | `reports` |
| 7 | ongoing | API & Integration | 5 | `api` (nếu cần) |
| 8 | 20-22 | UAT + Polish | – | – |
| 9 | 23-24 | Deploy + Buffer | – | – |

---

## PHASE 0 — Setup Dự Án (Tuần 1)

- [ ] Tạo Git repo (`nvl-wms` hoặc tên khác)
- [ ] `django-admin startproject` + kết nối PostgreSQL local (chưa Docker)
- [ ] Copy BRD/SRS/FSD gốc (nếu có) vào `docs/`
- [ ] Tạo `.claude/PROMPT_TEMPLATE.md` — nhắc Claude Code chỉ đọc đúng phần FSD của module đang làm trong mỗi session
- [ ] Tạo cấu trúc app Django theo bảng "Module → Phase → Django App" ở trên (có thể `startapp` tạo rỗng trước)

---

## PHASE 1 — Nền Tảng & Master Data (Tuần 1-2)

*(Làm trước tiên — mọi module khác phụ thuộc vào đây. Đây là điểm khác biệt lớn nhất so với bản backlog cũ: User & Permission được dời từ vị trí #9 lên đây.)*

### 1a. User & Permission Management — `accounts` app

#### Functional Requirements (5 FR)
- [x] **FR-USER-01** `MUST` — CRUD user account (Create, Read, Update, Delete)
- [x] **FR-USER-02** `MUST` — Role-based access control (RBAC): Warehouse Manager, Staff, QC, Purchasing, Accountant, Admin
- [x] **FR-USER-03** `MUST` — Login & authentication (username/password)
- [x] **FR-USER-04** `MUST` — Permission matrix: từng role có quyền hạn rõ ràng (Create, Read, Update, Delete, Approve)
- [x] **FR-USER-05** `MUST` — Ghi lại user action (who, what, when) cho audit

#### Permission Matrix (từ FSD)
| Role | GRN | GIN | Opname | QC | PO | Reports |
|---|---|---|---|---|---|---|
| Manager | CRUD | CRUD | CRUD | R | CRUD | R |
| Staff | CRU¹ | CR | CRU | – | R | R |
| QC Inspector | R | R | – | CRU | R | R |
| Purchasing | R | R | – | R | CRUD | R |
| Accountant | R | R | – | R | R | CRUD |
| Admin | CRUD | CRUD | CRUD | CRUD | CRUD | CRUD |

#### CRUD Operations
- [x] CREATE: admin tạo user → email temporary password
- [x] READ: xem danh sách, chi tiết, quyền hạn
- [x] UPDATE: đổi role, ~~gán warehouse~~ (hoãn tới mục 1b), deactivate
- [x] DELETE: soft delete (giữ audit trail)

> ⚠️ Làm module này xong **trước** khi đụng vào GRN/QC — mọi bảng audit log ở Phase 2 cần FK tới `User`.

> ¹ Điều chỉnh so với FSD gốc (bản FSD ghi Staff = CR trên GRN): khi cài Workflow States
> ở mục 2a (Task 3, Phase 2) phát hiện mâu thuẫn — state `PENDING_QC` yêu cầu "nhân viên
> Kho nhập Qty thực tế nhận, nút Submit to QC", tức STAFF cần sửa (`update`) GRN ở bước
> này, nhưng ma trận CR gốc không cho. Đã chốt thêm `update` cho STAFF/GRN (giữ MANAGER/
> ADMIN full CRUD+approve, `approve` — tức QC PASS/FAIL/PARTIAL_PASS — vẫn tách riêng ở
> module QC, STAFF không đụng tới).

### 1b. Warehouse Management — `warehouse` app

#### Functional Requirements (6 FR)
- [x] **FR-WM-01** `MUST` — Tạo & quản lý kho vật lý (CRUD) — Tên kho, địa chỉ, dung tích, hoạt động
- [x] **FR-WM-02** `MUST` — Tạo & quản lý vị trí lưu trữ trong kho — Mã vị trí (Giá-A-01), dung tích
- [x] **FR-WM-03** `MUST` — Hiển thị real-time inventory theo kho, vị trí — Qty on-hand, available, reserved, quarantine (chờ model `Inventory`, mục 1f)
- [x] **FR-WM-04** `MUST` — Cảnh báo tồn < Min Level — Gợi ý tạo PO tự động (chờ model `Inventory`, mục 1f)
- [x] **FR-WM-05** `MUST` — Cảnh báo tồn > Max Level — Để lại ghi chú cho quản lý (chờ model `Inventory`, mục 1f)
- [x] **FR-WM-06** `SHOULD` — Hỗ trợ multi-warehouse chuyển vị trí — Stock transfer, intra-warehouse (chờ tồn kho thật, làm cùng Phase 3 GIN/FIFO)

> ⚠️ FR-WM-03/04/05 đã triển khai ở dashboard `inventory` app (mục 3a, Phase 3) — cảnh báo Min/Max chỉ hiển thị on-the-fly, CHƯA auto-tạo PO draft (dời Phase 5 khi có Celery).
> ✅ FR-WM-06 đã lên UI: `inventory` app có `transfer_create`/`transfer_list` (`inventory.services.transfer_stock`) — điều chuyển batch cùng kho (chỉ đổi vị trí, Inventory không đổi) hoặc khác kho (trừ/cộng `Inventory` 2 đầu qua `StockMovement` TRANSFER_OUT/TRANSFER_IN); batch bất biến vị trí, luôn tách batch mới ACTIVE tại đích (cùng cách `qc_partial_pass` tách batch), batch nguồn CLOSED/PARTIAL_USED tuỳ còn dư hay hết (BR-GIN-006 style). Ghi `AuditLog` qua `log_action`.

#### Business Rules
- [x] BR-WM-001: `qty_on_hand >= 0` (không cho âm) — thuộc model Inventory, chưa tồn tại (mục 1f)
- [x] BR-WM-002: `qty_available = qty_on_hand - qty_reserved` (auto calculate) — thuộc model Inventory, chưa tồn tại (mục 1f)
- [ ] BR-WM-003: Khi GIN issue → `qty_on_hand` giảm, `qty_available` giảm — thuộc Phase 3 (GIN)
- [ ] BR-WM-004: Khi GRN receive → `qty_on_hand` tăng, `qty_available` tăng — thuộc Phase 2 (GRN)
- [x] BR-WM-005: Tạo warehouse → min 10 vị trí (tự sinh 10 vị trí mặc định A-01..A-10 khi tạo kho)
- [x] BR-WM-006: Không thể xóa warehouse nếu `qty_on_hand > 0` — `warehouse.services.deactivate_warehouse()` kiểm tra `Inventory.qty_on_hand > 0` trước khi khoá (soft), raise `ValidationError` nếu còn tồn; `warehouse/views.py::warehouse_deactivate` gọi hàm này trong try/except
- [x] BR-WM-007: `Warehouse.warehouse_type` (MAIN/STAGING/SCRAP) — MAIN là kho thường (duy nhất loại được GIN/FIFO chọn xuất hàng); tối đa 1 kho STAGING (Kho chờ QC) và 1 kho SCRAP (Kho phế) đang hoạt động cho toàn công ty, enforce ở tầng DB (`UniqueConstraint` có điều kiện `warehouse_type IN (STAGING, SCRAP) AND is_active`) lẫn tầng form (`WarehouseForm.clean_warehouse_type`); `warehouse_type` khoá (disabled) sau khi tạo — đổi loại phải tạo kho mới + khoá kho cũ, không sửa tại chỗ
- [x] BR-WM-008: Kho chờ/Kho phế được track tồn kho **đầy đủ** như kho thường (Batch + Inventory thật, không phải ghi chú) — nhưng bị loại khỏi mọi KPI/báo cáo tổng hợp (`reports.services`: `dashboard_kpis`, `abc_analysis`, `slow_moving_items` đều lọc `warehouse__warehouse_type=MAIN`) và khỏi cảnh báo Min/Max (`inventory/views.py::inventory_list` — dòng vẫn hiển thị, chỉ không tính `below_min`/`above_max`/`suggested_po_qty` cho row không phải MAIN) để không thổi phồng số liệu tồn khả dụng

> ⏸️ **FR-WM-04 phần "gợi ý tạo PO tự động"**: chỉ cần hiển thị cảnh báo trên dashboard ở Phase 1 (tính on-the-fly khi load trang). Auto-tạo PO draft thật sự (cần job chạy nền) dời qua Phase 5 khi có Celery.

### 1c. [Bổ sung] Product / SKU Master Data — `catalog` app

*(Không có mã FR riêng trong SRS gốc — nhưng bắt buộc phải có trước khi làm GRN/PO/Inventory)*

- [x] Product model tối thiểu: `product_code`, `name`, `category`, `uom` (đơn vị tính), `min_level`, `max_level`
- [x] CRUD cơ bản (list, create, edit) — role MANAGER/ADMIN tạo/sửa, mọi user đã đăng nhập xem được (đồng nhất với warehouse); `is_active` sửa trực tiếp trên form (không tách khoá/mở riêng vì không có business rule như BR-WM-006)

### 1d. [Bổ sung] Supplier Master Data — `partners` app

*(Không có mã FR riêng trong SRS gốc — cần cho GRN, PO, Reporting supplier performance)*

- [x] Supplier model tối thiểu: `supplier_code`, `name`, `contact`, `lead_time_days` (cần cho FR-PO-05 sau này)
- [x] CRUD cơ bản — role MANAGER/ADMIN tạo/sửa, mọi user đã đăng nhập xem được (đồng nhất với catalog); `is_active` sửa trực tiếp trên form
- [x] **Mở rộng field (theo yêu cầu người dùng, không thuộc FR gốc)**: Supplier nâng từ 4 field tối thiểu lên đủ 5 nhóm nghiệp vụ — định danh (`international_name`, `supplier_group`), pháp lý & địa chỉ (`tax_code`, `registered_address`, `delivery_address`, `website`), người liên hệ (`contact_name`/`contact_title`/`contact_phone`/`contact_email`, thay cho field `contact` gộp cũ), vận hành mua hàng (thêm `payment_terms`, `credit_limit`, `currency`), hệ thống & quản trị (`status` 3 giá trị ACTIVE/INACTIVE/SUSPENDED thay cho `is_active` Boolean cũ, `internal_note`). Đã cập nhật mọi nơi filter `Supplier.is_active=True` sang `status=ACTIVE` (`purchasing/views.py`, `purchasing/services.py`, `receiving/views.py`). Có trang `supplier_detail` mới (`partners/views.py`), xếp 5 nhóm thành các card riêng theo đúng pattern `warehouse_detail.html`, kèm bảng "PO gần đây" tham chiếu NCC.

### 1e. [Bổ sung] PO Stub — `purchasing` app (tối thiểu, để GRN tham chiếu)

*(Giải quyết circular dependency: GRN cần `po_id` FK hợp lệ, nhưng PO đầy đủ chỉ làm ở Phase 5)*

- [x] PO model tối thiểu: `po_no`, `supplier_id` (FK), `status` (mặc định `SENT` để test được), `items` (`product_id`, `qty_ordered`, `unit_price`) — CRUD qua inline formset; phân quyền dùng RBAC thật (`user.can(action, 'po')`) vì 'po' có trong Permission Matrix: MANAGER/PURCHASING/ADMIN Create+Update, STAFF/QC/ACCOUNTANT chỉ Read
- [x] **Chưa làm ở đây:** workflow DRAFT→APPROVED, so sánh giá NCC, auto-suggestion, lead-time tracking — tất cả dời qua Phase 5
- [x] **[Bổ sung theo yêu cầu người dùng, không thuộc FR gốc] Yêu cầu mua hàng (PR) + 2 tab Purchasing**: thêm `PurchaseRequest`/`PurchaseRequestItem` (`request_no` tự sinh `PR-YYYYMM-XXX`, mirror `Grn.generate_grn_no()`) — nhân viên các phòng ban tạo PR nhiều dòng SKU, PR `APPROVED` mới convert được thành đúng 1 PO qua `po_create(?from_pr=<pk>)` (prefill toàn bộ dòng item từ PR, set `linked_po` sau khi tạo xong). Trang `purchasing/po_list.html` và `pr_list.html` có chung 1 thanh tab điều hướng (2 URL riêng `purchasing:pr_list`/`purchasing:po_list`) kèm badge số PR đang chờ duyệt tính on-the-fly.
- [x] **[Bổ sung theo yêu cầu người dùng 2026-07-26, không thuộc FR gốc] PR routed qua Approval + phạm vi xem + Supplier managed_by + PO auto-numbering**:
  - **Duyệt PR chuyển sang cơ chế `Approval` dùng chung với GRN submit/GIN confirm**: `PurchaseRequest` thêm `assigned_to` (FK User, tuỳ chọn — người tạo PR có thể chỉ định 1 nhân viên phòng Mua hàng cụ thể xử lý; để trống thì `create_approval` báo cả phòng). Tạo PR xong tự nộp thẳng vào `Approval(department=PURCHASING)` qua `purchasing.services.submit_purchase_request` (không có state DRAFT riêng — tạo PR tức là nộp). Bỏ quyền `approve` PR khỏi role PURCHASING nói chung trong `ROLE_PERMISSIONS` (kèm migration `accounts/migrations/0012_reseed_purchasing_pr_permissions.py` re-seed user đã tồn tại) — giờ chỉ quản lý phòng Mua hàng (`user.is_department_manager('PURCHASING')`) hoặc Manager/Admin (`can('approve','pr')`, giữ nguyên fallback cũ) mới duyệt/từ chối được qua `purchasing.services.decide_purchase_request`, kể cả khi PR chỉ định đúng người đó (`assigned_to` chỉ mang tính thông báo/hiển thị). Vẫn giữ 2 lớp kiểm soát tách biệt: duyệt PR khác duyệt PO thật (Manager/Admin).
  - **Phạm vi xem PR**: `pr_list`/`pr_detail` — nhân viên phòng khác chỉ xem được PR do chính mình tạo (`requested_by`); nhân viên/quản lý phòng Mua hàng (role PURCHASING) và Manager/Admin xem toàn bộ PR (cần bức tranh tổng để xử lý/duyệt). Truy cập trực tiếp qua URL cũng bị chặn tương tự (không chỉ ẩn ở list).
  - **Supplier `managed_by`**: role PURCHASING giờ được tạo Supplier (trước đây chỉ Manager/Admin) — NCC tạo ra tự gán `managed_by` = người tạo; PURCHASING chỉ sửa được đúng NCC do chính mình tạo (`partners.views.can_edit_supplier`), Manager/Admin không đổi (toàn quyền mọi NCC như cũ).
  - **PO `po_no` tự sinh**: `PurchaseOrder.generate_po_no()` sinh mã `PO-XXXX` tăng dần toàn hệ thống (không theo tháng, khác PR), field đổi `editable=False` và bỏ khỏi `PurchaseOrderForm` — không còn nhập tay, tránh trùng mã.
  - **[Fix bug 2026-07-27] Backfill `Approval` cho PR PENDING cũ**: đợt đổi trên chỉ sửa code đường đi tiếp (`pr_create`/`pr_approve`/`pr_reject`), không kèm migration backfill — mọi `PurchaseRequest` tạo/còn `PENDING` từ TRƯỚC khi đổi (không có `Approval` nào) bị kẹt vĩnh viễn: UI vẫn hiện "Chờ duyệt" nhưng `pr_approve`/`pr_reject` luôn raise `ValidationError` vì `latest_approval_for()` trả `None`. Vá bằng `purchasing/migrations/0008_backfill_pr_approval.py` (data migration, tạo 1 `Approval(status=PENDING, department=PURCHASING, submitted_by=requested_by)` cho mỗi PR PENDING chưa có Approval, `submitted_at` set lại = `created_at` của PR). Đã verify: 9 PR PENDING hiện có đều có `Approval` PENDING hợp lệ sau migration.

### 1f. [Bổ sung] Inventory & Batch — `inventory` app (chỉ tạo schema, chưa làm logic)

*(GRN ở Phase 2 cần model này tồn tại để ghi `qty_on_hand`/tạo Batch — logic FIFO/EOQ/alert đầy đủ làm ở Phase 3)*

- [x] Inventory model: `product_id` (FK), `warehouse_id` (FK), `qty_on_hand`, `qty_reserved`, `qty_available` (computed) — unique per (product, warehouse); `qty_available` là property, không lưu cột riêng. Không có cột `qty_quarantine` riêng — từ khi tách kho theo `warehouse_type` (Phase 3, xem CLAUDE.md), số lượng quarantine đã có sẵn qua dòng `Inventory` của kho SCRAP nên field cũ (thiết kế trước khi có 3-loại-kho) đã bị xoá để tránh trùng state với `qty_on_hand` của kho SCRAP.
- [x] Batch model: `product_id` (FK), `batch_code`, `mfg_date`, `exp_date`, `qty_received`, `qty_used`, `qty_available` (computed), `status` (enum: ACTIVE/PARTIAL_USED/QUARANTINE/EXPIRED/CLOSED, mặc định ACTIVE), `supplier_id` (FK), `location_id` (FK) — chưa có view/form CRUD, chưa có transition logic (dời Phase 2/3), chỉ model + admin

### ✅ Definition of Done — Phase 1
- [x] Login được, phân quyền theo 6 role hoạt động đúng permission matrix — verify sống qua `runserver` (2026-07-24): 7 user test (1 superuser + 6 role) login thành công (POST /login/ → 302); ma trận GET status trên user_list, warehouse/product/supplier/PO list+create khớp 100% permission matrix (ADMIN/superuser full quyền; MANAGER thêm được Warehouse/Product/Supplier/PO; PURCHASING chỉ thêm được PO; STAFF/QC/ACCOUNTANT chỉ Read). User/data verify đã xoá sau khi xong.
- [x] Tạo được Warehouse + Location + Product + Supplier + 1 PO stub qua UI/admin — verify sống qua `runserver` (2026-07-24): tạo thành công cả 5 qua POST form thật (không phải admin) với role MANAGER — Warehouse (tự sinh 10 Location theo BR-WM-005) + 1 Location thủ công, Product, Supplier, PO kèm 1 dòng item — đã kiểm chứng lại qua DB rồi xoá (dữ liệu test tạm).
- [x] `qty_available = qty_on_hand - qty_reserved` tính đúng (unit test) — `TC-INV-001-001` (Inventory), `TC-INV-002-001` (Batch: qty_received - qty_used)

---

## PHASE 2 — Nhập Hàng: GRN + QC (Tuần 3-6)

*(Gộp chung vì QC là 1 phần workflow của GRN — đây là quy trình đã phân tích chi tiết ở phần trước của cuộc trò chuyện)*

### 2a. Phiếu Nhập (GRN) — `receiving` app

#### Functional Requirements (7 FR)
- [x] **FR-GRN-01** `MUST` — Tạo GRN với đầy đủ thông tin (Mã tự động, Ngày, NCC, PO, Chi tiết SKU)
- [x] **FR-GRN-02** `MUST` — Workflow GRN: DRAFT → PENDING_QC → RECEIVED → CLOSED — đầy đủ mọi transition (`receiving/services.py`: `submit_to_pending_qc`, `close_grn`; `quality/services.py`: `start_qc`, `qc_pass`/`qc_fail`/`qc_partial_pass`); GRN REJECTED chỉ `close_grn` được sau khi GRN_RETURN liên quan đã RETURNED/CLOSED
- [x] **FR-GRN-03** `MUST` — Khi GRN RECEIVED (QC pass), tự động tạo batch & cập nhật inventory tăng
- [x] **FR-GRN-04** `MUST` — Support partial GRN (nhận 1 phần, chờ phần còn lại)
- [x] **FR-GRN-05** `MUST` — Ghi lại ký nhận từ: Mua hàng, QC, Kho (audit trail) — dùng `accounts.AuditLog` (GenericForeignKey) chung, ghi actor+action ở mọi transition; chưa có bảng chữ ký riêng biệt theo 3 vai trò
- [x] **FR-GRN-06** `MUST` — In phiếu GRN với barcode để dán trên hàng
- [x] **FR-GRN-07** `SHOULD` — So sánh Qty GRN vs Qty PO để alert nếu vượt

#### Workflow States
- [x] State `DRAFT`: form tạo GRN, nút Save/Submit/Cancel
- [x] State `PENDING_QC`: nhân viên Kho nhập Qty thực tế nhận, nút Submit to QC
- [x] State `QC_IN_PROGRESS`: QC nhập kết quả Pass/Fail/Partial, nút Approve/Reject
- [x] State `RECEIVED` (Pass): auto tạo Batch + cập nhật inventory tăng, khóa edit
- [x] State `REJECTED` (Fail): auto tạo GRN_RETURN
- [x] State `CLOSED`: archive — `close_grn` (`receiving/services.py`), view `grn_close`, nút "Đóng GRN" ở `grn_detail.html` (quyền `approve` trên module `grn`)
- [x] Transition test: DRAFT → PENDING_QC → QC_IN_PROGRESS → RECEIVED/REJECTED → CLOSED — `GrnCloseServiceTest` (`receiving/tests.py`)

#### Data Model (tham khảo — bảng grn/grn_items)
- [x] `grn`: id, grn_no (auto GRN-YYYYMM-XXX), po_id (FK), supplier_id (FK), grn_date, status, created_by (FK User), created_at
- [x] `grn_items`: id, grn_id (FK), product_id (FK), qty_ordered, qty_received, mfg_date, exp_date, batch_code, unit_price, status

#### Quantity Handling & Edge Cases
- [x] BR-GRN-001: `qty_received <= qty_ordered` — validated tại `receiving/models.py:104`
- [x] BR-GRN-006: `exp_date > mfg_date` (luôn đúng) — validated tại `receiving/models.py:107`, test `TC-GRN-005-001`
- [x] BR-GRN-007: `qty_received = 0` → status = RECEIVED nhưng qty không tăng — thỏa mãn tự nhiên qua logic hiện có (Batch/`_credit_inventory` cộng đúng qty_received, bằng 0 thì không tăng)
- [x] **Qty tolerance check**: Alert nếu chênh lệch > tolerance % (config per supplier)
- [x] **Qty validation**: `qty_grn <= qty_po` (không cho nhập vượt quá PO)

### 2b. Quality Control (QC) — `quality` app

#### Functional Requirements (6 FR)
- [x] **FR-QC-01** `MUST` — Tạo quy trình QC: PENDING_QC → PASS/FAIL/PARTIAL_PASS
- [x] **FR-QC-02** `MUST` — Định nghĩa tiêu chuẩn QC (Ngoại hình, Trọng lượng, Màu sắc, Seal integrity)
- [x] **FR-QC-03** `MUST` — Nhập kết quả từng tiêu chuẩn: PASS / FAIL
- [x] **FR-QC-04** `MUST` — Kết quả FAIL → Ghi chú lý do & tạo GRN trả lại
- [x] **FR-QC-05** `MUST` — Kết quả PASS → GRN được approve, tạo batch & cập nhật inventory
- [x] **FR-QC-06** `SHOULD` — Hỗ trợ upload hình ảnh evidence

#### QC Criteria & Sampling
- [x] **Sampling method**: % based (default 10%) hoặc fixed qty (config per SKU) — `Product.qc_sampling_method`/`qc_sampling_value` (`catalog/models.py`), gợi ý cỡ mẫu qua `quality.services.suggested_sample_qty()`, hiện ở `qc_result.html`; chỉ gợi ý, không chặn quyết định PASS/FAIL/PARTIAL
- [x] **QC Criteria master data**: per category (Bột mỳ, Đường...), mỗi criteria có name/pass_rule/fail_rule, cho phép upload ảnh reference
- [x] **Result tracking**: mỗi item trong sample ghi PASS/FAIL từng criteria, không chỉ overall result
- [x] **QC duration tracking**: log start/end time (`QcInspection.started_at`/`completed_at` có sẵn), alert nếu > SLA (24h) — `quality.services.overdue_inspections()`, ⏸️ tính on-the-fly khi load trang `receiving:grn_list` (banner) + badge riêng ở `quality:qc_result`, chưa cần Celery
- [x] **QC approval override**: Supervisor (Manager/Admin — permission riêng `can_override_qc`, KHÔNG phải QC Inspector) override kết quả (ghi chú lý do) qua `quality:qc_override`, ghi `QcInspection.override_note`/`overridden_by`/`overridden_at` + AuditLog — **alert-only/annotation-only** (đã chốt với user): KHÔNG đảo ngược Batch/Inventory đã tạo bởi qc_pass/qc_fail/qc_partial_pass

### 2c. GRN ↔ QC Integration & Batch Lifecycle (dùng chung cho cả 2)

- [x] **Kho chờ (STAGING) là điểm vào bắt buộc**: `start_qc()` (gọi từ `receiving/views.py::grn_receive_qty` khi xác nhận Qty thực nhận) tạo ngay 1 Batch `ACTIVE` cho mỗi `GrnItem` tại vị trí mặc định của Kho chờ và cộng `Inventory` ở đó (RECEIPT) — hàng vật lý đã nhận hiện diện thật trong tồn kho từ lúc này, không "biến mất" cho tới khi QC quyết định
- [x] **QC result mapping** (đều tiêu thụ batch ở Kho chờ qua `inventory.services.move_batch_qty`, ghi `TRANSFER_OUT`/`TRANSFER_IN`, không phải RECEIPT):
  - PASS → GRN: RECEIVED, tách toàn bộ batch Kho chờ thành 1 Batch `ACTIVE` mới tại kho MAIN đích do QC chọn
  - FAIL → GRN: REJECTED, tạo GRN_RETURN, tách toàn bộ batch Kho chờ thành 1 Batch `QUARANTINE` tại Kho phế (SCRAP) — inventory Kho chờ giảm về 0, inventory Kho phế tăng đúng qty
  - PARTIAL_PASS → GRN: RECEIVED, tách batch Kho chờ làm 2: `ACTIVE` (qty pass) tại kho MAIN + `QUARANTINE` (qty fail) tại Kho phế
  (`quality/services.py`, xem `start_qc`/`qc_pass`/`qc_fail`/`qc_partial_pass`)
- [x] **Batch.grn_item** (FK nullable, `on_delete=PROTECT`, `inventory/models.py`): trace batch về đúng `GrnItem` sinh ra nó; copy sang batch con mỗi lần `move_batch_qty` tách, giữ lineage Kho chờ → MAIN/SCRAP qua nhiều lần tách
- [x] **Batch status enum**: ACTIVE, PARTIAL_USED, QUARANTINE, EXPIRED, CLOSED — định nghĩa ở `inventory` app
- [x] `qc_pass`/`qc_partial_pass` chỉ nhận vị trí đích thuộc kho `warehouse_type=MAIN` (`ValidationError` nếu không), `inventory.services.transfer_stock` chặn điều chuyển thủ công có nguồn là Kho chờ (phải qua QC) — 2 hàng rào giữ đúng ý nghĩa "phải qua QC"
- [x] **Quarantine batch — không thể xuất**: GIN chỉ FIFO chọn batch `status=ACTIVE` tại kho `MAIN`, `GinForm`/`Gin.clean()` chặn kho STAGING/SCRAP — QUARANTINE (luôn nằm ở Kho phế/SCRAP) tự động bị loại (test riêng, xem mục Phase 3 GIN)
- [x] **Quarantine batch — alert > 7 ngày**: `inventory.services.stale_quarantine_batches()`, ⏸️ tính on-the-fly khi load trang `inventory:batch_list` (banner + badge từng dòng) và `inventory:batch_detail`, chưa cần Celery
- [ ] **Quarantine batch — disposition scrap/return/rework**: admin thao tác xử lý lô Quarantine (scrap hẳn/trả NCC/tái chế) — **chưa làm, ngoài phạm vi round này** (đã chốt với user: chỉ làm alert trước)

#### GRN_RETURN Workflow (tự động tạo từ QC FAIL)
- [x] State: PENDING → APPROVED → RETURNED → CLOSED — `approve_return`/`mark_return_returned`/`close_return` (`receiving/services.py`), view + nút "Duyệt"/"Xác nhận đã trả"/"Đóng" ở `grn_detail.html` (duyệt/đóng cần quyền `approve`; xác nhận đã trả chỉ cần `update` — STAFF làm được)
- [x] Link tới GRN gốc (ref field), reason auto-fill "QC Fail" — `quality/services.py:103,115`
- [ ] ⏸️ Auto-email supplier khi reject — **hoãn** (cần Celery/email async); tạm thời: hiện thông báo trong app, gửi email thủ công

#### Inventory Update Triggers (transaction, atomicity)
- [x] **Submit QC transaction** (`start_qc`): Create Batch ACTIVE/item tại Kho chờ → cộng Inventory Kho chờ (RECEIPT) → GRN status QC_IN_PROGRESS → Audit log — all-or-nothing (`@transaction.atomic` + `select_for_update`)
- [x] **QC PASS transaction** (`qc_pass`): tách batch Kho chờ → Batch ACTIVE tại MAIN qua `move_batch_qty` → trừ Inventory Kho chờ/cộng Inventory MAIN (TRANSFER_OUT/TRANSFER_IN) → GRN status RECEIVED → Audit log
- [x] **QC FAIL transaction** (`qc_fail`): tách batch Kho chờ → Batch QUARANTINE tại Kho phế qua `move_batch_qty` → trừ Inventory Kho chờ/cộng Inventory Kho phế → Create GRN_RETURN → GRN status REJECTED → Audit log (không còn "không đụng Inventory" như trước M4 — hàng FAIL giờ có mặt thật ở Kho phế)
- [x] **PARTIAL_PASS transaction** (`qc_partial_pass`): tách batch Kho chờ làm 2 lần `move_batch_qty` (ACTIVE tại MAIN cho qty pass, QUARANTINE tại Kho phế cho qty fail) → cập nhật Inventory cả 2 đầu đích, Kho chờ về 0 → GRN status RECEIVED

#### Audit Trail (bắt buộc, khó thêm sau)
- [x] Ghi WHO/WHEN/WHAT/WHY cho mọi state transition của GRN, QC, Batch — qua `accounts.AuditLog` (`log_action()`), gọi ở tạo GRN, submit PENDING_QC, start QC, QC PASS/FAIL/PARTIAL
- [x] Ví dụ: `2026-07-16 10:30 | User#5 (QC) | GRN#001 | QC_IN_PROGRESS→RECEIVED | "All PASS"` — đúng hình dạng dữ liệu hiện lưu (actor/action/reason)

### ✅ Definition of Done — Phase 2
- [x] Test đầy đủ 1 vòng: tạo GRN từ PO stub → nhập Qty thực tế → QC PASS → Batch tạo tự động → Inventory tăng đúng số — `test_TC_QC_PASS_001_001_creates_active_batch_and_credits_inventory` (`quality/tests.py:127`)
- [x] Test đường FAIL: QC FAIL → GRN_RETURN tạo, Inventory KHÔNG đổi (unit test riêng, đây là chỗ dễ sai) — `test_TC_QC_FAIL_001_001_creates_return_rejects_grn_no_inventory_change` (`quality/tests.py:154`)
- [x] Test đường PARTIAL_PASS: 2 batch tạo đúng, Inventory chỉ cộng phần pass — `test_TC_QC_PARTIAL_001_001_splits_batch_and_credits_only_passed_qty` (`quality/tests.py:181`)
- [ ] Manual UAT theo Test Case (xem Phụ lục B) nếu chưa có FSD chi tiết cho GRN/QC — chưa chạy UAT thủ công sống qua `runserver`

---

## PHASE 3 — Tồn Kho Real-time + Xuất Hàng (GIN/FIFO) (Tuần 7-10)

### 3a. Inventory Management (hoàn thiện logic) — `inventory` app

#### Functional Requirements (5 FR)
- [x] **FR-INV-01** `MUST` — Tạo & quản lý lô hàng (batch) với: Mã lô/NSX/HSD/Nhà cung cấp; Qty received/used/available; Status: ACTIVE, PARTIAL_USED, CLOSED, EXPIRED, QUARANTINE
- [x] **FR-INV-02** `MUST` — Cảnh báo lô sắp hết hạn (< 30 ngày)
- [x] **FR-INV-03** `MUST` — Theo dõi lịch sử chuyển động tồn kho (audit trail) — `inventory/services.py::record_movement` + model `StockMovement`, gọi từ QC pass/partial-pass, GIN issue, Stock Opname adjustment, và stock transfer; test `StockMovementServiceTest` (`inventory/tests.py`)
- [x] **FR-INV-04** `MUST` — Hỗ trợ FIFO/LIFO logic khi xuất hàng — `inventory/services.py::suggest_fifo_batches` (dùng bởi `shipping.services.start_picking`); test `FifoSuggestionServiceTest` (`inventory/tests.py`)
- [x] **FR-INV-05** `SHOULD` — Tính toán EOQ (Economic Order Quantity)

#### Batch → Inventory Link
- [ ] `Inventory.qty_on_hand` phản ánh tổng batch vật lý còn trong kho (kể cả EXPIRED — hàng vẫn nằm đó); nhưng FIFO/GIN chỉ được chọn batch `status IN ('ACTIVE', 'PARTIAL_USED')`
- [x] Batch `QUARANTINE` tính riêng, KHÔNG cộng vào `qty_available` — không dùng cột `qty_quarantine` riêng (đã xoá, xem mục 1f), thay vào đó `move_batch_qty()` chuyển batch QUARANTINE sang kho SCRAP nên nó chỉ nằm trong `Inventory(warehouse=SCRAP).qty_on_hand`, tách biệt hoàn toàn khỏi `qty_available` của kho MAIN

> ✅ FR-INV-01/02 đã lên UI: `inventory` app có `batch_list`/`batch_detail` (danh sách + chi tiết lô, kèm lịch sử `StockMovement`) và banner cảnh báo lô `ACTIVE` sắp hết hạn (`expiring_soon_batches()`, tính on-the-fly mỗi lần load trang — ⏸️ chưa cần Celery/cron).
> ✅ FR-INV-05 (EOQ) đã lên UI: `inventory/services.py::calculate_eoq` (D = tổng qty ISSUE 365 ngày qua từ `StockMovement`, S/H từ 2 field mới `Product.ordering_cost`/`holding_cost_rate` cộng đơn giá bình quân từ lịch sử `PurchaseOrderItem.unit_price`) — view `inventory:product_eoq`, link "Tính EOQ" ở `inventory_list.html`. Thiếu dữ liệu thì hiển thị rõ lý do, không tự suy đoán giá trị mặc định. Test `EoqServiceTest`/`EoqViewTest` (`inventory/tests.py`).

### 3b. Phiếu Xuất (GIN) — `shipping` app

#### Functional Requirements (7 FR)
- [x] **FR-GIN-01** `MUST` — Tạo GIN từ yêu cầu xuất hàng (Ref PO, Sản xuất, Bán hàng)
- [x] **FR-GIN-02** `MUST` — Tự động gợi ý lô FIFO (sắp hết hạn nhất)
- [x] **FR-GIN-03** `MUST` — Allow overwrite batch selection nếu cần
- [x] **FR-GIN-04** `MUST` — Khi GIN issued, tự động cập nhật inventory & batch quantity
- [x] **FR-GIN-05** `MUST` — Ghi lại Qty thực tế xuất vs Qty yêu cầu (có thể khác)
- [x] **FR-GIN-06** `MUST` — In phiếu xuất & barcode để kiểm soát
- [x] **FR-GIN-07** `SHOULD` — Gợi ý kho/vị trí có hàng dựa trên logic

#### Workflow States
- [x] State `DRAFT`: chọn SKU/Qty, hệ thống suggest lô FIFO
- [x] State `PICKING`: quét barcode batch để confirm, cho phép đổi batch (ghi lý do)
- [x] State `ISSUED`: cập nhật `qty_on_hand -= qty_issued`, `batch.qty_used += qty_issued`, khóa edit
- [x] State `CLOSED`: archive

#### FIFO Algorithm — ⚠️ phần dễ sai nhất, BẮT BUỘC có unit test riêng
- [x] Query: `SELECT * FROM batch WHERE product_id=? AND qty_available>0 AND status IN ('ACTIVE','PARTIAL_USED') ORDER BY exp_date ASC, created_at ASC` (bug fix 2026-07-27: trước đó chỉ lọc `ACTIVE`, khiến lô đã xuất một phần còn tồn không bao giờ được FIFO chọn lại — xem CLAUDE.md)
- [x] Duyệt batch, lấy đủ `qty_needed` (có thể lấy từ nhiều batch nếu 1 batch không đủ)
- [x] Trả về list `{batch_id, qty_to_issue, exp_date, location}`
- [x] Edge case: không đủ hàng ở mọi batch cộng lại → error rõ ràng, không cho issue
- [x] BR-GIN-001: `qty_issued <= qty_available`
- [x] BR-GIN-006: khi `qty_on_hand` = 0 → `batch.status = CLOSED`
- [x] BR-GIN-007: `exp_date < today` → warning "Batch expired", GIN không được lấy batch EXPIRED/QUARANTINE (`sync_expired_batches` quét cả `ACTIVE` và `PARTIAL_USED`)
- [x] BR-GIN-008: GIN chỉ được chọn kho `warehouse_type=MAIN` (`GinForm.warehouse` giới hạn queryset + `Gin.clean()` chặn ở tầng model, dropdown filter ở `gin_list` cùng convention) — bắt buộc vì FIFO chỉ lọc `status IN ('ACTIVE','PARTIAL_USED')`, không tự loại được batch đang nằm ở Kho chờ (STAGING) dù batch đó cũng cùng status; ràng buộc kho là lớp chặn duy nhất

### ✅ Definition of Done — Phase 3
- [x] Unit test FIFO: nhiều batch cùng SKU, hạn khác nhau → lấy đúng thứ tự hạn gần nhất trước
- [x] Unit test edge case: 1 batch không đủ → tự động lấy tiếp batch kế, tổng đúng `qty_needed`
- [x] Unit test: không đủ hàng toàn bộ batch → trả lỗi rõ ràng, không cho issue âm
- [x] GIN không thể chọn batch QUARANTINE/EXPIRED (test riêng)

---

## PHASE 4 — Kiểm Kê (Stock Opname) (Tuần 11-13)

### Functional Requirements (7 FR)
- [x] **FR-SO-01** `MUST` — Tạo phiếu kiểm kê, chọn kho/vị trí, lập danh sách SKU
- [x] **FR-SO-02** `MUST` — Web form để nhân viên kho quét barcode SKU & nhập Qty thực tế
- [x] **FR-SO-03** `MUST` — Tự động so sánh Qty hệ thống vs Qty kiểm kê, tính chênh lệch
- [x] **FR-SO-04** `MUST` — Ghi chú lý do chênh lệch: Loss, Damage, Theft, Counting Error, Expired
- [x] **FR-SO-05** `MUST` — Tự động tạo Adjustment phiếu nếu chênh lệch > 0
- [x] **FR-SO-06** `MUST` — Báo cáo chi tiết phiếu kiểm kê (theo dõi từng dòng)
- [x] **FR-SO-07** `SHOULD` — Hỗ trợ kiểm kê từng vị trí hoặc toàn kho

### Workflow Phases
- [x] Phase `PLANNING`: chọn kho/vị trí cần kiểm, status = DRAFT
- [x] Phase `EXECUTION`: form quét barcode, nhập Qty thực tế, hiện Qty hệ thống vs Qty quét
- [x] Phase `RECONCILIATION`: so sánh `qty_system` vs `qty_actual`, tính variance
- [x] Phase `ADJUSTMENT`: auto tạo Adjustment phiếu, cập nhật inventory
- [x] UI color highlight: xanh (match) / vàng (variance ≤5) / đỏ (variance >5)

### ✅ Definition of Done — Phase 4
- [x] Test: chênh lệch dương/âm đều tạo đúng Adjustment, Inventory cập nhật đúng chiều

---

## PHASE 5 — Purchase Order Đầy Đủ (Tuần 14-16)

*(Nâng cấp PO stub đã tạo ở Phase 1 thành workflow đầy đủ)*

### Functional Requirements (6 FR)
- [x] **FR-PO-01** `MUST` — Workflow PO: DRAFT → APPROVED → SENT → PARTIAL_RECEIVED → RECEIVED → CLOSED — `purchasing/services.py::approve_po/send_po/close_po` + `sync_po_status` (tự động PARTIAL_RECEIVED/RECEIVED); không có auto-approve theo ngưỡng tiền, mọi PO đều cần Manager/Admin duyệt thủ công (quyết định chốt với user)
- [x] **FR-PO-02** `MUST` — Khi tồn < Min Level, gợi ý tạo PO tự động — MVP không Celery: nút "Tạo yêu cầu mua hàng" ở dòng dưới Min Level trên `inventory_list.html`, prefill sản phẩm + Qty gợi ý + kho (`?product=&qty=&warehouse=`) vào `pr_create` (đánh dấu `PurchaseRequest.origin=MIN_LEVEL`); PO thật chỉ tạo sau khi PR được duyệt qua `po_create?from_pr=<pk>` — bỏ lối tắt tạo PO thẳng từ gợi ý (2026-07-28, mọi PO phát sinh từ Min Level giờ đều qua PR/duyệt, không bỏ qua bước duyệt như trước)
- [x] **FR-PO-03** `MUST` — So sánh giá từ nhiều NCC để chọn optimal — `purchasing/services.py::supplier_price_history` + view/trang `po_price_comparison`
- [x] **FR-PO-04** `MUST` — GRN phải tham chiếu PO để đối soát qty — đã có từ Phase 1e/2 (`sync_po_status`, `GrnForm.po` giới hạn PO status SENT/PARTIAL_RECEIVED), chỉ bổ sung hiển thị Qty đã nhận/còn lại ở `po_detail`
- [x] **FR-PO-05** `MUST` — Theo dõi lead-time từng NCC — `purchasing/services.py::supplier_lead_time_stats` (so sánh `Supplier.lead_time_days` cấu hình với lead-time thực tế tính từ `created_at`→`received_at`), trang `po_supplier_performance`
- [x] **FR-PO-06** `MUST` — Tracking delivery status: On time, Delayed, Partial — `PurchaseOrder.delivery_status()`, hiển thị badge ở `po_list`/`po_detail`

### Workflow States
- [x] State `DRAFT`: nhập Supplier, SKU items, qty, price, delivery date (`expected_delivery_date`)
- [x] State `APPROVED`: Manager duyệt (`approve_po`, quyền `approve` — không có nhánh auto-approve theo ngưỡng tiền)
- [x] State `SENT`: gửi PO tới NCC (`send_po`), khóa edit (`po_update` chặn khi status != DRAFT)
- [x] State `PARTIAL_RECEIVED` / `RECEIVED`: nhập Qty received từ GRN (tự động qua `sync_po_status`, đã có từ Phase 2)
- [x] State `CLOSED`: archive (`close_po`, từ SENT/PARTIAL_RECEIVED/RECEIVED)

### PO ↔ GRN Reconciliation
- [x] PO line item: `qty_ordered`, `qty_grn_received`, `qty_remaining` (= ordered - received) — tính on-the-fly ở `po_detail` (không lưu cột riêng, cùng convention `qty_available`)
- [x] GRN nhận partial → auto-update `qty_received`/`qty_remaining`, PO state chuyển PARTIAL_RECEIVED/RECEIVED tương ứng — `sync_po_status` (đã có từ Phase 2)
- [x] Alert nếu `sum(Qty GRN) > Qty PO` — đã làm ở Phase 2 (FR-GRN-07: `BaseGrnItemFormSet` chặn cứng + `tolerance_alerts` cảnh báo mềm), không làm lại
- [x] Overdue tracking: alert nếu `expected_delivery < today` & PO vẫn SENT/PARTIAL_RECEIVED (⏸️ tính on-the-fly khi load PO list, chưa cần Celery) — banner "N PO trễ hạn" ở `po_list`, badge `delivery_status` mã `DELAYED`

### Auto-PO Suggestion — ⏸️ HOÃN (cần Celery), làm bản rút gọn trước
- [x] **MVP không Celery**: hiển thị danh sách "SKU dưới Min Level" trên dashboard (Phase 1 đã có), nút "Tạo PO draft" bấm thủ công từ đó
- [ ] **Bản đầy đủ (khi có Celery)**: job chạy hourly tự tạo PO DRAFT (supplier chính, qty = max_level - qty_on_hand, giá lần cuối, delivery = today + lead_time), Purchasing vẫn phải duyệt trước khi SEND

### ✅ Definition of Done — Phase 5
- [x] Test: tạo PO → gửi → nhận GRN partial 2 lần → PO tự chuyển RECEIVED khi đủ qty — `SyncPoStatusTest` (`purchasing/tests.py`, đã có từ Phase 2, verify lại không đổi)
- [x] Test: GRN reject không làm giảm `qty_remaining` của PO — `test_TC_PUR_SYNC_004_rejected_item_excluded_from_total` (`purchasing/tests.py`)
- [x] PR/PO tabs (bổ sung, xem mục 1e): `PurchaseRequestCrudTest` + `PoCreateFromPrTest` (`purchasing/tests.py`, 12 test) — verify sống qua `runserver` (2026-07-25): STAFF tạo PR 2 dòng → không thấy nút Duyệt/Từ chối, `pr_approve` trả 403; PURCHASING duyệt PR (302) → thấy nút "Tạo PO từ yêu cầu này" → `po_create?from_pr=` prefill đúng 2 dòng → tạo PO xong, `PurchaseRequest.linked_po` gán đúng, `pr_detail` hiện link PO; nhánh Từ chối (Manager, kèm lý do) hiển thị đúng, ẩn nút "Tạo PO"; PURCHASING vẫn bị chặn 403 ở `po_approve` (giữ 2 lớp kiểm soát PR/PO tách biệt). Dữ liệu test đã xoá sau khi verify. Full suite: 480 test, tất cả pass.

---

## PHASE 6 — Reporting & Analytics (Tuần 17-19)

### Functional Requirements (6 FR)
- [x] **FR-RPT-01** `MUST` — Dashboard KPI: Tổng giá trị tồn, Số SKU, Tồn sắp hết hạn, PO chưa nhận
- [x] **FR-RPT-02** `MUST` — Báo cáo ABC Analysis (Pareto 80-20)
- [x] **FR-RPT-03** `MUST` — Báo cáo tồn lỏng (SKU chưa bán > 180 ngày)
- [x] **FR-RPT-04** `MUST` — Báo cáo hiệu suất NCC: % đúng hạn, % QC pass, avg price
- [x] **FR-RPT-05** `MUST` — Export báo cáo sang Excel, PDF
- [x] **FR-RPT-06** `SHOULD` — ⏸️ Scheduling reports gửi email hàng tuần — **hoãn** (cần Celery), tạm thời: nút "Export" thủ công

### Báo cáo cụ thể
- [x] Dashboard KPI: Inventory Value, SKU count, Low stock, Near expiry
- [x] ABC Analysis (Pareto 80-20: A/B/C theo giá trị tồn)
- [x] Slow-moving items (SKU không xuất >180 ngày)
- [x] Supplier Performance (On-time %, Quality %, Avg price)

---

## PHASE 7 — API & Integration (Ongoing / Optional — hạ ưu tiên)

*(Vì UI chính dùng Django Template + HTMX, không phải React SPA, DRF/API đầy đủ **không phải đường chính** để dùng app. Chỉ làm khi thật cần.)*

### Functional Requirements (5 FR) — tất cả có thể defer
- [ ] **FR-API-01** `SHOULD` — RESTful API trên DRF (chỉ khi cần: app di động, tích hợp ngoài)
- [ ] **FR-API-02** `SHOULD` — Authentication: JWT token (nếu có API)
- [ ] **FR-API-03** `COULD` — Rate limiting
- [ ] **FR-API-04** `COULD` — Support tích hợp ERP/SAP
- [ ] **FR-API-05** `COULD` — Webhook support

> 💡 Nếu cần 1 endpoint JSON nhỏ sớm hơn (ví dụ: FIFO suggestion cho HTMX gọi async), làm 1 Django view trả JSON đơn giản — không cần dựng cả bộ DRF/JWT cho việc này.

#### API Endpoints tham khảo (nếu làm)
- [ ] `POST /api/v1/grn/` — Tạo GRN từ PO
- [ ] `PATCH /api/v1/grn/{id}/submit-to-qc/`
- [ ] `PATCH /api/v1/grn/{id}/qc-pass/` / `qc-fail/`
- [ ] `GET /api/v1/products/{id}/batch-suggestion/?qty_needed=` — FIFO suggestion
- [ ] `POST /api/v1/gin/` / `PATCH /api/v1/gin/{id}/issue/`
- [ ] `GET /api/v1/inventory/{sku_id}/`

---

## PHASE 8 — UAT Solo + Polish (Tuần 20-22)

- [ ] Chạy toàn bộ Test Case (xem Phụ lục B) như checklist UAT thủ công
- [ ] Đối chiếu lại **BRD Success Criteria** (xem Phụ lục C) — có đang đúng hướng không?
- [ ] Review lại Unit test cho các phần logic quan trọng: FIFO, `qty_available` calculation, QC pass/fail flow (KHÔNG cần chạy theo coverage 80% như SRS gốc ghi — không thực tế cho solo)
- [ ] Polish UI/UX các màn hình dùng nhiều nhất: GRN, QC, GIN
- [x] Audit toàn bộ UI tiếng Việt (2026-07-25, làm sớm hơn lịch Phase 8 vì phát hiện qua bug report): `LANGUAGE_CODE='vi'` +
  `verbose_name` tiếng Việt cho mọi field model (10 app) + dịch nốt `User.Role`/`ACTIONS`/`MODULES`/button/breadcrumb/flash
  message còn sót tiếng Anh. Quy ước bắt buộc cho code mới ghi ở `CLAUDE.md` § "Frontend language convention" — không cần
  nhắc lại mỗi lần.

## PHASE 9 — Deploy Production + Buffer (Tuần 23-24)

- [ ] Docker hóa (đã hoãn từ Phase 0, làm ở đây)
- [ ] Setup backup hàng ngày (NFR-REL-02)
- [ ] Review audit log đầy đủ cho mọi CRUD action (NFR-SEC-08)
- [ ] Buffer cho phát sinh

---

## 📎 Phụ Lục A: Non-Functional Requirements (tham khảo, không tính vào 60 FR)

- [ ] NFR-SEC-08: Audit log cho mọi CRUD action (nên làm sớm — khó thêm sau, đã tích hợp từ Phase 1-2)
- [ ] NFR-PERF-01: API response < 200ms (chỉ đo khi có traffic thật, đừng optimize sớm)
- [ ] NFR-USAB-02: Vietnamese UTF-8 đầy đủ dấu (mặc định có sẵn với PostgreSQL + Django)
- [ ] NFR-REL-02: Backup hàng ngày (setup ở Phase 9, trước khi có dữ liệu thật quan trọng)

## 📎 Phụ Lục B: Test Cases Convention

Đặt tên theo format `TC-<MODULE>-<FR số>-<STT>` (ví dụ FSD gốc dùng: `TC-GIN-002-001`). Test case tối thiểu cần có (bổ sung dần khi có FSD.docx đầy đủ):

- [ ] `TC-GRN-003-001`: QC PASS → status RECEIVED, batch tạo đúng qty, inventory tăng đúng
- [ ] `TC-GRN-004-001`: `qty_received > qty_ordered` → lỗi, không cho save
- [ ] `TC-QC-005-001`: QC FAIL → GRN REJECTED, GRN_RETURN tạo, inventory KHÔNG đổi
- [ ] `TC-QC-005-002`: QC PARTIAL_PASS → 2 batch tạo đúng (ACTIVE + QUARANTINE)
- [ ] `TC-GIN-002-001`: FIFO chọn đúng batch hạn gần nhất
- [ ] `TC-GIN-002-002`: FIFO lấy từ nhiều batch khi 1 batch không đủ
- [ ] `TC-GIN-002-003`: Không đủ hàng toàn bộ batch → lỗi rõ ràng
- [ ] `TC-INV-001-001`: Batch tự động chuyển EXPIRED khi `exp_date < today`
- [ ] `TC-PO-001-001`: `sum(GRN qty) == PO qty` khi PO chuyển RECEIVED

## 📎 Phụ Lục C: BRD Success Criteria (đối chiếu cuối mỗi phase)

*(Luôn nối kết lại code với mục tiêu kinh doanh gốc — đừng chỉ code theo FR mà quên mất WHY)*

| Metric | Trước | Mục tiêu | Hạn |
|---|---|---|---|
| Inventory Accuracy | 90% | 98%+ | cuối Phase 8 |
| Processing Time (nhập/xuất) | 30 phút | < 5 phút | cuối Phase 3 |
| Tồn lỏng (slow-moving) | $50k | < $10k | cuối Phase 6 |
| QC Error Rate | 5-10% | < 2% | cuối Phase 2 |
| Report Timeliness | 3 ngày | Real-time | cuối Phase 6 |
| System Uptime | N/A | 99.5% | cuối Phase 9 |
| User Adoption | 0% | 100% (chính bạn dùng hàng ngày) | cuối Phase 8 |

## 📎 Phụ Lục D: Nhịp Làm Việc Hàng Tuần (từ Ke_Hoach_Trien_Khai_NVL_Solo.pdf)

- Thứ 2-6 tối (sau 16:45, trước 22:00 ngủ): ~1.5-2h/tối coding với Claude Code
- Cuối tuần: 3-4h cho việc cần tập trung dài (thiết kế schema, review FIFO logic)
- Cuối mỗi tuần (15 phút): tick BACKLOG.md những FR đã xong + đối chiếu Phụ lục C

| Tuần | Ngày | FR xong tuần này | Đang ở Phase | Ghi chú/Blocker |
|---|---|---|---|---|
| 1 | | | Phase 0-1 | |
| 2 | | | Phase 1 | |
