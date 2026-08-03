"""Form app purchasing (PO stub — mục 1e)."""
from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory
from django.forms.models import BaseInlineFormSet

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


class PurchaseOrderItemFromPrForm(forms.ModelForm):
    """PO nguồn FROM_PR: product/qty_ordered chỉ đổi được qua create_allocation()/
    release_allocation() (mục 4 điểm 4) — disabled=True ở tầng Form (không chỉ
    HTML readonly) khiến Django luôn dùng giá trị initial trong cleaned_data,
    bỏ qua hoàn toàn giá trị POST.
    """
    product = forms.ModelChoiceField(queryset=Product.objects.all(), disabled=True, required=False)
    qty_ordered = forms.IntegerField(disabled=True, required=False)

    class Meta:
        model = PurchaseOrderItem
        fields = ['product', 'qty_ordered', 'unit_price']


class _PoItemFromPrFormSet(BaseInlineFormSet):
    """Vì ``product`` bị khoá (``disabled=True``), 2 form cùng trỏ 1
    ``PurchaseOrderItem`` (submit trùng pk — tampering) sẽ mang cùng giá trị
    ``product`` như nhau, và ``BaseModelFormSet.validate_unique()`` mặc định
    của Django so sánh ``cleaned_data`` GIỮA CÁC FORM để bắt lỗi trùng
    ``unique_together``/``UniqueConstraint`` — sẽ hiểu nhầm đây là 2 dòng MỚI
    đụng ràng buộc unique, và chặn ở tầng ``formset.is_valid()`` với thông báo
    Django mặc định, TRƯỚC KHI ``po_update`` kịp chạy check ``submitted_pks``
    riêng (rõ ràng hơn, đã có ở view). Tắt hẳn check này ở đây — check
    ``submitted_pks`` trong ``po_update`` đã phủ đúng bất biến cần thiết cho
    formset này (không có dòng nào thật sự MỚI được tạo qua form này, mọi pk
    đều phải khớp 1 ``PurchaseOrderItem`` đã tồn tại).
    """

    def validate_unique(self):
        pass


PurchaseOrderItemFromPrFormSet = inlineformset_factory(
    PurchaseOrder, PurchaseOrderItem, form=PurchaseOrderItemFromPrForm,
    formset=_PoItemFromPrFormSet, extra=0, can_delete=True,
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


class PrItemMapProductForm(forms.Form):
    existing_product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True), required=False, label='Chọn sản phẩm có sẵn')
    new_product_code = forms.CharField(max_length=50, required=False, label='Mã sản phẩm mới')
    new_product_name = forms.CharField(max_length=200, required=False, label='Tên sản phẩm mới')
    new_product_uom = forms.CharField(max_length=20, required=False, label='Đơn vị tính')
    new_product_category = forms.CharField(max_length=100, required=False, label='Danh mục sản phẩm mới')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)

    def clean(self):
        cleaned_data = super().clean()
        existing = cleaned_data.get('existing_product')
        # 4 field (kể cả new_product_category) bắt buộc CÙNG NHAU khi tạo sản
        # phẩm mới — nếu category bị bỏ trống, Product mới tạo ra sẽ không có gì
        # để PurchaseRequestItem.clean() (Task 2.6) fallback budget_category cho
        # các dòng PR sau này dùng sản phẩm mới đó.
        new_fields_filled = all([
            cleaned_data.get('new_product_code'), cleaned_data.get('new_product_name'),
            cleaned_data.get('new_product_uom'), cleaned_data.get('new_product_category'),
        ])
        if existing and new_fields_filled:
            raise forms.ValidationError('Chỉ chọn 1 trong 2: sản phẩm có sẵn HOẶC tạo sản phẩm mới.')
        if not existing and not new_fields_filled:
            raise forms.ValidationError(
                'Phải chọn 1 sản phẩm có sẵn, hoặc điền đủ Mã/Tên/Đơn vị tính/Danh mục để tạo mới.')
        new_code = cleaned_data.get('new_product_code')
        if not existing and new_code and Product.objects.filter(product_code=new_code).exists():
            self.add_error('new_product_code', 'Mã sản phẩm đã tồn tại.')
        return cleaned_data


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
