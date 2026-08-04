"""Form app inventory: điều chuyển tồn kho (FR-WM-06). Transaction thật (tách
batch, cập nhật Inventory 2 đầu) nằm ở ``inventory.services.transfer_stock`` —
form ở đây chỉ thu thập input, cùng convention với ``shipping.forms``.
"""
from django import forms
from django.db import models

from accounts.models import User
from warehouse.models import Location, Warehouse

from .models import Batch


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


class StockTransferForm(forms.Form):
    """Thu thập input điều chuyển — validate đầy đủ (qty available, cùng vị
    trí, is_active) nằm ở ``transfer_stock()`` vì view có thể bị submit ngoài
    ý, không dựa hoàn toàn vào queryset đã lọc ở đây (cùng lý do với
    ``shipping.forms.GinAllocationOverrideForm``).
    """

    batch = forms.ModelChoiceField(
        queryset=Batch.objects.filter(status__in=[
            Batch.Status.ACTIVE, Batch.Status.PARTIAL_USED, Batch.Status.PENDING_RECEIPT,
        ])
        .select_related('product', 'location__warehouse').order_by('exp_date', 'created_at'),
        label='Batch nguồn',
    )
    to_location = forms.ModelChoiceField(
        queryset=Location.objects.filter(
            is_active=True, warehouse__is_active=True,
            warehouse__warehouse_type=Warehouse.WarehouseType.MAIN,
        ).select_related('warehouse'),
        label='Vị trí đích',
    )
    qty = forms.IntegerField(min_value=1, label='Số lượng chuyển')
    note = forms.CharField(required=False, label='Ghi chú', widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)


class QuarantineDisposeForm(forms.Form):
    """Wave 1 QC disposition trên lô QUARANTINE — validate đầy đủ ở service."""

    class Disposition(models.TextChoices):
        WRITEOFF = 'writeoff', 'Huỷ tồn (scrap write-off)'
        RETURN = 'return', 'Trả NCC'
        RELEASE = 'release', 'Phóng thích về kho chính'

    disposition = forms.ChoiceField(choices=Disposition.choices, label='Hành động')
    qty = forms.IntegerField(min_value=1, label='Số lượng')
    reason = forms.CharField(label='Lý do', widget=forms.Textarea(attrs={'rows': 2}))
    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(
            is_active=True, warehouse__is_active=True,
            warehouse__warehouse_type=Warehouse.WarehouseType.MAIN,
        ).select_related('warehouse'),
        required=False, label='Vị trí đích (khi phóng thích)',
    )
    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.none(), required=False, label='Người nhận bàn giao (tuỳ chọn)',
    )

    def __init__(self, *args, batch=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.batch = batch
        self.user = user
        if batch is not None:
            self.fields['qty'].initial = batch.qty_available
            self.fields['qty'].widget.attrs['max'] = batch.qty_available
        # Restrict disposition choices by permission
        choices = []
        if user is not None and user.can('approve', 'qc'):
            choices.append((self.Disposition.WRITEOFF, self.Disposition.WRITEOFF.label))
            choices.append((self.Disposition.RETURN, self.Disposition.RETURN.label))
        if user is not None and user.can('override', 'qc'):
            choices.append((self.Disposition.RELEASE, self.Disposition.RELEASE.label))
        self.fields['disposition'].choices = choices
        self.fields['assigned_to'].queryset = User.objects.filter(
            is_active=True, department=User.Department.WAREHOUSE,
        ).order_by('username')
        _bootstrapify(self.fields)

    def clean(self):
        cleaned = super().clean()
        disp = cleaned.get('disposition')
        if disp == self.Disposition.RELEASE and not cleaned.get('location'):
            self.add_error('location', 'Bắt buộc chọn vị trí đích khi phóng thích.')
        if self.batch and cleaned.get('qty') and cleaned['qty'] > self.batch.qty_available:
            self.add_error('qty', f'Không vượt quá {self.batch.qty_available}.')
        return cleaned
