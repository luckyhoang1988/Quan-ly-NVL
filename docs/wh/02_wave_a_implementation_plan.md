# Warehouse Expansion — 02. Implementation Plan Wave A: Hardening

> Nguồn: [`01_wave_a_hardening_fsd.md`](01_wave_a_hardening_fsd.md) (đã duyệt) + [`wh_plan.md`](../../wh_plan.md).
> Thực thi tuần tự theo `thuc-thi-va-tdd` (không dùng sub-agent) — mỗi task: viết test FAIL trước,
> code tối thiểu cho PASS, chạy lại, commit.

**Mục tiêu:** A1 (tồn kho theo vị trí), A2 (capacity soft-warn), A3 (ops snapshot theo loại kho)
trên `warehouse_detail`, cộng A4 (đóng gap test có sẵn) — không đổi hành vi nghiệp vụ nào ngoài
3 điểm cảnh báo mới của A2.

**Kiến trúc:** Toàn bộ logic mới sống trong `warehouse.services` (đọc dữ liệu Batch/Inventory/
WarehouseHandoff từ các app khác qua FK, không tạo model mới). View chỉ lắp ráp context; 3 view
ghi có sẵn (`transfer_create`, `qc_result`, `grn_receive_qty`) gọi thêm 1 vòng lặp
`messages.warning` sau khi transaction gốc đã commit thành công.

**Công nghệ:** Django ORM (`aggregate`/`annotate` cho tính occupied qty), không JS mới, không
migration (không field DB nào thay đổi — chỉ hằng số Python + hàm service + template).

## Ràng buộc chung (Global Constraints)

- UI tiếng Việt toàn bộ (label, message, badge text) — theo quy ước dự án, không cần nhắc lại
  từng task.
- Không Celery/cron — mọi tính toán (aging STAGING, capacity ratio) chạy on-the-fly tại thời điểm
  render/action, đúng pattern `⏸️`.
- Badge 3 mức dùng đúng 3 class cố định: `bg-success` (OK), `bg-warning text-dark` (Gần đầy),
  `bg-danger` (Vượt) — không đổi tên class giữa các task.
- **Làm rõ 1 điểm ngoài câu chữ literal của AC-WH-CAP-01**: `capacity` bằng `0` (không phải
  `None`) được xử lý **giống hệt `None`** — coi là "chưa khai dung tích", không hiện badge/cảnh
  báo. Lý do: `Location.capacity`/`Warehouse.capacity` là `PositiveIntegerField(null=True,
  blank=True)` nên `0` là giá trị hợp lệ ở tầng DB, nhưng chia `occupied / 0` sẽ vỡ
  `ZeroDivisionError` — và dung tích bằng 0 không có ý nghĩa vận hành thực (không kho/vị trí nào
  thật sự chứa được 0 đơn vị nhưng vẫn đang lưu hàng). Áp dụng bằng cách dùng `if capacity:`
  (truthy, loại cả `None` lẫn `0`) thay vì `if capacity is not None:` ở mọi hàm liên quan —
  Task 4 có 1 test riêng khẳng định hành vi này.
- Mỗi task chạy `manage.py test <app>` của riêng file đang sửa trước khi qua task kế; Task 4.4
  (cuối Phase 4) chạy lại toàn bộ `warehouse inventory quality shipping stocktake receiving` một
  lần nữa để bắt regression chéo app.
- Không sửa `BACKLOG.md` — Wave A không có FR-code mới (đã chốt ở FSD mục 10).

## Cấu trúc file

| File | Vai trò |
|---|---|
| `inventory/models.py` | Thêm hằng `PHYSICAL_BATCH_STATUSES` (nơi định nghĩa duy nhất, Task 1) |
| `stocktake/services.py` | Đổi nguồn import `PHYSICAL_BATCH_STATUSES` (Task 1) |
| `warehouse/models.py` | Thêm hằng `CAPACITY_WARN_RATIO` (Task 4), `STAGING_AGING_DAYS` (Task 9) |
| `warehouse/services.py` | Toàn bộ hàm mới: `location_occupancy` (T2), `location_occupied_qty`/`warehouse_occupied_qty`/`location_capacity_alerts` (T4), `location_occupied_qty_map`/`capacity_badge` (T8), `ops_snapshot` (T9) |
| `warehouse/views.py` | `warehouse_detail` — mở rộng context qua T3, T8, T10 |
| `warehouse/templates/warehouse/warehouse_detail.html` | Badge (T8), card "Tồn kho theo vị trí" (T3), card "Snapshot vận hành" (T10) |
| `inventory/views.py` | `transfer_create` — thêm cảnh báo (T5) |
| `quality/views.py` | `qc_result` — thêm cảnh báo (T6) |
| `receiving/views.py` | `grn_receive_qty` — thêm cảnh báo (T7) |
| `warehouse/tests.py` | Toàn bộ test mới (T1-T13) + A4 (T11-T13) |
| `inventory/tests.py` | Test T1 (constant), T5 (transfer capacity) |
| `quality/tests.py` | Test T6 (qc_result capacity) |
| `receiving/tests.py` | Test T7 (grn_receive_qty capacity) |

---

## Phase 1 — A1: Inventory theo vị trí

### Task 1: Refactor `PHYSICAL_BATCH_STATUSES` → `inventory.models`

**File:**
- Sửa: `inventory/models.py:117-120` (chèn ngay sau `Batch`, trước `class WarehouseHandoff`)
- Sửa: `stocktake/services.py:33-58` (đổi import, xoá định nghĩa cục bộ)
- Test: `inventory/tests.py` (class mới `PhysicalBatchStatusesConstantTest`)

**Giao diện:**
- Cung cấp: `inventory.models.PHYSICAL_BATCH_STATUSES` (`list[str]`, 5 status trừ `CLOSED`) — mọi
  task sau (T2, T4, T8) import từ đây.

- [ ] **Bước 1: Viết test FAIL**
```python
# inventory/tests.py — thêm vào import: PHYSICAL_BATCH_STATUSES (từ .models)
class PhysicalBatchStatusesConstantTest(TestCase):
    """Refactor Wave A A1: định nghĩa duy nhất tại inventory.models (FSD 2.1)."""

    def test_contains_every_non_closed_status(self):
        self.assertEqual(
            set(PHYSICAL_BATCH_STATUSES),
            {
                Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED, Batch.Status.PENDING_RECEIPT,
                Batch.Status.EXPIRED, Batch.Status.QUARANTINE,
            },
        )
        self.assertNotIn(Batch.Status.CLOSED, PHYSICAL_BATCH_STATUSES)

    def test_stocktake_services_reexports_same_object(self):
        import stocktake.services as stocktake_services
        self.assertIs(stocktake_services.PHYSICAL_BATCH_STATUSES, PHYSICAL_BATCH_STATUSES)
```
  Sửa dòng import đầu file `inventory/tests.py:24` từ
  `from .models import Batch, Inventory, StockMovement, StockTransfer, WarehouseHandoff` thành
  `from .models import Batch, Inventory, PHYSICAL_BATCH_STATUSES, StockMovement, StockTransfer, WarehouseHandoff`.

- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `manage.py test inventory.tests.PhysicalBatchStatusesConstantTest -v 2`,
  kỳ vọng `ImportError: cannot import name 'PHYSICAL_BATCH_STATUSES'`.

- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
# inventory/models.py — chèn ngay sau class Batch (sau dòng "return self.qty_received - self.qty_used"),
# trước "class WarehouseHandoff(models.Model):"

#: Status coi là tồn vật lý còn nằm trong kho (phản ánh trong Inventory.qty_on_hand),
#: trừ CLOSED (dùng hết, qty=0 luôn). Dùng bởi warehouse.services.location_occupancy/
#: location_occupied_qty và stocktake.services._consume_shortage_batches — KHÁC tập
#: FIFO-eligible hẹp hơn (ACTIVE/PARTIAL_USED only) của suggest_fifo_batches.
PHYSICAL_BATCH_STATUSES = [
    Batch.Status.ACTIVE,
    Batch.Status.PARTIAL_USED,
    Batch.Status.PENDING_RECEIPT,
    Batch.Status.EXPIRED,
    Batch.Status.QUARANTINE,
]
```
```python
# stocktake/services.py — sửa khối import (dòng 33-44) và xoá định nghĩa cục bộ (dòng 46-58)
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog
from inventory.models import PHYSICAL_BATCH_STATUSES, Batch, Inventory, StockMovement, WarehouseHandoff
from inventory.services import record_movement
from warehouse.services import get_default_location

from .models import StocktakeItem, StocktakeSession
```
  (Xoá toàn bộ khối comment `#: Status nào cũng...` + `PHYSICAL_BATCH_STATUSES = [...]` gốc ở
  dòng 46-58 — phần dùng hằng số này ở dòng 307-328 giữ nguyên, không đổi.)

- [ ] **Bước 4: Chạy test, xác nhận PASS** — `manage.py test inventory.tests.PhysicalBatchStatusesConstantTest -v 2`.
  Chạy thêm `manage.py test stocktake` để xác nhận không regression (đặc biệt
  `_consume_shortage_batches`, TC dùng `PHYSICAL_BATCH_STATUSES` gián tiếp).

- [ ] **Bước 5: Commit**
```bash
git add inventory/models.py inventory/tests.py stocktake/services.py
git commit -m "refactor(wh): chuyen PHYSICAL_BATCH_STATUSES ve inventory.models (Wave A A1)"
```

---

### Task 2: `warehouse.services.location_occupancy(warehouse)`

**File:**
- Sửa: `warehouse/services.py:1-13` (import), thêm hàm cuối file
- Test: `warehouse/tests.py` (class mới `LocationOccupancyServiceTest`)

**Giao diện:**
- Sử dụng: `inventory.models.PHYSICAL_BATCH_STATUSES` (Task 1), `inventory.models.Batch`.
- Cung cấp: `location_occupancy(warehouse) -> QuerySet[Batch]` (đã `select_related('product',
  'location')`, order `location__code, batch_code`) — Task 3 (view) dùng trực tiếp.

- [ ] **Bước 1: Viết test FAIL**
```python
# warehouse/tests.py — thêm vào đầu file (sau các import hiện có):
from catalog.models import Product
from inventory.models import Batch, Inventory
from inventory.services import transfer_stock
from partners.models import Supplier

from .services import location_occupancy  # nối vào dòng import "from .services import ..." hiện có

class LocationOccupancyServiceTest(TestCase):
    """``location_occupancy`` (A1). TC-WH-LOC-001, 002, 004."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.other_warehouse = Warehouse.objects.create(code='KHO-HCM', name='Kho HCM')
        self.other_location = Location.objects.create(warehouse=self.other_warehouse, code='A-01')

    def _batch(self, code, location, status, qty_received=10, qty_used=0):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier,
            location=location, qty_received=qty_received, qty_used=qty_used, status=status,
        )

    def test_TC_WH_LOC_001_excludes_closed_and_other_warehouse(self):
        active = self._batch('LOT-01', self.location, Batch.Status.ACTIVE)
        self._batch('LOT-02', self.location, Batch.Status.CLOSED, qty_used=10)
        self._batch('LOT-03', self.other_location, Batch.Status.ACTIVE)
        result = list(location_occupancy(self.warehouse))
        self.assertEqual(result, [active])

    def test_TC_WH_LOC_002_includes_every_physical_status(self):
        statuses = [
            Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED, Batch.Status.PENDING_RECEIPT,
            Batch.Status.EXPIRED, Batch.Status.QUARANTINE,
        ]
        expected_codes = set()
        for i, status in enumerate(statuses):
            code = f'LOT-{i:02d}'
            self._batch(code, self.location, status, qty_used=1 if status == Batch.Status.PARTIAL_USED else 0)
            expected_codes.add(code)
        result_codes = {b.batch_code for b in location_occupancy(self.warehouse)}
        self.assertEqual(result_codes, expected_codes)

    def test_TC_WH_LOC_004_batch_closed_by_transfer_is_excluded(self):
        location2 = Location.objects.create(warehouse=self.warehouse, code='A-02')
        batch = self._batch('LOT-01', self.location, Batch.Status.ACTIVE, qty_received=10)
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=10)
        transfer_stock(batch=batch, to_location=location2, qty=10, actor=None)
        batch.refresh_from_db()
        self.assertEqual(batch.status, Batch.Status.CLOSED)
        result_ids = {b.pk for b in location_occupancy(self.warehouse)}
        self.assertNotIn(batch.pk, result_ids)
```

- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `manage.py test warehouse.tests.LocationOccupancyServiceTest -v 2`,
  kỳ vọng `ImportError: cannot import name 'location_occupancy'`.

- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
# warehouse/services.py — sửa dòng import (dòng 7-13):
from django.core.exceptions import ValidationError

from accounts.audit import log_action
from accounts.models import AuditLog
from inventory.models import Batch, Inventory, PHYSICAL_BATCH_STATUSES

from .models import Warehouse

# ... (giữ nguyên toàn bộ hàm hiện có) ...

# thêm cuối file:
def location_occupancy(warehouse):
    """A1: tồn kho theo vị trí, mọi status vật lý, mọi loại kho (MAIN/STAGING/
    SCRAP). Không lọc thêm qty_received > qty_used — CLOSED đã loại hết batch
    qty=0 khỏi PHYSICAL_BATCH_STATUSES (xem FSD 2.2)."""
    return (
        Batch.objects
        .filter(location__warehouse=warehouse, status__in=PHYSICAL_BATCH_STATUSES)
        .select_related('product', 'location')
        .order_by('location__code', 'batch_code')
    )
```

- [ ] **Bước 4: Chạy test, xác nhận PASS** — `manage.py test warehouse.tests.LocationOccupancyServiceTest -v 2`.

- [ ] **Bước 5: Commit**
```bash
git add warehouse/services.py warehouse/tests.py
git commit -m "feat(wh): location_occupancy — tong kho theo vi tri (Wave A A1)"
```

---

### Task 3: UI card "Tồn kho theo vị trí" trên `warehouse_detail`

**File:**
- Sửa: `warehouse/views.py:22-26` (import), `warehouse/views.py:80-89` (`warehouse_detail`)
- Sửa: `warehouse/templates/warehouse/warehouse_detail.html` (chèn card mới sau card "Vị trí lưu trữ", dòng 121-122)
- Test: `warehouse/tests.py` (class mới `WarehouseDetailOccupancyCardTest`)

**Giao diện:**
- Sử dụng: `location_occupancy` (Task 2), `accounts.pagination.paginate_queryset`.
- Cung cấp: context `page_obj`/`page_size` trên `warehouse_detail` — Task 8/10 tái sử dụng cùng context dict (thêm key, không đổi key này).

- [ ] **Bước 1: Viết test FAIL**
```python
# warehouse/tests.py — thêm import: from django.db import connection; from django.test.utils import CaptureQueriesContext
class WarehouseDetailOccupancyCardTest(TestCase):
    """Card "Tồn kho theo vị trí" (A1) trên warehouse_detail. TC-WH-LOC-003."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        Batch.objects.bulk_create([
            Batch(
                product=self.product, batch_code=f'LOT-{i:03d}', supplier=self.supplier,
                location=self.location, qty_received=1, status=Batch.Status.ACTIVE,
            )
            for i in range(35)
        ])
        self.client.force_login(self.staff)

    def test_TC_WH_LOC_003_card_paginates_default_30_and_shows_ui_text(self):
        response = self.client.get(reverse('warehouse:warehouse_detail', args=[self.warehouse.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tồn kho theo vị trí')
        self.assertEqual(len(response.context['page_obj']), 30)
        self.assertEqual(response.context['page_obj'].paginator.count, 35)

    def test_query_count_does_not_grow_with_batch_count(self):
        """N+1 guard (AC-WH-LOC-02): không dùng ngưỡng tuyệt đối (giòn khi Task 8/10
        thêm query sau này) — so query count khi 35 batch vs 5 batch, phải BẰNG NHAU vì
        select_related đã gộp join và trang chỉ đọc tối đa page_size=30 dòng bất kể
        tổng số batch thật có bao nhiêu. Warmup request trước mỗi lần đo để tránh sai
        lệch do cache nguội (ContentType/session...) — request đầu tiên trong test luôn
        tốn thêm vài query so với các request sau, không liên quan gì đến N+1."""
        url = reverse('warehouse:warehouse_detail', args=[self.warehouse.pk])
        self.client.get(url)  # warmup — bỏ kết quả, không đo
        with CaptureQueriesContext(connection) as ctx_many:
            self.client.get(url)
        Batch.objects.filter(location=self.location).exclude(
            batch_code__in=[f'LOT-{i:03d}' for i in range(5)]).delete()
        self.client.get(url)  # warmup lại sau khi đổi dữ liệu
        with CaptureQueriesContext(connection) as ctx_few:
            self.client.get(url)
        self.assertEqual(len(ctx_many.captured_queries), len(ctx_few.captured_queries))
```

- [ ] **Bước 2: Chạy test, xác nhận FAIL** — kỳ vọng `KeyError: 'page_obj'` (context chưa có key này)
  ở cả 2 method (`test_query_count_does_not_grow_with_batch_count` FAIL ngay ở request đầu, trước
  khi kịp xoá batch).

- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
# warehouse/views.py — sửa import (dòng 22-26):
from .forms import LocationForm, WarehouseForm
from .models import MIN_LOCATIONS_PER_WAREHOUSE, Location, Warehouse
from .services import activate_warehouse, deactivate_warehouse, location_occupancy

# sửa warehouse_detail (dòng 80-89):
@login_required
def warehouse_detail(request, pk):
    """READ — chi tiết kho + vị trí lưu trữ + tồn kho theo vị trí (A1)."""
    if not request.user.can_view_menu('warehouse'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Kho hàng".')
    obj = get_object_or_404(Warehouse, pk=pk)
    locations = obj.locations.all()
    page_obj, page_size = paginate_queryset(request, location_occupancy(obj))
    return render(request, 'warehouse/warehouse_detail.html', {
        'obj': obj, 'locations': locations, 'location_form': LocationForm(),
        'page_obj': page_obj, 'page_size': page_size,
    })
```
```html
<!-- warehouse_detail.html — chèn NGAY SAU dòng 121 ("</div>" đóng card "Vị trí lưu trữ"), TRƯỚC dòng 123 ("{% if user.is_superuser ... %}") -->
<div class="card shadow-sm mt-3">
  <div class="card-header panel-toolbar">
    <h2 class="panel-title">Tồn kho theo vị trí ({{ page_obj.paginator.count }})</h2>
  </div>
  <div class="card-body">
    <div class="table-responsive">
      <table class="table table-sm table-striped align-middle table-accent">
        <thead>
          <tr>
            <th>Vị trí</th>
            <th>SKU</th>
            <th>Mã lô</th>
            <th>Trạng thái</th>
            <th>Số lượng khả dụng</th>
          </tr>
        </thead>
        <tbody>
          {% for batch in page_obj %}
          <tr>
            <td>{{ batch.location.code }}</td>
            <td>{{ batch.product.product_code }} — {{ batch.product.name }}</td>
            <td><a href="{% url 'inventory:batch_detail' batch.pk %}">{{ batch.batch_code }}</a></td>
            <td><span class="badge bg-secondary">{{ batch.get_status_display }}</span></td>
            <td>{{ batch.qty_available }}</td>
          </tr>
          {% empty %}
          <tr><td colspan="5" class="text-center text-muted">Không có tồn kho tại vị trí nào.</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% include 'partials/pagination.html' %}
  </div>
</div>
```

- [ ] **Bước 4: Chạy test, xác nhận PASS** — `manage.py test warehouse.tests.WarehouseDetailOccupancyCardTest -v 2`.

- [ ] **Bước 5: Commit**
```bash
git add warehouse/views.py warehouse/templates/warehouse/warehouse_detail.html warehouse/tests.py
git commit -m "feat(wh): card ton kho theo vi tri tren warehouse_detail, phan trang 30/trang (Wave A A1)"
```

---

## Phase 2 — A2: Capacity soft-warn

### Task 4: `location_occupied_qty`, `warehouse_occupied_qty`, `location_capacity_alerts`

**File:**
- Sửa: `warehouse/models.py` (thêm `CAPACITY_WARN_RATIO`)
- Sửa: `warehouse/services.py` (import + 4 hàm mới)
- Test: `warehouse/tests.py` (class mới `LocationCapacityAlertsServiceTest`)

**Giao diện:**
- Cung cấp: `location_capacity_alerts(location) -> list[str]` — Task 5/6/7 gọi sau khi
  transfer/QC/GRN commit thành công.

- [ ] **Bước 1: Viết test FAIL**
```python
# warehouse/tests.py — nối vào import "from .services import ...": location_capacity_alerts
class LocationCapacityAlertsServiceTest(TestCase):
    """``location_capacity_alerts`` (A2). TC-WH-CAP-001, 002, 003, 009."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')

    def _batch(self, code, location, qty_received):
        return Batch.objects.create(
            product=self.product, batch_code=code, supplier=self.supplier,
            location=location, qty_received=qty_received, status=Batch.Status.ACTIVE,
        )

    def test_TC_WH_CAP_001_empty_when_both_capacity_none(self):
        self._batch('LOT-01', self.location, 100)
        self.assertEqual(location_capacity_alerts(self.location), [])

    def test_capacity_zero_treated_as_not_configured(self):
        self.location.capacity = 0
        self.location.save(update_fields=['capacity'])
        self._batch('LOT-01', self.location, 5)
        self.assertEqual(location_capacity_alerts(self.location), [])

    def test_TC_WH_CAP_002_location_near_full_warehouse_capacity_none(self):
        self.location.capacity = 100
        self.location.save(update_fields=['capacity'])
        self._batch('LOT-01', self.location, 95)  # ratio 0.95
        alerts = location_capacity_alerts(self.location)
        self.assertEqual(len(alerts), 1)
        self.assertIn('gần đầy', alerts[0])

    def test_TC_WH_CAP_003_location_over_capacity_warehouse_below_warn(self):
        self.location.capacity = 100
        self.location.save(update_fields=['capacity'])
        self.warehouse.capacity = 10000
        self.warehouse.save(update_fields=['capacity'])
        self._batch('LOT-01', self.location, 120)  # location ratio 1.2, warehouse ratio 0.012
        alerts = location_capacity_alerts(self.location)
        self.assertEqual(len(alerts), 1)
        self.assertIn('vượt dung tích', alerts[0])

    def test_TC_WH_CAP_009_both_levels_over_warn_return_two_messages(self):
        """AC-WH-CAP-08: warehouse ratio cộng dồn MỌI location của kho, gồm cả
        location đang xét (100) cộng location2 (90) -> 190/200 = 0.95 >= 0.9."""
        location2 = Location.objects.create(warehouse=self.warehouse, code='A-02')
        self.location.capacity = 100
        self.location.save(update_fields=['capacity'])
        self.warehouse.capacity = 200
        self.warehouse.save(update_fields=['capacity'])
        self._batch('LOT-01', self.location, 100)  # location ratio 1.0 -> OVER
        self._batch('LOT-02', location2, 90)
        alerts = location_capacity_alerts(self.location)
        self.assertEqual(len(alerts), 2)
```

- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError: cannot import name 'location_capacity_alerts'`.

- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
# warehouse/models.py — thêm ngay dưới MIN_LOCATIONS_PER_WAREHOUSE:
#: A2 (Wave A hardening): occupied/capacity >= ngưỡng này -> "gần đầy" (soft-warn, không chặn).
CAPACITY_WARN_RATIO = 0.9
```
```python
# warehouse/services.py — sửa import:
from django.core.exceptions import ValidationError
from django.db.models import F, Sum

from accounts.audit import log_action
from accounts.models import AuditLog
from inventory.models import Batch, Inventory, PHYSICAL_BATCH_STATUSES

from .models import CAPACITY_WARN_RATIO, Warehouse

# ... (giữ nguyên các hàm hiện có + location_occupancy từ Task 2) ...

# thêm cuối file:
def location_occupied_qty(location):
    """A2: occupied qty tại 1 vị trí = tổng qty_available mọi batch vật lý (FSD 3.1)."""
    return Batch.objects.filter(
        location=location, status__in=PHYSICAL_BATCH_STATUSES,
    ).aggregate(total=Sum(F('qty_received') - F('qty_used')))['total'] or 0


def warehouse_occupied_qty(warehouse):
    """A2: occupied qty cấp kho = tổng occupied qty mọi location thuộc kho đó
    (kể cả location đang được kiểm tra riêng — xem AC-WH-CAP-08)."""
    return Batch.objects.filter(
        location__warehouse=warehouse, status__in=PHYSICAL_BATCH_STATUSES,
    ).aggregate(total=Sum(F('qty_received') - F('qty_used')))['total'] or 0


def _capacity_level(ratio):
    """('OVER'|'WARN'|'OK', css class Bootstrap) theo ngưỡng CAPACITY_WARN_RATIO/1.0."""
    if ratio >= 1.0:
        return 'OVER', 'bg-danger'
    if ratio >= CAPACITY_WARN_RATIO:
        return 'WARN', 'bg-warning text-dark'
    return 'OK', 'bg-success'


def location_capacity_alerts(location):
    """A2(b)(c): list[str] cảnh báo (không chặn), gọi SAU khi transfer_stock()/
    qc_pass()/qc_partial_pass()/start_qc() đã commit. capacity None hoặc 0 (chưa
    khai) -> bỏ qua cấp đó. 0/1/2 message tuỳ mấy cấp đạt ngưỡng gần đầy/vượt."""
    alerts = []
    if location.capacity:
        level, _ = _capacity_level(location_occupied_qty(location) / location.capacity)
        if level == 'OVER':
            alerts.append(f'Vị trí "{location}" đã vượt dung tích.')
        elif level == 'WARN':
            alerts.append(f'Vị trí "{location}" gần đầy dung tích.')
    warehouse = location.warehouse
    if warehouse.capacity:
        level, _ = _capacity_level(warehouse_occupied_qty(warehouse) / warehouse.capacity)
        if level == 'OVER':
            alerts.append(f'Kho "{warehouse.code}" đã vượt dung tích.')
        elif level == 'WARN':
            alerts.append(f'Kho "{warehouse.code}" gần đầy dung tích.')
    return alerts
```

- [ ] **Bước 4: Chạy test, xác nhận PASS** — `manage.py test warehouse.tests.LocationCapacityAlertsServiceTest -v 2`.

- [ ] **Bước 5: Commit**
```bash
git add warehouse/models.py warehouse/services.py warehouse/tests.py
git commit -m "feat(wh): location_capacity_alerts - canh bao gan day/vuot dung tich (Wave A A2)"
```

---

### Task 5: Wire cảnh báo vào `transfer_create`

**File:**
- Sửa: `inventory/views.py:25` (import), `inventory/views.py:350-358`
- Test: `inventory/tests.py` (class mới `TransferCreateCapacityAlertTest`)

**Giao diện:**
- Sử dụng: `warehouse.services.location_capacity_alerts` (Task 4).

- [ ] **Bước 1: Viết test FAIL**
```python
# inventory/tests.py
class TransferCreateCapacityAlertTest(TestCase):
    """A2(b): transfer_create cảnh báo (không chặn) khi đích gần/vượt dung tích. TC-WH-CAP-004."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01')
        self.location2 = Location.objects.create(warehouse=self.warehouse, code='A-02', capacity=10)
        self.batch = Batch.objects.create(
            product=self.product, batch_code='LOT-0001', supplier=self.supplier,
            location=self.location, qty_received=20,
        )
        Inventory.objects.create(product=self.product, warehouse=self.warehouse, qty_on_hand=20)
        self.client.force_login(self.staff)

    def test_TC_WH_CAP_004_transfer_over_capacity_still_created_with_warning(self):
        response = self.client.post(reverse('inventory:transfer_create'), {
            'batch': self.batch.pk, 'to_location': self.location2.pk, 'qty': 20, 'note': '',
        }, follow=True)
        self.assertEqual(StockTransfer.objects.count(), 1)
        warning_messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('vượt dung tích' in m for m in warning_messages))
```

- [ ] **Bước 2: Chạy test, xác nhận FAIL** — transfer tạo thành công nhưng không có warning message
  nào chứa "vượt dung tích".

- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
# inventory/views.py — sửa import (dòng 25):
from warehouse.models import Warehouse
from warehouse.services import location_capacity_alerts

# sửa transfer_create (khối try, dòng 351-358):
        try:
            transfer = transfer_stock(
                batch=form.cleaned_data['batch'], to_location=form.cleaned_data['to_location'],
                qty=form.cleaned_data['qty'], note=form.cleaned_data['note'],
                actor=request.user, ip_address=client_ip(request),
            )
            for alert in location_capacity_alerts(form.cleaned_data['to_location']):
                messages.warning(request, alert)
            messages.success(request, f'Đã tạo phiếu điều chuyển "{transfer.transfer_no}".')
            return redirect('inventory:transfer_list')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
```

- [ ] **Bước 4: Chạy test, xác nhận PASS** — `manage.py test inventory.tests.TransferCreateCapacityAlertTest -v 2`.
  Chạy thêm `manage.py test inventory.tests.StockTransferViewTest` để xác nhận không regression.

- [ ] **Bước 5: Commit**
```bash
git add inventory/views.py inventory/tests.py
git commit -m "feat(wh): transfer_create canh bao dung tich dich sau khi transfer thanh cong (Wave A A2)"
```

---

### Task 6: Wire cảnh báo vào `qc_result` (action pass/partial)

**File:**
- Sửa: `quality/views.py:29` (import), `quality/views.py:105-125`
- Test: `quality/tests.py` (thêm 2 method vào `QcResultViewTest`)

**Giao diện:**
- Sử dụng: `warehouse.services.location_capacity_alerts` (Task 4).

- [ ] **Bước 1: Viết test FAIL**
```python
# quality/tests.py — thêm vào class QcResultViewTest (sau test_TC_QC_VIEW_001_006...):
    def test_TC_WH_CAP_005_pass_action_over_capacity_location_warns(self):
        self.location.capacity = 5
        self.location.save(update_fields=['capacity'])
        response = self.client.post(self._url(), self._payload(action='pass'), follow=True)
        warning_messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('vượt dung tích' in m for m in warning_messages))
        self.assertTrue(Batch.objects.filter(product=self.product, status=Batch.Status.PENDING_RECEIPT).exists())

    def test_TC_WH_CAP_006_partial_action_over_capacity_location_warns(self):
        self.location.capacity = 3
        self.location.save(update_fields=['capacity'])
        response = self.client.post(
            self._url(), self._payload(**{'items-0-qty_pass': 6, 'action': 'partial'}), follow=True)
        warning_messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('vượt dung tích' in m for m in warning_messages))
```

- [ ] **Bước 2: Chạy test, xác nhận FAIL** — QC pass/partial thành công nhưng không có warning
  "vượt dung tích".

- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
# quality/views.py — sửa import (dòng 25-29):
from receiving.models import Grn
from warehouse.services import location_capacity_alerts

from .forms import QcCriteriaForm, QcInspectionItemFormSet, QcItemResultFormSet, QcOverrideForm, QcResultForm
from .models import QcCriteria, QcInspection, QcInspectionItem
from .services import overdue_inspections, qc_fail, qc_partial_pass, qc_pass, suggested_sample_qty

# sửa khối try trong qc_result (dòng 105-124):
            try:
                if action == 'pass':
                    qc_pass(
                        inspection, actor=request.user, location=location, ip_address=ip_address,
                        assigned_to=assigned_to,
                    )
                elif action == 'fail':
                    qc_fail(inspection, actor=request.user, reason=reason, ip_address=ip_address)
                else:
                    item_results = {
                        form.instance.pk: form.cleaned_data.get('qty_pass') or 0
                        for form in formset.forms
                    }
                    qc_partial_pass(
                        inspection, item_results, actor=request.user,
                        location=location, ip_address=ip_address, assigned_to=assigned_to,
                    )
                if action in ('pass', 'partial'):
                    for alert in location_capacity_alerts(location):
                        messages.warning(request, alert)
                messages.success(request, f'Đã ghi kết quả QC cho "{grn.grn_no}".')
                return redirect('receiving:grn_detail', pk=grn.pk)
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
```

- [ ] **Bước 4: Chạy test, xác nhận PASS** — `manage.py test quality.tests.QcResultViewTest -v 2`.

- [ ] **Bước 5: Commit**
```bash
git add quality/views.py quality/tests.py
git commit -m "feat(wh): qc_result canh bao dung tich dich pass/partial (Wave A A2)"
```

---

### Task 7: Wire cảnh báo vào `grn_receive_qty` (start_qc → STAGING)

**File:**
- Sửa: `receiving/views.py:24` (import), `receiving/views.py:337-341`
- Test: `receiving/tests.py` (thêm method vào `GrnViewTest`)

**Giao diện:**
- Sử dụng: `warehouse.services.location_capacity_alerts`, `get_default_location`,
  `get_staging_warehouse` (đã tồn tại).

- [ ] **Bước 1: Viết test FAIL**
```python
# receiving/tests.py — thêm vào class GrnViewTest (sau test_TC_GRN_VIEW_003_003...):
    def test_TC_WH_CAP_007_receive_qty_over_staging_capacity_warns(self):
        grn = self._create_grn(status=Grn.Status.PENDING_QC)
        item = grn.items.first()
        staging_location = Location.objects.get(warehouse=self.staging_warehouse, code='A-01')
        staging_location.capacity = 5
        staging_location.save(update_fields=['capacity'])
        response = self.client.post(reverse('receiving:grn_receive_qty', args=[grn.pk]), {
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
            'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '1000',
            'items-0-id': item.pk, 'items-0-qty_received': 10,
            'inspector': self.qc_user.pk,
        }, follow=True)
        warning_messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('vượt dung tích' in m for m in warning_messages))
```

- [ ] **Bước 2: Chạy test, xác nhận FAIL** — submit thành công nhưng không có warning "vượt dung tích".

- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
# receiving/views.py — sửa import (dòng 24):
from quality.services import overdue_inspections, start_qc
from warehouse.services import get_default_location, get_staging_warehouse, location_capacity_alerts

# sửa khối else trong grn_receive_qty (dòng 337-341):
        else:
            for alert in tolerance_alerts(obj):
                messages.warning(request, alert)
            staging_location = get_default_location(get_staging_warehouse())
            for alert in location_capacity_alerts(staging_location):
                messages.warning(request, alert)
            messages.success(request, f'Đã submit GRN "{obj.grn_no}" sang QC ({inspection.qc_no}).')
            return redirect('receiving:grn_detail', pk=obj.pk)
```

- [ ] **Bước 4: Chạy test, xác nhận PASS** — `manage.py test receiving.tests.GrnViewTest -v 2`.

- [ ] **Bước 5: Commit**
```bash
git add receiving/views.py receiving/tests.py
git commit -m "feat(wh): grn_receive_qty canh bao dung tich Kho cho sau start_qc (Wave A A2)"
```

---

### Task 8: Badge OK/Gần đầy/Vượt trên `warehouse_detail`

**File:**
- Sửa: `warehouse/services.py` (thêm `location_occupied_qty_map`, `capacity_badge`)
- Sửa: `warehouse/views.py:22-26`, `warehouse/views.py` (`warehouse_detail`)
- Sửa: `warehouse/templates/warehouse/warehouse_detail.html` (panel đầu + cột "Dung tích" bảng vị trí)
- Test: `warehouse/tests.py` (class mới `WarehouseDetailCapacityBadgeTest`)

**Giao diện:**
- Sử dụng: `location_occupied_qty`, `warehouse_occupied_qty`, `_capacity_level` (Task 4).
- Cung cấp: context `warehouse_badge`, `warehouse_occupied`; mỗi `Location` trong `locations`
  được gắn thêm attribute `capacity_badge` (không phải field DB — ad-hoc như
  `types.SimpleNamespace` synthetic rows đã dùng ở `inventory_list`).

- [ ] **Bước 1: Viết test FAIL**
```python
# warehouse/tests.py
class WarehouseDetailCapacityBadgeTest(TestCase):
    """Badge OK/Gần đầy/Vượt trên warehouse_detail (A2.a). TC-WH-CAP-008."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội', capacity=1000)
        self.loc_ok = Location.objects.create(warehouse=self.warehouse, code='A-01', capacity=100)
        self.loc_warn = Location.objects.create(warehouse=self.warehouse, code='A-02', capacity=100)
        self.loc_over = Location.objects.create(warehouse=self.warehouse, code='A-03', capacity=100)
        self.loc_none = Location.objects.create(warehouse=self.warehouse, code='A-04')
        Batch.objects.create(
            product=self.product, batch_code='LOT-OK', supplier=self.supplier,
            location=self.loc_ok, qty_received=10, status=Batch.Status.ACTIVE)
        Batch.objects.create(
            product=self.product, batch_code='LOT-WARN', supplier=self.supplier,
            location=self.loc_warn, qty_received=95, status=Batch.Status.ACTIVE)
        Batch.objects.create(
            product=self.product, batch_code='LOT-OVER', supplier=self.supplier,
            location=self.loc_over, qty_received=150, status=Batch.Status.ACTIVE)
        self.client.force_login(self.staff)

    def test_TC_WH_CAP_008_badges_match_ratio_thresholds(self):
        response = self.client.get(reverse('warehouse:warehouse_detail', args=[self.warehouse.pk]))
        badges = {loc.code: loc.capacity_badge for loc in response.context['locations']}
        self.assertEqual(badges['A-01']['css'], 'bg-success')
        self.assertEqual(badges['A-02']['css'], 'bg-warning text-dark')
        self.assertEqual(badges['A-03']['css'], 'bg-danger')
        self.assertIsNone(badges['A-04'])
        # tổng occupied kho = 10+95+150 = 255 / capacity 1000 = 0.255 -> OK
        self.assertEqual(response.context['warehouse_badge']['css'], 'bg-success')
```

- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `KeyError`/`AttributeError` vì `capacity_badge`/`warehouse_badge` chưa có trong context.

- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
# warehouse/services.py — thêm cuối file (sau location_capacity_alerts của Task 4):
def location_occupied_qty_map(warehouse):
    """{location_id: occupied_qty} — 1 query cho toàn bộ vị trí của kho, tránh
    N+1 khi warehouse_detail tính badge dung tích cho mỗi dòng bảng "Vị trí
    lưu trữ"."""
    rows = (
        Batch.objects.filter(location__warehouse=warehouse, status__in=PHYSICAL_BATCH_STATUSES)
        .values('location_id')
        .annotate(total=Sum(F('qty_received') - F('qty_used')))
    )
    return {row['location_id']: row['total'] for row in rows}


def capacity_badge(occupied, capacity):
    """UI badge OK/Gần đầy/Vượt (3.3.a) — None nếu capacity chưa khai (None/0)."""
    if not capacity:
        return None
    level, css = _capacity_level(occupied / capacity)
    label = {'OVER': 'Vượt dung tích', 'WARN': 'Gần đầy', 'OK': 'Bình thường'}[level]
    return {'css': css, 'label': label}
```
```python
# warehouse/views.py — sửa import:
from .services import (
    activate_warehouse, capacity_badge, deactivate_warehouse, location_occupancy,
    location_occupied_qty_map, warehouse_occupied_qty,
)

# sửa warehouse_detail:
@login_required
def warehouse_detail(request, pk):
    """READ — chi tiết kho + vị trí lưu trữ + tồn kho theo vị trí (A1) + badge dung tích (A2)."""
    if not request.user.can_view_menu('warehouse'):
        raise PermissionDenied('Bạn không có quyền truy cập mục "Kho hàng".')
    obj = get_object_or_404(Warehouse, pk=pk)
    locations = list(obj.locations.all())
    occupied_map = location_occupied_qty_map(obj)
    for loc in locations:
        loc.capacity_badge = capacity_badge(occupied_map.get(loc.pk, 0), loc.capacity)
    warehouse_occupied = warehouse_occupied_qty(obj)
    page_obj, page_size = paginate_queryset(request, location_occupancy(obj))
    return render(request, 'warehouse/warehouse_detail.html', {
        'obj': obj, 'locations': locations, 'location_form': LocationForm(),
        'page_obj': page_obj, 'page_size': page_size,
        'warehouse_badge': capacity_badge(warehouse_occupied, obj.capacity),
        'warehouse_occupied': warehouse_occupied,
    })
```
```html
<!-- warehouse_detail.html — sửa panel đầu, chèn sau khối "Dung tích" (dòng 46-49), trước "Trạng thái" (dòng 50) -->
        <tr>
          <th class="text-nowrap">Dung tích</th>
          <td>{{ obj.capacity|default:"—" }}</td>
        </tr>
        {% if warehouse_badge %}
        <tr>
          <th class="text-nowrap">Dung tích sử dụng</th>
          <td><span class="badge {{ warehouse_badge.css }}">{{ warehouse_badge.label }} ({{ warehouse_occupied }}/{{ obj.capacity }})</span></td>
        </tr>
        {% endif %}
        <tr>
          <th class="text-nowrap">Trạng thái</th>

<!-- sửa cột "Dung tích" trong bảng "Vị trí lưu trữ" (dòng 94, trong khối {% for loc in locations %}) -->
            <td>
              {{ loc.capacity|default:"—" }}
              {% if loc.capacity_badge %}<span class="badge {{ loc.capacity_badge.css }}">{{ loc.capacity_badge.label }}</span>{% endif %}
            </td>
```

- [ ] **Bước 4: Chạy test, xác nhận PASS** — `manage.py test warehouse.tests.WarehouseDetailCapacityBadgeTest -v 2`.
  Chạy lại `warehouse.tests.WarehouseDetailOccupancyCardTest` (Task 3) để xác nhận không regression
  (context `locations` giờ là `list`, không còn `QuerySet` — template không phụ thuộc method
  QuerySet-only nào ngoài lặp, nên vẫn an toàn).

- [ ] **Bước 5: Commit**
```bash
git add warehouse/services.py warehouse/views.py warehouse/templates/warehouse/warehouse_detail.html warehouse/tests.py
git commit -m "feat(wh): badge OK/Gan day/Vuot dung tich tren warehouse_detail (Wave A A2)"
```

---

## Phase 3 — A3: Ops snapshot theo loại kho

### Task 9: `warehouse.services.ops_snapshot(warehouse)`

**File:**
- Sửa: `warehouse/models.py` (thêm `STAGING_AGING_DAYS`)
- Sửa: `warehouse/services.py` (import + 4 hàm mới)
- Test: `warehouse/tests.py` (class mới `OpsSnapshotServiceTest`)

**Giao diện:**
- Cung cấp: `ops_snapshot(warehouse) -> dict` — key khác nhau tuỳ `warehouse_type`:
  STAGING → `active_count`/`active_qty`/`aged_count`; MAIN → `pending_handoff_count`;
  SCRAP → `quarantine_qty`. Task 10 (template) chọn khối hiển thị theo `obj.warehouse_type`,
  không dựa vào key nào có mặt trong dict.

- [ ] **Bước 1: Viết test FAIL**
```python
# warehouse/tests.py — thêm import: from datetime import timedelta; from django.utils import timezone
# nối vào import "from .services import ...": ops_snapshot
# nối vào import "from .models import ...": STAGING_AGING_DAYS
class OpsSnapshotServiceTest(TestCase):
    """``ops_snapshot`` (A3). TC-WH-OPS-001, 002, 003."""

    def setUp(self):
        self.product = Product.objects.create(product_code='NVL-0001', name='Bột mì', uom='kg')
        self.supplier = Supplier.objects.create(supplier_code='NCC-0001', name='Công ty TNHH ABC')

    def test_TC_WH_OPS_001_staging_counts_active_qty_and_aged(self):
        staging = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        location = Location.objects.create(warehouse=staging, code='A-01')
        Batch.objects.create(
            product=self.product, batch_code='LOT-FRESH', supplier=self.supplier,
            location=location, qty_received=10, status=Batch.Status.ACTIVE)
        aged = Batch.objects.create(
            product=self.product, batch_code='LOT-AGED', supplier=self.supplier,
            location=location, qty_received=5, status=Batch.Status.ACTIVE)
        Batch.objects.filter(pk=aged.pk).update(
            created_at=timezone.now() - timedelta(days=STAGING_AGING_DAYS + 1))
        Batch.objects.create(
            product=self.product, batch_code='LOT-CLOSED', supplier=self.supplier,
            location=location, qty_received=1, qty_used=1, status=Batch.Status.CLOSED)
        snapshot = ops_snapshot(staging)
        self.assertEqual(snapshot['active_count'], 2)
        self.assertEqual(snapshot['active_qty'], 15)
        self.assertEqual(snapshot['aged_count'], 1)

    def test_TC_WH_OPS_002_main_counts_pending_handoff_only(self):
        """Mirror fixture pattern của ``inventory.tests.StockTransferPendingReceiptGuardTest``
        (Grn không cần GrnItem, QcInspection không cần started_at, WarehouseHandoff tạo
        thẳng bằng ``.objects.create`` — service ``create_handoff()`` chỉ cần khi test
        quan tâm tới side-effect notify(), không phải trường hợp ở đây)."""
        from inventory.models import WarehouseHandoff
        from inventory.services import accept_handoff
        from purchasing.models import PurchaseOrder
        from quality.models import QcInspection
        from receiving.models import Grn

        qc_user = User.objects.create_user(username='qc1', password='qc-pass-123', role=User.Role.QC)
        main = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        other_main = Warehouse.objects.create(code='KHO-HCM', name='Kho HCM')
        location = Location.objects.create(warehouse=main, code='A-01')
        po = PurchaseOrder.objects.create(po_no='PO-0001', supplier=self.supplier)
        grn = Grn.objects.create(po=po, supplier=self.supplier, created_by=qc_user)
        inspection = QcInspection.objects.create(grn=grn, inspector=qc_user)
        batch = Batch.objects.create(
            product=self.product, batch_code='LOT-PENDING', supplier=self.supplier,
            location=location, qty_received=10, status=Batch.Status.PENDING_RECEIPT)
        handoff = WarehouseHandoff.objects.create(
            batch=batch, qc_inspection=inspection, destination_warehouse=main)

        self.assertEqual(ops_snapshot(main)['pending_handoff_count'], 1)
        self.assertEqual(ops_snapshot(other_main)['pending_handoff_count'], 0)

        accept_handoff(handoff, actor=qc_user)
        self.assertEqual(ops_snapshot(main)['pending_handoff_count'], 0)

    def test_TC_WH_OPS_003_scrap_sums_quarantine_qty_only(self):
        scrap = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        location = Location.objects.create(warehouse=scrap, code='A-01')
        Batch.objects.create(
            product=self.product, batch_code='LOT-Q1', supplier=self.supplier,
            location=location, qty_received=10, status=Batch.Status.QUARANTINE)
        Batch.objects.create(
            product=self.product, batch_code='LOT-Q2', supplier=self.supplier,
            location=location, qty_received=5, qty_used=2, status=Batch.Status.QUARANTINE)
        Batch.objects.create(
            product=self.product, batch_code='LOT-CLOSED', supplier=self.supplier,
            location=location, qty_received=1, qty_used=1, status=Batch.Status.CLOSED)
        snapshot = ops_snapshot(scrap)
        self.assertEqual(snapshot['quarantine_qty'], 13)
```

- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `ImportError: cannot import name 'ops_snapshot'`.

- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
# warehouse/models.py — thêm ngay dưới CAPACITY_WARN_RATIO:
#: A3 (Wave A hardening): batch STAGING ACTIVE có created_at quá số ngày này -> tính "tồn đọng".
STAGING_AGING_DAYS = 3
```
```python
# warehouse/services.py — sửa import:
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db.models import Count, F, Sum
from django.utils import timezone

from accounts.audit import log_action
from accounts.models import AuditLog
from inventory.models import Batch, Inventory, PHYSICAL_BATCH_STATUSES, WarehouseHandoff

from .models import CAPACITY_WARN_RATIO, STAGING_AGING_DAYS, Warehouse

# ... (giữ nguyên toàn bộ hàm hiện có) ...

# thêm cuối file:
def _staging_ops_snapshot(warehouse):
    batches = Batch.objects.filter(location__warehouse=warehouse, status=Batch.Status.ACTIVE)
    agg = batches.aggregate(count=Count('pk'), qty=Sum(F('qty_received') - F('qty_used')))
    cutoff_date = timezone.localdate() - timedelta(days=STAGING_AGING_DAYS)
    cutoff_dt = timezone.make_aware(datetime.combine(cutoff_date, time.min))
    return {
        'active_count': agg['count'] or 0,
        'active_qty': agg['qty'] or 0,
        'aged_count': batches.filter(created_at__lt=cutoff_dt).count(),
    }


def _main_ops_snapshot(warehouse):
    pending_handoff_count = WarehouseHandoff.objects.filter(
        destination_warehouse=warehouse, status=WarehouseHandoff.Status.PENDING,
    ).count()
    return {'pending_handoff_count': pending_handoff_count}


def _scrap_ops_snapshot(warehouse):
    qty = Batch.objects.filter(
        location__warehouse=warehouse, status=Batch.Status.QUARANTINE,
    ).aggregate(total=Sum(F('qty_received') - F('qty_used')))['total'] or 0
    return {'quarantine_qty': qty}


def ops_snapshot(warehouse):
    """A3: KPI vận hành theo warehouse_type — trả dict cho đúng 1 loại kho.
    Template chọn khối hiển thị theo obj.warehouse_type, không theo key dict."""
    if warehouse.warehouse_type == Warehouse.WarehouseType.STAGING:
        return _staging_ops_snapshot(warehouse)
    if warehouse.warehouse_type == Warehouse.WarehouseType.MAIN:
        return _main_ops_snapshot(warehouse)
    return _scrap_ops_snapshot(warehouse)
```

- [ ] **Bước 4: Chạy test, xác nhận PASS** — `manage.py test warehouse.tests.OpsSnapshotServiceTest -v 2`.

- [ ] **Bước 5: Commit**
```bash
git add warehouse/models.py warehouse/services.py warehouse/tests.py
git commit -m "feat(wh): ops_snapshot - KPI van hanh theo loai kho (Wave A A3)"
```

---

### Task 10: UI card "Snapshot vận hành" trên `warehouse_detail`

**File:**
- Sửa: `warehouse/views.py` (import + `warehouse_detail`)
- Sửa: `warehouse/templates/warehouse/warehouse_detail.html` (card mới, sau card "Tồn kho theo vị trí")
- Test: `warehouse/tests.py` (class mới `WarehouseDetailOpsSnapshotCardTest`)

**Giao diện:**
- Sử dụng: `ops_snapshot` (Task 9), `STAGING_AGING_DAYS` (Task 9).

- [ ] **Bước 1: Viết test FAIL**
```python
# warehouse/tests.py
class WarehouseDetailOpsSnapshotCardTest(TestCase):
    """Card "Snapshot vận hành" (A3) — chỉ hiện đúng 1 khối theo warehouse_type. TC-WH-OPS-004."""

    def setUp(self):
        self.staff = User.objects.create_user(username='kho1', password='kho-pass-123', role=User.Role.STAFF)
        self.client.force_login(self.staff)

    def test_TC_WH_OPS_004_staging_page_shows_only_staging_block(self):
        staging = Warehouse.objects.create(
            code='KHO-CHO', name='Kho chờ', warehouse_type=Warehouse.WarehouseType.STAGING)
        response = self.client.get(reverse('warehouse:warehouse_detail', args=[staging.pk]))
        self.assertContains(response, 'Số lô đang hoạt động')
        self.assertNotContains(response, 'Phiếu chờ nhận hàng đang chờ')
        self.assertNotContains(response, 'Tổng số lượng đang Quarantine')

    def test_TC_WH_OPS_004_main_page_shows_only_main_block(self):
        main = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        response = self.client.get(reverse('warehouse:warehouse_detail', args=[main.pk]))
        self.assertContains(response, 'Phiếu chờ nhận hàng đang chờ')
        self.assertNotContains(response, 'Số lô đang hoạt động')
        self.assertNotContains(response, 'Tổng số lượng đang Quarantine')

    def test_TC_WH_OPS_004_scrap_page_shows_only_scrap_block(self):
        scrap = Warehouse.objects.create(
            code='KHO-PHE', name='Kho phế', warehouse_type=Warehouse.WarehouseType.SCRAP)
        response = self.client.get(reverse('warehouse:warehouse_detail', args=[scrap.pk]))
        self.assertContains(response, 'Tổng số lượng đang Quarantine')
        self.assertNotContains(response, 'Số lô đang hoạt động')
        self.assertNotContains(response, 'Phiếu chờ nhận hàng đang chờ')
```

- [ ] **Bước 2: Chạy test, xác nhận FAIL** — `AssertionError` vì trang chưa chứa text nào ở trên.

- [ ] **Bước 3: Viết code tối thiểu để PASS**
```python
# warehouse/views.py — sửa import:
from .models import MIN_LOCATIONS_PER_WAREHOUSE, STAGING_AGING_DAYS, Location, Warehouse
from .services import (
    activate_warehouse, capacity_badge, deactivate_warehouse, location_occupancy,
    location_occupied_qty_map, ops_snapshot, warehouse_occupied_qty,
)

# sửa warehouse_detail — thêm 2 key vào dict trả về của render():
    return render(request, 'warehouse/warehouse_detail.html', {
        'obj': obj, 'locations': locations, 'location_form': LocationForm(),
        'page_obj': page_obj, 'page_size': page_size,
        'warehouse_badge': capacity_badge(warehouse_occupied, obj.capacity),
        'warehouse_occupied': warehouse_occupied,
        'ops_snapshot': ops_snapshot(obj), 'staging_aging_days': STAGING_AGING_DAYS,
    })
```
```html
<!-- warehouse_detail.html — chèn NGAY SAU card "Tồn kho theo vị trí" (Task 3), TRƯỚC "{% if user.is_superuser ... %}" -->
<div class="card shadow-sm mt-3">
  <div class="card-header panel-toolbar">
    <h2 class="panel-title">Snapshot vận hành</h2>
  </div>
  <div class="card-body">
    {% if obj.warehouse_type == 'STAGING' %}
      <p class="mb-1">Số lô đang hoạt động: <strong>{{ ops_snapshot.active_count }}</strong> (tổng số lượng {{ ops_snapshot.active_qty }})</p>
      <p class="mb-0">Số lô tồn quá {{ staging_aging_days }} ngày: <strong>{{ ops_snapshot.aged_count }}</strong></p>
    {% elif obj.warehouse_type == 'MAIN' %}
      <p class="mb-0">
        Phiếu chờ nhận hàng đang chờ: <strong>{{ ops_snapshot.pending_handoff_count }}</strong>
        <a href="{% url 'inventory:handoff_list' %}" class="btn btn-sm btn-outline-secondary ms-2">Xem</a>
      </p>
    {% else %}
      <p class="mb-0">Tổng số lượng đang Quarantine: <strong>{{ ops_snapshot.quarantine_qty }}</strong></p>
    {% endif %}
  </div>
</div>
```

- [ ] **Bước 4: Chạy test, xác nhận PASS** — `manage.py test warehouse.tests.WarehouseDetailOpsSnapshotCardTest -v 2`.

- [ ] **Bước 5: Commit**
```bash
git add warehouse/views.py warehouse/templates/warehouse/warehouse_detail.html warehouse/tests.py
git commit -m "feat(wh): card snapshot van hanh tren warehouse_detail (Wave A A3)"
```

---

## Phase 4 — A4: Đóng gap test có sẵn + regression + doc sync

### Task 11: Test `Warehouse.staff` M2M

**File:**
- Test: `warehouse/tests.py` (class mới `WarehouseStaffAssignmentTest`) — không sửa code sản phẩm
  (hành vi đã có sẵn qua `WarehouseForm`, chỉ thiếu test).

- [ ] **Bước 1-4: Viết test, chạy PASS ngay** (hành vi đã tồn tại, đây là test bổ sung — không có
  bước RED riêng vì không sửa code, nhưng vẫn phải chạy để xác nhận test đúng và pass thật, không
  false-positive)
```python
class WarehouseStaffAssignmentTest(TestCase):
    """``Warehouse.staff`` M2M qua ``WarehouseForm`` (A4 test gap)."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.staff1 = User.objects.create_user(
            username='nv1', password='nv-pass-123', role=User.Role.STAFF,
            department=User.Department.WAREHOUSE)
        self.staff2 = User.objects.create_user(
            username='nv2', password='nv-pass-123', role=User.Role.STAFF,
            department=User.Department.WAREHOUSE)
        self.client.force_login(self.manager)

    def test_create_assigns_staff(self):
        response = self.client.post(reverse('warehouse:warehouse_create'), {
            'code': 'KHO-HN', 'name': 'Kho Hà Nội', 'address': '', 'capacity': '',
            'warehouse_type': Warehouse.WarehouseType.MAIN,
            'staff': [self.staff1.pk, self.staff2.pk],
        })
        warehouse = Warehouse.objects.get(code='KHO-HN')
        self.assertRedirects(response, reverse('warehouse:warehouse_detail', args=[warehouse.pk]))
        self.assertEqual(set(warehouse.staff.all()), {self.staff1, self.staff2})

    def test_update_replaces_staff(self):
        warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        warehouse.staff.add(self.staff1)
        self.client.post(reverse('warehouse:warehouse_update', args=[warehouse.pk]), {
            'code': 'KHO-HN', 'name': 'Kho Hà Nội', 'address': '', 'capacity': '',
            'staff': [self.staff2.pk],
        })
        warehouse.refresh_from_db()
        self.assertEqual(set(warehouse.staff.all()), {self.staff2})

    def test_non_warehouse_department_user_not_selectable_as_staff(self):
        """WarehouseForm.__init__ giới hạn queryset staff chỉ department=WAREHOUSE,
        is_active=True — user phòng khác POST được cũng phải bị từ chối, không chỉ
        ẩn khỏi dropdown."""
        outsider = User.objects.create_user(
            username='qc1', password='qc-pass-123', role=User.Role.QC, department=User.Department.QC)
        response = self.client.post(reverse('warehouse:warehouse_create'), {
            'code': 'KHO-HN', 'name': 'Kho Hà Nội', 'address': '', 'capacity': '',
            'warehouse_type': Warehouse.WarehouseType.MAIN,
            'staff': [outsider.pk],
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Warehouse.objects.filter(code='KHO-HN').exists())
```
  Chạy `manage.py test warehouse.tests.WarehouseStaffAssignmentTest -v 2`, xác nhận cả 3 PASS
  ngay (nếu FAIL, đây là bug thật cần sửa qua TDD bình thường — không phải kết quả mong đợi).

- [ ] **Bước 5: Commit**
```bash
git add warehouse/tests.py
git commit -m "test(wh): dong gap test Warehouse.staff M2M (Wave A A4)"
```

---

### Task 12: Test `location_update`

**File:**
- Test: `warehouse/tests.py` (class mới `LocationUpdateViewTest`) — hành vi có sẵn, không sửa code sản phẩm.

- [ ] **Bước 1-4: Viết test, chạy, xác nhận PASS**
```python
class LocationUpdateViewTest(TestCase):
    """``location_update`` (A4 test gap) — sửa mã/dung tích 1 vị trí."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username='wm', password='wm-pass-123', role=User.Role.MANAGER)
        self.staff = User.objects.create_user(
            username='staff', password='staff-pass-123', role=User.Role.STAFF)
        self.warehouse = Warehouse.objects.create(code='KHO-HN', name='Kho Hà Nội')
        self.location = Location.objects.create(warehouse=self.warehouse, code='A-01', capacity=100)
        self.client.force_login(self.manager)

    def test_update_changes_code_and_capacity_and_audits(self):
        response = self.client.post(
            reverse('warehouse:location_update', args=[self.warehouse.pk, self.location.pk]),
            {'code': 'A-01-B', 'capacity': 200},
        )
        self.location.refresh_from_db()
        self.assertRedirects(response, reverse('warehouse:warehouse_detail', args=[self.warehouse.pk]))
        self.assertEqual(self.location.code, 'A-01-B')
        self.assertEqual(self.location.capacity, 200)
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE, target_id=str(self.location.pk)).exists())

    def test_update_duplicate_code_in_same_warehouse_rejected(self):
        Location.objects.create(warehouse=self.warehouse, code='A-02')
        response = self.client.post(
            reverse('warehouse:location_update', args=[self.warehouse.pk, self.location.pk]),
            {'code': 'A-02', 'capacity': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.location.refresh_from_db()
        self.assertEqual(self.location.code, 'A-01')

    def test_non_manager_forbidden(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('warehouse:location_update', args=[self.warehouse.pk, self.location.pk]),
            {'code': 'A-01-B', 'capacity': ''},
        )
        self.assertEqual(response.status_code, 403)
        self.location.refresh_from_db()
        self.assertEqual(self.location.code, 'A-01')
```
  Chạy `manage.py test warehouse.tests.LocationUpdateViewTest -v 2`, xác nhận cả 3 PASS.

- [ ] **Bước 5: Commit**
```bash
git add warehouse/tests.py
git commit -m "test(wh): dong gap test location_update (Wave A A4)"
```

---

### Task 13: Test `get_scrap_warehouse()` khi thiếu Kho phế

**File:**
- Test: `warehouse/tests.py:339` — thêm 1 method vào class `WarehouseSingletonHelpersTest` đã có
  sẵn (ngay sau `test_get_staging_warehouse_raises_when_missing`), không sửa code sản phẩm.

- [ ] **Bước 1-4: Viết test, chạy, xác nhận PASS**
```python
    def test_get_scrap_warehouse_raises_when_missing(self):
        with self.assertRaises(ValidationError):
            get_scrap_warehouse()
```
  Chạy `manage.py test warehouse.tests.WarehouseSingletonHelpersTest -v 2`, xác nhận PASS (nhánh
  `count == 0` của `_get_singleton` đã đúng cho STAGING, giờ xác nhận thêm cho SCRAP).

- [ ] **Bước 5: Commit**
```bash
git add warehouse/tests.py
git commit -m "test(wh): dong gap test get_scrap_warehouse khi thieu Kho phe (Wave A A4)"
```

---

### Task 14: Regression toàn diện + doc sync

**File:**
- Không sửa code sản phẩm — chỉ chạy test + sửa `wh_plan.md`.

- [ ] **Bước 1: Chạy full regression**
```bash
manage.py test warehouse inventory quality shipping stocktake receiving -v 2
```
  Kỳ vọng: tất cả PASS (bao gồm mọi test cũ + 14 task vừa thêm). Nếu có FAIL, xử lý qua
  `debug-co-he-thong` trước khi tiếp tục — không bỏ qua.

- [ ] **Bước 2: Cross-check AC/TC** — grep từng mã `TC-WH-*`/`AC-WH-*` trong
  `docs/wh/01_wave_a_hardening_fsd.md` đối chiếu với tên method `test_TC_WH_*` thực tế trong
  `warehouse/tests.py`/`inventory/tests.py`/`quality/tests.py`/`receiving/tests.py` — xác nhận đủ
  17 TC (LOC 001-004, CAP 001-009, OPS 001-004) đều có method tương ứng, không sót cái nào (theo
  quy ước "AC/TC cross-check cuối mỗi implementation phase" đã chốt trong CLAUDE.md).

- [ ] **Bước 3: Cập nhật `wh_plan.md`** — (đích `PHYSICAL_BATCH_STATUSES`, cả bảng "Quyết định đã
  chốt" #4 lẫn §A1 chi tiết, đã sửa sẵn lúc FSD được duyệt, trước khi viết plan này — 3 việc còn lại
  dưới đây)
```markdown
<!-- dòng 11: tick checkbox Wave A -->
- [x] Wave A — Hardening (inventory theo vị trí, capacity soft-warn gồm STAGING receipt, ops snapshot, test/doc)

<!-- dòng 23-24 (bảng "Hiện trạng (BA)"): cập nhật 2 dòng -->
| Capacity kho/vị trí | Wave A: soft-warn 3 điểm (detail badge, transfer/QC đích, GRN→STAGING) — hard-block vẫn Wave B |
| Inventory theo vị trí | Wave A: card "Tồn kho theo vị trí" trên `warehouse_detail`, phân trang 30/trang |

<!-- thêm 1 dòng ghi chú ngay dưới bảng "Hiện trạng (BA)" vừa sửa ở trên — không lệch câu chữ
     AC-WH-CAP-01 "capacity is None" trong FSD, cho người đọc wh_plan.md sau này biết deviation
     mà không cần mở lại implementation plan: -->
> Lưu ý kỹ thuật (Wave A): `capacity == 0` được xử lý giống hệt `capacity is None` (không hiện
> badge/cảnh báo) — tránh `ZeroDivisionError`, xem "Ràng buộc chung" trong
> `docs/wh/02_wave_a_implementation_plan.md`.
```
  Trực tiếp `Edit` các dòng tương ứng trong `wh_plan.md` (không phải nội dung để chạy test).

- [ ] **Bước 4: Kiểm tra `CLAUDE.md`/skill file** — Wave A không phát sinh invariant cross-cutting
  mới ngoài phạm vi các quyết định đã ghi trong FSD (badge 3-mức chưa "tái dùng ở module khác" nên
  theo FSD mục 10 chưa cần ghi vào skill file). Không sửa `CLAUDE.md`/`SKILL.md` trong task này trừ
  khi Bước 1 phát hiện 1 bug thật cần invariant mới (nếu có, xử lý qua `debug-co-he-thong` rồi mới
  quay lại đây ghi invariant).

- [ ] **Bước 5: Commit**
```bash
git add wh_plan.md
git commit -m "docs(wh): sync wh_plan.md sau khi trien khai Wave A hardening"
```

---

## Tự rà soát kế hoạch

**Bao phủ spec** (AC/TC → Task):

| AC/TC | Task |
|---|---|
| AC-WH-LOC-01, TC-WH-LOC-001/002/004 | Task 2 |
| AC-WH-LOC-02, TC-WH-LOC-003 | Task 3 |
| AC-WH-CAP-01, TC-WH-CAP-001 | Task 4 |
| AC-WH-CAP-02, TC-WH-CAP-002 | Task 4 |
| AC-WH-CAP-03, TC-WH-CAP-003 | Task 4 |
| AC-WH-CAP-08, TC-WH-CAP-009 | Task 4 |
| AC-WH-CAP-04, TC-WH-CAP-004 | Task 5 |
| AC-WH-CAP-05, TC-WH-CAP-005/006 | Task 6 |
| AC-WH-CAP-06, TC-WH-CAP-007 | Task 7 |
| AC-WH-CAP-07, TC-WH-CAP-008 | Task 8 |
| AC-WH-OPS-01, TC-WH-OPS-001 | Task 9 |
| AC-WH-OPS-02, TC-WH-OPS-002 | Task 9 |
| AC-WH-OPS-03, TC-WH-OPS-003 | Task 9 |
| AC-WH-OPS-04, TC-WH-OPS-004 | Task 10 |
| A4 (staff/location_update/get_scrap_warehouse) | Task 11, 12, 13 |

Không có khoảng trống — mọi AC/TC trong FSD mục 5/6 đều map tới đúng 1 task.

**Quét placeholder**: không có "TBD"/"làm sau"/"giống Task N" nào trong tài liệu — mỗi task có
code thật, test thật.

**Nhất quán kiểu dữ liệu**: `location_capacity_alerts(location) -> list[str]` dùng xuyên suốt
Task 4/5/6/7 không đổi chữ ký; `ops_snapshot(warehouse) -> dict` dùng xuyên suốt Task 9/10; badge
dict luôn có đúng 2 key `css`/`label` ở cả `capacity_badge()` (Task 8) lẫn nơi dùng trong template.

**Điểm làm rõ thêm ngoài FSD** (đã nêu ở Ràng buộc chung, không phải khoảng trống spec): `capacity
== 0` xử lý như `None` (Task 4) — quyết định kỹ thuật cục bộ để tránh `ZeroDivisionError`, không
đổi AC nào, có test riêng.

## Bàn giao để thực thi

Kế hoạch đã lưu tại `docs/wh/02_wave_a_implementation_plan.md`. 2 lựa chọn:

1. **Thực thi tuần tự trong phiên hiện tại** (khuyến nghị — 14 task có phụ thuộc tuyến tính rõ
   ràng qua 4 Phase, ít lợi ích khi chạy song song bằng sub-agent): dùng skill `thuc-thi-va-tdd`,
   từng task một, dừng lại review sau mỗi task.
2. **Thực thi bằng sub-agent**: dùng skill `phan-viec-song-song-agent` — chỉ hợp lý nếu muốn tách
   Phase 1/2/3 cho các agent riêng, nhưng Phase 2 phụ thuộc trực tiếp vào hằng số/hàm Task 4 nên
   không tách độc lập hoàn toàn được; cân nhắc kỹ trước khi chọn hướng này.
