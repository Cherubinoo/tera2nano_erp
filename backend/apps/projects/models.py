"""Projects: the work (milestones, tasks, time), the plan (budget) and the link to the books.

Money is `Decimal`. A project's revenue and cost are never stored here: they are read from the ledger (`JournalLine.project_id`)
by `services.financials`, so the project's profit and loss can never disagree with the books. Links to other modules
(employees, invoices) are soft ids.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.company.models import CompanyProfile
from apps.core.models import UUIDModel
from apps.sales.models import Customer


class Project(UUIDModel):
    class Type(models.TextChoices):
        IT_SOLUTION = "it_solution", "IT solution"
        NANO_RND = "nano_rnd", "Nano-technology / R&D"
        CONSULTING = "consulting", "Consulting"
        INTERNAL = "internal", "Internal"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Billing(models.TextChoices):
        FIXED = "fixed", "Fixed price"
        TIME_MATERIAL = "time_material", "Time and material"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="projects")
    code = models.CharField(max_length=60, editable=False)  # T2N-PRJ-26-1211-0001, allocated by numbering
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="projects")
    project_type = models.CharField(max_length=14, choices=Type.choices, default=Type.IT_SOLUTION)
    manager_id = models.UUIDField(null=True, blank=True)  # an employee (apps.hr) or user id, soft
    manager_name = models.CharField(max_length=150, blank=True)
    start_date = models.DateField(null=True, blank=True)
    planned_end_date = models.DateField(null=True, blank=True)
    actual_end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PLANNED)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM)
    billing_method = models.CharField(max_length=14, choices=Billing.choices, default=Billing.FIXED)
    contract_value = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    budget = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # default bill rate for time and material
    tags = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_project_code"),
            models.CheckConstraint(condition=Q(contract_value__gte=0) & Q(budget__gte=0) & Q(hourly_rate__gte=0),
                                   name="project_money_not_negative"),
        ]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["customer"])]

    def __str__(self):
        return f"{self.code} {self.name}"


class BudgetLine(UUIDModel):
    class Category(models.TextChoices):
        LABOUR = "labour", "Labour"
        MATERIALS = "materials", "Materials"
        SUBCONTRACT = "subcontract", "Subcontract"
        TRAVEL = "travel", "Travel"
        OTHER = "other", "Other"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="budget_lines")
    category = models.CharField(max_length=12, choices=Category.choices)
    amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "category"], name="one_budget_line_per_category"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="budget_line_not_negative"),
        ]


class Milestone(UUIDModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        DONE = "done", "Done"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="milestones")
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True)
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)  # to bill when reached (optional)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    completed_on = models.DateField(null=True, blank=True)
    invoice_id = models.UUIDField(null=True, blank=True)  # the draft or issued invoice raised for it (apps.sales), soft

    class Meta:
        ordering = ["due_date", "created_at"]
        constraints = [models.CheckConstraint(condition=Q(amount__gte=0), name="milestone_amount_not_negative")]


class Task(UUIDModel):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        IN_PROGRESS = "in_progress", "In progress"
        REVIEW = "review", "In review"
        DONE = "done", "Done"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    milestone = models.ForeignKey(Milestone, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    code = models.CharField(max_length=60, editable=False)  # T2N-TSK-26-1211-0001
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee_id = models.UUIDField(null=True, blank=True)  # employee id (apps.hr lookup), soft
    assignee_name = models.CharField(max_length=150, blank=True)
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.TODO)
    estimated_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    completed_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "created_at"]
        constraints = [models.CheckConstraint(condition=Q(estimated_hours__gte=0), name="task_hours_not_negative")]
        indexes = [models.Index(fields=["status"]), models.Index(fields=["assignee_id"])]


class TeamMember(UUIDModel):
    """Lightweight team list; each person is picked from the HR directory (soft id) or typed in."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="team")
    employee_id = models.UUIDField(null=True, blank=True)
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=100, blank=True)
    allocation_percent = models.PositiveSmallIntegerField(default=100)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # bill rate used for their timesheets
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.CheckConstraint(condition=Q(hourly_rate__gte=0), name="member_rate_not_negative")]


class TimeEntry(UUIDModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="time_entries")
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="time_entries")
    date = models.DateField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    employee_id = models.UUIDField(null=True, blank=True)
    employee_name = models.CharField(max_length=150, blank=True)
    hours = models.DecimalField(max_digits=5, decimal_places=2)
    billable = models.BooleanField(default=True)
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(hours__gt=0) & Q(hours__lte=24), name="time_hours_between_0_and_24"),
            models.CheckConstraint(condition=Q(rate__gte=0), name="time_rate_not_negative"),
        ]
        indexes = [models.Index(fields=["project", "date"])]

    @property
    def amount(self):
        from decimal import ROUND_HALF_UP, Decimal
        return (self.hours * self.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if self.billable else Decimal("0.00")


class BudgetRevision(UUIDModel):
    """A proposed change to a project's budget (the total and the category lines). While the approvals module is deciding it,
    the project keeps its current budget; approving applies the proposal, rejecting or withdrawing leaves the budget as it was."""

    class Status(models.TextChoices):
        PENDING = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="budget_revisions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    previous_budget = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    previous_lines = models.JSONField(default=list, blank=True)   # [{category, amount, notes}] as they were when requested
    proposed_budget = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    proposed_lines = models.JSONField(default=list, blank=True)
    proposed_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)  # the budget in force if approved
    reason = models.CharField(max_length=300, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "status"])]
