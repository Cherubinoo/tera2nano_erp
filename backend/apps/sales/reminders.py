"""Payment reminders for unpaid invoices.

Rules say when a reminder falls due, as days from the invoice's due date (-3 = three days before, 0 = on the day, 7 = a week
after), and carry editable wording with `{placeholders}`. `due_reminders` works out, from the books as they stand today,
which invoice needs which reminder. It writes nothing, so asking twice always gives the same answer.

An invoice is reminded at most once per rule: sending or skipping records a `PaymentReminder` row, and the database allows one
row per (invoice, rule offset). When several rules have come due for the same invoice (for example it was never chased and
is now 40 days late) only the latest one is offered, so a customer is not sent five reminders at once.

"Send" records the reminder against the invoice and, when email is set up on the server (see apps.emails), emails the text
to the customer. The record is kept whatever happens to the email: a failed email is logged as failed (with the reason) in
the Sent emails log, where it can be sent again. Without an email server the person copies the text or opens it in their
own mail program, as before. With "Email payment reminders automatically" switched on, a daily job sends the reminders that
are due to customers who have an email address (apps.emails.jobs).
"""
import datetime
import re

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import quotations

from . import services
from .models import PaymentReminder, ReminderRule, SalesDocument

Action = CompanyProfileAuditEvent.Action
Status = SalesDocument.Status

PLACEHOLDERS = {
    "customer_name": "The customer's name",
    "invoice_no": "The invoice number",
    "invoice_date": "The invoice date",
    "invoice_total": "The invoice total",
    "amount_due": "What is still unpaid on it",
    "due_date": "The due date",
    "days_overdue": "Days past the due date (0 before it)",
    "days_until_due": "Days left until the due date (0 once due)",
    "company_name": "Your company's name",
}
_TOKEN = re.compile(r"\{([^{}\s]*)\}")

DEFAULT_RULES = [
    (-3, "Reminder: invoice {invoice_no} is due on {due_date}",
     "Dear {customer_name},\n\nThis is a friendly reminder that invoice {invoice_no} for {amount_due} falls due on {due_date}.\n\n"
     "If you have already arranged payment, please ignore this note. Otherwise we would be grateful if you could pay on or before the "
     "due date.\n\nRegards,\n{company_name}"),
    (0, "Invoice {invoice_no} is due today",
     "Dear {customer_name},\n\nInvoice {invoice_no} for {amount_due} is due today ({due_date}).\n\n"
     "Please arrange payment at your earliest convenience and let us know if you need anything from us.\n\nRegards,\n{company_name}"),
    (7, "Overdue: invoice {invoice_no} ({days_overdue} days past due)",
     "Dear {customer_name},\n\nOur records show that invoice {invoice_no} for {amount_due}, due on {due_date}, is now {days_overdue} days "
     "overdue.\n\nPlease arrange payment or tell us when we can expect it.\n\nRegards,\n{company_name}"),
    (15, "Second reminder: invoice {invoice_no} is {days_overdue} days overdue",
     "Dear {customer_name},\n\nWe wrote to you earlier about invoice {invoice_no}. {amount_due} remains unpaid and is {days_overdue} days "
     "past its due date of {due_date}.\n\nKindly settle it now, or contact us if there is a problem with the invoice.\n\n"
     "Regards,\n{company_name}"),
    (30, "Final reminder: invoice {invoice_no} is {days_overdue} days overdue",
     "Dear {customer_name},\n\nInvoice {invoice_no} for {amount_due} is {days_overdue} days past its due date of {due_date} and we have "
     "not yet received payment.\n\nPlease pay it immediately. If we do not hear from you we may have to pause further work or supplies.\n\n"
     "Regards,\n{company_name}"),
]


def offset_label(n):
    if n == 0:
        return "On the due date"
    unit = "day" if abs(n) == 1 else "days"
    return f"{-n} {unit} before the due date" if n < 0 else f"{n} {unit} after the due date"


# --------------------------------------------------------------------------- rules

def _audit(company, actor, entity, pk, action, **kw):
    services._audit(company, actor, entity, pk, action, **kw)


def seed_rules(company):
    """The standard five rules, the first time a company opens its reminder settings."""
    if ReminderRule.objects.filter(company=company).exists():
        return
    with transaction.atomic():
        for offset, subject, body in DEFAULT_RULES:
            ReminderRule.objects.get_or_create(company=company, days_offset=offset,
                                               defaults={"subject_template": subject, "body_template": body})


def rules_for(company):
    seed_rules(company)
    return ReminderRule.objects.filter(company=company).order_by("days_offset")


def check_template(text, field):
    unknown = sorted({m for m in _TOKEN.findall(text) if m not in PLACEHOLDERS})
    if unknown:
        raise ValidationError({field: "Unknown placeholder %s. Use: %s." % (
            ", ".join("{%s}" % u for u in unknown), ", ".join("{%s}" % k for k in PLACEHOLDERS))})


def _rule_values(company, data, current):
    def get(key, default=""):
        return data[key] if key in data else (getattr(current, key) if current is not None else default)

    try:
        offset = int(str(get("days_offset", "")).strip())
    except ValueError:
        raise ValidationError({"days_offset": "Enter a whole number of days (negative = before the due date)."})
    if not -60 <= offset <= 365:
        raise ValidationError({"days_offset": "Use a number from -60 to 365."})
    subject, body = get("subject_template"), get("body_template")
    if not isinstance(subject, str) or not subject.strip():
        raise ValidationError({"subject_template": "Write the subject."})
    if not isinstance(body, str) or not body.strip():
        raise ValidationError({"body_template": "Write the message."})
    subject, body = subject.strip(), body.strip()
    if len(subject) > 200:
        raise ValidationError({"subject_template": "Use at most 200 characters."})
    if len(body) > 4000:
        raise ValidationError({"body_template": "Use at most 4000 characters."})
    check_template(subject, "subject_template")
    check_template(body, "body_template")
    active = get("is_active", True)
    if not isinstance(active, bool):
        raise ValidationError({"is_active": "Send true or false."})
    clash = ReminderRule.objects.filter(company=company, days_offset=offset)
    if current is not None:
        clash = clash.exclude(pk=current.pk)
    if clash.exists():
        raise ValidationError({"days_offset": "There is already a rule for %s." % offset_label(offset).lower()})
    return {"days_offset": offset, "subject_template": subject, "body_template": body, "is_active": active}


def _rule_snapshot(r):
    return {"when": offset_label(r.days_offset), "days_offset": r.days_offset, "subject": r.subject_template,
            "body": r.body_template, "active": r.is_active}


@transaction.atomic
def create_rule(company, actor, data):
    seed_rules(company)
    rule = ReminderRule.objects.create(company=company, **_rule_values(company, data, None))
    _audit(company, actor, "reminder_rule", rule.pk, Action.CREATED, new=_rule_snapshot(rule))
    return rule


@transaction.atomic
def update_rule(rule, actor, data):
    before = _rule_snapshot(rule)
    for key, value in _rule_values(rule.company, data, rule).items():
        setattr(rule, key, value)
    rule.save()
    after = _rule_snapshot(rule)
    action = Action.UPDATED
    if before["active"] != after["active"] and {k: v for k, v in before.items() if k != "active"} == {k: v for k, v in after.items() if k != "active"}:
        action = Action.ACTIVATED if rule.is_active else Action.DEACTIVATED
    _audit(rule.company, actor, "reminder_rule", rule.pk, action, previous=before, new=after)
    return rule


# --------------------------------------------------------------------------- what is due

def _open_invoices(company):
    qs = services.with_amounts(SalesDocument.objects.filter(
        company=company, kind__in=SalesDocument.RECEIVABLE, status=Status.ISSUED, due_date__isnull=False))
    return qs.filter(balance_due__gt=0).select_related("customer", "company")


def _top_rule(doc, rules, today):
    """The latest active rule whose date has come for this invoice, or None."""
    days = (today - doc.due_date).days
    reached = [r for r in rules if r.days_offset <= days]
    return max(reached, key=lambda r: r.days_offset) if reached else None


def due_reminders(company, today=None):
    """[(invoice, rule)] needing a reminder today, oldest due date first. Paid, void and draft invoices never appear, nor an
    invoice that already got (or had skipped) that rule's reminder. Writes nothing."""
    today = today or datetime.date.today()
    rules = [r for r in rules_for(company) if r.is_active]  # the standard rules appear the first time anyone asks
    if not rules:
        return []
    handled = set(PaymentReminder.objects.filter(company=company).values_list("document_id", "days_offset"))
    out = []
    for doc in _open_invoices(company):
        rule = _top_rule(doc, rules, today)
        if rule is not None and (doc.pk, rule.days_offset) not in handled:
            out.append((doc, rule))
    out.sort(key=lambda pair: (pair[0].due_date, pair[0].number))
    return out


def due_count(company, today=None):
    return len(due_reminders(company, today))


def render(doc, rule, today=None):
    """(subject, body) with the placeholders filled in for this invoice."""
    today = today or datetime.date.today()
    cur = doc.currency or "INR"
    balance = max(services.balance_of(doc), services.ZERO)
    days = (today - doc.due_date).days if doc.due_date else 0
    values = {
        "customer_name": doc.customer.display_name, "invoice_no": doc.number,
        "invoice_date": doc.issue_date.strftime("%d %b %Y"),
        "invoice_total": f"{cur} {quotations.money(doc.grand_total, cur)}",
        "amount_due": f"{cur} {quotations.money(balance, cur)}",
        "due_date": doc.due_date.strftime("%d %b %Y") if doc.due_date else "",
        "days_overdue": str(max(days, 0)), "days_until_due": str(max(-days, 0)),
        "company_name": doc.company.display_name,
    }

    def fill(text):
        return _TOKEN.sub(lambda m: values.get(m.group(1), m.group(0)), text)

    return fill(rule.subject_template), fill(rule.body_template)


# --------------------------------------------------------------------------- send and skip

def _record(company, actor, document_id, days_offset, status, note, today, *, subject=None, body=None, to_email=None):
    try:
        doc = _open_invoices(company).select_for_update(of=("self",)).filter(pk=document_id).first()
    except (ValueError, ValidationError):
        doc = None
    if doc is None:
        raise ValidationError("This invoice is not waiting for a reminder (it may be paid, void or not yet issued).")
    rules = [r for r in rules_for(company) if r.is_active]
    rule = _top_rule(doc, rules, today)
    if rule is None or rule.days_offset != days_offset:
        raise ValidationError("This reminder is no longer due. Refresh the list.")
    if PaymentReminder.objects.filter(document=doc, days_offset=days_offset).exists():
        raise ValidationError("This reminder has already been sent or skipped.")
    if status == PaymentReminder.Status.SENT:
        rendered = render(doc, rule, today)
        subject = subject if subject is not None else rendered[0]
        body = body if body is not None else rendered[1]
    else:
        subject, body = "", ""
    try:
        with transaction.atomic():
            row = PaymentReminder.objects.create(
                company=company, document=doc, rule=rule, days_offset=rule.days_offset, status=status, subject=subject,
                body=body, to_email=(doc.customer.email if to_email is None else to_email)[:254] if status == PaymentReminder.Status.SENT else "",
                amount_due=max(services.balance_of(doc), services.ZERO), due_date=doc.due_date,
                days_overdue=max((today - doc.due_date).days, 0), note=(note or "").strip()[:300], created_by=actor)
    except IntegrityError:
        raise ValidationError("This reminder has already been sent or skipped.")
    _audit(company, actor, "payment_reminder", row.pk, Action.CREATED,
           new={"invoice": doc.number, "customer": doc.customer.display_name, "rule": offset_label(rule.days_offset),
                "status": status, "amount_due": str(row.amount_due)},
           reason=row.note)
    return row


def _mail():
    """The email module when it is installed and a mail server is set up; otherwise None (reminders are recorded only)."""
    if not django_apps.is_installed("apps.emails"):
        return None
    from apps.emails import services as mail

    return mail if mail.is_configured() else None


def _edited_text(subject, body):
    if subject is not None:
        if not isinstance(subject, str) or not subject.strip():
            raise ValidationError({"subject": "Write the subject."})
        subject = " ".join(subject.split())
        if len(subject) > 200:
            raise ValidationError({"subject": "Use at most 200 characters."})
    if body is not None:
        if not isinstance(body, str) or not body.strip():
            raise ValidationError({"body": "Write the message."})
        body = body.replace("\r\n", "\n").strip()
        if len(body) > 4000:
            raise ValidationError({"body": "Use at most 4000 characters."})
    return subject, body


def send(company, actor, document_id, days_offset, today=None, *, to=None, cc=None, subject=None, body=None, automatic=False):
    """Record the reminder as sent and, when email is set up, email it. `to`, `cc`, `subject` and `body` are what the person
    checked in the send dialog (by default: the customer's address and the rule's text). The returned row carries `email`:
    the log entry of the email (None when nothing was emailed). A failed email never undoes the record."""
    mail = _mail()
    subject, body = _edited_text(subject, body)
    recipients = cc_list = None
    if mail is not None:
        recipients = mail.clean_addresses(to, "to") if to is not None else None
        cc_list = mail.clean_addresses(cc, "cc")
    with transaction.atomic():
        row = _record(company, actor, document_id, days_offset, PaymentReminder.Status.SENT, "", today or datetime.date.today(),
                      subject=subject, body=body, to_email=", ".join(recipients) if recipients is not None else None)
    row.email = None
    if mail is not None:
        recipients = recipients if recipients is not None else ([row.to_email] if row.to_email else [])
        if recipients:
            signature = mail.settings_for(company).signature.strip()
            doc = row.document
            row.email = mail.send(company, actor, kind="payment_reminder", to=recipients, cc=cc_list or [], subject=row.subject,
                                  body=f"{row.body}\n\n{signature}" if signature else row.body, object_id=row.pk,
                                  object_label=f"{offset_label(row.days_offset)} · {doc.number} · {doc.customer.display_name}",
                                  url=f"/sales/documents/{doc.pk}", automatic=automatic)
    return row


def email_outcomes(reminder_ids):
    """{reminder id: {status, status_label, error}} for reminders that were emailed (the latest attempt wins)."""
    if not django_apps.is_installed("apps.emails") or not reminder_ids:
        return {}
    from apps.emails.models import EmailMessage

    out = {}
    for m in EmailMessage.objects.filter(kind="payment_reminder", object_id__in=[str(i) for i in reminder_ids]).order_by("created_at"):
        out[m.object_id] = {"status": m.status, "status_label": m.get_status_display(), "error": m.error, "id": str(m.pk)}
    return out


@transaction.atomic
def skip(company, actor, document_id, days_offset, note="", today=None):
    return _record(company, actor, document_id, days_offset, PaymentReminder.Status.SKIPPED, note, today or datetime.date.today())
