"""Payroll reports and the HR dashboard. Reports read finalised runs (approved, posted or paid) only."""
import datetime
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.db.models.fields.json import KeyTextTransform

from ..models import Attendance, Employee, LeaveRequest, Payslip, PayrollRun
from . import common, leave
from .common import ZERO, add_months, get_settings, month_start

FINAL = ("approved", "posted", "paid")
SUMS = {"gross": Sum("gross"), "net": Sum("net_pay"), "deductions": Sum("total_deductions"), "pf_employee": Sum("pf_employee"),
        "pf_employer": Sum("pf_employer"), "esi_employee": Sum("esi_employee"), "esi_employer": Sum("esi_employer"),
        "pt": Sum("professional_tax"), "tds": Sum("tds"), "employer_cost": Sum("employer_cost"), "employees": Count("id")}


def _slips(company, start, end):
    return Payslip.objects.filter(run__company=company, run__status__in=FINAL, month__gte=start, month__lte=end)


def _clean(row):
    return {k: (v if k in ("month", "department", "employees", "employee", "code") else (v or ZERO)) for k, v in row.items()}


def monthly_cost(company, start, end):
    rows = _slips(company, start, end).values("month").annotate(**SUMS).order_by("month")
    out = []
    for r in rows:
        r = _clean(r)
        r["employer_contributions"] = r["pf_employer"] + r["esi_employer"]
        out.append(r)
    return out


def department_cost(company, start, end):
    rows = _slips(company, start, end).annotate(department=KeyTextTransform("department", "who")).values("department").annotate(**SUMS).order_by("-employer_cost")
    out = []
    for r in rows:
        r = _clean(r)
        r["department"] = r["department"] or "No department"
        out.append(r)
    return out


def statutory(company, start, end):
    rows = _slips(company, start, end).values("month").annotate(**SUMS).order_by("month")
    out = [_clean(r) for r in rows]
    total = {k: sum((r[k] for r in out), ZERO) for k in ("pf_employee", "pf_employer", "esi_employee", "esi_employer", "pt", "tds")}
    return {"months": out, "total": total}


def register(company, month):
    qs = Payslip.objects.filter(run__company=company, run__status__in=FINAL, month=month).select_related("employee", "run")
    return [{"employee": s.who.get("name"), "code": s.who.get("code"), "department": s.who.get("department"), "paid_days": s.paid_days,
             "lop_days": s.lop_days, "basic": s.basic, "hra": s.hra, "special_allowance": s.special_allowance,
             "other_allowance": s.other_allowance, "bonus": s.bonus, "gross": s.gross, "pf_employee": s.pf_employee,
             "esi_employee": s.esi_employee, "professional_tax": s.professional_tax, "tds": s.tds, "loan_recovery": s.loan_recovery,
             "other_deductions": s.other_deductions, "total_deductions": s.total_deductions, "net_pay": s.net_pay,
             "pf_employer": s.pf_employer, "esi_employer": s.esi_employer, "run": s.run.number}
            for s in sorted(qs, key=lambda s: (s.who.get("name") or ""))]


REGISTER_COLUMNS = [("code", "Employee code"), ("employee", "Employee"), ("department", "Department"), ("run", "Payroll run"),
                    ("paid_days", "Paid days"), ("lop_days", "LOP days"), ("basic", "Basic"), ("hra", "HRA"),
                    ("special_allowance", "Special allowance"), ("other_allowance", "Other allowance"), ("bonus", "Bonus"),
                    ("gross", "Gross"), ("pf_employee", "PF (employee)"), ("esi_employee", "ESI (employee)"),
                    ("professional_tax", "Professional tax"), ("tds", "TDS"), ("loan_recovery", "Advance recovery"),
                    ("other_deductions", "Other deductions"), ("total_deductions", "Total deductions"), ("net_pay", "Net pay"),
                    ("pf_employer", "PF (employer)"), ("esi_employer", "ESI (employer)")]


def fy_range(year):
    return datetime.date(year, 4, 1), datetime.date(year + 1, 3, 31)


def ytd(company, year, employee=None):
    start, end = fy_range(year)
    qs = _slips(company, start, end)
    if employee is not None:
        qs = qs.filter(employee=employee)
    rows = qs.values("employee_id").annotate(first=Max("employee__first_name"), last=Max("employee__last_name"), code=Max("employee__code"),
                                             **{k: v for k, v in SUMS.items() if k != "employees"}, months=Count("id")).order_by("first", "last")
    skip = ("employee_id", "first", "last", "code", "months")
    return {"year": year, "start": start, "end": end,
            "rows": [{**{k: (v or ZERO) for k, v in r.items() if k not in skip}, "employee_id": str(r["employee_id"]),
                      "name": f"{r['first']} {r['last']}".strip(), "code": r["code"], "months": r["months"]} for r in rows]}


# --------------------------------------------------------------------------- dashboard

def _next_occurrence(d, today):
    try:
        n = d.replace(year=today.year)
    except ValueError:
        n = d.replace(year=today.year, day=28)
    if n < today:
        try:
            n = d.replace(year=today.year + 1)
        except ValueError:
            n = d.replace(year=today.year + 1, day=28)
    return n


def dashboard(company, user, today=None):
    today = today or datetime.date.today()
    base = Employee.objects.filter(company=company)
    current = base.exclude(status="exited")
    first = month_start(today)
    settings = get_settings(company)

    by_dept = list(current.values("department__name").annotate(n=Count("id")).order_by("-n"))
    status_counts = dict(base.values_list("status").annotate(n=Count("id")))
    trend = []
    for i in range(5, -1, -1):
        m = add_months(first, -i)
        e = common.month_end(m)
        trend.append({"month": m, "label": m.strftime("%b %y"), "joined": base.filter(date_of_joining__range=(m, e)).count(),
                      "exited": base.filter(exit_date__range=(m, e)).count()})

    upcoming = []
    for e in current:
        for kind, d in (("birthday", e.date_of_birth), ("anniversary", e.date_of_joining)):
            if not d:
                continue
            n = _next_occurrence(d, today)
            years = n.year - d.year
            if (n - today).days <= 30 and (kind == "birthday" or years >= 1):
                upcoming.append({"employee": {"id": str(e.id), "name": e.name}, "kind": kind, "date": n, "years": years if kind == "anniversary" else None,
                                 "in_days": (n - today).days})
    upcoming.sort(key=lambda r: (r["date"], r["employee"]["name"]))

    on_leave = list(LeaveRequest.objects.filter(company=company, status="approved", start_date__lte=today, end_date__gte=today)
                    .select_related("employee", "leave_type"))
    pending = LeaveRequest.objects.filter(company=company, status="pending_approval").select_related("employee", "leave_type").order_by("start_date")
    att = {s: 0 for s in Attendance.Status.values}
    att.update(dict(Attendance.objects.filter(employee__company=company, date=today).values_list("status").annotate(n=Count("id"))))
    expected = current.filter(date_of_joining__lte=today).count()

    out = {
        "today": today,
        "headcount": current.count(), "active": status_counts.get("active", 0), "on_notice": status_counts.get("on_notice", 0),
        "exited": status_counts.get("exited", 0),
        "new_joiners": base.filter(date_of_joining__range=(first, common.month_end(first))).count(),
        "exits_this_month": base.filter(exit_date__range=(first, common.month_end(first))).count(),
        "by_department": [{"name": r["department__name"] or "No department", "count": r["n"]} for r in by_dept],
        "by_type": [{"type": k, "count": v} for k, v in current.values_list("employment_type").annotate(n=Count("id")).order_by("-n")],
        "trend": trend, "upcoming": upcoming[:12],
        "on_leave_today": [{"employee": {"id": str(r.employee_id), "name": r.employee.name}, "leave_type": r.leave_type.name,
                            "until": r.end_date} for r in on_leave],
        "attendance_today": {**att, "expected": expected, "marked": sum(att.values()),
                             "is_weekly_off": today.weekday() in (settings.weekly_off_days or []),
                             "is_holiday": today in leave.holiday_dates(company, today, today)},
        "pending_leave": [{"id": str(r.id), "number": r.number, "employee": {"id": str(r.employee_id), "name": r.employee.name},
                           "leave_type": r.leave_type.name, "start_date": r.start_date, "end_date": r.end_date, "days": r.days}
                          for r in pending[:8]],
        "pending_leave_count": pending.count(),
        "payroll": None,
    }
    if user.has_code("hr.payroll"):
        run = PayrollRun.objects.filter(company=company, month=first).exclude(status="void").first()
        months = [r for r in monthly_cost(company, add_months(first, -5), first)]
        by_month = {r["month"]: r for r in months}
        out["payroll"] = {
            "month": first, "run": ({"id": str(run.id), "number": run.number, "status": run.status, "status_label": run.get_status_display(),
                                     "total_net": run.total_net, "employer_cost": run.total_employer_cost, "employees": run.employee_count}
                                    if run else None),
            "trend": [{"month": add_months(first, -i), "label": add_months(first, -i).strftime("%b %y"),
                       "employer_cost": (by_month.get(add_months(first, -i)) or {}).get("employer_cost", ZERO),
                       "net": (by_month.get(add_months(first, -i)) or {}).get("net", ZERO)} for i in range(5, -1, -1)],
        }
    return out
