# Roadmap mở rộng module Purchasing (PUR)

## Context

Module `purchasing` hiện đã đủ nền tảng vận hành cơ bản (PR nháp → duyệt 2 cấp → PO → GRN/QC →
tồn kho, workflow `DRAFT → APPROVED → SENT → PARTIAL_RECEIVED/RECEIVED → CLOSED`, audit log,
notification, khóa transaction, phân quyền) — toàn bộ 6 `FR-PO-*` trong Phase 5 của `BACKLOG.md`
đã tick `[x]`. Người dùng muốn mở rộng module này lên mức "procurement" đầy đủ hơn: RFQ/so sánh
NCC, ngân sách, hợp đồng, quản lý NCC theo scorecard, đối soát hóa đơn 3 chiều — tham khảo các
pattern từ Odoo/D365/SAP/Oracle nhưng không sao chép nguyên khối ERP.

Đây là một sáng kiến **ngoài phạm vi 60-FR gốc** của `BACKLOG.md` (không phải hoàn thiện nốt FR
còn thiếu, mà là năng lực mới). Quy mô thực (7 nhóm tính năng độc lập, ước lượng 8-10 tuần chỉ
riêng phần MVP, một mình 1 dev) vượt quá một plan triển khai duy nhất — theo đúng nguyên tắc
"đánh giá quy mô trước khi hỏi chi tiết" của quy trình brainstorming đang áp dụng, tài liệu này
dừng ở mức **roadmap**: xác nhận hiện trạng, chia nhỏ thành các dự án con độc lập, xác định thứ
tự xây dựng. Mỗi giai đoạn dưới đây sẽ có vòng brainstorm → spec → plan → triển khai riêng khi
đến lượt, không viết chi tiết code/model ngay trong tài liệu này.

## Hiện trạng đã xác minh (đối chiếu code thật, không suy đoán)

| Claim trong đánh giá gốc | Kết quả xác minh |
|---|---|
| `po_detail` cộng GRN không loại trừ CANCELLED, lệch với `sync_po_status()` | **Đúng** — [`purchasing/views.py:306-312`](purchasing/views.py#L306-L312) thiếu `.exclude(grn__status=Grn.Status.CANCELLED)` mà [`purchasing/services.py:52-58`](purchasing/services.py#L52-L58) có |
| Email PO `fail_silently=True`, audit log "đã gửi" bất kể thành công | **Đúng** — [`purchasing/services.py:140-160`](purchasing/services.py#L140-L160), `_send_po_email()` luôn `return True` |
| Lead time tính từ `created_at`, không phải lúc gửi/NCC xác nhận | **Đúng** — [`purchasing/services.py:234`](purchasing/services.py#L234), vì `PurchaseOrder` chưa có `sent_at`/`supplier_confirmed_at` |
| `PurchaseRequest.linked_po` là FK đơn, 1 PR → 1 PO | **Đúng** — [`purchasing/models.py:217-219`](purchasing/models.py#L217-L219) |
| PO thiếu thuế/chiết khấu/tiền tệ/điều khoản/vận chuyển | **Đúng** — không field nào trong nhóm này trên `PurchaseOrder`/`PurchaseOrderItem` |
| Supplier chưa có qualification/hồ sơ pháp lý/blacklist/hết hạn chứng chỉ | **Đúng** — `partners/models.py` chỉ có `status` chung chung, không có scorecard/expiry |
| Reports thiếu spend analysis/savings/cycle time/budget variance | **Đúng** — không có hàm nào khớp trong `reports/services.py` |
| 139 test PUR, "timeout, chưa kết luận được" | **Cần đính chính**: chạy đủ thời gian (~160s, vượt ngưỡng 120s mặc định của tool) → **139/139 PASS**. Nền hiện tại đáng tin cậy hơn đánh giá gốc nêu. |
| Mã FR là `FR-PUR-*` | **Cần đính chính**: mã thật trong `BACKLOG.md` là `FR-PO-*` (6 FR, Phase 5, tick hết); PR không có mã FR riêng (ghi rõ "bổ sung theo yêu cầu người dùng, không thuộc FR gốc"). |
| PO list giới hạn theo `created_by`, PO detail thì không | **Đúng, nhưng có chủ đích, không phải lỗ hổng** — docstring [`purchasing/views.py:296-301`](purchasing/views.py#L296-L301) giải thích PO là tác vụ nhiều vai trò cùng xử lý 1 phiếu; giữ nguyên, không cần "chốt" lại. |

## Nguyên tắc chia nhỏ

- Mỗi giai đoạn dưới đây là một **dự án con độc lập** — có mục tiêu riêng, có thể triển khai và
  mang lại giá trị mà không bắt buộc phải có giai đoạn sau nó.
- **Ranh giới Django app của từng giai đoạn (mở rộng `purchasing` sẵn có, hay tách app mới như
  `sourcing`/`budgeting`/`invoicing`) chưa quyết định trong tài liệu này** — đây là quyết định
  kiến trúc cần cân nhắc lúc brainstorm chi tiết từng giai đoạn, không quyết trước khi biết rõ
  phạm vi field/model thật sự cần (tránh over-design sớm).
- Vì đây là năng lực ngoài 60-FR gốc, đề xuất tạo file mới **`PUR_EXPANSION_ROADMAP.md`** ở gốc
  repo (nội dung tương đương roadmap này, cập nhật dần theo tiến độ thật) thay vì gò vào khung
  `FR-XX-##` của `BACKLOG.md` — và thêm một dòng trỏ sang file đó trong `CLAUDE.md` (mục "Source-
  of-truth documents").

## Các giai đoạn

### Giai đoạn 0 — Ổn định nền (~1-2 tuần)
Sửa 3 lệch logic đã xác minh ở trên (GRN CANCELLED, email fail-silently, lead-time), bổ sung
timestamp (`submitted_at`/`approved_at`/`sent_at`/`supplier_confirmed_at`/`closed_at`), chuẩn hóa
tổng tiền PO (subtotal/discount/tax/freight/grand total), attachment dùng chung cho PR/RFQ/PO.
**Không phụ thuộc giai đoạn nào khác** — nên làm trước tiên, rủi ro thấp, giá trị ngay (dữ liệu
đúng hơn cho mọi giai đoạn sau).

### Giai đoạn 1 — Purchase Requisition 2.0 (~2-3 tuần)
Mở rộng PR header (loại yêu cầu, ngày cần hàng, cost center/project, lý do mua) và PR line
(non-catalog/free-text, UOM, trạng thái theo dòng). Thay `linked_po` (FK đơn) bằng bảng phân bổ
`ProcurementAllocation` để 1 PR sinh nhiều PO / 1 PO gộp nhiều PR. **Phụ thuộc Giai đoạn 0**
(cần timestamp/attachment đã có sẵn).

### Giai đoạn 2 — Ngân sách & Approval engine (~2-3 tuần)
Cost center/budget/project làm master data mới; budget commitment khi PR duyệt, hoàn khi
hủy, chuyển actual khi có invoice (phụ thuộc Giai đoạn 6). Approval rule cấu hình được (theo
giá trị, nhóm hàng, vượt ngân sách) thay vì hard-code 2 cấp — giữ luồng 2 cấp hiện tại làm rule
mặc định để không phá dữ liệu cũ. **Phụ thuộc Giai đoạn 1** (cần cost center trên PR).

### Giai đoạn 3 — RFQ & quản lý báo giá (~3-4 tuần)
`SourcingEvent/RFQ`, mời NCC, nhập báo giá, so sánh landed cost, chấm điểm có trọng số, award
từng dòng, snapshot báo giá không đổi theo master data. **Giá trị lớn nhất cho PUR** theo đánh
giá gốc. **Phụ thuộc Giai đoạn 1** (chọn dòng PR để phát RFQ).

### Giai đoạn 4 — PO nâng cao & hợp đồng (~3-4 tuần)
Currency/thuế/chiết khấu/Incoterm/lịch giao theo dòng, in PDF, NCC xác nhận/từ chối/đổi lịch, PO
amendment có version. Blanket agreement, bảng giá theo SKU/thời gian, release PO từ hợp đồng.
**Phụ thuộc Giai đoạn 0** (timestamp/tổng tiền) và có thể nhận input từ Giai đoạn 3 (RFQ award).

### Giai đoạn 5 — Supplier Management (~2-3 tuần)
Đăng ký/phê duyệt NCC, approved supplier theo SKU/category, hồ sơ pháp lý + ngày hết hạn,
trạng thái prospective/approved/suspended/blacklisted, scorecard (on-time, fill rate, QC pass,
price variance...), cảnh báo phụ thuộc quá lớn vào 1 NCC. **Độc lập tương đối** — có thể làm
song song hoặc sau Giai đoạn 3/4, mở rộng `partners` app sẵn có.

### Giai đoạn 6 — Invoice & đối soát (~3-4 tuần)
Vendor invoice/invoice line, 2-way match (dịch vụ: PO↔Invoice) và 3-way match (hàng hóa:
PO↔GRN/QC↔Invoice) với tolerance, credit note, duyệt thanh toán, export sang kế toán — **không**
xây sổ cái kế toán trong PUR. **Phụ thuộc Giai đoạn 0** (tổng tiền PO) và Giai đoạn 4 (PO
version); là input cho phần "actual" của Giai đoạn 2.

### Giai đoạn 7 — Dashboard & tích hợp (~2-3 tuần)
PR aging, procurement cycle time, spend theo phòng ban/category/NCC, savings, budget vs
commitment vs actual, PO/supplier-confirmation overdue, contract utilization, maverick spend,
invoice exception aging. API/webhook/OCR/supplier portal để đợt sau, không trong roadmap này.
**Phụ thuộc gần như mọi giai đoạn trước** — làm cuối cùng vì cần dữ liệu từ các giai đoạn kia.

## Thứ tự khuyến nghị

Giữ đúng thứ tự đề xuất trong đánh giá gốc — đây là thứ tự ít rủi ro nhất vì đi thẳng vào nút
thắt nghiệp vụ hiện tại (bộ phận gửi nhu cầu → PUR tìm nguồn → chọn NCC → phát hành đơn mua có
kiểm soát) mà không cần chờ các phần nặng về sổ sách (invoice/kế toán):

**MVP (ưu tiên làm, ~8-10 tuần):** Giai đoạn 0 → 1 → 2 (bản cơ bản: giá trị + ngân sách) → 3 →
4 (bản cơ bản: version/PDF/confirmation, chưa cần blanket agreement đầy đủ) → 7 (bản cơ bản: PR
aging/RFQ/PO overdue).

**Đợt sau (brainstorm riêng khi có nhu cầu thật, chưa lên lịch):** Giai đoạn 6 (Invoice/3-way
match), phần hợp đồng nâng cao của Giai đoạn 4 (blanket agreement đầy đủ), Giai đoạn 5 đầy đủ
(supplier scorecard/qualification), API/webhook/OCR/supplier portal.

## Bước tiếp theo

Roadmap này **không phải** kế hoạch triển khai — mỗi giai đoạn cần một vòng
`brainstorming-thiet-ke` riêng (câu hỏi làm rõ, phương án, spec cụ thể) trước khi có
`viet-ke-hoach-trien-khai`. Đề xuất bắt đầu bằng **Giai đoạn 0** ở phiên làm việc kế tiếp, vì nó
độc lập, rủi ro thấp, và các giai đoạn sau đều phụ thuộc dữ liệu nó tạo ra (timestamp, tổng tiền
PO chuẩn hóa).
