# 📋 BACKLOG — NVL/WMS Implementation Checklist (Solo Dev Edition)

> **Nguồn:** `SRS` (52 FR gốc theo Section 3) + `FSD` (workflow/data model/API/UI từng module — hiện đầy đủ nhất cho GRN & GIN) + điều chỉnh theo `Ke_Hoach_Trien_Khai_NVL_Solo.pdf` (kế hoạch solo dev + Claude Code).
> Tick `[x]` khi hoàn thành. **Chỉ các dòng có mã in đậm `FR-XX-##` được tính vào bộ đếm 60 FR** — các dòng còn lại (Business Rules, Workflow States, Algorithm, Transaction...) là ghi chú kỹ thuật hỗ trợ Claude Code, tick tự do, không ảnh hưởng % tiến độ.

**Tổng tiến độ:** 7 / 60 FR
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
- [ ] **FR-WM-03** `MUST` — Hiển thị real-time inventory theo kho, vị trí — Qty on-hand, available, reserved, quarantine (chờ model `Inventory`, mục 1f)
- [ ] **FR-WM-04** `MUST` — Cảnh báo tồn < Min Level — Gợi ý tạo PO tự động (chờ model `Inventory`, mục 1f)
- [ ] **FR-WM-05** `MUST` — Cảnh báo tồn > Max Level — Để lại ghi chú cho quản lý (chờ model `Inventory`, mục 1f)
- [ ] **FR-WM-06** `SHOULD` — Hỗ trợ multi-warehouse chuyển vị trí — Stock transfer, intra-warehouse (chờ tồn kho thật, làm cùng Phase 3 GIN/FIFO)

> ⚠️ FR-WM-03/04/05/06 cần dữ liệu tồn kho (qty_on_hand...) mà app `inventory` (mục 1f) chưa tồn tại — CRUD kho + vị trí (FR-WM-01/02) đã đủ để Phase 1 DoD "tạo được Warehouse + Location qua UI" hoàn thành; 4 FR còn lại nối lại khi Inventory có mặt (1f/Phase 3).

#### Business Rules
- [ ] BR-WM-001: `qty_on_hand >= 0` (không cho âm) — thuộc model Inventory, chưa tồn tại (mục 1f)
- [ ] BR-WM-002: `qty_available = qty_on_hand - qty_reserved` (auto calculate) — thuộc model Inventory, chưa tồn tại (mục 1f)
- [ ] BR-WM-003: Khi GIN issue → `qty_on_hand` giảm, `qty_available` giảm — thuộc Phase 3 (GIN)
- [ ] BR-WM-004: Khi GRN receive → `qty_on_hand` tăng, `qty_available` tăng — thuộc Phase 2 (GRN)
- [x] BR-WM-005: Tạo warehouse → min 10 vị trí (tự sinh 10 vị trí mặc định A-01..A-10 khi tạo kho)
- [ ] BR-WM-006: Không thể xóa warehouse nếu `qty_on_hand > 0` — "xoá" hiện là khoá hoạt động (soft); kiểm tra `qty_on_hand` thực tế cần model Inventory (mục 1f), đã đánh dấu TODO trong code (`warehouse/views.py::warehouse_deactivate`)

> ⏸️ **FR-WM-04 phần "gợi ý tạo PO tự động"**: chỉ cần hiển thị cảnh báo trên dashboard ở Phase 1 (tính on-the-fly khi load trang). Auto-tạo PO draft thật sự (cần job chạy nền) dời qua Phase 5 khi có Celery.

### 1c. [Bổ sung] Product / SKU Master Data — `catalog` app

*(Không có mã FR riêng trong SRS gốc — nhưng bắt buộc phải có trước khi làm GRN/PO/Inventory)*

- [x] Product model tối thiểu: `product_code`, `name`, `category`, `uom` (đơn vị tính), `min_level`, `max_level`
- [x] CRUD cơ bản (list, create, edit) — role MANAGER/ADMIN tạo/sửa, mọi user đã đăng nhập xem được (đồng nhất với warehouse); `is_active` sửa trực tiếp trên form (không tách khoá/mở riêng vì không có business rule như BR-WM-006)

### 1d. [Bổ sung] Supplier Master Data — `partners` app

*(Không có mã FR riêng trong SRS gốc — cần cho GRN, PO, Reporting supplier performance)*

- [x] Supplier model tối thiểu: `supplier_code`, `name`, `contact`, `lead_time_days` (cần cho FR-PO-05 sau này)
- [x] CRUD cơ bản — role MANAGER/ADMIN tạo/sửa, mọi user đã đăng nhập xem được (đồng nhất với catalog); `is_active` sửa trực tiếp trên form

### 1e. [Bổ sung] PO Stub — `purchasing` app (tối thiểu, để GRN tham chiếu)

*(Giải quyết circular dependency: GRN cần `po_id` FK hợp lệ, nhưng PO đầy đủ chỉ làm ở Phase 5)*

- [x] PO model tối thiểu: `po_no`, `supplier_id` (FK), `status` (mặc định `SENT` để test được), `items` (`product_id`, `qty_ordered`, `unit_price`) — CRUD qua inline formset; phân quyền dùng RBAC thật (`user.can(action, 'po')`) vì 'po' có trong Permission Matrix: MANAGER/PURCHASING/ADMIN Create+Update, STAFF/QC/ACCOUNTANT chỉ Read
- [x] **Chưa làm ở đây:** workflow DRAFT→APPROVED, so sánh giá NCC, auto-suggestion, lead-time tracking — tất cả dời qua Phase 5

### 1f. [Bổ sung] Inventory & Batch — `inventory` app (chỉ tạo schema, chưa làm logic)

*(GRN ở Phase 2 cần model này tồn tại để ghi `qty_on_hand`/tạo Batch — logic FIFO/EOQ/alert đầy đủ làm ở Phase 3)*

- [x] Inventory model: `product_id` (FK), `warehouse_id` (FK), `qty_on_hand`, `qty_reserved`, `qty_available` (computed), `qty_quarantine` — unique per (product, warehouse); `qty_available` là property, không lưu cột riêng
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
- [ ] **FR-GRN-01** `MUST` — Tạo GRN với đầy đủ thông tin (Mã tự động, Ngày, NCC, PO, Chi tiết SKU)
- [ ] **FR-GRN-02** `MUST` — Workflow GRN: DRAFT → PENDING_QC → RECEIVED → CLOSED
- [ ] **FR-GRN-03** `MUST` — Khi GRN RECEIVED (QC pass), tự động tạo batch & cập nhật inventory tăng
- [ ] **FR-GRN-04** `MUST` — Support partial GRN (nhận 1 phần, chờ phần còn lại)
- [ ] **FR-GRN-05** `MUST` — Ghi lại ký nhận từ: Mua hàng, QC, Kho (audit trail)
- [ ] **FR-GRN-06** `MUST` — In phiếu GRN với barcode để dán trên hàng
- [ ] **FR-GRN-07** `SHOULD` — So sánh Qty GRN vs Qty PO để alert nếu vượt

#### Workflow States
- [ ] State `DRAFT`: form tạo GRN, nút Save/Submit/Cancel
- [ ] State `PENDING_QC`: nhân viên Kho nhập Qty thực tế nhận, nút Submit to QC
- [ ] State `QC_IN_PROGRESS`: QC nhập kết quả Pass/Fail/Partial, nút Approve/Reject
- [ ] State `RECEIVED` (Pass): auto tạo Batch + cập nhật inventory tăng, khóa edit
- [ ] State `REJECTED` (Fail): auto tạo GRN_RETURN
- [ ] State `CLOSED`: archive
- [ ] Transition test: DRAFT → PENDING_QC → QC_IN_PROGRESS → RECEIVED/REJECTED → CLOSED

#### Data Model (tham khảo — bảng grn/grn_items)
- [ ] `grn`: id, grn_no (auto GRN-YYYYMM-XXX), po_id (FK), supplier_id (FK), grn_date, status, created_by (FK User), created_at
- [ ] `grn_items`: id, grn_id (FK), product_id (FK), qty_ordered, qty_received, mfg_date, exp_date, batch_code, unit_price, status

#### Quantity Handling & Edge Cases
- [ ] BR-GRN-001: `qty_received <= qty_ordered`
- [ ] BR-GRN-006: `exp_date > mfg_date` (luôn đúng)
- [ ] BR-GRN-007: `qty_received = 0` → status = RECEIVED nhưng qty không tăng
- [ ] **Qty tolerance check**: Alert nếu chênh lệch > tolerance % (config per supplier)
- [ ] **Qty validation**: `qty_grn <= qty_po` (không cho nhập vượt quá PO)

### 2b. Quality Control (QC) — `quality` app

#### Functional Requirements (6 FR)
- [ ] **FR-QC-01** `MUST` — Tạo quy trình QC: PENDING_QC → PASS/FAIL/PARTIAL_PASS
- [ ] **FR-QC-02** `MUST` — Định nghĩa tiêu chuẩn QC (Ngoại hình, Trọng lượng, Màu sắc, Seal integrity)
- [ ] **FR-QC-03** `MUST` — Nhập kết quả từng tiêu chuẩn: PASS / FAIL
- [ ] **FR-QC-04** `MUST` — Kết quả FAIL → Ghi chú lý do & tạo GRN trả lại
- [ ] **FR-QC-05** `MUST` — Kết quả PASS → GRN được approve, tạo batch & cập nhật inventory
- [ ] **FR-QC-06** `SHOULD` — Hỗ trợ upload hình ảnh evidence

#### QC Criteria & Sampling
- [ ] **Sampling method**: % based (default 10%) hoặc fixed qty (config per SKU)
- [ ] **QC Criteria master data**: per category (Bột mỳ, Đường...), mỗi criteria có name/pass_rule/fail_rule, cho phép upload ảnh reference
- [ ] **Result tracking**: mỗi item trong sample ghi PASS/FAIL từng criteria, không chỉ overall result
- [ ] **QC duration tracking**: log start/end time, alert nếu > SLA (24h) — ⏸️ tính on-the-fly khi load trang QC dashboard, chưa cần Celery
- [ ] **QC approval override**: Supervisor có thể override kết quả (ghi chú lý do)

### 2c. GRN ↔ QC Integration & Batch Lifecycle (dùng chung cho cả 2)

- [ ] **QC result mapping**:
  - PASS → GRN: RECEIVED, tạo Batch status ACTIVE
  - FAIL → GRN: REJECTED, tạo GRN_RETURN, inventory KHÔNG cập nhật
  - PARTIAL_PASS → GRN: RECEIVED, Batch split: ACTIVE (qty pass) + QUARANTINE (qty fail)
- [ ] **Batch status enum**: ACTIVE, PARTIAL_USED, QUARANTINE, EXPIRED, CLOSED
- [ ] **Quarantine batch**: không thể xuất (GIN reject), admin quyết định scrap/return/rework, alert nếu quarantine > 7 ngày (⏸️ tính on-the-fly, chưa cần Celery)

#### GRN_RETURN Workflow (tự động tạo từ QC FAIL)
- [ ] State: PENDING → APPROVED → RETURNED → CLOSED
- [ ] Link tới GRN gốc (ref field), reason auto-fill "QC Fail"
- [ ] ⏸️ Auto-email supplier khi reject — **hoãn** (cần Celery/email async); tạm thời: hiện thông báo trong app, gửi email thủ công

#### Inventory Update Triggers (transaction, atomicity)
- [ ] **GRN RECEIVED transaction**: Create Batch → Update Inventory (qty_on_hand, qty_available) → Update GRN status → Audit log — all-or-nothing, rollback nếu fail bước nào
- [ ] **QC FAIL transaction**: Create GRN_RETURN → Update GRN status REJECTED → Audit log — **không** update Inventory
- [ ] **PARTIAL_PASS transaction**: Create 2 Batch (ACTIVE + QUARANTINE) → Update Inventory (chỉ cộng phần pass) → Update GRN status

#### Audit Trail (bắt buộc, khó thêm sau)
- [ ] Ghi WHO/WHEN/WHAT/WHY cho mọi state transition của GRN, QC, Batch
- [ ] Ví dụ: `2026-07-16 10:30 | User#5 (QC) | GRN#001 | QC_IN_PROGRESS→RECEIVED | "All PASS"`

### ✅ Definition of Done — Phase 2
- [ ] Test đầy đủ 1 vòng: tạo GRN từ PO stub → nhập Qty thực tế → QC PASS → Batch tạo tự động → Inventory tăng đúng số
- [ ] Test đường FAIL: QC FAIL → GRN_RETURN tạo, Inventory KHÔNG đổi (unit test riêng, đây là chỗ dễ sai)
- [ ] Test đường PARTIAL_PASS: 2 batch tạo đúng, Inventory chỉ cộng phần pass
- [ ] Manual UAT theo Test Case (xem Phụ lục B) nếu chưa có FSD chi tiết cho GRN/QC

---

## PHASE 3 — Tồn Kho Real-time + Xuất Hàng (GIN/FIFO) (Tuần 7-10)

### 3a. Inventory Management (hoàn thiện logic) — `inventory` app

#### Functional Requirements (5 FR)
- [ ] **FR-INV-01** `MUST` — Tạo & quản lý lô hàng (batch) với: Mã lô/NSX/HSD/Nhà cung cấp; Qty received/used/available; Status: ACTIVE, PARTIAL_USED, CLOSED, EXPIRED, QUARANTINE
- [ ] **FR-INV-02** `MUST` — Cảnh báo lô sắp hết hạn (< 30 ngày)
- [ ] **FR-INV-03** `MUST` — Theo dõi lịch sử chuyển động tồn kho (audit trail)
- [ ] **FR-INV-04** `MUST` — Hỗ trợ FIFO/LIFO logic khi xuất hàng
- [ ] **FR-INV-05** `SHOULD` — Tính toán EOQ (Economic Order Quantity)

#### Batch → Inventory Link
- [ ] `Inventory.qty_on_hand` phản ánh tổng batch vật lý còn trong kho (kể cả EXPIRED — hàng vẫn nằm đó); nhưng FIFO/GIN chỉ được chọn batch `status = ACTIVE`
- [ ] Batch `QUARANTINE` tính riêng vào `qty_quarantine`, KHÔNG cộng vào `qty_available`

> ⏸️ FR-INV-02 (cảnh báo hết hạn): tính on-the-fly mỗi lần load dashboard (`WHERE exp_date < today + 30`), chưa cần Celery/cron.
> ⏸️ FR-INV-05 (EOQ): SHOULD, có thể defer sang Phase 6 (cùng Reporting) nếu thời gian gấp.

### 3b. Phiếu Xuất (GIN) — `shipping` app

#### Functional Requirements (7 FR)
- [ ] **FR-GIN-01** `MUST` — Tạo GIN từ yêu cầu xuất hàng (Ref PO, Sản xuất, Bán hàng)
- [ ] **FR-GIN-02** `MUST` — Tự động gợi ý lô FIFO (sắp hết hạn nhất)
- [ ] **FR-GIN-03** `MUST` — Allow overwrite batch selection nếu cần
- [ ] **FR-GIN-04** `MUST` — Khi GIN issued, tự động cập nhật inventory & batch quantity
- [ ] **FR-GIN-05** `MUST` — Ghi lại Qty thực tế xuất vs Qty yêu cầu (có thể khác)
- [ ] **FR-GIN-06** `MUST` — In phiếu xuất & barcode để kiểm soát
- [ ] **FR-GIN-07** `SHOULD` — Gợi ý kho/vị trí có hàng dựa trên logic

#### Workflow States
- [ ] State `DRAFT`: chọn SKU/Qty, hệ thống suggest lô FIFO
- [ ] State `PICKING`: quét barcode batch để confirm, cho phép đổi batch (ghi lý do)
- [ ] State `ISSUED`: cập nhật `qty_on_hand -= qty_issued`, `batch.qty_used += qty_issued`, khóa edit
- [ ] State `CLOSED`: archive

#### FIFO Algorithm — ⚠️ phần dễ sai nhất, BẮT BUỘC có unit test riêng
- [ ] Query: `SELECT * FROM batch WHERE product_id=? AND qty_available>0 AND status='ACTIVE' ORDER BY exp_date ASC, created_at ASC`
- [ ] Duyệt batch, lấy đủ `qty_needed` (có thể lấy từ nhiều batch nếu 1 batch không đủ)
- [ ] Trả về list `{batch_id, qty_to_issue, exp_date, location}`
- [ ] Edge case: không đủ hàng ở mọi batch cộng lại → error rõ ràng, không cho issue
- [ ] BR-GIN-001: `qty_issued <= qty_available`
- [ ] BR-GIN-006: khi `qty_on_hand` = 0 → `batch.status = CLOSED`
- [ ] BR-GIN-007: `exp_date < today` → warning "Batch expired", GIN không được lấy batch EXPIRED/QUARANTINE

### ✅ Definition of Done — Phase 3
- [ ] Unit test FIFO: nhiều batch cùng SKU, hạn khác nhau → lấy đúng thứ tự hạn gần nhất trước
- [ ] Unit test edge case: 1 batch không đủ → tự động lấy tiếp batch kế, tổng đúng `qty_needed`
- [ ] Unit test: không đủ hàng toàn bộ batch → trả lỗi rõ ràng, không cho issue âm
- [ ] GIN không thể chọn batch QUARANTINE/EXPIRED (test riêng)

---

## PHASE 4 — Kiểm Kê (Stock Opname) (Tuần 11-13)

### Functional Requirements (7 FR)
- [ ] **FR-SO-01** `MUST` — Tạo phiếu kiểm kê, chọn kho/vị trí, lập danh sách SKU
- [ ] **FR-SO-02** `MUST` — Web form để nhân viên kho quét barcode SKU & nhập Qty thực tế
- [ ] **FR-SO-03** `MUST` — Tự động so sánh Qty hệ thống vs Qty kiểm kê, tính chênh lệch
- [ ] **FR-SO-04** `MUST` — Ghi chú lý do chênh lệch: Loss, Damage, Theft, Counting Error, Expired
- [ ] **FR-SO-05** `MUST` — Tự động tạo Adjustment phiếu nếu chênh lệch > 0
- [ ] **FR-SO-06** `MUST` — Báo cáo chi tiết phiếu kiểm kê (theo dõi từng dòng)
- [ ] **FR-SO-07** `SHOULD` — Hỗ trợ kiểm kê từng vị trí hoặc toàn kho

### Workflow Phases
- [ ] Phase `PLANNING`: chọn kho/vị trí cần kiểm, status = DRAFT
- [ ] Phase `EXECUTION`: form quét barcode, nhập Qty thực tế, hiện Qty hệ thống vs Qty quét
- [ ] Phase `RECONCILIATION`: so sánh `qty_system` vs `qty_actual`, tính variance
- [ ] Phase `ADJUSTMENT`: auto tạo Adjustment phiếu, cập nhật inventory
- [ ] UI color highlight: xanh (match) / vàng (variance ≤5) / đỏ (variance >5)

### ✅ Definition of Done — Phase 4
- [ ] Test: chênh lệch dương/âm đều tạo đúng Adjustment, Inventory cập nhật đúng chiều

---

## PHASE 5 — Purchase Order Đầy Đủ (Tuần 14-16)

*(Nâng cấp PO stub đã tạo ở Phase 1 thành workflow đầy đủ)*

### Functional Requirements (6 FR)
- [ ] **FR-PO-01** `MUST` — Workflow PO: DRAFT → APPROVED → SENT → PARTIAL_RECEIVED → RECEIVED → CLOSED
- [ ] **FR-PO-02** `MUST` — Khi tồn < Min Level, gợi ý tạo PO tự động
- [ ] **FR-PO-03** `MUST` — So sánh giá từ nhiều NCC để chọn optimal
- [ ] **FR-PO-04** `MUST` — GRN phải tham chiếu PO để đối soát qty
- [ ] **FR-PO-05** `MUST` — Theo dõi lead-time từng NCC
- [ ] **FR-PO-06** `MUST` — Tracking delivery status: On time, Delayed, Partial

### Workflow States
- [ ] State `DRAFT`: nhập Supplier, SKU items, qty, price, delivery date
- [ ] State `APPROVED`: Manager duyệt (auto-approve nếu <$10,000)
- [ ] State `SENT`: gửi PO tới NCC, khóa edit
- [ ] State `PARTIAL_RECEIVED` / `RECEIVED`: nhập Qty received từ GRN
- [ ] State `CLOSED`: archive

### PO ↔ GRN Reconciliation
- [ ] PO line item: `qty_ordered`, `qty_grn_received`, `qty_remaining` (= ordered - received)
- [ ] GRN nhận partial → auto-update `qty_received`/`qty_remaining`, PO state chuyển PARTIAL_RECEIVED/RECEIVED tương ứng
- [ ] Alert nếu `sum(Qty GRN) > Qty PO`
- [ ] Overdue tracking: alert nếu `expected_delivery < today` & PO vẫn SENT/PARTIAL_RECEIVED (⏸️ tính on-the-fly khi load PO list, chưa cần Celery)

### Auto-PO Suggestion — ⏸️ HOÃN (cần Celery), làm bản rút gọn trước
- [ ] **MVP không Celery**: hiển thị danh sách "SKU dưới Min Level" trên dashboard (Phase 1 đã có), nút "Tạo PO draft" bấm thủ công từ đó
- [ ] **Bản đầy đủ (khi có Celery)**: job chạy hourly tự tạo PO DRAFT (supplier chính, qty = max_level - qty_on_hand, giá lần cuối, delivery = today + lead_time), Purchasing vẫn phải duyệt trước khi SEND

### ✅ Definition of Done — Phase 5
- [ ] Test: tạo PO → gửi → nhận GRN partial 2 lần → PO tự chuyển RECEIVED khi đủ qty
- [ ] Test: GRN reject không làm giảm `qty_remaining` của PO

---

## PHASE 6 — Reporting & Analytics (Tuần 17-19)

### Functional Requirements (6 FR)
- [ ] **FR-RPT-01** `MUST` — Dashboard KPI: Tổng giá trị tồn, Số SKU, Tồn sắp hết hạn, PO chưa nhận
- [ ] **FR-RPT-02** `MUST` — Báo cáo ABC Analysis (Pareto 80-20)
- [ ] **FR-RPT-03** `MUST` — Báo cáo tồn lỏng (SKU chưa bán > 180 ngày)
- [ ] **FR-RPT-04** `MUST` — Báo cáo hiệu suất NCC: % đúng hạn, % QC pass, avg price
- [ ] **FR-RPT-05** `MUST` — Export báo cáo sang Excel, PDF
- [ ] **FR-RPT-06** `SHOULD` — ⏸️ Scheduling reports gửi email hàng tuần — **hoãn** (cần Celery), tạm thời: nút "Export" thủ công

### Báo cáo cụ thể
- [ ] Dashboard KPI: Inventory Value, SKU count, Low stock, Near expiry
- [ ] ABC Analysis (Pareto 80-20: A/B/C theo giá trị tồn)
- [ ] Slow-moving items (SKU không xuất >180 ngày)
- [ ] Supplier Performance (On-time %, Quality %, Avg price)

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
