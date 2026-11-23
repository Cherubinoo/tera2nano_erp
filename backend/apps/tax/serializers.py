"""Plain dict views of the tax records."""
import datetime

from . import constants as C
from .models import TaxFiling, TaxPayment, TdsCredit
from .services import gst, tds


def filing_dict(f, today=None, detail=False):
    today = today or datetime.date.today()
    status = gst.filing_status(f, today)
    rt = C.RETURN_TYPES[f.return_type]
    d = {
        "id": str(f.id), "reference": f.reference, "return_type": f.return_type, "return_label": rt["label"], "group": rt["group"],
        "financial_year": f.financial_year, "period_label": f.period_label, "period_start": f.period_start, "period_end": f.period_end,
        "due_date": f.due_date, "status": status, "stored_status": f.status, "overdue": status == "late",
        "days_left": (f.due_date - today).days if f.status != "filed" else None,
        "arn": f.arn, "filed_date": f.filed_date, "filed_late": bool(f.filed_date and f.filed_date > f.due_date),
        "late_fee": f.late_fee, "interest": f.interest, "audit_applicable": f.audit_applicable, "has_setoff": bool(f.setoff),
        "figures": f.figures, "notes": f.notes, "prepared_at": f.prepared_at,
    }
    if detail:
        d.update({"long_label": rt["long"], "figure_keys": C.FIGURE_KEYS[f.return_type], "setoff": f.setoff, "setoff_date": f.setoff_date,
                  "filed_by": f.filed_by.get_username() if f.filed_by_id else ""})
    return d


def payment_dict(p):
    return {
        "id": str(p.id), "reference": p.reference, "tax_type": p.tax_type, "tax_label": C.PAYMENT_TYPES[p.tax_type], "date": p.date,
        "financial_year": p.financial_year, "period_label": p.period_label, "instalment": p.instalment, "section": p.section,
        "heads": p.heads, "total": p.total, "cin": p.cin, "bank_name": p.bank_name, "bank_account": str(p.bank_account_id) if p.bank_account_id else "",
        "notes": p.notes, "status": p.status, "void_reason": p.void_reason,
    }


def credit_dict(c):
    return {
        "id": str(c.id), "deductor_name": c.deductor_name, "deductor_tan": c.deductor_tan, "section": c.section, "financial_year": c.financial_year,
        "quarter": c.quarter, "date": c.date, "gross_amount": c.gross_amount, "tds_amount": c.tds_amount, "certificate_no": c.certificate_no,
        "certificate_date": c.certificate_date, "status": c.status, "status_label": c.get_status_display(),
        "customer_id": str(c.customer_id) if c.customer_id else "", "notes": c.notes,
    }
