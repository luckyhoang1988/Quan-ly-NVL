"""FR-RPT-05: xuất báo cáo sang Excel/PDF.

Chỉ nhận dữ liệu đã format sẵn (list header + list-of-list dòng dữ liệu) — không
biết gì về model nghiệp vụ, để dùng chung cho cả 3 báo cáo bảng (ABC, Slow-moving,
Supplier Performance).
"""
from django.http import HttpResponse
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet
import io


_FORMULA_LEAD_CHARS = ('=', '+', '-', '@')


def _excel_safe(value):
    """Chặn CSV/Excel formula injection: cell bắt đầu bằng =/+/-/@ (vd tên SKU
    do người dùng nhập) bị Excel/LibreOffice diễn giải thành công thức khi mở
    file — thêm dấu nháy đơn để ép về kiểu text thuần (openpyxl lưu
    ``data_type='f'`` cho chuỗi thô bắt đầu bằng các ký tự này).
    """
    text = str(value) if value is not None else ''
    if text.startswith(_FORMULA_LEAD_CHARS):
        return "'" + text
    return text


def build_excel_response(filename, headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append([_excel_safe(cell) for cell in row])

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}.xlsx"'
    wb.save(response)
    return response


def build_pdf_response(filename, title, headers, rows):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    styles = getSampleStyleSheet()

    table_data = [headers] + [
        [str(cell) if cell is not None else '' for cell in row] for row in rows
    ]
    table = Table(table_data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0066CC')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F2F2F2')]),
    ]))
    doc.build([Paragraph(title, styles['Title']), table])

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}.pdf"'
    return response
