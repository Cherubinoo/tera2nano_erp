"""Workplace: meetings, tasks, announcements and circulars, and small admin registers (assets, visitors).

Links to other modules are soft references (UUIDs / text), never foreign keys, so migrations stay independent. The
only foreign keys that leave this app are the company profile, the user model and `IssuedDocument` (a circular's PDF).
Minutes of meeting are NOT stored here: they are issued by the company module and found again through the meeting number
(the minutes' `source_id`), so there is one minutes generator and one source of truth.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.company.models import CompanyProfile, IssuedDocument
from apps.core.models import UUIDModel

USER = settings.AUTH_USER_MODEL


class Meeting(UUIDModel):
    class Type(models.TextChoices):
        PARTNERS = "partners", "Partners"
        BOARD = "board", "Board"
        TEAM = "team", "Team"
        CLIENT = "client", "Client"
        VENDOR = "vendor", "Vendor"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        HELD = "held", "Held"
        CANCELLED = "cancelled", "Cancelled"
        RESCHEDULED = "rescheduled", "Rescheduled"

    class Recurrence(models.TextChoices):
        NONE = "none", "Does not repeat"
        WEEKLY = "weekly", "Every week"
        MONTHLY = "monthly", "Every month"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="workplace_meetings")
    # The meeting ID (T2N-MTG-...), allocated when the meeting is scheduled. The minutes carry the same ID.
    number = models.CharField(max_length=60, blank=True, default="")
    title = models.CharField(max_length=200)
    meeting_type = models.CharField(max_length=10, choices=Type.choices, default=Type.TEAM)
    date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    venue = models.CharField(max_length=200, blank=True)
    link = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    organiser = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED)
    status_reason = models.CharField(max_length=300, blank=True)
    recurrence = models.CharField(max_length=8, choices=Recurrence.choices, default=Recurrence.NONE)
    series_id = models.UUIDField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["date", "start_time", "created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_meeting_number")]
        indexes = [models.Index(fields=["date", "status"])]

    def __str__(self):
        return f"{self.number} {self.title}"


class MeetingAttendee(UUIDModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="attendees")
    position = models.PositiveSmallIntegerField(default=0)
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField(max_length=200)
    designation = models.CharField(max_length=200, blank=True)
    # None until the meeting is held; then present / absent.
    attended = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["position", "created_at"]

    @property
    def external(self):
        return self.user_id is None


class AgendaItem(UUIDModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="agenda")
    position = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(max_length=300)
    owner_name = models.CharField(max_length=200, blank=True)
    minutes = models.PositiveSmallIntegerField(null=True, blank=True)  # time allotted
    notes = models.CharField(max_length=1000, blank=True)

    class Meta:
        ordering = ["position", "created_at"]


class Decision(UUIDModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="decisions")
    position = models.PositiveSmallIntegerField(default=0)
    text = models.CharField(max_length=1000)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["position", "created_at"]


class Task(UUIDModel):
    class Status(models.TextChoices):
        TODO = "todo", "To do"
        IN_PROGRESS = "in_progress", "In progress"
        BLOCKED = "blocked", "Blocked"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    OPEN = ("todo", "in_progress", "blocked")

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="workplace_tasks")
    number = models.CharField(max_length=60, blank=True, default="")
    title = models.CharField(max_length=250)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reporter = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.TODO)
    labels = models.JSONField(default=list, blank=True)
    # Where the task came from: "manual", "meeting" (source_id = meeting id) or another module (soft reference).
    source = models.CharField(max_length=30, default="manual")
    source_id = models.CharField(max_length=60, blank=True)
    source_label = models.CharField(max_length=200, blank=True)
    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_task_number")]
        indexes = [models.Index(fields=["status", "due_date"]), models.Index(fields=["assignee", "status"])]

    def __str__(self):
        return f"{self.number} {self.title}"


class TaskItem(UUIDModel):
    """One line of a task's checklist (subtask)."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveSmallIntegerField(default=0)
    text = models.CharField(max_length=300)
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ["position", "created_at"]


class TaskComment(UUIDModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()

    class Meta:
        ordering = ["created_at"]


class Announcement(UUIDModel):
    class Audience(models.TextChoices):
        ALL = "all", "Everyone"
        ROLES = "roles", "Selected roles"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="workplace_announcements")
    title = models.CharField(max_length=200)
    body = models.TextField()  # Markdown
    audience = models.CharField(max_length=6, choices=Audience.choices, default=Audience.ALL)
    roles = models.JSONField(default=list, blank=True)
    pinned = models.BooleanField(default=False)
    publish_on = models.DateField()
    expires_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-pinned", "-publish_on", "-created_at"]


class Circular(UUIDModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="workplace_circulars")
    number = models.CharField(max_length=120, blank=True, default="")  # the reference number, from issue
    subject = models.CharField(max_length=250)
    body = models.TextField()  # Markdown
    addressed_to = models.CharField(max_length=300, blank=True)
    audience = models.CharField(max_length=6, choices=Announcement.Audience.choices, default=Announcement.Audience.ALL)
    roles = models.JSONField(default=list, blank=True)
    issue_date = models.DateField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    issued_document = models.OneToOneField(IssuedDocument, null=True, blank=True, on_delete=models.PROTECT, related_name="workplace_circular")
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    issued_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-issue_date", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], condition=~Q(number=""), name="unique_circular_number")]


class CircularAck(UUIDModel):
    circular = models.ForeignKey(Circular, on_delete=models.CASCADE, related_name="acknowledgements")
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="+")
    acknowledged_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["circular", "user"], name="one_ack_per_user")]


class Asset(UUIDModel):
    class Status(models.TextChoices):
        IN_STOCK = "in_stock", "In stock"
        IN_USE = "in_use", "In use"
        IN_REPAIR = "in_repair", "In repair"
        RETIRED = "retired", "Retired"
        LOST = "lost", "Lost"

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="workplace_assets")
    tag = models.CharField(max_length=60, blank=True, default="")
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=80, blank=True)
    serial_number = models.CharField(max_length=100, blank=True)
    assigned_to = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    location = models.CharField(max_length=150, blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_value = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.IN_STOCK)
    warranty_expiry = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["tag"]
        constraints = [models.UniqueConstraint(fields=["company", "tag"], condition=~Q(tag=""), name="unique_asset_tag")]


class Visitor(UUIDModel):
    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="workplace_visitors")
    name = models.CharField(max_length=200)
    visitor_company = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    purpose = models.CharField(max_length=300)
    host = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    host_name = models.CharField(max_length=200, blank=True)
    time_in = models.DateTimeField()
    time_out = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    recorded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-time_in"]
