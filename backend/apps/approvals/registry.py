"""Which kinds of record can be approved, and the callbacks each owning module registered.

A module registers once (usually in its AppConfig.ready()):

    from apps.approvals import api as approvals
    approvals.register_handler("purchase_order", on_approved=po_approved, on_rejected=po_rejected, label="Purchase order",
                               required_codes=("purchases.manage",))

Callbacks receive the `ApprovalRequest`. They run inside the transaction of the decision: if one raises, the decision is
rolled back and the approver sees the message.
"""
from dataclasses import dataclass
from typing import Callable, Optional

# kind -> (label, permission codes whose holders may submit it without anyone else's approval when no rule applies)
KNOWN_KINDS = {
    "purchase_order": ("Purchase order", ("purchases.manage",)),
    "vendor_bill": ("Vendor bill", ("purchases.manage",)),
    "leave_request": ("Leave request", ("hr.manage",)),
    "expense": ("Expense", ("accounting.manage",)),
    "payroll_run": ("Payroll run", ("hr.payroll",)),
    "budget": ("Project budget", ("projects.manage",)),
    "salary_revision": ("Salary revision", ("hr.payroll",)),
    "payment": ("Payment", ("finance.manage",)),
    "partner_withdrawal": ("Partner withdrawal", ("partners.manage",)),
    "quotation": ("Quotation", ("sales.manage",)),
}


@dataclass
class Handler:
    kind: str
    on_approved: Optional[Callable] = None
    on_rejected: Optional[Callable] = None
    on_cancelled: Optional[Callable] = None      # the requester withdrew the request
    on_resubmitted: Optional[Callable] = None    # a rejected/withdrawn request was sent again (new request object)
    label: str = ""
    required_codes: tuple = ()
    # describe(source_id) -> {"title", "amount", "summary", "url", "project_id", "requested_by_ok": fn(user) -> bool} or None
    # when the record does not exist. Only kinds with a describe function can be submitted through POST /submit/.
    describe: Optional[Callable] = None


_HANDLERS = {}


def register_handler(kind, *, on_approved=None, on_rejected=None, label="", required_codes=(), describe=None,
                     on_cancelled=None, on_resubmitted=None, chain=False):
    """Register the callbacks of a kind. With `chain=True` a handler registered earlier for the same kind is kept: when
    two modules own different records of one kind (customer receipts in sales, vendor payments in purchases) every
    callback runs for each of them and `describe` asks each module in turn. Each callback must ignore a record it does
    not own (the hooks in this project already do, because they look the record up by id)."""
    new = Handler(kind=kind, on_approved=on_approved, on_rejected=on_rejected, on_cancelled=on_cancelled,
                  on_resubmitted=on_resubmitted, label=label, required_codes=tuple(required_codes), describe=describe)
    old = _HANDLERS.get(kind)
    if chain and old is not None:
        new = Handler(kind=kind, on_approved=_both(old.on_approved, on_approved), on_rejected=_both(old.on_rejected, on_rejected),
                      on_cancelled=_both(old.on_cancelled, on_cancelled), on_resubmitted=_both(old.on_resubmitted, on_resubmitted),
                      label=old.label or label, required_codes=old.required_codes or tuple(required_codes),
                      describe=_first(old.describe, describe))
    _HANDLERS[kind] = new
    return new


def _both(first, second):
    if first is None or second is None:
        return first or second

    def run(request_obj):
        first(request_obj)
        second(request_obj)
    return run


def _first(first, second):
    if first is None or second is None:
        return first or second

    def describe(source_id):
        return first(source_id) or second(source_id)
    return describe


def unregister_handler(kind):
    _HANDLERS.pop(kind, None)


def handler_for(kind):
    return _HANDLERS.get(kind)


def kind_label(kind):
    h = _HANDLERS.get(kind)
    if h and h.label:
        return h.label
    if kind in KNOWN_KINDS:
        return KNOWN_KINDS[kind][0]
    return kind.replace("_", " ").capitalize()


def manage_codes(kind):
    h = _HANDLERS.get(kind)
    if h and h.required_codes:
        return h.required_codes
    return KNOWN_KINDS.get(kind, ("", ()))[1]


def all_kinds():
    kinds = list(KNOWN_KINDS)
    kinds += [k for k in _HANDLERS if k not in KNOWN_KINDS]
    return [{"value": k, "label": kind_label(k), "can_submit_from_ui": bool(_HANDLERS.get(k) and _HANDLERS[k].describe)}
            for k in kinds]
