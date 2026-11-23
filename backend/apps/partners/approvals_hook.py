"""Connects withdrawals to the approvals module, once it exists (see apps/approvals/api.py).

The approvals module calls `on_approved` / `on_rejected` with its request object; `source_id` is the partner transaction.
Until then a user with partners.manage releases a waiting withdrawal from the Transactions page (`services.approve`)."""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError

KIND = "partner_withdrawal"


def _tx_and_actor(request_obj):
    from .models import PartnerTransaction
    txn = PartnerTransaction.objects.get(pk=request_obj.source_id)
    actor = getattr(request_obj, "decided_by", None) or getattr(request_obj, "approved_by", None) or getattr(request_obj, "actor", None)
    if actor is None:
        # ApprovalRequest does not expose a decision actor; use the final recorded approver
        # so partner-account self-approval protection also applies to workflow decisions.
        decision = request_obj.decisions.filter(decision="approved").select_related("approver").order_by("-decided_at", "-id").first()
        actor = decision.approver if decision else None
    return txn, actor if isinstance(actor, get_user_model()) else None


def on_approved(request_obj):
    from . import services
    txn, actor = _tx_and_actor(request_obj)
    services.approve(txn, actor)


def _rejection(request_obj):
    """(the person who rejected, their comment) from the request's last rejecting decision."""
    d = request_obj.decisions.filter(decision="rejected").select_related("approver").order_by("-decided_at", "-id").first()
    return (d.approver, d.comment) if d is not None else (None, "")


def on_rejected(request_obj):
    from . import services
    txn = _tx_and_actor(request_obj)[0]
    actor, comment = _rejection(request_obj)
    services.reject(txn, actor if isinstance(actor, get_user_model()) else None, comment or "Rejected in approvals")


def on_cancelled(request_obj):
    """The requester withdrew the approval request: the withdrawal is cancelled (voided) rather than left waiting for nobody."""
    from . import services
    from .models import PartnerTransaction
    txn = PartnerTransaction.objects.filter(pk=request_obj.source_id).first()
    if txn is not None and txn.status == PartnerTransaction.Status.PENDING:
        services.void(txn, request_obj.requested_by, "Approval request withdrawn")


def describe(source_id):
    """Lets the Approval button send a waiting withdrawal whose request was closed back for approval."""
    from .models import PartnerTransaction
    try:
        txn = PartnerTransaction.objects.filter(pk=source_id).select_related("partner").first()
    except (ValueError, TypeError, DjangoValidationError):
        txn = None
    if txn is None:
        return None
    return {"title": f"{txn.get_kind_display()}: {txn.partner.full_name}", "amount": txn.amount, "summary": txn.narration,
            "url": "/partners/transactions", "project_id": None, "requested_by_ok": lambda user: user.has_code("partners.manage")}


def close_request(txn, actor):
    """The withdrawal was decided or voided from the Partners page: close its open approval request (the module is not called back)."""
    try:
        from apps.approvals import api
        api.cancel_for_source(KIND, str(txn.pk), actor)
    except (ImportError, AttributeError):
        pass


def submit(txn):
    """Ask the approvals module to decide on a waiting withdrawal; returns the request, or None when approvals are not installed."""
    try:
        from apps.approvals import api
        return api.submit(txn.company, kind=KIND, source_id=str(txn.pk), title=f"{txn.get_kind_display()}: {txn.partner.full_name}",
                          amount=txn.amount, requested_by=txn.requested_by, summary=txn.narration, url="/partners/transactions")
    except (ImportError, AttributeError):
        return None


def register():
    from apps.approvals import api
    api.register_handler(KIND, on_approved=on_approved, on_rejected=on_rejected, on_cancelled=on_cancelled,
                         label="Partner withdrawal", required_codes=("partners.manage",), describe=describe)
