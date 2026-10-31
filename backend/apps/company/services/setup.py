"""Initial company setup wizard: idempotent start, resumable progress, optional steps."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import (
    CompanyAddress,
    CompanyBankAccount,
    CompanyBranding,
    CompanyContact,
    CompanyPolicy,
    CompanyProfile,
    CompanyProfileAuditEvent,
    CompanyTaxRegistration,
    CompanyTaxSettings,
    DocumentNumberSequence,
    FinancialYear,
)
from . import audit, letterheads

# step number -> (key, title, required)
STEPS = {
    1: ("identity", "Company name", True),
    2: ("legal", "Incorporation and registration", False),
    3: ("address", "Registered office address", False),
    4: ("contacts", "Company contacts", False),
    5: ("tax", "Tax registrations", False),
    6: ("bank", "Bank details", False),
    7: ("logo", "Company logo", False),
    8: ("letterhead", "Letterhead and footer", False),
    9: ("numbering", "Numbering sequences", False),
    10: ("financial_year", "Financial year", False),
    11: ("terms", "Default payment terms", False),
    12: ("review", "Review and save", True),
}


def get_state(company):
    if company is None:
        return {"exists": False, "status": "not_started", "current_step": 1, "steps": _steps(None)}
    return {
        "exists": True,
        "company_id": str(company.id),
        "status": company.setup_status,
        "current_step": company.setup_step,
        "skipped_steps": company.setup_skipped_steps,
        "completed_at": company.setup_completed_at,
        "steps": _steps(company),
    }


def _steps(company):
    """Per-step completion derived from stored data, so progress survives a browser restart."""
    done = {}
    if company is not None:
        done = {
            1: bool(company.legal_name and company.display_name),
            2: bool(company.pan or company.llpin or company.date_of_incorporation or company.legal_entity_type),
            3: CompanyAddress.objects.filter(company=company, is_active=True).exists(),
            4: CompanyContact.objects.filter(company=company, is_active=True).exists(),
            5: CompanyTaxRegistration.objects.filter(company=company).exists()
            or company.gst_registration_status != CompanyProfile.GstStatus.NOT_DECLARED,
            6: CompanyBankAccount.objects.filter(company=company).exists(),
            7: bool(company.logo),
            8: CompanyBranding.objects.filter(company=company).exclude(footer_text_override="").exists(),
            9: DocumentNumberSequence.objects.filter(company=company).exists(),
            10: FinancialYear.objects.filter(company=company).exists(),
            11: CompanyPolicy.objects.filter(company=company).exists(),
            12: company.setup_status == CompanyProfile.SetupStatus.COMPLETED,
        }
    skipped = set(company.setup_skipped_steps) if company else set()
    return [
        {"step": n, "key": key, "title": title, "required": required,
         "complete": done.get(n, False), "skipped": n in skipped}
        for n, (key, title, required) in STEPS.items()
    ]


def start_setup(legal_name, display_name, actor):
    """Create the company profile, or return the existing one (never a duplicate)."""
    existing = CompanyProfile.objects.first()
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            company = CompanyProfile(
                legal_name=legal_name.strip(), display_name=display_name.strip(),
                created_by=actor if getattr(actor, "is_authenticated", False) else None, setup_step=2,
            )
            company.full_clean()
            company.save()
            CompanyBranding.objects.create(company=company)
            CompanyTaxSettings.objects.create(company=company)
            letterheads.seed_default_templates(company)
            audit.record_event(
                company, actor, "profile", company.id, CompanyProfileAuditEvent.Action.CREATED,
                new={"legal_name": company.legal_name, "display_name": company.display_name},
            )
        return company, True
    except IntegrityError:
        # Lost a race with a concurrent start: the singleton constraint guarantees one row.
        return CompanyProfile.objects.get(), False


def save_progress(company, step, skipped):
    if step not in STEPS:
        raise ValidationError({"step": "Unknown setup step."})
    clean_skipped = sorted({s for s in skipped if s in STEPS and not STEPS[s][2]})
    company.setup_step = step
    company.setup_skipped_steps = clean_skipped
    company.save(update_fields=["setup_step", "setup_skipped_steps", "updated_at"])
    return company


def readiness_warnings(company):
    """Optional items still missing. These never block completion."""
    warnings = []
    checks = [
        (CompanyAddress.objects.filter(company=company, is_active=True).exists(), "No registered office address yet."),
        (CompanyContact.objects.filter(company=company, is_active=True).exists(), "No company contacts yet."),
        (bool(company.logo), "No company logo uploaded."),
        (FinancialYear.objects.filter(company=company, is_current=True).exists(), "No current financial year set."),
        (DocumentNumberSequence.objects.filter(company=company, is_active=True).exists(),
         "No document numbering sequences configured; quotations and invoices cannot be numbered yet."),
        (CompanyPolicy.objects.filter(company=company, status="active").exists(), "No default business terms activated."),
    ]
    for ok, message in checks:
        if not ok and message:
            warnings.append(message)
    return warnings


@transaction.atomic
def complete_setup(company, actor):
    company = CompanyProfile.objects.select_for_update().get(pk=company.pk)
    if not (company.legal_name and company.display_name):
        raise ValidationError("Legal name and display name are required to finish setup.")
    before = audit.snapshot(company)
    company.setup_status = CompanyProfile.SetupStatus.COMPLETED
    company.setup_step = 12
    company.setup_completed_at = timezone.now()
    if company.status == CompanyProfile.Status.DRAFT:
        company.status = CompanyProfile.Status.ACTIVE
    company.save()
    changed, prev, new = audit.diff("profile", before, audit.snapshot(company))
    audit.record_event(
        company, actor, "profile", company.id, CompanyProfileAuditEvent.Action.UPDATED,
        changed=changed, previous=prev, new=new, reason="Initial company setup completed", category="profile",
    )
    return company, readiness_warnings(company)
