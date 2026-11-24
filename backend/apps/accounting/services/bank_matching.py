"""Matching bank statement lines to what is already in the books, and making the missing entry from a line.

A statement line is money in (positive) or out (negative); the book side is the bank account's journal lines (a debit is
money in). Suggestions look for the same amount on a nearby date first, then for a reference, invoice number or party name
from the entry inside the bank's narration. A match may join one line to several entries (one deposit for two payments) or
several lines to one entry, as long as both sides add up to the same money.
"""
import datetime
import re
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.company.models import CompanyProfileAuditEvent

from ..models import Account, BankMatch, BankStatementLine, JournalEntry, JournalLine, Voucher
from . import ledger, statements

Action = CompanyProfileAuditEvent.Action
Status = BankStatementLine.Status
ZERO = Decimal("0.00")
CENT = Decimal("0.01")
MAX_SUGGESTIONS = 3
HIGH, MEDIUM = 85, 65
STOP_WORDS = {
    "PVT", "PRIVATE", "LTD", "LIMITED", "LLP", "INDIA", "INDIAN", "SERVICES", "SERVICE", "SOLUTIONS", "TECHNOLOGIES", "TECHNOLOGY",
    "TECH", "INDUSTRIES", "ENTERPRISES", "ENTERPRISE", "TRADERS", "TRADING", "AND", "THE", "COMPANY", "CORPORATION", "GLOBAL",
    "SYSTEMS", "CONSULTING", "INTERNATIONAL", "GROUP", "BANK", "PAYMENT", "PAYMENTS", "TRANSFER", "NEFT", "RTGS", "IMPS", "UPI",
    "CHARGES", "FROM", "INFOTECH", "SUPPLIES", "STORE", "STORES", "AGENCY", "AGENCIES", "ASSOCIATES", "PARTNERS", "CO",
}


def confidence(score):
    return "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"


def _alnum(text):
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def _words(text):
    return set(re.findall(r"[A-Z0-9]{3,}", str(text or "").upper()))


def signed(jl):
    """A journal line on a bank account as money in (+) or out (-)."""
    return ledger.money(jl.debit - jl.credit)


def book_dict(jl):
    e = jl.entry
    return {"id": str(jl.id), "entry_id": str(e.id), "date": e.date, "reference": e.reference, "narration": e.narration,
            "memo": jl.memo, "amount": signed(jl), "source": e.source, "source_label": e.get_source_display(),
            "status": e.status, "reconciled": jl.reconciliation_id is not None}


def candidates(account):
    """Book lines on the account that are not matched or reconciled yet. Opening balances are never on a statement."""
    return (JournalLine.objects.filter(account=account, bank_match__isnull=True, reconciliation__isnull=True)
            .exclude(entry__source=JournalEntry.Source.OPENING).select_related("entry").order_by("entry__date", "created_at"))


def _extras(company, lines):
    """{entry id: (references, names)} from the records behind the entries: the UTR or cheque number typed on a payment, the
    invoice and bill numbers it settled, the customer, vendor or payee name."""
    by_source = {}
    for jl in lines:
        by_source.setdefault(jl.entry.source, {})[jl.entry.source_id.split(":")[0]] = jl.entry_id
    out = {}

    def add(entry_id, refs=(), names=()):
        r, n = out.setdefault(entry_id, (set(), set()))
        r.update(x for x in refs if x)
        n.update(x for x in names if x)

    ids = by_source.get(JournalEntry.Source.PAYMENT, {})
    if ids:
        try:
            from apps.sales.models import Payment
            for p in Payment.objects.filter(company=company, pk__in=list(ids)).select_related("customer").prefetch_related("allocations__document"):
                add(ids[str(p.pk)], [p.reference] + [a.document.number for a in p.allocations.all()],
                    [p.customer.display_name, p.customer.legal_name])
        except (ImportError, ValueError):
            pass
    ids = by_source.get(JournalEntry.Source.VENDOR_PAYMENT, {})
    if ids:
        try:
            from apps.purchases.models import VendorPayment
            for p in VendorPayment.objects.filter(company=company, pk__in=list(ids)).select_related("vendor").prefetch_related("allocations__bill"):
                bills = [a.bill for a in p.allocations.all() if a.bill_id]
                add(ids[str(p.pk)], [p.reference] + [b.number for b in bills] + [b.vendor_bill_no for b in bills], [p.vendor.name])
        except (ImportError, ValueError):
            pass
    vids = {**by_source.get(JournalEntry.Source.EXPENSE, {}), **by_source.get(JournalEntry.Source.INCOME, {})}
    if vids:
        for v in Voucher.objects.filter(company=company, pk__in=list(vids)):
            add(vids[str(v.pk)], [v.reference], [v.party_name])
    return out


def _text_score(line, jl, extras):
    """(points, reasons) for references and names of the entry found in the statement line's narration."""
    text = f"{line.description} {line.reference}"
    flat, words, ref_flat = _alnum(text), _words(text), _alnum(line.reference)
    refs, names = extras.get(jl.entry_id, (set(), set()))
    refs = set(refs) | {jl.entry.reference}
    points, reasons = 0, []
    for ref in sorted(refs, key=len, reverse=True):
        r = _alnum(ref)
        if len(r) < 4:
            continue
        hit = (r == ref_flat) or (r in words) if r.isdigit() else (len(r) >= 5 and r in flat)
        if hit:
            points, reasons = 25, [f"Reference {ref} is in the narration"]
            break
    name_words = set()
    for name in names | ({line_party(jl)} - {""}):
        name_words |= {w for w in _words(name) if len(w) >= 4 and w not in STOP_WORDS and not w.isdigit()}
    for w in sorted(name_words):
        if w in words or any(len(s) >= 5 and w.startswith(s) for s in words):
            points += 15
            reasons.append(f"Name \"{w.title()}\" is in the narration")
            return points, reasons
    # Otherwise, words of the entry's own description (for example "Capital introduced") found in the narration.
    described = {w for w in _words(f"{jl.entry.narration} {jl.memo}") if len(w) >= 4 and w not in STOP_WORDS and not any(ch.isdigit() for ch in w)}
    if described & words:
        points += 10
        reasons.append("Words of the description match")
    return points, reasons


def line_party(jl):
    """The party part of an automatic narration ("Payment T2N/PAY/..: Acme Industries" gives "Acme Industries")."""
    narration = jl.entry.narration or ""
    return narration.split(":", 1)[1].strip() if ":" in narration else ""


def _date_points(days):
    if days == 0:
        return 30, "Same date"
    if days <= 3:
        return 20, f"{days} day{'s' if days > 1 else ''} apart"
    if days <= 7:
        return 10, f"{days} days apart"
    if days <= 30:
        return 0, f"{days} days apart"
    return -20, f"{days} days apart"


def suggest(account, lines):
    """{statement line id: [suggestion]} for unmatched statement lines. A suggestion: {journal_lines, entries, score,
    confidence, reasons}. Single entries of the same amount come first; when none is convincing, two entries that add up to
    the line on nearby dates are offered."""
    pool = list(candidates(account))
    if not pool or not lines:
        return {str(l.id): [] for l in lines}
    extras = _extras(account.company, pool)
    by_amount = {}
    for jl in pool:
        by_amount.setdefault(signed(jl), []).append(jl)
    out = {}
    for line in lines:
        found = []
        for jl in by_amount.get(line.amount, []):
            days = abs((jl.entry.date - line.date).days)
            dp, dr = _date_points(days)
            tp, tr = _text_score(line, jl, extras)
            if days > 30 and not tp:
                continue
            score, reasons = 50 + dp + tp, ["Same amount", dr] + tr
            if line.created_source and jl.entry.source == line.created_source and jl.entry.source_id == line.created_source_id:
                score, reasons = 100, ["Made from this statement line"]
            found.append((min(score, 100), [jl], reasons))
        found.sort(key=lambda f: (-f[0], abs((f[1][0].entry.date - line.date).days)))
        if not found or found[0][0] < MEDIUM:
            found += _pairs(line, pool, extras)
            found.sort(key=lambda f: -f[0])
        out[str(line.id)] = [{"journal_lines": [str(j.id) for j in jls], "entries": [book_dict(j) for j in jls], "score": score,
                              "confidence": confidence(score), "reasons": reasons} for score, jls, reasons in found[:MAX_SUGGESTIONS]]
    return out


def _pairs(line, pool, extras):
    """Two book lines on nearby dates (within a week) that together make up the statement line."""
    near = [jl for jl in pool if abs((jl.entry.date - line.date).days) <= 7 and signed(jl) * line.amount > 0
            and abs(signed(jl)) < abs(line.amount)]
    near = sorted(near, key=lambda jl: abs((jl.entry.date - line.date).days))[:40]
    found = []
    for a, b in combinations(near, 2):
        if signed(a) + signed(b) != line.amount:
            continue
        days = max(abs((a.entry.date - line.date).days), abs((b.entry.date - line.date).days))
        dp, dr = _date_points(days)
        tp = max(_text_score(line, a, extras)[0], _text_score(line, b, extras)[0])
        score = min(40 + dp + tp, 100)
        found.append((score, [a, b], ["Two entries add up to this amount", dr] + (["Reference or name in the narration"] if tp else [])))
    found.sort(key=lambda f: -f[0])
    return found[:2]


# --------------------------------------------------------------------------- match, undo, ignore

def _audit(company, actor, entity, pk, action, **kw):
    ledger.audit_event(company, actor, entity, pk, action, **kw)


def _locked_lines(account, ids, model):
    try:
        rows = list(model.objects.select_for_update(of=("self",)).filter(pk__in=list(ids), account=account))
    except (ValueError, ValidationError):
        rows = []
    return rows


@transaction.atomic
def match(company, actor, *, account, statement_line_ids, journal_line_ids, kind=BankMatch.Kind.MANUAL, note=""):
    """Join statement line(s) and book line(s) of the same account whose totals agree."""
    statements.check_bank_account(company, account)
    s_ids, j_ids = set(map(str, statement_line_ids or [])), set(map(str, journal_line_ids or []))
    if not s_ids:
        raise ValidationError({"statement_lines": "Choose the statement line to match."})
    if not j_ids:
        raise ValidationError({"journal_lines": "Choose the entry in the books that matches."})
    s_lines = _locked_lines(account, s_ids, BankStatementLine)
    if len(s_lines) != len(s_ids):
        raise ValidationError({"statement_lines": "Some statement lines are not on this bank account."})
    if any(l.status != Status.UNMATCHED or l.reconciliation_id for l in s_lines):
        raise ValidationError({"statement_lines": "A statement line chosen is already matched or ignored."})
    j_lines = list(JournalLine.objects.select_for_update(of=("self",)).filter(pk__in=list(j_ids), account=account).select_related("entry")) \
        if all(_is_uuid(x) for x in j_ids) else []
    if len(j_lines) != len(j_ids):
        raise ValidationError({"journal_lines": "Some entries chosen are not in this bank account's ledger."})
    if any(jl.bank_match_id or jl.reconciliation_id for jl in j_lines):
        raise ValidationError({"journal_lines": "An entry chosen is already matched or reconciled."})
    bank_total = sum((l.amount for l in s_lines), ZERO)
    book_total = sum((signed(jl) for jl in j_lines), ZERO)
    if bank_total != book_total:
        raise ValidationError({"journal_lines": f"The statement line{'s' if len(s_lines) > 1 else ''} come{'' if len(s_lines) > 1 else 's'} "
                                                f"to {_rupees(bank_total)} but the entries chosen come to {_rupees(book_total)}. "
                                                f"The difference is {_rupees(bank_total - book_total)}."})
    m = BankMatch.objects.create(company=company, account=account, kind=kind, note=(note or "").strip()[:300],
                                 created_by=actor if getattr(actor, "is_authenticated", False) else None)
    BankStatementLine.objects.filter(pk__in=[l.pk for l in s_lines]).update(match=m, status=Status.MATCHED)
    JournalLine.objects.filter(pk__in=[jl.pk for jl in j_lines]).update(bank_match=m)
    _audit(company, actor, "bank_match", m.pk, Action.CREATED, reason=m.get_kind_display(),
           new={"account": account.name, "statement_lines": ", ".join(f"{l.date} {l.amount}" for l in s_lines),
                "entries": ", ".join(jl.entry.reference or jl.entry.get_source_display() for jl in j_lines), "amount": str(bank_total)})
    return m


def _is_uuid(value):
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _rupees(value):
    return f"Rs {ledger.money(value):,.2f}"


@transaction.atomic
def mark_cleared(company, actor, *, account, journal_line_ids, note=""):
    """Book lines that went through the bank before the first imported statement: cleared without a statement line."""
    statements.check_bank_account(company, account)
    j_ids = set(map(str, journal_line_ids or []))
    if not j_ids:
        raise ValidationError({"journal_lines": "Choose the entries that already went through the bank."})
    j_lines = list(JournalLine.objects.select_for_update(of=("self",)).filter(pk__in=list(j_ids), account=account).select_related("entry")) \
        if all(_is_uuid(x) for x in j_ids) else []
    if len(j_lines) != len(j_ids) or any(jl.bank_match_id or jl.reconciliation_id for jl in j_lines):
        raise ValidationError({"journal_lines": "Some entries chosen are not open entries of this bank account."})
    first = account.statement_lines.order_by("date").first()
    if first is None:
        raise ValidationError({"journal_lines": "Import a statement first; only entries from before it can be marked as cleared."})
    if any(jl.entry.date >= first.date for jl in j_lines):
        raise ValidationError({"journal_lines": f"Only entries dated before the first imported statement line ({first.date:%d %b %Y}) "
                                                "can be marked as already cleared. Match later ones to their statement lines."})
    m = BankMatch.objects.create(company=company, account=account, kind=BankMatch.Kind.CLEARED,
                                 note=(note or "Cleared before the imported statements").strip()[:300],
                                 created_by=actor if getattr(actor, "is_authenticated", False) else None)
    JournalLine.objects.filter(pk__in=[jl.pk for jl in j_lines]).update(bank_match=m)
    _audit(company, actor, "bank_match", m.pk, Action.CREATED, reason=m.get_kind_display(),
           new={"account": account.name, "entries": len(j_lines), "amount": str(sum((signed(j) for j in j_lines), ZERO))})
    return m


@transaction.atomic
def unmatch(m, actor):
    m = BankMatch.objects.select_for_update().select_related("account", "company").get(pk=m.pk)
    s_lines = list(m.statement_lines.all())
    j_lines = list(m.journal_lines.select_related("entry"))
    if any(l.reconciliation_id for l in s_lines) or any(jl.reconciliation_id for jl in j_lines):
        raise ValidationError("This match is part of a finished reconciliation. Undo that reconciliation first.")
    BankStatementLine.objects.filter(match=m).update(match=None, status=Status.UNMATCHED)
    JournalLine.objects.filter(bank_match=m).update(bank_match=None)
    _audit(m.company, actor, "bank_match", m.pk, Action.DELETED, reason="Match undone",
           previous={"account": m.account.name, "statement_lines": ", ".join(f"{l.date} {l.amount}" for l in s_lines),
                     "entries": ", ".join(jl.entry.reference or jl.entry.get_source_display() for jl in j_lines)})
    m.delete()


@transaction.atomic
def ignore(line, actor, reason):
    line = BankStatementLine.objects.select_for_update().select_related("account", "company").get(pk=line.pk)
    if not (reason or "").strip():
        raise ValidationError({"reason": "Say why this line is being ignored."})
    if line.status != Status.UNMATCHED:
        raise ValidationError("Only a line that is still to be matched can be ignored.")
    line.status, line.ignore_reason = Status.IGNORED, reason.strip()[:300]
    line.save(update_fields=["status", "ignore_reason", "updated_at"])
    _audit(line.company, actor, "statement_line", line.pk, Action.UPDATED, reason=f"Ignored: {line.ignore_reason}",
           previous={"status": "unmatched"}, new={"status": "ignored", "line": f"{line.date} {line.amount} {line.description[:80]}"})
    return line


@transaction.atomic
def unignore(line, actor):
    line = BankStatementLine.objects.select_for_update().select_related("account", "company").get(pk=line.pk)
    if line.status != Status.IGNORED:
        raise ValidationError("This line is not ignored.")
    if line.reconciliation_id:
        raise ValidationError("This line is part of a finished reconciliation. Undo that reconciliation first.")
    line.status, line.ignore_reason = Status.UNMATCHED, ""
    line.save(update_fields=["status", "ignore_reason", "updated_at"])
    _audit(line.company, actor, "statement_line", line.pk, Action.UPDATED, reason="No longer ignored",
           previous={"status": "ignored"}, new={"status": "unmatched"})
    return line


def auto_accept(company, actor, account):
    """Accept every high-confidence single-entry suggestion, best first, never using one entry twice. Returns the count."""
    lines = list(BankStatementLine.objects.filter(account=account, status=Status.UNMATCHED))
    sugg = suggest(account, lines)
    picks = []
    for line in lines:
        for s in sugg.get(str(line.id), []):
            if s["confidence"] == "high" and len(s["journal_lines"]) == 1:
                picks.append((s["score"], line, s["journal_lines"][0]))
    picks.sort(key=lambda p: -p[0])
    used, done = set(), 0
    for _score, line, jl in picks:
        if jl in used or line.pk in used:
            continue
        # A line whose two best suggestions are equally good is left for a person to decide.
        top = sugg[str(line.id)]
        if len(top) > 1 and top[1]["score"] == top[0]["score"]:
            continue
        used.update({jl, line.pk})
        match(company, actor, account=account, statement_line_ids=[line.pk], journal_line_ids=[jl], kind=BankMatch.Kind.SUGGESTED)
        done += 1
    return done


# --------------------------------------------------------------------------- create the missing entry from a line

CREATE_KINDS = ("customer_payment", "vendor_payment", "expense", "income", "bank_charge", "transfer")
MONEY_IN_ONLY = {"customer_payment", "income"}
MONEY_OUT_ONLY = {"vendor_payment", "expense", "bank_charge"}


def bank_charges_account(company):
    qs = Account.objects.filter(company=company, type=Account.Type.EXPENSE, is_active=True)
    return qs.filter(name__iexact="Bank charges").first() or qs.filter(code="5700").first()


def _note(line):
    return f"From bank statement {line.date:%d %b %Y}: {line.description}"[:300]


def _bank_record_account(account):
    """How a customer/vendor payment created from this ledger account's statement should point at it: as the company bank
    account behind it (Company > Bank Accounts, so it displays with the bank's name), as the default bank account (when
    this account IS the default), or, for any other bank ledger account, straight at the ledger account itself.
    Returns (deposit_account_or_none, bank_ledger_account_or_none) — exactly one of the two is set, unless both are None
    for the default account."""
    if account.bank_account_id:
        return account.bank_account, None
    if account.system_key == "bank":
        return None, None  # payments with no bank chosen post to the default bank account
    return None, account


def _allocate(amount, docs):
    """[(doc id, amount)] applying `amount` to the documents in the order given, each up to its balance."""
    left, plan = amount, []
    for doc_id, balance in docs:
        if left <= 0:
            break
        use = min(left, balance)
        if use > 0:
            plan.append((doc_id, use))
            left -= use
    return plan


def _gst_net(company, kind, category, account, total, tax_mode, tax_rate):
    """The amount before GST whose GST brings it to exactly `total` (the statement shows what left the bank)."""
    from . import vouchers
    if tax_mode in (None, "", "none"):
        return total
    rate = Decimal(str(tax_rate or 0))
    guess = (total * 100 / (100 + rate)).quantize(CENT, rounding=ROUND_HALF_UP)
    for net in (guess, guess - CENT, guess + CENT):
        fields = vouchers._prepare(company, kind=kind, date=datetime.date.today(), category=category, money_account=account,
                                   amount=net, tax_mode=tax_mode, tax_rate=tax_rate)
        if fields["total"] == total:
            return net
    raise ValidationError({"tax_rate": "This amount cannot be split exactly into a value and GST at that rate. Record it without GST "
                                       "here, or enter it in Expenses and Income and match it afterwards."})


@transaction.atomic
def create_from_line(line, actor, kind, data):
    """Record what the statement line shows (using the same services as the rest of the app, so numbering, approval rules and
    postings are respected) and match it to the line. Returns {"match": BankMatch | None, "record": {...}, "pending": bool}."""
    line = BankStatementLine.objects.select_for_update().select_related("account", "company").get(pk=line.pk)
    company, account = line.company, line.account
    statements.check_bank_account(company, account)
    if line.status != Status.UNMATCHED:
        raise ValidationError("This line is already matched or ignored.")
    if line.created_source:
        raise ValidationError("An entry was already made from this line and is waiting for approval. Match the line once it is approved.")
    if kind not in CREATE_KINDS:
        raise ValidationError({"kind": "Choose what this line is."})
    money_in = line.amount > 0
    if kind in MONEY_IN_ONLY and not money_in:
        raise ValidationError({"kind": "This line is money going out; a customer payment or income is money coming in."})
    if kind in MONEY_OUT_ONLY and money_in:
        raise ValidationError({"kind": "This line is money coming in; a vendor payment, expense or bank charge is money going out."})
    amount = abs(line.amount)
    data = data or {}
    record, source, source_id = {}, None, None

    if kind == "customer_payment":
        from apps.sales import services as sales
        from apps.sales.models import Customer, SalesDocument
        customer = _pick(Customer, data.get("customer"), company, "customer", "Choose the customer who paid.")
        open_docs = {str(d.pk): sales.balance_of(d) for d in sales.open_receivables(customer)}
        wanted = [str(x) for x in (data.get("invoices") or [])]
        if any(x not in open_docs for x in wanted):
            raise ValidationError({"invoices": "Choose open invoices of this customer."})
        plan = _allocate(amount, [(x, open_docs[x]) for x in wanted])
        deposit_account, bank_ledger_account = _bank_record_account(account)
        payment = sales.record_payment(company, actor, customer=customer, date=line.date, amount=amount, mode="bank_transfer",
                                       reference=(line.reference or line.description)[:100], deposit_account=deposit_account,
                                       bank_ledger_account=bank_ledger_account,
                                       notes=_note(line), allocations=[{"document": d, "amount": str(u)} for d, u in plan])
        source, source_id = JournalEntry.Source.PAYMENT, str(payment.pk)
        record = {"type": "customer_payment", "id": str(payment.pk), "number": payment.reference_code, "url": f"/sales/payments/{payment.pk}",
                  "applied_to": [SalesDocument.objects.get(pk=d).number for d, _ in plan]}

    elif kind == "vendor_payment":
        from apps.purchases.models import Vendor, VendorBill
        from apps.purchases.services import amounts as p_amounts, payments
        vendor = _pick(Vendor, data.get("vendor"), company, "vendor", "Choose the vendor who was paid.")
        wanted = [str(x) for x in (data.get("bills") or [])]
        open_bills = {str(b.pk): p_amounts.balance_of(b) for b in
                      p_amounts.bills_with_amounts(VendorBill.objects.filter(vendor=vendor, status=VendorBill.Status.OPEN))}
        if any(x not in open_bills or open_bills[x] <= 0 for x in wanted):
            raise ValidationError({"bills": "Choose open bills of this vendor."})
        plan = _allocate(amount, [(x, open_bills[x]) for x in wanted])
        paid_from, bank_ledger_account = _bank_record_account(account)
        pay = payments.record_payment(company, actor, vendor=vendor, date=line.date, amount=amount, mode="bank",
                                      reference=(line.reference or line.description)[:100], paid_from=paid_from,
                                      bank_ledger_account=bank_ledger_account,
                                      notes=_note(line), allocations=[{"bill": b, "amount": str(u)} for b, u in plan])
        source, source_id = JournalEntry.Source.VENDOR_PAYMENT, str(pay.pk)
        record = {"type": "vendor_payment", "id": str(pay.pk), "number": pay.number, "url": f"/purchases/payments/{pay.pk}",
                  "status": pay.status}

    elif kind in ("expense", "income", "bank_charge"):
        from . import vouchers
        v_kind = "income" if kind == "income" else "expense"
        if kind == "bank_charge" and not data.get("category"):
            category = bank_charges_account(company)
            if category is None:
                raise ValidationError({"category": "Choose the expense account for bank charges."})
        else:
            category = _pick(Account, data.get("category"), company, "category", f"Choose the {v_kind} account.")
        tax_mode, tax_rate = data.get("tax_mode") or "none", data.get("tax_rate") or 0
        net = _gst_net(company, v_kind, category, account, amount, tax_mode, tax_rate)
        v = vouchers.create_voucher(company, actor, kind=v_kind, date=line.date, category=category, money_account=account, amount=net,
                                    tax_mode=tax_mode, tax_rate=tax_rate,
                                    party_name=data.get("party_name") or ("" if kind != "bank_charge" else (account.bank_account.bank_name if account.bank_account_id else "")),
                                    reference=(line.reference or "")[:100], notes=(data.get("notes") or _note(line))[:300],
                                    project_id=data.get("project") or None, record=True)
        source, source_id = (JournalEntry.Source.INCOME if v_kind == "income" else JournalEntry.Source.EXPENSE), str(v.pk)
        record = {"type": v_kind, "id": str(v.pk), "number": v.number, "url": f"/accounting/vouchers/{v.pk}", "status": v.status}

    else:  # transfer between the company's own accounts
        from . import vouchers
        other = _pick(Account, data.get("other_account"), company, "other_account", "Choose the other account.")
        frm, to = (other, account) if money_in else (account, other)
        entry = vouchers.transfer(company, actor, date=line.date, from_account=frm, to_account=to, amount=amount,
                                  notes=(data.get("notes") or _note(line))[:300])
        record = {"type": "transfer", "id": str(entry.pk), "number": entry.reference, "url": f"/accounting/journal/{entry.pk}"}
        return _finish(line, actor, entry, record)

    entry = JournalEntry.objects.filter(company=company, source=source, source_id=source_id).first()
    if entry is None:  # held for approval: nothing is in the books yet
        line.created_source, line.created_source_id = source, source_id
        line.save(update_fields=["created_source", "created_source_id", "updated_at"])
        _audit(company, actor, "statement_line", line.pk, Action.UPDATED, reason="Entry made from the line is waiting for approval",
               new={"record": f"{record['type']} {record['id']}"})
        return {"match": None, "record": record, "pending": True}
    return _finish(line, actor, entry, record)


def _finish(line, actor, entry, record):
    jl = entry.lines.filter(account=line.account).first()
    if jl is None or signed(jl) != line.amount:
        raise ValidationError("The entry was not posted to this bank account, so it cannot be matched to the line.")
    m = match(line.company, actor, account=line.account, statement_line_ids=[line.pk], journal_line_ids=[jl.pk],
              kind=BankMatch.Kind.CREATED, note=f"{record['type'].replace('_', ' ').capitalize()} {record.get('number') or ''}".strip())
    return {"match": m, "record": record, "pending": False}


def _pick(model, ref, company, field, message):
    obj = None
    if ref and _is_uuid(ref):
        obj = model.objects.filter(pk=ref, company=company).first()
    if obj is None:
        raise ValidationError({field: message})
    return obj
