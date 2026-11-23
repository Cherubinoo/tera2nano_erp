"""Partner accounting rules: transactions and their postings, balances, profit sharing, statements, loans.

Everything that moves money runs in a transaction with the rows it depends on locked, and posts to the books only through
`apps.accounting.services.ledger`. Amounts are `Decimal`, rounded to the paisa half-up.

How a partner's money is kept (the usual LLP presentation):
* capital account: what the partner put in and may only take out as capital (capital introduced, withdrawn, profit transferred in);
* current account: the partner's share of profit, interest and remuneration, less drawings;
* loan account: money lent to the LLP, repaid separately and earning interest at an agreed rate.
"""
import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounting.models import Account, JournalEntry
from apps.accounting.services import chart, ledger, reports
from apps.company.constants import DocType
from apps.company.models import CompanyDocument, CompanyPartner, CompanyProfileAuditEvent
from apps.company.services import audit, documents, numbering

from .models import (AllocationShare, PartnerAccount, PartnerSettings, PartnerTransaction, ProfitAllocation, ProfitShareLine,
                     ProfitShareSchedule)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
Kind, Status, Mode = PartnerTransaction.Kind, PartnerTransaction.Status, PartnerTransaction.Mode
Action = CompanyProfileAuditEvent.Action
Source = JournalEntry.Source
DAYS_IN_YEAR = Decimal("365")

# Ledger accounts the partner module creates when first needed (found again by the marker in their description).
ORDINARY = {
    "loan": ("partners:loan", "Partners' loans", Account.Subtype.LONG_TERM_LIABILITY, "2500"),
    "remuneration": ("partners:remuneration", "Partners' remuneration", Account.Subtype.EXPENSE, "5150"),
    "interest": ("partners:interest", "Interest on partners' capital and loans", Account.Subtype.OTHER_EXPENSE, "5750"),
}


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def amount_from(value, label="Amount", field="amount"):
    """A positive amount with at most two decimal places, from user input."""
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError({field: f"{label} must be a number."})
    if not d.is_finite() or d <= 0:
        raise ValidationError({field: f"{label} must be more than zero."})
    if d.as_tuple().exponent < -2:
        raise ValidationError({field: f"{label}: use at most 2 decimal places."})
    if d > Decimal("100000000000"):
        raise ValidationError({field: f"{label} is too large."})
    return d


def percent_from(value, field="rate", label="Rate", allow_zero=True):
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError({field: f"{label} must be a number."})
    if not d.is_finite() or d < 0 or d > 100 or (d == 0 and not allow_zero):
        raise ValidationError({field: f"{label} must be between {'0' if allow_zero else '0.01'} and 100."})
    if d.as_tuple().exponent < -2:
        raise ValidationError({field: f"{label}: use at most 2 decimal places."})
    return d


def audit_event(company, actor, entity, instance_id, action, *, new=None, previous=None, reason=""):
    audit.log(company, actor, entity, instance_id, action, new=new, previous=previous, reason=reason, category="partners")


def is_admin(user):
    return bool(user and (user.is_superuser or getattr(user, "role", "") == "admin"))


# --------------------------------------------------------------------------- settings and links

def get_settings(company):
    obj, _ = PartnerSettings.objects.get_or_create(company=company)
    return obj


def account_for(partner):
    obj, _ = PartnerAccount.objects.get_or_create(partner=partner, defaults={"company": partner.company})
    return obj


def partner_for_user(company, user):
    """The partner record of a signed-in user: linked explicitly, or whose official email is the user's email."""
    if not getattr(user, "is_authenticated", False):
        return None
    linked = PartnerAccount.objects.filter(company=company, user=user).select_related("partner").first()
    if linked:
        return linked.partner
    email = (getattr(user, "email", "") or "").strip()
    if email:
        return CompanyPartner.objects.filter(company=company, is_active=True, official_email__iexact=email).first()
    return None


# --------------------------------------------------------------------------- ledger accounts

def ordinary_account(company, key):
    marker, name, subtype, code = ORDINARY[key]
    acct = Account.objects.filter(company=company, description=marker).first()
    if acct is None and key == "loan":  # the standard chart already has "Partners' loans"
        acct = Account.objects.filter(company=company, subtype=subtype, name=name).first()
    if acct is None:
        chart.ensure_chart(company)
        acct = Account.objects.filter(company=company, description=marker).first() or (
            Account.objects.filter(company=company, subtype=subtype, name=name).first() if key == "loan" else None)
    if acct is None:
        used = set(Account.objects.filter(company=company).values_list("code", flat=True))
        while code in used:
            code = str(int(code) + 1)
        acct = Account.objects.create(company=company, code=code, name=name, type=Account.TYPE_OF[subtype], subtype=subtype,
                                      description=marker)
    return acct


def money_account_for(company, mode, explicit=None):
    if explicit is not None:
        if explicit.company_id != company.pk or explicit.subtype not in (Account.Subtype.CASH, Account.Subtype.BANK):
            raise ValidationError({"money_account": "Choose a cash or bank account."})
        if not explicit.is_active:
            raise ValidationError({"money_account": "This account is switched off."})
        return explicit
    return chart.system_account(company, "cash" if mode == Mode.CASH else "bank")


# --------------------------------------------------------------------------- balances

# How each kind changes the partner's capital, current and loan accounts (sign applied to the amount).
EFFECT = {
    Kind.CAPITAL_INTRODUCED: ("capital", 1), Kind.CAPITAL_WITHDRAWN: ("capital", -1),
    Kind.DRAWING: ("current", -1), Kind.INTEREST_ON_CAPITAL: ("current", 1), Kind.REMUNERATION: ("current", 1),
    Kind.PROFIT_SHARE_CREDIT: ("current", 1),
    Kind.LOAN_FROM_PARTNER: ("loan", 1), Kind.LOAN_REPAYMENT: ("loan", -1),
}


def _blank():
    return {"capital": ZERO, "current": ZERO, "loan": ZERO}


def _apply(bal, kind, total):
    if kind == Kind.PROFIT_SHARE_TRANSFER:
        bal["current"] -= total
        bal["capital"] += total
    else:
        account, sign = EFFECT[kind]
        bal[account] += total * sign


def balances(company, as_of=None, partner_ids=None, start=None):
    """{partner id: {capital, current, loan}} for posted entries dated within [start, as_of] (the whole history by default)."""
    out = {}
    tx = PartnerTransaction.objects.filter(company=company, status=Status.POSTED)
    sh = AllocationShare.objects.filter(allocation__company=company, allocation__status=ProfitAllocation.Status.POSTED)
    if as_of:
        tx, sh = tx.filter(date__lte=as_of), sh.filter(allocation__post_date__lte=as_of)
    if start:
        tx, sh = tx.filter(date__gte=start), sh.filter(allocation__post_date__gte=start)
    if partner_ids is not None:
        tx, sh = tx.filter(partner_id__in=partner_ids), sh.filter(partner_id__in=partner_ids)
    for row in tx.values("partner_id", "kind").annotate(t=Sum("amount")):
        _apply(out.setdefault(row["partner_id"], _blank()), row["kind"], row["t"])
    for row in sh.values("partner_id").annotate(t=Sum("amount")):
        out.setdefault(row["partner_id"], _blank())["current"] += row["t"]
    return out


def balance_of(company, partner, as_of=None):
    return balances(company, as_of, [partner.pk]).get(partner.pk, _blank())


# --------------------------------------------------------------------------- transactions

def _clean_date(value, field="date", required=True):
    if isinstance(value, datetime.date):
        return value
    if value in (None, ""):
        if required:
            raise ValidationError({field: "Enter the date."})
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({field: "Use a date in YYYY-MM-DD format."})


def interest_for(principal, rate, start, end):
    """Simple interest on `principal` at `rate` percent a year from `start` to `end` inclusive, on a 365-day year."""
    days = (end - start).days + 1
    return money(Decimal(principal) * Decimal(rate) * days / (HUNDRED * DAYS_IN_YEAR)), days


def interest_preview(company, partner, rate, start, end):
    """Interest on the partner's capital balance on the day before the period starts (the opening capital)."""
    if end < start:
        raise ValidationError({"period_to": "The period cannot end before it starts."})
    opening = balance_of(company, partner, start - datetime.timedelta(days=1))["capital"]
    interest, days = interest_for(max(opening, ZERO), rate, start, end)
    return {"partner": str(partner.pk), "opening_capital": opening, "rate": Decimal(rate), "days": days, "interest": interest}


def _lines_for(txn, company, money_acct):
    party = {"type": "partner", "id": str(txn.partner_id)}
    amt, k = txn.amount, txn.kind
    memo = f"{txn.get_kind_display()}: {txn.partner.full_name}"
    capital = chart.system_account(company, "capital")
    current = chart.system_account(company, "partner_current")

    def dr(acct):
        return {"account": acct, "debit": amt, "memo": memo, "party": party}

    def cr(acct):
        return {"account": acct, "credit": amt, "memo": memo, "party": party}
    # Every line is tagged with the partner so the books can be read per partner.
    if k == Kind.CAPITAL_INTRODUCED:
        return [dr(money_acct), cr(capital)]
    if k == Kind.CAPITAL_WITHDRAWN:
        return [dr(capital), cr(money_acct)]
    if k == Kind.DRAWING:
        return [dr(current), cr(money_acct)]
    if k == Kind.INTEREST_ON_CAPITAL:
        return [dr(ordinary_account(company, "interest")), cr(current)]
    if k == Kind.REMUNERATION:
        return [dr(ordinary_account(company, "remuneration")), cr(current)]
    if k == Kind.LOAN_FROM_PARTNER:
        return [dr(money_acct), cr(ordinary_account(company, "loan"))]
    if k == Kind.LOAN_REPAYMENT:
        return [dr(ordinary_account(company, "loan")), cr(money_acct)]
    if k == Kind.PROFIT_SHARE_CREDIT:
        return [dr(chart.system_account(company, "retained")), cr(current)]
    if k == Kind.PROFIT_SHARE_TRANSFER:
        return [dr(current), cr(capital)]
    raise ValidationError({"kind": "Unknown transaction type."})


def _post(txn, actor):
    company = txn.company
    money_acct = txn.money_account if txn.kind in PartnerTransaction.MONEY_KINDS else None
    entry = ledger.post(company, date=txn.date, lines=_lines_for(txn, company, money_acct), source=Source.PARTNER,
                        source_id=str(txn.pk), reference=txn.number, actor=actor,
                        narration=f"{txn.get_kind_display()}: {txn.partner.full_name}" + (f" ({txn.narration})" if txn.narration else ""))
    txn.entry = entry
    txn.status = Status.POSTED
    txn.save(update_fields=["entry", "status", "updated_at"])


def snapshot(txn):
    return {"number": txn.number, "partner": txn.partner.full_name, "kind": txn.kind, "date": txn.date.isoformat(),
            "amount": str(txn.amount), "status": txn.status, "mode": txn.mode, "reference": txn.reference,
            "narration": txn.narration, "rate": str(txn.rate) if txn.rate is not None else None}


def _check_balances(company, partner, kind, amount, date, exclude=None):
    bal = balance_of(company, partner, date)
    if kind == Kind.CAPITAL_WITHDRAWN and amount > bal["capital"]:
        raise ValidationError({"amount": f"{partner.full_name}'s capital on {date:%d %b %Y} is {bal['capital']}; "
                                         "more than that cannot be withdrawn."})
    if kind == Kind.LOAN_REPAYMENT and amount > bal["loan"]:
        raise ValidationError({"amount": f"{partner.full_name}'s loan outstanding on {date:%d %b %Y} is {bal['loan']}; "
                                         "more than that cannot be repaid."})
    if kind == Kind.PROFIT_SHARE_TRANSFER and amount > bal["current"]:
        raise ValidationError({"amount": f"{partner.full_name}'s current account on {date:%d %b %Y} is {bal['current']}; "
                                         "more than that cannot be moved to capital."})


@transaction.atomic
def create_transaction(company, actor, *, partner, kind, date, amount, mode="", money_account=None, reference="", narration="",
                       rate=None, period_from=None, period_to=None):
    if kind not in Kind.values:
        raise ValidationError({"kind": "Choose the type of transaction."})
    date = _clean_date(date)
    partner = CompanyPartner.objects.select_for_update().get(pk=partner.pk)  # one transaction per partner at a time
    if partner.company_id != company.pk:
        raise ValidationError({"partner": "Not found."})
    reference, narration = (reference or "").strip(), (narration or "").strip()
    if len(reference) > 100:
        raise ValidationError({"reference": "Keep the reference to 100 characters."})
    if len(narration) > 300:
        raise ValidationError({"narration": "Keep the narration to 300 characters."})
    rate = None if rate in (None, "") else percent_from(rate)
    period_from, period_to = _clean_date(period_from, "period_from", False), _clean_date(period_to, "period_to", False)
    if kind == Kind.INTEREST_ON_CAPITAL:
        if rate is not None and period_from and period_to and amount in (None, ""):
            amount = interest_preview(company, partner, rate, period_from, period_to)["interest"]
            if amount <= 0:
                raise ValidationError({"amount": "The interest on this capital for the period works out to nothing."})
        if period_from and period_to and period_to < period_from:
            raise ValidationError({"period_to": "The period cannot end before it starts."})
    elif kind != Kind.LOAN_FROM_PARTNER:
        rate = period_from = period_to = None
    amount = money(amount_from(amount))
    if kind in PartnerTransaction.MONEY_KINDS:
        mode = mode or Mode.BANK_TRANSFER
        if mode not in Mode.values:
            raise ValidationError({"mode": "Choose how the money moved."})
        acct = money_account_for(company, mode, money_account)
    else:
        mode, acct = "", None
    _check_balances(company, partner, kind, amount, date)
    threshold = get_settings(company).withdrawal_approval_threshold
    needs_approval = kind in PartnerTransaction.WITHDRAWALS and threshold > 0 and amount > threshold
    number = numbering.allocate_number(company, DocType.PARTNER_TRANSACTION, actor=actor, on_date=date).reference_number
    txn = PartnerTransaction.objects.create(
        company=company, partner=partner, number=number, kind=kind, date=date, amount=amount, mode=mode, money_account=acct,
        reference=reference, narration=narration, rate=rate, period_from=period_from, period_to=period_to,
        status=Status.PENDING if needs_approval else Status.POSTED, requested_by=actor if getattr(actor, "is_authenticated", False) else None)
    if needs_approval:
        # Keep the local pending state as the source of truth while the shared workflow decides.
        # If no rule applies, its auto-approval callback posts the transaction in this same transaction.
        try:
            from .approvals_hook import submit
            request_obj = submit(txn)
        except ImportError:
            request_obj = None
        if request_obj is not None:
            txn.approval_request_id = request_obj.pk
            txn.save(update_fields=["approval_request_id", "updated_at"])
            if getattr(request_obj, "status", None) == "approved" and txn.status == Status.PENDING:
                # Defensive fallback for an auto-approved request if callback registration was unavailable.
                _post(txn, actor)
    else:
        _post(txn, actor)
    audit_event(company, actor, "partner_transaction", txn.pk, Action.CREATED, new=snapshot(txn))
    return txn


def _close_request(txn, actor):
    """Decided from this module: an approval request still open for it is closed so it does not wait in somebody's inbox."""
    from .approvals_hook import close_request
    close_request(txn, actor)


def _lock(txn):
    return PartnerTransaction.objects.select_for_update(of=("self",)).select_related("partner", "company").get(pk=txn.pk)


def _own_account(txn, actor):
    link = PartnerAccount.objects.filter(partner_id=txn.partner_id, user=actor).exists()
    email = (getattr(actor, "email", "") or "").strip().lower()
    return link or bool(email and txn.partner.official_email.strip().lower() == email)


@transaction.atomic
def approve(txn, actor):
    """Release a withdrawal that was waiting. Only an administrator may approve their own request or their own account's."""
    txn = _lock(txn)
    if txn.status != Status.PENDING:
        raise ValidationError("Only a transaction that is waiting for approval can be approved.")
    # The shared approvals callback supplies no actor in its current contract. In that case the
    # workflow has already enforced its approver/self-approval rules; direct actions always pass a user.
    if actor is not None and not is_admin(actor) and (txn.requested_by_id == actor.pk or _own_account(txn, actor)):
        raise ValidationError("You cannot approve a withdrawal from your own account or one you requested. Another approver is needed.")
    _check_balances(txn.company, txn.partner, txn.kind, txn.amount, txn.date)
    txn.decided_by, txn.decided_at = actor, timezone.now()
    txn.save(update_fields=["decided_by", "decided_at", "updated_at"])
    _post(txn, actor)
    audit_event(txn.company, actor, "partner_transaction", txn.pk, Action.VERIFIED, previous={"status": Status.PENDING},
                new={"status": txn.status})
    _close_request(txn, actor)
    return txn


@transaction.atomic
def reject(txn, actor, reason):
    txn = _lock(txn)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Give the reason for rejecting this."})
    if txn.status != Status.PENDING:
        raise ValidationError("Only a transaction that is waiting for approval can be rejected.")
    if not is_admin(actor) and (txn.requested_by_id == actor.pk or _own_account(txn, actor)):
        raise ValidationError("You cannot decide on a withdrawal from your own account or one you requested.")
    txn.status, txn.decided_by, txn.decided_at, txn.decision_reason = Status.REJECTED, actor, timezone.now(), reason[:300]
    txn.save(update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"])
    audit_event(txn.company, actor, "partner_transaction", txn.pk, Action.DEACTIVATED, previous={"status": Status.PENDING},
                new={"status": Status.REJECTED}, reason=reason)
    _close_request(txn, actor)
    return txn


@transaction.atomic
def void(txn, actor, reason):
    txn = _lock(txn)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Give the reason for voiding this."})
    if txn.status not in (Status.POSTED, Status.PENDING):
        raise ValidationError("Only a posted or waiting transaction can be voided.")
    before = txn.status
    if before == Status.POSTED:
        _void_would_break(txn)
        ledger.reverse_source(txn.company, Source.PARTNER, txn.pk, actor, reason)
    txn.status, txn.decision_reason = Status.VOID, reason[:300]
    txn.save(update_fields=["status", "decision_reason", "updated_at"])
    audit_event(txn.company, actor, "partner_transaction", txn.pk, Action.DEACTIVATED, previous={"status": before},
                new={"status": Status.VOID}, reason=reason)
    if before == Status.PENDING:
        _close_request(txn, actor)
    return txn


def _void_would_break(txn):
    """Voiding capital introduced or a loan must not leave the partner's account negative."""
    if txn.kind == Kind.CAPITAL_INTRODUCED and balance_of(txn.company, txn.partner)["capital"] - txn.amount < 0:
        raise ValidationError("Voiding this would leave the partner's capital negative; void the later withdrawals first.")
    if txn.kind == Kind.LOAN_FROM_PARTNER and balance_of(txn.company, txn.partner)["loan"] - txn.amount < 0:
        raise ValidationError("Voiding this would leave the partner's loan negative; void the later repayments first.")


@transaction.atomic
def update_notes(txn, actor, *, reference=None, narration=None):
    """Reference and narration are the only things that can change after the money has moved."""
    txn = _lock(txn)
    if txn.status == Status.VOID:
        raise ValidationError("A voided transaction cannot be changed.")
    before = snapshot(txn)
    if reference is not None:
        if len(reference.strip()) > 100:
            raise ValidationError({"reference": "Keep the reference to 100 characters."})
        txn.reference = reference.strip()
    if narration is not None:
        if len(narration.strip()) > 300:
            raise ValidationError({"narration": "Keep the narration to 300 characters."})
        txn.narration = narration.strip()
    txn.save(update_fields=["reference", "narration", "updated_at"])
    if snapshot(txn) != before:
        audit_event(txn.company, actor, "partner_transaction", txn.pk, Action.UPDATED, previous=before, new=snapshot(txn))
    return txn


# --------------------------------------------------------------------------- profit-sharing ratio

def schedule_on(company, on_date):
    return ProfitShareSchedule.objects.filter(company=company, effective_from__lte=on_date).order_by("-effective_from").first()


def schedule_dict(s):
    lines = list(s.lines.select_related("partner"))
    return {"id": str(s.id), "effective_from": s.effective_from, "note": s.note,
            "total": sum((l.percent for l in lines), ZERO),
            "used": s.allocations.exclude(status=ProfitAllocation.Status.REVERSED).exists() if s.pk else False,
            "shares": [{"partner": str(l.partner_id), "name": l.partner.full_name, "percent": l.percent} for l in lines]}


@transaction.atomic
def save_schedule(company, actor, *, effective_from, shares, note=""):
    effective_from = _clean_date(effective_from, "effective_from")
    if not isinstance(shares, list) or not shares:
        raise ValidationError({"shares": "Give each partner's share."})
    seen, total, rows = set(), ZERO, []
    for n, row in enumerate(shares, 1):
        if not isinstance(row, dict):
            raise ValidationError({"shares": f"Line {n} is not valid."})
        try:
            partner = CompanyPartner.objects.get(pk=row.get("partner"), company=company)
        except (CompanyPartner.DoesNotExist, ValueError, ValidationError, TypeError):
            raise ValidationError({"shares": f"Line {n}: choose a partner."})
        if partner.pk in seen:
            raise ValidationError({"shares": f"{partner.full_name} appears more than once."})
        seen.add(partner.pk)
        pct = percent_from(row.get("percent"), "shares", f"{partner.full_name}'s share", allow_zero=False)
        total += pct
        rows.append((partner, pct))
    if total != HUNDRED:
        raise ValidationError({"shares": f"The shares add up to {total}%; they must add up to exactly 100%."})
    if ProfitShareSchedule.objects.filter(company=company, effective_from=effective_from).exists():
        raise ValidationError({"effective_from": "A ratio already starts on this date. Choose another date or remove that one."})
    note = (note or "").strip()
    if len(note) > 300:
        raise ValidationError({"note": "Keep the note to 300 characters."})
    s = ProfitShareSchedule.objects.create(company=company, effective_from=effective_from, note=note,
                                           created_by=actor if getattr(actor, "is_authenticated", False) else None)
    ProfitShareLine.objects.bulk_create([ProfitShareLine(schedule=s, partner=p, percent=pct) for p, pct in rows])
    audit_event(company, actor, "partner_profit_share", s.pk, Action.CREATED,
                new={"effective_from": effective_from.isoformat(), "shares": ", ".join(f"{p.full_name} {pct}%" for p, pct in rows)})
    return s


@transaction.atomic
def delete_schedule(schedule, actor):
    if schedule.allocations.exists():
        raise ValidationError("A profit allocation uses this ratio, so it cannot be removed. Add a newer ratio instead.")
    snap = {"effective_from": schedule.effective_from.isoformat()}
    company, pk = schedule.company, schedule.pk
    schedule.delete()
    audit_event(company, actor, "partner_profit_share", pk, Action.DELETED, previous=snap)


# --------------------------------------------------------------------------- profit allocation

def apportion(total, lines):
    """Split `total` by percentage. Each share is rounded half-up to the paisa; what is left over (or short) after rounding
    goes to the partner with the largest share (the first by name when shares are equal), so the parts always add up exactly.
    `lines`: [(partner, percent)]. Returns [(partner, percent, amount)] in the same order."""
    sign = -1 if total < 0 else 1
    absolute = abs(Decimal(total))
    amounts = [money(absolute * pct / HUNDRED) for _p, pct in lines]
    remainder = absolute - sum(amounts, ZERO)
    if remainder != 0 and lines:
        best = min(range(len(lines)), key=lambda i: (-lines[i][1], lines[i][0].full_name))
        amounts[best] += remainder
    return [(p, pct, amt * sign) for (p, pct), amt in zip(lines, amounts)]


def compute_allocation(company, start, end, *, other_deductions=ZERO):
    """Preview a profit allocation: nothing is saved."""
    if end < start:
        raise ValidationError({"period_end": "The period cannot end before it starts."})
    schedule = schedule_on(company, end)
    if schedule is None:
        raise ValidationError({"period_end": "Set the profit-sharing ratio (effective on or before the end of the period) first."})
    pl = reports.profit_and_loss(company, start, end)
    net = pl["net_profit"]
    charged = PartnerTransaction.objects.filter(company=company, status=Status.POSTED, date__gte=start, date__lte=end)
    rem = charged.filter(kind=Kind.REMUNERATION).aggregate(t=Sum("amount"))["t"] or ZERO
    interest = charged.filter(kind=Kind.INTEREST_ON_CAPITAL).aggregate(t=Sum("amount"))["t"] or ZERO
    distributable = money(net - other_deductions)
    lines = [(l.partner, l.percent) for l in schedule.lines.select_related("partner")]
    shares = apportion(distributable, lines)
    changes = ProfitShareSchedule.objects.filter(company=company, effective_from__gt=start, effective_from__lte=end).exclude(pk=schedule.pk)
    warnings = []
    if schedule.effective_from > start:
        warnings.append(f"The ratio changes on {schedule.effective_from:%d %b %Y}, inside this period. The ratio in force at the end "
                        "of the period is applied to the whole period.")
    if changes.exists():
        warnings.append("More than one ratio change falls within this period. The ratio in force at the end is applied to all of it.")
    overlap = ProfitAllocation.objects.filter(company=company, status=ProfitAllocation.Status.POSTED, period_start__lte=end,
                                              period_end__gte=start).first()
    if overlap:
        warnings.append(f"A profit allocation for {overlap.period_start:%d %b %Y} to {overlap.period_end:%d %b %Y} is already posted; "
                        "this one overlaps it.")
    if distributable == 0:
        warnings.append("There is no profit to share for this period.")
    return {"period_start": start, "period_end": end, "net_profit": net, "remuneration_charged": rem, "interest_charged": interest,
            "other_deductions": other_deductions, "distributable": distributable, "schedule": schedule_dict(schedule),
            "shares": [{"partner": str(p.pk), "name": p.full_name, "percent": pct, "amount": amt} for p, pct, amt in shares],
            "warnings": warnings, "is_loss": distributable < 0}


def _deduction(value, note):
    if value in (None, "", 0, "0"):
        return ZERO
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError({"other_deductions": "Enter a number."})
    if not d.is_finite() or d.as_tuple().exponent < -2 or abs(d) > Decimal("100000000000"):
        raise ValidationError({"other_deductions": "Enter an amount of at most two decimal places."})
    return d


def _fill(alloc, computed):
    alloc.net_profit, alloc.distributable = computed["net_profit"], computed["distributable"]
    alloc.remuneration_charged, alloc.interest_charged = computed["remuneration_charged"], computed["interest_charged"]
    alloc.other_deductions = computed["other_deductions"]
    alloc.schedule_id = computed["schedule"]["id"]


def _save_shares(alloc, computed):
    alloc.shares.all().delete()
    AllocationShare.objects.bulk_create([
        AllocationShare(allocation=alloc, partner_id=s["partner"], percent=s["percent"], amount=s["amount"]) for s in computed["shares"]])


@transaction.atomic
def save_allocation(company, actor, *, period_start, period_end, other_deductions=None, deduction_note="", alloc=None):
    start, end = _clean_date(period_start, "period_start"), _clean_date(period_end, "period_end")
    if alloc is not None and alloc.status != ProfitAllocation.Status.DRAFT:
        raise ValidationError("Only a draft can be changed.")
    deductions = _deduction(other_deductions, deduction_note)
    computed = compute_allocation(company, start, end, other_deductions=deductions)
    note = (deduction_note or "").strip()
    if len(note) > 300:
        raise ValidationError({"deduction_note": "Keep the note to 300 characters."})
    creating = alloc is None
    if creating:
        alloc = ProfitAllocation(company=company, created_by=actor if getattr(actor, "is_authenticated", False) else None)
    alloc.period_start, alloc.period_end, alloc.deduction_note = start, end, note
    _fill(alloc, computed)
    alloc.save()
    _save_shares(alloc, computed)
    audit_event(company, actor, "partner_allocation", alloc.pk, Action.CREATED if creating else Action.UPDATED,
                new=allocation_snapshot(alloc))
    return alloc


def allocation_snapshot(a):
    return {"period": f"{a.period_start.isoformat()} to {a.period_end.isoformat()}", "net_profit": str(a.net_profit),
            "other_deductions": str(a.other_deductions), "distributable": str(a.distributable), "status": a.status}


@transaction.atomic
def post_allocation(alloc, actor, post_date=None):
    alloc = ProfitAllocation.objects.select_for_update(of=("self",)).select_related("company", "schedule").get(pk=alloc.pk)
    if alloc.status != ProfitAllocation.Status.DRAFT:
        raise ValidationError("Only a draft can be posted.")
    company = alloc.company
    post_date = _clean_date(post_date, "post_date", False) or alloc.period_end
    if alloc.distributable == 0:
        raise ValidationError("There is no profit or loss to share, so nothing can be posted.")
    if ProfitAllocation.objects.filter(company=company, status=ProfitAllocation.Status.POSTED, period_start__lte=alloc.period_end,
                                       period_end__gte=alloc.period_start).exists():
        raise ValidationError("A profit allocation covering part of this period is already posted. Reverse it first.")
    shares = list(alloc.shares.select_related("partner"))
    retained = chart.system_account(company, "retained")
    current = chart.system_account(company, "partner_current")
    memo = f"Profit share {alloc.period_start:%d %b %Y} to {alloc.period_end:%d %b %Y}"
    lines, total = [], ZERO
    for s in shares:
        if s.amount == 0:
            continue
        party = {"type": "partner", "id": str(s.partner_id)}
        if s.amount > 0:
            lines.append({"account": current, "credit": s.amount, "memo": f"{memo}: {s.partner.full_name}", "party": party})
        else:
            lines.append({"account": current, "debit": -s.amount, "memo": f"Loss share {memo[12:]}: {s.partner.full_name}", "party": party})
        total += s.amount
    lines.append({"account": retained, **({"debit": total} if total > 0 else {"credit": -total}), "memo": memo})
    alloc.number = numbering.allocate_number(company, DocType.PARTNER_TRANSACTION, actor=actor, on_date=post_date).reference_number
    entry = ledger.post(company, date=post_date, lines=lines, source=Source.PARTNER, source_id=str(alloc.pk), reference=alloc.number,
                        actor=actor, narration=f"Profit allocation: {memo}")
    alloc.entry, alloc.status, alloc.post_date, alloc.posted_by = entry, ProfitAllocation.Status.POSTED, post_date, actor
    alloc.save()
    audit_event(company, actor, "partner_allocation", alloc.pk, Action.ISSUED, previous={"status": "draft"}, new=allocation_snapshot(alloc))
    return alloc


@transaction.atomic
def reverse_allocation(alloc, actor, reason):
    alloc = ProfitAllocation.objects.select_for_update(of=("self",)).select_related("company").get(pk=alloc.pk)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Give the reason for reversing this."})
    if alloc.status != ProfitAllocation.Status.POSTED:
        raise ValidationError("Only a posted allocation can be reversed.")
    ledger.reverse_source(alloc.company, Source.PARTNER, alloc.pk, actor, reason)
    alloc.status, alloc.reverse_reason = ProfitAllocation.Status.REVERSED, reason[:300]
    alloc.save(update_fields=["status", "reverse_reason", "updated_at"])
    audit_event(alloc.company, actor, "partner_allocation", alloc.pk, Action.DEACTIVATED, previous={"status": "posted"},
                new={"status": "reversed"}, reason=reason)
    return alloc


@transaction.atomic
def delete_allocation(alloc, actor):
    if alloc.status != ProfitAllocation.Status.DRAFT:
        raise ValidationError("Only a draft can be deleted. Reverse a posted allocation instead.")
    snap, company, pk = allocation_snapshot(alloc), alloc.company, alloc.pk
    alloc.delete()
    audit_event(company, actor, "partner_allocation", pk, Action.DELETED, previous=snap)


# --------------------------------------------------------------------------- statements

MOVEMENTS = [
    ("capital_introduced", "Capital introduced"), ("capital_withdrawn", "Capital withdrawn"),
    ("profit_share", "Share of profit"), ("transfer", "Profit transferred to capital"), ("interest", "Interest on capital"),
    ("remuneration", "Remuneration"), ("drawings", "Drawings"),
]


def _movement_values(bal_by_kind):
    """From {kind: total} (and the allocation total under 'allocation') to {movement key: (capital effect, current effect)}."""
    g = lambda k: bal_by_kind.get(k, ZERO)
    return {
        "capital_introduced": (g(Kind.CAPITAL_INTRODUCED), ZERO), "capital_withdrawn": (-g(Kind.CAPITAL_WITHDRAWN), ZERO),
        "profit_share": (ZERO, g("allocation") + g(Kind.PROFIT_SHARE_CREDIT)),
        "transfer": (g(Kind.PROFIT_SHARE_TRANSFER), -g(Kind.PROFIT_SHARE_TRANSFER)),
        "interest": (ZERO, g(Kind.INTEREST_ON_CAPITAL)), "remuneration": (ZERO, g(Kind.REMUNERATION)), "drawings": (ZERO, -g(Kind.DRAWING)),
    }


KIND_MOVEMENT = {
    Kind.CAPITAL_INTRODUCED: "capital_introduced", Kind.CAPITAL_WITHDRAWN: "capital_withdrawn", Kind.DRAWING: "drawings",
    Kind.INTEREST_ON_CAPITAL: "interest", Kind.REMUNERATION: "remuneration", Kind.PROFIT_SHARE_CREDIT: "profit_share",
    Kind.PROFIT_SHARE_TRANSFER: "transfer",
}


def statement(company, partner, start, end):
    """Capital account statement: opening, each kind of movement, closing; plus every posting in date order."""
    if end < start:
        raise ValidationError({"to": "The period cannot end before it starts."})
    opening = balance_of(company, partner, start - datetime.timedelta(days=1))
    totals = {}
    for row in (PartnerTransaction.objects.filter(company=company, partner=partner, status=Status.POSTED, date__gte=start, date__lte=end)
                .exclude(kind__in=(Kind.LOAN_FROM_PARTNER, Kind.LOAN_REPAYMENT)).values("kind").annotate(t=Sum("amount"))):
        totals[row["kind"]] = row["t"]
    shares = AllocationShare.objects.filter(partner=partner, allocation__status=ProfitAllocation.Status.POSTED,
                                            allocation__post_date__gte=start, allocation__post_date__lte=end)
    totals["allocation"] = shares.aggregate(t=Sum("amount"))["t"] or ZERO
    values = _movement_values(totals)
    movements = [{"key": k, "label": label, "capital": values[k][0], "current": values[k][1], "total": values[k][0] + values[k][1]}
                 for k, label in MOVEMENTS]
    closing_capital = opening["capital"] + sum((m["capital"] for m in movements), ZERO)
    closing_current = opening["current"] + sum((m["current"] for m in movements), ZERO)
    # Every dated line, with a running total of capital + current.
    events = []
    for t in PartnerTransaction.objects.filter(company=company, partner=partner, status=Status.POSTED, date__gte=start, date__lte=end) \
            .exclude(kind__in=(Kind.LOAN_FROM_PARTNER, Kind.LOAN_REPAYMENT)).order_by("date", "created_at"):
        cap, cur = _movement_values({t.kind: t.amount})[KIND_MOVEMENT[t.kind]]
        events.append({"date": t.date, "number": t.number, "description": t.get_kind_display() + (f": {t.narration}" if t.narration else ""),
                       "capital": cap, "current": cur, "sort": (t.date, t.created_at)})
    for s in shares.select_related("allocation").order_by("allocation__post_date"):
        events.append({"date": s.allocation.post_date, "number": s.allocation.number,
                       "description": f"Share of profit {s.allocation.period_start:%d %b %Y} to {s.allocation.period_end:%d %b %Y} ({s.percent}%)",
                       "capital": ZERO, "current": s.amount, "sort": (s.allocation.post_date, s.allocation.created_at)})
    events.sort(key=lambda e: e["sort"])
    running, rows = opening["capital"] + opening["current"], []
    for e in events:
        running += e["capital"] + e["current"]
        e.pop("sort")
        rows.append({**e, "total": e["capital"] + e["current"], "balance": running})
    return {"partner": {"id": str(partner.pk), "name": partner.full_name, "designation": partner.designation},
            "start": start, "end": end,
            "opening": {"capital": opening["capital"], "current": opening["current"], "total": opening["capital"] + opening["current"]},
            "movements": movements,
            "closing": {"capital": closing_capital, "current": closing_current, "total": closing_capital + closing_current},
            "rows": rows, "pdf_available": DocType.PARTNER_TRANSACTION in DocType.PRINTABLE}


def statement_pdf(company, actor, partner, start, end):
    """Issue the statement as a numbered PDF. Needs PARTNER_TRANSACTION among the printable document types."""
    if DocType.PARTNER_TRANSACTION not in DocType.PRINTABLE:
        raise ValidationError("The PDF statement is not switched on for this installation yet. Download the CSV instead.")
    st = statement(company, partner, start, end)
    p = lambda v: f"{Decimal(v):,.2f}"
    columns = ["Particulars", "Capital account", "Current account", "Total"]
    rows = [["Opening balance", p(st["opening"]["capital"]), p(st["opening"]["current"]), p(st["opening"]["total"])]]
    rows += [[m["label"], p(m["capital"]), p(m["current"]), p(m["total"])] for m in st["movements"]
             if m["capital"] or m["current"]]
    rows.append(["Closing balance", p(st["closing"]["capital"]), p(st["closing"]["current"]), p(st["closing"]["total"])])
    content = {
        "recipient": [partner.full_name, partner.designation] if partner.designation else [partner.full_name],
        "subject": f"Capital account statement: {start:%d %b %Y} to {end:%d %b %Y}",
        "meta": [["Partner", partner.full_name], ["Period", f"{start:%d %b %Y} to {end:%d %b %Y}"]],
        "paragraphs": ["Amounts are in rupees."],
        "table": {"columns": columns, "rows": rows, "numeric_columns": [1, 2, 3], "column_widths_mm": [70, 36, 36, 36]},
    }
    return documents.issue_document(DocType.PARTNER_TRANSACTION, actor=actor, content=content, title="Partner capital account statement",
                                    issue_date=datetime.date.today(), source_module="partners", source_id=str(partner.pk))


def history(company, start, end, partner=None):
    """Capital and current account balances at the end of each month in [start, end]."""
    months, d = [], start.replace(day=1)
    while d <= end:
        nxt = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        months.append((d, min(nxt - datetime.timedelta(days=1), end)))
        d = nxt
    ids = [partner.pk] if partner else None
    out = []
    for first, last in months:
        bal = balances(company, last, ids)
        out.append({"month": f"{first:%Y-%m}", "label": first.strftime("%b %y"), "end": last,
                    "capital": sum((b["capital"] for b in bal.values()), ZERO), "current": sum((b["current"] for b in bal.values()), ZERO),
                    "per_partner": {str(k): b["capital"] for k, b in bal.items()}})
    return out


# --------------------------------------------------------------------------- loans

def loan_ledger(company, as_of, partner=None):
    """Per partner: loans, repayments, outstanding, and an estimate of interest accrued to `as_of`.

    Interest is simple interest at the rate each loan carries on a 365-day year; repayments reduce the oldest loan first.
    It is an estimate for planning: interest is not posted to the books unless someone records it."""
    qs = PartnerTransaction.objects.filter(company=company, status=Status.POSTED, kind__in=(Kind.LOAN_FROM_PARTNER, Kind.LOAN_REPAYMENT),
                                           date__lte=as_of).select_related("partner").order_by("date", "created_at")
    if partner:
        qs = qs.filter(partner=partner)
    by_partner = {}
    for t in qs:
        by_partner.setdefault(t.partner, []).append(t)
    rows = []
    for p, events in sorted(by_partner.items(), key=lambda kv: kv[0].full_name):
        loans, cursor, accrued, lent, repaid = [], None, ZERO, ZERO, ZERO
        exact = Decimal(0)

        def accrue(until):
            nonlocal exact
            if cursor is None:
                return
            days = (until - cursor).days
            for ln in loans:
                if ln["remaining"] > 0 and days > 0:
                    exact += ln["remaining"] * ln["rate"] * days / (HUNDRED * DAYS_IN_YEAR)
        for t in events:
            accrue(t.date)
            cursor = t.date
            if t.kind == Kind.LOAN_FROM_PARTNER:
                lent += t.amount
                loans.append({"remaining": t.amount, "rate": t.rate or ZERO, "date": t.date})
            else:
                repaid += t.amount
                left = t.amount
                for ln in loans:
                    use = min(ln["remaining"], left)
                    ln["remaining"] -= use
                    left -= use
        accrue(as_of)
        outstanding = lent - repaid
        rows.append({"partner": {"id": str(p.pk), "name": p.full_name}, "lent": lent, "repaid": repaid, "outstanding": outstanding,
                     "interest_estimate": money(exact),
                     "open_loans": [{"date": ln["date"], "remaining": ln["remaining"], "rate": ln["rate"]} for ln in loans if ln["remaining"] > 0]})
    return {"as_of": as_of, "rows": rows, "total_outstanding": sum((r["outstanding"] for r in rows), ZERO),
            "total_interest_estimate": sum((r["interest_estimate"] for r in rows), ZERO)}


# --------------------------------------------------------------------------- dashboard and lists

def partner_rows(company, as_of=None):
    """Every partner with balances and the share of capital against the profit-sharing ratio in force."""
    as_of = as_of or datetime.date.today()
    bal = balances(company, as_of)
    schedule = schedule_on(company, as_of)
    ratio = {l.partner_id: l.percent for l in schedule.lines.all()} if schedule else {}
    partners = list(CompanyPartner.objects.filter(company=company).select_related("capital_account__user").order_by("-is_active", "full_name"))
    total_cap = sum((bal.get(p.pk, _blank())["capital"] for p in partners), ZERO)
    rows = []
    for p in partners:
        b = bal.get(p.pk, _blank())
        acct = getattr(p, "capital_account", None)
        share = (b["capital"] / total_cap * HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP) if total_cap > 0 else ZERO
        rows.append({"id": str(p.pk), "name": p.full_name, "designation": p.designation, "partner_type": p.partner_type,
                     "is_active": p.is_active, "capital": b["capital"], "current": b["current"], "loan": b["loan"],
                     "total": b["capital"] + b["current"], "contribution_percent": share,
                     "profit_share_percent": ratio.get(p.pk, ZERO), "agreed_capital": acct.agreed_capital if acct else ZERO,
                     "user": str(acct.user_id) if acct and acct.user_id else None,
                     "user_name": acct.user.get_username() if acct and acct.user_id else ""})
    return rows


def dashboard(company, today=None):
    today = today or datetime.date.today()
    fy_start, fy_end = reports.default_period(company, today)
    rows = partner_rows(company, today)
    active = [r for r in rows if r["is_active"]]
    schedule = schedule_on(company, today)
    months, d = [], fy_start.replace(day=1)
    while d <= fy_end:
        months.append(d)
        d = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    drawn = {m.strftime("%Y-%m"): ZERO for m in months}
    for t in PartnerTransaction.objects.filter(company=company, status=Status.POSTED, kind=Kind.DRAWING, date__gte=fy_start, date__lte=fy_end):
        drawn[t.date.strftime("%Y-%m")] += t.amount
    ytd_shares = AllocationShare.objects.filter(allocation__company=company, allocation__status=ProfitAllocation.Status.POSTED,
                                                allocation__post_date__gte=fy_start, allocation__post_date__lte=fy_end).aggregate(t=Sum("amount"))["t"] or ZERO
    ytd_manual = PartnerTransaction.objects.filter(company=company, status=Status.POSTED, kind=Kind.PROFIT_SHARE_CREDIT, date__gte=fy_start,
                                                   date__lte=fy_end).aggregate(t=Sum("amount"))["t"] or ZERO
    loans = loan_ledger(company, today)
    pending = PartnerTransaction.objects.filter(company=company, status=Status.PENDING)
    recent = PartnerTransaction.objects.filter(company=company).select_related("partner")[:8]
    return {
        "today": today, "currency": company.default_currency, "period": {"start": fy_start, "end": fy_end},
        "total_capital": sum((r["capital"] for r in rows), ZERO), "total_current": sum((r["current"] for r in rows), ZERO),
        "partner_count": len(active), "loans_outstanding": loans["total_outstanding"],
        "drawings_this_fy": sum(drawn.values(), ZERO), "profit_share_ytd": ytd_shares + ytd_manual,
        "pending_approvals": pending.count(), "pending_amount": pending.aggregate(t=Sum("amount"))["t"] or ZERO,
        "ratio": {"effective_from": schedule.effective_from if schedule else None,
                  "shares": [{"partner": str(l.partner_id), "name": l.partner.full_name, "percent": l.percent}
                             for l in (schedule.lines.select_related("partner") if schedule else [])]},
        "capital_by_partner": [{"partner": r["id"], "name": r["name"], "capital": r["capital"], "current": r["current"]}
                               for r in sorted(rows, key=lambda r: -r["capital"]) if r["capital"] or r["is_active"]],
        "drawings_by_month": [{"month": m.strftime("%Y-%m"), "label": m.strftime("%b"), "amount": drawn[m.strftime("%Y-%m")]} for m in months],
        "comparison": [{"name": r["name"], "contribution": r["contribution_percent"], "profit_share": r["profit_share_percent"]} for r in active],
        "books_agree": books_agree(company, today),
        "recent": [transaction_dict(t) for t in recent],
    }


def books_agree(company, as_of=None):
    """True when the partner tables and the ledger hold the same totals for capital, current accounts and loans."""
    totals = ledger.totals(company, end=as_of)
    out = {}
    for key, acct in (("capital", chart.system_account(company, "capital")), ("current", chart.system_account(company, "partner_current")),
                      ("loan", ordinary_account(company, "loan"))):
        out[key] = ledger.natural_balance(acct, *totals.get(acct.id, (ZERO, ZERO)))
    mine = _blank()
    for b in balances(company, as_of).values():
        for k in mine:
            mine[k] += b[k]
    return {"ledger": out, "partners": mine, "agree": all(out[k] == mine[k] for k in out)}


def transaction_dict(t):
    return {"id": str(t.id), "number": t.number, "partner": {"id": str(t.partner_id), "name": t.partner.full_name}, "kind": t.kind,
            "kind_label": t.get_kind_display(), "date": t.date, "amount": t.amount, "mode": t.mode, "mode_label": t.get_mode_display() if t.mode else "",
            "money_account": str(t.money_account_id) if t.money_account_id else None, "reference": t.reference,
            "narration": t.narration, "rate": t.rate, "period_from": t.period_from, "period_to": t.period_to, "status": t.status,
            "status_label": t.get_status_display(), "decision_reason": t.decision_reason, "decided_at": t.decided_at,
            "requested_by": t.requested_by.get_username() if t.requested_by_id else "",
            "decided_by": t.decided_by.get_username() if t.decided_by_id else "",
            "entry_id": str(t.entry_id) if t.entry_id else None, "approval_request_id": str(t.approval_request_id) if t.approval_request_id else None,
            "created_at": t.created_at}


# --------------------------------------------------------------------------- agreements

def agreement_versions(company, user):
    """The LLP agreement documents kept in the Company module (read-only), newest first, that this user may see."""
    from apps.company.permissions import visible_classifications
    qs = CompanyDocument.objects.filter(company=company, document_type=CompanyDocument.Kind.LLP_AGREEMENT,
                                        access_classification__in=visible_classifications(user)).order_by("-created_at")
    return [{"id": str(d.id), "title": d.title, "version": d.version, "is_current": d.is_current, "expiry_date": d.expiry_date,
             "uploaded": d.created_at, "classification": d.access_classification, "verification_status": d.verification_status,
             "original_filename": d.original_filename} for d in qs]
