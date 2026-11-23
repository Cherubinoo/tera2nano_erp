"""Payables ageing, purchases by vendor / month / category, the payment calendar and the dashboard."""
import datetime
from decimal import Decimal

from django.db.models import Count, Sum

from apps.accounting.models import Account

from . import amounts
from .common import ZERO, money, months_back
from ..models import PurchaseOrder, Vendor, VendorBill, VendorPayment

AGING = [("current", "Current"), ("d1_30", "1-30 days"), ("d31_60", "31-60 days"), ("d61_90", "61-90 days"),
         ("d90_plus", "Over 90 days")]
Status = VendorBill.Status


def bucket_for(due_date, as_of):
    days = (as_of - due_date).days if due_date else 0
    if days <= 0:
        return "current"
    if days <= 30:
        return "d1_30"
    if days <= 60:
        return "d31_60"
    if days <= 90:
        return "d61_90"
    return "d90_plus"


def ageing(company, as_of=None):
    """Open bills (and opening balances) by how long they are overdue, per vendor and in total. Advances are shown apart."""
    as_of = as_of or datetime.date.today()
    totals = {k: ZERO for k, _ in AGING}
    per = {}

    def row(v):
        return per.setdefault(v.pk, {"vendor_id": str(v.pk), "vendor": v.name, "code": v.code, **{k: ZERO for k, _ in AGING},
                                     "total": ZERO, "advance": ZERO, "bills": 0})

    bills = amounts.bills_with_amounts(VendorBill.objects.filter(company=company, status=Status.OPEN, bill_date__lte=as_of)).filter(
        balance_due__gt=0).select_related("vendor")
    for b in bills:
        balance, key = amounts.balance_of(b), bucket_for(b.due_date, as_of)
        r = row(b.vendor)
        r[key] += balance
        r["total"] += balance
        r["bills"] += 1
        totals[key] += balance
    for v in Vendor.objects.filter(company=company, opening_balance__gt=0):
        left = amounts.opening_outstanding(v)
        if left > 0:
            key = bucket_for(v.opening_balance_date, as_of)
            r = row(v)
            r[key] += left
            r["total"] += left
            r["bills"] += 1
            totals[key] += left
    for vid, b in amounts.vendor_balances(company, as_of).items():
        if b["advance"] > 0 and vid in per:
            per[vid]["advance"] = b["advance"]
    rows = sorted(per.values(), key=lambda r: (-r["total"], r["vendor"]))
    total = sum(totals.values(), ZERO)
    return totals, rows, total


def _bills_in(company, start, end):
    return VendorBill.objects.filter(company=company, status=Status.OPEN, bill_date__gte=start, bill_date__lte=end)


def by_category(company, start, end):
    """Cost per account over recorded bills: what reaches each expense account (GST included only when credit is not claimed)."""
    spend = {}
    for b in _bills_in(company, start, end).only("record", "itc_status"):
        claim = b.itc_status == VendorBill.Itc.ELIGIBLE
        for line in b.record.get("lines", []):
            amount = Decimal(line["taxable"]) + (ZERO if claim else Decimal(line["tax"]))
            spend[line["account"]] = spend.get(line["account"], ZERO) + amount
    names = {str(a.pk): f"{a.code} {a.name}" for a in Account.objects.filter(company=company)}
    rows = [{"account_id": k, "category": names.get(k, "Other"), "amount": v} for k, v in spend.items() if v > 0]
    return sorted(rows, key=lambda r: -r["amount"])


def purchases_report(company, start, end):
    qs = _bills_in(company, start, end)
    by_vendor = [{"vendor_id": str(r["vendor_id"]), "vendor": r["vendor__name"], "bills": r["n"], "taxable": r["taxable"],
                  "tax": r["tax"], "total": r["total"]}
                 for r in qs.values("vendor_id", "vendor__name").annotate(
                     n=Count("id"), taxable=Sum("taxable_total"), tax=Sum("tax_total"), total=Sum("grand_total")).order_by("-total")]
    monthly = {}
    for b in qs.only("bill_date", "grand_total", "taxable_total"):
        key = f"{b.bill_date.year}-{b.bill_date.month:02d}"
        m = monthly.setdefault(key, {"month": key, "bills": 0, "taxable": ZERO, "total": ZERO})
        m["bills"] += 1
        m["taxable"] += b.taxable_total
        m["total"] += b.grand_total
    return {"start": start, "end": end, "by_vendor": by_vendor, "by_month": [monthly[k] for k in sorted(monthly)],
            "by_category": by_category(company, start, end),
            "total": sum((r["total"] for r in by_vendor), ZERO), "bills": sum((r["bills"] for r in by_vendor), 0)}


def due_calendar(company, today=None, days=30):
    """Bills falling due in the next `days` days, by date, with what is already overdue shown apart."""
    today = today or datetime.date.today()
    end = today + datetime.timedelta(days=days)
    bills = amounts.bills_with_amounts(VendorBill.objects.filter(company=company, status=Status.OPEN)).filter(
        balance_due__gt=0, due_date__isnull=False, due_date__lte=end).select_related("vendor").order_by("due_date")
    overdue, overdue_count, by_date = ZERO, 0, {}
    for b in bills:
        balance = amounts.balance_of(b)
        if b.due_date < today:
            overdue += balance
            overdue_count += 1
            continue
        d = by_date.setdefault(b.due_date, {"date": b.due_date, "amount": ZERO, "bills": []})
        d["amount"] += balance
        d["bills"].append({"id": str(b.pk), "number": b.number, "vendor": b.vendor.name, "vendor_id": str(b.vendor_id),
                           "vendor_bill_no": b.vendor_bill_no, "balance": balance})
    days_rows = [by_date[k] for k in sorted(by_date)]
    return {"today": today, "until": end, "days": days_rows, "due_total": sum((d["amount"] for d in days_rows), ZERO),
            "overdue_total": overdue, "overdue_count": overdue_count}


def dashboard(company, today=None):
    today = today or datetime.date.today()
    totals, rows, outstanding = ageing(company, today)
    overdue = outstanding - totals["current"]
    months = months_back(today, 6)
    first = datetime.date(*months[0], 1)
    billed = {m: ZERO for m in months}
    for b in _bills_in(company, first, today).only("bill_date", "grand_total"):
        key = (b.bill_date.year, b.bill_date.month)
        if key in billed:
            billed[key] += b.grand_total
    paid = {m: ZERO for m in months}
    for p in VendorPayment.objects.filter(company=company, status=VendorPayment.Status.RECORDED, date__gte=first, date__lte=today):
        key = (p.date.year, p.date.month)
        if key in paid:
            paid[key] += p.amount
    month_start = today.replace(day=1)
    this_month = _bills_in(company, month_start, today).aggregate(t=Sum("grand_total"), n=Count("id"))
    top = purchases_report(company, first, today)
    open_orders = PurchaseOrder.objects.filter(company=company, status__in=PurchaseOrder.OPEN)
    order_counts = {r["status"]: r["n"] for r in PurchaseOrder.objects.filter(company=company).values("status").annotate(n=Count("id"))}
    bill_counts = {"draft": 0, "pending_approval": 0, "open": 0, "overdue": 0, "partial": 0, "paid": 0, "void": 0}
    for b in amounts.bills_with_amounts(VendorBill.objects.filter(company=company)):
        key = amounts.display_status(b, today)[0]
        bill_counts[key] = bill_counts.get(key, 0) + 1
    balances = amounts.vendor_balances(company, today)
    calendar = due_calendar(company, today)
    return {
        "today": today, "currency": company.default_currency,
        "payables": {"outstanding": outstanding, "current": totals["current"], "overdue": overdue,
                     "vendors_owed": len(rows), "advances": sum((b["advance"] for b in balances.values()), ZERO)},
        "this_month": {"billed": this_month["t"] or ZERO, "bills": this_month["n"] or 0},
        "open_orders": {"count": open_orders.count(), "value": open_orders.aggregate(t=Sum("grand_total"))["t"] or ZERO},
        "due_next_30_days": {"amount": calendar["due_total"], "overdue": calendar["overdue_total"]},
        "aging": [{"key": k, "label": label, "amount": totals[k]} for k, label in AGING],
        "monthly": [{"month": f"{y}-{m:02d}", "label": datetime.date(y, m, 1).strftime("%b"), "billed": billed[(y, m)],
                     "paid": paid[(y, m)]} for y, m in months],
        "top_vendors": [{"vendor_id": r["vendor_id"], "vendor": r["vendor"], "total": r["total"]} for r in top["by_vendor"][:5]],
        "categories": top["by_category"][:8],
        "order_counts": order_counts, "bill_counts": bill_counts,
        "vendors": Vendor.objects.filter(company=company, is_active=True).count(),
    }
