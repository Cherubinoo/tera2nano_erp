"""GST and tax records: returns filed, challans paid, TDS credits to claim, the income tax worksheet.

The amounts of GST and TDS themselves are never stored here: they are read from the books (the journal) and from sales documents.
These tables hold what the books cannot: the return you filed (with its acknowledgement), the challan you paid with, the TDS
certificates customers gave you, and the assumptions behind your income tax estimate.
Money is `Decimal`; links to other modules are plain ids.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.company.models import CompanyBankAccount, CompanyProfile
from apps.core.models import UUIDModel


class TaxFiling(UUIDModel):
    class Type(models.TextChoices):
        GSTR1 = "gstr1", "GSTR-1"
        GSTR3B = "gstr3b", "GSTR-3B"
        GSTR9 = "gstr9", "GSTR-9"
        TDS_24Q = "tds_24q", "TDS 24Q"
        TDS_26Q = "tds_26q", "TDS 26Q"
        ITR5 = "itr5", "ITR-5"
        TAX_AUDIT = "tax_audit", "Tax audit"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PREPARED = "prepared", "Prepared"
        FILED = "filed", "Filed"
        LATE = "late", "Late"  # derived for the API: not filed and past its due date; never stored

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="tax_filings")
    reference = models.CharField(max_length=60, blank=True, default="")
    return_type = models.CharField(max_length=12, choices=Type.choices)
    financial_year = models.CharField(max_length=9)  # "2026-27"
    period_label = models.CharField(max_length=40)   # "Aug 2026", "Q2 2026-27", "FY 2026-27"
    period_start = models.DateField()
    period_end = models.DateField()
    due_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    audit_applicable = models.BooleanField(default=False)  # ITR-5: the later due date applies when the accounts are audited
    arn = models.CharField(max_length=60, blank=True)      # acknowledgement / ARN / token number
    filed_date = models.DateField(null=True, blank=True)
    figures = models.JSONField(default=dict, blank=True)   # {"outward_taxable": "123.00", ...}; see constants.FIGURE_KEYS
    late_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    interest = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.CharField(max_length=500, blank=True)
    prepared_at = models.DateTimeField(null=True, blank=True)
    # ITC set-off posted for a GSTR-3B: the matrix used and the journal entry it made.
    setoff = models.JSONField(default=dict, blank=True)
    setoff_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["due_date", "return_type"]
        constraints = [
            models.UniqueConstraint(fields=["company", "return_type", "period_start", "period_end"], name="one_filing_per_period"),
            models.UniqueConstraint(fields=["company", "reference"], condition=~Q(reference=""), name="unique_tax_filing_reference"),
        ]
        indexes = [models.Index(fields=["return_type", "status"]), models.Index(fields=["due_date"])]

    def __str__(self):
        return f"{self.get_return_type_display()} {self.period_label}"


class TaxPayment(UUIDModel):
    class Type(models.TextChoices):
        GST = "gst", "GST"
        TDS = "tds", "TDS"
        ADVANCE_TAX = "advance_tax", "Advance tax"
        INCOME_TAX = "income_tax", "Income tax (self-assessment)"

    class Status(models.TextChoices):
        RECORDED = "recorded", "Recorded"
        VOID = "void", "Void"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="tax_payments")
    reference = models.CharField(max_length=60, blank=True, default="")
    tax_type = models.CharField(max_length=12, choices=Type.choices)
    date = models.DateField()
    financial_year = models.CharField(max_length=9)
    period_label = models.CharField(max_length=40, blank=True)  # "Aug 2026" (GST month), "Q2 2026-27" (TDS), "Instalment 2" (advance tax)
    period_start = models.DateField(null=True, blank=True)
    instalment = models.PositiveSmallIntegerField(null=True, blank=True)  # advance tax instalment 1-4
    section = models.CharField(max_length=10, blank=True)                 # TDS section the challan is for
    heads = models.JSONField(default=dict)                                # {"cgst": "100.00", ...}
    total = models.DecimalField(max_digits=16, decimal_places=2)
    cin = models.CharField(max_length=40, blank=True)                     # challan identification / CPIN / CRN number
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account = models.ForeignKey(CompanyBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    notes = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECORDED)
    void_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "reference"], condition=~Q(reference=""), name="unique_tax_payment_reference"),
            models.CheckConstraint(condition=Q(total__gt=0), name="tax_payment_total_positive"),
        ]
        indexes = [models.Index(fields=["tax_type", "financial_year"])]

    def __str__(self):
        return f"{self.reference or 'Tax payment'} {self.total}"


class TdsCredit(UUIDModel):
    """TDS a customer deducted from what it paid us: a Form 26AS / Form 16A style tracker, entered by hand."""

    class Status(models.TextChoices):
        EXPECTED = "expected", "Certificate awaited"
        RECEIVED = "received", "Certificate received"
        CLAIMED = "claimed", "Matched in Form 26AS"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="tds_credits")
    deductor_name = models.CharField(max_length=200)
    deductor_tan = models.CharField(max_length=10, blank=True)
    section = models.CharField(max_length=10)
    financial_year = models.CharField(max_length=9)
    quarter = models.PositiveSmallIntegerField(null=True, blank=True)
    date = models.DateField()  # date of deduction or credit
    gross_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)  # amount paid or credited before TDS
    tds_amount = models.DecimalField(max_digits=16, decimal_places=2)
    certificate_no = models.CharField(max_length=60, blank=True)
    certificate_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.EXPECTED)
    customer_id = models.UUIDField(null=True, blank=True)  # apps.sales Customer, a soft link
    notes = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [models.CheckConstraint(condition=Q(tds_amount__gt=0), name="tds_credit_positive")]
        indexes = [models.Index(fields=["financial_year"])]


class IncomeTaxYear(UUIDModel):
    """The advance tax estimate and income tax worksheet inputs for one financial year."""

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="income_tax_years")
    financial_year = models.CharField(max_length=9)
    estimated_liability = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    profit_override = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)  # used instead of the books' profit
    addbacks = models.JSONField(default=list, blank=True)    # [{"label": "Penalties", "amount": "1000.00"}]
    deductions = models.JSONField(default=list, blank=True)
    rate = models.DecimalField(max_digits=5, decimal_places=2, default=30)
    surcharge_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    cess_pct = models.DecimalField(max_digits=5, decimal_places=2, default=4)
    notes = models.CharField(max_length=500, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "financial_year"], name="one_income_tax_year")]


class ItcFlag(UUIDModel):
    """A decision on whether the input GST on one journal entry (a purchase) is eligible as credit."""

    class Eligibility(models.TextChoices):
        ELIGIBLE = "eligible", "Eligible"
        INELIGIBLE = "ineligible", "Ineligible (blocked or not claimed)"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="itc_flags")
    entry_id = models.UUIDField()
    eligibility = models.CharField(max_length=12, choices=Eligibility.choices)
    reason = models.CharField(max_length=200, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "entry_id"], name="one_itc_flag_per_entry")]
