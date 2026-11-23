"""Salary structures and salary revisions.

A salary structure says what an employee is paid each month from a date. The first structure of an employee, a cut, or a raise
that no approval rule covers is saved at once. A raise that does fall under an approval rule (kind `salary_revision`, the
amount being the increase in yearly gross pay: (new monthly gross - gross in force on that date) x 12) is stored as a
`SalaryRevision`; payroll sees no change until it is approved and the structure is created.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from . import common, payroll
from ..models import SalaryRevision, SalaryStructure

Status = SalaryRevision.Status
Action = common.Action
SALARY_FIELDS = ("basic", "hra", "special_allowance", "other_allowance", "fixed_deductions", "tds_monthly")
FLAGS = ("pf_enabled", "pf_on_full_basic", "esi_enabled", "pt_enabled")
DEFAULT_ON = ("pf_enabled", "esi_enabled", "pt_enabled")


def _api():
    try:
        from apps.approvals import api
    except ImportError:
        return None
    return api if hasattr(api, "submit") and hasattr(api, "requires_approval") else None


def managed():
    """Whether decisions on salary revisions must go through the shared approvals workflow."""
    return _api() is not None


def snap(s):
    return {"effective_from": s.effective_from.isoformat(), **{f: str(getattr(s, f)) for f in SALARY_FIELDS}, **{f: getattr(s, f) for f in FLAGS}}


def parse(employee, d, actor):
    """Check a salary form and return an unsaved SalaryStructure. Raises a field-keyed ValidationError."""
    s = SalaryStructure(employee=employee, created_by=actor)
    s.effective_from = common.date_value(d, "effective_from", "Effective date", required=True)
    if s.effective_from < employee.date_of_joining:
        raise ValidationError({"effective_from": "The salary cannot start before the date of joining."})
    for f in SALARY_FIELDS:
        setattr(s, f, common.decimal(d, f, f.replace("_", " ").capitalize(), default=common.ZERO, high=1000000000))
    for f in FLAGS:
        setattr(s, f, common.boolean(d, f, f in DEFAULT_ON))
    s.notes = common.text(d, "notes", "Notes", 300)
    if s.gross <= 0:
        raise ValidationError({"basic": "Enter the salary: the monthly gross must be more than zero."})
    if SalaryStructure.objects.filter(employee=employee, effective_from=s.effective_from).exists():
        raise ValidationError({"effective_from": "A salary structure already starts on this date."})
    return s


def save_structure(s, actor):
    s.save()
    common.log(s.employee.company, actor, "salary_structure", s.pk, Action.CREATED, new=snap(s))
    return s


def increase_of(employee, s):
    """(structure in force on the effective date or None, yearly increase in gross pay)."""
    cur = payroll.structure_for(employee, s.effective_from)
    if cur is None:
        return None, common.ZERO
    return cur, common.money((s.gross - cur.gross) * 12)


def needs_approval(employee, s):
    api = _api()
    if api is None:
        return False
    cur, increase = increase_of(employee, s)
    return cur is not None and increase > 0 and api.requires_approval(employee.company, "salary_revision", increase)


def pending_for(employee):
    return employee.salary_revisions.filter(status=Status.PENDING).first()


def _lock(rev):
    return SalaryRevision.objects.select_for_update(of=("self",)).select_related("employee", "employee__company").get(pk=rev.pk)


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def title_of(rev):
    return f"Salary revision: {rev.employee.name} from {rev.effective_from.isoformat()}"


def summary_of(rev):
    return (f"Monthly gross {rev.previous_gross} -> {rev.new_gross}; yearly increase {rev.annual_increase}"
            + (f". {rev.reason}" if rev.reason else ""))


@transaction.atomic
def request_change(employee, s, actor, reason=""):
    """Store the proposed structure and send it for approval."""
    employee = type(employee).objects.select_for_update(of=("self",)).select_related("company").get(pk=employee.pk)
    if pending_for(employee) is not None:
        raise ValidationError({"effective_from": "A salary revision for this employee is already waiting for approval. Withdraw it first."})
    cur, increase = increase_of(employee, s)
    rev = SalaryRevision.objects.create(
        employee=employee, effective_from=s.effective_from, structure={**{f: str(common.money(getattr(s, f))) for f in SALARY_FIELDS},
                                                                       **{f: getattr(s, f) for f in FLAGS}, "notes": s.notes},
        previous_structure_id=cur.pk if cur else None, previous_gross=cur.gross if cur else common.ZERO, new_gross=s.gross,
        annual_increase=increase, reason=(reason or "")[:300], requested_by=actor)
    common.log(employee.company, actor, "salary_revision", rev.pk, Action.CREATED, new={
        "employee": employee.name, "effective_from": rev.effective_from.isoformat(), "monthly_gross": str(rev.new_gross),
        "annual_increase": str(rev.annual_increase)}, reason="Sent for approval")
    _api().submit(employee.company, kind="salary_revision", source_id=str(rev.pk), title=title_of(rev), amount=rev.annual_increase,
                  requested_by=actor, summary=summary_of(rev), url=f"/hr/employees/{employee.pk}",
                  meta={"department": employee.department.name if employee.department_id else ""})
    rev.refresh_from_db()
    return rev


@transaction.atomic
def approve(rev, actor, note=""):
    rev = _lock(rev)
    if rev.status != Status.PENDING:
        raise ValidationError("Only a salary revision that is waiting for approval can be approved.")
    e = rev.employee
    if SalaryStructure.objects.filter(employee=e, effective_from=rev.effective_from).exists():
        raise ValidationError("A salary structure already starts on this date, so this revision cannot be applied.")
    s = SalaryStructure(employee=e, effective_from=rev.effective_from, created_by=rev.requested_by, notes=rev.structure.get("notes", ""))
    for f in SALARY_FIELDS:
        setattr(s, f, Decimal(rev.structure[f]))
    for f in FLAGS:
        setattr(s, f, bool(rev.structure[f]))
    s.save()
    rev.status, rev.decided_by, rev.decided_at, rev.decision_note, rev.result_structure_id = (
        Status.APPROVED, actor, _now(), (note or "").strip()[:300], s.pk)
    rev.save()
    common.log(e.company, actor, "salary_structure", s.pk, Action.CREATED, new=snap(s), reason="Salary revision approved")
    common.log(e.company, actor, "salary_revision", rev.pk, Action.UPDATED, previous={"status": "pending_approval"},
               new={"status": "approved"}, reason=rev.decision_note or "Approved")
    return rev


@transaction.atomic
def reject(rev, actor, reason):
    rev = _lock(rev)
    if rev.status != Status.PENDING:
        raise ValidationError("Only a salary revision that is waiting for approval can be rejected.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this salary revision."})
    rev.status, rev.decided_by, rev.decided_at, rev.decision_note = Status.REJECTED, actor, _now(), reason.strip()[:300]
    rev.save()
    common.log(rev.employee.company, actor, "salary_revision", rev.pk, Action.UPDATED, previous={"status": "pending_approval"},
               new={"status": "rejected"}, reason=f"Rejected: {reason.strip()}")
    return rev


def withdraw(rev, actor):
    rev = _lock(rev)
    if rev.status == Status.PENDING:
        rev.status, rev.decided_at = Status.WITHDRAWN, _now()
        rev.save()
        common.log(rev.employee.company, actor, "salary_revision", rev.pk, Action.UPDATED, previous={"status": "pending_approval"},
                   new={"status": "withdrawn"}, reason="Approval request withdrawn")
    return rev


@transaction.atomic
def take_back(rev, actor):
    rev = _lock(rev)
    if rev.status != Status.PENDING:
        raise ValidationError("Only a salary revision that is waiting for approval can be withdrawn.")
    api = _api()
    if api is not None:
        api.cancel_for_source("salary_revision", rev.pk, actor)
    return withdraw(rev, actor)


def resubmit(rev, actor):
    """A rejected or withdrawn request was sent again from the Approvals module: the same proposal waits once more."""
    rev = _lock(rev)
    if rev.status in (Status.REJECTED, Status.WITHDRAWN) and pending_for(rev.employee) is None:
        rev.status, rev.decided_at, rev.decided_by, rev.decision_note = Status.PENDING, None, None, ""
        rev.save()
        common.log(rev.employee.company, actor, "salary_revision", rev.pk, Action.UPDATED, new={"status": "pending_approval"},
                   reason="Sent for approval again")
    return rev
