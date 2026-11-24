"""Sales: customers, invoices and notes, payments received and how they are applied.

Money is always `Decimal`. A document's lines and totals are worked out by `apps.company.services.quotations.build` (the same
code that prices quotations); the result is kept in `record` so listings and reports never re-calculate. Issued documents
are frozen: a draft can be edited, an issued one can only be voided.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core import india
from apps.core.models import UUIDModel
from apps.company.models import CompanyBankAccount, CompanyProfile, IssuedDocument

ZERO = "0.00"


class Customer(UUIDModel):
    class Type(models.TextChoices):
        BUSINESS = "business", "Business"
        INDIVIDUAL = "individual", "Individual"

    class GstTreatment(models.TextChoices):
        REGISTERED = "registered", "Registered business (has a GSTIN)"
        UNREGISTERED = "unregistered", "Unregistered business or consumer"
        OVERSEAS = "overseas", "Overseas"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="customers")
    # Allocated automatically (T2N-CUS-26-3110-0001); never edited or reused.
    reference_code = models.CharField(max_length=60, blank=True, default="", editable=False)
    display_name = models.CharField(max_length=200)
    legal_name = models.CharField(max_length=200, blank=True)
    customer_type = models.CharField(max_length=12, choices=Type.choices, default=Type.BUSINESS)
    contact_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    gst_treatment = models.CharField(max_length=14, choices=GstTreatment.choices, default=GstTreatment.UNREGISTERED)
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    state_code = models.CharField(max_length=2, blank=True)
    postal_code = models.CharField(max_length=10, blank=True)
    country = models.CharField(max_length=60, default="India")
    # Days from the invoice date until payment is due, when the customer has agreed terms.
    payment_terms_days = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "reference_code"], condition=~Q(reference_code=""),
                                    name="unique_customer_reference_code"),
            models.UniqueConstraint(fields=["gstin"], condition=Q(is_active=True) & ~Q(gstin=""),
                                    name="unique_active_customer_gstin"),
        ]

    def __str__(self):
        return self.display_name

    @property
    def address_lines(self):
        city_line = " ".join(p for p in (self.city, self.postal_code) if p)
        lines = [self.address_line1, self.address_line2, city_line, ", ".join(p for p in (self.state, self.country) if p)]
        return [line for line in lines if line]

    def clean(self):
        errors = {}
        self.gstin = (self.gstin or "").strip().upper()
        self.pan = (self.pan or "").strip().upper()
        if not (self.display_name or "").strip():
            errors["display_name"] = "Enter the customer name."
        if self.gstin:
            problem = india.validate_gstin(self.gstin, self.pan)
            if problem:
                errors["gstin"] = problem
        elif self.gst_treatment == self.GstTreatment.REGISTERED:
            errors["gstin"] = "Enter the GSTIN of a registered business."
        if self.pan and not india.PAN_RE.match(self.pan):
            errors["pan"] = "Enter a valid 10-character PAN."
        if self.gstin and not errors.get("gstin") and not self.state_code:
            self.state_code = self.gstin[:2]  # the first two digits of a GSTIN are the state
        if self.state_code and self.state_code not in india.GST_STATES:
            errors["state_code"] = "Unknown GST state code."
        elif self.state_code and not self.state:
            self.state = india.GST_STATES[self.state_code]
        if self.gstin and self.state_code and self.gstin[:2] != self.state_code and "gstin" not in errors:
            errors["state_code"] = "The state does not match the first two digits of the GSTIN."
        clash = Customer.objects.exclude(pk=self.pk).filter(company_id=self.company_id, display_name__iexact=self.display_name.strip())
        if self.is_active and clash.filter(is_active=True).exists():
            errors["display_name"] = "An active customer with this name already exists."
        if errors:
            raise ValidationError(errors)


class SalesDocument(UUIDModel):
    class Kind(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        PROFORMA = "proforma_invoice", "Proforma invoice"
        CREDIT_NOTE = "credit_note", "Credit note"
        DEBIT_NOTE = "debit_note", "Debit note"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        VOID = "void", "Void"

    # Kinds that a customer owes money on, and so that payments are applied to.
    RECEIVABLE = ("invoice", "debit_note")

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="sales_documents")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="documents")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    number = models.CharField(max_length=120, blank=True)  # the reference number, from issue
    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="INR")
    form = models.JSONField(default=dict, blank=True)    # what was entered: items, notes, terms ...
    record = models.JSONField(default=dict, blank=True)  # worked-out lines and totals
    taxable_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    against = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="notes")
    reason = models.CharField(max_length=300, blank=True)
    # The project this sale belongs to (apps.projects.Project id); its lines in the books carry the same tag.
    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    # The recurring-invoice profile that generated this document (soft link, see RecurringInvoice) and the scheduled run
    # date it was generated for: one document per (profile, run date), enforced below.
    recurring_id = models.UUIDField(null=True, blank=True, db_index=True)
    recurring_run_date = models.DateField(null=True, blank=True)
    issued_document = models.OneToOneField(IssuedDocument, null=True, blank=True, on_delete=models.PROTECT, related_name="sales_document")
    source_quotation = models.ForeignKey(IssuedDocument, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    void_reason = models.CharField(max_length=300, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-issue_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_sales_document_number"),
            models.CheckConstraint(condition=Q(grand_total__gte=0), name="sales_total_not_negative"),
            models.UniqueConstraint(fields=["recurring_id", "recurring_run_date"], condition=Q(recurring_id__isnull=False),
                                    name="unique_recurring_run"),
        ]
        indexes = [models.Index(fields=["kind", "status"]), models.Index(fields=["customer", "-issue_date"]),
                   models.Index(fields=["due_date"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.number or '(draft)'}"

    @property
    def is_receivable(self):
        return self.kind in self.RECEIVABLE


class Payment(UUIDModel):
    class Mode(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", "Bank transfer (NEFT/RTGS/IMPS)"
        UPI = "upi", "UPI"
        CHEQUE = "cheque", "Cheque"
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        RECORDED = "recorded", "Recorded"
        VOID = "void", "Void"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="customer_payments")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="payments")
    reference_code = models.CharField(max_length=60, blank=True, default="")
    date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    mode = models.CharField(max_length=14, choices=Mode.choices, default=Mode.BANK_TRANSFER)
    reference = models.CharField(max_length=100, blank=True)  # cheque number, UTR, UPI id ...
    deposit_account = models.ForeignKey(CompanyBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    # Set only when this payment was created from a bank statement line on a ledger account with no Company Bank Account of
    # its own (and not the default bank account): posts straight to this ledger account instead of through deposit_account.
    bank_ledger_account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    notes = models.CharField(max_length=300, blank=True)
    # The project this money belongs to (apps.projects.Project id); its lines in the books carry the same tag.
    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.RECORDED)
    void_reason = models.CharField(max_length=300, blank=True)
    # Voiding a payment above an approval rule waits for approval: the payment stays recorded (and counted) meanwhile.
    void_requested = models.BooleanField(default=False)
    void_request_reason = models.CharField(max_length=300, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "reference_code"], condition=~Q(reference_code=""),
                                    name="unique_payment_reference_code"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
        ]

    def __str__(self):
        return f"{self.reference_code or 'Payment'} {self.amount}"


class PaymentAllocation(UUIDModel):
    """Part of a payment applied to one invoice or debit note."""

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    document = models.ForeignKey(SalesDocument, on_delete=models.PROTECT, related_name="payment_allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_amount_positive")]


class CreditApplication(UUIDModel):
    """Part of a credit note used against an invoice or debit note."""

    credit_note = models.ForeignKey(SalesDocument, on_delete=models.PROTECT, related_name="credit_applications")
    document = models.ForeignKey(SalesDocument, on_delete=models.PROTECT, related_name="credits_received")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="credit_application_positive")]


class Quotation(UUIDModel):
    """A quotation held back before it is issued: it is above an approval rule (pending_approval), or an approver sent it
    back (draft). Issuing creates the numbered PDF document (`apps.company` IssuedDocument); a quotation that no rule holds is
    issued at once and never appears here."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        ISSUED = "issued", "Issued"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="held_quotations")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    issue_date = models.DateField()
    customer_name = models.CharField(max_length=200)
    content = models.JSONField(default=dict)  # the quotation exactly as entered: customer, items, scope ...
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="INR")
    submitted_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)
    issued_document = models.OneToOneField(IssuedDocument, null=True, blank=True, on_delete=models.PROTECT, related_name="held_quotation")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return f"Quotation for {self.customer_name} ({self.get_status_display()})"


class RecurringInvoice(UUIDModel):
    """A profile that raises the same invoice again and again on a schedule.

    The schedule is anchored on `start_date`: the n-th run is `start_date` plus n periods, so a profile that starts on the
    31st bills on the last day of shorter months and returns to the 31st afterwards. `next_run_date` is the next scheduled
    run (empty once stopped or completed). Editing a profile changes only the invoices still to come.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        STOPPED = "stopped", "Stopped"
        COMPLETED = "completed", "Completed"

    class Mode(models.TextChoices):
        DRAFT = "draft", "Create as draft"
        ISSUE = "issue", "Create and issue"

    class Frequency(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        HALF_YEARLY = "half_yearly", "Half-yearly"
        YEARLY = "yearly", "Yearly"
        CUSTOM = "custom", "Custom"

    class Unit(models.TextChoices):
        DAYS = "days", "Days"
        MONTHS = "months", "Months"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="recurring_invoices")
    reference_code = models.CharField(max_length=60, blank=True, default="", editable=False)  # T2N-RIN-26-1511-0001
    name = models.CharField(max_length=150)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="recurring_invoices")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    mode = models.CharField(max_length=6, choices=Mode.choices, default=Mode.DRAFT)
    frequency = models.CharField(max_length=12, choices=Frequency.choices, default=Frequency.MONTHLY)
    interval_unit = models.CharField(max_length=6, choices=Unit.choices, blank=True, default="")  # custom only
    interval_count = models.PositiveSmallIntegerField(null=True, blank=True)                        # custom only
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    max_occurrences = models.PositiveSmallIntegerField(null=True, blank=True)
    next_run_date = models.DateField(null=True, blank=True)
    last_run_date = models.DateField(null=True, blank=True)
    occurrences_done = models.PositiveIntegerField(default=0)
    # Days from the invoice date to the due date; empty = the customer's or company's payment terms.
    due_days = models.PositiveSmallIntegerField(null=True, blank=True)
    # What each invoice is made from: items, subject, customer reference, bank-details and signature switches.
    form = models.JSONField(default=dict, blank=True)
    # Notes and terms are copied onto every invoice only when `apply_terms` is on.
    apply_terms = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)
    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    last_error = models.CharField(max_length=500, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_generated_at = models.DateTimeField(null=True, blank=True)
    stop_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "reference_code"], condition=~Q(reference_code=""),
                                    name="unique_recurring_reference_code"),
        ]
        indexes = [models.Index(fields=["status", "next_run_date"])]

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"


class ReminderRule(UUIDModel):
    """When a payment reminder becomes due, as days from the invoice's due date (-3 = three days before, 7 = a week after),
    and the wording used. `{placeholders}` in the subject and body are filled in when a reminder is sent."""

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="reminder_rules")
    days_offset = models.SmallIntegerField()
    subject_template = models.CharField(max_length=200)
    body_template = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["days_offset"]
        constraints = [models.UniqueConstraint(fields=["company", "days_offset"], name="unique_reminder_rule_offset")]

    def __str__(self):
        return f"Reminder {self.days_offset:+d} days"


class PaymentReminder(UUIDModel):
    """The record of one reminder being sent or skipped for an invoice. "Sent" means the text was prepared and recorded here;
    when email is set up it was also emailed (the outcome is in apps.emails' log, kind "payment_reminder"), otherwise the
    user sent it themselves. One row per (invoice, rule offset), so a rule never reminds the same invoice twice, by hand or
    by the automatic daily job."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        SKIPPED = "skipped", "Skipped"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="payment_reminders")
    document = models.ForeignKey(SalesDocument, on_delete=models.PROTECT, related_name="reminders")
    rule = models.ForeignKey(ReminderRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="reminders")
    days_offset = models.SmallIntegerField()
    status = models.CharField(max_length=8, choices=Status.choices)
    subject = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)
    to_email = models.CharField(max_length=254, blank=True)
    amount_due = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    due_date = models.DateField(null=True, blank=True)
    days_overdue = models.SmallIntegerField(default=0)
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["document", "days_offset"], name="unique_reminder_per_rule")]
