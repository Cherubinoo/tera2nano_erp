"""People and projects reports: project profitability, partner capital and the salary report.

Salary figures are totals by month and by department only: no report here lists what one person is paid.
"""
from collections import defaultdict

from django.db.models import Count, Sum
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import TruncMonth

from apps.accounting.models import Account, JournalLine

from .common import ZERO, block, months_between, month_key, month_label, percent, money


def _sum(values):
    return sum(values, ZERO)


# --------------------------------------------------------------------------- 2. project profitability

def project_profitability(company, period, today):
    """Revenue, cost and profit per project from the journal lines tagged to it, within the period (or all time)."""
    from apps.projects.models import Project

    def lines():
        qs = JournalLine.objects.filter(entry__company=company, account__type__in=(Account.Type.INCOME, Account.Type.EXPENSE))
        if not period.is_all:
            qs = qs.filter(entry__date__gte=period.start, entry__date__lte=period.end)
        return qs

    money_by = defaultdict(lambda: {"revenue": ZERO, "cost": ZERO})
    for r in lines().filter(project_id__isnull=False).values("project_id", "account__type").annotate(d=Sum("debit"), c=Sum("credit")):
        slot = money_by[r["project_id"]]
        if r["account__type"] == Account.Type.INCOME:
            slot["revenue"] += (r["c"] or ZERO) - (r["d"] or ZERO)
        else:
            slot["cost"] += (r["d"] or ZERO) - (r["c"] or ZERO)
    rows = []
    for p in Project.objects.filter(company=company).select_related("customer").order_by("code"):
        m = money_by.get(p.pk)
        if p.status == Project.Status.CANCELLED and m is None:
            continue
        revenue, cost = money(m["revenue"]) if m else ZERO, money(m["cost"]) if m else ZERO
        rows.append({"id": str(p.pk), "code": p.code, "name": p.name, "customer": p.customer.display_name if p.customer_id else "",
                     "status": p.status, "status_label": p.get_status_display(), "contract_value": p.contract_value,
                     "budget": p.budget, "revenue": revenue, "cost": cost, "profit": revenue - cost,
                     "margin_percent": percent(revenue - cost, revenue)})
    rows.sort(key=lambda r: (-r["profit"], r["code"]))
    revenue, cost = _sum(r["revenue"] for r in rows), _sum(r["cost"] for r in rows)
    all_income = lines().filter(account__type=Account.Type.INCOME).aggregate(d=Sum("debit"), c=Sum("credit"))
    total_income = (all_income["c"] or ZERO) - (all_income["d"] or ZERO)
    return {
        "period": period.as_dict(),
        "kpis": {"revenue": revenue, "cost": cost, "profit": revenue - cost, "margin_percent": percent(revenue - cost, revenue),
                 "projects": len(rows), "loss_making": sum(1 for r in rows if r["profit"] < 0),
                 "untagged_revenue": money(total_income - revenue),
                 "best": {"name": rows[0]["name"], "profit": rows[0]["profit"]} if rows and (rows[0]["revenue"] or rows[0]["cost"]) else None},
        "projects": rows,
    }


def project_profitability_csv(d):
    return block("Project profitability", ["Code", "Project", "Customer", "Status", "Contract value", "Budget", "Revenue", "Cost", "Profit", "Margin %"],
                 [[r["code"], r["name"], r["customer"], r["status_label"], r["contract_value"], r["budget"], r["revenue"], r["cost"], r["profit"],
                   "" if r["margin_percent"] is None else r["margin_percent"]] for r in d["projects"]]
                 + [["Total", "", "", "", "", "", d["kpis"]["revenue"], d["kpis"]["cost"], d["kpis"]["profit"],
                     "" if d["kpis"]["margin_percent"] is None else d["kpis"]["margin_percent"]]])


# --------------------------------------------------------------------------- 3. partner capital

def partner_capital(company, period, today):
    """Each partner's capital, current account and loan at the end of the period, the capital movement and drawings in it."""
    import datetime

    from apps.partners import services as partners
    from apps.partners.models import PartnerTransaction

    end = today if period.is_all else period.end
    closing = {r["id"]: r for r in partners.partner_rows(company, end)}
    opening = {} if period.is_all else {str(k): v for k, v in partners.balances(company, period.start - datetime.timedelta(days=1)).items()}
    drawn = defaultdict(lambda: ZERO)
    txns = PartnerTransaction.objects.filter(company=company, status=PartnerTransaction.Status.POSTED, kind=PartnerTransaction.Kind.DRAWING)
    if not period.is_all:
        txns = txns.filter(date__gte=period.start, date__lte=period.end)
    for r in txns.values("partner_id").annotate(t=Sum("amount")):
        drawn[str(r["partner_id"])] = r["t"] or ZERO
    rows = []
    for pid, r in closing.items():
        open_cap = opening.get(pid, {}).get("capital", ZERO)
        if not r["is_active"] and not (r["capital"] or r["current"] or r["loan"] or drawn[pid]):
            continue
        rows.append({"id": pid, "name": r["name"], "partner_type": r["partner_type"], "is_active": r["is_active"],
                     "agreed_capital": r["agreed_capital"], "opening_capital": open_cap, "capital": r["capital"],
                     "capital_change": r["capital"] - open_cap, "current": r["current"], "loan": r["loan"],
                     "drawings": drawn[pid], "contribution_percent": r["contribution_percent"],
                     "profit_share_percent": r["profit_share_percent"]})
    rows.sort(key=lambda r: (-r["capital"], r["name"]))
    return {
        "period": period.as_dict(), "as_of": end,
        "kpis": {"capital": _sum(r["capital"] for r in rows), "agreed_capital": _sum(r["agreed_capital"] for r in rows),
                 "current": _sum(r["current"] for r in rows), "loan": _sum(r["loan"] for r in rows),
                 "drawings": _sum(r["drawings"] for r in rows), "partners": sum(1 for r in rows if r["is_active"])},
        "partners": rows,
    }


def partner_capital_csv(d):
    k = d["kpis"]
    return block("Partner capital as of %s" % d["as_of"].isoformat(),
                 ["Partner", "Agreed capital", "Opening capital", "Capital at end", "Capital change", "Current account", "Loan from partner",
                  "Drawings in period", "Share of capital %", "Profit share %"],
                 [[r["name"], r["agreed_capital"], r["opening_capital"], r["capital"], r["capital_change"], r["current"], r["loan"], r["drawings"],
                   r["contribution_percent"], r["profit_share_percent"]] for r in d["partners"]]
                 + [["Total", k["agreed_capital"], "", k["capital"], "", k["current"], k["loan"], k["drawings"], "", ""]])


# --------------------------------------------------------------------------- 4. salary

def salary(company, period, today):
    """Salary cost by month and by department from payroll runs that are approved, posted or paid. Totals only."""
    from apps.hr.models import Payslip
    from apps.hr.services.reports import FINAL

    qs = Payslip.objects.filter(run__company=company, run__status__in=FINAL, month__gte=period.start.replace(day=1), month__lte=period.end)
    sums = {"gross": Sum("gross"), "net": Sum("net_pay"), "deductions": Sum("total_deductions"), "employer_cost": Sum("employer_cost"),
            "employees": Count("employee", distinct=True)}
    by_month = {(r["m"].year, r["m"].month): r for r in qs.annotate(m=TruncMonth("month")).values("m").annotate(**sums)}
    months = []
    for first in months_between(period.start, period.end):
        r = by_month.get((first.year, first.month))
        months.append({"month": month_key(first), "label": month_label(first), "employees": r["employees"] if r else 0,
                       **{k: money(r[k]) if r else ZERO for k in ("gross", "net", "deductions", "employer_cost")}})
    departments = []
    for r in qs.annotate(department=KeyTextTransform("department", "who")).values("department").annotate(**sums):
        departments.append({"department": r["department"] or "No department", "employees": r["employees"],
                            **{k: money(r[k]) for k in ("gross", "net", "deductions", "employer_cost")}})
    departments.sort(key=lambda r: (-r["employer_cost"], r["department"]))
    paid_months = [m for m in months if m["employees"]]
    totals = qs.aggregate(**sums)
    return {
        "period": period.as_dict(),
        "kpis": {"gross": money(totals["gross"]), "net": money(totals["net"]), "deductions": money(totals["deductions"]),
                 "employer_cost": money(totals["employer_cost"]), "months_paid": len(paid_months),
                 "headcount": paid_months[-1]["employees"] if paid_months else 0},
        "months": months, "departments": departments,
    }


def salary_csv(d):
    k = d["kpis"]
    rows = block("Salary by month", ["Month", "Employees paid", "Gross pay", "Deductions", "Net pay", "Cost to company"],
                 [[m["label"], m["employees"], m["gross"], m["deductions"], m["net"], m["employer_cost"]] for m in d["months"]]
                 + [["Total", "", k["gross"], k["deductions"], k["net"], k["employer_cost"]]])
    return rows + block("Salary by department", ["Department", "Employees", "Gross pay", "Deductions", "Net pay", "Cost to company"],
                        [[r["department"], r["employees"], r["gross"], r["deductions"], r["net"], r["employer_cost"]] for r in d["departments"]])
