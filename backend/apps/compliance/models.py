"""Legal and compliance: the obligations catalogue, the due-date calendar, legal events, contracts, cases and policies.

Links to other modules (customer, vendor, project) are soft references (an id plus the name at the time), so this module's
migrations stay independent. Company registrations, partners and licences are read from `apps.company`, never copied.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.company.models import CompanyProfile
from apps.core.models import UUIDModel

from . import constants as C


def _choices(pairs):
    return [(k, v) for k, v in pairs]


class Obligation(UUIDModel):
    """One recurring (or event-driven) statutory duty in the catalogue: a form, its authority, when it falls due."""

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="compliance_obligations")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    form_no = models.CharField(max_length=60, blank=True)
    category = models.CharField(max_length=12, choices=_choices(C.CATEGORIES), default=C.OTHER)
    authority = models.CharField(max_length=160, blank=True)
    frequency = models.CharField(max_length=12, choices=_choices(C.FREQUENCIES), default="annual")
    # See services/schedule.py for the rule shapes: {"type": "dates"|"monthly"|"event", ...}
    due_rule = models.JSONField(default=dict, blank=True)
    due_rule_text = models.CharField(max_length=200, blank=True, help_text="Plain-words description of the due date")
    penalty_rule = models.CharField(max_length=20, choices=_choices(C.PENALTY_CHOICES), default="none")
    penalty_note = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    assignee_role = models.CharField(max_length=20, blank=True)
    default_priority = models.CharField(max_length=10, choices=_choices(C.PRIORITIES), default="normal")
    reminder_days = models.JSONField(default=list, blank=True)
    is_seed = models.BooleanField(default=False, editable=False)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "category", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_obligation_code")]

    def __str__(self):
        return self.name


class CalendarItem(UUIDModel):
    """One dated task: an obligation for a period (generated) or a free-standing task (added by hand)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        FILED = "filed", "Filed"
        NOT_APPLICABLE = "not_applicable", "Not applicable"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="compliance_items")
    obligation = models.ForeignKey(Obligation, null=True, blank=True, on_delete=models.PROTECT, related_name="items")
    fy_label = models.CharField(max_length=7, db_index=True)
    period_key = models.CharField(max_length=60)
    period_label = models.CharField(max_length=80, blank=True)
    title = models.CharField(max_length=200)
    form_no = models.CharField(max_length=60, blank=True)
    category = models.CharField(max_length=12, choices=_choices(C.CATEGORIES), default=C.OTHER)
    penalty_rule = models.CharField(max_length=20, choices=_choices(C.PENALTY_CHOICES), default="none")
    due_date = models.DateField(db_index=True)
    # Set when someone moved the due date by hand (an extension, say); regenerating leaves such dates alone.
    due_date_overridden = models.BooleanField(default=False)
    event_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.OPEN)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    priority = models.CharField(max_length=10, choices=_choices(C.PRIORITIES), default="normal")
    reminder_days = models.JSONField(default=list, blank=True)
    # Filing details
    reference = models.CharField(max_length=120, blank=True, editable=False)
    filed_date = models.DateField(null=True, blank=True)
    ack_number = models.CharField(max_length=80, blank=True)
    fee_paid = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    penalty_paid = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    remarks = models.TextField(blank=True)
    filed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    na_reason = models.CharField(max_length=300, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["due_date", "title"]
        constraints = [
            models.UniqueConstraint(fields=["company", "obligation", "period_key"], name="unique_calendar_period"),
            models.CheckConstraint(
                condition=~Q(status="filed") | Q(filed_date__isnull=False), name="filed_item_has_date"),
        ]
        indexes = [models.Index(fields=["company", "fy_label", "due_date"])]

    def __str__(self):
        return f"{self.title} {self.period_label}".strip()


class LegalEvent(UUIDModel):
    """A dated event in the LLP's legal life: incorporation, a name or partner change, a charge registered ..."""

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="legal_events")
    reference = models.CharField(max_length=120, blank=True, editable=False)
    event_type = models.CharField(max_length=20, choices=_choices(C.EVENT_TYPES))
    title = models.CharField(max_length=200)
    event_date = models.DateField()
    form_no = models.CharField(max_length=60, blank=True)
    srn = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)
    # "audit:<uuid>", "profile:incorporation", "partner:<uuid>:appointed" for rows imported from company history.
    source_key = models.CharField(max_length=120, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-event_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "source_key"], condition=~Q(source_key=""), name="unique_legal_event_source"),
        ]


class Contract(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        TERMINATED = "terminated", "Terminated"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="contracts")
    reference = models.CharField(max_length=120, blank=True, editable=False)
    title = models.CharField(max_length=200)
    counterparty = models.CharField(max_length=200)
    contract_type = models.CharField(max_length=12, choices=_choices(C.CONTRACT_TYPES), default="other")
    effective_date = models.DateField()
    expiry_date = models.DateField(null=True, blank=True)
    auto_renew = models.BooleanField(default=False)
    renewal_term_months = models.PositiveSmallIntegerField(default=12)
    renewal_notice_days = models.PositiveSmallIntegerField(default=30)
    renewal_count = models.PositiveSmallIntegerField(default=0)
    value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    terminated_on = models.DateField(null=True, blank=True)
    termination_reason = models.CharField(max_length=300, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    customer_id = models.UUIDField(null=True, blank=True)
    customer_name = models.CharField(max_length=200, blank=True)
    vendor_id = models.UUIDField(null=True, blank=True)
    vendor_name = models.CharField(max_length=200, blank=True)
    project_id = models.UUIDField(null=True, blank=True)
    project_name = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["expiry_date", "title"]
        indexes = [models.Index(fields=["company", "expiry_date"])]


class LegalCase(UUIDModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        DISPOSED = "disposed", "Disposed"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="legal_cases")
    reference = models.CharField(max_length=120, blank=True, editable=False)
    title = models.CharField(max_length=200)
    case_type = models.CharField(max_length=16, choices=_choices(C.CASE_TYPES), default="legal_notice")
    party_role = models.CharField(max_length=16, choices=_choices(C.PARTY_ROLES), default="respondent")
    forum = models.CharField(max_length=200, blank=True, help_text="Court, tribunal or authority")
    case_number = models.CharField(max_length=80, blank=True)
    counterparty = models.CharField(max_length=200, blank=True)
    filed_date = models.DateField(null=True, blank=True)
    next_hearing_date = models.DateField(null=True, blank=True)
    lawyer = models.CharField(max_length=200, blank=True)
    lawyer_contact = models.CharField(max_length=120, blank=True)
    amount_involved = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    outcome = models.TextField(blank=True)
    closed_on = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["next_hearing_date", "-created_at"]


class Policy(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Retired"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="compliance_policies")
    reference = models.CharField(max_length=120, blank=True, editable=False)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=60, blank=True)
    version = models.CharField(max_length=20, default="1.0")
    effective_date = models.DateField()
    review_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    summary = models.TextField(blank=True)
    requires_acknowledgement = models.BooleanField(default=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "policies"


class PolicyAcknowledgement(UUIDModel):
    policy = models.ForeignKey(Policy, on_delete=models.CASCADE, related_name="acknowledgements")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    version = models.CharField(max_length=20)
    acknowledged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-acknowledged_at"]
        constraints = [models.UniqueConstraint(fields=["policy", "user", "version"], name="unique_policy_ack")]
