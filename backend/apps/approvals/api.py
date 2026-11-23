"""The interface other modules use to put their records through approval.

    from apps.approvals import api as approvals

    # once, in AppConfig.ready():
    approvals.register_handler("purchase_order", on_approved=fn, on_rejected=fn, label="Purchase order",
                               required_codes=("purchases.manage",),
                               describe=fn_describe,          # optional: lets the UI button POST /approvals/submit/
                               on_cancelled=fn, on_resubmitted=fn)   # optional

    # from a view, inside the transaction that moves the record to "pending approval":
    req = approvals.submit(company, kind="purchase_order", source_id=po.id, title="PO 123", amount=po.total,
                           requested_by=request.user, summary="...", url=f"/purchases/orders/{po.id}")
    if req.status == "approved":   # no rule applied and the requester may approve it: on_approved has already run
        ...

Callbacks receive the ApprovalRequest and run inside the same database transaction as the decision. If one raises, the
decision is rolled back and the approver sees the error, so a callback must either finish its work or raise.
`describe(source_id)` returns {"title", "amount", "summary", "url", "project_id", "requested_by_ok": fn(user) -> bool}, or
None when the record does not exist.
"""
from . import engine, registry, rules
from .models import ApprovalRequest

register_handler = registry.register_handler
unregister_handler = registry.unregister_handler
seed_default_rules = rules.seed_default_rules


def submit(company, *, kind, source_id, title, amount=None, requested_by, summary="", url="", project_id=None, meta=None):
    """Ask for approval of a record. `meta` may carry {"project": name, "department": name} for rules that match on them."""
    return engine.submit(company, kind=kind, source_id=source_id, title=title, amount=amount, requested_by=requested_by,
                         summary=summary, url=url, project_id=project_id, meta=meta)


def status_of(kind, source_id):
    """"none", "pending", "approved", "rejected" or "cancelled": where the latest request for this record stands."""
    return engine.status_of(kind, source_id)


def cancel_for_source(kind, source_id, actor):
    """The record was voided or deleted: close its open request (the module is not called back). Returns the request or None."""
    return engine.cancel_for_source(kind, source_id, actor)


def requires_approval(company, kind, amount=None, meta=None):
    """Does an active rule apply to a record of this kind and size? Modules whose records are normally saved straight away
    (a vendor bill, a budget, a salary revision) hold the record for approval only when this is true. It reads the rules
    as they stand and creates none: an administrator who switches every rule of a kind off gets the direct flow again."""
    from decimal import Decimal
    if amount is not None and not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    return rules.match_rule(company, kind, amount, meta) is not None


def latest_request(kind, source_id):
    return ApprovalRequest.objects.filter(kind=kind, source_id=str(source_id)).order_by("-submitted_at").first()
