"""Bank reconciliation: tick off the book lines that appear on the bank statement until the balances agree."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.company.models import CompanyProfileAuditEvent

from ..models import Account, BankReconciliation, JournalLine
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
    ledger.audit_event(rec.company, actor, "reconciliation", rec.pk, CompanyProfileAuditEvent.Action.DELETED,
                       previous={"account": rec.account.name, "statement_date": str(rec.statement_date)})
    rec.delete()
