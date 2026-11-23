"""Vendor records: validation, numbering, the opening balance and switching a vendor off."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounting.models import JournalEntry
from apps.company.constants import DocType
from apps.company.services import numbering

from . import amounts, posting
from .common import Action, ZERO, log, money, optional_amount
from ..models import Vendor, VendorBill, VendorPayment, PurchaseOrder, VendorQuotation

FIELDS = ("name", "contact_person", "email", "phone", "gst_treatment", "gstin", "pan", "address_line1", "address_line2", "city",
          "state", "state_code", "postal_code", "country", "payment_terms_days", "tds_section", "tds_rate", "opening_balance",
          "opening_balance_date", "bank_account_holder", "bank_name", "bank_account_number", "bank_ifsc", "notes", "is_active")

# Common TDS sections with their usual rates (check the current Income-tax Act rates before relying on them).
TDS_SECTIONS = [
    {"section": "194C", "label": "194C Contractors (individual/HUF 1%, others 2%)", "rate": "2"},
    {"section": "194J", "label": "194J Professional fees (10%)", "rate": "10"},
    {"section": "194J-T", "label": "194J Technical services (2%)", "rate": "2"},
    {"section": "194H", "label": "194H Commission or brokerage (2%)", "rate": "2"},
    {"section": "194I-A", "label": "194I Rent of plant and machinery (2%)", "rate": "2"},
    {"section": "194I-B", "label": "194I Rent of land or building (10%)", "rate": "10"},
    {"section": "194Q", "label": "194Q Purchase of goods (0.1%)", "rate": "0.1"},
    {"section": "194A", "label": "194A Interest other than on securities (10%)", "rate": "10"},
]

# Audit never keeps these values; the fact that they changed is recorded.
REDACTED_FIELDS = ("email", "phone", "gstin", "pan", "bank_account_number", "bank_ifsc", "bank_account_holder")


def snapshot(v):
    return {f: (str(getattr(v, f)) if isinstance(getattr(v, f), (Decimal, datetime.date)) else getattr(v, f)) for f in FIELDS}


def opening_posted(vendor):
    return JournalEntry.objects.filter(company=vendor.company, source=JournalEntry.Source.OPENING,
                                       source_id=f"vendor:{vendor.pk}").exists()


def _apply(vendor, data):
    for f in FIELDS:
        if f not in data:
            continue
        value = data[f]
        if f == "payment_terms_days":
            value = None if value in (None, "") else value
            if value is not None:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise ValidationError({f: "Enter a whole number of days."})
                if not 0 <= value <= 365:
                    raise ValidationError({f: "Payment terms must be between 0 and 365 days."})
        elif f == "tds_rate":
            value = optional_amount(value, "TDS rate", f, high=Decimal("100"))
            value = None if value is None else money(value)
        elif f == "opening_balance":
            value = money(optional_amount(value, "Opening balance", f) or ZERO)
        elif f == "opening_balance_date":
            value = None if value in (None, "") else _date(value, f)
        elif f == "bank_account_number":
            vendor.set_bank_account_number(value if isinstance(value, str) else "")
            continue
        elif f == "is_active":
            if not isinstance(value, bool):
                raise ValidationError({f: "Send true or false."})
        elif isinstance(value, str):
            value = value.strip()
        elif value is None:
            value = ""
        elif not isinstance(value, (int, float)):
            raise ValidationError({f: "This value is not valid."})
        setattr(vendor, f, value)
    try:
        vendor.full_clean(exclude=["code"])
    except DjangoValidationError as exc:
        raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages})


def _date(value, field):
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({field: "Use a date in YYYY-MM-DD format."})


@transaction.atomic
def create(company, actor, data):
    v = Vendor(company=company)
    _apply(v, data)
    if v.opening_balance > 0 and not v.opening_balance_date:
        v.opening_balance_date = datetime.date.today()
    v.code = numbering.allocate_number(company, DocType.VENDOR, actor=actor).reference_number
    v.save()
    if v.opening_balance > 0:
        posting.post_opening_balance(v, actor)
    log(company, actor, "vendor", v.pk, Action.CREATED, new=snapshot(v))
    return v


@transaction.atomic
def update(vendor, actor, data):
    vendor = Vendor.objects.select_for_update().get(pk=vendor.pk)
    before = snapshot(vendor)
    was_posted = opening_posted(vendor)
    _apply(vendor, data)
    after = snapshot(vendor)
    if was_posted and (before["opening_balance"] != after["opening_balance"] or before["opening_balance_date"] != after["opening_balance_date"]):
        raise ValidationError({"opening_balance": "The opening balance has been posted to the books and cannot be changed here. "
                                                  "Record a journal entry in Accounting to correct it."})
    if before["is_active"] and not vendor.is_active:
        owed = amounts.vendor_balances(vendor.company, vendor_id=vendor.pk).get(vendor.pk, {}).get("outstanding", ZERO)
        if owed > 0:
            raise ValidationError({"is_active": f"We still owe this vendor {owed}; it cannot be switched off."})
    if vendor.opening_balance > 0 and not was_posted:
        if not vendor.opening_balance_date:
            vendor.opening_balance_date = datetime.date.today()
        vendor.save()
        posting.post_opening_balance(vendor, actor)
        after = snapshot(vendor)
    else:
        vendor.save()
    if before != after:
        action = Action.UPDATED
        if before["is_active"] != after["is_active"]:
            action = Action.ACTIVATED if after["is_active"] else Action.DEACTIVATED
        log(vendor.company, actor, "vendor", vendor.pk, action, previous=before, new=after)
    return vendor


@transaction.atomic
def delete(vendor, actor):
    vendor = Vendor.objects.select_for_update().get(pk=vendor.pk)
    used = (VendorBill.objects.filter(vendor=vendor).exists() or VendorPayment.objects.filter(vendor=vendor).exists()
            or PurchaseOrder.objects.filter(vendor=vendor).exists() or VendorQuotation.objects.filter(vendor=vendor).exists()
            or opening_posted(vendor))
    if used:
        raise ValidationError("This vendor has bills, payments, orders or an opening balance, so it cannot be deleted. Switch it off instead.")
    log(vendor.company, actor, "vendor", vendor.pk, Action.DELETED, previous={"name": vendor.name, "code": vendor.code})
    vendor.delete()
