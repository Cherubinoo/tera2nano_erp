import uuid

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from .profile import CompanyProfile


class CompanyProfileAuditEvent(models.Model):
    """Append-only history of company configuration changes.

    Rows cannot be updated or deleted: the model refuses, and a database trigger
    (migration 0002) refuses as well.
    """

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        DEACTIVATED = "deactivated", "Deactivated"
        ACTIVATED = "activated", "Activated"
        DELETED = "deleted", "Deleted"
        VERIFIED = "verified", "Verified"
        REVEALED = "revealed", "Sensitive value revealed"
        DOWNLOADED = "downloaded", "Downloaded"
        ISSUED = "issued", "Document issued"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="audit_events")
    # Stored as plain values (no FK) so history survives user removal.
    actor_id = models.UUIDField(null=True, blank=True)
    actor_label = models.CharField(max_length=150, blank=True)
    entity_type = models.CharField(max_length=40)
    entity_id = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=14, choices=Action.choices)
    category = models.CharField(max_length=30, blank=True)
    changed_fields = ArrayField(models.CharField(max_length=60), default=list, blank=True)
    previous_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    profile_version = models.PositiveIntegerField(null=True, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["category"]),
            models.Index(fields=["-occurred_at"]),
        ]

    def __str__(self):
        return f"{self.action} {self.entity_type} at {self.occurred_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit events are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events are append-only and cannot be deleted.")
