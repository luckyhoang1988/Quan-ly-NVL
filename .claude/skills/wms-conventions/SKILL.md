---
name: wms-conventions
description: Quy ước triển khai riêng của dự án NVL/WMS (đồng bộ tài liệu sau mỗi thay đổi, UI pattern cho trang detail). Dùng khi code/sửa bất kỳ phần nào của dự án này, đặc biệt khi tạo/sửa trang detail (key/value panel), hoặc khi cần biết có phải cập nhật CLAUDE.md/BACKLOG.md hay không.
---

# Quy ước dự án NVL/WMS

## 1. Đồng bộ tài liệu sau mỗi thay đổi

Sau khi hoàn thành MỘT thay đổi code (feature/fix/refactor/UI), kiểm tra và cập nhật:

- **Skill này** (`.claude/skills/wms-conventions/SKILL.md`) — nếu thay đổi thiết lập hoặc sửa một quy ước
  dùng lại được (pattern UI, cách tổ chức code, quy trình...). Thêm mục mới hoặc sửa mục cũ, đừng để skill
  lạc hậu so với code thật.
- **`CLAUDE.md`** (repo root) — nếu thay đổi là một quyết định thiết kế xuyên-module (cross-cutting), đổi
  kiến trúc đã "chốt", hoặc là quy ước áp dụng toàn project. Giữ mục "UI conventions" và "Non-obvious
  cross-cutting design decisions" khớp với code thật.
- **`BACKLOG.md`** — nếu thay đổi hoàn thành/ảnh hưởng một `FR-XX-##` đang track: tick checkbox + cập nhật
  dòng "Tổng tiến độ: X / 60 FR" ở đầu file. Business Rules/Data Model/Algorithm notes dưới mỗi module cũng
  nên tick nếu đã làm, dù không tính vào 60 FR.

Không phải thay đổi nào cũng chạm cả 3 — vd một pass polish UI thuần tuý (không gắn FR nào) chỉ cần cập
nhật skill này + CLAUDE.md, không có gì để tick trong BACKLOG.md.

## 2. Trang detail (key/value panel) — dùng `<table class="table-accent">`, không dùng `dl.row`

**Vấn đề với `dl.row`/`dt`/`dd` (cách cũ):** viền chỉ hiển thị đúng khi tổng cột `dt`+`dd` chia hết cho 12
theo đúng công thức (vd `col-sm-2`+`col-sm-4` × 2 cặp = 12), và layout chia 2 cột (`col-md-6`) chỉ có viền
phân cách từ breakpoint `md` trở lên — dưới đó vỡ layout/viền. Cách này đã bị người dùng từ chối 2 lần
trước khi chốt cách dưới đây (xem `receiving/templates/receiving/grn_detail.html` — trang tham chiếu gốc).

**Cách chuẩn:**

```html
<div class="card-body">
  <table class="table table-sm align-middle table-accent mb-0">
    <tbody>
      <tr>
        <th class="text-nowrap">Nhãn 1</th>
        <td>Giá trị 1</td>
        <th class="text-nowrap">Nhãn 2</th>
        <td>Giá trị 2</td>
      </tr>
      <tr>
        <th class="text-nowrap">Nhãn dài / field rộng</th>
        <td colspan="3">Giá trị dài (vd Ghi chú)</td>
      </tr>
    </tbody>
  </table>
</div>
```

- `table-accent` (định nghĩa ở `assets/css/custom.css`) tự cho viền ngoài + viền dọc giữa các ô + viền
  ngang giữa các hàng — không cần tính toán cột, không phụ thuộc breakpoint.
- **Mật độ**: field gốc là `dt col-sm-2`/`dd col-sm-4` (tổng 6, 2 cặp/hàng ngang) → bảng 4 cột (2 cặp
  nhãn/giá trị mỗi `<tr>`). Field gốc là `dt col-sm-3`/`dd col-sm-9` hoặc tổng 12 → bảng 2 cột (1 cặp/hàng).
  Field dài/ghi chú dùng `colspan="3"` trên bảng 4 cột.
- **Field điều kiện** (`{% if obj.x %}`): bọc quanh CẢ `<tr>...</tr>`, không tách/ghép nửa hàng theo điều
  kiện — tránh layout lệch khi điều kiện false.
- Layout chia 2 cột kiểu card (`supplier_detail.html`) vẫn giữ wrapper `.row > .col-md-6.detail-col-split +
  .col-md-6` như cũ — chỉ đổi phần `dl.row` bên TRONG mỗi cột thành bảng `table-accent` riêng, không đổi
  wrapper chia cột.
- Đã áp dụng cho toàn bộ trang detail hiện có (2026-07-25): `grn_detail`, `user_detail`, `batch_detail`,
  `supplier_detail` (7 khối), `po_detail`, `pr_detail`, `qc_override`, `gin_detail`, `so_detail`,
  `warehouse_detail`, `product_eoq`. Trang detail mới tạo sau này PHẢI theo mẫu này ngay từ đầu, không quay
  lại `dl.row`.

## 3. `LANGUAGE_CODE='vi'` KHÔNG dịch được mọi form built-in của Django — kiểm tra trước khi tin

Catalog dịch sẵn của Django (`django/conf/locale/vi/LC_MESSAGES/django.po`) chỉ đầy đủ cho các chuỗi lõi rất
phổ biến (vd `"This field is required."` → có dịch), nhưng **thưa/thiếu hẳn** cho những chuỗi ít dùng hơn của
`django.contrib.auth` — cụ thể đã xác nhận **không có** bản dịch cho `PasswordChangeForm`/`SetPasswordForm`
(label "Old password"/"New password"..., lỗi "The two password fields didn't match.", "Your old password was
entered incorrectly.") lẫn `django.contrib.auth.password_validation` (4 validator mặc định trong
`AUTH_PASSWORD_VALIDATORS`: too short/too similar/too common/entirely numeric). Set `LANGUAGE_CODE='vi'` một
mình sẽ để lộ các chuỗi này bằng tiếng Anh.

**Cách xử lý (xem `accounts/forms.py::WmsPasswordChangeForm`/`WmsSetPasswordForm`):**
- Label/help_text: ghi đè trực tiếp trong `__init__` của form con sau khi gọi `super().__init__()`.
- `error_messages` dict (`password_mismatch`, `password_incorrect`): ghi đè bằng cách merge
  `{**ParentForm.error_messages, 'key': 'chuỗi tiếng Việt'}` trên class con.
- Lỗi độ mạnh mật khẩu từ `AUTH_PASSWORD_VALIDATORS` (raise qua `password_validation.validate_password()`
  trong `SetPasswordMixin.validate_password_for_user`) không có hook ghi đè message trực tiếp — phải override
  hẳn method này, bắt `ValidationError`, map lại từng lỗi theo `error.code` (`password_too_short`,
  `password_too_similar`, `password_too_common`, `password_entirely_numeric`) sang tiếng Việt, fallback về
  message gốc nếu gặp code lạ (validator custom thêm sau này).
- **Trước khi tin `LANGUAGE_CODE='vi'` đã dịch xong một form/thông báo built-in nào đó của Django (đặc biệt
  ngoài `django/forms/` lõi), hãy grep thử chuỗi tiếng Anh gốc trong `django.po` của bản Django đang cài để
  xác nhận, đừng giả định.**
