"""Legal repository: events, contracts and agreements, notices and cases, policies."""
import datetime
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.company.models import CompanyPartner, CompanyProfileAuditEvent

from .. import constants as C
from ..models import Contract, LegalCase, LegalEvent, Obligation, Policy, PolicyAcknowledgement
from . import catalogue, common, filings, schedule

EVENT_FIELDS = ("event_type", "title", "event_date", "form_no", "srn", "description")
CONTRACT_FIELDS = ("title", "counterparty", "contract_type", "effective_date", "expiry_date", "auto_renew", "renewal_term_months",
                   "renewal_notice_days", "value", "status", "owner_id", "customer_id", "vendor_id", "project_id", "notes")
CASE_FIELDS = ("title", "case_type", "party_role", "forum", "case_number", "counterparty", "filed_date", "next_hearing_date",
               "lawyer", "amount_involved", "status", "outcome", "closed_on", "owner_id")
POLICY_FIELDS = ("name", "category", "version", "effective_date", "review_date", "status", "requires_acknowledgement", "owner_id")

EVENT_FORM_OBLIGATION = {
    "partner_change": "LLP-FORM-4", "name_change": "LLP-FORM-5", "agreement_amendment": "LLP-FORM-3", "address_change": "LLP-FORM-15",
}


def _uuid(value, field):
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise ValidationError({field: "Not a valid id."})


def _check_dates(first, second, field, message):
    if first and second and second < first:
        raise ValidationError({field: message})


# ============================================================================ legal events
def event_dict(e):
    return {
        "id": str(e.id), "reference": e.reference, "event_type": e.event_type,
        "event_type_label": C.EVENT_TYPE_LABELS.get(e.event_type, e.event_type), "title": e.title, "event_date": e.event_date,
        "form_no": e.form_no, "srn": e.srn, "description": e.description, "imported": bool(e.source_key),
    }


def save_event(company, actor, data, event=None, today=None):
    today = today or datetime.date.today()
    creating = event is None
    event = event or LegalEvent(company=company, created_by=actor)
    before = None if creating else common.snapshot(event, EVENT_FIELDS)
    if creating or "event_type" in data:
        event.event_type = common.pick(data.get("event_type"), "event_type", C.EVENT_TYPES)
    if creating or "title" in data:
        event.title = common.clean_text(data.get("title"), "title", max_length=200, required=True, label="a title")
    if creating or "event_date" in data:
        event.event_date = common.parse_date(data.get("event_date"), "event_date", required=True)
        if event.event_date > today:
            raise ValidationError({"event_date": "An event cannot be dated in the future."})
    for f, n in (("form_no", 60), ("srn", 60)):
        if f in data:
            setattr(event, f, common.clean_text(data[f], f, max_length=n))
    if "description" in data:
        event.description = common.clean_text(data["description"], "description", max_length=4000)
    filing = None
    with transaction.atomic():
        if creating:
            event.reference = common.new_reference(company, "LEV", actor=actor, on_date=event.event_date)
        event.save()
        if creating:
            common.log(company, actor, "legal_event", event.pk, "created", new=common.snapshot(event, EVENT_FIELDS))
            if data.get("create_filing"):
                filing = _filing_for_event(company, actor, event)
        else:
            after = common.snapshot(event, EVENT_FIELDS)
            if before != after:
                common.log(company, actor, "legal_event", event.pk, "updated", previous=before, new=after)
    return event, filing


def _filing_for_event(company, actor, event):
    code = EVENT_FORM_OBLIGATION.get(event.event_type)
    catalogue.seed_catalogue(company)
    ob = Obligation.objects.filter(company=company, code=code).first() if code else None
    if ob is None:
        raise ValidationError({"create_filing": "No filing is needed for this kind of event."})
    return filings.create_item(company, actor, {"obligation": ob.pk, "event_date": event.event_date})


def delete_event(event, actor):
    before = common.snapshot(event, EVENT_FIELDS)
    pk = event.pk
    event.delete()
    common.log(event.company, actor, "legal_event", pk, "deleted", previous=before)


def import_history(company, actor):
    """Create timeline events from what the company module already knows. Safe to repeat: each source is imported once."""
    candidates = []
    if company.date_of_incorporation:
        extra = f"LLPIN {company.llpin}. " if company.llpin else ""
        candidates.append(("profile:incorporation", "incorporation", f"{company.legal_name} incorporated", company.date_of_incorporation,
                           f"{extra}Certificate reference: {company.incorporation_certificate_reference}".strip(". ")
                           if company.incorporation_certificate_reference else extra.strip()))
    for p in CompanyPartner.objects.filter(company=company):
        kind = p.get_partner_type_display().lower()
        if p.appointment_date:
            candidates.append((f"partner:{p.pk}:appointed", "partner_change", f"{p.full_name} joined as {kind}", p.appointment_date, ""))
        if p.cessation_date:
            candidates.append((f"partner:{p.pk}:ceased", "partner_change", f"{p.full_name} ceased as {kind}", p.cessation_date, ""))
    audits = CompanyProfileAuditEvent.objects.filter(company=company, entity_type__in=("profile", "address")).order_by("occurred_at")
    for a in audits:
        day = a.occurred_at.date()
        if a.entity_type == "profile" and a.action == "updated" and "legal_name" in a.changed_fields:
            old, new = a.previous_values.get("legal_name"), a.new_values.get("legal_name")
            candidates.append((f"audit:{a.pk}", "name_change", f"Legal name changed to {new}", day,
                               f"From {old}. Reason: {a.reason}" if a.reason else f"From {old}."))
        elif a.entity_type == "address" and a.action in ("created", "updated") and (
                a.new_values.get("address_type") == "registered_office" or a.new_values.get("is_primary") is True):
            candidates.append((f"audit:{a.pk}", "address_change", "Registered office address recorded or changed", day, a.reason))
    existing = set(LegalEvent.objects.filter(company=company).exclude(source_key="").values_list("source_key", flat=True))
    created = 0
    with transaction.atomic():
        for key, kind, title, day, text in candidates:
            if key in existing:
                continue
            ev = LegalEvent(company=company, source_key=key, event_type=kind, title=title[:200], event_date=day,
                            description=text, created_by=actor, reference=common.new_reference(company, "LEV", actor=actor, on_date=day))
            ev.save()
            common.log(company, actor, "legal_event", ev.pk, "created", new={**common.snapshot(ev, EVENT_FIELDS), "imported": True})
            created += 1
    return {"created": created, "skipped": len(candidates) - created}


# ============================================================================ contracts
def effective_expiry(c, today=None):
    """The expiry date that counts today. An auto-renewing active contract rolls forward by its term each time it lapses."""
    if c.expiry_date is None:
        return None
    today = today or datetime.date.today()
    if c.status != Contract.Status.ACTIVE or not c.auto_renew or c.expiry_date >= today:
        return c.expiry_date
    term = max(c.renewal_term_months or 12, 1)
    n = 1
    while True:
        d = schedule.add_months(c.expiry_date, term * n)
        if d >= today:
            return d
        n += 1


def contract_state(c, today=None):
    """draft / active / expiring / expired / terminated."""
    today = today or datetime.date.today()
    if c.status == Contract.Status.TERMINATED:
        return "terminated"
    if c.status == Contract.Status.DRAFT:
        return "draft"
    eff = effective_expiry(c, today)
    if eff is None:
        return "active"
    if eff < today:
        return "expired"
    if (eff - today).days <= c.renewal_notice_days:
        return "expiring"
    return "active"


CONTRACT_STATE_LABELS = {"draft": "Draft", "active": "Active", "expiring": "Expiring soon", "expired": "Expired", "terminated": "Terminated"}


def contract_dict(c, today=None):
    today = today or datetime.date.today()
    state = contract_state(c, today)
    eff = effective_expiry(c, today)
    return {
        "id": str(c.id), "reference": c.reference, "title": c.title, "counterparty": c.counterparty,
        "contract_type": c.contract_type, "contract_type_label": dict(C.CONTRACT_TYPES).get(c.contract_type, c.contract_type),
        "effective_date": c.effective_date, "expiry_date": c.expiry_date, "current_expiry": eff,
        "auto_renewed": bool(eff and c.expiry_date and eff != c.expiry_date), "auto_renew": c.auto_renew,
        "renewal_term_months": c.renewal_term_months, "renewal_notice_days": c.renewal_notice_days, "renewal_count": c.renewal_count,
        "days_left": (eff - today).days if eff else None, "value": f"{c.value:.2f}" if c.value is not None else None,
        "status": c.status, "state": state, "state_label": CONTRACT_STATE_LABELS[state],
        "terminated_on": c.terminated_on, "termination_reason": c.termination_reason,
        "owner": {"id": str(c.owner_id), "name": common.user_name(c.owner)} if c.owner_id else None,
        "customer": {"id": str(c.customer_id), "name": c.customer_name} if c.customer_id else None,
        "vendor": {"id": str(c.vendor_id), "name": c.vendor_name} if c.vendor_id else None,
        "project": {"id": str(c.project_id), "name": c.project_name} if c.project_id else None,
        "notes": c.notes,
    }


def _soft_links(c, data):
    if "customer_id" in data or "customer" in data:
        value = data.get("customer_id", data.get("customer"))
        if value in (None, ""):
            c.customer_id, c.customer_name = None, ""
        else:
            from apps.sales.models import Customer
            cu = None
            try:
                cu = Customer.objects.filter(pk=value, company=c.company).first()
            except (ValueError, ValidationError):
                pass
            if cu is None:
                raise ValidationError({"customer_id": "Customer not found."})
            c.customer_id, c.customer_name = cu.pk, cu.display_name
    for kind in ("vendor", "project"):
        if f"{kind}_id" in data:
            value = _uuid(data[f"{kind}_id"], f"{kind}_id")
            setattr(c, f"{kind}_id", value)
            setattr(c, f"{kind}_name", common.clean_text(data.get(f"{kind}_name"), f"{kind}_name", max_length=200) if value else "")


def save_contract(company, actor, data, contract=None):
    creating = contract is None
    c = contract or Contract(company=company)
    if not creating and c.status == Contract.Status.TERMINATED and set(data) - {"notes"}:
        raise ValidationError({"detail": "A terminated contract can only have its notes changed."})
    before = None if creating else common.snapshot(c, CONTRACT_FIELDS)
    if creating or "title" in data:
        c.title = common.clean_text(data.get("title"), "title", max_length=200, required=True, label="the title")
    if creating or "counterparty" in data:
        c.counterparty = common.clean_text(data.get("counterparty"), "counterparty", max_length=200, required=True, label="the counterparty")
    if creating or "contract_type" in data:
        c.contract_type = common.pick(data.get("contract_type"), "contract_type", C.CONTRACT_TYPES, default="other")
    if creating or "effective_date" in data:
        c.effective_date = common.parse_date(data.get("effective_date"), "effective_date", required=True)
    if "expiry_date" in data:
        c.expiry_date = common.parse_date(data["expiry_date"], "expiry_date")
    if "auto_renew" in data:
        c.auto_renew = common.flag(data["auto_renew"], "auto_renew")
    for f, lo, hi, msg in (("renewal_term_months", 1, 120, "Renewal term must be 1 to 120 months."),
                           ("renewal_notice_days", 0, 365, "Notice days must be 0 to 365.")):
        if f in data:
            v = data[f]
            if isinstance(v, str) and v.strip().isdigit():
                v = int(v)
            if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                raise ValidationError({f: msg})
            setattr(c, f, v)
    if "value" in data:
        c.value = common.parse_money(data["value"], "value")
    if "status" in data:
        status = common.pick(data["status"], "status", Contract.Status.choices)
        if status == Contract.Status.TERMINATED and c.status != status:
            raise ValidationError({"status": "Use Terminate and give a reason."})
        c.status = status
    if "owner" in data:
        c.owner = common.get_user(data["owner"], "owner")
    if "notes" in data:
        c.notes = common.clean_text(data["notes"], "notes", max_length=4000)
    _check_dates(c.effective_date, c.expiry_date, "expiry_date", "The expiry date cannot be before the effective date.")
    if c.auto_renew and c.expiry_date is None:
        raise ValidationError({"auto_renew": "Auto-renewal needs an expiry date to renew from."})
    _soft_links(c, data)
    with transaction.atomic():
        if creating:
            c.reference = common.new_reference(company, "CON", actor=actor, on_date=c.effective_date)
        c.save()
        after = common.snapshot(c, CONTRACT_FIELDS)
        if creating:
            common.log(company, actor, "contract", c.pk, "created", new=after)
        elif before != after:
            common.log(company, actor, "contract", c.pk, "updated", previous=before, new=after)
    return c


def renew_contract(c, actor, data, today=None):
    today = today or datetime.date.today()
    if c.status == Contract.Status.TERMINATED:
        raise ValidationError({"detail": "A terminated contract cannot be renewed."})
    new_expiry = common.parse_date(data.get("new_expiry_date"), "new_expiry_date", required=True)
    current = effective_expiry(c, today)
    if current is not None and new_expiry <= current:
        raise ValidationError({"new_expiry_date": f"The new expiry must be after the current one ({current.isoformat()})."})
    before = common.snapshot(c, CONTRACT_FIELDS)
    c.expiry_date = new_expiry
    c.status = Contract.Status.ACTIVE
    c.renewal_count += 1
    if "value" in data:
        c.value = common.parse_money(data["value"], "value")
    c.save()
    common.log(c.company, actor, "contract", c.pk, "updated", previous=before, new=common.snapshot(c, CONTRACT_FIELDS),
               reason=common.clean_text(data.get("note"), "note", max_length=300) or "Renewed")
    return c


def terminate_contract(c, actor, data, today=None):
    today = today or datetime.date.today()
    if c.status == Contract.Status.TERMINATED:
        raise ValidationError({"detail": "This contract is already terminated."})
    reason = common.clean_text(data.get("reason"), "reason", max_length=300, required=True, label="the reason for ending it")
    on = common.parse_date(data.get("terminated_on"), "terminated_on") or today
    before = common.snapshot(c, CONTRACT_FIELDS)
    c.status, c.terminated_on, c.termination_reason = Contract.Status.TERMINATED, on, reason
    c.save()
    common.log(c.company, actor, "contract", c.pk, "deactivated", previous=before, new=common.snapshot(c, CONTRACT_FIELDS), reason=reason)
    return c


def delete_contract(c, actor):
    if c.status != Contract.Status.DRAFT:
        raise ValidationError({"detail": "Only a draft can be deleted. Terminate an active contract instead."})
    before = common.snapshot(c, CONTRACT_FIELDS)
    pk = c.pk
    c.delete()
    common.log(c.company, actor, "contract", pk, "deleted", previous=before)


# ============================================================================ notices and cases
def hearing_state(case, today):
    if case.status in (LegalCase.Status.DISPOSED, LegalCase.Status.CLOSED) or not case.next_hearing_date:
        return None
    if case.next_hearing_date < today:
        return "past"
    if case.next_hearing_date == today:
        return "today"
    return "soon" if (case.next_hearing_date - today).days <= 14 else "later"


def case_dict(c, today=None):
    today = today or datetime.date.today()
    return {
        "id": str(c.id), "reference": c.reference, "title": c.title, "case_type": c.case_type,
        "case_type_label": dict(C.CASE_TYPES).get(c.case_type, c.case_type), "party_role": c.party_role,
        "party_role_label": dict(C.PARTY_ROLES).get(c.party_role, c.party_role), "forum": c.forum, "case_number": c.case_number,
        "counterparty": c.counterparty, "filed_date": c.filed_date, "next_hearing_date": c.next_hearing_date,
        "hearing_state": hearing_state(c, today), "lawyer": c.lawyer, "lawyer_contact": c.lawyer_contact,
        "amount_involved": f"{c.amount_involved:.2f}" if c.amount_involved is not None else None, "status": c.status,
        "status_label": c.get_status_display(), "outcome": c.outcome, "closed_on": c.closed_on, "description": c.description,
        "owner": {"id": str(c.owner_id), "name": common.user_name(c.owner)} if c.owner_id else None,
    }


def save_case(company, actor, data, case=None, today=None):
    today = today or datetime.date.today()
    creating = case is None
    c = case or LegalCase(company=company)
    before = None if creating else common.snapshot(c, CASE_FIELDS)
    if creating or "title" in data:
        c.title = common.clean_text(data.get("title"), "title", max_length=200, required=True, label="a title")
    if "case_type" in data:
        c.case_type = common.pick(data["case_type"], "case_type", C.CASE_TYPES)
    if "party_role" in data:
        c.party_role = common.pick(data["party_role"], "party_role", C.PARTY_ROLES)
    for f, n in (("forum", 200), ("case_number", 80), ("counterparty", 200), ("lawyer", 200), ("lawyer_contact", 120),
                 ("outcome", 4000), ("description", 4000)):
        if f in data:
            setattr(c, f, common.clean_text(data[f], f, max_length=n))
    for f in ("filed_date", "next_hearing_date", "closed_on"):
        if f in data:
            setattr(c, f, common.parse_date(data[f], f))
    if "amount_involved" in data:
        c.amount_involved = common.parse_money(data["amount_involved"], "amount_involved")
    if "owner" in data:
        c.owner = common.get_user(data["owner"], "owner")
    if "status" in data:
        c.status = common.pick(data["status"], "status", LegalCase.Status.choices)
    closing = c.status in (LegalCase.Status.DISPOSED, LegalCase.Status.CLOSED)
    if closing:
        if not c.outcome.strip():
            raise ValidationError({"outcome": "Record the outcome before closing or disposing of the matter."})
        c.closed_on = c.closed_on or today
        c.next_hearing_date = None
    else:
        c.closed_on = None
    _check_dates(c.filed_date, c.next_hearing_date, "next_hearing_date", "The next hearing cannot be before the filing date.")
    with transaction.atomic():
        if creating:
            c.reference = common.new_reference(company, "CAS", actor=actor, on_date=c.filed_date or today)
        c.save()
        after = common.snapshot(c, CASE_FIELDS)
        if creating:
            common.log(company, actor, "legal_case", c.pk, "created", new=after)
        elif before != after:
            common.log(company, actor, "legal_case", c.pk, "updated", previous=before, new=after)
    return c


def delete_case(c, actor):
    if c.status in (LegalCase.Status.DISPOSED, LegalCase.Status.CLOSED):
        raise ValidationError({"detail": "A closed matter is kept as a record and cannot be deleted."})
    before = common.snapshot(c, CASE_FIELDS)
    pk = c.pk
    c.delete()
    common.log(c.company, actor, "legal_case", pk, "deleted", previous=before)


# ============================================================================ policies
def policy_dict(p, user=None, today=None):
    today = today or datetime.date.today()
    acks = list(p.acknowledgements.filter(version=p.version).select_related("user"))
    mine = any(a.user_id == user.pk for a in acks) if user else False
    return {
        "id": str(p.id), "reference": p.reference, "name": p.name, "category": p.category, "version": p.version,
        "effective_date": p.effective_date, "review_date": p.review_date,
        "review_due": bool(p.review_date and p.review_date <= today and p.status == Policy.Status.ACTIVE),
        "status": p.status, "status_label": p.get_status_display(), "summary": p.summary,
        "requires_acknowledgement": p.requires_acknowledgement, "acknowledged_count": len(acks), "acknowledged_by_me": mine,
        "acknowledgements": [{"user": common.user_name(a.user), "at": a.acknowledged_at} for a in acks],
        "owner": {"id": str(p.owner_id), "name": common.user_name(p.owner)} if p.owner_id else None,
    }


def save_policy(company, actor, data, policy=None, today=None):
    today = today or datetime.date.today()
    creating = policy is None
    p = policy or Policy(company=company)
    before = None if creating else common.snapshot(p, POLICY_FIELDS)
    if creating or "name" in data:
        p.name = common.clean_text(data.get("name"), "name", max_length=200, required=True, label="the policy name")
    if "category" in data:
        p.category = common.clean_text(data["category"], "category", max_length=60)
    if "version" in data or creating:
        p.version = common.clean_text(data.get("version", p.version), "version", max_length=20, required=True, label="the version")
    if creating or "effective_date" in data:
        p.effective_date = common.parse_date(data.get("effective_date"), "effective_date", required=True)
    if "review_date" in data:
        p.review_date = common.parse_date(data["review_date"], "review_date")
    if "summary" in data:
        p.summary = common.clean_text(data["summary"], "summary", max_length=4000)
    if "requires_acknowledgement" in data:
        p.requires_acknowledgement = common.flag(data["requires_acknowledgement"], "requires_acknowledgement")
    if "status" in data:
        p.status = common.pick(data["status"], "status", Policy.Status.choices)
    if "owner" in data:
        p.owner = common.get_user(data["owner"], "owner")
    _check_dates(p.effective_date, p.review_date, "review_date", "The review date cannot be before the effective date.")
    with transaction.atomic():
        if creating:
            p.reference = common.new_reference(company, "POL", actor=actor, on_date=p.effective_date)
        p.save()
        after = common.snapshot(p, POLICY_FIELDS)
        if creating:
            common.log(company, actor, "compliance_policy", p.pk, "created", new=after)
        elif before != after:
            common.log(company, actor, "compliance_policy", p.pk, "updated", previous=before, new=after)
    return p


def acknowledge_policy(p, user):
    if p.status != Policy.Status.ACTIVE:
        raise ValidationError({"detail": "Only an active policy can be acknowledged."})
    if not p.requires_acknowledgement:
        raise ValidationError({"detail": "This policy does not ask for acknowledgement."})
    ack, created = PolicyAcknowledgement.objects.get_or_create(policy=p, user=user, version=p.version)
    if created:
        common.log(p.company, user, "compliance_policy", p.pk, "updated", new={"acknowledged_version": p.version})
    return ack


def delete_policy(p, actor):
    if p.status == Policy.Status.ACTIVE:
        raise ValidationError({"detail": "Retire an active policy instead of deleting it."})
    before = common.snapshot(p, POLICY_FIELDS)
    pk = p.pk
    p.delete()
    common.log(p.company, actor, "compliance_policy", pk, "deleted", previous=before)
