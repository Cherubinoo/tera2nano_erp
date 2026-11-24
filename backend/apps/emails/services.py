"""Sending email and keeping the log.

`send()` is the one way the app sends a message. It never raises because the mail server failed: the failure is written to
the log (status "failed" with the reason) and returned, so the business action that asked for the email (issuing an invoice,
recording a reminder) is never undone by a mail problem. It does raise ValidationError for bad input (no recipient, a
malformed address, an empty subject), before anything is sent.

When EMAIL_HOST is not set the message still goes through Django's mail system, which writes it to a folder on the server
(see settings.EMAIL_FILE_PATH); its status is then "saved" and the app says that email is not set up.
"""
import logging
import re
import smtplib
import socket
from email.utils import formataddr

from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q

from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit

from .models import EmailMessage, EmailSettings

logger = logging.getLogger(__name__)
Status = EmailMessage.Status
Action = CompanyProfileAuditEvent.Action

NOT_SET_UP = "Email is not set up on the server; messages are saved but not delivered."
MAX_RECIPIENTS = 20
MAX_SUBJECT = 300
MAX_BODY = 20000
HIDDEN = "[hidden]"
_SPLIT = re.compile(r"[,;\s]+")


# --------------------------------------------------------------------------- server and company settings

def is_configured():
    """Whether a real mail server is set up (EMAIL_HOST). Without one, messages are saved to a folder, not delivered."""
    return bool(getattr(settings, "EMAIL_HOST", ""))


def server_status():
    """What the app may show about the mail server. Never includes the password or the user name."""
    configured = is_configured()
    return {
        "configured": configured,
        "host": settings.EMAIL_HOST if configured else "",
        "port": settings.EMAIL_PORT if configured else None,
        "security": ("SSL" if settings.EMAIL_USE_SSL else "TLS" if settings.EMAIL_USE_TLS else "none") if configured else "",
        "from_email": settings.DEFAULT_FROM_EMAIL,
        "message": "" if configured else NOT_SET_UP,
    }


def settings_for(company):
    row, _ = EmailSettings.objects.get_or_create(company=company)
    return row


def sender_name(company, conf=None):
    conf = conf or settings_for(company)
    return conf.sender_name or company.display_name or company.legal_name or ""


def from_address(company, conf=None):
    name = sender_name(company, conf)
    return formataddr((name, settings.DEFAULT_FROM_EMAIL)) if name else settings.DEFAULT_FROM_EMAIL


def sign_off(company, conf=None):
    """The end of a prefilled message: the company's signature, or "Regards," and the sender name."""
    conf = conf or settings_for(company)
    return conf.signature.strip() if conf.signature.strip() else f"Regards,\n{sender_name(company, conf)}"


def settings_dict(company, conf=None):
    conf = conf or settings_for(company)
    return {"sender_name": conf.sender_name, "reply_to": conf.reply_to, "signature": conf.signature,
            "auto_reminders": conf.auto_reminders, "effective_sender_name": sender_name(company, conf),
            "from_address": from_address(company, conf), "updated_at": conf.updated_at, "server": server_status()}


SETTINGS_FIELDS = ("sender_name", "reply_to", "signature", "auto_reminders")


def update_settings(company, actor, data, *, allowed=SETTINGS_FIELDS):
    """Change the email settings. `allowed` limits which fields this person may change (others are refused)."""
    conf = settings_for(company)
    unknown = [k for k in data if k in SETTINGS_FIELDS and k not in allowed]
    if unknown:
        raise ValidationError({unknown[0]: "Your role may not change this setting."})
    before = {k: getattr(conf, k) for k in SETTINGS_FIELDS}
    if "sender_name" in data:
        name = data["sender_name"]
        if not isinstance(name, str):
            raise ValidationError({"sender_name": "Enter text."})
        name = " ".join(name.split())
        if len(name) > 150:
            raise ValidationError({"sender_name": "Use at most 150 characters."})
        conf.sender_name = name
    if "reply_to" in data:
        reply = data["reply_to"]
        if not isinstance(reply, str):
            raise ValidationError({"reply_to": "Enter an email address."})
        reply = reply.strip()
        if reply:
            try:
                validate_email(reply)
            except ValidationError:
                raise ValidationError({"reply_to": "Enter a valid email address, for example accounts@example.com."})
        conf.reply_to = reply
    if "signature" in data:
        sig = data["signature"]
        if not isinstance(sig, str):
            raise ValidationError({"signature": "Enter text."})
        if len(sig) > 2000:
            raise ValidationError({"signature": "Use at most 2000 characters."})
        conf.signature = sig.strip()
    if "auto_reminders" in data:
        if not isinstance(data["auto_reminders"], bool):
            raise ValidationError({"auto_reminders": "Send true or false."})
        conf.auto_reminders = data["auto_reminders"]
    after = {k: getattr(conf, k) for k in SETTINGS_FIELDS}
    if after != before:
        conf.updated_by = actor if getattr(actor, "is_authenticated", False) else None
        conf.save()
        audit.log(company, actor, "email_settings", conf.pk, Action.UPDATED, previous=before, new=after)
    return conf


# --------------------------------------------------------------------------- checking what is sent

def clean_addresses(value, field, *, required=False):
    """A list of valid, distinct addresses from a list or from text separated by commas, semicolons or spaces."""
    if value in (None, ""):
        items = []
    elif isinstance(value, str):
        items = [v for v in _SPLIT.split(value) if v]
    elif isinstance(value, (list, tuple)):
        items = []
        for v in value:
            if not isinstance(v, str):
                raise ValidationError({field: "Enter email addresses as text."})
            items += [x for x in _SPLIT.split(v) if x]
    else:
        raise ValidationError({field: "Enter email addresses as text."})
    out = []
    for item in items:
        try:
            validate_email(item)
        except ValidationError:
            raise ValidationError({field: f"\"{item[:80]}\" is not a valid email address."})
        if item.lower() not in [o.lower() for o in out]:
            out.append(item)
    if required and not out:
        raise ValidationError({field: "Enter at least one email address to send to."})
    if len(out) > MAX_RECIPIENTS:
        raise ValidationError({field: f"Send to at most {MAX_RECIPIENTS} addresses at a time."})
    return out


def clean_subject(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError({"subject": "Write a subject."})
    value = " ".join(value.split())  # no line breaks in a subject line
    if len(value) > MAX_SUBJECT:
        raise ValidationError({"subject": f"Use at most {MAX_SUBJECT} characters."})
    return value


def clean_body(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError({"body": "Write the message."})
    value = value.replace("\r\n", "\n").strip()
    if len(value) > MAX_BODY:
        raise ValidationError({"body": f"Use at most {MAX_BODY} characters."})
    return value


def describe_failure(exc):
    """A plain-English reason for a failed send, for the log and the person who clicked Send."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "The mail server refused the sign-in. Check EMAIL_HOST_USER and EMAIL_HOST_PASSWORD on the server."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        refused = ", ".join(sorted(getattr(exc, "recipients", {}) or {}))
        return f"The mail server refused these addresses: {refused or 'all of them'}. Check them and try again."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "The mail server refused the sender address. Check DEFAULT_FROM_EMAIL on the server."
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "The mail server did not answer in time. Try again later, or check EMAIL_HOST and EMAIL_PORT."
    if isinstance(exc, (ConnectionError, socket.gaierror, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
        return "Could not connect to the mail server. Check EMAIL_HOST and EMAIL_PORT on the server."
    if isinstance(exc, FileNotFoundError):
        return "The PDF to attach is missing from the file store, so nothing was sent."
    text = str(exc).strip() or exc.__class__.__name__
    return f"Sending failed: {text}"[:500]


# --------------------------------------------------------------------------- sending

def _mask(text, secrets):
    for secret in secrets:
        if secret:
            text = text.replace(secret, HIDDEN)
    return text


def send(company, actor, *, kind, to, subject, body, cc=(), attachments=(), object_id="", object_label="", url="",
         hide=(), automatic=False, resend_of=None):
    """Send one message and log it. Returns the EmailMessage row (status sent, saved or failed); never raises for a mail
    server problem.

    `attachments` is a list of (file name, bytes or a function returning bytes, mime type); a function is called only
    when sending, so a missing file is logged as a failure. Any text in `hide` (a one-time link token) is sent but replaced
    by "[hidden]" in the logged copy, and such a message cannot be sent again from the log.
    """
    to = clean_addresses(to, "to", required=True)
    cc = clean_addresses(cc, "cc")
    subject, body = clean_subject(subject), clean_body(body)
    conf = settings_for(company)
    sender = from_address(company, conf)
    error = ""
    try:
        files = [(name, content() if callable(content) else content, mime) for name, content, mime in attachments]
        message = mail.EmailMessage(subject, body, from_email=sender, to=to, cc=cc,
                                    reply_to=[conf.reply_to] if conf.reply_to else None, attachments=files)
        message.send(fail_silently=False)
        status = Status.SENT if is_configured() else Status.SAVED
    except Exception as exc:  # a mail problem is logged, never raised: it must not undo the business action
        logger.warning("Email to %s failed: %s", ", ".join(to), exc)
        status, error = Status.FAILED, describe_failure(exc)
    hide = [h for h in hide if h]
    return EmailMessage.objects.create(
        company=company, kind=kind, object_id=str(object_id or "")[:64], object_label=(object_label or "")[:200],
        url=(url or "")[:300], from_email=sender[:300], reply_to=conf.reply_to, to=to, cc=cc,
        subject=_mask(subject, hide), body=_mask(body, hide), attachments=[a[0] for a in attachments], status=status,
        error=error, automatic=automatic, can_resend=not hide, resend_of=resend_of,
        sent_by=actor if getattr(actor, "is_authenticated", False) else None)


def send_test(company, actor, to):
    to = clean_addresses(to, "to", required=True)
    conf = settings_for(company)
    body = ("This is a test message from your ERP.\n\nIf you can read it, the app can send email: invoices, quotations, "
            "purchase orders, payment reminders and invitation links will reach people.\n\n" + sign_off(company, conf))
    return send(company, actor, kind="test", to=to, subject=f"Test email from {sender_name(company, conf) or 'the ERP'}",
                body=body, object_label="Test email", url="/company/email")


def resend(row, actor):
    """Send a failed message again (a new log row linked to the old one). Attachments are fetched again from the record."""
    from . import kinds

    if row.status != Status.FAILED:
        raise ValidationError("Only a message that failed can be sent again.")
    if not row.can_resend:
        raise ValidationError("This message held a one-time invitation link, which is not kept, so it cannot be sent again. "
                              "Make a new invitation link and email that instead.")
    attachments = kinds.attachments_again(row, actor) if row.attachments else []
    return send(row.company, actor, kind=row.kind, to=row.to, cc=row.cc, subject=row.subject, body=row.body,
                attachments=attachments, object_id=row.object_id, object_label=row.object_label, url=row.url,
                resend_of=row)


# --------------------------------------------------------------------------- reading the log

def message_dict(m, *, detail=False):
    out = {"id": str(m.id), "created_at": m.created_at, "kind": m.kind, "kind_label": KIND_LABELS.get(m.kind, m.kind),
           "object_id": m.object_id, "object_label": m.object_label, "url": m.url, "to": m.to, "cc": m.cc,
           "subject": m.subject, "attachments": m.attachments, "status": m.status, "status_label": m.get_status_display(),
           "error": m.error, "automatic": m.automatic, "can_resend": m.can_resend and m.status == Status.FAILED,
           "resent": getattr(m, "resent", None), "resend_of": str(m.resend_of_id) if m.resend_of_id else None,
           "sent_by": (m.sent_by.get_full_name() or m.sent_by.username) if m.sent_by_id and m.sent_by else ("Automatic" if m.automatic else "")}
    if detail:
        out.update({"body": m.body, "from_email": m.from_email, "reply_to": m.reply_to})
    return out


KIND_LABELS = {
    "sales_document": "Invoice or note", "issued_document": "Quotation or letter", "purchase_order": "Purchase order",
    "payment_reminder": "Payment reminder", "invitation": "Invitation link", "payslip": "Payslip", "test": "Test email",
}


def search(company, *, q="", status="", kind=""):
    qs = EmailMessage.objects.filter(company=company).select_related("sent_by")
    if status:
        if status not in Status.values:
            raise ValidationError({"status": f"Choose one of: {', '.join(Status.values)}."})
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)
    q = (q or "").strip()
    if q:
        qs = qs.filter(Q(subject__icontains=q) | Q(object_label__icontains=q) | Q(to__icontains=q) | Q(cc__icontains=q)
                       | Q(error__icontains=q))
    return qs


def for_object(company, kind, object_id, limit=10):
    return list(EmailMessage.objects.filter(company=company, kind=kind, object_id=str(object_id)).select_related("sent_by")[:limit])
