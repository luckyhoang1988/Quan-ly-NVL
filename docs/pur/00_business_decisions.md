# PUR Expansion — 00. Quyết định nghiệp vụ (Discovery / Stage 0)

> Trạng thái file: **Confirmed — đã chốt toàn bộ 19 mục** (14 mục gốc qua AskUserQuestion ngày
> 2026-08-02, cập nhật lại mục 4/5/6/8/14 theo review 02/08/2026; **+ 5 mục bổ sung DEFERRED —
> mục 15-19 — chốt qua AskUserQuestion ngày 03/08/2026**, xem phần "Quyết định bổ sung" cuối file).
> Nguồn: mục 16 `PUR_EXPANSION_MASTER_PLAN.md` (14 open decision), đối chiếu với nội dung đã có
> sẵn ở các mục 2/5/6/7/10 của cùng tài liệu; mục 15-19 phát hiện qua review lần 4 (02/08/2026)
> trên `01_foundation_fsd.md`.
> File này **không chặn** `01_foundation_fsd.md` — Stage Foundation không phụ thuộc bất kỳ mục nào
> dưới đây. **Không còn mục nào chặn Stage 2 Discovery/FSD** — toàn bộ 19 mục đã chốt, có thể bắt
> đầu viết `docs/pur/02_stage2_fsd.md`.
> **Mục 8 (ngoại tệ) cần đọc lại phụ lục trước khi brainstorm Stage 2** — vị trí Stage của
> `ExchangeRate`/currency đã đổi so với bản chốt đầu (Critical #2, xem phụ lục trong mục 8).

Cách đọc mỗi mục: **Câu hỏi gốc** → **Đề xuất ban đầu** (nếu có) → **Quyết định cuối** (đã chốt
qua lựa chọn trắc nghiệm trực tiếp với bạn — không phải suy đoán).

---

## 1. Ai là Business Owner có quyền chốt policy cuối cùng?

**Đề xuất**: bạn (solo-dev) đóng vai Business Owner kiêm PUR Manager kiêm Finance cho toàn bộ MVP
Release 1 — vai trò trong bảng stakeholder mục 4 master plan ánh xạ vào role `MANAGER`/`ADMIN` đã
có, không cần role mới.

**Quyết định cuối**: **Xác nhận đề xuất** — bạn (solo) kiêm mọi vai trò Business
Owner/PUR Manager/Finance cho Release 1.

---

## 2. Budget kiểm soát theo cost center + category/account, hay thêm project?

**Đề xuất** (nguồn: `PUR-BUD-01`, mục 7): budget key bắt buộc gồm **fiscal period + cost center +
category/account**; `project` là field tùy chọn, không nằm trong khoá bắt buộc của MVP.

**Quyết định cuối**: **Xác nhận đề xuất** — khoá bắt buộc là cost center + category/account;
`project` chỉ là field tùy chọn, không tham gia khoá ngân sách ở MVP.

---

## 3. Commitment phát sinh khi PR final-approved hay khi PO approved/sent?

**Đề xuất** (nguồn: `PUR-BUD-02`, mục 7): commitment ghi nhận tại thời điểm **PR được duyệt cuối
cùng** (`PENDING_PUR → APPROVED`), không đợi đến khi PO được tạo/gửi.

**Quyết định cuối**: **Xác nhận đề xuất** — commitment ghi nhận ngay khi PR chuyển
`PENDING_PUR → APPROVED`.

---

## 4. Vượt ngân sách bị chặn cứng hay được chuyển Finance duyệt?

**Đề xuất** (nguồn: mục 2 bảng đối chiếu): mặc định **block cứng** khi vượt budget trong
Release 1, không có luồng Finance-override riêng.

**Quyết định cuối**: **Xác nhận đề xuất** — chặn cứng, không cho tạo PR/PO vượt ngân sách còn
lại trong Release 1. Route-to-Finance-override để dành cho Release 2 nếu phát sinh nhu cầu thật.

> **Phụ lục làm rõ (bổ sung review 02/08/2026)** — "chặn cứng" không có nghĩa chặn ngay lúc lưu
> Draft, dễ hiểu nhầm thành "không cho requester lưu nhu cầu để xin điều chỉnh ngân sách". Áp
> dụng theo 4 điểm cụ thể:
> 1. PR **Draft** luôn lưu được, không kiểm tra ngân sách ở bước này (người yêu cầu có thể ghi
>    nhận nhu cầu trước, xin điều chỉnh ngân sách sau nếu cần).
> 2. Kiểm tra ngân sách chạy tại **submit** (PR rời `DRAFT`) và lại một lần nữa tại **final
>    approval** (`PENDING_PUR → APPROVED`, thời điểm commitment thật sự phát sinh — xem quyết
>    định #3) — kiểm 2 lần vì ngân sách có thể đã bị PR khác tiêu bớt giữa lúc submit và lúc
>    duyệt cuối.
> 3. **Chặn cứng** áp dụng tại final approval/commitment: không cho PR chuyển `APPROVED` nếu vượt
>    ngân sách còn lại tại thời điểm đó.
> 4. PO sinh từ PR đã có commitment không được vượt **approved/committed amount** ngoài dung sai
>    (tolerance) đã cấu hình — chênh lệch giá award thật so với ước tính PR xử lý qua
>    `BudgetTransaction` điều chỉnh (xem Stage 3, `PUR_EXPANSION_MASTER_PLAN.md`), không phải lý
>    do để chặn cứng PO nếu nằm trong tolerance.

---

## 5. Giá trị bao nhiêu (VND) thì bắt buộc RFQ, và RFQ tối thiểu cần bao nhiêu báo giá?

**Chưa có đề xuất ban đầu** — không tài liệu nguồn nào đưa ra một con số VND cụ thể.

**Quyết định cuối**: **≥ 10.000.000 VND (10 triệu VND) bắt buộc RFQ**; RFQ tối thiểu cần
**3 báo giá** từ các NCC khác nhau. Dưới 10 triệu VND thuộc diện `BELOW_THRESHOLD` (mục 6 dưới
đây) — được mua trực tiếp không cần RFQ. Ngưỡng 10 triệu tính trên **subtotal trước thuế/phí vận
chuyển** (bổ sung review 02/08/2026 — nhất quán vì thuế/freight là field của Stage 5, ngưỡng RFQ
phải dùng được từ Stage 4, trước khi các field đó tồn tại).

> **Phụ lục — trường hợp không đủ 3 báo giá phản hồi (bổ sung review 02/08/2026)**: quyết định
> gốc chưa có rule khi đã mời đủ ≥3 NCC nhưng chỉ 1-2 NCC phản hồi trước deadline. Thêm exception
> code **`INSUFFICIENT_RESPONSES`** (nhóm với 4 reason code ở mục 6 dưới đây, nhưng dùng khi RFQ
> *đã* phát hành đúng quy trình chứ không phải bỏ qua RFQ từ đầu) — PUR Manager duyệt kèm bằng
> chứng đã mời đủ NCC (danh sách NCC đã mời + log gửi RFQ) mới được award với dưới 3 báo giá.

---

## 6. Trường hợp nào được mua trực tiếp (direct buy / manual PO, bỏ qua RFQ)?

**Đề xuất** (nguồn: mục 6 "Quy trình nghiệp vụ mục tiêu Release 1"): đúng 4 loại reason code —

1. `BELOW_THRESHOLD` — dưới ngưỡng bắt buộc RFQ (10 triệu VND, xem mục 5).
2. `SOLE_SOURCE` — NCC độc quyền, không có lựa chọn thay thế.
3. `EMERGENCY` — khẩn cấp (xem mục 10 dưới đây).
4. `APPROVED_CONTRACT_PRICE` — đã có hợp đồng/bảng giá được duyệt trước đó.

**Quyết định cuối**: **Xác nhận đề xuất** — giữ đúng 4 reason code trên. Mỗi lần dùng phải lưu
`reason_code` + người phê duyệt ngoại lệ.

> **Phụ lục — `APPROVED_CONTRACT_PRICE` khi chưa có Contract module (bổ sung review 02/08/2026)**:
> reason code này dùng được ngay Release 1 dù module Contract/blanket agreement đầy đủ đẩy sang
> Release 2 (mục 5.3 master plan) — vì Release 1 chỉ cần lưu tối thiểu 4 trường làm bằng chứng,
> không cần cả object model Contract:
> 1. Số hợp đồng/tham chiếu (`contract_reference`, text tự do).
> 2. Ngày hiệu lực giá đã duyệt.
> 3. File hoặc link chứng từ (dùng chung hạ tầng attachment của Stage 4, quyết định #13).
> 4. Người xác nhận giá còn hiệu lực tại thời điểm dùng reason code này.

---

## 7. Ai được chọn NCC không có điểm đánh giá cao nhất?

**Đề xuất** (nguồn: `PUR-RFQ-05`/`PUR-RFQ-06` mục 7, bảng phân quyền mục 10): **PUR Manager**
duyệt mọi trường hợp award không chọn NCC điểm cao nhất, bắt buộc kèm lý do.

**Quyết định cuối**: **Xác nhận đề xuất** — PUR Manager duyệt mọi trường hợp, kèm lý do bắt
buộc. Không cần thêm ngưỡng giá trị PO để route thêm sang Finance ở Release 1 (khác đề xuất phụ
đã cân nhắc trong bản gốc — bỏ nhánh Finance-threshold vì Release 1 không có role Finance tách
biệt, xem mục 1).

---

## 8. Có mua bằng ngoại tệ trong Release 1 không? Nếu có, nguồn tỷ giá lấy từ đâu?

**Đề xuất ban đầu**: KHÔNG — Release 1 chỉ giao dịch VND, ngoại tệ đẩy sang Release 2 (theo mục
5.3 master plan lúc đó).

**Quyết định cuối**: **CÓ — thay đổi so với đề xuất ban đầu.** Release 1 hỗ trợ giao dịch ngoại
tệ ngay từ đầu, không đợi Release 2. Nguồn tỷ giá: **Admin nhập tỷ giá thủ công theo ngày** vào
một bảng master data mới (`ExchangeRate`: currency, rate_date, rate_to_vnd) — PO/RFQ chọn tỷ giá
tại ngày giao dịch từ bảng này; **không** tích hợp API tỷ giá ngoài (giữ đúng pattern solo-dev
hiện có — không phụ thuộc dịch vụ bên thứ ba, xem `⏸️` convention trong `CLAUDE.md`).

> ✅ **Đã đồng bộ `PUR_EXPANSION_MASTER_PLAN.md`** — mục 5.1 điểm 12, mục 5.3, Epic E (`PUR-PO-02`)
> và Stage 5 đều đã cập nhật ngày 02/08/2026. `ExchangeRate` + currency snapshot **không phải**
> Stage 1 Foundation — `PurchaseOrder`/`PurchaseOrderItem` chưa có field `currency` nào ở Stage 1,
> và `01_foundation_fsd.md` xác nhận Foundation không đụng tới trường này.
>
> ⚠️ **Sửa lại vị trí Stage (Critical #2, review 02/08/2026)** — bản chốt đầu tiên (đặt trọn
> `ExchangeRate`/currency ở Stage 5) tạo ra một lỗ hổng phụ thuộc: Budget commitment phát sinh khi
> PR duyệt ở **Stage 3** (quyết định #3), ngưỡng RFQ 10 triệu VND và so sánh báo giá khác tiền tệ
> ở **Stage 4** (quyết định #5) — cả hai đều cần tỷ giá *trước khi* Stage 5 tồn tại. Nếu PR/báo
> giá là USD, Stage 3-4 không có cách quy đổi VND để kiểm ngân sách/ngưỡng RFQ/tạo commitment.
> **Phương án chọn: dời hạ tầng tối thiểu lên sớm hơn** (giữ ngoại tệ cho RFQ Release 1 → dời tỷ
> giá lên sớm hợp lý hơn co lại chỉ hỗ trợ VND tới Stage 5):
> - Bảng `ExchangeRate` + field `currency`/`estimated_unit_price` trên PR line → **Stage 2**.
> - Quy đổi VND để kiểm ngân sách, snapshot tỷ giá tại **thời điểm PR duyệt cuối cùng** → **Stage
>   3** (cùng lúc với commitment, quyết định #3).
> - Quotation mang `currency` riêng, ngưỡng RFQ/so sánh khác tiền tệ quy đổi VND → **Stage 4**.
> - Phần còn lại của `PUR-PO-02` (payment term, delivery term, incoterm, PO revision/version) vẫn
>   ở **Stage 5** — chỉ tách currency ra khỏi nhóm này vì nó cần sớm hơn, Epic E không đổi tên/gộp
>   lại nội dung nào khác.
>
> Trả lời 4 câu hỏi phụ đã nêu khi phát hiện vấn đề này:
> 1. **PR có `estimated_unit_price` và `currency` không?** Có — bắt buộc trên PR line từ Stage 2,
>    `currency` mặc định VND.
> 2. **Tỷ giá nào dùng khi PR approval?** Tỷ giá `ExchangeRate` có `rate_date` gần nhất, không
>    muộn hơn ngày PR được duyệt cuối cùng — snapshot vào PR line tại thời điểm đó, **bất biến
>    sau đó** kể cả nếu `ExchangeRate` gốc bị sửa/thêm bản ghi mới sau này.
> 3. **Commitment điều chỉnh thế nào khi RFQ award khác giá ước tính?** Không sửa trực tiếp
>    commitment gốc — ghi nhận chênh lệch qua một `BudgetTransaction` loại điều chỉnh riêng khi PO
>    được tạo từ award (Stage 4→PO draft), giữ nguyên audit trail của commitment ban đầu.
> 4. **Ngưỡng 10 triệu tính trước hay sau thuế/freight?** **Trước** — trên subtotal (giá trị hàng
>    hoá thuần), vì field thuế/freight chỉ có ở Stage 5 trong khi ngưỡng RFQ phải dùng được từ
>    Stage 4 (xem quyết định #5 đã cập nhật ở trên).
>
> Nguyên tắc bất biến áp dụng cho `ExchangeRate` giống hệt cách áp dụng cho snapshot báo giá/PO:
> **thay đổi tỷ giá sau này không được làm đổi snapshot cũ** đã lưu trên PR/RFQ/PO đã chốt.
>
> **Đây là sửa đổi so với bản "Confirmed" ban đầu của mục này** — dù bạn (solo-dev) kiêm Business
> Owner nên có thể tự chốt luôn (quyết định #1), vẫn đánh dấu rõ ở đây thay vì âm thầm viết đè, vì
> nó dời phạm vi Stage 2-4 (thêm field/bảng mới) so với những gì `PUR_EXPANSION_MASTER_PLAN.md`
> mô tả trước review này. Đọc lại đoạn này trước khi bắt đầu brainstorm Stage 2 — nếu muốn giữ
> phương án cũ (chỉ hỗ trợ VND tới Stage 5) thay vì phương án trên, sửa trực tiếp tại đây.

---

## 9. Ai chịu trách nhiệm chuyển hàng non-catalog thành Product, và SLA bao lâu?

**Đề xuất một phần** (nguồn: `PUR-PR-06` mục 7): Buyer/PUR Staff phụ trách PR đó tạo Product
nháp ngay khi tiếp nhận một PR có dòng non-catalog, trước khi phát RFQ/tạo PO cho dòng đó (chờ
duyệt theo quy trình `catalog` hiện có).

**Quyết định cuối**: **Xác nhận đề xuất về owner.** SLA: **tối đa 3 ngày làm việc** kể từ khi PR
có dòng non-catalog được PUR tiếp nhận, đến khi Product nháp được tạo và sẵn sàng để RFQ/PO sử
dụng.

---

## 10. Có cần request type "Emergency" và hậu kiểm (retroactive approval) sau khi mua không?

**Đề xuất** (nguồn: `PUR-APR-02` mục 7, mục 6): **Có** — "Emergency" là 1 request type/reason
code hợp lệ trong Release 1. Hậu kiểm đầy đủ đẩy sang Release 2; MVP chỉ ghi nhận lý do + người
phê duyệt ngoại lệ tại thời điểm mua.

**Quyết định cuối**: **Xác nhận đề xuất** — có Emergency request type/reason code ngay Release 1;
hậu kiểm (workflow duyệt lại sau khi đã mua) đẩy sang Release 2.

---

## 11. Data scope xem PO/quotation: theo buyer, theo department, hay toàn bộ PUR?

**Đề xuất** (nguồn: hiện trạng code PO — `CLAUDE.md` mục "Non-obvious cross-cutting design
decisions § PO"): giữ nguyên pattern PO hiện tại khi mở rộng sang RFQ/Quotation — buyer chỉ thấy
RFQ/quotation do mình tạo ở danh sách; PUR Manager/Finance/Admin thấy toàn bộ; chi tiết không
chặn theo người tạo.

**Quyết định cuối**: **Xác nhận đề xuất** — giữ nguyên pattern PO hiện tại cho cả RFQ/Quotation
ở Stage 3.

---

## 12. SLA cho từng bước duyệt, và áp dụng lịch làm việc/ngày nghỉ thế nào?

**Đề xuất ban đầu**: chưa có số liệu — cần đo baseline thật trước (tối thiểu 4 tuần dữ liệu),
không suy đoán một con số ngày làm việc.

**Quyết định cuối**: **Xác nhận đề xuất** — đo baseline thật (tối thiểu 4 tuần vận hành, hoặc mẫu
hồ sơ thủ công tương đương) trước khi đặt SLA cứng. Ở Stage Foundation/Stage 2, dashboard/danh
sách PR/RFQ/PO chỉ hiển thị **aging** (số ngày đang chờ duyệt tại bước hiện tại), **chưa** cảnh
báo vi phạm ngưỡng — SLA cứng + quy tắc lịch làm việc/ngày nghỉ sẽ chốt lại sau khi có dữ liệu
baseline thật, ở một vòng quyết định riêng.

---

## 13. Volume và retention (thời gian lưu) cho attachment/audit là bao nhiêu?

**Đề xuất ban đầu**: chưa có số liệu — chỉ có sẵn pattern kỹ thuật
(`quality.models.validate_image_upload`: giới hạn size + whitelist extension).

**Quyết định cuối**: **10MB tối đa mỗi file đính kèm**; **lưu trữ tối thiểu 5 năm** cho cả
attachment (PR/RFQ/PO) và audit log liên quan (khớp thông lệ lưu chứng từ kế toán phổ biến). Áp
dụng lại pattern giới hạn size + whitelist extension đã có ở `quality.models.validate_image_upload`
khi build attachment ở Stage 4.

---

## 14. Pilot với phòng ban nào, tiêu chí dừng/rollout toàn công ty là gì?

**Đề xuất ban đầu**: chưa có gợi ý cụ thể — phụ thuộc cơ cấu phòng ban thật.

**Quyết định cuối**: Pilot với phòng ban **WAREHOUSE (Kho)** — đơn vị phát sinh PR/mua NVL nhiều
nhất trong hệ thống hiện tại. Tiêu chí rollout toàn công ty **(sửa lại cho rõ ràng, review
02/08/2026 — "N=10 PR/PO" trước đây mơ hồ về đơn vị đếm và mức lỗi chấp nhận được)**: tối thiểu
**10 PR hoàn chỉnh** (mỗi PR có thể sinh một hoặc nhiều PO), đi qua toàn bộ quy trình PR → duyệt →
RFQ → Award → PO → GRN/QC, **không phát sinh lỗi Severity 1/2** trong quá trình đó. Cho phép có
bug nhỏ (Severity 3/4) đã biết và đã ghi nhận — yêu cầu "không có hotfix nào" bị coi là quá cứng
cho một pilot thật. Tiêu chí dừng pilot (rollback): phát sinh lỗi chặn workflow (mất dữ liệu, sai
lệch ngân sách/tồn kho) trong quá trình pilot.

---

## Tổng hợp mức độ sẵn sàng

| # | Câu hỏi | Trạng thái |
|---|---|---|
| 1 | Business Owner | ✅ Chốt — solo kiêm mọi vai trò |
| 2 | Budget key | ✅ Chốt — cost center + category/account |
| 3 | Thời điểm commitment | ✅ Chốt — khi PR APPROVED |
| 4 | Vượt budget block/override | ✅ Chốt — chặn cứng **[phụ lục 02/08]**: Draft không chặn, kiểm tra tại submit + final approval |
| 5 | Ngưỡng RFQ + số báo giá tối thiểu | ✅ Chốt — ≥10 triệu VND (trước thuế/freight), tối thiểu 3 báo giá **[+ `INSUFFICIENT_RESPONSES`]** |
| 6 | Trường hợp direct buy | ✅ Chốt — 4 reason code **[+ phụ lục `APPROVED_CONTRACT_PRICE` tối thiểu 4 trường]** |
| 7 | Duyệt award không cao điểm nhất | ✅ Chốt — PUR Manager, kèm lý do |
| 8 | Ngoại tệ | ✅ Chốt — **CÓ** ngay Release 1 **[sửa Stage 02/08]**: hạ tầng tối thiểu dời lên Stage 2-4, phần snapshot đầy đủ còn lại Stage 5 |
| 9 | Non-catalog owner/SLA | ✅ Chốt — Buyer/PUR Staff, SLA 3 ngày làm việc |
| 10 | Emergency + hậu kiểm | ✅ Chốt — có type, hậu kiểm đẩy Release 2 |
| 11 | Data scope PO/quotation | ✅ Chốt — giữ pattern PO hiện tại |
| 12 | SLA từng bước + lịch làm việc | ✅ Chốt — đo baseline thật trước, dashboard chỉ hiện aging |
| 13 | Volume/retention attachment/audit | ✅ Chốt — 10MB/file, lưu 5 năm; attachment tự thân build ở Stage 4 (không phải Stage 1) |
| 14 | Pilot phòng ban + tiêu chí rollout | ✅ Chốt — WAREHOUSE, **[sửa 02/08]** 10 PR trôi trọn quy trình, không lỗi Severity 1/2 |
| 15 | Ngưỡng RFQ: line/PR/cộng dồn | ✅ Chốt — theo PR (subtotal cả PR), không cộng dồn nhiều PR ở Release 1 |
| 16 | 3 báo giá: theo RFQ hay theo line | ✅ Chốt — theo toàn bộ RFQ, `INSUFFICIENT_RESPONSES` áp dụng riêng theo line thiếu |
| 17 | Budget tolerance % + xử lý khi vượt | ✅ Chốt — 10%, vượt route thêm Finance duyệt (không chặn cứng, không chỉ cảnh báo) |
| 18 | `ExchangeRate` thiếu cho ngày snapshot | ✅ Chốt — dùng tỷ giá gần nhất ≤5 ngày + cảnh báo; quá 5 ngày thì chặn |
| 19 | Scenario bắt buộc trong pilot 10 PR | ✅ Chốt — chỉ định trước checklist, không để pilot tự phát sinh ngẫu nhiên |

**19/19 mục đã chốt** — 14 mục gốc (mục 4/5/6/8/14 có phụ lục làm rõ thêm sau review 02/08/2026,
không đổi kết luận gốc, chỉ làm rõ ranh giới áp dụng hoặc sửa vị trí Stage) + 5 mục bổ sung DEFERRED
(mục 15-19, chốt 03/08/2026, không còn điểm nào chặn Stage 2 Discovery/FSD). Mục 8 (ngoại tệ) là thay đổi phạm vi thật hai lần: lần đầu (2026-08-02) đổi từ "không
ngoại tệ" sang "có ngoại tệ Release 1"; lần hai (review 02/08/2026, Critical #2) sửa vị trí Stage
từ "gộp hết Stage 5" sang "hạ tầng tối thiểu ở Stage 2-4, phần snapshot đầy đủ còn lại Stage 5" —
cả hai lần đã đồng bộ ngược `PUR_EXPANSION_MASTER_PLAN.md` (mục 5.1/5.3/Stage 2-5). Attachment
(mục 13) cũng đổi vị trí Stage cùng đợt review này: Stage 4, không phải Stage 1 — đã sửa mâu thuẫn
3 nơi (master plan Stage 1 + mục 5.2, và chính mục 13 này vốn đã đúng từ đầu).
`01_foundation_fsd.md` đã cập nhật thêm rule Product duy nhất mỗi PO (`PUR-FND-06`, phát hiện qua
review 02/08/2026) — không liên quan 14 quyết định ở đây, đây là lỗi kỹ thuật thuần của Stage 1,
xem file đó.

---

## Quyết định bổ sung — DEFERRED trước Stage 2

Phát hiện qua review lần 4 (02/08/2026) trên `01_foundation_fsd.md`: 14 mục ở trên là quyết định
Release 1 nói chung, còn 5 câu hỏi dưới đây là chi tiết kỹ thuật riêng của RFQ/Budget (Stage 2-4)
chưa từng được liệt kê thành mục riêng ở đâu. Chốt qua AskUserQuestion ngày 03/08/2026 (đề xuất
kèm lý lẽ kỹ thuật cho từng mục, người dùng chọn phương án — không phải suy đoán một chiều).

---

## 15. Ngưỡng RFQ (quyết định #5) tính theo line, theo PR, hay cộng dồn theo product/period?

**Câu hỏi gốc**: ngưỡng ≥10 triệu VND tính theo line, theo PR, hay theo nhu cầu cộng dồn của cùng
một product/period?

**Quyết định cuối**: **Theo PR** — tổng giá trị (subtotal trước thuế/freight, xem quyết định #5)
của cả PR ≥10 triệu VND thì bắt buộc RFQ cho toàn bộ PR, dù từng line riêng lẻ dưới ngưỡng. Chặn
được kiểu lách bằng cách chia nhỏ line trong cùng một PR. Cộng dồn nhiều PR khác nhau của cùng
product/period **không** xây cơ chế tự động ở Release 1 — nếu phát hiện lách bằng nhiều PR nhỏ
liên tiếp trong thực tế vận hành, xử lý bằng kiểm soát thủ công (PUR Manager rà soát), để dành cơ
chế cộng dồn tự động cho Release 2 nếu cần thật.

---

## 16. Yêu cầu tối thiểu 3 báo giá (quyết định #5) áp dụng theo RFQ hay theo line?

**Câu hỏi gốc**: một RFQ có thể gồm nhiều line với tập NCC mời khác nhau — yêu cầu ≥3 báo giá áp
dụng cho toàn bộ RFQ hay cho từng line riêng lẻ?

**Quyết định cuối**: **Theo toàn bộ RFQ** — cần ≥3 NCC gửi báo giá cho RFQ nói chung (không bắt
buộc mỗi NCC báo đủ mọi line). Khi so sánh/award từng line, chỉ so sánh trong tập báo giá đã nhận
cho line đó — nếu một line cụ thể cuối cùng có dưới 3 báo giá dù RFQ tổng thể đủ 3 NCC phản hồi, áp
dụng exception `INSUFFICIENT_RESPONSES` (quyết định #5, phụ lục) riêng cho line đó.

---

## 17. Budget tolerance bằng bao nhiêu %, xử lý thế nào khi PO vượt tolerance so với PR đã commit?

**Câu hỏi gốc**: quyết định #4 (phụ lục, điểm 4) đã nêu PO không được vượt approved/committed
amount "ngoài dung sai (tolerance) đã cấu hình" nhưng chưa chốt con số % và hướng xử lý khi vượt.

**Quyết định cuối**: **Tolerance 10%.** PO có giá trị vượt quá 10% so với commitment gốc từ PR
(chênh lệch giá award thật so với ước tính PR) phải **route thêm một bước duyệt Finance** trước
khi PO được gửi đi — không tự động chặn cứng, cũng không chỉ dừng ở cảnh báo. Trong tolerance
(≤10%): xử lý như quyết định #4 điểm 4 đã chốt (ghi nhận qua `BudgetTransaction` điều chỉnh, không
chặn, không cần thêm duyệt).

> **Lưu ý phạm vi — không mâu thuẫn với quyết định #4**: quyết định #4 chốt "chặn cứng, không có
> luồng Finance-override" cho **kiểm tra ngân sách còn lại tại thời điểm PR submit/final
> approval** (quyết định có được tạo/duyệt PR hay không — luồng Finance-override cho bước này vẫn
> để dành Release 2 như đã chốt). Quyết định #17 này là một cơ chế **khác, hẹp hơn và ở giai đoạn
> sau** — chỉ áp dụng cho phần chênh lệch giữa giá PO thật (sau RFQ/award) và số tiền đã commit từ
> PR, đúng như điểm 4 của quyết định #4 đã dự trù sẵn khái niệm tolerance nhưng chưa có con số.

---

## 18. Khi không có `ExchangeRate` hợp lệ cho ngày cần snapshot, hệ thống xử lý sao?

**Câu hỏi gốc**: Admin có thể quên nhập tỷ giá cho một ngày cụ thể — hệ thống chặn transition hay
dùng tỷ giá gần nhất trước đó kèm cảnh báo?

**Quyết định cuối**: **Dùng `ExchangeRate` có `rate_date` gần nhất trước ngày cần snapshot**, kèm
cảnh báo rõ ràng trên UI và ghi audit log — **nhưng chỉ chấp nhận nếu tỷ giá đó không cũ quá 5
ngày lịch** (tính cả cuối tuần) so với ngày cần snapshot. Nếu không có `ExchangeRate` nào trong
vòng 5 ngày gần nhất, **chặn transition**, bắt buộc Admin nhập tỷ giá mới trước khi tiếp tục. Đây
là quyết định trực tiếp mở khóa Stage 2 — trong 5 mục DEFERRED, đây là mục duy nhất từng được xác
định là điểm chặn cứng cho phạm vi Stage 2 (PR Allocation).

---

## 19. Bộ scenario bắt buộc trong 10 PR pilot (quyết định #14) — chỉ định trước hay tự phát sinh?

**Câu hỏi gốc**: pilot 10 PR (quyết định #14) có tự phát sinh đủ case (ngoại tệ, direct buy, RFQ
đủ/thiếu báo giá, vượt budget...) hay cần chỉ định trước từng case để không lọt case quan trọng?

**Quyết định cuối**: **Chỉ định trước checklist case bắt buộc**, không để pilot tự phát sinh ngẫu
nhiên. Checklist tối thiểu phải xuất hiện trong 10 PR pilot (chốt danh sách chi tiết cuối cùng khi
viết kế hoạch UAT/`02_stage2_fsd.md`, khung tối thiểu ở đây):

- Ít nhất 1 PR có `currency` khác VND (quyết định #8).
- Ít nhất 1 PR direct buy cho mỗi reason code trong 4 loại (quyết định #6).
- Ít nhất 1 RFQ đủ ≥3 báo giá, và ít nhất 1 RFQ dùng exception `INSUFFICIENT_RESPONSES` (quyết
  định #5/#16).
- Ít nhất 1 PO vượt budget tolerance 10% (quyết định #17) để xác nhận route Finance hoạt động
  đúng.
- Ít nhất 1 lần hệ thống rơi vào trường hợp thiếu `ExchangeRate` (quyết định #18) để xác nhận
  cảnh báo/chặn đúng theo ngưỡng 5 ngày.

---

**Trạng thái: Confirmed — đã chốt cả 5 mục (15-19) qua AskUserQuestion ngày 03/08/2026.** Không
còn điểm nào chặn Stage 2 Discovery/FSD trong file này.
