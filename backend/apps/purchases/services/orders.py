"""Purchase orders: draft, approval, issue (PDF on the letterhead), receiving goods, closing and cancelling.

    draft -> pending_approval -> approved -> issued -> partially_received -> received -> closed
         +----------------------------------+  a draft can also be issued directly when nobody needs approval
    cancelled: from any state before goods arrive and a bill is booked.

`approve` and `reject` are plain service functions: the approvals module calls them from its handlers, and a person with the
purchases.manage permission can call them through the API when no approval rules are set up.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.company.constants import DocType
from apps.company.services import documents, numbering, quotations

from . import pricing
from .common import Action, ZERO, log, parse_date, text
from ..models import PurchaseOrder, PurchaseReceipt, VendorBill

Status = PurchaseOrder.Status


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def snapshot(o):
    return {"vendor": o.vendor.name, "status": o.status, "order_date": str(o.order_date), "number": o.number,
            "total": str(o.grand_total), "expected_delivery": str(o.expected_delivery or "")}


def clean_form(form):
    if not isinstance(form, dict):
        raise ValidationError({"form": "Send the order as an object."})
    out = {"items": form.get("items"), "terms": text(form.get("terms"), "terms", 10000, label="Terms"),
           "notes": text(form.get("notes"), "notes", 5000, label="Notes")}
    return out


def work_out(company, vendor, form):
    return pricing.price(company, vendor, form.get("items"))


@transaction.atomic
def save_draft(company, actor, *, vendor, form, order_date, expected_delivery=None, project_id=None, reference="",
               delivery_address="", source_request=None, source_quotation=None, order=None):
    if order is not None and order.status != Status.DRAFT:
        raise ValidationError("Only a draft can be edited. Cancel the order and make a new one.")
    if not vendor.is_active:
        raise ValidationError({"vendor": "This vendor is switched off."})
    if expected_delivery and expected_delivery < order_date:
        raise ValidationError({"expected_delivery": "The delivery date cannot be before the order date."})
    form = clean_form(form)
    record = work_out(company, vendor, form)
    fields = dict(vendor=vendor, order_date=order_date, expected_delivery=expected_delivery, form=form, record=record,
                  project_id=project_id, reference=text(reference, "reference", 100, label="Reference"),
                  delivery_address=text(delivery_address, "delivery_address", 500, label="Delivery address"),
                  **pricing.totals_of(record))
    if order is None:
        order = PurchaseOrder.objects.create(company=company, created_by=actor, source_request=source_request,
                                             source_quotation=source_quotation, **fields)
        log(company, actor, "purchase_order", order.pk, Action.CREATED, new=snapshot(order))
    else:
        before = snapshot(order)
        for key, value in fields.items():
            setattr(order, key, value)
        order.decision_note = ""
        order.save()
        log(company, actor, "purchase_order", order.pk, Action.UPDATED, previous=before, new=snapshot(order))
    return order


def _lock(order):
    return PurchaseOrder.objects.select_for_update(of=("self",)).select_related("vendor", "company", "issued_document").get(pk=order.pk)


# --------------------------------------------------------------------------- approval

def _approvals():
    """The approvals module's API, when it is installed; None keeps purchases working on its own."""
    try:
        from apps.approvals import api
        return api if hasattr(api, "submit") else None
    except ImportError:
        return None


def approval_managed():
    """Whether decisions must go through the shared approvals workflow."""
    return _approvals() is not None


@transaction.atomic
def submit(order, actor):
    order = _lock(order)
    if order.status != Status.DRAFT:
        raise ValidationError("Only a draft can be sent for approval.")
    if order.grand_total <= 0:
        raise ValidationError({"items": "The total must be more than zero."})
    order.status, order.submitted_at, order.decision_note = Status.PENDING_APPROVAL, _now(), ""
    order.save()
    log(order.company, actor, "purchase_order", order.pk, Action.UPDATED, previous={"status": "draft"},
        new={"status": "pending_approval"}, reason="Sent for approval")
    api = _approvals()
    if api is not None:
        # The approvals handler may approve immediately when no rule applies; otherwise it remains pending.
        api.submit(order.company, kind="purchase_order", source_id=str(order.pk),
                   title=f"Purchase order to {order.vendor.name}", amount=order.grand_total, requested_by=actor,
                   summary=f"{len(order.record['lines'])} item(s), total {order.grand_total}", url="/purchases/orders/" + str(order.pk),
                   project_id=order.project_id)
        order.refresh_from_db()
    return order


def _withdraw_request(order, actor):
    """The order was cancelled or deleted while waiting: close its open approval request."""
    api = _approvals()
    if api is not None and order.status == Status.PENDING_APPROVAL:
        api.cancel_for_source("purchase_order", order.pk, actor)


@transaction.atomic
def approve(order, actor, note=""):
    order = _lock(order)
    if order.status != Status.PENDING_APPROVAL:
        raise ValidationError("Only an order that is waiting for approval can be approved.")
    order.status, order.approved_by, order.approved_at = Status.APPROVED, actor if getattr(actor, "is_authenticated", False) else None, _now()
    order.decision_note = (note or "").strip()[:300]
    order.save()
    log(order.company, actor, "purchase_order", order.pk, Action.UPDATED, previous={"status": "pending_approval"},
        new={"status": "approved"}, reason=order.decision_note or "Approved")
    return order


@transaction.atomic
def reject(order, actor, reason):
    order = _lock(order)
    if order.status != Status.PENDING_APPROVAL:
        raise ValidationError("Only an order that is waiting for approval can be rejected.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this order."})
    order.status, order.decision_note = Status.DRAFT, f"Rejected: {reason.strip()}"[:300]  # back to draft to be corrected
    order.save()
    log(order.company, actor, "purchase_order", order.pk, Action.UPDATED, previous={"status": "pending_approval"},
        new={"status": "draft"}, reason=f"Rejected: {reason.strip()}")
    return order


# --------------------------------------------------------------------------- issue

def pdf_content(order):
    rec, vendor = order.record, order.vendor
    cur = rec["currency"]
    fmt = lambda v: quotations.money(v, cur)
    lines = rec["lines"]
    gst_on = rec["gst_charged"]
    show_hsn = any(l["hsn_sac"] for l in lines)
    show_disc = any(Decimal(l["discount_percent"]) for l in lines)
    header, widths = ["No.", "Description"], [10, None]
    if show_hsn:
        header.append("HSN/SAC")
        widths.append(20)
    header += ["Qty", "Rate"]
    widths += [18, 22]
    if show_disc:
        header.append("Disc %")
        widths.append(16)
    if gst_on:
        header.append("GST %")
        widths.append(14)
    header.append(f"Amount ({cur})")
    widths.append(26)
    rows = []
    for n, l in enumerate(lines, 1):
        row = [str(n), l["description"]]
        if show_hsn:
            row.append(l["hsn_sac"])
        row += [f"{quotations.plain_number(l['quantity'])} {l['unit']}".strip(), fmt(l["rate"])]
        if show_disc:
            row.append(quotations.plain_number(l["discount_percent"]))
        if gst_on:
            row.append(quotations.plain_number(l["gst_rate"]))
        row.append(fmt(l["taxable"]))
        rows.append(row)
    t = rec["totals"]
    totals = []
    if Decimal(t["discount"]):
        totals += [["Gross amount", fmt(t["gross"])], ["Discount", "-" + fmt(t["discount"])]]
    if gst_on:
        totals.append(["Taxable amount", fmt(t["taxable"])])
        totals += [[row["label"], fmt(row["amount"])] for row in t["tax"]]
        totals.append([f"Total ({cur})", fmt(t["grand_total"])])
    else:
        totals.append([f"Total ({cur})", fmt(t["grand_total"])])
    recipient = [vendor.name, *vendor.address_lines]
    if vendor.gstin:
        recipient.append(f"GSTIN: {vendor.gstin}")
    meta = []
    if order.expected_delivery:
        meta.append(["Delivery by", order.expected_delivery.strftime("%d %B %Y")])
    if order.reference:
        meta.append(["Reference", order.reference])
    if gst_on and rec["place_of_supply"]["name"]:
        meta.append(["Place of supply", f"{rec['place_of_supply']['name']} ({rec['place_of_supply']['code']})"])
    if vendor.payment_terms_days is not None:
        meta.append(["Payment terms", f"Net {vendor.payment_terms_days} days"])
    if order.delivery_address:
        meta.append(["Deliver to", order.delivery_address])
    after = [f"Amount in words: {rec['amount_in_words']}"] if rec["amount_in_words"] else []
    if order.form.get("notes"):
        after.append(order.form["notes"])
    content = {"recipient": recipient, "meta": meta, "subject": "Purchase order",
               "table": {"columns": header, "rows": rows, "numeric_columns": list(range(header.index("Qty"), len(header))),
                         "column_widths_mm": widths},
               "totals": totals, "after_totals": after, "show_signature": True}
    if order.form.get("terms"):
        content["terms"] = order.form["terms"]
    return content


@transaction.atomic
def issue(order, actor):
    order = _lock(order)
    if order.status not in (Status.DRAFT, Status.APPROVED):
        raise ValidationError("Only a draft or an approved order can be issued.")
    if not order.vendor.is_active:
        raise ValidationError({"vendor": "This vendor is switched off."})
    if order.grand_total <= 0:
        raise ValidationError({"items": "The total must be more than zero."})
    order.record = work_out(order.company, order.vendor, order.form)  # prices as they stand today
    order.taxable_total, order.tax_total, order.grand_total = (pricing.totals_of(order.record)[k] for k in
                                                               ("taxable_total", "tax_total", "grand_total"))
    issued = documents.issue_document(DocType.PURCHASE_ORDER, actor=actor, content=pdf_content(order), title="Purchase Order",
                                      issue_date=order.order_date, source_module="purchases", source_id=str(order.pk))
    order.number, order.issued_document, order.status, order.issued_by = issued.reference_number, issued, Status.ISSUED, actor
    order.save()
    log(order.company, actor, "purchase_order", order.pk, Action.ISSUED, new={"number": order.number, "status": "issued",
                                                                           "total": str(order.grand_total)}, reason="Issued")
    return order


# --------------------------------------------------------------------------- receiving

def received_quantities(order):
    got = {}
    for r in order.receipts.all():
        for row in r.lines:
            got[int(row["line"])] = got.get(int(row["line"]), ZERO) + Decimal(str(row["quantity"]))
    return got


def billed_quantities(order):
    billed = {}
    for b in VendorBill.objects.filter(order=order).exclude(status=VendorBill.Status.VOID):
        for line in b.record.get("lines", []):
            if line.get("po_line") is not None:
                billed[int(line["po_line"])] = billed.get(int(line["po_line"]), ZERO) + Decimal(line["quantity"])
    return billed


@transaction.atomic
def receive(order, actor, *, date, lines, notes=""):
    """Record goods received. `lines`: [{"line": index, "quantity": n}]; at most what is still expected on each line."""
    order = _lock(order)
    if order.status not in PurchaseOrder.OPEN:
        raise ValidationError("Goods can be received only against an issued order that is not yet fully received.")
    if date < order.order_date:
        raise ValidationError({"date": "The goods cannot be received before the order date."})
    if not isinstance(lines, list) or not lines:
        raise ValidationError({"lines": "Enter the quantity received for at least one item."})
    got = received_quantities(order)
    order_lines = order.record["lines"]
    rows, seen = [], set()
    for row in lines:
        try:
            idx, qty = int(row.get("line")), Decimal(str(row.get("quantity") or 0))
        except (TypeError, ValueError, ArithmeticError, AttributeError):
            raise ValidationError({"lines": "Each line needs an item and a quantity."})
        if not qty.is_finite():
            raise ValidationError({"lines": "Enter a finite quantity received."})
        if qty <= 0:
            continue
        if idx in seen or not 0 <= idx < len(order_lines):
            raise ValidationError({"lines": "Choose each item of the order once."})
        seen.add(idx)
        if qty.as_tuple().exponent < -3:
            raise ValidationError({"lines": "Use at most 3 decimal places in a quantity."})
        left = Decimal(order_lines[idx]["quantity"]) - got.get(idx, ZERO)
        if qty > left:
            raise ValidationError({"lines": f"Item {idx + 1} has only {left.normalize():f} still expected."})
        rows.append({"line": idx, "quantity": str(qty)})
    if not rows:
        raise ValidationError({"lines": "Enter the quantity received for at least one item."})
    PurchaseReceipt.objects.create(order=order, date=date, lines=rows, notes=(notes or "").strip()[:300], received_by=actor)
    got = received_quantities(order)
    complete = all(got.get(i, ZERO) >= Decimal(l["quantity"]) for i, l in enumerate(order_lines))
    before = order.status
    order.status = Status.RECEIVED if complete else Status.PARTIALLY_RECEIVED
    order.save()
    log(order.company, actor, "purchase_order", order.pk, Action.UPDATED, previous={"status": before},
        new={"status": order.status, "received": ", ".join(f"item {r['line'] + 1}: {r['quantity']}" for r in rows)},
        reason="Goods received")
    return order


@transaction.atomic
def close(order, actor, reason=""):
    order = _lock(order)
    if order.status not in (Status.ISSUED, Status.PARTIALLY_RECEIVED, Status.RECEIVED):
        raise ValidationError("Only an issued order can be closed.")
    if order.status != Status.RECEIVED and not (reason or "").strip():
        raise ValidationError({"change_reason": "Give a reason for closing an order that has not been fully received."})
    before = order.status
    order.status = Status.CLOSED
    order.save()
    log(order.company, actor, "purchase_order", order.pk, Action.UPDATED, previous={"status": before}, new={"status": "closed"},
        reason=(reason or "Closed").strip())
    return order


@transaction.atomic
def cancel(order, actor, reason):
    order = _lock(order)
    if order.status not in (Status.DRAFT, Status.PENDING_APPROVAL, Status.APPROVED, Status.ISSUED):
        raise ValidationError("This order can no longer be cancelled. Close it instead.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give a reason for cancelling this order."})
    if order.receipts.exists():
        raise ValidationError("Goods have been received against this order. Close it instead.")
    if order.bills.exclude(status=VendorBill.Status.VOID).exists():
        raise ValidationError("A bill is booked against this order. Void the bill first.")
    before = order.status
    _withdraw_request(order, actor)
    order.status, order.cancel_reason = Status.CANCELLED, reason.strip()[:300]
    order.save()
    issued = order.issued_document
    if issued is not None:
        issued.status = issued.Status.VOID
        issued.save(update_fields=["status", "updated_at"])
        if issued.reference_id:
            numbering.void_number(issued.reference, reason.strip(), actor)
    log(order.company, actor, "purchase_order", order.pk, Action.DEACTIVATED, previous={"status": before},
        new={"status": "cancelled"}, reason=reason.strip())
    return order


@transaction.atomic
def delete(order, actor):
    order = _lock(order)
    if order.status != Status.DRAFT:
        raise ValidationError("Only a draft can be deleted. Cancel an issued order instead.")
    log(order.company, actor, "purchase_order", order.pk, Action.DELETED, previous=snapshot(order))
    order.delete()


def resubmit(order, actor):
    """A rejected or withdrawn request was sent again from the Approvals module: the draft waits for approval once more."""
    order = _lock(order)
    if order.status == Status.DRAFT and order.grand_total > 0:
        order.status, order.submitted_at, order.decision_note = Status.PENDING_APPROVAL, _now(), ""
        order.save()
        log(order.company, actor, "purchase_order", order.pk, Action.UPDATED, previous={"status": "draft"},
            new={"status": "pending_approval"}, reason="Sent for approval again")
    return order


def withdraw(order, actor):
    """The approvals module says the requester withdrew the request: the order goes back to a draft."""
    order = _lock(order)
    if order.status == Status.PENDING_APPROVAL:
        order.status = Status.DRAFT
        order.save()
        log(order.company, actor, "purchase_order", order.pk, Action.UPDATED, previous={"status": "pending_approval"},
            new={"status": "draft"}, reason="Approval request withdrawn")
    return order
