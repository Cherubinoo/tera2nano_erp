import datetime

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Func, Q

from apps.core.models import UUIDModel

from ..constants import DocType
from .profile import CompanyProfile


def inclusive_date_range():
    """daterange(start, end, '[]') so a year ending 31 Mar does not clash with one starting 1 Apr."""
    return Func(
        models.F("start_date"), models.F("end_date"), models.Value("[]"),
        function="daterange", output_field=DateRangeField(),
    )


class FinancialYear(UUIDModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="financial_years")
    label = models.CharField(max_length=20)
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    is_locked = models.BooleanField(default=False)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["company", "label"], name="unique_financial_year_label"),
            models.UniqueConstraint(fields=["company"], condition=Q(is_current=True), name="one_current_financial_year"),
            models.CheckConstraint(condition=Q(end_date__gt=models.F("start_date")), name="financial_year_dates_valid"),
            ExclusionConstraint(
                name="financial_years_do_not_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    (inclusive_date_range(), RangeOperators.OVERLAPS),
                ],
            ),
        ]

    def __str__(self):
        return self.label

    def clean(self):
        errors = {}
        if self.start_date and self.end_date:
            if self.end_date <= self.start_date:
                errors["end_date"] = "End date must be after the start date."
            else:
                overlap = FinancialYear.objects.exclude(pk=self.pk).filter(
                    company_id=self.company_id, start_date__lte=self.end_date, end_date__gte=self.start_date
                )
                if overlap.exists():
                    errors["start_date"] = "This financial year overlaps an existing financial year."
        if self.label and FinancialYear.objects.exclude(pk=self.pk).filter(
            company_id=self.company_id, label=self.label
        ).exists():
            errors["label"] = "A financial year with this label already exists."
        if self.is_current and self.status == self.Status.CLOSED:
            errors["is_current"] = "A closed financial year cannot be the current year."
        if errors:
            raise ValidationError(errors)


class DocumentNumberSequence(UUIDModel):
    class Reset(models.TextChoices):
        NEVER = "never", "Never"
        YEARLY = "yearly", "Every calendar year"
        FINANCIAL_YEAR = "financial_year", "Every financial year"
        MONTHLY = "monthly", "Every month"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="number_sequences")
    document_type = models.CharField(max_length=30, choices=DocType.CHOICES)
    prefix = models.CharField(max_length=40)
    # Set for the per-template letter sequences (LHT-001 ...); otherwise the sequence is per document type.
    letterhead_template = models.ForeignKey(
        "company.LetterheadTemplate", null=True, blank=True, on_delete=models.PROTECT, related_name="sequences"
    )
    include_financial_year = models.BooleanField(default=False)
    include_short_year = models.BooleanField(default=False, help_text="Two-digit year of the issue date, e.g. 26")
    date_format = models.CharField(
        max_length=10, blank=True, choices=[("", "No date"), ("ddmm", "Day and month (DDMM)"),
                                            ("ddmmyyyy", "Day, month and year (DDMMYYYY)")],
        help_text="Issue date printed in the number.",
    )
    separator = models.CharField(max_length=3, default="/", blank=True)
    min_length = models.PositiveSmallIntegerField(default=5)
    reset_frequency = models.CharField(max_length=16, choices=Reset.choices, default=Reset.NEVER)
    next_value = models.PositiveIntegerField(default=1)
    # Identifies the period the counter currently belongs to (FY label, year, or year-month).
    current_period = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["document_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "document_type"], condition=models.Q(letterhead_template__isnull=True),
                name="one_sequence_per_document_type",
            ),
            models.UniqueConstraint(
                fields=["company", "letterhead_template"], condition=models.Q(letterhead_template__isnull=False),
                name="one_sequence_per_letterhead_template",
            ),
        ]

    def __str__(self):
        return f"{self.get_document_type_display()}: {self.prefix}"

    def clean(self):
        errors = {}
        if not (self.prefix or "").strip():
            errors["prefix"] = "Enter a prefix."
        if not 1 <= self.min_length <= 10:
            errors["min_length"] = "Sequence length must be between 1 and 10."
        if self.next_value < 1:
            errors["next_value"] = "Next value must be at least 1."
        # A restarting counter must not be able to repeat a reference: that needs the financial year, or the
        # year and the date, in the number itself.
        if (self.reset_frequency == self.Reset.FINANCIAL_YEAR and not self.include_financial_year
                and not (self.include_short_year and self.date_format)):
            errors["reset_frequency"] = (
                "Resetting every financial year needs the financial-year segment, or the year and date, in the number."
            )
        clash = DocumentNumberSequence.objects.exclude(pk=self.pk).filter(
            company_id=self.company_id, letterhead_template_id=self.letterhead_template_id
        )
        if self.letterhead_template_id is None:
            clash = clash.filter(document_type=self.document_type)
        if self.document_type and clash.exists():
            errors["document_type" if self.letterhead_template_id is None else "letterhead_template"] = (
                "A numbering sequence already exists for this document type."
                if self.letterhead_template_id is None else "This template already has a numbering sequence."
            )
        if errors:
            raise ValidationError(errors)

    def period_segment(self, on_date):
        """Calendar period printed in the number when the counter resets yearly or monthly.

        Without it, a reset would reissue identical references, so these two frequencies always add it.
        """
        # The number may already carry the year (and month) through its own segments; then nothing is added.
        has_year = self.include_short_year or self.date_format == "ddmmyyyy"
        has_month = self.date_format in ("ddmm", "ddmmyyyy")
        if self.reset_frequency == self.Reset.YEARLY:
            return "" if has_year else f"{on_date.year}"
        if self.reset_frequency == self.Reset.MONTHLY:
            return "" if (has_year and has_month) else f"{on_date.year}{on_date.month:02d}"
        return ""

    def preview(self, fy_label="2026-27", value=None, on_date=None):
        value = self.next_value if value is None else value
        parts = [self.prefix]
        if self.include_financial_year:
            parts.append(fy_label)
        on_date = on_date or datetime.date.today()
        if self.include_short_year:
            parts.append(f"{on_date.year % 100:02d}")
        if self.date_format == "ddmm":
            parts.append(f"{on_date.day:02d}{on_date.month:02d}")
        elif self.date_format == "ddmmyyyy":
            parts.append(f"{on_date.day:02d}{on_date.month:02d}{on_date.year}")
        segment = self.period_segment(on_date)
        if segment:
            parts.append(segment)
        parts.append(str(value).zfill(self.min_length))
        return self.separator.join(parts)


class IssuedReferenceNumber(UUIDModel):
    """Every number ever allocated. Rows are never deleted or reused; numbers can only be voided."""

    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        VOID = "void", "Void"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="reference_numbers")
    sequence = models.ForeignKey(DocumentNumberSequence, on_delete=models.PROTECT, related_name="issued_numbers")
    document_type = models.CharField(max_length=30, choices=DocType.CHOICES)
    reference_number = models.CharField(max_length=120, unique=True)
    sequence_value = models.PositiveIntegerField()
    period = models.CharField(max_length=20, blank=True)
    financial_year = models.ForeignKey(FinancialYear, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ISSUED)
    void_reason = models.TextField(blank=True)
    allocated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    allocated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-allocated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["sequence", "period", "sequence_value"], name="unique_value_per_sequence_period"
            ),
        ]

    def __str__(self):
        return self.reference_number

    def delete(self, *args, **kwargs):
        raise ValidationError("Issued reference numbers cannot be deleted; void them instead.")
