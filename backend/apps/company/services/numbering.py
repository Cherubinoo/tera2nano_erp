"""Concurrency-safe reference number allocation.

The sequence row is locked with SELECT ... FOR UPDATE inside a transaction, so concurrent
requests are serialised. Database unique constraints on the allocated reference number are the
safety net: a number is never reused, even if an administrator reconfigures a prefix.
"""
import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max

from ..constants import DocType
from ..models import DocumentNumberSequence, FinancialYear, IssuedReferenceNumber

# Every reference carries the 2-digit year and the day-month of its date: T2N/INV/26/2710/00001,
# T2N-MOM-26-2710-0001. The date is in the number, so counters that restart (every financial year) never
# repeat a reference.
STANDARD_SEGMENTS = {"include_financial_year": False, "include_short_year": True, "date_format": "ddmm"}

DEFAULT_SEQUENCES = {
    # document_type: (code, restarts per financial year, reset, min length, separator)
    DocType.QUOTATION: ("QUO", True, "financial_year", 5, "/"),
    DocType.INVOICE: ("INV", True, "financial_year", 5, "/"),
    DocType.CREDIT_NOTE: ("CN", True, "financial_year", 5, "/"),
    DocType.PROFORMA: ("PI", True, "financial_year", 5, "/"),
    DocType.DEBIT_NOTE: ("DN", True, "financial_year", 5, "/"),
    DocType.CUSTOMER: ("CUS", False, "never", 4, "-"),
    DocType.JOURNAL: ("JV", True, "financial_year", 5, "/"),
    DocType.INCOME: ("INC", True, "financial_year", 5, "/"),
    DocType.VENDOR: ("VEN", False, "never", 4, "-"),
    DocType.PURCHASE_ORDER: ("PO", True, "financial_year", 5, "/"),
    DocType.VENDOR_BILL: ("BILL", True, "financial_year", 5, "/"),
    DocType.VENDOR_PAYMENT: ("VPY", True, "financial_year", 5, "/"),
    DocType.VENDOR_QUOTATION: ("VQ", True, "financial_year", 5, "/"),
    DocType.TAX_FILING: ("TF", True, "financial_year", 5, "/"),
    DocType.TAX_PAYMENT: ("TP", True, "financial_year", 5, "/"),
    DocType.COMPLIANCE_FILING: ("CF", True, "financial_year", 5, "/"),
    DocType.PAYSLIP: ("PSL", True, "financial_year", 5, "/"),
    DocType.PAYROLL_RUN: ("PRN", True, "financial_year", 5, "/"),
    DocType.LEAVE_REQUEST: ("LV", True, "financial_year", 5, "/"),
    DocType.CIRCULAR: ("CIR", True, "financial_year", 5, "/"),
    DocType.TASK: ("TSK", False, "never", 4, "-"),
    DocType.APPROVAL: ("APR", True, "financial_year", 5, "/"),
    DocType.PARTNER_TRANSACTION: ("PTX", True, "financial_year", 5, "/"),
    DocType.LEAD: ("LD", False, "never", 4, "-"),
    DocType.DEAL: ("DL", False, "never", 4, "-"),
    DocType.RECURRING_INVOICE: ("RIN", False, "never", 4, "-"),
    DocType.FIXED_ASSET: ("FAS", False, "never", 4, "-"),
    DocType.PROJECT: ("PRJ", False, "never", 4, "-"),
    DocType.EMPLOYEE: ("EMP", False, "never", 4, "-"),
    DocType.MEETING: ("MTG", False, "never", 4, "-"),
    DocType.MEETING_MINUTES: ("MOM", False, "never", 4, "-"),
    DocType.BANK_ACCOUNT: ("BNK", False, "never", 4, "-"),
    DocType.EXPENSE: ("EXP", True, "financial_year", 5, "/"),
    DocType.PAYMENT: ("PAY", True, "financial_year", 5, "/"),
    DocType.PURCHASE_REQUEST: ("PR", True, "financial_year", 5, "/"),
    DocType.BUSINESS_LETTER: ("LTR", True, "financial_year", 5, "/"),
    DocType.OFFER_LETTER: ("OFR", True, "financial_year", 5, "/"),
    DocType.APPOINTMENT_LETTER: ("APT", True, "financial_year", 5, "/"),
    DocType.EXPERIENCE_LETTER: ("EXL", True, "financial_year", 5, "/"),
    DocType.HANDOVER_LETTER: ("HOL", True, "financial_year", 5, "/"),
    DocType.COMPLETION_CERTIFICATE: ("PCC", True, "financial_year", 5, "/"),
}


def financial_year_for(company, on_date):
    return FinancialYear.objects.filter(company=company, start_date__lte=on_date, end_date__gte=on_date).first()


def seed_default_sequences(company, short_code):
    """Create any missing sequences using `short_code` (e.g. T2N). Existing rows are untouched."""
    short_code = (short_code or "").strip().upper()
    if not short_code:
        raise ValidationError({"short_code": "Enter a short company code to suggest numbering prefixes."})
    created = []
    for doc_type, (code, include_fy, reset, length, sep) in DEFAULT_SEQUENCES.items():
        seq, was_created = DocumentNumberSequence.objects.get_or_create(
            company=company,
            document_type=doc_type,
            letterhead_template=None,
            defaults=dict(
                prefix=f"{short_code}{sep}{code}", **STANDARD_SEGMENTS, separator=sep,
                min_length=length, reset_frequency=reset, next_value=1, is_active=True,
            ),
        )
        if was_created:
            created.append(seq)
    return created


def company_code(company):
    """Prefix code for generated numbers: the short company code, else initials of the display name."""
    code = (company.short_code or "").strip().upper()
    if code:
        return code
    skip = {"LLP", "LTD", "LIMITED", "PVT", "PRIVATE", "THE", "AND", "&"}
    words = [w for w in "".join(c if c.isalnum() else " " for c in company.display_name).split() if w.upper() not in skip]
    return ("".join(w[0] for w in words).upper() or "CO")[:6]


def ensure_default_sequence(company, document_type):
    """Create the standard sequence for `document_type` the first time it is needed.

    An administrator who switched a sequence off is respected (it is only created when absent).
    """
    if document_type not in DEFAULT_SEQUENCES:
        return None
    code, include_fy, reset, length, sep = DEFAULT_SEQUENCES[document_type]
    seq, created = DocumentNumberSequence.objects.get_or_create(
        company=company, document_type=document_type, letterhead_template=None,
        defaults=dict(prefix=f"{company_code(company)}{sep}{code}", **STANDARD_SEGMENTS, separator=sep,
                      min_length=length, reset_frequency=reset, next_value=1, is_active=True),
    )
    if created:
        from . import audit
        audit.record_event(company, None, "numbering", seq.pk, "created", category="numbering",
                           changed=["document_type", "prefix"],
                           new={"document_type": document_type, "prefix": seq.prefix},
                           reason="Created automatically the first time it was needed")
    return seq


def ensure_financial_year(company, on_date):
    """Return the financial year covering `on_date`, creating the April-March year when none exists yet."""
    fy = financial_year_for(company, on_date)
    if fy is not None:
        return fy
    start_year = on_date.year if on_date.month >= 4 else on_date.year - 1
    label = f"{start_year}-{(start_year + 1) % 100:02d}"
    try:
        with transaction.atomic():
            fy = FinancialYear.objects.create(
                company=company, label=label, start_date=datetime.date(start_year, 4, 1),
                end_date=datetime.date(start_year + 1, 3, 31),
                is_current=not FinancialYear.objects.filter(company=company, is_current=True).exists(),
            )
    except IntegrityError:
        raise ValidationError(
            f"No financial year covers {on_date:%d %b %Y}, and the standard April-March year {label} would overlap "
            "an existing one. Add the year under Financial Year Settings."
        )
    from . import audit
    audit.record_event(company, None, "financial_year", fy.pk, "created", changed=["label", "start_date", "end_date"],
                       new={"label": fy.label, "start_date": str(fy.start_date), "end_date": str(fy.end_date)},
                       reason="Created automatically the first time a number was needed in this year")
    return fy


def _period_for(seq, on_date, fy):
    freq = seq.reset_frequency
    if freq == DocumentNumberSequence.Reset.FINANCIAL_YEAR:
        return fy.label
    if freq == DocumentNumberSequence.Reset.YEARLY:
        return str(on_date.year)
    if freq == DocumentNumberSequence.Reset.MONTHLY:
        return f"{on_date.year}-{on_date.month:02d}"
    return ""


def allocate_number(company, document_type, *, actor=None, on_date=None, template=None):
    """Allocate and persist the next reference number. Returns an IssuedReferenceNumber."""
    on_date = on_date or datetime.date.today()
    try:
        with transaction.atomic():
            lookup = {"company": company, "letterhead_template": template}
            if template is None:
                lookup["document_type"] = document_type
                ensure_default_sequence(company, document_type)
            try:
                seq = DocumentNumberSequence.objects.select_for_update().get(**lookup)
            except DocumentNumberSequence.DoesNotExist:
                raise ValidationError(
                    f"No numbering sequence is configured for '{template.code if template else document_type}'."
                )
            if not seq.is_active:
                raise ValidationError(f"The numbering sequence for '{document_type}' is switched off.")

            fy = None
            if seq.include_financial_year or seq.reset_frequency == DocumentNumberSequence.Reset.FINANCIAL_YEAR:
                fy = ensure_financial_year(company, on_date)
                if fy.is_locked or fy.status == FinancialYear.Status.CLOSED:
                    raise ValidationError(f"Financial year {fy.label} is locked; numbers cannot be issued.")

            period = _period_for(seq, on_date, fy)
            resets = seq.reset_frequency != DocumentNumberSequence.Reset.NEVER
            if resets and seq.current_period and period < seq.current_period:
                # A document dated in an earlier period (say last year's letter issued in January) continues that
                # period's own numbers. The live counter of the current period is left alone.
                last = IssuedReferenceNumber.objects.filter(sequence=seq, period=period).aggregate(
                    m=Max("sequence_value"))["m"]
                value = (last or 0) + 1
            else:
                if seq.current_period != period:
                    if seq.current_period and resets:
                        seq.next_value = 1  # a new period starts again at 1
                    seq.current_period = period
                value = seq.next_value
                seq.next_value = value + 1
                seq.save(update_fields=["next_value", "current_period", "updated_at"])

            return IssuedReferenceNumber.objects.create(
                company=company,
                sequence=seq,
                document_type=document_type,
                reference_number=seq.preview(fy.label if fy else "", value, on_date),
                sequence_value=value,
                period=period,
                financial_year=fy,
                allocated_by=actor if getattr(actor, "is_authenticated", False) else None,
            )
    except IntegrityError as exc:
        raise ValidationError(
            "That reference number has already been issued. Adjust the prefix or next value of the "
            "numbering sequence."
        ) from exc


def preview_next(company, document_type, on_date=None, template=None):
    """The reference the next document of this type would get, without using or creating anything.

    Mirrors `allocate_number`; returns None when the sequence is switched off. The number is only a preview: someone else may
    take it first, and the real one is allocated when the document is issued.
    """
    on_date = on_date or datetime.date.today()
    lookup = {"company": company, "letterhead_template": template}
    if template is None:
        lookup["document_type"] = document_type
    seq = DocumentNumberSequence.objects.filter(**lookup).first()
    if seq is None:
        if template is not None or document_type not in DEFAULT_SEQUENCES:
            return None
        code, _fy, reset, length, sep = DEFAULT_SEQUENCES[document_type]
        seq = DocumentNumberSequence(
            company=company, document_type=document_type, prefix=f"{company_code(company)}{sep}{code}",
            **STANDARD_SEGMENTS, separator=sep, min_length=length, reset_frequency=reset, next_value=1)
    if not seq.is_active:
        return None
    fy = financial_year_for(company, on_date)
    if fy is None:
        start_year = on_date.year if on_date.month >= 4 else on_date.year - 1
        label = f"{start_year}-{(start_year + 1) % 100:02d}"
    else:
        label = fy.label

    class _Year:  # stands in for a financial year that may not exist yet
        pass
    year = _Year()
    year.label = label
    period = _period_for(seq, on_date, year)
    value = seq.next_value
    if seq.reset_frequency != DocumentNumberSequence.Reset.NEVER and seq.current_period and period != seq.current_period:
        if period < seq.current_period:
            last = IssuedReferenceNumber.objects.filter(sequence=seq, period=period).aggregate(m=Max("sequence_value"))["m"]
            value = (last or 0) + 1
        else:
            value = 1
    return seq.preview(label, value, on_date)


def void_number(reference, reason, actor=None):
    if not reason.strip():
        raise ValidationError({"reason": "A reason is required to void a reference number."})
    reference.status = IssuedReferenceNumber.Status.VOID
    reference.void_reason = reason
    reference.save(update_fields=["status", "void_reason", "updated_at"])
    return reference


def minimum_next_value(seq):
    """Lowest value `next_value` may take without risking reuse within the current period."""
    qs = IssuedReferenceNumber.objects.filter(sequence=seq, period=seq.current_period)
    last = qs.order_by("-sequence_value").values_list("sequence_value", flat=True).first()
    return (last or 0) + 1
