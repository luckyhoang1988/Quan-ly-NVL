"""Form của app accounts."""
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

User = get_user_model()


class LoginForm(AuthenticationForm):
    """Login form (FR-USER-03) — thêm class Bootstrap cho widget mặc định."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update(
            {'class': 'form-control', 'autofocus': True}
        )
        self.fields['password'].widget.attrs.update({'class': 'form-control'})


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


class UserCreateForm(forms.ModelForm):
    """Admin tạo user (FR-USER-01 CREATE).

    KHÔNG có ô mật khẩu: mật khẩu tạm được sinh tự động trong view rồi gửi email —
    admin không tự đặt (tránh mật khẩu yếu/đoán được, buộc user đổi khi đăng nhập).
    ``email`` bắt buộc vì là nơi nhận mật khẩu tạm; ``role`` bắt buộc để user mới có
    quyền rõ ràng ngay.
    """

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'role']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True
        self.fields['role'].required = True
        _bootstrapify(self.fields)


class UserUpdateForm(forms.ModelForm):
    """Admin sửa user (FR-USER-01 UPDATE): đổi role, deactivate (is_active), thông tin.

    Không cho sửa ``username`` (khoá định danh) và không đụng mật khẩu ở đây.
    "Gán warehouse" (theo BACKLOG) hoãn tới khi có app warehouse (mục 1b).
    """

    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'role', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrapify(self.fields)
