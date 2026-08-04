# QC Expansion — 04. Implementation Plan Wave 2: Criteria Gate Nhẹ

> Nguồn: [`03_criteria_gate_fsd.md`](03_criteria_gate_fsd.md)
> Quy ước: checkbox Task giữ `- [ ]` sau khi xong (như `docs/pur/03_*`, `docs/qc/02_*`); xác nhận
> bằng `git log` / test.
> TDD: mỗi task — viết test FAIL → implement tối thiểu → PASS.

## Ràng buộc chung

- Gate ở service layer là nguồn chân lý (FSD §2) — đặt gate **sau** các validate tiền-lock hiện có
  (location non-MAIN/inactive) để giữ đúng nhánh lỗi gốc mà test cũ đang assert, **trước**
  `_lock_pending_inspection()` (fail sớm, không giữ lock oan).
- **Ripple bắt buộc phải sửa cùng Phase 1, không tách task riêng**: thêm gate sẽ làm vỡ mọi test/
  script hiện có gọi `qc_pass`/`qc_fail`/`qc_partial_pass` mà chưa từng tạo `QcInspectionItem` —
  gồm `quality/tests.py` (~20 call site) và
  `accounts/management/commands/seed_demo_data.py`. Coi Phase 1 chỉ "xong" khi
  `manage.py test quality receiving inventory --keepdb` xanh lại toàn bộ, không chỉ test mới.
- UI tiếng Việt; không có field/model mới ở Wave 2 (chỉ logic + JS) nên không cần migration.
- Không Celery.

## Bản đồ file

| File | Việc |
|---|---|
| `quality/services.py` | Gate `≥1 QcInspectionItem` trong `qc_pass`/`qc_fail`/`qc_partial_pass` |
| `quality/tests.py` | Test gate mới (`QcCriteriaGateTest`) + helper `_add_criteria_item` trên `QcServiceTestBase` + sửa ripple ở các class gọi thẳng service/qua view |
| `accounts/management/commands/seed_demo_data.py` | Tạo 1 `QcInspectionItem`/inspection trước khi gọi `qc_pass`/`qc_fail`/`qc_partial_pass` |
| `quality/views.py` | `qc_result`: thêm context `has_fail_item`/`has_pass_item`/`has_any_item`/`criteria_by_category` |
| `quality/templates/quality/qc_result.html` | Banner cảnh báo cạnh nút quyết định, disable nút khi 0 item, data island JSON + `<script>` prefill |
| `quality/forms.py` | Widget `GrnItemSelectWithCategory`, gán cho `QcInspectionItemForm.Meta.widgets['grn_item']` |
| `BACKLOG.md`, `CLAUDE.md`, `qc_plan.md`, skill | Doc sync cuối |

---

## Phase 1 — Gate service ≥1 criteria (+ sửa ripple)

### Task 1.1 — Gate `qc_pass`/`qc_fail`/`qc_partial_pass`

**File:** `quality/services.py`, `quality/tests.py`

- [ ] **Bước 1 — RED**: thêm class mới cuối `quality/tests.py` (sau `QcPartialPassTransactionTest`,
  trước `WarehouseHandoffTest` — hoặc cuối file, không quan trọng vị trí miễn cùng module):
```python
class QcCriteriaGateTest(QcServiceTestBase):
    """Wave 2 — gate ≥1 dòng criteria trước khi PASS/FAIL/PARTIAL. TC-QC-CRIT-001..004."""

    def test_TC_QC_CRIT_001_qc_pass_rejects_when_no_items(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_pass(inspection, actor=self.qc_user, location=self.location)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.PENDING_QC)
        self.grn.refresh_from_db()
        self.assertEqual(self.grn.status, Grn.Status.QC_IN_PROGRESS)

    def test_TC_QC_CRIT_002_qc_fail_rejects_when_no_items(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_fail(inspection, actor=self.qc_user, reason='x')
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.PENDING_QC)

    def test_TC_QC_CRIT_003_qc_partial_pass_rejects_when_no_items(self):
        inspection = self._start_qc()
        with self.assertRaises(ValidationError):
            qc_partial_pass(
                inspection, {self.grn_item.pk: 5}, actor=self.qc_user, location=self.location)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.PENDING_QC)

    def test_TC_QC_CRIT_004_qc_pass_succeeds_with_one_item(self):
        inspection = self._start_qc()
        self._add_criteria_item(inspection)
        qc_pass(inspection, actor=self.qc_user, location=self.location)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, QcInspection.Result.PASS)
```
- [ ] **Bước 2** — thêm helper vào `QcServiceTestBase` (`quality/tests.py`, ngay dưới `_start_qc`):
```python
    def _add_criteria_item(self, inspection, result=QcInspectionItem.Result.PASS, grn_item=None):
        return QcInspectionItem.objects.create(
            inspection=inspection, grn_item=grn_item or self.grn_item,
            criteria_name='Ngoại hình', result=result,
        )
```
  (`QcInspectionItem` đã có sẵn trong import `from .models import QcCriteria, QcInspection,
  QcInspectionItem, validate_image_upload` — không cần import thêm.)
- [ ] **Bước 3 — chạy `manage.py test quality.tests.QcCriteriaGateTest --keepdb`**, kỳ vọng FAIL (chưa
  có gate — `qc_pass`/`qc_fail`/`qc_partial_pass` chạy thành công dù 0 item, `assertRaises` không bắt
  được gì).
- [ ] **Bước 4 — GREEN**: sửa `quality/services.py` — thêm gate ở 3 hàm, đúng vị trí (sau validate
  location hiện có, trước `_lock_pending_inspection`):

  `qc_pass` (sau dòng `if not location.warehouse.is_active: raise ValidationError(...)`, trước
  `grn, inspection = _lock_pending_inspection(inspection)`):
```python
    if not inspection.items.exists():
        raise ValidationError(
            'Phải nhập ít nhất 1 dòng kết quả kiểm tra tiêu chuẩn trước khi ghi nhận kết quả QC.'
        )
```
  `qc_fail` (dòng đầu tiên trong thân hàm, trước `grn, inspection = _lock_pending_inspection(inspection)`
  — hàm này hiện chưa có validate tiền-lock nào khác):
```python
    if not inspection.items.exists():
        raise ValidationError(
            'Phải nhập ít nhất 1 dòng kết quả kiểm tra tiêu chuẩn trước khi ghi nhận kết quả QC.'
        )
```
  `qc_partial_pass` (cùng vị trí tương đối như `qc_pass` — sau 2 dòng validate location, trước
  `grn, inspection = _lock_pending_inspection(inspection)`): dùng đúng snippet như trên.

- [ ] **Bước 5 — chạy lại `QcCriteriaGateTest`**, xác nhận PASS.
- [ ] **Bước 6 — sửa ripple**: chạy `manage.py test quality --keepdb`. Với mỗi test FAIL vì
  `ValidationError: Phải nhập ít nhất 1 dòng...`, mở đúng dòng gọi `qc_pass`/`qc_fail`/
  `qc_partial_pass` đang fail và thêm `self._add_criteria_item(inspection)` (dùng đúng biến chứa
  `QcInspection` đang có trong scope test đó — có thể tên là `inspection` hoặc `self.inspection`)
  **ngay trước** dòng gọi. Lặp lại tới khi suite xanh. Các class dự kiến cần sửa (đã xác nhận qua
  đọc code): `OverdueInspectionsTest`, `QcPassTransactionTest`, `QcFailTransactionTest`,
  `QcPartialPassTransactionTest`, `WarehouseHandoffTest`, `GetStagingBatchTest` (nếu có gọi qua
  `_start_qc`+decision), `QcOverrideViewTest.setUp` (dòng `qc_pass(self.inspection, ...)`).
  **Ngoại lệ không cần sửa** (đã tự kiểm tra, giữ nguyên nhánh lỗi gốc vì check đó fire trước gate
  hoặc không phụ thuộc gate):
  - `QcPassTransactionTest.test_TC_QC_PASS_002_001_rejects_non_main_warehouse_location` — location
    check fire trước gate.
  - `QcPartialPassTransactionTest.test_TC_QC_PARTIAL_004_001_rejects_non_main_warehouse_location` —
    tương tự.
  - Lệnh gọi thứ 2 trong các test "already resolved" (`QcPassTransactionTest.
    test_TC_QC_PASS_001_002...`, `QcFailTransactionTest` tương ứng) — inspection đã có item từ lệnh
    gọi thứ nhất, gate qua trót lọt, đúng nhánh "đã resolved" bên trong `_lock_pending_inspection` vẫn
    fire như cũ.
  Riêng `QcPartialPassTransactionTest.test_TC_QC_PARTIAL_002/003/005` (missing item result / qty sai /
  all-zero — dùng `location=self.location` hợp lệ nên KHÔNG có check nào fire trước gate): **vẫn nên
  thêm** `self._add_criteria_item(inspection)` dù `assertRaises(ValidationError)` không kiểm tra
  message cụ thể — để test tiếp tục đúng đúng nhánh lỗi mà tên test đang mô tả, tránh test "trôi dạt"
  sang test nhầm nhánh.

  `QcResultViewTest` (view, không phải service test) cũng nằm trong ripple — sửa `setUp()` ngay tại
  Phase này (bắt buộc để suite xanh theo Ràng buộc chung ở trên, **không** đợi tới Phase 2):
```python
    def setUp(self):
        super().setUp()
        self.manager = User.objects.create_user(
            username='qlk', password='qlk-pass-123', role=User.Role.MANAGER)
        self.inspection = self._start_qc()
        self._add_criteria_item(self.inspection)
        self.client.force_login(self.qc_user)
```
  (thay cho `setUp()` hiện tại của `QcResultViewTest` — thêm 2 dòng `self.inspection = ...`/
  `self._add_criteria_item(...)`, đổi `self._start_qc()` cũ thành gán vào `self.inspection` vì Phase 2
  Task 2.1 sẽ cần tham chiếu `self.inspection` trực tiếp.)
- [ ] **Bước 7 — sửa `accounts/management/commands/seed_demo_data.py`**: trong vòng lặp GRN demo
  (quanh dòng `inspection = start_qc(grn, qc_user, actor=qc_user)`), thêm ngay sau đó:
```python
            inspection = start_qc(grn, qc_user, actor=qc_user)
            QcInspectionItem.objects.create(
                inspection=inspection, grn_item=grn_item, criteria_name='Ngoại hình',
                result=QcInspectionItem.Result.FAIL if group == 'FAIL' else QcInspectionItem.Result.PASS,
            )
```
  (đặt trước nhánh `if group == 'FAIL': ...`). `QcInspectionItem` đã import sẵn ở đầu file (dòng 46).
- [ ] **Bước 8** — chạy `manage.py test quality receiving inventory --keepdb`, xác nhận toàn bộ PASS.
  Nếu có `manage.py seed_demo_data` chạy được trong môi trường dev, chạy thử 1 lần xác nhận không
  crash (không bắt buộc nếu không có DB dev sẵn sàng — ghi rõ trong PR/commit message nếu bỏ qua
  bước này).
- [ ] **Bước 9 — Commit**
```bash
git add quality/services.py quality/tests.py accounts/management/commands/seed_demo_data.py
git commit -m "feat(qc): Wave 2 - gate >=1 dong criteria truoc khi PASS/FAIL/PARTIAL"
```

---

## Phase 2 — Cảnh báo mismatch + disable nút khi 0 item

### Task 2.1 — Context view

**File:** `quality/views.py`, `quality/tests.py`

- [ ] **Bước 1 — RED**: `QcResultViewTest.setUp()` đã sửa xong ở Phase 1 (Task 1.1 Bước 6) — đã có
  `self.inspection` với 1 `QcInspectionItem` kết quả PASS sẵn. Thêm test mới vào class này:
```python
    def test_TC_QC_CRIT_005_view_disables_actions_when_no_items(self):
        empty_grn = Grn.objects.create(po=self.po, supplier=self.supplier, created_by=self.purchasing_user)
        empty_item = GrnItem.objects.create(
            grn=empty_grn, product=self.product, qty_ordered=5, qty_received=5,
            unit_price=Decimal('15000.00'),
        )
        start_qc(empty_grn, self.qc_user, actor=self.qc_user)
        response = self.client.get(reverse('quality:qc_result', args=[empty_grn.pk]))
        self.assertContains(response, 'disabled', count=3)
        self.assertContains(response, 'Chưa có dòng kết quả tiêu chuẩn nào')

    def test_TC_QC_CRIT_006_fail_item_warns_near_pass_partial_buttons(self):
        self._add_criteria_item(self.inspection, result=QcInspectionItem.Result.FAIL)
        response = self.client.get(self._url())
        self.assertContains(response, 'Đã có dòng tiêu chuẩn Không đạt', count=2)

    def test_TC_QC_CRIT_007_pass_item_warns_near_fail_button(self):
        response = self.client.get(self._url())
        self.assertContains(response, 'Đã có dòng tiêu chuẩn Đạt', count=1)

    def test_TC_QC_CRIT_008_no_mismatch_when_only_pass_items(self):
        response = self.client.get(self._url())
        self.assertNotContains(response, 'Đã có dòng tiêu chuẩn Không đạt')
```
  (`start_qc` cần import thêm vào đầu `quality/tests.py` nếu chưa có tên trần — kiểm tra dòng
  `from .services import (` hiện tại, `start_qc` nhiều khả năng đã có sẵn vì `_start_qc()` gọi nó.)
- [ ] **Bước 2** — chạy 4 test trên, xác nhận FAIL (chưa có context/HTML mới).
- [ ] **Bước 3 — GREEN**: sửa `quality/views.py::qc_result` — thêm import `QcInspectionItem` vào dòng
  `from .models import QcCriteria, QcInspection` (→ `from .models import QcCriteria, QcInspection,
  QcInspectionItem`), rồi trước `return render(...)` cuối hàm:
```python
    has_any_item = inspection.items.exists()
    has_fail_item = inspection.items.filter(result=QcInspectionItem.Result.FAIL).exists()
    has_pass_item = inspection.items.filter(result=QcInspectionItem.Result.PASS).exists()
```
  và thêm 3 key vào dict context của `render(...)`:
```python
        'has_any_item': has_any_item, 'has_fail_item': has_fail_item, 'has_pass_item': has_pass_item,
```
- [ ] **Bước 4 — sửa template** `quality/templates/quality/qc_result.html`, thay khối nút (dòng 76-81
  hiện tại):
```html
          <div class="d-flex gap-2 mt-4 flex-wrap align-items-start">
            <div>
              <button type="submit" name="action" value="pass" class="btn btn-success"{% if not has_any_item %} disabled{% endif %}>QC Đạt</button>
              {% if has_fail_item %}<div class="text-warning small mt-1">⚠ Đã có dòng tiêu chuẩn Không đạt.</div>{% endif %}
            </div>
            <div>
              <button type="submit" name="action" value="partial" class="btn btn-warning"{% if not has_any_item %} disabled{% endif %}>QC Đạt một phần</button>
              {% if has_fail_item %}<div class="text-warning small mt-1">⚠ Đã có dòng tiêu chuẩn Không đạt.</div>{% endif %}
            </div>
            <div>
              <button type="submit" name="action" value="fail" class="btn btn-danger"{% if not has_any_item %} disabled{% endif %}>QC Không đạt</button>
              {% if has_pass_item %}<div class="text-warning small mt-1">⚠ Đã có dòng tiêu chuẩn Đạt.</div>{% endif %}
            </div>
            <a class="btn btn-link" href="{% url 'receiving:grn_detail' grn.pk %}">Huỷ</a>
          </div>
          {% if not has_any_item %}
            <div class="text-danger small mt-2">Chưa có dòng kết quả tiêu chuẩn nào — hãy lưu ít nhất 1 dòng ở bảng "Kết quả PASS/FAIL từng tiêu chuẩn" bên dưới trước khi ghi nhận kết quả QC tổng.</div>
          {% endif %}
```
- [ ] **Bước 5** — chạy lại 4 test Task 2.1, xác nhận PASS. Chạy `manage.py test quality --keepdb`
  toàn bộ, xác nhận không có regression (test cũ dùng `_payload(action='pass'/'fail'/'partial')` vẫn
  còn field `disabled` trên nút không ảnh hưởng submit vì `self.client.post` không mô phỏng HTML
  disabled — chỉ ảnh hưởng test kiểm `assertContains('disabled', ...)` mới).
- [ ] **Bước 6 — Commit**
```bash
git add quality/views.py quality/templates/quality/qc_result.html quality/tests.py
git commit -m "feat(qc): Wave 2 - canh bao mismatch + disable nut khi chua co criteria"
```

---

## Phase 3 — Prefill/gợi ý criteria theo category

### Task 3.1 — Widget `GrnItemSelectWithCategory`

**File:** `quality/forms.py`

- [ ] **Bước 1 — RED**: thêm test vào `quality/tests.py` (class mới `QcInspectionItemFormWidgetTest`
  hoặc thêm vào `QcResultViewTest` — dùng class riêng cho gọn):
```python
class QcInspectionItemFormWidgetTest(QcServiceTestBase):
    """TC-QC-CRIT-009 (nửa đầu — data-category). Nửa sau (context criteria_by_category)
    ở Task 3.2, `test_TC_QC_CRIT_009_02_...` — FSD gộp 2 test này chung 1 dòng TC-009 (AC 06,07),
    tách thành 2 method riêng cho rõ ràng khi implement, giữ chung tiền tố ID."""

    def test_TC_QC_CRIT_009_01_grn_item_option_has_data_category(self):
        self.product.category = 'Bột mì'
        self.product.save(update_fields=['category'])
        form = QcInspectionItemForm(grn=self.grn)
        rendered = str(form['grn_item'])
        self.assertIn('data-category="Bột mì"', rendered)
```
  (cần import `QcInspectionItemForm` vào đầu `quality/tests.py`: sửa dòng `from .forms import
  QcResultForm` → `from .forms import QcInspectionItemForm, QcResultForm`.)
- [ ] **Bước 2** — chạy test, xác nhận FAIL (`data-category` chưa tồn tại trong HTML).
- [ ] **Bước 3 — GREEN**: thêm vào `quality/forms.py`, trước `class QcInspectionItemForm`:
```python
class GrnItemSelectWithCategory(forms.Select):
    """Gắn ``data-category`` lên mỗi ``<option>`` để JS ở ``qc_result.html`` gợi ý tên tiêu chuẩn
    theo category — mirror ``purchasing.forms.ProductSelectWithCategory``. ``value`` là
    ``ModelChoiceIteratorValue`` (Django ≥3.1), đã bọc sẵn ``.instance`` (``GrnItem``), lấy category
    qua ``instance.product.category``.
    """

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, 'instance', None)
        if instance is not None:
            option['attrs']['data-category'] = instance.product.category
        return option
```
  rồi thêm `widgets` vào `QcInspectionItemForm.Meta`:
```python
    class Meta:
        model = QcInspectionItem
        fields = ['grn_item', 'criteria_name', 'expected_value', 'actual_value', 'result', 'notes', 'image']
        widgets = {'grn_item': GrnItemSelectWithCategory}
```
- [ ] **Bước 4** — chạy lại test, xác nhận PASS.
- [ ] **Bước 5 — Commit**
```bash
git add quality/forms.py quality/tests.py
git commit -m "feat(qc): Wave 2 - widget GrnItemSelectWithCategory cho grn_item select"
```

### Task 3.2 — Context `criteria_by_category` + data island

**File:** `quality/views.py`, `quality/templates/quality/qc_result.html`, `quality/tests.py`

- [ ] **Bước 1 — RED**: thêm test:
```python
    def test_TC_QC_CRIT_009_02_context_criteria_by_category_only_active(self):
        QcCriteria.objects.create(category='Bột mì', name='Trọng lượng', pass_rule='1000g ± 10g')
        QcCriteria.objects.create(category='Bột mì', name='Cũ', is_active=False)
        response = self.client.get(self._url())
        self.assertIn('Trọng lượng', response.context['criteria_by_category']['Bột mì'][0]['name'])
        self.assertEqual(len(response.context['criteria_by_category']['Bột mì']), 1)
```
  (thêm vào `QcResultViewTest`.)
- [ ] **Bước 2** — chạy test, xác nhận FAIL (`KeyError: 'criteria_by_category'`).
- [ ] **Bước 3 — GREEN**: trong `quality/views.py::qc_result`, trước `return render(...)`:
```python
    criteria_by_category = {}
    for c in QcCriteria.objects.filter(is_active=True).order_by('category', 'name'):
        criteria_by_category.setdefault(c.category, []).append(
            {'name': c.name, 'pass_rule': c.pass_rule, 'fail_rule': c.fail_rule})
```
  thêm `'criteria_by_category': criteria_by_category,` vào dict context.
- [ ] **Bước 4** — sửa template, thêm ngay trước `{% endblock %}` cuối `qc_result.html`:
```html
{{ criteria_by_category|json_script:"qc-criteria-by-category" }}
```
- [ ] **Bước 5** — chạy lại test, xác nhận PASS.
- [ ] **Bước 6 — Commit**
```bash
git add quality/views.py quality/templates/quality/qc_result.html quality/tests.py
git commit -m "feat(qc): Wave 2 - context criteria_by_category + data island JSON"
```

### Task 3.3 — JS datalist + autofill `expected_value`

**File:** `quality/templates/quality/qc_result.html`

Không có RED/GREEN Python cho JS thuần (dự án chưa có harness test JS — xem AC-QC-CRIT-06 trong FSD:
"kiểm tra qua HTML render, không cần chạy JS thật"). Task này chỉ có 1 bước code, verify thủ công qua
trình duyệt (chạy `manage.py runserver`, mở 1 GRN đang `QC_IN_PROGRESS`, đổi `grn_item` trong dòng
criteria, xác nhận datalist gợi ý xuất hiện và `expected_value` tự điền khi chọn đúng tên).

- [ ] **Bước 1** — thêm `<script>` vào cuối `qc_result.html`, ngay sau dòng `json_script` ở Task 3.2:
```html
<script>
(function () {
  var dataEl = document.getElementById('qc-criteria-by-category');
  var criteriaByCategory = dataEl ? JSON.parse(dataEl.textContent) : {};

  function buildDatalist(row, category) {
    var nameInput = row.querySelector('input[id$="-criteria_name"]');
    if (!nameInput) return;
    var listId = nameInput.id + '-list';
    var existing = document.getElementById(listId);
    if (existing) existing.remove();
    var datalist = document.createElement('datalist');
    datalist.id = listId;
    (criteriaByCategory[category] || []).forEach(function (c) {
      var opt = document.createElement('option');
      opt.value = c.name;
      datalist.appendChild(opt);
    });
    row.appendChild(datalist);
    nameInput.setAttribute('list', listId);
    nameInput.dataset.category = category;
  }

  document.addEventListener('change', function (event) {
    var select = event.target.closest('select[id$="-grn_item"]');
    if (select) {
      var opt = select.options[select.selectedIndex];
      var category = opt.getAttribute('data-category');
      var row = select.closest('tr');
      if (row && category) buildDatalist(row, category);
      return;
    }
    var nameInput = event.target.closest('input[id$="-criteria_name"]');
    if (nameInput && nameInput.dataset.category) {
      var row2 = nameInput.closest('tr');
      var expectedInput = row2 ? row2.querySelector('input[id$="-expected_value"]') : null;
      if (expectedInput && !expectedInput.value) {
        var match = (criteriaByCategory[nameInput.dataset.category] || []).find(function (c) {
          return c.name === nameInput.value;
        });
        if (match && match.pass_rule) expectedInput.value = match.pass_rule;
      }
    }
  });
})();
</script>
```
- [ ] **Bước 2 — verify thủ công** (không có test tự động cho JS trong dự án này): `manage.py
  runserver`, vào 1 GRN `QC_IN_PROGRESS` có sản phẩm với `category` đã set, mở trang `qc_result`, đổi
  `grn_item` ở 1 dòng criteria → xác nhận gõ vào ô "Tiêu chuẩn" hiện gợi ý đúng tên criteria của
  category đó; chọn 1 gợi ý khớp tên có `pass_rule` → xác nhận ô "Giá trị mong đợi" tự điền, và **không**
  bị ghi đè nếu đã gõ tay trước.
- [ ] **Bước 3 — Commit**
```bash
git add quality/templates/quality/qc_result.html
git commit -m "feat(qc): Wave 2 - JS datalist goi y criteria + autofill expected_value"
```

---

## Phase 4 — Doc sync

### Task 4.1

- [ ] Tick `qc_plan.md` §Todos dòng Wave 2 (bỏ ghi chú "triển khai chưa làm")
- [ ] `BACKLOG.md` — kiểm tra mục 2c/2b có dòng nào liên quan criteria gate cần cập nhật (nếu có)
- [ ] `CLAUDE.md` — thêm invariant mới nếu phát sinh bug fix đáng ghi trong lúc triển khai (theo mục
  "Established patterns to apply proactively"); nếu không phát sinh gì mới ngoài đúng thiết kế FSD,
  không cần thêm gì (Wave 2 không đổi model/lock order, rủi ro cross-cutting thấp hơn Wave 1)
- [ ] `.claude/skills/wms-conventions/SKILL.md` — cân nhắc ghi 1 mục về pattern JS `data-category` giờ
  đã dùng ở 2 app (`purchasing` + `quality`), nếu thấy đáng khái quát hoá thành quy ước dùng chung
- [ ] Chạy `manage.py test quality receiving inventory purchasing --keepdb` (full regression, không
  chỉ subset Wave 2) — xác nhận xanh toàn bộ trước khi coi Wave 2 hoàn tất
- [ ] Commit
```bash
git add qc_plan.md BACKLOG.md CLAUDE.md .claude/skills/wms-conventions/SKILL.md
git commit -m "docs(qc): sync BACKLOG/CLAUDE/skill/qc_plan sau khi trien khai Wave 2 criteria gate"
```

---

## Thứ tự commit gợi ý (khi Ryan yêu cầu commit)

1. Phase 1 — gate service + sửa ripple (test cũ + `seed_demo_data.py`)
2. Phase 2 — cảnh báo mismatch + disable nút
3. Phase 3 — widget category (Task 3.1) → context/data island (Task 3.2) → JS (Task 3.3)
4. Phase 4 — doc sync
