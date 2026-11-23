"""Project budget changes and the approvals module.

A change to a project's budget total or category lines that falls under an approval rule (kind `budget`, the amount being the
proposed total) does not touch the project. It is stored as a `BudgetRevision` that waits for approval; approving applies it,
rejecting or withdrawing leaves the budget as it was. A change no rule covers (no rules, or the module is not installed) is
applied at once, so the project works on its own.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import BudgetLine, BudgetRevision, Project
from .services import Action, log

ZERO = Decimal("0.00")
Status = BudgetRevision.Status


def _api():
    try:
        from apps.approvals import api
    except ImportError:
        return None
    return api if hasattr(api, "submit") and hasattr(api, "requires_approval") else None


def managed():
    """Whether decisions on budget changes must go through the shared approvals workflow."""
    return _api() is not None


def lines_of(project):
    return [{"category": l.category, "amount": str(l.amount), "notes": l.notes} for l in project.budget_lines.all()]


def _normal(lines):
    return sorted(([l["category"], str(Decimal(str(l["amount"])).quantize(ZERO)), l.get("notes", "")] for l in lines if Decimal(str(l["amount"])) > 0))


def proposed_total(budget, lines):
    """The budget in force: the entered total, or the sum of the lines when no total is entered."""
    return budget if budget > 0 else sum((Decimal(str(l["amount"])) for l in lines), ZERO)


def is_change(project, budget, lines):
    """Does the proposal differ from what the project has? `lines`: [{category, amount, notes}] or None for unchanged."""
    if budget is not None and budget != project.budget:
        return True
    return lines is not None and _normal(lines) != _normal(lines_of(project))


def needs_approval(project, budget, lines):
    api = _api()
    if api is None or not is_change(project, budget, lines):
        return False
    new_budget = project.budget if budget is None else budget
    new_lines = lines_of(project) if lines is None else lines
    return api.requires_approval(project.company, "budget", proposed_total(new_budget, new_lines))


def pending_for(project):
    return project.budget_revisions.filter(status=Status.PENDING).first()


def _lock(rev):
    return BudgetRevision.objects.select_for_update(of=("self",)).select_related("project", "project__company").get(pk=rev.pk)


def describe_text(rev):
    p = rev.project
    return (f"Budget of {p.code} {p.name} from {rev.previous_budget} to {rev.proposed_budget}"
            if rev.proposed_budget > 0 else f"Budget of {p.code} {p.name} set from its category lines ({rev.proposed_total})")


@transaction.atomic
def request_change(project, actor, *, budget, lines, reason=""):
    """Store the proposal and send it for approval. Returns the revision (already approved when no approver is needed)."""
    project = Project.objects.select_for_update(of=("self",)).select_related("company").get(pk=project.pk)
    if pending_for(project) is not None:
        raise ValidationError({"budget": "A budget change for this project is already waiting for approval. Withdraw it first."})
    new_budget = project.budget if budget is None else budget
    new_lines = lines_of(project) if lines is None else [
        {"category": l["category"], "amount": str(l["amount"]), "notes": l.get("notes", "")} for l in lines if l["amount"] > 0]
    rev = BudgetRevision.objects.create(
        project=project, previous_budget=project.budget, previous_lines=lines_of(project), proposed_budget=new_budget,
        proposed_lines=new_lines, proposed_total=proposed_total(new_budget, new_lines), reason=(reason or "")[:300], requested_by=actor)
    log(project.company, actor, project, Action.UPDATED, f"Budget change sent for approval: {describe_text(rev)}",
        previous={"budget": project.budget}, new={"budget": new_budget, "status": "pending approval"})
    _api().submit(project.company, kind="budget", source_id=str(rev.pk), title=f"Budget change: {project.code} {project.name}",
                  amount=rev.proposed_total, requested_by=actor, summary=_summary(rev), url=f"/projects/{project.pk}",
                  meta={"project": project.name})
    rev.refresh_from_db()
    return rev


def _summary(rev):
    rows = ", ".join(f"{l['category']} {l['amount']}" for l in rev.proposed_lines)
    return f"Total {rev.previous_budget} -> {rev.proposed_budget}" + (f"; lines: {rows}" if rows else "") + (f". {rev.reason}" if rev.reason else "")


def apply_directly(project, budget, lines):
    """Write the budget (and lines) onto the project; the caller logs the change."""
    if budget is not None:
        project.budget = budget
    project.save()
    if lines is not None:
        project.budget_lines.all().delete()
        BudgetLine.objects.bulk_create([BudgetLine(project=project, category=l["category"], amount=l["amount"], notes=l.get("notes", ""))
                                        for l in lines if l["amount"] > 0])


@transaction.atomic
def approve(rev, actor, note=""):
    rev = _lock(rev)
    if rev.status != Status.PENDING:
        raise ValidationError("Only a budget change that is waiting for approval can be approved.")
    project = rev.project
    before = {"budget": str(project.budget), "lines": str({l["category"]: l["amount"] for l in lines_of(project)})}
    apply_directly(project, rev.proposed_budget, [{"category": l["category"], "amount": Decimal(l["amount"]), "notes": l.get("notes", "")}
                                                  for l in rev.proposed_lines])
    rev.status, rev.decided_by, rev.decided_at, rev.decision_note = Status.APPROVED, actor, _now(), (note or "").strip()[:300]
    rev.save()
    after = {"budget": str(project.budget), "lines": str({l["category"]: l["amount"] for l in lines_of(project)})}
    log(project.company, actor, project, Action.UPDATED, f"Budget change approved: {describe_text(rev)}", previous=before, new=after)
    return rev


@transaction.atomic
def reject(rev, actor, reason):
    rev = _lock(rev)
    if rev.status != Status.PENDING:
        raise ValidationError("Only a budget change that is waiting for approval can be rejected.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this budget change."})
    rev.status, rev.decided_by, rev.decided_at, rev.decision_note = Status.REJECTED, actor, _now(), reason.strip()[:300]
    rev.save()
    log(rev.project.company, actor, rev.project, Action.UPDATED, f"Budget change rejected: {reason.strip()}")
    return rev


def withdraw(rev, actor):
    """The request was withdrawn (in approvals or on the project): the budget stays as it was."""
    rev = _lock(rev)
    if rev.status == Status.PENDING:
        rev.status, rev.decided_at = Status.WITHDRAWN, _now()
        rev.save()
        log(rev.project.company, actor, rev.project, Action.UPDATED, "Budget change withdrawn")
    return rev


@transaction.atomic
def take_back(rev, actor):
    rev = _lock(rev)
    if rev.status != Status.PENDING:
        raise ValidationError("Only a budget change that is waiting for approval can be withdrawn.")
    api = _api()
    if api is not None:
        api.cancel_for_source("budget", rev.pk, actor)
    return withdraw(rev, actor)


def resubmit(rev, actor):
    """A rejected or withdrawn request was sent again from the Approvals module: the same proposal waits once more, provided
    it is still the only one."""
    rev = _lock(rev)
    if rev.status in (Status.REJECTED, Status.WITHDRAWN) and pending_for(rev.project) is None:
        rev.status, rev.decided_at, rev.decided_by, rev.decision_note = Status.PENDING, None, None, ""
        rev.save()
        log(rev.project.company, actor, rev.project, Action.UPDATED, "Budget change sent for approval again")
    return rev


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


# --------------------------------------------------------------------------- what the approvals module calls

def _revision(request_obj):
    try:
        return BudgetRevision.objects.filter(pk=getattr(request_obj, "source_id", None)).first()
    except (ValueError, TypeError, ValidationError):
        return None


def _last_decision(request_obj):
    return request_obj.decisions.select_related("approver").order_by("-decided_at").first()


def _decider(request_obj):
    d = _last_decision(request_obj)
    return d.approver if d is not None else request_obj.requested_by


def _note(request_obj):
    d = _last_decision(request_obj)
    return d.comment if d is not None else ""


def on_approved(request_obj):
    rev = _revision(request_obj)
    if rev is not None and rev.status == Status.PENDING:
        approve(rev, _decider(request_obj), _note(request_obj))


def on_rejected(request_obj):
    rev = _revision(request_obj)
    if rev is not None and rev.status == Status.PENDING:
        reject(rev, _decider(request_obj), _note(request_obj) or "Rejected in approvals")


def on_cancelled(request_obj):
    rev = _revision(request_obj)
    if rev is not None:
        withdraw(rev, request_obj.requested_by)


def on_resubmitted(request_obj):
    rev = _revision(request_obj)
    if rev is not None:
        resubmit(rev, request_obj.requested_by)


def describe(source_id):
    try:
        rev = BudgetRevision.objects.filter(pk=source_id).select_related("project").first()
    except (ValueError, TypeError, ValidationError):
        rev = None
    if rev is None:
        return None
    return {"title": f"Budget change: {rev.project.code} {rev.project.name}", "amount": rev.proposed_total, "summary": _summary(rev),
            "url": f"/projects/{rev.project_id}", "project_id": rev.project_id,
            "requested_by_ok": lambda user: user.has_code("projects.manage")}


def register():
    api = _api()
    if api is None:
        return
    api.register_handler("budget", on_approved=on_approved, on_rejected=on_rejected, on_cancelled=on_cancelled,
                         on_resubmitted=on_resubmitted, label="Project budget", required_codes=("projects.manage",), describe=describe)
