"""Budgets by financial year: lines per ledger account (and optionally per project, which is the cost centre), monthly phasing,
a saved revision every time the lines change, budget against actual read from the journal, and the over-budget warning on expenses.

RULES
* A line budgets one income or expense account for the year; "cost centre" is the project the money is tagged to (journal lines
  carry the project). A line with no cost centre covers the account's spending that is not covered by a cost-centre line of the
  same account in the same budget, so nothing is counted twice.
* Phasing: "even" splits the yearly amount into 12; the first eleven months get amount / 12 rounded to the paisa and the last
  month takes the remainder, so the twelve always add up to the yearly amount. "Month by month" takes twelve amounts and the
  yearly amount is their sum. The first of the twelve is the financial year's first month.
* Variance is shown so that a positive number is good: for an expense, budget - actual; for income, actual - budget. A negative
  variance is over budget (expense) or short of target (income). Variance % = variance / budget x 100 (blank when the budget is 0).
* Only one budget per year can be Active; expenses are checked against it. The check is a warning, never a block.
"""
import datetime
import uuid
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import TruncMonth

from apps.company.models import CompanyProfileAuditEvent, FinancialYear

from ..models import Account, Budget, BudgetLine, BudgetRevision, JournalLine
from . import ledger

Action = CompanyProfileAuditEvent.Action
ZERO = Decimal("0.00")
CENT = Decimal("0.01")
MAX_LINES = 300


def _money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def fy_months(fy):
    """The twelve month starts of a financial year."""
    out, y, m = [], fy.start_date.year, fy.start_date.month
    for _ in range(12):
        out.append(datetime.date(y, m, 1))
        y, m = y + (m == 12), m % 12 + 1
    return out


def even_split(annual):
    per = _money(annual / 12)
    return [per] * 11 + [_money(annual - per * 11)]


def _amount(value, field, label):
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError({field: f"{label} must be a number."})
    if not d.is_finite() or d < 0:
        raise ValidationError({field: f"{label} cannot be negative."})
    if d.as_tuple().exponent < -2:
        raise ValidationError({field: f"{label}: use at most 2 decimal places."})
    if d > Decimal("100000000000"):
        raise ValidationError({field: f"{label} is too large."})
    return d


def parse_lines(company, rows):
    """Validate the lines sent for a budget: [{account, project, phasing, annual | monthly[12]}]."""
    if not isinstance(rows, list):
        raise ValidationError({"lines": "Send the budget lines."})
    if len(rows) > MAX_LINES:
        raise ValidationError({"lines": f"A budget can have at most {MAX_LINES} lines."})
    out, seen = [], set()
    for n, r in enumerate(rows, 1):
        where = f"Line {n}: "
        acct = None
        try:
            acct = Account.objects.filter(pk=r.get("account"), company=company).first() if r.get("account") else None
        except (ValueError, ValidationError):
            acct = None
        if acct is None or acct.type not in ("income", "expense") or not acct.is_active:
            raise ValidationError({"lines": f"{where}choose an income or expense account."})
        project = None
        if r.get("project"):
            try:
                project = uuid.UUID(str(r["project"]))
            except ValueError:
                raise ValidationError({"lines": f"{where}choose a valid cost centre."})
        if (acct.pk, project) in seen:
            raise ValidationError({"lines": f"{where}{acct.code} {acct.name} already has a line for this cost centre."})
        seen.add((acct.pk, project))
        phasing = r.get("phasing") or BudgetLine.Phasing.EVEN
        if phasing == BudgetLine.Phasing.CUSTOM:
            vals = r.get("monthly")
            if not isinstance(vals, list) or len(vals) != 12:
                raise ValidationError({"lines": f"{where}enter the twelve monthly amounts."})
            monthly = [_money(_amount(v if v not in (None, "") else 0, "lines", f"{where}a monthly amount")) for v in vals]
            annual = sum(monthly, ZERO)
        elif phasing == BudgetLine.Phasing.EVEN:
            annual = _money(_amount(r.get("annual") if r.get("annual") not in (None, "") else 0, "lines", f"{where}the yearly amount"))
            monthly = even_split(annual)
        else:
            raise ValidationError({"lines": f"{where}choose even or month-by-month phasing."})
        out.append({"account": acct, "project_id": project, "phasing": phasing, "annual": annual, "monthly": monthly})
    return out


def snapshot_of(lines):
    return [{"account": str(l.account_id), "code": l.account.code, "name": l.account.name, "type": l.account.type,
             "project": str(l.project_id) if l.project_id else None, "phasing": l.phasing, "annual": str(l.annual),
             "monthly": [str(m) for m in l.monthly]} for l in lines]


def _totals(snapshot):
    inc = sum((Decimal(l["annual"]) for l in snapshot if l["type"] == "income"), ZERO)
    exp = sum((Decimal(l["annual"]) for l in snapshot if l["type"] == "expense"), ZERO)
    return inc, exp


def _write_lines(budget, parsed):
    budget.lines.all().delete()
    BudgetLine.objects.bulk_create([BudgetLine(budget=budget, account=p["account"], project_id=p["project_id"], phasing=p["phasing"],
                                               annual=p["annual"], monthly=[str(m) for m in p["monthly"]]) for p in parsed])
    return list(budget.lines.select_related("account"))


def _revision(budget, actor, reason):
    snap = snapshot_of(budget.lines.select_related("account"))
    inc, exp = _totals(snap)
    BudgetRevision.objects.create(budget=budget, version=budget.version, snapshot=snap, total_income=inc, total_expense=exp,
                                  reason=(reason or "")[:300], created_by=actor if getattr(actor, "is_authenticated", False) else None)
    return snap


def _snapshot_audit(b):
    return {"name": b.name, "financial_year": b.financial_year.label, "status": b.status, "version": b.version, "notes": b.notes}


def _fy(company, ref):
    fy = None
    try:
        fy = FinancialYear.objects.filter(pk=ref, company=company).first() if ref else None
    except (ValueError, ValidationError):
        fy = None
    if fy is None:
        raise ValidationError({"financial_year": "Choose the financial year."})
    return fy


def _name(company, data, fy, current=None):
    name = str(data.get("name", current.name if current else "")).strip()[:150]
    if not name:
        raise ValidationError({"name": "Enter the budget name."})
    clash = Budget.objects.filter(company=company, financial_year=fy, name__iexact=name)
    if current:
        clash = clash.exclude(pk=current.pk)
    if clash.exists():
        raise ValidationError({"name": "This year already has a budget with this name."})
    return name


@transaction.atomic
def create_budget(company, actor, data):
    fy = _fy(company, data.get("financial_year"))
    name = _name(company, data, fy)
    parsed = parse_lines(company, data.get("lines", []))
    b = Budget.objects.create(company=company, name=name, financial_year=fy, notes=str(data.get("notes") or "").strip()[:500],
                              created_by=actor if getattr(actor, "is_authenticated", False) else None)
    _write_lines(b, parsed)
    _revision(b, actor, "First version")
    ledger.audit_event(company, actor, "budget", b.pk, Action.CREATED, new=_snapshot_audit(b))
    return b


@transaction.atomic
def update_budget(budget, actor, data):
    b = Budget.objects.select_for_update().select_related("financial_year", "company").get(pk=budget.pk)
    company, before = b.company, _snapshot_audit(b)
    if b.status == Budget.Status.CLOSED:
        raise ValidationError("A closed budget cannot be changed. Reopen it first.")
    if "financial_year" in data and str(data["financial_year"]) != str(b.financial_year_id):
        if b.status != Budget.Status.DRAFT:
            raise ValidationError({"financial_year": "The year of an active budget cannot change."})
        b.financial_year = _fy(company, data["financial_year"])
    b.name = _name(company, data, b.financial_year, b)
    if "notes" in data:
        b.notes = str(data.get("notes") or "").strip()[:500]
    revised = False
    if "lines" in data:
        parsed = parse_lines(company, data["lines"])
        old = snapshot_of(b.lines.select_related("account"))
        reason = str(data.get("change_reason") or "").strip()
        new_probe = [{"account": str(p["account"].pk), "project": str(p["project_id"]) if p["project_id"] else None,
                      "phasing": p["phasing"], "annual": str(p["annual"]), "monthly": [str(m) for m in p["monthly"]]} for p in parsed]
        strip = lambda snap: sorted(({k: l[k] for k in ("account", "project", "phasing", "annual", "monthly")} for l in snap),
                                    key=lambda l: (l["account"], l["project"] or ""))
        if strip(old) != sorted(new_probe, key=lambda l: (l["account"], l["project"] or "")):
            if b.status == Budget.Status.ACTIVE and not reason:
                raise ValidationError({"change_reason": "Give a reason for revising an active budget."})
            _write_lines(b, parsed)
            b.version += 1
            revised = True
    b.save()
    if revised:
        _revision(b, actor, str(data.get("change_reason") or "").strip())
    after = _snapshot_audit(b)
    if before != after:
        ledger.audit_event(company, actor, "budget", b.pk, Action.UPDATED, previous=before, new=after,
                           reason=str(data.get("change_reason") or "").strip() if revised else "")
    return b


@transaction.atomic
def set_status(budget, actor, status):
    b = Budget.objects.select_for_update().select_related("financial_year", "company").get(pk=budget.pk)
    if status not in Budget.Status.values:
        raise ValidationError({"status": "Choose draft, active or closed."})
    if status == b.status:
        return b
    allowed = {(Budget.Status.DRAFT, Budget.Status.ACTIVE), (Budget.Status.ACTIVE, Budget.Status.CLOSED),
               (Budget.Status.CLOSED, Budget.Status.ACTIVE)}
    if (b.status, status) not in allowed:
        raise ValidationError({"status": f"A {b.get_status_display().lower()} budget cannot become {status}."})
    if status == Budget.Status.ACTIVE:
        if not b.lines.exists():
            raise ValidationError({"status": "Add at least one line before making the budget active."})
        other = Budget.objects.filter(company=b.company, financial_year=b.financial_year, status=Budget.Status.ACTIVE).exclude(pk=b.pk).first()
        if other:
            raise ValidationError({"status": f"“{other.name}” is already the active budget for {b.financial_year.label}. Close it first."})
    before = b.status
    b.status = status
    b.save(update_fields=["status", "updated_at"])
    ledger.audit_event(b.company, actor, "budget", b.pk, Action.UPDATED, previous={"status": before}, new={"status": status})
    return b


@transaction.atomic
def delete_budget(budget, actor):
    b = Budget.objects.select_for_update().get(pk=budget.pk)
    if b.status != Budget.Status.DRAFT:
        raise ValidationError("Only a draft budget can be deleted. Close an active one instead.")
    ledger.audit_event(b.company, actor, "budget", b.pk, Action.DELETED, previous=_snapshot_audit(b))
    b.delete()


# --------------------------------------------------------------------------- actuals and the report

def project_labels(company, ids):
    """{project id: "PRJ-… Name"} from the projects module, when it is installed."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    try:
        from apps.projects.models import Project
        return {p.pk: f"{p.code} {p.name}".strip() for p in Project.objects.filter(company=company, pk__in=ids)}
    except ImportError:
        return {}


def actuals(company, fy, account_ids=None, project=None):
    """({(account id, project id, month index 0-11): amount in the account's own direction}, {account id: (code, name, type)})."""
    months = fy_months(fy)
    first = months[0].year * 12 + months[0].month
    qs = JournalLine.objects.filter(entry__company=company, entry__date__gte=fy.start_date, entry__date__lte=fy.end_date,
                                    account__type__in=("income", "expense"))
    if account_ids is not None:
        qs = qs.filter(account_id__in=account_ids)
    if project:
        qs = qs.filter(project_id=project)
    rows = qs.annotate(m=TruncMonth("entry__date")).values("account_id", "account__code", "account__name", "account__type", "project_id",
                                                           "m").annotate(d=Sum("debit"), c=Sum("credit"))
    out, info = defaultdict(lambda: ZERO), {}
    for r in rows:
        idx = r["m"].year * 12 + r["m"].month - first
        if not 0 <= idx < 12:
            continue
        net = (r["d"] - r["c"]) if r["account__type"] == "expense" else (r["c"] - r["d"])
        out[(r["account_id"], r["project_id"], idx)] += net
        info[r["account_id"]] = (r["account__code"], r["account__name"], r["account__type"])
    return out, info


def line_actuals(lines, acts):
    """{line id: [12 amounts]}: the spending each line is compared with (see the rules at the top of the module)."""
    own = defaultdict(set)
    for l in lines:
        if l.project_id:
            own[l.account_id].add(l.project_id)
    projects_seen = defaultdict(set)
    for (aid, pid, _i) in acts:
        projects_seen[aid].add(pid)
    out = {}
    for l in lines:
        if l.project_id:
            out[l.id] = [acts.get((l.account_id, l.project_id, i), ZERO) for i in range(12)]
        else:
            others = [p for p in projects_seen[l.account_id] if p not in own[l.account_id]]
            out[l.id] = [sum((acts.get((l.account_id, p, i), ZERO) for p in others), ZERO) for i in range(12)]
    return out


def favourable(kind, budget, actual):
    """Variance with the sign that makes positive good."""
    return budget - actual if kind == "expense" else actual - budget


def _pct(variance, budget):
    return None if not budget else (variance / budget * 100).quantize(CENT, rounding=ROUND_HALF_UP)


def _figures(kind, budget_months, actual_months, idx):
    budget_total = sum(budget_months, ZERO)
    b_to, a_to = sum(budget_months[:idx + 1], ZERO), sum(actual_months[:idx + 1], ZERO)
    var = favourable(kind, b_to, a_to)
    return {"budget_total": budget_total, "budget_to_date": b_to, "actual_to_date": a_to, "variance": var,
            "variance_pct": _pct(var, b_to), "remaining": favourable(kind, budget_total, a_to), "over": var < 0}


def through_index(fy, through=None, today=None):
    """The index (0-11) of the month the report runs to: the one asked for, else today's, kept within the year."""
    months = fy_months(fy)
    if through:
        try:
            d = datetime.date.fromisoformat(f"{through}-01" if len(str(through)) == 7 else str(through))
        except ValueError:
            raise ValidationError({"through": "Use a month in YYYY-MM format."})
    else:
        d = today or datetime.date.today()
    idx = (d.year * 12 + d.month) - (months[0].year * 12 + months[0].month)
    return min(max(idx, 0), 11)


def budget_vs_actual(company, budget, *, through=None, project=None, today=None):
    fy = budget.financial_year
    months = fy_months(fy)
    idx = through_index(fy, through, today)
    all_lines = list(budget.lines.select_related("account"))
    lines = [l for l in all_lines if not project or str(l.project_id) == str(project)]
    acts, info = actuals(company, fy, project=project)
    per_line = line_actuals(all_lines if not project else lines, acts)
    labels = project_labels(company, {l.project_id for l in lines} | {k[1] for k in acts})
    result = {"budget": {"id": str(budget.pk), "name": budget.name, "status": budget.status, "version": budget.version,
                         "financial_year": fy.label, "start": fy.start_date, "end": fy.end_date},
              "months": [{"month": f"{m:%Y-%m}", "label": m.strftime("%b %Y")} for m in months],
              "through": f"{months[idx]:%Y-%m}", "through_index": idx, "project": str(project) if project else None}
    unbudgeted = []
    for kind in ("expense", "income"):
        kl = [l for l in lines if l.account.type == kind]
        by_acct, by_proj = {}, {}
        monthly_b, monthly_a = [ZERO] * 12, [ZERO] * 12
        for l in kl:
            bm = [Decimal(x) for x in l.monthly]
            am = per_line[l.id]
            monthly_b = [x + y for x, y in zip(monthly_b, bm)]
            monthly_a = [x + y for x, y in zip(monthly_a, am)]
            a = by_acct.setdefault(l.account_id, {"account": {"id": str(l.account_id), "code": l.account.code, "name": l.account.name},
                                                  "type": kind, "bm": [ZERO] * 12, "am": [ZERO] * 12, "cost_centres": []})
            a["bm"] = [x + y for x, y in zip(a["bm"], bm)]
            a["am"] = [x + y for x, y in zip(a["am"], am)]
            if l.project_id:
                a["cost_centres"].append(labels.get(l.project_id, "Project"))
            p = by_proj.setdefault(l.project_id, {"bm": [ZERO] * 12, "am": [ZERO] * 12})
            p["bm"] = [x + y for x, y in zip(p["bm"], bm)]
            p["am"] = [x + y for x, y in zip(p["am"], am)]
        rows = []
        for a in sorted(by_acct.values(), key=lambda r: r["account"]["code"]):
            rows.append({"account": a["account"], "type": kind, "cost_centres": a["cost_centres"],
                         "budget_months": a["bm"], "actual_months": a["am"], **_figures(kind, a["bm"], a["am"], idx)})
        projects = []
        for pid, p in by_proj.items():
            projects.append({"project": {"id": str(pid), "label": labels.get(pid, "Project")} if pid else None,
                             **_figures(kind, p["bm"], p["am"], idx)})
        projects.sort(key=lambda r: (r["project"] is not None, (r["project"] or {}).get("label", "")))
        cum_b = cum_a = ZERO
        monthly = []
        for i, m in enumerate(months):
            cum_b, cum_a = cum_b + monthly_b[i], cum_a + monthly_a[i]
            var = favourable(kind, monthly_b[i], monthly_a[i])
            monthly.append({"month": f"{m:%Y-%m}", "label": m.strftime("%b"), "budget": monthly_b[i], "actual": monthly_a[i],
                            "variance": var, "variance_pct": _pct(var, monthly_b[i]), "cum_budget": cum_b, "cum_actual": cum_a,
                            "cum_variance": favourable(kind, cum_b, cum_a), "over": var < 0})
        result[kind] = {"rows": rows, "totals": _figures(kind, monthly_b, monthly_a, idx), "monthly": monthly, "by_project": projects}
    # Spending the budget does not cover: an account with no line, or a project not named on a line of an account that only has
    # cost-centre lines.
    covered_proj, covered_acct = set(), set()
    own = defaultdict(set)
    for l in lines:
        if l.project_id:
            covered_proj.add((l.account_id, l.project_id))
            own[l.account_id].add(l.project_id)
        else:
            covered_acct.add(l.account_id)
    totals_unb = defaultdict(lambda: ZERO)
    for (aid, pid, i), amount in acts.items():
        if i > idx or amount == 0:
            continue
        if (aid, pid) in covered_proj or (not project and aid in covered_acct and pid not in own[aid]):
            continue
        totals_unb[(aid, pid)] += amount
    for (aid, pid), amount in sorted(totals_unb.items(), key=lambda kv: info[kv[0][0]][0]):
        code, name, typ = info[aid]
        unbudgeted.append({"account": {"id": str(aid), "code": code, "name": name}, "type": typ,
                           "project": {"id": str(pid), "label": labels.get(pid, "Project")} if pid else None, "actual_to_date": amount})
    result["unbudgeted"] = {"rows": unbudgeted,
                            "total_expense": sum((r["actual_to_date"] for r in unbudgeted if r["type"] == "expense"), ZERO),
                            "total_income": sum((r["actual_to_date"] for r in unbudgeted if r["type"] == "income"), ZERO)}
    return result


# --------------------------------------------------------------------------- the warning on an expense

def active_budget_for(company, on_date):
    return Budget.objects.filter(company=company, status=Budget.Status.ACTIVE, financial_year__start_date__lte=on_date,
                                 financial_year__end_date__gte=on_date).select_related("financial_year").first()


def expense_warning(company, *, account, on_date, amount, project_id=None, already_posted=False):
    """A warning (never an error) when adding `amount` to `account` would take it past the active budget line for the year,
    else None. The line is the one for the expense's project when it has one, else the account's whole-company line.
    `already_posted`: the expense is already in the journal, so it is not added to the spending a second time."""
    budget = active_budget_for(company, on_date)
    if budget is None or account is None:
        return None
    if project_id and not isinstance(project_id, uuid.UUID):
        project_id = uuid.UUID(str(project_id))
    lines = list(budget.lines.filter(account=account).select_related("account"))
    if not lines:
        return None
    line = next((l for l in lines if project_id and l.project_id == project_id), None) or next((l for l in lines if not l.project_id), None)
    if line is None:
        return None
    acts, _ = actuals(company, budget.financial_year, account_ids=[account.pk])
    spent = sum(line_actuals(lines, acts)[line.id], ZERO)
    if already_posted:
        spent -= amount
    projected = spent + amount
    if projected <= line.annual:
        return None
    labels = project_labels(company, [line.project_id])
    centre = f" for {labels.get(line.project_id, 'this project')}" if line.project_id else ""
    over = projected - line.annual
    return {"budget": budget.name, "budget_id": str(budget.pk), "account": f"{account.code} {account.name}",
            "cost_centre": labels.get(line.project_id, "") if line.project_id else "", "budget_amount": line.annual,
            "spent_so_far": spent, "this_amount": amount, "projected": projected, "over_by": over,
            "message": (f"Over budget: with this expense {account.name}{centre} reaches {projected:,.2f} against a budget of "
                        f"{line.annual:,.2f} for {budget.financial_year.label} ({budget.name}), which is {over:,.2f} over.")}
