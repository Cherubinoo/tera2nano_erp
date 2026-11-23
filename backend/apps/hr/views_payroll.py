"""Payroll: statutory settings, runs, payslips, the bank file and the payroll reports."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework import status as http
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounting.models import JournalEntry
from apps.company.api import current_company, private_file_response
from apps.company.models import CompanyBankAccount, CompanyProfileAuditEvent

from .models import HRSettings, Payslip, PayrollRun
from .services import common, payroll, reports
from .views_base import brief, get_employee, get_or_404, is_self, me, need

Action = CompanyProfileAuditEvent.Action
Status = PayrollRun.Status
SETTING_FIELDS = ("pf_rate", "pf_wage_ceiling", "esi_ceiling", "esi_employee_rate", "esi_employer_rate", "pt_state", "pt_slabs",
                  "lop_basis", "weekly_off_days", "leave_year_start_month")


# --------------------------------------------------------------------------- settings

def settings_dict(s):
    return {f: getattr(s, f) for f in SETTING_FIELDS}


def _slabs(value):
    if not isinstance(value, list) or not value:
        raise ValidationError({"pt_slabs": "Give at least one slab."})
    out, last = [], Decimal(-1)
    for n, s in enumerate(value, 1):
        if not isinstance(s, dict):
            raise ValidationError({"pt_slabs": f"Slab {n}: invalid."})
        up_to = s.get("up_to")
        try:
            amount = common.money(Decimal(str(s.get("amount"))))
            limit = None if up_to in (None, "") else Decimal(str(up_to))
        except Exception:  # noqa: BLE001
            raise ValidationError({"pt_slabs": f"Slab {n}: use numbers for the limit and the amount."})
        if amount < 0:
            raise ValidationError({"pt_slabs": f"Slab {n}: the amount cannot be negative."})
        if limit is None and n != len(value):
            raise ValidationError({"pt_slabs": "Only the last slab can have no upper limit."})
        if limit is not None and limit <= last:
            raise ValidationError({"pt_slabs": f"Slab {n}: the limits must increase."})
        if limit is None and n == len(value):
            pass
        last = limit if limit is not None else last
        out.append({"up_to": None if limit is None else str(limit), "amount": str(amount)})
    if out[-1]["up_to"] is not None:
        raise ValidationError({"pt_slabs": "The last slab must have no upper limit (for everyone above)."})
    return out


class SettingsView(APIView):
    def get(self, request):
        need(request, "hr.view", "hr.payroll")
        return Response(settings_dict(common.get_settings(current_company())))

    @transaction.atomic
    def patch(self, request):
        need(request, "hr.payroll")
        s = common.get_settings(current_company())
        before = {f: str(getattr(s, f)) for f in SETTING_FIELDS}
        d = request.data
        dec = common.decimal
        if "pf_rate" in d:
            s.pf_rate = dec(d, "pf_rate", "PF rate", high=100, required=True)
        if "pf_wage_ceiling" in d:
            s.pf_wage_ceiling = dec(d, "pf_wage_ceiling", "PF wage ceiling", required=True)
        if "esi_ceiling" in d:
            s.esi_ceiling = dec(d, "esi_ceiling", "ESI ceiling", required=True)
        if "esi_employee_rate" in d:
            s.esi_employee_rate = dec(d, "esi_employee_rate", "ESI employee rate", high=100, required=True)
        if "esi_employer_rate" in d:
            s.esi_employer_rate = dec(d, "esi_employer_rate", "ESI employer rate", high=100, required=True)
        if "pt_state" in d:
            s.pt_state = common.text(d, "pt_state", "State", 60, required=True)
        if "pt_slabs" in d:
            s.pt_slabs = _slabs(d["pt_slabs"])
        if "lop_basis" in d:
            s.lop_basis = common.choice(d, "lop_basis", "basis", HRSettings.LopBasis.values, required=True)
        if "weekly_off_days" in d:
            days = d["weekly_off_days"]
            if not isinstance(days, list) or any(not isinstance(x, int) or not 0 <= x <= 6 for x in days) or len(set(days)) != len(days) or len(days) > 3:
                raise ValidationError({"weekly_off_days": "Choose up to three different weekdays (0 = Monday ... 6 = Sunday)."})
            s.weekly_off_days = sorted(days)
        if "leave_year_start_month" in d:
            m = dec(d, "leave_year_start_month", "Leave year start month", places=0, low=1, high=12, required=True)
            s.leave_year_start_month = int(m)
        s.save()
        after = {f: str(getattr(s, f)) for f in SETTING_FIELDS}
        if before != after:
            common.log(s.company, request.user, "payroll_settings", s.pk, Action.UPDATED, previous=before, new=after)
        return Response(settings_dict(s))


# --------------------------------------------------------------------------- runs

def slip_row(s):
    e = s.employee
    return {"id": str(s.id), "employee": brief(e), "department": s.who.get("department", ""), "paid_days": s.paid_days, "lop_days": s.lop_days,
            "gross": s.gross, "total_deductions": s.total_deductions, "net_pay": s.net_pay, "employer_cost": s.employer_cost,
            "has_bank": bool(e.bank_account_encrypted and e.bank_ifsc), "edited": s.lop_days != s.lop_days_auto or s.bonus > 0 or bool(s.remarks)}


def run_dict(r, *, detail=False):
    out = {"id": str(r.id), "number": r.number, "month": r.month, "month_label": r.month.strftime("%B %Y"), "status": r.status,
           "status_label": r.get_status_display(), "notes": r.notes, "employee_count": r.employee_count, "total_gross": r.total_gross,
           "total_deductions": r.total_deductions, "total_net": r.total_net, "total_employer_cost": r.total_employer_cost,
           "approved_at": r.approved_at, "posted_at": r.posted_at, "paid_date": r.paid_date, "paid_reference": r.paid_reference,
           "void_reason": r.void_reason, "created_at": r.created_at}
    if detail:
        slips = list(r.payslips.select_related("employee"))
        out["payslips"] = [slip_row(s) for s in slips]
        out["missing_bank"] = [s["employee"]["name"] for s in out["payslips"] if not s["has_bank"] and s["net_pay"] > 0]
        out["totals"] = {k: str(sum((getattr(s, k) for s in slips), common.ZERO)) for k in (
            "basic", "hra", "special_allowance", "other_allowance", "bonus", "gross", "pf_employee", "pf_employer", "esi_employee",
            "esi_employer", "professional_tax", "tds", "loan_recovery", "other_deductions", "total_deductions", "net_pay", "employer_cost")}
        out["approved_by"] = (r.approved_by.get_full_name() or r.approved_by.username) if r.approved_by_id else ""
        out["paid_from"] = r.paid_from.display_name if r.paid_from_id else ""
        ids = [str(r.pk), f"{r.pk}-paid"]
        out["journal"] = [{"id": str(j.id), "reference": j.reference, "date": j.date, "kind": "payment" if j.source_id.endswith("-paid") else "posting",
                           "status": j.status} for j in JournalEntry.objects.filter(company=r.company, source="payroll", source_id__in=ids).order_by("date")]
        out["actions"] = {"edit": r.status == Status.DRAFT, "lock": r.status == Status.DRAFT, "unlock": r.status in (Status.LOCKED, Status.PENDING),
                          "submit": r.status == Status.LOCKED, "approve": r.status in (Status.LOCKED, Status.PENDING),
                          "reject": r.status == Status.PENDING, "post": r.status == Status.APPROVED, "pay": r.status == Status.POSTED,
                          "void": r.status != Status.VOID, "delete": r.status == Status.DRAFT, "bank_file": r.status in reports.FINAL}
    return out


class RunCollection(APIView):
    def get(self, request):
        need(request, "hr.payroll")
        qs = PayrollRun.objects.filter(company=current_company())
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response([run_dict(r) for r in qs[:200]])

    @transaction.atomic
    def post(self, request):
        need(request, "hr.payroll")
        month = common.parse_month(request.data.get("month"))
        r = payroll.create_run(current_company(), month, request.user, common.text(request.data, "notes", "Notes", 300))
        out = run_dict(r, detail=True)
        out["skipped"] = r.skipped
        return Response(out, status=http.HTTP_201_CREATED)


def _run(pk):
    return get_or_404(PayrollRun, pk, "payroll run", company=current_company())


class RunDetail(APIView):
    def get(self, request, pk):
        need(request, "hr.payroll")
        return Response(run_dict(_run(pk), detail=True))

    @transaction.atomic
    def patch(self, request, pk):
        need(request, "hr.payroll")
        r = _run(pk)
        if r.status != Status.DRAFT:
            raise ValidationError("Only a draft run can be edited.")
        r.notes = common.text(request.data, "notes", "Notes", 300)
        r.save(update_fields=["notes", "updated_at"])
        return Response(run_dict(r, detail=True))

    @transaction.atomic
    def delete(self, request, pk):
        need(request, "hr.payroll")
        payroll.delete_draft(_run(pk), request.user)
        return Response(status=http.HTTP_204_NO_CONTENT)


class RunAction(APIView):
    action = ""

    @transaction.atomic
    def post(self, request, pk):
        need(request, "hr.payroll")
        r, u, d = _run(pk), request.user, request.data
        reason = d.get("reason") or d.get("note") or ""
        extra = {}
        if self.action == "regenerate":
            r = payroll.regenerate(r, u)
            extra["skipped"] = r.skipped
        elif self.action == "pay":
            r = payroll.pay(r, u, d)
        elif self.action == "void":
            r = payroll.void(r, u, reason)
        elif self.action == "reject":
            r = payroll.reject(r, u, reason)
        else:
            r = getattr(payroll, self.action)(r, u)
        r.refresh_from_db()
        return Response({**run_dict(r, detail=True), **extra})


class BankFile(APIView):
    def get(self, request, pk):
        need(request, "hr.payroll")
        r = _run(pk)
        if r.status not in reports.FINAL:
            raise ValidationError("The bank file is available once the run is approved.")
        rows, missing, total = payroll.bank_file_rows(r)
        if len(rows) == 1:
            raise ValidationError("No employee has complete bank details, so there is nothing to send to the bank.")
        common.log(r.company, request.user, "payroll_run", r.pk, Action.DOWNLOADED,
                   new={"file": "bank transfer", "rows": len(rows) - 1, "total": str(total), "without_bank_details": len(missing)})
        resp = common.csv_response(f"salary-transfer-{r.month.strftime('%Y-%m')}.csv", rows)
        resp["X-Missing-Bank-Details"] = str(len(missing))
        return resp


# --------------------------------------------------------------------------- payslips

def payslip_dict(s):
    return {"id": str(s.id), "run": {"id": str(s.run_id), "number": s.run.number, "status": s.run.status}, "employee": brief(s.employee),
            "month": s.month, "month_label": s.month.strftime("%B %Y"), "who": s.who, "basis_days": s.basis_days,
            "paid_days": s.paid_days, "lop_days": s.lop_days, "lop_days_auto": s.lop_days_auto, "bonus": s.bonus, "tds": s.tds,
            "loan_recovery": s.loan_recovery, "other_deductions": s.other_deductions, "remarks": s.remarks,
            "basic": s.basic, "hra": s.hra, "special_allowance": s.special_allowance, "other_allowance": s.other_allowance,
            "gross": s.gross, "pf_employee": s.pf_employee, "pf_employer": s.pf_employer, "esi_employee": s.esi_employee,
            "esi_employer": s.esi_employer, "professional_tax": s.professional_tax, "total_deductions": s.total_deductions,
            "net_pay": s.net_pay, "employer_cost": s.employer_cost, "full": {k: s.full.get(k) for k in (
                "basic", "hra", "special_allowance", "other_allowance", "pf_enabled", "esi_enabled", "pt_enabled")},
            "editable": s.run.status == Status.DRAFT, "has_pdf": s.issued_document_id is not None}


def _payslip(request, pk, *, write=False):
    s = get_or_404(Payslip, pk, "payslip", run__company=current_company())
    s = Payslip.objects.select_related("run", "employee").get(pk=s.pk)
    if request.user.has_code("hr.payroll"):
        return s
    if not write and is_self(request, s.employee) and s.run.status in (Status.POSTED, Status.PAID):
        return s
    # Not allowed, and not revealed: a payslip that exists but is not yours looks the same as one that does not exist.
    if is_self(request, s.employee) or request.user.has_code("hr.view"):
        raise PermissionDenied("Your role does not allow this.")
    from rest_framework.exceptions import NotFound
    raise NotFound("No such payslip.")


class PayslipDetail(APIView):
    def get(self, request, pk):
        return Response(payslip_dict(_payslip(request, pk)))

    @transaction.atomic
    def patch(self, request, pk):
        s = _payslip(request, pk, write=True)
        need(request, "hr.payroll")
        return Response(payslip_dict(payroll.update_payslip(s, request.data, request.user)))


class PayslipPdf(APIView):
    def get(self, request, pk):
        s = _payslip(request, pk)
        if s.run.status not in reports.FINAL:
            raise ValidationError("The payslip PDF is available once the run is approved.")
        doc = payroll.payslip_document(s, request.user)
        return private_file_response(doc.file, filename=f"payslip-{s.who.get('code', 'employee')}-{s.month.strftime('%Y-%m')}.pdf", attachment=request.query_params.get("inline") != "true",
                                     mime_type="application/pdf")


class MyPayslips(APIView):
    def get(self, request):
        e = me(request)
        if e is None:
            return Response({"linked": False, "rows": []})
        qs = Payslip.objects.filter(employee=e, run__status__in=(Status.POSTED, Status.PAID)).select_related("run", "employee").order_by("-month")
        return Response({"linked": True, "rows": [payslip_dict(s) for s in qs]})


class EmployeePayslips(APIView):
    def get(self, request, pk):
        e = get_employee(pk)
        if request.user.has_code("hr.payroll"):
            qs = Payslip.objects.filter(employee=e, run__status__in=reports.FINAL)
        elif is_self(request, e):
            qs = Payslip.objects.filter(employee=e, run__status__in=(Status.POSTED, Status.PAID))
        else:
            raise PermissionDenied("Your role does not allow this.")
        return Response([payslip_dict(s) for s in qs.select_related("run", "employee").order_by("-month")])


# --------------------------------------------------------------------------- reports

def _range(request):
    p = request.query_params
    today = common.month_start(datetime.date.today())
    fy = today.year if today.month >= 4 else today.year - 1
    start = common.parse_month(p.get("from"), "from") if p.get("from") else datetime.date(fy, 4, 1)
    end = common.parse_month(p.get("to"), "to") if p.get("to") else today
    if end < start:
        raise ValidationError({"to": "The end month cannot be before the start month."})
    return start, end


def _maybe_csv(request, name, header, rows):
    if request.query_params.get("csv") == "1":
        current = current_company()
        common.log(current, request.user, "payroll_run", current.pk, Action.DOWNLOADED, new={"file": name})
        return common.csv_response(f"{name}.csv", [header] + rows)
    return None


class MonthlyCostReport(APIView):
    def get(self, request):
        need(request, "hr.payroll")
        start, end = _range(request)
        rows = reports.monthly_cost(current_company(), start, end)
        csv = _maybe_csv(request, "payroll-monthly-cost", ["Month", "Employees", "Gross", "Employer PF and ESI", "Employer cost", "Deductions", "Net pay"],
                         [[r["month"].strftime("%b %Y"), r["employees"], r["gross"], r["employer_contributions"], r["employer_cost"], r["deductions"], r["net"]] for r in rows])
        return csv or Response({"from": start, "to": end, "rows": rows})


class DepartmentCostReport(APIView):
    def get(self, request):
        need(request, "hr.payroll")
        start, end = _range(request)
        rows = reports.department_cost(current_company(), start, end)
        csv = _maybe_csv(request, "payroll-department-cost", ["Department", "Payslips", "Gross", "Employer cost", "Net pay"],
                         [[r["department"], r["employees"], r["gross"], r["employer_cost"], r["net"]] for r in rows])
        return csv or Response({"from": start, "to": end, "rows": rows})


class StatutoryReport(APIView):
    def get(self, request):
        need(request, "hr.payroll")
        start, end = _range(request)
        data = reports.statutory(current_company(), start, end)
        csv = _maybe_csv(request, "payroll-statutory", ["Month", "PF employee", "PF employer", "ESI employee", "ESI employer", "Professional tax", "TDS"],
                         [[r["month"].strftime("%b %Y"), r["pf_employee"], r["pf_employer"], r["esi_employee"], r["esi_employer"], r["pt"], r["tds"]] for r in data["months"]])
        return csv or Response({"from": start, "to": end, **data})


class RegisterReport(APIView):
    def get(self, request):
        need(request, "hr.payroll")
        month = common.parse_month(request.query_params.get("month") or datetime.date.today().strftime("%Y-%m"))
        rows = reports.register(current_company(), month)
        csv = _maybe_csv(request, f"salary-register-{month.strftime('%Y-%m')}", [c[1] for c in reports.REGISTER_COLUMNS],
                         [[r[c[0]] for c in reports.REGISTER_COLUMNS] for r in rows])
        return csv or Response({"month": month, "rows": rows, "columns": [{"key": k, "label": l} for k, l in reports.REGISTER_COLUMNS]})


class YtdReport(APIView):
    def get(self, request):
        need(request, "hr.payroll")
        p = request.query_params
        today = datetime.date.today()
        try:
            year = int(p.get("year") or (today.year if today.month >= 4 else today.year - 1))
        except ValueError:
            raise ValidationError({"year": "Use a four-digit year."})
        emp = get_employee(p["employee"]) if p.get("employee") else None
        data = reports.ytd(current_company(), year, emp)
        csv = _maybe_csv(request, f"payroll-ytd-{year}", ["Code", "Employee", "Months", "Gross", "PF employee", "ESI employee", "Professional tax", "TDS", "Net pay"],
                         [[r["code"], r["name"], r["months"], r["gross"], r["pf_employee"], r["esi_employee"], r["pt"], r["tds"], r["net"]] for r in data["rows"]])
        return csv or Response(data)
