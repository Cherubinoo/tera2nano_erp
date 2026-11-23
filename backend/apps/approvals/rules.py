"""Rule helpers: the editable example rules, matching, and saving a rule with its steps."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError

from apps.accounts import access

from . import registry
from .models import APPROVER_ROLES, ApprovalRule, ApprovalRuleStep, ApprovalSettings

MP, PARTNER, FIN, HR = access.MANAGING_PARTNER, access.PARTNER, access.FINANCE_MANAGER, access.HR_MANAGER
ROLE_CODES = [r[0] for r in APPROVER_ROLES]

# (kind, name, description, amount_from, amount_to, [(role, mode, escalate_days), ...])
DEFAULT_RULES = [
    ("purchase_order", "Purchase orders above 50,000", "Finance Manager checks the order, then the Managing Partner signs it off.",
     "50000.01", None, [(FIN, "any", 2), (MP, "any", 3)]),
    ("vendor_bill", "Vendor bills above 1,00,000", "The Managing Partner approves large bills before they are booked.",
     "100000.01", None, [(MP, "any", 3)]),
    ("leave_request", "Leave requests", "HR Manager approves leave.", None, None, [(HR, "any", 3)]),
    ("expense", "Expenses above 50,000", "Finance Manager, then the Managing Partner.", "50000.01", None, [(FIN, "any", 2), (MP, "any", 3)]),
    ("expense", "Expenses of 10,000 to 50,000", "Finance Manager approves mid-sized expenses; smaller ones are posted straight away.",
     "10000.01", "50000", [(FIN, "any", 3)]),
    ("payroll_run", "Payroll runs", "The Managing Partner approves every payroll run.", None, None, [(MP, "any", 2)]),
    ("salary_revision", "Salary increases", "HR Manager, then the Managing Partner approve a raise (the amount is the increase in yearly gross pay).", None, None, [(HR, "any", 3), (MP, "any", 3)]),
    ("budget", "Project budgets", "The Managing Partner approves a new or changed project budget (the amount is the proposed total).", None, None, [(MP, "any", 5)]),
    ("payment", "Payments above 50,000", "The Managing Partner approves vendor payments before they are posted, and the voiding of a payment (made or received), above this amount.",
     "50000.01", None, [(MP, "any", 2)]),
    ("partner_withdrawal", "Partner withdrawals", "Every partner must agree to a withdrawal.", None, None, [(PARTNER, "all", 5)]),
    ("quotation", "Quotations above 5,00,000", "The Managing Partner approves large quotations before they go out.",
     "500000.01", None, [(MP, "any", 3)]),
]


def seed_default_rules(company):
    """Create the editable example rules once per company. Returns how many were created (0 when already seeded)."""
    with transaction.atomic():
        settings_row, _ = ApprovalSettings.objects.select_for_update().get_or_create(company=company)
        if settings_row.defaults_seeded:
            return 0
        created = 0
        priority = 0
        for kind, name, description, amount_from, amount_to, steps in DEFAULT_RULES:
            priority += 10
            _create_default(company, kind, name, description, amount_from, amount_to, steps, priority)
            created += 1
        settings_row.defaults_seeded = True
        settings_row.save(update_fields=["defaults_seeded", "updated_at"])
        return created


def seed_missing_rules(company):
    """Add the example rules for every wired kind that has no rule at all, so a company seeded before a kind was connected gets
    its example without its own rules being touched. Returns how many rules were created."""
    created = seed_default_rules(company)
    with transaction.atomic():
        have = set(ApprovalRule.objects.filter(company=company).values_list("kind", flat=True))
        wired = {k["value"] for k in registry.all_kinds() if registry.handler_for(k["value"])}
        priority = ApprovalRule.objects.filter(company=company).order_by("-priority").values_list("priority", flat=True).first() or 0
        for kind, name, description, amount_from, amount_to, steps in DEFAULT_RULES:
            if kind in have or kind not in wired:
                continue
            priority += 10
            _create_default(company, kind, name, description, amount_from, amount_to, steps, priority)
            created += 1
        return created


def _create_default(company, kind, name, description, amount_from, amount_to, steps, priority):
    rule = ApprovalRule.objects.create(
        company=company, kind=kind, name=name, description=description, priority=priority,
        amount_from=Decimal(amount_from) if amount_from else None, amount_to=Decimal(amount_to) if amount_to else None)
    for order, (role, mode, days) in enumerate(steps, 1):
        ApprovalRuleStep.objects.create(rule=rule, order=order, approver_type="role", role=role, mode=mode, escalate_after_days=days)
    return rule


def ensure_seeded(company):
    if not ApprovalSettings.objects.filter(company=company, defaults_seeded=True).exists():
        seed_default_rules(company)


def rule_matches(rule, amount, meta):
    amt = amount if amount is not None else Decimal("0")
    if rule.amount_from is not None and amt < rule.amount_from:
        return False
    if rule.amount_to is not None and amt > rule.amount_to:
        return False
    meta = meta or {}
    for text, key in ((rule.project_text, "project"), (rule.department_text, "department")):
        if text and text.strip().lower() not in str(meta.get(key, "")).lower():
            return False
    return True


def match_rule(company, kind, amount, meta=None):
    for rule in ApprovalRule.objects.filter(company=company, kind=kind, is_active=True).prefetch_related("steps").order_by(
            "priority", "created_at"):
        if rule_matches(rule, amount, meta):
            return rule
    return None


def _money(value, field):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValidationError({field: "Enter an amount as a number."})


RULE_FIELDS = ("kind", "name", "description", "priority", "is_active", "amount_from", "amount_to", "project_text", "department_text")


def rule_snapshot(rule):
    return {
        "kind": rule.kind, "name": rule.name, "description": rule.description, "priority": rule.priority,
        "is_active": rule.is_active, "amount_from": str(rule.amount_from) if rule.amount_from is not None else None,
        "amount_to": str(rule.amount_to) if rule.amount_to is not None else None,
        "project_text": rule.project_text, "department_text": rule.department_text,
        "steps": [step_text(s) for s in rule.steps.all()],
    }


def step_text(s):
    who = (s.user.get_full_name() or s.user.username) if s.approver_type == "user" and s.user_id else dict(APPROVER_ROLES).get(s.role, s.role)
    return f"{s.order}. {who} ({'all' if s.mode == 'all' else 'any one'})" + (f", escalate after {s.escalate_after_days}d" if s.escalate_after_days else "")


def save_rule(company, data, rule=None):
    """Validate and save a rule with its steps (replacing the old steps). Raises DRF ValidationError."""
    User = get_user_model()
    rule = rule or ApprovalRule(company=company)
    errors = {}
    if "kind" in data or rule.pk is None:
        kind = (data.get("kind") or "").strip()
        if not kind or not any(k["value"] == kind for k in registry.all_kinds()):
            errors["kind"] = "Choose what the rule applies to."
        rule.kind = kind
    for f in ("name", "description", "project_text", "department_text"):
        if f in data:
            setattr(rule, f, str(data.get(f) or "").strip())
    if "is_active" in data:
        if not isinstance(data["is_active"], bool):
            errors["is_active"] = "Send true or false."
        else:
            rule.is_active = data["is_active"]
    if "priority" in data and data["priority"] not in (None, ""):
        try:
            rule.priority = int(data["priority"])
            if rule.priority < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors["priority"] = "Enter a whole number, 0 or more."
    for f in ("amount_from", "amount_to"):
        if f in data:
            try:
                setattr(rule, f, _money(data[f], f))
            except ValidationError as exc:
                errors.update(exc.detail)
    steps = data.get("steps")
    clean_steps = []
    if steps is not None or rule.pk is None:
        if not isinstance(steps, list) or not steps:
            errors["steps"] = "Add at least one approval step."
        else:
            for i, s in enumerate(steps, 1):
                if not isinstance(s, dict):
                    errors["steps"] = "Each step needs an approver."
                    break
                kind = s.get("approver_type") or "role"
                mode = s.get("mode") or "any"
                days = s.get("escalate_after_days")
                item = {"order": i, "approver_type": kind, "role": "", "user": None, "mode": mode, "escalate_after_days": None}
                if mode not in ("any", "all"):
                    errors["steps"] = f"Step {i}: choose any one or all."
                if days not in (None, ""):
                    try:
                        item["escalate_after_days"] = int(days)
                        if not 1 <= item["escalate_after_days"] <= 90:
                            raise ValueError
                    except (TypeError, ValueError):
                        errors["steps"] = f"Step {i}: escalation days must be between 1 and 90."
                if kind == "role":
                    if s.get("role") not in ROLE_CODES:
                        errors["steps"] = f"Step {i}: choose the approver role."
                    item["role"] = s.get("role")
                elif kind == "user":
                    user = User.objects.filter(pk=s.get("user"), is_active=True).first() if s.get("user") else None
                    if user is None:
                        errors["steps"] = f"Step {i}: choose an active user."
                    item["user"] = user
                    item["mode"] = "any"
                else:
                    errors["steps"] = f"Step {i}: choose a role or a user."
                clean_steps.append(item)
    if errors:
        raise ValidationError(errors)
    try:
        rule.full_clean(exclude=["company"])
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages})
    with transaction.atomic():
        rule.save()
        if steps is not None:
            rule.steps.all().delete()
            for item in clean_steps:
                ApprovalRuleStep.objects.create(rule=rule, **item)
    return rule
