"""Money reports: annual revenue, cash flow, pending customer payments, vendor outstanding and the GST summary.

Each function takes the company, a `Period` and today's date and returns plain dicts and lists (Decimals for money); `*_csv`
turns that result into the rows of the CSV export. They read through the other modules' services and ORM and never write.
"""
from collections import defaultdict

from django.db.models import Exists, OuterRef, Sum
from django.db.models.functions import TruncMonth

from apps.accounting.models import Account, JournalLine
from apps.accounting.services import reports as books_reports
from apps.sales import services as sales
from apps.sales.models import SalesDocument

from .common import ZERO, block, change_percent, money, months_between, month_key, month_label, percent, shift_year

CASH = (Account.Subtype.CASH, Account.Subtype.BANK)


def _sum(values):
    return sum(values, ZERO)


def _lines(company, start, end, **filters):
    return JournalLine.objects.filter(entry__company=company, entry__date__gte=start, entry__date__lte=end, **filters)


# --------------------------------------------------------------------------- 1. annual revenue

def _income_by_month(company, start, end):
    rows = _lines(company, start, end, account__subtype=Account.Subtype.INCOME).annotate(m=TruncMonth("entry__date")).values("m").annotate(
        d=Sum("debit"), c=Sum("credit"))
    return {(r["m"].year, r["m"].month): money((r["c"] or 0) - (r["d"] or 0)) for r in rows}


def _revenue_by_customer(company, start, end):
    """{customer id: (name, net sales before GST)} from issued invoices and debit notes less credit notes."""
    kinds = (SalesDocument.Kind.INVOICE, SalesDocument.Kind.DEBIT_NOTE, SalesDocument.Kind.CREDIT_NOTE)
    out = {}
    rows = SalesDocument.objects.filter(company=company, status=SalesDocument.Status.ISSUED, kind__in=kinds, issue_date__gte=start,
                                        issue_date__lte=end).values("customer_id", "customer__display_name", "kind").annotate(t=Sum("taxable_total"))
    for r in rows:
        sign = -1 if r["kind"] == SalesDocument.Kind.CREDIT_NOTE else 1
        name, total = out.get(r["customer_id"], (r["customer__display_name"], ZERO))
        out[r["customer_id"]] = (name, total + sign * (r["t"] or ZERO))
    return out


def revenue(company, period, today, *, with_customers=True):
    """Sales income (before GST, net of credit notes) by month and by customer, against the same dates a year earlier."""
    start, end = period.start, period.end
    p_start, p_end = shift_year(start, -1), shift_year(end, -1)
    current, previous = _income_by_month(company, start, end), _income_by_month(company, p_start, p_end)
    months, p_months = months_between(start, end), months_between(p_start, p_end)
    rows = []
    for i, first in enumerate(months):
        cur = current.get((first.year, first.month), ZERO)
        prev_first = p_months[i] if i < len(p_months) else None
        prev = previous.get((prev_first.year, prev_first.month), ZERO) if prev_first else ZERO
        rows.append({"month": month_key(first), "label": month_label(first), "revenue": cur, "previous": prev,
                     "change_percent": change_percent(cur, prev)})
    total, prev_total = _sum(r["revenue"] for r in rows), _sum(previous.values())
    best = max(rows, key=lambda r: r["revenue"], default=None)
    out = {
        "period": period.as_dict(), "previous_period": {"from": p_start, "to": p_end},
        "kpis": {"revenue": total, "previous_revenue": prev_total, "change_percent": change_percent(total, prev_total),
                 "monthly_average": money(total / len(rows)) if rows else ZERO,
                 "best_month": {"label": best["label"], "amount": best["revenue"]} if best and best["revenue"] > 0 else None},
        "months": rows, "by_customer": None,
    }
    if with_customers:
        cur_c, prev_c = _revenue_by_customer(company, start, end), _revenue_by_customer(company, p_start, p_end)
        names = {cid: v[0] for cid, v in {**prev_c, **cur_c}.items()}
        customers = []
        for cid, name in names.items():
            c, p = cur_c.get(cid, (name, ZERO))[1], prev_c.get(cid, (name, ZERO))[1]
            customers.append({"customer_id": str(cid), "customer": name, "revenue": c, "previous": p,
                              "change_percent": change_percent(c, p), "share_percent": percent(c, total)})
        customers.sort(key=lambda r: (-r["revenue"], r["customer"]))
        other, prev_other = total - _sum(r["revenue"] for r in customers), prev_total - _sum(r["previous"] for r in customers)
        if other != 0 or prev_other != 0:
            customers.append({"customer_id": None, "customer": "Other entries (not from invoices)", "revenue": other, "previous": prev_other,
                              "change_percent": change_percent(other, prev_other), "share_percent": percent(other, total)})
        out["by_customer"] = customers
    return out


def revenue_csv(d):
    rows = block("Revenue by month", ["Month", "Revenue", "Same month last year", "Change %"],
                 [[r["label"], r["revenue"], r["previous"], r["change_percent"] if r["change_percent"] is not None else ""] for r in d["months"]])
    rows[-1:-1] = [["Total", d["kpis"]["revenue"], d["kpis"]["previous_revenue"],
                    d["kpis"]["change_percent"] if d["kpis"]["change_percent"] is not None else ""]]
    if d["by_customer"] is not None:
        rows += block("Revenue by customer", ["Customer", "Revenue", "Last year", "Change %", "Share %"],
                      [[r["customer"], r["revenue"], r["previous"], "" if r["change_percent"] is None else r["change_percent"],
                        "" if r["share_percent"] is None else r["share_percent"]] for r in d["by_customer"]])
    return rows


# --------------------------------------------------------------------------- 8. cash flow

def cash_flow(company, period, today):
    """The cash-flow statement (indirect method) plus money in and out of cash and bank accounts month by month."""
    start, end = period.start, period.end
    stmt = books_reports.cash_flow(company, start, end)  # also makes sure the chart of accounts exists
    cash_ids = list(Account.objects.filter(company=company, subtype__in=CASH).values_list("id", flat=True))
    other_side = JournalLine.objects.filter(entry_id=OuterRef("entry_id")).exclude(account_id__in=cash_ids)
    # Moves between two of our own cash or bank accounts are neither money in nor out.
    lines = _lines(company, start, end, account_id__in=cash_ids).annotate(has_other=Exists(other_side)).filter(has_other=True)
    found = {(r["m"].year, r["m"].month): (r["d"] or ZERO, r["c"] or ZERO)
             for r in lines.annotate(m=TruncMonth("entry__date")).values("m").annotate(d=Sum("debit"), c=Sum("credit"))}
    running, rows = stmt["opening_cash"], []
    for first in months_between(start, end):
        inflow, outflow = found.get((first.year, first.month), (ZERO, ZERO))
        running += inflow - outflow
        rows.append({"month": month_key(first), "label": month_label(first), "inflow": money(inflow), "outflow": money(outflow),
                     "net": money(inflow - outflow), "closing": money(running)})
    items = lambda key: [{"name": i["name"], "amount": i["amount"]} for i in stmt[key]]  # noqa: E731
    return {
        "period": period.as_dict(),
        "kpis": {"opening_cash": stmt["opening_cash"], "closing_cash": stmt["closing_cash"], "net_change": stmt["net_change"],
                 "operating": stmt["net_operating"], "investing": stmt["net_investing"], "financing": stmt["net_financing"],
                 "inflow": _sum(r["inflow"] for r in rows), "outflow": _sum(r["outflow"] for r in rows)},
        "statement": {"net_profit": stmt["net_profit"], "operating": items("operating"), "investing": items("investing"),
                      "financing": items("financing"), "ties": stmt["ties"]},
        "months": rows,
    }


def cash_flow_csv(d):
    s, k = d["statement"], d["kpis"]
    rows = block("Cash-flow statement", ["Line", "Amount"], [
        ["Net profit", s["net_profit"]], *[[i["name"], i["amount"]] for i in s["operating"]],
        ["Net cash from operating activities", k["operating"]], *[[i["name"], i["amount"]] for i in s["investing"]],
        ["Net cash from investing activities", k["investing"]], *[[i["name"], i["amount"]] for i in s["financing"]],
        ["Net cash from financing activities", k["financing"]], ["Net change in cash", k["net_change"]],
        ["Opening cash and bank", k["opening_cash"]], ["Closing cash and bank", k["closing_cash"]]])
    return rows + block("Cash and bank by month", ["Month", "Money in", "Money out", "Net", "Closing balance"],
                        [[r["label"], r["inflow"], r["outflow"], r["net"], r["closing"]] for r in d["months"]])


# --------------------------------------------------------------------------- 5. pending customer payments

def receivables(company, period, today):
    """What customers still owe on issued invoices and debit notes (raised within the period, if one is given), by age."""
    qs = sales.with_amounts(SalesDocument.objects.filter(company=company, status=SalesDocument.Status.ISSUED,
                                                         kind__in=SalesDocument.RECEIVABLE)).filter(balance_due__gt=0).select_related("customer")
    if not period.is_all:
        qs = qs.filter(issue_date__gte=period.start, issue_date__lte=period.end)
    keys = [k for k, _, _ in sales.AGING]
    totals, per = {k: ZERO for k in keys}, {}
    for d in qs:
        balance, bucket = sales.balance_of(d), sales.bucket_for(d, today)
        overdue_days = max((today - d.due_date).days, 0) if d.due_date else 0
        r = per.setdefault(d.customer_id, {"customer_id": str(d.customer_id), "customer": d.customer.display_name,
                                           **{k: ZERO for k in keys}, "total": ZERO, "invoices": 0, "oldest_overdue_days": 0})
        r[bucket] += balance
        r["total"] += balance
        r["invoices"] += 1
        r["oldest_overdue_days"] = max(r["oldest_overdue_days"], overdue_days)
        totals[bucket] += balance
    rows = sorted(per.values(), key=lambda r: (-r["total"], r["customer"]))
    outstanding = _sum(totals.values())
    overdue = outstanding - totals["current"]
    return {
        "period": period.as_dict(), "as_of": today,
        "kpis": {"outstanding": outstanding, "overdue": overdue, "overdue_percent": percent(overdue, outstanding),
                 "customers_owing": len(rows), "invoices": sum(r["invoices"] for r in rows),
                 "largest": {"customer": rows[0]["customer"], "amount": rows[0]["total"]} if rows else None},
        "buckets": [{"key": k, "label": label, "amount": totals[k]} for k, label, _ in sales.AGING],
        "customers": rows,
    }


def receivables_csv(d):
    labels = [b["label"] for b in d["buckets"]]
    keys = [b["key"] for b in d["buckets"]]
    return block("Pending customer payments (ageing as of %s)" % d["as_of"].isoformat(),
                 ["Customer", *labels, "Total", "Open invoices", "Oldest overdue (days)"],
                 [[r["customer"], *[r[k] for k in keys], r["total"], r["invoices"], r["oldest_overdue_days"]] for r in d["customers"]]
                 + [["Total", *[b["amount"] for b in d["buckets"]], d["kpis"]["outstanding"], d["kpis"]["invoices"], ""]])


# --------------------------------------------------------------------------- 6. vendor outstanding

def vendor_outstanding(company, period, today):
    """What we still owe vendors on recorded bills (dated within the period, if one is given) and opening balances, by age."""
    from apps.purchases.models import Vendor, VendorBill
    from apps.purchases.services import amounts, reports as purchase_reports

    keys = [k for k, _ in purchase_reports.AGING]
    totals, per = {k: ZERO for k in keys}, {}

    def row(v):
        return per.setdefault(v.pk, {"vendor_id": str(v.pk), "vendor": v.name, "code": v.code, **{k: ZERO for k in keys},
                                     "total": ZERO, "bills": 0, "oldest_overdue_days": 0})

    def add(v, balance, due, bucket_date):
        key = purchase_reports.bucket_for(bucket_date, today)
        r = row(v)
        r[key] += balance
        r["total"] += balance
        r["bills"] += 1
        r["oldest_overdue_days"] = max(r["oldest_overdue_days"], max((today - due).days, 0) if due else 0)
        totals[key] += balance

    bills = VendorBill.objects.filter(company=company, status=VendorBill.Status.OPEN)
    if not period.is_all:
        bills = bills.filter(bill_date__gte=period.start, bill_date__lte=period.end)
    for b in amounts.bills_with_amounts(bills).filter(balance_due__gt=0).select_related("vendor"):
        add(b.vendor, amounts.balance_of(b), b.due_date, b.due_date)
    openings = Vendor.objects.filter(company=company, opening_balance__gt=0)
    if not period.is_all:
        openings = openings.filter(opening_balance_date__gte=period.start, opening_balance_date__lte=period.end)
    for v in openings:
        left = amounts.opening_outstanding(v)
        if left > 0:
            add(v, left, v.opening_balance_date, v.opening_balance_date)
    rows = sorted(per.values(), key=lambda r: (-r["total"], r["vendor"]))
    outstanding = _sum(totals.values())
    overdue = outstanding - totals["current"]
    return {
        "period": period.as_dict(), "as_of": today,
        "kpis": {"outstanding": outstanding, "overdue": overdue, "overdue_percent": percent(overdue, outstanding),
                 "vendors_owed": len(rows), "bills": sum(r["bills"] for r in rows),
                 "largest": {"vendor": rows[0]["vendor"], "amount": rows[0]["total"]} if rows else None},
        "buckets": [{"key": k, "label": label, "amount": totals[k]} for k, label in purchase_reports.AGING],
        "vendors": rows,
    }


def vendor_outstanding_csv(d):
    labels = [b["label"] for b in d["buckets"]]
    keys = [b["key"] for b in d["buckets"]]
    return block("Vendor outstanding (ageing as of %s)" % d["as_of"].isoformat(),
                 ["Vendor", "Code", *labels, "Total", "Open bills", "Oldest overdue (days)"],
                 [[r["vendor"], r["code"], *[r[k] for k in keys], r["total"], r["bills"], r["oldest_overdue_days"]] for r in d["vendors"]]
                 + [["Total", "", *[b["amount"] for b in d["buckets"]], d["kpis"]["outstanding"], d["kpis"]["bills"], ""]])


# --------------------------------------------------------------------------- 7. GST summary and tax liability

def gst(company, period, today):
    """Output GST raised, input credit earned, what was set off and paid, and what is still owed, by month and by head."""
    from apps.tax.services import gst as tax_gst

    start, end = period.start, period.end
    ledger = tax_gst.liability_ledger(company, start, end)
    taxable = defaultdict(lambda: ZERO)
    for r in _lines(company, start, end, account__system_key__in=["sales", "sales_returns"]).annotate(m=TruncMonth("entry__date")).values(
            "m").annotate(d=Sum("debit"), c=Sum("credit")):
        taxable[(r["m"].year, r["m"].month)] += (r["c"] or ZERO) - (r["d"] or ZERO)
    months = []
    for m in ledger["months"]:
        y, mo = (int(x) for x in m["month"].split("-"))
        itc = _sum(h["itc_accrued"] for h in m["heads"])
        months.append({"month": m["month"], "label": m["label"], "taxable": money(taxable.get((y, mo), ZERO)), "output": m["liability"],
                       "itc": itc, "net": m["liability"] - itc, "itc_utilised": m["itc_utilised"], "paid": m["cash_paid"],
                       "closing": m["closing"]})
    t = ledger["total"]
    heads = [{"head": h["head"], "label": h["label"], "opening": h["opening"], "output": h["liability"], "itc": h["itc_accrued"],
              "itc_utilised": h["itc_utilised"], "paid": h["cash_paid"], "closing": h["closing"]} for h in ledger["heads"]]
    return {
        "period": period.as_dict(),
        "kpis": {"taxable": _sum(m["taxable"] for m in months), "output": t["liability"], "itc": t["itc_accrued"],
                 "net_payable": t["liability"] - t["itc_accrued"], "paid": t["cash_paid"], "closing_liability": t["closing"],
                 "itc_balance": t["itc_closing"]},
        "months": months, "heads": heads,
    }


def gst_csv(d):
    k = d["kpis"]
    rows = block("GST by month", ["Month", "Taxable sales", "Output GST", "Input tax credit", "Net (output less credit)", "Credit used", "Paid in cash", "Owed at month end"],
                 [[m["label"], m["taxable"], m["output"], m["itc"], m["net"], m["itc_utilised"], m["paid"], m["closing"]] for m in d["months"]]
                 + [["Total", k["taxable"], k["output"], k["itc"], k["net_payable"], "", k["paid"], k["closing_liability"]]])
    return rows + block("GST by head", ["Head", "Owed at start", "Output GST", "Input tax credit earned", "Credit used", "Paid in cash", "Owed at end"],
                        [[h["label"], h["opening"], h["output"], h["itc"], h["itc_utilised"], h["paid"], h["closing"]] for h in d["heads"]])
