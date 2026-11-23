"""Dashboard, performance reviews and project assignments."""
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

from .models import Employee, Goal, ProjectAssignment, Review, ReviewCycle
from .services import common, performance, reports
from .views_base import brief, get_employee, get_or_404, is_self, is_team_lead, me, need

Action = CompanyProfileAuditEvent.Action


class DashboardView(APIView):
    def get(self, request):
        need(request, "hr.view")
        return Response(reports.dashboard(current_company(), request.user))


# --------------------------------------------------------------------------- performance

def cycle_dict(c):
    return {"id": str(c.id), "name": c.name, "start_date": c.start_date, "end_date": c.end_date, "status": c.status,
            "status_label": c.get_status_display(), "reviews": c.reviews.count(), "completed": c.reviews.filter(status="completed").count()}


class CycleCollection(APIView):
    def get(self, request):
        need(request, "hr.view")
        return Response([cycle_dict(c) for c in ReviewCycle.objects.filter(company=current_company())])

    @transaction.atomic
    def post(self, request):
        need(request, "hr.manage")
        company = current_company()
        c = ReviewCycle.objects.create(company=company, **performance.cycle_fields(company, request.data))
        common.log(company, request.user, "review_cycle", c.pk, Action.CREATED, new={"name": c.name, "start_date": c.start_date.isoformat(), "end_date": c.end_date.isoformat()})
        return Response(cycle_dict(c), status=http.HTTP_201_CREATED)


class CycleDetail(APIView):
    def get(self, request, pk):
        need(request, "hr.view")
        return Response(cycle_dict(get_or_404(ReviewCycle, pk, "review cycle", company=current_company())))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "hr.manage")
        c = get_or_404(ReviewCycle, pk, "review cycle", company=current_company())
        if c.status == "closed":
            raise ValidationError("A closed cycle cannot be changed.")
        merged = {"name": c.name, "start_date": c.start_date.isoformat(), "end_date": c.end_date.isoformat(), **request.data}
        before = {"name": c.name, "start_date": c.start_date.isoformat(), "end_date": c.end_date.isoformat()}
        for k, v in performance.cycle_fields(c.company, merged, c).items():
            setattr(c, k, v)
        c.save()
        common.log(c.company, request.user, "review_cycle", c.pk, Action.UPDATED, previous=before,
                   new={"name": c.name, "start_date": c.start_date.isoformat(), "end_date": c.end_date.isoformat()})
        return Response(cycle_dict(c))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "hr.manage")
        c = get_or_404(ReviewCycle, pk, "review cycle", company=current_company())
        if c.status != "draft":
            raise ValidationError("Only a draft cycle can be deleted.")
        common.log(c.company, request.user, "review_cycle", c.pk, Action.DELETED, previous={"name": c.name})
        c.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class CycleAction(APIView):
    action = ""

    @transaction.atomic
    def post(self, request, pk):
        need(request, "hr.manage")
        c = get_or_404(ReviewCycle, pk, "review cycle", company=current_company())
        c = performance.launch(c, request.user) if self.action == "launch" else performance.close(c, request.user)
        return Response(cycle_dict(c))


class CycleSummary(APIView):
    def get(self, request, pk):
        need(request, "hr.view")
        c = get_or_404(ReviewCycle, pk, "review cycle", company=current_company())
        return Response(performance.summary(c.company, c))


def review_dict(r, request):
    mine = is_self(request, r.employee)
    out = {"id": str(r.id), "cycle": {"id": str(r.cycle_id), "name": r.cycle.name, "status": r.cycle.status},
           "employee": brief(r.employee), "department": r.employee.department.name if r.employee.department else "",
           "reviewer": brief(r.reviewer) if r.reviewer_id else None, "self_rating": r.self_rating, "manager_rating": r.manager_rating,
           "self_comments": r.self_comments, "manager_comments": r.manager_comments, "status": r.status, "status_label": r.get_status_display(),
           "can_self_review": mine and r.cycle.status == "open" and r.status != "completed",
           "can_rate": r.cycle.status == "open" and performance.can_rate(request.user, r)}
    return out


def _review_visible(request):
    qs = Review.objects.filter(cycle__company=current_company()).select_related("cycle", "employee", "employee__department", "employee__manager", "reviewer")
    if request.user.has_code("hr.view"):
        return qs
    mine = me(request)
    return qs.filter(Q(employee=mine) | Q(employee__manager=mine)) if mine else qs.none()


class ReviewCollection(APIView):
    def get(self, request):
        qs = _review_visible(request)
        p = request.query_params
        if p.get("cycle"):
            qs = qs.filter(cycle_id=p["cycle"])
        if p.get("employee"):
            qs = qs.filter(employee_id=p["employee"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("scope") == "mine":
            qs = qs.filter(employee=me(request)) if me(request) else qs.none()
        return Response([review_dict(r, request) for r in qs[:500]])


class ReviewAction(APIView):
    kind = ""

    @transaction.atomic
    def post(self, request, pk):
        r = get_or_404(Review, pk, "review", cycle__company=current_company())
        if not (request.user.has_code("hr.view") or is_self(request, r.employee) or is_team_lead(request, r.employee)):
            raise PermissionDenied("Your role does not allow this.")
        fn = performance.self_review if self.kind == "self" else performance.manager_review
        return Response(review_dict(fn(r, request.data, request.user), request))


def goal_dict(g, request):
    mine = is_self(request, g.employee)
    lead = is_team_lead(request, g.employee) or request.user.has_code("hr.manage")
    return {"id": str(g.id), "employee": brief(g.employee), "cycle": {"id": str(g.cycle_id), "name": g.cycle.name}, "title": g.title,
            "description": g.description, "weight": g.weight, "progress": g.progress, "status": g.status, "status_label": g.get_status_display(),
            "manager_comment": g.manager_comment, "can_update_progress": (mine or lead) and g.cycle.status != "closed", "can_manage": lead}


def _goal_scope(request):
    qs = Goal.objects.filter(cycle__company=current_company()).select_related("employee", "cycle")
    if request.user.has_code("hr.view"):
        return qs
    mine = me(request)
    return qs.filter(Q(employee=mine) | Q(employee__manager=mine)) if mine else qs.none()


class GoalCollection(APIView):
    def get(self, request):
        qs = _goal_scope(request)
        p = request.query_params
        if p.get("cycle"):
            qs = qs.filter(cycle_id=p["cycle"])
        if p.get("employee"):
            qs = qs.filter(employee_id=p["employee"])
        return Response([goal_dict(g, request) for g in qs[:500]])

    @transaction.atomic
    def post(self, request):
        company = current_company()
        e = get_employee(request.data.get("employee"), company) if request.data.get("employee") else me(request)
        if e is None:
            raise ValidationError({"employee": "Choose an employee."})
        if not (request.user.has_code("hr.manage") or is_team_lead(request, e) or is_self(request, e)):
            raise PermissionDenied("Your role does not allow this.")
        cycle = get_or_404(ReviewCycle, request.data.get("cycle"), "review cycle", company=company) if request.data.get("cycle") else None
        if cycle is None:
            raise ValidationError({"cycle": "Choose a review cycle."})
        if cycle.status == "closed":
            raise ValidationError({"cycle": "This review cycle is closed."})
        fields = performance.goal_fields(request.data)
        if is_self(request, e) and not (request.user.has_code("hr.manage") or is_team_lead(request, e)):
            fields.pop("manager_comment", None)
        total = sum(Goal.objects.filter(employee=e, cycle=cycle).values_list("weight", flat=True)) + fields.get("weight", 0)
        if total > 100:
            raise ValidationError({"weight": f"The goal weights would add up to {total}%; at most 100% is allowed."})
        g = Goal.objects.create(employee=e, cycle=cycle, **fields)
        common.log(company, request.user, "goal", g.pk, Action.CREATED, new={"employee": str(e.pk), "title": g.title, "weight": g.weight})
        return Response(goal_dict(g, request), status=http.HTTP_201_CREATED)


class GoalDetail(APIView):
    def _get(self, request, pk):
        g = get_or_404(Goal, pk, "goal", cycle__company=current_company())
        if not _goal_scope(request).filter(pk=g.pk).exists():
            raise PermissionDenied("Your role does not allow this.")
        return g

    @transaction.atomic
    def patch(self, request, pk):
        g = self._get(request, pk)
        lead = request.user.has_code("hr.manage") or is_team_lead(request, g.employee)
        if not (lead or is_self(request, g.employee)):
            raise PermissionDenied("Your role does not allow this.")
        if g.cycle.status == "closed":
            raise ValidationError("This review cycle is closed.")
        fields = performance.goal_fields(request.data, partial=True)
        if not lead:  # the employee reports progress; the manager sets the rest
            fields = {k: v for k, v in fields.items() if k in ("progress", "status")}
        before = {k: getattr(g, k) for k in fields}
        for k, v in fields.items():
            setattr(g, k, v)
        if "weight" in fields:
            total = sum(Goal.objects.filter(employee=g.employee, cycle=g.cycle).exclude(pk=g.pk).values_list("weight", flat=True)) + g.weight
            if total > 100:
                raise ValidationError({"weight": f"The goal weights would add up to {total}%; at most 100% is allowed."})
        g.save()
        common.log(g.cycle.company, request.user, "goal", g.pk, Action.UPDATED, previous=before, new={k: getattr(g, k) for k in fields})
        return Response(goal_dict(g, request))

    @transaction.atomic
    def delete(self, request, pk):
        g = self._get(request, pk)
        if not (request.user.has_code("hr.manage") or is_team_lead(request, g.employee)):
            raise PermissionDenied("Your role does not allow this.")
        common.log(g.cycle.company, request.user, "goal", g.pk, Action.DELETED, previous={"title": g.title})
        g.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- project assignments

def assignment_dict(a, names=None):
    names = names if names is not None else performance.project_names([a.project_id])
    p = names.get(str(a.project_id)) or {}
    today = datetime.date.today()
    return {"id": str(a.id), "employee": brief(a.employee), "department": a.employee.department.name if a.employee.department else "",
            "project_id": str(a.project_id), "project_name": p.get("name") or a.project_label, "project_code": p.get("code", ""),
            "role": a.role, "allocation_percent": a.allocation_percent, "start_date": a.start_date, "end_date": a.end_date, "notes": a.notes,
            "is_current": a.start_date <= today and (a.end_date is None or a.end_date >= today)}


class AssignmentCollection(APIView):
    def get(self, request):
        need(request, "hr.view", "projects.view")
        qs = ProjectAssignment.objects.filter(employee__company=current_company()).select_related("employee", "employee__department")
        p = request.query_params
        if p.get("project"):
            try:
                qs = qs.filter(project_id=p["project"])
            except (ValueError, ValidationError):
                qs = qs.none()
        if p.get("employee"):
            try:
                qs = qs.filter(employee_id=p["employee"])
            except (ValueError, ValidationError):
                qs = qs.none()
        if p.get("current") == "true":
            today = datetime.date.today()
            qs = qs.filter(start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        rows = list(qs[:500])
        names = performance.project_names({a.project_id for a in rows})
        return Response([assignment_dict(a, names) for a in rows])

    @transaction.atomic
    def post(self, request):
        need(request, "hr.manage")
        e = get_employee(request.data.get("employee")) if request.data.get("employee") else None
        if e is None:
            raise ValidationError({"employee": "Choose an employee."})
        a = ProjectAssignment(employee=e)
        performance.apply_assignment(a, request.data, True)
        a.save()
        common.log(e.company, request.user, "assignment", a.pk, Action.CREATED,
                   new={"employee": str(e.pk), "project": str(a.project_id), "allocation": a.allocation_percent})
        return Response(assignment_dict(a), status=http.HTTP_201_CREATED)


class AssignmentDetail(APIView):
    def _get(self, pk):
        return get_or_404(ProjectAssignment, pk, "assignment", employee__company=current_company())

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "hr.manage")
        a = self._get(pk)
        before = {"role": a.role, "allocation": a.allocation_percent, "start_date": a.start_date.isoformat(), "end_date": a.end_date.isoformat() if a.end_date else None}
        performance.apply_assignment(a, request.data, False)
        a.save()
        common.log(a.employee.company, request.user, "assignment", a.pk, Action.UPDATED, previous=before,
                   new={"role": a.role, "allocation": a.allocation_percent, "start_date": a.start_date.isoformat(), "end_date": a.end_date.isoformat() if a.end_date else None})
        return Response(assignment_dict(a))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "hr.manage")
        a = self._get(pk)
        common.log(a.employee.company, request.user, "assignment", a.pk, Action.DELETED, previous={"employee": str(a.employee_id), "project": str(a.project_id)})
        a.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class UtilisationView(APIView):
    def get(self, request):
        need(request, "hr.view", "projects.view")
        return Response(performance.utilisation(current_company()))
