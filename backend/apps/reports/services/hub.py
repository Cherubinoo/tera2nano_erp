"""The list of reports, the hub's headline numbers and the executive dashboard.

A section is only built for a user who holds `reports.view` and the permission of the module it reads. A headline that fails
to load (a module with odd data) shows as "no value" instead of breaking the page.
"""
import logging
from dataclasses import dataclass
from typing import Callable

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth

from apps.accounting.models import Account, JournalLine
from apps.accounting.services import ledger, reports as books_reports

from . import finance, operations
from .common import ZERO, Period, fy_dates, fy_label, fy_start_year, last_months, month_key, month_label, money, resolve_period

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Report:
    key: str
    title: str
    description: str
    perm: str                 # the source module's view permission, needed on top of reports.view
    build: Callable           # (company, period, today, user) -> dict
    csv: Callable             # (dict) -> list of rows
    headline: Callable        # (dict) -> (label, value, kind)
    allow_all: bool = False   # may be asked for without a period (everything to date)
    audit_export: bool = False  # every CSV download is written to the audit history


REPORTS = [
    Report("revenue", "Annual revenue", "Sales by month and by customer, against the previous year.", "accounting.view",
           lambda c, p, t, u: finance.revenue(c, p, t, with_customers=u.has_code("sales.view")), finance.revenue_csv,
           lambda d: ("Revenue this period", d["kpis"]["revenue"], "money")),
    Report("project-profitability", "Project profitability", "Revenue, cost and profit of each project from the books.", "projects.view",
           lambda c, p, t, u: operations.project_profitability(c, p, t), operations.project_profitability_csv,
           lambda d: ("Profit across projects", d["kpis"]["profit"], "money"), allow_all=True),
    Report("partner-capital", "Partner capital", "Capital, current accounts, loans and drawings of each partner.", "partners.view",
           lambda c, p, t, u: operations.partner_capital(c, p, t), operations.partner_capital_csv,
           lambda d: ("Partners' capital", d["kpis"]["capital"], "money"), allow_all=True),
    Report("salary", "Salary report", "Salary cost by month and department (totals only).", "hr.payroll",
           lambda c, p, t, u: operations.salary(c, p, t), operations.salary_csv,
           lambda d: ("Cost to company this period", d["kpis"]["employer_cost"], "money"), audit_export=True),
    Report("receivables", "Pending customer payments", "What customers still owe, by customer and age.", "sales.view",
           lambda c, p, t, u: finance.receivables(c, p, t), finance.receivables_csv,
           lambda d: ("Owed by customers", d["kpis"]["outstanding"], "money"), allow_all=True),
    Report("vendor-outstanding", "Vendor outstanding", "What we still owe vendors, by vendor and age.", "purchases.view",
           lambda c, p, t, u: finance.vendor_outstanding(c, p, t), finance.vendor_outstanding_csv,
           lambda d: ("Owed to vendors", d["kpis"]["outstanding"], "money"), allow_all=True),
    Report("gst", "GST summary and tax liability", "GST raised and credited by month and head, and what is still owed.", "tax.view",
           lambda c, p, t, u: finance.gst(c, p, t), finance.gst_csv,
           lambda d: ("Net GST this period", d["kpis"]["net_payable"], "money")),
    Report("cash-flow", "Cash-flow analysis", "Where cash came from and went, with the closing balance by month.", "accounting.view",
           lambda c, p, t, u: finance.cash_flow(c, p, t), finance.cash_flow_csv,
           lambda d: ("Cash and bank now", d["kpis"]["closing_cash"], "money")),
]
BY_KEY = {r.key: r for r in REPORTS}


def allowed(user, report):
    return user.has_code("reports.view") and user.has_code(report.perm)


def default_period(report, today):
    return resolve_period({}, today, allow_all=report.allow_all)


def _guard(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one broken section must not take the whole page down
        log.exception("report section failed")
        return None


def home(company, user, today):
    cards = []
    for r in REPORTS:
        if not allowed(user, r):
            continue
        headline = _guard(lambda r=r: r.headline(r.build(company, default_period(r, today), today, user)))
        cards.append({"key": r.key, "title": r.title, "description": r.description, "path": f"/reports/{r.key}",
                      "headline": {"label": headline[0], "value": headline[1], "kind": headline[2]} if headline else None})
    return {"today": today, "reports": cards}


# --------------------------------------------------------------------------- executive dashboard

def _trend(company, today):
    months = last_months(today, 6)
    rows = JournalLine.objects.filter(entry__company=company, entry__date__gte=months[0], entry__date__lte=today,
                                      account__type__in=(Account.Type.INCOME, Account.Type.EXPENSE))
    data = {(m.year, m.month): {"revenue": ZERO, "expenses": ZERO} for m in months}
    for r in rows.annotate(m=TruncMonth("entry__date")).values("m", "account__type").annotate(d=Sum("debit"), c=Sum("credit")):
        slot = data[(r["m"].year, r["m"].month)]
        if r["account__type"] == Account.Type.INCOME:
            slot["revenue"] += (r["c"] or ZERO) - (r["d"] or ZERO)
        else:
            slot["expenses"] += (r["d"] or ZERO) - (r["c"] or ZERO)
    return [{"month": month_key(m), "label": month_label(m), "revenue": money(data[(m.year, m.month)]["revenue"]),
             "expenses": money(data[(m.year, m.month)]["expenses"]),
             "profit": money(data[(m.year, m.month)]["revenue"] - data[(m.year, m.month)]["expenses"])} for m in months]


def _cash_now(company, today):
    chart_ids = list(Account.objects.filter(company=company, subtype__in=(Account.Subtype.CASH, Account.Subtype.BANK)).values_list("id", flat=True))
    totals = ledger.totals(company, end=today, account_ids=chart_ids)
    return money(sum((d - c for d, c in totals.values()), ZERO))


def dashboard(company, user, today):
    """Headline figures of the financial year to date, each shown only to someone who may see its source."""
    start, _ = fy_dates(fy_start_year(today))
    period = Period(start, today, fy=fy_label(fy_start_year(today)))
    tiles = []

    def tile(key, label, value, kind, hint="", link=""):
        tiles.append({"key": key, "label": label, "value": value, "kind": kind, "hint": hint, "link": link})

    trend = None
    if user.has_code("accounting.view"):
        pl = _guard(lambda: books_reports.profit_and_loss(company, start, today))
        revenue = pl["total_income"] + pl["total_other_income"] if pl else None
        expenses = pl["total_direct_costs"] + pl["total_expenses"] + pl["total_other_expenses"] if pl else None
        tile("revenue", "Revenue", revenue, "money", "Sales and other income, financial year to date", "/reports/revenue")
        tile("expenses", "Expenses", expenses, "money", "Costs and expenses, financial year to date", "/accounting/reports")
        tile("profit", "Profit", pl["net_profit"] if pl else None, "money", "Income less expenses, financial year to date", "/accounting/reports")
        tile("cash", "Cash and bank", _guard(lambda: _cash_now(company, today)), "money", "Balance today", "/reports/cash-flow")
        trend = _guard(lambda: _trend(company, today))
    if user.has_code("sales.view"):
        d = _guard(lambda: finance.receivables(company, Period(None, None), today))
        tile("receivables", "Receivables", d["kpis"]["outstanding"] if d else None, "money",
             f"{money(d['kpis']['overdue'])} overdue" if d else "", "/reports/receivables")
    if user.has_code("purchases.view"):
        d = _guard(lambda: finance.vendor_outstanding(company, Period(None, None), today))
        tile("payables", "Payables", d["kpis"]["outstanding"] if d else None, "money",
             f"{money(d['kpis']['overdue'])} overdue" if d else "", "/reports/vendor-outstanding")
    if user.has_code("hr.view"):
        def headcount():
            from apps.hr.models import Employee
            return Employee.objects.filter(company=company, status__in=(Employee.Status.ACTIVE, Employee.Status.NOTICE)).count()
        tile("headcount", "Headcount", _guard(headcount), "count", "Employees working or on notice", "/hr/employees")
    if user.has_code("crm.view"):
        def deals():
            from apps.crm.models import Deal
            return Deal.objects.filter(company=company, status="open").aggregate(n=Count("id"), v=Sum("amount"))
        d = _guard(deals)
        tile("deals", "Open CRM deals", d["n"] if d else None, "count", f"Worth {money(d['v'])}" if d else "", "/crm/deals")
    return {"today": today, "period": period.as_dict(), "tiles": tiles, "trend": trend}
