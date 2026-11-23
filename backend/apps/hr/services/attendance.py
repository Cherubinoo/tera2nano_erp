"""Attendance: daily marking, bulk marking, self check-in and the monthly grid."""
import calendar
import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q

from ..models import Attendance, Employee
from . import common, leave
from .common import Action, daterange, get_settings

S = Attendance.Status
# Letters shown in the monthly grid.
CODE = {S.PRESENT: "P", S.ABSENT: "A", S.HALF_DAY: "H", S.LEAVE: "L", S.HOLIDAY: "O", S.WFH: "W"}


def _time(data, key):
    value = data.get(key)
    if value in (None, ""):
        return None
    try:
        return datetime.time.fromisoformat(str(value)[:8])
    except ValueError:
        raise ValidationError({key: "Use a time in HH:MM format."})


def _check_date(employee, date):
    if date > datetime.date.today():
        raise ValidationError({"date": "Attendance cannot be marked for a future date."})
    if date < employee.date_of_joining:
        raise ValidationError({"date": f"{employee.name} joined on {employee.date_of_joining.isoformat()}."})
    if employee.exit_date and date > employee.exit_date:
        raise ValidationError({"date": f"{employee.name} left on {employee.exit_date.isoformat()}."})


def mark(employee, date, status, actor, *, check_in=None, check_out=None, note="", source=Attendance.Source.HR):
    if status not in S.values:
        raise ValidationError({"status": "Choose a valid attendance status."})
    _check_date(employee, date)
    if check_in and check_out and check_out < check_in:
        raise ValidationError({"check_out": "Check-out cannot be earlier than check-in."})
    row = Attendance.objects.filter(employee=employee, date=date).first()
    if row is not None and row.source == Attendance.Source.LEAVE:
        raise ValidationError({"date": f"{employee.name} has approved leave on {date.isoformat()}. Cancel the leave first."})
    row = row or Attendance(employee=employee, date=date)
    row.status, row.check_in, row.check_out, row.note, row.source, row.marked_by = status, check_in, check_out, note[:200], source, actor
    row.save()
    return row


@transaction.atomic
def bulk_mark(company, data, actor):
    """Mark many employees for one date. `records` gives each person a status; otherwise `status` applies to `employees`
    (or everyone active when `employees` is empty). People on approved leave that day are skipped."""
    date = common.date_value(data, "date", "Date", required=True)
    people = Employee.objects.filter(company=company, date_of_joining__lte=date).filter(Q(exit_date__isnull=True) | Q(exit_date__gte=date)).exclude(status="exited", exit_date__lt=date)
    records = data.get("records")
    done, skipped = 0, []
    if records is not None:
        if not isinstance(records, list):
            raise ValidationError({"records": "Send a list."})
        by_id = {str(e.pk): e for e in people.filter(pk__in=[r.get("employee") for r in records if isinstance(r, dict) and r.get("employee")])}
        for n, r in enumerate(records, 1):
            if not isinstance(r, dict) or str(r.get("employee")) not in by_id:
                raise ValidationError({"records": f"Row {n}: choose an employee who was working on that date."})
            e = by_id[str(r["employee"])]
            try:
                mark(e, date, r.get("status"), actor, check_in=_time(r, "check_in"), check_out=_time(r, "check_out"), note=str(r.get("note") or ""))
                done += 1
            except ValidationError as exc:
                skipped.append({"employee": e.name, "reason": "; ".join(exc.messages)})
    else:
        status = common.choice(data, "status", "status", S.values, required=True)
        ids = data.get("employees") or []
        if ids:
            people = people.filter(pk__in=ids)
        for e in people.exclude(status="exited"):
            try:
                mark(e, date, status, actor, note=common.text(data, "note", "Note", 200))
                done += 1
            except ValidationError as exc:
                skipped.append({"employee": e.name, "reason": "; ".join(exc.messages)})
    common.log(company, actor, "attendance", company.pk, Action.UPDATED, new={"date": date.isoformat(), "marked": done, "skipped": len(skipped)})
    return {"date": date, "marked": done, "skipped": skipped}


def day_sheet(company, date):
    """Everyone's attendance for one date, with unmarked people listed too."""
    rows = {a.employee_id: a for a in Attendance.objects.filter(employee__company=company, date=date)}
    settings = get_settings(company)
    people = Employee.objects.filter(company=company, date_of_joining__lte=date).filter(
        Q(exit_date__isnull=True) | Q(exit_date__gte=date)).exclude(status="exited", exit_date__lt=date).select_related("department", "designation")
    holiday = leave.holiday_dates(company, date, date)
    out = []
    for e in people:
        a = rows.get(e.pk)
        out.append({"employee": {"id": str(e.id), "name": e.name, "code": e.code, "department": e.department.name if e.department else ""},
                    "status": a.status if a else "", "check_in": a.check_in if a else None, "check_out": a.check_out if a else None,
                    "note": a.note if a else "", "source": a.source if a else ""})
    return {"date": date, "is_weekly_off": date.weekday() in (settings.weekly_off_days or []), "is_holiday": date in holiday, "rows": out}


def month_grid(company, month, *, department=None, employee_ids=None):
    """{days: [dates], rows: [{employee, cells: {date: code}, summary}]} for the month."""
    first, last = month, common.month_end(month)
    settings = get_settings(company)
    off = set(settings.weekly_off_days or [])
    holidays = leave.holiday_dates(company, first, last)
    people = Employee.objects.filter(company=company, date_of_joining__lte=last).filter(
        Q(exit_date__isnull=True) | Q(exit_date__gte=first)).select_related("department")
    if department:
        people = people.filter(department_id=department)
    if employee_ids is not None:
        people = people.filter(pk__in=employee_ids)
    by_emp = {}
    for a in Attendance.objects.filter(employee__in=people, date__range=(first, last)):
        by_emp.setdefault(a.employee_id, {})[a.date] = a
    today = datetime.date.today()
    days = [d for d in daterange(first, last)]
    rows = []
    for e in people:
        recs = by_emp.get(e.pk, {})
        cells, summary = {}, {s: 0.0 for s in S.values}
        unmarked = 0
        for d in days:
            a = recs.get(d)
            if a:
                cells[d.isoformat()] = CODE[a.status]
                summary[a.status] += 0.5 if a.status == S.HALF_DAY else 1
            elif d < e.date_of_joining or (e.exit_date and d > e.exit_date):
                cells[d.isoformat()] = "-"
            elif d in holidays:
                cells[d.isoformat()] = "O"
                summary[S.HOLIDAY] += 1
            elif d.weekday() in off:
                cells[d.isoformat()] = "-"
            else:
                cells[d.isoformat()] = ""
                if d <= today:
                    unmarked += 1
        rows.append({"employee": {"id": str(e.id), "name": e.name, "code": e.code, "department": e.department.name if e.department else ""},
                     "cells": cells, "summary": {**summary, "unmarked": unmarked}})
    return {"month": first, "days": [{"date": d.isoformat(), "weekday": d.strftime("%a"), "off": d.weekday() in off, "holiday": d in holidays} for d in days],
            "rows": rows, "codes": {v: k.label for k, v in CODE.items()}}


def self_check(employee, action, actor, *, status="present", now=None):
    """check-in creates today's row (present, or work from home); check-out adds the time out to it."""
    now = now or datetime.datetime.now()
    today = now.date()
    t = now.time().replace(microsecond=0, second=0)
    row = Attendance.objects.filter(employee=employee, date=today).first()
    if action == "in":
        if status not in (S.PRESENT, S.WFH):
            raise ValidationError({"status": "Choose present or work from home."})
        if row is not None and row.source == Attendance.Source.LEAVE:
            raise ValidationError("You are on approved leave today.")
        if row is not None and row.check_in is not None:
            raise ValidationError(f"You already checked in at {row.check_in.strftime('%H:%M')}.")
        return mark(employee, today, status, actor, check_in=t, source=Attendance.Source.SELF)
    if row is None or row.check_in is None:
        raise ValidationError("Check in first.")
    if row.check_out is not None:
        raise ValidationError(f"You already checked out at {row.check_out.strftime('%H:%M')}.")
    if t < row.check_in:
        raise ValidationError("Check-out cannot be earlier than check-in.")
    row.check_out = t
    row.save(update_fields=["check_out", "updated_at"])
    return row


def counts_for(company, date):
    c = dict(Attendance.objects.filter(employee__company=company, date=date).values_list("status").annotate(n=Count("id")))
    return {s: c.get(s, 0) for s in S.values}
