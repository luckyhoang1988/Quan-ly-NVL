# PUR Expansion — 01. FSD Stage 1: Foundation (Epic A, PUR-FND-01..07)

> Trạng thái: **Approved — v6, sửa theo review lần 4 (bổ sung) ngày 02/08/2026**
> Người duyệt: luckyhoang1988 (Trường Hoàng) | Ngày duyệt: 02/08/2026
> Phụ thuộc: không phụ thuộc `00_business_decisions.md` (Foundation là sửa lỗi kỹ thuật/bổ sung
> hạ tầng timestamp, không đụng RFQ/Budget/Approval Rule nên không cần chốt 14 quyết định nghiệp
> vụ trước). **Ngoại lệ**: quyết định #8 (ngoại tệ) và mục "Attachment" của quyết định #13 có chủ
> đích **không** đưa vào file này — `ExchangeRate`/`currency` và attachment đều không thuộc Stage 1
> (xem mục 0, và ghi chú đã chốt ở mục 8/13 của `00_business_decisions.md`). 5 quyết định nghiệp vụ
> bổ sung nêu trong review lần 3 (ngưỡng RFQ tính trên đơn vị nào, 3 báo giá theo RFQ hay từng dòng,
> giá trị budget tolerance, xử lý khi thiếu tỷ giá, độ phủ luồng pilot) **hoãn lại có chủ đích** —
> cần chốt trước khi brainstorm Stage 2, nhưng không chặn Stage Foundation vì cả 5 đều thuộc phạm
> vi nghiệp vụ Stage 2-4, không đụng file này.
> Nguồn: Epic A (`PUR-FND-01..07`, mục 7) + Stage 1 (mục 12) của `PUR_EXPANSION_MASTER_PLAN.md`,
> đối chiếu lại với code thật ngày 02/08/2026 (không chép mô tả chung chung từ master plan).
> **v2 (02/08/2026)**: sửa 4 điểm chặn + 3 điểm không chặn từ review lần 1. **v3 (02/08/2026)**:
> sửa 2 điểm Critical + 5 điểm High từ review lần 2 (thêm rule `PUR-FND-06`, sửa 2 chỗ ghi sai
> Stage 4→5, làm rõ transaction/save behavior của `email_status`, thêm test permission/UI, đặc tả
> `grand_total` bằng `Decimal`). **v4 (02/08/2026)**: sửa 3 điểm bắt buộc từ review lần 3 — (1)
> migration guard `PUR-FND-06` viết lại độc lập với model/service hiện tại (dùng historical model
> qua `apps.get_model()`, không import `purchasing.services`/`purchasing.models`), luôn giữ guard
> bất kể kết quả tiền kiểm; (2) thêm rule `PUR-FND-07` — action `retry_po_email`, cho phép gửi lại
> khi `email_status` là `FAILED`/`SKIPPED_NO_EMAIL` mà không chạy lại transition; (3) ghi rõ giới
> hạn chấp nhận được của email side-effect chạy trong `@transaction.atomic`. Kèm 3 lỗi tài liệu nhỏ
> (đếm sai "4 điểm" thành 5, số Epic lag `01..05`, thiếu `max_length`/`logger.exception()`).
> **v5 (02/08/2026)**: sửa 3 điểm từ review lần 4 — (1) "Người duyệt" đổi từ tên hiển thị
> (`Trương Hoàng`, sai chính tả) sang username ổn định `luckyhoang1988` (kèm tên trong ngoặc) để
> đối chiếu Git/audit được; (2) siết điều kiện `retry_po_email()` (`PUR-FND-07`) — thêm bắt buộc
> `po.supplier.contact_email` phải khác rỗng tại thời điểm gọi, chặn bằng `ValidationError` nếu
> không (trước đó cho phép gọi vô nghĩa khi `email_status=SKIPPED_NO_EMAIL` mà NCC vẫn chưa có
> email, sinh `AuditLog` rác); (3) sửa câu chữ mô tả `retry_po_email()` không còn là "đường khắc
> phục" cho tình huống email-gửi-nhưng-transaction-rollback (vì lúc đó PO đã về `APPROVED`, không
> gọi `retry_po_email()` được) — đổi thành ghi nhận giới hạn đã biết, chưa bảo đảm exactly-once
> delivery. **v6 (02/08/2026)**: sửa 1 lỗi chữ không chặn từ review lần 4 (bổ sung) — giá trị dài
> nhất của `email_status` là `SKIPPED_NO_EMAIL`, đếm lại đúng **16 ký tự** (không phải 17 như ghi ở
> v3); `max_length=20` không đổi, vẫn đúng. Xem mục 13 "Lịch sử review" ở cuối file để biết chi tiết
> đầy đủ.

## 0. Tóm tắt phạm vi

5 điểm sau đã được xác minh còn tồn tại trong code hiện tại (`purchasing` app), không phải suy
đoán từ tài liệu cũ:

| Mã | Vấn đề | Vị trí |
|---|---|---|
| `PUR-FND-01` | `po_detail` không loại trừ GRN đã `CANCELLED` khi tính Qty đã nhận | `purchasing/views.py:306-311` |
| `PUR-FND-02` | Audit log/flash message báo "đã gửi email" dù `send_mail` thất bại | `purchasing/services.py:140-160` |
| `PUR-FND-03` | Lead-time NCC tính từ `created_at` (ngày tạo nháp) thay vì ngày gửi NCC | `purchasing/services.py:224-236` |
| `PUR-FND-05` | Không có tổng tiền PO ở đâu cả (không phải lệch đồng bộ — thiếu hẳn tính năng) | `purchasing/models.py`, `po_detail.html` |
| `PUR-FND-06` **[mới, review v3]** | `PurchaseOrderItem` không có ràng buộc unique `(purchase_order, product)` — cùng 1 Product xuất hiện 2 dòng trong 1 PO làm sai công thức đối chiếu theo `product_id` ở `PUR-FND-01` (mỗi dòng tự so `received_by_product[product] >= qty_ordered` của riêng nó, không phải so với tổng `qty_ordered` của mọi dòng cùng Product) | `purchasing/models.py:159-173`, `purchasing/services.py:52-64` |

`PUR-FND-07` **[mới, review lần 3]** — không phải bug có sẵn như 5 dòng trên, mà là hành vi bổ
sung cho `PUR-FND-02`: sau `send_po()`, nếu `email_status` là `FAILED`/`SKIPPED_NO_EMAIL`, hiện
không có cách nào gửi lại email PO ngoài việc Buyer tự làm thủ công ngoài hệ thống. Thêm action
`retry_po_email` để gửi lại mà không phải chạy lại transition `APPROVED -> SENT` — xem mục 3, 4, 5
và mục 12 (T8).

**Attachment không thuộc Foundation** — dù master plan từng liệt kê "attachment tối thiểu" ở
Stage 1 (đã sửa, review v3), attachment thực sự cần ở Stage 4 (RFQ/quotation là nơi bắt buộc đính
kèm) — xem `00_business_decisions.md` mục 13 và `PUR_EXPANSION_MASTER_PLAN.md` Stage 4. File này
không có ticket nào cho attachment.

`PUR-FND-04` ("Mọi transition chính có actor, timestamp, note và audit log") **đã thoả mãn sẵn ở
tầng code** — `approve_po`/`send_po`/`close_po` gọi `log_action()` trực tiếp
(`purchasing/services.py:99,127,186`), `submit_purchase_request`/`decide_purchase_request` uỷ
quyền qua `accounts.approvals.create_approval()`/`decide_approval()` — cả hai đã tự `log_action()`
nội bộ (`accounts/approvals.py:48-51,82-85`). Không cần sửa code cho mục này. **Sửa theo review
v2**: bộ test xác nhận ban đầu (1 test, chỉ phủ `submit_purchase_request`) chưa đủ để "xác nhận"
tuyên bố "mọi transition" — mở rộng thành 5 test case bao phủ cả `approve_po`/`send_po`/`close_po`
và cả hai nhánh `decide_purchase_request` (approve/reject), xem mục 11 (`TC-PUR-FND-04-001..005`).

## 1. Actor và quyền

Foundation **không đổi RBAC hiện có** — không thêm role, không đổi quyền truy cập:

- Xem `po_detail` (nơi hiển thị Qty đã nhận đúng + tổng tiền mới): giữ nguyên
  `@po_permission_required('read')` đã có.
- Kích hoạt transition `send_po` (nơi gắn `sent_at` + trạng thái email chính xác): giữ nguyên
  quyền **`update`** trên module `po` — **đính chính so với v1** (v1 ghi nhầm là quyền `approve`;
  đối chiếu lại code xác nhận `po_send` dùng `@po_permission_required('update')`,
  `purchasing/views.py:487`, khác với `po_approve`/`po_close` dùng `approve`). Foundation giữ
  nguyên quyền `update` này — không đổi thành `approve`, vì đây là RBAC hiện có và Foundation cam
  kết không đổi RBAC (xem đầu mục). Việc gắn `sent_at`/`email_status` chỉ là side-effect thêm vào
  transition đã có, không phải lý do để siết quyền chặt hơn.
- Xem lead-time NCC (`po_supplier_performance`, dùng `sent_at` thay `created_at`): giữ nguyên
  quyền hiện có của trang này.
- Rule "Product duy nhất mỗi PO" (`PUR-FND-06`, mục 4): không cần quyền mới — validate ngay trong
  `PurchaseOrderItemFormSet` đã tồn tại (`purchasing/forms.py:60-63`), dùng chung quyền tạo/sửa PO
  hiện có (`po_create`/`po_update`).
- Kích hoạt action `retry_po_email` (`PUR-FND-07`, mục 3/12) **[mới, review lần 3]**: dùng chung
  quyền **`update`** trên module `po` — giống hệt `send_po` (cùng lý do ở trên: đây là thao tác
  gắn thêm vào một PO đã `SENT`, không phải quyền mới hay siết chặt hơn).

## 2. Trường dữ liệu

Thêm vào `PurchaseOrder` (`purchasing/models.py`):

| Field | Kiểu | Ghi chú |
|---|---|---|
| `sent_at` | `DateTimeField(null=True, blank=True)` | Set 1 lần trong `send_po()` khi `APPROVED -> SENT` — luôn set ngay khi transition xảy ra, **bất kể** kết quả gửi email (xem định nghĩa ở mục 3). PO tạo trước migration này không có giá trị thật → để `null`, **không** suy đoán bằng `created_at` (xem mục 9 Migration). |
| `email_status` | `CharField(max_length=20, choices=..., default=NOT_ATTEMPTED)` | **`max_length=20`** (bổ sung review lần 3 — giá trị dài nhất là `SKIPPED_NO_EMAIL`, 16 ký tự, chừa dư; đính chính review lần 4 — trước đó ghi nhầm 17 ký tự, `max_length=20` vẫn đúng). **5 giá trị (sửa so với v1, vốn chỉ có 3)** — v1 gộp lẫn 3 tình huống khác nhau vào cùng 1 giá trị `NOT_CONFIGURED`/thiếu hẳn 2 tình huống, review v2 tách rõ: `NOT_ATTEMPTED` (PO chưa từng qua `send_po()` — vẫn ở `DRAFT`/`APPROVED`, giá trị mặc định của field), `SKIPPED_NO_EMAIL` (đã `send_po()` nhưng NCC không có `contact_email` nên không gọi `send_mail`; đây là ý nghĩa thật của `NOT_CONFIGURED` cũ — đổi tên cho đúng bản chất, tránh nhầm với "chưa gửi"), `SENT` (`send_mail()` xác nhận gửi được — xem điều kiện chính xác ở `PUR-FND-02` mục 4), `FAILED` (đã thử gửi nhưng không thành công — exception hoặc trả về 0), `UNKNOWN_LEGACY` (chỉ set qua data migration cho PO đã ở trạng thái `SENT` trở lên **trước** migration này — code cũ không phân biệt được thật/giả nên không thể suy ra 1 trong 4 giá trị trên, xem mục 9). Không có trạng thái `queued` vì gửi đồng bộ (không Celery, theo quy ước dự án). |

**Không thêm cột** cho tổng tiền — `grand_total` là **property tính toán** trên `PurchaseOrder`,
không lưu DB. Lý do: đúng yêu cầu gốc "không cho sửa total trực tiếp" — cách chắc chắn nhất để
không ai sửa được là không có cột nào để sửa; tính lại on-the-fly cũng tránh việc tổng bị lệch nếu
1 dòng PO bị sửa mà quên đồng bộ lại tổng (đúng invariant "derived, never stored" đã áp dụng cho
`qty_available`/`qty_on_hand` ở các module khác, xem `CLAUDE.md`).
**Đặc tả chính xác bằng `Decimal` (sửa theo review v3 — v2 chỉ viết `sum(...)` không nêu kiểu, dễ
bị implement bằng `int`/`float` gây sai số làm tròn với `unit_price` là `DecimalField`)**:

```python
from decimal import Decimal

@property
def grand_total(self):
    return sum(
        (item.qty_ordered * item.unit_price for item in self.items.all()),
        Decimal("0.00"),
    )
```

`Decimal("0.00")` làm giá trị khởi tạo cho `sum()` — nếu không có, `sum()` mặc định khởi tạo bằng
`int 0`, cộng với phần tử `Decimal` đầu tiên vẫn ra `Decimal` đúng, nhưng PO **không có dòng nào**
(formset `min_num=1` nên hiếm xảy ra, vẫn nên phòng thủ) sẽ trả về `int 0` thay vì `Decimal`, gây
lệch kiểu nếu template/test so sánh với giá trị `Decimal` khác. **Chỉ dùng property này ở nơi đã
`prefetch_related('items')` hoặc trang chi tiết 1 PO** (`po_detail`, N=1 nên không đáng lo) — nếu
sau này Stage khác cần hiển thị `grand_total` ở `po_list` (N PO cùng lúc), phải thêm
`prefetch_related('items')` vào queryset trước, tránh N+1 query (mỗi PO tự query lại `items.all()`).

Không đổi `PurchaseOrderItem` fields hiện có — `qty_ordered`/`unit_price` đã đủ để tính
`grand_total`; Foundation chưa có discount/tax/freight (đó là **Stage 5**/Epic E, đính chính so
với v2 — v2 ghi nhầm "Stage 4", xác minh lại `PUR_EXPANSION_MASTER_PLAN.md`: Epic E/`PUR-PO-0*`
nằm ở "Stage 5 — PO Revision + Dashboard", không phải Stage 4/RFQ). **Có thêm 1 ràng buộc mới**
trên `PurchaseOrderItem` — xem `PUR-FND-06` ở mục 4:

| Field/Constraint | Vị trí | Ghi chú |
|---|---|---|
| `UniqueConstraint(['purchase_order', 'product'], name='unique_po_product')` | `PurchaseOrderItem.Meta.constraints` | Chặn cùng 1 Product xuất hiện 2 dòng trong 1 PO — xem `PUR-FND-06` mục 4 và migration guard mục 9. |

## 3. Trạng thái và transition

**Không đổi state machine** `DRAFT -> APPROVED -> SENT -> PARTIAL_RECEIVED/RECEIVED -> CLOSED`.
Chỉ gắn thêm side-effect vào transition đã có:

- `send_po()` (`APPROVED -> SENT`): sau khi set `po.status = SENT`, set thêm `po.sent_at =
  timezone.now()` trong cùng `update_fields`; gọi `_send_po_email()` phiên bản mới (mục 4) để lấy
  `email_status` thật thay vì gán `True` cố định.
  **Định nghĩa `sent_at` (làm rõ theo review v2)**: đây là **thời điểm hệ thống phát hành/thử gửi
  PO** (khi transition `APPROVED -> SENT` xảy ra), **không phải** thời điểm NCC xác nhận đã nhận
  được PO. `sent_at` được set **vô điều kiện** ngay khi transition thành công, hoàn toàn độc lập
  với `email_status` — kể cả khi `email_status` sau đó là `FAILED`/`SKIPPED_NO_EMAIL`, `sent_at`
  vẫn có giá trị (vì bản thân transition PO vẫn xảy ra, chỉ email là kênh thông báo phụ). Foundation
  chưa có khái niệm "NCC xác nhận" — đó là `supplier_confirmed_at`, thuộc phạm vi **Stage 5** (Epic
  E, PO xác nhận/từ chối) — **đính chính so với v2** (v2 ghi nhầm "Stage 4", xem lý do ở mục 2),
  ngoài phạm vi file này.

  **Transaction/save behavior (bổ sung review v3)** — v2 chỉ mô tả bằng lời "gọi `_send_po_email()`
  để lấy `email_status`", chưa đủ rõ để tránh lỗi "PO đã `SENT` nhưng quên lưu `email_status` thật,
  vẫn còn giá trị `default` `NOT_ATTEMPTED`". Thứ tự bắt buộc bên trong `@transaction.atomic` hiện
  có của `send_po()`:
  ```python
  po.status = PurchaseOrder.Status.SENT
  po.sent_at = timezone.now()
  po.save(update_fields=['status', 'sent_at'])

  email_status = _send_po_email(po)          # SENT / FAILED / SKIPPED_NO_EMAIL — không raise
  po.email_status = email_status
  po.save(update_fields=['email_status'])    # bắt buộc — đây là bước hay bị quên

  log_action(..., description=<nội dung theo email_status, mục 7>)
  ```
  Hai điểm bắt buộc:
  1. **`email_status` phải được ghi xuống DB bằng `save(update_fields=['email_status'])` riêng**
     (hoặc gộp chung `update_fields` của lần save đầu nếu implement gọi `_send_po_email()` trước
     khi save — thứ tự cụ thể không quan trọng, miễn cả 3 field `status`/`sent_at`/`email_status`
     đều thực sự được `save()`, không chỉ tồn tại trên đối tượng Python trong bộ nhớ). Khác với
     `_email_sent` (thuộc tính tạm trên instance, chỉ dùng để chọn flash message ở view, **không**
     phải field DB) — `email_status` là field model thật, không lưu thì mất vĩnh viễn kết quả gửi.
  2. **Email thất bại không rollback transition `SENT`** — vì `_send_po_email()` tự bắt exception
     bên trong nó (mục 4, rule `PUR-FND-02`: `try/except Exception` nằm *trong* `_send_po_email()`,
     không phải trong `send_po()`), nên `send_po()` không bao giờ nhận được exception từ bước gửi
     email; toàn bộ `@transaction.atomic` của `send_po()` chỉ có thể rollback vì lý do khác (ví dụ
     `ValidationError` ở đầu hàm khi `po.status != APPROVED`), không bao giờ vì SMTP lỗi.

  **Giới hạn chấp nhận được (bổ sung review lần 3, sửa câu chữ review lần 4)**: gửi email là
  side-effect bên ngoài (SMTP) chạy **bên trong** `@transaction.atomic` của `send_po()` (và của
  `retry_po_email()` bên dưới). Nếu email đã gửi thành công nhưng bước `save()`/`log_action()`
  ngay sau đó tự lỗi (ví dụ lỗi kết nối DB tức thời) khiến cả transaction rollback, email vẫn đã
  gửi thật cho NCC nhưng DB quay lại trạng thái trước đó (PO trở về `APPROVED`, không phải `SENT`)
  — email không thể thu hồi được. Xác suất xảy ra rất thấp (chỉ khi bước ghi DB tự lỗi ngay sau
  khi email đã gửi xong). **Foundation chấp nhận rủi ro này thay vì thêm hạ tầng** (ví dụ tách gửi
  email sang `transaction.on_commit()`, tách audit log thành 2 sự kiện riêng) — giữ thiết kế 1
  `@transaction.atomic` đơn giản như mục 3 mô tả, khớp quy ước dự án "chấp nhận giới hạn đã biết
  thay vì tăng phạm vi hạ tầng khi chưa cần thiết" (xem `CLAUDE.md` mục "Pre-deploy hardening
  deferred"). **Đính chính (review lần 4)**: `retry_po_email()` **không** phải đường khắc phục cho
  chính tình huống này — nó chỉ gọi được khi `po.status == SENT`, trong khi PO vừa rollback lại về
  `APPROVED`; người dùng chỉ có thể bấm `send_po()` lại như bình thường, và vì DB không còn dấu vết
  gì về lần gửi email đã rollback, `send_po()` lần 2 hoàn toàn có thể gửi email trùng cho NCC. Đây
  là **giới hạn đã biết, không có cơ chế tự động phát hiện/ngăn trùng lặp trong Foundation** — nếu
  tình huống này xảy ra thật ở production, người xử lý (Buyer/Manager) phải tự xác nhận với NCC
  xem email đã nhận được hay chưa trước khi gửi lại thủ công. Foundation chưa bảo đảm
  exactly-once delivery cho luồng gửi email PO.

- **`retry_po_email()` (`PUR-FND-07`) [mới, review lần 3, sửa review lần 4]** — không phải
  transition mới, PO vẫn giữ nguyên `status=SENT`/`sent_at` không đổi; chỉ gửi lại email và cập
  nhật `email_status`. Bù cho việc `send_po()` chỉ chạy đúng 1 lần lúc `APPROVED -> SENT`, không
  có đường retry nếu email lỗi ngay lần đầu (mục 0). Điều kiện gọi được — **2 tầng, cả hai đều bắt
  buộc**:
  1. `po.status == SENT` **và** `po.email_status in (FAILED, SKIPPED_NO_EMAIL)` — mọi trường hợp
     khác (`email_status in (NOT_ATTEMPTED, SENT, UNKNOWN_LEGACY)`, hoặc `status != SENT`) raise
     `ValidationError`.
  2. **[sửa review lần 4]** `po.supplier.contact_email` phải có giá trị tại **thời điểm gọi
     retry** (đọc lại từ DB, không phải giá trị `contact_email` tại thời điểm `send_po()` chạy
     lần đầu — NCC có thể đã được bổ sung hoặc xoá email từ đó tới nay) — nếu không, raise
     `ValidationError`, **không** gọi `send_mail`, **không** tạo `AuditLog` mới. **Lý do**: nếu bỏ
     qua check này, `email_status=SKIPPED_NO_EMAIL` mà NCC vẫn chưa có email sẽ tạo ra nút "Gửi lại
     email" hiển thị nhưng gọi xong vẫn chắc chắn trả về `SKIPPED_NO_EMAIL` — vừa đánh lừa người
     dùng vào 1 hành động vô nghĩa, vừa sinh thêm `AuditLog` rác cho mỗi lần bấm. Ngược lại, đây
     chính là **con đường hợp lệ duy nhất** để thoát khỏi `SKIPPED_NO_EMAIL`: Buyer/Manager bổ
     sung `contact_email` cho NCC (qua `partners` app, ngoài phạm vi file này) rồi mới bấm "Gửi lại
     email" — lúc đó check này pass và `_send_mail()` được gọi thật.

  Cả 2 check đều raise cùng exception type (`ValidationError`) nhưng **thông điệp khác nhau** (mục
  8) để người dùng phân biệt "PO chưa ở trạng thái gửi lại được" với "cần bổ sung email NCC
  trước". Chặn ở tầng service (mục 4, rule `PUR-FND-07`). Hành vi bên trong
  `@transaction.atomic` riêng của action này (chỉ chạy tới khi cả 2 check trên đều pass):
  ```python
  email_status = _send_po_email(po)          # dùng lại nguyên hàm của send_po(), mục 4
  po.email_status = email_status
  po.save(update_fields=['email_status'])    # không đụng status/sent_at

  log_action(..., description=<nội dung "gửi lại email", khác log gốc của send_po(), mục 7>)
  ```
  Quyền: giống `send_po`, actor cần quyền `update` trên module `po` (mục 1).
- Các transition khác (`approve_po`, `close_po`, PR submit/decide) không đổi.

## 4. Business rules

1. **`PUR-FND-01`** — Mọi truy vấn "Qty đã nhận" theo PO phải loại trừ đồng thời `GrnItem.status =
   REJECTED` **và** `Grn.status = CANCELLED`. Hiện có 3 chỗ tính giá trị này
   (`sync_po_status`, `BaseGrnItemFormSet.clean` ở `receiving/forms.py`, `po_detail`) — 2/3 đã đúng,
   chỉ `po_detail` thiếu. Rule kỹ thuật: **rút thành 1 hàm dùng chung**
   (`purchasing.services.received_qty_by_product(po)`), cả `sync_po_status` và `po_detail` gọi
   chung hàm này — không giữ 2 bản sao của cùng 1 query, để lớp bug "sửa 1 chỗ quên chỗ kia" không
   tái diễn.
2. **`PUR-FND-02`** — `_send_po_email()` trả về 1 trong 3 giá trị `email_status` khả dĩ tại runtime
   (`SENT`/`FAILED`/`SKIPPED_NO_EMAIL` — `NOT_ATTEMPTED`/`UNKNOWN_LEGACY` không bao giờ được hàm
   này trả về; `NOT_ATTEMPTED` chỉ là default trước khi `send_po()` chạy, `UNKNOWN_LEGACY` chỉ xuất
   hiện qua migration):
   - NCC không có `contact_email` → trả `SKIPPED_NO_EMAIL` ngay, không gọi `send_mail`.
   - Gọi `send_mail(..., fail_silently=False)` trong `try/except Exception`.
   - **Ghi log ứng dụng khi có exception (bổ sung review lần 3)**: trong nhánh `except Exception`,
     gọi `logger.exception(...)` (Python `logging` chuẩn, module logger riêng của
     `purchasing/services.py` — không phải `AuditLog`) để giữ traceback đầy đủ phục vụ chẩn đoán
     SMTP sau này. **Không** lưu toàn bộ nội dung lỗi/traceback vào `AuditLog` — audit log chỉ cần
     nêu "gửi thất bại" cho người dùng cuối, không phải log kỹ thuật.
   - **Sửa theo review v2**: v1 chỉ dựa vào "không có exception" để kết luận `SENT` — chưa đủ,
     vì `send_mail()` trả về **số email gửi thành công** (`int`) và có thể trả `0` mà **không**
     raise exception (ví dụ backend chấp nhận nhưng không gửi được). Điều kiện `SENT` phải là
     **cả hai**: không có exception **và** giá trị trả về của `send_mail()` > 0. Có exception
     hoặc trả về `0` (không exception nhưng 0 email gửi được) đều → `FAILED`.
   - Audit log mô tả (`log_action` trong `send_po`) phải dùng đúng 1 trong 3 nhánh câu tương ứng
     (`SENT`/`FAILED`/`SKIPPED_NO_EMAIL`), không còn khẳng định "đã gửi" khi thực tế `FAILED`, và
     không gộp `FAILED`/`SKIPPED_NO_EMAIL` vào 1 câu như code hiện tại.
3. **`PUR-FND-03`** — `supplier_lead_time_stats()` tính lead-time thực tế bằng `received_at -
   sent_at`, chỉ tính các PO có **cả hai** giá trị khác `null` (PO cũ chưa có `sent_at` bị loại
   khỏi thống kê thay vì tính sai bằng `created_at`). **Sửa theo review v2 — lỗi kiểu dữ liệu**:
   `PurchaseOrder.received_at` là `DateField` (đã xác minh `purchasing/models.py:68`), còn
   `sent_at` (mục 2) là `DateTimeField` — hai kiểu này **không trừ trực tiếp được**
   (`date.__sub__(datetime)` raise `TypeError` trong Python). Công thức đúng bắt buộc ép `sent_at`
   về `date` theo giờ địa phương trước:
   ```python
   sent_date = timezone.localtime(po.sent_at).date()
   lead_time_days = (po.received_at - sent_date).days
   ```
   Dùng `timezone.localtime()` (không phải `.date()` trần trên datetime UTC) — đúng quy ước dự án
   về so sánh ngày theo giờ Việt Nam đã ghi trong `CLAUDE.md` § "business-date comparison".
4. **`PUR-FND-05`** — `grand_total` luôn là `Σ(qty_ordered × unit_price)` của các dòng hiện tại,
   không có API/field nào cho phép ghi đè trực tiếp.
5. **`PUR-FND-06`** **[mới, review v3 — Critical #1]** — Một Product chỉ được xuất hiện **tối đa 1
   dòng** trong 1 PO. **Lý do**: công thức đối chiếu `received_by_product` ở `PUR-FND-01` tổng hợp
   qty đã nhận **theo `product_id`** (`services.py:52-59`), rồi so sánh với `qty_ordered` **của
   từng dòng riêng lẻ** (`services.py:61-64`). Nếu PO có 2 dòng cùng Product A (mỗi dòng
   `qty_ordered=10`) và GRN mới nhận tổng 10, cả 2 dòng đều tự so `received_by_product[A]=10 >=
   10` và **đều** báo "đã nhận đủ" — trong khi thực tế PO cần tổng 20, mới nhận 10. Bug này tồn
   tại sẵn ở `sync_po_status()` hiện tại, và nếu `PUR-FND-01` chỉ rút thành `received_qty_by_product()`
   dùng chung mà không sửa gì thêm, bug sẽ nhân đôi ra cả `po_detail`.
   **Phương án chọn** (theo đúng khuyến nghị review — không sửa `GrnItem` để trỏ thẳng
   `PurchaseOrderItem`, vì đó là thay đổi lớn hơn hẳn phạm vi Foundation): bắt buộc **Product duy
   nhất trong mỗi PO**, chặn ở 2 tầng —
   - **Formset** (`PurchaseOrderItemFormSet`, `purchasing/forms.py:60-63`): thêm
     `UniqueConstraint(['purchase_order', 'product'], name='unique_po_product')` vào
     `PurchaseOrderItem.Meta.constraints` (mục 2) — **không cần viết `clean()` tay**, Django's
     `BaseInlineFormSet.validate_unique()` tự phát hiện `UniqueConstraint` không điều kiện qua
     `_get_unique_checks()` và báo lỗi form ngay khi 2 dòng cùng Product được submit trong cùng 1
     PO (đã xác minh hành vi này là built-in từ Django 4.1+, dự án đang dùng bản mới hơn).
   - **DB**: `UniqueConstraint` ở trên tự nó là hàng rào cuối — chặn cả đường ghi trực tiếp không
     qua form (ví dụ qua Django Admin, dù `purchasing.admin` đã có `ServiceManagedAdminMixin`).
   - **Dữ liệu cũ**: xem migration guard bắt buộc ở mục 9 — không thể thêm `UniqueConstraint` an
     toàn nếu đã có PO thật vi phạm nó.
6. **`PUR-FND-07`** **[mới, review lần 3, sửa review lần 4]** — `retry_po_email()` chỉ được gọi
   khi **cả 2** điều kiện sau đều đúng:
   - `po.status == SENT` **và** `po.email_status in (FAILED, SKIPPED_NO_EMAIL)`; mọi trường hợp
     khác (`status != SENT`, hoặc `email_status in (NOT_ATTEMPTED, SENT, UNKNOWN_LEGACY)`) raise
     `ValidationError` — thông điệp nêu rõ "PO chưa ở trạng thái có thể gửi lại email".
   - **[mới, review lần 4]** `po.supplier.contact_email` (đọc lại từ DB tại thời điểm gọi, không
     dùng giá trị cache/cũ) phải khác rỗng; nếu không, raise `ValidationError` — thông điệp nêu rõ
     "Nhà cung cấp chưa có email, vui lòng bổ sung trước khi gửi lại" — **không** gọi
     `send_mail`, **không** tạo `AuditLog` mới cho lần gọi bị chặn này. Đây là điều kiện bắt buộc
     để đóng lỗ hổng: nếu bỏ qua, `email_status=SKIPPED_NO_EMAIL` mà NCC vẫn chưa có email sẽ cho
     phép gọi `retry_po_email()` một cách vô nghĩa (chắc chắn lại trả `SKIPPED_NO_EMAIL`, sinh
     `AuditLog` rác) — trường hợp NCC **đã** được bổ sung `contact_email` sau khi PO chuyển
     `SKIPPED_NO_EMAIL` mới là ca hợp lệ cho retry.
   Cả 2 nhánh chặn dùng chung exception type nhưng thông điệp khác nhau (mục 8). Dùng lại nguyên
   `_send_po_email()` (không viết hàm gửi email thứ hai) để 2 đường gửi — lần đầu qua `send_po()`,
   gửi lại qua `retry_po_email()` — không bao giờ lệch logic điều kiện `SENT`/`FAILED` với nhau
   (mục 3).

## 5. Màn hình

- `po_detail.html`: thêm dòng tổng tiền (`grand_total`) dưới bảng item; sửa hiển thị trạng thái
  email dùng đủ 5 giá trị `email_status` (mục 2) thay vì suy luận nhị phân — PO ở `DRAFT`/`APPROVED`
  hiển thị `NOT_ATTEMPTED` (hoặc ẩn dòng này, chưa có gì để báo); PO cũ trước migration hiển thị rõ
  "Không rõ (dữ liệu trước nâng cấp)" cho `UNKNOWN_LEGACY`, không hiển thị nhầm thành "đã gửi".
- View `po_send` (`purchasing/views.py:487-503`): quyền giữ nguyên `update` (đính chính mục 1).
  Flash message theo 3 nhánh runtime — đã gửi thành công (`SENT`) / gửi thất bại (`FAILED`, khác
  với "NCC chưa có email" hiện tại đang bị gộp chung vào 1 nhánh `else`) / NCC chưa có email
  (`SKIPPED_NO_EMAIL`).
- `po_supplier_performance` (trang thống kê lead-time): không đổi layout, chỉ đổi nguồn dữ liệu
  (mục 4.3) — nên thêm 1 dòng ghi chú nhỏ "chỉ tính PO có ngày gửi thực tế" để không gây hiểu lầm
  khi số PO trong thống kê giảm so với trước (PO cũ chưa có `sent_at`).
- `po_detail.html` **[mới, review lần 3, sửa review lần 4]**: khi PO ở `status=SENT` **và**
  `email_status in (FAILED, SKIPPED_NO_EMAIL)`, hiển thị khu vực "Gửi lại email" — nội dung cụ thể
  tuỳ `po.supplier.contact_email`:
  - **Có `contact_email`**: hiện nút "Gửi lại email" gọi `retry_po_email`, bấm được.
  - **Không có `contact_email`** **[mới, review lần 4]**: nút bị **vô hiệu hoá** (`disabled`,
    không phải ẩn hẳn — người dùng cần thấy vẫn có hành động này tồn tại), kèm dòng chú thích
    "Vui lòng bổ sung email nhà cung cấp trước khi gửi lại" liên kết sang trang sửa NCC
    (`partners:supplier_update` hoặc tương đương, ngoài phạm vi sửa ở file này — chỉ cần link).
    **Đây là UX convenience, không phải hàng rào bảo vệ** — tầng service (mục 3/4) mới là nơi thật
    sự chặn, theo đúng nguyên tắc "Form querysets filter, services must re-validate independently"
    (`CLAUDE.md`).
  Ẩn hẳn khu vực này khi `email_status in (SENT, NOT_ATTEMPTED, UNKNOWN_LEGACY)` hoặc
  `status != SENT` (mục 3/4, `PUR-FND-07`). Chỉ hiện với actor có quyền `update` trên module `po`
  (mục 1), giống điều kiện ẩn/hiện nút hiện có của `send_po`.
- `po_create`/`po_update` (form `PurchaseOrderItemFormSet`) **[mới, review v3]**: khi submit 2
  dòng cùng Product, Django tự hiện lỗi form-level tại dòng vi phạm (từ `UniqueConstraint` mục 2 —
  không cần custom text, nhưng nên kiểm nội dung lỗi mặc định của Django đủ dễ hiểu bằng tiếng
  Việt hay cần override — xem test `TC-PUR-FND-06-002` mục 11).

Không có màn hình mới — Foundation không thêm luồng nghiệp vụ, chỉ sửa độ chính xác của luồng cũ.

## 6. Notification

Không đổi. Không có notification mới nào gắn với `sent_at`/`email_status`/`grand_total` — đây là
dữ liệu hiển thị, không phải sự kiện cần báo ai.

## 7. Audit log

- `send_po()`: nội dung `log_action` phải nêu đúng 1 trong 3 kết quả runtime (`SENT`/`FAILED`/
  `SKIPPED_NO_EMAIL`) thay vì luôn viết "Đã gửi email tới NCC" hay chung chung "chỉ cập nhật trạng
  thái" như hiện tại (dòng này đang lẫn 2 trường hợp `FAILED` và "NCC chưa có email" vào cùng 1
  câu). `UNKNOWN_LEGACY` không bao giờ xuất hiện trong audit log mới — chỉ là giá trị backfill.
- `retry_po_email()` **[mới, review lần 3]**: `log_action` riêng cho lần gửi lại, nội dung nêu rõ
  đây là **retry** (khác câu chữ với audit log gốc của `send_po()`, để không nhầm 2 sự kiện khi đọc
  lịch sử PO), nêu đúng kết quả runtime mới (`SENT`/`FAILED`).
- Các transition khác: không đổi, đã đạt `PUR-FND-04` (xem mục 0).

## 8. Validation

`PUR-FND-01/02/03/05` không có input mới từ người dùng — thuần logic backend/hiển thị, không cần
validation tầng form. **`PUR-FND-06` là ngoại lệ (thêm review v3)**: form `PurchaseOrderItemFormSet`
đã tồn tại (`po_create`/`po_update`) nay có thêm 1 ràng buộc — không cho submit 2 dòng cùng
Product trong cùng 1 PO. Không cần code validation tay ở tầng form (mục 5) — chỉ cần thêm
`UniqueConstraint` vào `Meta.constraints` (mục 2), Django's `BaseInlineFormSet.validate_unique()`
tự kích hoạt. Việc còn lại ở tầng form chỉ là viết test xác nhận lỗi hiển thị đúng chỗ
(`TC-PUR-FND-06-002`), không phải viết logic validate mới.

`PUR-FND-07` **[mới, review lần 3, sửa review lần 4]** cũng không có input form — `retry_po_email`
chỉ nhận PO pk từ URL, validate hoàn toàn ở tầng service (mục 4) bằng **2 điều kiện độc lập**:
`status`/`email_status`, và `po.supplier.contact_email` phải khác rỗng tại thời điểm gọi (không
tin vào việc nút UI đã bị vô hiệu hoá ở mục 5 — đó chỉ là convenience, service phải tự kiểm tra
lại, theo đúng nguyên tắc "Form querysets filter, services must re-validate independently" đã ghi
trong `CLAUDE.md`). Không cần form riêng cho cả 2 điều kiện.

## 9. Migration dữ liệu cũ

- Migration thêm `sent_at`/`email_status` vào `PurchaseOrder`: **không backfill suy đoán** cho
  `sent_at` — luôn để `null` cho PO đã tồn tại, không suy ra `sent_at = created_at` vì đó chính là
  sai số `PUR-FND-03` đang muốn sửa (đúng nguyên tắc "Backfill timestamp không có dữ liệu thật phải
  để null/unknown" đã ghi trong `CLAUDE.md` § "Established patterns", cũng khớp mục 14 master
  plan).
  **`email_status` — sửa theo review v2, phân biệt 2 nhóm PO cũ thay vì gán 1 giá trị mặc định cho
  tất cả**:
  - PO cũ đã ở trạng thái `{SENT, PARTIAL_RECEIVED, RECEIVED, CLOSED}` **trước** migration (đã
    từng chạy qua `send_po()` bản cũ) → data migration set `email_status = UNKNOWN_LEGACY` — code
    cũ luôn `return True` bất kể kết quả thật (xem mục 0), nên không thể suy ra `SENT`/`FAILED`.
  - PO cũ vẫn còn ở `{DRAFT, APPROVED}` trước migration (chưa từng gửi) → giữ **`NOT_ATTEMPTED`**
    (giá trị `default` của field, không cần data migration riêng) — đây là PO thật sự chưa gửi,
    gán `UNKNOWN_LEGACY` cho nhóm này sẽ sai (ngụ ý "đã gửi nhưng không rõ kết quả" trong khi thực
    ra chưa hề gửi).
  - Điều kiện phân nhóm dùng đúng `status`, không dùng `sent_at IS NULL` để suy luận (`sent_at`
    của cả 2 nhóm đều `null` sau bước trên — không phân biệt được nếu chỉ nhìn `sent_at`).
- Hệ quả: `supplier_lead_time_stats()` sau khi đổi nguồn dữ liệu sẽ **giảm số PO có trong thống
  kê** ngay sau khi migrate (chỉ PO gửi mới từ nay trở đi mới có `sent_at`) — đây là đánh đổi có
  chủ đích (dữ liệu đúng nhưng ít hơn, thay vì dữ liệu sai nhưng đầy đủ), cần lưu ý khi xem báo cáo
  ngay sau khi deploy.
- `email_status` không có migration ngược — PO cũ không có cách nào biết email đã thật sự gửi
  thành công hay chưa trong quá khứ (audit log cũ, nếu có, chỉ ghi "Đã gửi" một cách sai — không
  đáng tin để backfill).

**Migration guard cho `PUR-FND-06` (mới, review v3 — bắt buộc, khác nguyên tắc "để null" ở trên
vì đây là ràng buộc uniqueness, không phải backfill giá trị. Viết lại toàn bộ theo review lần 3 —
2 lỗi Critical trong bản v3: migration gọi lại service hiện tại, và có nhánh bỏ guard)**:

- **Bước 1 — báo cáo trước khi migrate schema (không đổi so với v3)**: thêm hàm read-only
  `purchasing.services.find_duplicate_po_products()` — trả về danh sách `(po, product, [item_ids])`
  cho mọi PO đang có ≥2 dòng cùng Product, dùng model ORM hiện tại (`purchasing.models.
  PurchaseOrderItem`). Chạy hàm này (qua `manage.py shell` hoặc management command tạm) **trước
  khi** áp dụng migration thêm `UniqueConstraint`, để biết trước quy mô dữ liệu cần dọn trên
  production. Hàm này **chỉ dùng để kiểm tra thủ công/định kỳ ở tầng ứng dụng — migration ở Bước 2
  không được gọi lại nó** (xem lý do ngay dưới).
- **Bước 2 — migration tự chặn nếu còn vi phạm, viết độc lập với model/service hiện tại (sửa theo
  review lần 3 — Critical)**: migration **không được** `import`/gọi lại
  `purchasing.services.find_duplicate_po_products()` hay bất kỳ model hiện tại nào từ
  `purchasing.models` — migration phải chạy đúng kể cả nhiều năm sau, khi service/model hiện tại đã
  đổi form, đổi tên field, hoặc bị xoá hẳn. Dùng **historical model** qua `apps.get_model()` (tham
  số `apps` mà Django tự truyền vào mọi hàm `RunPython`), viết lại đúng phép đếm trùng lặp **ngay
  trong file migration**, không phụ thuộc `purchasing/services.py`:
  ```python
  from django.db import migrations, models
  from django.db.models import Count


  def ensure_no_duplicate_po_products(apps, schema_editor):
      PurchaseOrderItem = apps.get_model("purchasing", "PurchaseOrderItem")

      duplicates = (
          PurchaseOrderItem.objects
          .values("purchase_order_id", "product_id")
          .annotate(item_count=Count("id"))
          .filter(item_count__gt=1)
      )

      if duplicates.exists():
          raise RuntimeError(
              "Không thể thêm UniqueConstraint(purchase_order, product) — còn PO vi phạm: "
              f"{list(duplicates)}. Xử lý thủ công (gộp/xoá dòng thừa theo nghiệp vụ thật của "
              "từng PO) rồi chạy lại migration này."
          )


  class Migration(migrations.Migration):
      dependencies = [...]
      operations = [
          migrations.RunPython(ensure_no_duplicate_po_products, migrations.RunPython.noop),
          migrations.AddConstraint(
              model_name="purchaseorderitem",
              constraint=models.UniqueConstraint(
                  fields=["purchase_order", "product"], name="unique_po_product"
              ),
          ),
      ]
  ```
  Dùng `RuntimeError` với thông báo rõ ràng — **không** viết `migrations.exceptions...` (không tồn
  tại loại exception này trong Django); bất kỳ exception nào raise trong `RunPython` cũng đủ khiến
  `migrate` dừng lại, không cần loại đặc biệt. `migrations.RunPython.noop` cho chiều lùi (`migrate`
  xuống migration trước) — một hàm kiểm tra không có gì để "undo", lùi migration chỉ cần bỏ
  `UniqueConstraint` lại (Django tự làm ở `RemoveConstraint` khi lùi qua op `AddConstraint`, không
  liên quan tới `RunPython`). **Không tự động gộp/xoá dòng trùng** — 2 dòng cùng Product có thể
  khác `unit_price` (đàm phán giá khác nhau ở 2 thời điểm khác nhau khi tạo PO), gộp tự động có thể
  sai số tiền thật đã cam kết với NCC.
- **Guard luôn có mặt, không có nhánh bỏ qua (sửa theo review lần 3 — Critical)** — bản v3 từng
  viết "nếu Bước 1 xác nhận 0 PO vi phạm thì Bước 2 chỉ là `AddConstraint` thường, không cần
  `RunPython` guard". **Bỏ hẳn nhánh này.** Khoảng thời gian giữa lúc chạy Bước 1 (kiểm tra thủ
  công) và lúc thật sự `migrate` trên production vẫn có thể phát sinh PO vi phạm mới (import thủ
  công, script khác chạy song song, hoặc đơn giản là có người tạo PO đúng lúc đó). `RunPython`
  guard ở Bước 2 **luôn luôn** đứng ngay trước `AddConstraint` trong migration này, không phụ thuộc
  kết quả Bước 1 — Bước 1 chỉ để biết trước quy mô dọn dữ liệu cần làm, không phải điều kiện quyết
  định có cần guard hay không.

## 10. Acceptance criteria

1. `po_detail` hiển thị đúng Qty đã nhận/còn lại khi có 1 GRN `CANCELLED` — kết quả khớp với
   `sync_po_status()` cho cùng PO (2 hàm dùng chung 1 nguồn tính, không thể lệch nhau nữa).
2. Khi `send_mail()` raise exception **hoặc** trả về `0` (không exception) (mock SMTP lỗi/mock trả
   `0`), `send_po()` vẫn hoàn tất transition `APPROVED -> SENT` (không rollback status vì lỗi email
   không phải lỗi nghiệp vụ chặn gửi PO), nhưng `email_status = FAILED` và audit log/flash message
   phản ánh đúng thất bại — không còn câu khẳng định giả. **[sửa v2]** thêm nhánh "trả về `0`" vào
   tiêu chí, không chỉ riêng nhánh exception.
3. `supplier_lead_time_stats()` chỉ tính PO có cả `sent_at` và `received_at`; PO thiếu `sent_at`
   (dữ liệu cũ) không góp vào trung bình. Công thức dùng đúng `timezone.localtime(po.sent_at).date()`
   trước khi trừ `received_at` (`DateField`) — không raise `TypeError` do lệch kiểu `date`/`datetime`
   **[thêm v2]**.
4. `po_detail` hiển thị đúng `grand_total` = tổng `qty_ordered × unit_price` mọi dòng, không có
   cách nào từ UI sửa trực tiếp con số này.
5. PO đã tồn tại trước migration hiển thị đúng nhóm `email_status`: PO đã từng `SENT` trở lên →
   `UNKNOWN_LEGACY`; PO còn `DRAFT`/`APPROVED` → `NOT_ATTEMPTED` — không lẫn 2 nhóm **[thêm v2]**.
6. `send_po` chỉ actor có quyền `update` trên module `po` mới gọi được (giữ đúng RBAC hiện có, đối
   chiếu code — không phải `approve`) **[thêm v2]**.
7. **[thêm v3]** Submit `PurchaseOrderItemFormSet` với 2 dòng cùng Product bị formset chặn (lỗi
   hiển thị đúng dòng vi phạm), PO không được tạo/sửa — `PUR-FND-06`.
8. **[thêm v3]** Migration thêm `UniqueConstraint(['purchase_order', 'product'])` không chạy được
   nếu còn PO vi phạm trên dữ liệu hiện tại (guard tự dừng, in báo cáo) — không âm thầm gộp/xoá dữ
   liệu.
9. **[thêm v3]** Sau `send_po()` thành công, `PurchaseOrder.objects.get(pk=po.pk).email_status`
   (đọc lại từ DB, không phải đọc lại instance trong bộ nhớ) khớp đúng 1 trong 3 giá trị runtime —
   xác nhận `email_status` thực sự được `save()`, không chỉ tồn tại tạm trên object Python.
10. **[thêm v3]** User chỉ có quyền `read` trên module `po` (không có `update`) gọi `POST
    /purchasing/po/<pk>/send/` nhận `403`/`PermissionDenied`, PO không đổi trạng thái.
11. 139 test PUR hiện có + toàn bộ test mới cho các mục trên đều pass.
12. **[thêm review lần 3, sửa review lần 4]** `retry_po_email` chỉ gọi được khi **cả 2**: PO
    `status=SENT` **và** `email_status in (FAILED, SKIPPED_NO_EMAIL)`, **và**
    `po.supplier.contact_email` khác rỗng tại thời điểm gọi — thiếu 1 trong 2 đều bị chặn bằng
    `ValidationError` (không gọi `send_mail`, không tạo `AuditLog` mới); sau khi gọi thành công,
    `status`/`sent_at` của PO không đổi, chỉ `email_status` (và audit log) cập nhật.
13. **[thêm review lần 3]** File migration thêm `UniqueConstraint(['purchase_order', 'product'])`
    không chứa bất kỳ `import` nào từ `purchasing.services`/`purchasing.models` — xác nhận bằng
    cách đọc lại nội dung file migration, hàm guard chỉ dùng model lấy qua `apps.get_model()`.

## 11. Test case

Đặt tên theo convention `TC-PUR-FND-0X-00Y` (khác `TC-<MODULE>-<FR#>-<seq>` chuẩn chung của
`BACKLOG.md` vì đây là sáng kiến ngoài 60-FR — dùng tiền tố `PUR-FND` để rõ nguồn):

- `TC-PUR-FND-01-001`: PO có 1 GRN `CANCELLED` (đã nhận qty > 0 trước khi huỷ) → `po_detail`
  hiển thị Qty đã nhận = 0 cho dòng đó (regression test trực tiếp cho gap đã xác minh, viết ở tầng
  view/response, không chỉ tầng service như `TC_PUR_SYNC_005` hiện có).
- `TC-PUR-FND-02-001`: mock `purchasing.services.send_mail` raise → `_send_po_email()` trả về
  `FAILED`, `send_po()` vẫn set `status=SENT` thành công.
- `TC-PUR-FND-02-002`: NCC không có `contact_email` → `email_status = SKIPPED_NO_EMAIL` **[đổi tên
  giá trị v2, hành vi giữ nguyên: không gọi `send_mail`]**.
- `TC-PUR-FND-02-003`: mock `send_mail` trả về `1` (thành công) → `email_status = SENT`.
- `TC-PUR-FND-02-004` **[thêm v2]**: mock `send_mail` trả về `0` mà **không** raise exception →
  `email_status = FAILED` (không phải `SENT`) — regression test riêng cho lỗ hổng "chỉ dựa vào
  exception" đã nêu trong review.
- `TC-PUR-FND-02-005` **[thêm v3]**: sau `send_po()`, đọc lại PO bằng
  `PurchaseOrder.objects.get(pk=po.pk)` (query mới, không phải instance cũ trong test) →
  `email_status` khớp đúng giá trị runtime — xác nhận field thật sự được `save()` (mục 3 "Transaction/
  save behavior"), không chỉ tồn tại trên object Python.
- `TC-PUR-FND-02-006` **[thêm v3]**: user chỉ có quyền `read` trên module `po` → `POST` tới URL
  `po_send` trả `403`, PO giữ nguyên `status=APPROVED`, không có `AuditLog` mới nào được tạo.
- `TC-PUR-FND-02-007` **[thêm v3]**: user có quyền `update` trên module `po` → `POST` tới `po_send`
  thành công (302 redirect), PO chuyển `SENT`.
- `TC-PUR-FND-02-008` **[thêm v3]**: 3 sub-case, mock từng nhánh runtime (`SENT`/`FAILED`/
  `SKIPPED_NO_EMAIL`) → flash message trả về đúng nội dung tương ứng (dùng
  `response.context['messages']` hoặc `django.test.Client` follow redirect), không lẫn 2 nhánh
  `FAILED`/`SKIPPED_NO_EMAIL` vào chung 1 câu như code hiện tại (mục 5).
- `TC-PUR-FND-03-001`: PO có `sent_at`/`received_at` cách nhau N ngày → `avg_actual_lead_time_days`
  tính đúng N, không lệch theo `created_at`.
- `TC-PUR-FND-03-002`: PO chưa có `sent_at` (dữ liệu cũ) → bị loại khỏi `received_po_count`.
- `TC-PUR-FND-03-003` **[thêm v2]**: gọi `supplier_lead_time_stats()` với PO có `sent_at` thật (không
  mock kiểu dữ liệu) không raise `TypeError` — regression test riêng cho lỗi lệch kiểu
  `DateField`/`DateTimeField` đã nêu trong review; assert kết quả số ngày đúng bằng tay
  (`(received_at - sent_at.date()).days`) để chốt luôn công thức, không chỉ chốt "không crash".
- `TC-PUR-FND-05-001`: PO 2 dòng, `qty_ordered`/`unit_price` khác nhau → `grand_total` = tổng đúng,
  kiểu trả về là `Decimal` (assert `isinstance(result, Decimal)`, không chỉ so bằng giá trị số).
- `TC-PUR-FND-05-002` **[thêm v3]**: PO có `email_status=UNKNOWN_LEGACY` (dữ liệu giả lập trước
  migration) → `po_detail` render đúng chuỗi "Không rõ (dữ liệu trước nâng cấp)", không hiển thị
  nhầm thành "đã gửi" hay để trống (mục 5).
- `TC-PUR-FND-06-001` **[thêm v3]**: submit `PurchaseOrderItemFormSet` với 2 dòng cùng `product`
  trong cùng 1 PO (`po_create`) → formset `is_valid() == False`, PO/PO item không được lưu.
- `TC-PUR-FND-06-002` **[thêm v3]**: PO đã có sẵn 1 dòng Product A, `po_update` thêm 1 dòng Product
  A nữa → formset invalid với lỗi gắn đúng vào dòng vừa thêm (mục 5).
- `TC-PUR-FND-06-003` **[thêm v3, sửa review lần 3]**: fixture có sẵn PO vi phạm (2
  `PurchaseOrderItem` cùng `purchase_order`+`product`, tạo trực tiếp qua ORM để né formset
  validation, mô phỏng dữ liệu cũ trước constraint) → gọi trực tiếp hàm `RunPython` của migration
  (`ensure_no_duplicate_po_products`, mục 9 Bước 2 — import từ module migration, gọi với
  `apps=django.apps.apps`) raise `RuntimeError` đúng như thiết kế; **không** gọi qua
  `find_duplicate_po_products()` (đó là hàm ứng dụng riêng ở Bước 1, migration không import nó —
  đây chính là điều test phải xác nhận, không phải giả định).
- `TC-PUR-FND-06-004` **[thêm v3, sửa review lần 3]**: fixture sạch (không PO nào vi phạm) → cả
  `find_duplicate_po_products()` (Bước 1) **và** `ensure_no_duplicate_po_products` (Bước 2, gọi
  trực tiếp như test 003) đều không raise/trả rỗng; migration thêm `UniqueConstraint` chạy thành
  công qua `manage.py migrate` trên DB test; PO hợp lệ (nhiều dòng khác Product, hoặc 2 PO khác
  nhau cùng chứa Product A) vẫn tạo/lưu bình thường — không bị chặn nhầm.
- `TC-PUR-FND-07-001` **[mới, review lần 3]**: PO `status=SENT`, `email_status=FAILED`, mock
  `send_mail` trả về `1` (thành công) → `retry_po_email()` cập nhật `email_status=SENT`;
  `status`/`sent_at` giữ nguyên như trước khi retry.
- `TC-PUR-FND-07-002` **[mới, review lần 3, sửa review lần 4]**: PO `status=SENT`,
  `email_status=SKIPPED_NO_EMAIL`, NCC **vẫn chưa có** `contact_email` tại thời điểm retry →
  `retry_po_email()` raise `ValidationError`, **không** gọi `send_mail`, **không** tạo `AuditLog`
  mới, `email_status` giữ nguyên `SKIPPED_NO_EMAIL` **[đổi hành vi so với v3/v4 cũ — trước đây kỳ
  vọng gọi thành công và vẫn trả `SKIPPED_NO_EMAIL`, nay bị chặn hẳn ở tầng service trước khi gọi
  `_send_po_email()`]**.
- `TC-PUR-FND-07-007` **[mới, review lần 4]**: PO `status=SENT`, `email_status=SKIPPED_NO_EMAIL`,
  NCC **đã được bổ sung** `contact_email` sau khi PO chuyển `SKIPPED_NO_EMAIL` (cập nhật
  `supplier.contact_email` trực tiếp trong test trước khi gọi retry), mock `send_mail` trả về `1`
  → `retry_po_email()` gọi được, `email_status` chuyển thành `SENT`, tạo đúng 1 `AuditLog` mới —
  xác nhận đây là con đường hợp lệ duy nhất để thoát khỏi `SKIPPED_NO_EMAIL` (mục 3).
- `TC-PUR-FND-07-003` **[mới, review lần 3]**: PO có `email_status=SENT` (đã gửi thành công từ
  trước) → gọi `retry_po_email()` raise `ValidationError`, không gọi `send_mail`, không có
  `AuditLog` mới nào được tạo.
- `TC-PUR-FND-07-004` **[mới, review lần 3]**: PO `status=DRAFT`/`APPROVED` (`email_status` còn
  `NOT_ATTEMPTED`, chưa từng qua `send_po()`) → gọi `retry_po_email()` raise `ValidationError`.
- `TC-PUR-FND-07-005` **[mới, review lần 3]**: sau khi `send_po()` rồi `retry_po_email()` thành
  công trên cùng 1 PO → tồn tại đúng 2 `AuditLog` row (1 từ `send_po()`, 1 từ `retry_po_email()`)
  với `description` khác nhau — audit log lần retry nêu rõ đây là gửi lại, không trùng nội dung
  log gốc.
- `TC-PUR-FND-07-006` **[mới, review lần 3]**: user chỉ có quyền `read` trên module `po` → `POST`
  tới URL `retry_po_email` trả `403`, `email_status` của PO không đổi.
- `TC-PUR-FND-04-001`: `submit_purchase_request()` tạo đúng 1 `AuditLog` row qua `create_approval()`.
- `TC-PUR-FND-04-002` **[thêm v2]**: `approve_po()` tạo đúng 1 `AuditLog` (`Action.APPROVE`) với
  `actor`/`description` khớp `po.po_no`.
- `TC-PUR-FND-04-003` **[thêm v2]**: `close_po()` đóng sớm (từ `SENT`, có `reason`) tạo đúng 1
  `AuditLog` chứa `reason` trong `description`.
- `TC-PUR-FND-04-004` **[thêm v2]**: `decide_purchase_request()` nhánh approve **và** nhánh reject
  (2 test con) mỗi lần tạo đúng 1 `AuditLog` mới qua `decide_approval()`.
- `TC-PUR-FND-04-005` **[thêm v2]**: `send_po()` — mỗi 1 trong 3 kết quả runtime (`SENT`/`FAILED`/
  `SKIPPED_NO_EMAIL`) tạo đúng 1 `AuditLog` với `description` chứa đúng từ khoá tương ứng, không
  lẫn nhánh (liên kết trực tiếp với `TC-PUR-FND-02-001..004`, thêm assertion `AuditLog` mà các test
  đó chưa chắc đã kiểm).
- `TC-PUR-FND-09-001` **[thêm v2]**: chạy data migration trên fixture có PO ở cả 2 nhóm trạng thái
  (`{SENT,PARTIAL_RECEIVED,RECEIVED,CLOSED}` và `{DRAFT,APPROVED}`) → đúng nhóm đầu thành
  `UNKNOWN_LEGACY`, nhóm sau giữ `NOT_ATTEMPTED`, không lẫn nhóm (khớp mục 9 acceptance #5).

## 12. Backlog kỹ thuật (Stage Foundation)

| Ticket | Mã | Việc cần làm | File chính |
|---|---|---|---|
| T1 | `PUR-FND-01` | Rút `received_qty_by_product(po)` dùng chung, gọi lại từ `sync_po_status` và `po_detail` | `purchasing/services.py`, `purchasing/views.py` |
| T2 | `PUR-FND-05` | Thêm `PurchaseOrder.grand_total` (property, `Decimal`, mục 2), hiển thị ở `po_detail.html` | `purchasing/models.py`, `purchasing/templates/purchasing/po_detail.html` |
| T3 | `PUR-FND-02` | Migration `sent_at`/`email_status` (5 giá trị + data migration 2 nhóm, mục 9); viết lại `_send_po_email()` phân biệt `SENT`/`FAILED`/`SKIPPED_NO_EMAIL` theo điều kiện "không exception **và** `send_mail()` trả về > 0" (mục 4); `send_po()` lưu `email_status` bằng `save(update_fields=[...])` riêng, không chỉ set trên instance (mục 3 "Transaction/save behavior"); sửa audit log + flash message ở `po_send` (3 nhánh riêng biệt, mục 5/7) | `purchasing/models.py` (schema + data migration), `purchasing/services.py`, `purchasing/views.py` |
| T4 | `PUR-FND-03` | Set `sent_at` trong `send_po()`; đổi `supplier_lead_time_stats()` dùng `timezone.localtime(po.sent_at).date()` trước khi trừ `received_at` (mục 4 — bắt buộc, nếu không sẽ raise `TypeError` do lệch `DateField`/`DateTimeField`), loại PO thiếu `sent_at` | `purchasing/services.py` |
| T5 | `PUR-FND-04` | Viết 5 test case `TC-PUR-FND-04-001..005` xác nhận (mục 11) — không sửa code | `purchasing/tests.py` |
| T6 | — | Backfill/regression: chạy lại 139 test PUR hiện có sau T1-T4+T7+T8, thêm toàn bộ test case mục 11 (nay 31 case, không còn 24) | `purchasing/tests.py` |
| T7 **[mới, review v3]** | `PUR-FND-06` | `find_duplicate_po_products()` (báo cáo, tầng ứng dụng); `UniqueConstraint(['purchase_order','product'])` lên `PurchaseOrderItem.Meta.constraints`; migration guard viết độc lập bằng historical model qua `apps.get_model()`, **không** gọi lại service hiện tại, luôn giữ `RunPython` guard bất kể kết quả tiền kiểm (mục 9, sửa lần 3); test `TC-PUR-FND-06-001..004` | `purchasing/models.py`, `purchasing/services.py`, `purchasing/migrations/` |
| T8 **[mới, review lần 3, sửa review lần 4]** | `PUR-FND-07` | `retry_po_email()` (service, dùng lại `_send_po_email()` của T3, thêm check `po.supplier.contact_email` — mục 3/4 sửa lần 4) + view/URL + khu vực "Gửi lại email" (nút bật/vô hiệu hoá theo `contact_email`) trên `po_detail.html` (mục 3/4/5); test `TC-PUR-FND-07-001..007` (007 mới, review lần 4) | `purchasing/services.py`, `purchasing/views.py`, `purchasing/urls.py`, `purchasing/templates/purchasing/po_detail.html` |

**Phụ thuộc giữa T1 và T7 (quan trọng, review v3)** — T1 tự nó **không sửa được** Critical #1: rút
`received_qty_by_product()` dùng chung chỉ dọn trùng lặp code, nhưng phép so sánh per-line
(`received_by_product.get(item.product_id) >= item.qty_ordered`) vẫn sai với PO có 2 dòng cùng
Product **cho tới khi** `UniqueConstraint` của T7 tồn tại (khi đó mỗi Product chỉ còn đúng 1 dòng,
phép so sánh per-line mới tương đương phép so sánh per-product). **T1 và T7 phải coi là 1 đơn vị
merge — không merge/đóng T1 riêng nếu T7 chưa xong**, dù có thể viết code song song. T2 độc lập
hoàn toàn, có thể **bắt đầu ngay**. **[giữ nguyên quyết định v2]** T3/T4 dùng thiết kế đã sửa
trong mục 2/4/9 file này. T3 trước T4 (cùng chạm `send_po()`, tránh 2 lần sửa 1 hàm trong 2 commit
chồng nhau). **T8 phụ thuộc T3** (dùng lại `_send_po_email()`/5 giá trị `email_status` mà T3 tạo
ra) — bắt đầu sau khi T3 xong, độc lập với T1/T7 (không đụng `PurchaseOrderItem`). T5/T6 làm cuối
cùng, sau khi T1-T4+T7+T8 xong.

Đây vẫn là mô tả backlog — **chưa viết code**. Ticket T1-T4, T7-T8 sẽ là các bước TDD (test FAIL
trước) trong phiên triển khai kế tiếp, nay FSD đã Approved (xem header).

## 13. Lịch sử review

| Bản | Ngày | Thay đổi |
|---|---|---|
| v1 | 02/08/2026 | Bản đầu, 4 điểm xác minh (`PUR-FND-01/02/03/05`), `PUR-FND-04` xác nhận sẵn có. |
| v2 | 02/08/2026 | Sửa theo review: (1) đính chính quyền `send_po` từ `approve` → `update` đúng code (mục 1); (2) mở rộng `email_status` từ 3 lên 5 giá trị — thêm `NOT_ATTEMPTED`/`SKIPPED_NO_EMAIL` (đổi tên từ `NOT_CONFIGURED`)/`UNKNOWN_LEGACY`, phân biệt rõ "chưa gửi" / "gửi nhưng NCC không có email" / "dữ liệu cũ không rõ kết quả" (mục 2, 9); (3) sửa công thức lead-time bị lệch kiểu `DateField`/`DateTimeField`, thêm `timezone.localtime(po.sent_at).date()` (mục 4); (4) sửa điều kiện `email_status=SENT` — không chỉ dựa vào không có exception, phải kèm `send_mail()` trả về > 0 (mục 4); (5, không chặn) làm rõ định nghĩa `sent_at` = thời điểm hệ thống thử gửi, không phải NCC xác nhận (mục 3); (6, không chặn) mở rộng test `PUR-FND-04` từ 1 lên 5 case, bao phủ `approve_po`/`close_po`/`decide_purchase_request` (mục 11); (7, không chặn) thêm dòng "Người duyệt/Ngày duyệt" vào header, để trống chờ điền khi user thật sự approve. |
| v3 | 02/08/2026 | Sửa theo review lần 2, 2 Critical + 5 High: (1) **Critical** — thêm rule `PUR-FND-06`: `PurchaseOrderItem` thiếu unique `(purchase_order, product)` làm sai đối chiếu `PUR-FND-01` khi PO có 2 dòng cùng Product; thêm `UniqueConstraint` + formset validation (built-in Django, không cần code tay) + migration guard 2 bước chặn nếu còn dữ liệu vi phạm (mục 2, 4, 5, 8, 9); ghi rõ T1 và T7 phải merge cùng nhau, T1 riêng không đủ sửa bug (mục 12). Critical #2 (multi-currency đặt sai Stage, phụ thuộc Budget Stage 3/RFQ Stage 4) **không thuộc phạm vi file này** — đã xử lý ở `00_business_decisions.md` mục 8 và `PUR_EXPANSION_MASTER_PLAN.md`, không đụng Foundation. (2) **High** — sửa 2 chỗ ghi nhầm "Stage 4/Epic E" thành đúng **Stage 5** (mục 2, 3), đối chiếu lại `PUR_EXPANSION_MASTER_PLAN.md`. (3) **High** — bỏ attachment ra khỏi phạm vi Foundation, ghi rõ thuộc Stage 4 (mục 0) — attachment vốn không có ticket nào trong file này, chỉ cần nêu tường minh để tránh mâu thuẫn 3 nơi tài liệu đã phát hiện. (4) **High** — đặc tả rõ transaction/save behavior của `email_status`: bắt buộc `save(update_fields=['email_status'])` riêng, không chỉ set trên instance; xác nhận email lỗi không rollback transition `SENT` vì `try/except` nằm trong `_send_po_email()` (mục 3). (5) **High** — thêm test permission (403 khi chỉ có quyền `read`, thành công khi có `update`) và test flash message đúng nội dung theo từng nhánh, test hiển thị `UNKNOWN_LEGACY` trên `po_detail` (mục 11, `TC-PUR-FND-02-005..008`, `TC-PUR-FND-05-002`). (6) **High** — đặc tả `grand_total` bằng `Decimal("0.00")` làm giá trị khởi tạo `sum()`, ghi rõ ràng buộc chỉ dùng ở trang đã prefetch `items` để tránh N+1 (mục 2). Tổng số test case: 15 → 24. |
| v4 | 02/08/2026 | Sửa theo review lần 3, 3 điểm bắt buộc + 5 lỗi tài liệu nhỏ: (1) **Bắt buộc** — migration guard `PUR-FND-06` viết lại độc lập với model/service hiện tại: dùng historical model qua `apps.get_model()` thay vì `import`/gọi lại `purchasing.services.find_duplicate_po_products()`, dùng `RuntimeError` thay vì `migrations.exceptions...` (không tồn tại), bỏ hẳn nhánh "0 vi phạm thì không cần `RunPython` guard" — guard luôn có mặt trước `AddConstraint` (mục 9). (2) **Bắt buộc** — thêm rule `PUR-FND-07`: action `retry_po_email()` cho PO `status=SENT` với `email_status in (FAILED, SKIPPED_NO_EMAIL)`, dùng lại `_send_po_email()` của T3, không chạy lại transition, `log_action` riêng (mục 1, 3, 4, 5, 7, 8, 10, 11, 12 — ticket T8). (3) **Bắt buộc** — ghi rõ giới hạn chấp nhận được của email side-effect chạy trong `@transaction.atomic` (email đã gửi nhưng save/log_action sau đó lỗi thì không thu hồi được) — chấp nhận rủi ro thay vì thêm hạ tầng `transaction.on_commit()` (mục 3). (4) FSD chuyển **Approved**, người duyệt Trương Hoàng, ngày 02/08/2026 (header). (5, không chặn) sửa "4 điểm" → "5 điểm" (mục 0, khớp 5 dòng bảng `PUR-FND-01/02/03/05/06`); (6, không chặn) sửa "Nguồn: PUR-FND-01..05" → "01..07" (header); (7, không chặn) thêm `max_length=20` cho `email_status` (mục 2); (8, không chặn) thêm `logger.exception()` khi `send_mail` raise, không lưu traceback vào `AuditLog` (mục 4). 5 quyết định nghiệp vụ Stage 2-4 review lần 3 nêu ra (ngưỡng RFQ theo đơn vị nào, 3 báo giá theo RFQ hay từng dòng, budget tolerance, tỷ giá thiếu, pilot coverage) **hoãn lại có chủ đích**, không thuộc phạm vi file này — chốt riêng ở `00_business_decisions.md` trước khi brainstorm Stage 2. Tổng số test case: 24 → 30. |
| v5 | 02/08/2026 | Sửa theo review lần 4, 2 điểm bắt buộc + 1 điểm câu chữ: (1) **Bắt buộc** — "Người duyệt" đổi từ tên hiển thị `Trương Hoàng` (sai chính tả, không ổn định cho đối chiếu Git/audit) sang username `luckyhoang1988` (kèm tên `Trường Hoàng` trong ngoặc, header). (2) **Bắt buộc** — `retry_po_email()` (`PUR-FND-07`) thêm điều kiện bắt buộc thứ 2: `po.supplier.contact_email` phải khác rỗng tại thời điểm gọi, chặn bằng `ValidationError` (không gọi `send_mail`, không tạo `AuditLog`) nếu không — trước đó (v4) cho phép gọi khi `email_status=SKIPPED_NO_EMAIL` dù NCC vẫn chưa có email, tạo nút bấm vô nghĩa và `AuditLog` rác; nay đây là con đường hợp lệ duy nhất để thoát `SKIPPED_NO_EMAIL` sau khi NCC được bổ sung email (mục 3, 4, 5, 8, 10). Sửa `TC-PUR-FND-07-002` (đổi kỳ vọng sang `ValidationError`) + thêm `TC-PUR-FND-07-007` (ca retry thành công sau khi bổ sung email) (mục 11). (3, không chặn) sửa câu chữ mô tả `retry_po_email()` — không còn mô tả là "đường khắc phục" cho tình huống email-gửi-nhưng-transaction-rollback (sai vì lúc đó `po.status` đã về `APPROVED`, `retry_po_email()` chỉ chạy được với `status=SENT`, không gọi được) — đổi thành ghi nhận đây là giới hạn đã biết, người xử lý phải tự xác nhận với NCC trước khi gửi lại thủ công, Foundation chưa bảo đảm exactly-once delivery (mục 3). Tổng số test case: 30 → 31. |
| v6 | 02/08/2026 | Sửa theo review lần 4 (bổ sung), 1 điểm câu chữ, không chặn: đính chính số ký tự của `SKIPPED_NO_EMAIL` — v3 ghi nhầm 17 ký tự, đếm lại đúng là **16 ký tự** (mục 2); `max_length=20` không đổi vì vẫn đủ dư. Không có thay đổi test case. |
