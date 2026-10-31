"""Catalogue of letter templates (LHT-001 ...) and operations (OPS-001 ...), with their reference sequences."""
from django.db import IntegrityError, transaction

from ..constants import DocType
from ..models import DocumentNumberSequence, LetterheadTemplate

LETTER, OPERATION = LetterheadTemplate.Kind.LETTER, LetterheadTemplate.Kind.OPERATION

# (code, name, default heading printed on the document)
DEFAULT_TEMPLATES = [
    ("LHT-001", "General Company Letterhead", ""),
    ("LHT-002", "Client & Business Correspondence", ""),
    ("LHT-003", "Employee & HR Letters", ""),
    ("LHT-004", "Bank & Government Correspondence", ""),
    ("LHT-005", "Legal & Compliance Letters", ""),
    ("LHT-006", "Internal Office Orders", "Office Order"),
    ("LHT-007", "Vendor & Partnership Letters", ""),
    ("LHT-008", "Authorization Letters", "Authorization Letter"),
]

# A starting list of operations; add, rename or switch off any of them under Operations.
DEFAULT_OPERATIONS = [
    ("OPS-001", "Work Order", "Work Order"),
    ("OPS-002", "Job Card / Task Assignment", "Job Card"),
    ("OPS-003", "Site Visit / Inspection Report", "Site Visit Report"),
    ("OPS-004", "Material Requisition", "Material Requisition"),
    ("OPS-005", "Delivery & Dispatch Note", "Delivery Note"),
    ("OPS-006", "Service / Maintenance Report", "Service Report"),
    ("OPS-007", "Standard Operating Procedure (SOP)", "Standard Operating Procedure"),
    ("OPS-008", "Incident / Non-conformance Report", "Incident Report"),
    ("OPS-009", "Project Status Report", "Project Status Report"),
    ("OPS-010", "Daily / Weekly Operations Report", "Operations Report"),
]

DEFAULTS = {LETTER: DEFAULT_TEMPLATES, OPERATION: DEFAULT_OPERATIONS}


def ensure_template_sequence(template):
    """Reference sequence for an entry: LHT-002/26/2710/00001 = code / 2-digit year / DDMM / running number.

    Each entry has its own counter, which restarts at 00001 every calendar year. The year is part of the
    number, so a restarted counter never repeats an earlier reference.
    """
    existing = DocumentNumberSequence.objects.filter(company=template.company, letterhead_template=template).first()
    if existing:
        return existing
    doc_type = DocType.OPERATION if template.kind == OPERATION else DocType.BUSINESS_LETTER
    try:
        with transaction.atomic():
            return DocumentNumberSequence.objects.create(
                company=template.company, letterhead_template=template, document_type=doc_type,
                prefix=template.code, separator="/", include_short_year=True, date_format="ddmm", min_length=5,
                reset_frequency="yearly", next_value=1, is_active=True,
            )
    except IntegrityError:  # created concurrently
        return DocumentNumberSequence.objects.get(company=template.company, letterhead_template=template)


def seed_default_templates(company, kind=LETTER):
    """Create any missing default entries of this kind (and their sequences). Existing ones are left as they are."""
    created = []
    for order, (code, name, title) in enumerate(DEFAULTS[kind], start=1):
        template, was_created = LetterheadTemplate.objects.get_or_create(
            company=company, code=code, defaults=dict(kind=kind, name=name, default_title=title, sort_order=order * 10))
        ensure_template_sequence(template)
        if was_created:
            created.append(template)
    return created
