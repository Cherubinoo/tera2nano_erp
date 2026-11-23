"""Small helpers shared by the purchases services: money, input checking and audit events."""
import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
Action = CompanyProfileAuditEvent.Action


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def amount_from(value, label="Amount", field="amount"):
    """A positive amount with at most two decimal places, from user input."""
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError({field: f"{label} must be a number."})
    if not d.is_finite() or d <= 0:
        raise ValidationError({field: f"{label} must be more than zero."})
    if d.as_tuple().exponent < -2:
        raise ValidationError({field: f"{label}: use at most 2 decimal places."})
    if d > Decimal("100000000000"):
        raise ValidationError({field: f"{label} is too large."})
    return d


def optional_amount(value, label, field, high=Decimal("100000000000")):
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError({field: f"{label} must be a number."})
    if not d.is_finite() or d < 0 or d > high:
        raise ValidationError({field: f"{label} must be between 0 and {high}."})
    if d.as_tuple().exponent < -2:
        raise ValidationError({field: f"{label}: use at most 2 decimal places."})
    return d


def parse_date(value, field, required=False):
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


def text(value, field, limit, required=False, label=None):
    label = label or field.replace("_", " ").capitalize()
    value = "" if value is None else value
    if not isinstance(value, str):
        raise ValidationError({field: f"{label} must be text."})
    value = value.strip()
    if required and not value:
        raise ValidationError({field: f"Enter {label.lower()}."})
    if len(value) > limit:
        raise ValidationError({field: f"{label} is too long (at most {limit} characters)."})
    return value


def log(company, actor, entity, instance_id, action, *, new=None, previous=None, reason=""):
    audit.log(company, actor, entity, instance_id, action, new=new, previous=previous, reason=reason, category="purchases")


def months_back(today, n):
    """[(year, month)] for the last n months ending with today's, oldest first."""
    months, (year, month) = [], (today.year, today.month)
    for _ in range(n):
        months.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    months.reverse()
    return months
