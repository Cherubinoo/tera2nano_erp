"""Approval management: rules, requests, per-step state, decisions and an immutable history.

A rule says "for this kind of record (and these conditions) the request goes through these steps". When a request is
submitted the matching rule's steps are copied onto the request (`ApprovalStep`), so editing a rule later never changes a
request already in flight. Decisions and events are append-only.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.accounts import access
from apps.company.models import CompanyProfile
from apps.core.models import UUIDModel

APPROVER_ROLES = [
    (access.MANAGING_PARTNER, "Managing Partner"),
    (access.PARTNER, "Partner"),
    (access.FINANCE_MANAGER, "Finance Manager"),
    (access.HR_MANAGER, "HR Manager"),
    (access.PROJECT_MANAGER, "Project Manager"),
    (access.ADMIN, "Admin"),
]
ROLE_LABELS = dict(access.ROLES)


class ApprovalSettings(UUIDModel):
    """One row per company: remembers that the example rules were created once (so deleting them all is respected)."""

    company = models.OneToOneField(CompanyProfile, on_delete=models.CASCADE, related_name="approval_settings")
    defaults_seeded = models.BooleanField(default=False)


class ApprovalRule(UUIDModel):
    """For records of `kind` that satisfy the conditions, the request goes through the steps in order.

    Rules are tried in `priority` order (lowest first); the first active rule whose conditions match is used.
    """

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="approval_rules")
    kind = models.CharField(max_length=40)
    name = models.CharField(max_length=150)
    description = models.CharField(max_length=300, blank=True)
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    # Conditions. Amounts are inclusive; empty means no limit. Project/department are matched as case-insensitive
    # free text against what the submitting module sent in `meta`.
    amount_from = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    amount_to = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    project_text = models.CharField(max_length=150, blank=True)
    department_text = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["priority", "created_at"]
        indexes = [models.Index(fields=["company", "kind", "priority"])]

    def __str__(self):
        return f"{self.kind}: {self.name}"

    def clean(self):
        errors = {}
        if self.amount_from is not None and self.amount_from < 0:
            errors["amount_from"] = "The amount cannot be negative."
        if self.amount_to is not None and self.amount_to < 0:
            errors["amount_to"] = "The amount cannot be negative."
        if self.amount_from is not None and self.amount_to is not None and self.amount_to < self.amount_from:
            errors["amount_to"] = "The upper amount must not be below the lower amount."
        if not (self.name or "").strip():
            errors["name"] = "Enter a name for the rule."
        if errors:
            raise ValidationError(errors)


class StepBase(models.Model):
    class ApproverType(models.TextChoices):
        ROLE = "role", "A role"
        USER = "user", "A specific user"

    class Mode(models.TextChoices):
        ANY = "any", "Any one of them"
        ALL = "all", "All of them"

    order = models.PositiveSmallIntegerField()
    approver_type = models.CharField(max_length=5, choices=ApproverType.choices, default=ApproverType.ROLE)
    role = models.CharField(max_length=20, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    mode = models.CharField(max_length=3, choices=Mode.choices, default=Mode.ANY)
    # Days after the step becomes active before it counts as overdue and is escalated; empty means never.
    escalate_after_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ["order"]


class ApprovalRuleStep(StepBase):
    rule = models.ForeignKey(ApprovalRule, on_delete=models.CASCADE, related_name="steps")

    class Meta(StepBase.Meta):
        constraints = [models.UniqueConstraint(fields=["rule", "order"], name="approval_rule_step_order")]


class ApprovalRequest(UUIDModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        ESCALATED = "escalated", "Escalated"

    OPEN = (Status.PENDING, Status.ESCALATED)

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="approval_requests")
    reference_number = models.CharField(max_length=60, editable=False)
    kind = models.CharField(max_length=40)
    source_id = models.CharField(max_length=64)
    title = models.CharField(max_length=250)
    amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    summary = models.TextField(blank=True)
    url = models.CharField(max_length=300, blank=True)
    project_id = models.UUIDField(null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="approval_requests")
    rule = models.ForeignKey(ApprovalRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="requests")
    rule_name = models.CharField(max_length=150, blank=True)
    previous = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="resubmissions")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    current_step = models.PositiveSmallIntegerField(default=1)
    total_steps = models.PositiveSmallIntegerField(default=1)
    submitted_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "reference_number"], name="approval_unique_reference"),
            # At most one open request per source record (guards against a double submit).
            models.UniqueConstraint(fields=["company", "kind", "source_id"], condition=Q(status__in=["pending", "escalated"]),
                                    name="approval_one_open_per_source"),
        ]
        indexes = [models.Index(fields=["company", "status"]), models.Index(fields=["kind", "source_id"])]

    def __str__(self):
        return f"{self.reference_number} {self.title}"

    @property
    def is_open(self):
        return self.status in self.OPEN


class ApprovalStep(StepBase):
    class State(models.TextChoices):
        WAITING = "waiting", "Waiting"
        ACTIVE = "active", "Active"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="steps")
    state = models.CharField(max_length=8, choices=State.choices, default=State.WAITING)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Set when someone with approvals.manage / hr.manage handed the step to another user.
    delegated = models.BooleanField(default=False)

    class Meta(StepBase.Meta):
        constraints = [models.UniqueConstraint(fields=["request", "order"], name="approval_step_order")]


class Immutable(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("History records cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("History records cannot be deleted.")


class ApprovalDecision(Immutable):
    """One approver's decision on one step."""

    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    id = models.BigAutoField(primary_key=True)
    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="decisions")
    step = models.ForeignKey(ApprovalStep, on_delete=models.CASCADE, related_name="decisions")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    approver_label = models.CharField(max_length=150)
    decision = models.CharField(max_length=10, choices=Decision.choices)
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField()
    # The step's active period this decision belongs to (seconds from step start), for turnaround analytics.
    hours_taken = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["decided_at", "id"]
        constraints = [models.UniqueConstraint(fields=["step", "approver"], name="approval_one_decision_per_approver_step")]


class ApprovalEvent(Immutable):
    """The timeline of a request. Never edited or deleted."""

    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        AUTO_APPROVED = "auto_approved", "Approved automatically"
        STEP_APPROVED = "step_approved", "Step approved"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        CANCELLED = "cancelled", "Cancelled by the source record"
        RESUBMITTED = "resubmitted", "Resubmitted"
        DELEGATED = "delegated", "Reassigned"
        ESCALATED = "escalated", "Escalated"

    id = models.BigAutoField(primary_key=True)
    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=14, choices=Action.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actor_label = models.CharField(max_length=150, blank=True)
    step_order = models.PositiveSmallIntegerField(null=True, blank=True)
    comment = models.TextField(blank=True)
    at = models.DateTimeField()

    class Meta:
        ordering = ["at", "id"]
