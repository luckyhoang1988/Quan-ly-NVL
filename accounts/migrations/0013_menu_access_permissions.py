"""Thêm 7 permission "Truy cập menu" mới (``MENU_ITEMS`` — các mục sidebar trước đây
không có ma trận CRUD: warehouse/catalog/partners/inventory/handoff/user_mgmt/audit_log).

Backfill: cấp đủ 7 quyền này cho MỌI user đã tồn tại (giữ nguyên hành vi hiện có — các
mục này đang mở cho tất cả), dùng ``.add()`` chứ KHÔNG dùng ``.set()`` để không đụng tới
phân quyền CRUD user đã tuỳ biến trước đó qua trang "Phân quyền chi tiết" — cùng pattern
backfill đã dùng ở ``0007_seed_user_permissions_from_role.py``/``0012_...py``.
"""
from django.db import migrations

from accounts.permissions import all_menu_codenames


def grant_menu_access_to_existing_users(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    Permission = apps.get_model('auth', 'Permission')
    perms = Permission.objects.filter(
        content_type__app_label='accounts', codename__in=all_menu_codenames(),
    )
    for user in User.objects.all():
        user.user_permissions.add(*perms)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_reseed_purchasing_pr_permissions'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='user',
            options={'permissions': [('can_create_grn', 'Tạo GRN (phiếu nhập)'), ('can_read_grn', 'Xem GRN (phiếu nhập)'), ('can_update_grn', 'Sửa GRN (phiếu nhập)'), ('can_delete_grn', 'Xoá GRN (phiếu nhập)'), ('can_approve_grn', 'Duyệt GRN (phiếu nhập)'), ('can_override_grn', 'Override GRN (phiếu nhập)'), ('can_create_gin', 'Tạo GIN (phiếu xuất)'), ('can_read_gin', 'Xem GIN (phiếu xuất)'), ('can_update_gin', 'Sửa GIN (phiếu xuất)'), ('can_delete_gin', 'Xoá GIN (phiếu xuất)'), ('can_approve_gin', 'Duyệt GIN (phiếu xuất)'), ('can_override_gin', 'Override GIN (phiếu xuất)'), ('can_create_opname', 'Tạo Kiểm kê kho (Stock Opname)'), ('can_read_opname', 'Xem Kiểm kê kho (Stock Opname)'), ('can_update_opname', 'Sửa Kiểm kê kho (Stock Opname)'), ('can_delete_opname', 'Xoá Kiểm kê kho (Stock Opname)'), ('can_approve_opname', 'Duyệt Kiểm kê kho (Stock Opname)'), ('can_override_opname', 'Override Kiểm kê kho (Stock Opname)'), ('can_create_qc', 'Tạo Kiểm tra chất lượng (QC)'), ('can_read_qc', 'Xem Kiểm tra chất lượng (QC)'), ('can_update_qc', 'Sửa Kiểm tra chất lượng (QC)'), ('can_delete_qc', 'Xoá Kiểm tra chất lượng (QC)'), ('can_approve_qc', 'Duyệt Kiểm tra chất lượng (QC)'), ('can_override_qc', 'Override Kiểm tra chất lượng (QC)'), ('can_create_pr', 'Tạo Yêu cầu mua hàng (PR)'), ('can_read_pr', 'Xem Yêu cầu mua hàng (PR)'), ('can_update_pr', 'Sửa Yêu cầu mua hàng (PR)'), ('can_delete_pr', 'Xoá Yêu cầu mua hàng (PR)'), ('can_approve_pr', 'Duyệt Yêu cầu mua hàng (PR)'), ('can_override_pr', 'Override Yêu cầu mua hàng (PR)'), ('can_create_po', 'Tạo Đơn mua hàng (PO)'), ('can_read_po', 'Xem Đơn mua hàng (PO)'), ('can_update_po', 'Sửa Đơn mua hàng (PO)'), ('can_delete_po', 'Xoá Đơn mua hàng (PO)'), ('can_approve_po', 'Duyệt Đơn mua hàng (PO)'), ('can_override_po', 'Override Đơn mua hàng (PO)'), ('can_create_reports', 'Tạo Báo cáo'), ('can_read_reports', 'Xem Báo cáo'), ('can_update_reports', 'Sửa Báo cáo'), ('can_delete_reports', 'Xoá Báo cáo'), ('can_approve_reports', 'Duyệt Báo cáo'), ('can_override_reports', 'Override Báo cáo'), ('can_view_menu_warehouse', 'Truy cập menu Kho hàng'), ('can_view_menu_catalog', 'Truy cập menu Sản phẩm (Danh mục)'), ('can_view_menu_partners', 'Truy cập menu Nhà cung cấp'), ('can_view_menu_inventory', 'Truy cập menu Tồn kho'), ('can_view_menu_handoff', 'Truy cập menu Phiếu chờ nhận hàng'), ('can_view_menu_user_mgmt', 'Truy cập menu Quản lý user'), ('can_view_menu_audit_log', 'Truy cập menu Nhật ký hành động')]},
        ),
        migrations.RunPython(grant_menu_access_to_existing_users, noop_reverse),
    ]
