"""Small helpers shared by the HR services and views: settings, dates, validation of input, audit and CSV."""
import calendar
import csv
import datetime
import io
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.http import HttpResponse

from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit

from ..models import DEFAULT_PT_SLABS, HRSettings

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
Action = CompanyProfileAuditEvent.Action


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def half(value):
    """Round to the nearest half day."""
    return (Decimal(value) * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2


def get_settings(company):
    s, created = HRSettings.objects.get_or_create(company=company, defaults={
        "pt_slabs": DEFAULT_PT_SLABS, "weekly_off_days": [6]})
    return s


def month_start(d):
    return d.replace(day=1)


def month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d, n):
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return datetime.date(y, m + 1, 1)


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += datetime.timedelta(days=1)


def parse_month(value, label="month"):
    """'2026-08' or '2026-08-01' -> the first day of that month."""
    value = str(value or "").strip()
    try:
        if len(value) == 7:
            return datetime.date.fromisoformat(value + "-01")
        return month_start(datetime.date.fromisoformat(value))
    except ValueError:
        raise ValidationError({label: "Use a month in YYYY-MM format."})


# --------------------------------------------------------------------------- input

def text(data, key, label, limit, required=False, default=""):
    value = data.get(key, default)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError({key: f"{label} must be text."})
    value = value.strip()
    if required and not value:
        raise ValidationError({key: f"Enter {label.lower()}."})
    if len(value) > limit:
        raise ValidationError({key: f"{label} is too long (at most {limit} characters)."})
    return value


def date_value(data, key, label, required=False):
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise ValidationError({key: f"Enter {label.lower()}."})
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({key: f"{label}: use a date in YYYY-MM-DD format."})


def decimal(data, key, label, *, places=2, low=Decimal("0"), high=Decimal("100000000"), default=None, required=False):
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise ValidationError({key: f"Enter {label.lower()}."})
        return default
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError({key: f"{label} must be a number."})
    if not d.is_finite():
        raise ValidationError({key: f"{label} must be a number."})
    if d != d.quantize(Decimal(1).scaleb(-places)):  # "0.00" is fine for one place; "0.05" is not
        raise ValidationError({key: f"{label}: use at most {places} decimal places."})
    if d < low or d > high:
        raise ValidationError({key: f"{label} must be between {low} and {high}."})
    return d


def boolean(data, key, default=False):
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    if str(value).lower() in ("true", "1"):
        return True
    if str(value).lower() in ("false", "0"):
        return False
    raise ValidationError({key: "Send true or false."})


def choice(data, key, label, choices, default=None, required=False):
    value = data.get(key, default)
    if value in (None, ""):
        if required:
            raise ValidationError({key: f"Choose {label.lower()}."})
        return default
    if value not in choices:
        raise ValidationError({key: f"Choose a valid {label.lower()}."})
    return value


# --------------------------------------------------------------------------- audit

def log(company, actor, entity, entity_id, action, *, new=None, previous=None, reason=""):
    return audit.log(company, actor, entity, entity_id, action, new=new, previous=previous, reason=reason, category="hr")


def snapshot(instance, fields):
    out = {}
    for f in fields:
        v = getattr(instance, f)
        out[f] = v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else (str(v) if isinstance(v, Decimal) else v)
        if hasattr(v, "pk"):
            out[f] = str(v.pk)
    return out


# --------------------------------------------------------------------------- CSV

def csv_response(filename, rows):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    response = HttpResponse("﻿" + buf.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    return response
