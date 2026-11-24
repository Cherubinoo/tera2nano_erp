"""The books: chart of accounts, journal entries, expense and income vouchers, bank reconciliation.

Double entry: every journal entry's debits equal its credits. Posted entries are never edited; a mistake is corrected by a
reversing entry that mirrors the original. Amounts are exact decimals.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.company.models import CompanyBankAccount, CompanyProfile, FinancialYear
from apps.core.models import UUIDModel
from apps.core.storage import UploadTo, private_storage


class Account(UUIDModel):
    class Type(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    class Subtype(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank", "Bank"
        RECEIVABLE = "receivable", "Accounts receivable"
        OTHER_CURRENT_ASSET = "other_current_asset", "Other current asset"
        TAX_ASSET = "tax_asset", "Tax receivable (GST input, TDS)"
        FIXED_ASSET = "fixed_asset", "Fixed asset"
        ACCUMULATED_DEPRECIATION = "accumulated_depreciation", "Accumulated depreciation"
        PAYABLE = "payable", "Accounts payable"
        TAX_LIABILITY = "tax_liability", "Tax payable (GST output, TDS)"
        OTHER_CURRENT_LIABILITY = "other_current_liability", "Other current liability"
        LONG_TERM_LIABILITY = "long_term_liability", "Long-term liability"
        PARTNER_CAPITAL = "partner_capital", "Partners' capital"
        PARTNER_CURRENT = "partner_current", "Partners' current account"
        RETAINED = "retained", "Retained earnings"
        INCOME = "income", "Sales / operating income"
        OTHER_INCOME = "other_income", "Other income"
        DIRECT_COST = "direct_cost", "Direct cost of services"
        EXPENSE = "expense", "Operating expense"
        DEPRECIATION = "depreciation", "Depreciation"
        OTHER_EXPENSE = "other_expense", "Other expense"

    # Which type each subtype belongs to.
    TYPE_OF = {
        "cash": "asset", "bank": "asset", "receivable": "asset", "other_current_asset": "asset", "tax_asset": "asset",
        "fixed_asset": "asset", "accumulated_depreciation": "asset", "payable": "liability", "tax_liability": "liability",
        "other_current_liability": "liability", "long_term_liability": "liability", "partner_capital": "equity",
        "partner_current": "equity", "retained": "equity", "income": "income", "other_income": "income",
        "direct_cost": "expense", "expense": "expense", "depreciation": "expense", "other_expense": "expense",
    }
    DEBIT_TYPES = ("asset", "expense")

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="ledger_accounts")
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=150)
    type = models.CharField(max_length=10, choices=Type.choices)
    subtype = models.CharField(max_length=30, choices=Subtype.choices)
    # Set on the accounts the system posts to ("receivable", "sales" ...); these cannot be removed or change kind.
    system_key = models.CharField(max_length=40, blank=True)
    description = models.CharField(max_length=300, blank=True)
    # A bank ledger account is tied to one of the company's bank accounts (see Company > Bank Accounts).
    bank_account = models.OneToOneField(CompanyBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_account")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_account_code"),
            models.UniqueConstraint(fields=["company", "system_key"], condition=~Q(system_key=""), name="unique_system_account"),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def is_system(self):
        return bool(self.system_key)

    @property
    def is_debit_normal(self):
        return self.type in self.DEBIT_TYPES

    def clean(self):
        errors = {}
        self.code = (self.code or "").strip()
        self.name = (self.name or "").strip()
        if not self.code or not self.code.isalnum():
            errors["code"] = "Use letters and digits only, for example 5250."
        if not self.name:
            errors["name"] = "Enter the account name."
        if self.subtype and self.TYPE_OF.get(self.subtype) != self.type:
            errors["subtype"] = "This kind of account does not belong to that type."
        if Account.objects.exclude(pk=self.pk).filter(company_id=self.company_id, code=self.code).exists():
            errors["code"] = "This account code is already used."
        if errors:
            raise ValidationError(errors)


class JournalEntry(UUIDModel):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual journal"
        INVOICE = "invoice", "Invoice"
        DEBIT_NOTE = "debit_note", "Debit note"
        CREDIT_NOTE = "credit_note", "Credit note"
        PAYMENT = "payment", "Payment received"
        EXPENSE = "expense", "Expense"
        INCOME = "income", "Income"
        TRANSFER = "transfer", "Transfer"
        REVERSAL = "reversal", "Reversal"
        OPENING = "opening", "Opening balance"
        VENDOR_BILL = "vendor_bill", "Vendor bill"
        VENDOR_PAYMENT = "vendor_payment", "Vendor payment"
        PAYROLL = "payroll", "Payroll"
        PARTNER = "partner", "Partner transaction"
        DEPRECIATION = "depreciation", "Depreciation"
        FIXED_ASSET = "fixed_asset", "Fixed asset"

    class Status(models.TextChoices):
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="journal_entries")
    date = models.DateField()
    # Journal number for manual entries (T2N/JV/26/1211/00001); automatic postings carry the source document's number.
    reference = models.CharField(max_length=120, blank=True)
    narration = models.CharField(max_length=500, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    source_id = models.CharField(max_length=64, blank=True)  # the id of the invoice, payment, voucher ... that caused it
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.POSTED)
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")
    financial_year = models.ForeignKey(FinancialYear, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name_plural = "journal entries"
        constraints = [
            # One automatic posting per business event, so retrying or rebuilding can never double-post.
            models.UniqueConstraint(fields=["company", "source", "source_id"], condition=~Q(source_id="") & ~Q(source="reversal"),
                                    name="one_posting_per_source"),
        ]
        indexes = [models.Index(fields=["date"]), models.Index(fields=["source", "source_id"])]

    def __str__(self):
        return f"{self.reference or self.get_source_display()} {self.date}"


class JournalLine(UUIDModel):
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    memo = models.CharField(max_length=300, blank=True)
    # The customer or vendor this line is about, so receivables and payables can be traced to a party.
    party_type = models.CharField(max_length=20, blank=True)
    party_id = models.CharField(max_length=64, blank=True)
    reconciliation = models.ForeignKey("BankReconciliation", null=True, blank=True, on_delete=models.PROTECT, related_name="lines")
    # The bank statement line(s) this book line was matched to (see BankMatch); empty while it is not yet seen on a statement.
    bank_match = models.ForeignKey("BankMatch", null=True, blank=True, on_delete=models.SET_NULL, related_name="journal_lines")
    # The project this line belongs to (apps.projects.Project id), so project profit and loss comes straight from the books.
    project_id = models.UUIDField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(debit__gte=0) & Q(credit__gte=0), name="journal_line_not_negative"),
            # A line is a debit or a credit, never both and never empty.
            models.CheckConstraint(condition=(Q(debit__gt=0) & Q(credit=0)) | (Q(credit__gt=0) & Q(debit=0)), name="journal_line_one_side"),
        ]
        indexes = [models.Index(fields=["account"])]


class Voucher(UUIDModel):
    """Money paid out (expense) or received (other income) outside the sales and purchase flows."""

    class Kind(models.TextChoices):
        EXPENSE = "expense", "Expense"
        INCOME = "income", "Income"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"                              # an expense not yet recorded (or sent back by an approver)
        PENDING_APPROVAL = "pending_approval", "Pending approval"   # waiting for the approvals module; nothing is posted yet
        RECORDED = "recorded", "Recorded"
        VOID = "void", "Void"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="vouchers")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    number = models.CharField(max_length=120, blank=True)
    date = models.DateField()
    category = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")  # the expense or income account
    money_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")  # cash or bank it moved through
    party_name = models.CharField(max_length=200, blank=True)  # payee or payer
    reference = models.CharField(max_length=100, blank=True)  # bill, receipt or UTR number
    notes = models.CharField(max_length=300, blank=True)
    amount = models.DecimalField(max_digits=16, decimal_places=2)  # before tax
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=16, decimal_places=2)
    gst_split = models.JSONField(default=list, blank=True)  # [{"label": "CGST @ 9%", "account": "gst_cgst_in", "amount": "90.00"}]
    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="voucher")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECORDED)
    void_reason = models.CharField(max_length=300, blank=True)
    # What the approver wrote (or "Rejected: reason"); shown on a draft that came back.
    decision_note = models.CharField(max_length=300, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0) & Q(total__gt=0), name="voucher_amount_positive"),
            models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_voucher_number"),
        ]


class BankReconciliation(UUIDModel):
    """A bank or cash account checked against the statement: the lines ticked off, and the statement's closing balance."""

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="reconciliations")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="reconciliations")
    statement_date = models.DateField()
    statement_balance = models.DecimalField(max_digits=16, decimal_places=2)
    book_balance = models.DecimalField(max_digits=16, decimal_places=2)  # the ledger balance of the cleared lines
    notes = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-statement_date", "-created_at"]


# --------------------------------------------------------------------------- bank statements and matching

class BankStatement(UUIDModel):
    """A statement file uploaded for one bank account. The file is kept privately; its rows become BankStatementLines when
    imported. A statement stays a draft between the upload and the column-mapping step."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Uploaded, not imported yet"
        IMPORTED = "imported", "Imported"

    class Format(models.TextChoices):
        CSV = "csv", "CSV"
        XLSX = "xlsx", "Excel"
        XLS = "xls", "Excel (.xls)"
        PDF = "pdf", "PDF"
        OFX = "ofx", "OFX"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="bank_statements")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="statements")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    format = models.CharField(max_length=4, choices=Format.choices)
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=UploadTo("bank-statements"), storage=private_storage, max_length=255)
    file_size = models.PositiveBigIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64)
    # The column mapping used (remembered for the account's next statement): {"header_row": 0, "date": 0, ...}.
    mapping = models.JSONField(default=dict, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    closing_balance = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    rows_read = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)   # blank, heading and total rows
    problems = models.JSONField(default=list, blank=True)    # [{"row": 12, "reason": "..."}]
    imported_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]


class BankMatch(UUIDModel):
    """Statement line(s) and book line(s) that are the same money: one line to one entry, one deposit to several payments,
    several lines to one entry. A match with no statement line marks book lines as cleared before the imported statements."""

    class Kind(models.TextChoices):
        SUGGESTED = "suggested", "Suggestion accepted"
        MANUAL = "manual", "Matched by hand"
        CREATED = "created", "Entry made from the statement line"
        CLEARED = "cleared", "Cleared before the imported statements"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="bank_matches")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="bank_matches")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.MANUAL)
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "bank matches"


class BankStatementLine(UUIDModel):
    """One transaction on a bank statement. Amount is signed: positive money in (deposit), negative money out (withdrawal)."""

    class Status(models.TextChoices):
        UNMATCHED = "unmatched", "To match"
        MATCHED = "matched", "Matched"
        IGNORED = "ignored", "Ignored"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="+")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="statement_lines")
    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="lines")
    row_number = models.PositiveIntegerField()  # the row in the file (1 = first row), for the import summary
    seq = models.PositiveIntegerField(default=0)  # position in date order within the statement (files may run newest first)
    date = models.DateField()
    description = models.CharField(max_length=500, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    balance = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    # Same date, amount, narration and reference (plus the occurrence number) = the same line, so re-importing an
    # overlapping statement never adds it twice.
    fingerprint = models.CharField(max_length=80)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNMATCHED)
    match = models.ForeignKey(BankMatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="statement_lines")
    ignore_reason = models.CharField(max_length=300, blank=True)
    # Set when "reconciled up to" a date covered this line; it can then no longer be unmatched or changed.
    reconciliation = models.ForeignKey(BankReconciliation, null=True, blank=True, on_delete=models.SET_NULL, related_name="statement_lines")
    # The record made from this line ("vendor_payment", id) when it could not be matched at once (it waits for approval).
    created_source = models.CharField(max_length=20, blank=True)
    created_source_id = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["date", "seq"]
        constraints = [
            models.UniqueConstraint(fields=["account", "fingerprint"], name="one_statement_line_per_fingerprint"),
            models.CheckConstraint(condition=~Q(amount=0), name="statement_line_not_zero"),
        ]
        indexes = [models.Index(fields=["account", "status"]), models.Index(fields=["account", "date"])]


# --------------------------------------------------------------------------- fixed assets

class AssetCategory(UUIDModel):
    """A kind of fixed asset with the depreciation terms and the three ledger accounts its postings use."""

    class Method(models.TextChoices):
        SLM = "slm", "Straight line (Companies Act)"
        WDV = "wdv", "Written-down value (income-tax block rate)"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="asset_categories")
    name = models.CharField(max_length=120)
    method = models.CharField(max_length=3, choices=Method.choices, default=Method.SLM)
    # Straight line uses the useful life; written-down value uses the rate. Both may be kept so the method can be switched.
    useful_life_years = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    rate_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    salvage_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    # Written-down value only: in the year an asset is put to use for fewer than 180 days, charge half the rate (income-tax rule).
    half_year_rule = models.BooleanField(default=False)
    cost_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    accumulated_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "asset categories"
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_asset_category_name")]

    def __str__(self):
        return self.name


class FixedAsset(UUIDModel):
    class Status(models.TextChoices):
        IN_USE = "in_use", "In use"
        DISPOSED = "disposed", "Disposed"
        SOLD = "sold", "Sold"
        WRITTEN_OFF = "written_off", "Written off"

    class Acquisition(models.TextChoices):
        PENDING = "pending", "Not in the books yet"
        POSTED = "posted", "Posted from here"
        EXISTING = "existing", "Already in the books"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="fixed_assets")
    code = models.CharField(max_length=120)
    name = models.CharField(max_length=200)
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name="assets")
    purchase_date = models.DateField()
    put_to_use_date = models.DateField()
    cost = models.DecimalField(max_digits=16, decimal_places=2)  # including GST that cannot be claimed back
    # The depreciation terms, copied from the category when the asset is created (and changeable until depreciation is posted).
    method = models.CharField(max_length=3, choices=AssetCategory.Method.choices)
    useful_life_years = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    rate_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    salvage_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    half_year_rule = models.BooleanField(default=False)
    vendor_id = models.UUIDField(null=True, blank=True)
    vendor_name = models.CharField(max_length=200, blank=True)
    bill_id = models.UUIDField(null=True, blank=True)
    bill_number = models.CharField(max_length=120, blank=True)
    location = models.CharField(max_length=200, blank=True)
    serial_number = models.CharField(max_length=120, blank=True)
    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    notes = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_USE)
    acquisition = models.CharField(max_length=10, choices=Acquisition.choices, default=Acquisition.PENDING)
    # Counts the times the purchase was posted: a posting that was reversed cannot be reused, so each gets its own source id.
    acquisition_seq = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_fixed_asset_code"),
            models.CheckConstraint(condition=Q(cost__gt=0), name="fixed_asset_cost_positive"),
        ]
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return f"{self.code} {self.name}"


class DepreciationRun(UUIDModel):
    """Depreciation posted for one month: one journal entry for every asset charged in it."""

    class Status(models.TextChoices):
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="depreciation_runs")
    period_start = models.DateField()  # the first day of the month
    period_end = models.DateField()
    total = models.DecimalField(max_digits=16, decimal_places=2)
    asset_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.POSTED)
    reverse_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-period_start", "-created_at"]


class AssetDisposal(UUIDModel):
    class Kind(models.TextChoices):
        SOLD = "sold", "Sold"
        DISPOSED = "disposed", "Scrapped or given away"
        WRITTEN_OFF = "written_off", "Written off"

    class Status(models.TextChoices):
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="asset_disposals")
    asset = models.ForeignKey(FixedAsset, on_delete=models.PROTECT, related_name="disposals")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    date = models.DateField()
    proceeds = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    proceeds_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cost = models.DecimalField(max_digits=16, decimal_places=2)
    accumulated = models.DecimalField(max_digits=16, decimal_places=2)  # including the depreciation charged up to the date
    final_depreciation = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    wdv = models.DecimalField(max_digits=16, decimal_places=2)
    gain_loss = models.DecimalField(max_digits=16, decimal_places=2)  # positive gain, negative loss
    buyer = models.CharField(max_length=200, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.POSTED)
    reverse_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-created_at"]


class DepreciationLine(UUIDModel):
    """The depreciation charged on one asset for one month. At most one live line per asset and month, which is what
    stops a second run (or a retry) from charging the same month twice."""

    asset = models.ForeignKey(FixedAsset, on_delete=models.PROTECT, related_name="depreciation_lines")
    run = models.ForeignKey(DepreciationRun, null=True, blank=True, on_delete=models.PROTECT, related_name="lines")
    disposal = models.ForeignKey(AssetDisposal, null=True, blank=True, on_delete=models.PROTECT, related_name="lines")
    period_start = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    accumulated = models.DecimalField(max_digits=16, decimal_places=2)  # total depreciation after this month
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["period_start"]
        constraints = [
            models.UniqueConstraint(fields=["asset", "period_start"], condition=Q(active=True), name="one_live_depreciation_per_month"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="depreciation_amount_positive"),
        ]


# --------------------------------------------------------------------------- budgets

class Budget(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="budgets")
    name = models.CharField(max_length=150)
    financial_year = models.ForeignKey(FinancialYear, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)
    notes = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-financial_year__start_date", "name"]
        constraints = [
            # Only one budget per year is the live one that expenses are checked against.
            models.UniqueConstraint(fields=["company", "financial_year"], condition=Q(status="active"), name="one_active_budget_per_year"),
        ]


class BudgetLine(UUIDModel):
    class Phasing(models.TextChoices):
        EVEN = "even", "Even over 12 months"
        CUSTOM = "custom", "Month by month"

    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    # The cost centre is the project the money is tagged to (journal lines carry project_id); empty means the whole company.
    project_id = models.UUIDField(null=True, blank=True)
    phasing = models.CharField(max_length=6, choices=Phasing.choices, default=Phasing.EVEN)
    annual = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    monthly = models.JSONField(default=list)  # twelve amounts as text, the first for the financial year's first month

    class Meta:
        ordering = ["account__code", "project_id"]


class BudgetRevision(UUIDModel):
    """A saved copy of the budget's lines each time they change, so earlier versions can be read back."""

    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="revisions")
    version = models.PositiveIntegerField()
    snapshot = models.JSONField(default=list)
    total_income = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_expense = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["budget", "version"], name="one_revision_per_version")]
