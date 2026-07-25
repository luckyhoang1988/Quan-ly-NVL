"""Form của app warehouse (FR-WM-01, FR-WM-02)."""
from django import forms
from django.contrib.auth import get_user_model

from .models import Location, Warehouse

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


class WarehouseForm(forms.ModelForm):
    """Tạo/sửa kho (FR-WM-01). Không có ``is_active`` ở đây — khoá/mở kho đi qua
    view riêng (``warehouse_deactivate``/``warehouse_activate``) để BR-WM-006 có
    một điểm kiểm tra duy nhất, tránh khoá kho "lọt" qua form sửa thông thường.

    ``warehouse_type`` bất biến sau khi tạo (disabled khi update): đổi loại kho
    tại chỗ sẽ làm sai lệch mọi nơi lọc theo ``warehouse_type=MAIN`` mà không
    qua data migration nào — muốn đổi loại phải tạo kho mới + khoá kho cũ.
    """

    class Meta:
        model = Warehouse
        fields = ['code', 'name', 'address', 'capacity', 'warehouse_type', 'staff']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
        self.fields['staff'].queryset = User.objects.filter(
            department=User.Department.WAREHOUSE, is_active=True,
        ).order_by('username')
        self.fields['staff'].required = False
        if self.instance.pk:
            self.fields['warehouse_type'].disabled = True

    def clean_warehouse_type(self):
        warehouse_type = self.cleaned_data['warehouse_type']
        if self.instance.pk:
            # disabled=True -> Django tự trả về giá trị gốc, không cho POST giả mạo qua.
            return warehouse_type
        if warehouse_type in (Warehouse.WarehouseType.STAGING, Warehouse.WarehouseType.SCRAP):
            label = dict(Warehouse.WarehouseType.choices)[warehouse_type]
            if Warehouse.objects.filter(warehouse_type=warehouse_type, is_active=True).exists():
                raise forms.ValidationError(
                    f'Đã có 1 kho loại "{label}" đang hoạt động — mỗi loại chỉ được tối đa 1 kho hoạt động.')
        return warehouse_type


class LocationForm(forms.ModelForm):
    """Tạo/sửa vị trí lưu trữ trong 1 kho (FR-WM-02).

    ``warehouse`` không phải field của form (gán sẵn trên ``instance`` trước khi
    validate — xem view) nên ``ModelForm.validate_unique()`` mặc định BỎ QUA field
    này khi kiểm tra ``UniqueConstraint(warehouse, code)`` (Django chỉ validate
    field có mặt trong form). Vì vậy tự kiểm tra trùng mã trong ``clean_code``.
    """

    class Meta:
        model = Location
        fields = ['code', 'capacity']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)

    def clean_code(self):
        code = self.cleaned_data['code']
        warehouse = self.instance.warehouse
        qs = Location.objects.filter(warehouse=warehouse, code=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('Mã vị trí đã tồn tại trong kho này.')
        return code
