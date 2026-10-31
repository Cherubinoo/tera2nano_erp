"""Shared upload handling: size limit, content sniffing, checksum."""
import hashlib
from dataclasses import dataclass

from django.conf import settings
from rest_framework.exceptions import ValidationError

from .validators import DOCUMENT_TYPES, IMAGE_TYPES, safe_filename, sniff_mime


@dataclass
class InspectedUpload:
    file: object
    original_filename: str
    mime_type: str
    size: int
    checksum: str


def inspect_upload(uploaded, *, images_only=False):
    if uploaded is None:
        raise ValidationError({"file": "No file was uploaded."})
    if uploaded.size > settings.MAX_UPLOAD_BYTES:
        raise ValidationError({"file": f"File exceeds the {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."})
    if uploaded.size == 0:
        raise ValidationError({"file": "The uploaded file is empty."})
    head = uploaded.read(16)
    mime = sniff_mime(head)
    allowed = IMAGE_TYPES if images_only else DOCUMENT_TYPES
    if mime not in allowed:
        kinds = "PNG or JPEG images" if images_only else "PDF, PNG or JPEG files"
        raise ValidationError({"file": f"Unsupported file content. Only {kinds} are accepted."})
    uploaded.seek(0)
    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)
    return InspectedUpload(uploaded, safe_filename(uploaded.name), mime, uploaded.size, digest.hexdigest())
