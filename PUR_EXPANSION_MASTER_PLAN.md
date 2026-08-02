# PUR Expansion Master Plan

> Trạng thái: Confirmed — 14/14 quyết định mục 16 đã chốt, xem `docs/pur/00_business_decisions.md`
> Phiên bản: 1.0 — 02/08/2026 (mục 5.3/5.1 cập nhật 02/08/2026 theo quyết định #8: ngoại tệ
> chuyển từ Release 2 lên Release 1, xem ghi chú tại mục 5.3)  
> Nguồn hợp nhất: `plan_pur.md`, `pur_2.md` và hiện trạng code module `purchasing`

## 1. Kết luận điều hành

PUR hiện đã vận hành tốt luồng cơ bản `PR → duyệt 2 cấp → PO → GRN/QC → tồn kho`.
Hướng mở rộng đúng là đưa PUR thành nền tảng **Request-to-Order** trước, sau đó mới tiến tới
**Request-to-Pay**.

Không nên đưa toàn bộ Budget, RFQ, Supplier Management, Contract và Invoice vào một lần phát hành.
Kế hoạch tối ưu được chia thành:

- **Release 1 — PUR Core Expansion:** PR 2.0, phân bổ dòng PR, nhiều PR/nhiều PO, approval theo
  rule, budget commitment cơ bản, RFQ/quotation/award, PO revision, ngoại tệ cơ bản (tỷ giá nhập
  tay theo ngày, không API/tax/landed-cost nâng cao — xem quyết định #8 trong
  `docs/pur/00_business_decisions.md`) và dashboard vận hành.
- **Release 2 — Procurement Control:** hợp đồng, supplier qualification/scorecard nâng cao,
  multi-currency nâng cao (API tỷ giá tự động, tax/landed-cost), service procurement.
- **Release 3 — Request-to-Pay:** vendor invoice, 2-way/3-way matching, credit note và tích hợp
  kế toán.

Ước lượng thực tế cho Release 1:

- **12–16 tuần** nếu một developer thực hiện, có người nghiệp vụ tham gia review/UAT đúng lịch.
- **9–12 tuần** nếu có hai developer và QA/UAT chạy song song.
- Mốc **8–10 tuần** chỉ khả thi khi cắt Budget thành commitment rất cơ bản, không làm service
  procurement, không làm supplier portal và không làm invoice matching.

## 2. Đối chiếu và phản biện hai tài liệu nguồn

| Nội dung | `plan_pur.md` | `pur_2.md` | Quyết định hợp nhất |
|---|---|---|---|
| Kiểm chứng hiện trạng code | Tốt, có dẫn chứng | Tóm tắt | Giữ kết quả từ `plan_pur.md` |
| Mức độ tài liệu | Roadmap kỹ thuật | Gọi là BRD nhưng còn sơ lược | Bổ sung business scope, actor, rule, UAT, rollout |
| Mô hình Allocation | Có, đúng dependency | Nhấn mạnh là ưu tiên số 1 | Bắt buộc làm trước RFQ/Multi-PO |
| Timeline MVP | 8–10 tuần nhưng tổng estimate phase lớn hơn | 8–10 tuần cố định | Điều chỉnh theo capacity; dùng stage gate |
| Budget | Planned/Committed/Actual | Có nêu ba lớp | MVP chỉ quản lý Planned/Committed/Released; Actual chờ Invoice/ERP |
| Approval Engine | Khá rộng | Rule khái quát | MVP dùng decision table + serial steps; parallel/delegation để Release 2 |
| Non-catalog/service | Gộp chung | Gộp chung | Tách non-catalog goods và services; service procurement để Release 2 |
| Invoice | Để giai đoạn sau | Vừa ngoài phạm vi vừa nằm trong flow/KPI | Loại khỏi MVP; giữ trong target state Release 3 |
| Supplier Management | Giai đoạn riêng | Nêu qualification/blacklist/scorecard | MVP chỉ dùng NCC active/approved; nâng cao ở Release 2 |
| Dashboard | Làm cuối | Đưa vào tuần 8 | MVP chỉ KPI có dữ liệu thật; KPI invoice/actual để Release 3 |
| Acceptance Criteria | Chưa đủ | Có nhưng quá tổng quát | Viết theo end-to-end scenario và traceability |
| Migration/rollback/security | Thiếu | Thiếu | Bổ sung bắt buộc trước go-live |

### Nội dung giữ lại

- Sửa sai lệch GRN `CANCELLED`, trạng thái gửi email và lead-time trước khi mở rộng.
- Thay liên kết `PurchaseRequest.linked_po` đơn bằng mô hình phân bổ theo dòng.
- RFQ phải xuất phát từ nhu cầu đã được duyệt và award được theo từng dòng.
- Budget phải phân biệt planned, committed và actual.
- Không xây sổ cái kế toán trong PUR.
- Hai file gốc tiếp tục được giữ làm tài liệu tham chiếu, không dùng làm source of truth triển khai.

### Nội dung loại khỏi MVP

- Vendor invoice và 2-way/3-way matching.
- Supplier portal, OCR invoice, webhook/API công khai.
- Blanket agreement đầy đủ.
- Approval song song, delegation phức tạp và workflow designer tổng quát.
- Mua dịch vụ end-to-end vì cần Service Acceptance thay cho GRN/QC.
- Budget Actual nếu chưa có nguồn dữ liệu invoice/kế toán đáng tin cậy.

## 3. Business case

### 3.1 Vấn đề cần giải quyết

- Một PR chỉ tạo được một PO, không thể chia theo NCC hoặc mua từng phần.
- PUR chưa lưu được bằng chứng mời giá, báo giá, đánh giá và lý do chọn NCC.
- Duyệt PR đang cố định theo hai cấp, chưa phản ánh giá trị/nhóm hàng/ngân sách.
- Chưa theo dõi cam kết chi tại thời điểm PR/PO được duyệt.
- Người yêu cầu và quản lý chưa nhìn thấy tiến độ chi tiết theo từng dòng.
- PO chưa có revision và xác nhận NCC nên thay đổi sau gửi khó kiểm soát.

### 3.2 Mục tiêu Release 1

- Chuẩn hóa một đầu vào mua hàng chung cho các phòng ban.
- Truy vết mỗi dòng nhu cầu từ PR đến RFQ, award, PO và GRN.
- Cho phép một PR tạo nhiều PO và một PO gom nhiều PR mà không mua vượt số lượng đã duyệt.
- Kiểm soát duyệt theo department, amount, category, emergency và trạng thái ngân sách.
- Ghi nhận cạnh tranh giá minh bạch và lý do award.
- Theo dõi budget commitment và giải phóng cam kết khi hủy/đóng phần không mua.
- Đo được thời gian xử lý PR, RFQ, PO và tỷ lệ giao đúng hạn.

### 3.3 KPI và target cần Business Owner xác nhận

Trong Discovery phải đo baseline tối thiểu 4 tuần dữ liệu hoặc lấy mẫu hồ sơ thủ công.

| KPI | Công thức MVP | Target đề xuất |
|---|---|---|
| PR approval cycle time | Approved at − Submitted at | Giảm ≥ 30% sau 3 tháng |
| Procurement cycle time | PO sent at − PR submitted at | Giảm ≥ 20% |
| PR aging | Số PR quá SLA theo bước hiện tại | < 10% PR mở |
| Competitive sourcing rate | RFQ có ≥ 2 báo giá / RFQ đủ điều kiện | ≥ 80% |
| PO on-time delivery | PO/line nhận đúng hạn / đã nhận | ≥ 90% hoặc baseline +10% |
| Budget commitment accuracy | Commitment hệ thống − hồ sơ đối chiếu | Sai lệch < 1% |
| PO without approved demand | PO không có allocation hợp lệ | 0, trừ ngoại lệ được duyệt |

Không đưa `Invoice Exception`, `Actual Spend` và `Budget Variance Actual` vào KPI Release 1 vì
chưa có Invoice/Accounting integration.

## 4. Stakeholder và trách nhiệm

| Vai trò | Trách nhiệm chính |
|---|---|
| Business Owner/CFO hoặc COO | Phê duyệt policy, scope, budget rule và go-live |
| PUR Manager | Process owner; duyệt sourcing/award; quản lý workload |
| PUR Staff/Buyer | Tiếp nhận PR, tạo RFQ, nhập quote, đề xuất award, tạo PO |
| Requester | Tạo PR, bổ sung thông tin, theo dõi và xác nhận nhu cầu |
| Department Manager | Duyệt nhu cầu và cost center của phòng |
| Finance/Accountant | Xác nhận budget structure, tolerance và báo cáo cam kết |
| Warehouse/QC | GRN, QC và phản hồi chất lượng/giao hàng |
| Supplier | Nhận RFQ/PO và phản hồi ngoài hệ thống trong Release 1 |
| Admin | Cấu hình rule, category, SLA, budget period và quyền |
| IT/Developer | Thiết kế, migration, kiểm thử kỹ thuật và vận hành |

Business Owner, PUR Manager và Finance là ba bên bắt buộc ký UAT; không giao toàn bộ quyết định
nghiệp vụ cho đội phát triển.

## 5. Phạm vi sản phẩm

### 5.1 Release 1 — Must have

1. Ổn định dữ liệu PO/GRN và bổ sung timestamp/audit cần thiết.
2. PR header 2.0: request type, priority, required date, department, cost center, project, reason.
3. PR line lifecycle và approved quantity.
4. Non-catalog **goods request**; trước khi tạo PO/GRN phải map sang Product đã được phê duyệt.
5. Allocation giữa PR line và PO line; hỗ trợ split/consolidate.
6. Approval decision table theo amount/category/budget/emergency, tuần tự.
7. Budget plan và commitment/release cơ bản.
8. RFQ, supplier invitation, quotation snapshot, comparison, evaluation và line award.
9. PO được sinh từ award/allocation, revision có kiểm soát và supplier confirmation do Buyer ghi nhận.
10. Dashboard PR aging, approval cycle, RFQ status, PO overdue, OTD và commitment.
11. Migration/backfill, permission, audit, notification, UAT và runbook go-live.
12. Ngoại tệ cơ bản: `ExchangeRate` (currency, rate_date, rate_to_vnd) nhập tay bởi Admin theo
    ngày; PR/RFQ/PO có thể chọn currency + tỷ giá tại ngày giao dịch. Không tích hợp API tỷ giá
    ngoài, không tax/landed-cost nâng cao (đẩy sang Release 2) — xem quyết định #8 trong
    `docs/pur/00_business_decisions.md`. **Không thuộc Stage 1 Foundation** —
    `PurchaseOrder`/`PurchaseOrderItem` chưa có field `currency` nào để sửa ở Stage 1, và
    `01_foundation_fsd.md` xác nhận Foundation không đụng tới trường này.
    **Sửa vị trí Stage theo review 02/08/2026 (Critical #2)**: đặt trọn `ExchangeRate` +
    `currency` ở Stage 5 tạo ra một khoảng trống — Budget commitment (Stage 3) và ngưỡng RFQ 10
    triệu VND/so sánh báo giá khác tiền tệ (Stage 4) đều cần quy đổi VND *trước khi* Stage 5 tồn
    tại. Đã tách làm 2 phần thay vì gộp hết vào Stage 5 (xem lại Stage 2/3/4/5 bên dưới):
    hạ tầng tối thiểu (bảng `ExchangeRate`, field `currency`/`estimated_unit_price` trên PR line,
    snapshot tỷ giá tại thời điểm PR duyệt/RFQ award) chuyển lên Stage 2/3/4; phần "PO nâng cao"
    còn lại của `PUR-PO-02` (payment term, delivery term, PO revision/version) vẫn ở Stage 5 —
    Epic E không đổi, chỉ tách currency ra khỏi nhóm "PO nâng cao" vì currency cần sớm hơn các
    field kia. **Đã xác nhận** — xem phần bổ sung ở cuối quyết định #8, `docs/pur/
    00_business_decisions.md` (mục "Tổng hợp mức độ sẵn sàng", dòng #8: "✅ Chốt").

### 5.2 Release 1 — Should have nếu còn capacity

- Email template và resend log cho RFQ/PO.
- Export bảng so sánh quotation ra Excel/PDF.
- Request-for-information và withdraw/resubmit PR.
- SLA reminder tính khi người dùng mở dashboard; chưa cần Celery.

### 5.3 Release 2

- Service procurement và Service Acceptance.
- Supplier onboarding, qualification, certificate expiry, blacklist và approved supplier list.
- Contract/blanket agreement, price break và release order.
- Approval parallel/delegation/escalation nâng cao.
- Multi-currency nâng cao: API tỷ giá tự động, tax/landed-cost (ngoại tệ cơ bản đã chuyển sang
  Release 1 — mục 5.1 điểm 12).
- Supplier scorecard toàn diện và corrective action.

### 5.4 Release 3

- Vendor Invoice, credit/debit note.
- 2-way matching cho dịch vụ và 3-way matching cho hàng hóa.
- Invoice tolerance, exception, hold/release và payment approval.
- Đồng bộ Actual Spend và tích hợp Accounting/ERP.
- Supplier portal, API/webhook, OCR chỉ triển khai sau khi core process ổn định.

## 6. Quy trình nghiệp vụ mục tiêu Release 1

```text
Requester tạo PR
    ↓
Department Manager duyệt nhu cầu
    ↓
Budget check + các bước duyệt theo Approval Rule
    ↓
PUR Manager triage/phân công Buyer
    ├── Direct buy hợp lệ → Allocation → PO
    └── Cần cạnh tranh → RFQ → Quotes → Evaluation → Award approval
                                      ↓
                              Allocation → một/nhiều PO
                                      ↓
                            PO approval → gửi NCC
                                      ↓
                      Buyer ghi nhận NCC confirm/reject/change
                                      ↓
                             GRN/QC hiện tại xử lý
                                      ↓
                    Cập nhật fulfillment từng PR line
```

Direct buy chỉ được dùng khi thuộc một trong các ngoại lệ cấu hình: dưới ngưỡng, NCC độc quyền,
khẩn cấp, hoặc có hợp đồng/giá đã được duyệt. Phải lưu reason code và người phê duyệt ngoại lệ.

## 7. Yêu cầu nghiệp vụ cấp cao

### Epic A — Foundation

- `PUR-FND-01`: Số lượng hiển thị trên PO phải loại GRN cancelled/rejected thống nhất với status sync.
- `PUR-FND-02`: Email log phải phân biệt not attempted/sent/failed/skipped no email/unknown legacy;
  không ghi thành công giả.
- `PUR-FND-03`: Lead-time lấy mốc gửi/xác nhận/nhận thay vì ngày tạo chứng từ.
- `PUR-FND-04`: Mọi transition chính có actor, timestamp, note và audit log.
- `PUR-FND-05`: Tổng tiền phải tính từ line và component có kiểm soát; không cho sửa total trực tiếp.
- `PUR-FND-06`: Một Product chỉ xuất hiện tối đa một dòng trong một PO.
- `PUR-FND-07`: Cho phép gửi lại email PO `SENT` khi lần gửi trước thất bại hoặc chưa có email —
  chỉ khi NCC hiện đã có `contact_email` tại thời điểm gọi (xem `01_foundation_fsd.md`).

### Epic B — PR 2.0 và Allocation

- `PUR-PR-01`: Requester tạo/sửa PR draft và nộp một lần có tối thiểu một dòng hợp lệ.
- `PUR-PR-02`: Hệ thống snapshot department/cost center tại thời điểm nộp.
- `PUR-PR-03`: Mỗi dòng có requested, approved, allocated, ordered, received, cancelled và open qty.
- `PUR-PR-04`: Không allocation vượt approved/open quantity.
- `PUR-PR-05`: Một PR line được chia cho nhiều PO line; một PO line chỉ nên đại diện một product,
  supplier và commercial condition nhưng có thể nhận allocation từ nhiều PR line tương thích.
- `PUR-PR-06`: Non-catalog goods phải được map/tạo Product trước khi sinh PO.
- `PUR-PR-07`: PR đã phát sinh PO/GRN không được hard-delete; chỉ cancel phần open với reason.

### Epic C — Approval và Budget

- `PUR-APR-01`: Rule có hiệu lực theo ngày và version; thay rule không làm đổi workflow đang chạy.
- `PUR-APR-02`: Điều kiện MVP gồm department, amount band, category, budget result, emergency.
- `PUR-APR-03`: Các bước duyệt chạy tuần tự, không cho approver tự duyệt chứng từ do mình tạo nếu
  policy không cho phép.
- `PUR-APR-04`: Reject phải có lý do; return-for-revision không được coi là reject cuối.
- `PUR-BUD-01`: Budget key tối thiểu gồm fiscal period + cost center + account/category, project tùy chọn.
- `PUR-BUD-02`: Tạo commitment ở thời điểm business xác nhận; thời điểm mặc định đề xuất là PR được
  duyệt cuối cùng.
- `PUR-BUD-03`: Điều chỉnh commitment khi approved quantity/value thay đổi; release phần hủy/không mua.
- `PUR-BUD-04`: Vượt budget phải block hoặc route thêm Finance theo policy cấu hình.

### Epic D — Sourcing/RFQ

- `PUR-RFQ-01`: Buyer tạo RFQ từ một hoặc nhiều PR line đã duyệt và còn open quantity.
- `PUR-RFQ-02`: Mỗi RFQ có deadline, commercial terms, invited suppliers và immutable sent snapshot.
- `PUR-RFQ-03`: Hệ thống lưu quotation theo NCC gồm price, discount, tax/freight cơ bản, lead time,
  payment term và validity.
- `PUR-RFQ-04`: Evaluation có tiêu chí/trọng số tổng 100%; hệ thống tính score nhưng Buyer chịu trách
  nhiệm đề xuất.
- `PUR-RFQ-05`: Award được theo từng dòng và có thể split; chọn ngoài đề xuất tốt nhất phải có reason.
- `PUR-RFQ-06`: Award cần duyệt trước khi tạo PO nếu vượt threshold hoặc có exception.
- `PUR-RFQ-07`: Quote đã dùng để award không được sửa; correction tạo version mới.

### Epic E — PO nâng cao

- `PUR-PO-01`: PO từ PR/RFQ phải có allocation trace; manual PO là exception có reason/approval.
- `PUR-PO-02`: PO snapshot supplier, currency, payment term, delivery term và address tại lúc gửi.
  Currency lấy tỷ giá từ `ExchangeRate` (nhập tay theo ngày bởi Admin, không API ngoài — quyết định
  #8 trong `docs/pur/00_business_decisions.md`); PO/RFQ chọn tỷ giá tại ngày giao dịch, snapshot lại
  giá trị tỷ giá đã dùng, không tính lại nếu `ExchangeRate` đổi sau đó.
- `PUR-PO-03`: PO đã gửi chỉ thay đổi bằng revision/change order; revision quan trọng phải duyệt lại.
- `PUR-PO-04`: Supplier response gồm pending/confirmed/rejected/change-requested và timestamp.
- `PUR-PO-05`: Không nhận hàng vượt open PO quantity ngoài tolerance hiện hành.
- `PUR-PO-06`: Đóng sớm PO phải release allocation/commitment còn mở theo rule và lưu lý do.

### Epic F — Dashboard

- `PUR-RPT-01`: Mỗi role chỉ thấy KPI và chứng từ trong data scope được phép.
- `PUR-RPT-02`: KPI drill-down về đúng danh sách chứng từ tạo nên số liệu.
- `PUR-RPT-03`: KPI dùng timestamp nghiệp vụ, không dùng `created_at` thay thế nếu đã có mốc thật.
- `PUR-RPT-04`: Công thức KPI được document và khóa version theo release.

## 8. Business rules trọng yếu

1. Tổng `allocated_qty` của PR line không vượt `approved_qty`.
2. Tổng `ordered_qty` và trạng thái PR line lấy từ allocation/PO hợp lệ, không nhập tay.
3. Khi PO draft bị xóa/cancel, allocation phải được release trong cùng transaction.
4. PO đã sent không được sửa trực tiếp line thương mại.
5. RFQ sent và quotation awarded phải lưu snapshot bất biến.
6. Không award NCC inactive/suspended; ngoại lệ chỉ Manager/Admin và phải audit.
7. Direct buy phải có exception type; threshold do Admin cấu hình, không hard-code.
8. Budget commitment không được ghi trùng khi resubmit/retry.
9. Giá trị dùng routing approval phải là giá trị snapshot tại lúc submit; thay đổi vượt ngưỡng phải
   route lại.
10. PR/PO có downstream document không được hard-delete.
11. Goods non-catalog phải được chuẩn hóa Product/UOM trước PO; service line không thuộc Release 1.
12. Mọi thao tác approve/reject/award/change order/cancel/close phải là POST và kiểm tra quyền ở service.

## 9. Mô hình dữ liệu khái niệm

Không chốt tên Django app trong BRD, nhưng phải giữ các aggregate sau:

- `PurchaseRequest` / `PurchaseRequestItem` mở rộng.
- `ProcurementAllocation`: PR item, PO item, allocated qty/value, status và release reason.
- `ApprovalPolicy`, `ApprovalRule`, `ApprovalStep`, `ApprovalInstance` hoặc mở rộng hạ tầng Approval
  hiện có nhưng phải có policy version snapshot.
- `Budget`, `BudgetLine`, `BudgetCommitment`, `BudgetTransaction`.
- `RFQ`, `RFQLine`, `RFQSupplier`, `Quotation`, `QuotationLine`.
- `EvaluationTemplate`, `EvaluationCriterion`, `BidEvaluation`, `Award`, `AwardLine`.
- `PurchaseOrderRevision` hoặc version snapshot/change log đủ khả năng tái dựng bản đã gửi.
- `DocumentAttachment`, `CommunicationLog` nếu chưa có hạ tầng generic phù hợp.

Phải quyết định ownership, unique constraint, immutable state và transaction boundary trong FSD; BRD
không ép tất cả model nằm trong app `purchasing`.

## 10. Phân quyền tối thiểu

| Action | Requester | Dept Manager | Buyer | PUR Manager | Finance | Admin |
|---|---:|---:|---:|---:|---:|---:|
| Tạo/sửa PR của mình | ✓ | ✓ | ✓ | ✓ | Theo policy | ✓ |
| Duyệt nhu cầu phòng |  | ✓ |  | Theo escalation |  | ✓ |
| Xem budget chi tiết | Theo scope | Theo scope | Theo scope | ✓ | ✓ | ✓ |
| Tạo/gửi RFQ |  |  | ✓ | ✓ |  | ✓ |
| Nhập quotation |  |  | ✓ | ✓ |  | ✓ |
| Duyệt award |  |  |  | ✓ | Theo threshold | ✓ |
| Tạo/sửa PO draft |  |  | ✓ | ✓ |  | ✓ |
| Duyệt PO/change order |  |  | Theo policy | ✓ | Theo threshold | ✓ |
| Cấu hình policy/rule |  |  |  | Theo policy | Theo policy | ✓ |

Chi tiết role/action/data scope phải được xác nhận trong permission matrix trước development sprint đầu.

## 11. Non-functional requirements

- Tất cả nghiệp vụ phân bổ, commitment, award và transition chạy trong transaction atomic.
- Chống double-submit và race condition bằng DB constraint/locking, không chỉ kiểm tra ở form.
- Danh sách 95th percentile < 2 giây với dataset mục tiêu được chốt trong Discovery.
- Mọi list lớn có pagination, filter và index theo status/owner/date/department.
- Audit có actor, before/after hoặc snapshot đủ dùng cho điều tra.
- Dữ liệu tiền dùng Decimal; currency/exchange rate phải snapshot nếu bật ngoại tệ.
- Attachment giới hạn loại/kích thước, không cho thực thi nội dung upload.
- Không lộ giá quotation cho requester/NCC khác ngoài data scope.
- Migration phải chạy lại an toàn hoặc có checkpoint/rollback script được kiểm thử.

## 12. Kế hoạch triển khai Release 1

### Stage 0 — Discovery và baseline, 1 tuần

Deliverables:

- Process map As-Is/To-Be được PUR, Finance và đại diện phòng ban ký.
- Data dictionary, permission matrix, approval decision table.
- Chốt thời điểm budget commitment, direct-buy threshold, RFQ threshold và SLA.
- Baseline KPI và volume: PR/tháng, line/PR, NCC/RFQ, concurrent users.
- Product backlog có acceptance criteria và estimate đã refine.

Exit criteria: không còn open decision mức Critical trong mục 16.

### Stage 1 — Foundation, 1–2 tuần

- Sửa ba sai lệch logic đã xác minh, cộng thêm rule Product duy nhất mỗi PO (`PUR-FND-06`, phát
  hiện qua review 02/08/2026 — xem `docs/pur/01_foundation_fsd.md` mục 4).
- Timestamp nghiệp vụ và communication status.
- Gửi lại email PO (`PUR-FND-07`, `retry_po_email()`) khi lần gửi trước `FAILED`/`SKIPPED_NO_EMAIL`
  và NCC hiện đã có `contact_email` — phát hiện qua review lần 4, xem
  `docs/pur/01_foundation_fsd.md` mục 3/4.
- Tổng tiền tối thiểu (`grand_total`, tính toán, không lưu cột).
- Backfill, regression test và data reconciliation report.

> **Attachment đã bỏ khỏi Stage 1** (sửa review 02/08/2026 — trước đó bị liệt kê mâu thuẫn ở cả
> đây, ở mục 5.2 "Should have", và ở `docs/pur/00_business_decisions.md` mục 13 nói Stage 4).
> Foundation không làm attachment; xem Stage 4 bên dưới — nơi attachment thực sự bắt buộc (RFQ/
> quotation).

Exit criteria: 139 test PUR hiện tại pass và test mới pass; PO/GRN reconciliation không sai trên mẫu UAT.

### Stage 2 — PR 2.0 và Allocation, 2–3 tuần

- Header/line lifecycle, required date, category/cost center/project.
- `ExchangeRate` master data (currency, rate_date, rate_to_vnd, nhập tay Admin) + field `currency`
  (mặc định VND) và `estimated_unit_price` trên PR line — hạ tầng tối thiểu để Stage 3 quy đổi
  ngân sách, chuyển từ Stage 5 về đây theo review 02/08/2026 (Critical #2, xem mục 5.1 điểm 12).
- Allocation split/consolidate và open quantity.
- Non-catalog goods intake + map Product gate.
- Migrate `linked_po` cũ sang allocation, giữ compatibility đọc trong thời gian chuyển đổi.

Exit criteria: UAT thành công các case 1 PR→n PO, n PR→1 PO, partial/cancel/reopen và không over-order.

### Stage 3 — Approval + Budget Commitment, 2–3 tuần

- Decision table versioned và approval serial.
- Budget plan, availability check, commitment, adjustment và release — PR ngoại tệ quy đổi VND
  bằng tỷ giá `ExchangeRate` tại ngày PR được duyệt cuối cùng (snapshot bất biến, không đổi lại
  nếu tỷ giá sau này thay đổi — xem quyết định #8 bổ sung, `docs/pur/00_business_decisions.md`).
  Chênh lệch giữa giá ước tính lúc PR duyệt và giá award thật (Stage 4) ghi nhận qua
  `BudgetTransaction` loại điều chỉnh, không sửa trực tiếp commitment gốc.
- Notification/inbox/SLA cơ bản.

Exit criteria: routing đúng toàn bộ decision-table test; retry không tạo commitment trùng; vượt budget
được block/route đúng policy; PR ngoại tệ quy đổi đúng VND theo tỷ giá snapshot.

### Stage 4 — RFQ/Quotation/Award, 3–4 tuần

- RFQ từ open PR lines, mời NCC, gửi/log.
- Quote entry/version, comparison và weighted scoring.
- Attachment dùng chung PR/RFQ/Quotation/PO (**bắt buộc**, không còn "should have" — chuyển từ
  Stage 1 về đây theo review 02/08/2026: báo giá là nơi attachment thực sự cần, không phải giai
  đoạn sửa lỗi kỹ thuật của Stage 1). 10MB/file, whitelist extension, lưu tối thiểu 5 năm — theo
  quyết định #13 `docs/pur/00_business_decisions.md`.
- Line-level award, exception reason và award approval. Trường hợp mời đủ NCC nhưng không đủ 3
  báo giá phản hồi trước deadline → exception code `INSUFFICIENT_RESPONSES`, cần PUR Manager duyệt
  kèm bằng chứng đã mời đủ NCC (bổ sung review 02/08/2026 — xem quyết định #5/#6
  `docs/pur/00_business_decisions.md`).
- Quotation mang `currency` riêng (không nhất thiết trùng PR); ngưỡng bắt buộc RFQ (10 triệu VND,
  quyết định #5) và so sánh landed cost giữa các báo giá khác tiền tệ đều quy đổi VND bằng
  `ExchangeRate` tại ngày đánh giá — tính trên **subtotal trước thuế/phí vận chuyển** (Foundation/
  Stage 1-4 chưa có field thuế/freight, những field đó vẫn ở Stage 5). Award/PO draft ghi
  `exchange_rate_snapshot` bất biến tại thời điểm award, không đổi lại nếu tỷ giá sau này thay đổi.
- Sinh allocation/PO draft từ award.

Exit criteria: chạy UAT end-to-end với ít nhất 3 NCC, split award và quote correction không làm đổi
snapshot cũ; ngưỡng RFQ/so sánh khác tiền tệ quy đổi đúng VND.

### Stage 5 — PO Revision + Dashboard, 1–2 tuần

- PO commercial snapshot đầy đủ (payment term, delivery term, incoterm), revision và supplier
  response — phần còn lại của `PUR-PO-02` sau khi tách `currency`/`ExchangeRate` ra Stage 2-4
  (review 02/08/2026, Critical #2). Không còn giới thiệu currency mới ở đây — chỉ hoàn thiện các
  field snapshot khác của PO nâng cao.
- PR/RFQ/PO queues theo role.
- KPI MVP và drill-down.

Exit criteria: change order không sửa bản PO đã gửi; KPI đối chiếu đúng với dataset UAT.

### Stage 6 — UAT, hardening và go-live, 2 tuần

- End-to-end UAT, permission/security, concurrency và performance test.
- Data migration rehearsal, training, SOP, cutover và rollback plan.
- Pilot với PUR và 1–2 phòng ban trước rollout toàn công ty.
- Hypercare tối thiểu 2 tuần sau go-live.

Exit criteria: không còn Severity 1/2 defect; Business Owner, PUR Manager và Finance ký go-live.

## 13. UAT tối thiểu

1. PR một dòng được duyệt và tạo một PO direct-buy hợp lệ.
2. Một PR nhiều dòng được award cho hai NCC và tạo hai PO.
3. Hai PR cùng SKU/cost center tương thích được gom vào một PO, trace lại được từng nguồn.
4. Split một PR line thành hai award/PO, tổng không vượt approved quantity.
5. Partial PO/cancel phần còn lại làm open qty và commitment đúng.
6. PR vượt budget được block hoặc route Finance theo rule.
7. Thay amount/category sau return-for-revision làm routing lại đúng version.
8. RFQ gửi ba NCC, hai NCC báo giá, award NCC không thấp giá nhất và bắt buộc lý do.
9. Correction quotation tạo version mới, award cũ không bị thay đổi.
10. PO đã gửi tạo revision; lịch sử cho thấy bản cũ/mới và approval tương ứng.
11. GRN cancelled không làm sai received/open quantity trên PO/PR.
12. User ngoài scope không xem được quotation, budget hoặc chứng từ phòng khác.
13. Double-click/retry không tạo PO, allocation, approval hay commitment trùng.
14. Migration PR/PO cũ bảo toàn liên kết và số lượng.

## 14. Cutover và vận hành

- Chọn ngày khóa thay đổi cấu hình approval/budget trước migration.
- Backup và rehearsal migration trên bản sao dữ liệu gần production.
- Backfill timestamp không có dữ liệu thật phải để `null/unknown`, không suy đoán thành sự kiện thật.
- Chuyển `linked_po` cũ thành allocation bằng mapping line theo product; trường hợp mơ hồ đưa vào
  exception report để xử lý thủ công.
- Trong pilot, manual PO cũ vẫn dùng theo feature flag; sau pilot mới bật enforcement allocation.
- Chuẩn bị runbook rollback, reconciliation SQL/report và người chịu trách nhiệm quyết định rollback.
- Training tách theo Requester, Approver, Buyer, PUR Manager, Finance/Admin.

## 15. Rủi ro và biện pháp

| Rủi ro | Mức | Biện pháp |
|---|---|---|
| Scope creep sang ERP/accounting | Cao | Giữ Release 1 ở Request-to-Order; change control bắt buộc |
| Budget definition chưa thống nhất | Cao | Finance ký data dictionary và commitment event trong Stage 0 |
| Allocation migration sai | Cao | Reconciliation report, rehearsal và exception queue |
| Approval engine over-design | Cao | Decision table + serial workflow cho MVP |
| Non-catalog làm vỡ GRN/Product FK | Cao | Product mapping gate; defer service procurement |
| Người dùng bỏ qua RFQ bằng manual PO | Trung bình/Cao | Exception reason, threshold, approval và dashboard compliance |
| Dữ liệu supplier/product kém | Trung bình | Data cleansing và owner cho master data trước pilot |
| Estimate 8–10 tuần thiếu buffer | Cao | Stage gate, 12–16 tuần solo và buffer UAT riêng |
| KPI không có baseline | Trung bình | Đo baseline trong Discovery, không đặt target cảm tính |
| Test suite chậm | Trung bình | Tách test layer, profiling và CI timeout phù hợp; không bỏ regression |

## 16. Open decisions bắt buộc chốt trong Discovery

> **14/14 đã chốt** ngày 2026-08-02 — xem quyết định cuối đầy đủ tại
> `docs/pur/00_business_decisions.md`. Danh sách câu hỏi gốc giữ nguyên dưới đây để tra cứu; tóm
> tắt nhanh quyết định cuối kèm theo từng câu.

1. Ai là Business Owner có quyền chốt policy cuối cùng? → **Bạn (solo) kiêm mọi vai trò.**
2. Budget kiểm soát theo cost center + category/account hay thêm project? → **Cost center +
   category/account; project tùy chọn.**
3. Commitment phát sinh khi PR final-approved hay khi PO approved/sent? → **Khi PR
   `PENDING_PUR → APPROVED`.**
4. Vượt ngân sách block cứng hay cho phép Finance override? → **Chặn cứng, không override ở
   Release 1.**
5. Ngưỡng bắt buộc RFQ và số báo giá tối thiểu theo loại mua? → **≥ 10 triệu VND, tối thiểu 3
   báo giá.**
6. Trường hợp nào được direct buy/manual PO? → **4 reason code: BELOW_THRESHOLD, SOLE_SOURCE,
   EMERGENCY, APPROVED_CONTRACT_PRICE.**
7. Award thấp điểm hơn cần ai duyệt? → **PUR Manager, kèm lý do bắt buộc.**
8. Có mua ngoại tệ trong Release 1 không; nguồn exchange rate nào? → **Có** (đổi từ đề xuất ban
   đầu "Release 2") — tỷ giá nhập tay theo ngày qua bảng `ExchangeRate`, không API ngoài. Xem
   mục 5.1 điểm 12 và 5.3 đã cập nhật ở trên.
9. Non-catalog goods do ai tạo/mapping Product và SLA bao lâu? → **Buyer/PUR Staff phụ trách PR,
   SLA 3 ngày làm việc.**
10. Có cần request type Emergency và hậu kiểm sau mua không? → **Có type Emergency; hậu kiểm đầy
    đủ đẩy Release 2.**
11. Data scope PO/quotation theo buyer, department hay toàn PUR? → **Giữ nguyên pattern PO hiện
    tại** (buyer thấy PO/RFQ của mình ở list; PUR Manager/Finance/Admin thấy toàn bộ; detail
    không giới hạn).
12. SLA cho từng bước và lịch làm việc/ngày nghỉ áp dụng thế nào? → **Đo baseline thật (≥4 tuần)
    trước khi đặt SLA cứng; giai đoạn đầu chỉ hiện aging.**
13. Volume và retention attachment/audit là bao nhiêu? → **10MB/file, lưu tối thiểu 5 năm.**
14. Pilot với phòng ban nào và tiêu chí dừng/rollout là gì? → **Pilot WAREHOUSE; rollout sau N=10
    PR/PO trôi trọn quy trình PR→RFQ→Award→PO→GRN/QC không cần can thiệp tay.**

## 17. Definition of Ready và Definition of Done

### Definition of Ready cho mỗi Epic

- Process/rule đã được Process Owner ký.
- Field/data owner và permission rõ.
- Acceptance criteria có example dữ liệu.
- Dependency/migration/impact report được đánh giá.
- Không còn open question Critical.

### Definition of Done

- Code review, migration review và automated tests pass.
- Permission, audit, concurrency và negative cases được kiểm thử.
- UAT scenario liên quan pass và có bằng chứng.
- Tài liệu SOP/help text/data dictionary được cập nhật.
- Dashboard/reconciliation không sai dữ liệu mẫu.
- Có rollback hoặc recovery procedure phù hợp mức rủi ro.
- Product Owner chấp nhận deliverable của stage.

## 18. Bước tiếp theo

1. Xác nhận tài liệu này là source of truth cho sáng kiến PUR Expansion.
2. Tổ chức workshop Discovery 3 phiên: Process, Finance/Budget, Sourcing/Approval.
3. ~~Chốt 14 open decisions~~ **Đã chốt 2026-08-02** — xem `docs/pur/00_business_decisions.md`.
   KPI baseline (mục 12: SLA từng bước) vẫn cần đo thực tế trước Stage 2, chưa có số liệu.
4. Chuyển requirement Release 1 thành backlog có traceability `Business Objective → Requirement →
   Acceptance Criteria → Test Case`.
5. Chỉ sau khi Stage 0 được ký mới chốt estimate sprint và bắt đầu thay đổi schema/code.
