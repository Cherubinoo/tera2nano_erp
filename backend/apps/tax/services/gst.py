"""GST figures from the books: the summary, the liability ledger, ITC set-off and the books-versus-filed reconciliation."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.accounting.models import JournalEntry, JournalLine
from apps.accounting.services import ledger as acct_ledger

from .. import constants as C
from ..models import TaxFiling, TaxPayment
from . import books, periods, registers
from .books import HEADS, ZERO, money

HEAD_LABEL = {"cgst": "CGST", "sgst": "SGST / UTGST", "igst": "IGST"}


# --------------------------------------------------------------------------- set-off

def compute_setoff(output, credit):
    """Utilise input credit against output tax in the order the GST law requires.

    IGST credit first against IGST, then CGST, then SGST. CGST credit against CGST, then IGST. SGST credit against SGST, then IGST.
    Returns {"matrix": [{"credit": "igst", "against": "cgst", "amount": ...}], "used_output": {head}, "used_credit": {head},
    "cash": {head: output left to pay in cash}, "credit_left": {head}}.
    """
    out = {h: money(output.get(h, 0)) for h in HEADS}
    cr = {h: money(credit.get(h, 0)) for h in HEADS}
    matrix = []

    def use(credit_head, against):
        amount = min(cr[credit_head], out[against])
        if amount > 0:
            cr[credit_head] -= amount
            out[against] -= amount
            matrix.append({"credit": credit_head, "against": against, "amount": amount})

    use("igst", "igst")
    use("igst", "cgst")
    use("igst", "sgst")
    use("cgst", "cgst")
    use("cgst", "igst")
    use("sgst", "sgst")
    use("sgst", "igst")
    used_output = {h: ZERO for h in HEADS}
    used_credit = {h: ZERO for h in HEADS}
    for m in matrix:
        used_output[m["against"]] += m["amount"]
        used_credit[m["credit"]] += m["amount"]
    return {"matrix": matrix, "used_output": used_output, "used_credit": used_credit, "cash": out, "credit_left": cr}


def ineligible_itc(company, start, end):
    """ITC (by head) the user has marked ineligible on purchases dated in the period."""
    reg = registers.purchase_register(company, start, end)
    res = {h: ZERO for h in HEADS}
    for r in reg["rows"]:
        if r["eligibility"] == "ineligible":
            for h in HEADS:
                res[h] += r[h]
    return res


def settlement(company, filing, today=None):
    """What setting off the credit would do for a GSTR-3B period, from the books as of the date it would be posted."""
    today = today or datetime.date.today()
    on = min(filing.period_end, today)
    bal = books.balances(company, on)
    bad = ineligible_itc(company, filing.period_start, filing.period_end)
    credit = {h: max(ZERO, bal["in"][h] - bad[h]) for h in HEADS}
    posted = bool(filing.setoff)
    if posted:
        # What was left to pay in cash straight after the set-off, and what GST challans for the month have paid since.
        remaining = books.balances(company, filing.setoff_date)["out"]
        total = sum(remaining.values(), ZERO)
        paid = ZERO
        for p in TaxPayment.objects.filter(company=company, tax_type="gst", status="recorded", period_start=filing.period_start):
            paid += sum((Decimal(v) for k, v in p.heads.items() if k in ("cgst", "sgst", "igst")), ZERO)
        return {"posted": True, "date": filing.setoff_date, "as_of": filing.setoff_date, "matrix": filing.setoff.get("matrix", []),
                "output": filing.setoff.get("output", {}), "credit": filing.setoff.get("credit", {}),
                "used_output": filing.setoff.get("used_output", {}), "cash_payable": remaining, "cash_total": total,
                "paid_so_far": money(paid), "still_to_pay": max(ZERO, money(total - paid)), "ineligible": bad}
    plan = compute_setoff(bal["out"], credit)
    cash_total = sum(plan["cash"].values(), ZERO)
    return {"posted": False, "date": on, "as_of": on, "matrix": plan["matrix"], "output": bal["out"], "credit": credit,
            "used_output": plan["used_output"], "cash_payable": plan["cash"], "cash_total": cash_total, "paid_so_far": ZERO,
            "still_to_pay": cash_total,
            "credit_left": plan["credit_left"], "ineligible": bad}


def _jsonable_setoff(plan, output, credit):
    return {"matrix": [{**m, "amount": str(m["amount"])} for m in plan["matrix"]],
            "output": {h: str(v) for h, v in output.items()}, "credit": {h: str(v) for h, v in credit.items()},
            "used_output": {h: str(v) for h, v in plan["used_output"].items()}}


def post_setoff(company, filing, actor):
    """Post the ITC set-off for a GSTR-3B filing: Dr GST payable (each head) / Cr GST input credit (each head)."""
    if filing.return_type != TaxFiling.Type.GSTR3B:
        raise ValidationError({"detail": "Set-off belongs to a GSTR-3B return."})
    if filing.setoff:
        raise ValidationError({"detail": "The credit has already been set off for this return. Undo it first to do it again."})
    info = settlement(company, filing)
    plan = compute_setoff(info["output"], info["credit"])
    if not plan["matrix"]:
        raise ValidationError({"detail": "There is no input credit to set off against the tax payable."})
    lines = []
    for h in HEADS:
        if plan["used_output"][h] > 0:
            lines.append({"account": books.OUT_KEY[h], "debit": plan["used_output"][h], "memo": f"ITC set-off {filing.reference}"})
    for h in HEADS:
        if plan["used_credit"][h] > 0:
            lines.append({"account": books.IN_KEY[h], "credit": plan["used_credit"][h], "memo": f"ITC set-off {filing.reference}"})
    # One posting per source id: a set-off redone after an undo gets the next suffix.
    n = JournalEntry.objects.filter(company=company, source=books.SETOFF_SOURCE, source_id__startswith=str(filing.id)).count()
    source_id = str(filing.id) if n == 0 else f"{filing.id}:{n}"
    entry = acct_ledger.post(company, date=info["date"], lines=lines, narration=f"ITC set-off for {filing.get_return_type_display()} {filing.period_label}",
                             source=books.SETOFF_SOURCE, source_id=source_id, reference=filing.reference, actor=actor)
    filing.setoff = {**_jsonable_setoff(plan, info["output"], info["credit"]), "source_id": source_id}
    filing.setoff_date = info["date"]
    filing.save(update_fields=["setoff", "setoff_date", "updated_at"])
    return entry


def undo_setoff(company, filing, actor, reason):
    if not filing.setoff:
        raise ValidationError({"detail": "No set-off has been posted for this return."})
    entry = JournalEntry.objects.filter(company=company, source=books.SETOFF_SOURCE, source_id=str(filing.setoff.get("source_id") or filing.id)).first()
    if entry is not None and entry.status == "posted":
        acct_ledger.reverse(entry, actor, reason, on_date=entry.date)  # dated like the original so the period's figures are as if it never happened
    filing.setoff, filing.setoff_date = {}, None
    filing.save(update_fields=["setoff", "setoff_date", "updated_at"])


# --------------------------------------------------------------------------- liability ledger

def _head_row(act, bal_before, head, month=None):
    """One head's liability and credit rows for a span. `bal_before` is the balances the day before it starts."""
    o_open, c_open = bal_before["out"][head], bal_before["in"][head]
    liability = books.raised(act, head, month)
    utilised = books.setoff_used(act, head, "out", month)
    cash = books.paid(act, head, month)
    accrued = books.accrued(act, head, month)
    credit_used = books.setoff_used(act, head, "in", month)
    return {
        "head": head, "label": HEAD_LABEL[head],
        "opening": o_open, "liability": liability, "itc_utilised": utilised, "cash_paid": cash,
        "closing": money(o_open + liability - utilised - cash),
        "itc_opening": c_open, "itc_accrued": accrued, "itc_used": credit_used, "itc_closing": money(c_open + accrued - credit_used),
    }


def liability_ledger(company, start, end):
    """Per head over a range: opening, liability raised, ITC utilised, cash paid, closing. Also month by month."""
    before = books.balances(company, start - datetime.timedelta(days=1))
    act = books.activity(company, start, end)
    rows = [_head_row(act, before, h) for h in HEADS]
    total = {k: sum((r[k] for r in rows), ZERO) for k in rows[0] if k not in ("head", "label")}
    months, bal = [], before
    mact = books.activity(company, start, end, monthly=True)
    cur = datetime.date(start.year, start.month, 1)
    while cur <= end:
        key = periods.month_key(cur)
        mrows = [_head_row(mact, bal, h, key) for h in HEADS]
        months.append({"month": key, "label": periods.month_label(cur), "heads": mrows,
                       "liability": sum((r["liability"] for r in mrows), ZERO), "itc_utilised": sum((r["itc_utilised"] for r in mrows), ZERO),
                       "cash_paid": sum((r["cash_paid"] for r in mrows), ZERO), "closing": sum((r["closing"] for r in mrows), ZERO)})
        bal = {"out": {r["head"]: r["closing"] for r in mrows}, "in": {r["head"]: r["itc_closing"] for r in mrows}}
        cur = datetime.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return {"from": start, "to": end, "heads": rows, "total": total, "months": months}


# --------------------------------------------------------------------------- summary by month / financial year

def _taxable_by_month(company, start, end):
    from django.db.models.functions import TruncMonth

    qs = JournalLine.objects.filter(entry__company=company, account__system_key__in=["sales", "sales_returns"],
                                    entry__date__gte=start, entry__date__lte=end).annotate(m=TruncMonth("entry__date"))
    res = {}
    for r in qs.values("m", "account__system_key").annotate(d=Sum("debit"), c=Sum("credit")):
        k = periods.month_key(r["m"])
        res[k] = res.get(k, ZERO) + (r["c"] or ZERO) - (r["d"] or ZERO)
    return res


def filing_status(f, today):
    if f.status == TaxFiling.Status.FILED:
        return "filed"
    return "late" if f.due_date < today else f.status


def return_counts(company, fy, today=None, groups=None):
    """Filed, prepared, pending and late returns whose due date falls in a financial year's cycle."""
    today = today or datetime.date.today()
    qs = TaxFiling.objects.filter(company=company, financial_year=fy)
    if groups:
        qs = qs.filter(return_type__in=[k for k, v in C.RETURN_TYPES.items() if v["group"] in groups])
    counts = {"filed": 0, "prepared": 0, "pending": 0, "late": 0, "total": 0}
    for f in qs:
        counts[filing_status(f, today)] += 1
        counts["total"] += 1
    return counts


def summary(company, fy, today=None):
    today = today or datetime.date.today()
    _, fy_start, fy_end = periods.parse_fy(fy)
    mact = books.activity(company, fy_start, fy_end, monthly=True)
    taxable = books.safe(lambda: _taxable_by_month(company, fy_start, fy_end), {}, "taxable by month")
    filings = {(f.return_type, f.period_start): f for f in TaxFiling.objects.filter(
        company=company, return_type__in=["gstr1", "gstr3b"], period_start__gte=fy_start, period_end__lte=fy_end)}
    months, tot = [], {"output": ZERO, "itc": ZERO, "taxable": ZERO, **{f"out_{h}": ZERO for h in HEADS}, **{f"itc_{h}": ZERO for h in HEADS}}
    for first, _last, label in periods.fy_months(fy):
        key = periods.month_key(first)
        out = {h: books.raised(mact, h, key) for h in HEADS}
        itc = {h: books.accrued(mact, h, key) for h in HEADS}
        o, i = sum(out.values(), ZERO), sum(itc.values(), ZERO)
        row = {"month": key, "label": label, "short": label.split()[0], "taxable": money(taxable.get(key, ZERO)),
               "output": out, "itc": itc, "output_total": o, "itc_total": i, "net": o - i,
               "gstr1": filing_status(filings[("gstr1", first)], today) if ("gstr1", first) in filings else "",
               "gstr3b": filing_status(filings[("gstr3b", first)], today) if ("gstr3b", first) in filings else ""}
        months.append(row)
        tot["output"] += o
        tot["itc"] += i
        tot["taxable"] += row["taxable"]
        for h in HEADS:
            tot[f"out_{h}"] += out[h]
            tot[f"itc_{h}"] += itc[h]
    reg = registers.sales_register(company, fy_start, min(fy_end, today) if today >= fy_start else fy_end)
    by_rate = [{"rate": r["rate"], "taxable": r["taxable"], "tax": r["tax"]} for r in reg["tax_by_rate"] if r["tax"] != 0 or r["taxable"] != 0]
    bal = books.balances(company, today)
    month_key_now = periods.month_key(today)
    this = next((m for m in months if m["month"] == month_key_now), None)
    paid = TaxPayment.objects.filter(company=company, tax_type="gst", financial_year=fy, status="recorded").aggregate(t=Sum("total"))["t"] or ZERO
    return {
        "financial_year": fy, "from": fy_start, "to": fy_end, "months": months, "totals": tot, "by_rate": by_rate,
        "kpis": {
            "this_month_liability": this["output_total"] if this else ZERO, "this_month_net": this["net"] if this else ZERO,
            "itc_available": sum(bal["in"].values(), ZERO), "liability_outstanding": sum(bal["out"].values(), ZERO),
            "tax_paid_ytd": money(paid), "returns": return_counts(company, fy, today, groups=["gst"]),
        },
    }


# --------------------------------------------------------------------------- books versus filed

def _filed_sum(filings, key):
    vals = [Decimal(str(f.figures.get(key, 0) or 0)) for f in filings]
    return money(sum(vals, ZERO)) if filings else None


def reconciliation(company, start, end):
    """Books, registers and filed returns side by side for a period; a row's difference is filed (GSTR-3B) minus books."""
    act = books.activity(company, start, end)
    sales = registers.sales_register(company, start, end)
    purchases = registers.purchase_register(company, start, end)
    f1 = list(TaxFiling.objects.filter(company=company, return_type="gstr1", status="filed", period_start__gte=start, period_end__lte=end))
    f3 = list(TaxFiling.objects.filter(company=company, return_type="gstr3b", status="filed", period_start__gte=start, period_end__lte=end))
    taxable_books = books.safe(lambda: sum(_taxable_by_month(company, start, end).values(), ZERO), ZERO, "books taxable")
    rows = []

    def add(key, label, section, books_v, register_v, g1, g3, tol=Decimal("0.01")):
        diff = money(g3 - books_v) if g3 is not None else None
        diff1 = money(g1 - books_v) if g1 is not None else None
        if g3 is None and g1 is None:
            status = "not_filed"
        else:
            status = "mismatch" if any(d is not None and abs(d) > tol for d in (diff, diff1)) else "match"
        reg_diff = money(register_v - books_v)
        rows.append({"key": key, "label": label, "section": section, "books": money(books_v), "register": money(register_v), "gstr1": g1, "gstr3b": g3,
                     "difference": diff, "gstr1_difference": diff1, "register_difference": reg_diff, "status": status})

    add("taxable", "Taxable value of outward supplies", "output", taxable_books, sales["totals"]["taxable"], _filed_sum(f1, "outward_taxable"),
        _filed_sum(f3, "outward_taxable"))
    for h in HEADS:
        add(f"out_{h}", f"Output tax: {HEAD_LABEL[h]}", "output", books.raised(act, h), sales["totals"][h], _filed_sum(f1, h), _filed_sum(f3, h))
    for h in HEADS:
        add(f"itc_{h}", f"Input credit: {HEAD_LABEL[h]}", "itc", books.accrued(act, h), purchases["totals"][h], None, _filed_sum(f3, f"itc_{h}"))
    mismatches = sum(1 for r in rows if r["status"] == "mismatch")
    return {"from": start, "to": end, "rows": rows, "filed": {"gstr1": len(f1), "gstr3b": len(f3)},
            "mismatches": mismatches, "not_filed": all(r["status"] == "not_filed" for r in rows)}
