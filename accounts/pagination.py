"""Tiện ích phân trang dùng chung cho mọi list view trong dự án.

Quy ước: mỗi list view gọi ``paginate_queryset(request, queryset)`` để lấy về
``Page`` object rồi gán thẳng vào context (Page iterate y hệt queryset gốc);
template include ``partials/pagination.html`` để hiển thị điều hướng trang +
dropdown chọn số dòng/trang, giữ nguyên mọi filter query-param khác qua
template tag ``{% querystring %}`` (Django 5.1+).
"""
from django.core.paginator import Paginator

PAGE_SIZE_OPTIONS = [30, 40, 50]
DEFAULT_PAGE_SIZE = 30


def paginate_queryset(request, queryset, default_size=DEFAULT_PAGE_SIZE):
    """Trả về ``(page_obj, page_size)`` theo ``?page=``/``?page_size=`` trên request.

    ``page_size`` thiếu/không phải số/không nằm trong ``PAGE_SIZE_OPTIONS`` ->
    dùng ``default_size``. ``page`` không hợp lệ hoặc vượt quá số trang ->
    ``Paginator.get_page`` tự trả về trang 1/trang cuối tương ứng.
    """
    try:
        page_size = int(request.GET.get('page_size', default_size))
    except (TypeError, ValueError):
        page_size = default_size
    if page_size not in PAGE_SIZE_OPTIONS:
        page_size = default_size

    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(request.GET.get('page'))
    # Django Template Language không gọi được method có tham số
    # (get_elided_page_range(number)) -> tính sẵn ở đây, gắn vào page_obj để
    # partials/pagination.html chỉ cần {% for num in page_obj.elided_page_range %}.
    page_obj.elided_page_range = list(paginator.get_elided_page_range(page_obj.number))
    return page_obj, page_size
