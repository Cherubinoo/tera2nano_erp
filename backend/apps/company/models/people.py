from datetime import date

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import UUIDModel
from apps.core.storage import UploadTo, private_storage
from apps.core.validators import phone_validator

from ..constants import ContactType
from .profile import CompanyProfile, document_type_array


class CompanyPartner(UUIDModel):
    class PartnerType(models.TextChoices):
        DESIGNATED = "designated_partner", "Designated partner"
        PARTNER = "partner", "Partner"
        NOMINEE = "nominee_partner", "Nominee partner"
        OTHER = "other", "Other"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="partners")
    full_name = models.CharField(max_length=200)
    designation = models.CharField(max_length=120, blank=True)
    partner_type = models.CharField(max_length=20, choices=PartnerType.choices, default=PartnerType.PARTNER)
    is_designated_partner = models.BooleanField(default=False)
    dpin = models.CharField(max_length=8, blank=True, help_text="Designated partner identification number")
    official_email = models.EmailField(blank=True)
    official_phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    appointment_date = models.DateField(null=True, blank=True)
    cessation_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    authorization_scope = models.TextField(blank=True)
    signature_image = models.FileField(
        upload_to=UploadTo("company/signatures"), storage=private_storage, null=True, blank=True
    )
    signature_checksum = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["full_name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(cessation_date__isnull=True) | Q(appointment_date__isnull=True)
                | Q(cessation_date__gte=models.F("appointment_date")),
                name="partner_dates_valid",
            ),
        ]

    def __str__(self):
        return self.full_name

    def clean(self):
        errors = {}
        if self.dpin and not (self.dpin.isdigit() and len(self.dpin) == 8):
            errors["dpin"] = "DPIN must be 8 digits."
        if self.appointment_date and self.cessation_date and self.cessation_date < self.appointment_date:
            errors["cessation_date"] = "Cessation date cannot be before the appointment date."
        if self.is_active and self.cessation_date and self.cessation_date <= date.today():
            errors["is_active"] = "A partner whose cessation date has passed cannot be active."
        if self.is_designated_partner and self.partner_type == self.PartnerType.OTHER:
            errors["partner_type"] = "A designated partner must be a partner type."
        if errors:
            raise ValidationError(errors)


class AuthorizedSignatory(UUIDModel):
    class AuthType(models.TextChoices):
        ALL_DOCUMENTS = "all_documents", "All company documents"
        SPECIFIC = "specific_documents", "Specific document types"
        BANKING = "banking", "Banking and payments"
        STATUTORY = "statutory", "Statutory filings"
        CONTRACTS = "contracts", "Contracts and agreements"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        REVOKED = "revoked", "Revoked"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="signatories")
    partner = models.ForeignKey(
        CompanyPartner, null=True, blank=True, on_delete=models.PROTECT, related_name="signatory_authorizations"
    )
    name = models.CharField(max_length=200)
    designation = models.CharField(max_length=120, blank=True)
    authorization_type = models.CharField(max_length=20, choices=AuthType.choices, default=AuthType.SPECIFIC)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    authorized_document_types = document_type_array()
    authorization_reference = models.CharField(max_length=200, blank=True)
    supporting_document = models.ForeignKey(
        "company.CompanyDocument", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    signature_image = models.FileField(
        upload_to=UploadTo("company/signatures"), storage=private_storage, null=True, blank=True
    )
    signature_checksum = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "authorized signatories"
        constraints = [
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_from__isnull=True) | Q(valid_to__gte=models.F("valid_from")),
                name="signatory_dates_valid",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        errors = {}
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            errors["valid_to"] = "Authorization end date cannot be before the start date."
        if self.authorization_type == self.AuthType.SPECIFIC and not self.authorized_document_types:
            errors["authorized_document_types"] = "Select the document types this signatory may sign."
        if self.status == self.Status.ACTIVE and not self.valid_from:
            errors["valid_from"] = "An active authorization needs a start date."
        if errors:
            raise ValidationError(errors)

    def authorizes(self, doc_type, on_date):
        """Whether this signatory may sign `doc_type` on `on_date`. Not proof of approval."""
        if self.status != self.Status.ACTIVE:
            return False
        if self.valid_from and on_date < self.valid_from:
            return False
        if self.valid_to and on_date > self.valid_to:
            return False
        if self.authorization_type == self.AuthType.ALL_DOCUMENTS:
            return True
        return doc_type in self.authorized_document_types


class CompanyContact(UUIDModel):
    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="contacts")
    contact_type = models.CharField(max_length=20, choices=ContactType.CHOICES)
    display_name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    department = models.CharField(max_length=120, blank=True)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    # Documents on which this contact is printed; empty means "use by contact type".
    document_types = document_type_array()

    class Meta:
        ordering = ["contact_type", "-is_primary", "display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["contact_type"],
                condition=Q(is_primary=True, is_active=True),
                name="one_active_primary_contact_per_type",
            ),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.get_contact_type_display()})"

    def clean(self):
        if not self.email and not self.phone:
            raise ValidationError("Provide at least an email address or a telephone number.")
