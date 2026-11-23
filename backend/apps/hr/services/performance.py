"""Performance reviews (cycles, goals, ratings) and project assignments."""
import datetime
import uuid

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from rest_framework.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum

from ..models import Employee, Goal, ProjectAssignment, Review, ReviewCycle
from . import common
from .common import Action, boolean, date_value, text


# --------------------------------------------------------------------------- cycles

def cycle_fields(company, data, instance=None):
    name = text(data, "name", "Name", 120, required=True)
    clash = ReviewCycle.objects.filter(company=company, name__iexact=name)
    if instance is not None:
        clash = clash.exclude(pk=instance.pk)
    if clash.exists():
        raise ValidationError({"name": "A review cycle with this name already exists."})
    start, end = date_value(data, "start_date", "Start date", required=True), date_value(data, "end_date", "End date", required=True)
    if end < start:
        raise ValidationError({"end_date": "The end date cannot be before the start date."})
    return {"name": name, "start_date": start, "end_date": end}


@transaction.atomic
def launch(cycle, actor):
    cycle = ReviewCycle.objects.select_for_update().get(pk=cycle.pk)
    if cycle.status != ReviewCycle.Status.DRAFT:
        raise ValidationError("Only a draft cycle can be launched.")
    people = Employee.objects.filter(company=cycle.company, date_of_joining__lte=cycle.end_date).exclude(status="exited")
    Review.objects.bulk_create([Review(cycle=cycle, employee=e, reviewer=e.manager) for e in people])
    cycle.status = ReviewCycle.Status.OPEN
    cycle.save()
    common.log(cycle.company, actor, "review_cycle", cycle.pk, Action.UPDATED, previous={"status": "draft"}, new={"status": "open", "reviews": people.count()})
    return cycle


@transaction.atomic
def close(cycle, actor):
    cycle = ReviewCycle.objects.select_for_update().get(pk=cycle.pk)
    if cycle.status != ReviewCycle.Status.OPEN:
        raise ValidationError("Only an open cycle can be closed.")
    cycle.status = ReviewCycle.Status.CLOSED
    cycle.save()
    common.log(cycle.company, actor, "review_cycle", cycle.pk, Action.UPDATED, previous={"status": "open"}, new={"status": "closed"})
    return cycle


# --------------------------------------------------------------------------- reviews

def _rating(data, key, label):
    v = data.get(key)
    if v in (None, ""):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValidationError({key: f"{label} must be a whole number from 1 to 5."})
    if not 1 <= n <= 5 or str(v).strip() != str(n):
        raise ValidationError({key: f"{label} must be a whole number from 1 to 5."})
    return n


def _open(review):
    if review.cycle.status != ReviewCycle.Status.OPEN:
        raise ValidationError("This review cycle is not open.")


@transaction.atomic
def self_review(review, data, actor):
    review = Review.objects.select_for_update(of=("self",)).select_related("cycle", "employee", "employee__company").get(pk=review.pk)
    if getattr(actor, "employee", None) is None or actor.employee.pk != review.employee_id:
        raise PermissionDenied("Only the employee can write their own review.")
    _open(review)
    if review.status == Review.Status.COMPLETED:
        raise ValidationError("This review is already completed.")
    rating = _rating(data, "self_rating", "Rating")
    if rating is None:
        raise ValidationError({"self_rating": "Give yourself a rating from 1 to 5."})
    review.self_rating, review.self_comments = rating, text(data, "self_comments", "Comments", 2000)
    review.status = Review.Status.SELF_DONE
    review.save()
    common.log(review.employee.company, actor, "review", review.pk, Action.UPDATED, new={"self_rating": rating, "status": review.status})
    return review


def can_rate(actor, review):
    mine = getattr(actor, "employee", None)
    if mine is not None and mine.pk == review.employee_id:
        return False
    return actor.has_code("hr.manage") or bool(mine is not None and review.employee.manager_id == mine.pk)


@transaction.atomic
def manager_review(review, data, actor):
    review = Review.objects.select_for_update(of=("self",)).select_related("cycle", "employee", "employee__company").get(pk=review.pk)
    if not can_rate(actor, review):
        raise PermissionDenied("Only the manager or HR can rate this review, and nobody rates themselves.")
    _open(review)
    rating = _rating(data, "manager_rating", "Rating")
    if rating is None:
        raise ValidationError({"manager_rating": "Give a rating from 1 to 5."})
    before = {"manager_rating": review.manager_rating, "status": review.status}
    review.manager_rating, review.manager_comments = rating, text(data, "manager_comments", "Comments", 2000)
    mine = getattr(actor, "employee", None)
    if mine is not None:
        review.reviewer = mine
    review.status = Review.Status.COMPLETED
    review.save()
    common.log(review.employee.company, actor, "review", review.pk, Action.UPDATED, previous=before,
               new={"manager_rating": rating, "status": review.status})
    return review


def summary(company, cycle):
    reviews = Review.objects.filter(cycle=cycle)
    dist = {n: 0 for n in range(1, 6)}
    for r in reviews.exclude(manager_rating=None).values_list("manager_rating").annotate(n=Count("id")):
        dist[r[0]] = r[1]
    agg = reviews.aggregate(self_avg=Avg("self_rating"), manager_avg=Avg("manager_rating"))
    by_dept = reviews.exclude(manager_rating=None).values("employee__department__name").annotate(avg=Avg("manager_rating"), n=Count("id")).order_by("-avg")
    status = dict(reviews.values_list("status").annotate(n=Count("id")))
    goals = Goal.objects.filter(cycle=cycle)
    return {
        "cycle": {"id": str(cycle.id), "name": cycle.name, "status": cycle.status},
        "reviews": reviews.count(), "completed": status.get("completed", 0), "waiting_self": status.get("pending", 0),
        "waiting_manager": status.get("self_done", 0),
        "self_average": round(agg["self_avg"], 2) if agg["self_avg"] else None,
        "manager_average": round(agg["manager_avg"], 2) if agg["manager_avg"] else None,
        "distribution": [{"rating": n, "count": c} for n, c in dist.items()],
        "by_department": [{"name": r["employee__department__name"] or "No department", "average": round(r["avg"], 2), "count": r["n"]} for r in by_dept],
        "goals": {"total": goals.count(), "achieved": goals.filter(status="achieved").count(),
                  "average_progress": round(goals.aggregate(a=Avg("progress"))["a"] or 0)},
    }


# --------------------------------------------------------------------------- goals

def goal_fields(data, *, partial=False):
    out = {}
    if not partial or "title" in data:
        out["title"] = text(data, "title", "Title", 200, required=True)
    if "description" in data:
        out["description"] = text(data, "description", "Description", 2000)
    if "weight" in data:
        w = common.decimal(data, "weight", "Weight", places=0, high=100, default=0)
        out["weight"] = int(w)
    if "progress" in data:
        p = common.decimal(data, "progress", "Progress", places=0, high=100, default=0)
        out["progress"] = int(p)
    if "status" in data:
        out["status"] = common.choice(data, "status", "status", Goal.Status.values, default="not_started")
    if "manager_comment" in data:
        out["manager_comment"] = text(data, "manager_comment", "Comment", 500)
    return out


# --------------------------------------------------------------------------- project assignments

def project_names(ids):
    """{id: {code, name}} from the projects module when it is installed; empty when it is not (soft reference)."""
    try:
        Project = django_apps.get_model("projects", "Project")
        return {str(p["id"]): {"code": p["code"], "name": p["name"]} for p in Project.objects.filter(pk__in=list(ids)).values("id", "code", "name")}
    except Exception:  # noqa: BLE001 - the link is optional
        return {}


def _peak_allocation(employee, start, end, extra, exclude=None):
    """The most allocation on any one day in [start, end] across existing assignments plus the new one."""
    far = datetime.date(9999, 12, 31)
    rows = [(a.start_date, a.end_date or far, a.allocation_percent) for a in ProjectAssignment.objects.filter(employee=employee).exclude(pk=exclude)
            if a.start_date <= (end or far) and (a.end_date or far) >= start]
    rows.append((start, end or far, extra))
    points = {r[0] for r in rows} | {start}
    return max(sum(p for s, e, p in rows if s <= day <= e) for day in points if day >= start)


def apply_assignment(a, data, creating):
    if creating or "project_id" in data:
        try:
            pid = uuid.UUID(str(data.get("project_id")))
        except ValueError:
            raise ValidationError({"project_id": "Choose a project."})
        names = project_names([pid])
        if django_apps.is_installed("apps.projects") and not names:
            raise ValidationError({"project_id": "No such project."})
        a.project_id = pid
        a.project_label = (names.get(str(pid)) or {}).get("name", text(data, "project_label", "Project", 200))
    if creating or "role" in data:
        a.role = text(data, "role", "Role", 100)
    if creating or "allocation_percent" in data:
        a.allocation_percent = int(common.decimal(data, "allocation_percent", "Allocation", places=0, low=1, high=100, default=100))
    if creating or "start_date" in data:
        a.start_date = date_value(data, "start_date", "Start date", required=True)
    if "end_date" in data:
        a.end_date = date_value(data, "end_date", "End date")
    if a.end_date and a.end_date < a.start_date:
        raise ValidationError({"end_date": "The end date cannot be before the start date."})
    if "notes" in data:
        a.notes = text(data, "notes", "Notes", 300)
    if a.employee.status == Employee.Status.EXITED:
        raise ValidationError({"employee": "This employee has left the company."})
    peak = _peak_allocation(a.employee, a.start_date, a.end_date, 0, exclude=a.pk) + a.allocation_percent
    if peak > 100:
        raise ValidationError({"allocation_percent": f"This would put {a.employee.name} at {peak}% on some day; at most 100% is allowed."})


def utilisation(company, today=None):
    today = today or datetime.date.today()
    people = Employee.objects.filter(company=company).exclude(status="exited")
    sums = dict(ProjectAssignment.objects.filter(employee__in=people, start_date__lte=today).filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
                .values_list("employee_id").annotate(t=Sum("allocation_percent")))
    rows = [{"employee": {"id": str(e.id), "name": e.name}, "allocation": sums.get(e.pk, 0)} for e in people.order_by("first_name")]
    return {"rows": rows, "average": round(sum(r["allocation"] for r in rows) / len(rows)) if rows else 0,
            "bench": sum(1 for r in rows if r["allocation"] == 0), "over": sum(1 for r in rows if r["allocation"] > 100)}
