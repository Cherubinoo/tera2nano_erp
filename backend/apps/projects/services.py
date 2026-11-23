"""Project rules: progress, health, and money read from the books.

PROGRESS (percent complete, 0 to 100) is worked out, never typed in:
  * Milestones: each counts equally. pending = 0, in progress = 50%, done = 100%.
  * Tasks: weighted by estimated hours (a task with no estimate weighs 1). todo = 0, in progress = 50%, in review = 80%, done = 100%.
  * A project that has both takes the average of the two figures; with only one kind, that figure; with neither, 0.
  * A completed project is 100%.

SCHEDULE HEALTH (for planned, active and on-hold projects; completed ones are judged on their actual end date):
  * delayed   : the planned end date has passed and progress is below 100, or progress trails the elapsed share of the
                schedule (days since start / days planned) by more than 25 points.
  * at risk   : progress trails the elapsed share by more than 10 points, or any milestone is overdue.
  * on track  : otherwise.

BUDGET HEALTH compares cost booked to the project in the ledger with the budget (the entered total, or the sum of the budget
lines when no total was entered): on track up to 90% used, at risk from 90% to 100%, over budget beyond that, none if no budget.
OVERALL HEALTH is the worse of the two.

MONEY comes from the ledger (`JournalLine.project_id`, never stored here):
  * revenue = credits less debits on income accounts (sales, other income; credit notes post to a sales-returns income account),
  * cost    = debits less credits on expense accounts,
  * profit  = revenue - cost, margin = profit / revenue.
Cost is split into budget categories by the account it was booked to (see `category_for_account`).

WORK IN PROGRESS (unbilled work): fixed price: contract value x progress less revenue booked; time and material: billable
timesheet value less revenue booked; never below zero; counted for active and on-hold projects.
"""
import datetime
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth

from apps.accounting.models import Account, JournalLine
from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit
from apps.sales.models import SalesDocument
from apps.sales.services import balance_of, with_amounts

from .models import BudgetLine, Milestone, Project, Task, TimeEntry

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
Cat = BudgetLine.Category
CATEGORIES = [c for c, _ in Cat.choices]
OPEN_STATUSES = (Project.Status.PLANNED, Project.Status.ACTIVE, Project.Status.ON_HOLD)

TASK_CREDIT = {"todo": Decimal("0"), "in_progress": Decimal("0.5"), "review": Decimal("0.8"), "done": Decimal("1")}
MILESTONE_CREDIT = {"pending": Decimal("0"), "in_progress": Decimal("0.5"), "done": Decimal("1")}
HEALTH_RANK = {"on_track": 0, "at_risk": 1, "delayed": 2}


def money(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def percent(part, whole):
    whole = Decimal(whole or 0)
    return None if whole <= 0 else (Decimal(part) * 100 / whole).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- audit

def log(company, actor, project, action, text, *, previous=None, new=None, reason=""):
    """One audit event against the project; `text` says what happened in words."""
    return audit.log(company, actor, "project", project.pk, action, previous=audit._jsonable(previous or {}),
                     new=audit._jsonable(new or {}), reason=reason or text, category="projects")


Action = CompanyProfileAuditEvent.Action


# --------------------------------------------------------------------------- progress and health

def milestone_overdue(m, today):
    return m.status != Milestone.Status.DONE and m.due_date < today


def milestone_key(m, today):
    return "overdue" if milestone_overdue(m, today) else m.status


def progress_of(project, milestones, tasks):
    if project.status == Project.Status.COMPLETED:
        return 100
    parts = []
    if milestones:
        parts.append(sum(MILESTONE_CREDIT[m.status] for m in milestones) / len(milestones))
    if tasks:
        weights = [(t.estimated_hours if t.estimated_hours > 0 else Decimal(1)) for t in tasks]
        parts.append(sum(w * TASK_CREDIT[t.status] for w, t in zip(weights, tasks)) / sum(weights))
    if not parts:
        return 0
    return int((sum(parts) / len(parts) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def schedule_health(project, progress, overdue_count, today):
    if project.status == Project.Status.CANCELLED:
        return "on_track"
    if project.status == Project.Status.COMPLETED:
        late = project.actual_end_date and project.planned_end_date and project.actual_end_date > project.planned_end_date
        return "delayed" if late else "on_track"
    level = 0
    end, start = project.planned_end_date, project.start_date
    if end:
        if today > end and progress < 100:
            level = 2
        elif start and today >= start and end > start:
            expected = min(100.0, (today - start).days / (end - start).days * 100)
            gap = expected - progress
            level = 2 if gap > 25 else 1 if gap > 10 else 0
    if overdue_count and level < 1:
        level = 1
    return ["on_track", "at_risk", "delayed"][level]


def effective_budget(project, lines):
    return project.budget if project.budget > 0 else sum((l.amount for l in lines), ZERO)


def budget_health(budget, cost):
    if budget <= 0:
        return "none"
    used = cost / budget
    return "over_budget" if used > 1 else "at_risk" if used >= Decimal("0.9") else "on_track"


def overall_health(schedule, budget):
    b = {"over_budget": "delayed", "at_risk": "at_risk"}.get(budget, "on_track")
    return schedule if HEALTH_RANK[schedule] >= HEALTH_RANK[b] else b


# --------------------------------------------------------------------------- ledger

_KEYWORDS = [
    (Cat.TRAVEL, ("travel", "conveyance", "accommodation", "lodging", "flight", "fuel", "transport", "mileage", "taxi")),
    (Cat.LABOUR, ("salar", "wage", "labour", "labor", "payroll", "bonus", "staff", "overtime")),
    (Cat.SUBCONTRACT, ("subcontract", "contractor", "outsourc", "professional", "consult", "freelanc", "legal")),
    (Cat.MATERIALS, ("material", "hardware", "equipment", "component", "supplies", "consumable", "reagent", "inventory", "software",
                     "licen")),
]


def category_for_account(account):
    """Budget category of an expense account. Defaults, in order: the 'salaries' system account is labour; account names are
    matched on keywords (travel/conveyance, salary/wages, subcontract/professional/consulting, materials/hardware/software);
    'Direct costs of services' (the direct-cost subtype) counts as materials; everything else is other."""
    if account.system_key == "salaries":
        return Cat.LABOUR
    name = account.name.lower()
    for category, words in _KEYWORDS:
        if any(w in name for w in words):
            return category
    if account.subtype == Account.Subtype.DIRECT_COST:
        return Cat.MATERIALS
    return Cat.OTHER


def _ledger_rows(company, project_ids=None):
    qs = JournalLine.objects.filter(entry__company=company, project_id__isnull=False)
    if project_ids is not None:
        qs = qs.filter(project_id__in=project_ids)
    return qs.values("project_id", "account_id").annotate(d=Sum("debit"), c=Sum("credit"))


def ledger_money(company, project_ids=None):
    """{project id: {"revenue", "cost", "categories": {category: cost}, "revenue_rows", "cost_rows"}} from the books."""
    accounts = {a.pk: a for a in Account.objects.filter(company=company, type__in=["income", "expense"])}
    out = defaultdict(lambda: {"revenue": ZERO, "cost": ZERO, "categories": {c: ZERO for c in CATEGORIES},
                               "revenue_rows": [], "cost_rows": []})
    for row in _ledger_rows(company, project_ids):
        acct = accounts.get(row["account_id"])
        if acct is None:
            continue
        slot = out[row["project_id"]]
        if acct.type == Account.Type.INCOME:
            amount = money(row["c"] - row["d"])
            slot["revenue"] += amount
            slot["revenue_rows"].append({"account_id": str(acct.pk), "code": acct.code, "name": acct.name, "amount": amount})
        else:
            amount = money(row["d"] - row["c"])
            category = category_for_account(acct)
            slot["cost"] += amount
            slot["categories"][category] += amount
            slot["cost_rows"].append({"account_id": str(acct.pk), "code": acct.code, "name": acct.name, "amount": amount,
                                      "category": category})
    return out


def empty_money():
    return {"revenue": ZERO, "cost": ZERO, "categories": {c: ZERO for c in CATEGORIES}, "revenue_rows": [], "cost_rows": []}


def monthly_money(company, project_ids=None, months=6, today=None):
    """[{month 'YYYY-MM', label, revenue, cost}] for the last `months` months, from the books."""
    today = today or datetime.date.today()
    keys, (y, m) = [], (today.year, today.month)
    for _ in range(months):
        keys.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    first = datetime.date(keys[0][0], keys[0][1], 1)
    qs = JournalLine.objects.filter(entry__company=company, project_id__isnull=False, entry__date__gte=first,
                                    account__type__in=["income", "expense"])
    if project_ids is not None:
        qs = qs.filter(project_id__in=project_ids)
    rows = qs.annotate(month=TruncMonth("entry__date")).values("month", "account__type").annotate(d=Sum("debit"), c=Sum("credit"))
    data = {k: {"revenue": ZERO, "cost": ZERO} for k in keys}
    for r in rows:
        k = (r["month"].year, r["month"].month)
        if k not in data:
            continue
        if r["account__type"] == "income":
            data[k]["revenue"] += money(r["c"] - r["d"])
        else:
            data[k]["cost"] += money(r["d"] - r["c"])
    return [{"month": f"{y}-{m:02d}", "label": datetime.date(y, m, 1).strftime("%b %y"), **data[(y, m)]} for y, m in keys]


def receivable_by_project(company, project_ids=None):
    """{project id: {"invoiced", "collected", "outstanding"}} on issued invoices and debit notes tagged to the project."""
    qs = SalesDocument.objects.filter(company=company, project_id__isnull=False, status=SalesDocument.Status.ISSUED,
                                      kind__in=SalesDocument.RECEIVABLE)
    if project_ids is not None:
        qs = qs.filter(project_id__in=project_ids)
    out = defaultdict(lambda: {"invoiced": ZERO, "collected": ZERO, "outstanding": ZERO})
    for d in with_amounts(qs):
        slot = out[d.project_id]
        balance = max(balance_of(d), ZERO)
        slot["invoiced"] += d.grand_total
        slot["outstanding"] += balance
        slot["collected"] += d.grand_total - balance
    return out


def time_totals(entries):
    """(hours, billable hours, billable amount) of an iterable of TimeEntry values (date, hours, billable, rate)."""
    hours = billable = amount = ZERO
    for _date, h, is_billable, rate in entries:
        hours += h
        if is_billable:
            billable += h
            amount += money(h * rate)
    return hours, billable, amount


def week_start(d):
    return d - datetime.timedelta(days=d.weekday())


def weekly(entries):
    """[{week_start, hours, billable_hours, amount}] newest first; weeks run Monday to Sunday."""
    weeks = defaultdict(lambda: [ZERO, ZERO, ZERO])
    for d, h, is_billable, rate in entries:
        w = weeks[week_start(d)]
        w[0] += h
        if is_billable:
            w[1] += h
            w[2] += money(h * rate)
    return [{"week_start": k, "hours": v[0], "billable_hours": v[1], "amount": v[2]} for k, v in sorted(weeks.items(), reverse=True)]


def wip_of(project, progress, revenue, billable_amount):
    if project.status not in (Project.Status.ACTIVE, Project.Status.ON_HOLD):
        return ZERO
    if project.billing_method == Project.Billing.TIME_MATERIAL:
        earned = billable_amount
    else:
        earned = money(project.contract_value * progress / 100)
    return max(money(earned - revenue), ZERO)


# --------------------------------------------------------------------------- summaries

def summarise(projects, company, today=None):
    """{project id: figures} for many projects with a fixed number of queries.

    Figures: progress, schedule/budget/overall health, overdue milestones, open tasks, cost, revenue, profit, margin, wip,
    budget, hours.
    """
    today = today or datetime.date.today()
    ids = [p.pk for p in projects]
    ledger = ledger_money(company, ids)
    time = defaultdict(list)
    for pid, d, h, b, r in TimeEntry.objects.filter(project_id__in=ids).values_list("project_id", "date", "hours", "billable", "rate"):
        time[pid].append((d, h, b, r))
    out = {}
    for p in projects:
        milestones, tasks, lines = list(p.milestones.all()), list(p.tasks.all()), list(p.budget_lines.all())
        led = ledger.get(p.pk) or empty_money()
        progress = progress_of(p, milestones, tasks)
        overdue = sum(1 for m in milestones if milestone_overdue(m, today))
        budget = effective_budget(p, lines)
        sched, bud = schedule_health(p, progress, overdue, today), budget_health(budget, led["cost"])
        hours, billable_hours, billable_amount = time_totals(time.get(p.pk, []))
        out[p.pk] = {
            "progress": progress, "schedule_health": sched, "budget_health": bud, "health": overall_health(sched, bud),
            "overdue_milestones": overdue, "milestones_total": len(milestones),
            "milestones_done": sum(1 for m in milestones if m.status == Milestone.Status.DONE),
            "tasks_total": len(tasks), "tasks_done": sum(1 for t in tasks if t.status == Task.Status.DONE),
            "revenue": led["revenue"], "cost": led["cost"], "profit": led["revenue"] - led["cost"],
            "margin_percent": percent(led["revenue"] - led["cost"], led["revenue"]),
            "budget_total": budget, "budget_used_percent": percent(led["cost"], budget),
            "hours": hours, "billable_hours": billable_hours, "billable_amount": billable_amount,
            "wip": wip_of(p, progress, led["revenue"], billable_amount), "ledger": led,
        }
    return out


def budget_vs_actual(project, lines, categories_actual, total_cost):
    """Rows per category: budget, actual, variance (budget - actual) and percent used; plus totals."""
    budgets = {l.category: l.amount for l in lines}
    rows = []
    for category in CATEGORIES:
        b, a = budgets.get(category, ZERO), categories_actual.get(category, ZERO)
        rows.append({"category": category, "label": Cat(category).label, "budget": b, "actual": a, "variance": b - a,
                     "used_percent": percent(a, b)})
    total_budget = effective_budget(project, lines)
    return {
        "rows": rows, "budget_total": total_budget, "allocated": sum(budgets.values(), ZERO),
        "unallocated": total_budget - sum(budgets.values(), ZERO), "actual_total": total_cost,
        "variance": total_budget - total_cost, "used_percent": percent(total_cost, total_budget),
        "health": budget_health(total_budget, total_cost),
    }
