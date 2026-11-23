"""Shared helpers for the CRM services: money, defaults, audit, applying request data to a record."""
import datetime
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models

from apps.company.models import CompanyProfileAuditEvent
from apps.company.services import audit

from .. import constants as c
from ..models import LeadSource, LostReason, PipelineStage

Action = CompanyProfileAuditEvent.Action
CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def decimal_from(value, label, *, places=2, maximum=Decimal("100000000000")):
    """A non-negative decimal from user input, or a ValidationError keyed by `label`."""
    if value in (None, ""):
        return Decimal(0)
    try:
        d = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValidationError({label: "Enter a number."})
    if not d.is_finite() or d < 0:
        raise ValidationError({label: "Enter zero or more."})
    if d.as_tuple().exponent < -places:
        raise ValidationError({label: f"Use at most {places} decimal places."})
    if d > maximum:
        raise ValidationError({label: "This number is too large."})
    return d


def log(company, actor, entity, instance_id, action, *, new=None, previous=None, reason=""):
    return audit.log(company, actor, entity, instance_id, action, new=new, previous=previous, reason=reason, category="crm")


def snapshot(instance, fields):
    """{field: value} for the audit trail; related records are shown by name."""
    out = {}
    for f in fields:
        v = getattr(instance, f)
        if isinstance(v, models.Model):
            v = str(v)
        elif isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
            v = v.isoformat()
        elif isinstance(v, (Decimal, uuid.UUID)):
            v = str(v)
        out[f] = v
    return out


def apply_fields(instance, data, fields, *, skip_clean=False):
    """Set `fields` found in `data` on a model instance, converting each value with its model field, then validate.

    Every problem is reported against its field name in one ValidationError.
    """
    errors = {}
    for name in fields:
        if name not in data:
            continue
        field = instance._meta.get_field(name)
        value = data[name]
        if isinstance(value, str):
            value = value.strip()
        if value in (None, ""):
            if isinstance(field, (models.CharField, models.TextField)):
                value = ""
            elif field.null:
                value = None
            elif isinstance(field, models.BooleanField):
                errors[name] = "Send true or false."
                continue
            elif field.has_default():
                value = field.get_default()
            else:
                errors[name] = "This field is required."
                continue
        else:
            if isinstance(field, models.BooleanField) and not isinstance(value, bool):
                errors[name] = "Send true or false."
                continue
            if isinstance(field, models.DecimalField):
                try:
                    value = decimal_from(value, name, places=field.decimal_places)
                except ValidationError as exc:
                    errors[name] = exc.message_dict[name][0]
                    continue
            try:
                value = field.to_python(value)
                if field.choices and value not in dict(field.choices):
                    raise ValidationError(f"'{value}' is not one of the choices.")
            except ValidationError as exc:
                errors[name] = exc.messages[0]
                continue
        setattr(instance, name, value)
    if errors:
        raise ValidationError(errors)
    if not skip_clean:
        exclude = [f.name for f in instance._meta.concrete_fields if f.name in ("reference_code", "company")]
        instance.full_clean(exclude=exclude, validate_unique=False, validate_constraints=False)


# --------------------------------------------------------------------------- default settings lists

def ensure_defaults(company):
    """Create the starting stages, lead sources and lost reasons the first time the CRM is used."""
    if not PipelineStage.objects.filter(company=company).exists():
        for i, (name, prob, colour, kind) in enumerate(c.DEFAULT_STAGES):
            PipelineStage.objects.create(company=company, name=name, probability=prob, colour=colour, kind=kind, position=i + 1)
    if not LeadSource.objects.filter(company=company).exists():
        for i, (key, name) in enumerate(c.DEFAULT_SOURCES):
            LeadSource.objects.create(company=company, key=key, name=name, position=i + 1)
    if not LostReason.objects.filter(company=company).exists():
        for i, name in enumerate(c.DEFAULT_LOST_REASONS):
            LostReason.objects.create(company=company, name=name, position=i + 1)


def source_keys(company, *, active_only=True):
    qs = LeadSource.objects.filter(company=company)
    if active_only:
        qs = qs.filter(is_active=True)
    return set(qs.values_list("key", flat=True))


def source_label(company, key):
    return dict(LeadSource.objects.filter(company=company).values_list("key", "name")).get(key, key)


from ..normalize import normal_name, normal_phone  # noqa: E402,F401
