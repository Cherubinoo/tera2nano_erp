import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting.models import JournalLine
from apps.attachments.models import Attachment
from apps.company.api import current_company
from apps.company.constants import DocType
from apps.company.services import numbering
from apps.sales import services as sales_services
from apps.sales.models import Customer, SalesDocument

from . import budget_approval, services
from .forms import Parser
from .models import BudgetLine, BudgetRevision, Milestone, Project, Task, TeamMember, TimeEntry
from .services import Action

MAX_ROWS = 1000
ZERO = services.ZERO
PStatus, MStatus, TStatus = Project.Status, Milestone.Status, Task.Status


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def _get(qs, pk, label):
    try:
        obj = qs.filter(pk=pk).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise NotFound(f"No such {label}.")
    return obj


def project_qs(company):
    return Project.objects.filter(company=company).select_related("customer").prefetch_related(
        "milestones", "tasks", "budget_lines")


def get_project(pk, company):
    return _get(project_qs(company), pk, "project")


def _body_project(request, company):
    pid = request.data.get("project")
    if not pid:
        raise ValidationError({"project": "Choose the project."})
    try:
        project = project_qs(company).filter(pk=pid).first()
    except (ValueError, DjangoValidationError):
        project = None
    if project is None:
        raise ValidationError({"project": "Project not found."})
    return project


def _project_ref(p):
    return {"id": str(p.id), "code": p.code, "name": p.name}


def _choices(model_choices):
    return [v for v, _ in model_choices]


# --------------------------------------------------------------------------- lookup (contract with the other modules)

class LookupView(APIView):
    """Open to every signed-in user: the Project dropdown in purchases, hr, sales and accounting."""

    def get(self, request):
        qs = Project.objects.filter(company=current_company()).exclude(status=PStatus.CANCELLED).select_related("customer")
        return Response([{"id": str(p.id), "code": p.code, "name": p.name, "status": p.status,
                          "customer": str(p.customer_id) if p.customer_id else None,
                          "customer_name": p.customer.display_name if p.customer_id else ""}
                         for p in qs.order_by("code")])


class OptionsView(APIView):
    def get(self, request):
        need(request, "projects.view")
        return Response({
            "types": [{"value": v, "label": l} for v, l in Project.Type.choices],
            "statuses": [{"value": v, "label": l} for v, l in Project.Status.choices],
            "priorities": [{"value": v, "label": l} for v, l in Project.Priority.choices],
            "billing": [{"value": v, "label": l} for v, l in Project.Billing.choices],
            "categories": [{"value": v, "label": l} for v, l in BudgetLine.Category.choices],
            "task_statuses": [{"value": v, "label": l} for v, l in Task.Status.choices],
            "milestone_statuses": [{"value": v, "label": l} for v, l in Milestone.Status.choices],
        })


# --------------------------------------------------------------------------- projects

PROJECT_ENUMS = {
    "project_type": Project.Type, "status": Project.Status, "priority": Project.Priority, "billing_method": Project.Billing,
}


def project_dict(p, s=None, *, detail=False):
    out = {
        "id": str(p.id), "code": p.code, "name": p.name, "description": p.description,
        "customer": {"id": str(p.customer_id), "name": p.customer.display_name} if p.customer_id else None,
        "project_type": p.project_type, "type_label": p.get_project_type_display(),
        "manager_id": str(p.manager_id) if p.manager_id else None, "manager_name": p.manager_name,
        "start_date": p.start_date, "planned_end_date": p.planned_end_date, "actual_end_date": p.actual_end_date,
        "status": p.status, "status_label": p.get_status_display(), "priority": p.priority,
        "priority_label": p.get_priority_display(), "billing_method": p.billing_method,
        "billing_label": p.get_billing_method_display(), "contract_value": p.contract_value, "budget": p.budget,
        "hourly_rate": p.hourly_rate, "tags": p.tags, "created_at": p.created_at,
    }
    if s is not None:
        out.update({k: v for k, v in s.items() if k != "ledger"})
    if detail:
        out["budget_lines"] = [{"category": l.category, "label": l.get_category_display(), "amount": l.amount, "notes": l.notes}
                               for l in p.budget_lines.all()]
        rev = budget_approval.pending_for(p)
        out["pending_budget_change"] = revision_dict(rev) if rev else None
        out["budget_approval_managed"] = budget_approval.managed()
    return out


def revision_dict(r):
    labels = dict(BudgetLine.Category.choices)
    return {"id": str(r.id), "project": str(r.project_id), "status": r.status, "status_label": r.get_status_display(),
            "previous_budget": r.previous_budget, "proposed_budget": r.proposed_budget, "proposed_total": r.proposed_total,
            "previous_lines": [{**l, "label": labels.get(l["category"], l["category"])} for l in r.previous_lines],
            "proposed_lines": [{**l, "label": labels.get(l["category"], l["category"])} for l in r.proposed_lines],
            "reason": r.reason, "decision_note": r.decision_note, "created_at": r.created_at, "decided_at": r.decided_at,
            "requested_by": (r.requested_by.get_full_name() or r.requested_by.get_username()) if r.requested_by_id else ""}


def _parse_project(request, partial=False, existing=None):
    p = Parser(request.data, partial)
    p.text("name", "Project name", required=True)
    p.text("description", "Description", max_len=5000)
    p.uuid("customer", "Client")
    p.choice("project_type", "Project type", _choices(Project.Type.choices), default="it_solution")
    p.uuid("manager_id", "Manager")
    p.text("manager_name", "Manager name", max_len=150)
    p.date("start_date", "Start date")
    p.date("planned_end_date", "Planned end date")
    p.date("actual_end_date", "Actual end date")
    p.choice("priority", "Priority", _choices(Project.Priority.choices), default="medium")
    p.choice("billing_method", "Billing method", _choices(Project.Billing.choices), default="fixed")
    p.decimal("contract_value", "Contract value")
    p.decimal("budget", "Budget")
    p.decimal("hourly_rate", "Hourly rate")
    p.tags()
    if not partial or "status" in request.data:
        p.choice("status", "Status", _choices(Project.Status.choices), default="planned")
    lines = None
    if "budget_lines" in request.data:
        lines = _parse_lines(request.data["budget_lines"], p)
    out = p.out
    errors = p.errors
    ex = existing
    start = out["start_date"] if "start_date" in out else (ex.start_date if ex else None)
    end = out["planned_end_date"] if "planned_end_date" in out else (ex.planned_end_date if ex else None)
    actual = out["actual_end_date"] if "actual_end_date" in out else (ex.actual_end_date if ex else None)
    if start and end and end < start and "planned_end_date" not in errors:
        errors["planned_end_date"] = "The planned end date cannot be before the start date."
    if start and actual and actual < start and "actual_end_date" not in errors:
        errors["actual_end_date"] = "The actual end date cannot be before the start date."
    budget = out["budget"] if "budget" in out else (ex.budget if ex else ZERO)
    if lines is not None and budget > 0 and sum(l["amount"] for l in lines) > budget and "budget_lines" not in errors:
        errors["budget_lines"] = "The budget lines add up to more than the project budget."
    if errors:
        raise ValidationError(errors)
    return out, lines


def _parse_lines(raw, parser):
    if not isinstance(raw, list):
        parser.errors["budget_lines"] = "Send the budget lines as a list."
        return []
    lines, seen = [], set()
    for i, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            parser.errors["budget_lines"] = f"Line {i}: send an object."
            continue
        lp = Parser(row)
        lp.choice("category", "Category", _choices(BudgetLine.Category.choices), required=True)
        lp.decimal("amount", "Amount")
        lp.text("notes", "Notes", max_len=200)
        if lp.errors:
            parser.errors["budget_lines"] = f"Line {i}: " + " ".join(lp.errors.values())
            continue
        if lp.out["category"] in seen:
            parser.errors["budget_lines"] = f"Line {i}: each category can appear once."
            continue
        seen.add(lp.out["category"])
        lines.append(lp.out)
    return lines


def _customer_for(company, customer_id, project_type):
    if customer_id is None:
        if project_type != Project.Type.INTERNAL:
            raise ValidationError({"customer": "Choose the client. Only internal projects have none."})
        return None
    c = Customer.objects.filter(pk=customer_id, company=company).first()
    if c is None:
        raise ValidationError({"customer": "Client not found."})
    return c


def _snapshot(p):
    return {"name": p.name, "client": p.customer.display_name if p.customer_id else "", "type": p.project_type,
            "status": p.status, "priority": p.priority, "billing": p.billing_method, "manager": p.manager_name,
            "start_date": p.start_date, "planned_end_date": p.planned_end_date, "actual_end_date": p.actual_end_date,
            "contract_value": p.contract_value, "budget": p.budget, "hourly_rate": p.hourly_rate, "tags": p.tags,
            "description": p.description}


def _save_lines(project, lines):
    project.budget_lines.all().delete()
    BudgetLine.objects.bulk_create([BudgetLine(project=project, **l) for l in lines if l["amount"] > 0])


def _lines_snapshot(project):
    return {l.category: str(l.amount) for l in project.budget_lines.all()}


class ProjectCollectionView(APIView):
    def get(self, request):
        need(request, "projects.view")
        company = current_company()
        p = request.query_params
        qs = project_qs(company)
        if p.get("status") == "open":
            qs = qs.filter(status__in=services.OPEN_STATUSES)
        elif p.get("status"):
            qs = qs.filter(status=p["status"])
        for key, field in (("customer", "customer_id"), ("type", "project_type"), ("priority", "priority")):
            if p.get(key):
                qs = qs.filter(**{field: p[key]})
        if p.get("manager"):
            qs = qs.filter(manager_name__icontains=p["manager"])
        if p.get("q"):
            q = p["q"]
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(customer__display_name__icontains=q)
                           | Q(manager_name__icontains=q) | Q(tags__icontains=q))
        projects = list(qs[:MAX_ROWS])
        summary = services.summarise(projects, company)
        if p.get("health"):
            projects = [x for x in projects if summary[x.pk]["health"] == p["health"]]
        rows = [project_dict(x, summary[x.pk]) for x in projects]
        return Response({
            "count": len(rows), "results": rows,
            "totals": {"contract_value": sum((x.contract_value for x in projects), ZERO),
                       "budget": sum((summary[x.pk]["budget_total"] for x in projects), ZERO),
                       "revenue": sum((summary[x.pk]["revenue"] for x in projects), ZERO),
                       "cost": sum((summary[x.pk]["cost"] for x in projects), ZERO)},
        })

    @transaction.atomic
    def post(self, request):
        need(request, "projects.manage")
        company = current_company()
        data, lines = _parse_project(request)
        customer = _customer_for(company, data.pop("customer", None), data["project_type"])
        status = data["status"]
        if status in (PStatus.COMPLETED, PStatus.CANCELLED):
            raise ValidationError({"status": "A new project starts as planned, active or on hold."})
        if data.get("actual_end_date"):
            raise ValidationError({"actual_end_date": "A new project has no actual end date yet."})
        project = Project(company=company, customer=customer, created_by=request.user, **data)
        project.code = numbering.allocate_number(company, DocType.PROJECT, actor=request.user).reference_number
        project.save()
        if lines:
            _save_lines(project, lines)
        services.log(company, request.user, project, Action.CREATED, f"Project {project.code} created", new=_snapshot(project))
        return Response(project_dict(*_one(project.pk, company), detail=True), status=http.HTTP_201_CREATED)


def _one(pk, company):
    p = get_project(pk, company)
    return p, services.summarise([p], company)[p.pk]


def _apply_status(project, new, actor, reason="", actual_end=None):
    """The one place a project changes status."""
    old = project.status
    if new == old:
        return
    reason = (reason or "").strip()
    if new == PStatus.CANCELLED and not reason:
        raise ValidationError({"reason": "Say why the project is cancelled."})
    if new == PStatus.COMPLETED:
        project.actual_end_date = actual_end or datetime.date.today()
        if project.start_date and project.actual_end_date < project.start_date:
            raise ValidationError({"actual_end_date": "The actual end date cannot be before the start date."})
    elif old in (PStatus.COMPLETED, PStatus.CANCELLED):
        project.actual_end_date = None  # reopened
    project.status = new
    project.save()
    services.log(project.company, actor, project, Action.UPDATED,
                 f"Status changed from {PStatus(old).label} to {PStatus(new).label}" + (f": {reason}" if reason else ""),
                 previous={"status": old}, new={"status": new})


class ProjectDetailView(APIView):
    def get(self, request, pk):
        need(request, "projects.view")
        return Response(project_dict(*_one(pk, current_company()), detail=True))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        project = get_project(pk, company)
        before, before_lines = _snapshot(project), _lines_snapshot(project)
        data, lines = _parse_project(request, partial=True, existing=project)
        new_status = data.pop("status", None)
        # A budget change under an approval rule waits as a revision; everything else in this request is saved now.
        held = None
        new_budget = data.get("budget")
        if budget_approval.needs_approval(project, new_budget, lines):
            held = {"budget": data.pop("budget", None), "lines": lines}
            lines = None
            if budget_approval.pending_for(project) is not None:
                raise ValidationError({"budget": "A budget change for this project is already waiting for approval. Withdraw it first."})
        if "customer" in data:
            ptype = data.get("project_type", project.project_type)
            project.customer = _customer_for(company, data.pop("customer"), ptype)
        elif "project_type" in data and data["project_type"] != Project.Type.INTERNAL and not project.customer_id:
            raise ValidationError({"customer": "Choose the client. Only internal projects have none."})
        for key, value in data.items():
            setattr(project, key, value)
        project.save()
        if lines is not None:
            _save_lines(project, lines)
        if new_status and new_status != project.status:
            _apply_status(project, new_status, request.user, request.data.get("reason", ""), data.get("actual_end_date"))
        project = get_project(pk, company)
        after, after_lines = _snapshot(project), _lines_snapshot(project)
        if lines is not None and before_lines != after_lines:
            before["budget_lines"], after["budget_lines"] = str(before_lines), str(after_lines)
        before.pop("status"), after.pop("status")  # status changes are logged by _apply_status
        if before != after:
            services.log(company, request.user, project, Action.UPDATED, "Project details changed", previous=before, new=after)
        if held is not None:
            budget_approval.request_change(project, request.user, budget=held["budget"], lines=held["lines"],
                                           reason=request.data.get("budget_change_reason") or request.data.get("reason") or "")
        return Response(project_dict(*_one(pk, company), detail=True))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        project = get_project(pk, company)
        reasons = []
        if project.time_entries.exists():
            reasons.append("time has been logged")
        if SalesDocument.objects.filter(project_id=project.pk).exists():
            reasons.append("invoices are tagged to it")
        if JournalLine.objects.filter(project_id=project.pk).exists():
            reasons.append("it has entries in the books")
        if Attachment.objects.filter(entity_type="project", entity_id=str(project.pk)).exists():
            reasons.append("files are attached")
        if reasons:
            raise ValidationError(f"This project cannot be deleted because {' and '.join(reasons)}. Cancel it instead.")
        services.log(company, request.user, project, Action.DELETED, f"Project {project.code} deleted", previous=_snapshot(project))
        project.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class ProjectStatusView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        project = get_project(pk, company)
        p = Parser(request.data)
        p.choice("status", "Status", _choices(Project.Status.choices), required=True)
        p.date("actual_end_date", "Actual end date")
        p.text("reason", "Reason", max_len=300)
        d = p.done()
        _apply_status(project, d["status"], request.user, d.get("reason", ""), d.get("actual_end_date"))
        return Response(project_dict(*_one(pk, company), detail=True))


# --------------------------------------------------------------------------- milestones

def _invoice_ref(m, docs):
    d = docs.get(m.invoice_id) if m.invoice_id else None
    if d is None:
        return None
    return {"id": str(d.id), "number": d.number, "status": d.status}


def milestone_dict(m, today, docs=None):
    key = services.milestone_key(m, today)
    return {
        "id": str(m.id), "project": _project_ref(m.project), "name": m.name, "description": m.description, "due_date": m.due_date,
        "amount": m.amount, "status": m.status, "status_key": key, "status_label": "Overdue" if key == "overdue" else m.get_status_display(),
        "completed_on": m.completed_on, "days_late": (today - m.due_date).days if key == "overdue" else 0,
        "invoice": _invoice_ref(m, docs or {}), "customer_id": str(m.project.customer_id) if m.project.customer_id else None,
    }


def _docs_for(milestones):
    ids = [m.invoice_id for m in milestones if m.invoice_id]
    return {d.pk: d for d in SalesDocument.objects.filter(pk__in=ids)} if ids else {}


def _parse_milestone(request, partial=False):
    p = Parser(request.data, partial)
    p.text("name", "Milestone name", required=True)
    p.text("description", "Description", max_len=500)
    p.date("due_date", "Due date", required=True)
    p.decimal("amount", "Amount to bill")
    p.choice("status", "Status", _choices(Milestone.Status.choices), default="pending")
    p.date("completed_on", "Completion date")
    return p.done()


def _set_milestone_state(m, status, completed_on):
    """done needs a completion date (today when none is given); any other status clears it."""
    m.status = status
    if status == MStatus.DONE:
        m.completed_on = completed_on or m.completed_on or datetime.date.today()
        if m.completed_on > datetime.date.today():
            raise ValidationError({"completed_on": "The completion date cannot be in the future."})
        if m.project.start_date and m.completed_on < m.project.start_date:
            raise ValidationError({"completed_on": "The completion date is before the project started."})
    else:
        m.completed_on = None


def _m_snapshot(m):
    return f"{m.name} ({m.get_status_display()}, due {m.due_date:%d %b %Y}, {m.amount})"


class MilestoneCollectionView(APIView):
    def get(self, request):
        need(request, "projects.view")
        company = current_company()
        today = datetime.date.today()
        p = request.query_params
        qs = Milestone.objects.filter(project__company=company).select_related("project", "project__customer")
        if p.get("project"):
            qs = qs.filter(project_id=p["project"])
        status = p.get("status")
        if status == "overdue":
            qs = qs.exclude(status=MStatus.DONE).filter(due_date__lt=today)
        elif status == "open":
            qs = qs.exclude(status=MStatus.DONE)
        elif status:
            qs = qs.filter(status=status)
            if status != MStatus.DONE:
                qs = qs.filter(due_date__gte=today)
        if p.get("q"):
            qs = qs.filter(Q(name__icontains=p["q"]) | Q(project__name__icontains=p["q"]) | Q(project__code__icontains=p["q"]))
        rows = list(qs[:MAX_ROWS])
        docs = _docs_for(rows)
        return Response({
            "count": len(rows), "results": [milestone_dict(m, today, docs) for m in rows],
            "total_amount": sum((m.amount for m in rows), ZERO),
            "overdue_count": sum(1 for m in rows if services.milestone_overdue(m, today)),
        })

    @transaction.atomic
    def post(self, request):
        need(request, "projects.manage")
        company = current_company()
        project = _body_project(request, company)
        d = _parse_milestone(request)
        if d["due_date"] and project.start_date and d["due_date"] < project.start_date:
            raise ValidationError({"due_date": "The due date is before the project starts."})
        m = Milestone(project=project, name=d["name"], description=d["description"], due_date=d["due_date"], amount=d["amount"])
        _set_milestone_state(m, d["status"], d.get("completed_on"))
        m.save()
        services.log(company, request.user, project, Action.UPDATED, f"Milestone added: {_m_snapshot(m)}",
                     new={"milestone": _m_snapshot(m)})
        return Response(milestone_dict(m, datetime.date.today()), status=http.HTTP_201_CREATED)


def _get_milestone(pk, company):
    return _get(Milestone.objects.filter(project__company=company).select_related("project", "project__customer"), pk, "milestone")


class MilestoneDetailView(APIView):
    @transaction.atomic
    def patch(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        m = _get_milestone(pk, company)
        before = _m_snapshot(m)
        d = _parse_milestone(request, partial=True)
        if m.invoice_id and "amount" in d and d["amount"] != m.amount:
            doc = SalesDocument.objects.filter(pk=m.invoice_id).exclude(status=SalesDocument.Status.VOID).first()
            if doc:
                raise ValidationError({"amount": "An invoice was already raised for this milestone; its amount can no longer change."})
        for key in ("name", "description", "due_date", "amount"):
            if key in d:
                setattr(m, key, d[key])
        if "status" in d or "completed_on" in d:
            _set_milestone_state(m, d.get("status", m.status), d.get("completed_on"))
        m.save()
        services.log(company, request.user, m.project, Action.UPDATED, f"Milestone changed: {_m_snapshot(m)}",
                     previous={"milestone": before}, new={"milestone": _m_snapshot(m)})
        return Response(milestone_dict(m, datetime.date.today(), _docs_for([m])))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        m = _get_milestone(pk, company)
        if m.invoice_id and SalesDocument.objects.filter(pk=m.invoice_id).exclude(status=SalesDocument.Status.VOID).exists():
            raise ValidationError("An invoice was raised for this milestone. Void or delete that invoice first.")
        services.log(company, request.user, m.project, Action.UPDATED, f"Milestone removed: {_m_snapshot(m)}",
                     previous={"milestone": _m_snapshot(m)})
        m.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class MilestoneInvoiceView(APIView):
    """A draft sales invoice for the milestone's amount, tagged to the project; the person then reviews and issues it."""

    @transaction.atomic
    def post(self, request, pk):
        need(request, "projects.manage")
        need(request, "sales.manage")
        company = current_company()
        m = _get_milestone(pk, company)
        project = m.project
        if not project.customer_id:
            raise ValidationError("This project has no client to invoice. Choose one on the project first.")
        if m.amount <= 0:
            raise ValidationError({"amount": "Enter the amount to bill on this milestone first."})
        if m.invoice_id and SalesDocument.objects.filter(pk=m.invoice_id).exclude(status=SalesDocument.Status.VOID).exists():
            raise ValidationError("An invoice has already been raised for this milestone.")
        doc = sales_services.save_draft(
            company, request.user, kind="invoice", customer=project.customer, issue_date=datetime.date.today(),
            form={"items": [{"description": f"{project.name}: {m.name}", "quantity": "1", "rate": str(m.amount)}],
                  "subject": f"{project.name} ({project.code})"},
            project_id=project.pk)
        m.invoice_id = doc.pk
        m.save()
        services.log(company, request.user, project, Action.UPDATED, f"Draft invoice raised for milestone {m.name}",
                     new={"milestone_invoice": f"{m.name}: draft invoice"})
        return Response({"invoice": {"id": str(doc.id), "status": doc.status, "number": doc.number},
                         "milestone": milestone_dict(m, datetime.date.today(), _docs_for([m]))}, status=http.HTTP_201_CREATED)


# --------------------------------------------------------------------------- tasks

def task_dict(t, today, logged=None):
    overdue = bool(t.due_date and t.status != TStatus.DONE and t.due_date < today)
    logged = logged if logged is not None else getattr(t, "logged", None) or ZERO
    return {
        "id": str(t.id), "code": t.code, "project": _project_ref(t.project), "title": t.title, "description": t.description,
        "milestone": {"id": str(t.milestone_id), "name": t.milestone.name} if t.milestone_id else None,
        "assignee_id": str(t.assignee_id) if t.assignee_id else None, "assignee_name": t.assignee_name,
        "due_date": t.due_date, "priority": t.priority, "priority_label": t.get_priority_display(), "status": t.status,
        "status_label": t.get_status_display(), "overdue": overdue, "estimated_hours": t.estimated_hours,
        "logged_hours": logged, "completed_on": t.completed_on,
    }


def _task_qs(company):
    return Task.objects.filter(project__company=company).select_related("project", "milestone").annotate(logged=Sum("time_entries__hours"))


def _parse_task(request, company, project, partial=False):
    p = Parser(request.data, partial)
    p.text("title", "Task title", required=True)
    p.text("description", "Description", max_len=3000)
    p.uuid("assignee_id", "Assignee")
    p.text("assignee_name", "Assignee name", max_len=150)
    p.date("due_date", "Due date")
    p.choice("priority", "Priority", _choices(Task.Priority.choices), default="medium")
    p.choice("status", "Status", _choices(Task.Status.choices), default="todo")
    p.decimal("estimated_hours", "Estimated hours", places=2, maximum=Decimal("100000"))
    p.uuid("milestone", "Milestone")
    d = p.done()
    if d.get("milestone"):
        if not Milestone.objects.filter(pk=d["milestone"], project=project).exists():
            raise ValidationError({"milestone": "Choose a milestone of this project."})
    return d


def _task_state(t, status):
    t.status = status
    t.completed_on = (t.completed_on or datetime.date.today()) if status == TStatus.DONE else None


def _t_snapshot(t):
    return f"{t.code} {t.title} ({t.get_status_display()}{', ' + t.assignee_name if t.assignee_name else ''})"


class TaskCollectionView(APIView):
    def get(self, request):
        need(request, "projects.view")
        company = current_company()
        today = datetime.date.today()
        p = request.query_params
        qs = _task_qs(company)
        if p.get("project"):
            qs = qs.filter(project_id=p["project"])
        if p.get("status") == "open":
            qs = qs.exclude(status=TStatus.DONE)
        elif p.get("status") == "overdue":
            qs = qs.exclude(status=TStatus.DONE).filter(due_date__lt=today)
        elif p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("assignee"):
            qs = qs.filter(Q(assignee_id=p["assignee"]) if len(p["assignee"]) == 36 else Q(assignee_name__icontains=p["assignee"]))
        if p.get("priority"):
            qs = qs.filter(priority=p["priority"])
        if p.get("q"):
            qs = qs.filter(Q(title__icontains=p["q"]) | Q(code__icontains=p["q"]) | Q(assignee_name__icontains=p["q"])
                           | Q(project__name__icontains=p["q"]))
        rows = list(qs[:MAX_ROWS])
        return Response({"count": len(rows), "results": [task_dict(t, today) for t in rows],
                         "estimated_hours": sum((t.estimated_hours for t in rows), ZERO),
                         "logged_hours": sum(((t.logged or ZERO) for t in rows), ZERO)})

    @transaction.atomic
    def post(self, request):
        need(request, "projects.manage")
        company = current_company()
        project = _body_project(request, company)
        if project.status in (PStatus.CANCELLED,):
            raise ValidationError({"project": "This project is cancelled."})
        d = _parse_task(request, company, project)
        milestone = d.pop("milestone", None)
        status = d.pop("status")
        t = Task(project=project, milestone_id=milestone, **d)
        _task_state(t, status)
        t.code = numbering.allocate_number(company, DocType.TASK, actor=request.user).reference_number
        t.save()
        services.log(company, request.user, project, Action.UPDATED, f"Task added: {_t_snapshot(t)}", new={"task": _t_snapshot(t)})
        return Response(task_dict(_task_qs(company).get(pk=t.pk), datetime.date.today()), status=http.HTTP_201_CREATED)


class TaskDetailView(APIView):
    def get(self, request, pk):
        need(request, "projects.view")
        return Response(task_dict(_get(_task_qs(current_company()), pk, "task"), datetime.date.today()))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        t = _get(_task_qs(company), pk, "task")
        before = _t_snapshot(t)
        d = _parse_task(request, company, t.project, partial=True)
        if "milestone" in d:
            t.milestone_id = d.pop("milestone")
        status = d.pop("status", None)
        for key, value in d.items():
            setattr(t, key, value)
        if status:
            _task_state(t, status)
        t.save()
        if _t_snapshot(t) != before:
            services.log(company, request.user, t.project, Action.UPDATED, f"Task changed: {_t_snapshot(t)}",
                         previous={"task": before}, new={"task": _t_snapshot(t)})
        return Response(task_dict(_task_qs(company).get(pk=pk), datetime.date.today()))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        t = _get(_task_qs(company), pk, "task")
        services.log(company, request.user, t.project, Action.UPDATED, f"Task removed: {_t_snapshot(t)}",
                     previous={"task": _t_snapshot(t)})
        t.delete()  # time already logged stays on the project (the entry just loses its task)
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- team

def member_dict(m):
    return {"id": str(m.id), "project": str(m.project_id), "employee_id": str(m.employee_id) if m.employee_id else None,
            "name": m.name, "role": m.role, "allocation_percent": m.allocation_percent, "hourly_rate": m.hourly_rate,
            "is_active": m.is_active}


def _parse_member(request, partial=False):
    p = Parser(request.data, partial)
    p.text("name", "Name", required=True, max_len=150)
    p.uuid("employee_id", "Employee")
    p.text("role", "Role", max_len=100)
    p.integer("allocation_percent", "Allocation", minimum=0, maximum=100, default=100)
    p.decimal("hourly_rate", "Hourly rate")
    p.boolean("is_active", "Active")
    return p.done()


class TeamCollectionView(APIView):
    def get(self, request):
        need(request, "projects.view")
        company = current_company()
        qs = TeamMember.objects.filter(project__company=company)
        if request.query_params.get("project"):
            qs = qs.filter(project_id=request.query_params["project"])
        rows = list(qs.select_related("project")[:MAX_ROWS])
        # How much time each person has logged on the project, so the team tab shows the effort.
        hours = {(pid, name): h for pid, name, h in TimeEntry.objects.filter(project__company=company).values_list(
            "project_id", "employee_name").annotate(h=Sum("hours"))}
        return Response({"count": len(rows), "results": [{**member_dict(m), "logged_hours": hours.get((m.project_id, m.name), ZERO)}
                                                         for m in rows]})

    @transaction.atomic
    def post(self, request):
        need(request, "projects.manage")
        company = current_company()
        project = _body_project(request, company)
        d = _parse_member(request)
        _unique_member(project, d)
        m = TeamMember.objects.create(project=project, **d)
        services.log(company, request.user, project, Action.UPDATED, f"Team member added: {m.name}", new={"team_member": m.name})
        return Response(member_dict(m), status=http.HTTP_201_CREATED)


def _unique_member(project, d, exclude=None):
    qs = project.team.exclude(pk=exclude) if exclude else project.team.all()
    if d.get("employee_id") and qs.filter(employee_id=d["employee_id"]).exists():
        raise ValidationError({"employee_id": "This person is already on the team."})
    if d.get("name") and qs.filter(name__iexact=d["name"]).exists():
        raise ValidationError({"name": "A team member with this name already exists."})


def _get_member(pk, company):
    return _get(TeamMember.objects.filter(project__company=company).select_related("project"), pk, "team member")


class TeamDetailView(APIView):
    @transaction.atomic
    def patch(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        m = _get_member(pk, company)
        d = _parse_member(request, partial=True)
        _unique_member(m.project, d, exclude=m.pk)
        for key, value in d.items():
            setattr(m, key, value)
        m.save()
        services.log(company, request.user, m.project, Action.UPDATED, f"Team member changed: {m.name}",
                     new={"team_member": f"{m.name} ({m.role or 'no role'}, {m.allocation_percent}%)"})
        return Response(member_dict(m))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        m = _get_member(pk, company)
        services.log(company, request.user, m.project, Action.UPDATED, f"Team member removed: {m.name}", previous={"team_member": m.name})
        m.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- timesheets

def entry_dict(e):
    return {
        "id": str(e.id), "project": _project_ref(e.project),
        "task": {"id": str(e.task_id), "code": e.task.code, "title": e.task.title} if e.task_id else None,
        "date": e.date, "employee_id": str(e.employee_id) if e.employee_id else None, "employee_name": e.employee_name,
        "user": e.user.get_username() if e.user_id else "", "hours": e.hours, "billable": e.billable, "rate": e.rate,
        "amount": e.amount, "notes": e.notes,
    }


def _parse_entry(request, project, partial=False):
    p = Parser(request.data, partial)
    p.date("date", "Date", required=True)
    p.decimal("hours", "Hours", maximum=Decimal("24"), positive=True)
    p.boolean("billable", "Billable")
    p.decimal("rate", "Rate", maximum=Decimal("10000000"))
    p.uuid("employee_id", "Employee")
    p.text("employee_name", "Employee", max_len=150)
    p.text("notes", "Notes", max_len=300)
    p.uuid("task", "Task")
    d = p.done()
    if not partial and "hours" not in d:
        raise ValidationError({"hours": "Enter the hours."})
    if d.get("task") and not Task.objects.filter(pk=d["task"], project=project).exists():
        raise ValidationError({"task": "Choose a task of this project."})
    if d.get("date") and d["date"] > datetime.date.today() + datetime.timedelta(days=1):
        raise ValidationError({"date": "Time cannot be logged for a future date."})
    return d


def _default_rate(project, d):
    """The member's bill rate when the person is on the team, else the project's hourly rate."""
    member = None
    if d.get("employee_id"):
        member = project.team.filter(employee_id=d["employee_id"]).first()
    if member is None and d.get("employee_name"):
        member = project.team.filter(name__iexact=d["employee_name"]).first()
    return member.hourly_rate if member and member.hourly_rate > 0 else project.hourly_rate


def _e_text(e):
    return f"{e.employee_name or 'someone'} {e.hours} h on {e.date:%d %b %Y}{'' if e.billable else ' (non-billable)'}"


def _entries_qs(company):
    return TimeEntry.objects.filter(project__company=company).select_related("project", "task", "user")


class TimesheetCollectionView(APIView):
    def get(self, request):
        need(request, "projects.view")
        company = current_company()
        p = request.query_params
        qs = _entries_qs(company)
        if p.get("project"):
            qs = qs.filter(project_id=p["project"])
        if p.get("task"):
            qs = qs.filter(task_id=p["task"])
        if p.get("employee"):
            qs = qs.filter(Q(employee_id=p["employee"]) if len(p["employee"]) == 36 else Q(employee_name__icontains=p["employee"]))
        if p.get("billable") in ("true", "false"):
            qs = qs.filter(billable=p["billable"] == "true")
        for key, op in (("from", "gte"), ("to", "lte")):
            if p.get(key):
                try:
                    qs = qs.filter(**{f"date__{op}": datetime.date.fromisoformat(p[key])})
                except ValueError:
                    raise ValidationError({key: "Use a date in YYYY-MM-DD format."})
        tuples = list(qs.values_list("date", "hours", "billable", "rate"))
        hours, billable, amount = services.time_totals(tuples)
        rows = list(qs[:MAX_ROWS])
        return Response({
            "count": len(tuples), "results": [entry_dict(e) for e in rows], "hours": hours, "billable_hours": billable,
            "non_billable_hours": hours - billable, "amount": amount, "weekly": services.weekly(tuples)[:26],
        })

    @transaction.atomic
    def post(self, request):
        need(request, "projects.manage")
        company = current_company()
        project = _body_project(request, company)
        if project.status == PStatus.CANCELLED:
            raise ValidationError({"project": "Time cannot be logged on a cancelled project."})
        d = _parse_entry(request, project)
        if not d.get("employee_name") and not d.get("employee_id"):
            d["employee_name"] = request.user.get_full_name() or request.user.get_username()
        if "rate" not in request.data or request.data.get("rate") in (None, ""):
            d["rate"] = _default_rate(project, d)
        task = d.pop("task", None)
        e = TimeEntry(project=project, task_id=task, user=request.user, created_by=request.user, **d)
        e.save()
        services.log(company, request.user, project, Action.UPDATED, f"Time logged: {_e_text(e)}", new={"time": _e_text(e)})
        return Response(entry_dict(_entries_qs(company).get(pk=e.pk)), status=http.HTTP_201_CREATED)


class TimesheetDetailView(APIView):
    @transaction.atomic
    def patch(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        e = _get(_entries_qs(company), pk, "time entry")
        before = _e_text(e)
        d = _parse_entry(request, e.project, partial=True)
        if "task" in d:
            e.task_id = d.pop("task")
        for key, value in d.items():
            setattr(e, key, value)
        e.save()
        services.log(company, request.user, e.project, Action.UPDATED, f"Time changed: {_e_text(e)}",
                     previous={"time": before}, new={"time": _e_text(e)})
        return Response(entry_dict(_entries_qs(company).get(pk=pk)))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        e = _get(_entries_qs(company), pk, "time entry")
        services.log(company, request.user, e.project, Action.UPDATED, f"Time removed: {_e_text(e)}", previous={"time": _e_text(e)})
        e.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- money, from the books

def _tagged_docs(company, project):
    return SalesDocument.objects.filter(company=company, project_id=project.pk).select_related("customer")


class FinancialsView(APIView):
    """The project's profit and loss, straight from journal lines tagged with its id."""

    def get(self, request, pk):
        need(request, "projects.view")
        company = current_company()
        project = get_project(pk, company)
        s = services.summarise([project], company)[project.pk]
        led = s["ledger"]
        rec = services.receivable_by_project(company, [project.pk]).get(project.pk) or {"invoiced": ZERO, "collected": ZERO, "outstanding": ZERO}
        return Response({
            "project": _project_ref(project), "revenue": led["revenue"], "cost": led["cost"], "profit": s["profit"],
            "margin_percent": s["margin_percent"], "revenue_rows": sorted(led["revenue_rows"], key=lambda r: r["code"]),
            "cost_rows": sorted(led["cost_rows"], key=lambda r: r["code"]), "categories": led["categories"],
            "invoiced": rec["invoiced"], "collected": rec["collected"], "outstanding": rec["outstanding"],
            "contract_value": project.contract_value, "wip": s["wip"], "billable_amount": s["billable_amount"],
            "monthly": services.monthly_money(company, [project.pk]),
        })


class BudgetView(APIView):
    def get(self, request, pk):
        need(request, "projects.view")
        company = current_company()
        project = get_project(pk, company)
        led = services.ledger_money(company, [project.pk]).get(project.pk) or services.empty_money()
        out = services.budget_vs_actual(project, list(project.budget_lines.all()), led["categories"], led["cost"])
        out["cost_rows"] = sorted(led["cost_rows"], key=lambda r: r["code"])
        return Response(out)


def _revision(pk, company):
    try:
        rev = BudgetRevision.objects.filter(pk=pk, project__company=company).select_related("project", "requested_by").first()
    except (ValueError, DjangoValidationError):
        rev = None
    if rev is None:
        raise NotFound("No such budget change.")
    return rev


class BudgetRevisionCollectionView(APIView):
    """The budget changes asked for on a project, newest first (waiting, approved, rejected, withdrawn)."""

    def get(self, request, pk):
        need(request, "projects.view")
        project = get_project(pk, current_company())
        return Response([revision_dict(r) for r in project.budget_revisions.select_related("requested_by")[:50]])


class BudgetRevisionActionView(APIView):
    action = ""

    @transaction.atomic
    def post(self, request, pk):
        need(request, "projects.manage")
        company = current_company()
        rev, user = _revision(pk, company), request.user
        if self.action == "withdraw":
            budget_approval.take_back(rev, user)
        else:
            if budget_approval.managed():
                raise ValidationError("Budget changes waiting for approval are decided in the Approvals module.")
            if self.action == "approve":
                budget_approval.approve(rev, user, request.data.get("note") or "")
            else:
                budget_approval.reject(rev, user, request.data.get("change_reason"))
        return Response(revision_dict(_revision(pk, company)))


class InvoicesView(APIView):
    """Sales documents tagged to the project (the same list as /sales/documents/?project=)."""

    def get(self, request, pk):
        need(request, "projects.view")
        company = current_company()
        project = get_project(pk, company)
        today = datetime.date.today()
        docs = sales_services.with_amounts(_tagged_docs(company, project)).exclude(status=SalesDocument.Status.VOID)
        rows, total, balance = [], ZERO, ZERO
        for d in docs:
            key, label = sales_services.display_status(d, today)
            bal = max(sales_services.balance_of(d), ZERO) if d.is_receivable and d.status == SalesDocument.Status.ISSUED else None
            if d.status == SalesDocument.Status.ISSUED and d.kind in (SalesDocument.Kind.INVOICE, SalesDocument.Kind.DEBIT_NOTE):
                total += d.grand_total
            if bal:
                balance += bal
            rows.append({"id": str(d.id), "number": d.number, "kind": d.kind, "kind_label": d.get_kind_display(),
                         "issue_date": d.issue_date, "due_date": d.due_date, "status_key": key, "status_label": label,
                         "grand_total": d.grand_total, "balance": bal, "customer": d.customer.display_name})
        return Response({"count": len(rows), "results": rows, "invoiced": total, "outstanding": balance})


class ExpensesView(APIView):
    """Bills and expenses: ledger lines on expense accounts tagged with the project."""

    def get(self, request, pk):
        need(request, "projects.view")
        company = current_company()
        project = get_project(pk, company)
        lines = JournalLine.objects.filter(entry__company=company, project_id=project.pk, account__type="expense").select_related(
            "entry", "account").order_by("-entry__date", "-created_at")
        rows, total = [], ZERO
        for l in lines[:MAX_ROWS]:
            amount = l.debit - l.credit
            total += amount
            rows.append({"id": str(l.id), "date": l.entry.date, "entry_id": str(l.entry_id), "reference": l.entry.reference,
                         "source": l.entry.source, "source_label": l.entry.get_source_display(), "memo": l.memo or l.entry.narration,
                         "account": f"{l.account.code} {l.account.name}", "category": services.category_for_account(l.account),
                         "amount": amount, "reversed": l.entry.status == "reversed"})
        return Response({"count": len(rows), "results": rows, "total": total})


class ActivityView(APIView):
    def get(self, request, pk):
        need(request, "projects.view")
        from apps.company.models import CompanyProfileAuditEvent
        company = current_company()
        project = get_project(pk, company)
        events = CompanyProfileAuditEvent.objects.filter(company=company, entity_type="project", entity_id=project.pk)[:200]
        return Response([{"id": str(e.id), "occurred_at": e.occurred_at, "actor": e.actor_label or "System", "action": e.action,
                          "text": e.reason or ", ".join(e.changed_fields), "changed_fields": e.changed_fields,
                          "previous": e.previous_values, "new": e.new_values} for e in events])


# --------------------------------------------------------------------------- portfolio and dashboard

def _portfolio_rows(company, statuses=None):
    qs = project_qs(company)
    if statuses:
        qs = qs.filter(status__in=statuses)
    projects = list(qs[:MAX_ROWS])
    summary = services.summarise(projects, company)
    rows = []
    for p in projects:
        s = summary[p.pk]
        rows.append(project_dict(p, s))
    return projects, summary, rows


class PortfolioView(APIView):
    """Profitability per project plus the figures the portfolio charts need."""

    def get(self, request):
        need(request, "projects.view")
        company = current_company()
        status = request.query_params.get("status")
        projects, summary, rows = _portfolio_rows(company, [status] if status else None)
        revenue = sum((summary[p.pk]["revenue"] for p in projects), ZERO)
        cost = sum((summary[p.pk]["cost"] for p in projects), ZERO)
        return Response({
            "count": len(rows), "results": rows,
            "totals": {"revenue": revenue, "cost": cost, "profit": revenue - cost, "margin_percent": services.percent(revenue - cost, revenue),
                       "contract_value": sum((p.contract_value for p in projects), ZERO),
                       "wip": sum((summary[p.pk]["wip"] for p in projects), ZERO)},
            "monthly": services.monthly_money(company, [p.pk for p in projects]),
        })


class DashboardView(APIView):
    def get(self, request):
        need(request, "projects.view")
        company = current_company()
        today = datetime.date.today()
        projects, summary, rows = _portfolio_rows(company)
        live = [p for p in projects if p.status != PStatus.CANCELLED]
        open_ = [p for p in projects if p.status in services.OPEN_STATUSES]
        revenue = sum((summary[p.pk]["revenue"] for p in live), ZERO)
        cost = sum((summary[p.pk]["cost"] for p in live), ZERO)
        overdue = Milestone.objects.filter(project__company=company, project__status__in=services.OPEN_STATUSES).exclude(
            status=MStatus.DONE).filter(due_date__lt=today).select_related("project").order_by("due_date")
        soon = Milestone.objects.filter(project__company=company, project__status__in=services.OPEN_STATUSES).exclude(
            status=MStatus.DONE).filter(due_date__gte=today, due_date__lte=today + datetime.timedelta(days=30)).select_related(
            "project").order_by("due_date")
        by_profit = sorted(live, key=lambda p: summary[p.pk]["profit"], reverse=True)
        by_revenue = sorted([p for p in live if summary[p.pk]["revenue"] or summary[p.pk]["cost"]],
                            key=lambda p: summary[p.pk]["revenue"], reverse=True)

        def short(p):
            return {"id": str(p.id), "code": p.code, "name": p.name}

        return Response({
            "tiles": {
                "active_projects": sum(1 for p in projects if p.status == PStatus.ACTIVE), "open_projects": len(open_),
                "overdue_milestones": overdue.count(), "contract_value": sum((p.contract_value for p in open_), ZERO),
                "wip": sum((summary[p.pk]["wip"] for p in open_), ZERO), "revenue": revenue, "cost": cost, "profit": revenue - cost,
                "margin_percent": services.percent(revenue - cost, revenue),
                "open_tasks": Task.objects.filter(project__company=company).exclude(status=TStatus.DONE).count(),
                "overdue_tasks": Task.objects.filter(project__company=company, due_date__lt=today).exclude(status=TStatus.DONE).count(),
            },
            "status_counts": {v: sum(1 for p in projects if p.status == v) for v, _ in Project.Status.choices},
            "health_counts": {h: sum(1 for p in open_ if summary[p.pk]["health"] == h) for h in ("on_track", "at_risk", "delayed")},
            "profitability": [{**short(p), "profit": summary[p.pk]["profit"], "margin_percent": summary[p.pk]["margin_percent"]}
                              for p in by_profit if summary[p.pk]["revenue"] or summary[p.pk]["cost"]][:8],
            "revenue_vs_cost": [{**short(p), "revenue": summary[p.pk]["revenue"], "cost": summary[p.pk]["cost"]} for p in by_revenue[:6]],
            "monthly": services.monthly_money(company, [p.pk for p in live]),
            "overdue_milestones": [{"id": str(m.id), "name": m.name, "due_date": m.due_date, "amount": m.amount,
                                    "days_late": (today - m.due_date).days, "project": short(m.project)} for m in overdue[:10]],
            "upcoming_milestones": [{"id": str(m.id), "name": m.name, "due_date": m.due_date, "amount": m.amount,
                                     "project": short(m.project)} for m in soon[:8]],
            "timeline": [{**short(p), "status": p.status, "start_date": p.start_date, "planned_end_date": p.planned_end_date,
                          "progress": summary[p.pk]["progress"], "health": summary[p.pk]["health"]}
                         for p in sorted(open_, key=lambda p: (p.start_date or datetime.date.max, p.code))[:12]],
            "today": today,
        })
