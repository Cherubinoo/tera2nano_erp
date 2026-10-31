from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core import india
from apps.core.models import UUIDModel

from .profile import CompanyProfile


class CompanyTaxRegistration(UUIDModel):
    class RegType(models.TextChoices):
        GSTIN = "gstin", "GSTIN"
        PAN = "pan", "PAN"
        TAN = "tan", "TAN"
        LLPIN = "llpin", "LLPIN"
        MSME = "msme", "MSME / Udyam"
        IEC = "iec", "Import Export Code"
        SHOPS = "shops_establishment", "Shops and Establishment"
        PROFESSIONAL_TAX = "professional_tax", "Professional tax"
        OTHER = "other", "Other registration"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="tax_registrations")
    registration_type = models.CharField(max_length=24, choices=RegType.choices)
    # A string, never numeric: preserves leading zeros and formatting.
    registration_number = models.CharField(max_length=60)
    jurisdiction = models.CharField(max_length=200, blank=True)
    state = models.CharField(max_length=120, blank=True)
    state_code = models.CharField(max_length=2, blank=True)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    verification_status = models.CharField(
        max_length=12,
        choices=[("unverified", "Unverified"), ("pending", "Pending verification"),
                 ("verified", "Verified"), ("rejected", "Rejected")],
        default="unverified",
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    supporting_document = models.ForeignKey(
        "company.CompanyDocument", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["registration_type", "registration_number"]
        constraints = [
            models.UniqueConstraint(fields=["registration_type", "registration_number"], name="unique_registration"),
            models.CheckConstraint(
                condition=Q(effective_to__isnull=True) | Q(effective_from__isnull=True)
                | Q(effective_to__gte=models.F("effective_from")),
                name="registration_dates_valid",
            ),
        ]

    def __str__(self):
        return f"{self.get_registration_type_display()} {self.registration_number}"

    def clean(self):
        errors = {}
        self.registration_number = (self.registration_number or "").strip().upper()
        number, kind = self.registration_number, self.registration_type
        pan = self.company.pan if self.company_id else ""
        if kind == self.RegType.GSTIN:
            message = india.validate_gstin(number, pan)
            if message:
                errors["registration_number"] = message
            else:
                self.state_code = number[:2]
                self.state = self.state or india.GST_STATES[number[:2]]
        elif kind == self.RegType.PAN and not india.PAN_RE.match(number):
            errors["registration_number"] = "Enter a valid PAN."
        elif kind == self.RegType.TAN and not india.TAN_RE.match(number):
            errors["registration_number"] = "Enter a valid TAN."
        elif kind == self.RegType.LLPIN and not india.LLPIN_RE.match(number):
            errors["registration_number"] = "Enter a valid LLPIN."
        if self.state_code and self.state_code not in india.GST_STATES:
            errors["state_code"] = "Unknown GST state code."
        if number and CompanyTaxRegistration.objects.exclude(pk=self.pk).filter(
            registration_type=kind, registration_number=number
        ).exists():
            errors["registration_number"] = "This registration is already recorded."
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            errors["effective_to"] = "Effective-to date cannot be before the effective-from date."
        if self.verification_status == "verified" and not self.supporting_document_id:
            errors["supporting_document"] = "Attach the supporting document before marking as verified."
        if errors:
            raise ValidationError(errors)


def validate_tax_codes(value, gst_current, gst_legacy):
    """Validate the `tax_codes` configuration list."""
    if not isinstance(value, list):
        raise ValidationError("Tax codes must be a list.")
    seen = set()
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValidationError(f"Tax code #{i + 1} must be an object.")
        code = str(item.get("code", "")).strip()
        if not code:
            raise ValidationError(f"Tax code #{i + 1} needs a code.")
        if code in seen:
            raise ValidationError(f"Duplicate tax code '{code}'.")
        seen.add(code)
        try:
            rate = Decimal(str(item.get("rate")))
        except (InvalidOperation, TypeError):
            raise ValidationError(f"Tax code '{code}' needs a numeric rate.")
        if rate in {Decimal(str(r)) for r in gst_current}:
            continue
        if rate in {Decimal(str(r)) for r in gst_legacy} and item.get("effective_to"):
            continue
        raise ValidationError(
            f"Tax code '{code}': {rate}% is not a currently applicable GST rate. Historic rates are accepted "
            "only when an effective-to date is supplied."
        )


class CompanyTaxSettings(UUIDModel):
    """Configurable tax behaviour. Nothing here is assumed to apply until an administrator enables it."""

    class GstTreatment(models.TextChoices):
        NOT_APPLICABLE = "not_applicable", "GST not applicable"
        REGULAR = "regular", "Regular taxpayer"
        COMPOSITION = "composition", "Composition scheme"
        UNREGISTERED = "unregistered", "Unregistered"

    company = models.OneToOneField(CompanyProfile, on_delete=models.PROTECT, related_name="tax_settings")
    gst_applicable = models.BooleanField(default=False)
    gst_treatment = models.CharField(max_length=20, choices=GstTreatment.choices, default=GstTreatment.NOT_APPLICABLE)
    # [{"code": "GST18", "label": "...", "rate": 18, "hsn_sac": "...", "effective_from": ..., "effective_to": ...}]
    tax_codes = models.JSONField(default=list, blank=True)
    default_tax_code = models.CharField(max_length=30, blank=True)
    # Place of supply used when a document does not name one (GST state code).
    default_place_of_supply_state_code = models.CharField(max_length=2, blank=True)
    # Invoice display switches.
    show_gstin_on_documents = models.BooleanField(default=True)
    show_hsn_sac = models.BooleanField(default=True)
    show_place_of_supply = models.BooleanField(default=True)
    show_tax_breakup = models.BooleanField(default=True)
    invoice_title = models.CharField(max_length=60, default="Invoice")
    effective_from = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "company tax settings"

    def clean(self):
        errors = {}
        try:
            validate_tax_codes(self.tax_codes, settings.GST_CURRENT_RATES, settings.GST_LEGACY_RATES)
        except ValidationError as exc:
            errors["tax_codes"] = exc.messages
        codes = {str(c.get("code")) for c in self.tax_codes if isinstance(c, dict)}
        if self.default_tax_code and self.default_tax_code not in codes:
            errors["default_tax_code"] = "Default tax code must be one of the configured tax codes."
        if self.default_place_of_supply_state_code and self.default_place_of_supply_state_code not in india.GST_STATES:
            errors["default_place_of_supply_state_code"] = "Unknown GST state code."
        if self.gst_applicable and self.gst_treatment == self.GstTreatment.NOT_APPLICABLE:
            errors["gst_treatment"] = "Choose a GST treatment when GST is applicable."
        if not self.gst_applicable and self.gst_treatment != self.GstTreatment.NOT_APPLICABLE:
            errors["gst_treatment"] = "GST treatment must be 'not applicable' while GST is switched off."
        if self.gst_applicable and self.company_id:
            if self.company.gst_registration_status != CompanyProfile.GstStatus.REGISTERED and \
                    self.gst_treatment in (self.GstTreatment.REGULAR, self.GstTreatment.COMPOSITION):
                errors["gst_applicable"] = (
                    "Record the GST registration on the company profile before enabling regular or "
                    "composition GST treatment."
                )
        if errors:
            raise ValidationError(errors)
