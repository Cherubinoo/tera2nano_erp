from django.conf import settings
from django.db import models

from apps.company.models import CompanyProfile
from apps.core.models import UUIDModel
from apps.core.storage import UploadTo, private_storage


class Attachment(UUIDModel):
    """A file attached to any record (an employee, a vendor, a project, a purchase order ...). Stored privately."""

    company = models.ForeignKey(CompanyProfile, on_delete=models.PROTECT, related_name="attachments")
    entity_type = models.CharField(max_length=40)
    entity_id = models.CharField(max_length=64)
    title = models.CharField(max_length=200)
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=UploadTo("attachments/{document_type}"), storage=private_storage, max_length=255)
    mime_type = models.CharField(max_length=100)
    file_size = models.PositiveBigIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    expiry_date = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["entity_type", "entity_id"])]

    @property
    def document_type(self):  # gives each record type its own storage folder (see UploadTo)
        return self.entity_type
