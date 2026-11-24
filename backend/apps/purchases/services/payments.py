"""Payments to vendors: applying them to bills, keeping an advance, applying that advance later, and voiding."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from apps.company.constants import DocType
from apps.company.services import numbering

from . import amounts, posting
from .common import Action, ZERO, amount_from, log, money, text
from ..models import Vendor, VendorBill, VendorPayment, VendorPaymentAllocation

OPENING = "opening"  # the key that stands for the vendor's opening balance in a list of allocations


def snapshot(p):
    return {"vendor": p.vendor.name, "number": p.number, "date": str(p.date), "amount": str(p.amount), "mode": p.mode,
            "status": p.status}


def open_items(vendor, *, lock=False):
    """[(key, label, due date, balance)] oldest due first: the opening balance and each recorded bill with something left."""
    qs = amounts.bills_with_amounts(VendorBill.objects.filter(vendor=vendor, status=VendorBill.Status.OPEN))
    if lock:
        qs = qs.select_for_update(of=("self",))
    items = []
    left = amounts.opening_outstanding(vendor)
    if left > 0:
        items.append((OPENING, "Opening balance", vendor.opening_balance_date, left))
    for b in qs.filter(balance_due__gt=0):
        items.append((str(b.pk), b.number, b.due_date, amounts.balance_of(b)))
    items.sort(key=lambda i: (i[2] or datetime.date.max, i[1]))
    return items


def _plan(vendor, amount, allocations, auto_allocate):
    items = {k: (label, balance) for k, label, _due, balance in open_items(vendor, lock=True)}
    order = [k for k, *_ in open_items(vendor, lock=True)]
    plan = []
    if auto_allocate:
        left = amount
        for k in order:
            if left <= 0:
                break
            use = min(left, items[k][1])
            plan.append((k, use))
            left -= use
        return plan
    if allocations in (None, ""):
        allocations = []
    if not isinstance(allocations, list):
        raise ValidationError({"allocations": "Send a list."})
    for row in allocations:
        if not isinstance(row, dict):
            raise ValidationError({"allocations": "Each row needs a bill and an amount."})
        key = str(row.get("bill"))
        if key not in items:
            raise ValidationError({"allocations": "Choose bills of this vendor that still have a balance."})
        use = amount_from(row.get("amount"), "Applied amount", "allocations")
        if use > items[key][1]:
            raise ValidationError({"allocations": f"{items[key][0]} has only {items[key][1]} left to pay."})
        if use > amount:
            raise ValidationError({"allocations": "More is applied to bills than the payment amount."})
        plan.append((key, use))
    if len({k for k, _ in plan}) != len(plan):
        raise ValidationError({"allocations": "Each bill can be listed once."})
    if sum((u for _, u in plan), ZERO) > amount:
        raise ValidationError({"allocations": "More is applied to bills than the payment amount."})
    return plan


@transaction.atomic
def record_payment(company, actor, *, vendor, date, amount, mode, reference="", paid_from=None, notes="", allocations=None,
                   auto_allocate=False, project_id=None, bank_ledger_account=None):
    """Record money paid to a vendor and apply it to bills. Whatever is not applied stays as the vendor's advance.
    `bank_ledger_account` is for bank-statement matching only: it posts straight to that ledger account instead of the
    usual paid_from lookup, for a bank account with no Company Bank Account record of its own. It still posts once approved
    if the payment is held for approval."""
    vendor = Vendor.objects.select_for_update().get(pk=vendor.pk)
    amount = amount_from(amount)
    if mode not in VendorPayment.Mode.values:
        raise ValidationError({"mode": "Choose how the payment was made."})
    if paid_from is not None and not paid_from.is_active:
        raise ValidationError({"paid_from": "This bank account is switched off."})
    if mode == VendorPayment.Mode.CASH and paid_from is not None:
        raise ValidationError({"paid_from": "A cash payment does not come from a bank account."})
    plan = _plan(vendor, amount, allocations, auto_allocate)
    api = _approvals()
    hold = api is not None and api.requires_approval(company, "payment", amount)
    payment = VendorPayment.objects.create(
        company=company, vendor=vendor, date=date, amount=amount, mode=mode, reference=text(reference, "reference", 100, label="Reference"),
        paid_from=paid_from, bank_ledger_account=bank_ledger_account, notes=text(notes, "notes", 300, label="Notes"),
        project_id=project_id, created_by=actor,
        number="" if hold else numbering.allocate_number(company, DocType.VENDOR_PAYMENT, actor=actor, on_date=date).reference_number)
    if hold:
        # Not numbered, applied to any bill or posted until it is approved (see `approve`).
        payment.status, payment.submitted_at = VendorPayment.Status.PENDING_APPROVAL, _now()
        payment.plan = [[k, str(u)] for k, u in plan]
        payment.save()
        log(company, actor, "vendor_payment", payment.pk, Action.CREATED, new=snapshot(payment), reason="Sent for approval")
        api.submit(company, kind="payment", source_id=str(payment.pk), title=payment_title(payment), amount=amount, requested_by=actor,
                   summary=f"{payment.get_mode_display()}, {amount}", url=f"/purchases/payments/{payment.pk}", project_id=project_id)
        payment.refresh_from_db()
        return payment
    for key, use in plan:
        VendorPaymentAllocation.objects.create(payment=payment, bill_id=None if key == OPENING else key, amount=use)
    posting.post_payment(payment, actor)
    log(company, actor, "vendor_payment", payment.pk, Action.CREATED,
        new={**snapshot(payment), "applied_to": [("opening balance" if k == OPENING else k) for k, _ in plan]})
    return payment


@transaction.atomic
def apply_advance(payment, actor, *, allocations, date=None):
    """Set part of a payment's unapplied amount (the vendor's advance) against bills that are now due."""
    payment = VendorPayment.objects.select_for_update().select_related("vendor", "company").get(pk=payment.pk)
    if payment.status != VendorPayment.Status.RECORDED:
        raise ValidationError("This payment is void.")
    free = amounts.unapplied(payment)
    if free <= 0:
        raise ValidationError("This payment has no unapplied amount left.")
    plan = _plan(payment.vendor, free, allocations, False)
    if not plan:
        raise ValidationError({"allocations": "Choose at least one bill and an amount."})
    total = sum((u for _, u in plan), ZERO)
    if total > free:
        raise ValidationError({"allocations": f"Only {free} of this payment is unapplied."})
    on_date = date or datetime.date.today()
    batch = posting.post_advance_applied(payment, total, on_date, actor)
    for key, use in plan:
        VendorPaymentAllocation.objects.create(payment=payment, bill_id=None if key == OPENING else key, amount=use, batch=batch)
    log(payment.company, actor, "vendor_payment", payment.pk, Action.UPDATED, new={"advance_applied": str(total)},
        reason="Advance applied to bills")
    return payment


def _lock(payment):
    return VendorPayment.objects.select_for_update(of=("self",)).select_related("company", "vendor", "paid_from").get(pk=payment.pk)


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _approvals():
    """The approvals module's API when it is installed; None keeps purchases working on its own."""
    try:
        from apps.approvals import api
        return api if hasattr(api, "submit") else None
    except ImportError:
        return None


def approval_managed():
    return _approvals() is not None


def payment_title(p):
    return f"Payment to {p.vendor.name}"


def void_title(p):
    return f"Void payment {p.number} to {p.vendor.name}"


# --------------------------------------------------------------------------- a payment held for approval

@transaction.atomic
def approve(payment, actor, note=""):
    """The approval was granted: number the payment, apply it to the bills it was meant for (checked again against what
    is owed now) and post it to the books, exactly as a payment recorded directly."""
    payment = _lock(payment)
    if payment.status != VendorPayment.Status.PENDING_APPROVAL:
        raise ValidationError("Only a payment that is waiting for approval can be approved.")
    vendor = Vendor.objects.select_for_update().get(pk=payment.vendor_id)
    if payment.paid_from is not None and not payment.paid_from.is_active:
        raise ValidationError({"paid_from": "This bank account is switched off."})
    rows = [{"bill": key, "amount": amount} for key, amount in payment.plan]
    plan = _plan(vendor, payment.amount, rows, False)
    payment.decision_note = (note or "").strip()[:300]
    payment.number = numbering.allocate_number(payment.company, DocType.VENDOR_PAYMENT, actor=actor, on_date=payment.date).reference_number
    payment.status, payment.plan = VendorPayment.Status.RECORDED, []
    payment.save()
    for key, use in plan:
        VendorPaymentAllocation.objects.create(payment=payment, bill_id=None if key == OPENING else key, amount=use)
    posting.post_payment(payment, actor)
    log(payment.company, actor, "vendor_payment", payment.pk, Action.UPDATED, previous={"status": "pending_approval"},
        new={**snapshot(payment), "applied_to": [("opening balance" if k == OPENING else k) for k, _ in plan]},
        reason=f"Approved{': ' + payment.decision_note if payment.decision_note else ''}")
    return payment


@transaction.atomic
def reject(payment, actor, reason):
    """The payment is refused: it stays on record as rejected (nothing was posted or applied)."""
    payment = _lock(payment)
    if payment.status != VendorPayment.Status.PENDING_APPROVAL:
        raise ValidationError("Only a payment that is waiting for approval can be rejected.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this payment."})
    payment.status, payment.decision_note = VendorPayment.Status.REJECTED, f"Rejected: {reason.strip()}"[:300]
    payment.save()
    log(payment.company, actor, "vendor_payment", payment.pk, Action.UPDATED, previous={"status": "pending_approval"},
        new={"status": "rejected"}, reason=f"Rejected: {reason.strip()}")
    return payment


def withdraw(payment, actor):
    """The request was withdrawn: the payment is closed as rejected (record a new one if it is still due)."""
    payment = _lock(payment)
    if payment.status == VendorPayment.Status.PENDING_APPROVAL:
        payment.status, payment.decision_note = VendorPayment.Status.REJECTED, "Withdrawn before it was approved"
        payment.save()
        log(payment.company, actor, "vendor_payment", payment.pk, Action.UPDATED, previous={"status": "pending_approval"},
            new={"status": "rejected"}, reason="Approval request withdrawn")
    return payment


@transaction.atomic
def take_back(payment, actor):
    """Withdraw a payment that is waiting for approval from its own page (closes its approval request)."""
    payment = _lock(payment)
    if payment.status != VendorPayment.Status.PENDING_APPROVAL:
        raise ValidationError("Only a payment that is waiting for approval can be taken back.")
    api = _approvals()
    if api is not None:
        api.cancel_for_source("payment", payment.pk, actor)
    return withdraw(payment, actor)


def resubmit(payment, actor):
    """A rejected or withdrawn request was sent again from the Approvals module: the payment waits once more."""
    payment = _lock(payment)
    if payment.status == VendorPayment.Status.REJECTED:
        payment.status, payment.submitted_at, payment.decision_note = VendorPayment.Status.PENDING_APPROVAL, _now(), ""
        payment.save()
        log(payment.company, actor, "vendor_payment", payment.pk, Action.UPDATED, previous={"status": "rejected"},
            new={"status": "pending_approval"}, reason="Sent for approval again")
    return payment


@transaction.atomic
def delete_rejected(payment, actor):
    """Remove a rejected payment that never posted (it has no number and no effect on any bill)."""
    payment = _lock(payment)
    if payment.status != VendorPayment.Status.REJECTED:
        raise ValidationError("Only a rejected payment can be deleted. Void a recorded payment instead.")
    log(payment.company, actor, "vendor_payment", payment.pk, Action.DELETED, previous=snapshot(payment))
    payment.delete()


# --------------------------------------------------------------------------- voiding

def _void_now(payment, reason, actor):
    before = snapshot(payment)
    payment.status, payment.void_reason = VendorPayment.Status.VOID, reason.strip()[:300]
    payment.void_requested = False
    payment.save()
    posting.reverse_payment(payment, reason.strip(), actor)
    log(payment.company, actor, "vendor_payment", payment.pk, Action.DEACTIVATED, previous=before, new=snapshot(payment),
        reason=reason.strip())
    return payment


@transaction.atomic
def void_payment(payment, reason, actor):
    """Void a payment. Above an approval rule this only asks for approval: the payment stays recorded until it is given."""
    payment = _lock(payment)
    if payment.status == VendorPayment.Status.VOID:
        raise ValidationError("This payment is already void.")
    if payment.status == VendorPayment.Status.PENDING_APPROVAL:
        raise ValidationError("This payment is waiting for approval. Take it back instead of voiding it.")
    if payment.status != VendorPayment.Status.RECORDED:
        raise ValidationError("Only a recorded payment can be voided.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give a reason for voiding this payment."})
    if payment.void_requested:
        raise ValidationError("Voiding this payment is already waiting for approval.")
    api = _approvals()
    if api is None or not api.requires_approval(payment.company, "payment", payment.amount):
        return _void_now(payment, reason, actor)
    payment.void_requested, payment.void_request_reason, payment.decision_note = True, reason.strip()[:300], ""
    payment.save()
    log(payment.company, actor, "vendor_payment", payment.pk, Action.UPDATED, previous={"void_requested": False},
        new={"void_requested": True}, reason=f"Void sent for approval: {reason.strip()}")
    api.submit(payment.company, kind="payment", source_id=str(payment.pk), title=void_title(payment), amount=payment.amount,
               requested_by=actor, summary=f"Reason: {payment.void_request_reason}", url=f"/purchases/payments/{payment.pk}",
               project_id=payment.project_id)
    payment.refresh_from_db()
    return payment


@transaction.atomic
def approve_void(payment, actor, note=""):
    payment = _lock(payment)
    if payment.status != VendorPayment.Status.RECORDED or not payment.void_requested:
        raise ValidationError("Only a payment whose voiding is waiting for approval can be approved.")
    payment.decision_note = (note or "").strip()[:300]
    return _void_now(payment, payment.void_request_reason, actor)


@transaction.atomic
def reject_void(payment, actor, reason):
    payment = _lock(payment)
    if not payment.void_requested:
        raise ValidationError("Voiding this payment is not waiting for approval.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this."})
    payment.void_requested, payment.decision_note = False, f"Void rejected: {reason.strip()}"[:300]
    payment.save()
    log(payment.company, actor, "vendor_payment", payment.pk, Action.UPDATED, previous={"void_requested": True},
        new={"void_requested": False}, reason=f"Void rejected: {reason.strip()}")
    return payment


def withdraw_void(payment, actor):
    payment = _lock(payment)
    if payment.void_requested:
        payment.void_requested = False
        payment.save()
        log(payment.company, actor, "vendor_payment", payment.pk, Action.UPDATED, previous={"void_requested": True},
            new={"void_requested": False}, reason="Void request withdrawn")
    return payment


@transaction.atomic
def take_back_void(payment, actor):
    payment = _lock(payment)
    if not payment.void_requested:
        raise ValidationError("Voiding this payment is not waiting for approval.")
    api = _approvals()
    if api is not None:
        api.cancel_for_source("payment", payment.pk, actor)
    return withdraw_void(payment, actor)


def resubmit_void(payment, actor):
    payment = _lock(payment)
    if payment.status == VendorPayment.Status.RECORDED and not payment.void_requested and payment.void_request_reason:
        payment.void_requested, payment.decision_note = True, ""
        payment.save()
        log(payment.company, actor, "vendor_payment", payment.pk, Action.UPDATED, previous={"void_requested": False},
            new={"void_requested": True}, reason="Void sent for approval again")
    return payment
