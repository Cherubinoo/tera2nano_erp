"""Partner accounting for the LLP: capital, drawings, loans, profit sharing.

The partners themselves are `apps.company.models.CompanyPartner` (managed in the Company module). Nothing here duplicates that
master data; every table points at it. Money movements are posted to the books through `apps.accounting.services.ledger`;
these tables hold the business record (who, what, why, approval state) and make partner balances cheap to read.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.accounting.models import Account, JournalEntry
from apps.company.models import CompanyPartner, CompanyProfile
from apps.core.models import UUIDModel


class PartnerSettings(UUIDModel):
    """Company-wide partner accounting settings (one row per company, created on first use)."""

    company = models.OneToOneField(CompanyProfile, on_delete=models.PROTECT, related_name="partner_settings")
    # A drawing or capital withdrawal above this amount waits for approval. Zero switches approval off.
    withdrawal_approval_threshold = models.DecimalField(max_digits=16, decimal_places=2, default=100000)
    # Suggested annual rate (percent) for interest on capital and on partners' loans.
    interest_on_capital_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    loan_interest_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)


class PartnerAccount(UUIDModel):
    """What the partner module adds to one CompanyPartner: the linked sign-in user and the agreed capital."""

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="partner_accounts")
    partner = models.OneToOneField(CompanyPartner, on_delete=models.PROTECT, related_name="capital_account")
    # A partner who is also a user can see their own capital account without the partners.view permission.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    agreed_capital = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(agreed_capital__gte=0), name="partner_agreed_capital_not_negative")]


class PartnerTransaction(UUIDModel):
    class Kind(models.TextChoices):
        CAPITAL_INTRODUCED = "capital_introduced", "Capital introduced"
        CAPITAL_WITHDRAWN = "capital_withdrawn", "Capital withdrawn"
        DRAWING = "drawing", "Drawing"
        INTEREST_ON_CAPITAL = "interest_on_capital", "Interest on capital"
        REMUNERATION = "remuneration", "Remuneration"
        LOAN_FROM_PARTNER = "loan_from_partner", "Loan from partner"
        LOAN_REPAYMENT = "loan_repayment", "Loan repayment"
        PROFIT_SHARE_CREDIT = "profit_share_credit", "Profit share credit"
        PROFIT_SHARE_TRANSFER = "profit_share_transfer", "Profit share transferred to capital"

    class Status(models.TextChoices):
        PENDING = "pending_approval", "Waiting for approval"
        POSTED = "posted", "Posted"
        REJECTED = "rejected", "Rejected"
        VOID = "void", "Void"

    class Mode(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        CHEQUE = "cheque", "Cheque"
        UPI = "upi", "UPI"
        CASH = "cash", "Cash"
        OTHER = "other", "Other"

    # Kinds that move money through a bank or cash account (the others are book entries between partner accounts).
    MONEY_KINDS = (Kind.CAPITAL_INTRODUCED, Kind.CAPITAL_WITHDRAWN, Kind.DRAWING, Kind.LOAN_FROM_PARTNER, Kind.LOAN_REPAYMENT)
    # Kinds that can wait for approval.
    WITHDRAWALS = (Kind.CAPITAL_WITHDRAWN, Kind.DRAWING)

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="partner_transactions")
    partner = models.ForeignKey(CompanyPartner, on_delete=models.PROTECT, related_name="transactions")
    number = models.CharField(max_length=120)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    date = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    mode = models.CharField(max_length=14, choices=Mode.choices, blank=True)
    money_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reference = models.CharField(max_length=100, blank=True)
    narration = models.CharField(max_length=300, blank=True)
    # Interest on capital, and a loan's interest: annual simple rate, and the period interest was worked out for.
    rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.POSTED)
    entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.CharField(max_length=300, blank=True)  # why it was rejected or voided
    # The approvals module's request, when one exists (soft reference).
    approval_request_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="partner_tx_amount_positive"),
            models.UniqueConstraint(fields=["company", "number"], name="unique_partner_tx_number"),
        ]
        indexes = [models.Index(fields=["partner", "date"]), models.Index(fields=["kind", "status"])]


class ProfitShareSchedule(UUIDModel):
    """The profit-sharing ratio the LLP agreement sets from a date. The lines always total 100%."""

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="profit_share_schedules")
    effective_from = models.DateField()
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-effective_from"]
        constraints = [models.UniqueConstraint(fields=["company", "effective_from"], name="one_profit_schedule_per_date")]


class ProfitShareLine(UUIDModel):
    schedule = models.ForeignKey(ProfitShareSchedule, on_delete=models.CASCADE, related_name="lines")
    partner = models.ForeignKey(CompanyPartner, on_delete=models.PROTECT, related_name="profit_share_lines")
    percent = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        ordering = ["-percent", "partner__full_name"]
        constraints = [
            models.UniqueConstraint(fields=["schedule", "partner"], name="one_line_per_partner_in_schedule"),
            models.CheckConstraint(condition=Q(percent__gt=0) & Q(percent__lte=100), name="profit_share_percent_range"),
        ]


class ProfitAllocation(UUIDModel):
    """Profit (or loss) of a period shared among the partners by the ratio, posted to their current accounts."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="profit_allocations")
    number = models.CharField(max_length=120, blank=True)  # allocated when it is posted
    period_start = models.DateField()
    period_end = models.DateField()
    schedule = models.ForeignKey(ProfitShareSchedule, on_delete=models.PROTECT, related_name="allocations")
    net_profit = models.DecimalField(max_digits=16, decimal_places=2)  # from the profit and loss statement
    remuneration_charged = models.DecimalField(max_digits=16, decimal_places=2, default=0)  # already in the expenses
    interest_charged = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    other_deductions = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    deduction_note = models.CharField(max_length=300, blank=True)
    distributable = models.DecimalField(max_digits=16, decimal_places=2)
    post_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    reverse_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-period_end", "-created_at"]
        constraints = [models.CheckConstraint(condition=Q(period_end__gte=models.F("period_start")), name="allocation_period_valid")]


class AllocationShare(UUIDModel):
    allocation = models.ForeignKey(ProfitAllocation, on_delete=models.CASCADE, related_name="shares")
    partner = models.ForeignKey(CompanyPartner, on_delete=models.PROTECT, related_name="allocation_shares")
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    amount = models.DecimalField(max_digits=16, decimal_places=2)  # negative when the period made a loss

    class Meta:
        ordering = ["-percent", "partner__full_name"]
        constraints = [models.UniqueConstraint(fields=["allocation", "partner"], name="one_share_per_partner_in_allocation")]
