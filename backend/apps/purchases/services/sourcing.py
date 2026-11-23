"""Purchase requests and the quotations received from vendors, up to turning either into a purchase order."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.company.constants import DocType
from apps.company.services import numbering

from . import orders, pricing
from .common import Action, ZERO, log, money, optional_amount, text
from ..models import PurchaseRequest, VendorQuotation

RStatus, QStatus = PurchaseRequest.Status, VendorQuotation.Status


# --------------------------------------------------------------------------- requests

def clean_request_items(raw):
    if not isinstance(raw, list) or not raw:
        raise ValidationError({"items": "Add at least one item."})
    if len(raw) > 100:
        raise ValidationError({"items": "At most 100 items."})
    items, total = [], ZERO
    for n, it in enumerate(raw, 1):
        if not isinstance(it, dict):
            raise ValidationError({"items": f"Item {n}: send the item as an object."})
        desc = text(it.get("description"), "items", 500, required=True, label=f"Item {n} description")
        qty = optional_amount(it.get("quantity"), f"Item {n} quantity", "items", high=Decimal("10000000"))
        if not qty:
            raise ValidationError({"items": f"Item {n}: enter a quantity of more than zero."})
        if qty.as_tuple().exponent < -3:
            raise ValidationError({"items": f"Item {n}: use at most 3 decimal places in the quantity."})
        est = optional_amount(it.get("est_rate"), f"Item {n} estimated rate", "items", high=Decimal("1000000000")) or ZERO
        total += money(qty * est)
        items.append({"description": desc, "unit": text(it.get("unit"), "items", 20, label=f"Item {n} unit"),
                      "hsn_sac": text(it.get("hsn_sac"), "items", 8, label=f"Item {n} HSN/SAC"),
                      "quantity": str(qty), "est_rate": str(est)})
    return items, total


def request_snapshot(r):
    return {"number": r.number, "title": r.title, "status": r.status, "needed_by": str(r.needed_by or ""),
            "estimated_total": str(r.estimated_total), "items": len(r.items)}


@transaction.atomic
def save_request(company, actor, *, title, items, request_date, needed_by=None, notes="", project_id=None,
                 requested_by_name="", req=None):
    if req is not None and req.status not in (RStatus.DRAFT, RStatus.REJECTED):
        raise ValidationError("Only a draft or a rejected request can be edited.")
    title = text(title, "title", 200, required=True, label="A title")
    items, total = clean_request_items(items)
    if needed_by and needed_by < request_date:
        raise ValidationError({"needed_by": "The needed-by date cannot be before the request date."})
    fields = dict(title=title, items=items, estimated_total=total, request_date=request_date, needed_by=needed_by,
                  notes=text(notes, "notes", 5000, label="Notes"), project_id=project_id,
                  requested_by_name=text(requested_by_name, "requested_by_name", 150, label="Requested by"))
    if req is None:
        number = numbering.allocate_number(company, DocType.PURCHASE_REQUEST, actor=actor, on_date=request_date).reference_number
        req = PurchaseRequest.objects.create(company=company, number=number, created_by=actor, requested_by=actor,
                                             **fields)
        if not req.requested_by_name:
            req.requested_by_name = actor.get_full_name() or actor.get_username()
            req.save(update_fields=["requested_by_name", "updated_at"])
        log(company, actor, "purchase_request", req.pk, Action.CREATED, new=request_snapshot(req))
    else:
        before = request_snapshot(req)
        for key, value in fields.items():
            setattr(req, key, value)
        req.status, req.decision_note = RStatus.DRAFT, ""
        req.save()
        log(company, actor, "purchase_request", req.pk, Action.UPDATED, previous=before, new=request_snapshot(req))
    return req


def _request_move(req, actor, *, allowed, to, reason="", note_required=False, label):
    req = PurchaseRequest.objects.select_for_update().get(pk=req.pk)
    if req.status not in allowed:
        raise ValidationError(f"A request that is {req.get_status_display().lower()} cannot be {label}.")
    if note_required and not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this request."})
    before = req.status
    req.status = to
    req.decision_note = (reason or "").strip()[:300] if to in (RStatus.REJECTED, RStatus.APPROVED) else ""
    req.save()
    log(req.company, actor, "purchase_request", req.pk, Action.UPDATED, previous={"status": before}, new={"status": to},
        reason=(reason or "").strip() or label.capitalize())
    return req


@transaction.atomic
def submit_request(req, actor):
    return _request_move(req, actor, allowed=(RStatus.DRAFT, RStatus.REJECTED), to=RStatus.SUBMITTED, label="submitted")


@transaction.atomic
def approve_request(req, actor, note=""):
    return _request_move(req, actor, allowed=(RStatus.SUBMITTED,), to=RStatus.APPROVED, reason=note, label="approved")


@transaction.atomic
def reject_request(req, actor, reason):
    return _request_move(req, actor, allowed=(RStatus.SUBMITTED,), to=RStatus.REJECTED, reason=reason, note_required=True,
                         label="rejected")


@transaction.atomic
def delete_request(req, actor):
    req = PurchaseRequest.objects.select_for_update().get(pk=req.pk)
    if req.status in (RStatus.CONVERTED,) or req.quotations.exists() or req.orders.exists():
        raise ValidationError("This request has quotations or an order attached, so it cannot be deleted.")
    log(req.company, actor, "purchase_request", req.pk, Action.DELETED, previous=request_snapshot(req))
    req.delete()


def _items_for_order(vendor, rows):
    default_rate = "18" if vendor.gst_treatment == vendor.GstTreatment.REGISTERED else "0"
    return [{"description": r["description"], "hsn_sac": r.get("hsn_sac", ""), "unit": r.get("unit", ""), "quantity": r["quantity"],
             "rate": r.get("est_rate") or r.get("rate") or "0", "discount_percent": r.get("discount_percent", "0"),
             "gst_rate": r.get("gst_rate") or default_rate} for r in rows]


@transaction.atomic
def convert_request(req, actor, vendor):
    """A draft purchase order for `vendor` with the request's items; the request is marked converted."""
    req = PurchaseRequest.objects.select_for_update().get(pk=req.pk)
    if req.status != RStatus.APPROVED:
        raise ValidationError("Only an approved request can be turned into an order.")
    today = datetime.date.today()
    order = orders.save_draft(req.company, actor, vendor=vendor, form={"items": _items_for_order(vendor, req.items), "notes": req.notes},
                              order_date=today, expected_delivery=req.needed_by if req.needed_by and req.needed_by >= today else None,
                              project_id=req.project_id, reference=req.number, source_request=req)
    req.status, req.converted_order_id = RStatus.CONVERTED, order.pk
    req.save()
    log(req.company, actor, "purchase_request", req.pk, Action.UPDATED, previous={"status": "approved"},
        new={"status": "converted", "order": str(order.pk)}, reason="Turned into a purchase order")
    return order


# --------------------------------------------------------------------------- vendor quotations

def display_status(q, today=None):
    today = today or datetime.date.today()
    if q.status == QStatus.RECEIVED and q.valid_until and q.valid_until < today:
        return QStatus.EXPIRED
    return q.status


def quotation_snapshot(q):
    return {"number": q.number, "vendor": q.vendor.name, "status": q.status, "total": str(q.grand_total),
            "valid_until": str(q.valid_until or ""), "vendor_ref": q.vendor_ref}


@transaction.atomic
def save_quotation(company, actor, *, vendor, items, quote_date, valid_until=None, request=None, vendor_ref="",
                   delivery_days=None, payment_terms="", notes="", quotation=None):
    if quotation is not None and quotation.status != QStatus.RECEIVED:
        raise ValidationError("Only a quotation that is still open can be edited.")
    if not vendor.is_active:
        raise ValidationError({"vendor": "This vendor is switched off."})
    if valid_until and valid_until < quote_date:
        raise ValidationError({"valid_until": "The quotation cannot be valid until a date before it was given."})
    if delivery_days not in (None, ""):
        try:
            delivery_days = int(delivery_days)
        except (TypeError, ValueError):
            raise ValidationError({"delivery_days": "Enter a whole number of days."})
        if not 0 <= delivery_days <= 1000:
            raise ValidationError({"delivery_days": "Delivery time must be between 0 and 1000 days."})
    else:
        delivery_days = None
    record = pricing.price(company, vendor, items)
    fields = dict(vendor=vendor, request=request, vendor_ref=text(vendor_ref, "vendor_ref", 100, label="Vendor's quotation number"),
                  quote_date=quote_date, valid_until=valid_until, form={"items": items}, record=record,
                  delivery_days=delivery_days, payment_terms=text(payment_terms, "payment_terms", 200, label="Payment terms"),
                  notes=text(notes, "notes", 5000, label="Notes"), **pricing.totals_of(record))
    if quotation is None:
        fields["number"] = numbering.allocate_number(company, DocType.VENDOR_QUOTATION, actor=actor, on_date=quote_date).reference_number
        quotation = VendorQuotation.objects.create(company=company, created_by=actor, **fields)
        log(company, actor, "vendor_quotation", quotation.pk, Action.CREATED, new=quotation_snapshot(quotation))
    else:
        before = quotation_snapshot(quotation)
        for key, value in fields.items():
            setattr(quotation, key, value)
        quotation.save()
        log(company, actor, "vendor_quotation", quotation.pk, Action.UPDATED, previous=before, new=quotation_snapshot(quotation))
    return quotation


def _quote_move(q, actor, *, to, reason="", label):
    q = VendorQuotation.objects.select_for_update().select_related("vendor", "company").get(pk=q.pk)
    if q.status != QStatus.RECEIVED:
        raise ValidationError(f"A quotation that is {q.get_status_display().lower()} cannot be {label}.")
    if to == QStatus.ACCEPTED and display_status(q) == QStatus.EXPIRED:
        raise ValidationError("This quotation has expired. Ask the vendor for a new one.")
    if to == QStatus.REJECTED and not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this quotation."})
    q.status = to
    q.save()
    log(q.company, actor, "vendor_quotation", q.pk, Action.UPDATED, previous={"status": "received"}, new={"status": to},
        reason=(reason or "").strip() or label.capitalize())
    return q


@transaction.atomic
def accept_quotation(q, actor):
    return _quote_move(q, actor, to=QStatus.ACCEPTED, label="accepted")


@transaction.atomic
def reject_quotation(q, actor, reason):
    return _quote_move(q, actor, to=QStatus.REJECTED, reason=reason, label="rejected")


@transaction.atomic
def convert_quotation(q, actor):
    """A draft purchase order from an accepted quotation (a quotation that is still open is accepted first)."""
    q = VendorQuotation.objects.select_for_update(of=("self",)).select_related("vendor", "company", "request").get(pk=q.pk)
    if q.converted_order_id:
        raise ValidationError("This quotation has already been turned into an order.")
    if q.status == QStatus.RECEIVED:
        q = _quote_move(q, actor, to=QStatus.ACCEPTED, label="accepted")
    if q.status != QStatus.ACCEPTED:
        raise ValidationError("Only an accepted quotation can be turned into an order.")
    today = datetime.date.today()
    rows = [{**l, "rate": l["rate"]} for l in q.record["lines"]]
    order = orders.save_draft(q.company, actor, vendor=q.vendor, form={"items": _items_for_order(q.vendor, rows)}, order_date=today,
                              expected_delivery=today + datetime.timedelta(days=q.delivery_days) if q.delivery_days else None,
                              reference=q.vendor_ref or q.number, source_request=q.request, source_quotation=q,
                              project_id=q.request.project_id if q.request_id else None)
    q.converted_order_id = order.pk
    q.save()
    if q.request_id and q.request.status not in (RStatus.CONVERTED, RStatus.REJECTED):
        q.request.status, q.request.converted_order_id = RStatus.CONVERTED, order.pk
        q.request.save()
    log(q.company, actor, "vendor_quotation", q.pk, Action.UPDATED, new={"order": str(order.pk)}, reason="Turned into a purchase order")
    return order


@transaction.atomic
def delete_quotation(q, actor):
    q = VendorQuotation.objects.select_for_update().select_related("vendor").get(pk=q.pk)
    if q.status != QStatus.RECEIVED or q.converted_order_id:
        raise ValidationError("Only a quotation that is still open can be deleted.")
    log(q.company, actor, "vendor_quotation", q.pk, Action.DELETED, previous=quotation_snapshot(q))
    q.delete()
