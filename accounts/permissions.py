"""RBAC — nguồn dữ liệu của ma trận quyền MẶC ĐỊNH theo role (FR-USER-02, FR-USER-04).

Toàn bộ ma trận mặc định khai báo tập trung ở đây để:
- ``models.py`` sinh danh sách custom Permission (``User.Meta.permissions``).
- ``rbac.sync_roles()`` tạo 6 Group theo đúng ma trận (giữ làm "mẫu" tham chiếu/test).
- ``rbac.sync_user_permissions()`` seed ``user.user_permissions`` theo ma trận này
  khi tạo user mới hoặc khi đổi role.
- View kiểm tra quyền qua ``user.can(...)``.

Quyền HIỆU LỰC của một user cụ thể có thể khác ma trận này: admin có thể phân
quyền chi tiết riêng cho từng user (thêm hoặc thu hồi quyền) qua trang "Phân
quyền chi tiết" (``views.user_permission_edit``) — xem ``accounts/backends.py``
để biết vì sao Group không còn cộng dồn quyền vào kết quả ``has_perm``.

Quyền KHÔNG gắn với model nghiệp vụ (GRN/GIN/... chưa tồn tại ở Phase 1) mà là
"quyền hành động cấp module", neo vào content type của ``accounts.User`` — tạo một
vocabulary phân quyền ổn định, độc lập với CRUD model của từng app ở các Phase sau.

Codename quy ước: ``can_<action>_<module>`` (vd ``can_approve_grn``).
"""

# Module nghiệp vụ (khớp các cột trong bảng Permission Matrix ở BACKLOG mục 1a).
MODULES = {
    'grn': 'GRN (phiếu nhập)',
    'gin': 'GIN (phiếu xuất)',
    'opname': 'Kiểm kê kho (Stock Opname)',
    'qc': 'Kiểm tra chất lượng (QC)',
    'pr': 'Yêu cầu mua hàng (PR)',
    'po': 'Đơn mua hàng (PO)',
    'reports': 'Báo cáo',
}

# Mục sidebar KHÔNG nằm trong MODULES ở trên — trước đây không có khái niệm phân
# quyền gì cả (mở cho mọi user đã đăng nhập, hoặc chỉ gate cứng theo role/department
# trong view). Khối "Truy cập menu" trên trang Phân quyền chi tiết
# (``views.user_permission_edit``) cho admin bật/tắt truy cập TỪNG mục này riêng
# theo user, độc lập với ma trận CRUD của MODULES — mặc định luôn cấp cho MỌI role
# (xem ``codenames_for_role``) để không đổi hành vi hiện có, admin chỉ dùng để
# THU HẸP cho từng user khi cần.
MENU_ITEMS = {
    'warehouse': 'Kho hàng',
    'catalog': 'Sản phẩm (Danh mục)',
    'partners': 'Nhà cung cấp',
    'inventory': 'Tồn kho',
    'handoff': 'Phiếu chờ nhận hàng',
    'user_mgmt': 'Quản lý user',
    'audit_log': 'Nhật ký hành động',
}

# Hành động (FR-USER-04: Create / Read / Update / Delete / Approve).
# 'override' bổ sung riêng cho QC approval override (mục 2b/2c) — Supervisor
# (Manager/Admin) ghi chú override lên 1 kết quả QC đã quyết định, tách biệt
# với 'approve' (mà QC Inspector cũng có) vì override đúng nghĩa là "người
# khác xem lại quyết định", không phải người ra quyết định ban đầu.
ACTIONS = {
    'create': 'Tạo',
    'read': 'Xem',
    'update': 'Sửa',
    'delete': 'Xoá',
    'approve': 'Duyệt',
    'override': 'Override',
}

# Ma trận: role -> module -> set(action).
# - Phần C/R/U/D: chép nguyên bảng Permission Matrix ở BACKLOG mục 1a.
# - Cột Approve: theo phương án "Theo nghiệp vụ" (user chốt ở bước A2):
#     GRN/GIN/Opname/PO/Reports -> Manager + Admin;  QC -> QC Inspector + Manager + Admin.
# - 'override' chỉ gán cho QC module, Manager + Admin (QC Inspector KHÔNG có —
#   xem BACKLOG mục 2b "QC approval override", phạm vi đã chốt: chỉ ghi chú,
#   không đảo giao dịch Batch/Inventory).
ROLE_PERMISSIONS = {
    'MANAGER': {
        'grn': {'create', 'read', 'update', 'delete', 'approve'},   # CRUD + approve
        'gin': {'create', 'read', 'update', 'delete', 'approve'},    # CRUD + approve
        'opname': {'create', 'read', 'update', 'delete', 'approve'}, # CRUD + approve
        'qc': {'read', 'approve', 'override'},                       # R + approve + override
        'pr': {'create', 'read', 'update', 'delete', 'approve'},     # CRUD + approve
        'po': {'create', 'read', 'update', 'delete', 'approve'},     # CRUD + approve
        'reports': {'read', 'approve'},                              # R + approve
    },
    'STAFF': {
        'grn': {'create', 'read', 'update'},        # CRU (nhập Qty thực nhận + Submit to QC ở PENDING_QC)
        'gin': {'create', 'read'},                 # CR
        'opname': {'create', 'read', 'update'},    # CRU
        'qc': set(),                               # –
        'pr': {'create', 'read'},                  # CR (tự tạo PR, không tự duyệt)
        'po': {'read'},                            # R
        'reports': {'read'},                       # R
    },
    'QC': {
        'grn': {'read'},                                    # R
        'gin': {'read'},                                    # R
        'opname': set(),                                    # –
        'qc': {'create', 'read', 'update', 'approve'},      # CRU + approve
        'pr': {'read'},                                     # R
        'po': {'read'},                                     # R
        'reports': {'read'},                                # R
    },
    'PURCHASING': {
        'grn': {'read'},                                    # R
        'gin': {'read'},                                    # R
        'opname': set(),                                    # –
        'qc': {'read'},                                     # R
        # 'approve' PR KHÔNG còn gán cho cả role PURCHASING nữa (trước đây mọi
        # nhân viên mua hàng tự duyệt được) — duyệt PR giờ đi qua Approval,
        # chỉ quản lý phòng Mua hàng (``is_department_manager('PURCHASING')``,
        # xem accounts/models.py) hoặc Manager/Admin (fallback 'approve' ở
        # dòng MANAGER/ADMIN bên dưới) được quyết định. Duyệt PO thật
        # (approve_po, gửi NCC) vẫn giữ riêng cho Manager/Admin — 2 lớp kiểm
        # soát tách biệt, không hạ guard PO hiện có.
        'pr': {'read', 'update'},                           # R + U (không tự approve)
        'po': {'create', 'read', 'update', 'delete'},       # CRUD (approve -> Manager/Admin)
        'reports': {'read'},                                # R
    },
    'ACCOUNTANT': {
        'grn': {'read'},                                    # R
        'gin': {'read'},                                    # R
        'opname': set(),                                    # –
        'qc': {'read'},                                     # R
        'pr': {'read'},                                     # R
        'po': {'read'},                                     # R
        'reports': {'create', 'read', 'update', 'delete'},  # CRUD (approve -> Manager/Admin)
    },
    'ADMIN': {
        # Full mọi quyền trên mọi module.
        module: set(ACTIONS) for module in MODULES
    },
}


def all_menu_codenames():
    """Toàn bộ codename truy cập menu = MENU_ITEMS (dùng khi sinh Meta.permissions)."""
    return [f'can_view_menu_{key}' for key in MENU_ITEMS]


def all_permission_codenames():
    """Toàn bộ codename có thể có: CRUD (MODULES × ACTIONS) + truy cập menu (MENU_ITEMS)
    — dùng khi sinh Meta.permissions và ở trang "Phân quyền chi tiết"."""
    crud = [f'can_{action}_{module}' for module in MODULES for action in ACTIONS]
    return crud + all_menu_codenames()


def codenames_for_role(role):
    """Tập codename mà một role được cấp: CRUD theo ``ROLE_PERMISSIONS`` + TOÀN BỘ
    quyền truy cập menu (``MENU_ITEMS`` mặc định cấp cho mọi role như nhau, xem
    docstring ``MENU_ITEMS`` ở trên) — nhờ vậy user mới/đổi role/bấm "Đặt lại theo
    vai trò" đều tự động có đủ quyền menu mà không cần logic riêng."""
    module_actions = ROLE_PERMISSIONS.get(role, {})
    crud = [
        f'can_{action}_{module}'
        for module, actions in module_actions.items()
        for action in actions
    ]
    return crud + all_menu_codenames()
