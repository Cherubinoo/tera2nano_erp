"""Daily job: email the payment reminders that are due, when the company has switched that on.

Registered with apps.company.daily (see apps.py). It sends only when a mail server is set up, only to customers who have an
email address, and never twice for the same reminder step: sending records a PaymentReminder row, and the database allows
one row per (invoice, rule), so a second run the same day (or a later one) finds nothing left for that step. Reminders for
customers without an address stay on the Reminders page for someone to handle by hand.
"""
import logging

from django.core.exceptions import ValidationError

from . import services

logger = logging.getLogger(__name__)


def send_due_reminders(company, today):
    conf = services.settings_for(company)
    if not conf.auto_reminders:
        return "automatic reminder emails are switched off"
    if not services.is_configured():
        return "email is not set up on the server; no reminders were emailed"
    from apps.sales import reminders

    sent = failed = no_address = 0
    for doc, rule in reminders.due_reminders(company, today):
        if not doc.customer.email:
            no_address += 1
            continue
        try:
            row = reminders.send(company, None, doc.pk, rule.days_offset, today=today, automatic=True)
        except ValidationError as exc:  # handled meanwhile by someone else, or no longer due: skip it
            logger.info("Automatic reminder for %s not sent: %s", doc.number, exc)
            continue
        if row.email is not None and row.email.status == row.email.Status.FAILED:
            failed += 1
        else:
            sent += 1
    text = f"{sent} reminder email(s) sent"
    if failed:
        text += f", {failed} failed (see Sent emails)"
    if no_address:
        text += f", {no_address} left for you (customer has no email address)"
    return text
