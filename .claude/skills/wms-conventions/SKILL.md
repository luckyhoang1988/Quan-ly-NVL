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

**Cách xử lý (xem `accounts/forms.py::WmsPasswordChangeForm`/`WmsSetPasswordForm`/`UserCreateForm`):**
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
- Áp dụng mixin này cho một `ModelForm` thường (không kế thừa `SetPasswordForm`, vd `UserCreateForm` —
  2026-07-26, admin tự đặt mật khẩu khi tạo user thay vì sinh tự động) thì phải gọi
  `validate_password_for_user(self.instance, ...)` trong `_post_clean()` (sau `super()._post_clean()`), KHÔNG
  phải trong `clean()` — `self.instance` của `ModelForm` chỉ được Django gán các field đã nhập (username,
  email...) ở bước `_post_clean`, nên validator so sánh mật khẩu với thông tin user (`UserAttributeSimilarityValidator`)
  gọi ở `clean()` sẽ thấy instance rỗng.

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

**Đánh dấu đã đọc + deep-link (`target`)**: `accounts.views.notification_mark_read` là **POST-only** (không
GET — đánh dấu-đã-đọc là side-effect ghi DB, GET không có CSRF và dễ bị prefetch/bot click hàng loạt, vá
2026-07-28, xem CLAUDE.md) — template dùng `<form method="post" class="list-group-item ...">` bọc 1
`<button>` thay vì `<a href>`, giữ nguyên class `list-group-item` trên chính thẻ `<form>` (không phải trên
`<button>` bên trong) để CSS border/hover của Bootstrap `.list-group-item + .list-group-item` vẫn khớp — bọc
`<form>` NGOÀI `list-group-item` sẽ vỡ style vì lúc đó `<form>` mới là sibling thật trong `.list-group`, không
phải `<button>`. Bất kỳ model nào được truyền vào `notify(..., target=obj)` hoặc
`accounts.approvals.create_approval(obj, ...)` (mọi model đang dùng: `Grn`, `GrnReturn`, `Gin`,
`PurchaseRequest`, `WarehouseHandoff`) PHẢI có `get_absolute_url()` — nếu không, click thông báo chỉ quay lại
`notification_list` (deep-link chết, vá 2026-07-28). Model không có trang detail riêng (`GrnReturn` lồng trong
`grn_detail` của GRN cha; `WarehouseHandoff` không có trang riêng, quyết định ngay trên `handoff_list`) thì
trỏ `get_absolute_url()` về đúng trang cha/hàng-đợi đó thay vì cố tạo 1 trang detail mới chỉ để có URL.

**Tra cứu lịch sử**: `accounts.audit_log_list` (`/audit-log/`) đã có sẵn, filter theo module/actor/phòng
ban/hành động/khoảng ngày. Gate quyền (thu hẹp 2026-07-29, áp dụng toàn hệ thống — không riêng PR):
`accounts.permissions.can_view_audit_log(user)` = `user.is_superuser or user.role == 'ADMIN'` — quản lý
phòng ban thường (`is_manager=True`) KHÔNG còn xem được trang này nữa, kể cả log của đúng phòng mình; lịch
sử duyệt theo phòng ban của họ đã có sẵn ngay trên trang detail của từng đối tượng (PR/GRN/GIN) qua
`Approval`, không cần tới log toàn hệ thống. Gate cả 3 lớp: view (`can_view_audit_log`), sidebar
(`accounts.context_processors.sidebar_permissions` gộp `can_view_menu('audit_log') and
can_view_audit_log(user)` trong Python — xem bẫy `{% if a or b and c %}` ở mục 6.1), và
`can_view_menu('audit_log')` (trục MENU_ITEMS riêng, xem mục 6.1) — 2 điều kiện độc lập, thiếu 1 trong 2 vẫn
lộ/ẩn sai. Mọi transition mới (Approval, WarehouseHandoff...) chỉ cần tiếp tục gọi `log_action()` như cũ —
không cần tạo trang tra cứu riêng.

**Gán nhân viên theo kho**: `Warehouse.staff` (M2M→User, giới hạn `department=WAREHOUSE` qua
`WarehouseForm.__init__`) — dùng để chọn/mặc định người nhận khi bàn giao lô hàng về kho cụ thể; nếu rỗng,
fallback thông báo toàn bộ `department=WAREHOUSE`.

**Bọc 1 transition bằng `Approval` ("nhân viên nộp -> quản lý phòng ban duyệt")** — pattern đã dùng cho GRN
submit + GrnReturn QC-confirm (Phase B, `receiving/services.py`), GIN confirm (Phase C,
`shipping/services.py`), và PR submit (`purchasing/services.py::submit_purchase_request`/
`decide_purchase_request`), dùng lại y hệt cho mọi bước duyệt mới sau này. **PR (2026-07-28)** giờ mirror
đúng shape DRAFT/submit của GRN/GIN — trước đó tạo PR *là* nộp PR luôn (PENDING ngay), không có bước DRAFT
tách biệt; nay `pr_create` chỉ lưu ở `DRAFT`, người tạo tự sửa tiếp qua `pr_update` rồi bấm "Nộp yêu cầu"
(`pr_submit` → `submit_purchase_request`) khi sẵn sàng — sửa/nộp chỉ cho đúng chủ hoặc người có tầm nhìn
toàn bộ (`purchasing.views._pr_can_edit`, mirror check hiển thị ở `_pr_can_view_all`), không chỉ dựa quyền
module. PR còn có 1 nhánh KHÔNG có ở GRN/GIN: `REJECTED` không phải ngõ cụt — mở lại được về `DRAFT` qua
`purchasing.services.reopen_purchase_request` (giữ nguyên `decided_by`/`decided_at`/`reject_reason` làm
lịch sử, chỉ đổi `status`) rồi sửa/nộp lại bằng đúng `pr_update`/`pr_submit` đã có, không cần view riêng —
do `Approval` cũ đã REJECTED (không còn PENDING) nên nộp lại không đụng `unique_pending_approval_per_target`.
`pr_detail.html` chỉ hiện dòng "Lý do từ chối" khi `obj.status == 'REJECTED'` (bug fix 2026-07-28, L1) —
`reject_reason` vẫn còn giá trị trong DB sau khi reopen (làm lịch sử) nên KHÔNG được coi field có giá trị =
còn hiển thị, phải luôn kèm check `status` khi template hiện 1 field lịch sử kiểu này. Một PR còn `DRAFT`
(chưa từng qua `Approval`, không cần giữ lịch sử) xoá được thật qua
`purchasing.services.delete_purchase_request`/view `pr_delete` (bug fix 2026-07-28, L2) — POST-only, gate
kép `@pr_permission_required('delete')` (mặc định chỉ MANAGER/ADMIN có `delete` trên module `pr`) VÀ
`_pr_can_edit` (đúng chủ hoặc tầm nhìn toàn bộ), mirror y hệt cặp gate của `pr_update`/`pr_reopen` — khác
`user_delete` (soft-delete, giữ bản ghi), đây là delete cứng vì DRAFT chưa có gì cần giữ audit.

**PR duyệt 2 CẤP tuần tự (2026-07-29), khác mọi flow `Approval` khác trong dự án (chỉ 1 cấp)**: trước đó
`submit_purchase_request` hard-code luôn tạo `Approval(department=PURCHASING)`, bỏ qua hoàn toàn quản lý
của chính phòng ban người nộp. Nay `Status` có 2 state trung gian tuần tự — `PENDING_DEPT` (chờ quản lý
*phòng gốc của người nộp*) rồi `PENDING_PUR` (chờ quản lý phòng Mua hàng) — thay vì 1 `PENDING` duy nhất.
Không thêm field lưu lịch sử riêng: `Approval` vốn đã hỗ trợ nhiều bản ghi tuần tự cho cùng 1 target (ràng
buộc unique chỉ chặn 2 bản ghi PENDING cùng lúc), nên mỗi cấp chỉ đơn giản là 1 `Approval` mới —
`accounts.approvals.approval_history_for(target)` (khác `latest_approval_for` — trả TOÀN BỘ theo
`submitted_at` tăng dần, không chỉ bản mới nhất) là hàm dùng để hiện đủ lịch sử 2 bước ở template. Người
nộp thuộc chính phòng Mua hàng (hoặc không có `department`, vd role ADMIN) thì bỏ qua cấp 1, vào thẳng
`PENDING_PUR` — tránh 1 người tự duyệt PR của chính họ 2 lần. `decide_purchase_request` vẫn là 1 hàm DUY
NHẤT cho cả 2 cấp (đọc `pr.status` để biết đang ở cấp nào trước khi mutate), KHÔNG tách 2 hàm riêng —
duyệt ở `PENDING_DEPT` chỉ chuyển tiếp sang `PENDING_PUR` (chưa phải quyết định cuối, `decided_by` chưa
set), duyệt ở `PENDING_PUR` mới là `APPROVED` thật. **Bẫy thứ tự quan trọng nhất của flow 2 cấp**: tạo
`Approval` cấp 2 phải làm SAU KHI `decide_approval()` (bước 1) return, không được làm trong callback
`on_approve()` truyền vào nó — `decide_approval()` gọi callback TRƯỚC khi lưu `approval.status=APPROVED`
vào DB, nên tạo `Approval` mới trong lúc bản ghi cấp 1 vẫn còn `PENDING` sẽ đụng ngay
`unique_pending_approval_per_target` (2 bản ghi PENDING cùng target). `can_decide_pr(user, pr)` đổi sang
nhận thêm `pr` — đọc `department` đang giữ quyền quyết định qua `latest_approval_for(pr).department` rồi
check `user.is_department_manager(department)` (không còn hard-code PURCHASING), fallback
`user.can('approve','pr')` cho Manager/Admin như cũ. Tách riêng `can_manage_pur_pr(user)` (ý nghĩa cũ của
`can_decide_pr(user)` không gắn 1 PR cụ thể — quản lý PUR HOẶC Manager/Admin) dùng cho `pr_forward` và gate
`?from_pr=` ở `po_create`, vì đây luôn là tác vụ ở cấp Mua hàng bất kể PR đó từng qua cấp nào.
`_pr_can_view_all` thu hẹp chỉ còn superuser/MANAGER/ADMIN (quản lý PUR ra khỏi tầng "xem hết"); 4 tầng
nhìn PR đầy đủ (`_pr_visible_queryset`/`_pr_can_view`, dùng đồng nhất ở cả `pr_list` VÀ `pr_detail`) xem
docstring 2 hàm đó trong `purchasing/views.py` hoặc mục "Purchase Request (PR)" trong CLAUDE.md — điểm cần
nhớ nhất: quản lý phòng gốc **vẫn xem được** (read-only) PR sau khi đã chuyển sang PUR, và người
`assigned_to` **không** thấy PR ở bất kỳ cấp chờ nào, chỉ thấy sau khi `APPROVED`.

1. Thêm 1 state trung gian vào `Status` enum của model đó (vd `PENDING_APPROVAL`) — KHÔNG tự chuyển thẳng
   sang state kế tiếp trong view/service khi user bấm nút "Nộp"/"Xác nhận".
2. Viết 1 hàm `request_x(obj, actor, ip_address=None)`: đổi `obj.status` sang state trung gian, rồi gọi
   `accounts.approvals.create_approval(obj, department=..., action_label='...', submitted_by=actor,
   ip_address=ip_address)`. `create_approval` tự chặn tạo trùng (đang có `Approval` PENDING khác trên cùng
   `obj` thì raise `ValidationError`) và tự `notify()` toàn bộ `is_manager=True` của `department` đó. Chặn
   trùng này là race-safe thật (không chỉ check-rồi-tạo): `Approval.Meta` có
   `UniqueConstraint(fields=['target_type','target_id'], condition=Q(status='PENDING'))` làm chốt chặn ở DB,
   `create_approval` bắt `IntegrityError` từ đó và dịch lại thành cùng 1 `ValidationError` — 2 request đồng
   thời gọi `request_x` trên cùng `obj` không bao giờ tạo ra 2 `Approval` PENDING (vá 2026-07-28, xem
   CLAUDE.md). Nếu sau này thêm 1 cơ chế "chặn trùng theo target" tương tự ở ngoài `Approval` (không dùng
   pattern này), nhớ áp dụng lại đúng shape đó — `.exists()` một mình không đủ.
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
   **Hiện danh sách nhiều `obj` cùng lúc (không phải 1 trang detail)**: đừng gọi `latest_approval_for()`
   trong vòng lặp (N+1) — dùng `accounts.approvals.latest_approvals_for(model_class, pks)` (1 query, trả
   `{str(pk): Approval}`, `.get(str(pk))` ở nơi gọi). Bug thật: `receiving.views.grn_detail` từng lặp qua từng
   `GrnReturn` gọi `latest_approval_for` riêng lẻ — vá 2026-07-27, xem CLAUDE.md.
6. Đừng quên: nếu `obj` có hành động "Hủy" tách biệt (khác với từ chối duyệt), hàm hủy phải tự
   `Approval.objects.filter(target_type=..., target_id=..., status=PENDING).update(status=REJECTED, ...)` để
   không mồ côi 1 `Approval` đang chờ xử lý trên 1 `obj` đã bị hủy (xem `receiving.services.cancel_grn`).
   **Cùng lỗi loại đó nhưng cho side-effect thay vì Approval**: nếu 1 module KHÁC đã tạo dữ liệu thật
   (Batch/Inventory...) ở 1 state trung gian của `obj` trước khi state đó vẫn cho phép hủy, hàm hủy PHẢI đảo
   ngược side-effect đó luôn — chỉ đổi `obj.status` là chưa đủ, dữ liệu tạo ra sẽ "kẹt" vĩnh viễn dù `obj` đã
   CANCELLED. Đặt hàm đảo ngược ở app SỞ HỮU side-effect đó rồi gọi từ hàm hủy của app gốc (giữ đúng ranh giới
   module đã có), và phân biệt rõ với boundary "override/reject chỉ annotation, không đảo transaction đã hoàn
   tất" ở mục 5.4 bên dưới: side-effect ở state TRUNG GIAN (chưa ra quyết định) đảo được, transaction đã hoàn
   tất (đã PASS/FAIL/quyết định xong) thì không. Bug thật: `cancel_grn` cho hủy lúc `QC_IN_PROGRESS` nhưng
   không đảo Batch `ACTIVE` + Inventory Kho chờ mà `start_qc` đã tạo — vá bằng
   `quality.services.cancel_qc_inspection()` (phát hiện + vá 2026-07-27, xem CLAUDE.md). Viết test cho case
   này phải đi qua flow thật tạo ra side-effect (vd gọi view `grn_receive_qty` để `start_qc` chạy thật) — test
   dựng `obj` thẳng ở state đó (`Model(status=X)`) sẽ không bao giờ bắt được lỗi thiếu-đảo-ngược này.
   **Side-effect không chỉ giới hạn ở Batch/Inventory** — bug thứ 2 cùng dạng (phát hiện + vá 2026-07-29, xem
   CLAUDE.md): `grn_receive_qty` cùng transaction còn gọi `sync_po_status(po)` đẩy `PurchaseOrder.status` lên
   `PARTIAL_RECEIVED`/`RECEIVED`, nhưng `cancel_grn` bản vá lần 1 chỉ đảo Batch/Inventory, quên gọi lại
   `sync_po_status` — PO kẹt vĩnh viễn ở status cũ dù GRN mang qty đó đã bị hủy. Tổng quát: khi rà 1 hàm hủy
   theo mục 6 này, liệt kê HẾT mọi lệnh gọi trong transaction gốc đã đưa `obj` tới state trung gian đó (không
   dừng lại ở lệnh đầu tiên tìm thấy), vì các lệnh gọi đó thường thuộc nhiều app khác nhau (ở đây: QC lo
   Batch/Inventory, PUR lo `PurchaseOrder.status`) và dễ chỉ vá 1 trong số đó rồi coi là xong.
   **Ordering hazard nếu hàm đảo tự re-query DB để loại trừ `obj`** (vd `sync_po_status` loại trừ GRN qua
   `.exclude(grn__status=CANCELLED)`): phải gọi hàm đảo đó SAU khi `obj.status` đã `save()`, không phải
   trước — gọi trước thì query loại trừ vẫn thấy status cũ, âm thầm no-op (không lỗi, chỉ sai kết quả, rất dễ
   sót khi review).
7. Nếu muốn thêm "báo riêng 1 người cụ thể" bên CẠNH việc bubble lên quản lý phòng ban (chứ không thay thế),
   thêm 1 field `assigned_to` (FK User, `null=True`, tuỳ chọn) ngay trên `obj`, KHÔNG phải trên `Approval` —
   `assigned_to` chỉ để `notify()` thêm người đó và hiển thị "ai sẽ xử lý", KHÔNG tự có quyền quyết định:
   quyền quyết định thật vẫn chỉ đi qua `is_department_manager(department)`/`can('approve', module)` ở bước
   4 — đừng để `user.pk == obj.assigned_to_id` lọt vào điều kiện cho phép duyệt (khác pattern §5 bên dưới, nơi
   `assigned_to` CÓ quyền quyết định) (xem `PurchaseRequest.assigned_to` + `purchasing.views.can_decide_pr`,
   Phase E).
8. **Nếu retrofit pattern này vào 1 workflow ĐÃ có sẵn dữ liệu đang ở trạng thái "chờ" theo cơ chế cũ (không
   phải build từ đầu)**: PHẢI viết kèm 1 data migration (`RunPython`) backfill 1 `Approval` PENDING cho mỗi
   bản ghi đang ở state "chờ duyệt" cũ mà chưa có `Approval` nào — nếu không, các bản ghi đó bị kẹt vĩnh viễn
   (UI vẫn hiện đúng trạng thái cũ, nhưng `decide_x` luôn raise lỗi vì `latest_approval_for()` trả `None`, và
   không ai — kể cả Manager/Admin — duyệt/từ chối được nữa). Migration schema tạo model `Approval`
   (`0011_approval.py`) và migration re-seed quyền (`0012_reseed_purchasing_pr_permissions.py`) KHÔNG tự làm
   việc này — chúng chỉ lo phần schema/quyền, không đụng tới dữ liệu nghiệp vụ đang tồn tại. Bug thật đã xảy
   ra với PR (Phase E, phát hiện + vá 2026-07-27, xem `purchasing/migrations/0008_backfill_pr_approval.py` +
   CLAUDE.md) — coi đây là checklist bắt buộc, không phải tuỳ chọn, mỗi khi bước 1-7 ở trên được áp dụng cho
   1 workflow đã có dữ liệu sống từ trước.

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
3. Quyền quyết định (`can_decide_x(user, obj)`, viết ở view, KHÔNG viết ở model) phải cho qua nếu: (0)
   `user.is_superuser or user.role == User.Role.ADMIN` (oversight toàn hệ thống — kiểm tra NGAY ĐẦU HÀM, xem
   bug fix `inventory.views.can_decide_handoff` 2026-07-27 trong CLAUDE.md: model bàn giao nhẹ này không có
   module riêng trong `accounts/permissions.py` nên không thể dùng fallback `user.can('approve', module)` như
   GRN/GIN/PR — phải check role/superuser trực tiếp thay vào, và nhớ áp cùng điều kiện ở CẢ view liệt kê danh
   sách (nhánh "thấy toàn bộ") lẫn view quyết định, không chỉ 1 trong 2), HOẶC (a)
   `user.is_department_manager(<phòng ban>)` (oversight theo phòng ban — luôn thấy/xử lý được mọi phiếu),
   HOẶC (b) `user.pk == obj.assigned_to_id` (nếu có chỉ định), HOẶC (c) nếu không chỉ định thì `user` thuộc
   nhóm mặc định ở bước 2 (kể cả nhánh fallback department). **Lưu ý**: sidebar nav (`base.html`) thường có
   điều kiện hiển thị link riêng (vd `user.is_superuser or user.role == 'ADMIN' or user.department == ...`)
   — đây là 1 điều kiện TÁCH BIỆT với `can_decide_x`, dễ bị lệch nếu chỉ sửa 1 bên; luôn grep view đích khi
   sửa điều kiện sidebar, và ngược lại.
4. Nhánh "từ chối" nếu có nhiều phương án xử lý khác nhau (vd chuyển kho phế / trả về nơi gửi): chỉ nhánh nào
   thực sự cần đảo ngược dữ liệu (vd chuyển kho phế) mới gọi transition thật (`move_batch_qty`...); nhánh
   "trả lại thủ công" KHÔNG tự động đảo ngược transaction gốc đã hoàn tất trước đó — chỉ đổi status của bản
   ghi bàn giao + `notify()` phòng ban liên quan để họ tự xử lý tay (đúng boundary "override chỉ annotation,
   không đảo transaction gốc" đã chốt cho QC — xem `quality.views.qc_override`).
5. Nếu 1 primitive dùng chung (vd `inventory.services.move_batch_qty`) cần chấp nhận thêm 1 status nguồn mới
   để phục vụ nhánh từ chối ở bước 4, mở rộng guard ngay tại primitive đó (không viết lại logic tách batch
   riêng) — xem `move_batch_qty`'s status-nguồn guard được mở rộng thêm `PENDING_RECEIPT` cho
   `reject_handoff(..., destination=TO_SCRAP)`.

## 6. Gate link sidebar (`base.html`) bằng `user.can()` qua context processor, KHÔNG hardcode role

`accounts/templates/base.html` render ở mọi trang (nằm trong `layout-wrapper`, không phải 1 view riêng), nên
không thể truyền flag quyền thủ công từng view như các trang khác — phải dùng **context processor**, đúng
pattern `accounts.context_processors.notifications` (badge thông báo) đã có sẵn. Khi thêm/sửa 1 link sidebar
cần gate theo quyền:

1. Nếu module đó đã có trong `accounts/permissions.py` `MODULES` (`grn`/`gin`/`opname`/`qc`/`pr`/`po`/
   `reports`) — thêm 1 key vào `accounts.context_processors.sidebar_permissions()` kiểu
   `'can_read_<module>': user.can('read', '<module>')`, rồi gate link bằng `{% if can_read_<module> %}` trong
   `base.html`. **Không** viết `user.role == 'X'`/liệt kê role trực tiếp trong template — role cứng trùng kết
   quả với ma trận mặc định (`ROLE_PERMISSIONS`) nhưng bỏ qua quyền chi tiết theo-user (trang "Phân quyền chi
   tiết", `views.user_permission_edit` cho phép admin cấp/thu hồi quyền lệch khỏi role mặc định) — 1 user bị
   thu hồi quyền vẫn thấy link rồi 403, hoặc được cấp thêm quyền mà sidebar vẫn giấu link.
2. Nếu module đó **không** có trong `MODULES` (vd `inventory`/`warehouse`/`catalog`/`partners` — theo BACKLOG
   Permission Matrix không có cột riêng CRUD) — dùng `accounts.permissions.MENU_ITEMS` +
   `user.can_view_menu(key)` (2026-07-28, xem bên dưới), KHÔNG còn để link hiện vô điều kiện như trước.
3. Nếu module đó dùng model bàn giao nhẹ kiểu `WarehouseHandoff` (mục 5 ở trên) hoặc là trang quản trị
   (`user_mgmt`/`audit_log`) — link vẫn giữ điều kiện role/department/`is_superuser` hiện có LÀM ĐIỀU KIỆN
   OVERSIGHT, cộng thêm (AND) `can_view_menu_<key>` tương ứng — xem điểm "Kết hợp 2 điều kiện" bên dưới, đừng
   xoá điều kiện role/department cũ, chỉ thêm điều kiện menu vào.
4. `sidebar_permissions()` phải tự `return {}` khi `not request.user.is_authenticated` (cùng rule
   `notifications()` đã áp dụng) — context processor chạy trên mọi request kể cả trang login.

Đã áp dụng cho "Tiêu chuẩn QC" (`can_read_qc`) và "Kiểm kê" (`can_read_opname`); "Yêu cầu mua hàng" (PR) được
thêm link sidebar mới cùng lúc (`can_read_pr`) — trước đó PR có route (`purchasing:pr_list`) nhưng không có
link, chỉ vào được qua tab trong `po_list.html`.

### 6.1. `MENU_ITEMS`/`can_view_menu` — bật/tắt cả 1 mục sidebar không có ma trận CRUD (2026-07-28)

Khác với `MODULES` (Module × Action, phần mục 6 ở trên), một số mục sidebar chưa từng có khái niệm CRUD gì cả
— trước đây các view entry-point này chỉ có `@login_required`, mở cho MỌI user đăng nhập, không cách nào thu
hẹp riêng cho 1 user. `accounts/permissions.py::MENU_ITEMS` (dict `key: label` tiếng Việt) định nghĩa 7 mục
này: `warehouse`, `catalog`, `partners`, `inventory`, `handoff`, `user_mgmt`, `audit_log`. Mỗi key sinh 1
Django permission `can_view_menu_<key>` (khai báo trong `User.Meta.permissions`, đọc qua
`user.can_view_menu(key)` — mirror `.can()` nhưng không có action/module, chỉ có "được xem mục này hay
không"). **Mặc định cấp cho MỌI user/role** (`codenames_for_role()` luôn nối thêm `all_menu_codenames()`) —
giữ đúng hành vi cũ (mở cho tất cả), admin chỉ dùng trang "Phân quyền chi tiết"
(`user_permission_edit`/`user_permission_form.html`, khối "Ứng dụng được phép truy cập") để **thu hẹp** riêng
từng user khi cần, không phải để mở rộng.

**Không tạo checkbox `can_view_menu_*` cho 7 module đã có trong `MODULES`** (grn/gin/opname/qc/pr/po/reports)
— checkbox "Xem" (read) sẵn có trong ma trận CRUD đã đóng đúng vai trò đó, thêm 1 khái niệm song song sẽ
trùng lặp và dễ lệch nhau.

**3 nơi phải enforce cùng lúc cho 1 mục menu mới** (thiếu 1 trong 3 là chỉ ẩn link hoặc chỉ enforce nửa vời):

1. **Sidebar link** (`base.html`) — bọc `{% if can_view_menu_<key> %}` (flag lấy từ
   `accounts.context_processors.sidebar_permissions`, đã thêm 7 flag `can_view_menu_*` sẵn trong context mọi
   trang).
2. **View thật** — thêm ngay đầu hàm (sau docstring, trước logic chính), theo đúng style
   `raise PermissionDenied(...)` cục bộ từng app đã dùng (không tạo decorator dùng chung mới):
   ```python
   if not request.user.can_view_menu('warehouse'):
       raise PermissionDenied('Bạn không có quyền truy cập mục "Kho hàng".')
   ```
   Ẩn link mà không gate view thật = chỉ cosmetic, gõ thẳng URL vẫn vào được. Xem
   `warehouse.views.warehouse_list`/`catalog.views.product_list`/`partners.views.supplier_list`/
   `inventory.views.inventory_list`/`inventory.views.handoff_list` làm mẫu. Với mục có decorator riêng
   (`user_admin_required`, `audit_log_required` ở `accounts/views.py`) — thêm điều kiện
   `and request.user.can_view_menu('user_mgmt'/'audit_log')` NGAY TRONG decorator, tự áp dụng cho mọi view
   dùng decorator đó, không cần sửa từng view con.
3. **Trang "Phân quyền chi tiết"** — `menu_rows` (context của `user_permission_edit`) build từ `MENU_ITEMS`,
   render trong khối `<div class="card mt-3">` "Ứng dụng được phép truy cập" (`user_permission_form.html`) —
   checkbox độc lập, KHÔNG nằm trong bảng CRUD.

**Kết hợp điều kiện role/department cũ với `can_view_menu` mới trong `base.html` — LUÔN dùng `{% if %}` LỒNG
NHAU, KHÔNG viết `or`/`and` chung 1 dòng**: Django template `and` bind chặt hơn `or` (giống Python), nên
`{% if A or B and C %}` được parse là `A or (B and C)`, không phải `(A or B) and C` như trực giác thường nghĩ
— viết sai dạng này từng khiến điều kiện role (`user.role == 'ADMIN'`) bỏ qua hẳn điều kiện `can_view_menu`
phía sau nó (bug thật, tự phát hiện lúc code review 2026-07-28, sửa trước khi merge). Luôn viết:
```html
{% if user.is_superuser or user.role == 'ADMIN' or user.department == 'WAREHOUSE' %}
  {% if can_view_menu_handoff %}
  <li class="nav-item">...</li>
  {% endif %}
{% endif %}
```
không viết gộp `{% if (role check) and can_view_menu_x %}` trên 1 dòng.

**Retrofit vào bảng đã có user thật**: thêm 1 permission mới vào `User.Meta.permissions` cần migration
`RunPython` backfill — với MỌI user đã tồn tại, `user.user_permissions.add(*perms)` (dùng `.add()`, KHÔNG
`.set()`, để không xoá mất quyền CRUD đã tuỳ biến trước đó của user đó) — xem
`accounts/migrations/0013_menu_access_permissions.py`, cùng nguyên tắc backfill đã ghi ở mục 4.8 phía trên,
áp dụng cho permission thường chứ không chỉ riêng `Approval`.

**Guard tự khoá**: mirror `user_toggle_active`/`user_update`'s self-lock — `user_permission_edit` chặn 1
ADMIN tự bỏ tick `can_view_menu_user_mgmt` của chính họ (không có cách khôi phục qua UI nếu lỡ khoá, giống
lý do chặn tự khoá `is_active`).

## 7. Topbar user-menu (bánh răng góc trên-phải) — thay cho khối user-box cuối sidebar (2026-07-28)

Khối "tên user · role + nút Đổi mật khẩu + nút Đăng xuất" trước đây nằm cố định cuối sidebar (`.user-box`),
chiếm không gian dọc cố định trên MỌI trang dù ít khi thao tác. Đã chuyển sang 1 nút icon bánh răng
(`bi-gear`, class `.user-menu-toggle`) đặt trong `.app-topbar` — hàng đầu tiên bên trong `<main>` của
`base.html`, canh phải bằng `dropdown ms-auto`, `position: sticky; top: 0` (định nghĩa ở
`assets/css/custom.css`) nên luôn hiện dù cuộn trang dài. Dropdown mở ra mới hiện role
(`dropdown-header`), rồi "Đổi mật khẩu" + form "Đăng xuất" — y hệt nội dung cũ, chỉ đổi chỗ hiển thị.

- **Tên user hiển thị luôn ngoài nút** (`<span class="user-menu-name">{{ user.username }}</span>` đặt trước
  icon `bi-gear`, cùng trong 1 `<button>`), không phải bấm mở dropdown mới thấy — bổ sung 2026-07-28 theo yêu
  cầu người dùng. Vì vậy `.user-menu-toggle` không còn là nút tròn chỉ-icon nữa mà là pill (`border-radius:
  999px`, `padding: .35rem .7rem`, `gap: .5rem`) tự co giãn theo độ dài username; đừng revert về kích thước
  cố định 2.25rem trước đây, sẽ cắt mất chữ.
- Nút bánh răng KHÔNG gắn class `dropdown-toggle` của Bootstrap (chỉ cần `data-bs-toggle="dropdown"` là đủ
  để JS dropdown hoạt động) — tránh caret mũi tên thừa, giữ nút gọn gàng.
- Nút toggle sidebar mobile (`#sidebarToggle`, chỉ hiện < 992px qua `.sidebar-toggle { display:none }`) nằm
  chung hàng `.app-topbar` ở bên trái, `justify-content-between` tự đẩy 2 nút về 2 đầu khi cả 2 cùng hiện.
- Sidebar giờ chỉ còn danh sách nav + logo/brand — không còn thông tin user ở cuối. Nếu thêm hành động
  user mới (vd đổi ngôn ngữ, xem hồ sơ...) thì thêm `<li>` vào đúng `<ul class="dropdown-menu">` này, không
  tạo lại khối user-box trong sidebar.
