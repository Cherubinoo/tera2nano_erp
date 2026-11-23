"""Posting to the books, reversing entries, and the balances reports are built from."""
import datetime
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit, numbering

from ..models import Account, JournalEntry, JournalLine
from . import chart

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
Source = JournalEntry.Source


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def amount(value, label="Amount", allow_zero=False):
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError({"amount": f"{label} must be a number."})
    if not d.is_finite() or d < 0 or (d == 0 and not allow_zero):
        raise ValidationError({"amount": f"{label} must be more than zero."})
    if d.as_tuple().exponent < -2:
        raise ValidationError({"amount": f"{label}: use at most 2 decimal places."})
    if d > Decimal("100000000000"):
        raise ValidationError({"amount": f"{label} is too large."})
    return d


def _side(value, n):
    """One side of a line: zero when empty, else an amount of at most two decimal places."""
    if value in (None, "", 0):
        return ZERO
    try:
        return amount(value, "Amount", allow_zero=True)
    except ValidationError:
        raise ValidationError({"lines": f"Line {n}: enter an amount of at most two decimal places."})


def resolve_account(company, ref):
    """An Account, a UUID (text) of one, or a system key such as "receivable"."""
    if isinstance(ref, Account):
        return ref
    if isinstance(ref, str):
        try:
            return Account.objects.filter(pk=uuid.UUID(ref), company=company).first()
        except ValueError:
            return chart.system_account(company, ref)
    return None


def audit_event(company, actor, entity, entity_id, action, *, new=None, previous=None, reason=""):
    changed, prev, cur = audit.diff(entity, previous or {}, new or {})
    audit.record_event(company, actor, entity, entity_id, action, changed=changed, previous=prev, new=cur, reason=reason,
                       category="accounting")


def open_financial_year(company, on_date):
    """The financial year covering `on_date` (created if missing), refusing a locked or closed one."""
    fy = numbering.ensure_financial_year(company, on_date)
    if fy.is_locked or fy.status == fy.Status.CLOSED:
        raise ValidationError({"date": f"Financial year {fy.label} is locked; nothing can be posted in it."})
    return fy


@transaction.atomic
def post(company, *, date, lines, narration="", source=Source.MANUAL, source_id="", reference="", actor=None, reverses=None):
    """Write a balanced journal entry. `lines`: [{account: Account | system key, debit, credit, memo, party}] ."""
    if not isinstance(date, datetime.date):
        raise ValidationError({"date": "Enter the date."})
    if len(lines) < 2:
        raise ValidationError({"lines": "A journal entry needs at least two lines."})
    fy = open_financial_year(company, date)
    resolved, debit_total, credit_total = [], ZERO, ZERO
    for n, line in enumerate(lines, 1):
        acct = resolve_account(company, line["account"])
        if acct is None or acct.company_id != company.pk:
            raise ValidationError({"lines": f"Line {n}: choose an account."})
        if not acct.is_active:
            raise ValidationError({"lines": f"Line {n}: {acct.code} {acct.name} is switched off."})
        debit, credit = _side(line.get("debit"), n), _side(line.get("credit"), n)
        if (debit > 0) == (credit > 0):
            raise ValidationError({"lines": f"Line {n}: enter either a debit or a credit, not both and not neither."})
        party = line.get("party") or {}
        resolved.append(dict(account=acct, debit=debit, credit=credit, memo=(line.get("memo") or "")[:300],
                             party_type=party.get("type", ""), party_id=str(party.get("id", "")),
                             project_id=line.get("project") or None))
        debit_total += debit
        credit_total += credit
    if debit_total != credit_total:
        raise ValidationError({"lines": f"The entry does not balance: debits {debit_total}, credits {credit_total}."})
    entry = JournalEntry.objects.create(
        company=company, date=date, reference=reference[:120], narration=(narration or "")[:500], source=source,
        source_id=str(source_id or ""), financial_year=fy, created_by=actor if getattr(actor, "is_authenticated", False) else None,
        reverses=reverses)
    JournalLine.objects.bulk_create([JournalLine(entry=entry, **r) for r in resolved])
    return entry


@transaction.atomic
def reverse(entry, actor, reason="", on_date=None):
    """Mirror an entry with debits and credits swapped. The original is marked reversed; nothing is deleted."""
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status == JournalEntry.Status.REVERSED or entry.reverses_id:
        raise ValidationError("This entry has already been reversed, or is itself a reversal.")
    lines = [{"account": l.account, "debit": l.credit, "credit": l.debit, "memo": l.memo,
              "party": {"type": l.party_type, "id": l.party_id}, "project": l.project_id}
             for l in entry.lines.select_related("account")]
    back = post(entry.company, date=on_date or datetime.date.today(), lines=lines, source=Source.REVERSAL,
                narration=f"Reversal of {entry.reference or entry.get_source_display()}" + (f": {reason}" if reason else ""),
                reference=entry.reference, actor=actor, reverses=entry)
    entry.status = JournalEntry.Status.REVERSED
    entry.save(update_fields=["status", "updated_at"])
    audit_event(entry.company, actor, "journal_entry", entry.pk, CompanyProfileAuditEvent.Action.DEACTIVATED,
                previous={"status": "posted"}, new={"status": "reversed"}, reason=reason)
    return back


def reverse_source(company, source, source_id, actor, reason=""):
    """Reverse the posting a business event made, if it made one (events from before the books existed made none)."""
    entry = JournalEntry.objects.filter(company=company, source=source, source_id=str(source_id)).first()
    if entry is None or entry.status == JournalEntry.Status.REVERSED:
        return None
    return reverse(entry, actor, reason)


# --------------------------------------------------------------------------- balances

def totals(company, *, start=None, end=None, account_ids=None):
    """{account id: (total debits, total credits)} for entries dated within [start, end]."""
    qs = JournalLine.objects.filter(entry__company=company)
    if start:
        qs = qs.filter(entry__date__gte=start)
    if end:
        qs = qs.filter(entry__date__lte=end)
    if account_ids is not None:
        qs = qs.filter(account_id__in=account_ids)
    return {row["account_id"]: (row["d"] or ZERO, row["c"] or ZERO)
            for row in qs.values("account_id").annotate(d=Sum("debit"), c=Sum("credit"))}


def natural_balance(account, debit, credit):
    """A balance shown the way the account normally runs: debit-normal accounts as debit minus credit, others the reverse."""
    return money(debit - credit) if account.is_debit_normal else money(credit - debit)
