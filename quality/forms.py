"""Form app quality: nhập kết quả QC PASS/FAIL/PARTIAL_PASS (mục 2c) ở state
QC_IN_PROGRESS. Transaction thật (Batch/Inventory/GRN) nằm ở ``quality.services``
— form ở đây chỉ thu thập input (location, lý do fail, qty_pass từng item).
"""
from django import forms
from django.forms import inlineformset_factory

from receiving.models import Grn, GrnItem
from warehouse.models import Location, Warehouse

from .models import QcCriteria, QcInspection, QcInspectionItem


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


class QcResultForm(forms.Form):
    """Vị trí lưu kho cho Batch mới (ACTIVE/QUARANTINE) + lý do khi FAIL."""

    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True, warehouse__warehouse_type=Warehouse.WarehouseType.MAIN),
        label='Vị trí lưu kho', required=False,
        help_text='Bắt buộc khi kết quả là Pass hoặc Partial Pass (cần chỗ đặt Batch, chỉ Kho thành phẩm).',
    )
    reason = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 2}),
        label='Lý do (bắt buộc nếu Fail)',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


class QcItemResultForm(forms.ModelForm):
    """Qty đạt QC của từng item — dùng cho action Partial Pass (0 <= qty_pass <= qty_received)."""

    class Meta:
        model = GrnItem
        fields = ['qty_pass']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
        self.fields['qty_pass'].required = False
        if self.instance.pk:
            self.fields['qty_pass'].widget.attrs['max'] = self.instance.qty_received
            if self.instance.qty_pass is None:
                self.initial['qty_pass'] = self.instance.qty_received

    def clean_qty_pass(self):
        qty_pass = self.cleaned_data.get('qty_pass')
        if qty_pass is not None and qty_pass > self.instance.qty_received:
            raise forms.ValidationError('qty_pass không được lớn hơn qty_received.')
        return qty_pass


QcItemResultFormSet = inlineformset_factory(
    Grn, GrnItem, form=QcItemResultForm,
    extra=0, can_delete=False,
)


class QcInspectionItemForm(forms.ModelForm):
    """Kết quả PASS/FAIL của 1 tiêu chuẩn QC trên 1 GrnItem (FR-QC-03).

    ``criteria_name``/``expected_value`` là text tự do (snapshot, không FK tới
    ``QcCriteria`` — xem docstring ``quality.models``), inspector tự đối chiếu
    với bảng tiêu chuẩn tham khảo hiển thị cùng trang.
    """

    class Meta:
        model = QcInspectionItem
        fields = ['grn_item', 'criteria_name', 'expected_value', 'actual_value', 'result', 'notes', 'image']

    def __init__(self, *args, grn=None, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
        if grn is not None:
            self.fields['grn_item'].queryset = grn.items.all()


QcInspectionItemFormSet = inlineformset_factory(
    QcInspection, QcInspectionItem, form=QcInspectionItemForm,
    extra=3, can_delete=True,
)


class QcCriteriaForm(forms.ModelForm):
    """Tạo/sửa tiêu chuẩn QC master data (FR-QC-02)."""

    class Meta:
        model = QcCriteria
        fields = ['category', 'name', 'pass_rule', 'fail_rule', 'reference_image', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
