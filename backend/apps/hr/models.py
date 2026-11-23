"""People: employees, attendance, leave, payroll, performance and project assignments.

Money is `Decimal`. Sensitive identifiers (PAN, Aadhaar, salary bank account) are stored encrypted with only the last four
characters kept in the clear, so lists can show them masked. A payslip keeps the figures it was worked out with, so a later
change to a salary structure or to the statutory settings never rewrites history. Links to other modules (projects) are
soft ids; the books are written only through `apps.accounting.services.ledger`.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.company.models import CompanyBankAccount, CompanyProfile, IssuedDocument
from apps.core import crypto
from apps.core.models import UUIDModel

ZERO = Decimal("0.00")


def money_field(**kw):
    kw.setdefault("default", 0)
    return models.DecimalField(max_digits=14, decimal_places=2, **kw)


DEFAULT_PT_SLABS = [  # Tamil Nadu half-yearly slab, simplified to a monthly amount (half-yearly / 6) on monthly gross
    {"up_to": "21000", "amount": "0.00"},
    {"up_to": "30000", "amount": "22.50"},
    {"up_to": "45000", "amount": "52.50"},
    {"up_to": "60000", "amount": "115.00"},
    {"up_to": "75000", "amount": "171.25"},
    {"up_to": None, "amount": "208.33"},
]


class HRSettings(UUIDModel):
    """Statutory rates and working-calendar choices for payroll and leave. One row per company."""

    class LopBasis(models.TextChoices):
        CALENDAR = "calendar", "Calendar days in the month"
        WORKING = "working", "Working days (excluding weekly offs and holidays)"

    company = models.OneToOneField(CompanyProfile, on_delete=models.PROTECT, related_name="hr_settings")
    pf_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("12.00"))
    pf_wage_ceiling = money_field(default=Decimal("15000.00"))
    esi_ceiling = money_field(default=Decimal("21000.00"))
    esi_employee_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.75"))
    esi_employer_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("3.25"))
    pt_state = models.CharField(max_length=60, default="Tamil Nadu")
    pt_slabs = models.JSONField(default=list, blank=True)
    lop_basis = models.CharField(max_length=10, choices=LopBasis.choices, default=LopBasis.CALENDAR)
    weekly_off_days = models.JSONField(default=list, blank=True)  # Python weekday numbers, Monday=0 ... Sunday=6
    leave_year_start_month = models.PositiveSmallIntegerField(default=4)

    class Meta:
        verbose_name_plural = "HR settings"


class Department(UUIDModel):
    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="hr_departments")
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    head = models.ForeignKey("Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_department_name")]

    def __str__(self):
        return self.name


class Designation(UUIDModel):
    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="hr_designations")
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_designation_name")]

    def __str__(self):
        return self.name


class Employee(UUIDModel):
    class Gender(models.TextChoices):
        FEMALE = "female", "Female"
        MALE = "male", "Male"
        OTHER = "other", "Other"
        UNDISCLOSED = "undisclosed", "Prefer not to say"

    class Type(models.TextChoices):
        FULL_TIME = "full_time", "Full-time"
        CONTRACT = "contract", "Contract"
        INTERN = "intern", "Intern"
        CONSULTANT = "consultant", "Consultant"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        NOTICE = "on_notice", "On notice"
        EXITED = "exited", "Exited"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="employees")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="employee")
    code = models.CharField(max_length=60, editable=False)  # T2N-EMP-26-1211-0001, allocated automatically
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=12, choices=Gender.choices, blank=True)
    personal_email = models.EmailField(blank=True)
    work_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    emergency_name = models.CharField(max_length=120, blank=True)
    emergency_relation = models.CharField(max_length=60, blank=True)
    emergency_phone = models.CharField(max_length=30, blank=True)

    date_of_joining = models.DateField()
    employment_type = models.CharField(max_length=12, choices=Type.choices, default=Type.FULL_TIME)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT, related_name="employees")
    designation = models.ForeignKey(Designation, null=True, blank=True, on_delete=models.PROTECT, related_name="employees")
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports")
    work_location = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    exit_date = models.DateField(null=True, blank=True)
    exit_reason = models.CharField(max_length=300, blank=True)

    # Sensitive identifiers: encrypted, with the last four characters kept for masked display.
    pan_encrypted = models.TextField(blank=True, editable=False)
    pan_last4 = models.CharField(max_length=4, blank=True, editable=False)
    pan_hash = models.CharField(max_length=64, blank=True, editable=False)
    aadhaar_encrypted = models.TextField(blank=True, editable=False)
    aadhaar_last4 = models.CharField(max_length=4, blank=True, editable=False)
    aadhaar_hash = models.CharField(max_length=64, blank=True, editable=False)
    bank_account_encrypted = models.TextField(blank=True, editable=False)
    bank_account_last4 = models.CharField(max_length=4, blank=True, editable=False)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_ifsc = models.CharField(max_length=11, blank=True)
    bank_account_holder = models.CharField(max_length=150, blank=True)
    uan = models.CharField(max_length=12, blank=True)
    esi_number = models.CharField(max_length=17, blank=True)

    class Meta:
        ordering = ["first_name", "last_name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], condition=~Q(code=""), name="unique_employee_code"),
            models.UniqueConstraint(fields=["pan_hash"], condition=~Q(pan_hash=""), name="unique_employee_pan"),
            models.UniqueConstraint(fields=["aadhaar_hash"], condition=~Q(aadhaar_hash=""), name="unique_employee_aadhaar"),
        ]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["date_of_joining"])]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}".strip()

    # -- secrets
    def set_secret(self, kind, value):
        """kind: pan | aadhaar | bank_account. Empty clears it."""
        value = (value or "").strip()
        if not value:
            setattr(self, f"{kind}_encrypted", "")
            setattr(self, f"{kind}_last4", "")
            if kind != "bank_account":
                setattr(self, f"{kind}_hash", "")
            return
        setattr(self, f"{kind}_encrypted", crypto.encrypt(value))
        setattr(self, f"{kind}_last4", value[-4:])
        if kind != "bank_account":
            setattr(self, f"{kind}_hash", crypto.keyed_hash(f"{kind}:{value}"))

    def reveal(self, kind):
        token = getattr(self, f"{kind}_encrypted")
        return crypto.decrypt(token) if token else ""

    def masked(self, kind):
        last4 = getattr(self, f"{kind}_last4")
        return f"XXXXXX{last4}" if last4 else ""


class SalaryStructure(UUIDModel):
    """What an employee is paid each month, effective from a date. Later rows supersede earlier ones; none is overwritten."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="salary_structures")
    effective_from = models.DateField()
    basic = money_field()
    hra = money_field()
    special_allowance = money_field()
    other_allowance = money_field()
    fixed_deductions = money_field()  # a recurring deduction other than the statutory ones
    tds_monthly = money_field()  # income tax on salary, set by hand each year
    pf_enabled = models.BooleanField(default=True)
    pf_on_full_basic = models.BooleanField(default=False)  # voluntary: PF on the whole basic, ignoring the wage ceiling
    esi_enabled = models.BooleanField(default=True)
    pt_enabled = models.BooleanField(default=True)
    notes = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "effective_from"], name="one_structure_per_effective_date"),
            models.CheckConstraint(condition=Q(basic__gte=0) & Q(hra__gte=0) & Q(special_allowance__gte=0)
                                   & Q(other_allowance__gte=0) & Q(fixed_deductions__gte=0) & Q(tds_monthly__gte=0),
                                   name="salary_not_negative"),
        ]

    @property
    def gross(self):
        return self.basic + self.hra + self.special_allowance + self.other_allowance


class EmployeeAdvance(UUIDModel):
    """Money lent to an employee and recovered through payroll in instalments."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="advances")
    date = models.DateField()
    amount = money_field()
    installment = money_field()
    recovered = money_field()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0) & Q(installment__gt=0), name="advance_positive")]

    @property
    def outstanding(self):
        return self.amount - self.recovered


# --------------------------------------------------------------------------- attendance and leave

class Holiday(UUIDModel):
    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="hr_holidays")
    date = models.DateField()
    name = models.CharField(max_length=120)
    is_optional = models.BooleanField(default=False)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["company", "date"], name="one_holiday_per_date")]


class LeaveType(UUIDModel):
    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="leave_types")
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=80)
    annual_quota = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    carry_forward = models.BooleanField(default=False)
    max_carry_forward = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    is_paid = models.BooleanField(default=True)  # unpaid leave reduces pay (loss of pay)
    is_active = models.BooleanField(default=True)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_leave_type_code")]

    def __str__(self):
        return self.name


class LeaveAdjustment(UUIDModel):
    """A manual credit or debit to a leave balance (opening balance, encashment, correction) in one leave year."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leave_adjustments")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="adjustments")
    year_start = models.DateField()
    days = models.DecimalField(max_digits=5, decimal_places=1)
    reason = models.CharField(max_length=300)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")


class LeaveRequest(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="leave_requests")
    number = models.CharField(max_length=120, blank=True)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_requests")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="requests")
    start_date = models.DateField()
    end_date = models.DateField()
    half_day = models.BooleanField(default=False)
    days = models.DecimalField(max_digits=5, decimal_places=1)
    reason = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-start_date", "-created_at"]
        indexes = [models.Index(fields=["employee", "start_date"]), models.Index(fields=["status"])]
        constraints = [models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")), name="leave_dates_ordered")]


class Attendance(UUIDModel):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        HALF_DAY = "half_day", "Half day"
        LEAVE = "leave", "On leave"
        HOLIDAY = "holiday", "Holiday"
        WFH = "wfh", "Work from home"

    class Source(models.TextChoices):
        HR = "hr", "Marked by HR"
        SELF = "self", "Checked in by the employee"
        LEAVE = "leave", "From an approved leave"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices)
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    source = models.CharField(max_length=6, choices=Source.choices, default=Source.HR)
    leave_request = models.ForeignKey(LeaveRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="attendance_rows")
    marked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="one_attendance_row_per_day")]
        indexes = [models.Index(fields=["date", "status"])]


# --------------------------------------------------------------------------- payroll

class PayrollRun(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        LOCKED = "locked", "Locked"
        PENDING = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        POSTED = "posted", "Posted to books"
        PAID = "paid", "Salaries paid"
        VOID = "void", "Void"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="payroll_runs")
    number = models.CharField(max_length=120, blank=True)
    month = models.DateField()  # the first day of the month paid
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.CharField(max_length=300, blank=True)
    employee_count = models.PositiveIntegerField(default=0)
    total_gross = money_field()
    total_deductions = money_field()
    total_net = money_field()
    total_employer_cost = money_field()  # gross + employer PF and ESI
    locked_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    paid_date = models.DateField(null=True, blank=True)
    paid_reference = models.CharField(max_length=100, blank=True)
    paid_from = models.ForeignKey(CompanyBankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    void_reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-month", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "month"], condition=~Q(status="void"), name="one_live_payroll_run_per_month"),
            models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_payroll_run_number"),
        ]


class Payslip(UUIDModel):
    """One employee's pay for one run. Inputs (days, bonus, TDS ...) are kept beside the worked-out figures."""

    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    month = models.DateField()
    who = models.JSONField(default=dict, blank=True)  # name, code, department, designation, PAN/bank last four at the time
    structure = models.ForeignKey(SalaryStructure, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    full = models.JSONField(default=dict, blank=True)  # the monthly structure used: basic, hra ...
    basis_days = models.PositiveSmallIntegerField(default=30)  # the days the month's pay is divided by (calendar or working days)
    lop_days_auto = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    lop_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    paid_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    # inputs
    bonus = money_field()
    tds = money_field()
    loan_recovery = money_field()
    other_deductions = money_field()
    remarks = models.CharField(max_length=300, blank=True)
    # worked out
    basic = money_field()
    hra = money_field()
    special_allowance = money_field()
    other_allowance = money_field()
    gross = money_field()
    pf_employee = money_field()
    pf_employer = money_field()
    esi_employee = money_field()
    esi_employer = money_field()
    professional_tax = money_field()
    total_deductions = money_field()
    net_pay = money_field()
    employer_cost = money_field()
    issued_document = models.ForeignKey(IssuedDocument, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["employee__first_name", "employee__last_name"]
        constraints = [models.UniqueConstraint(fields=["run", "employee"], name="one_payslip_per_run_and_employee")]
        indexes = [models.Index(fields=["employee", "month"])]


# --------------------------------------------------------------------------- performance

class ReviewCycle(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="review_cycles")
    name = models.CharField(max_length=120)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-start_date"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_review_cycle_name")]


class Goal(UUIDModel):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        ACHIEVED = "achieved", "Achieved"
        MISSED = "missed", "Missed"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="goals")
    cycle = models.ForeignKey(ReviewCycle, on_delete=models.CASCADE, related_name="goals")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    weight = models.PositiveSmallIntegerField(default=0)  # percent of the overall rating
    progress = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NOT_STARTED)
    manager_comment = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["created_at"]


class Review(UUIDModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for self review"
        SELF_DONE = "self_done", "Waiting for manager"
        COMPLETED = "completed", "Completed"

    cycle = models.ForeignKey(ReviewCycle, on_delete=models.CASCADE, related_name="reviews")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    self_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    manager_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    self_comments = models.TextField(blank=True)
    manager_comments = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["employee__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["cycle", "employee"], name="one_review_per_cycle_and_employee"),
            models.CheckConstraint(condition=Q(self_rating__isnull=True) | (Q(self_rating__gte=1) & Q(self_rating__lte=5)), name="self_rating_1_5"),
            models.CheckConstraint(condition=Q(manager_rating__isnull=True) | (Q(manager_rating__gte=1) & Q(manager_rating__lte=5)), name="manager_rating_1_5"),
        ]


# --------------------------------------------------------------------------- projects

class ProjectAssignment(UUIDModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="assignments")
    project_id = models.UUIDField(db_index=True)  # apps.projects.Project id (soft reference)
    project_label = models.CharField(max_length=200, blank=True)  # name kept for display if the project is not reachable
    role = models.CharField(max_length=100, blank=True)
    allocation_percent = models.PositiveSmallIntegerField(default=100)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(condition=Q(allocation_percent__gte=1) & Q(allocation_percent__lte=100), name="allocation_1_100"),
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")), name="assignment_dates_ordered"),
        ]


class SalaryRevision(UUIDModel):
    """A new salary structure for an employee that is waiting for approval because it raises their yearly pay under an
    approval rule. Nothing is paid from it until it is approved; approving creates the `SalaryStructure`."""

    class Status(models.TextChoices):
        PENDING = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="salary_revisions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    effective_from = models.DateField()
    structure = models.JSONField(default=dict)  # the proposed pay, as sent: amounts as text, the PF/ESI/PT switches, notes
    previous_structure_id = models.UUIDField(null=True, blank=True)  # the structure in force on the effective date
    previous_gross = money_field()  # monthly
    new_gross = money_field()
    annual_increase = money_field()  # (new gross - previous gross) x 12: what the approval rule's amount is measured on
    reason = models.CharField(max_length=300, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)
    result_structure_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["employee", "status"])]
