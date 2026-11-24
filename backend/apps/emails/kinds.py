"""What can be emailed from a record's page, who may do it, and the message the send dialog starts with.

Each kind knows how to find its record, who may send it (the same permission as the underlying action: sales.manage for
invoices, purchases.manage for purchase orders, documents.issue for quotations and letters, hr.payroll for payslips), the
prefilled recipients, subject and message, and the PDF to attach. Invitation links and payment reminders have their own
flows (apps.accounts.invitations, apps.sales.reminders) because they carry extra rules; they still log through `services`.

Add a kind: subclass `Kind`, then `register(MyKind())`.
"""
import datetime

from django.core.exceptions import ValidationError
from rest_framework.exceptions import NotFound, PermissionDenied

from . import services

KINDS = {}


def register(kind):
    KINDS[kind.key] = kind
    return kind


def _get(model, pk, label, **filters):
    try:
        obj = model.objects.filter(pk=pk, **filters).first()
    except (ValueError, ValidationError):
        obj = None
    if obj is None:
        raise NotFound(f"No such {label}.")
    return obj


def _money(value, currency):
    from apps.company.services import quotations

    return f"{currency} {quotations.money(value, currency)}"


def _pdf(field):
    """A function that reads a stored PDF when the message is sent (a missing file is then logged as a failure)."""
    def read():
        with field.open("rb") as handle:
            return handle.read()
    return read


def _filename(text):
    return f"{(text or 'document').replace('/', '-')}.pdf"


class Kind:
    key = ""
    label = ""
    permission = ""

    def load(self, company, object_id):
        raise NotImplementedError

    def allowed(self, user, obj):
        return user.has_code(self.permission)

    def check_ready(self, obj):
        """Raise ValidationError when the record cannot be emailed yet (a draft with no PDF)."""

    def recipients(self, company, obj):
        return [], []

    def subject(self, company, obj):
        raise NotImplementedError

    def message(self, company, obj):
        """The prefilled message without the sign-off."""
        raise NotImplementedError

    def attachments(self, obj, actor):
        return []

    def object_label(self, obj):
        return str(obj)

    def url(self, obj):
        return ""


# --------------------------------------------------------------------------- sales invoices and notes

class SalesDocumentKind(Kind):
    key, label, permission = "sales_document", "Invoice or note", "sales.manage"

    def load(self, company, object_id):
        from apps.sales.models import SalesDocument

        doc = _get(SalesDocument, object_id, "invoice or note", company=company)
        return SalesDocument.objects.select_related("customer", "issued_document", "company").get(pk=doc.pk)

    def check_ready(self, doc):
        if doc.status == "draft" or not doc.issued_document_id:
            raise ValidationError("Issue the document first: a draft has no number or PDF to send.")
        if doc.status == "void":
            raise ValidationError("This document is void and cannot be emailed.")

    def recipients(self, company, doc):
        return ([doc.customer.email] if doc.customer.email else []), []

    def subject(self, company, doc):
        return f"{doc.get_kind_display()} {doc.number} from {services.sender_name(company)}"

    def message(self, company, doc):
        from apps.sales import services as sales

        kind = doc.get_kind_display().lower()
        text = (f"Dear {doc.customer.display_name},\n\nPlease find attached our {kind} {doc.number} dated "
                f"{doc.issue_date.strftime('%d %b %Y')}, for {_money(doc.grand_total, doc.currency)}.")
        if doc.kind in ("invoice", "debit_note") and doc.due_date:
            balance = sales.balance_of(sales.with_amounts(type(doc).objects.filter(pk=doc.pk)).get())
            if balance > 0:
                text += f"\n\nThe amount of {_money(balance, doc.currency)} is due by {doc.due_date.strftime('%d %b %Y')}."
        return text + "\n\nPlease let us know if you have any questions."

    def attachments(self, doc, actor):
        return [(_filename(doc.number), _pdf(doc.issued_document.file), "application/pdf")]

    def object_label(self, doc):
        return f"{doc.get_kind_display()} {doc.number or '(draft)'} · {doc.customer.display_name}"

    def url(self, doc):
        return f"/sales/documents/{doc.pk}"


# --------------------------------------------------------------------------- quotations and letters

class IssuedDocumentKind(Kind):
    """Quotations and letters issued from the Documents and Quotations pages. Money documents (invoices, notes), purchase
    orders and payslips have their own kinds with stricter permissions, so they are not sent through this one."""

    key, label, permission = "issued_document", "Quotation or letter", "documents.issue"
    TYPES = ("quotation", "business_letter", "offer_letter", "appointment_letter", "experience_letter", "handover_letter",
             "completion_certificate", "meeting_minutes", "operation_document", "circular")

    def load(self, company, object_id):
        from apps.company.models import IssuedDocument

        doc = _get(IssuedDocument, object_id, "document", company=company)
        if doc.document_type not in self.TYPES:
            raise ValidationError("This kind of document is emailed from its own page (invoices, purchase orders, payslips).")
        return doc

    def check_ready(self, doc):
        if doc.status == "void":
            raise ValidationError("This document is void and cannot be emailed.")

    def _customer(self, doc):
        return (((doc.snapshot or {}).get("quotation") or {}).get("customer") or {}).get("name", "")

    def recipients(self, company, doc):
        if doc.document_type != "quotation":
            return [], []
        email = ""
        if doc.source_module == "crm" and doc.source_id:
            try:
                from apps.crm.models import Deal

                deal = Deal.objects.filter(pk=doc.source_id, company=company).select_related("contact", "account").first()
            except (ValueError, ValidationError, ImportError):
                deal = None
            if deal is not None:
                email = (deal.contact.email if deal.contact_id else "") or deal.account.email
        name = self._customer(doc)
        if not email and name:
            from apps.sales.models import Customer

            found = list(Customer.objects.filter(company=company, display_name__iexact=name).exclude(email="")
                         .values_list("email", flat=True)[:2])
            email = found[0] if len(found) == 1 else ""
        return ([email] if email else []), []

    def _what(self, doc):
        return "quotation" if doc.document_type == "quotation" else (doc.title or doc.get_document_type_display()).lower()

    def subject(self, company, doc):
        what = "Quotation" if doc.document_type == "quotation" else (doc.title or doc.get_document_type_display())
        return f"{what} {doc.reference_number} from {services.sender_name(company)}".replace("  ", " ")

    def message(self, company, doc):
        name = self._customer(doc)
        text = f"Dear {name or 'Sir or Madam'},\n\nPlease find attached our {self._what(doc)}"
        text += f" {doc.reference_number}" if doc.reference_number else ""
        text += f" dated {doc.issue_date.strftime('%d %b %Y')}."
        if doc.document_type == "quotation":
            quote = (doc.snapshot or {}).get("quotation") or {}
            valid = quote.get("valid_until")
            if valid:
                try:
                    text += f" It is valid until {datetime.date.fromisoformat(str(valid)[:10]).strftime('%d %b %Y')}."
                except ValueError:
                    pass
            text += "\n\nWe would be glad to answer any questions and look forward to hearing from you."
        return text

    def attachments(self, doc, actor):
        return [(_filename(doc.reference_number or doc.title), _pdf(doc.file), "application/pdf")]

    def object_label(self, doc):
        label = f"{doc.get_document_type_display()} {doc.reference_number}".strip()
        name = self._customer(doc)
        return f"{label} · {name}" if name else label

    def url(self, doc):
        return "/company/quotations" if doc.document_type == "quotation" else "/company/letters"


# --------------------------------------------------------------------------- purchase orders

class PurchaseOrderKind(Kind):
    key, label, permission = "purchase_order", "Purchase order", "purchases.manage"
    SENDABLE = ("issued", "partially_received", "received", "closed")

    def load(self, company, object_id):
        from apps.purchases.models import PurchaseOrder

        order = _get(PurchaseOrder, object_id, "purchase order", company=company)
        return PurchaseOrder.objects.select_related("vendor", "issued_document").get(pk=order.pk)

    def check_ready(self, order):
        if order.status not in self.SENDABLE or not order.issued_document_id:
            raise ValidationError("Issue the purchase order first: only an issued order has a number and a PDF to send.")

    def recipients(self, company, order):
        return ([order.vendor.email] if order.vendor.email else []), []

    def subject(self, company, order):
        return f"Purchase order {order.number} from {services.sender_name(company)}"

    def message(self, company, order):
        cur = (order.record or {}).get("currency") or "INR"
        text = (f"Dear {order.vendor.contact_person or order.vendor.name},\n\nPlease find attached our purchase order "
                f"{order.number} dated {order.order_date.strftime('%d %b %Y')}, for {_money(order.grand_total, cur)}.")
        if order.expected_delivery:
            text += f" Please deliver by {order.expected_delivery.strftime('%d %b %Y')}."
        return text + "\n\nKindly confirm that you have received this order."

    def attachments(self, order, actor):
        return [(_filename(order.number), _pdf(order.issued_document.file), "application/pdf")]

    def object_label(self, order):
        return f"Purchase order {order.number or '(draft)'} · {order.vendor.name}"

    def url(self, order):
        return f"/purchases/orders/{order.pk}"


# --------------------------------------------------------------------------- payslips

class PayslipKind(Kind):
    key, label, permission = "payslip", "Payslip", "hr.payroll"

    def load(self, company, object_id):
        from apps.hr.models import Payslip

        slip = _get(Payslip, object_id, "payslip", run__company=company)
        return Payslip.objects.select_related("run", "employee", "issued_document").get(pk=slip.pk)

    def check_ready(self, slip):
        from apps.hr.services import payroll

        if slip.run.status not in payroll.FINAL:
            raise ValidationError("The payslip can be emailed once the payroll run is approved.")

    def recipients(self, company, slip):
        email = slip.employee.work_email or slip.employee.personal_email
        return ([email] if email else []), []

    def subject(self, company, slip):
        return f"Your payslip for {slip.month.strftime('%B %Y')}"

    def message(self, company, slip):
        name = slip.employee.first_name or (slip.who or {}).get("name") or "colleague"
        return (f"Dear {name},\n\nPlease find attached your payslip for {slip.month.strftime('%B %Y')}.\n\n"
                "If anything on it looks wrong, please tell the HR team.")

    def attachments(self, slip, actor):
        from apps.hr.services import payroll

        doc = payroll.payslip_document(slip, actor)  # numbered the first time it is needed, then reused
        code = (slip.who or {}).get("code") or "employee"
        return [(f"payslip-{code}-{slip.month.strftime('%Y-%m')}.pdf", _pdf(doc.file), "application/pdf")]

    def object_label(self, slip):
        name = (slip.who or {}).get("name") or f"{slip.employee.first_name} {slip.employee.last_name}".strip()
        return f"Payslip {slip.month.strftime('%B %Y')} · {name}"

    def url(self, slip):
        return f"/hr/payroll/runs/{slip.run_id}"


# --------------------------------------------------------------------------- payment reminders (log and resend only)

class PaymentReminderKind(Kind):
    """Reminders are sent from the Reminders page (apps.sales.reminders). This kind lets a failed reminder email be sent
    again from the log and shows who may do it."""

    key, label, permission = "payment_reminder", "Payment reminder", "sales.manage"

    def load(self, company, object_id):
        from apps.sales.models import PaymentReminder

        return _get(PaymentReminder, object_id, "payment reminder", company=company)


for _kind in (SalesDocumentKind(), IssuedDocumentKind(), PurchaseOrderKind(), PayslipKind(), PaymentReminderKind()):
    register(_kind)

DIALOG_KINDS = ("sales_document", "issued_document", "purchase_order", "payslip")


# --------------------------------------------------------------------------- the send dialog

def _kind(key):
    kind = KINDS.get(key) if key in DIALOG_KINDS else None
    if kind is None:
        raise ValidationError({"kind": f"Choose one of: {', '.join(DIALOG_KINDS)}."})
    return kind


def _prepare(company, user, key, object_id):
    kind = _kind(key)
    if not user.has_code(kind.permission):
        raise PermissionDenied("Your role does not allow sending this by email.")
    obj = kind.load(company, object_id)
    if not kind.allowed(user, obj):
        raise PermissionDenied("Your role does not allow sending this by email.")
    kind.check_ready(obj)
    return kind, obj


def history(company, key, object_id):
    return [services.message_dict(m) for m in services.for_object(company, key, object_id)]


def draft(company, user, key, object_id):
    """What the send dialog opens with: recipients, subject, message (with the company's sign-off) and the attachment."""
    kind, obj = _prepare(company, user, key, object_id)
    to, cc = kind.recipients(company, obj)
    names = [a[0] for a in kind.attachments(obj, user)] if key != "payslip" else [
        f"payslip-{(obj.who or {}).get('code') or 'employee'}-{obj.month.strftime('%Y-%m')}.pdf"]
    return {"kind": key, "object_id": str(obj.pk), "label": kind.object_label(obj), "url": kind.url(obj), "to": to, "cc": cc,
            "subject": kind.subject(company, obj), "body": f"{kind.message(company, obj)}\n\n{services.sign_off(company)}",
            "attachments": names, "history": history(company, key, obj.pk), "server": services.server_status(),
            "from_address": services.from_address(company)}


def send(company, user, key, object_id, data):
    """Send what the person checked in the dialog. Returns the log row (it may say "failed"; nothing is undone)."""
    kind, obj = _prepare(company, user, key, object_id)
    return services.send(company, user, kind=key, to=data.get("to"), cc=data.get("cc"), subject=data.get("subject"),
                         body=data.get("body"), attachments=kind.attachments(obj, user), object_id=obj.pk,
                         object_label=kind.object_label(obj), url=kind.url(obj))


def attachments_again(row, actor):
    """The attachments for sending a logged message again, fetched from the record it was about."""
    kind = KINDS.get(row.kind)
    if kind is None:
        return []
    try:
        obj = kind.load(row.company, row.object_id)
    except NotFound:
        raise ValidationError("The record this message was about no longer exists, so its PDF cannot be attached.")
    return kind.attachments(obj, actor)
