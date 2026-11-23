"""CompanyService: the one place other ERP modules read company details from.

Quotation, invoice, minutes and letter code must call these functions rather than reading
models directly or hardcoding company details.
"""
import datetime
from dataclasses import dataclass

from django.db.models import Q

from ..constants import ContactType, DocType
from ..models import (
    AuthorizedSignatory,
    CompanyAddress,
    CompanyBankAccount,
    CompanyBranding,
    CompanyContact,
    CompanyPolicy,
    CompanyProfile,
    CompanyTaxRegistration,
    CompanyTaxSettings,
)


class CompanyNotConfigured(Exception):
    """Raised when a document is requested before the company profile exists."""


def get_company():
    company = CompanyProfile.objects.first()
    if company is None:
        raise CompanyNotConfigured("The company profile has not been set up yet.")
    return company


def _effective(qs, on_date, from_field="effective_from", to_field="effective_to"):
    return qs.filter(Q(**{f"{from_field}__isnull": True}) | Q(**{f"{from_field}__lte": on_date})).filter(
        Q(**{f"{to_field}__isnull": True}) | Q(**{f"{to_field}__gte": on_date})
    )


def get_branding(company):
    branding, _ = CompanyBranding.objects.get_or_create(company=company)
    return branding


def get_tax_settings(company):
    settings_, _ = CompanyTaxSettings.objects.get_or_create(company=company)
    return settings_


def resolve_address(company, doc_type, on_date=None):
    """Address printed on `doc_type`: an explicit selection, else the primary, else the registered office."""
    on_date = on_date or datetime.date.today()
    active = _effective(CompanyAddress.objects.filter(company=company, is_active=True), on_date)
    return (
        active.filter(document_types__contains=[doc_type]).first()
        or active.filter(is_primary=True).first()
        or active.filter(address_type=CompanyAddress.AddressType.REGISTERED).first()
        or active.first()
    )


def resolve_contact(company, doc_type):
    """Contact printed on `doc_type`: explicit selection, else the primary contact of the natural type."""
    active = CompanyContact.objects.filter(company=company, is_active=True)
    explicit = active.filter(document_types__contains=[doc_type]).order_by("-is_primary").first()
    if explicit:
        return explicit
    for contact_type in (ContactType.FOR_DOCUMENT.get(doc_type), ContactType.GENERAL):
        if not contact_type:
            continue
        match = active.filter(contact_type=contact_type).order_by("-is_primary").first()
        if match:
            return match
    return None


def resolve_tax_registration(company, on_date=None):
    """The GSTIN to print: only when the company is registered and the GSTIN is verified and in force."""
    on_date = on_date or datetime.date.today()
    if company.gst_registration_status != CompanyProfile.GstStatus.REGISTERED:
        return None
    regs = _effective(
        CompanyTaxRegistration.objects.filter(
            company=company,
            registration_type=CompanyTaxRegistration.RegType.GSTIN,
            is_active=True,
            verification_status="verified",
        ),
        on_date,
    )
    return regs.first()


def resolve_bank_account(company, doc_type, on_date=None):
    """Bank details are printed only on documents where the administrator enabled it, and only when verified."""
    if doc_type not in DocType.BANK_DETAIL_TYPES:
        return None
    on_date = on_date or datetime.date.today()
    flag = "show_on_quotations" if doc_type == DocType.QUOTATION else "show_on_invoices"
    accounts = _effective(
        CompanyBankAccount.objects.filter(
            company=company, is_active=True, verification_status="verified", **{flag: True}
        ),
        on_date,
    )
    return accounts.order_by("-is_primary").first()


def resolve_policy(company, on_date=None):
    on_date = on_date or datetime.date.today()
    # Superseded versions stay in force until their effective-to date, so a future-dated successor does
    # not leave a gap.
    in_force = CompanyPolicy.objects.filter(
        company=company, status__in=[CompanyPolicy.Status.ACTIVE, CompanyPolicy.Status.SUPERSEDED]
    )
    return _effective(in_force, on_date).order_by("-version_number").first()


def resolve_signatory(company, doc_type, on_date=None):
    on_date = on_date or datetime.date.today()
    for signatory in AuthorizedSignatory.objects.filter(company=company, status="active").order_by("name"):
        if signatory.authorizes(doc_type, on_date):
            return signatory
    return None


@dataclass
class ResolvedCompany:
    company: CompanyProfile
    branding: CompanyBranding
    tax_settings: CompanyTaxSettings
    address: CompanyAddress | None
    contact: CompanyContact | None
    tax_registration: CompanyTaxRegistration | None
    bank_account: CompanyBankAccount | None
    policy: CompanyPolicy | None
    signatory: AuthorizedSignatory | None


def resolve_for_document(doc_type, on_date=None, company=None):
    """Everything a document of `doc_type` needs, resolved in one call."""
    company = company or get_company()
    on_date = on_date or datetime.date.today()
    return ResolvedCompany(
        company=company,
        branding=get_branding(company),
        tax_settings=get_tax_settings(company),
        address=resolve_address(company, doc_type, on_date),
        contact=resolve_contact(company, doc_type),
        tax_registration=resolve_tax_registration(company, on_date),
        bank_account=resolve_bank_account(company, doc_type, on_date),
        policy=resolve_policy(company, on_date),
        signatory=resolve_signatory(company, doc_type, on_date),
    )


def build_snapshot(resolved: ResolvedCompany, doc_type):
    """Frozen, JSON-safe copy of every company detail printed on a document.

    The bank account number is deliberately included only in masked form.
    """
    c, b = resolved.company, resolved.branding
    a, k = resolved.address, resolved.contact
    snap = {
        "company": {
            "legal_name": c.legal_name, "display_name": c.display_name, "trade_name": c.trade_name,
            "legal_entity_type": c.legal_entity_type, "llpin": c.llpin, "pan": c.pan,
            "website": c.website, "primary_email": c.primary_email, "primary_phone": c.primary_phone,
            "currency": c.default_currency,
        },
        "address": None if a is None else {
            "id": str(a.id), "label": a.label, "type": a.address_type, "lines": _address_lines(a),
            "single_line": _address_single_line(a),
            "state": a.state, "state_code": a.state_code,
        },
        "contact": None if k is None else {
            "id": str(k.id), "name": k.display_name, "email": k.email, "phone": k.phone, "type": k.contact_type,
        },
        "tax": {
            "gst_status": c.gst_registration_status,
            "gstin": resolved.tax_registration.registration_number if resolved.tax_registration else "",
            "gst_applicable": resolved.tax_settings.gst_applicable,
            "gst_treatment": resolved.tax_settings.gst_treatment,
            "invoice_title": resolved.tax_settings.invoice_title,
            "show_gstin": resolved.tax_settings.show_gstin_on_documents,
        },
        "bank": None if resolved.bank_account is None else {
            "id": str(resolved.bank_account.id), "bank_name": resolved.bank_account.bank_name,
            "branch": resolved.bank_account.branch, "holder": resolved.bank_account.account_holder_name,
            "account_number_masked": resolved.bank_account.masked_account_number,
            "ifsc": resolved.bank_account.ifsc_code,
        },
        "policy": None if resolved.policy is None else {
            "id": str(resolved.policy.id), "version": resolved.policy.version_number,
            "payment_terms": resolved.policy.payment_terms,
            "terms": {
                DocType.QUOTATION: resolved.policy.quotation_terms,
                DocType.INVOICE: resolved.policy.invoice_terms,
                DocType.PROFORMA: resolved.policy.invoice_terms,
                DocType.DEBIT_NOTE: resolved.policy.invoice_terms,
            }.get(doc_type, ""),
            "confidentiality_notice": resolved.policy.confidentiality_notice,
            "footer_text": resolved.policy.document_footer_text,
        },
        "signatory": None if resolved.signatory is None else {
            "id": str(resolved.signatory.id), "name": resolved.signatory.name,
            "designation": resolved.signatory.designation,
        },
        "branding": {
            "version": b.version, "template": b.default_template, "page_size": b.default_page_size,
            "font_family": b.font_family, "primary_colour": b.primary_colour,
            "secondary_colour": b.secondary_colour, "logo_position": b.logo_position,
            "logo_width_mm": b.logo_width_mm, "header_alignment": b.header_alignment,
            "footer_alignment": b.footer_alignment, "body_font_size": b.body_font_size,
            "heading_font_size": b.heading_font_size, "footer_font_size": b.footer_font_size,
            "margins_mm": [b.margin_top_mm, b.margin_right_mm, b.margin_bottom_mm, b.margin_left_mm],
            "header_height_mm": b.header_height_mm, "footer_height_mm": b.footer_height_mm,
            "show_page_numbers": b.show_page_numbers, "page_number_format": b.page_number_format,
            "watermark": b.watermark_text if b.watermark_enabled and b.watermark_type == "text" else "",
            "watermark_enabled": b.watermark_enabled, "watermark_type": b.watermark_type,
            "watermark_size_mm": b.watermark_size_mm, "watermark_opacity": str(b.watermark_opacity),
            "page_background_colour": b.page_background_colour, "phone_label": b.phone_label,
            "line_spacing": str(b.line_spacing), "paragraph_spacing_pt": b.paragraph_spacing_pt,
            "body_top_gap_mm": b.body_top_gap_mm, "justify_body": b.justify_body, "signature_block": b.signature_block,
            "show_address_in_header": b.show_address_in_header,
            "show_contact_in_header": b.show_contact_in_header, "footer_text_override": b.footer_text_override,
            "language": b.default_language,
        },
        "profile_version": c.version,
    }
    return snap


def _address_single_line(a):
    city = f"{a.city} - {a.postal_code}" if a.city else a.postal_code
    parts = [a.building_name, a.line1, a.line2, a.locality, a.district if a.district != a.city else "", city,
             a.state, a.country]
    return ", ".join(p for p in parts if p)


def _address_lines(a):
    first = ", ".join(p for p in (a.building_name, a.line1) if p)
    second = ", ".join(p for p in (a.line2, a.locality) if p)
    city = ", ".join(p for p in (a.city, a.district if a.district != a.city else "") if p)
    region = f"{a.state} - {a.postal_code}" + (f", {a.country}" if a.country and a.country != "India" else "")
    return [line for line in (first, second, city, region) if line]
