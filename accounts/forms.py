"""Form của app accounts."""
from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm
from django.core.cache import cache
from django.core.exceptions import ValidationError

from .audit import client_ip, log_action
from .models import AuditLog

User = get_user_model()

# Chặn brute-force đăng nhập (SEC): tối đa N lần sai/IP trong 1 cửa sổ thời
# gian — dùng cache có sẵn (LocMemCache mặc định, không cần Redis) thay vì
# thêm dependency mới (django-ratelimit/axes).
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 900  # 15 phút


class LoginForm(AuthenticationForm):
    """Login form (FR-USER-03) — thêm class Bootstrap cho widget mặc định +
    rate-limit theo IP để chặn brute-force (đếm bằng cache, reset khi đăng
    nhập thành công).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update(
            {'class': 'form-control', 'autofocus': True}
        )
        self.fields['password'].widget.attrs.update({'class': 'form-control'})
        # Cờ riêng cho template (login.html) phân biệt "sai mật khẩu" (thông
        # báo chung chung, tránh lộ thông tin) với "bị chặn do rate-limit"
        # (cần thông báo rõ, không phải lỗi gõ sai) — không dò text lỗi.
        self.rate_limited = False

    def clean(self):
        ip = client_ip(self.request) if self.request else None
        cache_key = f'login_rate_limit:{ip}' if ip else None
        if cache_key and cache.get(cache_key, 0) >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
            self.rate_limited = True
            # L5: bị chặn ở đây nghĩa là không bao giờ chạm authenticate() thật của
            # Django -> signal user_login_failed (accounts/signals.py) không phát,
            # nên audit trail trước đây "mù" hoàn toàn với các lượt bị block —
            # chỉ thấy các lượt sai thật (dưới ngưỡng). Ghi thủ công tại đây.
            username = (self.data or {}).get('username', '')
            log_action(
                None,
                AuditLog.Action.LOGIN_FAILED,
                description=f'Đăng nhập bị chặn do rate-limit: {username}'.strip(),
                ip_address=ip,
            )
            raise ValidationError(
                'Đăng nhập sai quá nhiều lần. Vui lòng thử lại sau 15 phút.',
                code='rate_limited',
            )
        try:
            cleaned_data = super().clean()
        except ValidationError:
            if cache_key:
                cache.set(cache_key, cache.get(cache_key, 0) + 1, LOGIN_RATE_LIMIT_WINDOW_SECONDS)
            raise
        # super().clean() không raise cả khi username/password rỗng (AuthenticationForm
        # chỉ gọi authenticate() lúc cả 2 field có giá trị) — chỉ reset counter khi thực
        # sự đăng nhập thành công (self.get_user() trả về user), tránh spam form thiếu
        # mật khẩu để tự xoá rate-limit của chính IP mình.
        if cache_key and self.get_user():
            cache.delete(cache_key)
        return cleaned_data


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


# --- Mật khẩu ---
#
# Django dịch sẵn "This field is required." qua LANGUAGE_CODE='vi' (catalog forms lõi
# khá đầy đủ), nhưng catalog vi.po lại KHÔNG có bản dịch cho các chuỗi của
# contrib.auth.forms (PasswordChangeForm/SetPasswordForm) lẫn contrib.auth.password_validation
# (MinimumLengthValidator...) — nên các label/help_text/error_messages liên quan mật khẩu
# phải khai báo tiếng Việt tường minh ở đây thay vì trông chờ LANGUAGE_CODE.

_PASSWORD_ERROR_MESSAGES_VI = {
    'password_too_short': 'Mật khẩu quá ngắn, phải có ít nhất 8 ký tự.',
    'password_too_similar': 'Mật khẩu quá giống với thông tin cá nhân của bạn.',
    'password_too_common': 'Mật khẩu này quá phổ biến, dễ bị đoán ra.',
    'password_entirely_numeric': 'Mật khẩu không được chỉ gồm toàn chữ số.',
}

_PASSWORD_HELP_TEXT = 'Tối thiểu 8 ký tự, không quá đơn giản/phổ biến và không được chỉ toàn chữ số.'


class _VietnamesePasswordValidationMixin:
    """Dịch lỗi độ mạnh mật khẩu (``AUTH_PASSWORD_VALIDATORS``) sang tiếng Việt theo
    ``code`` của từng validator, vì bản thân message gốc không có trong catalog vi.po."""

    def validate_password_for_user(self, user, password_field_name='password2'):
        password = self.cleaned_data.get(password_field_name)
        if not password:
            return
        try:
            password_validation.validate_password(password, user)
        except ValidationError as error:
            messages = [_PASSWORD_ERROR_MESSAGES_VI.get(e.code, e.message) for e in error.error_list]
            self.add_error(password_field_name, messages)


class _ManagerRequiresDepartmentMixin:
    """M6: ``is_manager=True`` mà ``department`` bỏ trống thì
    ``user.is_department_manager(dept)`` luôn False với MỌI ``dept`` (so sánh
    ``self.department == department`` không bao giờ khớp chuỗi rỗng) — thành
    một "quản lý phòng ban ma": ``can_view_audit_log()`` (``is_manager`` trần,
    không phân biệt phòng ban) vẫn cho vào tra cứu audit log, nhưng không duyệt
    được phiếu nào của phòng ban cụ thể nào cả. Bắt buộc chọn ``department``
    ngay khi tick ``is_manager`` để tránh trạng thái nửa vời này."""

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('is_manager') and not cleaned_data.get('department'):
            self.add_error(
                'department', 'Phải chọn phòng ban khi đánh dấu là quản lý phòng ban.')
        return cleaned_data


class UserCreateForm(_VietnamesePasswordValidationMixin, _ManagerRequiresDepartmentMixin, forms.ModelForm):
    """Admin tạo user (FR-USER-01 CREATE).

    Admin tự đặt mật khẩu ban đầu cho user (không còn sinh tự động) — validate độ
    mạnh qua ``AUTH_PASSWORD_VALIDATORS`` giống ``WmsSetPasswordForm``. ``email`` bắt
    buộc để gửi thông báo tài khoản; ``role`` bắt buộc để user mới có quyền rõ ràng ngay.
    """

    password1 = forms.CharField(
        label='Mật khẩu', widget=forms.PasswordInput, help_text=_PASSWORD_HELP_TEXT,
    )
    password2 = forms.CharField(
        label='Xác nhận mật khẩu', widget=forms.PasswordInput,
        help_text='Nhập lại đúng mật khẩu ở trên.',
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'role', 'department', 'is_manager']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True
        self.fields['role'].required = True
        _bootstrapify(self.fields)

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise ValidationError('Hai mật khẩu không khớp nhau.', code='password_mismatch')
        return password2

    def _post_clean(self):
        super()._post_clean()
        # self.instance đã được gán username/email/... tại đây nên validate độ mạnh
        # (vd UserAttributeSimilarityValidator) so sánh được với thông tin user mới.
        self.validate_password_for_user(self.instance, password_field_name='password2')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class UserUpdateForm(_ManagerRequiresDepartmentMixin, forms.ModelForm):
    """Admin sửa user (FR-USER-01 UPDATE): đổi role, deactivate (is_active), thông tin.

    Không cho sửa ``username`` (khoá định danh) và không đụng mật khẩu ở đây.
    "Gán warehouse" (theo BACKLOG) hoãn tới khi có app warehouse (mục 1b).
    """

    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'role', 'department', 'is_manager', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # L6: model field `role` có blank=True (để cho phép seed/migration tạo user
        # chưa gán role) nhưng form sửa không nên để admin bỏ trống role của 1 user
        # đang tồn tại — `codenames_for_role('')` trả về rỗng (mất hết quyền CRUD,
        # chỉ còn quyền xem menu), khác hẳn ý định thật là "chưa phân loại". Mirror
        # UserCreateForm đã required=True sẵn.
        self.fields['role'].required = True
        _bootstrapify(self.fields)


class WmsSetPasswordForm(_VietnamesePasswordValidationMixin, SetPasswordForm):
    """Admin đặt mật khẩu mới cho user khác — không cần nhập mật khẩu cũ."""

    error_messages = {
        **SetPasswordForm.error_messages,
        'password_mismatch': 'Hai mật khẩu không khớp nhau.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['new_password1'].label = 'Mật khẩu mới'
        self.fields['new_password1'].help_text = _PASSWORD_HELP_TEXT
        self.fields['new_password2'].label = 'Xác nhận mật khẩu mới'
        self.fields['new_password2'].help_text = 'Nhập lại đúng mật khẩu mới ở trên.'
        _bootstrapify(self.fields)


class WmsPasswordChangeForm(_VietnamesePasswordValidationMixin, PasswordChangeForm):
    """User tự đổi mật khẩu của chính mình — yêu cầu nhập đúng mật khẩu hiện tại."""

    error_messages = {
        **PasswordChangeForm.error_messages,
        'password_mismatch': 'Hai mật khẩu không khớp nhau.',
        'password_incorrect': 'Mật khẩu hiện tại không đúng. Vui lòng nhập lại.',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].label = 'Mật khẩu hiện tại'
        self.fields['new_password1'].label = 'Mật khẩu mới'
        self.fields['new_password1'].help_text = _PASSWORD_HELP_TEXT
        self.fields['new_password2'].label = 'Xác nhận mật khẩu mới'
        self.fields['new_password2'].help_text = 'Nhập lại đúng mật khẩu mới ở trên.'
        _bootstrapify(self.fields)
