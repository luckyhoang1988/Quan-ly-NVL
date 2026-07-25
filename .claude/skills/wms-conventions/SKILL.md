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

## 4. Phân quyền theo phòng ban (`department`/`is_manager`) + Thông báo trong app — hạ tầng dùng chung

Từ 2026-07-25, `accounts.User` có **2 trục phân quyền song song, không thay thế nhau**:

- `role` (cũ) — quyết định ma trận CRUD qua `user.can(action, module)` (`accounts/permissions.py`). KHÔNG
  đụng vào khi thêm luồng duyệt mới.
- `department` (`WAREHOUSE`/`QC`/`PURCHASING`/`ACCOUNTING`) + `is_manager` (bool) — trục MỚI, chỉ dùng cho
  luồng duyệt/hủy/thông báo theo phòng ban. Kiểm tra bằng `user.is_department_manager(department)` (helper ở
  `accounts/models.py`), KHÔNG dùng `user.role == 'MANAGER'` cho việc này (role MANAGER chỉ còn là chức danh
  CRUD cơ bản của phòng Kho, không còn đại diện "ai được duyệt" nữa).

**Gửi thông báo trong app**: dùng `accounts.notifications.notify(recipients, verb, target=None)` — nhận 1
user hoặc list/queryset, tự bulk-create `Notification` + tự loại trùng theo pk. Đừng tạo `Notification` trực
tiếp. Badge số chưa đọc + dropdown hiện tự động ở mọi trang qua context processor
`accounts.context_processors.notifications` (đã đăng ký trong `config/settings.py` TEMPLATES) — view mới
KHÔNG cần tự truyền `unread_notification_count` vào context.

**Tra cứu lịch sử**: `accounts.audit_log_list` (`/audit-log/`) đã có sẵn, filter theo module/actor/phòng
ban/hành động/khoảng ngày, gate quyền `user.is_manager or user.role == 'ADMIN' or user.is_superuser`. Mọi
transition mới (Approval, WarehouseHandoff...) chỉ cần tiếp tục gọi `log_action()` như cũ — không cần tạo
trang tra cứu riêng.

**Gán nhân viên theo kho**: `Warehouse.staff` (M2M→User, giới hạn `department=WAREHOUSE` qua
`WarehouseForm.__init__`) — dùng để chọn/mặc định người nhận khi bàn giao lô hàng về kho cụ thể; nếu rỗng,
fallback thông báo toàn bộ `department=WAREHOUSE`.

**Bọc 1 transition bằng `Approval` ("nhân viên nộp -> quản lý phòng ban duyệt")** — pattern đã dùng cho GRN
submit + GrnReturn QC-confirm (Phase B, `receiving/services.py`) và GIN confirm (Phase C,
`shipping/services.py`), dùng lại y hệt cho mọi bước duyệt mới sau này:

1. Thêm 1 state trung gian vào `Status` enum của model đó (vd `PENDING_APPROVAL`) — KHÔNG tự chuyển thẳng
   sang state kế tiếp trong view/service khi user bấm nút "Nộp"/"Xác nhận".
2. Viết 1 hàm `request_x(obj, actor, ip_address=None)`: đổi `obj.status` sang state trung gian, rồi gọi
   `accounts.approvals.create_approval(obj, department=..., action_label='...', submitted_by=actor,
   ip_address=ip_address)`. `create_approval` tự chặn tạo trùng (đang có `Approval` PENDING khác trên cùng
   `obj` thì raise `ValidationError`) và tự `notify()` toàn bộ `is_manager=True` của `department` đó.
3. Viết 1 hàm `decide_x(approval, approved, actor, note='', ip_address=None)`: định nghĩa 2 closure
   `on_approve`/`on_reject` (transition THẬT của `obj`, KHÔNG import ngược accounts vào business logic — closure
   được định nghĩa ngay trong app gọi), rồi gọi
   `accounts.approvals.decide_approval(approval, approved, actor=actor, note=note, on_approve=on_approve,
   on_reject=on_reject, ip_address=ip_address)`. Cả `request_x`/`decide_x` đều `@transaction.atomic` — nếu
   `on_approve` raise lỗi, quyết định duyệt cũng rollback theo (atomic lồng nhau qua savepoint).
4. View: 1 view cho nút "Nộp" (gọi `request_x`), 2 view cho nút "Duyệt"/"Từ chối" (lấy `Approval` PENDING mới
   nhất qua `accounts.approvals.latest_approval_for(obj)`, 404/báo lỗi nếu không có, rồi gọi `decide_x`).
   Quyền quyết định: `user.is_department_manager(department) or user.can('approve', <module>)` — vế sau giữ
   nguyên đường Manager/Admin cũ (không hạ quyền ai đang có), vế trước là quyền MỚI theo phòng ban.
   Nếu người bấm nút "Nộp" vốn KHÔNG có quyền tự làm transition trực tiếp (vd GIN: STAFF chỉ có `create`+
   `read`, không có `update` để tự `start_picking`) thì viết 1 view MỚI riêng (gate bằng quyền thấp hơn, vd
   `create`) thay vì rewire view cũ — giữ nguyên view/nút cũ cho user vốn đã có quyền trực tiếp (Manager/Admin)
   để họ không bị bắt tự duyệt chính mình (xem `shipping.views.gin_confirm_request` — view mới, tách biệt
   hoàn toàn khỏi `gin_start_picking` cũ). Ngược lại, nếu người bấm nút vốn đã có đủ quyền update/approve như
   nút đó yêu cầu từ trước (vd GRN: STAFF đã có `update` trên GRN, đủ để gọi `grn_submit`) thì rewire thẳng
   nội dung view cũ để đi qua `request_x`, không cần tạo view riêng (xem `receiving.views.grn_submit`).
5. Template: hiện `latest_approval_for(obj)` (badge trạng thái PENDING/APPROVED/REJECTED + `submitted_by`/
   `submitted_at`/`decided_by`/`decided_at`/`decision_note`) làm khung thời gian nộp/duyệt cho nhân viên tra
   cứu — không cần tạo bảng lịch sử riêng, `Approval` tự đóng vai trò đó cho transition liên quan.
6. Đừng quên: nếu `obj` có hành động "Hủy" tách biệt (khác với từ chối duyệt), hàm hủy phải tự
   `Approval.objects.filter(target_type=..., target_id=..., status=PENDING).update(status=REJECTED, ...)` để
   không mồ côi 1 `Approval` đang chờ xử lý trên 1 `obj` đã bị hủy (xem `receiving.services.cancel_grn`).

## 5. Bàn giao trực tiếp cho 1 người/nhóm cụ thể — KHÔNG dùng `Approval` (pattern Phase D)

`Approval` (mục 4) đúng cho "nhân viên nộp → quản lý phòng ban duyệt" — một quyết định bubble lên đúng 1 cấp
quản lý. Nhưng khi nghiệp vụ là "bàn giao vật lý cho 1 người/nhóm cụ thể nhận" (không phải xin duyệt), dùng
pattern nhẹ hơn — xem `inventory.models.WarehouseHandoff` + `inventory.services.create_handoff()`/
`accept_handoff()`/`reject_handoff()` (Phase D: QC PASS → kho xác nhận nhận hàng) làm mẫu:

1. Model riêng cho đối tượng bàn giao, KHÔNG dùng chung `Approval` — field tối thiểu: `status`
   (`PENDING`/`ACCEPTED`/`REJECTED`), `assigned_to` (FK User, `null=True` — chọn thủ công, để trống thì
   fallback), `decided_by`/`decided_at`, và bất kỳ field "lý do"/"phương án xử lý" riêng của nghiệp vụ đó (vd
   `reject_reason` + `reject_destination` choices).
2. Hàm tạo (`create_x(...)`) nhận `assigned_to=None` optional: có chỉ định thì `notify()` đúng người đó; để
   trống thì `notify()` nhóm mặc định (vd `Warehouse.staff` của đích), fallback về cả
   `department=<phòng ban>` nếu nhóm mặc định rỗng — cùng rule 3 tầng dùng lại ở bước 3 (không được để lệch
   giữa "ai được báo" và "ai được quyền quyết định").
3. Quyền quyết định (`can_decide_x(user, obj)`, viết ở view, KHÔNG viết ở model) phải cho qua nếu: (a)
   `user.is_department_manager(<phòng ban>)` (oversight — luôn thấy/xử lý được mọi phiếu), HOẶC (b)
   `user.pk == obj.assigned_to_id` (nếu có chỉ định), HOẶC (c) nếu không chỉ định thì `user` thuộc nhóm mặc
   định ở bước 2 (kể cả nhánh fallback department).
4. Nhánh "từ chối" nếu có nhiều phương án xử lý khác nhau (vd chuyển kho phế / trả về nơi gửi): chỉ nhánh nào
   thực sự cần đảo ngược dữ liệu (vd chuyển kho phế) mới gọi transition thật (`move_batch_qty`...); nhánh
   "trả lại thủ công" KHÔNG tự động đảo ngược transaction gốc đã hoàn tất trước đó — chỉ đổi status của bản
   ghi bàn giao + `notify()` phòng ban liên quan để họ tự xử lý tay (đúng boundary "override chỉ annotation,
   không đảo transaction gốc" đã chốt cho QC — xem `quality.views.qc_override`).
5. Nếu 1 primitive dùng chung (vd `inventory.services.move_batch_qty`) cần chấp nhận thêm 1 status nguồn mới
   để phục vụ nhánh từ chối ở bước 4, mở rộng guard ngay tại primitive đó (không viết lại logic tách batch
   riêng) — xem `move_batch_qty`'s status-nguồn guard được mở rộng thêm `PENDING_RECEIPT` cho
   `reject_handoff(..., destination=TO_SCRAP)`.
