"""Email: the company's sender settings and a log of every message the app has sent (or tried to send).

The SMTP server itself (host, port, user name, password) is configured on the server through environment variables
(see config/settings.py); nothing secret is stored here.
"""
from django.conf import settings
from django.db import models

from apps.company.models import CompanyProfile
from apps.core.models import UUIDModel


class EmailSettings(UUIDModel):
    """How the company's emails look: sender name, reply-to address and signature, plus automatic payment reminders."""

    company = models.OneToOneField(CompanyProfile, on_delete=models.CASCADE, related_name="email_settings")
    sender_name = models.CharField(max_length=150, blank=True)  # empty: the company's display name
    reply_to = models.EmailField(blank=True)
    signature = models.TextField(blank=True)
    auto_reminders = models.BooleanField(default=False)  # email payment reminders automatically once a day
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    def __str__(self):
        return f"Email settings for {self.company}"


class EmailMessage(UUIDModel):
    """One message: who it went to, what it said, which file was attached, and whether the mail server took it."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        SAVED = "saved", "Saved, not delivered"  # email is not set up on the server: the message was written to a folder
        FAILED = "failed", "Failed"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="email_messages")
    # What the message was about: a kind (sales_document, purchase_order, payment_reminder, invitation, payslip, test ...),
    # the record's id, a readable label and the page in the app where it can be opened.
    kind = models.CharField(max_length=40)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_label = models.CharField(max_length=200, blank=True)
    url = models.CharField(max_length=300, blank=True)
    from_email = models.CharField(max_length=300)
    reply_to = models.CharField(max_length=254, blank=True)
    to = models.JSONField(default=list)
    cc = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=300)
    body = models.TextField()
    attachments = models.JSONField(default=list, blank=True)  # file names only; the files stay where they are
    status = models.CharField(max_length=10, choices=Status.choices)
    error = models.TextField(blank=True)
    automatic = models.BooleanField(default=False)  # sent by the daily job, not by a person
    can_resend = models.BooleanField(default=True)  # False when the logged copy hides something (an invitation link)
    resend_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="resends")
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "-created_at"]), models.Index(fields=["kind", "object_id"])]

    def __str__(self):
        return f"{self.subject} ({self.status})"
