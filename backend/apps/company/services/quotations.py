"""Quotation builder: line prices, discounts, GST and totals are worked out here, once, so the page and the PDF
always show the same figures.

Rates come from the line (or the default tax code) and must be a currently applicable GST rate; the CGST/SGST/IGST
split follows `services.tax`. Confirm both against current GST notifications.
"""
import datetime
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.core import india

from . import tax

CENT = Decimal("0.01")
HSN_RE = re.compile(r"^[0-9]{2,8}$")
MAX_LINES = 100


class QuotationError(ValueError):
    """The quotation input is not usable; the message is shown to the person filling it in."""


# --------------------------------------------------------------------------- formatting

def money(value, currency="INR"):
    """1234567.5 -> 12,34,567.50 for INR (lakh/crore grouping), 1,234,567.50 for other currencies."""
    value = Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    if currency != "INR":
        return f"{value:,.2f}"
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(value):.2f}".split(".")
    if len(whole) > 3:
        head, tail, groups = whole[:-3], whole[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{whole}.{frac}"


def plain_number(value):
    """18.00 -> 18, 2.500 -> 2.5."""
    d = Decimal(value)
    return f"{d.normalize():f}" if d != d.to_integral_value() else f"{int(d)}"


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_thousand(n):
    words = []
    hundreds, n = divmod(n, 100)
    if hundreds:
        words.append(f"{_ONES[hundreds]} Hundred")
    if n >= 20:
        words.append(_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        words.append(_ONES[n])
    return " ".join(words)


def _words(n):
    if n == 0:
        return "Zero"
    parts = []
    for size, name in ((10 ** 7, "Crore"), (10 ** 5, "Lakh"), (1000, "Thousand")):
        q, n = divmod(n, size)
        if q:
            parts.append(f"{_words(q)} {name}")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(value):
    """INR One Lakh Twenty Three Thousand Four Hundred Fifty Six and Fifty Paise Only."""
    total_paise = int((Decimal(value) * 100).to_integral_value(ROUND_HALF_UP))
    rupees, paise = divmod(total_paise, 100)
    text = f"INR {_words(rupees)}"
    if paise:
        text += f" and {_words(paise)} Paise"
    return text + " Only"


# --------------------------------------------------------------------------- input

def _text(value, label, limit, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise QuotationError(f"{label} must be text.")
    value = value.strip()
    if required and not value:
        raise QuotationError(f"{label} is required.")
    if len(value) > limit:
        raise QuotationError(f"{label} is too long (maximum {limit} characters).")
    return value


def _number(value, label, *, places, low, high):
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise QuotationError(f"{label} must be a number.")
    if not d.is_finite():
        raise QuotationError(f"{label} must be a number.")
    if d.as_tuple().exponent < -places:
        raise QuotationError(f"{label}: use at most {places} decimal places.")
    if not low <= d <= high:
        raise QuotationError(f"{label} must be between {low} and {high}.")
    return d


def parse(raw):
    """Validate the quotation input and return it with numbers as Decimals. Raises QuotationError."""
    if not isinstance(raw, dict):
        raise QuotationError("The quotation must be an object.")
    customer = raw.get("customer")
    if not isinstance(customer, dict):
        raise QuotationError("Enter the customer.")
    address = customer.get("address") or []
    if isinstance(address, str):
        address = address.splitlines()
    if not isinstance(address, list) or len(address) > 8:
        raise QuotationError("Customer address: send at most 8 lines.")
    gstin = _text(customer.get("gstin"), "Customer GSTIN", 15).upper()
    if gstin:
        problem = india.validate_gstin(gstin)
        if problem:
            raise QuotationError(f"Customer GSTIN: {problem}")
    state_code = _text(customer.get("state_code"), "Customer state", 2)
    if state_code and state_code not in india.GST_STATES:
        raise QuotationError("Customer state: unknown GST state code.")
    out = {
        "customer": {
            "name": _text(customer.get("name"), "Customer name", 200, required=True),
            "address": [line for line in (_text(a, "Customer address", 200) for a in address) if line],
            "gstin": gstin, "state_code": state_code,
        },
        "enquiry_ref": _text(raw.get("enquiry_ref"), "Customer reference", 100),
        "subject": _text(raw.get("subject"), "Subject", 200),
        "intro": _text(raw.get("intro"), "Introduction", 10000),
        "notes": _text(raw.get("notes"), "Notes", 10000),
        "terms": _text(raw.get("terms"), "Terms and conditions", 30000),
        "include_bank": bool(raw.get("include_bank", True)),
    }
    sections = raw.get("sections") or []
    if not isinstance(sections, list) or len(sections) > 20:
        raise QuotationError("Sections: send at most 20.")
    out["sections"] = []
    for s in sections:
        if not isinstance(s, dict):
            raise QuotationError("Each section needs a heading and text.")
        heading, body = _text(s.get("heading"), "Section heading", 200), _text(s.get("body"), "Section text", 20000)
        if heading or body:  # blank rows are ignored
            out["sections"].append({"heading": heading, "body": body})
    details = raw.get("details") or []
    if not isinstance(details, list) or len(details) > 30:
        raise QuotationError("Key details: send at most 30 rows.")
    out["details"] = []
    for d in details:
        if not isinstance(d, dict):
            raise QuotationError("Each key detail needs a label and a value.")
        label, value = _text(d.get("label"), "Detail label", 100), _text(d.get("value"), "Detail value", 1000)
        if bool(label) != bool(value):
            raise QuotationError(f"Key detail '{label or value[:30]}' needs both a label and a value.")
        if label:
            out["details"].append({"label": label, "value": value})
    out["show_signature"] = bool(raw.get("show_signature", False))
    valid_until = _text(raw.get("valid_until"), "Valid until", 10)
    try:
        out["valid_until"] = datetime.date.fromisoformat(valid_until) if valid_until else None
    except ValueError:
        raise QuotationError("Valid until must be a date (YYYY-MM-DD).")

    items = raw.get("items")
    if not isinstance(items, list) or not items:
        raise QuotationError("Add at least one item.")
    if len(items) > MAX_LINES:
        raise QuotationError(f"A quotation can have at most {MAX_LINES} items.")
    out["items"] = []
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise QuotationError(f"Item {n}: send the item as an object.")
        label = f"Item {n}"
        hsn = _text(item.get("hsn_sac"), f"{label} HSN/SAC", 8)
        if hsn and not HSN_RE.match(hsn):
            raise QuotationError(f"{label}: HSN/SAC must be 2 to 8 digits.")
        gst_rate = item.get("gst_rate")
        out["items"].append({
            "description": _text(item.get("description"), f"{label} description", 500, required=True),
            "hsn_sac": hsn,
            "unit": _text(item.get("unit"), f"{label} unit", 20),
            "quantity": _number(item.get("quantity"), f"{label} quantity", places=3, low=Decimal("0.001"),
                                high=Decimal("10000000")),
            "rate": _number(item.get("rate"), f"{label} rate", places=4, low=Decimal(0), high=Decimal("1000000000")),
            "discount_percent": _number(item.get("discount_percent") or 0, f"{label} discount", places=2,
                                        low=Decimal(0), high=Decimal(100)),
            "gst_rate": None if gst_rate in (None, "") else _number(
                gst_rate, f"{label} GST rate", places=2, low=Decimal(0), high=Decimal(100)),
        })
    return out


# --------------------------------------------------------------------------- build

def _default_rate(tax_settings):
    for code in tax_settings.tax_codes or []:
        if isinstance(code, dict) and str(code.get("code")) == tax_settings.default_tax_code:
            return Decimal(str(code["rate"]))
    return None


def _validity(parsed, policy, issue_date):
    if parsed["valid_until"]:
        if parsed["valid_until"] < issue_date:
            raise ValidationError({"content": "The quotation cannot be valid until a date before it is issued."})
        return parsed["valid_until"]
    days = getattr(policy, "quotation_validity_days", None)
    return issue_date + datetime.timedelta(days=days) if days else None


def build(company, resolved, raw, issue_date):
    """Work out the lines and totals. Returns (record, content): the JSON-safe working to keep with the issued
    document, and the content the PDF renderer prints."""
    try:
        parsed = parse(raw)
    except QuotationError as exc:
        raise ValidationError({"content": str(exc)})
    ts, currency = resolved.tax_settings, company.default_currency or "INR"
    gst_on = ts.gst_applicable and ts.gst_treatment == "regular"
    customer = parsed["customer"]

    supplier = place = ""
    if gst_on:
        supplier = tax.supplier_state_code(company)
        if not supplier:
            raise ValidationError({"content": "Set the registered office state code or GSTIN so GST can be worked out."})
        place = (customer["state_code"] or (customer["gstin"][:2] if customer["gstin"] else "")
                 or ts.default_place_of_supply_state_code or supplier)
    default_rate = _default_rate(ts) if gst_on else None
    current_rates = {Decimal(str(r)) for r in settings.GST_CURRENT_RATES}

    lines, tax_rows = [], {}
    gross_total = discount_total = taxable_total = tax_total = Decimal("0.00")
    for n, item in enumerate(parsed["items"], 1):
        gross = (item["quantity"] * item["rate"]).quantize(CENT, rounding=ROUND_HALF_UP)
        discount = (gross * item["discount_percent"] / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        taxable = gross - discount
        line_tax, rate = Decimal("0.00"), None
        if gst_on:
            rate = item["gst_rate"] if item["gst_rate"] is not None else default_rate
            if rate is None:
                raise ValidationError({"content": f"Item {n}: choose a GST rate."})
            if rate not in current_rates:
                raise ValidationError({"content": (
                    f"Item {n}: {plain_number(rate)}% is not a currently applicable GST rate "
                    f"({', '.join(plain_number(r) for r in sorted(current_rates))}%).")})
            split = tax.calculate_gst(taxable, rate, supplier_state_code=supplier, place_of_supply_code=place,
                                      treatment="regular")
            line_tax = split["total_tax"]
            half = rate / 2
            # Zero amounts (a 0% line) are left out of the breakup; UTGST replaces SGST in some union territories.
            for key, label, shown in (("cgst", "CGST", half), ("sgst", "SGST", half), ("utgst", "UTGST", half),
                                      ("igst", "IGST", rate)):
                if split[key]:
                    tax_rows[(label, shown)] = tax_rows.get((label, shown), Decimal("0.00")) + split[key]
        gross_total += gross
        discount_total += discount
        taxable_total += taxable
        tax_total += line_tax
        lines.append({
            "description": item["description"], "hsn_sac": item["hsn_sac"], "unit": item["unit"],
            "quantity": item["quantity"], "rate": item["rate"], "discount_percent": item["discount_percent"],
            "gst_rate": rate, "gross": gross, "discount": discount, "taxable": taxable, "tax": line_tax,
            "total": taxable + line_tax,
        })
    grand_total = taxable_total + tax_total
    valid_until = _validity(parsed, resolved.policy, issue_date)

    state_name = india.GST_STATES.get(place, "") if place else ""
    record = {
        "customer": customer, "enquiry_ref": parsed["enquiry_ref"], "subject": parsed["subject"],
        "valid_until": valid_until.isoformat() if valid_until else "", "currency": currency,
        "gst_applied": gst_on, "supplier_state_code": supplier,
        "place_of_supply": {"code": place, "name": state_name} if gst_on else None,
        "supply_type": ("intra_state" if supplier == place and place != "97" else "inter_state") if gst_on else "",
        "lines": [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in line.items()} for line in lines],
        "totals": {
            "gross": str(gross_total), "discount": str(discount_total), "taxable": str(taxable_total),
            "tax": [{"label": f"{label} @ {plain_number(rate)}%", "amount": str(amount)}
                    for (label, rate), amount in sorted(tax_rows.items(), key=lambda kv: (kv[0][0], kv[0][1]))],
            "tax_total": str(tax_total), "grand_total": str(grand_total),
        },
        "amount_in_words": amount_in_words(grand_total) if currency == "INR" else "",
        "sections": parsed["sections"], "details": parsed["details"], "show_signature": parsed["show_signature"],
    }
    return record, _content(record, parsed, resolved, valid_until, lines, tax_rows, gross_total, discount_total,
                            taxable_total, tax_total, grand_total, currency)


def _content(record, parsed, resolved, valid_until, lines, tax_rows, gross_total, discount_total, taxable_total,
             tax_total, grand_total, currency):
    ts, customer, gst_on = resolved.tax_settings, parsed["customer"], record["gst_applied"]
    fmt = lambda v: money(v, currency)
    show_hsn = ts.show_hsn_sac and any(line["hsn_sac"] for line in lines)
    show_discount = any(line["discount_percent"] for line in lines)

    # Fixed widths (mm) keep the headings on one line; the description (None) takes the rest of the page width.
    header, widths = ["No.", "Description"], [10, None]
    if show_hsn:
        header.append("HSN/SAC")
        widths.append(20)
    header += ["Qty", "Rate"]
    widths += [18, 22]
    if show_discount:
        header.append("Disc %")
        widths.append(18)
    if gst_on:
        header.append("GST %")
        widths.append(15)
    header.append(f"Amount ({currency})")
    widths.append(26)

    rows = []
    for n, line in enumerate(lines, 1):
        row = [str(n), line["description"]]
        if show_hsn:
            row.append(line["hsn_sac"])
        row += [f"{plain_number(line['quantity'])} {line['unit']}".strip(), fmt(line["rate"])]
        if show_discount:
            row.append(plain_number(line["discount_percent"]))
        if gst_on:
            row.append(plain_number(line["gst_rate"]))
        row.append(fmt(line["taxable"]))
        rows.append(row)
    numeric = list(range(header.index("Qty"), len(header)))

    totals = []
    if discount_total:
        totals += [["Gross amount", fmt(gross_total)], ["Discount", "-" + fmt(discount_total)]]
    if gst_on:
        totals.append(["Taxable amount", fmt(taxable_total)])
        if ts.show_tax_breakup:
            totals += [[f"{label} @ {plain_number(rate)}%", fmt(amount)] for (label, rate), amount in sorted(tax_rows.items())]
        else:
            totals.append(["GST", fmt(tax_total)])
        totals.append([f"Grand total ({currency})", fmt(grand_total)])
    else:
        totals.append([f"Total ({currency})", fmt(grand_total)])

    recipient = [customer["name"], *customer["address"]]
    if customer["gstin"]:
        recipient.append(f"GSTIN: {customer['gstin']}")
    meta = []
    if valid_until:
        meta.append(["Valid until", valid_until.strftime("%d %B %Y")])
    if parsed["enquiry_ref"]:
        meta.append(["Your reference", parsed["enquiry_ref"]])
    if gst_on and ts.show_place_of_supply and record["place_of_supply"]["name"]:
        meta.append(["Place of supply", f"{record['place_of_supply']['name']} ({record['place_of_supply']['code']})"])

    after = []
    if record["amount_in_words"]:
        after.append(f"Amount in words: {record['amount_in_words']}")
    if parsed["notes"]:
        after.append(parsed["notes"])
    content = {
        "recipient": recipient, "meta": meta, "table": {"columns": header, "rows": rows, "numeric_columns": numeric,
                                                        "column_widths_mm": widths},
        "totals": totals, "after_totals": after, "include_bank": parsed["include_bank"],
    }
    # The company seal is stamped on the printed page, so the "For <company> / Authorised Signatory" block is opt-in.
    content["show_signature"] = parsed["show_signature"]
    if parsed["details"]:
        content["details"] = [[d["label"], d["value"]] for d in parsed["details"]]
    if parsed["sections"]:
        content["closing_sections"] = parsed["sections"]
    if parsed["subject"]:
        content["subject"] = parsed["subject"]
    if parsed["intro"]:
        content["paragraphs"] = [p.strip() for p in re.split(r"\n\s*\n", parsed["intro"]) if p.strip()]
    if parsed["terms"]:
        content["terms"] = parsed["terms"]
    return content
