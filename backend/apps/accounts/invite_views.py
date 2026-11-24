"""Invitation endpoints: signed-in ones need `users.invite`; `lookup` and `accept` are public and throttled."""
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import access, invitations

INVITE = access.HasCode.for_code("users.invite")


class InvitationCollectionView(APIView):
    permission_classes = [INVITE]

    def get(self, request):
        rows = [invitations.row(i, request.user) for i in invitations.listing(request.user, request.query_params.get("status"))]
        return Response({"results": rows, "allowed_roles": invitations.role_options(request.user),
                         "company_name": invitations.company_name(),
                         "default_days": invitations.DEFAULT_DAYS, "min_days": invitations.MIN_DAYS,
                         "max_days": invitations.MAX_DAYS})

    def post(self, request):
        from django.db import transaction

        with transaction.atomic():
            invitation, token = invitations.create_invitation(request.user, request.data)
        # The token is returned this once; only its hash is stored, so it cannot be shown again.
        return Response({**invitations.row(invitation, request.user), "token": token, "path": f"/join/{token}"},
                        status=status.HTTP_201_CREATED)


class InvitationRevokeView(APIView):
    permission_classes = [INVITE]

    def post(self, request, pk):
        invitation = invitations.revoke(request.user, pk)
        return Response(invitations.row(invitation, request.user))


class InvitationRegenerateView(APIView):
    permission_classes = [INVITE]

    def post(self, request, pk):
        invitation, token = invitations.regenerate(request.user, pk)
        return Response({**invitations.row(invitation, request.user), "token": token, "path": f"/join/{token}"},
                        status=status.HTTP_201_CREATED)


class InvitationEmailDraftView(APIView):
    """POST {token, origin}: the prefilled email for this link (the token is needed because only its hash is stored)."""

    permission_classes = [INVITE]

    def post(self, request, pk):
        return Response(invitations.email_draft(request.user, pk, request.data.get("token"), request.data.get("origin")))


class InvitationEmailView(APIView):
    """POST {token, to, cc, subject, body}: email the link. 201 with the log entry (its status may be "failed")."""

    permission_classes = [INVITE]

    def post(self, request, pk):
        from apps.emails import services as mail

        row = invitations.email_link(request.user, pk, request.data.get("token"), request.data)
        return Response(mail.message_dict(row, detail=True), status=status.HTTP_201_CREATED)


class _PublicView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "invite_public"


class InvitationLookupView(_PublicView):
    def post(self, request):
        return Response(invitations.lookup(request.data.get("token")))


class InvitationAcceptView(_PublicView):
    def post(self, request):
        invitations.accept(request.data.get("token"), request.data)
        return Response({"ok": True, "detail": "Your account is ready. You can now sign in."}, status=status.HTTP_201_CREATED)
