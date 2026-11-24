"""Invitation links: the list of details a new member must enter, and the rules for creating, withdrawing and accepting a link.

HOW TO CHANGE WHAT THE INVITEE ENTERS (one place: `INVITE_FIELDS` below)
    Each entry is {key, label, type, required, help}. The server validates the submitted form from this list and the same list
    is sent to the browser (`invitations/lookup/`), which renders the form from it, so adding, removing, renaming or making a
    field optional is an edit to this list only.
    * `type` is one of: text, textarea, email, tel, date, username, password, select (a select also needs `options`).
      An optional `max_length` overrides the default length limit. A password field with `confirm_of: "<key>"` is the
      "type it again" box for that password field.
    * Special keys: `full_name`, `username`, `password` create the user account (the password is never stored anywhere else).
      `designation`, `phone`, `personal_email`, `department`, `date_of_birth`, `address`, `emergency_name`, `emergency_phone`
      are saved in their own column of `MemberProfile`. Any other key is saved in `MemberProfile.extra` under that key, so a new
      question needs no migration.
    * The invitee never chooses an access role: it is fixed by the person who creates the link (`access.INVITE_GRANTS`).
      `designation` is a free-text job title only; it grants nothing.
"""
import datetime
import hashlib
import re
import secrets
from datetime import timedelta

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import UnicodeUsernameValidator
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from . import access
from .models import Invitation, MemberProfile

User = get_user_model()

INVITE_FIELDS = [
    {"key": "full_name", "label": "Full name", "type": "text", "required": True, "help": "As you want it to appear in the ERP."},
    {"key": "username", "label": "Username", "type": "username", "required": True,
     "help": "Used to sign in. Letters, digits and . @ + - _ only, no spaces."},
    {"key": "password", "label": "Password", "type": "password", "required": True,
     "help": "At least 10 characters; not a common or purely numeric password."},
    {"key": "password_confirm", "label": "Confirm password", "type": "password", "required": True, "confirm_of": "password",
     "help": ""},
    {"key": "phone", "label": "Mobile number", "type": "tel", "required": True, "help": "Digits, spaces and + ( ) - only."},
    {"key": "personal_email", "label": "Personal email", "type": "email", "required": True, "help": ""},
    {"key": "designation", "label": "Your role in the company", "type": "text", "required": True,
     "help": "Your job title, for example Site Engineer. It does not change what you can open in the ERP."},
    {"key": "department", "label": "Department", "type": "text", "required": False, "help": ""},
    {"key": "date_of_birth", "label": "Date of birth", "type": "date", "required": False, "help": ""},
    {"key": "address", "label": "Address", "type": "textarea", "required": False, "help": ""},
    {"key": "emergency_name", "label": "Emergency contact name", "type": "text", "required": False, "help": ""},
    {"key": "emergency_phone", "label": "Emergency contact phone", "type": "tel", "required": False, "help": ""},
]

COLUMN_KEYS = ("designation", "phone", "personal_email", "department", "date_of_birth", "address", "emergency_name",
               "emergency_phone")
ACCOUNT_KEYS = ("full_name", "username", "password")
COLUMN_LIMITS = {"designation": 150, "phone": 30, "personal_email": 254, "department": 120, "emergency_name": 120,
                 "emergency_phone": 30, "address": 600}
DEFAULT_LIMITS = {"text": 150, "textarea": 600, "email": 254, "tel": 30, "select": 100, "username": 150}

DEFAULT_DAYS, MIN_DAYS, MAX_DAYS = 7, 1, 30

# Roles that also get a People (HR) record, so HR sees the new member in the directory. Partners and administrators are not
# employees, so they get the account and profile only.
EMPLOYEE_LIKE_ROLES = (access.EMPLOYEE, access.PROJECT_MANAGER, access.HR_MANAGER, access.FINANCE_MANAGER)

PHONE_RE = re.compile(r"^[0-9+()\-. ]{7,20}$")
NOT_VALID = "This invitation link is not valid. It may have expired, already been used or been withdrawn."


def now():
    """The current time. Tests replace this to move the clock."""
    return timezone.now()


def hash_token(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def company_name():
    from apps.company.models import CompanyProfile

    company = CompanyProfile.objects.first()
    return (company.display_name if company else "") or "the company"


def fields_for_client():
    return [dict(f) for f in INVITE_FIELDS]


# --------------------------------------------------------------------------- who may do what

def _actor_roles(user):
    return access.invite_roles_for(user)


def can_manage(user, invitation):
    return invitation.role in _actor_roles(user)


def visible_to(user):
    roles = _actor_roles(user)
    return Invitation.objects.filter(Q(created_by=user) | Q(role__in=roles)).select_related("created_by", "used_by", "revoked_by")


def role_options(user):
    labels = dict(access.ROLES)
    return [{"value": r, "label": labels[r]} for r in _actor_roles(user)]


# --------------------------------------------------------------------------- creating and withdrawing

def _clean_days(value):
    if value in (None, ""):
        return DEFAULT_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise ValidationError({"expires_in_days": f"Enter a whole number of days from {MIN_DAYS} to {MAX_DAYS}."})
    if isinstance(value, bool) or not MIN_DAYS <= days <= MAX_DAYS:
        raise ValidationError({"expires_in_days": f"Enter a whole number of days from {MIN_DAYS} to {MAX_DAYS}."})
    return days


def _clean_str(value, key, limit):
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValidationError({key: "Enter text."})
    value = value.strip()
    if len(value) > limit:
        raise ValidationError({key: f"Too long (maximum {limit} characters)."})
    return value


def _invitation_view(i):
    return {"role": i.role, "name_hint": i.name_hint, "email_hint": i.email_hint, "note": i.note,
            "status": i.status_at(now()), "expires_at": i.expires_at.isoformat() if i.expires_at else None}


def audit(actor, invitation, action, *, previous=None, new=None, reason=""):
    from apps.accounts.views import _audit

    _audit(actor, "invitation", invitation.pk, action, previous=previous, new=new, reason=reason)


def create_invitation(actor, data):
    """Create a link for `data` (role, name_hint, email_hint, note, expires_in_days). Returns (invitation, token)."""
    allowed = _actor_roles(actor)
    role = data.get("role")
    if role not in access.ROLE_VALUES:
        raise ValidationError({"role": f"Choose one of: {', '.join(allowed)}."})
    if role not in allowed:
        raise PermissionDenied("You may not invite someone with this access role.")
    email = _clean_str(data.get("email_hint"), "email_hint", 254)
    if email:
        try:
            validate_email(email)
        except DjangoValidationError:
            raise ValidationError({"email_hint": "Enter a valid email address."})
    name = _clean_str(data.get("name_hint"), "name_hint", 150)
    note = _clean_str(data.get("note"), "note", 500)
    days = _clean_days(data.get("expires_in_days"))
    return _make(actor, role=role, name_hint=name, email_hint=email, note=note, days=days)


def _make(actor, *, role, name_hint, email_hint, note, days):
    token = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(
        token_hash=hash_token(token), role=role, name_hint=name_hint, email_hint=email_hint, note=note, created_by=actor,
        expires_at=now() + timedelta(days=days))
    audit(actor, invitation, "created", new=_invitation_view(invitation))
    return invitation, token


def _locked(pk):
    invitation = Invitation.objects.select_for_update().filter(pk=pk).first()
    if invitation is None:
        raise NotFound("No such invitation.")
    return invitation


def _revoke_locked(actor, invitation, reason):
    before = _invitation_view(invitation)
    invitation.revoked_at, invitation.revoked_by = now(), actor
    invitation.save(update_fields=["revoked_at", "revoked_by"])
    audit(actor, invitation, "deactivated", previous=before, new=_invitation_view(invitation), reason=reason)


@transaction.atomic
def revoke(actor, pk):
    invitation = _locked(pk)
    if not can_manage(actor, invitation) and invitation.created_by_id != actor.pk:
        raise PermissionDenied("You may not change this invitation.")
    if invitation.used_at:
        raise ValidationError({"detail": "This link has already been used."})
    if invitation.revoked_at:
        raise ValidationError({"detail": "This link has already been withdrawn."})
    _revoke_locked(actor, invitation, "Invitation withdrawn")
    return invitation


@transaction.atomic
def regenerate(actor, pk):
    """Withdraw the link (if it is still open) and create a fresh one with the same settings."""
    old = _locked(pk)
    if not can_manage(actor, old):
        raise PermissionDenied("You may not give this access role.")
    if old.used_at:
        raise ValidationError({"detail": "This link has already been used; nothing to replace."})
    if not old.revoked_at:
        _revoke_locked(actor, old, "Replaced by a new link")
    days = max(MIN_DAYS, min(MAX_DAYS, round((old.expires_at - old.created_at).total_seconds() / 86400)))
    return _make(actor, role=old.role, name_hint=old.name_hint, email_hint=old.email_hint, note=old.note, days=days)


# --------------------------------------------------------------------------- emailing the link

_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")


def _open_for_email(actor, pk, token):
    """The pending invitation `pk`, checked against the one-time token the person was shown (only its hash is stored)."""
    invitation = Invitation.objects.filter(pk=pk).first()
    if invitation is None:
        raise NotFound("No such invitation.")
    if not can_manage(actor, invitation) and invitation.created_by_id != actor.pk:
        raise PermissionDenied("You may not send this invitation.")
    if not isinstance(token, str) or hash_token(token) != invitation.token_hash:
        raise ValidationError({"token": "This is not the link of this invitation. Create a new link and email that."})
    if invitation.status_at(now()) != Invitation.Status.PENDING:
        raise ValidationError({"detail": "This link can no longer be used (used, expired or withdrawn). Create a new link."})
    return invitation


def join_link(token, origin):
    """The full join link. APP_BASE_URL (server setting) wins; otherwise the address the sender has open in the browser."""
    from django.conf import settings

    base = getattr(settings, "APP_BASE_URL", "") or (origin or "").strip().rstrip("/")
    if not _ORIGIN.match(base or "") and not getattr(settings, "APP_BASE_URL", ""):
        raise ValidationError({"origin": "Send the web address of the app, for example https://erp.example.com."})
    return f"{base}/join/{token}"


def email_draft(actor, pk, token, origin):
    """The prefilled message for emailing the link: to the invitee's address, with the link, role and expiry date."""
    from apps.emails import services as mail
    from apps.company.models import CompanyProfile

    invitation = _open_for_email(actor, pk, token)
    company = CompanyProfile.objects.first()
    name = company_name()
    role = dict(access.ROLES).get(invitation.role, invitation.role)
    lines = [f"Hello{' ' + invitation.name_hint if invitation.name_hint else ''},", "",
             f"You have been invited to join {name} in our ERP as {role}.",
             f"Open this link to create your account. It works once and expires on {timezone.localtime(invitation.expires_at).strftime('%d %b %Y')}:",
             "", join_link(token, origin), ""]
    if invitation.note:
        lines += [invitation.note, ""]
    lines.append(mail.sign_off(company) if company else "Regards")
    return {"to": [invitation.email_hint] if invitation.email_hint else [], "cc": [],
            "subject": f"Your invitation to join {name}", "body": "\n".join(lines), "attachments": [],
            "label": f"Invitation for {invitation.name_hint or invitation.email_hint or role}",
            "server": mail.server_status(), "from_address": mail.from_address(company) if company else ""}


def email_link(actor, pk, token, data):
    """Email the link. The message is sent as written, but the logged copy has the token replaced by "[hidden]" (the link
    is a password-like secret and only its hash is kept), so the log can never be used to join. Returns the log row."""
    from apps.emails import services as mail
    from apps.company.models import CompanyProfile

    invitation = _open_for_email(actor, pk, token)
    company = CompanyProfile.objects.first()
    if company is None:
        raise ValidationError({"detail": "Set up the company first."})
    row = mail.send(company, actor, kind="invitation", to=data.get("to"), cc=data.get("cc"), subject=data.get("subject"),
                    body=data.get("body"), object_id=invitation.pk,
                    object_label=f"Invitation for {invitation.name_hint or invitation.email_hint or dict(access.ROLES).get(invitation.role)}",
                    url="/company/invite", hide=[token])
    audit(actor, invitation, "updated", new={"emailed": row.get_status_display()},
          reason=f"Invitation link emailed ({row.get_status_display().lower()})")
    return row


def row(i, viewer):
    return {
        "id": str(i.id), "status": i.status_at(now()), "role": i.role, "role_label": dict(access.ROLES).get(i.role, i.role),
        "name_hint": i.name_hint, "email_hint": i.email_hint, "note": i.note,
        "created_by": _person(i.created_by), "created_at": i.created_at, "expires_at": i.expires_at,
        "used_at": i.used_at, "used_by": _person(i.used_by), "revoked_at": i.revoked_at, "revoked_by": _person(i.revoked_by),
        "can_manage": can_manage(viewer, i),
    }


def _person(user):
    return None if user is None else {"id": str(user.pk), "name": user.get_full_name() or user.username}


def listing(viewer, status=None):
    qs, when = visible_to(viewer), now()
    if status:
        if status not in Invitation.Status.values:
            raise ValidationError({"status": f"Choose one of: {', '.join(Invitation.Status.values)}."})
        pending = Q(revoked_at__isnull=True, used_at__isnull=True)
        qs = qs.filter({
            "revoked": Q(revoked_at__isnull=False), "used": Q(revoked_at__isnull=True, used_at__isnull=False),
            "expired": pending & Q(expires_at__lte=when), "pending": pending & Q(expires_at__gt=when)}[status])
    return qs[:500]


# --------------------------------------------------------------------------- the public side

def find_open(token):
    """The invitation for this token if it can still be used; otherwise None (the caller gives one generic answer)."""
    if not isinstance(token, str) or not 20 <= len(token) <= 200:
        return None
    invitation = Invitation.objects.filter(token_hash=hash_token(token)).first()
    if invitation is None or invitation.status_at(now()) != Invitation.Status.PENDING:
        return None
    return invitation


def lookup(token):
    invitation = find_open(token)
    if invitation is None:
        raise NotFound(NOT_VALID)
    return {
        "company_name": company_name(), "role": invitation.role, "role_label": dict(access.ROLES).get(invitation.role, invitation.role),
        "expires_at": invitation.expires_at, "name_hint": invitation.name_hint, "email_hint": invitation.email_hint,
        "fields": fields_for_client(),
    }


def _clean_value(field, raw):
    """Validate one submitted answer; returns the cleaned text ('' when an optional field is left empty)."""
    key, kind, label = field["key"], field["type"], field["label"]
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if field.get("required"):
            raise ValidationError({key: f"{label}: this field is required."})
        return ""
    if not isinstance(raw, str):
        raise ValidationError({key: f"{label}: enter text."})
    value = raw if kind == "password" else raw.strip()
    limit = field.get("max_length") or COLUMN_LIMITS.get(key) or DEFAULT_LIMITS.get(kind, 150)
    if len(value) > (1000 if kind == "password" else limit):
        raise ValidationError({key: f"{label}: too long (maximum {limit} characters)."})
    if kind == "email":
        try:
            validate_email(value)
        except DjangoValidationError:
            raise ValidationError({key: f"{label}: enter a valid email address."})
    elif kind == "tel" and not PHONE_RE.match(value):
        raise ValidationError({key: f"{label}: enter digits, spaces and + ( ) - only (7 to 20 characters)."})
    elif kind == "date":
        try:
            parsed = datetime.date.fromisoformat(value)
        except ValueError:
            raise ValidationError({key: f"{label}: enter a valid date."})
        today = datetime.date.today()
        if key == "date_of_birth" and (parsed >= today or parsed.year < 1920 or parsed > today - timedelta(days=365 * 14)):
            raise ValidationError({key: f"{label}: enter a valid date of birth."})
    elif kind == "username":
        if " " in value:
            raise ValidationError({key: "Usernames cannot contain spaces."})
        try:
            UnicodeUsernameValidator()(value)
        except DjangoValidationError:
            raise ValidationError({key: "Use letters, digits and . @ + - _ only."})
        if User.objects.filter(username__iexact=value).exists():
            raise ValidationError({key: "This username is already taken. Choose another."})
    elif kind == "select" and value not in [str(o) for o in field.get("options", [])]:
        raise ValidationError({key: f"{label}: choose one of the listed options."})
    return value


def clean_submission(data):
    """Validate everything against INVITE_FIELDS. Returns {key: cleaned text}; raises one 400 listing every problem."""
    errors, clean = {}, {}
    for field in INVITE_FIELDS:
        try:
            clean[field["key"]] = _clean_value(field, data.get(field["key"]))
        except ValidationError as exc:
            errors.update({k: (v[0] if isinstance(v, list) else v) for k, v in exc.detail.items()})
    for field in INVITE_FIELDS:
        confirm_of = field.get("confirm_of")
        if confirm_of and field["key"] not in errors and clean.get(field["key"]) != clean.get(confirm_of):
            errors[field["key"]] = f"{field['label']}: the two passwords do not match."
    if errors:
        raise ValidationError(errors)
    return clean


def _split_name(full):
    parts = full.split()
    first, last = (parts[0], " ".join(parts[1:])) if parts else ("", "")
    return first[:150], last[:150]


@transaction.atomic
def accept(token, data):
    """Create the account for a valid token. Returns (user, employee_or_None). Raises NotFound for a bad link."""
    invitation = find_open(token)
    if invitation is None:
        raise NotFound(NOT_VALID)
    invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)  # one use only, even for two quick submits
    if invitation.status_at(now()) != Invitation.Status.PENDING:
        raise NotFound(NOT_VALID)
    clean = clean_submission(data)
    first, last = _split_name(clean["full_name"])
    user = User(username=clean["username"], first_name=first, last_name=last, email=clean.get("personal_email", ""),
                role=invitation.role)
    try:
        validate_password(clean["password"], user=user)
    except DjangoValidationError as exc:
        raise ValidationError({"password": list(exc.messages)})
    user.set_password(clean["password"])
    user.save()
    columns = {k: clean.get(k, "") for k in COLUMN_KEYS if k in clean}
    if columns.get("date_of_birth"):
        columns["date_of_birth"] = datetime.date.fromisoformat(columns["date_of_birth"])
    elif "date_of_birth" in columns:
        columns["date_of_birth"] = None
    extra = {f["key"]: clean[f["key"]] for f in INVITE_FIELDS
             if f["key"] not in COLUMN_KEYS and f["key"] not in ACCOUNT_KEYS and not f.get("confirm_of") and clean.get(f["key"])}
    profile = MemberProfile.objects.create(user=user, extra=extra, invited_by=invitation.created_by, invitation=invitation, **columns)
    employee = _create_employee(user, profile, invitation)
    before = _invitation_view(invitation)
    invitation.used_at, invitation.used_by = now(), user
    invitation.save(update_fields=["used_at", "used_by"])
    audit(user, invitation, "updated", previous=before, new=_invitation_view(invitation), reason="Invitation accepted")
    from apps.accounts.views import _audit, _user_view

    _audit(user, "user", user.pk, "created", new=_user_view(user), reason=f"Joined through an invitation from "
           f"{invitation.created_by.username if invitation.created_by else 'a removed user'}")
    return user, employee


def _create_employee(user, profile, invitation):
    """Give a staff member a People record linked to the account. Skipped when the HR app is not installed or the role is not
    a staff role. It runs inside the caller's transaction, so a failure here cancels the whole sign-up."""
    if invitation.role not in EMPLOYEE_LIKE_ROLES or not django_apps.is_installed("apps.hr"):
        return None
    from apps.company.models import CompanyProfile
    from apps.hr.models import Department, Designation
    from apps.hr.services import people

    company = CompanyProfile.objects.first()
    if company is None:
        return None
    data = {"first_name": user.first_name, "last_name": user.last_name, "phone": profile.phone, "personal_email": profile.personal_email,
            "address": profile.address, "emergency_name": profile.emergency_name, "emergency_phone": profile.emergency_phone,
            "date_of_joining": datetime.date.today().isoformat(), "employment_type": "full_time", "user_id": str(user.pk)}
    if profile.date_of_birth:
        data["date_of_birth"] = profile.date_of_birth.isoformat()
    if profile.designation:
        designation = (Designation.objects.filter(company=company, name__iexact=profile.designation[:120]).first()
                       or Designation.objects.create(company=company, name=profile.designation[:120]))
        data["designation"] = str(designation.pk)
    if profile.department:
        department = Department.objects.filter(company=company, name__iexact=profile.department).first()
        if department:
            data["department"] = str(department.pk)
    try:
        return people.create(company, data, user)
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages})
