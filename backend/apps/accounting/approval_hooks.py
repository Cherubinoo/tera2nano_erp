"""Connects expenses to the approvals module when it is installed. Without it, accounting works on its own:
a person with accounting.manage approves a waiting expense through `POST vouchers/<id>/approve/`."""
from django.core.exceptions import ValidationError

from .models import Voucher
from .services import vouchers


def _voucher(request_obj):
    try:
        return Voucher.objects.filter(pk=getattr(request_obj, "source_id", None)).first()
    except (ValueError, TypeError, ValidationError):
        return None


def _last_decision(request_obj):
    return request_obj.decisions.select_related("approver").order_by("-decided_at").first()


def _decider(request_obj):
    d = _last_decision(request_obj)
    return d.approver if d is not None else None


def _note(request_obj):
    d = _last_decision(request_obj)
    return d.comment if d is not None else ""


def on_approved(request_obj):
    v = _voucher(request_obj)
    if v is not None and v.status == Voucher.Status.PENDING_APPROVAL:
        vouchers.approve(v, _decider(request_obj) or request_obj.requested_by, _note(request_obj))


def on_rejected(request_obj):
    v = _voucher(request_obj)
    if v is not None and v.status == Voucher.Status.PENDING_APPROVAL:
        vouchers.reject(v, _decider(request_obj), _note(request_obj) or "Rejected in approvals")


def on_cancelled(request_obj):
    v = _voucher(request_obj)
    if v is not None:
        vouchers.withdraw(v, request_obj.requested_by)


def on_resubmitted(request_obj):
    v = _voucher(request_obj)
    if v is not None:
        vouchers.resubmit(v, request_obj.requested_by)


def describe(source_id):
    try:
        v = Voucher.objects.filter(pk=source_id, kind=Voucher.Kind.EXPENSE).select_related("category").first()
    except (ValueError, TypeError, ValidationError):
        v = None
    if v is None:
        return None
    return {"title": vouchers.expense_title(v), "amount": v.total, "summary": f"{v.category.name}, total {v.total}",
            "url": f"/accounting/vouchers/{v.pk}", "project_id": v.project_id,
            "requested_by_ok": lambda user: user.has_code("accounting.manage")}


def register():
    try:
        from apps.approvals import api
    except ImportError:
        return
    if hasattr(api, "register_handler"):
        api.register_handler("expense", on_approved=on_approved, on_rejected=on_rejected, on_cancelled=on_cancelled,
                             on_resubmitted=on_resubmitted, label="Expense", required_codes=("accounting.manage",),
                             describe=describe)
