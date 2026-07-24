"""Form app receiving: GRN (mục 2a) — state DRAFT (tạo/sửa) và PENDING_QC (nhập Qty thực nhận)."""
from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory

from .models import Grn, GrnItem

User = get_user_model()


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


class GrnForm(forms.ModelForm):
    class Meta:
        model = Grn
        fields = ['po', 'supplier', 'grn_date', 'expected_arrival_date', 'notes']
        widgets = {
            'grn_date': forms.DateInput(attrs={'type': 'date'}),
            'expected_arrival_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


class GrnItemForm(forms.ModelForm):
    class Meta:
        model = GrnItem
        fields = ['product', 'qty_ordered', 'unit_price', 'mfg_date', 'exp_date', 'batch_code']
        widgets = {
            'mfg_date': forms.DateInput(attrs={'type': 'date'}),
            'exp_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


GrnItemFormSet = inlineformset_factory(
    Grn, GrnItem, form=GrnItemForm,
    extra=3, min_num=1, validate_min=True, can_delete=True,
)


class ReceiveQtyItemForm(forms.ModelForm):
    """State PENDING_QC: nhân viên Kho chỉ nhập Qty thực tế nhận (mục 2a Workflow States)."""

    class Meta:
        model = GrnItem
        fields = ['qty_received']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


ReceiveQtyFormSet = inlineformset_factory(
    Grn, GrnItem, form=ReceiveQtyItemForm,
    extra=0, can_delete=False,
)


class SubmitToQcForm(forms.Form):
    """Chọn người phụ trách QC khi bấm "Submit to QC" (PENDING_QC -> QC_IN_PROGRESS)."""

    inspector = forms.ModelChoiceField(
        queryset=User.objects.filter(role=User.Role.QC, is_active=True),
        label='QC phụ trách',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
