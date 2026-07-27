"""View app reports: Dashboard KPI + 3 báo cáo phân tích (Phase 6, FR-RPT-01..05).

Phân quyền: dùng RBAC thật qua ``user.can('read', 'reports')`` — mọi role đã có
sẵn quyền này từ Phase 1 (xem ``accounts/permissions.py``), nên chỉ cần chặn
user chưa đăng nhập; không có action create/update/delete/approve nào ở đây vì
Reporting chỉ đọc dữ liệu.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .exports import build_excel_response, build_pdf_response
from .services import abc_analysis, dashboard_kpis, slow_moving_items, supplier_performance


def reports_permission_required(action):
    """Decorator factory: chưa đăng nhập -> login; thiếu quyền ``action`` trên
    module 'reports' -> 403 (mirror ``receiving.views.grn_permission_required``).
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not request.user.can(action, 'reports'):
                raise PermissionDenied(f'Không có quyền "{action}" trên Reports.')
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


@reports_permission_required('read')
def dashboard(request):
    """FR-RPT-01: Dashboard KPI."""
    return render(request, 'reports/dashboard.html', {'kpis': dashboard_kpis()})


@reports_permission_required('read')
def abc_analysis_view(request):
    """FR-RPT-02: Báo cáo ABC Analysis, kèm export Excel/PDF (FR-RPT-05)."""
    result = abc_analysis()
    export = request.GET.get('export')
    if export in ('excel', 'pdf'):
        headers = ['SKU', 'Tên sản phẩm', 'Tồn kho', 'Đơn giá', 'Giá trị', '% Tích luỹ', 'Loại']
        rows = [
            [
                row['product'].product_code, row['product'].name, row['total_qty'],
                row['unit_price'], row['value'], f"{row['cumulative_pct']:.1f}%", row['class'],
            ]
            for row in result['rows']
        ]
        if export == 'excel':
            return build_excel_response('abc_analysis', headers, rows)
        return build_pdf_response('abc_analysis', 'Báo cáo ABC Analysis', headers, rows)
    return render(request, 'reports/abc_analysis.html', result)


@reports_permission_required('read')
def slow_moving_view(request):
    """FR-RPT-03: Báo cáo tồn lỏng, kèm export Excel/PDF (FR-RPT-05)."""
    try:
        days = int(request.GET.get('days', 180))
    except (TypeError, ValueError):
        # ?days= không phải số nguyên hợp lệ -> dùng mặc định thay vì crash 500
        # (bug fix 2026-07-27, xem CLAUDE.md).
        days = 180
    rows_data = slow_moving_items(days=days)
    export = request.GET.get('export')
    if export in ('excel', 'pdf'):
        headers = ['SKU', 'Tên sản phẩm', 'Tồn kho', 'Giá trị', 'Số ngày chưa xuất', 'Đề xuất']
        rows = [
            [
                row['product'].product_code, row['product'].name, row['total_qty'],
                row['value'], row['days_idle'], row['recommendation'],
            ]
            for row in rows_data
        ]
        if export == 'excel':
            return build_excel_response('slow_moving', headers, rows)
        return build_pdf_response('slow_moving', 'Báo cáo Tồn Lỏng', headers, rows)
    return render(request, 'reports/slow_moving.html', {'rows': rows_data, 'days': days})


@reports_permission_required('read')
def supplier_performance_view(request):
    """FR-RPT-04: Báo cáo hiệu suất NCC, kèm export Excel/PDF (FR-RPT-05)."""
    rows_data = supplier_performance()
    export = request.GET.get('export')
    if export in ('excel', 'pdf'):
        headers = [
            'Mã NCC', 'Tên NCC', 'Số PO đã nhận', '% Đúng hạn', '% QC Pass',
            'Đơn giá bình quân', 'Lead time cấu hình (ngày)', 'Lead time thực tế (ngày)',
        ]
        rows = [
            [
                row['supplier'].supplier_code, row['supplier'].name, row['received_po_count'],
                f"{row['on_time_pct']:.1f}%" if row['on_time_pct'] is not None else '—',
                f"{row['qc_pass_pct']:.1f}%" if row['qc_pass_pct'] is not None else '—',
                row['avg_price'], row['configured_lead_time_days'],
                f"{row['avg_actual_lead_time_days']:.1f}" if row['avg_actual_lead_time_days'] is not None else '—',
            ]
            for row in rows_data
        ]
        if export == 'excel':
            return build_excel_response('supplier_performance', headers, rows)
        return build_pdf_response('supplier_performance', 'Báo cáo Hiệu suất NCC', headers, rows)
    return render(request, 'reports/supplier_performance.html', {'rows': rows_data})
