"""Plain-dict representations of CRM records for the API."""
import datetime
from decimal import Decimal

from apps.company.constants import DocType
from apps.company.models import CompanyProfileAuditEvent, IssuedDocument
from apps.sales.models import Customer

from . import constants as c
from .models import Activity, DealItem
from .services import activities as activity_service
from .services import leads as lead_service
from .services.common import money
from .services.deals import item_totals, weighted

LEAD_STATUS = dict(c.LEAD_STATUSES)
RATING = dict(c.RATINGS)
ACTIVITY_TYPE = dict(c.ACTIVITY_TYPES)


def person(user):
    return {"id": str(user.pk), "name": user.get_full_name() or user.get_username()} if user else None


def ref(obj, name_attr="name"):
    return {"id": str(obj.pk), "name": getattr(obj, name_attr)} if obj else None


def lead_dict(lead, *, source_names=None, detail=False):
    source_names = source_names or {}
    out = {
        "id": str(lead.pk), "reference_code": lead.reference_code, "display_name": lead.display_name,
        "company_name": lead.company_name, "contact_name": lead.contact_name, "email": lead.email, "phone": lead.phone,
        "source": lead.source, "source_label": source_names.get(lead.source, lead.source.replace("_", " ").title()),
        "industry": lead.industry, "product_interest": lead.product_interest, "estimated_value": lead.estimated_value,
        "status": lead.status, "status_label": LEAD_STATUS.get(lead.status, lead.status), "rating": lead.rating,
        "rating_label": RATING.get(lead.rating, lead.rating), "owner": person(lead.owner), "city": lead.city, "state": lead.state,
        "notes": lead.notes, "score": lead.score, "suggested_rating": lead_service.suggested_rating(lead.score),
        "converted_at": lead.converted_at, "created_at": lead.created_at,
        "converted": {"account": ref(lead.converted_account), "contact": ref(lead.converted_contact),
                      "deal": ref(lead.converted_deal)} if lead.status == "converted" else None,
    }
    if detail:
        points, parts = lead_service.score(lead)
        out["score_breakdown"] = parts
    return out


def account_dict(a, *, counts=None, customer=None, detail=False):
    out = {
        "id": str(a.pk), "name": a.name, "industry": a.industry, "website": a.website, "email": a.email, "phone": a.phone,
        "gstin": a.gstin, "address": a.address, "city": a.city, "state": a.state, "owner": person(a.owner), "notes": a.notes,
        "customer_id": str(a.customer_id) if a.customer_id else None, "is_active": a.is_active, "created_at": a.created_at,
    }
    if counts is not None:
        out.update(counts)
    if customer is not None:
        out["customer"] = {"id": str(customer.pk), "name": customer.display_name, "reference_code": customer.reference_code}
    return out


def contact_dict(ct):
    return {
        "id": str(ct.pk), "name": ct.name, "designation": ct.designation, "email": ct.email, "phone": ct.phone,
        "account": ref(ct.account), "is_primary": ct.is_primary, "owner": person(ct.owner), "notes": ct.notes,
        "created_at": ct.created_at,
    }


def stage_dict(s, deal_count=None):
    out = {"id": str(s.pk), "name": s.name, "probability": s.probability, "position": s.position, "colour": s.colour, "kind": s.kind,
           "is_active": s.is_active}
    if deal_count is not None:
        out["deals"] = deal_count
    return out


def deal_dict(d, *, today=None, detail=False, extra=None):
    today = today or datetime.date.today()
    out = {
        "id": str(d.pk), "reference_code": d.reference_code, "name": d.name, "account": ref(d.account), "contact": ref(d.contact),
        "stage": {"id": str(d.stage_id), "name": d.stage.name, "colour": d.stage.colour, "kind": d.stage.kind},
        "status": d.status, "amount": d.amount, "probability": d.probability, "weighted": weighted(d.amount, d.probability),
        "expected_close_date": d.expected_close_date, "closed_on": d.closed_on, "owner": person(d.owner), "source": d.source,
        "next_step": d.next_step, "lost_reason": d.lost_reason, "competitor": d.competitor, "notes": d.notes,
        "lead": {"id": str(d.lead_id), "name": d.lead.display_name, "reference_code": d.lead.reference_code} if d.lead_id else None,
        "customer_id": str(d.customer_id) if d.customer_id else None,
        "project_id": str(d.project_id) if d.project_id else None, "created_at": d.created_at,
        "overdue": d.status == "open" and bool(d.expected_close_date) and d.expected_close_date < today,
        "age_days": (today - d.created_at.date()).days,
    }
    if extra:
        out.update(extra)
    if detail:
        items = list(d.items.all())
        taxable, tax, total = item_totals(items)
        out["items"] = [item_dict(i) for i in items]
        out["item_totals"] = {"taxable": taxable, "tax": tax, "total": total}
        history, prev = [], None
        for h in d.history.select_related("changed_by"):
            history.append({
                "id": str(h.pk), "from": h.from_name, "to": h.to_name, "at": h.created_at, "by": person(h.changed_by),
                "amount": h.amount, "probability": h.probability, "note": h.note,
                "days_in_previous": (h.created_at - prev).days if prev else None})
            prev = h.created_at
        out["history"] = history
        customer = Customer.objects.filter(pk=d.customer_id).first() if d.customer_id else None
        out["customer"] = {"id": str(customer.pk), "name": customer.display_name, "reference_code": customer.reference_code} if customer else None
    return out


def item_dict(i: DealItem):
    line = money(i.quantity * i.unit_price)
    return {"id": str(i.pk), "product": str(i.product_id) if i.product_id else None, "description": i.description,
            "quantity": i.quantity, "unit_price": i.unit_price, "gst_rate": i.gst_rate, "line_total": line,
            "gst_amount": money(line * i.gst_rate / 100)}


def product_dict(p):
    return {"id": str(p.pk), "name": p.name, "code": p.code, "description": p.description, "unit_price": p.unit_price,
            "gst_rate": p.gst_rate, "is_active": p.is_active}


def activity_dict(a: Activity, today=None):
    today = today or datetime.date.today()
    related = None
    for kind, obj in (("deal", a.deal), ("lead", a.lead), ("contact", a.contact), ("account", a.account)):
        if obj is not None:
            related = {"type": kind, "id": str(obj.pk), "name": getattr(obj, "name", None) or obj.display_name}
            break
    return {
        "id": str(a.pk), "type": a.type, "type_label": ACTIVITY_TYPE.get(a.type, a.type), "subject": a.subject,
        "description": a.description, "due_date": a.due_date, "due_time": a.due_time.strftime("%H:%M") if a.due_time else None,
        "priority": a.priority, "status": a.status, "bucket": activity_service.bucket(a, today), "completed_at": a.completed_at,
        "outcome": a.outcome, "owner": person(a.owner), "related": related,
        "links": {"lead": ref(a.lead, "display_name"), "contact": ref(a.contact), "account": ref(a.account), "deal": ref(a.deal)},
        "created_at": a.created_at, "created_by": person(a.created_by),
    }


def audit_summary(e: CompanyProfileAuditEvent):
    verb = {"created": "Created", "updated": "Updated", "deleted": "Deleted"}.get(e.action, e.get_action_display())
    detail = e.reason
    if e.action == "updated" and e.changed_fields:
        detail = ", ".join(f.replace("_", " ") for f in e.changed_fields[:8]) + (f" ({e.reason})" if e.reason else "")
    return {"kind": "audit", "id": str(e.pk), "at": e.occurred_at, "action": e.action, "title": verb, "detail": detail,
            "by": e.actor_label, "previous": e.previous_values, "new": e.new_values}


def quotations_for(company, names, deal_id=None, limit=25):
    """Issued quotations addressed to any of `names` (or raised for the deal): read-only, from the company's issued documents."""
    from django.db.models import Q
    q = Q(source_module="crm", source_id=str(deal_id)) if deal_id else Q(pk__in=[])
    for n in {x.strip() for x in names if x and x.strip()}:
        q |= Q(snapshot__quotation__customer__name__iexact=n)
    rows = []
    for d in IssuedDocument.objects.filter(q, company=company, document_type=DocType.QUOTATION).order_by("-issue_date", "-created_at")[:limit]:
        total = ((d.snapshot or {}).get("quotation") or {}).get("totals", {}).get("grand_total")
        rows.append({"id": str(d.pk), "number": d.reference_number, "issue_date": d.issue_date, "status": d.status,
                     "total": Decimal(total) if total else None, "title": d.title})
    return rows

