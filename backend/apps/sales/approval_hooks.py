"""Connects quotations and the voiding of payments received to the approvals module when it is installed.

Without it, sales works on its own: a person with sales.manage approves a waiting quotation or void through the
`approve/` endpoints. Customer receipts are never held back (the money has already arrived); only voiding one above an
approval rule waits.
"""
from django.core.exceptions import ValidationError

from . import quotes, services
from .models import Payment, Quotation


def _last_decision(request_obj):
    return request_obj.decisions.select_related("approver").order_by("-decided_at").first()


def _decider(request_obj):
    d = _last_decision(request_obj)
    return d.approver if d is not None else None


def _note(request_obj):
    d = _last_decision(request_obj)
    return d.comment if d is not None else ""


def _one(model, request_obj):
    try:
        return model.objects.filter(pk=getattr(request_obj, "source_id", None)).first()
    except (ValueError, TypeError, ValidationError):
        return None


# --------------------------------------------------------------------------- quotations

def quotation_approved(request_obj):
    q = _one(Quotation, request_obj)
    if q is not None and q.status == Quotation.Status.PENDING_APPROVAL:
        quotes.approve(q, _decider(request_obj) or request_obj.requested_by, _note(request_obj))


def quotation_rejected(request_obj):
    q = _one(Quotation, request_obj)
    if q is not None and q.status == Quotation.Status.PENDING_APPROVAL:
        quotes.reject(q, _decider(request_obj), _note(request_obj) or "Rejected in approvals")


def quotation_cancelled(request_obj):
    q = _one(Quotation, request_obj)
    if q is not None:
        quotes.withdraw(q, request_obj.requested_by)


def quotation_resubmitted(request_obj):
    q = _one(Quotation, request_obj)
    if q is not None:
        quotes.resubmit(q, request_obj.requested_by)


def describe_quotation(source_id):
    try:
        q = Quotation.objects.filter(pk=source_id).first()
    except (ValueError, TypeError, ValidationError):
        q = None
    if q is None:
        return None
    return {"title": quotes.title_of(q), "amount": q.total, "summary": f"{q.currency} {q.total}, dated {q.issue_date:%d %b %Y}",
            "url": f"/company/quotations/{q.pk}", "project_id": None,
            "requested_by_ok": lambda user: user.has_code("documents.issue")}


# --------------------------------------------------------------------------- voiding a payment received

def payment_approved(request_obj):
    p = _one(Payment, request_obj)
    if p is not None and p.void_requested and p.status == Payment.Status.RECORDED:
        services.approve_void(p, _decider(request_obj) or request_obj.requested_by, _note(request_obj))


def payment_rejected(request_obj):
    p = _one(Payment, request_obj)
    if p is not None and p.void_requested:
        services.reject_void(p, _decider(request_obj), _note(request_obj) or "Rejected in approvals")


def payment_cancelled(request_obj):
    p = _one(Payment, request_obj)
    if p is not None:
        services.withdraw_void(p, request_obj.requested_by)


def payment_resubmitted(request_obj):
    p = _one(Payment, request_obj)
    if p is not None:
        services.resubmit_void(p, request_obj.requested_by)


def describe_payment(source_id):
    try:
        p = Payment.objects.filter(pk=source_id).select_related("customer").first()
    except (ValueError, TypeError, ValidationError):
        p = None
    if p is None or not p.void_requested:
        return None
    return {"title": services.payment_title(p), "amount": p.amount, "summary": f"Reason: {p.void_request_reason}",
            "url": f"/sales/payments/{p.pk}", "project_id": p.project_id,
            "requested_by_ok": lambda user: user.has_code("sales.manage")}


def register():
    try:
        from apps.approvals import api
    except ImportError:
        return
    if hasattr(api, "register_handler"):
        api.register_handler("quotation", on_approved=quotation_approved, on_rejected=quotation_rejected,
                             on_cancelled=quotation_cancelled, on_resubmitted=quotation_resubmitted, label="Quotation",
                             required_codes=("sales.manage",), describe=describe_quotation)
        api.register_handler("payment", on_approved=payment_approved, on_rejected=payment_rejected, on_cancelled=payment_cancelled,
                             on_resubmitted=payment_resubmitted, label="Payment", required_codes=("finance.manage",),
                             describe=describe_payment, chain=True)
