from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from rest_framework import serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import access
from .models import LoginEvent, RolePermission

User = get_user_model()
ROLE_LABELS = dict(access.ROLES)
MANAGE = access.HasCode.for_code("users.manage")
VIEW_ACTIVITY = access.HasCode.for_code("users.view_activity")


class UserSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    username = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    full_name = serializers.SerializerMethodField()
    role = serializers.CharField(read_only=True)
    role_label = serializers.SerializerMethodField()
    is_company_admin = serializers.BooleanField(read_only=True)
    is_finance = serializers.BooleanField(read_only=True)
    permissions = serializers.SerializerMethodField()

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_role_label(self, obj):
        return ROLE_LABELS.get(obj.role, obj.role)

    def get_permissions(self, obj):
        return sorted(obj.codes())


# --------------------------------------------------------------------------- sign in and out

def _client_ip(request):
    return request.META.get("REMOTE_ADDR") or None  # forwarded headers can be forged, so they are not trusted


def record_login_event(request, outcome, user=None, username=""):
    LoginEvent.objects.create(
        user=user, username=(user.username if user else username)[:150], outcome=outcome,
        ip_address=_client_ip(request), user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255],
    )


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        username = request.data.get("username", "")
        user = authenticate(request, username=username, password=request.data.get("password", ""))
        if user is None or not user.is_active:
            # The name is kept only when it belongs to a real user (see LoginEvent.username).
            named = User.objects.filter(username=username).first() if isinstance(username, str) else None
            inactive = named is not None and not named.is_active
            record_login_event(request, LoginEvent.Outcome.INACTIVE if inactive else LoginEvent.Outcome.FAILED, user=named)
            return Response({"detail": "Invalid username or password."}, status=status.HTTP_400_BAD_REQUEST)
        token, _ = Token.objects.get_or_create(user=user)
        update_last_login(None, user)
        record_login_event(request, LoginEvent.Outcome.SUCCESS, user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data})


class LogoutView(APIView):
    def post(self, request):
        record_login_event(request, LoginEvent.Outcome.LOGOUT, user=request.user)
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


# --------------------------------------------------------------------------- audit helpers

def _audit(actor, entity, entity_id, action, *, previous=None, new=None, reason=""):
    """Write the change to the company's append-only history (skipped before a company exists)."""
    from apps.company.models import CompanyProfile
    from apps.company.services import audit

    company = CompanyProfile.objects.first()
    if company is None:
        return
    changed, prev, cur = audit.diff(entity, previous or {}, new or {})
    audit.record_event(company, actor, entity, entity_id, action, changed=changed, previous=prev, new=cur,
                       reason=reason, category="access")


def _user_view(user):
    return {"username": user.username, "email": user.email, "first_name": user.first_name,
            "last_name": user.last_name, "role": user.role, "is_active": user.is_active}


def _row(user):
    return {
        "id": str(user.id), "username": user.username, "email": user.email, "first_name": user.first_name,
        "last_name": user.last_name, "full_name": user.get_full_name() or user.username, "role": user.role,
        "role_label": ROLE_LABELS.get(user.role, user.role), "is_active": user.is_active,
        "is_superuser": user.is_superuser, "last_login": user.last_login, "date_joined": user.date_joined,
    }


def _check_password(password, user=None):
    if not isinstance(password, str) or not password:
        raise ValidationError({"password": "Enter a password."})
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise ValidationError({"password": list(exc.messages)})


def _check_email(email):
    if email in (None, ""):
        return ""
    try:
        validate_email(email)
    except DjangoValidationError:
        raise ValidationError({"email": "Enter a valid email address."})
    return email.strip()


def _check_role(role):
    if role not in ROLE_LABELS:
        raise ValidationError({"role": f"Choose one of: {', '.join(ROLE_LABELS)}."})
    return role


def _another_admin_remains(excluding):
    """An active administrator (or superuser) other than `excluding` exists, so nobody can be locked out."""
    return User.objects.filter(is_active=True).exclude(pk=excluding.pk).filter(
        role=access.ADMIN).exists() or User.objects.filter(is_active=True, is_superuser=True).exclude(pk=excluding.pk).exists()


def _name(value, label):
    value = (value or "").strip() if isinstance(value, str) or value is None else None
    if value is None:
        raise ValidationError({label: "Enter text."})
    if len(value) > 150:
        raise ValidationError({label: "Too long (maximum 150 characters)."})
    return value


# --------------------------------------------------------------------------- users

class UserCollectionView(APIView):
    permission_classes = [MANAGE]

    def get(self, request):
        return Response([_row(u) for u in User.objects.order_by("username")])

    @transaction.atomic
    def post(self, request):
        d = request.data
        username = (d.get("username") or "").strip()
        if not username or len(username) > 150 or " " in username:
            raise ValidationError({"username": "Enter a username of up to 150 characters, without spaces."})
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError({"username": "This username is taken."})
        role = _check_role(d.get("role") or access.EMPLOYEE)
        email = _check_email(d.get("email"))
        user = User(username=username, email=email, role=role, first_name=_name(d.get("first_name"), "first_name"),
                    last_name=_name(d.get("last_name"), "last_name"))
        _check_password(d.get("password"), user)
        user.set_password(d["password"])
        user.save()
        _audit(request.user, "user", user.pk, "created", new=_user_view(user))
        return Response(_row(user), status=status.HTTP_201_CREATED)


class UserDetailView(APIView):
    permission_classes = [MANAGE]

    def _get(self, pk):
        user = User.objects.select_for_update().filter(pk=pk).first()
        if user is None:
            raise NotFound("No such user.")
        return user

    @transaction.atomic
    def patch(self, request, pk):
        user, d = self._get(pk), request.data
        before = _user_view(user)
        if "email" in d:
            user.email = _check_email(d["email"])
        for key in ("first_name", "last_name"):
            if key in d:
                setattr(user, key, _name(d[key], key))
        if "role" in d:
            role = _check_role(d["role"])
            if role != user.role:
                if user == request.user:
                    raise ValidationError({"role": "You cannot change your own role."})
                if user.role == access.ADMIN and not _another_admin_remains(user):
                    raise ValidationError({"role": "This is the only administrator; make someone else an administrator first."})
                user.role = role
        if "is_active" in d:
            active = d["is_active"]
            if not isinstance(active, bool):
                raise ValidationError({"is_active": "Send true or false."})
            if not active and user.is_active:
                if user == request.user:
                    raise ValidationError({"is_active": "You cannot switch off your own account."})
                if (user.role == access.ADMIN or user.is_superuser) and not _another_admin_remains(user):
                    raise ValidationError({"is_active": "This is the only administrator; it cannot be switched off."})
            user.is_active = active
        user.save()
        if not user.is_active:
            Token.objects.filter(user=user).delete()  # an account switched off is signed out at once
        after = _user_view(user)
        action = "updated"
        if before["is_active"] != after["is_active"]:
            action = "activated" if after["is_active"] else "deactivated"
        if before != after:
            _audit(request.user, "user", user.pk, action, previous=before, new=after, reason=(d.get("change_reason") or ""))
        return Response(_row(user))


class UserPasswordView(APIView):
    permission_classes = [MANAGE]

    @transaction.atomic
    def post(self, request, pk):
        user = User.objects.select_for_update().filter(pk=pk).first()
        if user is None:
            raise NotFound("No such user.")
        _check_password(request.data.get("password"), user)
        user.set_password(request.data["password"])
        user.save(update_fields=["password"])
        Token.objects.filter(user=user).delete()  # the old sessions end; the new password is needed
        _audit(request.user, "user", user.pk, "updated", previous={"password": "set"}, new={"password": "changed"},
               reason=(request.data.get("change_reason") or "Password set by an administrator"))
        return Response({"detail": "Password changed. The user must sign in again."})


# --------------------------------------------------------------------------- permission matrix

def _matrix_payload():
    matrix = access.effective_matrix()
    return {
        "roles": [{"value": v, "label": l, "locked": v == access.ADMIN} for v, l in access.ROLES],
        "permissions": [{"code": c, "label": l, "group": g} for c, l, g in access.PERMISSIONS],
        "matrix": {role: sorted(codes) for role, codes in matrix.items()},
        "defaults": {role: sorted(codes) for role, codes in access.DEFAULT_MATRIX.items()},
    }


class AccessMatrixView(APIView):
    permission_classes = [MANAGE]

    def get(self, request):
        return Response(_matrix_payload())

    @transaction.atomic
    def put(self, request):
        reason = (request.data.get("change_reason") or "").strip()
        if not reason:
            raise ValidationError({"change_reason": "Give a reason for changing permissions."})
        submitted = request.data.get("matrix")
        if not isinstance(submitted, dict):
            raise ValidationError({"matrix": "Send {role: [permission codes]}."})
        unknown_roles = set(submitted) - set(access.ROLE_VALUES)
        if unknown_roles:
            raise ValidationError({"matrix": f"Unknown roles: {', '.join(sorted(unknown_roles))}."})
        before = {role: sorted(codes) for role, codes in access.effective_matrix().items()}
        for role, codes in submitted.items():
            if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
                raise ValidationError({"matrix": f"{role}: send a list of permission codes."})
            unknown = set(codes) - set(access.CODES)
            if unknown:
                raise ValidationError({"matrix": f"{role}: unknown permissions {', '.join(sorted(unknown))}."})
            if role == access.ADMIN:
                continue  # the admin role always holds everything
            for code in access.CODES:
                RolePermission.objects.update_or_create(role=role, code=code, defaults={"granted": code in codes})
        access.matrix_changed()
        after = {role: sorted(codes) for role, codes in access.effective_matrix().items()}
        _audit(request.user, "access_matrix", None, "updated", previous=before, new=after, reason=reason)
        return Response(_matrix_payload())


class AccessResetView(APIView):
    permission_classes = [MANAGE]

    @transaction.atomic
    def post(self, request):
        reason = (request.data.get("change_reason") or "").strip()
        if not reason:
            raise ValidationError({"change_reason": "Give a reason for resetting permissions."})
        before = {role: sorted(codes) for role, codes in access.effective_matrix().items()}
        RolePermission.objects.all().delete()
        access.matrix_changed()
        after = {role: sorted(codes) for role, codes in access.effective_matrix().items()}
        _audit(request.user, "access_matrix", None, "updated", previous=before, new=after,
               reason=f"Reset to the defaults. {reason}")
        return Response(_matrix_payload())


# --------------------------------------------------------------------------- history

class LoginHistoryView(APIView):
    permission_classes = [VIEW_ACTIVITY]

    def get(self, request):
        qs = LoginEvent.objects.select_related("user")
        p = request.query_params
        if p.get("user"):
            qs = qs.filter(user_id=p["user"]) if len(p["user"]) == 36 else qs.filter(username__icontains=p["user"])
        if p.get("outcome"):
            qs = qs.filter(outcome=p["outcome"])
        try:
            limit = min(max(int(p.get("limit", 200)), 1), 1000)
        except ValueError:
            raise ValidationError({"limit": "Send a number."})
        return Response([{
            "id": str(e.id), "username": e.username or "(unknown user)", "user_id": str(e.user_id) if e.user_id else None,
            "outcome": e.outcome, "outcome_label": e.get_outcome_display(), "ip_address": e.ip_address,
            "user_agent": e.user_agent, "occurred_at": e.occurred_at,
        } for e in qs[:limit]])


class ActivityView(APIView):
    """User and permission changes, from the company's append-only history."""

    permission_classes = [VIEW_ACTIVITY]

    def get(self, request):
        from apps.company.models import CompanyProfileAuditEvent

        qs = CompanyProfileAuditEvent.objects.filter(entity_type__in=["user", "access_matrix"]).order_by("-occurred_at")[:300]
        return Response([{
            "id": str(e.id), "entity_type": e.entity_type, "entity_id": str(e.entity_id) if e.entity_id else None,
            "action": e.action, "actor": e.actor_label, "changed_fields": e.changed_fields,
            "previous_values": e.previous_values, "new_values": e.new_values, "reason": e.reason,
            "occurred_at": e.occurred_at,
        } for e in qs])
