"""Form app shipping: GIN (mục 3b) — tạo GIN (state DRAFT) và đổi batch ở state
PICKING (FR-GIN-03). Transaction thật (FIFO suggest, trừ Inventory/Batch) nằm ở
``shipping.services`` — form ở đây chỉ thu thập input.
"""
from django import forms
from django.forms import inlineformset_factory

from inventory.models import Batch
from warehouse.models import Warehouse

from .models import Gin, GinItem


def _bootstrapify(fields):
    """Gắn class Bootstrap phù hợp từng loại widget (dùng chung cho form CRUD)."""
    for field in fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault('class', 'form-check-input')
        elif isinstance(widget, forms.Select):
            widget.attrs.setdefault('class', 'form-select')
        else:
            widget.attrs.setdefault('class', 'form-control')


class GinForm(forms.ModelForm):
    class Meta:
        model = Gin
        fields = ['warehouse', 'reference_type', 'reference_no', 'notes']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['warehouse'].queryset = Warehouse.objects.filter(
            is_active=True, warehouse_type=Warehouse.WarehouseType.MAIN)
        _bootstrapify(self.fields)


class GinItemForm(forms.ModelForm):
    class Meta:
        model = GinItem
        fields = ['product', 'qty_requested']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


GinItemFormSet = inlineformset_factory(
    Gin, GinItem, form=GinItemForm,
    extra=3, min_num=1, validate_min=True, can_delete=True,
)


class BatchLocationChoiceField(forms.ModelChoiceField):
    """FR-GIN-07: hiển thị kèm vị trí (``batch.location``) trong dropdown chọn
    batch, để người soạn hàng thấy ngay batch thay thế đang nằm ở đâu trong
    kho thay vì chỉ thấy mã lô.
    """

    def label_from_instance(self, obj):
        exp = obj.exp_date or '—'
        return f'{obj.batch_code} — {obj.location} (HSD {exp}, còn {obj.qty_available})'


class GinAllocationOverrideForm(forms.Form):
    """FR-GIN-03: đổi batch khác gợi ý FIFO cho 1 dòng allocation (state PICKING).

    Queryset của ``batch`` giới hạn theo đúng sản phẩm + kho của dòng hàng và
    ``status`` ACTIVE hoặc PARTIAL_USED — cùng tập FIFO-eligible với
    ``inventory.services.suggest_fifo_batches`` (BR-GIN-007 vẫn loại
    QUARANTINE/EXPIRED/PENDING_RECEIPT; bug fix 2026-07-27, xem CLAUDE.md) —
    validate lại lần nữa (product/qty) ở ``shipping.services.override_allocation``
    vì view có thể bị submit ngoài ý (curl trực tiếp), không dựa hoàn toàn vào
    queryset đã lọc.
    """

    batch = BatchLocationChoiceField(queryset=Batch.objects.none(), label='Batch mới')
    reason = forms.CharField(
        label='Lý do đổi batch', widget=forms.Textarea(attrs={'rows': 1}),
    )

    def __init__(self, *args, product=None, warehouse=None, **kwargs):
        super().__init__(*args, **kwargs)
        if product is not None and warehouse is not None:
            self.fields['batch'].queryset = Batch.objects.select_related('location').filter(
                product=product, location__warehouse=warehouse,
                status__in=[Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED],
            ).order_by('exp_date', 'created_at')
        _bootstrapify(self.fields)
