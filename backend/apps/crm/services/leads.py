"""Leads: scoring, duplicate detection, conversion, CSV import.

Lead score (0-100), recalculated whenever a lead or one of its activities changes:

* source: referral 25, partner 20, event 15, website 12, social 8, cold call 5, other 3 (a source you add yourself earns 5)
* estimated value: 10 lakh or more 30, 5 lakh 20, 1 lakh 10, anything above zero 5
* engagement: 5 for each completed call, meeting or logged email, at most 30
* contact details: 5 for an email address and 5 for a phone number
Hot is 60 or more, warm is 30 to 59, cold is below 30. The score only *suggests* a rating; the rating is set by people.
"""
import csv
import io
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.company.constants import DocType
from apps.company.services import numbering

from .. import constants as c
from ..models import Account, Activity, Contact, Lead, LeadSource
from ..normalize import normal_name, normal_phone
from . import common
from .common import Action

LEAD_FIELDS = ("company_name", "contact_name", "email", "phone", "source", "industry", "product_interest", "estimated_value",
               "status", "rating", "city", "state", "notes")
AUDIT_FIELDS = ("reference_code", "company_name", "contact_name", "email", "phone", "source", "industry", "product_interest",
                "estimated_value", "status", "rating", "owner", "city", "state", "score")
ENGAGING = ("call", "meeting", "email")


# --------------------------------------------------------------------------- scoring

def score(lead, engagement=None):
    """(points, [{"label", "points"}]) for a lead. `engagement` is the number of completed calls, meetings and emails."""
    if engagement is None:
        engagement = Activity.objects.filter(lead=lead, status="completed", type__in=ENGAGING).count() if lead.pk else 0
    parts = [{"label": f"Source: {lead.source.replace('_', ' ')}", "points": c.SOURCE_POINTS.get(lead.source, c.OTHER_SOURCE_POINTS)}]
    value = Decimal(lead.estimated_value or 0)
    value_points = next((pts for floor, pts in c.VALUE_POINTS if value >= floor), 0)
    parts.append({"label": "Estimated value", "points": value_points})
    parts.append({"label": f"Engagement ({engagement} completed)", "points": min(engagement * c.ENGAGEMENT_POINTS_EACH, c.ENGAGEMENT_MAX)})
    parts.append({"label": "Email address", "points": c.CONTACT_POINTS if lead.email else 0})
    parts.append({"label": "Phone number", "points": c.CONTACT_POINTS if lead.phone else 0})
    return min(sum(p["points"] for p in parts), 100), parts


def suggested_rating(points):
    return "hot" if points >= c.HOT_SCORE else "warm" if points >= c.WARM_SCORE else "cold"


def refresh_score(lead):
    points, _ = score(lead)
    if points != lead.score:
        lead.score = points
        Lead.objects.filter(pk=lead.pk).update(score=points)


# --------------------------------------------------------------------------- duplicates

def find_duplicates(company, *, email="", phone="", company_name="", exclude_lead=None, limit=10):
    """Leads, contacts and accounts that share this email, phone number or company name."""
    email = (email or "").strip()
    pkey, nkey = normal_phone(phone), normal_name(company_name)
    found = {}

    def add(kind, obj, name, matched, detail=""):
        entry = found.setdefault((kind, obj.pk), {"type": kind, "id": str(obj.pk), "name": name, "matched_on": [], "detail": detail})
        if matched not in entry["matched_on"]:
            entry["matched_on"].append(matched)

    leads = Lead.objects.filter(company=company)
    if exclude_lead is not None:
        leads = leads.exclude(pk=exclude_lead.pk)
    contacts, accounts = Contact.objects.filter(company=company), Account.objects.filter(company=company, is_active=True)
    if email:
        for o in leads.filter(email__iexact=email)[:limit]:
            add("lead", o, o.display_name, "email", o.reference_code)
        for o in contacts.filter(email__iexact=email)[:limit]:
            add("contact", o, o.name, "email")
        for o in accounts.filter(email__iexact=email)[:limit]:
            add("account", o, o.name, "email")
    if pkey:
        for o in leads.filter(phone_key=pkey)[:limit]:
            add("lead", o, o.display_name, "phone", o.reference_code)
        for o in contacts.filter(phone_key=pkey)[:limit]:
            add("contact", o, o.name, "phone")
        for o in accounts.filter(phone_key=pkey)[:limit]:
            add("account", o, o.name, "phone")
    if nkey:
        for o in leads.filter(company_key=nkey)[:limit]:
            add("lead", o, o.display_name, "company", o.reference_code)
        for o in accounts.filter(name_key=nkey)[:limit]:
            add("account", o, o.name, "company")
    return list(found.values())[:limit]


class DuplicateFound(Exception):
    def __init__(self, duplicates):
        super().__init__("This looks like a record you already have.")
        self.duplicates = duplicates


# --------------------------------------------------------------------------- create and update

def _audit_values(lead):
    return common.snapshot(lead, AUDIT_FIELDS)


@transaction.atomic
def create_lead(company, actor, data, *, owner=None, allow_duplicates=False):
    lead = Lead(company=company, created_by=actor, owner=owner)
    common.apply_fields(lead, {"source": "other", **data}, LEAD_FIELDS)
    check_source(company, lead)
    dups = find_duplicates(company, email=lead.email, phone=lead.phone, company_name=lead.company_name)
    if dups and not allow_duplicates:
        raise DuplicateFound(dups)
    lead.reference_code = numbering.allocate_number(company, DocType.LEAD, actor=actor).reference_number
    lead.score = score(lead, 0)[0]
    lead.save()
    common.log(company, actor, "lead", lead.pk, Action.CREATED, new=_audit_values(lead))
    return lead


def check_source(company, lead, *, previous=None):
    # A lead keeps a source that was later switched off; a *new* choice must be an active one.
    if lead.source != previous and lead.source not in common.source_keys(company):
        raise ValidationError({"source": "Choose one of the lead sources set up in CRM settings."})


@transaction.atomic
def update_lead(lead, actor, data, *, owner=..., allow_duplicates=True):
    if lead.status == "converted" and "status" in data and data["status"] != "converted":
        raise ValidationError({"status": "A converted lead cannot be moved back. Open its deal or account instead."})
    before = _audit_values(lead)
    previous_source = lead.source
    common.apply_fields(lead, data, LEAD_FIELDS)
    if data.get("status") == "converted" and before["status"] != "converted":
        raise ValidationError({"status": "Use Convert to turn a lead into a contact, account and deal."})
    check_source(lead.company, lead, previous=previous_source)
    if owner is not ...:
        lead.owner = owner
    lead.score = score(lead)[0]
    lead.save()
    after = _audit_values(lead)
    if before != after:
        common.log(lead.company, actor, "lead", lead.pk, Action.UPDATED, previous=before, new=after)
    return lead


@transaction.atomic
def delete_lead(lead, actor):
    if lead.status == "converted":
        raise ValidationError("A converted lead is kept as history. Delete the deal or account instead.")
    Activity.objects.filter(lead=lead, contact=None, account=None, deal=None).delete()
    common.log(lead.company, actor, "lead", lead.pk, Action.DELETED, previous=_audit_values(lead))
    lead.delete()


@transaction.atomic
def bulk(company, actor, ids, action, *, owner=None, status=None):
    """Assign an owner, set a status or delete many leads at once. Returns (done, skipped_converted)."""
    leads = list(Lead.objects.select_for_update().filter(company=company, pk__in=ids))
    if len(leads) != len(set(map(str, ids))):
        raise ValidationError({"ids": "Some of the selected leads no longer exist."})
    if action == "status":
        if status not in dict(c.LEAD_STATUSES) or status == "converted":
            raise ValidationError({"status": "Choose a status (use Convert to convert leads)."})
    elif action == "assign":
        pass
    elif action != "delete":
        raise ValidationError({"action": "Choose assign, status or delete."})
    done = skipped = 0
    for lead in leads:
        if lead.status == "converted":
            skipped += 1
            continue
        if action == "delete":
            delete_lead(lead, actor)
        else:
            before = _audit_values(lead)
            if action == "assign":
                lead.owner = owner
            else:
                lead.status = status
            lead.save()
            common.log(company, actor, "lead", lead.pk, Action.UPDATED, previous=before, new=_audit_values(lead),
                       reason=f"Bulk {action}")
        done += 1
    return done, skipped


# --------------------------------------------------------------------------- conversion

@transaction.atomic
def convert(lead, actor, *, create_deal=True, deal_name="", amount=None, expected_close_date=None, stage=None,
            account=None, owner=None):
    """Turn a lead into a Contact, an Account (company) and, optionally, a Deal.

    The Account stays in the CRM; no sales Customer exists until a deal is won. An existing account may be chosen,
    otherwise a company with the same normalised name is reused, otherwise a new one is made (for a lead without a
    company the person's name is the account name).
    """
    lead = Lead.objects.select_for_update().get(pk=lead.pk)
    company = lead.company
    if lead.status == "converted":
        raise ValidationError("This lead has already been converted.")
    if lead.status == "unqualified":
        raise ValidationError({"status": "An unqualified lead cannot be converted. Change its status first."})
    owner = owner or lead.owner or actor
    if account is None:
        key = normal_name(lead.company_name or lead.contact_name)
        account = Account.objects.filter(company=company, is_active=True, name_key=key).first() if key else None
        if account is None:
            account = Account(company=company, name=lead.company_name or lead.contact_name, industry=lead.industry,
                              email=lead.email if lead.company_name else "", phone=lead.phone if lead.company_name else "",
                              city=lead.city, state=lead.state, owner=owner)
            account.full_clean(exclude=["company"], validate_unique=False, validate_constraints=False)
            account.save()
            common.log(company, actor, "crm_account", account.pk, Action.CREATED, new=common.snapshot(account, ACCOUNT_AUDIT),
                       reason=f"Converted from {lead.reference_code}")
    contact = None
    if lead.email:
        contact = Contact.objects.filter(company=company, account=account, email__iexact=lead.email).first()
    if contact is None:
        contact = Contact(company=company, name=lead.contact_name or lead.company_name, email=lead.email, phone=lead.phone,
                          account=account, owner=owner, is_primary=not account.contacts.filter(is_primary=True).exists(),
                          designation="")
        contact.full_clean(exclude=["company"], validate_unique=False, validate_constraints=False)
        contact.save()
        common.log(company, actor, "crm_contact", contact.pk, Action.CREATED, new=common.snapshot(contact, CONTACT_AUDIT),
                   reason=f"Converted from {lead.reference_code}")
    deal = None
    if create_deal:
        from . import deals
        deal = deals.create_deal(
            company, actor, {"name": deal_name or f"{account.name} - {lead.product_interest or 'new deal'}"[:200],
                             "amount": lead.estimated_value if amount in (None, "") else amount,
                             "expected_close_date": expected_close_date, "source": lead.source,
                             "notes": ""},
            account=account, contact=contact, stage=stage, owner=owner, lead=lead)
    lead.status = "converted"
    lead.converted_at = timezone.now()
    lead.converted_account, lead.converted_contact, lead.converted_deal = account, contact, deal
    lead.save()
    # The lead's history now also belongs to the contact, account and deal, so it shows where they are opened.
    Activity.objects.filter(lead=lead).update(contact=contact, account=account, deal=deal)
    common.log(company, actor, "lead", lead.pk, Action.UPDATED, previous={"status": "open"},
               new={"status": "converted", "account": str(account), "contact": str(contact), "deal": str(deal) if deal else ""},
               reason="Converted")
    return lead, contact, account, deal


ACCOUNT_AUDIT = ("name", "industry", "website", "email", "phone", "gstin", "city", "state", "owner", "is_active", "customer_id")
CONTACT_AUDIT = ("name", "designation", "email", "phone", "account", "is_primary", "owner")


# --------------------------------------------------------------------------- CSV import

IMPORT_COLUMNS = ["company_name", "contact_name", "email", "phone", "source", "industry", "product_interest",
                  "estimated_value", "status", "rating", "city", "state", "notes"]
_ALIASES = {
    "company": "company_name", "company name": "company_name", "organisation": "company_name", "organization": "company_name",
    "name": "contact_name", "contact": "contact_name", "contact person": "contact_name", "contact name": "contact_name",
    "e-mail": "email", "email address": "email", "mobile": "phone", "phone number": "phone", "telephone": "phone",
    "lead source": "source", "interest": "product_interest", "product": "product_interest", "value": "estimated_value",
    "estimated value": "estimated_value", "amount": "estimated_value", "lead status": "status",
}
MAX_IMPORT_ROWS = 1000
MAX_IMPORT_BYTES = 1_000_000


def _header(name):
    n = re.sub(r"[\s_]+", " ", (name or "").strip().lower().lstrip("﻿"))
    return _ALIASES.get(n, n.replace(" ", "_"))


def _lookup(value, choices):
    v = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    for key, label in choices:
        if v in (key.lower(), label.lower().replace(" ", "_")):
            return key
    return None


def import_leads(company, actor, text, *, commit=False, import_duplicates=False, owner=None):
    """Check (preview) or load a CSV of leads. Each row is validated on its own; one bad row never blocks the rest.

    Returns {"rows": [...], "valid": n, "errors": n, "duplicates": n, "created": n}. A row that matches an existing record
    (or an earlier row of the file) on email, phone or company is reported as a duplicate and skipped unless
    `import_duplicates` is true.
    """
    if len(text.encode("utf-8", "ignore")) > MAX_IMPORT_BYTES:
        raise ValidationError({"csv": "The file is larger than 1 MB. Split it into smaller files."})
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = [_header(h) for h in next(reader)]
    except StopIteration:
        raise ValidationError({"csv": "The file is empty."})
    known = [h for h in header if h in IMPORT_COLUMNS]
    if not known:
        raise ValidationError({"csv": "No known column headings. Use: " + ", ".join(IMPORT_COLUMNS) + "."})
    if not ({"company_name", "contact_name"} & set(known)):
        raise ValidationError({"csv": "The file needs a company_name or contact_name column."})
    keys = common.source_keys(company)
    source_choices = list(LeadSource.objects.filter(company=company, is_active=True).values_list("key", "name"))
    rows, seen_email, seen_phone, seen_company = [], {}, {}, {}
    for line_no, raw in enumerate(reader, start=2):
        if not any((cell or "").strip() for cell in raw):
            continue
        if len(rows) >= MAX_IMPORT_ROWS:
            raise ValidationError({"csv": f"At most {MAX_IMPORT_ROWS} rows can be imported at a time."})
        data = {h: (raw[i].strip() if i < len(raw) else "") for i, h in enumerate(header) if h in IMPORT_COLUMNS}
        errors = {}
        if data.get("source"):
            key = _lookup(data["source"], source_choices)
            if key:
                data["source"] = key
            else:
                errors["source"] = f"Unknown source '{data['source']}'."
        else:
            data["source"] = "other" if "other" in keys else (next(iter(sorted(keys)), "other"))
        for field, choices in (("status", c.LEAD_STATUSES), ("rating", c.RATINGS)):
            if data.get(field):
                key = _lookup(data[field], choices)
                if key is None or key == "converted":
                    errors[field] = f"'{data[field]}' is not a valid {field}."
                else:
                    data[field] = key
            else:
                data.pop(field, None)
        if data.get("email"):
            try:
                validate_email(data["email"])
            except ValidationError:
                errors["email"] = "Not a valid email address."
        if data.get("estimated_value") == "":
            data.pop("estimated_value", None)
        lead = Lead(company=company, owner=owner)
        if not errors:
            try:
                common.apply_fields(lead, data, LEAD_FIELDS)
            except ValidationError as exc:
                errors.update({k: v[0] for k, v in exc.message_dict.items()})
        dup = []
        if not errors:
            pkey, nkey, email = normal_phone(lead.phone), normal_name(lead.company_name), lead.email.lower()
            dup = [f"{d['type']} {d['name']} ({'/'.join(d['matched_on'])})" for d in
                   find_duplicates(company, email=lead.email, phone=lead.phone, company_name=lead.company_name, limit=3)]
            if email and email in seen_email:
                dup.append(f"row {seen_email[email]} of this file (email)")
            if pkey and pkey in seen_phone:
                dup.append(f"row {seen_phone[pkey]} of this file (phone)")
            if nkey and nkey in seen_company:
                dup.append(f"row {seen_company[nkey]} of this file (company)")
            if email:
                seen_email.setdefault(email, line_no)
            if pkey:
                seen_phone.setdefault(pkey, line_no)
            if nkey:
                seen_company.setdefault(nkey, line_no)
        rows.append({"row": line_no, "data": data, "errors": errors, "duplicates": dup, "lead": lead})
    valid = [r for r in rows if not r["errors"]]
    summary = {
        "total": len(rows), "errors": sum(1 for r in rows if r["errors"]),
        "duplicates": sum(1 for r in valid if r["duplicates"]),
        "valid": sum(1 for r in valid if import_duplicates or not r["duplicates"]), "created": 0,
    }
    if commit:
        with transaction.atomic():
            for r in valid:
                if r["duplicates"] and not import_duplicates:
                    continue
                lead = r["lead"]
                lead.created_by = actor
                lead.reference_code = numbering.allocate_number(company, DocType.LEAD, actor=actor).reference_number
                lead.score = score(lead, 0)[0]
                lead.save()
                common.log(company, actor, "lead", lead.pk, Action.CREATED, new=_audit_values(lead), reason="CSV import")
                summary["created"] += 1
    summary["rows"] = [{"row": r["row"], "data": r["data"], "errors": r["errors"], "duplicates": r["duplicates"],
                        "status": "error" if r["errors"] else "duplicate" if r["duplicates"] else "ok"} for r in rows]
    return summary
