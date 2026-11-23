"""Expenses, other income, manual journals, transfers between bank and cash, and opening balances."""
import datetime
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.company.constants import DocType
from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import company as company_service, numbering

from ..models import Account, JournalEntry, Voucher
from . import chart, ledger

Action = CompanyProfileAuditEvent.Action
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MONEY_SUBTYPES = (Account.Subtype.CASH, Account.Subtype.BANK)


def _round(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def gst_split(company, net, mode, rate, direction):
    """GST on `net`: [(system account key, label, amount)]. Same state: CGST + SGST halves; other state: IGST.

    Only for a company that charges GST (regular taxpayer): input credit on an expense and output tax on income.
    """
    if mode in (None, "", "none"):
        return []
    if mode not in ("intra", "inter"):
        raise ValidationError({"tax_mode": "Choose same-state (CGST + SGST), other-state (IGST) or none."})
    ts = company_service.get_tax_settings(company)
    if not (ts.gst_applicable and ts.gst_treatment == "regular"):
        raise ValidationError({"tax_mode": "GST is not switched on for the company, so no GST can be recorded."})
    try:
        rate = Decimal(str(rate))
    except Exception:
        raise ValidationError({"tax_rate": "Choose the GST rate."})
    if rate not in {Decimal(str(r)) for r in settings.GST_CURRENT_RATES} or rate == 0:
        raise ValidationError({"tax_rate": "Choose a current GST rate."})
    suffix = "in" if direction == "in" else "out"
    if mode == "inter":
        return [(f"gst_igst_{suffix}", f"IGST @ {rate.normalize():f}%", _round(net * rate / 100))]
    half = _round(net * rate / 200)
    return [(f"gst_cgst_{suffix}", f"CGST @ {(rate / 2).normalize():f}%", half),
            (f"gst_sgst_{suffix}", f"SGST @ {(rate / 2).normalize():f}%", half)]


def snapshot(v):
    return {"kind": v.kind, "number": v.number, "status": v.status, "category": v.category.name, "total": str(v.total),
            "date": str(v.date), "party": v.party_name}


def project_refs(company, ids):
    """{project id: {id, code, name}} for tagged records (cancelled projects included), when the projects app is installed."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    from django.apps import apps
    if not apps.is_installed("apps.projects"):
        return {}
    from apps.projects.models import Project
    return {p.pk: {"id": str(p.pk), "code": p.code, "name": p.name} for p in Project.objects.filter(company=company, pk__in=ids)}


def _resolve_project(company, value, current=None):
    """The project id for what was sent, None for no project. An unknown, other-company, cancelled or malformed id is a 400;
    keeping the project a draft already carries is always allowed."""
    if value in (None, ""):
        return None
    try:
        pid = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValidationError({"project": "Choose a project from the list."})
    if current is not None and pid == current:
        return pid
    from django.apps import apps
    if apps.is_installed("apps.projects"):
        from apps.projects.models import Project
        if Project.objects.filter(company=company, pk=pid).exclude(status=Project.Status.CANCELLED).exists():
            return pid
    raise ValidationError({"project": "This project does not exist, or it has been cancelled."})


def _prepare(company, *, kind, date, category, money_account, amount, tax_mode="none", tax_rate=0, party_name="", reference="",
             notes="", project_id=None, current_project=None):
    """Validate what was entered and work out the GST: the field values of a voucher (nothing is saved or posted)."""
    amount = ledger.amount(amount)
    if kind not in Voucher.Kind.values:
        raise ValidationError({"kind": "Choose expense or income."})
    if not isinstance(date, datetime.date):
        raise ValidationError({"date": "Enter the date."})
    want = "expense" if kind == Voucher.Kind.EXPENSE else "income"
    if category is None or category.company_id != company.pk or category.type != want or not category.is_active:
        raise ValidationError({"category": f"Choose an {want} account."})
    if (money_account is None or money_account.company_id != company.pk or money_account.subtype not in MONEY_SUBTYPES
            or not money_account.is_active):
        raise ValidationError({"money_account": "Choose the cash or bank account the money moved through."})
    split = gst_split(company, amount, tax_mode, tax_rate, "in" if kind == "expense" else "out")
    tax_total = sum((a for _, _, a in split), ZERO)
    return dict(kind=kind, date=date, category=category, money_account=money_account, party_name=(party_name or "").strip()[:200],
                reference=(reference or "").strip()[:100], notes=(notes or "").strip()[:300], amount=amount,
                tax_rate=Decimal(str(tax_rate)) if split else ZERO, tax_amount=tax_total, total=amount + tax_total,
                gst_split=[{"label": label, "account": key, "amount": str(a)} for key, label, a in split],
                project_id=_resolve_project(company, project_id, current_project))


def _post(voucher, actor):
    """Number the voucher, write its entry in the books and mark it recorded. Runs inside the caller's transaction."""
    company, kind = voucher.company, voucher.kind
    if not voucher.category.is_active:
        raise ValidationError({"category": f"{voucher.category.code} {voucher.category.name} is switched off."})
    if not voucher.money_account.is_active:
        raise ValidationError({"money_account": f"{voucher.money_account.name} is switched off."})
    doc_type = DocType.EXPENSE if kind == "expense" else DocType.INCOME
    number = numbering.allocate_number(company, doc_type, actor=actor, on_date=voucher.date).reference_number
    split = [(g["account"], g["label"], Decimal(g["amount"])) for g in voucher.gst_split]
    amount, total, project_id = voucher.amount, voucher.total, voucher.project_id
    label = f"{number}: {voucher.party_name or voucher.category.name}"
    if kind == "expense":
        lines = [{"account": voucher.category, "debit": amount, "memo": label, "project": project_id}]
        lines += [{"account": key, "debit": a, "memo": f"{label} ({lbl})", "project": project_id} for key, lbl, a in split]
        lines.append({"account": voucher.money_account, "credit": total, "memo": label})
        source = JournalEntry.Source.EXPENSE
    else:
        lines = [{"account": voucher.money_account, "debit": total, "memo": label},
                 {"account": voucher.category, "credit": amount, "memo": label, "project": project_id}]
        lines += [{"account": key, "credit": a, "memo": f"{label} ({lbl})", "project": project_id} for key, lbl, a in split]
        source = JournalEntry.Source.INCOME
    voucher.entry = ledger.post(company, date=voucher.date, lines=lines, narration=label, source=source, source_id=str(voucher.pk),
                                reference=number, actor=actor)
    voucher.number, voucher.status = number, Voucher.Status.RECORDED
    voucher.save()


@transaction.atomic
def create_voucher(company, actor, *, kind, date, category, money_account, amount, tax_mode="none", tax_rate=0, party_name="",
                   reference="", notes="", project_id=None, record=True):
    """Save an expense or income. `record=True` records it now: it is posted, unless an approval rule holds it (an expense
    above a rule waits as `pending_approval`). `record=False` keeps an expense as a draft to finish or send later."""
    fields = _prepare(company, kind=kind, date=date, category=category, money_account=money_account, amount=amount,
                      tax_mode=tax_mode, tax_rate=tax_rate, party_name=party_name, reference=reference, notes=notes,
                      project_id=project_id)
    if not record and kind != Voucher.Kind.EXPENSE:
        raise ValidationError({"record": "Only an expense can be kept as a draft."})
    voucher = Voucher(company=company, status=Voucher.Status.DRAFT, created_by=actor if getattr(actor, "is_authenticated", False) else None,
                      **fields)
    voucher.save()
    if record:
        _record(voucher, actor, created=True)
    else:
        ledger.audit_event(company, actor, "voucher", voucher.pk, Action.CREATED, new=snapshot(voucher), reason="Saved as a draft")
    return voucher


@transaction.atomic
def update_draft(voucher, actor, **changes):
    """Change an expense that has not been recorded (a new draft, or one an approver sent back)."""
    voucher = _lock(voucher)
    if voucher.status == Voucher.Status.PENDING_APPROVAL:
        raise ValidationError("This expense is waiting for approval and cannot be edited. Take it back first.")
    if voucher.status != Voucher.Status.DRAFT:
        raise ValidationError("A recorded entry cannot be edited. Void it and enter it again.")
    current = dict(kind=voucher.kind, date=voucher.date, category=voucher.category, money_account=voucher.money_account,
                   amount=voucher.amount, tax_mode="none", tax_rate=0, party_name=voucher.party_name, reference=voucher.reference,
                   notes=voucher.notes, project_id=voucher.project_id)
    if voucher.gst_split:
        current["tax_mode"] = "inter" if any(g["account"].startswith("gst_igst") for g in voucher.gst_split) else "intra"
        current["tax_rate"] = voucher.tax_rate
    current.update(changes)
    current["kind"] = voucher.kind
    before = snapshot(voucher)
    fields = _prepare(voucher.company, current_project=voucher.project_id, **current)
    for key, value in fields.items():
        setattr(voucher, key, value)
    voucher.save()
    ledger.audit_event(voucher.company, actor, "voucher", voucher.pk, Action.UPDATED, previous=before, new=snapshot(voucher))
    return voucher


@transaction.atomic
def delete_draft(voucher, actor):
    voucher = _lock(voucher)
    if voucher.status == Voucher.Status.PENDING_APPROVAL:
        raise ValidationError("This expense is waiting for approval. Take it back first, then delete it.")
    if voucher.status != Voucher.Status.DRAFT:
        raise ValidationError("Only a draft can be deleted. Void a recorded entry instead.")
    ledger.audit_event(voucher.company, actor, "voucher", voucher.pk, Action.DELETED, previous=snapshot(voucher))
    voucher.delete()


def _lock(voucher):
    return Voucher.objects.select_for_update(of=("self",)).select_related("company", "category", "money_account", "entry").get(pk=voucher.pk)


# --------------------------------------------------------------------------- approval

def _approvals():
    """The approvals module's API when it is installed; None keeps accounting working on its own."""
    try:
        from apps.approvals import api
        return api if hasattr(api, "submit") else None
    except ImportError:
        return None


def approval_managed():
    """Whether decisions on expenses held for approval must go through the shared approvals workflow."""
    return _approvals() is not None


def needs_approval(voucher):
    """An expense is held for approval when an active rule applies to an expense of this size (income is never held)."""
    api = _approvals()
    return api is not None and voucher.kind == Voucher.Kind.EXPENSE and api.requires_approval(voucher.company, "expense", voucher.total)


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def expense_title(v):
    return f"Expense: {v.party_name or v.category.name}"


def _record(voucher, actor, *, created=False):
    if not needs_approval(voucher):
        before = snapshot(voucher)
        _post(voucher, actor)
        ledger.audit_event(voucher.company, actor, "voucher", voucher.pk, Action.CREATED if created else Action.UPDATED,
                           previous=None if created else before, new=snapshot(voucher), reason="" if created else "Recorded")
        return voucher
    voucher.status, voucher.submitted_at, voucher.decision_note = Voucher.Status.PENDING_APPROVAL, _now(), ""
    voucher.save()
    ledger.audit_event(voucher.company, actor, "voucher", voucher.pk, Action.CREATED if created else Action.UPDATED,
                       previous=None if created else {"status": "draft"},
                       new=snapshot(voucher) if created else {"status": "pending_approval"}, reason="Sent for approval")
    _approvals().submit(voucher.company, kind="expense", source_id=str(voucher.pk), title=expense_title(voucher), amount=voucher.total,
                        requested_by=actor, summary=f"{voucher.category.name}, total {voucher.total}",
                        url=f"/accounting/vouchers/{voucher.pk}", project_id=voucher.project_id)
    voucher.refresh_from_db()
    return voucher


@transaction.atomic
def record(voucher, actor):
    """Record a draft expense. Above an approval rule it waits (pending_approval); only on approval is it numbered and posted."""
    voucher = _lock(voucher)
    if voucher.status != Voucher.Status.DRAFT:
        raise ValidationError("Only a draft can be recorded.")
    return _record(voucher, actor)


@transaction.atomic
def approve(voucher, actor, note=""):
    """The approval was granted: number the expense and post it to the books exactly as a directly recorded one."""
    voucher = _lock(voucher)
    if voucher.status != Voucher.Status.PENDING_APPROVAL:
        raise ValidationError("Only an expense that is waiting for approval can be approved.")
    before = snapshot(voucher)
    voucher.decision_note = (note or "").strip()[:300]
    _post(voucher, actor)
    ledger.audit_event(voucher.company, actor, "voucher", voucher.pk, Action.UPDATED, previous=before, new=snapshot(voucher),
                       reason=f"Approved{': ' + voucher.decision_note if voucher.decision_note else ''}")
    return voucher


@transaction.atomic
def reject(voucher, actor, reason):
    voucher = _lock(voucher)
    if voucher.status != Voucher.Status.PENDING_APPROVAL:
        raise ValidationError("Only an expense that is waiting for approval can be rejected.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this expense."})
    voucher.status, voucher.decision_note = Voucher.Status.DRAFT, f"Rejected: {reason.strip()}"[:300]
    voucher.save()
    ledger.audit_event(voucher.company, actor, "voucher", voucher.pk, Action.UPDATED, previous={"status": "pending_approval"},
                       new={"status": "draft"}, reason=f"Rejected: {reason.strip()}")
    return voucher


def withdraw(voucher, actor):
    """The approval request was withdrawn (or the person took the expense back): it is a draft again."""
    voucher = _lock(voucher)
    if voucher.status == Voucher.Status.PENDING_APPROVAL:
        voucher.status = Voucher.Status.DRAFT
        voucher.save()
        ledger.audit_event(voucher.company, actor, "voucher", voucher.pk, Action.UPDATED, previous={"status": "pending_approval"},
                           new={"status": "draft"}, reason="Approval request withdrawn")
    return voucher


@transaction.atomic
def take_back(voucher, actor):
    """Withdraw an expense that is waiting for approval from its own page (closes its approval request)."""
    voucher = _lock(voucher)
    if voucher.status != Voucher.Status.PENDING_APPROVAL:
        raise ValidationError("Only an expense that is waiting for approval can be taken back.")
    api = _approvals()
    if api is not None:
        api.cancel_for_source("expense", voucher.pk, actor)
    return withdraw(voucher, actor)


def resubmit(voucher, actor):
    """A rejected or withdrawn request was sent again from the Approvals module: the draft waits for approval once more."""
    voucher = _lock(voucher)
    if voucher.status == Voucher.Status.DRAFT and voucher.kind == Voucher.Kind.EXPENSE:
        voucher.status, voucher.submitted_at, voucher.decision_note = Voucher.Status.PENDING_APPROVAL, _now(), ""
        voucher.save()
        ledger.audit_event(voucher.company, actor, "voucher", voucher.pk, Action.UPDATED, previous={"status": "draft"},
                           new={"status": "pending_approval"}, reason="Sent for approval again")
    return voucher


@transaction.atomic
def void_voucher(voucher, reason, actor):
    voucher = _lock(voucher)
    if voucher.status == Voucher.Status.VOID:
        raise ValidationError("This entry is already void.")
    if voucher.status != Voucher.Status.RECORDED:
        raise ValidationError("Only a recorded entry can be voided; delete a draft instead.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give a reason for voiding this entry."})
    ledger.reverse(voucher.entry, actor, reason.strip())
    voucher.status, voucher.void_reason = Voucher.Status.VOID, reason.strip()[:300]
    voucher.save(update_fields=["status", "void_reason", "updated_at"])
    ledger.audit_event(voucher.company, actor, "voucher", voucher.pk, Action.DEACTIVATED, previous={"status": "recorded"},
                       new={"status": "void"}, reason=reason.strip())
    return voucher


@transaction.atomic
def create_journal(company, actor, *, date, lines, narration=""):
    """A manual journal entry with its own number (T2N/JV/26/1211/00001)."""
    if not (narration or "").strip():
        raise ValidationError({"narration": "Say what the entry is for."})
    number = numbering.allocate_number(company, DocType.JOURNAL, actor=actor, on_date=date).reference_number
    entry = ledger.post(company, date=date, lines=lines, narration=narration.strip(), reference=number, actor=actor)
    ledger.audit_event(company, actor, "journal_entry", entry.pk, Action.CREATED,
                       new={"number": number, "narration": entry.narration, "lines": entry.lines.count()})
    return entry


@transaction.atomic
def transfer(company, actor, *, date, from_account, to_account, amount, notes=""):
    """Move money between two cash or bank accounts (for example, cash deposited at the bank)."""
    amount = ledger.amount(amount)
    for a in (from_account, to_account):
        if a.company_id != company.pk or a.subtype not in MONEY_SUBTYPES or not a.is_active:
            raise ValidationError({"account": "Transfers are between cash and bank accounts."})
    if from_account.pk == to_account.pk:
        raise ValidationError({"account": "Choose two different accounts."})
    number = numbering.allocate_number(company, DocType.JOURNAL, actor=actor, on_date=date).reference_number
    label = f"Transfer {number}: {from_account.name} to {to_account.name}"
    entry = ledger.post(company, date=date, source=JournalEntry.Source.TRANSFER, reference=number,
                        narration=(notes.strip() or label)[:500], actor=actor,
                        lines=[{"account": to_account, "debit": amount, "memo": label},
                               {"account": from_account, "credit": amount, "memo": label}])
    ledger.audit_event(company, actor, "journal_entry", entry.pk, Action.CREATED,
                       new={"number": number, "narration": label, "amount": str(amount)})
    return entry


@transaction.atomic
def current_opening(company):
    """The opening-balances entry that is in force (posted, not reversed), or None."""
    return JournalEntry.objects.filter(company=company, source=JournalEntry.Source.OPENING, status=JournalEntry.Status.POSTED).order_by("-created_at").first()


def opening_balances(company, actor, *, date, rows):
    """Balances brought forward when the books start. The difference goes to "Opening balance adjustments".

    Entered once: while an opening-balances entry stands, a second one is refused (it would double every balance). To change
    the figures, reverse the earlier entry in the Journal and enter them again."""
    earlier = current_opening(company)
    if earlier is not None:
        raise ValidationError({"rows": f"Opening balances were already entered as at {earlier.date:%d %b %Y}. Reverse that entry in the "
                                       "Journal first if the figures need to change, then enter them again."})
    lines, debit, credit = [], ZERO, ZERO
    for row in rows:
        acct = ledger.resolve_account(company, row.get("account"))
        d, c = ledger.money(row.get("debit") or 0), ledger.money(row.get("credit") or 0)
        if acct is None:
            raise ValidationError({"rows": "Choose an account on every row."})
        if d == 0 and c == 0:
            continue
        if d > 0 and c > 0:
            raise ValidationError({"rows": f"{acct.code}: enter a debit or a credit, not both."})
        lines.append({"account": acct, "debit": d, "credit": c, "memo": "Opening balance"})
        debit += d
        credit += c
    if not lines:
        raise ValidationError({"rows": "Enter at least one balance."})
    balancing = chart.system_account(company, "opening_balance_equity")
    if debit > credit:
        lines.append({"account": balancing, "credit": debit - credit, "memo": "Opening balance adjustment"})
    elif credit > debit:
        lines.append({"account": balancing, "debit": credit - debit, "memo": "Opening balance adjustment"})
    entry = ledger.post(company, date=date, lines=lines, source=JournalEntry.Source.OPENING, narration="Opening balances",
                        reference="Opening balances", actor=actor)
    ledger.audit_event(company, actor, "journal_entry", entry.pk, Action.CREATED, new={"narration": "Opening balances", "lines": len(lines)})
    return entry
