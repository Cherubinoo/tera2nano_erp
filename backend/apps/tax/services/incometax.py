"""Advance tax schedule and the income tax worksheet (estimated tax on the LLP's profit)."""
import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.services import reports

from .. import constants as C
from ..models import IncomeTaxYear, TaxPayment, TdsCredit
from . import books, periods
from .common import ZERO, amount, money, percent, signed_amount

TEN = Decimal("10")


def round_ten(v):
    """Income and tax are rounded to the nearest ten rupees (a 5 rounds up)."""
    if not C.ROUND_TAX_TO_TEN:
        return money(v)
    return (v / TEN).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * TEN


def clean_items(value, field):
    """[{"label", "amount"}] with a label and a non-negative amount."""
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 50:
        raise ValidationError({field: "Send a list of at most 50 items."})
    out = []
    for i, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ValidationError({field: f"Item {i}: enter a label and an amount."})
        label = str(item.get("label") or "").strip()
        if not label or len(label) > 100:
            raise ValidationError({field: f"Item {i}: enter a label of up to 100 characters."})
        try:
            amt = amount(item.get("amount"), field, allow_zero=False)
        except ValidationError:
            raise ValidationError({field: f"Item {i}: enter an amount of more than zero with at most 2 decimal places."})
        out.append({"label": label, "amount": str(money(amt))})
    return out


def get_year(company, fy):
    periods.parse_fy(fy, "fy")
    return IncomeTaxYear.objects.filter(company=company, financial_year=fy).first()


def book_profit(company, fy):
    _, start, end = periods.parse_fy(fy)
    return books.safe(lambda: money(reports.profit_and_loss(company, start, end)["net_profit"]), ZERO, "profit and loss")


def tds_credit_total(company, fy):
    return money(TdsCredit.objects.filter(company=company, financial_year=fy).aggregate(t=Sum("tds_amount"))["t"] or ZERO)


def advance_paid(company, fy):
    """(advance tax paid, self-assessment tax paid) in a year, from recorded challans."""
    qs = TaxPayment.objects.filter(company=company, financial_year=fy, status="recorded")
    out = {}
    for t in ("advance_tax", "income_tax"):
        out[t] = money(qs.filter(tax_type=t).aggregate(s=Sum("total"))["s"] or ZERO)
    return out["advance_tax"], out["income_tax"]


def compute(company, fy, year=None):
    """The worksheet: books profit, add-backs, deductions, taxable income, tax at the rate, surcharge, cess, and the balance."""
    year = year if year is not None else get_year(company, fy)
    profit_books = book_profit(company, fy)
    profit = year.profit_override if year and year.profit_override is not None else profit_books
    addbacks = (year.addbacks if year else []) or []
    deductions = (year.deductions if year else []) or []
    add_total = sum((Decimal(i["amount"]) for i in addbacks), ZERO)
    ded_total = sum((Decimal(i["amount"]) for i in deductions), ZERO)
    raw_income = profit + add_total - ded_total
    taxable = round_ten(max(raw_income, ZERO))
    rate = year.rate if year else C.DEFAULT_TAX_RATE
    surcharge_pct = year.surcharge_pct if year else C.DEFAULT_SURCHARGE
    cess_pct = year.cess_pct if year else C.DEFAULT_CESS
    tax = money(taxable * rate / 100)
    surcharge = money(tax * surcharge_pct / 100)
    cess = money((tax + surcharge) * cess_pct / 100)
    total = round_ten(tax + surcharge + cess)
    tds = tds_credit_total(company, fy)
    adv, sa = advance_paid(company, fy)
    return {
        "financial_year": fy, "book_profit": profit_books, "profit_used": money(profit), "profit_overridden": bool(year and year.profit_override is not None),
        "addbacks": addbacks, "deductions": deductions, "total_addbacks": money(add_total), "total_deductions": money(ded_total),
        "income_before_rounding": money(raw_income), "taxable_income": taxable, "rate": rate, "surcharge_pct": surcharge_pct, "cess_pct": cess_pct,
        "tax": tax, "surcharge": surcharge, "cess": cess, "total_tax": total,
        "tds_credit": tds, "advance_tax_paid": adv, "self_assessment_paid": sa,
        "net_payable": money(total - tds - adv - sa),
        "notes": year.notes if year else "",
    }


def save_inputs(company, fy, data):
    """Validate and store the worksheet inputs and the estimated liability. Returns (year, changed field dict)."""
    periods.parse_fy(fy, "fy")
    year = IncomeTaxYear.objects.filter(company=company, financial_year=fy).first() or IncomeTaxYear(company=company, financial_year=fy)
    before = {f: str(getattr(year, f)) if not isinstance(getattr(year, f), list) else getattr(year, f)
              for f in ("estimated_liability", "profit_override", "addbacks", "deductions", "rate", "surcharge_pct", "cess_pct", "notes")}
    if "estimated_liability" in data:
        year.estimated_liability = None if data["estimated_liability"] in (None, "") else amount(data["estimated_liability"], "estimated_liability")
    if "profit_override" in data:
        year.profit_override = None if data["profit_override"] in (None, "") else signed_amount(data["profit_override"], "profit_override")
    if "addbacks" in data:
        year.addbacks = clean_items(data["addbacks"], "addbacks")
    if "deductions" in data:
        year.deductions = clean_items(data["deductions"], "deductions")
    if "rate" in data:
        year.rate = percent(data["rate"], "rate", C.DEFAULT_TAX_RATE)
    if "surcharge_pct" in data:
        year.surcharge_pct = percent(data["surcharge_pct"], "surcharge_pct", C.DEFAULT_SURCHARGE)
    if "cess_pct" in data:
        year.cess_pct = percent(data["cess_pct"], "cess_pct", C.DEFAULT_CESS)
    if "notes" in data:
        notes = str(data["notes"] or "").strip()
        if len(notes) > 500:
            raise ValidationError({"notes": "Use at most 500 characters."})
        year.notes = notes
    year.save()
    after = {f: str(getattr(year, f)) if not isinstance(getattr(year, f), list) else getattr(year, f) for f in before}
    return year, before, after


def schedule(company, fy, today=None, comp=None):
    """The four advance tax instalments for a year with what is required and what has been paid."""
    today = today or datetime.date.today()
    year = get_year(company, fy)
    comp = comp or compute(company, fy, year)
    estimate = year.estimated_liability if year and year.estimated_liability is not None else None
    base = estimate if estimate is not None else ZERO
    payments = list(TaxPayment.objects.filter(company=company, financial_year=fy, tax_type="advance_tax", status="recorded"))
    rows, prev_required, cumulative_paid = [], ZERO, ZERO
    for n, due, pct in periods.advance_tax_dates(fy):
        required_cum = money(base * pct / 100)
        paid_here = money(sum((p.total for p in payments if (p.instalment or 4) == n), ZERO))
        cumulative_paid += paid_here
        shortfall = max(ZERO, required_cum - cumulative_paid)
        if base == 0:
            status = "not_set"
        elif shortfall == 0:
            status = "paid"
        elif due < today:
            status = "overdue"
        elif (due - today).days <= 30:
            status = "due_soon"
        else:
            status = "upcoming"
        rows.append({"instalment": n, "due_date": due, "cumulative_pct": pct, "required": money(required_cum - prev_required),
                     "cumulative_required": required_cum, "paid": paid_here, "cumulative_paid": money(cumulative_paid),
                     "shortfall": money(shortfall), "status": status})
        prev_required = required_cum
    total_paid = money(sum((p.total for p in payments), ZERO))
    return {"financial_year": fy, "estimated_liability": estimate, "suggested_liability": money(max(comp["total_tax"] - comp["tds_credit"], ZERO)),
            "instalments": rows, "total_paid": total_paid, "balance": money(max(ZERO, base - total_paid)),
            "payments": [{"id": str(p.id), "reference": p.reference, "date": p.date, "instalment": p.instalment, "total": p.total} for p in payments]}
