"""Transaction nghiệp vụ GRN (mục 2a) — state DRAFT -> PENDING_QC.

Chuyển tiếp PENDING_QC -> QC_IN_PROGRESS (``start_qc``) và các nhánh QC PASS/
FAIL/PARTIAL_PASS nằm ở ``quality.services`` vì gắn liền với ``QcInspection``
(mục 2c) — module ``receiving`` chỉ giữ transition thuần GRN, chưa đụng QC.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.audit import log_action
from accounts.models import AuditLog

from .models import Grn


@transaction.atomic
def submit_to_pending_qc(grn, actor=None, ip_address=None):
    """DRAFT -> PENDING_QC (mục 2a Workflow States: nút "Submit" ở state DRAFT)."""
    grn = Grn.objects.select_for_update().get(pk=grn.pk)
    if grn.status != Grn.Status.DRAFT:
        raise ValidationError(f'Không thể submit khi GRN đang ở trạng thái {grn.status}.')
    if not grn.items.exists():
        raise ValidationError('GRN chưa có dòng hàng nào.')

    grn.status = Grn.Status.PENDING_QC
    grn.save(update_fields=['status'])
    log_action(
        actor, AuditLog.Action.UPDATE, target=grn,
        description=f'Submit GRN: {grn.grn_no} DRAFT -> PENDING_QC.',
        ip_address=ip_address,
    )
    return grn
