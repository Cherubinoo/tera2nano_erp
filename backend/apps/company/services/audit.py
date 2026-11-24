"""Append-only audit trail for company configuration.

`snapshot()` turns a model instance into a JSON-safe dict with sensitive values removed;
`record_event()` writes one immutable row and bumps the company profile version.
"""
import datetime
import decimal
import uuid
from dataclasses import dataclass, field

from django.db import models
from django.db.models import F

from apps.core.middleware import get_request_id

from ..models import CompanyProfile, CompanyProfileAuditEvent

REDACTED = "[redacted]"

# Fields never written to the audit trail, not even in redacted form.
ALWAYS_SKIP = {
    "id", "created_at", "updated_at", "company", "singleton",
    "account_number_encrypted", "account_number_hash",
}


@dataclass(frozen=True)
class EntityAudit:
    label: str
    default_category: str
    # Values replaced by "[redacted]" (the fact of change is still recorded).
    redact: frozenset = frozenset()
    # field -> category overrides
    categories: dict = field(default_factory=dict)
    # Changing any of these (after setup is complete) needs a stated reason.
    reason_required: frozenset = frozenset()
    # False for records that change all the time (customers, payments): they do not bump the company record version.
    bump_version: bool = True


ENTITIES = {
    "profile": EntityAudit(
        "Company profile", "profile",
        categories={
            "legal_name": "legal_name", "display_name": "legal_name", "trade_name": "legal_name",
            "legal_entity_type": "legal_name",
            "pan": "registration", "tan": "registration", "llpin": "registration",
            "incorporation_certificate_reference": "registration",
            "gst_registration_status": "gst", "gstin": "gst", "logo": "branding",
        },
        reason_required=frozenset(
            {"legal_name", "display_name", "legal_entity_type", "pan", "tan", "llpin", "gstin", "gst_registration_status"}
        ),
    ),
    "address": EntityAudit(
        "Address", "address",
        reason_required=frozenset(
            {"address_type", "line1", "line2", "building_name", "locality", "city", "district", "state",
             "state_code", "postal_code", "country", "is_primary"}
        ),
    ),
    "partner": EntityAudit(
        "Partner", "signatory",
        redact=frozenset({"dpin", "official_email", "official_phone", "signature_image", "signature_checksum"}),
        reason_required=frozenset({"full_name", "partner_type", "is_designated_partner", "cessation_date", "authorization_scope"}),
    ),
    "signatory": EntityAudit(
        "Authorized signatory", "signatory",
        redact=frozenset({"signature_image", "signature_checksum"}),
        reason_required=frozenset(
            {"name", "authorization_type", "valid_from", "valid_to", "authorized_document_types", "status"}
        ),
    ),
    "contact": EntityAudit("Contact", "contact", redact=frozenset({"email", "phone"})),
    "tax_registration": EntityAudit(
        "Tax registration", "registration",
        reason_required=frozenset({"registration_number", "registration_type", "effective_from", "effective_to"}),
    ),
    "tax_settings": EntityAudit(
        "Tax settings", "tax",
        reason_required=frozenset({"gst_applicable", "gst_treatment", "tax_codes", "default_tax_code"}),
    ),
    "bank_account": EntityAudit(
        "Bank account", "bank",
        redact=frozenset({"account_holder_name", "account_fingerprint"}),
        reason_required=frozenset(
            {"bank_name", "branch", "account_number", "account_fingerprint", "ifsc_code", "account_holder_name", "is_primary",
             "show_on_invoices", "show_on_quotations"}
        ),
    ),
    "branding": EntityAudit("Branding", "branding"),
    "numbering": EntityAudit(
        "Document numbering", "numbering",
        reason_required=frozenset(
            {"prefix", "include_financial_year", "include_short_year", "date_format", "separator", "min_length",
             "reset_frequency", "next_value"}
        ),
    ),
    "financial_year": EntityAudit(
        "Financial year", "financial_year",
        reason_required=frozenset({"start_date", "end_date", "is_locked", "status"}),
    ),
    "policy": EntityAudit(
        "Policy", "policy",
        categories={
            "payment_terms": "payment_terms", "payment_terms_days": "payment_terms",
            "invoice_due_days": "payment_terms", "quotation_validity_days": "payment_terms",
            "approval_thresholds": "approval_thresholds", "retention_settings": "retention",
        },
    ),
    "letterhead_template": EntityAudit("Letterhead template", "letterhead"),
    "document": EntityAudit("Company document", "document"),
    "issued_document": EntityAudit("Issued document", "issued_document"),
    "number_reference": EntityAudit("Reference number", "numbering"),
    "customer": EntityAudit("Customer", "sales", redact=frozenset({"email", "phone", "gstin", "pan"}), bump_version=False),
    "sales_document": EntityAudit("Sales document", "sales", bump_version=False),
    "payment": EntityAudit("Payment received", "sales", bump_version=False),
    "account": EntityAudit("Ledger account", "accounting", bump_version=False),
    "journal_entry": EntityAudit("Journal entry", "accounting", bump_version=False),
    "voucher": EntityAudit("Expense or income", "accounting", bump_version=False),
    "reconciliation": EntityAudit("Bank reconciliation", "accounting", bump_version=False),
    "bank_statement": EntityAudit("Bank statement import", "accounting", bump_version=False),
    "bank_match": EntityAudit("Bank statement match", "accounting", bump_version=False),
    "statement_line": EntityAudit("Bank statement line", "accounting", bump_version=False),
    "user": EntityAudit("User", "access", redact=frozenset({"email", "password"})),
    "access_matrix": EntityAudit("Access permissions", "access"),
    "invitation": EntityAudit("Invitation link", "access", redact=frozenset({"email_hint"}), bump_version=False),
}


def register_entity(name, spec):
    """Let a module add its own audited record types (call from its AppConfig.ready())."""
    ENTITIES.setdefault(name, spec)


def _jsonable(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def snapshot(instance):
    """Audit-safe field values of a model instance (no secrets, no files, no timestamps)."""
    data = {}
    for f in instance._meta.concrete_fields:
        if f.name in ALWAYS_SKIP:
            continue
        value = getattr(instance, f.attname)
        if isinstance(f, models.FileField):
            value = "set" if value else ""
        data[f.name] = _jsonable(value)
    if hasattr(instance, "account_number_last4"):
        data["account_number"] = instance.masked_account_number
        # Detects a changed number even when the last four digits happen to match; always redacted.
        data["account_fingerprint"] = instance.account_number_hash[:10]
    return data


def diff(entity, before, after):
    """Return (changed_field_names, previous, new) with redaction applied."""
    spec = ENTITIES[entity]
    changed, prev, new = [], {}, {}
    for name in sorted(set(before) | set(after)):
        old, cur = before.get(name), after.get(name)
        if old == cur:
            continue
        changed.append(name)
        if name in spec.redact:
            prev[name], new[name] = REDACTED, REDACTED
        else:
            prev[name], new[name] = old, cur
    return changed, prev, new


def category_for(entity, changed, instance=None):
    spec = ENTITIES[entity]
    if entity == "tax_registration" and instance is not None and instance.registration_type == "gstin":
        return "gst"
    for name in changed:
        if name in spec.categories:
            return spec.categories[name]
    return spec.default_category


def reason_needed(entity, changed, before, company):
    """Fields changed that need a stated reason (only for established, non-blank values)."""
    if company is None or company.setup_status != CompanyProfile.SetupStatus.COMPLETED:
        return []
    spec = ENTITIES[entity]
    return [n for n in changed if n in spec.reason_required and before.get(n) not in (None, "", [], {})]


def _actor_fields(actor):
    if actor is None or not getattr(actor, "is_authenticated", False):
        return {"actor_id": None, "actor_label": ""}
    return {"actor_id": actor.pk, "actor_label": (actor.get_username() or "")[:150]}


def record_event(company, actor, entity, entity_id, action, *, changed=(), previous=None, new=None,
                 reason="", category=None, instance=None):
    spec = ENTITIES[entity]
    bump = spec.bump_version and action not in {
        CompanyProfileAuditEvent.Action.REVEALED,
        CompanyProfileAuditEvent.Action.DOWNLOADED,
        CompanyProfileAuditEvent.Action.ISSUED,
    }
    if bump:
        CompanyProfile.objects.filter(pk=company.pk).update(version=F("version") + 1)
        company.refresh_from_db(fields=["version"])
    return CompanyProfileAuditEvent.objects.create(
        company=company,
        entity_type=entity,
        entity_id=entity_id,
        action=action,
        category=category or category_for(entity, list(changed), instance) or spec.default_category,
        changed_fields=list(changed),
        previous_values=previous or {},
        new_values=new or {},
        reason=reason or "",
        request_id=get_request_id() if get_request_id() != "-" else "",
        profile_version=company.version,
        **_actor_fields(actor),
    )


def log(company, actor, entity, entity_id, action, *, new=None, previous=None, reason="", category=None):
    """One call for a module to write an audit event: `previous` and `new` are plain dicts of the fields that changed.

    `action` is a CompanyProfileAuditEvent.Action value ("created", "updated", "deactivated", "deleted", "issued" ...).
    The entity must have been registered (see register_entity). Emails, phone numbers and similar personal data should be
    listed in the EntityAudit's `redact` so they are not copied into the history.
    """
    changed, prev, cur = diff(entity, previous or {}, new or {})
    return record_event(company, actor, entity, entity_id, action, changed=changed, previous=prev, new=cur, reason=reason,
                        category=category or ENTITIES[entity].default_category)
