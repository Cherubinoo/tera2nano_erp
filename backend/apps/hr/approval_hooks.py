"""Connects leave requests and payroll runs to the approvals module when it is installed.

Sending a record for approval sets it to `pending_approval` and calls `approvals.submit(...)` in the same transaction; if no
rule applies and the requester may approve it, the approvals module approves it at once and the callbacks below have already
run. Without the approvals module HR works on its own: leave is decided by the manager or HR, a payroll run by hr.payroll.
"""
from django.core.exceptions import ValidationError

from .models import LeaveRequest, PayrollRun, SalaryRevision


def _api():
    try:
        from apps.approvals import api
    except ImportError:
        return None
    return api if hasattr(api, "submit") and hasattr(api, "register_handler") else None


def managed():
    """Whether decisions on payroll runs must go through the shared approvals workflow."""
    return _api() is not None


def close_request(kind, source_id, actor):
    """The record was decided, withdrawn or voided here: close its open approval request (the module is not called back)."""
    api = _api()
    if api is not None:
        api.cancel_for_source(kind, str(source_id), actor)


# --------------------------------------------------------------------------- what the approvals module needs to know

def _last_decision(request_obj):
    return request_obj.decisions.select_related("approver").order_by("-decided_at").first()


def _decider(request_obj):
    d = _last_decision(request_obj)
    return d.approver if d is not None else request_obj.requested_by


def _note(request_obj):
    d = _last_decision(request_obj)
    return d.comment if d is not None else ""


def _leave(request_obj):
    return LeaveRequest.objects.filter(pk=getattr(request_obj, "source_id", None)).first()


def _run(request_obj):
    return PayrollRun.objects.filter(pk=getattr(request_obj, "source_id", None)).first()


def _leave_info(r):
    who = r.employee.name
    span = r.start_date.isoformat() if r.start_date == r.end_date else f"{r.start_date.isoformat()} to {r.end_date.isoformat()}"
    return {"title": f"Leave: {who}, {r.days} day(s)", "amount": None,
            "summary": f"{r.leave_type.name}, {span}" + (f". {r.reason}" if r.reason else ""),
            "url": f"/hr/leave?open={r.pk}", "project_id": None}


def _run_info(run):
    return {"title": f"Payroll for {run.month.strftime('%B %Y')} ({run.number})", "amount": run.total_net,
            "summary": f"{run.employee_count} employees, net pay {run.total_net}, cost to the company {run.total_employer_cost}",
            "url": f"/hr/payroll/runs/{run.pk}", "project_id": None}


# --------------------------------------------------------------------------- submit (called by the HR services)

def submit_leave(req, actor):
    api = _api()
    if api is not None:
        info = _leave_info(req)
        api.submit(req.company, kind="leave_request", source_id=str(req.pk), requested_by=actor, **info)


def submit_run(run, actor):
    api = _api()
    if api is not None:
        api.submit(run.company, kind="payroll_run", source_id=str(run.pk), requested_by=actor, **_run_info(run))


# --------------------------------------------------------------------------- callbacks

def leave_approved(request_obj):
    from .services import leave
    r = _leave(request_obj)
    if r is not None and r.status == LeaveRequest.Status.PENDING:
        leave.approve(r, _decider(request_obj), _note(request_obj), trusted=True)


def leave_rejected(request_obj):
    from .services import leave
    r = _leave(request_obj)
    if r is not None and r.status == LeaveRequest.Status.PENDING:
        leave.reject(r, _decider(request_obj), _note(request_obj) or "Rejected in approvals", trusted=True)


def leave_cancelled(request_obj):
    from .services import leave
    r = _leave(request_obj)
    if r is not None:
        leave.withdraw(r, request_obj.requested_by)


def run_approved(request_obj):
    from .services import payroll
    run = _run(request_obj)
    if run is not None and run.status == PayrollRun.Status.PENDING:
        payroll.approve(run, _decider(request_obj), trusted=True)


def run_rejected(request_obj):
    from .services import payroll
    run = _run(request_obj)
    if run is not None and run.status == PayrollRun.Status.PENDING:
        payroll.reject(run, _decider(request_obj), _note(request_obj) or "Rejected in approvals", trusted=True)


def run_cancelled(request_obj):
    from .services import payroll
    run = _run(request_obj)
    if run is not None:
        payroll.withdraw(run, request_obj.requested_by)


def describe_leave(source_id):
    try:
        r = LeaveRequest.objects.filter(pk=source_id).select_related("employee", "leave_type").first()
    except (ValueError, TypeError):
        r = None
    if r is None:
        return None
    return {**_leave_info(r), "requested_by_ok": lambda user: user.has_code("hr.manage") or (getattr(user, "employee", None) is not None
                                                                                              and user.employee.pk == r.employee_id)}


def describe_run(source_id):
    try:
        run = PayrollRun.objects.filter(pk=source_id).first()
    except (ValueError, TypeError):
        run = None
    if run is None:
        return None
    return {**_run_info(run), "requested_by_ok": lambda user: user.has_code("hr.payroll")}


# --------------------------------------------------------------------------- salary revisions

def _revision(request_obj):
    try:
        return SalaryRevision.objects.filter(pk=getattr(request_obj, "source_id", None)).first()
    except (ValueError, TypeError, ValidationError):
        return None


def revision_approved(request_obj):
    from .services import salary
    rev = _revision(request_obj)
    if rev is not None and rev.status == SalaryRevision.Status.PENDING:
        salary.approve(rev, _decider(request_obj), _note(request_obj))


def revision_rejected(request_obj):
    from .services import salary
    rev = _revision(request_obj)
    if rev is not None and rev.status == SalaryRevision.Status.PENDING:
        salary.reject(rev, _decider(request_obj), _note(request_obj) or "Rejected in approvals")


def revision_cancelled(request_obj):
    from .services import salary
    rev = _revision(request_obj)
    if rev is not None:
        salary.withdraw(rev, request_obj.requested_by)


def revision_resubmitted(request_obj):
    from .services import salary
    rev = _revision(request_obj)
    if rev is not None:
        salary.resubmit(rev, request_obj.requested_by)


def describe_revision(source_id):
    from .services import salary
    try:
        rev = SalaryRevision.objects.filter(pk=source_id).select_related("employee").first()
    except (ValueError, TypeError, ValidationError):
        rev = None
    if rev is None:
        return None
    return {"title": salary.title_of(rev), "amount": rev.annual_increase, "summary": salary.summary_of(rev),
            "url": f"/hr/employees/{rev.employee_id}", "project_id": None,
            "requested_by_ok": lambda user: user.has_code("hr.payroll")}


def register():
    api = _api()
    if api is None:
        return
    api.register_handler("leave_request", on_approved=leave_approved, on_rejected=leave_rejected, on_cancelled=leave_cancelled,
                         label="Leave request", required_codes=("hr.manage",), describe=describe_leave)
    api.register_handler("payroll_run", on_approved=run_approved, on_rejected=run_rejected, on_cancelled=run_cancelled,
                         label="Payroll run", required_codes=("hr.payroll",), describe=describe_run)
    api.register_handler("salary_revision", on_approved=revision_approved, on_rejected=revision_rejected, on_cancelled=revision_cancelled,
                         on_resubmitted=revision_resubmitted, label="Salary revision", required_codes=("hr.payroll",),
                         describe=describe_revision)
