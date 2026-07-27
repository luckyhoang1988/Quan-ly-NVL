"""Form app stocktake: tạo phiếu kiểm kê (PLANNING, FR-SO-01/FR-SO-07) và nhập
Qty thực tế theo từng lần quét barcode (EXECUTION, FR-SO-02). Transaction thật
(snapshot SKU, ghi nhận Qty thực tế) nằm ở ``stocktake.services`` — form ở đây
chỉ thu thập input.
"""
from django import forms

from warehouse.models import Location, Warehouse

from .models import StocktakeItem


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


class StocktakeSessionForm(forms.Form):
    """FR-SO-01/FR-SO-07: chọn kho (bắt buộc) + vị trí (tuỳ chọn — để trống =
    kiểm toàn kho, xem ``stocktake.services.create_session``).

    ``location`` queryset không lọc theo ``warehouse`` đã chọn (không có JS/HTMX
    cascading dropdown trong project này) nên vẫn liệt kê vị trí của mọi kho —
    ``clean()`` chặn lại tổ hợp kho A + vị trí thuộc kho B (bug fix 2026-07-27,
    xem CLAUDE.md); ``stocktake.services.create_session`` re-check lại constraint
    này lần nữa, không chỉ dựa vào form (cùng convention "form thu thập, service
    validate lại" đã dùng cho ``StockTransferForm``/``GinAllocationOverrideForm``).
    """

    warehouse = forms.ModelChoiceField(queryset=Warehouse.objects.filter(is_active=True), label='Kho')
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True).select_related('warehouse'), required=False,
        label='Vị trí (để trống = kiểm toàn kho)',
    )
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False, label='Ghi chú')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)

    def clean(self):
        cleaned_data = super().clean()
        warehouse = cleaned_data.get('warehouse')
        location = cleaned_data.get('location')
        if warehouse and location and location.warehouse_id != warehouse.id:
            self.add_error('location', 'Vị trí này không thuộc kho đã chọn.')
        return cleaned_data


class StocktakeCountForm(forms.Form):
    """FR-SO-02: 1 lần quét = 1 dòng SKU trong phiếu — nhập mã SKU (barcode) để
    xác định dòng, Qty thực tế đếm được, và lý do nếu có chênh lệch (FR-SO-04,
    validate lại đầy đủ ở ``stocktake.services.record_count``).
    """

    product_code = forms.CharField(label='Quét mã SKU')
    qty_actual = forms.IntegerField(label='Qty thực tế', min_value=0)
    reason = forms.ChoiceField(
        choices=[('', '—')] + StocktakeItem.Reason.choices, required=False, label='Lý do chênh lệch',
    )

    def __init__(self, *args, session=None, **kwargs):
        self._session = session
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)

    def clean_product_code(self):
        code = self.cleaned_data['product_code'].strip()
        try:
            # Gắn trực tiếp lên form để view dùng form.item, tránh phải truy
            # vấn lại lần 2 (cùng convention với GinAllocationOverrideForm).
            self.item = self._session.items.select_related('product').get(product__product_code=code)
        except StocktakeItem.DoesNotExist:
            raise forms.ValidationError(f'SKU "{code}" không có trong phiếu kiểm kê này.')
        return code
