"""The tax calendar (due dates across GST, TDS, advance tax and income tax) and the tax dashboard."""
import datetime
from decimal import Decimal

from django.db.models import Sum

from .. import constants as C
from ..models import TaxFiling, TaxPayment
from . import books, filings, gst, incometax, periods, tds
from .common import ZERO, money

GROUP_LABEL = {"gst": "GST", "tds": "TDS", "advance_tax": "Advance tax", "income_tax": "Income tax"}
LOOKBACK_DAYS = 30


def _event(kind, rtype, title, period, due, today, *, status="pending", filing_id=None, amount=None, link="/tax/returns"):
    return {"id": f"{rtype}:{period}", "kind": kind, "kind_label": GROUP_LABEL[kind], "type": rtype, "title": title, "period": period,
            "due_date": due, "days_left": (due - today).days, "overdue": due < today, "status": "late" if due < today else status,
            "filing_id": filing_id, "amount": amount, "link": link}


def calendar(company, today=None, days=90):
    """Upcoming (and the last 30 days' overdue) due dates, soonest first. Filed returns and paid instalments are left out.

    Shape: {"from", "to", "today", "counts": {"overdue","next_7","next_30","total"},
            "events": [{"id","kind","kind_label","type","title","period","due_date","days_left","overdue","status","filing_id","amount","link"}]}
    `kind` is gst | tds | advance_tax | income_tax; `status` is pending | prepared | late; `days_left` is negative when overdue.
    """
    today = today or datetime.date.today()
    lo, hi = today - datetime.timedelta(days=LOOKBACK_DAYS), today + datetime.timedelta(days=days)
    records = {(f.return_type, f.period_start, f.period_end): f for f in TaxFiling.objects.filter(company=company)}
    events, seen = [], set()
    first_fy = periods.fy_label_for(lo - datetime.timedelta(days=366))
    start_year = periods.parse_fy(first_fy)[0]
    for y in range(start_year, periods.parse_fy(periods.fy_label_for(hi))[0] + 1):
        fy = periods.fy_label(y)
        for spec in filings.expected(fy, include_gst=filings.gst_registered(company)):
            key = (spec["return_type"], spec["period_start"], spec["period_end"])
            rec = records.get(key)
            due = rec.due_date if rec else spec["due_date"]
            if rec and rec.status == "filed":
                seen.add(key)
                continue
            if not lo <= due <= hi:
                continue
            seen.add(key)
            rt = C.RETURN_TYPES[spec["return_type"]]
            events.append(_event(rt["group"], spec["return_type"], f"{rt['label']} for {spec['label']}", spec["label"], due, today,
                                 status=rec.status if rec else "pending", filing_id=str(rec.id) if rec else None,
                                 link="/tax/returns" + (f"/{rec.id}" if rec else "")))
        sched = incometax.schedule(company, fy, today)
        for row in sched["instalments"]:
            if row["status"] == "paid" or not lo <= row["due_date"] <= hi:
                continue
            events.append(_event("advance_tax", "advance_tax", f"Advance tax instalment {row['instalment']} ({row['cumulative_pct']:g}%)",
                                 f"FY {fy} instalment {row['instalment']}", row["due_date"], today,
                                 amount=row["shortfall"] if row["shortfall"] else None, link=f"/tax/income-tax?fy={fy}"))
    for key, rec in records.items():  # manually added records outside the standard rules
        if key in seen or rec.status == "filed" or not lo <= rec.due_date <= hi:
            continue
        rt = C.RETURN_TYPES[rec.return_type]
        events.append(_event(rt["group"], rec.return_type, f"{rt['label']} for {rec.period_label}", rec.period_label, rec.due_date, today,
                             status=rec.status, filing_id=str(rec.id), link=f"/tax/returns/{rec.id}"))
    events.sort(key=lambda e: (e["due_date"], e["title"]))
    counts = {"overdue": sum(1 for e in events if e["overdue"]), "next_7": sum(1 for e in events if 0 <= e["days_left"] <= 7),
              "next_30": sum(1 for e in events if 0 <= e["days_left"] <= 30), "total": len(events)}
    return {"today": today, "from": lo, "to": hi, "counts": counts, "events": events}


def dashboard(company, today=None, fy=None):
    today = today or datetime.date.today()
    fy = fy or periods.fy_label_for(today)
    _, fy_start, fy_end = periods.parse_fy(fy)
    summ = gst.summary(company, fy, today)
    cal = calendar(company, today, 90)
    bal = books.safe(lambda: books.balances(company, today), {"out": books.zero_heads(), "in": books.zero_heads()}, "gst balances")
    tds_payable = books.safe(lambda: books.account_balance(company, "tds_payable", today), ZERO, "tds payable")
    rec = tds.receivable_summary(company, fy)
    comp = incometax.compute(company, fy)
    sched = incometax.schedule(company, fy, today, comp)
    paid_by_type = {t: ZERO for t in C.PAYMENT_TYPES}
    for r in TaxPayment.objects.filter(company=company, financial_year=fy, status="recorded").values("tax_type").annotate(t=Sum("total")):
        paid_by_type[r["tax_type"]] = money(r["t"])
    next_adv = next((r for r in sched["instalments"] if r["status"] in ("overdue", "due_soon", "upcoming")), None)
    returns = gst.return_counts(company, fy, today)
    last6 = [m for m in summ["months"] if m["month"] <= periods.month_key(today)][-6:]
    return {
        "financial_year": fy, "today": today,
        "kpis": {"gst_this_month": summ["kpis"]["this_month_liability"], "gst_outstanding": summ["kpis"]["liability_outstanding"],
                 "itc_available": summ["kpis"]["itc_available"], "tds_payable": tds_payable, "tds_receivable_tracker": rec["total"],
                 "tds_receivable_books": rec["books_balance"], "tax_paid_ytd": sum(paid_by_type.values(), ZERO),
                 "advance_tax_next": next_adv, "estimated_income_tax": comp["total_tax"], "overdue": cal["counts"]["overdue"],
                 "due_next_30": cal["counts"]["next_30"]},
        "returns": returns, "gst_returns": summ["kpis"]["returns"],
        "trend": [{"month": m["month"], "label": m["short"], "output": m["output_total"], "itc": m["itc_total"], "net": m["net"]} for m in last6],
        "liability_by_head": [{"head": h, "label": gst.HEAD_LABEL[h], "output": bal["out"][h], "itc": bal["in"][h]} for h in books.HEADS],
        "paid_by_type": [{"type": t, "label": C.PAYMENT_TYPES[t], "amount": v} for t, v in paid_by_type.items()],
        "upcoming": cal["events"][:6], "calendar_counts": cal["counts"],
    }
