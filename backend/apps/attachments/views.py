import datetime

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.company.api import current_company, private_file_response
from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit
from apps.core.files import inspect_upload

from . import registry
from .models import Attachment


def _guard(request, entity_type, kind):
    t = registry.target(entity_type)
    if t is None:
        raise ValidationError({"entity_type": "Files cannot be attached to this kind of record."})
    if not request.user.has_code(t[kind]):
        raise PermissionDenied("Your role does not allow this.")
    return t


def _dict(a):
    return {"id": str(a.id), "entity_type": a.entity_type, "entity_id": a.entity_id, "title": a.title,
            "original_filename": a.original_filename, "mime_type": a.mime_type, "file_size": a.file_size,
            "expiry_date": a.expiry_date, "is_expired": bool(a.expiry_date and a.expiry_date < datetime.date.today()),
            "notes": a.notes, "uploaded_by": a.uploaded_by.get_username() if a.uploaded_by else "", "created_at": a.created_at}


class AttachmentCollectionView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        entity_type, entity_id = request.query_params.get("entity_type", ""), request.query_params.get("entity_id", "")
        _guard(request, entity_type, "view")
        if not entity_id:
            raise ValidationError({"entity_id": "Say which record."})
        qs = Attachment.objects.filter(company=current_company(), entity_type=entity_type, entity_id=entity_id).select_related("uploaded_by")
        return Response([_dict(a) for a in qs])

    @transaction.atomic
    def post(self, request):
        d = request.data
        entity_type, entity_id = d.get("entity_type", ""), (d.get("entity_id") or "").strip()
        _guard(request, entity_type, "manage")
        if not entity_id:
            raise ValidationError({"entity_id": "Say which record."})
        upload = inspect_upload(request.FILES.get("file"))
        expiry = d.get("expiry_date") or None
        if expiry:
            try:
                expiry = datetime.date.fromisoformat(expiry)
            except ValueError:
                raise ValidationError({"expiry_date": "Use a date in YYYY-MM-DD format."})
        a = Attachment(company=current_company(), entity_type=entity_type, entity_id=entity_id,
                       title=(d.get("title") or upload.original_filename)[:200], original_filename=upload.original_filename,
                       mime_type=upload.mime_type, file_size=upload.size, checksum_sha256=upload.checksum, expiry_date=expiry,
                       notes=(d.get("notes") or "")[:300], uploaded_by=request.user)
        a.file.save(upload.original_filename, upload.file, save=False)
        a.save()
        audit.record_event(a.company, request.user, "attachment", a.pk, CompanyProfileAuditEvent.Action.CREATED, changed=["title"],
                           new={"title": a.title, "record": f"{entity_type} {entity_id}", "checksum": a.checksum_sha256}, category="documents")
        return Response(_dict(a), status=status.HTTP_201_CREATED)


class AttachmentDetailView(APIView):
    def _get(self, pk):
        try:
            a = Attachment.objects.filter(pk=pk, company=current_company()).select_related("uploaded_by").first()
        except (ValueError, TypeError):
            a = None
        if a is None:
            raise NotFound("No such attachment.")
        return a

    @transaction.atomic
    def delete(self, request, pk):
        a = self._get(pk)
        _guard(request, a.entity_type, "manage")
        audit.record_event(a.company, request.user, "attachment", a.pk, CompanyProfileAuditEvent.Action.DELETED, changed=["title"],
                           previous={"title": a.title, "record": f"{a.entity_type} {a.entity_id}"}, category="documents")
        a.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AttachmentDownloadView(AttachmentDetailView):
    def get(self, request, pk):
        a = self._get(pk)
        _guard(request, a.entity_type, "view")
        return private_file_response(a.file, filename=a.original_filename, attachment=request.query_params.get("inline") != "true",
                                     mime_type=a.mime_type)
