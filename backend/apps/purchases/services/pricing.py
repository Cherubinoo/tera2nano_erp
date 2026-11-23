"""Line prices, discounts and GST for the documents we receive or send to vendors.

The company is the buyer, so the place of supply is the company's own state: a vendor in the same state charges CGST and
SGST (UTGST in some union territories), a vendor in another state charges IGST. An unregistered or overseas vendor
cannot charge GST, so those lines carry none. Rates must be a currently applicable GST rate. Confirm both against current
GST notifications.
"""
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.accounting.models import Account
from apps.company.services import quotations, tax
from apps.core import india

from .common import CENT, ZERO, money

HSN_RE = re.compile(r"^[0-9]{2,8}$")
MAX_LINES = 100


def _err(message):
    raise ValidationError({"items": message})


def _number(value, label, *, places, low, high):
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        _err(f"{label} must be a number.")
    if not d.is_finite():
        _err(f"{label} must be a number.")
    if d.as_tuple().exponent < -places:
        _err(f"{label}: use at most {places} decimal places.")
    if not low <= d <= high:
        _err(f"{label} must be between {low} and {high}.")
    return d


def _text(value, label, limit, required=False):
    value = "" if value is None else value
    if not isinstance(value, str):
        _err(f"{label} must be text.")
    value = value.strip()
    if required and not value:
        _err(f"{label} is required.")
    if len(value) > limit:
        _err(f"{label} is too long (at most {limit} characters).")
    return value


def clean_items(raw):
    """The items as entered, with numbers as Decimals. Raises ValidationError({"items": ...})."""
    if not isinstance(raw, list) or not raw:
        _err("Add at least one item.")
    if len(raw) > MAX_LINES:
        _err(f"At most {MAX_LINES} items.")
    items = []
    for n, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            _err(f"Item {n}: send the item as an object.")
        label = f"Item {n}"
        hsn = _text(item.get("hsn_sac"), f"{label} HSN/SAC", 8)
        if hsn and not HSN_RE.match(hsn):
            _err(f"{label}: HSN/SAC must be 2 to 8 digits.")
        gst_rate = item.get("gst_rate")
        po_line = item.get("po_line")
        try:
            po_line = None if po_line in (None, "") else int(po_line)
        except (TypeError, ValueError):
            _err(f"{label}: the order line is not valid.")
        items.append({
            "description": _text(item.get("description"), f"{label} description", 500, required=True),
            "hsn_sac": hsn, "unit": _text(item.get("unit"), f"{label} unit", 20),
            "quantity": _number(item.get("quantity"), f"{label} quantity", places=3, low=Decimal("0.001"), high=Decimal("10000000")),
            "rate": _number(item.get("rate"), f"{label} rate", places=4, low=Decimal(0), high=Decimal("1000000000")),
            "discount_percent": _number(item.get("discount_percent") or 0, f"{label} discount", places=2, low=Decimal(0), high=Decimal(100)),
            "gst_rate": None if gst_rate in (None, "") else _number(gst_rate, f"{label} GST rate", places=2, low=Decimal(0), high=Decimal(100)),
            "account": str(item.get("account") or ""),
            "po_line": po_line,
        })
    return items


def company_state(company):
    code = tax.supplier_state_code(company)
    if not code:
        raise ValidationError({"vendor": "Set the company's registered office state (or GSTIN) so GST can be worked out."})
    return code


def usable_account(company, ref, label):
    """An active account that a purchase can be booked to: an expense, cost, or an asset being bought."""
    try:
        acct = Account.objects.filter(pk=uuid.UUID(str(ref)), company=company).first()
    except ValueError:
        acct = None
    if acct is None:
        _err(f"{label}: choose a valid account.")
    if not acct.is_active:
        _err(f"{label}: {acct.code} {acct.name} is switched off.")
    if acct.type != "expense" and acct.subtype not in ("fixed_asset", "other_current_asset"):
        _err(f"{label}: choose an expense account (or an asset account for something that is being capitalised).")
    return acct


def price(company, vendor, raw_items, *, default_account=None, with_accounts=False):
    """Work out lines and totals. Returns the record kept with the document (JSON-safe; amounts as text)."""
    items = clean_items(raw_items)
    buyer_state = company_state(company)
    vendor_state = vendor.state_code
    charges_gst = vendor.gst_treatment == vendor.GstTreatment.REGISTERED
    current_rates = {Decimal(str(r)) for r in settings.GST_CURRENT_RATES}
    intra = bool(vendor_state) and vendor_state == buyer_state and buyer_state != "97"
    lines, buckets = [], {"cgst": ZERO, "sgst": ZERO, "igst": ZERO}
    gross_t = disc_t = taxable_t = tax_t = ZERO
    for n, item in enumerate(items, 1):
        gross = (item["quantity"] * item["rate"]).quantize(CENT, rounding=ROUND_HALF_UP)
        discount = (gross * item["discount_percent"] / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        taxable = gross - discount
        rate = item["gst_rate"]
        cgst = sgst = igst = ZERO
        if charges_gst:
            if rate is None:
                _err(f"Item {n}: choose a GST rate.")
            if rate not in current_rates:
                _err(f"Item {n}: {quotations.plain_number(rate)}% is not a currently applicable GST rate "
                     f"({', '.join(quotations.plain_number(r) for r in sorted(current_rates))}%).")
            if rate > 0:
                if not vendor_state:
                    raise ValidationError({"vendor": "Set the vendor's state so the GST can be split."})
                split = tax.calculate_gst(taxable, rate, supplier_state_code=vendor_state, place_of_supply_code=buyer_state)
                cgst, sgst, igst = split["cgst"], split["sgst"] + split["utgst"], split["igst"]
        else:
            rate = Decimal("0")  # an unregistered or overseas vendor cannot charge GST
        line_tax = cgst + sgst + igst
        account = ""
        if with_accounts:
            if item["account"]:
                account = str(usable_account(company, item["account"], f"Item {n}").pk)
            elif default_account is not None:
                account = str(default_account.pk)
        gross_t += gross
        disc_t += discount
        taxable_t += taxable
        tax_t += line_tax
        buckets["cgst"] += cgst
        buckets["sgst"] += sgst
        buckets["igst"] += igst
        lines.append({
            "description": item["description"], "hsn_sac": item["hsn_sac"], "unit": item["unit"],
            "quantity": item["quantity"], "rate": item["rate"], "discount_percent": item["discount_percent"],
            "gst_rate": rate, "gross": gross, "discount": discount, "taxable": taxable, "cgst": cgst, "sgst": sgst,
            "igst": igst, "tax": line_tax, "total": taxable + line_tax, "account": account, "po_line": item["po_line"],
        })
    grand = taxable_t + tax_t
    rate_rows = {}
    for line in lines:  # the breakup printed under the totals, per rate: CGST @ 9%, SGST @ 9%, IGST @ 18%
        r = line["gst_rate"]
        for key, label, shown in (("cgst", "CGST", r / 2), ("sgst", "SGST", r / 2), ("igst", "IGST", r)):
            if line[key]:
                rate_rows[(label, shown)] = rate_rows.get((label, shown), ZERO) + line[key]
    record = {
        "currency": company.default_currency or "INR", "gst_charged": charges_gst,
        "supply_type": ("intra_state" if intra else "inter_state") if charges_gst else "none",
        "company_state_code": buyer_state, "vendor_state_code": vendor_state,
        "place_of_supply": {"code": buyer_state, "name": india.GST_STATES.get(buyer_state, "")},
        "lines": [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in line.items()} for line in lines],
        "totals": {
            "gross": str(gross_t), "discount": str(disc_t), "taxable": str(taxable_t),
            "cgst": str(buckets["cgst"]), "sgst": str(buckets["sgst"]), "igst": str(buckets["igst"]),
            "tax": [{"label": f"{label} @ {quotations.plain_number(shown)}%", "amount": str(amount)}
                    for (label, shown), amount in sorted(rate_rows.items(), key=lambda kv: (kv[0][0], kv[0][1]))],
            "tax_total": str(tax_t), "grand_total": str(grand),
        },
        "amount_in_words": quotations.amount_in_words(grand) if (company.default_currency or "INR") == "INR" else "",
    }
    return record


def totals_of(record):
    t = record["totals"]
    return {"taxable_total": Decimal(t["taxable"]), "tax_total": Decimal(t["tax_total"]), "grand_total": Decimal(t["grand_total"])}


def tds_for(taxable, rate):
    """TDS is withheld on the value before GST (when GST is shown separately on the invoice)."""
    return money(Decimal(taxable) * Decimal(rate) / 100)
