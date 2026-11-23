"""Return filing records: GST returns, TDS returns, ITR and tax audit. Created per period, prepared from the books, filed."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounting.services import reports
from apps.company.constants import DocType
from apps.company.services import numbering

from .. import constants as C
from ..models import TaxFiling, TaxPayment
from . import books, gst, incometax, periods, registers, tds
from .common import Action, ZERO, amount, audit_event, money, parse_date, signed_amount

ARN_MAX = 60


def snapshot(f):
    return {"reference": f.reference, "return_type": f.return_type, "period": f.period_label, "due_date": f.due_date, "status": f.status,
            "arn": f.arn, "filed_date": f.filed_date, "late_fee": f.late_fee, "interest": f.interest, "figures": f.figures, "notes": f.notes}


def gst_registered(company):
    return getattr(company, "gst_registration_status", "") in ("registered", "applied") or bool(getattr(company, "gstin", ""))


def expected(fy, types=None, *, audit_applicable=False, include_gst=True):
    """[{return_type, period_start, period_end, label, due_date}] the rules say are due for a financial year."""
    periods.parse_fy(fy)
    out = []
    for rt in (types or list(C.RETURN_TYPES)):
        if rt not in C.RETURN_TYPES:
            raise ValidationError({"types": f"Unknown return type {rt}."})
        if C.RETURN_TYPES[rt]["group"] == "gst" and not include_gst:
            continue
        for s, e, label in periods.periods_for(rt, fy):
            out.append({"return_type": rt, "financial_year": fy, "period_start": s, "period_end": e, "label": label,
                        "due_date": periods.due_date(rt, fy, s, audit_applicable), "audit_applicable": audit_applicable and rt == "itr5"})
    return out


def _new(company, actor, spec, notes=""):
    ref = numbering.allocate_number(company, DocType.TAX_FILING, actor=actor, on_date=spec["period_end"]).reference_number
    return TaxFiling.objects.create(
        company=company, reference=ref, return_type=spec["return_type"], financial_year=spec["financial_year"], period_label=spec["label"],
        period_start=spec["period_start"], period_end=spec["period_end"], due_date=spec["due_date"],
        audit_applicable=spec.get("audit_applicable", False), notes=notes, created_by=actor)


@transaction.atomic
def generate(company, actor, fy, types=None, audit_applicable=False):
    """Create the filing records a financial year needs and that do not exist yet. Returns (created, skipped)."""
    periods.parse_fy(fy)
    existing = {(f.return_type, f.period_start, f.period_end) for f in TaxFiling.objects.filter(company=company, financial_year=fy)}
    created = []
    for spec in expected(fy, types, audit_applicable=audit_applicable, include_gst=gst_registered(company)):
        if (spec["return_type"], spec["period_start"], spec["period_end"]) in existing:
            continue
        f = _new(company, actor, spec)
        audit_event(company, actor, "tax_filing", f.pk, Action.CREATED, new=snapshot(f))
        created.append(f)
    return created, len(existing)


@transaction.atomic
def create(company, actor, data):
    rt = data.get("return_type")
    if rt not in C.RETURN_TYPES:
        raise ValidationError({"return_type": "Choose the return."})
    fy = data.get("financial_year") or ""
    if not fy and data.get("period"):
        fy = periods.fy_label_for(periods.parse_month(data["period"])[0])
    if not fy:
        raise ValidationError({"financial_year": "Choose the financial year."})
    periods.parse_fy(fy)
    kind = C.DUE_RULES[rt]["kind"]
    audit_applicable = bool(data.get("audit_applicable")) and rt == "itr5"
    if kind == "monthly":
        if not data.get("period"):
            raise ValidationError({"period": "Choose the month."})
        start, end = periods.parse_month(data["period"])
        if periods.fy_label_for(start) != fy:
            raise ValidationError({"period": f"That month is not in financial year {fy}."})
        label = periods.month_label(start)
    elif kind == "quarterly":
        q = str(data.get("quarter") or "")
        if q not in ("1", "2", "3", "4"):
            raise ValidationError({"quarter": "Choose the quarter."})
        _, start, end, label = periods.fy_quarters(fy)[int(q) - 1]
    else:
        _, start, end = periods.parse_fy(fy)
        label = f"FY {fy}"
    due = parse_date(data.get("due_date"), "due_date") or periods.due_date(rt, fy, start, audit_applicable)
    spec = {"return_type": rt, "financial_year": fy, "period_start": start, "period_end": end, "label": label, "due_date": due,
            "audit_applicable": audit_applicable}
    if TaxFiling.objects.filter(company=company, return_type=rt, period_start=start, period_end=end).exists():
        raise ValidationError({"period": f"There is already a {C.RETURN_TYPES[rt]['label']} record for {label}."})
    f = _new(company, actor, spec, str(data.get("notes") or "").strip()[:500])
    audit_event(company, actor, "tax_filing", f.pk, Action.CREATED, new=snapshot(f))
    return f


def clean_figures(return_type, figures):
    if not isinstance(figures, dict):
        raise ValidationError({"figures": "Send the figures as an object."})
    allowed = C.FIGURE_KEYS[return_type]
    out = {}
    for k, v in figures.items():
        if k not in allowed:
            raise ValidationError({"figures": f"{k} is not a figure of this return."})
        out[k] = str(signed_amount(v, "figures"))
    return out


@transaction.atomic
def update(company, actor, filing, data):
    f = TaxFiling.objects.select_for_update(of=("self",)).get(pk=filing.pk)
    before = snapshot(f)
    filed = f.status == TaxFiling.Status.FILED
    locked_fields = {"figures", "due_date", "audit_applicable"}
    if filed and locked_fields & set(data):
        raise ValidationError({"detail": "This return is filed. Reopen it (with a reason) to change its figures or due date."})
    if "arn" in data:
        arn = str(data["arn"] or "").strip()
        if len(arn) > ARN_MAX or any(c in arn for c in "<>"):
            raise ValidationError({"arn": f"Use at most {ARN_MAX} characters."})
        f.arn = arn
    if "filed_date" in data and filed:
        fd = parse_date(data["filed_date"], "filed_date", required=True)
        _check_filed_date(f, fd)
        f.filed_date = fd
    if "late_fee" in data:
        f.late_fee = amount(data["late_fee"], "late_fee")
    if "interest" in data:
        f.interest = amount(data["interest"], "interest")
    if "notes" in data:
        notes = str(data["notes"] or "").strip()
        if len(notes) > 500:
            raise ValidationError({"notes": "Use at most 500 characters."})
        f.notes = notes
    if "due_date" in data:
        f.due_date = parse_date(data["due_date"], "due_date", required=True)
    if "audit_applicable" in data and f.return_type == "itr5":
        f.audit_applicable = bool(data["audit_applicable"])
        if "due_date" not in data:
            f.due_date = periods.due_date("itr5", f.financial_year, f.period_start, f.audit_applicable)
    if "figures" in data:
        f.figures = {**f.figures, **clean_figures(f.return_type, data["figures"])}
        if f.status == TaxFiling.Status.PENDING and f.figures:
            f.status = TaxFiling.Status.PREPARED
    f.save()
    audit_event(company, actor, "tax_filing", f.pk, Action.UPDATED, previous=before, new=snapshot(f))
    return f


def _check_filed_date(f, d):
    if d > datetime.date.today():
        raise ValidationError({"filed_date": "The filing date cannot be in the future."})
    if d < f.period_start:
        raise ValidationError({"filed_date": "The return cannot be filed before its period starts."})


def figures_for(company, filing):
    """The figures a return of this kind holds, worked out from the registers and the books for its period."""
    rt, s, e = filing.return_type, filing.period_start, filing.period_end
    S = lambda v: str(money(v))  # noqa: E731
    if rt == "gstr1":
        sales = registers.sales_register(company, s, e)
        st = sales["section_totals"]
        cdn = sum((r["taxable"] * (-1 if r["kind"] == "credit_note" else 1) for r in sales["cdnr"] + sales["cdnur"]), ZERO)
        t = sales["totals"]
        return {"outward_taxable": S(t["taxable"]), "b2b_taxable": S(st["b2b"]["taxable"]),
                "b2c_taxable": S(st["b2cl"]["taxable"] + st["b2cs"]["taxable"]), "credit_debit_notes_taxable": S(cdn),
                "igst": S(t["igst"]), "cgst": S(t["cgst"]), "sgst": S(t["sgst"]), "cess": S(t["cess"]), "invoice_value": S(t["invoice_value"])}
    if rt in ("gstr3b", "gstr9"):
        sales = registers.sales_register(company, s, e)
        purch = registers.purchase_register(company, s, e)
        t = sales["totals"]
        good = {h: sum((r[h] for r in purch["rows"] if r["eligibility"] != "ineligible"), ZERO) for h in books.HEADS}
        bad = sum((r["itc"] for r in purch["rows"] if r["eligibility"] == "ineligible"), ZERO)
        out = {"outward_taxable": S(t["taxable"]), "igst": S(t["igst"]), "cgst": S(t["cgst"]), "sgst": S(t["sgst"]), "cess": "0.00",
               "itc_igst": S(good["igst"]), "itc_cgst": S(good["cgst"]), "itc_sgst": S(good["sgst"]), "itc_cess": "0.00"}
        if rt == "gstr9":
            paid = ZERO
            for p in TaxPayment.objects.filter(company=company, tax_type="gst", status="recorded", financial_year=filing.financial_year):
                paid += sum((Decimal(v) for k, v in p.heads.items() if k in ("cgst", "sgst", "igst", "cess")), ZERO)
            out["tax_paid"] = S(paid)
            return out
        outputs = {h: t[h] for h in books.HEADS}
        plan = gst.compute_setoff(outputs, good)
        out.update({"itc_reversed": S(bad), "net_igst": S(t["igst"] - good["igst"]), "net_cgst": S(t["cgst"] - good["cgst"]),
                    "net_sgst": S(t["sgst"] - good["sgst"]), "net_cess": "0.00",
                    "net_payable": S(sum(outputs.values(), ZERO) - sum(good.values(), ZERO)), "cash_payable": S(sum(plan["cash"].values(), ZERO))})
        return out
    if rt in ("tds_24q", "tds_26q"):
        return {k: S(v) for k, v in tds.quarter_figures(company, rt, s, e).items()}
    if rt == "itr5":
        c = incometax.compute(company, filing.financial_year)
        return {"book_profit": S(c["profit_used"]), "taxable_income": S(c["taxable_income"]), "total_tax": S(c["total_tax"]),
                "tds_credit": S(c["tds_credit"]), "advance_tax_paid": S(c["advance_tax_paid"] + c["self_assessment_paid"]),
                "net_payable": S(c["net_payable"])}
    pl = books.safe(lambda: reports.profit_and_loss(company, s, e), None, "profit and loss")
    return {"turnover": S(pl["total_income"] if pl else 0), "book_profit": S(pl["net_profit"] if pl else 0)}


@transaction.atomic
def prepare(company, actor, filing):
    f = TaxFiling.objects.select_for_update(of=("self",)).get(pk=filing.pk)
    if f.status == TaxFiling.Status.FILED:
        raise ValidationError({"detail": "This return is already filed. Reopen it to prepare it again."})
    before = snapshot(f)
    f.figures = figures_for(company, f)
    f.status, f.prepared_at = TaxFiling.Status.PREPARED, timezone.now()
    f.save(update_fields=["figures", "status", "prepared_at", "updated_at"])
    audit_event(company, actor, "tax_filing", f.pk, Action.UPDATED, previous=before, new=snapshot(f), reason="Prepared from the books")
    return f


@transaction.atomic
def mark_filed(company, actor, filing, data):
    f = TaxFiling.objects.select_for_update(of=("self",)).get(pk=filing.pk)
    if f.status == TaxFiling.Status.FILED:
        raise ValidationError({"detail": "This return is already marked as filed."})
    before = snapshot(f)
    fd = parse_date(data.get("filed_date"), "filed_date", required=True)
    _check_filed_date(f, fd)
    arn = str(data.get("arn") or "").strip()
    if len(arn) > ARN_MAX:
        raise ValidationError({"arn": f"Use at most {ARN_MAX} characters."})
    f.arn, f.filed_date, f.filed_by, f.status = arn, fd, actor, TaxFiling.Status.FILED
    if "late_fee" in data:
        f.late_fee = amount(data["late_fee"], "late_fee")
    if "interest" in data:
        f.interest = amount(data["interest"], "interest")
    if not f.figures:
        f.figures = figures_for(company, f)
    f.save()
    audit_event(company, actor, "tax_filing", f.pk, Action.UPDATED, previous=before, new=snapshot(f), reason="Marked as filed")
    return f


@transaction.atomic
def reopen(company, actor, filing, reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why the filed return is being reopened."})
    f = TaxFiling.objects.select_for_update(of=("self",)).get(pk=filing.pk)
    if f.status != TaxFiling.Status.FILED:
        raise ValidationError({"detail": "Only a filed return can be reopened."})
    before = snapshot(f)
    f.status = TaxFiling.Status.PREPARED if f.figures else TaxFiling.Status.PENDING
    f.filed_date = None
    f.save(update_fields=["status", "filed_date", "updated_at"])
    audit_event(company, actor, "tax_filing", f.pk, Action.UPDATED, previous=before, new=snapshot(f), reason=reason)
    return f


@transaction.atomic
def delete(company, actor, filing, reason=""):
    f = TaxFiling.objects.select_for_update(of=("self",)).get(pk=filing.pk)
    if f.status == TaxFiling.Status.FILED:
        raise ValidationError({"detail": "A filed return cannot be deleted. Reopen it first."})
    if f.setoff:
        raise ValidationError({"detail": "The input credit set-off for this return is still in the books. Undo it first."})
    snap = snapshot(f)
    pk = f.pk
    f.delete()
    audit_event(company, actor, "tax_filing", pk, Action.DELETED, previous=snap, reason=reason)
