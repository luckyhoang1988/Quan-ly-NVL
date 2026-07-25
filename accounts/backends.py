"""Auth backend riêng cho RBAC theo user (không cộng dồn quyền từ Group).

``rbac.sync_roles()`` vẫn tạo 6 Group và gán đủ quyền theo ``ROLE_PERMISSIONS`` —
đóng vai trò "mẫu quyền mặc định của role" (giữ cho test/tham chiếu). Nhưng
quyền HIỆU LỰC của một user cụ thể chỉ tính từ ``user.user_permissions`` (gán
trực tiếp — xem ``rbac.sync_user_permissions``) chứ KHÔNG cộng thêm quyền từ
Group nữa. Lý do: nếu vẫn union với Group, admin không thể THU HỒI một quyền cụ
thể mà role mặc định cấp (Group vẫn còn quyền đó thì union luôn trả True) —
điều này phá vỡ đúng yêu cầu "phân quyền chi tiết cho từng user" (cho phép cả
thêm lẫn bớt so với mặc định vai trò).
"""
from django.contrib.auth.backends import ModelBackend


class DirectPermissionsBackend(ModelBackend):
    def get_group_permissions(self, user_obj, obj=None):
        return set()
