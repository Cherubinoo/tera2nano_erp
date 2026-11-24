"""Purchases: vendors, purchase requests, vendor quotations, purchase orders, vendor bills and vendor payments.

Money is always `Decimal`. A document's lines and totals are worked out once by `services.pricing.price` and kept in
`record`, so lists and reports never re-calculate. A bill that has been recorded and a payment that has been made are
frozen: they can only be voided (which reverses their entries in the books).
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.company.models import CompanyBankAccount, CompanyProfile, IssuedDocument
from apps.core import crypto, india
from apps.core.models import UUIDModel


def _user_fk():
    return models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")


class Vendor(UUIDModel):
    class GstTreatment(models.TextChoices):
        REGISTERED = "registered", "Registered business (has a GSTIN)"
        UNREGISTERED = "unregistered", "Unregistered business or individual"
        OVERSEAS = "overseas", "Overseas"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="vendors")
    code = models.CharField(max_length=60, blank=True, default="", editable=False)  # VEN-26-1211-0001, never edited
    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    gst_treatment = models.CharField(max_length=14, choices=GstTreatment.choices, default=GstTreatment.UNREGISTERED)
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    state_code = models.CharField(max_length=2, blank=True)  # place of supply for purchases from this vendor
    postal_code = models.CharField(max_length=10, blank=True)
    country = models.CharField(max_length=60, default="India")
    payment_terms_days = models.PositiveSmallIntegerField(null=True, blank=True)
    tds_section = models.CharField(max_length=20, blank=True)
    tds_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # What was owed to this vendor before the books started. Posted once, against accounts payable, only when given.
    opening_balance = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    opening_balance_date = models.DateField(null=True, blank=True)
    bank_account_holder = models.CharField(max_length=150, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account_encrypted = models.TextField(blank=True, default="", editable=False)  # encrypted at rest
    bank_account_last4 = models.CharField(max_length=4, blank=True, default="", editable=False)
    bank_ifsc = models.CharField(max_length=11, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], condition=~Q(code=""), name="unique_vendor_code"),
            models.UniqueConstraint(fields=["gstin"], condition=Q(is_active=True) & ~Q(gstin=""), name="unique_active_vendor_gstin"),
            models.CheckConstraint(condition=Q(opening_balance__gte=0), name="vendor_opening_not_negative"),
        ]

    def __str__(self):
        return self.name

    @property
    def bank_account_number(self):
        """The full account number (decrypted); show it only to people who manage purchases."""
        return crypto.decrypt(self.bank_account_encrypted) if self.bank_account_encrypted else ""

    def set_bank_account_number(self, number):
        number = (number or "").strip().replace(" ", "")
        if number and not india.ACCOUNT_RE.match(number):
            raise ValidationError({"bank_account_number": "Enter a valid bank account number (6 to 20 digits)."})
        self.bank_account_encrypted = crypto.encrypt(number) if number else ""
        self.bank_account_last4 = number[-4:]

    @property
    def masked_bank_account(self):
        return f"XXXXXX{self.bank_account_last4}" if self.bank_account_last4 else ""

    @property
    def address_lines(self):
        city_line = " ".join(p for p in (self.city, self.postal_code) if p)
        lines = [self.address_line1, self.address_line2, city_line, ", ".join(p for p in (self.state, self.country) if p)]
        return [line for line in lines if line]

    def clean(self):
        errors = {}
        self.gstin = (self.gstin or "").strip().upper()
        self.pan = (self.pan or "").strip().upper()
        self.bank_ifsc = (self.bank_ifsc or "").strip().upper()
        if not (self.name or "").strip():
            errors["name"] = "Enter the vendor name."
        if self.gstin:
            problem = india.validate_gstin(self.gstin, self.pan)
            if problem:
                errors["gstin"] = problem
        elif self.gst_treatment == self.GstTreatment.REGISTERED:
            errors["gstin"] = "Enter the GSTIN of a registered vendor."
        if self.pan and not india.PAN_RE.match(self.pan):
            errors["pan"] = "Enter a valid 10-character PAN."
        if self.gstin and "gstin" not in errors and not self.state_code:
            self.state_code = self.gstin[:2]
        if self.state_code and self.state_code not in india.GST_STATES:
            errors["state_code"] = "Unknown GST state code."
        elif self.state_code and not self.state:
            self.state = india.GST_STATES[self.state_code]
        if self.gstin and self.state_code and self.gstin[:2] != self.state_code and "gstin" not in errors and "state_code" not in errors:
            errors["state_code"] = "The state does not match the first two digits of the GSTIN."
        if self.postal_code and not india.PIN_RE.match(self.postal_code) and (self.country or "India") == "India":
            errors["postal_code"] = "Enter a valid 6-digit PIN code."
        if self.bank_ifsc and not india.IFSC_RE.match(self.bank_ifsc):
            errors["bank_ifsc"] = "Enter a valid IFSC code, such as HDFC0001234."
        if self.tds_section and self.tds_rate is None:
            errors["tds_rate"] = "Enter the TDS rate for this section."
        if self.tds_rate is not None and not (0 <= self.tds_rate <= 100):
            errors["tds_rate"] = "The TDS rate must be between 0 and 100."
        if self.tds_rate is not None and not self.tds_section:
            errors["tds_section"] = "Enter the TDS section (for example 194C)."
        if self.opening_balance is not None and self.opening_balance < 0:
            errors["opening_balance"] = "The opening balance cannot be negative."
        if self.is_active and self.gstin and "gstin" not in errors and Vendor.objects.exclude(pk=self.pk).filter(
                gstin=self.gstin, is_active=True).exists():
            errors["gstin"] = "An active vendor with this GSTIN already exists."
        clash = Vendor.objects.exclude(pk=self.pk).filter(company_id=self.company_id, name__iexact=(self.name or "").strip())
        if self.is_active and clash.filter(is_active=True).exists():
            errors["name"] = "An active vendor with this name already exists."
        if errors:
            raise ValidationError(errors)


class PurchaseRequest(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CONVERTED = "converted", "Converted to order"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="purchase_requests")
    number = models.CharField(max_length=120, blank=True, default="")  # PR/26/1211/00001, allocated when created
    title = models.CharField(max_length=200)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    requested_by_name = models.CharField(max_length=150, blank=True)
    request_date = models.DateField()
    needed_by = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    items = models.JSONField(default=list, blank=True)  # [{description, hsn_sac, unit, quantity, est_rate}]
    estimated_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    decision_note = models.CharField(max_length=300, blank=True)
    converted_order_id = models.UUIDField(null=True, blank=True)
    created_by = _user_fk()

    class Meta:
        ordering = ["-request_date", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_purchase_request_number")]

    def __str__(self):
        return f"{self.number} {self.title}"


class VendorQuotation(UUIDModel):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="vendor_quotations")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="quotations")
    request = models.ForeignKey(PurchaseRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="quotations")
    number = models.CharField(max_length=120, blank=True, default="")  # VQ/26/1211/00001: our reference
    vendor_ref = models.CharField(max_length=100, blank=True)  # the vendor's own quotation number
    quote_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECEIVED)
    form = models.JSONField(default=dict, blank=True)
    record = models.JSONField(default=dict, blank=True)
    taxable_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    delivery_days = models.PositiveSmallIntegerField(null=True, blank=True)
    payment_terms = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    converted_order_id = models.UUIDField(null=True, blank=True)
    created_by = _user_fk()

    class Meta:
        ordering = ["-quote_date", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_vendor_quotation_number")]


class PurchaseOrder(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        ISSUED = "issued", "Issued"
        PARTIALLY_RECEIVED = "partially_received", "Partially received"
        RECEIVED = "received", "Received"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    OPEN = ("issued", "partially_received")  # sent to the vendor, goods still expected

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="purchase_orders")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="orders")
    number = models.CharField(max_length=120, blank=True, default="")  # the reference number, from issue
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    order_date = models.DateField()
    expected_delivery = models.DateField(null=True, blank=True)
    form = models.JSONField(default=dict, blank=True)    # what was entered: items, terms, notes
    record = models.JSONField(default=dict, blank=True)  # worked-out lines and totals
    taxable_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    delivery_address = models.CharField(max_length=500, blank=True)
    reference = models.CharField(max_length=100, blank=True)  # the vendor's quotation or our request number
    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    source_request = models.ForeignKey(PurchaseRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    source_quotation = models.ForeignKey(VendorQuotation, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    issued_document = models.OneToOneField(IssuedDocument, null=True, blank=True, on_delete=models.PROTECT, related_name="purchase_order")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = _user_fk()
    approved_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    created_by = _user_fk()
    issued_by = _user_fk()

    class Meta:
        ordering = ["-order_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_purchase_order_number"),
            models.CheckConstraint(condition=Q(grand_total__gte=0), name="purchase_order_total_not_negative"),
        ]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["vendor", "-order_date"])]

    def __str__(self):
        return f"PO {self.number or '(draft)'}"


class PurchaseReceipt(UUIDModel):
    """Goods received against an order: how much of which line arrived on a date."""

    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipts")
    date = models.DateField()
    lines = models.JSONField(default=list)  # [{"line": 0, "quantity": "5"}]
    notes = models.CharField(max_length=300, blank=True)
    received_by = _user_fk()

    class Meta:
        ordering = ["date", "created_at"]


class VendorBill(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        OPEN = "open", "Recorded"
        VOID = "void", "Void"

    class Itc(models.TextChoices):
        ELIGIBLE = "eligible", "Eligible: claim input tax credit"
        INELIGIBLE = "ineligible", "Ineligible: no credit claimed"
        BLOCKED = "blocked", "Blocked credit (Section 17(5))"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="vendor_bills")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="bills")
    order = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT, related_name="bills")
    number = models.CharField(max_length=120, blank=True, default="")  # our reference, BILL/26/1211/00001, from recording
    vendor_bill_no = models.CharField(max_length=100)  # the number printed on the vendor's invoice
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    bill_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    form = models.JSONField(default=dict, blank=True)
    record = models.JSONField(default=dict, blank=True)
    taxable_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    itc_status = models.CharField(max_length=12, choices=Itc.choices, default=Itc.ELIGIBLE)
    tds_section = models.CharField(max_length=20, blank=True)
    tds_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tds_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    net_payable = models.DecimalField(max_digits=16, decimal_places=2, default=0)  # grand total less TDS withheld
    expense_account_id = models.UUIDField(null=True, blank=True)  # default account for lines that name none
    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    notes = models.TextField(blank=True)
    void_reason = models.CharField(max_length=300, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)  # when it was sent for approval
    decision_note = models.CharField(max_length=300, blank=True)  # the approver's comment, or why it was sent back
    created_by = _user_fk()
    recorded_by = _user_fk()

    class Meta:
        ordering = ["-bill_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_vendor_bill_number"),
            # The same vendor invoice cannot be booked twice (a voided one may be booked again).
            models.UniqueConstraint(fields=["vendor", "vendor_bill_no"], condition=~Q(status="void") & ~Q(vendor_bill_no=""),
                                    name="unique_live_vendor_invoice_number"),
            models.CheckConstraint(condition=Q(grand_total__gte=0), name="vendor_bill_total_not_negative"),
        ]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["vendor", "-bill_date"]), models.Index(fields=["due_date"])]

    def __str__(self):
        return f"Bill {self.number or '(draft)'}"


class VendorPayment(UUIDModel):
    class Mode(models.TextChoices):
        BANK = "bank", "Bank transfer (NEFT/RTGS/IMPS)"
        CASH = "cash", "Cash"
        UPI = "upi", "UPI"
        CHEQUE = "cheque", "Cheque"

    class Status(models.TextChoices):
        PENDING_APPROVAL = "pending_approval", "Pending approval"  # above an approval rule: not numbered, applied or posted yet
        REJECTED = "rejected", "Rejected"                          # the approver refused it (or the requester withdrew it)
        RECORDED = "recorded", "Recorded"
        VOID = "void", "Void"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="vendor_payments")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="payments")
    number = models.CharField(max_length=120, blank=True, default="")  # VPY/26/1211/00001
    date = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.BANK)
    reference = models.CharField(max_length=100, blank=True)  # UTR, cheque number, UPI reference
    paid_from = models.ForeignKey(CompanyBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    # Set only when this payment was created from a bank statement line on a ledger account with no Company Bank Account of
    # its own (and not the default bank account): posts straight to this ledger account instead of through paid_from.
    bank_ledger_account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    notes = models.CharField(max_length=300, blank=True)
    project_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECORDED)
    void_reason = models.CharField(max_length=300, blank=True)
    # While the payment waits for approval: how it will be applied, [[bill id or "opening", amount], ...]. Applied for real
    # (and checked again against what is still owed) only when it is approved.
    plan = models.JSONField(default=list, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    # Voiding a payment above an approval rule waits for approval: the payment stays recorded (and counted) meanwhile.
    void_requested = models.BooleanField(default=False)
    void_request_reason = models.CharField(max_length=300, blank=True)
    created_by = _user_fk()

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_vendor_payment_number"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="vendor_payment_amount_positive"),
        ]

    def __str__(self):
        return f"{self.number or 'Payment'} {self.amount}"


class VendorPaymentAllocation(UUIDModel):
    """Part of a payment applied to one bill (or, with no bill, to the vendor's opening balance)."""

    payment = models.ForeignKey(VendorPayment, on_delete=models.PROTECT, related_name="allocations")
    bill = models.ForeignKey(VendorBill, null=True, blank=True, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    # "" for what was applied when the payment was made; otherwise the id of a later "apply advance" posting.
    batch = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="vendor_allocation_positive")]
