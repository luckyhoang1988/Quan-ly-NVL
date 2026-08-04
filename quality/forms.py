"""Form app quality: nhập kết quả QC PASS/FAIL/PARTIAL_PASS (mục 2c) ở state
QC_IN_PROGRESS. Transaction thật (Batch/Inventory/GRN) nằm ở ``quality.services``
— form ở đây chỉ thu thập input (location, lý do fail, qty_pass từng item).
"""
from django import forms
from django.forms import inlineformset_factory

from accounts.models import User
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
        queryset=Location.objects.filter(
            is_active=True, warehouse__is_active=True,
            warehouse__warehouse_type=Warehouse.WarehouseType.MAIN,
        ),
        label='Vị trí lưu kho', required=False,
        help_text='Bắt buộc khi kết quả là Pass hoặc Partial Pass (cần chỗ đặt Batch, chỉ Kho thành phẩm).',
    )
    reason = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 2}),
        label='Lý do (bắt buộc nếu Fail)',
    )
    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.filter(department=User.Department.WAREHOUSE, is_active=True),
        label='Người nhận chỉ định (bàn giao kho)', required=False,
        help_text='Chỉ áp dụng khi Pass/Partial Pass. Bỏ trống thì báo toàn bộ nhân viên phụ trách kho đích.',
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


class QcInspectionItemForm(forms.ModelForm):
    """Kết quả PASS/FAIL của 1 tiêu chuẩn QC trên 1 GrnItem (FR-QC-03).

    ``criteria_name``/``expected_value`` là text tự do (snapshot, không FK tới
    ``QcCriteria`` — xem docstring ``quality.models``), inspector tự đối chiếu
    với bảng tiêu chuẩn tham khảo hiển thị cùng trang.
    """

    class Meta:
        model = QcInspectionItem
        fields = ['grn_item', 'criteria_name', 'expected_value', 'actual_value', 'result', 'notes', 'image']
        widgets = {'grn_item': GrnItemSelectWithCategory}

    def __init__(self, *args, grn=None, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
        if grn is not None:
            self.fields['grn_item'].queryset = grn.items.all()


QcInspectionItemFormSet = inlineformset_factory(
    QcInspection, QcInspectionItem, form=QcInspectionItemForm,
    extra=3, can_delete=True,
)


class QcOverrideForm(forms.Form):
    """QC approval override (BACKLOG mục 2b) — Supervisor ghi chú lý do xem lại
    1 kết quả QC đã quyết định. Chỉ annotation nên bắt buộc phải có lý do.
    """

    override_note = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}), label='Lý do override',
        help_text='Bắt buộc — ghi rõ vì sao xem lại kết quả này (không đảo ngược Batch/Inventory đã tạo).',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


class QcCriteriaForm(forms.ModelForm):
    """Tạo/sửa tiêu chuẩn QC master data (FR-QC-02)."""

    class Meta:
        model = QcCriteria
        fields = ['category', 'name', 'pass_rule', 'fail_rule', 'reference_image', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
