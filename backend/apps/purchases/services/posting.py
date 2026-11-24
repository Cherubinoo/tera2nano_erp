"""What a vendor bill, a payment to a vendor and a vendor's opening balance do to the books.

    Vendor bill      Dr Expense (or asset) accounts   taxable value (plus GST when the credit is not claimed)
                     Dr GST input credit (CGST/SGST/IGST)   only when the bill's ITC is eligible
                     Cr Accounts payable (party = vendor)   the bill total less TDS
                     Cr TDS payable                          TDS withheld
    Vendor payment   Dr Accounts payable   the part applied to bills
                     Dr Advances to vendors   the part not applied (the vendor's advance)
                     Cr Cash or bank   the amount paid
    Advance applied  Dr Accounts payable / Cr Advances to vendors
    Opening balance  Dr Opening balance adjustments / Cr Accounts payable   (only when the vendor has one)

All of it goes through `apps.accounting.services.ledger.post`, in the same transaction as the purchase record, so the
purchase and the books cannot disagree. A void posts the mirror image with `ledger.reverse_source`.
"""
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounting.models import Account, JournalEntry
from apps.accounting.services import chart, ledger

from .common import ZERO

Source = JournalEntry.Source
ADVANCE_NAME = "Advances to vendors"
DEFAULT_EXPENSE_CODE = "5000"


def advance_account(company):
    """The asset account holding money paid to vendors before there was a bill to set it against."""
    acct = Account.objects.filter(company=company, name=ADVANCE_NAME).first()
    if acct is not None:
        return acct
    chart.ensure_chart(company)
    used = set(Account.objects.filter(company=company).values_list("code", flat=True))
    code = next(str(n) for n in range(1510, 1599) if str(n) not in used)
    try:
        with transaction.atomic():
            return Account.objects.create(company=company, code=code, name=ADVANCE_NAME, type="asset",
                                          subtype=Account.Subtype.OTHER_CURRENT_ASSET)
    except IntegrityError:
        return Account.objects.get(company=company, name=ADVANCE_NAME)


def default_expense_account(company):
    """Where a bill line goes when none is chosen: account 5000 (Purchases / direct costs), created if the chart lacks it."""
    chart.ensure_chart(company)
    acct = Account.objects.filter(company=company, code=DEFAULT_EXPENSE_CODE, is_active=True).first()
    if acct is None:
        acct = Account.objects.filter(company=company, type="expense", is_active=True).order_by("code").first()
    if acct is None:
        acct = Account.objects.create(company=company, code=DEFAULT_EXPENSE_CODE, name="Purchases", type="expense",
                                      subtype=Account.Subtype.DIRECT_COST)
    return acct


def expense_accounts(company):
    chart.ensure_chart(company)
    return Account.objects.filter(company=company, is_active=True).filter(
        type="expense").order_by("code")


def money_account_for(company, payment):
    """Where the money left from: a specific ledger account from a bank statement line, else the chosen bank's ledger
    account, else cash for cash payments, else the default bank."""
    if payment.bank_ledger_account_id:
        return payment.bank_ledger_account
    if payment.paid_from_id:
        return chart.ledger_for_bank(company, payment.paid_from)
    return chart.system_account(company, "cash" if payment.mode == payment.Mode.CASH else "bank")


# --------------------------------------------------------------------------- bills

def post_bill(bill, actor=None):
    rec = bill.record or {}
    claim = bill.itc_status == bill.Itc.ELIGIBLE
    party = {"type": "vendor", "id": bill.vendor_id}
    label = f"Bill {bill.number}: {bill.vendor.name} (invoice {bill.vendor_bill_no})"
    spend, taxes = {}, {"gst_cgst_in": ZERO, "gst_sgst_in": ZERO, "gst_igst_in": ZERO}
    for line in rec["lines"]:
        amount = Decimal(line["taxable"])
        if claim:
            taxes["gst_cgst_in"] += Decimal(line["cgst"])
            taxes["gst_sgst_in"] += Decimal(line["sgst"])
            taxes["gst_igst_in"] += Decimal(line["igst"])
        else:
            amount += Decimal(line["tax"])  # credit not claimed: the tax is part of the cost
        spend[line["account"]] = spend.get(line["account"], ZERO) + amount
    accounts = {str(a.pk): a for a in Account.objects.filter(pk__in=[k for k in spend if k], company=bill.company)}
    lines = []
    for account_id, amount in spend.items():
        if amount <= 0:
            continue
        acct = accounts.get(account_id)
        if acct is None:
            raise ValidationError({"items": "A line has no valid expense account."})
        lines.append({"account": acct, "debit": amount, "memo": label, "project": bill.project_id})
    lines += [{"account": key, "debit": amount, "memo": f"{label}: input GST", "project": bill.project_id}
              for key, amount in taxes.items() if amount > 0]
    if bill.net_payable > 0:
        lines.append({"account": "payable", "credit": bill.net_payable, "memo": label, "party": party, "project": bill.project_id})
    if bill.tds_amount > 0:
        lines.append({"account": "tds_payable", "credit": bill.tds_amount, "memo": f"{label}: TDS {bill.tds_section}",
                      "party": party, "project": bill.project_id})
    return ledger.post(bill.company, date=bill.bill_date, lines=lines, narration=label, source=Source.VENDOR_BILL,
                       source_id=str(bill.pk), reference=bill.number, actor=actor)


def reverse_bill(bill, reason, actor):
    return ledger.reverse_source(bill.company, Source.VENDOR_BILL, bill.pk, actor, reason)


# --------------------------------------------------------------------------- payments

def post_payment(payment, actor=None):
    applied = sum((a.amount for a in payment.allocations.all()), ZERO)
    advance = payment.amount - applied
    label = f"Payment {payment.number} to {payment.vendor.name}"
    party = {"type": "vendor", "id": payment.vendor_id}
    lines = [{"account": money_account_for(payment.company, payment), "credit": payment.amount, "memo": label,
              "project": payment.project_id}]
    if applied > 0:
        lines.append({"account": "payable", "debit": applied, "memo": label, "party": party, "project": payment.project_id})
    if advance > 0:
        lines.append({"account": advance_account(payment.company), "debit": advance, "memo": f"{label} (advance)",
                      "party": party, "project": payment.project_id})
    return ledger.post(payment.company, date=payment.date, lines=lines, narration=label, source=Source.VENDOR_PAYMENT,
                       source_id=str(payment.pk), reference=payment.number, actor=actor)


def post_advance_applied(payment, amount, on_date, actor=None):
    """Move part of a payment's advance onto bills: Dr payable, Cr advances. Returns the batch id of the posting."""
    batch = uuid.uuid4().hex[:12]
    party = {"type": "vendor", "id": payment.vendor_id}
    label = f"Advance from {payment.number} applied to bills of {payment.vendor.name}"
    ledger.post(payment.company, date=on_date, lines=[
        {"account": "payable", "debit": amount, "memo": label, "party": party},
        {"account": advance_account(payment.company), "credit": amount, "memo": label, "party": party},
    ], narration=label, source=Source.VENDOR_PAYMENT, source_id=f"{payment.pk}:{batch}", reference=payment.number, actor=actor)
    return batch


def reverse_payment(payment, reason, actor):
    """Reverse the payment's entry and any entries that applied its advance later."""
    ids = JournalEntry.objects.filter(company=payment.company, source=Source.VENDOR_PAYMENT,
                                      source_id__startswith=str(payment.pk)).values_list("source_id", flat=True)
    for source_id in list(ids):
        ledger.reverse_source(payment.company, Source.VENDOR_PAYMENT, source_id, actor, reason)


# --------------------------------------------------------------------------- opening balance

def post_opening_balance(vendor, actor=None):
    label = f"Opening balance owed to {vendor.name}"
    return ledger.post(vendor.company, date=vendor.opening_balance_date, lines=[
        {"account": "opening_balance_equity", "debit": vendor.opening_balance, "memo": label},
        {"account": "payable", "credit": vendor.opening_balance, "memo": label, "party": {"type": "vendor", "id": vendor.pk}},
    ], narration=label, source=Source.OPENING, source_id=f"vendor:{vendor.pk}", reference=vendor.code, actor=actor)
