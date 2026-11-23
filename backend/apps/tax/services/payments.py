"""Tax payments (challans): record, post to the books, void.

    GST      Dr GST payable CGST / SGST / IGST, cess payable   Dr Interest and penalty on taxes   Cr Bank
    TDS      Dr TDS payable                                    Dr Interest and penalty on taxes   Cr Bank
    Advance tax / self-assessment   Dr Advance tax paid       Dr Interest and penalty on taxes   Cr Bank
"""
import datetime
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounting.models import JournalEntry
from apps.accounting.services import chart, ledger
from apps.company.constants import DocType
from apps.company.models import CompanyBankAccount
from apps.company.services import numbering

from .. import constants as C
from ..models import TaxPayment
from . import books, periods
from .common import Action, ZERO, amount, audit_event, money, parse_date

CIN_RE = re.compile(r"^[A-Za-z0-9\-/]{0,40}$")


def head_names(tax_type):
    return [h for h, _ in C.PAYMENT_HEADS[tax_type]]


def snapshot(p):
    return {"reference": p.reference, "tax_type": p.tax_type, "date": p.date, "total": p.total, "heads": p.heads, "cin": p.cin,
            "financial_year": p.financial_year, "status": p.status}


def clean(company, data):
    """Validated, normalised values for a new payment."""
    tax_type = data.get("tax_type")
    if tax_type not in C.PAYMENT_HEADS:
        raise ValidationError({"tax_type": "Choose GST, TDS, advance tax or income tax."})
    date = parse_date(data.get("date"), "date", required=True)
    if date > datetime.date.today() + datetime.timedelta(days=1):
        raise ValidationError({"date": "A payment cannot be dated in the future."})
    raw_heads = data.get("heads") or {}
    if not isinstance(raw_heads, dict):
        raise ValidationError({"heads": "Send the amounts as an object, for example {\"cgst\": \"100.00\"}."})
    allowed = head_names(tax_type)
    unknown = [k for k in raw_heads if k not in allowed]
    if unknown:
        raise ValidationError({"heads": f"Unknown head: {', '.join(unknown)}."})
    heads = {}
    for h in allowed:
        try:
            v = amount(raw_heads.get(h), f"heads.{h}")
        except ValidationError as exc:
            raise ValidationError({"heads": f"{dict(C.PAYMENT_HEADS[tax_type])[h]}: {exc.message_dict[f'heads.{h}'][0]}"})
        if v > 0:
            heads[h] = str(money(v))
    if not heads:
        raise ValidationError({"heads": "Enter the amount paid under at least one head."})
    total = sum((Decimal(v) for v in heads.values()), ZERO)
    cin = str(data.get("cin") or "").strip()
    if not CIN_RE.match(cin):
        raise ValidationError({"cin": "Use letters, digits, - or / (at most 40 characters)."})
    fy = data.get("financial_year") or periods.fy_label_for(date)
    periods.parse_fy(fy)
    out = {"tax_type": tax_type, "date": date, "heads": heads, "total": money(total), "cin": cin, "financial_year": fy,
           "bank_name": str(data.get("bank_name") or "").strip()[:100], "notes": str(data.get("notes") or "").strip()[:300],
           "period_label": "", "period_start": None, "instalment": None, "section": "", "bank_account": None}
    if data.get("bank_account"):
        try:
            out["bank_account"] = CompanyBankAccount.objects.filter(pk=data["bank_account"], company=company).first()
        except Exception:  # noqa: BLE001  (a malformed id)
            out["bank_account"] = None
        if out["bank_account"] is None:
            raise ValidationError({"bank_account": "Choose one of the company's bank accounts."})
        out["bank_name"] = out["bank_name"] or out["bank_account"].bank_name
    if tax_type == "gst" and data.get("period"):
        start, _end = periods.parse_month(data["period"])
        out["period_start"], out["period_label"] = start, periods.month_label(start)
    if tax_type == "tds":
        section = str(data.get("section") or "").strip().upper()
        if section and not re.match(r"^[0-9]{3}[A-Z]{0,3}$", section):
            raise ValidationError({"section": "Use a section such as 194J, or 192 for salary."})
        out["section"] = section
        q = data.get("quarter")
        if q not in (None, ""):
            if str(q) not in ("1", "2", "3", "4"):
                raise ValidationError({"quarter": "Choose quarter 1 to 4."})
            out["period_label"] = f"Q{q} {fy}"
    if tax_type == "advance_tax":
        n = data.get("instalment")
        if n in (None, ""):
            n = next((i for i, due, _ in periods.advance_tax_dates(fy) if date <= due), 4)
        if str(n) not in ("1", "2", "3", "4"):
            raise ValidationError({"instalment": "Choose instalment 1 to 4."})
        out["instalment"] = int(n)
        out["period_label"] = f"Instalment {n} {fy}"
    return out


def _accounts_for(company, p):
    """(debit lines as [(account, amount)], bank account) for a payment."""
    h = {k: Decimal(v) for k, v in p.heads.items()}
    debits = []
    if p.tax_type == "gst":
        for head in books.HEADS:
            if h.get(head):
                debits.append((chart.system_account(company, books.OUT_KEY[head]), h[head], f"GST {head.upper()}"))
        if h.get("cess"):
            debits.append((books.ordinary_account(company, books.CESS_ACCOUNT, "liability", "tax_liability"), h["cess"], "GST cess"))
        extra = h.get("interest", ZERO) + h.get("penalty", ZERO)
    elif p.tax_type == "tds":
        if h.get("tds"):
            debits.append((chart.system_account(company, "tds_payable"), h["tds"], f"TDS {p.section}".strip()))
        extra = h.get("interest", ZERO) + h.get("fee", ZERO)
    else:
        if h.get("tax"):
            debits.append((books.ordinary_account(company, books.ADVANCE_TAX_ACCOUNT, "asset", "other_current_asset"), h["tax"],
                           "Advance tax" if p.tax_type == "advance_tax" else "Self-assessment tax"))
        extra = h.get("interest", ZERO) + h.get("penalty", ZERO)
    if extra > 0:
        debits.append((books.ordinary_account(company, books.TAX_EXPENSE_ACCOUNT, "expense", "other_expense"), extra, "Interest, penalty or fee"))
    bank = chart.ledger_for_bank(company, p.bank_account) if p.bank_account_id else chart.system_account(company, "bank")
    return debits, bank


@transaction.atomic
def create(company, actor, data):
    v = clean(company, data)
    ref = numbering.allocate_number(company, DocType.TAX_PAYMENT, actor=actor, on_date=v["date"]).reference_number
    p = TaxPayment.objects.create(company=company, reference=ref, created_by=actor, **v)
    debits, bank = _accounts_for(company, p)
    label = f"{p.get_tax_type_display()} challan {p.reference}" + (f" {p.period_label}" if p.period_label else "") + (f" section {p.section}" if p.section else "")
    lines = [{"account": acct, "debit": amt, "memo": f"{label}: {what}"} for acct, amt, what in debits]
    lines.append({"account": bank, "credit": p.total, "memo": label})
    ledger.post(company, date=p.date, lines=lines, narration=label, source=books.PAYMENT_SOURCE, source_id=str(p.id), reference=p.reference, actor=actor)
    audit_event(company, actor, "tax_payment", p.pk, Action.CREATED, new=snapshot(p))
    return p


@transaction.atomic
def void(company, actor, payment, reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why this payment is being voided."})
    if len(reason) > 300:
        raise ValidationError({"reason": "Use at most 300 characters."})
    p = TaxPayment.objects.select_for_update(of=("self",)).get(pk=payment.pk)
    if p.status == TaxPayment.Status.VOID:
        raise ValidationError({"detail": "This payment is already void."})
    entry = JournalEntry.objects.filter(company=company, source=books.PAYMENT_SOURCE, source_id=str(p.id)).first()
    if entry is not None and entry.status == "posted":
        ledger.reverse(entry, actor, f"Void {p.reference}: {reason}", on_date=entry.date)  # dated like the payment, so that period is as if it never happened
    p.status, p.void_reason = TaxPayment.Status.VOID, reason
    p.save(update_fields=["status", "void_reason", "updated_at"])
    audit_event(company, actor, "tax_payment", p.pk, Action.DEACTIVATED, previous={"status": "recorded"}, new={"status": "void"}, reason=reason)
    return p


def update_notes(company, actor, payment, data):
    """Only the descriptive fields can change once the money is in the books."""
    before = {"cin": payment.cin, "notes": payment.notes, "bank_name": payment.bank_name}
    if "cin" in data:
        cin = str(data["cin"] or "").strip()
        if not CIN_RE.match(cin):
            raise ValidationError({"cin": "Use letters, digits, - or / (at most 40 characters)."})
        payment.cin = cin
    if "notes" in data:
        payment.notes = str(data["notes"] or "").strip()[:300]
    if "bank_name" in data:
        payment.bank_name = str(data["bank_name"] or "").strip()[:100]
    payment.save(update_fields=["cin", "notes", "bank_name", "updated_at"])
    audit_event(company, actor, "tax_payment", payment.pk, Action.UPDATED, previous=before,
                new={"cin": payment.cin, "notes": payment.notes, "bank_name": payment.bank_name})
    return payment
