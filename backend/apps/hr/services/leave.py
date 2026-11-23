"""Leave: types, balances, requests and their approval.

A leave year runs from `HRSettings.leave_year_start_month` (April by default). For each type an employee's balance is

    accrued (the annual quota, pro-rated by whole months left in the year for someone who joined during it)
  + carried forward (last year's unused leave, up to the type's limit, for types that carry forward)
  + adjustments (manual credits or debits)
  - taken (approved requests)  - pending (requests waiting for approval, so two requests cannot spend the same days)

Days are counted without the weekly offs and holidays. A request may not overlap another pending or approved request
of the same person. Unpaid types have no balance limit; their days reduce pay (see payroll.lop).
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from rest_framework.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.company.constants import DocType
from apps.company.services import numbering

from ..models import Attendance, Employee, Holiday, LeaveAdjustment, LeaveRequest, LeaveType
from . import common
from .common import Action, daterange, get_settings

Status = LeaveRequest.Status
D0 = Decimal("0.0")


def leave_year(on_date, settings):
    start_month = settings.leave_year_start_month or 1
    year = on_date.year if on_date.month >= start_month else on_date.year - 1
    start = datetime.date(year, start_month, 1)
    end = datetime.date(year + 1, start_month, 1) - datetime.timedelta(days=1)
    return start, end


def holiday_dates(company, start, end, include_optional=False):
    qs = Holiday.objects.filter(company=company, date__range=(start, end))
    if not include_optional:
        qs = qs.filter(is_optional=False)
    return set(qs.values_list("date", flat=True))


def counted_dates(company, start, end, settings):
    """The days in [start, end] that use up leave: not a weekly off, not a holiday."""
    off = set(settings.weekly_off_days or [])
    holidays = holiday_dates(company, start, end)
    return [d for d in daterange(start, end) if d.weekday() not in off and d not in holidays]


def count_days(company, start, end, half_day, settings):
    n = len(counted_dates(company, start, end, settings))
    return Decimal("0.5") if (half_day and n) else Decimal(n)


def _accrued(employee, lt, start, end):
    quota = lt.annual_quota
    if employee.date_of_joining > start:
        j = employee.date_of_joining
        months_left = (end.year - j.year) * 12 + (end.month - j.month) + (0 if j.day > 15 else 1)
        months_left = max(0, min(12, months_left))
        return common.half(quota * months_left / 12)
    return quota


def _taken(employee, lt, start, end, status):
    return LeaveRequest.objects.filter(employee=employee, leave_type=lt, status=status, start_date__gte=start, start_date__lte=end
                                       ).aggregate(t=Sum("days"))["t"] or D0


def _adjust(employee, lt, start):
    return LeaveAdjustment.objects.filter(employee=employee, leave_type=lt, year_start=start).aggregate(t=Sum("days"))["t"] or D0


def balance(employee, lt, on_date, settings):
    start, end = leave_year(on_date, settings)
    accrued = _accrued(employee, lt, start, end)
    carried = D0
    if lt.carry_forward and employee.date_of_joining < start:
        p_start, p_end = leave_year(start - datetime.timedelta(days=1), settings)
        left = _accrued(employee, lt, p_start, p_end) + _adjust(employee, lt, p_start) - _taken(employee, lt, p_start, p_end, Status.APPROVED)
        carried = min(max(left, D0), lt.max_carry_forward)
    adjustments = _adjust(employee, lt, start)
    taken = _taken(employee, lt, start, end, Status.APPROVED)
    pending = _taken(employee, lt, start, end, Status.PENDING)
    available = accrued + carried + adjustments - taken - pending
    return {"leave_type": {"id": str(lt.id), "code": lt.code, "name": lt.name, "is_paid": lt.is_paid},
            "year_start": start, "year_end": end, "accrued": accrued, "carried_forward": carried, "adjustments": adjustments,
            "taken": taken, "pending": pending, "available": available, "opening": carried + adjustments,
            "total": accrued + carried + adjustments}


def balances(employee, on_date=None):
    on_date = on_date or datetime.date.today()
    settings = get_settings(employee.company)
    return [balance(employee, lt, on_date, settings) for lt in LeaveType.objects.filter(company=employee.company, is_active=True)]


# --------------------------------------------------------------------------- requests

def _check(employee, lt, start, end, half_day, settings, exclude=None):
    """Validate a request's dates, overlap and balance; return its day count."""
    if start is None or end is None:
        raise ValidationError({"start_date": "Choose the first and last day."})
    if end < start:
        raise ValidationError({"end_date": "The last day cannot be before the first day."})
    if half_day and start != end:
        raise ValidationError({"half_day": "A half day can only be taken on a single date."})
    if employee.status == Employee.Status.EXITED:
        raise ValidationError({"employee": "This employee has left the company."})
    if start < employee.date_of_joining:
        raise ValidationError({"start_date": "Leave cannot start before the date of joining."})
    if employee.exit_date and end > employee.exit_date:
        raise ValidationError({"end_date": "Leave cannot run past the last working day."})
    if not lt.is_active:
        raise ValidationError({"leave_type": "This leave type is switched off."})
    if (end - start).days > 365:
        raise ValidationError({"end_date": "A request cannot be longer than a year."})
    y_start, y_end = leave_year(start, settings)
    if end > y_end:
        raise ValidationError({"end_date": f"This runs into the next leave year (from {y_end + datetime.timedelta(days=1)}). Make a separate request for each year."})
    days = count_days(employee.company, start, end, half_day, settings)
    if days <= 0:
        raise ValidationError({"start_date": "These dates are all weekly offs or holidays; no leave is needed."})
    clash = LeaveRequest.objects.filter(employee=employee, status__in=(Status.PENDING, Status.APPROVED),
                                        start_date__lte=end, end_date__gte=start)
    if exclude is not None:
        clash = clash.exclude(pk=exclude.pk)
    other = clash.first()
    if other:
        raise ValidationError({"start_date": f"This overlaps another request that is {other.get_status_display().lower()} "
                                             f"({other.start_date.isoformat()} to {other.end_date.isoformat()})."})
    if lt.is_paid:
        have = balance(employee, lt, start, settings)["available"]
        if exclude is not None and exclude.status == Status.PENDING and exclude.leave_type_id == lt.pk:
            have += exclude.days  # the request being re-checked is already counted as pending
        if days > have:
            raise ValidationError({"leave_type": f"Not enough {lt.name}: {days} days asked, {have} available."})
    return days


def create_request(employee, data, actor, *, submit=False):
    company = employee.company
    settings = get_settings(company)
    lt = _leave_type(company, data.get("leave_type"))
    start = common.date_value(data, "start_date", "First day", required=True)
    end = common.date_value(data, "end_date", "Last day") or start
    half_day = common.boolean(data, "half_day", False)
    reason = common.text(data, "reason", "Reason", 500)
    days = _check(employee, lt, start, end, half_day, settings)
    req = LeaveRequest.objects.create(company=company, employee=employee, leave_type=lt, start_date=start, end_date=end,
                                      half_day=half_day, days=days, reason=reason, requested_by=actor)
    common.log(company, actor, "leave_request", req.pk, Action.CREATED, new=_snap(req))
    if submit:
        req = submit_request(req, actor)
    return req


def _leave_type(company, value):
    try:
        lt = LeaveType.objects.filter(pk=value, company=company).first()
    except (ValueError, ValidationError):
        lt = None
    if lt is None:
        raise ValidationError({"leave_type": "Choose a leave type."})
    return lt


def _snap(r):
    return {"employee": str(r.employee_id), "leave_type": r.leave_type.code, "start_date": r.start_date.isoformat(),
            "end_date": r.end_date.isoformat(), "days": str(r.days), "status": r.status, "number": r.number}


@transaction.atomic
def update_request(req, data, actor):
    req = LeaveRequest.objects.select_for_update(of=("self",)).select_related("employee", "leave_type", "company").get(pk=req.pk)
    if req.status != Status.DRAFT:
        raise ValidationError("Only a draft can be edited. Cancel this request and make a new one.")
    before = _snap(req)
    settings = get_settings(req.company)
    if "leave_type" in data:
        req.leave_type = _leave_type(req.company, data["leave_type"])
    if "start_date" in data:
        req.start_date = common.date_value(data, "start_date", "First day", required=True)
    if "end_date" in data:
        req.end_date = common.date_value(data, "end_date", "Last day") or req.start_date
    if "half_day" in data:
        req.half_day = common.boolean(data, "half_day")
    if "reason" in data:
        req.reason = common.text(data, "reason", "Reason", 500)
    req.days = _check(req.employee, req.leave_type, req.start_date, req.end_date, req.half_day, settings, exclude=req)
    req.save()
    common.log(req.company, actor, "leave_request", req.pk, Action.UPDATED, previous=before, new=_snap(req))
    return req


@transaction.atomic
def submit_request(req, actor):
    req = LeaveRequest.objects.select_for_update(of=("self",)).select_related("employee", "leave_type", "company").get(pk=req.pk)
    if req.status != Status.DRAFT:
        raise ValidationError("Only a draft can be submitted.")
    settings = get_settings(req.company)
    req.days = _check(req.employee, req.leave_type, req.start_date, req.end_date, req.half_day, settings, exclude=req)
    req.number = req.number or numbering.allocate_number(req.company, DocType.LEAVE_REQUEST, actor=actor, on_date=req.start_date).reference_number
    req.status = Status.PENDING
    req.save()
    common.log(req.company, actor, "leave_request", req.pk, Action.UPDATED, previous={"status": "draft"},
               new={"status": "pending_approval", "number": req.number})
    from .. import approval_hooks
    approval_hooks.submit_leave(req, actor)  # the approvals module may approve it straight away (callbacks run in this transaction)
    req.refresh_from_db()
    return req


def can_decide(actor, req):
    """The employee's manager, or anyone who manages HR, may decide a request; nobody decides their own."""
    mine = getattr(actor, "employee", None)
    if mine is not None and mine.pk == req.employee_id:
        return False
    if actor.has_code("hr.manage"):
        return True
    return bool(mine is not None and req.employee.manager_id == mine.pk)


def _decide_guard(actor, req, trusted=False):
    if trusted:  # the approvals module has already checked who may decide
        return
    if not can_decide(actor, req):
        raise PermissionDenied("Only the employee's manager or HR can decide this request, and nobody can decide their own.")


@transaction.atomic
def approve(req, actor, note="", *, trusted=False):
    req = LeaveRequest.objects.select_for_update(of=("self",)).select_related("employee", "leave_type", "company").get(pk=req.pk)
    _decide_guard(actor, req, trusted)
    if req.status != Status.PENDING:
        raise ValidationError("Only a request that is waiting for approval can be approved.")
    settings = get_settings(req.company)
    req.days = _check(req.employee, req.leave_type, req.start_date, req.end_date, req.half_day, settings, exclude=req)
    req.status, req.decided_by, req.decided_at, req.decision_note = Status.APPROVED, actor, timezone.now(), (note or "")[:300]
    req.save()
    _write_attendance(req, settings)
    common.log(req.company, actor, "leave_request", req.pk, Action.UPDATED, previous={"status": "pending_approval"},
               new={"status": "approved"}, reason=note)
    _close_approval(req, actor, trusted)
    return req


@transaction.atomic
def reject(req, actor, note, *, trusted=False):
    req = LeaveRequest.objects.select_for_update(of=("self",)).select_related("employee", "company").get(pk=req.pk)
    _decide_guard(actor, req, trusted)
    if req.status != Status.PENDING:
        raise ValidationError("Only a request that is waiting for approval can be rejected.")
    if not (note or "").strip():
        raise ValidationError({"note": "Give a reason for rejecting this request."})
    req.status, req.decided_by, req.decided_at, req.decision_note = Status.REJECTED, actor, timezone.now(), note.strip()[:300]
    req.save()
    common.log(req.company, actor, "leave_request", req.pk, Action.UPDATED, previous={"status": "pending_approval"},
               new={"status": "rejected"}, reason=note)
    _close_approval(req, actor, trusted)
    return req


@transaction.atomic
def cancel(req, actor, note=""):
    req = LeaveRequest.objects.select_for_update(of=("self",)).select_related("employee", "company").get(pk=req.pk)
    mine = getattr(actor, "employee", None)
    owner = mine is not None and mine.pk == req.employee_id
    if not (owner or actor.has_code("hr.manage")):
        raise PermissionDenied("Only the employee or HR can cancel a request.")
    if req.status not in (Status.DRAFT, Status.PENDING, Status.APPROVED):
        raise ValidationError("This request can no longer be cancelled.")
    if req.status == Status.APPROVED and owner and not actor.has_code("hr.manage") and req.start_date <= datetime.date.today():
        raise ValidationError("Leave that has started can only be cancelled by HR.")
    was = req.status
    req.status, req.decision_note = Status.CANCELLED, (note or req.decision_note)[:300]
    req.save()
    Attendance.objects.filter(leave_request=req).delete()
    common.log(req.company, actor, "leave_request", req.pk, Action.UPDATED, previous={"status": was}, new={"status": "cancelled"}, reason=note)
    _close_approval(req, actor, False)
    return req


def _close_approval(req, actor, trusted):
    """Decided or cancelled here (by the manager or HR): close the matching approval request so it is not left open."""
    if not trusted:
        from .. import approval_hooks
        approval_hooks.close_request("leave_request", req.pk, actor)


@transaction.atomic
def withdraw(req, actor):
    """The approval request was withdrawn in the Approvals module: the leave request goes back to a draft."""
    req = LeaveRequest.objects.select_for_update(of=("self",)).select_related("employee", "company").get(pk=req.pk)
    if req.status == Status.PENDING:
        req.status = Status.DRAFT
        req.save()
        common.log(req.company, actor, "leave_request", req.pk, Action.UPDATED, previous={"status": "pending_approval"}, new={"status": "draft"},
                   reason="Approval request withdrawn")
    return req


def _write_attendance(req, settings):
    """An approved leave shows in the attendance sheet: 'On leave' (or a half day for a half-day leave)."""
    for d in counted_dates(req.company, req.start_date, req.end_date, settings):
        status = Attendance.Status.HALF_DAY if req.half_day else Attendance.Status.LEAVE
        Attendance.objects.update_or_create(employee=req.employee, date=d, defaults={
            "status": status, "source": Attendance.Source.LEAVE, "leave_request": req, "check_in": None, "check_out": None,
            "note": req.leave_type.name})


def unpaid_leave_dates(employee, start, end, settings):
    """{date: weight} of approved unpaid leave inside [start, end]; a half day weighs 0.5."""
    out = {}
    reqs = LeaveRequest.objects.filter(employee=employee, status=Status.APPROVED, leave_type__is_paid=False,
                                       start_date__lte=end, end_date__gte=start).select_related("leave_type")
    for r in reqs:
        for d in counted_dates(employee.company, max(r.start_date, start), min(r.end_date, end), settings):
            out[d] = Decimal("0.5") if r.half_day else Decimal(1)
    return out
