"""Form app inventory: điều chuyển tồn kho (FR-WM-06). Transaction thật (tách
batch, cập nhật Inventory 2 đầu) nằm ở ``inventory.services.transfer_stock`` —
form ở đây chỉ thu thập input, cùng convention với ``shipping.forms``.
"""
from django import forms

from warehouse.models import Location, Warehouse

from .models import Batch


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


class StockTransferForm(forms.Form):
    """Thu thập input điều chuyển — validate đầy đủ (qty available, cùng vị
    trí, is_active) nằm ở ``transfer_stock()`` vì view có thể bị submit ngoài
    ý, không dựa hoàn toàn vào queryset đã lọc ở đây (cùng lý do với
    ``shipping.forms.GinAllocationOverrideForm``).
    """

    batch = forms.ModelChoiceField(
        queryset=Batch.objects.filter(status__in=[
            Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED, Batch.Status.PENDING_RECEIPT,
        ])
        .select_related('product', 'location__warehouse').order_by('exp_date', 'created_at'),
        label='Batch nguồn',
    )
    to_location = forms.ModelChoiceField(
        queryset=Location.objects.filter(
            is_active=True, warehouse__is_active=True,
            warehouse__warehouse_type=Warehouse.WarehouseType.MAIN,
        ).select_related('warehouse'),
        label='Vị trí đích',
    )
    qty = forms.IntegerField(min_value=1, label='Số lượng chuyển')
    note = forms.CharField(required=False, label='Ghi chú', widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
