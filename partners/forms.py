"""Form app partners (Supplier — mục 1d)."""
from django import forms

from .models import Supplier


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


class SupplierForm(forms.ModelForm):
    """Tạo/sửa Supplier. Không có business rule tách khoá/mở như Warehouse
    (BR-WM-006) nên ``is_active`` sửa trực tiếp trên form này.
    """

    class Meta:
        model = Supplier
        fields = [
            'supplier_code', 'name', 'international_name', 'supplier_group',
            'tax_code', 'registered_address', 'delivery_address', 'website',
            'contact_name', 'contact_title', 'contact_phone', 'contact_email',
            'lead_time_days', 'qty_tolerance_percent', 'payment_terms', 'credit_limit', 'currency',
            'status', 'internal_note',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
