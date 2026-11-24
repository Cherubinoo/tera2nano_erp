"""Bank statement import, matching and reconciliation (the Bank reconciliation pages of Accounting)."""
import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company, private_file_response

from .models import Account, BankMatch, BankReconciliation, BankStatement, BankStatementLine, JournalLine
from .services import bank_matching, chart, ledger, reconcile, statement_files, statements
from .views import _date, need

MAX_LINES = 500


def _bank(pk, company=None):
    company = company or current_company()
    try:
        a = Account.objects.filter(pk=pk, company=company, subtype=Account.Subtype.BANK).select_related("bank_account").first()
    except (ValueError, DjangoValidationError):
        a = None
    if a is None:
        raise NotFound("No such bank account.")
    return a


def _get(model, pk, label="No such record."):
    try:
        obj = model.objects.filter(pk=pk, company=current_company()).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise NotFound(label)
    return obj


def _money(value, label):
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError({label: "Enter the balance as a number."})
    if not d.is_finite() or d.as_tuple().exponent < -2:
        raise ValidationError({label: "Enter the balance with at most 2 decimal places."})
    return d


def statement_dict(s):
    return {"id": str(s.id), "account": str(s.account_id), "status": s.status, "format": s.format, "format_label": s.get_format_display(),
            "file_name": s.original_filename, "file_size": s.file_size, "period_start": s.period_start, "period_end": s.period_end,
            "closing_balance": s.closing_balance, "rows_read": s.rows_read, "imported": s.imported_count,
            "duplicates": s.duplicate_count, "skipped": s.skipped_count, "problems": s.problems, "imported_at": s.imported_at,
            "created_at": s.created_at, "created_by": s.created_by.get_username() if s.created_by_id else "",
            "matched": getattr(s, "n_done", None)}


def line_dict(l, suggestions=None, matches=None):
    out = bank_matching_dict(l)
    if suggestions is not None:
        out["suggestions"] = suggestions.get(str(l.id), [])
    if matches is not None and l.match_id:
        out["matched_with"] = matches.get(l.match_id, [])
    return out


def bank_matching_dict(l):
    return {**reconcile.stmt_dict(l), "match": str(l.match_id) if l.match_id else None, "reconciled": l.reconciliation_id is not None,
            "balance": l.balance, "row": l.row_number, "statement": str(l.statement_id),
            "waiting_for": {"type": l.created_source, "id": l.created_source_id} if l.created_source else None}


# --------------------------------------------------------------------------- overview

class BankRecOverviewView(APIView):
    """Each bank account with where its reconciliation stands."""

    def get(self, request):
        need(request, "accounting.view")
        company = current_company()
        chart.money_accounts(company)
        totals = ledger.totals(company)
        rows = []
        for a in Account.objects.filter(company=company, subtype=Account.Subtype.BANK, is_active=True).select_related("bank_account"):
            counts = {r["status"]: r["n"] for r in BankStatementLine.objects.filter(account=a).values("status").annotate(n=Count("id"))}
            last_line = BankStatementLine.objects.filter(account=a).order_by("-date").first()
            bal, bal_date = reconcile.statement_balance_on(a, datetime.date.max)
            last = BankReconciliation.objects.filter(account=a).order_by("-statement_date").first()
            rows.append({"id": str(a.id), "code": a.code, "name": a.name, "bank": a.bank_account.bank_name if a.bank_account_id else "",
                         "book_balance": ledger.natural_balance(a, *totals.get(a.id, (0, 0))),
                         "statement_balance": bal, "statement_balance_date": bal_date,
                         "last_statement_date": last_line.date if last_line else None,
                         "to_match": counts.get("unmatched", 0), "matched": counts.get("matched", 0), "ignored": counts.get("ignored", 0),
                         "reconciled_up_to": last.statement_date if last else None,
                         "statements": BankStatement.objects.filter(account=a, status=BankStatement.Status.IMPORTED).count()})
        return Response(rows)


# --------------------------------------------------------------------------- statements

class StatementCollectionView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, pk):
        need(request, "accounting.view")
        a = _bank(pk)
        qs = (BankStatement.objects.filter(account=a, status=BankStatement.Status.IMPORTED).select_related("created_by")
              .annotate(n_done=Count("lines", filter=~Q(lines__status="unmatched"))))
        return Response([statement_dict(s) for s in qs])

    def post(self, request, pk):
        need(request, "accounting.manage")
        company = current_company()
        st = statements.upload(company, request.user, account=_bank(pk, company), uploaded=request.FILES.get("file"))
        return Response({"statement": statement_dict(st), **statements.preview(st)}, status=http.HTTP_201_CREATED)


class StatementDetailView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        st = _get(BankStatement, pk, "No such statement.")
        out = {"statement": statement_dict(st)}
        if st.status == BankStatement.Status.DRAFT:
            header_row = request.query_params.get("header_row")
            try:
                header_row = int(header_row) if header_row not in (None, "") else None
            except ValueError:
                raise ValidationError({"header_row": "Choose the row that holds the column names."})
            out.update(statements.preview(st, header_row=header_row))
        out["fields"] = [{"key": k, "label": statement_files.FIELD_LABELS[k]} for k in statement_files.FIELDS]
        return Response(out)

    def delete(self, request, pk):
        need(request, "accounting.manage")
        statements.delete_statement(_get(BankStatement, pk, "No such statement."), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class StatementImportView(APIView):
    def post(self, request, pk):
        need(request, "accounting.manage")
        st = _get(BankStatement, pk, "No such statement.")
        dry = bool(request.data.get("dry_run"))
        summary = statements.import_statement(st, request.user, mapping=request.data.get("mapping"), dry_run=dry)
        st.refresh_from_db()
        return Response({"statement": statement_dict(st), "summary": summary, "dry_run": dry})


class StatementFileView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        st = _get(BankStatement, pk, "No such statement.")
        return private_file_response(st.file, filename=st.original_filename, attachment=True, mime_type="application/octet-stream")


# --------------------------------------------------------------------------- lines and matching

def _matches_for(lines):
    """{match id: [book line dicts]} for the matched lines shown."""
    ids = {l.match_id for l in lines if l.match_id}
    out = {}
    for jl in JournalLine.objects.filter(bank_match_id__in=ids).select_related("entry"):
        out.setdefault(jl.bank_match_id, []).append(bank_matching.book_dict(jl))
    return out


class StatementLinesView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        a = _bank(pk)
        p = request.query_params
        qs = BankStatementLine.objects.filter(account=a)
        status = p.get("status") or "unmatched"
        if status != "all":
            if status not in BankStatementLine.Status.values:
                raise ValidationError({"status": "Choose to match, matched, ignored or all."})
            qs = qs.filter(status=status)
        if p.get("from"):
            qs = qs.filter(date__gte=_date(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(date__lte=_date(p["to"], "to"))
        if p.get("statement"):
            qs = qs.filter(statement_id=p["statement"])
        if p.get("q"):
            qs = qs.filter(Q(description__icontains=p["q"]) | Q(reference__icontains=p["q"]))
        count = qs.count()
        lines = list(qs.order_by("date", "seq")[:MAX_LINES])
        unmatched = [l for l in lines if l.status == BankStatementLine.Status.UNMATCHED]
        sugg = bank_matching.suggest(a, unmatched)
        counts = {r["status"]: r["n"] for r in BankStatementLine.objects.filter(account=a).values("status").annotate(n=Count("id"))}
        matches = _matches_for(lines)
        return Response({"account": {"id": str(a.id), "name": a.name, "code": a.code,
                                     "linked_bank": bool(a.bank_account_id) or a.system_key == "bank"},
                         "count": count, "status_counts": counts,
                         "results": [line_dict(l, sugg, matches) for l in lines]})


class BookEntriesView(APIView):
    """Book lines of the bank account that are not matched yet, to pick a match by hand."""

    def get(self, request, pk):
        need(request, "accounting.view")
        a = _bank(pk)
        p = request.query_params
        qs = bank_matching.candidates(a)
        if p.get("direction") == "in":
            qs = qs.filter(debit__gt=0)
        elif p.get("direction") == "out":
            qs = qs.filter(credit__gt=0)
        if p.get("from"):
            qs = qs.filter(entry__date__gte=_date(p["from"], "from"))
        if p.get("to"):
            qs = qs.filter(entry__date__lte=_date(p["to"], "to"))
        if p.get("q"):
            q = p["q"].strip()
            cond = Q(entry__reference__icontains=q) | Q(entry__narration__icontains=q) | Q(memo__icontains=q)
            try:
                amt = Decimal(q.replace(",", ""))
                cond |= Q(debit=abs(amt)) | Q(credit=abs(amt))
            except (InvalidOperation, ValueError):
                pass
            qs = qs.filter(cond)
        return Response({"count": qs.count(), "results": [bank_matching.book_dict(jl) for jl in qs[:200]]})


class MatchCollectionView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        company, d = current_company(), request.data
        s_ids, j_ids = d.get("statement_lines"), d.get("journal_lines")
        if not isinstance(s_ids, list) or not isinstance(j_ids, list):
            raise ValidationError({"statement_lines": "Send the statement lines and the entries to match."})
        kind = d.get("kind") if d.get("kind") in (BankMatch.Kind.SUGGESTED, BankMatch.Kind.MANUAL) else BankMatch.Kind.MANUAL
        m = bank_matching.match(company, request.user, account=_bank(pk, company), statement_line_ids=s_ids, journal_line_ids=j_ids,
                                kind=kind, note=d.get("note") or "")
        return Response({"id": str(m.id), "kind": m.kind}, status=http.HTTP_201_CREATED)


class AutoMatchView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        company = current_company()
        return Response({"matched": bank_matching.auto_accept(company, request.user, _bank(pk, company))})


class ClearedView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        company, d = current_company(), request.data
        ids = d.get("journal_lines")
        if not isinstance(ids, list):
            raise ValidationError({"journal_lines": "Choose the entries that already went through the bank."})
        m = bank_matching.mark_cleared(company, request.user, account=_bank(pk, company), journal_line_ids=ids, note=d.get("note") or "")
        return Response({"id": str(m.id), "count": len(ids)}, status=http.HTTP_201_CREATED)


class MatchUndoView(APIView):
    def post(self, request, pk):
        need(request, "accounting.manage")
        bank_matching.unmatch(_get(BankMatch, pk, "No such match."), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class LineIgnoreView(APIView):
    undo = False

    def post(self, request, pk):
        need(request, "accounting.manage")
        line = _get(BankStatementLine, pk, "No such statement line.")
        if self.undo:
            line = bank_matching.unignore(line, request.user)
        else:
            line = bank_matching.ignore(line, request.user, request.data.get("reason") or "")
        return Response(bank_matching_dict(line))


# The module permission each kind of entry needs on top of accounting.manage (the same as entering it on its own page).
CREATE_PERMS = {"customer_payment": "sales.manage", "vendor_payment": "purchases.manage"}


class LineCreateView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        line = _get(BankStatementLine, pk, "No such statement line.")
        kind = request.data.get("kind") or ""
        if kind in CREATE_PERMS and not request.user.has_code(CREATE_PERMS[kind]):
            raise PermissionDenied("Your role does not allow recording this kind of payment.")
        out = bank_matching.create_from_line(line, request.user, kind, request.data)
        line.refresh_from_db()
        return Response({"line": bank_matching_dict(line), "record": out["record"], "pending": out["pending"],
                         "match": str(out["match"].id) if out["match"] else None}, status=http.HTTP_201_CREATED)


class LineCreateOptionsView(APIView):
    """What the "Create entry" dialog offers for a line: open invoices or bills (closest amount first), customers, vendors,
    accounts. `kind` narrows it; `q` searches invoices, bills and parties."""

    def get(self, request, pk):
        need(request, "accounting.view")
        line = _get(BankStatementLine, pk, "No such statement line.")
        company, kind, q = current_company(), request.query_params.get("kind") or "", (request.query_params.get("q") or "").strip()
        amount = abs(line.amount)
        out = {"line": bank_matching_dict(line), "amount": amount}
        pick = lambda qs: [{"id": str(a.id), "code": a.code, "name": a.name} for a in qs]
        if kind == "customer_payment":
            from apps.sales import services as sales
            from apps.sales.models import Customer, SalesDocument
            docs = sales.with_amounts(SalesDocument.objects.filter(company=company, kind__in=SalesDocument.RECEIVABLE,
                                                                   status=SalesDocument.Status.ISSUED)).filter(balance_due__gt=0).select_related("customer")
            if q:
                docs = docs.filter(Q(number__icontains=q) | Q(customer__display_name__icontains=q))
            rows = [{"id": str(d.id), "number": d.number, "customer": {"id": str(d.customer_id), "name": d.customer.display_name},
                     "date": d.issue_date, "due_date": d.due_date, "balance": sales.balance_of(d)} for d in docs[:300]]
            rows.sort(key=lambda r: (r["balance"] != amount, abs(r["balance"] - amount)))
            customers = Customer.objects.filter(company=company)
            if q:
                customers = customers.filter(display_name__icontains=q)
            out.update(invoices=rows[:50], customers=[{"id": str(c.id), "name": c.display_name} for c in customers.order_by("display_name")[:50]])
        elif kind == "vendor_payment":
            from apps.purchases.models import Vendor, VendorBill
            from apps.purchases.services import amounts as p_amounts
            bills = p_amounts.bills_with_amounts(VendorBill.objects.filter(company=company, status=VendorBill.Status.OPEN)).filter(
                balance_due__gt=0).select_related("vendor")
            if q:
                bills = bills.filter(Q(number__icontains=q) | Q(vendor_bill_no__icontains=q) | Q(vendor__name__icontains=q))
            rows = [{"id": str(b.id), "number": b.number, "vendor_bill_no": b.vendor_bill_no,
                     "vendor": {"id": str(b.vendor_id), "name": b.vendor.name}, "date": b.bill_date, "due_date": b.due_date,
                     "balance": p_amounts.balance_of(b)} for b in bills[:300]]
            rows.sort(key=lambda r: (r["balance"] != amount, abs(r["balance"] - amount)))
            vendors = Vendor.objects.filter(company=company)
            if q:
                vendors = vendors.filter(name__icontains=q)
            out.update(bills=rows[:50], vendors=[{"id": str(v.id), "name": v.name} for v in vendors.order_by("name")[:50]])
        else:
            from apps.company.services import company as company_service
            from django.conf import settings
            ts = company_service.get_tax_settings(company)
            gst_on = ts.gst_applicable and ts.gst_treatment == "regular"
            accounts = Account.objects.filter(company=company, is_active=True)
            charges = bank_matching.bank_charges_account(company)
            out.update(expense_accounts=pick(accounts.filter(type="expense")), income_accounts=pick(accounts.filter(type="income")),
                       money_accounts=pick(chart.money_accounts(company).exclude(pk=line.account_id)),
                       bank_charges_account=str(charges.id) if charges else None,
                       gst_applied=gst_on, gst_rates=[str(r) for r in settings.GST_CURRENT_RATES if r] if gst_on else [])
        return Response(out)


# --------------------------------------------------------------------------- reconciliation

class ReconciliationView(APIView):
    def get(self, request, pk):
        need(request, "accounting.view")
        a = _bank(pk)
        p = request.query_params
        as_of = _date(p.get("to") or p.get("as_of"), "to", datetime.date.today())
        start = _date(p.get("from"), "from")
        if start and start > as_of:
            raise ValidationError({"to": "The end date is before the start date."})
        out = reconcile.position(a, as_of, start=start, statement_balance=_money(p.get("statement_balance"), "statement_balance"))
        out["history"] = [{"id": str(r.id), "statement_date": r.statement_date, "statement_balance": r.statement_balance,
                           "notes": r.notes, "created_at": r.created_at}
                          for r in BankReconciliation.objects.filter(account=a).order_by("-statement_date", "-created_at")[:24]]
        return Response(out)


class ReconciliationCloseView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        need(request, "accounting.manage")
        company, d = current_company(), request.data
        rec = reconcile.close(company, request.user, account=_bank(pk, company), as_of=_date(d.get("as_of"), "as_of", required=True),
                              statement_balance=_money(d.get("statement_balance"), "statement_balance"), notes=d.get("notes") or "")
        return Response({"id": str(rec.id), "statement_date": rec.statement_date, "statement_balance": rec.statement_balance},
                        status=http.HTTP_201_CREATED)
