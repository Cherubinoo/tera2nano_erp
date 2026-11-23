"""The obligations catalogue: created lazily per company from `constants.CATALOGUE`, then edited like any other data."""
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts import access

from .. import constants as C
from ..models import Obligation
from . import common, schedule

AUDIT_FIELDS = ("code", "name", "form_no", "category", "authority", "frequency", "due_rule", "due_rule_text", "penalty_rule",
                "penalty_note", "enabled", "assignee_role", "default_priority", "reminder_days")


def seed_catalogue(company):
    """Create the starting catalogue for `company`. Idempotent: existing codes (even edited or switched off) are left alone.

    Returns how many obligations were created.
    """
    existing = set(Obligation.objects.filter(company=company).values_list("code", flat=True))
    fresh = []
    for spec in C.CATALOGUE:
        if spec["code"] in existing:
            continue
        fresh.append(Obligation(
            company=company, is_seed=True, enabled=spec.get("enabled", True), reminder_days=list(C.DEFAULT_REMINDER_DAYS),
            **{k: v for k, v in spec.items() if k != "enabled"}))
    if fresh:
        with transaction.atomic():
            Obligation.objects.bulk_create(fresh, ignore_conflicts=True)
    return len(fresh)


def obligation_dict(o, *, item_count=None):
    return {
        "id": str(o.id), "code": o.code, "name": o.name, "form_no": o.form_no, "category": o.category,
        "category_label": C.CATEGORY_LABELS.get(o.category, o.category), "authority": o.authority, "frequency": o.frequency,
        "frequency_label": dict(C.FREQUENCIES).get(o.frequency, o.frequency), "due_rule": o.due_rule,
        "due_rule_text": o.due_rule_text, "penalty_rule": o.penalty_rule, "penalty_note": o.penalty_note,
        "description": o.description, "enabled": o.enabled, "assignee_role": o.assignee_role,
        "assignee_role_label": dict(access.ROLES).get(o.assignee_role, ""), "default_priority": o.default_priority,
        "reminder_days": o.reminder_days or list(C.DEFAULT_REMINDER_DAYS), "is_seed": o.is_seed, "is_event": o.frequency == "event",
        "items": item_count,
    }


def _apply(o, data, *, creating):
    if "code" in data or creating:
        code = common.clean_text(data.get("code", o.code if not creating else ""), "code", max_length=40, required=True, label="a code").upper()
        if not all(ch.isalnum() or ch in "-_" for ch in code):
            raise ValidationError({"code": "Use letters, numbers, hyphen and underscore only."})
        if not creating and code != o.code:
            raise ValidationError({"code": "The code cannot be changed."})
        o.code = code
    if "name" in data or creating:
        o.name = common.clean_text(data.get("name"), "name", max_length=200, required=True, label="the name")
    for f, n in (("form_no", 60), ("authority", 160), ("due_rule_text", 200), ("penalty_note", 300)):
        if f in data:
            setattr(o, f, common.clean_text(data[f], f, max_length=n))
    if "description" in data:
        o.description = common.clean_text(data["description"], "description", max_length=2000)
    if "category" in data:
        o.category = common.pick(data["category"], "category", C.CATEGORIES)
    if "frequency" in data:
        o.frequency = common.pick(data["frequency"], "frequency", C.FREQUENCIES)
    if "penalty_rule" in data:
        o.penalty_rule = common.pick(data["penalty_rule"], "penalty_rule", C.PENALTY_CHOICES)
    if "default_priority" in data:
        o.default_priority = common.pick(data["default_priority"], "default_priority", C.PRIORITIES)
    if "assignee_role" in data:
        role = data["assignee_role"] or ""
        if role and role not in access.ROLE_VALUES:
            raise ValidationError({"assignee_role": "Choose one of the roles."})
        o.assignee_role = role
    if "enabled" in data:
        o.enabled = common.flag(data["enabled"], "enabled")
    if "reminder_days" in data:
        o.reminder_days = common.clean_reminders(data["reminder_days"]) or []
    if "due_rule" in data:
        o.due_rule = schedule.validate_rule(data["due_rule"])
    elif creating:
        raise ValidationError({"due_rule": "Choose how the due date is worked out."})
    if o.frequency == "event" and o.due_rule.get("type") != "event":
        raise ValidationError({"due_rule": "An event obligation needs the 'days after the event' rule."})
    if o.frequency != "event" and o.due_rule.get("type") == "event":
        raise ValidationError({"frequency": "Set the frequency to 'On an event' for this rule."})


def create_obligation(company, actor, data):
    from ..models import Obligation as M
    code = str(data.get("code") or "").strip().upper()
    if code and M.objects.filter(company=company, code=code).exists():
        raise ValidationError({"code": "That code is already used."})
    o = M(company=company, reminder_days=list(C.DEFAULT_REMINDER_DAYS))
    _apply(o, data, creating=True)
    o.is_seed = False
    o.save()
    common.log(company, actor, "compliance_obligation", o.pk, "created", new=common.snapshot(o, AUDIT_FIELDS))
    return o


def update_obligation(o, actor, data):
    before = common.snapshot(o, AUDIT_FIELDS)
    _apply(o, data, creating=False)
    o.save()
    after = common.snapshot(o, AUDIT_FIELDS)
    if before != after:
        action = "updated"
        if before["enabled"] != after["enabled"] and {k for k in before if before[k] != after[k]} == {"enabled"}:
            action = "activated" if after["enabled"] else "deactivated"
        common.log(o.company, actor, "compliance_obligation", o.pk, action, previous=before, new=after)
    return o


def delete_obligation(o, actor):
    if o.is_seed:
        raise ValidationError({"detail": "A catalogue entry cannot be deleted. Switch it off instead."})
    if o.items.exists():
        raise ValidationError({"detail": "This obligation has calendar items. Switch it off instead."})
    before = common.snapshot(o, AUDIT_FIELDS)
    oid = o.pk
    o.delete()
    common.log(o.company, actor, "compliance_obligation", oid, "deleted", previous=before)
