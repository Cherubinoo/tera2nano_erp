from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core import crypto, india
from apps.core.models import UUIDModel
from apps.core.validators import ifsc_validator

from .profile import CompanyProfile


class CompanyBankAccount(UUIDModel):
    """Company bank account. The account number is encrypted at rest and masked by default."""

    class AccountType(models.TextChoices):
        CURRENT = "current", "Current"
        SAVINGS = "savings", "Savings"
        OVERDRAFT = "overdraft", "Overdraft / cash credit"
        OTHER = "other", "Other"

    class Verification(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        PENDING = "pending", "Pending verification"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="bank_accounts")
    # Allocated automatically (T2N-BNK-26-2710-0001) when the account is created; never edited or reused.
    reference_code = models.CharField(max_length=60, blank=True, default="", editable=False)
    display_name = models.CharField(max_length=120)
    bank_name = models.CharField(max_length=150)
    branch = models.CharField(max_length=150, blank=True)
    account_holder_name = models.CharField(max_length=200)
    account_number_encrypted = models.TextField(editable=False)
    account_number_last4 = models.CharField(max_length=4, editable=False)
    account_number_hash = models.CharField(max_length=64, editable=False)
    ifsc_code = models.CharField(max_length=11, validators=[ifsc_validator])
    account_type = models.CharField(max_length=12, choices=AccountType.choices, default=AccountType.CURRENT)
    currency = models.CharField(max_length=3, default="INR")
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    verification_status = models.CharField(max_length=12, choices=Verification.choices, default=Verification.UNVERIFIED)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    # Who last changed the payment-critical fields; the verifier must be a different user.
    last_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    supporting_document = models.ForeignKey(
        "company.CompanyDocument", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    show_on_invoices = models.BooleanField(default=False)
    show_on_quotations = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_primary", "display_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_primary"], condition=Q(is_primary=True, is_active=True), name="one_active_primary_bank"
            ),
            models.UniqueConstraint(
                fields=["account_number_hash"], condition=Q(is_active=True), name="unique_active_account_number"
            ),
            models.UniqueConstraint(
                fields=["company", "reference_code"], condition=~Q(reference_code=""), name="unique_bank_reference_code"
            ),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_from__isnull=True)
                | Q(effective_to__gte=models.F("effective_from")),
                name="bank_dates_valid",
            ),
        ]

    def __str__(self):
        return f"{self.reference_code + ' ' if self.reference_code else ''}{self.display_name} ({self.masked_account_number})"

    # --- account number handling ------------------------------------------
    def set_account_number(self, number: str):
        number = (number or "").strip().replace(" ", "")
        if not india.ACCOUNT_RE.match(number):
            raise ValidationError({"account_number": "Account number must be 6 to 20 digits."})
        self.account_number_encrypted = crypto.encrypt(number)
        self.account_number_last4 = number[-4:]
        self.account_number_hash = crypto.keyed_hash(f"{self.ifsc_code}:{number}" if self.ifsc_code else number)

    def reveal_account_number(self) -> str:
        return crypto.decrypt(self.account_number_encrypted)

    @property
    def masked_account_number(self) -> str:
        return crypto.mask_account_number(self.account_number_last4)

    def clean(self):
        errors = {}
        self.ifsc_code = (self.ifsc_code or "").strip().upper()
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            errors["effective_to"] = "Effective-to date cannot be before the effective-from date."
        if (self.show_on_invoices or self.show_on_quotations) and self.verification_status != self.Verification.VERIFIED:
            errors["show_on_invoices"] = "Bank details can appear on documents only after they are verified."
        if self.is_primary and not self.is_active:
            errors["is_primary"] = "An inactive account cannot be the primary account."
        if errors:
            raise ValidationError(errors)
