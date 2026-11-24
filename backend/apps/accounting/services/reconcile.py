"""Bank reconciliation: tick off the book lines that appear on the bank statement until the balances agree, or (with an
imported statement) match its lines and mark the account reconciled up to a date."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.company.models import CompanyProfileAuditEvent

from ..models import Account, BankReconciliation, BankStatementLine, JournalEntry, JournalLine
from . import ledger

ZERO = Decimal("0.00")
MONEY_SUBTYPES = (Account.Subtype.CASH, Account.Subtype.BANK)


def cleared_balance(account):
    """Debit minus credit of the lines already reconciled: what the bank statement showed at the last reconciliation."""
    agg = JournalLine.objects.filter(account=account, reconciliation__isnull=False).aggregate(d=Sum("debit"), c=Sum("credit"))
    return ledger.money((agg["d"] or ZERO) - (agg["c"] or ZERO))


def unreconciled(account, up_to=None):
    qs = JournalLine.objects.filter(account=account, reconciliation__isnull=True).select_related("entry").order_by("entry__date", "created_at")
    return qs.filter(entry__date__lte=up_to) if up_to else qs


@transaction.atomic
def reconcile(company, actor, *, account, statement_date, statement_balance, line_ids, notes=""):
    """Mark lines as cleared. The cleared balance must equal the statement's closing balance."""
    if account.company_id != company.pk or account.subtype not in MONEY_SUBTYPES:
        raise ValidationError({"account": "Choose a bank or cash account."})
    statement_balance = ledger.money(statement_balance)
    lines = list(JournalLine.objects.select_for_update(of=("self",)).filter(
        pk__in=line_ids, account=account, reconciliation__isnull=True).select_related("entry"))
    if len(lines) != len(set(line_ids)):
        raise ValidationError({"lines": "Some lines are not available to reconcile (already cleared or not in this account)."})
    if any(l.entry.date > statement_date for l in lines):
        raise ValidationError({"lines": "A line dated after the statement date cannot be cleared on this statement."})
    book = cleared_balance(account) + sum((l.debit - l.credit for l in lines), ZERO)
    if ledger.money(book) != statement_balance:
        raise ValidationError({"statement_balance": f"The cleared lines come to {ledger.money(book)} but the statement says "
                                                    f"{statement_balance}. The difference is {ledger.money(statement_balance - book)}."})
    rec = BankReconciliation.objects.create(company=company, account=account, statement_date=statement_date,
                                            statement_balance=statement_balance, book_balance=ledger.money(book),
                                            notes=notes.strip()[:300], created_by=actor)
    JournalLine.objects.filter(pk__in=[l.pk for l in lines]).update(reconciliation=rec)
    ledger.audit_event(company, actor, "reconciliation", rec.pk, CompanyProfileAuditEvent.Action.CREATED,
                       new={"account": account.name, "statement_date": str(statement_date), "balance": str(statement_balance),
                            "lines": len(lines)})
    return rec


@transaction.atomic
def undo(rec, actor):
    """Take back the most recent reconciliation of an account (older ones are built on it and stay)."""
    rec = BankReconciliation.objects.select_for_update(of=("self",)).select_related("account", "company").get(pk=rec.pk)
    newest = BankReconciliation.objects.filter(account=rec.account).order_by("-created_at").first()
    if newest.pk != rec.pk:
        raise ValidationError("Only the latest reconciliation of an account can be undone.")
    JournalLine.objects.filter(reconciliation=rec).update(reconciliation=None)
    BankStatementLine.objects.filter(reconciliation=rec).update(reconciliation=None)
    ledger.audit_event(rec.company, actor, "reconciliation", rec.pk, CompanyProfileAuditEvent.Action.DELETED,
                       previous={"account": rec.account.name, "statement_date": str(rec.statement_date)})
    rec.delete()


# --------------------------------------------------------------------------- reconciliation from imported statements

def _match_dates(account):
    """{match id: the date by which every line of the match is in both the bank and the books}."""
    dates = {}
    for mid, on in BankStatementLine.objects.filter(account=account, match__isnull=False).values_list("match_id", "date"):
        dates[mid] = max(dates.get(mid, on), on)
    for mid, on in JournalLine.objects.filter(account=account, bank_match__isnull=False).values_list("bank_match_id", "entry__date"):
        dates[mid] = max(dates.get(mid, on), on)
    return dates


def statement_balance_on(account, as_of):
    """(balance, date) from the last statement line on or before `as_of` that shows a running balance, or (None, None)."""
    line = (BankStatementLine.objects.filter(account=account, date__lte=as_of, balance__isnull=False)
            .order_by("-date", "-statement__imported_at", "-seq").first())
    return (line.balance, line.date) if line else (None, None)


def stmt_dict(line):
    return {"id": str(line.id), "date": line.date, "description": line.description, "reference": line.reference,
            "amount": line.amount, "status": line.status, "ignore_reason": line.ignore_reason}


def _total(rows, value):
    return ledger.money(sum((value(r) for r in rows), ZERO))


def position(account, as_of, *, start=None, statement_balance=None):
    """The bank reconciliation of `account` on `as_of`: the balance in the books, what the books have that the bank has not
    shown yet (and the other way round), the balance the statement should therefore show, and the difference to it."""
    from . import bank_matching
    dates = _match_dates(account)
    book_lines = list(JournalLine.objects.filter(account=account, entry__date__lte=as_of).select_related("entry")
                      .order_by("entry__date", "created_at"))
    book = _total(book_lines, lambda l: l.debit - l.credit)
    # A book line is cleared when the bank has shown it by this date (matched, marked cleared, or reconciled earlier).
    uncleared = [l for l in book_lines if l.reconciliation_id is None and l.entry.source != JournalEntry.Source.OPENING
                 and (l.bank_match_id is None or dates.get(l.bank_match_id, as_of) > as_of)]
    stmt_lines = list(BankStatementLine.objects.filter(account=account, date__lte=as_of).order_by("date", "seq"))
    not_in_books = [l for l in stmt_lines if l.status != BankStatementLine.Status.MATCHED or dates.get(l.match_id, as_of) > as_of]
    u_in = [l for l in uncleared if l.debit > 0]
    u_out = [l for l in uncleared if l.credit > 0]
    b_in = [l for l in not_in_books if l.amount > 0]
    b_out = [l for l in not_in_books if l.amount < 0]
    expected = book - _total(u_in, lambda l: l.debit) + _total(u_out, lambda l: l.credit) + _total(not_in_books, lambda l: l.amount)
    if statement_balance is not None:
        source, bal_date = "entered", as_of
    else:
        statement_balance, bal_date = statement_balance_on(account, as_of)
        source = "statement" if statement_balance is not None else None
    last = BankReconciliation.objects.filter(account=account).order_by("-statement_date", "-created_at").first()
    unmatched = [l for l in stmt_lines if l.status == BankStatementLine.Status.UNMATCHED]
    period = [l for l in stmt_lines if start is None or l.date >= start]
    first = BankStatementLine.objects.filter(account=account).order_by("date").values_list("date", flat=True).first()
    out = {
        "account": {"id": str(account.id), "name": account.name, "code": account.code},
        "as_of": as_of, "start": start, "book_balance": book,
        "uncleared_in": {"total": _total(u_in, lambda l: l.debit), "lines": [bank_matching.book_dict(l) for l in u_in]},
        "uncleared_out": {"total": _total(u_out, lambda l: l.credit), "lines": [bank_matching.book_dict(l) for l in u_out]},
        "bank_in": {"total": _total(b_in, lambda l: l.amount), "lines": [stmt_dict(l) for l in b_in]},
        "bank_out": {"total": _total(b_out, lambda l: -l.amount), "lines": [stmt_dict(l) for l in b_out]},
        "expected_bank_balance": ledger.money(expected),
        "statement_balance": statement_balance, "statement_balance_date": bal_date, "statement_balance_source": source,
        "difference": ledger.money(statement_balance - expected) if statement_balance is not None else None,
        "unmatched_count": len(unmatched),
        "last_reconciled": last.statement_date if last else None,
        "last_reconciliation_id": str(last.id) if last else None,
        "period_counts": {"lines": len(period), "matched": sum(1 for l in period if l.status == "matched"),
                          "ignored": sum(1 for l in period if l.status == "ignored"),
                          "unmatched": sum(1 for l in period if l.status == "unmatched")},
        "first_statement_date": first,
    }
    blockers = []
    if first is None:
        blockers.append("Import a statement for this account first.")
    if last and as_of <= last.statement_date:
        blockers.append(f"The account is already reconciled up to {last.statement_date:%d %b %Y}. Choose a later date.")
    if as_of > datetime.date.today():
        blockers.append("The date cannot be in the future.")
    if unmatched:
        many = len(unmatched) != 1
        blockers.append(f"{len(unmatched)} statement line{'s are' if many else ' is'} still to be matched or ignored up to this date.")
    if statement_balance is None:
        blockers.append("The statement shows no balance for this date. Enter the closing balance from the statement.")
    elif out["difference"] != 0:
        blockers.append(f"The statement balance and the books differ by {out['difference']}.")
    out["can_close"], out["blockers"] = not blockers, blockers
    return out


@transaction.atomic
def close(company, actor, *, account, as_of, statement_balance=None, notes=""):
    """Mark the account reconciled up to `as_of`: every statement line to that date is matched or ignored and the statement
    balance agrees with the books. The lines covered are locked (their matches cannot be undone) until this is undone."""
    if account.company_id != company.pk or account.subtype != Account.Subtype.BANK:
        raise ValidationError({"account": "Choose a bank account."})
    Account.objects.select_for_update().get(pk=account.pk)  # one reconciliation of an account at a time
    pos = position(account, as_of, statement_balance=statement_balance)
    if not pos["can_close"]:
        raise ValidationError({"as_of": " ".join(pos["blockers"])})
    dates = _match_dates(account)
    rec = BankReconciliation.objects.create(company=company, account=account, statement_date=as_of,
                                            statement_balance=pos["statement_balance"], book_balance=ZERO,
                                            notes=(notes or "Reconciled from the imported statement").strip()[:300], created_by=actor)
    j_ids = [l.pk for l in JournalLine.objects.filter(account=account, entry__date__lte=as_of, reconciliation__isnull=True).select_related("entry")
             if l.entry.source == JournalEntry.Source.OPENING or (l.bank_match_id and dates.get(l.bank_match_id, as_of) <= as_of)]
    s_ids = [l.pk for l in BankStatementLine.objects.filter(account=account, date__lte=as_of, reconciliation__isnull=True)
             if l.status == BankStatementLine.Status.IGNORED or (l.match_id and dates.get(l.match_id, as_of) <= as_of)]
    JournalLine.objects.filter(pk__in=j_ids).update(reconciliation=rec)
    BankStatementLine.objects.filter(pk__in=s_ids).update(reconciliation=rec)
    rec.book_balance = cleared_balance(account)
    rec.save(update_fields=["book_balance", "updated_at"])
    ledger.audit_event(company, actor, "reconciliation", rec.pk, CompanyProfileAuditEvent.Action.CREATED,
                       new={"account": account.name, "statement_date": str(as_of), "balance": str(pos["statement_balance"]),
                            "lines": len(j_ids), "statement_lines": len(s_ids)}, reason="Reconciled up to date")
    return rec
