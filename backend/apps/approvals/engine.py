"""The approval workflow: submit, decide, withdraw, resubmit, reassign, escalate.

Who may approve a step (the rules every function here follows):

* The approvers of a step are the active users with the step's role, or the one chosen user.
* The requester never approves their own request. Exception: when the requester is an administrator and there is
  nobody else who could approve the step (not even a Managing Partner or another administrator), the administrator may
  approve it themselves, so a one-person company is never stuck. The history records this.
* A step nobody else can approve falls back to the Managing Partners and administrators.
* When a step is past its escalation days, the Managing Partners and administrators may approve it too, and the request
  shows as "escalated".
* A step in "all" mode needs every approver; an escalation approver completes it on their own.
"""
import datetime
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.accounts import access
from apps.company.constants import DocType
from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit, numbering

from . import registry, rules
from .models import ApprovalDecision, ApprovalEvent, ApprovalRequest, ApprovalRuleStep, ApprovalStep

Status, Event, State = ApprovalRequest.Status, ApprovalEvent.Action, ApprovalStep.State
Action = CompanyProfileAuditEvent.Action
User = get_user_model()


class Conflict(APIException):
    status_code = 409
    default_detail = "This request has already been decided."
    default_code = "conflict"


def user_label(user):
    if user is None:
        return ""
    return (user.get_full_name() or user.get_username() or "")[:150]


def is_admin(user):
    return bool(user.is_superuser or user.role == access.ADMIN)


# --------------------------------------------------------------------------- who can approve

class Directory:
    """Role members, looked up once per call."""

    def __init__(self):
        self._roles = {}

    def role_members(self, role):
        if role not in self._roles:
            q = Q(role=role) | Q(is_superuser=True) if role == access.ADMIN else Q(role=role)
            self._roles[role] = list(User.objects.filter(q, is_active=True).order_by("first_name", "username"))
        return self._roles[role]

    def fallback(self):
        seen, out = set(), []
        for u in self.role_members(access.MANAGING_PARTNER) + self.role_members(access.ADMIN):
            if u.pk not in seen:
                seen.add(u.pk)
                out.append(u)
        return out


@dataclass
class StepInfo:
    step: object
    base: list = field(default_factory=list)       # the people the step is meant for
    extra: list = field(default_factory=list)      # also allowed because the step is overdue
    mode: str = "any"
    overdue: bool = False
    fallback: bool = False                         # nobody else could approve: Managing Partners / administrators
    self_approval: bool = False                    # the requester is the only possible approver (an administrator)

    @property
    def everyone(self):
        seen, out = set(), []
        for u in self.base + self.extra:
            if u.pk not in seen:
                seen.add(u.pk)
                out.append(u)
        return out


def deadline(step):
    """When the step becomes overdue, or None when it never escalates."""
    if step.escalate_after_days and step.started_at:
        return step.started_at + datetime.timedelta(days=step.escalate_after_days)
    return None


def is_overdue(step, now):
    d = deadline(step)
    return bool(d and step.state == State.ACTIVE and now >= d)


def step_info(step, req, now, directory):
    if step.approver_type == ApprovalRuleStep.ApproverType.USER:
        people = [step.user] if step.user_id and step.user.is_active else []
    else:
        people = directory.role_members(step.role)
    base = [u for u in people if u.pk != req.requested_by_id]
    info = StepInfo(step=step, mode=step.mode if step.approver_type == "role" else "any", overdue=is_overdue(step, now))
    if base:
        info.base = base
    else:
        info.mode = "any"
        others = [u for u in directory.fallback() if u.pk != req.requested_by_id]
        if others:
            info.base, info.fallback = others, True
        elif is_admin(req.requested_by):
            info.base, info.fallback, info.self_approval = [req.requested_by], True, True
    if info.overdue:
        base_ids = {u.pk for u in info.base}
        info.extra = [u for u in directory.fallback() if u.pk != req.requested_by_id and u.pk not in base_ids]
    return info


def active_step(req):
    for s in req.steps.all():
        if s.order == req.current_step:
            return s
    return None


def approved_by(step):
    return {d.approver_id for d in step.decisions.all() if d.decision == ApprovalDecision.Decision.APPROVED}


def can_act(user, req, now, directory, info=None):
    if not req.is_open:
        return False
    step = active_step(req)
    if step is None:
        return False
    info = info or step_info(step, req, now, directory)
    if user.pk in {d.approver_id for d in step.decisions.all()}:
        return False
    return user.pk in {u.pk for u in info.everyone}


def step_complete(info, step, approver, extra_ids=()):
    """Is the step finished once `approver`'s approval is counted?"""
    done = approved_by(step) | {approver.pk}
    base_ids = {u.pk for u in info.base}
    if done & {u.pk for u in info.extra}:
        return True
    if info.mode == "all":
        return base_ids <= done
    return bool(done & base_ids)


# --------------------------------------------------------------------------- helpers

def _fail(exc):
    if isinstance(exc, DjangoValidationError):
        msg = "; ".join(exc.messages)
    else:
        msg = str(exc)
    return ValidationError({"detail": msg})


def _amount(value):
    if value in (None, ""):
        return None
    try:
        amt = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValidationError({"amount": "Enter the amount as a number."})
    if amt < 0:
        raise ValidationError({"amount": "The amount cannot be negative."})
    return amt


def _log(req, action, actor, *, step=None, comment="", now=None):
    return ApprovalEvent.objects.create(
        request=req, action=action, actor=actor if getattr(actor, "pk", None) else None, actor_label=user_label(actor) or "System",
        step_order=step, comment=comment or "", at=now or timezone.now())


def _audit(req, actor, action, *, previous=None, new=None, reason=""):
    audit.log(req.company, actor, "approval_request", req.pk, action, previous=previous, new=new, reason=reason, category="approvals")


def _state(req):
    return {"status": req.status, "current_step": req.current_step}


def _run_handler(req, name):
    handler = registry.handler_for(req.kind)
    fn = getattr(handler, name, None) if handler else None
    if fn is None:
        return
    try:
        with transaction.atomic():
            fn(req)
    except APIException:
        raise
    except Exception as exc:  # the decision is rolled back by the caller's transaction
        message = "; ".join(exc.messages) if isinstance(exc, DjangoValidationError) else (str(exc) or exc.__class__.__name__)
        raise ValidationError({"detail": f"The {registry.kind_label(req.kind).lower()} could not be updated, so nothing was changed: {message}"})


def has_manage_code(user, kind):
    return any(user.has_code(c) for c in registry.manage_codes(kind) if c)


# --------------------------------------------------------------------------- submit

def submit(company, *, kind, source_id, title, amount=None, requested_by, summary="", url="", project_id=None, meta=None,
           previous=None, pre_hook=None, now=None):
    now = now or timezone.now()
    kind = (kind or "").strip()
    source_id = str(source_id or "").strip()
    errors = {}
    if not kind:
        errors["kind"] = "Say what is being approved."
    if not source_id:
        errors["source_id"] = "Say which record is being approved."
    if not (title or "").strip():
        errors["title"] = "Give the request a title."
    if errors:
        raise ValidationError(errors)
    amount = _amount(amount)
    with transaction.atomic():
        rules.ensure_seeded(company)
        if ApprovalRequest.objects.filter(company=company, kind=kind, source_id=source_id, status__in=ApprovalRequest.OPEN).exists():
            raise ValidationError({"detail": "This record is already waiting for approval."})
        rule = rules.match_rule(company, kind, amount, meta)
        if rule is not None:
            steps = [{"order": s.order, "approver_type": s.approver_type, "role": s.role, "user": s.user, "mode": s.mode,
                      "escalate_after_days": s.escalate_after_days} for s in rule.steps.all()]
        else:
            steps = [{"order": 1, "approver_type": "role", "role": access.MANAGING_PARTNER, "user": None, "mode": "any",
                      "escalate_after_days": None}]
        auto = rule is None and has_manage_code(requested_by, kind)
        try:
            number = numbering.allocate_number(company, DocType.APPROVAL, actor=requested_by,
                                               on_date=timezone.localtime(now).date()).reference_number
        except DjangoValidationError as exc:
            raise _fail(exc)
        try:
            with transaction.atomic():
                req = ApprovalRequest.objects.create(
                    company=company, reference_number=number, kind=kind, source_id=source_id, title=title.strip()[:250],
                    amount=amount, summary=summary or "", url=url or "", project_id=project_id, meta=meta or {},
                    requested_by=requested_by, rule=rule,
                    rule_name=rule.name if rule else ("Automatic approval" if auto else "No matching rule: Managing Partner or administrator"),
                    previous=previous, status=Status.PENDING, current_step=1, total_steps=len(steps), submitted_at=now)
        except IntegrityError:
            raise ValidationError({"detail": "This record is already waiting for approval."})
        for s in steps:
            ApprovalStep.objects.create(request=req, **s)
        _log(req, Event.RESUBMITTED if previous else Event.SUBMITTED, requested_by, now=now,
             comment=f"Rule: {req.rule_name}" + (f" (resubmission of {previous.reference_number})" if previous else ""))
        _audit(req, requested_by, Action.CREATED, new={"reference_number": number, "kind": kind, "source_id": source_id,
                                                      "title": req.title, "amount": str(amount) if amount is not None else None,
                                                      "rule": req.rule_name})
        if pre_hook:
            pre_hook(req)
        if auto:
            _finish(req, Status.APPROVED, requested_by, now, Event.AUTO_APPROVED,
                    "Approved automatically: no approval rule applies and the requester may approve this kind of record.")
        else:
            first = req.steps.get(order=1)
            first.state, first.started_at = State.ACTIVE, now
            first.save(update_fields=["state", "started_at"])
        return _fresh(req.pk)


def _fresh(pk):
    return ApprovalRequest.objects.prefetch_related("steps__decisions").get(pk=pk)


def _finish(req, status, actor, now, event, comment):
    before = _state(req)
    req.status, req.decided_at = status, now
    req.save(update_fields=["status", "decided_at", "updated_at"])
    _log(req, event, actor, step=req.current_step, comment=comment, now=now)
    _audit(req, actor, Action.UPDATED, previous=before, new=_state(req), reason=comment)
    if status == Status.APPROVED:
        _run_handler(req, "on_approved")
    elif status == Status.REJECTED:
        _run_handler(req, "on_rejected")


# --------------------------------------------------------------------------- decide

def _locked(pk, company=None, full=False):
    """The request row, locked until the surrounding transaction ends (two people deciding at once are serialised)."""
    try:
        qs = ApprovalRequest.objects.select_for_update(of=("self",)).filter(pk=pk)
        if company is not None:
            qs = qs.filter(company=company)
        if full:
            qs = qs.select_related("requested_by").prefetch_related("steps__decisions", "steps__user")
        req = qs.get()
    except (ApprovalRequest.DoesNotExist, ValueError, DjangoValidationError):
        raise NotFound("No such approval request.")
    return req


def decide(req_id, user, decision, comment="", *, now=None, company=None):
    now = now or timezone.now()
    comment = (comment or "").strip()
    if decision not in ("approve", "reject"):
        raise ValidationError({"decision": "Choose approve or reject."})
    if decision == "reject" and not comment:
        raise ValidationError({"comment": "Give a reason for rejecting."})
    with transaction.atomic():
        req = _locked(req_id, company, full=True)
        if not req.is_open:
            raise Conflict(f"This request is already {req.get_status_display().lower()}.")
        step = active_step(req)
        directory = Directory()
        info = step_info(step, req, now, directory)
        if any(d.approver_id == user.pk for d in step.decisions.all()):
            raise Conflict("You have already decided on this step.")
        if user.pk not in {u.pk for u in info.everyone}:
            raise PermissionDenied("You are not an approver for this step.")
        started = step.started_at or req.submitted_at
        hours = Decimal(str(max((now - started).total_seconds(), 0) / 3600)).quantize(Decimal("0.01"))
        ApprovalDecision.objects.create(
            request=req, step=step, approver=user, approver_label=user_label(user),
            decision=ApprovalDecision.Decision.APPROVED if decision == "approve" else ApprovalDecision.Decision.REJECTED,
            comment=comment, decided_at=now, hours_taken=hours)
        note = comment
        if info.self_approval:
            note = (comment + " " if comment else "") + "(Approved by the requester: no other approver is available.)"
        if decision == "reject":
            step.state, step.completed_at = State.REJECTED, now
            step.save(update_fields=["state", "completed_at"])
            _finish(req, Status.REJECTED, user, now, Event.REJECTED, comment)
            return _fresh(req.pk)
        if not step_complete(info, step, user):
            pending = len({u.pk for u in info.base} - approved_by(step) - {user.pk})
            _log(req, Event.STEP_APPROVED, user, step=step.order, now=now,
                 comment=(note + " " if note else "") + f"Waiting for {pending} more approver{'s' if pending != 1 else ''} in this step.")
            return _fresh(req.pk)
        step.state, step.completed_at = State.APPROVED, now
        step.save(update_fields=["state", "completed_at"])
        following = req.steps.filter(order__gt=step.order).order_by("order").first()
        if following is None:
            _finish(req, Status.APPROVED, user, now, Event.APPROVED, note)
        else:
            before = _state(req)
            following.state, following.started_at = State.ACTIVE, now
            following.save(update_fields=["state", "started_at"])
            req.current_step, req.status = following.order, Status.PENDING
            req.save(update_fields=["current_step", "status", "updated_at"])
            _log(req, Event.STEP_APPROVED, user, step=step.order, comment=note, now=now)
            _audit(req, user, Action.UPDATED, previous=before, new=_state(req), reason=note)
        return _fresh(req.pk)


# --------------------------------------------------------------------------- withdraw, cancel, resubmit, reassign

def withdraw(req_id, user, comment="", *, can_manage=False, now=None, company=None):
    now = now or timezone.now()
    with transaction.atomic():
        req = _locked(req_id, company)
        if req.requested_by_id != user.pk and not can_manage:
            raise PermissionDenied("Only the person who asked for approval can withdraw the request.")
        if not req.is_open:
            raise Conflict(f"This request is already {req.get_status_display().lower()}.")
        before = _state(req)
        req.status, req.decided_at = Status.CANCELLED, now
        req.save(update_fields=["status", "decided_at", "updated_at"])
        _log(req, Event.WITHDRAWN, user, step=req.current_step, comment=comment, now=now)
        _audit(req, user, Action.UPDATED, previous=before, new=_state(req), reason=comment or "Withdrawn")
        _run_handler(req, "on_cancelled")
        return _fresh(req.pk)


def cancel_for_source(kind, source_id, actor, *, now=None):
    """The source record was voided or deleted: close its open request without calling the module back."""
    now = now or timezone.now()
    with transaction.atomic():
        req = ApprovalRequest.objects.select_for_update().filter(kind=kind, source_id=str(source_id), status__in=ApprovalRequest.OPEN).first()
        if req is None:
            return None
        before = _state(req)
        req.status, req.decided_at = Status.CANCELLED, now
        req.save(update_fields=["status", "decided_at", "updated_at"])
        _log(req, Event.CANCELLED, actor, step=req.current_step, comment="The record was cancelled or changed, so its approval was closed.", now=now)
        _audit(req, actor, Action.UPDATED, previous=before, new=_state(req), reason="Source record cancelled")
        return req


def resubmit(req_id, user, *, can_manage=False, now=None, company=None):
    now = now or timezone.now()
    with transaction.atomic():
        old = _locked(req_id, company)
        if old.requested_by_id != user.pk and not can_manage:
            raise PermissionDenied("Only the person who asked for approval can send it again.")
        if old.status not in (Status.REJECTED, Status.CANCELLED):
            raise ValidationError({"detail": "Only a rejected or withdrawn request can be sent again."})
        if ApprovalRequest.objects.filter(company=old.company, kind=old.kind, source_id=old.source_id, status__in=ApprovalRequest.OPEN).exists():
            raise ValidationError({"detail": "This record is already waiting for approval."})
        info = {"title": old.title, "amount": old.amount, "summary": old.summary, "url": old.url, "project_id": old.project_id}
        handler = registry.handler_for(old.kind)
        if handler and handler.describe:
            fresh = handler.describe(old.source_id)
            if fresh:
                info.update({k: fresh[k] for k in ("title", "amount", "summary", "url", "project_id") if k in fresh})
        hook = (lambda new: _run_handler(new, "on_resubmitted"))
        return submit(old.company, kind=old.kind, source_id=old.source_id, title=info["title"], amount=info["amount"],
                      requested_by=old.requested_by, summary=info["summary"] or "", url=info["url"] or "",
                      project_id=info["project_id"], meta=old.meta, previous=old, pre_hook=hook, now=now)


def reassign(req_id, actor, new_user, comment="", *, step_order=None, now=None, company=None):
    now = now or timezone.now()
    with transaction.atomic():
        req = _locked(req_id, company)
        if not req.is_open:
            raise Conflict(f"This request is already {req.get_status_display().lower()}.")
        if new_user is None or not new_user.is_active:
            raise ValidationError({"user": "Choose an active user."})
        if new_user.pk == req.requested_by_id:
            raise ValidationError({"user": "The person who asked for approval cannot be the approver."})
        order = step_order or req.current_step
        step = req.steps.filter(order=order, state__in=[State.ACTIVE, State.WAITING]).first()
        if step is None:
            raise ValidationError({"step": "That step has already been completed."})
        old = step.user.get_username() if step.approver_type == "user" and step.user_id else step.role
        step.approver_type, step.user, step.role, step.mode, step.delegated = "user", new_user, "", "any", True
        step.save()
        _log(req, Event.DELEGATED, actor, step=step.order, now=now,
             comment=f"Step {step.order} reassigned to {user_label(new_user)}." + (f" {comment}" if comment else ""))
        _audit(req, actor, Action.UPDATED, previous={"step": step.order, "approver": old},
               new={"step": step.order, "approver": new_user.get_username()}, reason=comment or "Reassigned")
        return _fresh(req.pk)


# --------------------------------------------------------------------------- escalation

def sweep(company, now=None):
    """Mark pending requests whose current step is past its escalation days as escalated. Returns how many changed."""
    now = now or timezone.now()
    changed = 0
    # One cheap query decides whether there is anything to do, so the summary the app polls takes no locks in the usual case.
    waiting = ApprovalStep.objects.filter(request__company=company, request__status=Status.PENDING, state=State.ACTIVE,
                                          escalate_after_days__isnull=False, started_at__isnull=False
                                          ).values_list("started_at", "escalate_after_days")
    if not any(now >= started + datetime.timedelta(days=days) for started, days in waiting):
        return 0
    with transaction.atomic():
        qs = ApprovalRequest.objects.select_for_update(of=("self",)).filter(company=company, status=Status.PENDING)
        for req in qs.prefetch_related("steps"):
            step = active_step(req)
            if step is not None and is_overdue(step, now):
                before = _state(req)
                req.status, req.escalated_at = Status.ESCALATED, now
                req.save(update_fields=["status", "escalated_at", "updated_at"])
                _log(req, Event.ESCALATED, None, step=step.order, now=now,
                     comment=f"Step {step.order} has been waiting more than {step.escalate_after_days} day(s). The Managing Partners and administrators can now approve it.")
                _audit(req, None, Action.UPDATED, previous=before, new=_state(req), reason="Escalated: waiting too long")
                changed += 1
    return changed


# --------------------------------------------------------------------------- queries

def open_requests(company):
    return ApprovalRequest.objects.filter(company=company, status__in=ApprovalRequest.OPEN).select_related(
        "requested_by").prefetch_related("steps__decisions", "steps__user")


def pending_for(user, company, now=None, directory=None):
    now = now or timezone.now()
    directory = directory or Directory()
    return [r for r in open_requests(company) if can_act(user, r, now, directory)]


def status_of(kind, source_id):
    req = ApprovalRequest.objects.filter(kind=kind, source_id=str(source_id)).order_by("-submitted_at").first()
    if req is None:
        return "none"
    return "pending" if req.status == Status.ESCALATED else req.status
