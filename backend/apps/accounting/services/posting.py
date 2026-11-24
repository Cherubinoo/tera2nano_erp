"""Automatic postings: what an invoice, credit note, debit note or payment does to the books.

    Invoice / debit note   Dr Accounts receivable (total)    Cr Sales (before tax)    Cr GST payable (each tax)
    Credit note            Dr Sales returns (before tax)     Dr GST payable (each)    Cr Accounts receivable (total)
    Payment received       Dr Cash or bank (amount)          Cr Accounts receivable (applied)   Cr Customer advances (the rest)

Each event posts once (the entry is keyed on the source document), and a void posts the mirror image. Proforma invoices are not
accounting events and post nothing. Called from `apps.sales.services` inside the same transaction as the sale itself, so a sale
and its books can never disagree.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.sales.models import Payment, SalesDocument

from ..models import JournalEntry
from . import chart, ledger

Kind = SalesDocument.Kind
TAX_KEY = {"CGST": "cgst", "SGST": "sgst", "UTGST": "sgst", "IGST": "igst"}


def tax_rows(record):
    """[(system account key, amount)] for each GST line of a worked-out document, such as ("gst_cgst_out", 162.00)."""
    rows = {}
    for row in (record or {}).get("totals", {}).get("tax", []):
        key = f"gst_{TAX_KEY[row['label'].split()[0]]}_out"
        rows[key] = rows.get(key, Decimal("0.00")) + Decimal(row["amount"])
    return sorted(rows.items())


def post_document(doc, actor=None):
    """Post an issued invoice, debit note or credit note. Returns the entry, or None for a proforma."""
    if doc.kind == Kind.PROFORMA:
        return None
    record = doc.record or {}
    taxable = Decimal(record["totals"]["taxable"])
    taxes = tax_rows(record)
    if taxable + sum((a for _, a in taxes), Decimal("0.00")) != doc.grand_total:
        raise ValidationError(f"{doc.number}: its tax lines do not add up to its total; it cannot be posted.")
    party = {"type": "customer", "id": doc.customer_id}
    label = f"{doc.get_kind_display()} {doc.number}: {doc.customer.display_name}"
    if doc.kind == Kind.CREDIT_NOTE:
        lines = [{"account": "sales_returns", "debit": taxable, "memo": label, "project": doc.project_id}]
        lines += [{"account": key, "debit": amount, "memo": label, "project": doc.project_id} for key, amount in taxes]
        lines.append({"account": "receivable", "credit": doc.grand_total, "memo": label, "party": party, "project": doc.project_id})
    else:
        lines = [{"account": "receivable", "debit": doc.grand_total, "memo": label, "party": party, "project": doc.project_id},
                 {"account": "sales", "credit": taxable, "memo": label, "project": doc.project_id}]
        lines += [{"account": key, "credit": amount, "memo": label, "project": doc.project_id} for key, amount in taxes]
    return ledger.post(doc.company, date=doc.issue_date, lines=lines, narration=label, source=doc.kind,
                       source_id=str(doc.pk), reference=doc.number, actor=actor)


def reverse_document(doc, reason, actor):
    return ledger.reverse_source(doc.company, doc.kind, doc.pk, actor, reason)


def money_account_for(company, payment):
    """Where a payment's money went: a specific ledger account from a bank statement line, else the chosen bank's ledger
    account, else cash for cash payments, else the default bank."""
    if payment.bank_ledger_account_id:
        return payment.bank_ledger_account
    if payment.deposit_account_id:
        return chart.ledger_for_bank(company, payment.deposit_account)
    return chart.system_account(company, "cash" if payment.mode == Payment.Mode.CASH else "bank")


def post_payment(payment, actor=None):
    company = payment.company
    applied = sum((a.amount for a in payment.allocations.all()), Decimal("0.00"))
    advance = payment.amount - applied
    label = f"Payment {payment.reference_code}: {payment.customer.display_name}"
    party = {"type": "customer", "id": payment.customer_id}
    lines = [{"account": money_account_for(company, payment), "debit": payment.amount, "memo": label}]
    if applied > 0:
        lines.append({"account": "receivable", "credit": applied, "memo": label, "party": party})
    if advance > 0:
        lines.append({"account": "customer_advances", "credit": advance, "memo": f"{label} (advance)", "party": party})
    return ledger.post(company, date=payment.date, lines=lines, narration=label, source=JournalEntry.Source.PAYMENT,
                       source_id=str(payment.pk), reference=payment.reference_code, actor=actor)


def reverse_payment(payment, reason, actor):
    return ledger.reverse_source(payment.company, JournalEntry.Source.PAYMENT, payment.pk, actor, reason)


def rebuild(company, actor=None):
    """Post every issued document and recorded payment that has no entry yet (sales made before the books existed)."""
    done = {"documents": 0, "payments": 0}
    posted = set(JournalEntry.objects.filter(company=company).exclude(source_id="").values_list("source", "source_id"))
    for doc in SalesDocument.objects.filter(company=company, status=SalesDocument.Status.ISSUED).exclude(
            kind=Kind.PROFORMA).select_related("customer", "company").order_by("issue_date", "created_at"):
        if (doc.kind, str(doc.pk)) not in posted:
            post_document(doc, actor)
            done["documents"] += 1
    for pay in Payment.objects.filter(company=company, status=Payment.Status.RECORDED).select_related(
            "customer", "company", "deposit_account").order_by("date", "created_at"):
        if (JournalEntry.Source.PAYMENT, str(pay.pk)) not in posted:
            post_payment(pay, actor)
            done["payments"] += 1
    return done
