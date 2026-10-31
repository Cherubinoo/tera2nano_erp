import zoneinfo

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core import india
from apps.core.models import UUIDModel
from apps.core.storage import UploadTo, private_storage
from apps.core.validators import (
    llpin_validator,
    pan_validator,
    phone_validator,
    pin_validator,
    tan_validator,
)

from ..constants import DocType


def document_type_array():
    """Postgres array of document-type codes (supports the `overlap` lookup)."""
    return ArrayField(
        models.CharField(max_length=30, choices=DocType.CHOICES), default=list, blank=True
    )


class CompanyProfile(UUIDModel):
    """The single company record. Exactly one row may exist (enforced in the database)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (setup in progress)"
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    class EntityType(models.TextChoices):
        LLP = "llp", "Limited Liability Partnership"
        PRIVATE_LIMITED = "private_limited", "Private Limited Company"
        PARTNERSHIP = "partnership", "Partnership firm"
        PROPRIETORSHIP = "proprietorship", "Proprietorship"
        OTHER = "other", "Other"

    class GstStatus(models.TextChoices):
        NOT_DECLARED = "not_declared", "Not declared"
        NOT_REGISTERED = "not_registered", "Not registered"
        APPLIED = "applied", "Application in progress"
        REGISTERED = "registered", "Registered"
        CANCELLED = "cancelled", "Registration cancelled"

    class SetupStatus(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    # Enforces a single company row; unique=True on a constant makes a second insert fail.
    singleton = models.BooleanField(default=True, unique=True, editable=False)

    # --- basic information -------------------------------------------------
    legal_name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)
    trade_name = models.CharField(max_length=255, blank=True)
    short_code = models.CharField(
        max_length=12, blank=True, help_text="Short mnemonic used when suggesting document number prefixes."
    )
    logo = models.FileField(
        upload_to=UploadTo("company/logo"), storage=private_storage, null=True, blank=True
    )
    logo_checksum = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    industry = models.CharField(max_length=120, blank=True)
    business_type = models.CharField(max_length=120, blank=True)
    date_of_incorporation = models.DateField(null=True, blank=True)
    website = models.URLField(blank=True)
    primary_email = models.EmailField(blank=True)
    primary_phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    alternate_phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    default_currency = models.CharField(max_length=3, default="INR")
    default_timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)

    # --- legal information -------------------------------------------------
    legal_entity_type = models.CharField(max_length=20, choices=EntityType.choices, blank=True)
    llpin = models.CharField(max_length=8, blank=True, validators=[llpin_validator])
    pan = models.CharField(max_length=10, blank=True, validators=[pan_validator])
    tan = models.CharField(max_length=10, blank=True, validators=[tan_validator])
    gst_registration_status = models.CharField(
        max_length=20, choices=GstStatus.choices, default=GstStatus.NOT_DECLARED
    )
    gstin = models.CharField(max_length=15, blank=True)
    incorporation_certificate_reference = models.CharField(max_length=120, blank=True)
    incorporation_certificate = models.ForeignKey(
        "company.CompanyDocument", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    # --- setup wizard progress --------------------------------------------
    setup_status = models.CharField(max_length=12, choices=SetupStatus.choices, default=SetupStatus.IN_PROGRESS)
    setup_step = models.PositiveSmallIntegerField(default=1)
    setup_skipped_steps = models.JSONField(default=list, blank=True)
    setup_completed_at = models.DateTimeField(null=True, blank=True)

    # Bumped by the audit service on every audited change; recorded on issued documents.
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = "company profile"

    def __str__(self):
        return self.display_name

    def clean(self):
        errors = {}
        for field in ("pan", "tan", "gstin", "llpin", "short_code", "default_currency"):
            value = getattr(self, field)
            if value:
                setattr(self, field, value.strip().upper())
        try:
            zoneinfo.ZoneInfo(self.default_timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            errors["default_timezone"] = "Unknown time zone."
        if self.gstin:
            if self.gst_registration_status != self.GstStatus.REGISTERED:
                errors["gstin"] = "A GSTIN can only be stored when GST registration status is 'Registered'."
            else:
                message = india.validate_gstin(self.gstin, self.pan)
                if message:
                    errors["gstin"] = message
        elif self.gst_registration_status == self.GstStatus.REGISTERED:
            errors["gstin"] = "Enter the GSTIN for a registered company."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.singleton = True
        super().save(*args, **kwargs)


class CompanyAddress(UUIDModel):
    class AddressType(models.TextChoices):
        REGISTERED = "registered_office", "Registered office"
        CORPORATE = "corporate_office", "Corporate office"
        BRANCH = "branch_office", "Branch office"
        CORRESPONDENCE = "correspondence", "Correspondence address"
        BILLING = "billing", "Billing address"
        OTHER = "other", "Other business address"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="addresses")
    label = models.CharField(max_length=120)
    address_type = models.CharField(max_length=20, choices=AddressType.choices)
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True)
    building_name = models.CharField(max_length=255, blank=True)
    locality = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120)
    district = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120)
    state_code = models.CharField(max_length=2, blank=True, help_text="GST state code, e.g. 29")
    postal_code = models.CharField(max_length=6, validators=[pin_validator])
    country = models.CharField(max_length=120, default="India")
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    # Document types on which this address is printed (quotation, invoice, letters ...).
    document_types = document_type_array()

    class Meta:
        verbose_name_plural = "company addresses"
        ordering = ["-is_primary", "address_type", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_primary"], condition=Q(is_primary=True, is_active=True), name="one_active_primary_address"
            ),
            models.UniqueConstraint(
                fields=["address_type"],
                condition=Q(address_type="registered_office", is_active=True),
                name="one_active_registered_office",
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_from__isnull=True)
                | Q(effective_to__gte=models.F("effective_from")),
                name="address_effective_range_valid",
            ),
        ]

    def __str__(self):
        return f"{self.label} ({self.get_address_type_display()})"

    def clean(self):
        errors = {}
        if self.state_code and self.state_code not in india.GST_STATES:
            errors["state_code"] = "Unknown GST state code."
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            errors["effective_to"] = "Effective-to date cannot be before the effective-from date."
        if self.is_active and self.address_type == self.AddressType.REGISTERED:
            other = CompanyAddress.objects.filter(is_active=True, address_type=self.AddressType.REGISTERED)
            if other.exclude(pk=self.pk).exists():
                errors["address_type"] = (
                    "An active registered office already exists. Deactivate it (and set its effective-to date) "
                    "before adding a new one."
                )
        if self.is_active and self.document_types:
            clash = (
                CompanyAddress.objects.filter(is_active=True)
                .exclude(pk=self.pk)
                .filter(document_types__overlap=self.document_types)
            )
            if clash.exists():
                errors["document_types"] = (
                    "Another active address is already used for one of these document types."
                )
        if errors:
            raise ValidationError(errors)
