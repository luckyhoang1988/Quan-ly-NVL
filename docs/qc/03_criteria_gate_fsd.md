# QC Expansion — 03. FSD Wave 2: Criteria Gate Nhẹ

> Trạng thái: **Đã triển khai** — xem [implementation plan](04_criteria_gate_implementation_plan.md)
> Nguồn: [`qc_plan.md`](../../qc_plan.md) §2 mục P1 / §4 lộ trình (Wave 2)
> Phạm vi: bắt buộc ghi nhận ≥1 dòng `QcInspectionItem` trước khi ghi kết quả QC tổng, cảnh báo
> (không chặn) khi kết quả tổng mâu thuẫn với dòng criteria đã lưu, và gợi ý nhập tên tiêu chuẩn
> theo `Product.category`.
> **Ngoài phạm vi Wave 2:** `REWORK` (Wave 1 đã loại), AQL/ANSI Z1.4, risk-based/skip-lot QC, đổi
> `QcInspectionItem.criteria_name`/`expected_value` thành FK cứng tới `QcCriteria`, chặn cứng submit
> khi mismatch.

## 0. Tóm tắt vấn đề

`quality.views.qc_result` hiện có 2 `<form>` độc lập cùng POST về 1 view: form lưu từng dòng
`QcInspectionItemFormSet` (FR-QC-03, PASS/FAIL từng tiêu chuẩn trên từng `GrnItem`) và form quyết
định tổng (3 nút `action=pass/fail/partial` gọi thẳng `quality.services.qc_pass`/`qc_fail`/
`qc_partial_pass`). Hai luồng này hoàn toàn tách rời: có thể bấm Pass/Fail/Partial dù `inspection`
chưa từng có dòng `QcInspectionItem` nào, và không có cảnh báo nào nếu dòng criteria đã lưu mâu
thuẫn với kết quả tổng vừa chọn (vd có dòng FAIL nhưng vẫn chọn Pass tổng). `criteria_name` là ô
text tự do, không gợi ý từ `QcCriteria` master data theo category sản phẩm dù bảng tham chiếu
(`criteria_ref`) đã hiện sẵn trên trang.

## 1. Actor và quyền

Không đổi. `qc_result` vẫn gate `qc_permission_required('approve')` (`quality/views.py`) — cùng
người được quyết định QC hiện tại (QC Inspector, Manager, Admin). Wave 2 không thêm permission mới,
không đổi ai được làm gì — chỉ thêm điều kiện **dữ liệu** (đã có ≥1 dòng criteria) trước khi hành
động quyết định tổng được phép chạy.

## 2. Gate ≥1 dòng criteria (service layer là nguồn chân lý)

Áp dụng cho cả 3 hàm trong `quality/services.py`, kiểm tra ngay đầu mỗi hàm (trước mọi side-effect
Batch/Inventory/GRN khác), tính trên **toàn phiếu QC** — không theo từng `GrnItem`:

```python
if not inspection.items.exists():
    raise ValidationError(
        'Phải nhập ít nhất 1 dòng kết quả kiểm tra tiêu chuẩn trước khi ghi nhận kết quả QC.'
    )
```

- `qc_pass(inspection, ...)`, `qc_fail(inspection, ...)`, `qc_partial_pass(inspection, item_results, ...)`
  đều raise như nhau — không có nhánh nào được miễn.
- View `quality.views.qc_result`: khi `inspection.items.count() == 0`, disable 3 nút quyết định
  (hoặc render kèm ghi chú "Chưa có dòng kết quả tiêu chuẩn nào — không thể ghi nhận kết quả QC
  tổng") — **chỉ để UX**, không thay cho gate service. Người dùng có kinh nghiệm né qua request
  thủ công vẫn bị service chặn.
- Không có ngoại lệ: kể cả FAIL toàn phần vẫn cần ≥1 dòng ghi nhận lý do FAIL dạng criteria (đã có
  sẵn field `reason` riêng ở `QcResultForm`, nhưng đó là lý do tổng — không thay thế được yêu cầu có
  ít nhất 1 dòng criteria làm bằng chứng kiểm tra).

## 3. Cảnh báo mismatch (không chặn)

Tại thời điểm render `qc_result` (GET và sau mọi POST không redirect), tính từ
`inspection.items.all()`:

```python
has_fail_item = inspection.items.filter(result=QcInspectionItem.Result.FAIL).exists()
has_pass_item = inspection.items.filter(result=QcInspectionItem.Result.PASS).exists()
```

Template hiện cảnh báo nhỏ (`text-warning small`, không phải `alert` chặn mắt) ngay cạnh nút liên
quan:
- Cạnh nút **Pass** và **Partial**: nếu `has_fail_item` → *"⚠ Đã có dòng tiêu chuẩn Không đạt."*
- Cạnh nút **Fail**: nếu `has_pass_item` → *"⚠ Đã có dòng tiêu chuẩn Đạt."*

Thuần hiển thị — không thêm field ẩn, không double-submit, không ghi thêm gì vào `AuditLog` ngoài
audit trail sẵn có của `qc_pass`/`qc_fail`/`qc_partial_pass`. Submit vẫn thành công bình thường dù
có cảnh báo.

## 4. Prefill/gợi ý criteria theo category

Tái dùng nguyên pattern JS `data-category` đã có ở `purchasing/forms.py`
(`ProductSelectWithCategory` + script trong `pr_form.html`):

### 4.1 Widget mới — `GrnItemSelectWithCategory` (`quality/forms.py`)

Mirror `ProductSelectWithCategory`: override `create_option()`, gắn
`option['attrs']['data-category'] = instance.product.category` cho mỗi `<option>` (instance ở đây
là `GrnItem`, lấy category qua `instance.product.category`). Gán làm widget của field `grn_item`
trong `QcInspectionItemForm`.

### 4.2 Data island — danh sách criteria theo category

View `qc_result` truyền thêm vào context 1 dict `category -> [{name, pass_rule, fail_rule}]` build
từ `QcCriteria.objects.filter(is_active=True)` (cùng nguồn `criteria_ref` đã truyền sẵn, chỉ nhóm
lại theo category). Template render qua `{{ criteria_by_category|json_script:"qc-criteria-by-category" }}`
(an toàn XSS, dữ liệu chỉ từ master data active, không có input người dùng).

### 4.3 JS hành vi (`qc_result.html`)

- Đổi `grn_item` ở 1 dòng formset → đọc `data-category` từ option đang chọn → lấy danh sách criteria
  khớp category từ data island → build/refresh 1 `<datalist>` gắn vào input `criteria_name` của
  đúng dòng đó (autocomplete gợi ý, người dùng vẫn gõ tự do được — không ép chọn).
- Chọn 1 gợi ý khớp đúng tên criteria có sẵn **và** ô `expected_value` của dòng đó đang trống → tự
  điền `expected_value` = `pass_rule` của criteria đó. Chỉ điền khi đang trống — không ghi đè giá
  trị người dùng đã tự nhập (cùng invariant "chỉ tự điền khi trống" đã dùng ở PUR, xem
  `test_TC_PUR_PR_01_004` làm ví dụ pattern test).
- Không đổi cấu trúc `QcInspectionItemFormSet` (`extra=3`, `can_delete=True` giữ nguyên) — đây là
  gợi ý nhập liệu, không phải sinh sẵn dòng.

`criteria_name`/`expected_value` tiếp tục là free text snapshot (không FK) — giữ nguyên lý do đã
ghi trong docstring `quality.models` (tránh vỡ lịch sử khi `QcCriteria` đổi sau).

## 5. Acceptance Criteria

| ID | AC |
|---|---|
| AC-QC-CRIT-01 | `qc_pass`/`qc_fail`/`qc_partial_pass` raise `ValidationError` khi `inspection.items.count() == 0`, không tạo Batch/Inventory/GRN side-effect nào |
| AC-QC-CRIT-02 | Có ≥1 dòng `QcInspectionItem` (bất kỳ PASS/FAIL) thì cả 3 hành động chạy bình thường như hiện tại |
| AC-QC-CRIT-03 | View `qc_result`: khi 0 dòng item, 3 nút quyết định bị disable/ẩn kèm ghi chú hướng dẫn |
| AC-QC-CRIT-04 | Có dòng FAIL đã lưu → trang hiện cảnh báo cạnh nút Pass/Partial; có dòng PASS đã lưu → cảnh báo cạnh nút Fail; submit vẫn thành công (không bị chặn) |
| AC-QC-CRIT-05 | Không có mismatch (toàn PASS hoặc toàn FAIL) → không hiện cảnh báo nào |
| AC-QC-CRIT-06 | Đổi `grn_item` trong 1 dòng formset → `data-category` đúng `product.category`; datalist gợi ý đúng tên criteria thuộc category đó (kiểm tra qua HTML render, không cần chạy JS thật trong test Django) |
| AC-QC-CRIT-07 | Context `criteria_by_category` nhóm đúng theo category, chỉ gồm `QcCriteria` `is_active=True` |

## 6. Test Cases

| TC | Map AC | Mô tả ngắn |
|---|---|---|
| TC-QC-CRIT-001 | 01 | `qc_pass` reject khi 0 items, không đổi GRN/Batch/Inventory |
| TC-QC-CRIT-002 | 01 | `qc_fail` reject khi 0 items |
| TC-QC-CRIT-003 | 01 | `qc_partial_pass` reject khi 0 items |
| TC-QC-CRIT-004 | 02 | `qc_pass` thành công khi có ≥1 item (regression, không phá luồng cũ) |
| TC-QC-CRIT-005 | 03 | View `qc_result` disable nút quyết định khi 0 item; POST `action=pass` trực tiếp (bypass UI) vẫn bị service chặn — view bắt `ValidationError`, hiện `messages.error`, không tạo Batch/Inventory/GRN side-effect nào, không crash |
| TC-QC-CRIT-006 | 04 | Có item FAIL → response chứa cảnh báo cạnh nút Pass/Partial |
| TC-QC-CRIT-007 | 04 | Có item PASS → response chứa cảnh báo cạnh nút Fail |
| TC-QC-CRIT-008 | 05 | Toàn PASS hoặc toàn FAIL → không có cảnh báo mismatch trong response |
| TC-QC-CRIT-009 | 06, 07 | Context `criteria_by_category` đúng cấu trúc, chỉ gồm active criteria; option `grn_item` có đúng `data-category` |

## 7. Ngoài phạm vi

- `REWORK` (đã loại từ Wave 1)
- AQL/ANSI Z1.4, sample-size chuẩn hoá
- Risk-based / skip-lot QC (`Product.qc_required` — P2 trong `qc_plan.md`, wave riêng)
- Đổi `criteria_name`/`expected_value` thành FK tới `QcCriteria`
- Chặn cứng (block) submit khi mismatch — chỉ cảnh báo
- KPI/báo cáo QC theo NCC/SKU (P4, wave riêng)

## 8. Doc sync kèm Wave 2

- Cập nhật `qc_plan.md` §Todos: tick Wave 2 khi triển khai xong
- `CLAUDE.md` — thêm invariant mới nếu gate/service có bug fix đáng ghi (theo mục "Established
  patterns to apply proactively" nếu phát sinh)
- `.claude/skills/wms-conventions/SKILL.md` — nếu pattern JS `data-category` được khái quát hoá
  thành quy ước dùng chung cho ≥2 app (PUR + QC), cân nhắc ghi thành 1 mục riêng thay vì lặp lại
  rải rác
