"""Form app receiving: GRN (mục 2a) — state DRAFT (tạo/sửa) và PENDING_QC (nhập Qty thực nhận)."""
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Sum
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


class BaseGrnItemFormSet(forms.BaseInlineFormSet):
    """BR mục 2a "Qty validation" (FR-GRN-07): tổng ``qty_ordered`` của mọi GRN
    chưa bị QC REJECT, cho cùng 1 sản phẩm trên cùng 1 PO, không được vượt
    ``PurchaseOrderItem.qty_ordered``. Item REJECTED bị loại khỏi tổng để PO có
    thể được giao lại (re-ship) ở GRN kế tiếp — đây là cơ chế cho phép nhận
    nhiều đợt/1 PO (FR-GRN-04) mà không vượt quá Qty đã đặt.
    """

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        po = self.instance.po
        if po is None:
            return

        totals = {}
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or form.cleaned_data.get('DELETE'):
                continue
            product = form.cleaned_data.get('product')
            qty_ordered = form.cleaned_data.get('qty_ordered')
            if not product or not qty_ordered:
                continue
            totals[product] = totals.get(product, 0) + qty_ordered

        po_qty_by_product = {item.product_id: item.qty_ordered for item in po.items.all()}
        for product, new_qty in totals.items():
            po_qty = po_qty_by_product.get(product.pk)
            if po_qty is None:
                raise forms.ValidationError(f'Sản phẩm "{product}" không có trong PO "{po.po_no}".')

            already = GrnItem.objects.filter(
                grn__po=po, product=product).exclude(status=GrnItem.Status.REJECTED)
            if self.instance.pk:
                already = already.exclude(grn=self.instance)
            already_qty = already.aggregate(total=Sum('qty_ordered'))['total'] or 0

            remaining = po_qty - already_qty
            if new_qty > remaining:
                raise forms.ValidationError(
                    f'Qty đặt cho "{product}" ({new_qty}) vượt quá Qty PO "{po.po_no}" còn lại ({remaining}).')


GrnItemFormSet = inlineformset_factory(
    Grn, GrnItem, form=GrnItemForm, formset=BaseGrnItemFormSet,
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
