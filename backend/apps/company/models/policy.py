from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import UUIDModel

from ..constants import DocType
from .profile import CompanyProfile


class CompanyPolicy(UUIDModel):
    """A versioned set of default business terms.

    Active versions are immutable: a change creates a new version, so previously issued
    quotations and invoices keep pointing at the terms that applied when they were issued.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="policies")
    version_number = models.PositiveIntegerField(editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    change_summary = models.CharField(max_length=300, blank=True)

    payment_terms = models.TextField(blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(null=True, blank=True)
    quotation_validity_days = models.PositiveSmallIntegerField(null=True, blank=True)
    invoice_due_days = models.PositiveSmallIntegerField(null=True, blank=True)
    quotation_terms = models.TextField(blank=True)
    invoice_terms = models.TextField(blank=True)
    project_terms = models.TextField(blank=True)
    confidentiality_notice = models.TextField(blank=True)
    document_footer_text = models.CharField(max_length=300, blank=True)
    refund_cancellation_terms = models.TextField(blank=True)
    # [{"scope": "expense", "amount": "50000.00", "approver_role": "admin"}]
    approval_thresholds = models.JSONField(default=list, blank=True)
    # {"invoice": 8, "quotation": 3}   (years)
    retention_settings = models.JSONField(default=dict, blank=True)
    email_signature = models.TextField(blank=True)
    correspondence_wording = models.TextField(blank=True)

    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version_number"]
        verbose_name_plural = "company policies"
        constraints = [
            models.UniqueConstraint(fields=["company", "version_number"], name="unique_policy_version"),
            models.UniqueConstraint(fields=["company"], condition=Q(status="active"), name="one_active_policy"),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_from__isnull=True)
                | Q(effective_to__gte=models.F("effective_from")),
                name="policy_dates_valid",
            ),
        ]

    def __str__(self):
        return f"Policy v{self.version_number} ({self.status})"

    def clean(self):
        errors = {}
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            errors["effective_to"] = "Effective-to date cannot be before the effective-from date."
        if not isinstance(self.approval_thresholds, list):
            errors["approval_thresholds"] = "Must be a list."
        else:
            for item in self.approval_thresholds:
                try:
                    amount = Decimal(str(item.get("amount")))
                    if amount < 0 or not item.get("scope"):
                        raise InvalidOperation
                except (InvalidOperation, AttributeError, TypeError):
                    errors["approval_thresholds"] = "Each threshold needs a scope and a non-negative amount."
                    break
        if not isinstance(self.retention_settings, dict):
            errors["retention_settings"] = "Must be an object of document type to years."
        else:
            for key, years in self.retention_settings.items():
                if key not in DocType.VALUES or not isinstance(years, int) or not 0 < years <= 99:
                    errors["retention_settings"] = "Use a valid document type and whole years between 1 and 99."
                    break
        if errors:
            raise ValidationError(errors)
