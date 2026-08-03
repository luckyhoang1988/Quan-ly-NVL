"""Form app purchasing (PO stub — mục 1e)."""
from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory

from accounts.models import User
from catalog.models import Product
from partners.models import Supplier
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
        fields = ['supplier', 'expected_delivery_date']
        widgets = {
            'expected_delivery_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Chỉ cho chọn NCC đang ACTIVE khi tạo/sửa PO mới. Cộng thêm NCC hiện tại
        # của instance (khi sửa) để không vỡ PO cũ nếu NCC đã chuyển INACTIVE/
        # SUSPENDED sau đó — cùng convention với GrnForm.po ở receiving/forms.py.
        queryset = Q(status=Supplier.Status.ACTIVE)
        if self.instance.pk and self.instance.supplier_id:
            queryset |= Q(pk=self.instance.supplier_id)
        self.fields['supplier'].queryset = Supplier.objects.filter(queryset).distinct()
        _bootstrapify(self.fields)


class PurchaseOrderItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderItem
        fields = ['product', 'qty_ordered', 'unit_price']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Q(is_active=True)
        if self.instance.pk and self.instance.product_id:
            queryset |= Q(pk=self.instance.product_id)
        self.fields['product'].queryset = Product.objects.filter(queryset).distinct()
        _bootstrapify(self.fields)


PurchaseOrderItemFormSet = inlineformset_factory(
    PurchaseOrder, PurchaseOrderItem, form=PurchaseOrderItemForm,
    extra=3, min_num=1, validate_min=True, can_delete=True,
)


class PurchaseRequestForm(forms.ModelForm):
    class Meta:
        model = PurchaseRequest
        fields = ['warehouse', 'assigned_to', 'cost_center', 'project', 'note']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['warehouse'].queryset = Warehouse.objects.filter(
            is_active=True, warehouse_type=Warehouse.WarehouseType.MAIN)
        self.fields['assigned_to'].queryset = User.objects.filter(
            department=User.Department.PURCHASING, is_active=True)
        self.fields['assigned_to'].required = False
        _bootstrapify(self.fields)


class ProductSelectWithCategory(forms.Select):
    """Gắn ``data-category`` lên mỗi ``<option>`` để JS ở ``pr_form.html`` tự
    điền ``budget_category`` khi chọn sản phẩm. ``value`` mà Django truyền vào
    đây (từ Django 3.1) là ``ModelChoiceIteratorValue``, không phải pk thô — nó
    đã bọc sẵn ``.instance`` (object ``Product`` fetch lúc dựng choices), nên
    dùng thẳng ``value.instance`` thay vì query lại theo pk (query lại sẽ crash:
    ``ModelChoiceIteratorValue`` không định nghĩa ``__int__``).
    """

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, 'instance', None)
        if instance is not None:
            option['attrs']['data-category'] = instance.category
        return option


class PurchaseRequestItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseRequestItem
        fields = [
            'product', 'qty_requested', 'required_date', 'currency', 'estimated_unit_price',
            'budget_category', 'non_catalog_name', 'non_catalog_uom', 'non_catalog_note',
        ]
        widgets = {
            'product': ProductSelectWithCategory,
            'required_date': forms.DateInput(attrs={'type': 'date'}),
            'non_catalog_note': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Q(is_active=True)
        if self.instance.pk and self.instance.product_id:
            queryset |= Q(pk=self.instance.product_id)
        self.fields['product'].queryset = Product.objects.filter(queryset).distinct()
        _bootstrapify(self.fields)


PurchaseRequestItemFormSet = inlineformset_factory(
    PurchaseRequest, PurchaseRequestItem, form=PurchaseRequestItemForm,
    extra=3, min_num=1, validate_min=True, can_delete=True,
)


class PurchaseRequestForwardForm(forms.Form):
    staff = forms.ModelChoiceField(
        queryset=User.objects.filter(department=User.Department.PURCHASING, is_active=True),
        label='Chuyển tiếp cho nhân viên')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


class PurchaseRequestRejectForm(forms.Form):
    reject_reason = forms.CharField(
        max_length=255, widget=forms.Textarea(attrs={'rows': 2}), label='Lý do từ chối')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


class PurchaseOrderCloseForm(forms.Form):
    """Không bắt buộc ở mức field (đóng từ RECEIVED không cần lý do) — ``clean()``
    tự bắt buộc khi ``po.status`` là SENT/PARTIAL_RECEIVED, dựa vào ``po`` truyền
    vào ``__init__``. Service ``close_po`` tự re-validate lại y hệt logic này,
    không chỉ tin form (theo đúng pattern "form filter — service re-validate" đã
    dùng khắp dự án).
    """
    close_reason = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 2}), label='Lý do đóng sớm')

    def __init__(self, *args, po=None, **kwargs):
        self.po = po
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)

    def clean(self):
        cleaned_data = super().clean()
        close_reason = cleaned_data.get('close_reason', '').strip()
        if self.po and self.po.status != PurchaseOrder.Status.RECEIVED and not close_reason:
            self.add_error('close_reason', 'Bắt buộc nhập lý do khi đóng PO trước khi NCC giao đủ hàng.')
        return cleaned_data
