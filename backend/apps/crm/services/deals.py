"""Deals: creating, the pipeline, moving between stages, winning (which links or creates the sales Customer), line items."""
import datetime
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from apps.company.constants import DocType
from apps.company.services import audit, numbering
from apps.sales.models import Customer

from .. import constants as c
from ..models import Account, Activity, Contact, Deal, DealItem, DealStageHistory, PipelineStage, Product
from . import common
from .common import Action, money

DEAL_FIELDS = ("name", "amount", "expected_close_date", "source", "next_step", "competitor", "notes", "probability")
AUDIT_FIELDS = ("reference_code", "name", "account", "contact", "stage", "status", "amount", "probability", "expected_close_date",
                "owner", "next_step", "lost_reason", "competitor", "customer_id", "project_id")


def first_open_stage(company):
    stage = PipelineStage.objects.filter(company=company, is_active=True, kind="open").order_by("position").first()
    if stage is None:
        raise ValidationError({"stage": "There is no open pipeline stage. Add one in CRM settings."})
    return stage


def _audit_values(deal):
    return common.snapshot(deal, AUDIT_FIELDS)


def _record_history(deal, from_stage, to_stage, actor, note=""):
    DealStageHistory.objects.create(
        deal=deal, from_stage=from_stage, to_stage=to_stage, from_name=from_stage.name if from_stage else "", to_name=to_stage.name,
        amount=deal.amount, probability=deal.probability, changed_by=actor, note=note[:300])


@transaction.atomic
def create_deal(company, actor, data, *, account, contact=None, stage=None, owner=None, lead=None):
    stage = stage or first_open_stage(company)
    if stage.kind != "open" or not stage.is_active:
        raise ValidationError({"stage": "A new deal starts in an open stage; move it to Won or Lost when it closes."})
    if not account.is_active:
        raise ValidationError({"account": "This account is switched off."})
    if contact is not None and contact.account_id not in (None, account.pk):
        raise ValidationError({"contact": "This contact belongs to a different account."})
    deal = Deal(company=company, account=account, contact=contact, stage=stage, owner=owner or actor, lead=lead, created_by=actor,
                probability=stage.probability)
    common.apply_fields(deal, {**data, "probability": data.get("probability", stage.probability)}, DEAL_FIELDS)
    deal.reference_code = numbering.allocate_number(company, DocType.DEAL, actor=actor).reference_number
    deal.save()
    _record_history(deal, None, stage, actor, "Deal created")
    common.log(company, actor, "deal", deal.pk, Action.CREATED, new=_audit_values(deal))
    return deal


@transaction.atomic
def update_deal(deal, actor, data, *, account=None, contact=..., owner=...):
    deal = Deal.objects.select_for_update(of=("self",)).select_related("stage", "account").get(pk=deal.pk)
    before = _audit_values(deal)
    if "amount" in data and deal.items.exists():
        raise ValidationError({"amount": "The value comes from the line items; change the items instead."})
    if "probability" in data and deal.status != "open":
        raise ValidationError({"probability": "A closed deal's probability is fixed."})
    if account is not None and account.pk != deal.account_id:
        if deal.status == "won":
            raise ValidationError({"account": "A won deal stays with the account that became the customer."})
        if not account.is_active:
            raise ValidationError({"account": "This account is switched off."})
        deal.account = account
        if contact is ... and deal.contact_id and deal.contact.account_id not in (None, account.pk):
            deal.contact = None
    if contact is not ...:
        if contact is not None and contact.account_id not in (None, deal.account_id):
            raise ValidationError({"contact": "This contact belongs to a different account."})
        deal.contact = contact
    if owner is not ...:
        deal.owner = owner
    common.apply_fields(deal, data, DEAL_FIELDS)
    deal.save()
    after = _audit_values(deal)
    if before != after:
        common.log(deal.company, actor, "deal", deal.pk, Action.UPDATED, previous=before, new=after)
    return deal


@transaction.atomic
def delete_deal(deal, actor):
    if deal.status == "won":
        raise ValidationError("A won deal is kept as a record. Move it to another stage first if it was won by mistake.")
    Activity.objects.filter(deal=deal, lead=None, contact=None, account=None).delete()
    common.log(deal.company, actor, "deal", deal.pk, Action.DELETED, previous=_audit_values(deal))
    deal.delete()


# --------------------------------------------------------------------------- line items

@transaction.atomic
def set_items(deal, actor, items):
    """Replace a deal's line items; the deal value becomes the sum of the lines (before GST)."""
    deal = Deal.objects.select_for_update(of=("self",)).get(pk=deal.pk)
    if not isinstance(items, list):
        raise ValidationError({"items": "Send a list of items."})
    if len(items) > 50:
        raise ValidationError({"items": "At most 50 lines."})
    rows, errors = [], {}
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            errors[f"items.{i}"] = "Send each line as an object."
            continue
        product = None
        if raw.get("product"):
            try:
                product = Product.objects.filter(pk=raw["product"], company=deal.company).first()
            except (ValueError, ValidationError):
                product = None
            if product is None:
                errors[f"items.{i}.product"] = "Product not found."
                continue
        try:
            qty = common.decimal_from(raw.get("quantity") or 1, "quantity", maximum=Decimal("1000000"))
            price = common.decimal_from(raw.get("unit_price") if raw.get("unit_price") not in (None, "") else (product.unit_price if product else 0),
                                        "unit_price")
            gst = common.decimal_from(raw.get("gst_rate") if raw.get("gst_rate") not in (None, "") else (product.gst_rate if product else 0),
                                      "gst_rate", maximum=Decimal(100))
        except ValidationError as exc:
            for k, v in exc.message_dict.items():
                errors[f"items.{i}.{k}"] = v[0]
            continue
        desc = (raw.get("description") or (product.name if product else "")).strip()
        if not desc:
            errors[f"items.{i}.description"] = "Describe the item."
        if qty <= 0:
            errors[f"items.{i}.quantity"] = "Quantity must be more than zero."
        rows.append(DealItem(deal=deal, product=product, description=desc[:300], quantity=qty, unit_price=price, gst_rate=gst, position=i))
    if errors:
        raise ValidationError(errors)
    before = _audit_values(deal)
    deal.items.all().delete()
    DealItem.objects.bulk_create(rows)
    if rows:
        deal.amount = sum((money(r.quantity * r.unit_price) for r in rows), Decimal("0.00"))
        deal.save()
    after = _audit_values(deal)
    common.log(deal.company, actor, "deal", deal.pk, Action.UPDATED, previous=before, new={**after, "items": len(rows)},
               reason="Line items changed")
    return deal


def item_totals(items):
    """(taxable, tax, total) for line items, each line rounded to the paisa."""
    taxable = tax = Decimal("0.00")
    for it in items:
        line = money(it.quantity * it.unit_price)
        taxable += line
        tax += money(line * it.gst_rate / 100)
    return taxable, tax, taxable + tax


# --------------------------------------------------------------------------- stages and winning

def ensure_customer(account, actor):
    """The sales Customer for an account: the linked one, an existing match, or a new one. Returns (customer, created)."""
    company = account.company
    if account.customer_id:
        existing = Customer.objects.filter(pk=account.customer_id, company=company).first()
        if existing is not None:
            return existing, False
    active = Customer.objects.filter(company=company, is_active=True)
    match = None
    if account.gstin:
        match = active.filter(gstin=account.gstin.strip().upper()).first()
    match = match or active.filter(display_name__iexact=account.name.strip()).first()
    if match is not None:
        account.customer_id = match.pk
        account.save(update_fields=["customer_id", "updated_at"])
        return match, False
    primary = account.contacts.order_by("-is_primary", "created_at").first()
    customer = Customer(
        company=company, display_name=account.name, customer_type=Customer.Type.BUSINESS,
        contact_name=primary.name if primary else "", email=account.email or (primary.email if primary else ""),
        phone=account.phone or (primary.phone if primary else ""), gstin=account.gstin.strip().upper(),
        gst_treatment=Customer.GstTreatment.REGISTERED if account.gstin else Customer.GstTreatment.UNREGISTERED,
        address_line1=account.address, city=account.city, state=account.state if not account.gstin else "",
        notes=f"Created from the CRM account {account.name}.")
    try:
        customer.full_clean(exclude=["reference_code"], validate_unique=False, validate_constraints=False)
    except ValidationError as exc:
        problems = "; ".join(m for msgs in getattr(exc, "message_dict", {"": exc.messages}).values() for m in msgs)
        raise ValidationError({"account": f"The account cannot become a customer yet: {problems} Fix the account's details first."})
    customer.reference_code = numbering.allocate_number(company, DocType.CUSTOMER, actor=actor).reference_number
    customer.save()
    audit.log(company, actor, "customer", customer.pk, Action.CREATED,
              new={"display_name": customer.display_name, "reference_code": customer.reference_code, "gstin": customer.gstin},
              reason=f"Created when CRM deal was won ({account.name})", category="sales")
    account.customer_id = customer.pk
    account.save(update_fields=["customer_id", "updated_at"])
    return customer, True


@transaction.atomic
def move_stage(deal, stage, actor, *, lost_reason="", competitor="", note="", today=None):
    """Move a deal to another stage. Probability follows the stage; winning links or creates the Customer; losing needs a reason."""
    today = today or datetime.date.today()
    deal = Deal.objects.select_for_update(of=("self",)).select_related("stage", "account", "company").get(pk=deal.pk)
    if stage.company_id != deal.company_id:
        raise ValidationError({"stage": "Stage not found."})
    if stage.pk == deal.stage_id:
        return deal
    if not stage.is_active:
        raise ValidationError({"stage": "This stage is switched off."})
    before = _audit_values(deal)
    previous = deal.stage
    lost_reason = (lost_reason or "").strip()
    if stage.is_lost and not lost_reason:
        raise ValidationError({"lost_reason": "Say why the deal was lost."})
    deal.stage, deal.probability = stage, stage.probability
    if stage.is_won:
        deal.status, deal.closed_on, deal.lost_reason = "won", today, ""
        customer, _created = ensure_customer(deal.account, actor)
        deal.customer_id = customer.pk
    elif stage.is_lost:
        deal.status, deal.closed_on, deal.lost_reason = "lost", today, lost_reason[:100]
        if competitor.strip():
            deal.competitor = competitor.strip()[:150]
    else:
        deal.status, deal.closed_on, deal.lost_reason = "open", None, ""
    deal.save()
    _record_history(deal, previous, stage, actor, note or lost_reason)
    common.log(deal.company, actor, "deal", deal.pk, Action.UPDATED, previous=before, new=_audit_values(deal),
               reason=f"Moved to {stage.name}")
    return deal


# --------------------------------------------------------------------------- the board and forecasts

def weighted(deal_amount, probability):
    return money(Decimal(deal_amount) * Decimal(probability) / Decimal(100))


def forecast(company, *, owner=None):
    """(open value, weighted forecast, open deal count) for open deals. Each deal's weighted value is rounded to the paisa."""
    qs = Deal.objects.filter(company=company, status="open")
    if owner is not None:
        qs = qs.filter(owner=owner)
    total = weighted_total = Decimal("0.00")
    n = 0
    for amount, prob in qs.values_list("amount", "probability"):
        total += amount
        weighted_total += weighted(amount, prob)
        n += 1
    return money(total), money(weighted_total), n


def resolve_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ValidationError({"project_id": "Not a valid project."})
