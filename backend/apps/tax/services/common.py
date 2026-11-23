"""Small shared helpers: audit events, amount parsing."""
import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit

Action = CompanyProfileAuditEvent.Action
CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(v):
    return Decimal(v or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def audit_event(company, actor, entity, entity_id, action, *, new=None, previous=None, reason=""):
    audit.log(company, actor, entity, entity_id, action, new=audit._jsonable(new or {}), previous=audit._jsonable(previous or {}),
              reason=reason, category="tax")


def amount(value, field, *, allow_zero=True, maximum=Decimal("100000000000")):
    """A non-negative amount with at most two decimal places."""
    if value in (None, ""):
        return ZERO
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError({field: "Enter a number."})
    if not d.is_finite() or d < 0:
        raise ValidationError({field: "Enter an amount of zero or more."})
    if d == 0 and not allow_zero:
        raise ValidationError({field: "Enter an amount more than zero."})
    if d.as_tuple().exponent < -2:
        raise ValidationError({field: "Use at most 2 decimal places."})
    if d > maximum:
        raise ValidationError({field: "That amount is too large."})
    return d


def signed_amount(value, field):
    """Like `amount` but a negative number is allowed (a loss, a refund)."""
    if value in (None, ""):
        return ZERO
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError({field: "Enter a number."})
    if not d.is_finite() or d.as_tuple().exponent < -2 or abs(d) > Decimal("100000000000"):
        raise ValidationError({field: "Enter an amount with at most 2 decimal places."})
    return d


def percent(value, field, default):
    if value in (None, ""):
        return Decimal(default)
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError({field: "Enter a percentage."})
    if not d.is_finite() or d < 0 or d > 100 or d.as_tuple().exponent < -2:
        raise ValidationError({field: "Enter a percentage between 0 and 100 with at most 2 decimals."})
    return d


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
