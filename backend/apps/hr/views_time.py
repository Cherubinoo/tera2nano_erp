"""Holidays, attendance and leave."""
import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from rest_framework import status as http
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company
from apps.company.models import CompanyProfileAuditEvent

from .models import Attendance, Employee, Holiday, LeaveAdjustment, LeaveRequest, LeaveType
from .services import attendance as att
from .services import common, leave
from .views_base import brief, get_employee, get_or_404, is_self, is_team_lead, me, need

Action = CompanyProfileAuditEvent.Action


def _month(request):
    value = request.query_params.get("month")
    return common.parse_month(value) if value else common.month_start(datetime.date.today())


def can_see_person(request, employee):
    return request.user.has_code("hr.view") or is_self(request, employee) or is_team_lead(request, employee)


# --------------------------------------------------------------------------- holidays

def holiday_dict(h):
    return {"id": str(h.id), "date": h.date, "name": h.name, "is_optional": h.is_optional, "weekday": h.date.strftime("%A")}


def _holiday_apply(h, data):
    h.date = common.date_value(data, "date", "Date", required=True) if "date" in data or h._state.adding else h.date
    if "name" in data or h._state.adding:
        h.name = common.text(data, "name", "Name", 120, required=True)
    if "is_optional" in data:
        h.is_optional = common.boolean(data, "is_optional")
    if Holiday.objects.exclude(pk=h.pk).filter(company=h.company, date=h.date).exists():
        raise ValidationError({"date": "There is already a holiday on this date."})


class HolidayCollection(APIView):
    def get(self, request):  # every signed-in user: needed to plan leave
        year = request.query_params.get("year")
        qs = Holiday.objects.filter(company=current_company())
        if year:
            try:
                qs = qs.filter(date__year=int(year))
            except ValueError:
                raise ValidationError({"year": "Use a four-digit year."})
        return Response([holiday_dict(h) for h in qs])

    @transaction.atomic
    def post(self, request):
        need(request, "hr.manage")
        h = Holiday(company=current_company())
        _holiday_apply(h, request.data)
        h.save()
        common.log(h.company, request.user, "holiday", h.pk, Action.CREATED, new={"date": h.date.isoformat(), "name": h.name})
        return Response(holiday_dict(h), status=http.HTTP_201_CREATED)


class HolidayDetail(APIView):
    @transaction.atomic
    def patch(self, request, pk):
        need(request, "hr.manage")
        h = get_or_404(Holiday, pk, "holiday", company=current_company())
        before = {"date": h.date.isoformat(), "name": h.name, "is_optional": h.is_optional}
        _holiday_apply(h, request.data)
        h.save()
        common.log(h.company, request.user, "holiday", h.pk, Action.UPDATED, previous=before,
                   new={"date": h.date.isoformat(), "name": h.name, "is_optional": h.is_optional})
        return Response(holiday_dict(h))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "hr.manage")
        h = get_or_404(Holiday, pk, "holiday", company=current_company())
        common.log(h.company, request.user, "holiday", h.pk, Action.DELETED, previous={"date": h.date.isoformat(), "name": h.name})
        h.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- attendance

def att_dict(a):
    return {"id": str(a.id), "date": a.date, "status": a.status, "status_label": a.get_status_display(), "check_in": a.check_in,
            "check_out": a.check_out, "note": a.note, "source": a.source}


class AttendanceCollection(APIView):
    def get(self, request):
        need(request, "hr.view")
        date = common.date_value(request.query_params, "date", "Date") or datetime.date.today()
        return Response(att.day_sheet(current_company(), date))

    @transaction.atomic
    def post(self, request):
        need(request, "hr.manage")
        return Response(att.bulk_mark(current_company(), request.data, request.user))


class AttendanceGrid(APIView):
    def get(self, request):
        need(request, "hr.view")
        return Response(att.month_grid(current_company(), _month(request), department=request.query_params.get("department") or None))


class AttendanceDetail(APIView):
    @transaction.atomic
    def delete(self, request, pk):
        need(request, "hr.manage")
        a = get_or_404(Attendance, pk, "attendance record", employee__company=current_company())
        if a.source == Attendance.Source.LEAVE:
            raise ValidationError("This day comes from an approved leave. Cancel the leave instead.")
        common.log(a.employee.company, request.user, "attendance", a.pk, Action.DELETED, previous={"employee": str(a.employee_id), "date": a.date.isoformat(), "status": a.status})
        a.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


def _person_month(employee, month):
    grid = att.month_grid(employee.company, month, employee_ids=[employee.pk])
    rows = Attendance.objects.filter(employee=employee, date__range=(month, common.month_end(month)))
    return {"employee": brief(employee), "month": month, "grid": grid["rows"][0] if grid["rows"] else None, "days": grid["days"],
            "codes": grid["codes"], "records": [att_dict(a) for a in rows]}


class EmployeeAttendance(APIView):
    def get(self, request, pk):
        e = get_employee(pk)
        if not can_see_person(request, e):
            raise PermissionDenied("Your role does not allow this.")
        return Response(_person_month(e, _month(request)))


def _mine(request):
    e = me(request)
    if e is None:
        raise ValidationError("Your login is not linked to an employee record. Ask HR to link it.")
    return e


class MyAttendance(APIView):
    def get(self, request):
        e = me(request)
        if e is None:
            return Response({"linked": False})
        today = Attendance.objects.filter(employee=e, date=datetime.date.today()).first()
        return Response({"linked": True, **_person_month(e, _month(request)), "today": att_dict(today) if today else None})


class MyCheckIn(APIView):
    @transaction.atomic
    def post(self, request):
        a = att.self_check(_mine(request), "in", request.user, status=request.data.get("status") or "present")
        return Response(att_dict(a), status=http.HTTP_201_CREATED)


class MyCheckOut(APIView):
    @transaction.atomic
    def post(self, request):
        return Response(att_dict(att.self_check(_mine(request), "out", request.user)))


# --------------------------------------------------------------------------- leave types

def type_dict(t):
    return {"id": str(t.id), "code": t.code, "name": t.name, "annual_quota": t.annual_quota, "carry_forward": t.carry_forward,
            "max_carry_forward": t.max_carry_forward, "is_paid": t.is_paid, "is_active": t.is_active, "description": t.description}


def _type_snap(t):
    return {"code": t.code, "name": t.name, "annual_quota": str(t.annual_quota), "carry_forward": t.carry_forward,
            "max_carry_forward": str(t.max_carry_forward), "is_paid": t.is_paid, "is_active": t.is_active}


def _type_apply(t, data):
    adding = t._state.adding
    if adding or "code" in data:
        t.code = common.text(data, "code", "Code", 10, required=True).upper()
    if adding or "name" in data:
        t.name = common.text(data, "name", "Name", 80, required=True)
    if adding or "annual_quota" in data:
        t.annual_quota = common.decimal(data, "annual_quota", "Annual quota", places=1, high=366, default=0)
    if "carry_forward" in data:
        t.carry_forward = common.boolean(data, "carry_forward")
    if adding or "max_carry_forward" in data:
        t.max_carry_forward = common.decimal(data, "max_carry_forward", "Carry-forward limit", places=1, high=366, default=0)
    if t.carry_forward and t.max_carry_forward <= 0:
        raise ValidationError({"max_carry_forward": "Say how many days can be carried forward."})
    if not t.carry_forward:
        t.max_carry_forward = 0
    if "is_paid" in data:
        t.is_paid = common.boolean(data, "is_paid")
    if "is_active" in data:
        t.is_active = common.boolean(data, "is_active")
    if "description" in data:
        t.description = common.text(data, "description", "Description", 200)
    clash = LeaveType.objects.exclude(pk=t.pk).filter(company=t.company).filter(Q(code__iexact=t.code) | Q(name__iexact=t.name))
    if clash.exists():
        raise ValidationError({"code": "A leave type with this code or name already exists."})


class LeaveTypeCollection(APIView):
    def get(self, request):
        qs = LeaveType.objects.filter(company=current_company())
        if request.query_params.get("all") != "true" or not request.user.has_code("hr.view"):
            qs = qs.filter(is_active=True)
        return Response([type_dict(t) for t in qs])

    @transaction.atomic
    def post(self, request):
        need(request, "hr.manage")
        t = LeaveType(company=current_company())
        _type_apply(t, request.data)
        t.save()
        common.log(t.company, request.user, "leave_type", t.pk, Action.CREATED, new=_type_snap(t))
        return Response(type_dict(t), status=http.HTTP_201_CREATED)


class LeaveTypeDetail(APIView):
    @transaction.atomic
    def patch(self, request, pk):
        need(request, "hr.manage")
        t = get_or_404(LeaveType, pk, "leave type", company=current_company())
        before = _type_snap(t)
        _type_apply(t, request.data)
        t.save()
        common.log(t.company, request.user, "leave_type", t.pk, Action.UPDATED, previous=before, new=_type_snap(t))
        return Response(type_dict(t))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "hr.manage")
        t = get_or_404(LeaveType, pk, "leave type", company=current_company())
        if t.requests.exists() or t.adjustments.exists():
            raise ValidationError("This leave type has been used. Switch it off instead.")
        common.log(t.company, request.user, "leave_type", t.pk, Action.DELETED, previous=_type_snap(t))
        t.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- balances, requests

class LeaveBalances(APIView):
    def get(self, request):
        emp = request.query_params.get("employee")
        e = get_employee(emp) if emp else me(request)
        if e is None:
            return Response({"linked": False, "rows": []})
        if not can_see_person(request, e):
            raise PermissionDenied("Your role does not allow this.")
        on = common.date_value(request.query_params, "date", "Date") or datetime.date.today()
        return Response({"linked": True, "employee": brief(e), "rows": leave.balances(e, on)})


def request_dict(r, request=None):
    out = {"id": str(r.id), "number": r.number, "employee": brief(r.employee), "department": r.employee.department.name if r.employee.department else "",
           "leave_type": {"id": str(r.leave_type_id), "name": r.leave_type.name, "code": r.leave_type.code, "is_paid": r.leave_type.is_paid},
           "start_date": r.start_date, "end_date": r.end_date, "half_day": r.half_day, "days": r.days, "reason": r.reason,
           "status": r.status, "status_label": r.get_status_display(), "decision_note": r.decision_note, "decided_at": r.decided_at,
           "decided_by": (r.decided_by.get_full_name() or r.decided_by.username) if r.decided_by_id else "", "created_at": r.created_at}
    if request is not None:
        mine = is_self(request, r.employee)
        out["can_approve"] = r.status == "pending_approval" and leave.can_decide(request.user, r)
        out["can_cancel"] = r.status in ("draft", "pending_approval", "approved") and (mine or request.user.has_code("hr.manage"))
        out["can_edit"] = r.status == "draft" and (mine or request.user.has_code("hr.manage"))
        out["is_mine"] = mine
    return out


def _requests_qs(request):
    qs = LeaveRequest.objects.filter(company=current_company()).select_related("employee", "employee__department", "leave_type", "decided_by")
    if request.user.has_code("hr.view"):
        return qs
    mine = me(request)
    if mine is None:
        return qs.none()
    return qs.filter(Q(employee=mine) | Q(employee__manager=mine))


class LeaveRequestCollection(APIView):
    def get(self, request):
        qs = _requests_qs(request)
        p = request.query_params
        scope = p.get("scope")
        mine = me(request)
        if scope == "mine":
            qs = qs.filter(employee=mine) if mine else qs.none()
        elif scope == "approvals":  # waiting for me: my team's requests (or everyone's for HR)
            qs = qs.filter(status="pending_approval")
            if not request.user.has_code("hr.manage"):
                qs = qs.filter(employee__manager=mine) if mine else qs.none()
            if mine:
                qs = qs.exclude(employee=mine)
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("employee"):
            try:
                qs = qs.filter(employee_id=p["employee"])
            except (ValueError, ValidationError):
                qs = qs.none()
        if p.get("month"):
            m = common.parse_month(p["month"])
            qs = qs.filter(start_date__lte=common.month_end(m), end_date__gte=m)
        if p.get("q"):
            qs = qs.filter(Q(employee__first_name__icontains=p["q"]) | Q(employee__last_name__icontains=p["q"]) | Q(number__icontains=p["q"]))
        return Response([request_dict(r, request) for r in qs[:500]])

    @transaction.atomic
    def post(self, request):
        target = request.data.get("employee")
        mine = me(request)
        if target and (mine is None or str(mine.pk) != str(target)):
            need(request, "hr.manage")
            e = get_employee(target)
        else:
            e = mine
            if e is None:
                raise ValidationError("Your login is not linked to an employee record. Ask HR to link it.")
        r = leave.create_request(e, request.data, request.user, submit=common.boolean(request.data, "submit", False))
        return Response(request_dict(r, request), status=http.HTTP_201_CREATED)


def _req(request, pk):
    r = get_or_404(LeaveRequest, pk, "leave request", company=current_company())
    if not (request.user.has_code("hr.view") or is_self(request, r.employee) or is_team_lead(request, r.employee)):
        raise PermissionDenied("Your role does not allow this.")
    return r


def _own_or_hr(request, r):
    if not (is_self(request, r.employee) or request.user.has_code("hr.manage")):
        raise PermissionDenied("Only the employee or HR can do this.")


class LeaveRequestDetail(APIView):
    def get(self, request, pk):
        return Response(request_dict(_req(request, pk), request))

    @transaction.atomic
    def patch(self, request, pk):
        r = _req(request, pk)
        _own_or_hr(request, r)
        return Response(request_dict(leave.update_request(r, request.data, request.user), request))

    @transaction.atomic
    def delete(self, request, pk):
        r = _req(request, pk)
        _own_or_hr(request, r)
        if r.status != "draft":
            raise ValidationError("Only a draft can be deleted. Cancel a submitted request instead.")
        common.log(r.company, request.user, "leave_request", r.pk, Action.DELETED, previous={"start_date": r.start_date.isoformat(), "days": str(r.days)})
        r.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class LeaveRequestAction(APIView):
    action = ""

    @transaction.atomic
    def post(self, request, pk):
        r = _req(request, pk)
        note = request.data.get("note") or request.data.get("reason") or ""
        if self.action == "submit":
            _own_or_hr(request, r)
            r = leave.submit_request(r, request.user)
        elif self.action == "approve":
            r = leave.approve(r, request.user, note)
        elif self.action == "reject":
            r = leave.reject(r, request.user, note)
        else:
            r = leave.cancel(r, request.user, note)
        r.refresh_from_db()
        return Response(request_dict(r, request))


class LeaveCalendar(APIView):
    """Who is away in a month: for the team calendar chart. HR sees everyone; others see themselves and their team."""

    def get(self, request):
        m = _month(request)
        last = common.month_end(m)
        company = current_company()
        qs = _requests_qs(request).filter(status__in=("approved", "pending_approval"), start_date__lte=last, end_date__gte=m)
        settings = common.get_settings(company)
        days = [d.isoformat() for d in common.daterange(m, last)]
        per_day = {d: 0 for d in days}
        items = []
        for r in qs:
            for d in leave.counted_dates(company, max(r.start_date, m), min(r.end_date, last), settings):
                if r.status == "approved":
                    per_day[d.isoformat()] += 1
            items.append(request_dict(r))
        return Response({"month": m, "days": days, "per_day": [{"date": d, "away": per_day[d]} for d in days], "requests": items,
                         "holidays": [holiday_dict(h) for h in Holiday.objects.filter(company=company, date__range=(m, last))],
                         "weekly_off": settings.weekly_off_days or []})


class LeaveAdjustmentCollection(APIView):
    def get(self, request):
        need(request, "hr.view")
        qs = LeaveAdjustment.objects.filter(employee__company=current_company()).select_related("employee", "leave_type")
        if request.query_params.get("employee"):
            qs = qs.filter(employee_id=request.query_params["employee"])
        return Response([{"id": str(a.id), "employee": brief(a.employee), "leave_type": a.leave_type.name, "year_start": a.year_start,
                          "days": a.days, "reason": a.reason, "created_at": a.created_at} for a in qs[:200]])

    @transaction.atomic
    def post(self, request):
        need(request, "hr.manage")
        company = current_company()
        d = request.data
        e = get_employee(d.get("employee"), company) if d.get("employee") else None
        if e is None:
            raise ValidationError({"employee": "Choose an employee."})
        lt = get_or_404(LeaveType, d.get("leave_type"), "leave type", company=company) if d.get("leave_type") else None
        if lt is None:
            raise ValidationError({"leave_type": "Choose a leave type."})
        days = common.decimal(d, "days", "Days", places=1, low=-366, high=366, required=True)
        if days == 0:
            raise ValidationError({"days": "Enter a number of days other than zero."})
        reason = common.text(d, "reason", "Reason", 300, required=True)
        on = common.date_value(d, "date", "Date") or datetime.date.today()
        start, _ = leave.leave_year(on, common.get_settings(company))
        a = LeaveAdjustment.objects.create(employee=e, leave_type=lt, year_start=start, days=days, reason=reason, created_by=request.user)
        common.log(company, request.user, "leave_adjustment", a.pk, Action.CREATED,
                   new={"employee": str(e.pk), "leave_type": lt.code, "days": str(days), "reason": reason})
        return Response({"id": str(a.id), "days": a.days, "year_start": a.year_start}, status=http.HTTP_201_CREATED)
