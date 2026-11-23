"""Issue branded documents and preserve exactly what was issued."""
import datetime
import hashlib

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction

from ..constants import DocType
from ..models import CompanyProfileAuditEvent, IssuedDocument
from . import audit, company as company_service, numbering, pdf, quotations


def _read(field_file):
    if not field_file:
        return None
    try:
        with field_file.open("rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None  # a missing logo file must not stop documents from being issued


def effective_logo(company, branding):
    """Alternate-less logo resolution: branding override first, then the company profile logo."""
    for source in (branding.primary_logo, company.logo):
        data = _read(source)
        if data:
            return data
    return None


def effective_watermark(company, branding):
    """Watermark image: the dedicated watermark image, else the letterhead logo."""
    return _read(branding.watermark_image) or effective_logo(company, branding)


def _signature_bytes(resolved):
    signatory = resolved.signatory
    if signatory is None or not resolved.branding.signature_block.get("show_signature_image"):
        return None
    return _read(signatory.signature_image) or (_read(signatory.partner.signature_image) if signatory.partner else None)


def _default_content(doc_type, resolved, snapshot, content):
    content = dict(content or {})
    policy = snapshot.get("policy") or {}
    if doc_type in (DocType.QUOTATION, DocType.INVOICE, DocType.PROFORMA, DocType.DEBIT_NOTE):
        content.setdefault("terms", policy.get("terms", ""))
        content.setdefault("include_bank", snapshot.get("bank") is not None)
        if policy.get("payment_terms"):
            # A quotation built from items prints these under the totals, not above the table.
            key = "after_totals" if "after_totals" in content else "paragraphs"
            content[key] = list(content.get(key, [])) + [f"Payment terms: {policy['payment_terms']}"]
    return content


TITLES = {DocType.PROFORMA: "Proforma Invoice", DocType.CREDIT_NOTE: "Credit Note", DocType.DEBIT_NOTE: "Debit Note"}
MODE_LABELS = {"in_person": "In person", "online": "Online", "hybrid": "Hybrid"}


def _prepare_minutes(company, actor, content, issue_date):
    """Allocate the meeting ID (unless one is given) and build the header rows of the minutes."""
    minutes = dict(content["minutes"])
    try:
        meeting_date = datetime.date.fromisoformat(minutes.get("date") or "") if minutes.get("date") else issue_date
    except ValueError:
        raise ValidationError({"content": "The meeting date must be in YYYY-MM-DD format."})
    meeting_id = (minutes.get("meeting_id") or "").strip()
    if not meeting_id:
        meeting_id = numbering.allocate_number(company, DocType.MEETING, actor=actor, on_date=meeting_date).reference_number
    minutes["meeting_id"] = meeting_id
    minutes["date"] = meeting_date.isoformat()
    when = meeting_date.strftime("%d %B %Y")
    start, end = minutes.get("start_time"), minutes.get("end_time")
    if start:
        when += f", {start}" + (f" to {end}" if end else "")
    venue = ", ".join(p for p in (minutes.get("venue"), MODE_LABELS.get(minutes.get("mode"))) if p)
    rows = [["Meeting ID", meeting_id], ["Meeting", minutes.get("title", "")], ["Meeting date and time", when],
            ["Venue / mode", venue], ["Minutes taken by", minutes.get("minute_taker", "")]]
    content = dict(content, minutes=minutes, meta=[r for r in rows if r[1]] + list(content.get("meta") or []))
    return content, meeting_id


def _title(doc_type, resolved, title):
    if title:
        return title
    if doc_type == DocType.MEETING_MINUTES:
        return "Minutes of Meeting"
    if doc_type == DocType.INVOICE:
        return resolved.tax_settings.invoice_title or "Invoice"
    if doc_type in TITLES:
        return TITLES[doc_type]
    return dict(DocType.CHOICES)[doc_type]


def issue_document(doc_type, *, actor, content, title="", issue_date=None, source_module="", source_id="",
                   numbered=True, supersedes=None, letterhead_template=None):
    """Resolve company data, allocate a number, render the PDF and store everything atomically.

    If rendering or storage fails the number allocation is rolled back too, so no number is
    consumed by a document that was never produced.
    """
    if doc_type not in DocType.PRINTABLE:
        raise ValidationError({"document_type": "This document type cannot be generated."})
    if letterhead_template is not None:
        if not letterhead_template.is_active:
            raise ValidationError({"letterhead_template": "This letterhead template is switched off."})
        if not numbered:
            raise ValidationError({"numbered": "Documents on a letterhead template are always numbered."})
    issue_date = issue_date or datetime.date.today()
    company = company_service.get_company()

    with transaction.atomic():
        resolved = company_service.resolve_for_document(doc_type, issue_date, company)
        snapshot = company_service.build_snapshot(resolved, doc_type)
        if doc_type == DocType.QUOTATION and content.get("quotation"):
            snapshot["quotation"], built = quotations.build(company, resolved, content["quotation"], issue_date)
            content = built
        elif doc_type in DocType.BILLING and content.get("billing"):
            # Sales documents (invoices, notes, proforma): the sales module passes the worked-out input; see apps/sales.
            snapshot["billing"], built = quotations.build(
                company, resolved, content["billing"], issue_date, purpose=doc_type, extra_meta=content.get("extra_meta"))
            content = built
        content = _default_content(doc_type, resolved, snapshot, content)
        if doc_type == DocType.MEETING_MINUTES and content.get("minutes"):
            content, meeting_id = _prepare_minutes(company, actor, content, issue_date)
            source_module, source_id = source_module or "meeting", source_id or meeting_id
        doc_title = title or (letterhead_template.default_title if letterhead_template else "")
        doc_title = _title(doc_type, resolved, doc_title)
        if letterhead_template is not None:
            snapshot["letterhead"] = {"id": str(letterhead_template.id), "code": letterhead_template.code,
                                      "name": letterhead_template.name}

        reference = None
        if numbered:
            reference = numbering.allocate_number(
                company, doc_type, actor=actor, on_date=issue_date, template=letterhead_template)

        logo = effective_logo(company, resolved.branding)
        watermark = effective_watermark(company, resolved.branding) if snapshot["branding"]["watermark_enabled"] else None
        pdf_bytes = pdf.render_pdf(
            snapshot, content, title=doc_title,
            reference_number=reference.reference_number if reference else "",
            issue_date=issue_date, logo_bytes=logo, signature_bytes=_signature_bytes(resolved),
            watermark_bytes=watermark,
        )
        # Same content and positions without the letterhead, for pre-printed official letterhead paper.
        print_bytes = pdf.render_pdf(
            snapshot, content, title=doc_title,
            reference_number=reference.reference_number if reference else "",
            issue_date=issue_date, signature_bytes=_signature_bytes(resolved), variant="plain",
        )

        document = IssuedDocument(
            company=company, document_type=doc_type, title=doc_title, reference=reference,
            reference_number=reference.reference_number if reference else "",
            issue_date=issue_date, source_module=source_module, source_id=str(source_id or ""),
            file_size=len(pdf_bytes), checksum_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            snapshot=snapshot, template_key=resolved.branding.default_template,
            template_version=pdf.TEMPLATE_VERSION, profile_version=company.version,
            branding_version=resolved.branding.version,
            letterhead_template=letterhead_template,
            policy=resolved.policy, policy_version=resolved.policy.version_number if resolved.policy else None,
            address=resolved.address, contact=resolved.contact, tax_registration=resolved.tax_registration,
            bank_account=resolved.bank_account, signatory=resolved.signatory,
            generated_by=actor if getattr(actor, "is_authenticated", False) else None,
            supersedes=supersedes,
        )
        document.file.save("document.pdf", ContentFile(pdf_bytes), save=False)
        document.print_file.save("print.pdf", ContentFile(print_bytes), save=False)
        document.print_checksum_sha256 = hashlib.sha256(print_bytes).hexdigest()
        if logo:
            document.logo_snapshot.save("logo", ContentFile(logo), save=False)
        if watermark:
            document.watermark_snapshot.save("watermark", ContentFile(watermark), save=False)
        document.save()

        if supersedes is not None:
            supersedes.status = IssuedDocument.Status.SUPERSEDED
            supersedes.save(update_fields=["status", "updated_at"])

        audit.record_event(
            company, actor, "issued_document", document.id, CompanyProfileAuditEvent.Action.ISSUED,
            new={"document_type": doc_type, "reference_number": document.reference_number,
                 "profile_version": company.version, "checksum": document.checksum_sha256},
        )
    return document


def preview_letterhead(company, branding, doc_type=DocType.BUSINESS_LETTER, variant="letterhead"):
    """Render a clearly-marked sample using an unsaved branding configuration."""
    today = datetime.date.today()
    resolved = company_service.resolve_for_document(doc_type, today, company)
    resolved.branding = branding
    snapshot = company_service.build_snapshot(resolved, doc_type)
    sample = {
        "recipient": ["Sample Recipient", "Sample Organisation"],
        "subject": "Letterhead preview",
        "paragraphs": [
            "This is a sample document used to preview the company letterhead. It does not consume a "
            "document number and is not stored.",
            "Body text, headings, margins, header and footer follow the branding settings shown on screen.",
        ],
        "table": {
            "columns": ["Item", "Description", "Amount (INR)"], "numeric_columns": [2],
            "rows": [["1", "Sample line item", "10,000.00"], ["2", "Another line item", "2,500.00"]],
            "column_widths_mm": [15, 100, 35],
        },
        "totals": [["Total", "12,500.00"]],
    }
    sample["paragraphs"].append(
        "A longer paragraph shows how line spacing and paragraph spacing look on the page. Lorem ipsum dolor sit "
        "amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
        "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.")
    return pdf.render_pdf(
        snapshot, sample, title="Sample Document", reference_number="SAMPLE/0001", issue_date=today,
        logo_bytes=effective_logo(company, branding), draft=True, variant=variant,
        watermark_bytes=effective_watermark(company, branding) if branding.watermark_enabled else None,
    )
