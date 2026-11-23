"""Payroll runs: generate payslips for a month, review, lock, approve, post to the books, pay, void.

Status flow:  draft -> locked -> (pending_approval ->) approved -> posted -> paid;  any state -> void.
Only a draft run can be edited or regenerated. Posting writes one journal entry (source `payroll`, id = the run) and
paying writes a second one (id = `<run>-paid`); voiding reverses whichever were made, through `ledger.reverse_source`.

Journal on posting (debits = credits):
    Dr Salaries and wages (gross) / Employer PF contribution / Employer ESI contribution
    Cr Salaries payable (net pay) / PF payable / ESI payable / Professional tax payable / TDS payable /
       Employee advances (loan recovery) / Other payroll deductions payable
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounting.models import Account, JournalEntry
from apps.accounting.services import chart, ledger
from apps.company.constants import DocType
from apps.company.models import CompanyBankAccount
from apps.company.services import documents, numbering, quotations

from ..models import Attendance, Employee, EmployeeAdvance, Payslip, PayrollRun, SalaryStructure
from . import calc, common, leave
from .common import ZERO, Action, get_settings, money

Status = PayrollRun.Status
LOCKED_FOR_EDIT = (Status.LOCKED, Status.PENDING, Status.APPROVED, Status.POSTED, Status.PAID)
FINAL = (Status.APPROVED, Status.POSTED, Status.PAID)

# (code, name, subtype) of the ordinary accounts payroll posts to; created on first use.
ACCOUNTS = {
    "employer_pf": ("5110", "Employer PF contribution", Account.Subtype.EXPENSE),
    "employer_esi": ("5120", "Employer ESI contribution", Account.Subtype.EXPENSE),
    "pf_payable": ("2310", "PF payable", Account.Subtype.OTHER_CURRENT_LIABILITY),
    "esi_payable": ("2320", "ESI payable", Account.Subtype.OTHER_CURRENT_LIABILITY),
    "pt_payable": ("2330", "Professional tax payable", Account.Subtype.OTHER_CURRENT_LIABILITY),
    "other_deductions_payable": ("2340", "Other payroll deductions payable", Account.Subtype.OTHER_CURRENT_LIABILITY),
    "employee_advances": ("1550", "Employee advances", Account.Subtype.OTHER_CURRENT_ASSET),
}


def payroll_account(company, key):
    code, name, subtype = ACCOUNTS[key]
    acct = Account.objects.filter(company=company, name=name).first()
    if acct is not None:
        return acct
    chart.ensure_chart(company)
    used = set(Account.objects.filter(company=company).values_list("code", flat=True))
    while code in used:
        code = str(int(code) + 1)
    return Account.objects.create(company=company, code=code, name=name, type=Account.TYPE_OF[subtype], subtype=subtype)


# --------------------------------------------------------------------------- inputs to a payslip

def structure_for(employee, on_date):
    return SalaryStructure.objects.filter(employee=employee, effective_from__lte=on_date).order_by("-effective_from").first()


def basis_days(company, month, settings):
    last = common.month_end(month)
    total = last.day
    if settings.lop_basis == settings.LopBasis.WORKING:
        return len(leave.counted_dates(company, month, last, settings))
    return total


def lop_days(employee, month, settings, basis):
    """Days of pay lost in the month: unpaid leave, absences and half days, and days outside the employment period."""
    last = common.month_end(month)
    off = set(settings.weekly_off_days or [])
    holidays = leave.holiday_dates(employee.company, month, last)
    working = settings.lop_basis == settings.LopBasis.WORKING

    def counts(d):
        return not working or (d.weekday() not in off and d not in holidays)

    lost = {}
    for d in common.daterange(month, last):  # before joining / after leaving
        if counts(d) and (d < employee.date_of_joining or (employee.exit_date and d > employee.exit_date)):
            lost[d] = Decimal(1)
    unpaid = leave.unpaid_leave_dates(employee, month, last, settings)
    for d, w in unpaid.items():
        lost[d] = max(lost.get(d, Decimal(0)), w)
    rows = Attendance.objects.filter(employee=employee, date__range=(month, last), status__in=("absent", "half_day")).exclude(source="leave")
    for a in rows:
        if a.date not in lost and a.date not in unpaid and counts(a.date):
            lost[a.date] = Decimal(1) if a.status == "absent" else Decimal("0.5")
    return min(sum(lost.values(), Decimal(0)), Decimal(basis))


def _full(structure):
    return {"basic": str(structure.basic), "hra": str(structure.hra), "special_allowance": str(structure.special_allowance),
            "other_allowance": str(structure.other_allowance), "pf_enabled": structure.pf_enabled,
            "pf_on_full_basic": structure.pf_on_full_basic, "esi_enabled": structure.esi_enabled, "pt_enabled": structure.pt_enabled,
            "fixed_deductions": str(structure.fixed_deductions), "tds_monthly": str(structure.tds_monthly)}


def _decimal_full(full):
    return {**full, **{k: Decimal(full[k]) for k in calc.COMPONENTS}}


def advance_due(employee):
    return money(sum((min(a.installment, a.outstanding) for a in employee.advances.filter(status="open")), Decimal(0)))


def advance_outstanding(employee):
    return money(sum((a.outstanding for a in employee.advances.filter(status="open")), Decimal(0)))


def _apply(slip, settings):
    """Work out every amount from the slip's inputs and structure."""
    r = calc.compute(_decimal_full(slip.full), basis_days=slip.basis_days, lop_days=slip.lop_days, settings=settings, bonus=slip.bonus,
                     tds=slip.tds, loan_recovery=slip.loan_recovery, other_deductions=slip.other_deductions)
    for k, v in r.items():
        setattr(slip, k, v)
    return slip


def _who(e):
    return {"name": e.name, "code": e.code, "department": e.department.name if e.department else "",
            "designation": e.designation.name if e.designation else "", "date_of_joining": e.date_of_joining.isoformat(),
            "pan": e.masked("pan"), "bank_account": e.masked("bank_account"), "bank_name": e.bank_name, "uan": e.uan,
            "esi_number": e.esi_number}


def build_payslip(run, employee, settings, structure):
    slip = Payslip(run=run, employee=employee, month=run.month, structure=structure, who=_who(employee), full=_full(structure))
    slip.basis_days = basis_days(run.company, run.month, settings)
    slip.lop_days_auto = slip.lop_days = lop_days(employee, run.month, settings, slip.basis_days)
    slip.tds, slip.other_deductions = structure.tds_monthly, structure.fixed_deductions
    slip.loan_recovery = advance_due(employee)
    _apply(slip, settings)
    if slip.net_pay < 0:  # keep the take-home positive: recover less of the advance
        slip.loan_recovery = max(ZERO, slip.loan_recovery + slip.net_pay)
        _apply(slip, settings)
    return slip


def eligible(company, month):
    last = common.month_end(month)
    return Employee.objects.filter(company=company, date_of_joining__lte=last).filter(
        Q(exit_date__isnull=True) | Q(exit_date__gte=month)).select_related("department", "designation").order_by("first_name", "last_name")


def _generate(run, settings):
    slips, skipped = [], []
    for e in eligible(run.company, run.month):
        s = structure_for(e, common.month_end(run.month))
        if s is None:
            skipped.append({"employee": e.name, "code": e.code, "reason": "No salary structure is effective for this month."})
            continue
        slips.append(build_payslip(run, e, settings, s))
    return slips, skipped


def retotal(run):
    t = run.payslips.aggregate(g=Sum("gross"), d=Sum("total_deductions"), net=Sum("net_pay"), c=Sum("employer_cost"))
    run.employee_count = run.payslips.count()
    run.total_gross, run.total_deductions = t["g"] or ZERO, t["d"] or ZERO
    run.total_net, run.total_employer_cost = t["net"] or ZERO, t["c"] or ZERO
    run.save(update_fields=["employee_count", "total_gross", "total_deductions", "total_net", "total_employer_cost", "updated_at"])


def _snap(run):
    return {"number": run.number, "month": run.month.isoformat(), "status": run.status, "employees": run.employee_count,
            "net": str(run.total_net)}


# --------------------------------------------------------------------------- run lifecycle

@transaction.atomic
def create_run(company, month, actor, notes=""):
    month = common.month_start(month)
    if month > common.month_start(datetime.date.today()):
        raise ValidationError({"month": "Payroll cannot be run for a month that has not started."})
    live = PayrollRun.objects.filter(company=company, month=month).exclude(status=Status.VOID).first()
    if live:
        raise ValidationError({"month": f"There is already a payroll run for this month ({live.number or 'draft'}). Void it to start again."})
    settings = get_settings(company)
    run = PayrollRun(company=company, month=month, notes=(notes or "")[:300], created_by=actor)
    slips, skipped = _generate(run, settings)
    if not slips:
        raise ValidationError({"month": "No employee has a salary structure effective for this month, so there is nothing to pay."})
    run.number = numbering.allocate_number(company, DocType.PAYROLL_RUN, actor=actor, on_date=common.month_end(month)).reference_number
    run.save()
    Payslip.objects.bulk_create(slips)
    retotal(run)
    common.log(company, actor, "payroll_run", run.pk, Action.CREATED, new=_snap(run))
    run.skipped = skipped
    return run


def _lock_row(run):
    return PayrollRun.objects.select_for_update(of=("self",)).select_related("company").get(pk=run.pk)


@transaction.atomic
def regenerate(run, actor):
    run = _lock_row(run)
    if run.status != Status.DRAFT:
        raise ValidationError("Only a draft run can be regenerated.")
    run.payslips.all().delete()
    slips, skipped = _generate(run, get_settings(run.company))
    if not slips:
        raise ValidationError("No employee has a salary structure effective for this month.")
    Payslip.objects.bulk_create(slips)
    retotal(run)
    common.log(run.company, actor, "payroll_run", run.pk, Action.UPDATED, new={"regenerated": True, "employees": run.employee_count})
    run.skipped = skipped
    return run


def _move(run, actor, allowed, to, *, reason=""):
    run = _lock_row(run)
    if run.status not in allowed:
        raise ValidationError(f"A run that is {run.get_status_display().lower()} cannot do this.")
    before = run.status
    run.status = to
    return run, before


@transaction.atomic
def lock(run, actor):
    run, before = _move(run, actor, (Status.DRAFT,), Status.LOCKED)
    bad = run.payslips.filter(net_pay__lt=0)
    if bad.exists():
        raise ValidationError(f"{bad.first().who.get('name')}'s deductions are more than their pay. Fix it before locking.")
    run.locked_at = timezone.now()
    run.save()
    common.log(run.company, actor, "payroll_run", run.pk, Action.UPDATED, previous={"status": before}, new={"status": run.status})
    return run


@transaction.atomic
def unlock(run, actor):
    run, before = _move(run, actor, (Status.LOCKED, Status.PENDING), Status.DRAFT)
    run.locked_at = None
    run.save()
    common.log(run.company, actor, "payroll_run", run.pk, Action.UPDATED, previous={"status": before}, new={"status": run.status})
    _close_approval(run, actor)
    return run


@transaction.atomic
def submit(run, actor):
    """Send for approval. The approvals module listens for this status; without approval rules anyone with hr.payroll may approve."""
    run, before = _move(run, actor, (Status.LOCKED,), Status.PENDING)
    run.save()
    common.log(run.company, actor, "payroll_run", run.pk, Action.UPDATED, previous={"status": before}, new={"status": run.status})
    from .. import approval_hooks
    approval_hooks.submit_run(run, actor)  # may approve straight away; the callback runs in this transaction
    run.refresh_from_db()
    return run


def _close_approval(run, actor):
    from .. import approval_hooks
    approval_hooks.close_request("payroll_run", run.pk, actor)


def _decided_in_approvals(trusted):
    from .. import approval_hooks
    if not trusted and approval_hooks.managed():
        raise ValidationError("Payroll runs are decided in the Approvals module. Send the run for approval.")


@transaction.atomic
def withdraw(run, actor):
    """The approval request was withdrawn in the Approvals module: the run goes back to locked."""
    run = _lock_row(run)
    if run.status == Status.PENDING:
        run.status = Status.LOCKED
        run.save()
        common.log(run.company, actor, "payroll_run", run.pk, Action.UPDATED, previous={"status": "pending_approval"},
                   new={"status": "locked"}, reason="Approval request withdrawn")
    return run


@transaction.atomic
def approve(run, actor, *, trusted=False):
    _decided_in_approvals(trusted)
    run, before = _move(run, actor, (Status.PENDING,) if trusted else (Status.LOCKED, Status.PENDING), Status.APPROVED)
    run.approved_by, run.approved_at = actor, timezone.now()
    run.save()
    common.log(run.company, actor, "payroll_run", run.pk, Action.UPDATED, previous={"status": before}, new={"status": run.status})
    return run


@transaction.atomic
def reject(run, actor, reason, *, trusted=False):
    _decided_in_approvals(trusted)
    if not (reason or "").strip():
        raise ValidationError({"reason": "Give a reason for sending this run back."})
    run, before = _move(run, actor, (Status.PENDING,), Status.DRAFT)
    run.locked_at = None
    run.save()
    common.log(run.company, actor, "payroll_run", run.pk, Action.UPDATED, previous={"status": before}, new={"status": run.status}, reason=reason)
    return run


@transaction.atomic
def delete_draft(run, actor):
    run = _lock_row(run)
    if run.status != Status.DRAFT:
        raise ValidationError("Only a draft run can be deleted. Void a later run instead.")
    common.log(run.company, actor, "payroll_run", run.pk, Action.DELETED, previous=_snap(run))
    run.delete()


def _recover_advances(run, *, reverse=False):
    for s in run.payslips.filter(loan_recovery__gt=0).select_related("employee"):
        if reverse:
            for adv_id, amt in s.full.get("advances", []):
                adv = EmployeeAdvance.objects.select_for_update().filter(pk=adv_id).first()
                if adv:
                    adv.recovered = max(ZERO, adv.recovered - Decimal(amt))
                    adv.status = "open"
                    adv.save()
            s.full = {**s.full, "advances": []}
            s.save(update_fields=["full", "updated_at"])
            continue
        left, split = s.loan_recovery, []
        for adv in EmployeeAdvance.objects.select_for_update().filter(employee=s.employee, status="open").order_by("date"):
            take = min(left, adv.outstanding)
            if take <= 0:
                continue
            adv.recovered += take
            if adv.outstanding <= 0:
                adv.status = "closed"
            adv.save()
            split.append([str(adv.pk), str(take)])
            left -= take
        if left > 0:
            raise ValidationError(f"{s.who.get('name')}'s advance balance is less than the recovery on the payslip. Regenerate the run.")
        s.full = {**s.full, "advances": split}
        s.save(update_fields=["full", "updated_at"])


@transaction.atomic
def post(run, actor):
    run, before = _move(run, actor, (Status.APPROVED,), Status.POSTED)
    company = run.company
    t = run.payslips.aggregate(gross=Sum("gross"), pf_e=Sum("pf_employee"), pf_r=Sum("pf_employer"), esi_e=Sum("esi_employee"),
                               esi_r=Sum("esi_employer"), pt=Sum("professional_tax"), tds=Sum("tds"), loan=Sum("loan_recovery"),
                               other=Sum("other_deductions"), net=Sum("net_pay"))
    v = {k: (x or ZERO) for k, x in t.items()}
    memo = f"Payroll {run.month.strftime('%B %Y')}"
    lines = [
        {"account": "salaries", "debit": v["gross"], "memo": f"{memo}: gross salaries"},
        {"account": payroll_account(company, "employer_pf"), "debit": v["pf_r"], "memo": f"{memo}: employer PF"},
        {"account": payroll_account(company, "employer_esi"), "debit": v["esi_r"], "memo": f"{memo}: employer ESI"},
        {"account": "salaries_payable", "credit": v["net"], "memo": f"{memo}: net pay"},
        {"account": payroll_account(company, "pf_payable"), "credit": v["pf_e"] + v["pf_r"], "memo": f"{memo}: PF (employee + employer)"},
        {"account": payroll_account(company, "esi_payable"), "credit": v["esi_e"] + v["esi_r"], "memo": f"{memo}: ESI (employee + employer)"},
        {"account": payroll_account(company, "pt_payable"), "credit": v["pt"], "memo": f"{memo}: professional tax"},
        {"account": "tds_payable", "credit": v["tds"], "memo": f"{memo}: TDS on salary"},
        {"account": payroll_account(company, "employee_advances"), "credit": v["loan"], "memo": f"{memo}: advances recovered"},
        {"account": payroll_account(company, "other_deductions_payable"), "credit": v["other"], "memo": f"{memo}: other deductions"},
    ]
    lines = [l for l in lines if (l.get("debit") or l.get("credit") or ZERO) > 0]
    ledger.post(company, date=common.month_end(run.month), lines=lines, narration=f"{memo} ({run.employee_count} employees)",
                source=ledger.Source.PAYROLL, source_id=str(run.pk), reference=run.number, actor=actor)
    _recover_advances(run)
    run.posted_at = timezone.now()
    run.save()
    common.log(company, actor, "payroll_run", run.pk, Action.UPDATED, previous={"status": before}, new={"status": run.status})
    return run


@transaction.atomic
def pay(run, actor, data):
    run, before = _move(run, actor, (Status.POSTED,), Status.PAID)
    paid_date = common.date_value(data, "paid_date", "Payment date", required=True)
    if paid_date < run.month:
        raise ValidationError({"paid_date": "The payment date cannot be before the payroll month."})
    if paid_date > datetime.date.today():
        raise ValidationError({"paid_date": "The payment date cannot be in the future."})
    reference = common.text(data, "reference", "Reference", 100)
    bank = None
    if data.get("bank_account"):
        try:
            bank = CompanyBankAccount.objects.filter(pk=data["bank_account"], company=run.company, is_active=True).first()
        except (ValueError, ValidationError):
            bank = None
        if bank is None:
            raise ValidationError({"bank_account": "Choose one of the company's bank accounts."})
    source = chart.ledger_for_bank(run.company, bank) if bank else chart.system_account(run.company, "bank")
    memo = f"Salaries {run.month.strftime('%B %Y')} paid"
    ledger.post(run.company, date=paid_date, lines=[
        {"account": "salaries_payable", "debit": run.total_net, "memo": memo},
        {"account": source, "credit": run.total_net, "memo": memo + (f" ({reference})" if reference else "")},
    ], narration=memo, source=ledger.Source.PAYROLL, source_id=f"{run.pk}-paid", reference=reference or run.number, actor=actor)
    run.paid_date, run.paid_reference, run.paid_from = paid_date, reference, bank
    run.save()
    common.log(run.company, actor, "payroll_run", run.pk, Action.UPDATED, previous={"status": before},
               new={"status": run.status, "paid_date": paid_date.isoformat(), "reference": reference})
    return run


@transaction.atomic
def void(run, actor, reason):
    run = _lock_row(run)
    if run.status == Status.VOID:
        raise ValidationError("This run is already void.")
    if not (reason or "").strip():
        raise ValidationError({"reason": "Give a reason for voiding this run."})
    before = run.status
    if before in (Status.POSTED, Status.PAID):
        ledger.reverse_source(run.company, ledger.Source.PAYROLL, f"{run.pk}-paid", actor, reason)
        ledger.reverse_source(run.company, ledger.Source.PAYROLL, str(run.pk), actor, reason)
        _recover_advances(run, reverse=True)
    run.status, run.void_reason = Status.VOID, reason.strip()[:300]
    run.save()
    _close_approval(run, actor)
    common.log(run.company, actor, "payroll_run", run.pk, Action.DEACTIVATED, previous={"status": before}, new={"status": "void"}, reason=reason)
    return run


# --------------------------------------------------------------------------- editing one payslip

@transaction.atomic
def update_payslip(slip, data, actor):
    slip = Payslip.objects.select_for_update(of=("self",)).select_related("run", "employee", "run__company").get(pk=slip.pk)
    if slip.run.status != Status.DRAFT:
        raise ValidationError("Payslips can only be edited while the run is a draft. Unlock it first.")
    before = {k: str(getattr(slip, k)) for k in ("lop_days", "bonus", "tds", "loan_recovery", "other_deductions", "remarks")}
    d = common.decimal
    if "lop_days" in data:
        lop = d(data, "lop_days", "Loss of pay days", places=1, high=Decimal(slip.basis_days))
        if lop is not None and lop * 2 != int(lop * 2):
            raise ValidationError({"lop_days": "Use whole or half days."})
        slip.lop_days = lop if lop is not None else slip.lop_days_auto
    for key, label in (("bonus", "Bonus"), ("tds", "TDS"), ("other_deductions", "Other deductions"), ("loan_recovery", "Advance recovery")):
        if key in data:
            setattr(slip, key, d(data, key, label, default=ZERO))
    if "remarks" in data:
        slip.remarks = common.text(data, "remarks", "Remarks", 300)
    if slip.loan_recovery > advance_outstanding(slip.employee):
        raise ValidationError({"loan_recovery": f"The employee only owes {advance_outstanding(slip.employee)} in advances."})
    _apply(slip, get_settings(slip.run.company))
    if slip.net_pay < 0:
        raise ValidationError(f"Deductions ({slip.total_deductions}) are more than the gross pay ({slip.gross}).")
    slip.save()
    retotal(slip.run)
    after = {k: str(getattr(slip, k)) for k in before}
    if before != after:
        common.log(slip.run.company, actor, "payslip", slip.pk, Action.UPDATED, previous=before, new=after)
    return slip


# --------------------------------------------------------------------------- documents

def payslip_content(slip):
    w = slip.who
    inr = lambda v: f"{Decimal(v):,.2f}"  # noqa: E731
    earnings = [("Basic", slip.basic), ("House rent allowance", slip.hra), ("Special allowance", slip.special_allowance),
                ("Other allowances", slip.other_allowance), ("Bonus", slip.bonus)]
    deductions = [("Provident fund", slip.pf_employee), ("ESI", slip.esi_employee), ("Professional tax", slip.professional_tax),
                  ("Income tax (TDS)", slip.tds), ("Advance recovery", slip.loan_recovery), ("Other deductions", slip.other_deductions)]
    earnings = [(n, v) for n, v in earnings if Decimal(v) > 0]
    deductions = [(n, v) for n, v in deductions if Decimal(v) > 0]
    rows = []
    for i in range(max(len(earnings), len(deductions), 1)):
        e = earnings[i] if i < len(earnings) else ("", None)
        d = deductions[i] if i < len(deductions) else ("", None)
        rows.append([e[0], inr(e[1]) if e[1] is not None else "", d[0], inr(d[1]) if d[1] is not None else ""])
    employer = []
    if slip.pf_employer > 0:
        employer.append(f"PF {inr(slip.pf_employer)}")
    if slip.esi_employer > 0:
        employer.append(f"ESI {inr(slip.esi_employer)}")
    after = [f"**Net pay in words:** {quotations.amount_in_words(slip.net_pay)}"]
    if employer:
        after.append("Employer contributions for this month (not deducted from your pay): " + ", ".join(employer) + ".")
    if slip.remarks:
        after.append(f"Remarks: {slip.remarks}")
    after.append("This is a computer-generated payslip and needs no signature.")
    details = [["Employee", f"{w.get('name')} ({w.get('code')})"], ["Department", w.get("department") or "-"],
               ["Designation", w.get("designation") or "-"], ["Date of joining", w.get("date_of_joining", "")]]
    if w.get("pan"):
        details.append(["PAN", w["pan"]])
    if w.get("uan"):
        details.append(["UAN", w["uan"]])
    if w.get("bank_account"):
        details.append(["Salary account", f"{w['bank_account']}" + (f", {w['bank_name']}" if w.get("bank_name") else "")])
    return {
        "meta": [["Pay period", slip.month.strftime("%B %Y")], ["Paid days", f"{slip.paid_days} of {slip.basis_days}"],
                 ["Loss of pay days", str(slip.lop_days)]],
        "details": details,
        "table": {"columns": ["Earnings", "Amount (INR)", "Deductions", "Amount (INR)"], "rows": rows, "numeric_columns": [1, 3],
                  "column_widths_mm": [None, 30, 45, 30]},
        "totals": [["Gross earnings", inr(slip.gross)], ["Total deductions", inr(slip.total_deductions)], ["Net pay", inr(slip.net_pay)]],
        "after_totals": after,
        "show_signature": False,
    }


@transaction.atomic
def payslip_document(slip, actor):
    """The payslip PDF, issued (numbered) the first time it is asked for and reused afterwards."""
    slip = Payslip.objects.select_for_update(of=("self",)).select_related("run", "employee", "issued_document").get(pk=slip.pk)
    if slip.issued_document_id:
        return slip.issued_document
    doc = documents.issue_document(DocType.PAYSLIP, actor=actor, content=payslip_content(slip), title=f"Payslip {slip.month.strftime('%B %Y')}",
                                   issue_date=common.month_end(slip.month), source_module="hr", source_id=str(slip.pk))
    slip.issued_document = doc
    slip.save(update_fields=["issued_document", "updated_at"])
    return doc


def bank_file_rows(run):
    """For the bank: the full account number is included, so this is only for people holding hr.payroll (and is audited)."""
    rows = [["Employee code", "Employee name", "Beneficiary name", "Account number", "IFSC", "Bank", "Amount (INR)", "Narration"]]
    missing, total = [], ZERO
    for s in run.payslips.select_related("employee").order_by("employee__first_name", "employee__last_name"):
        e = s.employee
        if s.net_pay <= 0:
            continue
        if not (e.bank_account_encrypted and e.bank_ifsc):
            missing.append(e.name)
            continue
        rows.append([e.code, e.name, e.bank_account_holder or e.name, e.reveal("bank_account"), e.bank_ifsc, e.bank_name, str(s.net_pay),
                     f"Salary {run.month.strftime('%b %Y')}"])
        total += s.net_pay
    return rows, missing, total
