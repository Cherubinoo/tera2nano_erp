"""Vendor bills: entering a bill (alone or from a purchase order), recording it in the books, and voiding it.

A bill is a draft until it is recorded. Recording numbers it, posts it (see `posting`) and freezes it; after that a mistake is
fixed by voiding the bill (which reverses the entry) and entering it again.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.company.constants import DocType
from apps.company.services import numbering

from . import amounts, orders, posting, pricing
from .common import Action, ZERO, log, money, optional_amount, text
from ..models import PurchaseOrder, VendorBill, VendorPayment

Status, Itc = VendorBill.Status, VendorBill.Itc


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def snapshot(b):
    return {"vendor": b.vendor.name, "number": b.number, "vendor_bill_no": b.vendor_bill_no, "status": b.status,
            "bill_date": str(b.bill_date), "due_date": str(b.due_date or ""), "total": str(b.grand_total),
            "tds": str(b.tds_amount), "net_payable": str(b.net_payable), "itc": b.itc_status}


def default_due_date(vendor, bill_date):
    days = vendor.payment_terms_days
    return bill_date + datetime.timedelta(days=days) if days else bill_date


def _tds(taxable, section, rate):
    section = text(section, "tds_section", 20, label="TDS section")
    rate = optional_amount(rate, "TDS rate", "tds_rate", high=Decimal("100")) or ZERO
    if rate > 0 and not section:
        raise ValidationError({"tds_section": "Enter the TDS section (for example 194C)."})
    if section and rate <= 0:
        raise ValidationError({"tds_rate": "Enter the TDS rate for this section, or clear the section."})
    return section, rate, pricing.tds_for(taxable, rate) if rate > 0 else ZERO


def _check_against_order(order, vendor, record, exclude=None):
    if order.vendor_id != vendor.pk:
        raise ValidationError({"order": "This order belongs to a different vendor."})
    if order.status in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.PENDING_APPROVAL, PurchaseOrder.Status.APPROVED,
                        PurchaseOrder.Status.CANCELLED):
        raise ValidationError({"order": "Choose an order that has been issued."})
    billed = {}
    for b in VendorBill.objects.filter(order=order).exclude(status=Status.VOID):
        if exclude is not None and b.pk == exclude.pk:
            continue
        for line in b.record.get("lines", []):
            if line.get("po_line") is not None:
                billed[int(line["po_line"])] = billed.get(int(line["po_line"]), ZERO) + Decimal(line["quantity"])
    ordered = order.record["lines"]
    now = {}
    for line in record["lines"]:
        if line["po_line"] is None:
            continue
        i = int(line["po_line"])
        if not 0 <= i < len(ordered):
            raise ValidationError({"items": "A line refers to an item that is not on the order."})
        now[i] = now.get(i, ZERO) + Decimal(line["quantity"])
    for i, qty in now.items():
        if billed.get(i, ZERO) + qty > Decimal(ordered[i]["quantity"]):
            raise ValidationError({"items": f"Item {i + 1} of the order is billed for more than was ordered."})


@transaction.atomic
def save_draft(company, actor, *, vendor, vendor_bill_no, bill_date, due_date=None, items, itc_status=Itc.ELIGIBLE,
               tds_section="", tds_rate=None, expense_account=None, project_id=None, notes="", order=None, bill=None):
    if bill is not None and bill.status == Status.PENDING_APPROVAL:
        raise ValidationError("This bill is waiting for approval and cannot be edited. Take it back first.")
    if bill is not None and bill.status != Status.DRAFT:
        raise ValidationError("A recorded bill cannot be edited. Void it and enter it again.")
    if not vendor.is_active:
        raise ValidationError({"vendor": "This vendor is switched off."})
    vendor_bill_no = text(vendor_bill_no, "vendor_bill_no", 100, required=True, label="The vendor's bill number")
    if itc_status not in Itc.values:
        raise ValidationError({"itc_status": "Choose eligible, ineligible or blocked."})
    due_date = due_date or default_due_date(vendor, bill_date)
    if due_date < bill_date:
        raise ValidationError({"due_date": "The due date cannot be before the bill date."})
    clash = VendorBill.objects.filter(vendor=vendor, vendor_bill_no__iexact=vendor_bill_no).exclude(status=Status.VOID)
    if bill is not None:
        clash = clash.exclude(pk=bill.pk)
    if clash.exists():
        raise ValidationError({"vendor_bill_no": "A bill with this number has already been entered for this vendor."})
    default = pricing.usable_account(company, expense_account, "Expense account") if expense_account else posting.default_expense_account(company)
    record = pricing.price(company, vendor, items, default_account=default, with_accounts=True)
    totals = pricing.totals_of(record)
    section, rate, tds_amount = _tds(totals["taxable_total"], tds_section, tds_rate)
    if order is not None:
        _check_against_order(order, vendor, record, exclude=bill)
    fields = dict(vendor=vendor, order=order, vendor_bill_no=vendor_bill_no, bill_date=bill_date, due_date=due_date,
                  form={"items": items}, record=record, itc_status=itc_status, tds_section=section, tds_rate=rate,
                  tds_amount=tds_amount, net_payable=totals["grand_total"] - tds_amount, expense_account_id=default.pk,
                  project_id=project_id, notes=text(notes, "notes", 5000, label="Notes"), **totals)
    if bill is None:
        bill = VendorBill.objects.create(company=company, created_by=actor, **fields)
        log(company, actor, "vendor_bill", bill.pk, Action.CREATED, new=snapshot(bill))
    else:
        before = snapshot(bill)
        for key, value in fields.items():
            setattr(bill, key, value)
        bill.save()
        log(company, actor, "vendor_bill", bill.pk, Action.UPDATED, previous=before, new=snapshot(bill))
    return bill


@transaction.atomic
def from_order(order, actor):
    """A draft bill for what is still to be billed on an order: received goods when some arrived, else the ordered quantity."""
    order = PurchaseOrder.objects.select_for_update(of=("self",)).select_related("vendor", "company").get(pk=order.pk)
    if order.status in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.PENDING_APPROVAL, PurchaseOrder.Status.APPROVED,
                        PurchaseOrder.Status.CANCELLED):
        raise ValidationError("Only an issued order can be billed.")
    got, billed = orders.received_quantities(order), orders.billed_quantities(order)
    items = []
    for i, line in enumerate(order.record["lines"]):
        base = got.get(i, ZERO) if got else Decimal(line["quantity"])
        qty = base - billed.get(i, ZERO)
        if qty > 0:
            items.append({"description": line["description"], "hsn_sac": line["hsn_sac"], "unit": line["unit"],
                          "quantity": str(qty), "rate": line["rate"], "discount_percent": line["discount_percent"],
                          "gst_rate": line["gst_rate"], "po_line": i})
    if not items:
        raise ValidationError("Everything on this order has been billed already (or nothing has been received yet).")
    today = datetime.date.today()
    n = VendorBill.objects.filter(vendor=order.vendor, vendor_bill_no__startswith=f"{order.number}-").count() + 1
    return save_draft(order.company, actor, vendor=order.vendor, vendor_bill_no=f"{order.number}-{n}", bill_date=today, items=items,
                      project_id=order.project_id, order=order, notes=f"Against purchase order {order.number}",
                      tds_section=order.vendor.tds_section, tds_rate=order.vendor.tds_rate)


def _lock(bill):
    return VendorBill.objects.select_for_update(of=("self",)).select_related("vendor", "company", "order").get(pk=bill.pk)


def _check_recordable(bill):
    if not bill.vendor.is_active:
        raise ValidationError({"vendor": "This vendor is switched off."})
    if bill.grand_total <= 0:
        raise ValidationError({"items": "The total must be more than zero."})
    clash = VendorBill.objects.filter(vendor=bill.vendor, vendor_bill_no__iexact=bill.vendor_bill_no).exclude(
        status=Status.VOID).exclude(pk=bill.pk)
    if clash.exists():
        raise ValidationError({"vendor_bill_no": "A bill with this number has already been entered for this vendor."})


def _open(bill, actor, reason="Recorded"):
    """Number the bill, post it to the books and freeze it."""
    bill.number = numbering.allocate_number(bill.company, DocType.VENDOR_BILL, actor=actor, on_date=bill.bill_date).reference_number
    bill.status = Status.OPEN
    bill.save()
    posting.post_bill(bill, actor)
    log(bill.company, actor, "vendor_bill", bill.pk, Action.ISSUED, new=snapshot(bill), reason=reason)


# --------------------------------------------------------------------------- approval

def _approvals():
    return orders._approvals()


def approval_managed():
    """Whether decisions on bills held for approval must go through the shared approvals workflow."""
    return _approvals() is not None


def needs_approval(bill):
    """A bill is held for approval when the approvals module has an active rule for a bill of this size."""
    api = _approvals()
    return api is not None and api.requires_approval(bill.company, "vendor_bill", bill.grand_total)


@transaction.atomic
def record(bill, actor):
    """Record a draft. A bill above an approval rule waits (pending_approval) until it is approved, and only then is it
    numbered and posted; any other bill is numbered and posted at once."""
    bill = _lock(bill)
    if bill.status != Status.DRAFT:
        raise ValidationError("Only a draft can be recorded.")
    _check_recordable(bill)
    if not needs_approval(bill):
        bill.recorded_by = actor
        _open(bill, actor)
        return bill
    bill.status, bill.submitted_at, bill.decision_note, bill.recorded_by = Status.PENDING_APPROVAL, _now(), "", actor
    bill.save()
    log(bill.company, actor, "vendor_bill", bill.pk, Action.UPDATED, previous={"status": "draft"}, new={"status": "pending_approval"},
        reason="Sent for approval")
    _approvals().submit(bill.company, kind="vendor_bill", source_id=str(bill.pk),
                        title=f"Vendor bill {bill.vendor_bill_no} from {bill.vendor.name}", amount=bill.grand_total, requested_by=actor,
                        summary=f"Total {bill.grand_total}, net payable {bill.net_payable}", url=f"/purchases/bills/{bill.pk}",
                        project_id=bill.project_id)
    bill.refresh_from_db()
    return bill


@transaction.atomic
def approve(bill, actor, note=""):
    """The approval was granted: number the bill and post it to the books."""
    bill = _lock(bill)
    if bill.status != Status.PENDING_APPROVAL:
        raise ValidationError("Only a bill that is waiting for approval can be approved.")
    _check_recordable(bill)
    bill.decision_note = (note or "").strip()[:300]
    _open(bill, actor, reason=f"Approved{': ' + bill.decision_note if bill.decision_note else ''}")
    return bill


@transaction.atomic
def reject(bill, actor, reason):
    bill = _lock(bill)
    if bill.status != Status.PENDING_APPROVAL:
        raise ValidationError("Only a bill that is waiting for approval can be rejected.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this bill."})
    bill.status, bill.decision_note = Status.DRAFT, f"Rejected: {reason.strip()}"[:300]  # back to a draft to be corrected
    bill.save()
    log(bill.company, actor, "vendor_bill", bill.pk, Action.UPDATED, previous={"status": "pending_approval"}, new={"status": "draft"},
        reason=f"Rejected: {reason.strip()}")
    return bill


def withdraw(bill, actor):
    """The approval request was withdrawn (or the person took the bill back): the bill is a draft again."""
    bill = _lock(bill)
    if bill.status == Status.PENDING_APPROVAL:
        bill.status = Status.DRAFT
        bill.save()
        log(bill.company, actor, "vendor_bill", bill.pk, Action.UPDATED, previous={"status": "pending_approval"},
            new={"status": "draft"}, reason="Approval request withdrawn")
    return bill


@transaction.atomic
def take_back(bill, actor):
    """Withdraw a bill that is waiting for approval from the bill's own page (closes its approval request)."""
    bill = _lock(bill)
    if bill.status != Status.PENDING_APPROVAL:
        raise ValidationError("Only a bill that is waiting for approval can be taken back.")
    api = _approvals()
    if api is not None:
        api.cancel_for_source("vendor_bill", bill.pk, actor)
    return withdraw(bill, actor)


def resubmit(bill, actor):
    """A rejected or withdrawn request was sent again from the Approvals module: the draft waits for approval once more."""
    bill = _lock(bill)
    if bill.status == Status.DRAFT:
        _check_recordable(bill)
        bill.status, bill.submitted_at, bill.decision_note = Status.PENDING_APPROVAL, _now(), ""
        bill.save()
        log(bill.company, actor, "vendor_bill", bill.pk, Action.UPDATED, previous={"status": "draft"}, new={"status": "pending_approval"},
            reason="Sent for approval again")
    return bill


@transaction.atomic
def void(bill, reason, actor):
    bill = VendorBill.objects.select_for_update(of=("self",)).select_related("vendor", "company").get(pk=bill.pk)
    if bill.status != Status.OPEN:
        raise ValidationError("Only a recorded bill can be voided; delete a draft instead.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give a reason for voiding this bill."})
    if bill.allocations.exclude(payment__status=VendorPayment.Status.VOID).exists():
        raise ValidationError("Payments are applied to this bill. Void those payments first.")
    before = snapshot(bill)
    bill.status, bill.void_reason, bill.voided_at = Status.VOID, reason.strip()[:300], _now()
    bill.save()
    posting.reverse_bill(bill, reason.strip(), actor)
    log(bill.company, actor, "vendor_bill", bill.pk, Action.DEACTIVATED, previous=before, new=snapshot(bill), reason=reason.strip())
    return bill


@transaction.atomic
def delete(bill, actor):
    bill = VendorBill.objects.select_for_update(of=("self",)).select_related("vendor").get(pk=bill.pk)
    if bill.status == Status.PENDING_APPROVAL:
        raise ValidationError("This bill is waiting for approval. Take it back first, then delete it.")
    if bill.status != Status.DRAFT:
        raise ValidationError("Only a draft can be deleted. Void a recorded bill instead.")
    log(bill.company, actor, "vendor_bill", bill.pk, Action.DELETED, previous=snapshot(bill))
    bill.delete()
