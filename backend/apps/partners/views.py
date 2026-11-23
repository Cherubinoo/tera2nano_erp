import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q, Sum
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting.models import Account
from apps.accounting.services import reports
from apps.company.api import current_company
from apps.company.constants import DocType
from apps.company.models import CompanyPartner, CompanyProfileAuditEvent

from . import services
from .models import PartnerAccount, PartnerTransaction, ProfitAllocation, ProfitShareSchedule

Action = CompanyProfileAuditEvent.Action
MAX_ROWS = 500


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def _uuid_get(model, pk, company, label, **extra):
    try:
        obj = model.objects.filter(pk=pk, company=company, **extra).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise NotFound(f"No such {label}.")
    return obj


def _partner(pk, company):
    return _uuid_get(CompanyPartner, pk, company, "partner")


def _date(value, label, required=False, default=None):
    if value in (None, ""):
        if required and default is None:
            raise ValidationError({label: "Enter a date."})
        return default
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({label: "Use a date in YYYY-MM-DD format."})


def can_view_all(request):
    return request.user.has_code("partners.view")


def mine(request, company):
    return services.partner_for_user(company, request.user)


def need_partner_access(request, partner, company):
    """Everyone with partners.view; otherwise only the partner whose own account this is."""
    if can_view_all(request):
        return
    me = mine(request, company)
    if me is None or me.pk != partner.pk:
        raise PermissionDenied("Your role does not allow this.")


def _body(request):
    return request.data if hasattr(request.data, "get") else {}


# --------------------------------------------------------------------------- lookups, settings

class LookupsView(APIView):
    def get(self, request):
        need(request, "partners.view")
        company = current_company()
        s = services.get_settings(company)
        banks = services.chart.money_accounts(company)
        return Response({
            "kinds": [{"value": v, "label": l, "money": v in PartnerTransaction.MONEY_KINDS} for v, l in PartnerTransaction.Kind.choices],
            "modes": [{"value": v, "label": l} for v, l in PartnerTransaction.Mode.choices],
            "money_accounts": [{"id": str(a.id), "name": f"{a.code} {a.name}", "subtype": a.subtype} for a in banks],
            "partners": [{"id": str(p.id), "name": p.full_name, "is_active": p.is_active}
                         for p in CompanyPartner.objects.filter(company=company).order_by("-is_active", "full_name")],
            "settings": _settings_dict(s), "pdf_available": DocType.PARTNER_TRANSACTION in DocType.PRINTABLE,
            "can_manage": request.user.has_code("partners.manage"),
        })


def _settings_dict(s):
    return {"withdrawal_approval_threshold": s.withdrawal_approval_threshold, "interest_on_capital_rate": s.interest_on_capital_rate,
            "loan_interest_rate": s.loan_interest_rate}


class SettingsView(APIView):
    def get(self, request):
        need(request, "partners.view")
        return Response(_settings_dict(services.get_settings(current_company())))

    @transaction.atomic
    def patch(self, request):
        need(request, "partners.manage")
        company = current_company()
        s = services.get_settings(company)
        before = _settings_dict(s)
        d = _body(request)
        if "withdrawal_approval_threshold" in d:
            try:
                v = services.Decimal(str(d["withdrawal_approval_threshold"]).strip())
            except Exception:
                raise ValidationError({"withdrawal_approval_threshold": "Enter a number."})
            if not v.is_finite() or v < 0 or v.as_tuple().exponent < -2 or v > services.Decimal("100000000000"):
                raise ValidationError({"withdrawal_approval_threshold": "Enter zero (no approval needed) or an amount with at most two decimals."})
            s.withdrawal_approval_threshold = v
        for f, label in (("interest_on_capital_rate", "Rate"), ("loan_interest_rate", "Rate")):
            if f in d:
                setattr(s, f, services.percent_from(d[f], f, label))
        s.save()
        if _settings_dict(s) != before:
            services.audit_event(company, request.user, "partner_settings", s.pk, Action.UPDATED,
                                 previous={k: str(v) for k, v in before.items()}, new={k: str(v) for k, v in _settings_dict(s).items()})
        return Response(_settings_dict(s))


class DashboardView(APIView):
    def get(self, request):
        need(request, "partners.view")
        return Response(services.dashboard(current_company()))


class MeView(APIView):
    """The signed-in user's own partner record and balances (any signed-in user; 404 when they are not a partner)."""

    def get(self, request):
        company = current_company()
        p = mine(request, company)
        if p is None:
            raise NotFound("You are not linked to a partner record.")
        row = next(r for r in services.partner_rows(company) if r["id"] == str(p.pk))
        return Response(row)


# --------------------------------------------------------------------------- partners

class PartnerCollectionView(APIView):
    def get(self, request):
        company = current_company()
        rows = services.partner_rows(company, _date(request.query_params.get("as_of"), "as_of"))
        if not can_view_all(request):
            me = mine(request, company)
            if me is None:
                raise PermissionDenied("Your role does not allow this.")
            rows = [r for r in rows if r["id"] == str(me.pk)]
        elif request.query_params.get("active") in ("true", "false"):
            rows = [r for r in rows if r["is_active"] == (request.query_params["active"] == "true")]
        q = (request.query_params.get("q") or "").strip().lower()
        if q:
            rows = [r for r in rows if q in r["name"].lower() or q in r["designation"].lower()]
        return Response({"results": rows, "count": len(rows)})


class PartnerDetailView(APIView):
    def get(self, request, pk):
        company = current_company()
        p = _partner(pk, company)
        need_partner_access(request, p, company)
        row = next(r for r in services.partner_rows(company) if r["id"] == str(p.pk))
        me = mine(request, company)
        recent = PartnerTransaction.objects.filter(company=company, partner=p).select_related("partner", "requested_by", "decided_by")[:10]
        loans = services.loan_ledger(company, datetime.date.today(), p)
        return Response({**row, "official_email": p.official_email if request.user.has_code("partners.manage") else "",
                         "appointment_date": p.appointment_date, "cessation_date": p.cessation_date,
                         "recent": [services.transaction_dict(t) for t in recent], "loan_ledger": loans["rows"][0] if loans["rows"] else None,
                         "notes": services.account_for(p).notes,
                         "is_self": bool(me and me.pk == p.pk)})

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "partners.manage")
        company = current_company()
        p = _partner(pk, company)
        acct = services.account_for(p)
        d = _body(request)
        before = {"agreed_capital": str(acct.agreed_capital), "user": str(acct.user_id or ""), "notes": acct.notes}
        if "agreed_capital" in d:
            v = d["agreed_capital"]
            acct.agreed_capital = services.ZERO if v in (None, "") else services.amount_from(v, "Agreed capital", "agreed_capital") \
                if str(v).strip() not in ("0", "0.00") else services.ZERO
        if "notes" in d:
            notes = (d["notes"] or "").strip()
            if len(notes) > 300:
                raise ValidationError({"notes": "Keep the notes to 300 characters."})
            acct.notes = notes
        if "user" in d:
            if d["user"] in (None, ""):
                acct.user = None
            else:
                from django.contrib.auth import get_user_model
                try:
                    acct.user = get_user_model().objects.get(pk=d["user"])
                except (get_user_model().DoesNotExist, ValueError, DjangoValidationError):
                    raise ValidationError({"user": "Choose a user."})
                if PartnerAccount.objects.filter(user=acct.user).exclude(pk=acct.pk).exists():
                    raise ValidationError({"user": "This user is already linked to another partner."})
        acct.save()
        after = {"agreed_capital": str(acct.agreed_capital), "user": str(acct.user_id or ""), "notes": acct.notes}
        if before != after:
            services.audit_event(company, request.user, "partner_account", acct.pk, Action.UPDATED, previous=before, new=after)
        return self.get(request, pk)


# --------------------------------------------------------------------------- transactions

def _tx_qs(company):
    return PartnerTransaction.objects.filter(company=company).select_related("partner", "requested_by", "decided_by")


class TransactionCollectionView(APIView):
    def get(self, request):
        company = current_company()
        qs = _tx_qs(company)
        if not can_view_all(request):
            me = mine(request, company)
            if me is None:
                raise PermissionDenied("Your role does not allow this.")
            qs = qs.filter(partner=me)
        p = request.query_params
        if p.get("partner"):
            qs = qs.filter(partner_id=p["partner"]) if _valid_uuid(p["partner"]) else qs.none()
        if p.get("kind"):
            qs = qs.filter(kind=p["kind"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("from"):
            qs = qs.filter(date__gte=_date(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(date__lte=_date(p["to"], "to"))
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(partner__full_name__icontains=p["q"]) | Q(reference__icontains=p["q"])
                           | Q(narration__icontains=p["q"]))
        posted_total = qs.filter(status="posted").aggregate(t=Sum("amount"))["t"] or services.ZERO
        count = qs.count()
        return Response({"results": [services.transaction_dict(t) for t in qs[:MAX_ROWS]], "count": count, "truncated": count > MAX_ROWS,
                         "posted_total": posted_total})

    @transaction.atomic
    def post(self, request):
        need(request, "partners.manage")
        company = current_company()
        d = _body(request)
        partner = _partner(d.get("partner"), company) if d.get("partner") else None
        if partner is None:
            raise ValidationError({"partner": "Choose the partner."})
        money_account = None
        if d.get("money_account"):
            money_account = _uuid_get(Account, d["money_account"], company, "account")
        txn = services.create_transaction(
            company, request.user, partner=partner, kind=d.get("kind"), date=d.get("date"), amount=d.get("amount"),
            mode=d.get("mode") or "", money_account=money_account, reference=d.get("reference") or "",
            narration=d.get("narration") or "", rate=d.get("rate"), period_from=d.get("period_from"), period_to=d.get("period_to"))
        return Response(services.transaction_dict(txn), status=http.HTTP_201_CREATED)


def _valid_uuid(value):
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


class TransactionDetailView(APIView):
    def _get(self, request, pk):
        company = current_company()
        t = _uuid_get(PartnerTransaction, pk, company, "transaction")
        need_partner_access(request, t.partner, company)
        return t

    def get(self, request, pk):
        t = self._get(request, pk)
        t = _tx_qs(t.company).get(pk=t.pk)
        return Response({**services.transaction_dict(t), "journal": _journal(t)})

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "partners.manage")
        t = _uuid_get(PartnerTransaction, pk, current_company(), "transaction")
        d = _body(request)
        t = services.update_notes(t, request.user, reference=d.get("reference"), narration=d.get("narration"))
        return Response(services.transaction_dict(_tx_qs(t.company).get(pk=t.pk)))


def _journal(t):
    if not t.entry_id:
        return None
    e = t.entry
    return {"id": str(e.id), "reference": e.reference, "date": e.date, "status": e.status,
            "lines": [{"account": f"{l.account.code} {l.account.name}", "debit": l.debit, "credit": l.credit} for l in e.lines.select_related("account")]}


class TransactionActionView(APIView):
    action = None

    @transaction.atomic
    def post(self, request, pk):
        need(request, "partners.manage")
        t = _uuid_get(PartnerTransaction, pk, current_company(), "transaction")
        reason = (_body(request).get("reason") or _body(request).get("change_reason") or "")
        if self.action == "approve":
            t = services.approve(t, request.user)
        elif self.action == "reject":
            t = services.reject(t, request.user, reason)
        else:
            t = services.void(t, request.user, reason)
        return Response(services.transaction_dict(_tx_qs(t.company).get(pk=t.pk)))


class InterestPreviewView(APIView):
    def get(self, request):
        need(request, "partners.view")
        company = current_company()
        p = request.query_params
        partner = _partner(p.get("partner"), company) if p.get("partner") else None
        if partner is None:
            raise ValidationError({"partner": "Choose the partner."})
        rate = services.percent_from(p.get("rate", services.get_settings(company).interest_on_capital_rate), "rate", "Rate")
        start, end = _date(p.get("from"), "from", True), _date(p.get("to"), "to", True)
        return Response(services.interest_preview(company, partner, rate, start, end))


# --------------------------------------------------------------------------- profit sharing

class ScheduleCollectionView(APIView):
    def get(self, request):
        need(request, "partners.view")
        company = current_company()
        rows = [services.schedule_dict(s) for s in ProfitShareSchedule.objects.filter(company=company).prefetch_related("lines__partner")]
        today = datetime.date.today()
        current = services.schedule_on(company, today)
        for r in rows:
            r["current"] = bool(current and r["id"] == str(current.id))
        return Response(rows)

    @transaction.atomic
    def post(self, request):
        need(request, "partners.manage")
        d = _body(request)
        s = services.save_schedule(current_company(), request.user, effective_from=d.get("effective_from"), shares=d.get("shares"),
                                   note=d.get("note") or "")
        return Response(services.schedule_dict(s), status=http.HTTP_201_CREATED)


class ScheduleDetailView(APIView):
    @transaction.atomic
    def delete(self, request, pk):
        need(request, "partners.manage")
        s = _uuid_get(ProfitShareSchedule, pk, current_company(), "profit-sharing ratio")
        services.delete_schedule(s, request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


def allocation_dict(a, detail=True):
    out = {"id": str(a.id), "number": a.number, "period_start": a.period_start, "period_end": a.period_end, "status": a.status,
           "status_label": a.get_status_display(), "net_profit": a.net_profit, "remuneration_charged": a.remuneration_charged,
           "interest_charged": a.interest_charged, "other_deductions": a.other_deductions, "deduction_note": a.deduction_note,
           "distributable": a.distributable, "post_date": a.post_date, "reverse_reason": a.reverse_reason,
           "schedule_from": a.schedule.effective_from, "created_at": a.created_at,
           "shares": [{"partner": str(s.partner_id), "name": s.partner.full_name, "percent": s.percent, "amount": s.amount}
                      for s in a.shares.select_related("partner")]}
    return out


def _alloc_qs(company):
    return ProfitAllocation.objects.filter(company=company).select_related("schedule").prefetch_related("shares__partner")


class AllocationCollectionView(APIView):
    def get(self, request):
        need(request, "partners.view")
        qs = _alloc_qs(current_company())
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response([allocation_dict(a) for a in qs])

    @transaction.atomic
    def post(self, request):
        need(request, "partners.manage")
        d = _body(request)
        a = services.save_allocation(current_company(), request.user, period_start=d.get("period_start"), period_end=d.get("period_end"),
                                     other_deductions=d.get("other_deductions"), deduction_note=d.get("deduction_note") or "")
        return Response(allocation_dict(_alloc_qs(a.company).get(pk=a.pk)), status=http.HTTP_201_CREATED)


class AllocationPreviewView(APIView):
    def post(self, request):
        need(request, "partners.view")
        d = _body(request)
        company = current_company()
        start, end = _date(d.get("period_start"), "period_start", True), _date(d.get("period_end"), "period_end", True)
        return Response(services.compute_allocation(company, start, end, other_deductions=services._deduction(d.get("other_deductions"), "")))


class AllocationDetailView(APIView):
    def _get(self, pk):
        return _uuid_get(ProfitAllocation, pk, current_company(), "profit allocation")

    def get(self, request, pk):
        need(request, "partners.view")
        a = self._get(pk)
        return Response(allocation_dict(_alloc_qs(a.company).get(pk=a.pk)))

    @transaction.atomic
    def patch(self, request, pk):
        """Re-run a draft with new dates or deductions (and the profit as it stands now)."""
        need(request, "partners.manage")
        a = self._get(pk)
        d = _body(request)
        a = services.save_allocation(
            a.company, request.user, period_start=d.get("period_start", a.period_start), period_end=d.get("period_end", a.period_end),
            other_deductions=d.get("other_deductions", a.other_deductions), deduction_note=d.get("deduction_note", a.deduction_note), alloc=a)
        return Response(allocation_dict(_alloc_qs(a.company).get(pk=a.pk)))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "partners.manage")
        services.delete_allocation(self._get(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class AllocationActionView(APIView):
    action = None

    @transaction.atomic
    def post(self, request, pk):
        need(request, "partners.manage")
        a = _uuid_get(ProfitAllocation, pk, current_company(), "profit allocation")
        d = _body(request)
        if self.action == "post":
            a = services.post_allocation(a, request.user, d.get("post_date"))
        else:
            a = services.reverse_allocation(a, request.user, d.get("reason") or d.get("change_reason") or "")
        return Response(allocation_dict(_alloc_qs(a.company).get(pk=a.pk)))


# --------------------------------------------------------------------------- statements, loans, history

def _period(request, company):
    start, end = reports.default_period(company)
    p = request.query_params
    return _date(p.get("from"), "from", default=start), _date(p.get("to"), "to", default=end)


class StatementView(APIView):
    def get(self, request, pk):
        company = current_company()
        partner = _partner(pk, company)
        need_partner_access(request, partner, company)
        start, end = _period(request, company)
        return Response(services.statement(company, partner, start, end))


class StatementPdfView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        company = current_company()
        partner = _partner(pk, company)
        need_partner_access(request, partner, company)
        d = _body(request)
        default_start, default_end = reports.default_period(company)
        start, end = _date(d.get("from"), "from", default=default_start), _date(d.get("to"), "to", default=default_end)
        doc = services.statement_pdf(company, request.user, partner, start, end)
        return Response({"id": str(doc.id), "reference_number": doc.reference_number}, status=http.HTTP_201_CREATED)


class LoanLedgerView(APIView):
    def get(self, request):
        company = current_company()
        as_of = _date(request.query_params.get("as_of"), "as_of", default=datetime.date.today())
        partner = None
        if can_view_all(request):
            if request.query_params.get("partner"):
                partner = _partner(request.query_params["partner"], company)
        else:
            partner = mine(request, company)
            if partner is None:
                raise PermissionDenied("Your role does not allow this.")
        data = services.loan_ledger(company, as_of, partner)
        if partner is not None and not data["rows"]:
            data["rows"] = []
        return Response(data)


class HistoryView(APIView):
    def get(self, request):
        company = current_company()
        start, end = _period(request, company)
        partner = None
        if can_view_all(request):
            if request.query_params.get("partner"):
                partner = _partner(request.query_params["partner"], company)
        else:
            partner = mine(request, company)
            if partner is None:
                raise PermissionDenied("Your role does not allow this.")
        if (end - start).days > 366 * 5:
            raise ValidationError({"to": "Choose a period of at most five years."})
        return Response({"start": start, "end": end, "months": services.history(company, start, min(end, datetime.date.today()), partner)})


class AgreementsView(APIView):
    def get(self, request):
        need(request, "partners.view")
        return Response(services.agreement_versions(current_company(), request.user))
