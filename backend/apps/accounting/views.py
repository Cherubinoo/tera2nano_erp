import datetime
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company
from apps.company.constants import DocType
from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import company as company_service, numbering

from .models import Account, BankReconciliation, JournalEntry, JournalLine, Voucher
from .services import budgets, chart, ledger, posting, reconcile, reports, vouchers

Action = CompanyProfileAuditEvent.Action
MAX_ROWS = 500


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def _date(value, label, default=None, required=False):
    if value in (None, ""):
        if default is not None:
            return default
        if required:
            raise ValidationError({label: "Enter a date."})
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({label: "Use a date in YYYY-MM-DD format."})


def _account(ref, company, label="account"):
    acct = None
    try:
        acct = Account.objects.filter(pk=ref, company=company).first() if ref else None
    except (ValueError, DjangoValidationError):
        pass
    if acct is None:
        raise ValidationError({label: "Choose an account."})
    return acct


def _period(request, company):
    start_default, end_default = reports.default_period(company)
    start = _date(request.query_params.get("from"), "from", start_default)
    end = _date(request.query_params.get("to"), "to", end_default)
    if end < start:
        raise ValidationError({"to": "The end date is before the start date."})
    return start, end


# --------------------------------------------------------------------------- chart of accounts

def account_dict(a, balance=None):
    return {"id": str(a.id), "code": a.code, "name": a.name, "type": a.type, "type_label": a.get_type_display(),
            "subtype": a.subtype, "subtype_label": a.get_subtype_display(), "system_key": a.system_key, "is_system": a.is_system,
            "description": a.description, "is_active": a.is_active, "normal": "debit" if a.is_debit_normal else "credit",
            "bank_account": a.bank_account.display_name if a.bank_account_id else "",
            "balance": balance if balance is not None else None}


class AccountCollectionView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        chart.ensure_chart(company)
        totals = ledger.totals(company, end=_date(request.query_params.get("as_of"), "as_of"))
        qs = Account.objects.filter(company=company).select_related("bank_account")
        if request.query_params.get("type"):
            qs = qs.filter(type=request.query_params["type"])
        if request.query_params.get("active") in ("true", "false"):
            qs = qs.filter(is_active=request.query_params["active"] == "true")
        return Response([account_dict(a, ledger.natural_balance(a, *totals.get(a.id, (0, 0)))) for a in qs])

    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        company = current_company()
        d = request.data
        a = Account(company=company, code=d.get("code", ""), name=d.get("name", ""), subtype=d.get("subtype", ""),
                    description=(d.get("description") or "")[:300])
        a.type = Account.TYPE_OF.get(a.subtype, "")
        try:
            a.full_clean(exclude=["system_key", "bank_account"])
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages})
        a.save()
        ledger.audit_event(company, request.user, "account", a.pk, Action.CREATED, new={"code": a.code, "name": a.name, "type": a.type})
        return Response(account_dict(a, 0), status=http.HTTP_201_CREATED)


class AccountDetailView(APIView):
    def _get(self, pk):
        try:
            a = Account.objects.filter(pk=pk, company=current_company()).select_related("bank_account").first()
        except (ValueError, DjangoValidationError):
            a = None
        if a is None:
            raise NotFound("No such account.")
        return a

    def get(self, request, pk):
        need(request, "accounting.view")
        return Response(account_dict(self._get(pk)))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "accounting.manage")
        a, d = self._get(pk), request.data
        before = {"code": a.code, "name": a.name, "description": a.description, "is_active": a.is_active}
        if "subtype" in d and d["subtype"] != a.subtype:
            if a.is_system or JournalLine.objects.filter(account=a).exists():
                raise ValidationError({"subtype": "An account with entries, or one the system posts to, cannot change kind."})
            a.subtype, a.type = d["subtype"], Account.TYPE_OF.get(d["subtype"], "")
        for f in ("code", "name", "description"):
            if f in d:
                setattr(a, f, str(d[f]).strip())
        if "is_active" in d:
            if not isinstance(d["is_active"], bool):
                raise ValidationError({"is_active": "Send true or false."})
            if not d["is_active"] and a.is_system:
                raise ValidationError({"is_active": "The system posts to this account, so it cannot be switched off."})
            a.is_active = d["is_active"]
        try:
            a.full_clean(exclude=["system_key", "bank_account"])
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages})
        a.save()
        after = {"code": a.code, "name": a.name, "description": a.description, "is_active": a.is_active}
        if before != after:
            ledger.audit_event(a.company, request.user, "account", a.pk, Action.UPDATED, previous=before, new=after)
        return Response(account_dict(a))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "accounting.manage")
        a = self._get(pk)
        if a.is_system:
            raise ValidationError("The system posts to this account, so it cannot be deleted.")
        if JournalLine.objects.filter(account=a).exists():
            raise ValidationError("This account has entries. Switch it off instead of deleting it.")
        ledger.audit_event(a.company, request.user, "account", a.pk, Action.DELETED, previous={"code": a.code, "name": a.name})
        a.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)


class AccountSetupView(APIView):
    """Create any missing standard account and post sales made before the books existed."""

    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        company = current_company()
        made = chart.ensure_chart(company)
        rebuilt = posting.rebuild(company, request.user)
        return Response({"accounts_created": len(made), **rebuilt})


# --------------------------------------------------------------------------- journal

def entry_dict(e, detail=False):
    lines = list(e.lines.select_related("account"))
    out = {"id": str(e.id), "date": e.date, "reference": e.reference, "narration": e.narration, "source": e.source,
           "source_label": e.get_source_display(), "status": e.status, "amount": sum((l.debit for l in lines), 0),
           "reverses": str(e.reverses_id) if e.reverses_id else None,
           "reversed_by": str(e.reversed_by.id) if hasattr(e, "reversed_by") and e.reversed_by else None,
           "source_id": e.source_id}
    if detail:
        out["lines"] = [{"id": str(l.id), "account": {"id": str(l.account_id), "code": l.account.code, "name": l.account.name},
                         "debit": l.debit, "credit": l.credit, "memo": l.memo} for l in lines]
        out["financial_year"] = e.financial_year.label if e.financial_year_id else ""
    return out


class JournalCollectionView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        p = request.query_params
        qs = JournalEntry.objects.filter(company=company)
        if p.get("source"):
            qs = qs.filter(source=p["source"])
        if p.get("q"):
            qs = qs.filter(Q(reference__icontains=p["q"]) | Q(narration__icontains=p["q"]))
        if p.get("from"):
            qs = qs.filter(date__gte=_date(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(date__lte=_date(p["to"], "to"))
        if p.get("account"):
            qs = qs.filter(lines__account_id=p["account"]).distinct()
        count = qs.count()
        return Response({"count": count, "results": [entry_dict(e) for e in qs.select_related("reversed_by")[:MAX_ROWS]]})

    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        company = current_company()
        rows = request.data.get("lines")
        if not isinstance(rows, list):
            raise ValidationError({"lines": "Send the lines of the entry."})
        lines = []
        for r in rows:
            lines.append({"account": _account(r.get("account"), company, "lines"), "debit": r.get("debit") or 0,
                          "credit": r.get("credit") or 0, "memo": r.get("memo") or ""})
        entry = vouchers.create_journal(company, request.user, date=_date(request.data.get("date"), "date", required=True),
                                        lines=lines, narration=request.data.get("narration") or "")
        return Response(entry_dict(entry, detail=True), status=http.HTTP_201_CREATED)


class JournalDetailView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        try:
            e = JournalEntry.objects.filter(pk=pk, company=current_company()).select_related("financial_year", "reversed_by").first()
        except (ValueError, DjangoValidationError):
            e = None
        if e is None:
            raise NotFound("No such entry.")
        return Response(entry_dict(e, detail=True))


class JournalReverseView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        e = JournalEntry.objects.filter(pk=pk, company=current_company()).first()
        if e is None:
            raise NotFound("No such entry.")
        if e.source not in (JournalEntry.Source.MANUAL, JournalEntry.Source.TRANSFER, JournalEntry.Source.OPENING):
            raise ValidationError("This entry was made by a sale or a voucher. Void that record instead; it reverses its entry.")
        reason = (request.data.get("change_reason") or "").strip()
        if not reason:
            raise ValidationError({"change_reason": "Give a reason for reversing this entry."})
        back = ledger.reverse(e, request.user, reason, on_date=_date(request.data.get("date"), "date", datetime.date.today()))
        return Response(entry_dict(back, detail=True))


class TransferView(APIView):
    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        company, d = current_company(), request.data
        entry = vouchers.transfer(company, request.user, date=_date(d.get("date"), "date", required=True),
                                  from_account=_account(d.get("from_account"), company, "from_account"),
                                  to_account=_account(d.get("to_account"), company, "to_account"), amount=d.get("amount"),
                                  notes=d.get("notes") or "")
        return Response(entry_dict(entry, detail=True), status=http.HTTP_201_CREATED)


class OpeningBalancesView(APIView):
    def get(self, request):
        """The opening-balances entry in force (with its lines), or null when none has been entered."""
        need(request, "accounting.view")
        entry = vouchers.current_opening(current_company())
        return Response({"entry": entry_dict(entry, detail=True) if entry else None})

    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        company, d = current_company(), request.data
        rows = d.get("rows")
        if not isinstance(rows, list):
            raise ValidationError({"rows": "Send the balances."})
        entry = vouchers.opening_balances(company, request.user, date=_date(d.get("date"), "date", required=True),
                                          rows=[{**r, "account": _account(r.get("account"), company, "rows")} for r in rows])
        return Response(entry_dict(entry, detail=True), status=http.HTTP_201_CREATED)


# --------------------------------------------------------------------------- vouchers (expense and income)

def voucher_dict(v, projects=None):
    if projects is None:
        projects = vouchers.project_refs(v.company, [v.project_id])
    return {"id": str(v.id), "kind": v.kind, "number": v.number, "date": v.date, "party_name": v.party_name, "reference": v.reference,
            "notes": v.notes, "category": {"id": str(v.category_id), "code": v.category.code, "name": v.category.name},
            "money_account": {"id": str(v.money_account_id), "name": v.money_account.name}, "amount": v.amount,
            "tax_rate": v.tax_rate, "tax_amount": v.tax_amount, "total": v.total, "gst_split": v.gst_split, "status": v.status,
            "void_reason": v.void_reason, "entry_id": str(v.entry_id) if v.entry_id else None,
            "project": str(v.project_id) if v.project_id else None, "project_ref": projects.get(v.project_id), "decision_note": v.decision_note,
            "submitted_at": v.submitted_at, "status_label": v.get_status_display(),
            "approval_managed": vouchers.approval_managed(), "created_by": v.created_by.get_username() if v.created_by_id else ""}


class VoucherCollectionView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        p = request.query_params
        qs = Voucher.objects.filter(company=current_company()).select_related("category", "money_account")
        if p.get("kind"):
            qs = qs.filter(kind=p["kind"])
        if p.get("status"):
            qs = qs.filter(status=p["status"])
        if p.get("category"):
            qs = qs.filter(category_id=p["category"])
        if p.get("project"):
            try:
                qs = qs.filter(project_id=uuid.UUID(p["project"]))
            except ValueError:
                raise ValidationError({"project": "Choose a project from the list."})
        if p.get("q"):
            qs = qs.filter(Q(number__icontains=p["q"]) | Q(party_name__icontains=p["q"]) | Q(reference__icontains=p["q"]) | Q(notes__icontains=p["q"]))
        if p.get("from"):
            qs = qs.filter(date__gte=_date(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(date__lte=_date(p["to"], "to"))
        total = sum((v.total for v in qs.filter(status="recorded")), 0)
        rows = list(qs[:MAX_ROWS])
        refs = vouchers.project_refs(current_company(), [v.project_id for v in rows])
        counts = {row["status"]: row["n"] for row in Voucher.objects.filter(company=current_company(), kind=p.get("kind") or "expense")
                  .values("status").annotate(n=Count("id"))}
        return Response({"count": qs.count(), "total": total, "status_counts": counts,
                         "results": [voucher_dict(v, refs) for v in rows]})

    @transaction.atomic
    def post(self, request):
        need(request, "accounting.manage")
        company, d = current_company(), request.data
        v = vouchers.create_voucher(
            company, request.user, kind=d.get("kind"), date=_date(d.get("date"), "date", required=True),
            category=_account(d.get("category"), company, "category"), money_account=_account(d.get("money_account"), company, "money_account"),
            amount=d.get("amount"), tax_mode=d.get("tax_mode") or "none", tax_rate=d.get("tax_rate") or 0,
            party_name=d.get("party_name") or "", reference=d.get("reference") or "", notes=d.get("notes") or "",
            project_id=d.get("project") or None, record=_flag(d.get("record", True), "record"))
        out = voucher_dict(_voucher_qs().get(pk=v.pk))
        # A warning only (the expense is saved either way) when it takes the account past its active budget line.
        out["budget_warning"] = (budgets.expense_warning(company, account=v.category, on_date=v.date, amount=v.amount,
                                                         project_id=v.project_id, already_posted=v.status == Voucher.Status.RECORDED)
                                 if v.kind == Voucher.Kind.EXPENSE else None)
        return Response(out, status=http.HTTP_201_CREATED)


def _flag(value, label):
    if isinstance(value, bool):
        return value
    if str(value).lower() in ("true", "1"):
        return True
    if str(value).lower() in ("false", "0"):
        return False
    raise ValidationError({label: "Send true or false."})


def _voucher_qs():
    return Voucher.objects.select_related("category", "money_account", "created_by")


def _get_voucher(pk):
    v = _voucher_qs().filter(pk=pk, company=current_company()).first()
    if v is None:
        raise NotFound("No such entry.")
    return v


class VoucherDetailView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        return Response(voucher_dict(_get_voucher(pk)))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "accounting.manage")
        company, v, d = current_company(), _get_voucher(pk), request.data
        changes = {}
        if "date" in d:
            changes["date"] = _date(d.get("date"), "date", required=True)
        if "category" in d:
            changes["category"] = _account(d.get("category"), company, "category")
        if "money_account" in d:
            changes["money_account"] = _account(d.get("money_account"), company, "money_account")
        for key in ("amount", "tax_mode", "tax_rate", "party_name", "reference", "notes"):
            if key in d:
                changes[key] = d.get(key) if d.get(key) is not None else ("none" if key == "tax_mode" else "")
        if "project" in d:
            changes["project_id"] = d.get("project") or None
        vouchers.update_draft(v, request.user, **changes)
        return Response(voucher_dict(_get_voucher(pk)))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "accounting.manage")
        vouchers.delete_draft(_get_voucher(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class VoucherActionView(APIView):
    """record (a draft), withdraw (take it back from approval), approve / reject (only when approvals are not installed)."""
    action = ""

    def post(self, request, pk):
        need(request, "accounting.manage")
        v = _get_voucher(pk)
        if self.action == "record":
            vouchers.record(v, request.user)
        elif self.action == "withdraw":
            vouchers.take_back(v, request.user)
        else:
            if vouchers.approval_managed():
                raise ValidationError("Expenses waiting for approval are decided in the Approvals module.")
            if self.action == "approve":
                vouchers.approve(v, request.user, request.data.get("note") or "")
            else:
                vouchers.reject(v, request.user, request.data.get("change_reason") or request.data.get("note"))
        return Response(voucher_dict(_get_voucher(pk)))


class VoucherVoidView(APIView):
    def post(self, request, pk):
        need(request, "accounting.manage")
        v = _get_voucher(pk)
        vouchers.void_voucher(v, request.data.get("change_reason"), request.user)
        return Response(voucher_dict(_get_voucher(pk)))


class VoucherOptionsView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        chart.ensure_chart(company)
        ts = company_service.get_tax_settings(company)
        gst_on = ts.gst_applicable and ts.gst_treatment == "regular"
        from django.conf import settings
        accounts = Account.objects.filter(company=company, is_active=True)
        pick = lambda qs: [{"id": str(a.id), "code": a.code, "name": a.name} for a in qs]
        return Response({
            "gst_applied": gst_on, "gst_rates": [str(r) for r in settings.GST_CURRENT_RATES if r] if gst_on else [],
            "expense_accounts": pick(accounts.filter(type="expense")), "income_accounts": pick(accounts.filter(type="income")),
            "money_accounts": pick(chart.money_accounts(company)),
        })


# --------------------------------------------------------------------------- bank and cash

class BankOverviewView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        totals = ledger.totals(company)
        rows = []
        for a in chart.money_accounts(company).select_related("bank_account"):
            last = BankReconciliation.objects.filter(account=a).first()
            rows.append({"id": str(a.id), "code": a.code, "name": a.name, "subtype": a.subtype,
                         "balance": ledger.natural_balance(a, *totals.get(a.id, (0, 0))),
                         "unreconciled": JournalLine.objects.filter(account=a, reconciliation__isnull=True).count(),
                         "last_reconciled": last.statement_date if last else None,
                         "cleared_balance": reconcile.cleared_balance(a)})
        return Response(rows)


class BankUnreconciledView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        company = current_company()
        a = _account(pk, company)
        up_to = _date(request.query_params.get("to"), "to")
        return Response({"account": account_dict(a), "cleared_balance": reconcile.cleared_balance(a), "lines": [
            {"id": str(l.id), "date": l.entry.date, "reference": l.entry.reference, "narration": l.entry.narration, "memo": l.memo,
             "debit": l.debit, "credit": l.credit} for l in reconcile.unreconciled(a, up_to)]})


class BankReconcileView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        company, d = current_company(), request.data
        a = _account(pk, company)
        ids = d.get("lines")
        if not isinstance(ids, list):
            raise ValidationError({"lines": "Tick the lines that appear on the statement."})
        rec = reconcile.reconcile(company, request.user, account=a, statement_date=_date(d.get("statement_date"), "statement_date", required=True),
                                  statement_balance=_signed(d.get("statement_balance")),
                                  line_ids=ids, notes=d.get("notes") or "")
        return Response(reconciliation_dict(rec), status=http.HTTP_201_CREATED)


def _signed(value):
    """A statement balance may be negative (an overdraft)."""
    from decimal import Decimal, InvalidOperation
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError({"statement_balance": "Enter the closing balance shown on the statement."})
    if not d.is_finite() or d.as_tuple().exponent < -2:
        raise ValidationError({"statement_balance": "Enter the balance with at most 2 decimal places."})
    return d


def reconciliation_dict(r):
    return {"id": str(r.id), "account": str(r.account_id), "statement_date": r.statement_date, "statement_balance": r.statement_balance,
            "book_balance": r.book_balance, "notes": r.notes, "lines": r.lines.count(), "created_at": r.created_at}


class BankReconciliationsView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        a = _account(pk, current_company())
        return Response([reconciliation_dict(r) for r in BankReconciliation.objects.filter(account=a)])


class ReconciliationUndoView(APIView):
    def post(self, request, pk):
        need(request, "accounting.manage")
        r = BankReconciliation.objects.filter(pk=pk, company=current_company()).first()
        if r is None:
            raise NotFound("No such reconciliation.")
        reconcile.undo(r, request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- reports

class DashboardView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        return Response(reports.dashboard(current_company()))


class TrialBalanceView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        return Response(reports.trial_balance(company, _date(request.query_params.get("as_of"), "as_of", datetime.date.today())))


class LedgerView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        company = current_company()
        start, end = _period(request, company)
        return Response(reports.account_ledger(company, _account(pk, company), start, end))


class ProfitLossView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        start, end = _period(request, company)
        return Response(reports.profit_and_loss(company, start, end))


class BalanceSheetView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        return Response(reports.balance_sheet(company, _date(request.query_params.get("as_of"), "as_of", datetime.date.today())))


class CashFlowView(APIView):
    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        start, end = _period(request, company)
        return Response(reports.cash_flow(company, start, end))
