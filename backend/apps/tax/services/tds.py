"""TDS: the payable ledger by section (from the books), and the receivable tracker (credits customers deducted)."""
import datetime
import re
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import Account, JournalLine

from .. import constants as C
from ..models import TaxPayment, TdsCredit
from . import books, periods
from .common import ZERO, amount, money, parse_date

SECTION_RE = re.compile(C.SECTION_PATTERN)
TAN_RE = re.compile(r"^[A-Z]{4}\d{5}[A-Z]$")


def section_of(entry, memo):
    """The TDS section a posting is for: named in its memo, narration or reference; payroll is salary (192)."""
    for text in (memo, entry.narration, entry.reference):
        m = SECTION_RE.search((text or "").upper())
        if m:
            return m.group(1)
    return C.SALARY_SECTION if entry.source == "payroll" else "Unspecified"


def ledger(company, start, end):
    """TDS payable between two dates: rows, and a summary by section (opening, deducted, paid, closing)."""
    acct = Account.objects.filter(company=company, system_key="tds_payable").first()
    empty = {"from": start, "to": end, "rows": [], "sections": [], "opening": ZERO, "deducted": ZERO, "paid": ZERO, "closing": ZERO}
    if acct is None:
        return empty
    lines = JournalLine.objects.filter(account=acct, entry__date__lte=end).select_related("entry", "entry__reverses").order_by(
        "entry__date", "entry__created_at", "created_at")
    sections = defaultdict(lambda: {"opening": ZERO, "deducted": ZERO, "paid": ZERO})
    rows, running = [], ZERO
    opening_total = ZERO
    for l in lines:
        e = l.entry
        in_period = e.date >= start
        eff = e.reverses.source if e.source == "reversal" and e.reverses_id else e.source
        section = section_of(e, l.memo)
        if e.source == "reversal" and e.reverses_id:
            section = section_of(e.reverses, l.memo)
        is_payment = eff == books.PAYMENT_SOURCE
        net = l.credit - l.debit  # + increases what we owe
        running += net
        s = sections[section]
        if not in_period:
            s["opening"] += net
            opening_total += net
            continue
        if is_payment:
            s["paid"] -= net
        else:
            s["deducted"] += net
        rows.append({"id": str(l.id), "entry_id": str(e.id), "date": e.date, "reference": e.reference, "narration": e.narration,
                     "source": e.source, "memo": l.memo, "section": section, "party": l.party_id,
                     "deducted": l.credit if not is_payment else ZERO, "paid": l.debit if is_payment else ZERO,
                     "balance": money(running), "kind": "payment" if is_payment else "deduction"})
    sec_rows = [{"section": k, **{f: money(v[f]) for f in ("opening", "deducted", "paid")},
                 "closing": money(v["opening"] + v["deducted"] - v["paid"])} for k, v in sorted(sections.items())
                if any(v[f] for f in ("opening", "deducted", "paid"))]
    return {"from": start, "to": end, "rows": rows, "sections": sec_rows, "opening": money(opening_total),
            "deducted": money(sum((r["deducted"] for r in sec_rows), ZERO)), "paid": money(sum((r["paid"] for r in sec_rows), ZERO)),
            "closing": money(running)}


def quarter_figures(company, return_type, start, end):
    """TDS deducted and paid in a quarter. 24Q is salary (section 192); 26Q is every other section."""
    led = ledger(company, start, end)
    want = (lambda s: s == C.SALARY_SECTION) if return_type == "tds_24q" else (lambda s: s != C.SALARY_SECTION)
    return {"tds_deducted": money(sum((r["deducted"] for r in led["sections"] if want(r["section"])), ZERO)),
            "tds_paid": money(sum((r["paid"] for r in led["sections"] if want(r["section"])), ZERO))}


# --------------------------------------------------------------------------- receivable tracker

FIELDS = ("deductor_name", "deductor_tan", "section", "financial_year", "quarter", "date", "gross_amount", "tds_amount",
          "certificate_no", "certificate_date", "status", "customer_id", "notes")


def clean_credit(data, instance=None):
    """Validated field values for a TDS credit from request data (only the keys given)."""
    out, errors = {}, {}

    def text(field, label, limit, required=False, upper=False):
        if field in data:
            v = str(data[field] or "").strip()
            v = v.upper() if upper else v
            if required and not v:
                errors[field] = f"Enter the {label}."
            elif len(v) > limit:
                errors[field] = f"Use at most {limit} characters."
            else:
                out[field] = v
        elif required and instance is None:
            errors[field] = f"Enter the {label}."

    text("deductor_name", "deductor's name", 200, required=True)
    text("deductor_tan", "deductor's TAN", 10, upper=True)
    if out.get("deductor_tan") and not TAN_RE.match(out["deductor_tan"]):
        errors["deductor_tan"] = "A TAN has 4 letters, 5 digits and 1 letter, for example MUMA12345B."
    text("section", "section", 10, required=True, upper=True)
    if out.get("section") and not re.match(r"^[0-9]{3}[A-Z]{0,3}$", out["section"]):
        errors["section"] = "Use a section such as 194J, 194C or 194Q."
    text("certificate_no", "certificate number", 60)
    text("notes", "notes", 300)
    try:
        if "date" in data or instance is None:
            out["date"] = parse_date(data.get("date"), "date", required=True)
        if "certificate_date" in data:
            out["certificate_date"] = parse_date(data.get("certificate_date"), "certificate_date")
        if "tds_amount" in data or instance is None:
            out["tds_amount"] = amount(data.get("tds_amount"), "tds_amount", allow_zero=False)
        if "gross_amount" in data:
            out["gross_amount"] = None if data.get("gross_amount") in (None, "") else amount(data["gross_amount"], "gross_amount", allow_zero=False)
    except ValidationError as exc:
        errors.update({k: v[0] for k, v in exc.message_dict.items()})
    if "financial_year" in data or instance is None:
        raw = data.get("financial_year") or (periods.fy_label_for(out["date"]) if out.get("date") else "")
        try:
            periods.parse_fy(raw)
            out["financial_year"] = raw
        except ValidationError as exc:
            errors["financial_year"] = exc.message_dict["financial_year"][0]
    if "quarter" in data:
        q = data["quarter"]
        if q in (None, ""):
            out["quarter"] = None
        elif str(q) in ("1", "2", "3", "4"):
            out["quarter"] = int(q)
        else:
            errors["quarter"] = "Choose quarter 1 to 4."
    elif out.get("date") and instance is None:
        out["quarter"] = periods.quarter_of(out["date"])
    if "status" in data:
        if data["status"] not in TdsCredit.Status.values:
            errors["status"] = "Choose a valid status."
        else:
            out["status"] = data["status"]
    if "customer_id" in data:
        v = data["customer_id"]
        try:
            import uuid
            out["customer_id"] = uuid.UUID(str(v)) if v else None
        except ValueError:
            errors["customer_id"] = "Not a valid customer."
    if out.get("status") in ("received", "claimed") or (instance and instance.status in ("received", "claimed") and "status" not in data):
        cert = out.get("certificate_no", instance.certificate_no if instance else "")
        if not cert and out.get("status") in ("received", "claimed"):
            errors["certificate_no"] = "Enter the certificate number once the certificate is received."
    if errors:
        raise ValidationError(errors)
    return out


def receivable_summary(company, fy=None):
    qs = TdsCredit.objects.filter(company=company)
    if fy:
        qs = qs.filter(financial_year=fy)
    by_status = {s: ZERO for s in TdsCredit.Status.values}
    for r in qs.values("status").annotate(t=Sum("tds_amount")):
        by_status[r["status"]] = money(r["t"])
    total = sum(by_status.values(), ZERO)
    by_section = [{"section": r["section"], "amount": money(r["t"])} for r in qs.values("section").annotate(t=Sum("tds_amount")).order_by("section")]
    return {"total": total, "by_status": by_status, "by_section": by_section,
            "books_balance": books.safe(lambda: books.account_balance(company, "tds_receivable"), ZERO, "tds receivable"),
            "count": qs.count()}
