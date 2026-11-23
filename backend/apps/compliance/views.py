import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import access
from apps.company.api import current_company
from apps.company.models import CompanyProfileAuditEvent

from . import constants as C
from .models import CalendarItem, Contract, LegalCase, LegalEvent, Obligation, Policy
from .services import catalogue, common, filings, legal, penalty, reports, schedule

MAX_ROWS = 1000
VIEW, MANAGE = "compliance.view", "compliance.manage"


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def _safe(fn, *args, **kwargs):
    """Run a service; Django validation errors become the 400 responses DRF produces."""
    try:
        return fn(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages})


def _get(model, pk, company, label, **extra):
    try:
        obj = model.objects.filter(pk=pk, company=company, **extra).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise NotFound(f"No such {label}.")
    return obj


def _history(entity, pk, limit=20):
    rows = CompanyProfileAuditEvent.objects.filter(entity_type=entity, entity_id=pk).order_by("-occurred_at")[:limit]
    return [{"at": r.occurred_at, "who": r.actor_label, "action": r.action, "changed": r.changed_fields, "reason": r.reason,
             "previous": r.previous_values, "new": r.new_values} for r in rows]


def _data(request):
    data = request.data
    if not hasattr(data, "get"):
        raise ValidationError({"detail": "Send a JSON object."})
    return data


# ============================================================================ overview
class SummaryView(APIView):
    def get(self, request):
        need(request, VIEW)
        return Response(reports.summary(current_company(), request))


class DashboardView(APIView):
    def get(self, request):
        need(request, VIEW)
        return Response(_safe(reports.dashboard, current_company(), request, fy=request.query_params.get("fy") or None))


class RemindersView(APIView):
    def get(self, request):
        need(request, VIEW)
        raw = request.query_params.get("days", C.DEFAULT_HORIZON_DAYS)
        try:
            days = int(raw)
        except ValueError:
            raise ValidationError({"days": "Enter a whole number of days."})
        if not 0 <= days <= C.MAX_HORIZON_DAYS:
            raise ValidationError({"days": f"Use 0 to {C.MAX_HORIZON_DAYS} days."})
        rows = reports.reminders(current_company(), request, horizon=days)
        kind = request.query_params.get("kind")
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        return Response({"days": days, "count": len(rows), "results": rows})


class ExpiryWatchView(APIView):
    def get(self, request):
        need(request, VIEW)
        try:
            days = int(request.query_params.get("days", 90))
        except ValueError:
            raise ValidationError({"days": "Enter a whole number of days."})
        rows = reports.expiry_rows(current_company(), request.user, ahead_days=max(0, min(days, C.MAX_HORIZON_DAYS)))
        return Response({"count": len(rows), "results": rows})


class TaxEventsView(APIView):
    """Read-only tax due dates from the tax module, for the calendar. Empty (never an error) when the tax module is unavailable."""

    def get(self, request):
        need(request, VIEW)
        try:
            days = max(1, min(int(request.query_params.get("days", 90)), 400))
        except ValueError:
            raise ValidationError({"days": "Enter a whole number of days."})
        rows = reports.tax_rows(request, datetime.date.today(), days) if request.user.has_code("tax.view") else []
        return Response({"count": len(rows), "results": rows})


class RegistryView(APIView):
    def get(self, request):
        need(request, VIEW)
        return Response(reports.registry(current_company(), request.user))


class AssigneesView(APIView):
    """Active users who can be given a task, for the assignee drop-downs."""

    def get(self, request):
        need(request, VIEW)
        return Response([{"id": str(u.pk), "name": common.user_name(u), "role": u.role,
                          "role_label": dict(access.ROLES).get(u.role, u.role)} for u in common.assignable_users()])


class OptionsView(APIView):
    def get(self, request):
        need(request, VIEW)
        return Response({
            "categories": [{"value": k, "label": v} for k, v in C.CATEGORIES],
            "frequencies": [{"value": k, "label": v} for k, v in C.FREQUENCIES],
            "priorities": [{"value": k, "label": v} for k, v in C.PRIORITIES],
            "penalty_rules": [{"value": k, "label": v} for k, v in C.PENALTY_CHOICES],
            "event_types": [{"value": k, "label": v} for k, v in C.EVENT_TYPES],
            "contract_types": [{"value": k, "label": v} for k, v in C.CONTRACT_TYPES],
            "case_types": [{"value": k, "label": v} for k, v in C.CASE_TYPES],
            "party_roles": [{"value": k, "label": v} for k, v in C.PARTY_ROLES],
            "roles": [{"value": k, "label": v} for k, v in access.ROLES],
            "due_soon_days": C.DUE_SOON_DAYS, "default_reminder_days": C.DEFAULT_REMINDER_DAYS,
        })


# ============================================================================ obligations
class ObligationCollectionView(APIView):
    def get(self, request):
        need(request, VIEW)
        company = current_company()
        catalogue.seed_catalogue(company)
        p = request.query_params
        qs = Obligation.objects.filter(company=company).annotate(n=Count("items"))
        if p.get("category"):
            qs = qs.filter(category=p["category"])
        if p.get("frequency"):
            qs = qs.filter(frequency=p["frequency"])
        if p.get("enabled") in ("true", "false"):
            qs = qs.filter(enabled=p["enabled"] == "true")
        if p.get("q"):
            qs = qs.filter(Q(name__icontains=p["q"]) | Q(form_no__icontains=p["q"]) | Q(code__icontains=p["q"]) | Q(authority__icontains=p["q"]))
        rows = [catalogue.obligation_dict(o, item_count=o.n) for o in qs]
        return Response({"count": len(rows), "results": rows})

    @transaction.atomic
    def post(self, request):
        need(request, MANAGE)
        o = _safe(catalogue.create_obligation, current_company(), request.user, _data(request))
        return Response(catalogue.obligation_dict(o, item_count=0), status=http.HTTP_201_CREATED)


class ObligationDetailView(APIView):
    def _get(self, pk):
        return _get(Obligation, pk, current_company(), "obligation")

    def get(self, request, pk):
        need(request, VIEW)
        o = self._get(pk)
        return Response({**catalogue.obligation_dict(o, item_count=o.items.count()), "history": _history("compliance_obligation", o.pk)})

    @transaction.atomic
    def patch(self, request, pk):
        need(request, MANAGE)
        o = _safe(catalogue.update_obligation, self._get(pk), request.user, _data(request))
        return Response(catalogue.obligation_dict(o, item_count=o.items.count()))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, MANAGE)
        _safe(catalogue.delete_obligation, self._get(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class ObligationSeedView(APIView):
    def post(self, request):
        need(request, MANAGE)
        return Response({"created": catalogue.seed_catalogue(current_company())})


# ============================================================================ calendar items
def _status_q(key, today):
    soon = today + datetime.timedelta(days=C.DUE_SOON_DAYS)
    if key == "filed":
        return Q(status="filed")
    if key == "not_applicable":
        return Q(status="not_applicable")
    if key == "overdue":
        return Q(status="open", due_date__lt=today)
    if key == "due_soon":
        return Q(status="open", due_date__gte=today, due_date__lte=soon)
    if key == "upcoming":
        return Q(status="open", due_date__gt=soon)
    if key == "open":
        return Q(status="open")
    raise ValidationError({"status": "Use upcoming, due_soon, overdue, filed, not_applicable or open."})


def _item_qs(company):
    return CalendarItem.objects.filter(company=company).select_related("assignee", "obligation")


class ItemCollectionView(APIView):
    def get(self, request):
        need(request, VIEW)
        company = current_company()
        today = datetime.date.today()
        p = request.query_params
        qs = _item_qs(company)
        if p.get("fy"):
            _safe(schedule.parse_fy, p["fy"])
            qs = qs.filter(fy_label=p["fy"])
        if p.get("category"):
            qs = qs.filter(category=p["category"])
        if p.get("obligation"):
            qs = qs.filter(obligation_id=p["obligation"]) if _valid_uuid(p["obligation"]) else qs.none()
        if p.get("assignee") == "me":
            qs = qs.filter(assignee=request.user)
        elif p.get("assignee") == "none":
            qs = qs.filter(assignee__isnull=True)
        elif p.get("assignee"):
            qs = qs.filter(assignee_id=p["assignee"]) if _valid_uuid(p["assignee"]) else qs.none()
        if p.get("priority"):
            qs = qs.filter(priority=p["priority"])
        if p.get("month"):
            try:
                y, m = p["month"].split("-")
                start = datetime.date(int(y), int(m), 1)
            except ValueError:
                raise ValidationError({"month": "Use YYYY-MM."})
            qs = qs.filter(due_date__gte=start, due_date__lt=schedule.add_months(start, 1))
        if p.get("from"):
            qs = qs.filter(due_date__gte=common.parse_date(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(due_date__lte=common.parse_date(p["to"], "to"))
        if p.get("q"):
            qs = qs.filter(Q(title__icontains=p["q"]) | Q(form_no__icontains=p["q"]) | Q(period_label__icontains=p["q"])
                           | Q(ack_number__icontains=p["q"]) | Q(reference__icontains=p["q"]))
        counts_rows = [filings.item_dict(i, today) for i in qs[:MAX_ROWS]]
        totals = filings.totals(counts_rows)
        rows = counts_rows
        if p.get("status"):
            wanted = p["status"]
            _status_q(wanted, today)  # validates the name
            rows = [r for r in counts_rows if r["status_key"] == wanted or (wanted == "open" and r["status"] == "open")]
        return Response({"count": len(rows), "totals": totals, "results": rows, "today": today})

    @transaction.atomic
    def post(self, request):
        need(request, MANAGE)
        item = _safe(filings.create_item, current_company(), request.user, _data(request))
        item = _item_qs(item.company).get(pk=item.pk)
        return Response(filings.item_dict(item), status=http.HTTP_201_CREATED)


def _valid_uuid(value):
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


class GenerateView(APIView):
    def post(self, request):
        need(request, MANAGE)
        fy = _data(request).get("fy") or schedule.fy_for_date(datetime.date.today())
        return Response(_safe(filings.generate, current_company(), fy, request.user), status=http.HTTP_200_OK)


class ItemDetailView(APIView):
    def _get(self, pk, lock=False):
        company = current_company()
        if lock:
            item = None
            try:
                item = CalendarItem.objects.select_for_update(of=("self",)).select_related("assignee", "obligation").filter(
                    pk=pk, company=company).first()
            except (ValueError, DjangoValidationError):
                pass
            if item is None:
                raise NotFound("No such item.")
            return item
        return _get(CalendarItem, pk, company, "item")

    def get(self, request, pk):
        need(request, VIEW)
        item = _item_qs(current_company()).filter(pk=pk).first() if _valid_uuid(pk) else None
        if item is None:
            raise NotFound("No such item.")
        out = filings.item_dict(item)
        out["history"] = _history("compliance_item", item.pk)
        out["obligation"] = ({"id": str(item.obligation_id), "name": item.obligation.name, "due_rule_text": item.obligation.due_rule_text,
                              "authority": item.obligation.authority, "penalty_note": item.obligation.penalty_note}
                             if item.obligation_id else None)
        return Response(out)

    @transaction.atomic
    def patch(self, request, pk):
        need(request, MANAGE)
        item = _safe(filings.update_item, self._get(pk, lock=True), request.user, _data(request))
        return Response(filings.item_dict(item))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, MANAGE)
        _safe(filings.delete_item, self._get(pk, lock=True), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class _ItemAction(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, MANAGE)
        item = None
        try:
            item = CalendarItem.objects.select_for_update(of=("self",)).select_related("assignee", "obligation").filter(
                pk=pk, company=current_company()).first()
        except (ValueError, DjangoValidationError):
            pass
        if item is None:
            raise NotFound("No such item.")
        item = _safe(self.act, item, request.user, _data(request))
        return Response(filings.item_dict(item))


class ItemFileView(_ItemAction):
    def act(self, item, user, data):
        return filings.mark_filed(item, user, data)


class ItemNotApplicableView(_ItemAction):
    def act(self, item, user, data):
        return filings.mark_not_applicable(item, user, data.get("reason"))


class ItemReopenView(_ItemAction):
    def act(self, item, user, data):
        return filings.reopen(item, user, data.get("reason"))


class ItemBulkView(APIView):
    @transaction.atomic  # the rows are locked with SELECT ... FOR UPDATE, which needs a transaction
    def post(self, request):
        need(request, MANAGE)
        return Response(_safe(filings.bulk, current_company(), request.user, _data(request)))


# ============================================================================ penalties
class PenaltyRulesView(APIView):
    def get(self, request):
        need(request, VIEW)
        return Response(penalty.rules_for_display())


class PenaltyEstimateView(APIView):
    def post(self, request):
        need(request, VIEW)
        d = _data(request)
        rule = d.get("rule") or ""
        if d.get("item"):
            item = _get(CalendarItem, d["item"], current_company(), "item")
            rule = rule or item.penalty_rule
            due = item.due_date
        else:
            due = common.parse_date(d.get("due_date"), "due_date", required=True)
        if rule not in C.PENALTY_RULES:
            raise ValidationError({"rule": "Choose one of: " + ", ".join(C.PENALTY_RULES) + "."})
        filed = common.parse_date(d.get("filed_date"), "filed_date") or datetime.date.today()
        out = _safe(penalty.estimate, rule, due, filed, total_income=d.get("total_income"), tax_amount=d.get("tax_amount"),
                    nil_return=bool(d.get("nil_return")))
        return Response({**out, "due_date": due, "filed_date": filed, "note": "An estimate only. Check the amount on the official portal."})


# ============================================================================ legal events
class EventCollectionView(APIView):
    def get(self, request):
        need(request, VIEW)
        qs = LegalEvent.objects.filter(company=current_company())
        p = request.query_params
        if p.get("type"):
            qs = qs.filter(event_type=p["type"])
        if p.get("q"):
            qs = qs.filter(Q(title__icontains=p["q"]) | Q(form_no__icontains=p["q"]) | Q(srn__icontains=p["q"]) | Q(description__icontains=p["q"]))
        rows = [legal.event_dict(e) for e in qs[:MAX_ROWS]]
        return Response({"count": len(rows), "results": rows})

    @transaction.atomic
    def post(self, request):
        need(request, MANAGE)
        event, filing = _safe(legal.save_event, current_company(), request.user, _data(request))
        out = legal.event_dict(event)
        out["filing"] = filings.item_dict(_item_qs(event.company).get(pk=filing.pk)) if filing else None
        return Response(out, status=http.HTTP_201_CREATED)


class EventDetailView(APIView):
    def get(self, request, pk):
        need(request, VIEW)
        e = _get(LegalEvent, pk, current_company(), "event")
        return Response({**legal.event_dict(e), "history": _history("legal_event", e.pk)})

    @transaction.atomic
    def patch(self, request, pk):
        need(request, MANAGE)
        e = _get(LegalEvent, pk, current_company(), "event")
        e, _ = _safe(legal.save_event, e.company, request.user, _data(request), e)
        return Response(legal.event_dict(e))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, MANAGE)
        _safe(legal.delete_event, _get(LegalEvent, pk, current_company(), "event"), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class EventImportView(APIView):
    def post(self, request):
        need(request, MANAGE)
        return Response(_safe(legal.import_history, current_company(), request.user))


# ============================================================================ contracts
class ContractCollectionView(APIView):
    def get(self, request):
        need(request, VIEW)
        today = datetime.date.today()
        p = request.query_params
        qs = Contract.objects.filter(company=current_company()).select_related("owner")
        if p.get("type"):
            qs = qs.filter(contract_type=p["type"])
        if p.get("q"):
            qs = qs.filter(Q(title__icontains=p["q"]) | Q(counterparty__icontains=p["q"]) | Q(reference__icontains=p["q"]))
        if p.get("customer") and _valid_uuid(p["customer"]):
            qs = qs.filter(customer_id=p["customer"])
        if p.get("project") and _valid_uuid(p["project"]):
            qs = qs.filter(project_id=p["project"])
        rows = [legal.contract_dict(c, today) for c in qs[:MAX_ROWS]]
        counts = {k: 0 for k in legal.CONTRACT_STATE_LABELS}
        for r in rows:
            counts[r["state"]] += 1
        if p.get("state"):
            rows = [r for r in rows if r["state"] == p["state"]]
        rows.sort(key=lambda r: (r["current_expiry"] is None, r["current_expiry"] or today, r["title"]))
        return Response({"count": len(rows), "totals": counts, "results": rows})

    @transaction.atomic
    def post(self, request):
        need(request, MANAGE)
        c = _safe(legal.save_contract, current_company(), request.user, _data(request))
        return Response(legal.contract_dict(c), status=http.HTTP_201_CREATED)


class ContractDetailView(APIView):
    def _get(self, pk):
        c = None
        try:
            c = Contract.objects.select_related("owner").filter(pk=pk, company=current_company()).first()
        except (ValueError, DjangoValidationError):
            pass
        if c is None:
            raise NotFound("No such contract.")
        return c

    def get(self, request, pk):
        need(request, VIEW)
        c = self._get(pk)
        return Response({**legal.contract_dict(c), "history": _history("contract", c.pk)})

    @transaction.atomic
    def patch(self, request, pk):
        need(request, MANAGE)
        c = _safe(legal.save_contract, current_company(), request.user, _data(request), self._get(pk))
        return Response(legal.contract_dict(c))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, MANAGE)
        _safe(legal.delete_contract, self._get(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class ContractRenewView(ContractDetailView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, MANAGE)
        return Response(legal.contract_dict(_safe(legal.renew_contract, self._get(pk), request.user, _data(request))))


class ContractTerminateView(ContractDetailView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, MANAGE)
        return Response(legal.contract_dict(_safe(legal.terminate_contract, self._get(pk), request.user, _data(request))))


# ============================================================================ notices and cases
class CaseCollectionView(APIView):
    def get(self, request):
        need(request, VIEW)
        today = datetime.date.today()
        p = request.query_params
        qs = LegalCase.objects.filter(company=current_company()).select_related("owner")
        if p.get("type"):
            qs = qs.filter(case_type=p["type"])
        if p.get("status") == "active":
            qs = qs.filter(status__in=[LegalCase.Status.OPEN, LegalCase.Status.IN_PROGRESS])
        elif p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(Q(title__icontains=p["q"]) | Q(case_number__icontains=p["q"]) | Q(counterparty__icontains=p["q"])
                           | Q(forum__icontains=p["q"]) | Q(lawyer__icontains=p["q"]) | Q(reference__icontains=p["q"]))
        all_rows = [legal.case_dict(c, today) for c in qs[:MAX_ROWS]]
        counts = _case_counts(all_rows)
        return Response({"count": len(all_rows), "totals": counts, "results": all_rows})

    @transaction.atomic
    def post(self, request):
        need(request, MANAGE)
        c = _safe(legal.save_case, current_company(), request.user, _data(request))
        return Response(legal.case_dict(c), status=http.HTTP_201_CREATED)


def _case_counts(rows):
    out = {s: 0 for s, _ in LegalCase.Status.choices}
    for r in rows:
        out[r["status"]] += 1
    return out


class CaseDetailView(APIView):
    def _get(self, pk):
        c = None
        try:
            c = LegalCase.objects.select_related("owner").filter(pk=pk, company=current_company()).first()
        except (ValueError, DjangoValidationError):
            pass
        if c is None:
            raise NotFound("No such matter.")
        return c

    def get(self, request, pk):
        need(request, VIEW)
        c = self._get(pk)
        return Response({**legal.case_dict(c), "history": _history("legal_case", c.pk)})

    @transaction.atomic
    def patch(self, request, pk):
        need(request, MANAGE)
        c = _safe(legal.save_case, current_company(), request.user, _data(request), self._get(pk))
        return Response(legal.case_dict(c))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, MANAGE)
        _safe(legal.delete_case, self._get(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


# ============================================================================ policies
class PolicyCollectionView(APIView):
    def get(self, request):
        need(request, VIEW)
        p = request.query_params
        qs = Policy.objects.filter(company=current_company()).select_related("owner")
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("q"):
            qs = qs.filter(Q(name__icontains=p["q"]) | Q(category__icontains=p["q"]) | Q(reference__icontains=p["q"]))
        rows = [legal.policy_dict(x, request.user) for x in qs[:MAX_ROWS]]
        return Response({"count": len(rows), "results": rows})

    @transaction.atomic
    def post(self, request):
        need(request, MANAGE)
        x = _safe(legal.save_policy, current_company(), request.user, _data(request))
        return Response(legal.policy_dict(x, request.user), status=http.HTTP_201_CREATED)


class PolicyDetailView(APIView):
    def _get(self, pk):
        x = None
        try:
            x = Policy.objects.select_related("owner").filter(pk=pk, company=current_company()).first()
        except (ValueError, DjangoValidationError):
            pass
        if x is None:
            raise NotFound("No such policy.")
        return x

    def get(self, request, pk):
        need(request, VIEW)
        x = self._get(pk)
        return Response({**legal.policy_dict(x, request.user), "history": _history("compliance_policy", x.pk)})

    @transaction.atomic
    def patch(self, request, pk):
        need(request, MANAGE)
        x = _safe(legal.save_policy, current_company(), request.user, _data(request), self._get(pk))
        return Response(legal.policy_dict(x, request.user))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, MANAGE)
        _safe(legal.delete_policy, self._get(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class PolicyAcknowledgeView(PolicyDetailView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, VIEW)
        x = self._get(pk)
        _safe(legal.acknowledge_policy, x, request.user)
        return Response(legal.policy_dict(x, request.user))
