"""Small helpers shared by the compliance services: audit, reference numbers, users, validated inputs."""
import datetime
import re
import uuid
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.company.constants import DocType
from apps.company.services import audit, numbering


def log(company, actor, entity, entity_id, action, *, new=None, previous=None, reason=""):
    audit.log(company, actor, entity, entity_id, action, new=new, previous=previous, reason=reason, category="compliance")


def new_reference(company, code, *, actor=None, on_date=None):
    """A reference from the COMPLIANCE_FILING sequence, with its 'CF' token replaced by `code` (CON, CAS, LEV, POL).

    Using one counter keeps every number unique and carries the 2-digit year and DDMM that the numbering service builds in.
    """
    ref = numbering.allocate_number(company, DocType.COMPLIANCE_FILING, actor=actor, on_date=on_date or datetime.date.today()).reference_number
    if code == "CF":
        return ref
    return re.sub(r"(?<=[/\-])CF(?=[/\-])", code, ref, count=1)


def user_name(user):
    return (user.get_full_name() or user.get_username()) if user else ""


def assignable_users():
    User = get_user_model()
    return User.objects.filter(is_active=True).order_by("first_name", "username")


def get_user(value, field="assignee"):
    """An active user by id, or None for an empty value."""
    if value in (None, ""):
        return None
    try:
        user = assignable_users().filter(pk=value).first()
    except (ValueError, ValidationError, TypeError):
        user = None
    if user is None:
        raise ValidationError({field: "Choose an active user."})
    return user


def parse_date(value, field, *, required=False):
    if value in (None, ""):
        if required:
            raise ValidationError({field: "Enter a date."})
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({field: "Use a date in YYYY-MM-DD format."})


def parse_money(value, field):
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ValidationError({field: "Enter an amount."})
    if not d.is_finite() or d < 0:
        raise ValidationError({field: "Enter an amount of zero or more."})
    if d.as_tuple().exponent < -2:
        raise ValidationError({field: "Use at most two decimal places."})
    if d >= Decimal("1e14"):
        raise ValidationError({field: "That amount is too large."})
    return d


def clean_text(value, field, *, max_length, required=False, label=None):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError({field: "Enter text."})
    value = value.strip()
    if required and not value:
        raise ValidationError({field: f"Enter {label or field.replace('_', ' ')}."})
    if len(value) > max_length:
        raise ValidationError({field: f"Use at most {max_length} characters."})
    return value


def pick(value, field, choices, *, default=None):
    if value in (None, ""):
        if default is not None:
            return default
        raise ValidationError({field: "Choose one."})
    keys = [c[0] for c in choices]
    if value not in keys:
        raise ValidationError({field: "Choose one of: " + ", ".join(keys) + "."})
    return value


def clean_reminders(value, field="reminder_days"):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = [int(x) for x in value.replace(";", ",").split(",") if x.strip()]
        except ValueError:
            raise ValidationError({field: "Give whole numbers of days, such as 15, 7, 1."})
    if not isinstance(value, list) or len(value) > 8 or not all(isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 120 for n in value):
        raise ValidationError({field: "Give up to 8 whole numbers of days (0 to 120), such as 15, 7, 1."})
    return sorted(set(value), reverse=True)


def flag(value, field):
    if not isinstance(value, bool):
        raise ValidationError({field: "Send true or false."})
    return value


def snapshot(obj, fields):
    out = {}
    for f in fields:
        v = getattr(obj, f)
        if isinstance(v, (datetime.date, Decimal, uuid.UUID)):
            v = str(v)
        out[f] = v
    return out
