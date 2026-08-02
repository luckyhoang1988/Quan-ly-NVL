# PUR 2.0 -- Business Requirements Document (BRD) & Kế hoạch triển khai 8--10 tuần

## 1. Đánh giá hiện trạng

### Điểm mạnh

-   PR có workflow nháp → duyệt 2 cấp → PUR xử lý.
-   Tạo PO từ PR.
-   PO workflow: DRAFT → APPROVED → SENT → PARTIAL_RECEIVED/RECEIVED →
    CLOSED.
-   Nhận hàng nhiều đợt (GRN), QC, cập nhật tồn kho.
-   Audit log, notification, transaction lock và phân quyền.
-   So sánh giá lịch sử và thống kê lead time NCC.

### Hạn chế chính

  Vấn đề                   Ảnh hưởng
  ------------------------ -----------------------------------------
  1 PR chỉ sinh 1 PO       Không chia nhiều NCC hoặc mua từng phần
  Chỉ hỗ trợ SKU           Không mua dịch vụ/non-catalog
  Approval cố định         Không theo giá trị, ngân sách
  Chưa có budget           Không kiểm soát cam kết chi
  Không có RFQ             Không lưu quy trình lựa chọn NCC
  Thiếu invoice matching   Không hỗ trợ 2-way/3-way matching

## 2. Phản biện & bổ sung

### Ưu tiên số 1

Không nên xây RFQ trước khi sửa mô hình: PR Item → Procurement
Allocation → PO Item

Điều này cho phép: - 1 PR → nhiều PO - 1 PO → nhiều PR - Theo dõi
ordered / received / cancelled theo từng dòng.

### Budget

Tách: - Planned - Committed - Actual

### Approval Engine

Rule gồm: - Department - Amount - Category - Budget - Emergency

### Supplier

Bổ sung: - Qualification - Blacklist - Approved Supplier List -
Scorecard

## 3. Đặc tả nghiệp vụ

### Phạm vi MVP

-   PR 2.0
-   Approval Engine
-   RFQ
-   Multi PO
-   Budget Commitment
-   Dashboard cơ bản

### Ngoài phạm vi

-   Accounting
-   Supplier Portal
-   OCR Invoice

### Quy trình

Need → PR → Approval → RFQ → Award → PO → Supplier Confirmation → GRN/QC
→ Invoice Matching → Payment

## 4. Roadmap 8--10 tuần

### Tuần 1

-   Sửa bug
-   Timestamp
-   Total Amount
-   Test

### Tuần 2--3

-   PR 2.0
-   Allocation
-   Non Catalog

### Tuần 4--5

-   Budget
-   Approval Rule

### Tuần 6--7

-   RFQ
-   Quotation
-   Award

### Tuần 8

-   PO Revision
-   Supplier Confirmation
-   Dashboard

### Tuần 9--10

-   UAT
-   Performance
-   Bug Fix
-   Go Live

## 5. KPI

-   PR Aging
-   Approval Cycle Time
-   Procurement Cycle Time
-   Spend Analysis
-   Savings
-   Supplier OTD
-   Budget Variance
-   PO Overdue
-   Invoice Exception

## 6. Tiêu chí nghiệm thu

-   Một PR sinh nhiều PO.
-   Một PO gom nhiều PR.
-   Budget Commitment hoạt động.
-   Approval Rule cấu hình được.
-   RFQ hỗ trợ nhiều NCC.
-   Award theo từng dòng.
-   Dashboard hoạt động.
-   Audit đầy đủ.
