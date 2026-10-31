import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import UUIDModel
from apps.core.storage import UploadTo, private_storage

from ..constants import AccessClass, DocType, VerificationStatus
from .people import AuthorizedSignatory, CompanyPartner
from .profile import CompanyAddress, CompanyProfile
from .bank import CompanyBankAccount
from .tax import CompanyTaxRegistration
from .policy import CompanyPolicy
from .numbering import IssuedReferenceNumber
from .people import CompanyContact


class CompanyDocument(UUIDModel):
    """An uploaded official record (certificates, agreements, bank proofs ...)."""

    class Kind(models.TextChoices):
        INCORPORATION = "incorporation_certificate", "Incorporation certificate"
        LLP_AGREEMENT = "llp_agreement", "LLP agreement"
        PAN = "pan", "PAN documentation"
        GST = "gst_certificate", "GST registration certificate"
        TAN = "tan", "TAN documentation"
        ADDRESS_PROOF = "address_proof", "Registered office address evidence"
        PARTNER_AUTH = "partner_authorization", "Partner authorization record"
        BANK_PROOF = "bank_verification", "Bank verification document"
        INSURANCE = "insurance", "Insurance document"
        LICENCE = "licence", "Licence or registration"
        POLICY = "policy", "Company policy"
        OTHER = "other", "Other official record"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="documents")
    document_type = models.CharField(max_length=30, choices=Kind.choices)
    title = models.CharField(max_length=200)
    original_filename = models.CharField(max_length=255)
    # Protected storage reference: a path inside PRIVATE_MEDIA_ROOT, never a URL.
    file = models.FileField(
        upload_to=UploadTo("company/documents/{document_type}"), storage=private_storage, max_length=255
    )
    mime_type = models.CharField(max_length=100)
    file_size = models.PositiveBigIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    access_classification = models.CharField(max_length=14, choices=AccessClass.CHOICES, default=AccessClass.CONFIDENTIAL)
    expiry_date = models.DateField(null=True, blank=True)
    verification_status = models.CharField(
        max_length=12, choices=VerificationStatus.CHOICES, default=VerificationStatus.UNVERIFIED
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    # Explicit optional links to the record this document supports.
    partner = models.ForeignKey(CompanyPartner, null=True, blank=True, on_delete=models.PROTECT, related_name="documents")
    signatory = models.ForeignKey(AuthorizedSignatory, null=True, blank=True, on_delete=models.PROTECT, related_name="documents")
    tax_registration = models.ForeignKey(
        CompanyTaxRegistration, null=True, blank=True, on_delete=models.PROTECT, related_name="documents"
    )
    bank_account = models.ForeignKey(
        CompanyBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="documents"
    )
    address = models.ForeignKey(CompanyAddress, null=True, blank=True, on_delete=models.PROTECT, related_name="documents")
    # Versioning: all versions of one logical document share a group id.
    document_group = models.UUIDField(default=uuid.uuid4, db_index=True)
    version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["document_group", "version"], name="unique_document_version"),
            models.UniqueConstraint(
                fields=["document_group"], condition=models.Q(is_current=True), name="one_current_document_version"
            ),
        ]

    def __str__(self):
        return f"{self.title} v{self.version}"


class IssuedDocument(UUIDModel):
    """A generated, issued business document. Immutable once created.

    The exact PDF is stored, together with a JSON snapshot of every company detail that was
    printed on it, so an issued invoice is never re-rendered from current company settings.
    """

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        SUPERSEDED = "superseded", "Superseded"
        VOID = "void", "Void"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="issued_documents")
    document_type = models.CharField(max_length=30, choices=DocType.CHOICES)
    title = models.CharField(max_length=200)
    reference = models.OneToOneField(
        IssuedReferenceNumber, null=True, blank=True, on_delete=models.PROTECT, related_name="document"
    )
    reference_number = models.CharField(max_length=120, blank=True, db_index=True)
    issue_date = models.DateField()
    source_module = models.CharField(max_length=60, blank=True)
    source_id = models.CharField(max_length=64, blank=True)

    file = models.FileField(
        upload_to=UploadTo("company/issued/{document_type}"), storage=private_storage, max_length=255
    )
    file_size = models.PositiveBigIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    # The same document without the letterhead, for printing on pre-printed official letterhead paper.
    print_file = models.FileField(
        upload_to=UploadTo("company/issued/{document_type}/print"), storage=private_storage, null=True,
        blank=True, max_length=255
    )
    print_checksum_sha256 = models.CharField(max_length=64, blank=True)
    logo_snapshot = models.FileField(
        upload_to=UploadTo("company/issued/{document_type}/logos"), storage=private_storage, null=True,
        blank=True, max_length=255
    )

    watermark_snapshot = models.FileField(
        upload_to=UploadTo("company/issued/{document_type}/logos"), storage=private_storage, null=True,
        blank=True, max_length=255
    )

    # Frozen company details used for this document.
    snapshot = models.JSONField()
    template_key = models.CharField(max_length=30)
    template_version = models.CharField(max_length=10)
    profile_version = models.PositiveIntegerField()
    branding_version = models.PositiveIntegerField()
    policy_version = models.PositiveIntegerField(null=True, blank=True)

    address = models.ForeignKey(CompanyAddress, null=True, blank=True, on_delete=models.PROTECT, related_name="issued_documents")
    contact = models.ForeignKey(CompanyContact, null=True, blank=True, on_delete=models.PROTECT, related_name="issued_documents")
    tax_registration = models.ForeignKey(
        CompanyTaxRegistration, null=True, blank=True, on_delete=models.PROTECT, related_name="issued_documents"
    )
    bank_account = models.ForeignKey(
        CompanyBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="issued_documents"
    )
    policy = models.ForeignKey(CompanyPolicy, null=True, blank=True, on_delete=models.PROTECT, related_name="issued_documents")
    letterhead_template = models.ForeignKey(
        "company.LetterheadTemplate", null=True, blank=True, on_delete=models.PROTECT, related_name="issued_documents"
    )
    signatory = models.ForeignKey(
        AuthorizedSignatory, null=True, blank=True, on_delete=models.PROTECT, related_name="issued_documents"
    )

    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ISSUED)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by")

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return self.reference_number or f"{self.get_document_type_display()} {self.id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            fields = set(kwargs.get("update_fields") or [])
            if not fields or not fields <= {"status", "updated_at"}:
                raise ValidationError("Issued documents are immutable; only their status may change.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Issued documents cannot be deleted; mark them void instead.")
