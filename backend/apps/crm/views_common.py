"""Helpers shared by the CRM views."""
import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.company.models import CompanyProfileAuditEvent

PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


def need(request, code):
    if not request.user.has_code(code):
        raise PermissionDenied("Your role does not allow this.")


def view_or_manage(request, write):
    need(request, "crm.manage" if write else "crm.view")


def get_object(model, pk, company, label="record", **extra):
    try:
        obj = model.objects.filter(pk=pk, company=company, **extra).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise NotFound(f"No such {label}.")
    return obj


def optional_object(model, value, company, field, label=None):
    """Look up a record from an id in a request body: None when empty, a 400 on that field when it does not exist."""
    if value in (None, ""):
        return None
    try:
        obj = model.objects.filter(pk=value, company=company).first()
    except (ValueError, DjangoValidationError):
        obj = None
    if obj is None:
        raise ValidationError({field: f"{label or field.replace('_', ' ').title()} not found."})
    return obj


def user_from(value, field="owner"):
    if value in (None, ""):
        return None
    User = get_user_model()
    try:
        user = User.objects.filter(pk=value, is_active=True).first()
    except (ValueError, DjangoValidationError):
        user = None
    if user is None:
        raise ValidationError({field: "Choose an active user."})
    return user


def owner_param(request, qs, field="owner"):
    """?owner=<id> | me | none."""
    value = request.query_params.get("owner")
    if not value:
        return qs
    if value == "me":
        return qs.filter(**{field: request.user})
    if value == "none":
        return qs.filter(**{f"{field}__isnull": True})
    try:
        return qs.filter(**{f"{field}_id": value})
    except (ValueError, DjangoValidationError):
        return qs.none()


def date_param(request, name):
    value = request.query_params.get(name)
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ValidationError({name: "Use a date in YYYY-MM-DD format."})


def paginate(request, qs, default=PAGE_SIZE):
    """(rows, count, page, page_size) for ?page= and ?page_size= (at most 100)."""
    try:
        size = max(1, min(int(request.query_params.get("page_size", default)), MAX_PAGE_SIZE))
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        raise ValidationError({"page": "Page numbers must be whole numbers."})
    count = qs.count()
    return list(qs[(page - 1) * size: page * size]), count, page, size


def sorted_by(request, qs, allowed, default):
    key = request.query_params.get("sort") or default
    bare = key.lstrip("-")
    if bare not in allowed:
        key = default
    return qs.order_by(key, "-created_at")


def timeline(company, entity_type, entity_id, activities):
    """Activities and audit events of one record, newest first."""
    from .serializers import activity_dict, audit_summary
    rows = [{"kind": "activity", "at": a.created_at, **activity_dict(a)} for a in activities]
    events = CompanyProfileAuditEvent.objects.filter(company=company, entity_type=entity_type, entity_id=entity_id)[:200]
    rows += [audit_summary(e) for e in events]
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows
