"""Email API: server status and company email settings, the send dialog (draft + send), test email, and the Sent emails log."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import access
from apps.company.api import current_company

from . import kinds, services
from .models import EmailMessage

MANAGE = access.HasCode.for_code("email.manage")
PAGE_SIZE, MAX_PAGE_SIZE = 25, 100


class StatusView(APIView):
    """Whether email is set up on the server: shown in every send dialog. Any signed-in user."""

    def get(self, request):
        company = current_company()
        return Response({**services.server_status(), "from_address": services.from_address(company)})


class SettingsView(APIView):
    """Sender name, reply-to, signature (email.manage) and automatic payment reminders (email.manage or sales.manage)."""

    def get(self, request):
        user = request.user
        if not (user.has_code("email.manage") or user.has_code("sales.view")):
            raise PermissionDenied("Your role does not allow this.")
        data = services.settings_dict(current_company())
        data["can_edit"] = user.has_code("email.manage")
        data["can_edit_reminders"] = user.has_code("email.manage") or user.has_code("sales.manage")
        return Response(data)

    def patch(self, request):
        user = request.user
        if user.has_code("email.manage"):
            allowed = services.SETTINGS_FIELDS
        elif user.has_code("sales.manage"):
            allowed = ("auto_reminders",)
        else:
            raise PermissionDenied("Your role does not allow this.")
        company = current_company()
        services.update_settings(company, user, request.data, allowed=allowed)
        return self.get(request)


class TestView(APIView):
    permission_classes = [MANAGE]

    def post(self, request):
        to = request.data.get("to") or request.user.email
        if not to:
            raise DjangoValidationError({"to": "Enter the address to send the test email to."})
        row = services.send_test(current_company(), request.user, to)
        return Response(services.message_dict(row, detail=True), status=http.HTTP_201_CREATED)


class DraftView(APIView):
    """GET ?kind=&id=: what the send dialog opens with. Needs the same permission as the record's own action."""

    def get(self, request):
        p = request.query_params
        return Response(kinds.draft(current_company(), request.user, p.get("kind", ""), p.get("id", "")))


class SendView(APIView):
    """POST {kind, id, to, cc, subject, body}. Answers 201 with the log entry, whose status may be "failed" (with the reason):
    a mail problem is reported, never raised, and the record itself is not changed."""

    def post(self, request):
        d = request.data
        row = kinds.send(current_company(), request.user, d.get("kind", ""), d.get("id", ""), d)
        return Response(services.message_dict(row, detail=True), status=http.HTTP_201_CREATED)


def _message(pk, company):
    try:
        row = EmailMessage.objects.select_related("sent_by", "company").filter(pk=pk, company=company).first()
    except (ValueError, DjangoValidationError):
        row = None
    if row is None:
        raise NotFound("No such email.")
    return row


class MessageCollectionView(APIView):
    permission_classes = [MANAGE]

    def get(self, request):
        p = request.query_params
        qs = services.search(current_company(), q=p.get("q", ""), status=p.get("status", ""), kind=p.get("kind", ""))
        try:
            size = max(1, min(int(p.get("page_size", PAGE_SIZE)), MAX_PAGE_SIZE))
            page = max(1, int(p.get("page", 1)))
        except ValueError:
            raise DjangoValidationError({"page": "Page numbers must be whole numbers."})
        count = qs.count()
        rows = list(qs[(page - 1) * size: page * size])
        resent = set(EmailMessage.objects.filter(resend_of__in=rows).values_list("resend_of_id", flat=True))
        for r in rows:
            r.resent = r.pk in resent
        counts = {s: services.search(current_company(), status=s).count() for s in EmailMessage.Status.values}
        return Response({"results": [services.message_dict(r) for r in rows], "count": count, "page": page, "page_size": size,
                         "counts": counts, "kinds": [{"value": k, "label": v} for k, v in services.KIND_LABELS.items()],
                         "server": services.server_status()})


class MessageDetailView(APIView):
    permission_classes = [MANAGE]

    def get(self, request, pk):
        row = _message(pk, current_company())
        row.resent = row.resends.exists()
        return Response(services.message_dict(row, detail=True))


class MessageResendView(APIView):
    permission_classes = [MANAGE]

    def post(self, request, pk):
        row = _message(pk, current_company())
        new = services.resend(row, request.user)
        return Response(services.message_dict(new, detail=True), status=http.HTTP_201_CREATED)
