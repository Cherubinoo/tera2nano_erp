"""Reading GST and TDS figures out of the journal, and the few ledger accounts this module posts to.

Everything is a `Sum` over journal lines grouped by account and by what caused the entry. Entries are sorted into three kinds:
 - "setoff"  : ITC set-off entries and their reversals (made by this module)
 - "payment" : tax challans and their reversals (made by this module)
 - "books"   : everything else (invoices, credit notes, bills, vouchers, manual journals): the tax raised or credit earned
A reversal is classed by the entry it reverses, so a voided invoice cancels its own liability and a voided challan restores the liability.
"""
import logging
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import TruncMonth

SETOFF_SOURCE = "tax_setoff"
PAYMENT_SOURCE = "tax_payment"
OPENING_SOURCE = "opening"

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
log = logging.getLogger(__name__)

HEADS = ("cgst", "sgst", "igst")
OUT_KEY = {h: f"gst_{h}_out" for h in HEADS}
IN_KEY = {h: f"gst_{h}_in" for h in HEADS}
ALL_KEYS = [*OUT_KEY.values(), *IN_KEY.values()]

CESS_ACCOUNT = ("2140", "GST cess payable")
TAX_EXPENSE_ACCOUNT = ("5960", "Interest and penalty on taxes")
ADVANCE_TAX_ACCOUNT = ("1450", "Advance tax paid")


def money(v):
    return Decimal(v or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def category(source, reverses_source):
    eff = reverses_source if source == "reversal" and reverses_source else source
    if eff == SETOFF_SOURCE:
        return "setoff"
    if eff == PAYMENT_SOURCE:
        return "payment"
    return "books"


def safe(fn, default, what=""):
    """Run a read that may meet a missing table or field (another module not migrated yet); give `default` instead of an error."""
    try:
        with transaction.atomic():
            return fn()
    except Exception:  # noqa: BLE001  (a register must never break the page)
        log.exception("tax: %s could not be read", what or fn.__name__)
        return default


def accounts_by_key(company, keys=None):
    from apps.accounting.models import Account

    qs = Account.objects.filter(company=company).exclude(system_key="")
    if keys:
        qs = qs.filter(system_key__in=keys)
    return {a.system_key: a for a in qs}


def zero_heads():
    return {h: ZERO for h in HEADS}


def activity(company, start, end, *, monthly=False):
    """GST activity in [start, end] split by kind.

    Returns {key or (key, "YYYY-MM"): {"books": (debit, credit), "setoff": (...), "payment": (...)}} for the six GST accounts.
    """
    from apps.accounting.models import JournalLine

    qs = JournalLine.objects.filter(entry__company=company, account__system_key__in=ALL_KEYS)
    if start:
        qs = qs.filter(entry__date__gte=start)
    if end:
        qs = qs.filter(entry__date__lte=end)
    fields = ["account__system_key", "entry__source", "entry__reverses__source"]
    if monthly:
        qs = qs.annotate(m=TruncMonth("entry__date"))
        fields.append("m")
    out = defaultdict(lambda: {"books": [ZERO, ZERO], "setoff": [ZERO, ZERO], "payment": [ZERO, ZERO]})
    for r in qs.values(*fields).annotate(d=Sum("debit"), c=Sum("credit")):
        if r["entry__source"] == OPENING_SOURCE:
            kind = "opening"
            out_key = r["account__system_key"] if not monthly else (r["account__system_key"], f"{r['m'].year}-{r['m'].month:02d}")
            bucket = out[out_key].setdefault("opening", [ZERO, ZERO])
        else:
            kind = category(r["entry__source"], r["entry__reverses__source"])
            out_key = r["account__system_key"] if not monthly else (r["account__system_key"], f"{r['m'].year}-{r['m'].month:02d}")
            bucket = out[out_key][kind]
        bucket[0] += r["d"] or ZERO
        bucket[1] += r["c"] or ZERO
    return out


def raised(act, head, month=None):
    """Output tax raised: credits minus debits of the books kind (credit notes reduce it). Opening balances count as raised."""
    key = OUT_KEY[head] if month is None else (OUT_KEY[head], month)
    a = act.get(key)
    if not a:
        return ZERO
    d, c = a["books"]
    od, oc = a.get("opening", (ZERO, ZERO))
    return money(c - d + oc - od)


def accrued(act, head, month=None):
    """Input credit earned: debits minus credits of the books kind (credit notes received reduce it)."""
    key = IN_KEY[head] if month is None else (IN_KEY[head], month)
    a = act.get(key)
    if not a:
        return ZERO
    d, c = a["books"]
    od, oc = a.get("opening", (ZERO, ZERO))
    return money(d - c + od - oc)


def setoff_used(act, head, side, month=None):
    """What set-off took from the liability (side 'out') or used of the credit (side 'in')."""
    key = (OUT_KEY if side == "out" else IN_KEY)[head]
    key = key if month is None else (key, month)
    a = act.get(key)
    if not a:
        return ZERO
    d, c = a["setoff"]
    return money(d - c) if side == "out" else money(c - d)


def paid(act, head, month=None):
    key = OUT_KEY[head] if month is None else (OUT_KEY[head], month)
    a = act.get(key)
    if not a:
        return ZERO
    d, c = a["payment"]
    return money(d - c)


def balances(company, as_of):
    """Natural balances of the GST accounts on a date: {"out": {head: amount owed}, "in": {head: credit available}}."""
    from apps.accounting.models import JournalLine

    qs = JournalLine.objects.filter(entry__company=company, account__system_key__in=ALL_KEYS)
    if as_of:
        qs = qs.filter(entry__date__lte=as_of)
    sums = {r["account__system_key"]: (r["d"] or ZERO, r["c"] or ZERO)
            for r in qs.values("account__system_key").annotate(d=Sum("debit"), c=Sum("credit"))}
    out = {h: money(sums.get(OUT_KEY[h], (ZERO, ZERO))[1] - sums.get(OUT_KEY[h], (ZERO, ZERO))[0]) for h in HEADS}
    inp = {h: money(sums.get(IN_KEY[h], (ZERO, ZERO))[0] - sums.get(IN_KEY[h], (ZERO, ZERO))[1]) for h in HEADS}
    return {"out": out, "in": inp}


def account_balance(company, key, as_of=None):
    """Natural balance of one system account (0 when it does not exist yet)."""
    from apps.accounting.models import Account, JournalLine

    acct = Account.objects.filter(company=company, system_key=key).first()
    if acct is None:
        return ZERO
    qs = JournalLine.objects.filter(account=acct)
    if as_of:
        qs = qs.filter(entry__date__lte=as_of)
    r = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    d, c = r["d"] or ZERO, r["c"] or ZERO
    return money(d - c) if acct.is_debit_normal else money(c - d)


def ordinary_account(company, spec, type_, subtype):
    """An ordinary (non-system) account found by name, or created under the first free code from `spec`'s code."""
    from apps.accounting.models import Account
    from apps.accounting.services import chart

    code, name = spec
    acct = Account.objects.filter(company=company, name=name).first()
    if acct:
        return acct
    chart.ensure_chart(company)
    used = set(Account.objects.filter(company=company).values_list("code", flat=True))
    n = int(code)
    while str(n) in used:
        n += 1
    return Account.objects.create(company=company, code=str(n), name=name, type=type_, subtype=subtype)
