"""Form app catalog (Product/SKU — mục 1c)."""
from django import forms

from .models import Product


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


class ProductForm(forms.ModelForm):
    """Tạo/sửa Product (SKU). Không có business rule tách khoá/mở như Warehouse
    (BR-WM-006) nên ``is_active`` sửa trực tiếp trên form này.
    """

    class Meta:
        model = Product
        fields = ['product_code', 'name', 'category', 'uom', 'min_level', 'max_level', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
