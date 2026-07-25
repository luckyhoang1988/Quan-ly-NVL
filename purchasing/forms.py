"""Form app purchasing (PO stub — mục 1e)."""
from django import forms
from django.forms import inlineformset_factory

from warehouse.models import Warehouse

from .models import PurchaseOrder, PurchaseOrderItem, PurchaseRequest, PurchaseRequestItem


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


class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ['po_no', 'supplier', 'expected_delivery_date']
        widgets = {
            'expected_delivery_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


class PurchaseOrderItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderItem
        fields = ['product', 'qty_ordered', 'unit_price']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


PurchaseOrderItemFormSet = inlineformset_factory(
    PurchaseOrder, PurchaseOrderItem, form=PurchaseOrderItemForm,
    extra=3, min_num=1, validate_min=True, can_delete=True,
)


class PurchaseRequestForm(forms.ModelForm):
    class Meta:
        model = PurchaseRequest
        fields = ['warehouse', 'note']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['warehouse'].queryset = Warehouse.objects.filter(
            is_active=True, warehouse_type=Warehouse.WarehouseType.MAIN)
        _bootstrapify(self.fields)


class PurchaseRequestItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseRequestItem
        fields = ['product', 'qty_requested']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


PurchaseRequestItemFormSet = inlineformset_factory(
    PurchaseRequest, PurchaseRequestItem, form=PurchaseRequestItemForm,
    extra=3, min_num=1, validate_min=True, can_delete=True,
)


class PurchaseRequestRejectForm(forms.Form):
    reject_reason = forms.CharField(
        max_length=255, widget=forms.Textarea(attrs={'rows': 2}), label='Lý do từ chối')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
