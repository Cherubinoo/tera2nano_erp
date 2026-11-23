"""Employees, departments, designations, salary structures, advances, org chart and the signed-in user's own profile."""
import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from rest_framework import status as http
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company
from apps.company.models import CompanyProfileAuditEvent

from .models import Department, Designation, Employee, EmployeeAdvance, SalaryRevision, SalaryStructure
from .services import common, payroll, people, salary
from .views_base import employee_dict, get_employee, get_or_404, is_self, me, need, sees_salary

Action = CompanyProfileAuditEvent.Action
User = get_user_model()
MAX_ROWS = 1000


class LookupView(APIView):
    """Active employees, no pay data: for pickers in other modules. Open to every signed-in user."""

    def get(self, request):
        return Response(people.lookup(current_company()))


# --------------------------------------------------------------------------- departments / designations

class _Named(APIView):
    model, entity, label, rows = None, "", "", None

    def get(self, request):
        need(request, "hr.view")
        return Response(self.rows(current_company()))

    @transaction.atomic
    def post(self, request):
        need(request, "hr.manage")
        company = current_company()
        obj = self.model(company=company, name=people.named(self.model, company, request.data, label=self.label))
        self.extra(obj, request.data)
        obj.save()
        common.log(company, request.user, self.entity, obj.pk, Action.CREATED, new=self.snap(obj))
        return Response(self.one(obj), status=http.HTTP_201_CREATED)

    def extra(self, obj, data):
        pass

    def snap(self, obj):
        return {"name": obj.name, "is_active": obj.is_active}

    def one(self, obj):
        return next(r for r in self.rows(obj.company) if r["id"] == str(obj.pk))


class _NamedDetail(_Named):
    def _get(self, pk):
        return get_or_404(self.model, pk, self.label.lower(), company=current_company())

    def get(self, request, pk):
        need(request, "hr.view")
        return Response(self.one(self._get(pk)))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "hr.manage")
        obj = self._get(pk)
        before = self.snap(obj)
        data = request.data
        if "name" in data:
            obj.name = people.named(self.model, obj.company, data, obj, label=self.label)
        if "is_active" in data:
            obj.is_active = common.boolean(data, "is_active")
        self.extra(obj, data)
        obj.save()
        after = self.snap(obj)
        if before != after:
            common.log(obj.company, request.user, self.entity, obj.pk, Action.UPDATED, previous=before, new=after)
        return Response(self.one(obj))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "hr.manage")
        obj = self._get(pk)
        if obj.employees.exists():
            raise ValidationError(f"People are still assigned to this {self.label.lower()}. Switch it off instead.")
        common.log(obj.company, request.user, self.entity, obj.pk, Action.DELETED, previous=self.snap(obj))
        obj.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class DepartmentCollection(_Named):
    model, entity, label, rows = Department, "department", "Department", staticmethod(people.department_rows)

    def extra(self, obj, data):
        if "description" in data:
            obj.description = common.text(data, "description", "Description", 300)
        if "head" in data:
            head = data.get("head")
            obj.head = get_or_404(Employee, head, "employee", company=obj.company) if head else None


class DepartmentDetail(DepartmentCollection, _NamedDetail):
    pass


class DesignationCollection(_Named):
    model, entity, label, rows = Designation, "designation", "Designation", staticmethod(people.designation_rows)


class DesignationDetail(DesignationCollection, _NamedDetail):
    pass


# --------------------------------------------------------------------------- employees

class EmployeeCollection(APIView):
    def get(self, request):
        need(request, "hr.view")
        company = current_company()
        p = request.query_params
        qs = Employee.objects.filter(company=company).select_related("department", "designation", "manager")
        st = p.get("status", "current")
        if st == "current":
            qs = qs.exclude(status="exited")
        elif st in Employee.Status.values:
            qs = qs.filter(status=st)
        if p.get("q"):
            q = p["q"].strip()
            qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(code__icontains=q) | Q(work_email__icontains=q)
                           | Q(phone__icontains=q))
        for key, field in (("department", "department_id"), ("designation", "designation_id"), ("manager", "manager_id"),
                           ("type", "employment_type")):
            if p.get(key):
                try:
                    qs = qs.filter(**{field: p[key]})
                except (ValueError, ValidationError):
                    qs = qs.none()
        return Response([employee_dict(e, request) for e in qs[:MAX_ROWS]])

    @transaction.atomic
    def post(self, request):
        need(request, "hr.manage")
        e = people.create(current_company(), request.data, request.user)
        return Response(employee_dict(e, request, detail=True), status=http.HTTP_201_CREATED)


class EmployeeDetail(APIView):
    def get(self, request, pk):
        e = get_employee(pk)
        if not (request.user.has_code("hr.view") or is_self(request, e)):
            need(request)  # raises
        reveal = request.query_params.get("reveal") == "true"
        if reveal:
            need(request, "hr.manage")
            common.log(e.company, request.user, "employee", e.pk, Action.REVEALED, new={"revealed": "PAN, Aadhaar and bank account"})
        out = employee_dict(e, request, detail=True, reveal=reveal)
        if sees_salary(request, e):
            s = payroll.structure_for(e, datetime.date.today()) or SalaryStructure.objects.filter(employee=e).order_by("effective_from").first()
            out["current_salary"] = salary_dict(s) if s else None
        return Response(out)

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "hr.manage")
        e = get_employee(pk)
        people.update(e, request.data, request.user)
        return Response(employee_dict(e, request, detail=True))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "hr.manage")
        e = get_employee(pk)
        blockers = people.can_delete(e)
        if blockers:
            raise ValidationError(f"This employee has {', '.join(blockers)}, so the record cannot be deleted. Mark them as exited instead.")
        common.log(e.company, request.user, "employee", e.pk, Action.DELETED, previous=people.snapshot(e))
        e.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class AvailableUsersView(APIView):
    """Sign-in users not yet linked to an employee, for the 'link to a user' choice."""

    def get(self, request):
        need(request, "hr.manage")
        linked = Employee.objects.exclude(user=None).values_list("user_id", flat=True)
        return Response([{"id": str(u.id), "username": u.username, "full_name": u.get_full_name() or u.username, "role": u.role}
                         for u in User.objects.filter(is_active=True).exclude(pk__in=linked).order_by("username")])


class OrgChartView(APIView):
    def get(self, request):
        need(request, "hr.view")
        return Response(people.org_chart(current_company()))


# --------------------------------------------------------------------------- salary

SALARY_FIELDS, FLAGS = salary.SALARY_FIELDS, salary.FLAGS


def salary_dict(s):
    return {"id": str(s.id), "effective_from": s.effective_from, **{f: getattr(s, f) for f in SALARY_FIELDS}, "gross": s.gross,
            **{f: getattr(s, f) for f in FLAGS}, "notes": s.notes, "created_at": s.created_at}


def _salary_snap(s):
    return salary.snap(s)


def revision_dict(r):
    return {"id": str(r.id), "employee": str(r.employee_id), "status": r.status, "status_label": r.get_status_display(),
            "effective_from": r.effective_from, **{f: r.structure.get(f) for f in SALARY_FIELDS}, **{f: r.structure.get(f) for f in FLAGS},
            "notes": r.structure.get("notes", ""), "previous_gross": r.previous_gross, "new_gross": r.new_gross,
            "annual_increase": r.annual_increase, "reason": r.reason, "decision_note": r.decision_note,
            "created_at": r.created_at, "decided_at": r.decided_at,
            "requested_by": (r.requested_by.get_full_name() or r.requested_by.get_username()) if r.requested_by_id else ""}


class SalaryCollection(APIView):
    def get(self, request, pk):
        e = get_employee(pk)
        if not sees_salary(request, e):
            need(request, "hr.payroll")
        today = datetime.date.today()
        cur = payroll.structure_for(e, today)
        revisions = e.salary_revisions.select_related("requested_by")[:20]
        return Response({"current": str(cur.id) if cur else None, "rows": [salary_dict(s) for s in e.salary_structures.all()],
                         "revisions": [revision_dict(r) for r in revisions], "approval_managed": salary.managed()})

    @transaction.atomic
    def post(self, request, pk):
        need(request, "hr.payroll")
        e = get_employee(pk)
        d = request.data
        s = salary.parse(e, d, request.user)
        if salary.needs_approval(e, s):
            rev = salary.request_change(e, s, request.user, reason=d.get("reason") or "")
            return Response({**revision_dict(rev), "pending_approval": True}, status=http.HTTP_202_ACCEPTED)
        salary.save_structure(s, request.user)
        return Response(salary_dict(s), status=http.HTTP_201_CREATED)


class SalaryRevisionAction(APIView):
    action = ""

    @transaction.atomic
    def post(self, request, pk, rid):
        need(request, "hr.payroll")
        e = get_employee(pk)
        rev = get_or_404(SalaryRevision, rid, "salary revision", employee=e)
        if self.action == "withdraw":
            salary.take_back(rev, request.user)
        else:
            if salary.managed():
                raise ValidationError("Salary revisions waiting for approval are decided in the Approvals module.")
            if self.action == "approve":
                salary.approve(rev, request.user, request.data.get("note") or "")
            else:
                salary.reject(rev, request.user, request.data.get("change_reason"))
        return Response(revision_dict(get_or_404(SalaryRevision, rid, "salary revision", employee=e)))


class SalaryDetail(APIView):
    @transaction.atomic
    def delete(self, request, pk, sid):
        need(request, "hr.payroll")
        e = get_employee(pk)
        s = get_or_404(SalaryStructure, sid, "salary structure", employee=e)
        if e.salary_structures.count() == 1 and e.payslips.exists():
            raise ValidationError("This is the only salary structure and payslips have been made from it.")
        common.log(e.company, request.user, "salary_structure", s.pk, Action.DELETED, previous=_salary_snap(s))
        s.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- advances

def advance_dict(a):
    return {"id": str(a.id), "date": a.date, "amount": a.amount, "installment": a.installment, "recovered": a.recovered,
            "outstanding": a.outstanding, "status": a.status, "notes": a.notes}


class AdvanceCollection(APIView):
    def get(self, request, pk):
        e = get_employee(pk)
        if not sees_salary(request, e):
            need(request, "hr.payroll")
        return Response([advance_dict(a) for a in e.advances.all()])

    @transaction.atomic
    def post(self, request, pk):
        need(request, "hr.payroll")
        e = get_employee(pk)
        d = request.data
        a = EmployeeAdvance(employee=e)
        a.date = common.date_value(d, "date", "Date", required=True)
        a.amount = common.decimal(d, "amount", "Amount", required=True, high=100000000)
        a.installment = common.decimal(d, "installment", "Monthly recovery", required=True, high=100000000)
        if a.amount <= 0:
            raise ValidationError({"amount": "Amount must be more than zero."})
        if a.installment <= 0 or a.installment > a.amount:
            raise ValidationError({"installment": "The monthly recovery must be more than zero and no more than the amount."})
        a.notes = common.text(d, "notes", "Notes", 300)
        a.save()
        common.log(e.company, request.user, "advance", a.pk, Action.CREATED, new={"employee": str(e.pk), "amount": str(a.amount)})
        return Response(advance_dict(a), status=http.HTTP_201_CREATED)


class AdvanceDetail(APIView):
    @transaction.atomic
    def delete(self, request, pk, aid):
        need(request, "hr.payroll")
        e = get_employee(pk)
        a = get_or_404(EmployeeAdvance, aid, "advance", employee=e)
        if a.recovered > 0:
            raise ValidationError("Part of this advance has been recovered through payroll, so it cannot be deleted.")
        common.log(e.company, request.user, "advance", a.pk, Action.DELETED, previous={"amount": str(a.amount)})
        a.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- my profile

class MeView(APIView):
    """The signed-in user's own employee record. `linked: false` when no employee is tied to their login."""

    def get(self, request):
        e = me(request)
        if e is None:
            return Response({"linked": False})
        out = employee_dict(e, request, detail=True)
        out["linked"] = True
        out["manager_user"] = bool(e.reports.exists())
        return Response(out)

    @transaction.atomic
    def patch(self, request):
        e = me(request)
        if e is None:
            raise ValidationError("Your login is not linked to an employee record.")
        stray = [k for k in request.data if k not in people.SELF_FIELDS]
        if stray:
            raise ValidationError({k: "You cannot change this yourself. Ask HR." for k in stray})
        people.update(e, request.data, request.user, fields=people.SELF_FIELDS)
        return Response(employee_dict(e, request, detail=True))
