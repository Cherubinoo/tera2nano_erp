"""Shared API plumbing: company lookup, audited saves, private file responses."""
import mimetypes

from django.db import transaction
from django.http import FileResponse
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from apps.core.validators import sniff_mime

from .models import CompanyProfile, CompanyProfileAuditEvent
from .services import audit

Action = CompanyProfileAuditEvent.Action
EMPTY = (None, "", [], {})


class NotConfigured(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "The company profile has not been set up yet."
    default_code = "not_configured"


def current_company():
    company = CompanyProfile.objects.first()
    if company is None:
        raise NotConfigured()
    return company


def request_reason(request):
    value = request.data.get("change_reason") if hasattr(request.data, "get") else None
    return (value or "").strip() if isinstance(value, str) else ""


def require_reason_for(entity, changed, before, company, reason):
    needed = audit.reason_needed(entity, changed, before, company)
    if needed and not reason:
        raise ValidationError(
            {"change_reason": f"Give a reason for this change (affects: {', '.join(needed)})."}
        )


def save_with_audit(serializer, *, entity, company, request, **save_kwargs):
    """Save through the serializer and write one audit event describing the change.

    Must run inside a transaction: a missing change reason raises after the save, and the
    surrounding transaction rolls the save back.
    """
    creating = serializer.instance is None
    before = {} if creating else audit.snapshot(serializer.instance)
    instance = serializer.save(**save_kwargs)
    after = audit.snapshot(instance)
    changed, previous, new = audit.diff(entity, before, after)
    if creating:
        changed = [c for c in changed if after.get(c) not in EMPTY]
        previous = {}
        new = {c: new[c] for c in changed}
    if not changed:
        return instance
    reason = request_reason(request)
    if not creating:
        require_reason_for(entity, changed, before, company, reason)
    audit.record_event(
        company, request.user, entity, instance.pk, Action.CREATED if creating else Action.UPDATED,
        changed=changed, previous=previous, new=new, reason=reason, instance=instance,
    )
    return instance


def record_simple(company, request, entity, instance, action, *, before=None, reason=""):
    """Audit a state change (activate, deactivate, verify, delete) from before/after snapshots."""
    after = audit.snapshot(instance) if action != Action.DELETED else {}
    before = before or {}
    changed, previous, new = audit.diff(entity, before, after)
    if action == Action.DELETED:
        changed, previous, new = list(before), {}, {}
    return audit.record_event(
        company, request.user, entity, instance.pk, action, changed=changed, previous=previous, new=new,
        reason=reason, instance=instance if action != Action.DELETED else None,
    )


def private_file_response(field_file, *, filename=None, attachment=False, mime_type=None):
    """Stream a stored private file. Content type is sniffed from the bytes, not the filename."""
    try:
        handle = field_file.open("rb")
    except FileNotFoundError:
        # The database row exists but the stored file is gone (for example the private folder was cleared).
        return Response(
            {"detail": "The stored file is missing. Upload it again.", "code": "file_missing"},
            status=status.HTTP_404_NOT_FOUND,
        )
    head = handle.read(16)
    handle.seek(0)
    content_type = mime_type or sniff_mime(head) or mimetypes.guess_type(filename or "")[0] or "application/octet-stream"
    response = FileResponse(handle, content_type=content_type, as_attachment=attachment, filename=filename)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


atomic = transaction.atomic
