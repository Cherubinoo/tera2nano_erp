import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import status as http
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company
from apps.company.models import CompanyProfileAuditEvent

from apps.company.services import audit

from . import analytics, engine, registry, rules
from .models import APPROVER_ROLES, ROLE_LABELS, ApprovalRequest, ApprovalRule

User = get_user_model()
Status = ApprovalRequest.Status
Action = CompanyProfileAuditEvent.Action
MAX_ROWS = 2000
STATUS_FILTERS = {s for s, _ in Status.choices}


def need(request, *codes):
    if not any(request.user.has_code(c) for c in codes):
        raise PermissionDenied("Your role does not allow this.")


def can_view_all(user):
    return user.has_code("approvals.view") or user.has_code("approvals.manage")


def can_reassign(user):
    return user.has_code("approvals.manage") or user.has_code("hr.manage")


def person(user):
    return {"id": str(user.pk), "name": engine.user_label(user), "role": user.role, "role_label": ROLE_LABELS.get(user.role, user.role)} if user else None


# --------------------------------------------------------------------------- serialisers

def step_who(step, info=None):
    if step.approver_type == "user" and step.user_id:
        return engine.user_label(step.user)
    return ROLE_LABELS.get(step.role, step.role)


def request_dict(req, user, now, directory, *, detail=False):
    step = engine.active_step(req) if req.is_open else None
    info = engine.step_info(step, req, now, directory) if step else None
    acts = bool(step and engine.can_act(user, req, now, directory, info))
    data = {
        "id": str(req.pk), "reference_number": req.reference_number, "kind": req.kind, "kind_label": registry.kind_label(req.kind),
        "source_id": req.source_id, "title": req.title, "amount": str(req.amount) if req.amount is not None else None,
        "summary": req.summary, "url": req.url, "status": req.status, "status_label": req.get_status_display(),
        "current_step": req.current_step, "total_steps": req.total_steps, "requested_by": person(req.requested_by),
        "rule_name": req.rule_name, "submitted_at": req.submitted_at, "decided_at": req.decided_at,
        "escalated_at": req.escalated_at, "age_hours": analytics.request_age_hours(req, now),
        "overdue": bool(info and info.overdue), "can_act": acts,
        "can_withdraw": req.is_open and (req.requested_by_id == user.pk or user.has_code("approvals.manage")),
        "can_resubmit": req.status in (Status.REJECTED, Status.CANCELLED)
                        and (req.requested_by_id == user.pk or user.has_code("approvals.manage")),
        "can_reassign": req.is_open and can_reassign(user),
        "waiting_on": [engine.user_label(u) for u in info.base] if info else [],
        "waiting_on_role": step_who(step) if step else "",
        "previous": req.previous.reference_number if req.previous_id else None,
    }
    if not detail:
        return data
    steps = []
    for s in req.steps.all():
        sinfo = engine.step_info(s, req, now, directory) if s.state in ("active", "waiting") else None
        due = engine.deadline(s)
        steps.append({
            "order": s.order, "approver_type": s.approver_type, "role": s.role, "who": step_who(s),
            "user": str(s.user_id) if s.user_id else None, "mode": s.mode, "state": s.state, "delegated": s.delegated,
            "escalate_after_days": s.escalate_after_days, "started_at": s.started_at, "completed_at": s.completed_at,
            "due_at": due, "overdue": bool(sinfo and sinfo.overdue and s.state == "active"),
            "approvers": [engine.user_label(u) for u in sinfo.base] if sinfo else [],
            "decisions": [{"approver": d.approver_label, "decision": d.decision, "comment": d.comment, "decided_at": d.decided_at,
                           "hours_taken": float(d.hours_taken)} for d in s.decisions.all()],
        })
    data["steps"] = steps
    data["events"] = [{"id": e.pk, "action": e.action, "action_label": e.get_action_display(), "actor": e.actor_label,
                       "step_order": e.step_order, "comment": e.comment, "at": e.at} for e in req.events.all()]
    data["resubmissions"] = [r.reference_number for r in req.resubmissions.all()]
    return data


def involved(user, req, now, directory):
    """May this user open the request? The requester, anyone who may see every approval, and anyone who is or was an approver."""
    if req.requested_by_id == user.pk or can_view_all(user):
        return True
    for s in req.steps.all():
        if any(d.approver_id == user.pk for d in s.decisions.all()):
            return True
        if s.state in ("active", "waiting"):
            if user.pk in {u.pk for u in engine.step_info(s, req, now, directory).everyone}:
                return True
        elif (s.approver_type == "user" and s.user_id == user.pk) or (s.approver_type == "role" and s.role == user.role):
            return True
    return False


def _load(pk, company):
    try:
        return ApprovalRequest.objects.select_related("requested_by", "previous").prefetch_related(
            "steps__decisions", "steps__user", "events", "resubmissions").get(pk=pk, company=company)
    except (ApprovalRequest.DoesNotExist, ValueError, DjangoValidationError):
        raise NotFound("No such approval request.")


def _filtered(qs, p):
    if p.get("kind"):
        qs = qs.filter(kind=p["kind"])
    st = p.get("status")
    if st == "open":
        qs = qs.filter(status__in=ApprovalRequest.OPEN)
    elif st in STATUS_FILTERS:
        qs = qs.filter(status=st)
    for key, op in (("min_amount", "gte"), ("max_amount", "lte")):
        if p.get(key):
            try:
                qs = qs.filter(**{f"amount__{op}": Decimal(p[key])})
            except InvalidOperation:
                raise ValidationError({key: "Enter a number."})
    if p.get("older_than_days"):
        try:
            qs = qs.filter(submitted_at__lte=timezone.now() - datetime.timedelta(days=int(p["older_than_days"])))
        except ValueError:
            raise ValidationError({"older_than_days": "Enter a whole number of days."})
    if p.get("q"):
        q = p["q"].strip()
        qs = qs.filter(Q(reference_number__icontains=q) | Q(title__icontains=q) | Q(requested_by__username__icontains=q)
                       | Q(requested_by__first_name__icontains=q) | Q(requested_by__last_name__icontains=q) | Q(source_id__icontains=q))
    return qs


# --------------------------------------------------------------------------- inbox and lists

class SummaryView(APIView):
    """Counts for the sidebar and the page tabs. Open to every signed-in user."""

    def get(self, request):
        company = current_company()
        now = timezone.now()
        engine.sweep(company, now)
        mine = engine.pending_for(request.user, company, now)
        return Response({
            "pending_for_me": len(mine),
            "overdue_for_me": sum(1 for r in mine if r.status == Status.ESCALATED),
            "my_open_requests": ApprovalRequest.objects.filter(company=company, requested_by=request.user, status__in=ApprovalRequest.OPEN).count(),
        })


class KindsView(APIView):
    def get(self, request):
        return Response({"kinds": registry.all_kinds(), "roles": [{"value": v, "label": l} for v, l in APPROVER_ROLES]})


class UsersView(APIView):
    """Active users who can be picked as an approver or a delegate."""

    def get(self, request):
        need(request, "approvals.manage", "approvals.view", "hr.manage")
        users = User.objects.filter(is_active=True).order_by("first_name", "username")
        return Response([person(u) for u in users])


class RequestCollectionView(APIView):
    def get(self, request):
        company = current_company()
        user = request.user
        p = request.query_params
        scope = p.get("scope", "inbox")
        now = timezone.now()
        if scope not in ("inbox", "mine", "all"):
            raise ValidationError({"scope": "Use inbox, mine or all."})
        if scope == "all":
            need(request, "approvals.view", "approvals.manage")
        engine.sweep(company, now)
        qs = ApprovalRequest.objects.filter(company=company).select_related("requested_by", "previous").prefetch_related(
            "steps__decisions", "steps__user")
        if scope == "mine":
            qs = qs.filter(requested_by=user)
        elif scope == "inbox":
            qs = qs.filter(status__in=ApprovalRequest.OPEN)
        qs = _filtered(qs, p).order_by("submitted_at" if scope == "inbox" else "-submitted_at")
        directory = engine.Directory()
        try:
            limit = min(max(int(p.get("limit", 100)), 1), MAX_ROWS)
            offset = max(int(p.get("offset", 0)), 0)
        except ValueError:
            raise ValidationError({"limit": "Use whole numbers."})
        if scope == "inbox":
            rows = [r for r in qs if engine.can_act(user, r, now, directory)]
            count = len(rows)
            total = sum((r.amount or Decimal(0)) for r in rows)
            rows = rows[offset:offset + limit]
        else:
            count = qs.count()
            total = qs.aggregate(t=Sum("amount"))["t"] or Decimal(0)
            rows = list(qs[offset:offset + limit])
        return Response({"results": [request_dict(r, user, now, directory) for r in rows], "count": count, "total_amount": str(total)})


class RequestDetailView(APIView):
    def get(self, request, pk):
        company = current_company()
        req = _load(pk, company)
        now = timezone.now()
        directory = engine.Directory()
        if not involved(request.user, req, now, directory):
            raise NotFound("No such approval request.")
        return Response(request_dict(req, request.user, now, directory, detail=True))


class _ActionView(APIView):
    def respond(self, request, req):
        now = timezone.now()
        req = _load(req.pk, req.company)
        return Response(request_dict(req, request.user, now, engine.Directory(), detail=True))

    def visible(self, request, pk):
        req = _load(pk, current_company())
        if not involved(request.user, req, timezone.now(), engine.Directory()):
            raise NotFound("No such approval request.")
        return req


class DecideView(_ActionView):
    decision = "approve"

    def post(self, request, pk):
        req = self.visible(request, pk)
        done = engine.decide(req.pk, request.user, self.decision, request.data.get("comment", ""), company=req.company)
        return self.respond(request, done)


class ApproveView(DecideView):
    decision = "approve"


class RejectView(DecideView):
    decision = "reject"


class WithdrawView(_ActionView):
    def post(self, request, pk):
        req = self.visible(request, pk)
        done = engine.withdraw(req.pk, request.user, request.data.get("comment", ""),
                               can_manage=request.user.has_code("approvals.manage"), company=req.company)
        return self.respond(request, done)


class ResubmitView(_ActionView):
    def post(self, request, pk):
        req = self.visible(request, pk)
        done = engine.resubmit(req.pk, request.user, can_manage=request.user.has_code("approvals.manage"), company=req.company)
        return Response(request_dict(_load(done.pk, done.company), request.user, timezone.now(), engine.Directory(), detail=True),
                        status=http.HTTP_201_CREATED)


class ReassignView(_ActionView):
    def post(self, request, pk):
        need(request, "approvals.manage", "hr.manage")
        req = self.visible(request, pk)
        target = None
        if request.data.get("user"):
            try:
                target = User.objects.filter(pk=request.data["user"], is_active=True).first()
            except (ValueError, DjangoValidationError):
                target = None
        if target is None:
            raise ValidationError({"user": "Choose an active user."})
        step = request.data.get("step")
        try:
            step = int(step) if step not in (None, "") else None
        except ValueError:
            raise ValidationError({"step": "Use a step number."})
        done = engine.reassign(req.pk, request.user, target, str(request.data.get("comment") or "").strip(), step_order=step,
                               company=req.company)
        return self.respond(request, done)


class BulkApproveView(APIView):
    def post(self, request):
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ValidationError({"ids": "Choose at least one request."})
        if len(ids) > 100:
            raise ValidationError({"ids": "Approve at most 100 requests at a time."})
        company = current_company()
        comment = str(request.data.get("comment") or "").strip()
        approved, failed = [], []
        for pk in ids:
            try:
                with transaction.atomic():
                    req = engine.decide(pk, request.user, "approve", comment, company=company)
                approved.append(req.reference_number)
            except (APIException, DjangoValidationError) as exc:
                detail = exc.detail if isinstance(exc, APIException) else exc.messages
                text = detail.get("detail") if isinstance(detail, dict) and "detail" in detail else detail
                if isinstance(text, (list, tuple)) and text:
                    text = text[0]
                failed.append({"id": str(pk), "error": str(text)})
        return Response({"approved": len(approved), "references": approved, "failed": failed})


class SubmitView(APIView):
    """Submit a record for approval from its page. Only kinds whose owning module registered a `describe` function."""

    def post(self, request):
        kind = str(request.data.get("kind") or "").strip()
        source_id = str(request.data.get("source_id") or "").strip()
        if not kind or not source_id:
            raise ValidationError({"kind": "Say what to submit." if not kind else None, "source_id": "Say which record." if not source_id else None})
        handler = registry.handler_for(kind)
        if handler is None or handler.describe is None:
            raise ValidationError({"kind": "This kind of record cannot be submitted from here."})
        info = handler.describe(source_id)
        if not info:
            raise NotFound("No such record.")
        ok = info.get("requested_by_ok")
        if ok is not None and not ok(request.user):
            raise PermissionDenied("Your role does not allow you to submit this.")
        with transaction.atomic():
            req = engine.submit(current_company(), kind=kind, source_id=source_id, title=info.get("title") or registry.kind_label(kind),
                                amount=info.get("amount"), requested_by=request.user, summary=info.get("summary") or "",
                                url=info.get("url") or "", project_id=info.get("project_id"))
        return Response(request_dict(_load(req.pk, req.company), request.user, timezone.now(), engine.Directory()),
                        status=http.HTTP_201_CREATED)


class StatusView(APIView):
    """Where the latest request for a record stands (for the badge other modules show). Open to every signed-in user."""

    def get(self, request):
        kind, source_id = request.query_params.get("kind", ""), request.query_params.get("source_id", "")
        req = ApprovalRequest.objects.filter(kind=kind, source_id=str(source_id)).order_by("-submitted_at").first()
        if req is None:
            return Response({"status": "none", "id": None, "reference_number": None, "can_view": False})
        now = timezone.now()
        directory = engine.Directory()
        req = _load(req.pk, req.company)
        return Response({"status": "pending" if req.status == Status.ESCALATED else req.status, "status_label": req.get_status_display(),
                         "id": str(req.pk), "reference_number": req.reference_number,
                         "can_view": involved(request.user, req, now, directory)})


class DashboardView(APIView):
    def get(self, request):
        need(request, "approvals.view", "approvals.manage")
        return Response(analytics.dashboard(current_company()))


# --------------------------------------------------------------------------- rules

def rule_dict(rule, open_count=0):
    return {
        "id": str(rule.pk), "kind": rule.kind, "kind_label": registry.kind_label(rule.kind), "name": rule.name,
        "description": rule.description, "priority": rule.priority, "is_active": rule.is_active,
        "amount_from": str(rule.amount_from) if rule.amount_from is not None else None,
        "amount_to": str(rule.amount_to) if rule.amount_to is not None else None,
        "project_text": rule.project_text, "department_text": rule.department_text, "open_requests": open_count,
        "steps": [{
            "order": s.order, "approver_type": s.approver_type, "role": s.role, "user": str(s.user_id) if s.user_id else None,
            "who": engine.user_label(s.user) if s.approver_type == "user" and s.user_id else ROLE_LABELS.get(s.role, s.role),
            "mode": s.mode, "escalate_after_days": s.escalate_after_days,
        } for s in rule.steps.all()],
    }


def _rule(pk, company):
    try:
        return ApprovalRule.objects.prefetch_related("steps__user").get(pk=pk, company=company)
    except (ApprovalRule.DoesNotExist, ValueError, DjangoValidationError):
        raise NotFound("No such rule.")


class RuleCollectionView(APIView):
    def get(self, request):
        need(request, "approvals.view", "approvals.manage")
        company = current_company()
        rules.ensure_seeded(company)
        open_counts = {r["rule"]: r["n"] for r in ApprovalRequest.objects.filter(company=company, status__in=ApprovalRequest.OPEN).values("rule").annotate(n=Count("id"))}
        qs = ApprovalRule.objects.filter(company=company).prefetch_related("steps__user")
        if request.query_params.get("kind"):
            qs = qs.filter(kind=request.query_params["kind"])
        return Response([rule_dict(r, open_counts.get(r.pk, 0)) for r in qs])

    @transaction.atomic
    def post(self, request):
        need(request, "approvals.manage")
        company = current_company()
        rules.ensure_seeded(company)
        data = dict(request.data)
        if "priority" not in data or data["priority"] in (None, ""):
            data["priority"] = (ApprovalRule.objects.filter(company=company).order_by("-priority").values_list("priority", flat=True).first() or 0) + 10
        rule = rules.save_rule(company, data)
        rule = _rule(rule.pk, company)
        audit.log(company, request.user, "approval_rule", rule.pk, Action.CREATED, new=rules.rule_snapshot(rule), category="approvals")
        return Response(rule_dict(rule), status=http.HTTP_201_CREATED)


class RuleDetailView(APIView):
    def get(self, request, pk):
        need(request, "approvals.view", "approvals.manage")
        return Response(rule_dict(_rule(pk, current_company())))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "approvals.manage")
        company = current_company()
        rule = _rule(pk, company)
        before = rules.rule_snapshot(rule)
        rules.save_rule(company, request.data, rule)
        rule = _rule(pk, company)
        audit.log(company, request.user, "approval_rule", rule.pk, Action.UPDATED, previous=before, new=rules.rule_snapshot(rule),
                  reason=str(request.data.get("change_reason") or ""), category="approvals")
        return Response(rule_dict(rule))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "approvals.manage")
        company = current_company()
        rule = _rule(pk, company)
        before = rules.rule_snapshot(rule)
        rule.delete()
        audit.log(company, request.user, "approval_rule", pk, Action.DELETED, previous=before, new={}, category="approvals")
        return Response(status=http.HTTP_204_NO_CONTENT)


class RuleReorderView(APIView):
    """Set the order rules are tried in: body {"ids": [rule ids, first to last]}. Gaps of 10 are left between priorities."""

    @transaction.atomic
    def post(self, request):
        need(request, "approvals.manage")
        company = current_company()
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ValidationError({"ids": "Send the rule ids in the order wanted."})
        found = {str(r.pk): r for r in ApprovalRule.objects.filter(company=company, pk__in=ids)}
        if len(found) != len(set(map(str, ids))):
            raise ValidationError({"ids": "One of the rules was not found."})
        # Keep the slots the chosen rules already occupy: only their relative order changes.
        slots = sorted(r.priority for r in found.values())
        for pos, rid in enumerate(map(str, ids)):
            rule = found[rid]
            if rule.priority != slots[pos]:
                prev = rule.priority
                rule.priority = slots[pos]
                rule.save(update_fields=["priority", "updated_at"])
                audit.log(company, request.user, "approval_rule", rule.pk, Action.UPDATED, previous={"priority": prev},
                          new={"priority": rule.priority}, category="approvals")
        return Response({"ok": True})


class SeedRulesView(APIView):
    """Add the example rules if they were never created (they are created automatically the first time rules are opened)."""

    @transaction.atomic
    def post(self, request):
        need(request, "approvals.manage")
        return Response({"created": rules.seed_default_rules(current_company())})
