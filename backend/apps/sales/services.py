"""Sales rules: building and issuing documents, applying payments and credits, balances, ageing and the dashboard.

Everything that changes money runs inside a transaction with the rows it depends on locked, so two people recording
payments against the same invoice cannot both succeed. Amounts are `Decimal` and only ever rounded to the paisa.
"""
import datetime
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce

from apps.company.constants import DocType
from apps.company.models import CompanyProfileAuditEvent
from apps.accounting.models import JournalEntry
from apps.accounting.services import ledger, posting
from apps.company.services import audit, company as company_service, documents, numbering, quotations

from .models import CreditApplication, Customer, Payment, PaymentAllocation, SalesDocument

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
Kind, Status = SalesDocument.Kind, SalesDocument.Status
Action = CompanyProfileAuditEvent.Action

# Days overdue -> ageing bucket, as in most accounting packages.
AGING = [("current", "Current", None), ("d1_15", "1-15 days", 15), ("d16_30", "16-30 days", 30),
         ("d31_45", "31-45 days", 45), ("d46_plus", "Over 45 days", None)]


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def amount_from(value, label="Amount"):
    """A positive amount with at most two decimal places, from user input."""
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError({"amount": f"{label} must be a number."})
    if not d.is_finite() or d <= 0:
        raise ValidationError({"amount": f"{label} must be more than zero."})
    if d.as_tuple().exponent < -2:
        raise ValidationError({"amount": f"{label}: use at most 2 decimal places."})
    if d > Decimal("100000000000"):
        raise ValidationError({"amount": f"{label} is too large."})
    return d


# --------------------------------------------------------------------------- projects (soft reference, apps.projects)

def project_lookup(company):
    """The projects a sale can be tagged to: the same data as GET /api/v1/projects/lookup/ (cancelled ones are left out).

    Read through the projects app, never a foreign key. Without the projects app installed the list is empty."""
    from django.apps import apps
    if not apps.is_installed("apps.projects"):
        return {}
    from apps.projects.models import Project
    qs = Project.objects.filter(company=company).exclude(status=Project.Status.CANCELLED)
    return {p.pk: {"id": str(p.pk), "code": p.code, "name": p.name} for p in qs}


def resolve_project(company, value, *, current=None):
    """The project id (a UUID) for what the caller sent, None for "no project". An unknown, cancelled or malformed id is a
    400. Keeping the project a draft is already tagged with is always allowed, even if it was cancelled since."""
    if value in (None, ""):
        return None
    try:
        pid = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValidationError({"project": "Choose a project from the list."})
    if current is not None and pid == current:
        return pid
    if pid not in project_lookup(company):
        raise ValidationError({"project": "This project does not exist, or it has been cancelled."})
    return pid


def project_refs(company, ids):
    """{project id: {id, code, name}} for the given ids (cancelled projects included), for showing next to a record."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    from django.apps import apps
    if not apps.is_installed("apps.projects"):
        return {}
    from apps.projects.models import Project
    return {p.pk: {"id": str(p.pk), "code": p.code, "name": p.name}
            for p in Project.objects.filter(company=company, pk__in=ids)}


def _audit(company, actor, entity, instance_id, action, *, new=None, previous=None, reason=""):
    changed, prev, cur = audit.diff(entity, previous or {}, new or {})
    audit.record_event(company, actor, entity, instance_id, action, changed=changed, previous=prev, new=cur,
                       reason=reason, category="sales")


# --------------------------------------------------------------------------- amounts

def with_amounts(qs):
    """Annotate documents with what has been paid, credited and used.

    paid_amount: payments applied (voided payments do not count); credited_amount: credit notes applied to it;
    applied_amount: for a credit note, how much of it has been used; balance_due: what the customer still owes on it.
    """
    dec = DecimalField(max_digits=16, decimal_places=2)
    zero = Value(ZERO, output_field=dec)

    def total(sub):
        return Coalesce(Subquery(sub.annotate(t=Sum("amount")).values("t"), output_field=dec), zero, output_field=dec)

    paid = PaymentAllocation.objects.filter(document=OuterRef("pk")).exclude(payment__status=Payment.Status.VOID).values("document")
    credited = CreditApplication.objects.filter(document=OuterRef("pk")).exclude(credit_note__status=Status.VOID).values("document")
    applied = CreditApplication.objects.filter(credit_note=OuterRef("pk")).values("credit_note")
    return qs.annotate(paid_amount=total(paid), credited_amount=total(credited), applied_amount=total(applied)).annotate(
        balance_due=F("grand_total") - F("paid_amount") - F("credited_amount"))


def balance_of(doc):
    return money(doc.grand_total - doc.paid_amount - doc.credited_amount)


def credit_left(doc):
    return money(doc.grand_total - doc.applied_amount)


def display_status(doc, today=None):
    """(key, label) shown to people: draft, void, paid, overdue, partial, unpaid; for credit notes open or closed."""
    today = today or datetime.date.today()
    if doc.status == Status.DRAFT:
        return "draft", "Draft"
    if doc.status == Status.VOID:
        return "void", "Void"
    if doc.kind == Kind.CREDIT_NOTE:
        return ("open", "Open") if credit_left(doc) > 0 else ("closed", "Closed")
    if doc.kind == Kind.PROFORMA:
        return "issued", "Issued"
    balance = balance_of(doc)
    if balance <= 0:
        return "paid", "Paid"
    if doc.due_date and doc.due_date < today:
        return "overdue", "Overdue"
    if doc.paid_amount + doc.credited_amount > 0:
        return "partial", "Partially paid"
    return "unpaid", "Unpaid"


def apply_status_filter(qs, status, today):
    receivable = Q(kind__in=SalesDocument.RECEIVABLE, status=Status.ISSUED)
    if status == "draft":
        return qs.filter(status=Status.DRAFT)
    if status == "void":
        return qs.filter(status=Status.VOID)
    if status == "unpaid":
        return qs.filter(receivable, balance_due__gt=0)
    if status == "overdue":
        return qs.filter(receivable, balance_due__gt=0, due_date__lt=today)
    if status == "partial":
        return qs.filter(receivable, balance_due__gt=0).filter(Q(paid_amount__gt=0) | Q(credited_amount__gt=0))
    if status == "paid":
        return qs.filter(receivable, balance_due__lte=0)
    if status == "open":
        return qs.filter(kind=Kind.CREDIT_NOTE, status=Status.ISSUED, applied_amount__lt=F("grand_total"))
    return qs


# --------------------------------------------------------------------------- building documents

def default_due_date(company, customer, issue_date, kind):
    if kind not in SalesDocument.RECEIVABLE:
        return None
    days = customer.payment_terms_days
    if days is None:
        policy = company_service.resolve_policy(company, issue_date)
        days = getattr(policy, "payment_terms_days", None)
    return issue_date + datetime.timedelta(days=days) if days else None


def _raw(customer, form, kind, issue_date, due_date):
    """The input `quotations.build` understands, from the customer record and what was entered."""
    raw = {
        "customer": {"name": customer.legal_name or customer.display_name, "address": customer.address_lines,
                     "gstin": customer.gstin, "state_code": customer.state_code},
        "items": form.get("items"),
    }
    for key in ("subject", "intro", "notes", "terms", "details", "sections", "enquiry_ref", "include_bank",
                "show_signature", "valid_until"):
        if key in form:
            raw[key] = form[key]
    if due_date:
        raw["due_date"] = due_date.isoformat()
    return raw


def work_out(company, customer, kind, form, issue_date, due_date):
    """Lines and totals for a document that is being written, without saving or numbering anything."""
    resolved = company_service.resolve_for_document(kind, issue_date, company)
    record, _content = quotations.build(company, resolved, _raw(customer, form, kind, issue_date, due_date), issue_date,
                                        purpose=kind)
    return record


def _clean_form(form):
    if not isinstance(form, dict):
        raise ValidationError({"form": "Send the document as an object."})
    return {k: v for k, v in form.items() if k in (
        "items", "subject", "intro", "notes", "terms", "details", "sections", "enquiry_ref", "include_bank",
        "show_signature", "valid_until")}


def _totals(record):
    t = record["totals"]
    return {"taxable_total": Decimal(t["taxable"]), "tax_total": Decimal(t["tax_total"]), "grand_total": Decimal(t["grand_total"])}


@transaction.atomic
def save_draft(company, actor, *, kind, customer, form, issue_date, due_date=None, against=None, reason="",
               source_quotation=None, doc=None, project_id=None):
    """Create a draft, or replace what a draft holds. Items, GST and totals are checked and worked out here."""
    if kind not in Kind.values:
        raise ValidationError({"kind": "Unknown document type."})
    if doc is not None and doc.status != Status.DRAFT:
        raise ValidationError("An issued document cannot be edited. Void it and make a new one.")
    if not customer.is_active:
        raise ValidationError({"customer": "This customer is switched off."})
    form = _clean_form(form)
    project_id = resolve_project(company, project_id, current=doc.project_id if doc is not None else None)
    if due_date is None:
        due_date = default_due_date(company, customer, issue_date, kind)
    if kind in (Kind.CREDIT_NOTE, Kind.DEBIT_NOTE):
        _check_against(kind, customer, against, doc)
        if not reason.strip():
            raise ValidationError({"reason": "Give the reason for this note."})
    elif against is not None:
        raise ValidationError({"against": "Only credit and debit notes are raised against an invoice."})
    record = work_out(company, customer, kind, form, issue_date, due_date if kind in SalesDocument.RECEIVABLE else None)
    fields = dict(kind=kind, customer=customer, issue_date=issue_date,
                  due_date=due_date if kind in SalesDocument.RECEIVABLE else None, form=form, record=record,
                  currency=record["currency"], against=against, reason=reason.strip()[:300],
                  source_quotation=source_quotation, project_id=project_id, **_totals(record))
    if doc is None:
        doc = SalesDocument.objects.create(company=company, created_by=actor, **fields)
        _audit(company, actor, "sales_document", doc.pk, Action.CREATED,
               new={"kind": kind, "customer": customer.display_name, "status": "draft", "total": str(doc.grand_total),
                    "project": str(project_id) if project_id else ""})
    else:
        before = {"customer": doc.customer.display_name, "total": str(doc.grand_total),
                  "project": str(doc.project_id) if doc.project_id else ""}
        for key, value in fields.items():
            setattr(doc, key, value)
        doc.save()
        _audit(company, actor, "sales_document", doc.pk, Action.UPDATED, previous=before,
               new={"customer": customer.display_name, "total": str(doc.grand_total),
                    "project": str(project_id) if project_id else ""})
    return doc


def _check_against(kind, customer, against, doc):
    if against is None:
        raise ValidationError({"against": "Choose the invoice this note is raised against."})
    if against.kind != Kind.INVOICE or against.status != Status.ISSUED:
        raise ValidationError({"against": "Choose an issued invoice."})
    if against.customer_id != customer.pk:
        raise ValidationError({"against": "The invoice belongs to a different customer."})


def _credited_against(invoice, exclude=None):
    qs = SalesDocument.objects.filter(against=invoice, kind=Kind.CREDIT_NOTE, status=Status.ISSUED)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return money(qs.aggregate(t=Coalesce(Sum("grand_total"), Value(ZERO)))["t"])


@transaction.atomic
def issue(doc, actor):
    """Number the document, make its PDFs and freeze it. A credit note is applied to its invoice straight away."""
    doc = SalesDocument.objects.select_for_update(of=("self",)).select_related("customer", "against", "company").get(pk=doc.pk)
    company = doc.company
    if doc.status != Status.DRAFT:
        raise ValidationError("Only a draft can be issued.")
    if not doc.customer.is_active:
        raise ValidationError({"customer": "This customer is switched off."})
    if doc.grand_total <= 0:
        raise ValidationError({"items": "The total must be more than zero."})
    extra_meta = []
    if doc.kind in (Kind.CREDIT_NOTE, Kind.DEBIT_NOTE):
        _check_against(doc.kind, doc.customer, doc.against, doc)
        extra_meta = [["Against invoice", doc.against.number], ["Reason", doc.reason]]
        if doc.kind == Kind.CREDIT_NOTE and _credited_against(doc.against, doc) + doc.grand_total > doc.against.grand_total:
            raise ValidationError({"items": "Credit notes against this invoice would exceed the invoice total."})
    due = doc.due_date if doc.kind in SalesDocument.RECEIVABLE else None
    raw = _raw(doc.customer, doc.form, doc.kind, doc.issue_date, due)
    issued = documents.issue_document(
        doc.kind, actor=actor, content={"billing": raw, "extra_meta": extra_meta}, issue_date=doc.issue_date,
        source_module="sales", source_id=str(doc.pk))
    snap = issued.snapshot["billing"]
    doc.number, doc.issued_document, doc.status, doc.issued_by = issued.reference_number, issued, Status.ISSUED, actor
    doc.record = snap
    for key, value in _totals(snap).items():
        setattr(doc, key, value)
    doc.save()
    _audit(company, actor, "sales_document", doc.pk, Action.ISSUED,
           new={"issued": doc.number, "status": "issued", "total": str(doc.grand_total)},
           reason="Issued")
    posting.post_document(doc, actor)  # the books record the sale in the same transaction
    if doc.kind == Kind.CREDIT_NOTE:
        invoice = with_amounts(SalesDocument.objects.filter(pk=doc.against_id)).get()
        use = min(doc.grand_total, max(balance_of(invoice), ZERO))
        if use > 0:
            CreditApplication.objects.create(credit_note=doc, document=invoice, amount=use, applied_by=actor)
    return doc


@transaction.atomic
def void(doc, reason, actor):
    doc = SalesDocument.objects.select_for_update(of=("self",)).select_related("issued_document", "company").get(pk=doc.pk)
    if doc.status != Status.ISSUED:
        raise ValidationError("Only an issued document can be voided; delete a draft instead.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give a reason for voiding this document."})
    live_payments = doc.payment_allocations.exclude(payment__status=Payment.Status.VOID).exists()
    if doc.is_receivable and (live_payments or doc.credits_received.exclude(credit_note__status=Status.VOID).exists()):
        raise ValidationError("Payments or credits are applied to this document. Void those first.")
    if doc.kind == Kind.CREDIT_NOTE and doc.credit_applications.exists():
        raise ValidationError("This credit note has been used. Remove its use first by voiding the payments it settled.")
    if doc.kind == Kind.INVOICE and doc.notes.filter(status=Status.ISSUED).exists():
        raise ValidationError("Credit or debit notes are raised against this invoice. Void them first.")
    doc.status, doc.void_reason, doc.voided_at = Status.VOID, reason.strip()[:300], datetime.datetime.now(datetime.timezone.utc)
    doc.save()
    issued = doc.issued_document
    issued.status = issued.Status.VOID
    issued.save(update_fields=["status", "updated_at"])
    if issued.reference_id:
        numbering.void_number(issued.reference, reason.strip(), actor)
    posting.reverse_document(doc, reason.strip(), actor)
    _audit(doc.company, actor, "sales_document", doc.pk, Action.DEACTIVATED, new={"status": "void"},
           previous={"status": "issued"}, reason=reason.strip())
    return doc


# --------------------------------------------------------------------------- payments and credits

def open_receivables(customer, *, lock=False):
    qs = with_amounts(SalesDocument.objects.filter(
        customer=customer, kind__in=SalesDocument.RECEIVABLE, status=Status.ISSUED))
    if lock:
        qs = qs.select_for_update(of=("self",))
    return qs.filter(balance_due__gt=0).order_by(F("due_date").asc(nulls_last=True), "issue_date", "created_at")


@transaction.atomic
def record_payment(company, actor, *, customer, date, amount, mode, reference="", deposit_account=None, notes="",
                   allocations=None, auto_allocate=False, project_id=None, bank_ledger_account=None):
    """Record money received and apply it to invoices. Anything not applied stays as the customer's advance.
    `bank_ledger_account` is for bank-statement matching only: it posts straight to that ledger account instead of the
    usual deposit_account lookup, for a bank account with no Company Bank Account record of its own."""
    amount = amount_from(amount)
    project_id = resolve_project(company, project_id)
    if mode not in Payment.Mode.values:
        raise ValidationError({"mode": "Choose how the payment was made."})
    if deposit_account is not None and not deposit_account.is_active:
        raise ValidationError({"deposit_account": "This bank account is switched off."})
    open_docs = {str(d.pk): d for d in open_receivables(customer, lock=True)}  # keyed by text, as the API sends ids
    plan = []  # (document, amount)
    if auto_allocate:
        left = amount
        for doc in open_docs.values():
            if left <= 0:
                break
            use = min(left, balance_of(doc))
            plan.append((doc, use))
            left -= use
    else:
        for row in allocations or []:
            doc = open_docs.get(str(row.get("document")))
            if doc is None:
                raise ValidationError({"allocations": "Choose invoices of this customer that still have a balance."})
            use = amount_from(row.get("amount"), "Applied amount")
            if use > balance_of(doc):
                raise ValidationError({"allocations": f"{doc.number} has only {balance_of(doc)} left to pay."})
            plan.append((doc, use))
        if len({d.pk for d, _ in plan}) != len(plan):
            raise ValidationError({"allocations": "Each invoice can be listed once."})
        if sum((u for _, u in plan), ZERO) > amount:
            raise ValidationError({"allocations": "More is applied to invoices than the payment amount."})
    payment = Payment.objects.create(
        company=company, customer=customer, date=date, amount=amount, mode=mode, reference=(reference or "").strip()[:100],
        deposit_account=deposit_account, bank_ledger_account=bank_ledger_account, notes=(notes or "").strip()[:300],
        created_by=actor, project_id=project_id,
        reference_code=numbering.allocate_number(company, DocType.PAYMENT, actor=actor, on_date=date).reference_number)
    for doc, use in plan:
        PaymentAllocation.objects.create(payment=payment, document=doc, amount=use)
    post_payment(payment, actor)
    _audit(company, actor, "payment", payment.pk, Action.CREATED,
           new={"reference": payment.reference_code, "customer": customer.display_name, "amount": str(amount),
                "mode": mode, "applied_to": [d.number for d, _ in plan],
                "project": str(project_id) if project_id else ""})
    return payment


def post_payment(payment, actor):
    """Post a recorded payment to the books. Untagged payments use the standard posting; one tagged to a project posts the
    same lines with the project on each, so the project's cash view and its ledger agree."""
    if not payment.project_id:
        return posting.post_payment(payment, actor)
    company = payment.company
    applied = sum((a.amount for a in payment.allocations.all()), ZERO)
    advance = payment.amount - applied
    label = f"Payment {payment.reference_code}: {payment.customer.display_name}"
    party, tag = {"type": "customer", "id": payment.customer_id}, payment.project_id
    lines = [{"account": posting.money_account_for(company, payment), "debit": payment.amount, "memo": label, "project": tag}]
    if applied > 0:
        lines.append({"account": "receivable", "credit": applied, "memo": label, "party": party, "project": tag})
    if advance > 0:
        lines.append({"account": "customer_advances", "credit": advance, "memo": f"{label} (advance)", "party": party,
                      "project": tag})
    return ledger.post(company, date=payment.date, lines=lines, narration=label, source=JournalEntry.Source.PAYMENT,
                       source_id=str(payment.pk), reference=payment.reference_code, actor=actor)


def _approvals():
    """The approvals module's API when it is installed; None keeps sales working on its own."""
    try:
        from apps.approvals import api
        return api if hasattr(api, "submit") else None
    except ImportError:
        return None


def payment_approval_managed():
    """Whether voids held for approval must be decided in the shared approvals workflow."""
    return _approvals() is not None


def payment_title(payment):
    return f"Void payment {payment.reference_code} from {payment.customer.display_name}"


def _lock_payment(payment):
    return Payment.objects.select_for_update(of=("self",)).select_related("company", "customer").get(pk=payment.pk)


def _void_now(payment, reason, actor):
    payment.status, payment.void_reason = Payment.Status.VOID, reason.strip()[:300]
    payment.void_requested = False
    payment.save()
    posting.reverse_payment(payment, reason.strip(), actor)
    _audit(payment.company, actor, "payment", payment.pk, Action.DEACTIVATED, previous={"status": "recorded"},
           new={"status": "void"}, reason=reason.strip())
    return payment


@transaction.atomic
def void_payment(payment, reason, actor):
    """Void a payment received. Receipts themselves are never held (money has already arrived), but undoing one above an
    approval rule waits for approval: the payment stays recorded until the approver agrees."""
    payment = _lock_payment(payment)
    if payment.status == Payment.Status.VOID:
        raise ValidationError("This payment is already void.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give a reason for voiding this payment."})
    if payment.void_requested:
        raise ValidationError("Voiding this payment is already waiting for approval.")
    api = _approvals()
    if api is None or not api.requires_approval(payment.company, "payment", payment.amount):
        return _void_now(payment, reason, actor)
    payment.void_requested, payment.void_request_reason, payment.decision_note = True, reason.strip()[:300], ""
    payment.save()
    _audit(payment.company, actor, "payment", payment.pk, Action.UPDATED, previous={"void_requested": False},
           new={"void_requested": True}, reason=f"Void sent for approval: {reason.strip()}")
    api.submit(payment.company, kind="payment", source_id=str(payment.pk), title=payment_title(payment), amount=payment.amount,
               requested_by=actor, summary=f"Reason: {payment.void_request_reason}", url=f"/sales/payments/{payment.pk}")
    payment.refresh_from_db()
    return payment


@transaction.atomic
def approve_void(payment, actor, note=""):
    """The approval was granted: void the payment, reversing its entry in the books."""
    payment = _lock_payment(payment)
    if payment.status != Payment.Status.RECORDED or not payment.void_requested:
        raise ValidationError("Only a payment whose voiding is waiting for approval can be approved.")
    payment.decision_note = (note or "").strip()[:300]
    return _void_now(payment, payment.void_request_reason, actor)


@transaction.atomic
def reject_void(payment, actor, reason):
    payment = _lock_payment(payment)
    if not payment.void_requested:
        raise ValidationError("Voiding this payment is not waiting for approval.")
    if not (reason or "").strip():
        raise ValidationError({"change_reason": "Give the reason for rejecting this."})
    payment.void_requested, payment.decision_note = False, f"Void rejected: {reason.strip()}"[:300]
    payment.save()
    _audit(payment.company, actor, "payment", payment.pk, Action.UPDATED, previous={"void_requested": True},
           new={"void_requested": False}, reason=f"Void rejected: {reason.strip()}")
    return payment


def withdraw_void(payment, actor):
    """The approval request was withdrawn: the payment stays recorded."""
    payment = _lock_payment(payment)
    if payment.void_requested:
        payment.void_requested = False
        payment.save()
        _audit(payment.company, actor, "payment", payment.pk, Action.UPDATED, previous={"void_requested": True},
               new={"void_requested": False}, reason="Void request withdrawn")
    return payment


@transaction.atomic
def take_back_void(payment, actor):
    payment = _lock_payment(payment)
    if not payment.void_requested:
        raise ValidationError("Voiding this payment is not waiting for approval.")
    api = _approvals()
    if api is not None:
        api.cancel_for_source("payment", payment.pk, actor)
    return withdraw_void(payment, actor)


def resubmit_void(payment, actor):
    """A rejected or withdrawn void request was sent again from the Approvals module."""
    payment = _lock_payment(payment)
    if payment.status == Payment.Status.RECORDED and not payment.void_requested and payment.void_request_reason:
        payment.void_requested, payment.decision_note = True, ""
        payment.save()
        _audit(payment.company, actor, "payment", payment.pk, Action.UPDATED, previous={"void_requested": False},
               new={"void_requested": True}, reason="Void sent for approval again")
    return payment


@transaction.atomic
def apply_credit(credit_note, document, amount, actor):
    """Use part of an issued credit note against an invoice or debit note of the same customer."""
    credit_note = with_amounts(SalesDocument.objects.select_for_update(of=("self",)).filter(pk=credit_note.pk)).get()
    document = with_amounts(SalesDocument.objects.select_for_update(of=("self",)).filter(pk=document.pk)).get()
    amount = amount_from(amount)
    if credit_note.kind != Kind.CREDIT_NOTE or credit_note.status != Status.ISSUED:
        raise ValidationError({"credit_note": "Choose an issued credit note."})
    if not document.is_receivable or document.status != Status.ISSUED or document.customer_id != credit_note.customer_id:
        raise ValidationError({"document": "Choose an issued invoice or debit note of the same customer."})
    if amount > credit_left(credit_note):
        raise ValidationError({"amount": f"Only {credit_left(credit_note)} of this credit note is left."})
    if amount > balance_of(document):
        raise ValidationError({"amount": f"{document.number} has only {balance_of(document)} left to pay."})
    app = CreditApplication.objects.create(credit_note=credit_note, document=document, amount=amount, applied_by=actor)
    _audit(credit_note.company, actor, "sales_document", credit_note.pk, Action.UPDATED,
           new={"credit_applied": f"{amount} to {document.number}"}, reason="Credit applied")
    return app


# --------------------------------------------------------------------------- balances, ageing, statements

def customer_balances(company):
    """{customer id: outstanding, advance (unapplied receipts) and unused credit notes}."""
    out = {}

    def row(cid):
        return out.setdefault(cid, {"outstanding": ZERO, "advance": ZERO, "credit": ZERO})

    docs = with_amounts(SalesDocument.objects.filter(company=company, status=Status.ISSUED))
    for d in docs:
        if d.kind in SalesDocument.RECEIVABLE:
            row(d.customer_id)["outstanding"] += max(balance_of(d), ZERO)
        elif d.kind == Kind.CREDIT_NOTE:
            row(d.customer_id)["credit"] += max(credit_left(d), ZERO)
    allocated = {p: t for p, t in PaymentAllocation.objects.values_list("payment").annotate(t=Sum("amount"))}
    for p in Payment.objects.filter(company=company, status=Payment.Status.RECORDED):
        row(p.customer_id)["advance"] += max(money(p.amount - allocated.get(p.pk, ZERO)), ZERO)
    return out


def net_balance(b):
    return money(b["outstanding"] - b["advance"] - b["credit"])


def bucket_for(doc, today):
    days = (today - doc.due_date).days if doc.due_date else 0
    if days <= 0:
        return "current"
    if days <= 15:
        return "d1_15"
    if days <= 30:
        return "d16_30"
    if days <= 45:
        return "d31_45"
    return "d46_plus"


def receivables_report(company, today=None):
    """Open invoices and debit notes with their ageing, per customer and in total."""
    today = today or datetime.date.today()
    docs = with_amounts(SalesDocument.objects.filter(
        company=company, status=Status.ISSUED, kind__in=SalesDocument.RECEIVABLE)).filter(balance_due__gt=0).select_related("customer")
    totals = {key: ZERO for key, _, _ in AGING}
    per = {}
    for d in docs:
        balance, bucket = balance_of(d), bucket_for(d, today)
        totals[bucket] += balance
        c = per.setdefault(d.customer_id, {"customer_id": str(d.customer_id), "customer": d.customer.display_name,
                                           **{key: ZERO for key, _, _ in AGING}, "total": ZERO})
        c[bucket] += balance
        c["total"] += balance
    rows = sorted(per.values(), key=lambda r: -r["total"])
    return totals, rows


def statement(customer):
    """Every document and payment of the customer in date order with a running balance (what the customer owes)."""
    rows = []
    docs = with_amounts(SalesDocument.objects.filter(customer=customer, status=Status.ISSUED).exclude(kind=Kind.PROFORMA))
    for d in docs:
        sign = -1 if d.kind == Kind.CREDIT_NOTE else 1
        rows.append({"date": d.issue_date, "sort": (d.issue_date, 0, str(d.created_at)), "type": d.get_kind_display(),
                     "number": d.number, "debit": d.grand_total if sign > 0 else ZERO,
                     "credit": d.grand_total if sign < 0 else ZERO, "id": str(d.pk), "kind": d.kind})
    for p in Payment.objects.filter(customer=customer, status=Payment.Status.RECORDED):
        rows.append({"date": p.date, "sort": (p.date, 1, str(p.created_at)), "type": "Payment", "number": p.reference_code,
                     "debit": ZERO, "credit": p.amount, "id": str(p.pk), "kind": "payment"})
    rows.sort(key=lambda r: r["sort"])
    running = ZERO
    for r in rows:
        running += r["debit"] - r["credit"]
        r["balance"] = money(running)
        del r["sort"]
    return rows


def dashboard(company, today=None):
    from . import recurring, reminders  # these import this module
    today = today or datetime.date.today()
    totals, per_customer = receivables_report(company, today)
    outstanding = sum(totals.values(), ZERO)
    overdue = outstanding - totals["current"]

    months, (year, month) = [], (today.year, today.month)
    for _ in range(6):
        months.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    months.reverse()
    first = datetime.date(*months[0], 1)
    invoiced = {m: ZERO for m in months}
    for d in SalesDocument.objects.filter(company=company, status=Status.ISSUED, issue_date__gte=first).exclude(kind=Kind.PROFORMA):
        key = (d.issue_date.year, d.issue_date.month)
        if key in invoiced:
            invoiced[key] += -d.grand_total if d.kind == Kind.CREDIT_NOTE else d.grand_total
    received = {m: ZERO for m in months}
    for p in Payment.objects.filter(company=company, status=Payment.Status.RECORDED, date__gte=first):
        key = (p.date.year, p.date.month)
        if key in received:
            received[key] += p.amount

    docs = with_amounts(SalesDocument.objects.filter(company=company))
    status_counts = {k: 0 for k in ("draft", "unpaid", "overdue", "partial", "paid", "void")}
    for d in docs.filter(kind__in=SalesDocument.RECEIVABLE):
        key = display_status(d, today)[0]
        if key in status_counts:
            status_counts[key] += 1
        if key in ("overdue", "partial"):
            status_counts["unpaid"] += 1  # "unpaid" counts everything still owed, as the list filter does
    balances = customer_balances(company)
    this_month = SalesDocument.objects.filter(company=company, status=Status.ISSUED, kind=Kind.INVOICE,
                                              issue_date__year=today.year, issue_date__month=today.month)
    return {
        "today": today.isoformat(), "currency": company.default_currency,
        "receivables": {"outstanding": str(outstanding), "current": str(totals["current"]), "overdue": str(overdue),
                        "customers_owing": len(per_customer)},
        "aging": [{"key": key, "label": label, "amount": str(totals[key])} for key, label, _ in AGING],
        "monthly": [{"month": f"{y}-{m:02d}", "label": datetime.date(y, m, 1).strftime("%b"),
                     "invoiced": str(invoiced[(y, m)]), "received": str(received[(y, m)])} for y, m in months],
        "top_customers": [{"customer_id": r["customer_id"], "customer": r["customer"], "outstanding": str(r["total"])}
                          for r in per_customer[:5]],
        "status_counts": status_counts,
        "invoices_this_month": this_month.count(),
        "invoiced_this_month": str(this_month.aggregate(t=Coalesce(Sum("grand_total"), Value(ZERO)))["t"]),
        "customers": Customer.objects.filter(company=company, is_active=True).count(),
        "advances": str(sum((b["advance"] for b in balances.values()), ZERO)),
        "unused_credits": str(sum((b["credit"] for b in balances.values()), ZERO)),
        "reminders_due": reminders.due_count(company, today),
        "recurring_due": recurring.due_count(company, today),
    }


def form_options(company, kind=Kind.INVOICE):
    """What the document form needs: GST on or off, rates, default terms, payment terms."""
    resolved = company_service.resolve_for_document(kind, datetime.date.today(), company)
    ts, policy = resolved.tax_settings, resolved.policy
    gst_on = ts.gst_applicable and ts.gst_treatment == "regular"
    default_rate = quotations._default_rate(ts) if gst_on else None
    return {
        "gst_applied": gst_on,
        "gst_rates": [str(r) for r in settings.GST_CURRENT_RATES] if gst_on else [],
        "default_gst_rate": quotations.plain_number(default_rate) if default_rate is not None else "",
        "show_hsn_sac": ts.show_hsn_sac, "currency": company.default_currency,
        "default_terms": getattr(policy, "invoice_terms", "") if policy else "",
        "payment_terms": getattr(policy, "payment_terms", "") if policy else "",
        "payment_terms_days": getattr(policy, "payment_terms_days", None) if policy else None,
        "bank_will_print": resolved.bank_account is not None,
    }
