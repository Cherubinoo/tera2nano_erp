"""Payroll runs end to end with hand-computed figures. August 2026 has 31 days (five Sundays: 2, 9, 16, 23, 30)."""
import uuid

from apps.accounting.models import JournalEntry
from apps.accounts.models import Role
from apps.company.models import CompanyProfileAuditEvent, IssuedDocument
from apps.company.tests.base import client_for, make_user
from apps.hr.models import EmployeeAdvance, PayrollRun

from .base import ACCOUNT, A, D, H, HRBase

try:
    import fitz  # PyMuPDF is only a test-time helper
except ImportError:  # pragma: no cover
    fitz = None

Action = CompanyProfileAuditEvent.Action


def pdf_text(doc):
    with doc.file.open("rb") as fh:
        return "".join(p.get_text() for p in fitz.open(stream=fh.read(), filetype="pdf"))


class PayrollBase(HRBase):
    def setUp(self):
        super().setUp()
        dept = self.dept("Engineering")
        self.asha = self.emp("Asha", "Rao", department=dept["id"], bank_account_number=ACCOUNT, bank_ifsc="HDFC0001234", bank_name="HDFC Bank",
                             bank_account_holder="Asha Rao", pan="ABCDE1234F")
        self.bala = self.emp("Bala", "Iyer", department=dept["id"], bank_account_number="998877665544", bank_ifsc="ICIC0000123", bank_name="ICICI")
        self.structure(self.asha)                                                     # gross 40,000
        self.structure(self.bala, basic="10000", hra="4000", special="3000", other="1000")  # gross 18,000
        self.pf = self.mp  # the Managing Partner decides payroll in approvals

    # -- helpers
    def slip(self, run, employee):
        detail = self.fin.get(f"{H}/payroll/runs/{run['id']}/").json()
        row = next(s for s in detail["payslips"] if s["employee"]["id"] == employee["id"])
        return self.fin.get(f"{H}/payroll/payslips/{row['id']}/").json()

    def decide(self, run, action="approve", client=None, expect=200, **data):
        status = self.fin.get("/api/v1/approvals/status/", {"kind": "payroll_run", "source_id": run["id"]}).json()
        r = (client or self.api_mp).post(f"/api/v1/approvals/requests/{status['id']}/{action}/", data)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def approved(self, month="2026-08"):
        run = self.run_payroll(month)
        self.step(run, "lock")
        self.step(run, "submit")
        self.decide(run)
        return self.fin.get(f"{H}/payroll/runs/{run['id']}/").json()

    def posted(self, month="2026-08"):
        run = self.approved(month)
        return self.step(run, "post")


class PayslipMath(PayrollBase):
    def test_full_month_figures_for_two_employees(self):
        run = self.run_payroll()
        self.assertRegex(run["number"], r"^T2N/PRN/\d{2}/\d{4}/00001$")
        self.assertEqual((run["status"], run["employee_count"]), ("draft", 2))
        a, b = self.slip(run, self.asha), self.slip(run, self.bala)
        self.assertEqual((D(a["gross"]), D(a["pf_employee"]), D(a["esi_employee"]), D(a["professional_tax"]), D(a["net_pay"])),
                         (D("40000"), D("1800"), D("0"), D("52.50"), D("38147.50")))
        self.assertEqual((D(b["gross"]), D(b["pf_employee"]), D(b["esi_employee"]), D(b["esi_employer"]), D(b["professional_tax"]), D(b["net_pay"])),
                         (D("18000"), D("1200"), D("135"), D("585"), D("0"), D("16665")))
        self.assertEqual((D(run["total_gross"]), D(run["total_deductions"]), D(run["total_net"]), D(run["total_employer_cost"])),
                         (D("58000"), D("3187.50"), D("54812.50"), D("61585")))
        self.assertEqual(a["who"]["pan"], "XXXXXX234F")  # the payslip keeps only masked identifiers
        self.assertEqual(a["who"]["bank_account"], "XXXXXX9012")

    def test_loss_of_pay_from_absence_and_half_days(self):
        self.api_hr.post(f"{H}/attendance/", {"date": "2026-08-04", "status": "absent", "employees": [self.asha["id"]]})
        self.api_hr.post(f"{H}/attendance/", {"date": "2026-08-05", "status": "absent", "employees": [self.asha["id"]]})
        self.api_hr.post(f"{H}/attendance/", {"date": "2026-08-06", "status": "half_day", "employees": [self.bala["id"]]})
        run = self.run_payroll()
        a, b = self.slip(run, self.asha), self.slip(run, self.bala)
        self.assertEqual((D(a["lop_days"]), D(a["paid_days"])), (D("2"), D("29")))
        self.assertEqual((D(a["basic"]), D(a["hra"]), D(a["special_allowance"]), D(a["gross"])), (D("18709.68"), D("7483.87"), D("11225.81"), D("37419.36")))
        self.assertEqual(D(a["net_pay"]), D("35566.86"))  # 37419.36 - PF 1800 - PT 52.50
        self.assertEqual(D(b["lop_days"]), D("0.5"))
        self.assertEqual(D(b["basic"]), D("9838.71"))  # 10000 * 30.5 / 31

    def test_unpaid_leave_reduces_pay_but_paid_leave_and_sundays_do_not(self):
        cl = self.leave_type("CL", "Casual leave", "12")
        lop = self.leave_type("LOP", "Loss of pay", "0", paid=False)
        user, client, e = self.employee_user("emp", date_of_joining="2025-04-01")
        self.structure(e, basic="31000", hra="0", special="0", esi_enabled=False, pf_enabled=False, pt_enabled=False)
        for lt, start, end in ((cl, "2026-08-03", "2026-08-03"), (lop, "2026-08-07", "2026-08-10")):  # Fri-Mon over a Saturday... and Sunday off
            r = client.post(f"{H}/leave/requests/", {"leave_type": lt["id"], "start_date": start, "end_date": end, "submit": True})
            self.assertEqual(r.status_code, 201, r.content)
            self.assertEqual(self.api_hr.post(f"{H}/leave/requests/{r.json()['id']}/approve/", {}).status_code, 200)
        run = self.run_payroll()
        s = self.slip(run, e)
        self.assertEqual(D(s["lop_days"]), D("3"))  # Fri, Sat, Mon; Sunday 9th is a weekly off; the casual leave day is paid
        self.assertEqual(D(s["basic"]), D("28000.00"))  # 31000 * 28 / 31

    def test_a_half_day_of_unpaid_leave_costs_half_a_day(self):
        lop = self.leave_type("LOP", "Loss of pay", "0", paid=False)
        user, client, e = self.employee_user("emp")
        self.structure(e, basic="31000", hra="0", special="0", esi_enabled=False, pf_enabled=False, pt_enabled=False)
        r = client.post(f"{H}/leave/requests/", {"leave_type": lop["id"], "start_date": "2026-08-12", "half_day": True, "submit": True})
        self.api_hr.post(f"{H}/leave/requests/{r.json()['id']}/approve/", {})
        s = self.slip(self.run_payroll(), e)
        self.assertEqual((D(s["lop_days"]), D(s["basic"])), (D("0.5"), D("30500.00")))

    def test_mid_month_joiner_and_leaver_are_prorated(self):
        joiner = self.emp("Late", "Joiner", date_of_joining="2026-08-16")
        self.structure(joiner, basic="31000", hra="0", special="0", effective="2026-08-16", esi_enabled=False, pf_enabled=False, pt_enabled=False)
        leaver = self.emp("Early", "Leaver")
        self.structure(leaver, basic="31000", hra="0", special="0", esi_enabled=False, pf_enabled=False, pt_enabled=False)
        self.api_hr.patch(f"{H}/employees/{leaver['id']}/", {"status": "exited", "exit_date": "2026-08-10", "exit_reason": "Resigned"})
        run = self.run_payroll()
        j, l = self.slip(run, joiner), self.slip(run, leaver)
        self.assertEqual((D(j["lop_days"]), D(j["basic"])), (D("15"), D("16000.00")))  # 16 of 31 days: 31000 * 16 / 31
        self.assertEqual((D(l["lop_days"]), D(l["basic"])), (D("21"), D("10000.00")))  # worked 10 of 31 days
        later = self.emp("Next", "Month", date_of_joining="2026-09-01")
        self.structure(later, effective="2026-09-01")
        gone = self.emp("Gone", "Already", date_of_joining="2025-04-01")
        self.structure(gone)
        self.api_hr.patch(f"{H}/employees/{gone['id']}/", {"status": "exited", "exit_date": "2026-07-31", "exit_reason": "x"})
        run2 = self.run_payroll("2026-08", expect=400)  # one live run per month
        self.assertIn("month", run2)
        names = {s["employee"]["name"] for s in self.fin.get(f"{H}/payroll/runs/{run['id']}/").json()["payslips"]}
        self.assertNotIn("Next Month", names)
        self.assertNotIn("Gone Already", names)

    def test_working_day_basis(self):
        r = self.fin.patch(f"{H}/payroll/settings/", {"lop_basis": "working"})
        self.assertEqual(r.status_code, 200, r.content)
        user, client, e = self.employee_user("emp")
        self.structure(e, basic="26000", hra="0", special="0", esi_enabled=False, pf_enabled=False, pt_enabled=False)
        self.api_hr.post(f"{H}/attendance/", {"date": "2026-08-04", "status": "absent", "employees": [e["id"]]})
        s = self.slip(self.run_payroll(), e)
        self.assertEqual((s["basis_days"], D(s["lop_days"]), D(s["basic"])), (26, D("1"), D("25000.00")))  # 31 days - 5 Sundays = 26

    def test_structure_history_uses_the_one_in_force(self):
        self.structure(self.asha, basic="30000", hra="0", special="0", effective="2026-08-15")
        s = self.slip(self.run_payroll(), self.asha)
        self.assertEqual(D(s["basic"]), D("30000"))  # latest structure effective by the month end
        rows = self.fin.get(f"{H}/employees/{self.asha['id']}/salary/").json()["rows"]
        self.assertEqual([r["effective_from"] for r in rows], ["2026-08-15", "2025-04-01"])
        self.structure(self.asha, effective="2026-08-15", expect=400)  # one structure per effective date

    def test_employee_without_a_structure_is_reported_not_paid(self):
        self.emp("No", "Pay")
        run = self.run_payroll()
        self.assertEqual(run["employee_count"], 2)
        self.assertEqual(run["skipped"][0]["employee"], "No Pay")

    def test_tds_and_advance_recovery(self):
        self.structure(self.asha, effective="2026-06-01", tds_monthly="1000")
        adv = self.fin.post(f"{H}/employees/{self.asha['id']}/advances/", {"date": "2026-07-01", "amount": "5000", "installment": "2000"})
        self.assertEqual(adv.status_code, 201, adv.content)
        run = self.run_payroll()
        a = self.slip(run, self.asha)
        self.assertEqual((D(a["tds"]), D(a["loan_recovery"]), D(a["total_deductions"]), D(a["net_pay"])), (D("1000"), D("2000"), D("4852.50"), D("35147.50")))
        self.step(run, "lock")
        self.step(run, "submit")
        self.decide(run)
        self.step(run, "post")
        tb = self.trial_balance()
        self.assertEqual(tb["TDS payable"], D("-1000"))
        self.assertEqual(tb["Employee advances"], D("-2000"))
        self.assertEqual(self.fin.get(f"{H}/employees/{self.asha['id']}/advances/").json()[0]["outstanding"], "3000.00")
        self.step(run, "void", reason="Wrong month")
        self.assertEqual(self.fin.get(f"{H}/employees/{self.asha['id']}/advances/").json()[0]["outstanding"], "5000.00")  # recovery undone

    def test_editing_a_payslip_recalculates_and_is_validated(self):
        run = self.run_payroll()
        b = self.slip(run, self.bala)
        r = self.fin.patch(f"{H}/payroll/payslips/{b['id']}/", {"bonus": "5000", "remarks": "Diwali bonus"})
        self.assertEqual(r.status_code, 200, r.content)
        e = r.json()
        self.assertEqual((D(e["gross"]), D(e["esi_employee"]), D(e["esi_employer"]), D(e["professional_tax"])), (D("23000"), D("172.50"), D("747.50"), D("22.50")))
        self.assertEqual(D(e["net_pay"]), D("21605.00"))  # 23000 - PF 1200 - ESI 172.50 - PT 22.50
        detail = self.fin.get(f"{H}/payroll/runs/{run['id']}/").json()
        self.assertEqual(D(detail["total_net"]), D("38147.50") + D("21605.00"))
        self.assertEqual(self.fin.patch(f"{H}/payroll/payslips/{b['id']}/", {"lop_days": "31.5"}).status_code, 400)
        self.assertEqual(self.fin.patch(f"{H}/payroll/payslips/{b['id']}/", {"lop_days": "1.3"}).status_code, 400)
        self.assertEqual(self.fin.patch(f"{H}/payroll/payslips/{b['id']}/", {"bonus": "-1"}).status_code, 400)
        self.assertEqual(self.fin.patch(f"{H}/payroll/payslips/{b['id']}/", {"tds": "999999"}).status_code, 400)  # more than the pay
        self.assertEqual(self.fin.patch(f"{H}/payroll/payslips/{b['id']}/", {"loan_recovery": "10"}).status_code, 400)  # no advance
        ok = self.fin.patch(f"{H}/payroll/payslips/{b['id']}/", {"lop_days": "3.00"}).json()  # the form sends back what the API showed
        self.assertEqual((D(ok["lop_days"]), D(ok["lop_days_auto"])), (D("3"), D("0")))
        self.step(run, "lock")
        self.assertEqual(self.fin.patch(f"{H}/payroll/payslips/{b['id']}/", {"bonus": "1"}).status_code, 400)  # only a draft is editable
        ev = CompanyProfileAuditEvent.objects.filter(entity_type="payslip", action=Action.UPDATED, changed_fields__contains=["bonus"]).first()
        self.assertEqual(ev.new_values.get("bonus"), "[redacted]")


class RunLifecycle(PayrollBase):
    def test_full_cycle_posts_balanced_entries_and_void_reverses_them(self):
        run = self.run_payroll()
        self.step(run, "lock")
        pending = self.step(run, "submit")
        self.assertEqual(pending["status"], "pending_approval")
        self.assertEqual(self.step(run, "post", expect=400) is not None, True)  # not approved yet
        approved = self.decide(run)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(self.fin.get(f"{H}/payroll/runs/{run['id']}/").json()["status"], "approved")
        before = self.trial_balance()
        self.assertEqual(before.get("Salaries and wages", D(0)), D(0))
        posted = self.step(run, "post")
        self.assertEqual(posted["status"], "posted")
        tb = self.trial_balance()
        self.assertEqual(tb["Salaries and wages"], D("58000"))
        self.assertEqual(tb["Employer PF contribution"], D("3000"))
        self.assertEqual(tb["Employer ESI contribution"], D("585"))
        self.assertEqual(tb["Salaries payable"], D("-54812.50"))
        self.assertEqual(tb["PF payable"], D("-6000"))
        self.assertEqual(tb["ESI payable"], D("-720"))
        self.assertEqual(tb["Professional tax payable"], D("-52.50"))
        self.assertNotIn("TDS payable", tb)
        entry = JournalEntry.objects.get(source="payroll", source_id=run["id"])
        self.assertEqual((entry.date.isoformat(), entry.reference), ("2026-08-31", run["number"]))
        self.step(run, "post", expect=400)  # once only
        # pay
        self.step(run, "pay", expect=400, paid_date="2999-01-01")
        self.step(run, "pay", expect=400, paid_date="2026-07-31")
        self.step(run, "pay", expect=400)
        paid = self.step(run, "pay", paid_date="2026-09-01", reference="UTR998877")
        self.assertEqual((paid["status"], paid["paid_date"], paid["paid_reference"]), ("paid", "2026-09-01", "UTR998877"))
        tb = self.trial_balance()
        self.assertEqual(tb["Salaries payable"], D("0"))
        self.assertEqual(tb["Bank (default account)"], D("-54812.50"))
        self.assertEqual([j["kind"] for j in paid["journal"]], ["posting", "payment"])
        # void reverses both entries and frees the month
        self.step(run, "void", expect=400)  # a reason is needed
        voided = self.step(run, "void", reason="Entered the wrong attendance")
        self.assertEqual(voided["status"], "void")
        tb = self.trial_balance()
        for name in ("Salaries and wages", "Employer PF contribution", "Salaries payable", "PF payable", "ESI payable", "Professional tax payable", "Bank (default account)"):
            self.assertEqual(tb.get(name, D(0)), D(0), name)
        self.assertEqual(JournalEntry.objects.filter(source="reversal").count(), 2)
        self.step(run, "void", expect=400)
        again = self.run_payroll()
        self.assertNotEqual(again["id"], run["id"])
        self.assertEqual(PayrollRun.objects.filter(month="2026-08-01").count(), 2)

    def test_run_numbers_and_month_rules(self):
        self.run_payroll("2026-08")
        self.assertIn("month", self.run_payroll("2026-08", expect=400))
        self.assertIn("month", self.run_payroll("2999-01", expect=400))
        self.assertIn("month", self.run_payroll("garbage", expect=400))
        sep = self.run_payroll("2026-09")
        self.assertRegex(sep["number"], r"/00002$")
        fresh_company_month = self.run_payroll("2019-01", expect=400)  # nobody had a structure then
        self.assertIn("month", fresh_company_month)

    def test_draft_can_be_regenerated_and_deleted_only_while_draft(self):
        run = self.run_payroll()
        b = self.slip(run, self.bala)
        self.fin.patch(f"{H}/payroll/payslips/{b['id']}/", {"bonus": "100"})
        again = self.step(run, "regenerate")
        self.assertEqual(D(self.slip(run, self.bala)["bonus"]), D("0"))  # manual edits are replaced
        self.assertEqual(again["employee_count"], 2)
        self.step(run, "lock")
        self.step(run, "regenerate", expect=400)
        self.assertEqual(self.fin.delete(f"{H}/payroll/runs/{run['id']}/").status_code, 400)
        self.step(run, "unlock")
        self.assertEqual(self.fin.delete(f"{H}/payroll/runs/{run['id']}/").status_code, 204)
        self.assertEqual(self.fin.get(f"{H}/payroll/runs/{run['id']}/").status_code, 404)

    def test_direct_approval_is_refused_when_the_approvals_module_decides(self):
        run = self.run_payroll()
        self.step(run, "lock")
        self.step(run, "approve", expect=400)
        self.step(run, "submit")
        self.step(run, "approve", expect=400)
        self.step(run, "reject", expect=400, reason="no")
        self.assertEqual(self.fin.get(f"{H}/payroll/runs/{run['id']}/").json()["status"], "pending_approval")

    def test_rejected_in_approvals_goes_back_to_draft_and_withdrawing_unlocks(self):
        run = self.run_payroll()
        self.step(run, "lock")
        self.step(run, "submit")
        self.decide(run, "reject", comment="Check the bonus first")
        r = self.fin.get(f"{H}/payroll/runs/{run['id']}/").json()
        self.assertEqual(r["status"], "draft")
        self.step(run, "lock")
        self.step(run, "submit")
        status = lambda: self.fin.get("/api/v1/approvals/status/", {"kind": "payroll_run", "source_id": run["id"]}).json()["status"]  # noqa: E731
        self.assertEqual(status(), "pending")
        self.step(run, "unlock")  # sending it back closes the open request
        self.assertEqual(status(), "cancelled")
        self.step(run, "lock")
        self.step(run, "submit")
        self.step(run, "void", reason="Abandoned")  # voiding closes it too
        self.assertEqual(status(), "cancelled")

    def test_withdrawing_in_approvals_puts_the_run_back_to_locked(self):
        run = self.run_payroll()
        self.step(run, "lock")
        self.step(run, "submit")
        self.decide(run, "withdraw", client=self.fin)
        self.assertEqual(self.fin.get(f"{H}/payroll/runs/{run['id']}/").json()["status"], "locked")
        self.step(run, "submit")  # can be sent again

    def test_only_a_payroll_run_with_a_negative_net_cannot_be_locked(self):
        run = self.run_payroll()
        b = self.slip(run, self.bala)
        self.assertEqual(self.fin.patch(f"{H}/payroll/payslips/{b['id']}/", {"other_deductions": "20000"}).status_code, 400)

    def test_posting_and_every_step_are_audited(self):
        run = self.posted()
        events = CompanyProfileAuditEvent.objects.filter(entity_type="payroll_run", entity_id=run["id"]).order_by("occurred_at")
        statuses = [e.new_values.get("status") for e in events if e.new_values.get("status")]
        self.assertEqual(statuses, ["draft", "locked", "pending_approval", "approved", "posted"])
        self.assertEqual(events[0].action, Action.CREATED)
        self.assertEqual(events[0].new_values.get("net"), "[redacted]")


class Documents(PayrollBase):
    def test_payslip_pdf_has_the_figures_and_never_the_full_account_number(self):
        if fitz is None:
            self.skipTest("PyMuPDF not installed")
        run = self.run_payroll()
        slip = self.slip(run, self.asha)
        self.assertEqual(self.fin.get(f"{H}/payroll/payslips/{slip['id']}/pdf/").status_code, 400)  # not approved yet
        self.step(run, "lock")
        self.step(run, "submit")
        self.decide(run)
        r = self.fin.get(f"{H}/payroll/payslips/{slip['id']}/pdf/")
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        doc = IssuedDocument.objects.get(source_module="hr", source_id=slip["id"])
        self.assertEqual(doc.document_type, "payslip")
        text = pdf_text(doc)
        for needle in ("August 2026", "Asha Rao", "40,000.00", "1,800.00", "52.50", "38,147.50", "Net pay in words", "Thirty Eight Thousand One Hundred Forty Seven",
                       "Employer contributions", "XXXXXX9012", "XXXXXX234F"):
            self.assertIn(needle, text, needle)
        for secret in (ACCOUNT, "ABCDE1234F"):
            self.assertNotIn(secret, text)
        self.assertNotIn(ACCOUNT, "".join(doc.snapshot.__repr__().split()))
        again = self.fin.get(f"{H}/payroll/payslips/{slip['id']}/pdf/")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(IssuedDocument.objects.filter(source_module="hr", source_id=slip["id"]).count(), 1)  # numbered once

    def test_bank_file_has_full_details_needs_payroll_and_is_audited(self):
        run = self.run_payroll()
        self.assertEqual(self.fin.get(f"{H}/payroll/runs/{run['id']}/bank-file/").status_code, 400)  # not approved
        self.step(run, "lock")
        self.step(run, "submit")
        self.decide(run)
        carol = self.emp("Carol", "NoBank")
        self.assertIsNotNone(carol)
        r = self.fin.get(f"{H}/payroll/runs/{run['id']}/bank-file/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r["Content-Type"])
        rows = r.content.decode("utf-8-sig").strip().splitlines()
        self.assertEqual(rows[0], "Employee code,Employee name,Beneficiary name,Account number,IFSC,Bank,Amount (INR),Narration")
        self.assertEqual(len(rows), 3)
        self.assertIn(f",Asha Rao,Asha Rao,{ACCOUNT},HDFC0001234,HDFC Bank,38147.50,Salary Aug 2026", rows[1])
        self.assertIn("998877665544,ICIC0000123,ICICI,16665.00", rows[2])
        ev = CompanyProfileAuditEvent.objects.get(entity_type="payroll_run", action=Action.DOWNLOADED)
        self.assertEqual((ev.new_values["rows"], ev.new_values["total"]), (2, "54812.50"))
        self.assertNotIn(ACCOUNT, str(ev.new_values))
        for client in (self.api_staff, self.api_pm):
            self.assertEqual(client.get(f"{H}/payroll/runs/{run['id']}/bank-file/").status_code, 403)

    def test_salary_register_csv_and_reports_count_only_finalised_runs(self):
        draft = self.run_payroll("2026-07")
        for u in (self.fin,):
            self.assertEqual(u.get(f"{H}/payroll/reports/register/", {"month": "2026-07"}).json()["rows"], [])  # a draft is not final
        self.step(draft, "void", reason="redo")
        self.posted("2026-08")
        reg = self.fin.get(f"{H}/payroll/reports/register/", {"month": "2026-08"}).json()
        self.assertEqual([r["employee"] for r in reg["rows"]], ["Asha Rao", "Bala Iyer"])
        csv = self.fin.get(f"{H}/payroll/reports/register/", {"month": "2026-08", "csv": "1"})
        lines = csv.content.decode("utf-8-sig").strip().splitlines()
        self.assertTrue(lines[0].startswith("Employee code,Employee,Department,Payroll run"))
        self.assertEqual(len(lines), 3)
        mc = self.fin.get(f"{H}/payroll/reports/monthly-cost/", {"from": "2026-04", "to": "2026-12"}).json()["rows"]
        self.assertEqual(len(mc), 1)
        self.assertEqual((mc[0]["month"], D(mc[0]["gross"]), D(mc[0]["employer_cost"]), D(mc[0]["net"]), mc[0]["employees"]), ("2026-08-01", D("58000"), D("61585"), D("54812.50"), 2))
        self.assertEqual(D(mc[0]["employer_contributions"]), D("3585"))
        dc = self.fin.get(f"{H}/payroll/reports/department-cost/", {"from": "2026-08", "to": "2026-08"}).json()["rows"]
        self.assertEqual([(r["department"], D(r["employer_cost"])) for r in dc], [("Engineering", D("61585"))])
        st = self.fin.get(f"{H}/payroll/reports/statutory/", {"from": "2026-08", "to": "2026-08"}).json()["total"]
        self.assertEqual({k: D(v) for k, v in st.items()}, {"pf_employee": D("3000"), "pf_employer": D("3000"), "esi_employee": D("135"),
                                                          "esi_employer": D("585"), "pt": D("52.50"), "tds": D("0")})
        ytd = self.fin.get(f"{H}/payroll/reports/ytd/", {"year": 2026}).json()["rows"]
        self.assertEqual([(r["name"], D(r["gross"]), D(r["net"])) for r in ytd], [("Asha Rao", D("40000"), D("38147.50")), ("Bala Iyer", D("18000"), D("16665"))])
        one = self.fin.get(f"{H}/payroll/reports/ytd/", {"year": 2026, "employee": self.bala["id"]}).json()["rows"]
        self.assertEqual(len(one), 1)
        self.assertEqual(self.fin.get(f"{H}/payroll/reports/ytd/", {"year": "abc"}).status_code, 400)
        self.assertEqual(self.fin.get(f"{H}/payroll/reports/monthly-cost/", {"from": "2026-09", "to": "2026-08"}).status_code, 400)
        self.assertEqual(CompanyProfileAuditEvent.objects.filter(action=Action.DOWNLOADED).count(), 1)  # the register CSV
        for client in (self.api_staff, self.api_pm):
            for path in ("monthly-cost", "department-cost", "statutory", "register", "ytd"):
                self.assertEqual(client.get(f"{H}/payroll/reports/{path}/").status_code, 403, path)


class SelfServiceIsolation(PayrollBase):
    def setUp(self):
        super().setUp()
        self.user, self.client_e, self.me = self.employee_user("mine")
        self.structure(self.me, basic="15000", hra="5000", special="0", esi_enabled=False)
        self.user2, self.client_o, self.other = self.employee_user("other")
        self.structure(self.other, basic="50000", hra="0", special="0")

    def test_nothing_is_visible_until_the_run_is_posted(self):
        run = self.run_payroll()
        self.assertEqual(self.client_e.get(f"{H}/me/payslips/").json()["rows"], [])
        mine = self.slip(run, self.me)
        self.assertEqual(self.client_e.get(f"{H}/payroll/payslips/{mine['id']}/").status_code, 403)  # draft
        self.step(run, "lock")
        self.step(run, "submit")
        self.decide(run)
        self.assertEqual(self.client_e.get(f"{H}/me/payslips/").json()["rows"], [])  # approved but not yet posted
        self.step(run, "post")
        rows = self.client_e.get(f"{H}/me/payslips/").json()["rows"]
        self.assertEqual([r["id"] for r in rows], [mine["id"]])
        self.assertEqual(D(rows[0]["net_pay"]), D("18200"))  # 20,000 gross - PF 1,800; no professional tax up to 21,000
        self.assertEqual(self.client_e.get(f"{H}/payroll/payslips/{mine['id']}/").status_code, 200)
        self.assertEqual(self.client_e.get(f"{H}/payroll/payslips/{mine['id']}/pdf/").status_code, 200)

    def test_an_employee_cannot_reach_anyone_elses_pay(self):
        run = self.posted()
        theirs = self.slip(run, self.other)
        mine = self.slip(run, self.me)
        self.assertEqual(self.client_e.get(f"{H}/payroll/payslips/{theirs['id']}/").status_code, 404)  # not even confirmed to exist
        self.assertEqual(self.client_e.get(f"{H}/payroll/payslips/{theirs['id']}/pdf/").status_code, 404)
        self.assertEqual(self.client_e.get(f"{H}/employees/{self.other['id']}/salary/").status_code, 403)
        self.assertEqual(self.client_e.get(f"{H}/employees/{self.other['id']}/payslips/").status_code, 403)
        self.assertEqual(self.client_e.get(f"{H}/employees/{self.other['id']}/advances/").status_code, 403)
        self.assertEqual(self.client_e.get(f"{H}/employees/{self.other['id']}/").status_code, 403)  # no directory access
        self.assertEqual(self.client_e.get(f"{H}/payroll/runs/").status_code, 403)
        self.assertEqual(self.client_e.get(f"{H}/payroll/runs/{run['id']}/").status_code, 403)
        self.assertEqual(self.client_e.patch(f"{H}/payroll/payslips/{mine['id']}/", {"bonus": "99999"}).status_code, 403)
        self.assertEqual(self.client_e.post(f"{H}/employees/{self.me['id']}/salary/", {"effective_from": "2026-09-01", "basic": "99999"}).status_code, 403)
        # but their own pay is open to them
        sal = self.client_e.get(f"{H}/employees/{self.me['id']}/salary/")
        self.assertEqual(sal.status_code, 200)
        self.assertEqual(D(sal.json()["rows"][0]["basic"]), D("15000"))
        own = self.client_e.get(f"{H}/employees/{self.me['id']}/").json()
        self.assertEqual(D(own["current_salary"]["gross"]), D("20000"))
        self.assertEqual(len(self.client_e.get(f"{H}/employees/{self.me['id']}/payslips/").json()), 1)
        # and the other employee sees only theirs
        self.assertEqual([r["id"] for r in self.client_o.get(f"{H}/me/payslips/").json()["rows"]], [theirs["id"]])

    def test_directory_viewers_never_see_pay(self):
        run = self.posted()
        theirs = self.slip(run, self.other)
        self.assertEqual(self.api_pm.get(f"{H}/payroll/payslips/{theirs['id']}/").status_code, 403)
        self.assertEqual(self.api_partner.get(f"{H}/employees/{self.other['id']}/salary/").status_code, 200)  # a partner holds hr.payroll
        self.assertNotIn("current_salary", self.api_pm.get(f"{H}/employees/{self.other['id']}/").json())

    def test_my_profile_is_limited_and_a_user_without_a_record_is_told_so(self):
        me = self.client_e.get(f"{H}/me/").json()
        self.assertEqual((me["linked"], me["name"]), (True, "Mine Test"))
        self.assertEqual(self.api_staff.get(f"{H}/me/").json(), {"linked": False})
        self.assertEqual(self.api_staff.get(f"{H}/me/payslips/").json()["rows"], [])
        ok = self.client_e.patch(f"{H}/me/", {"phone": "9876543210", "address": "12 Lake Road", "emergency_name": "Ravi", "emergency_phone": "9123456780"})
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["phone"], "9876543210")
        for field in ("work_email", "department", "status", "date_of_joining", "pan", "manager", "user_id", "first_name"):
            r = self.client_e.patch(f"{H}/me/", {field: "x"})
            self.assertEqual(r.status_code, 400, field)
        self.assertEqual(self.client_e.patch(f"{H}/me/", {"phone": "bad phone!"}).status_code, 400)
        self.assertEqual(self.api_staff.patch(f"{H}/me/", {"phone": "9876543210"}).status_code, 400)

    def test_dashboard_pay_numbers_need_payroll_permission(self):
        self.posted()
        self.assertIsNotNone(self.fin.get(f"{H}/dashboard/").json()["payroll"])
        self.assertIsNone(self.api_pm.get(f"{H}/dashboard/").json()["payroll"])
        self.assertEqual(self.client_e.get(f"{H}/dashboard/").status_code, 403)


class SettingsTests(PayrollBase):
    def test_settings_are_validated_and_audited(self):
        s = self.fin.get(f"{H}/payroll/settings/").json()
        self.assertEqual((D(s["pf_rate"]), D(s["pf_wage_ceiling"]), D(s["esi_ceiling"]), s["pt_state"]), (D("12"), D("15000"), D("21000"), "Tamil Nadu"))
        for bad in ({"pf_rate": "120"}, {"pf_rate": "abc"}, {"esi_ceiling": "-1"}, {"lop_basis": "x"}, {"weekly_off_days": [7]},
                    {"weekly_off_days": [1, 1]}, {"leave_year_start_month": 13}, {"pt_slabs": []},
                    {"pt_slabs": [{"up_to": "100", "amount": "5"}]},  # the last slab must have no limit
                    {"pt_slabs": [{"up_to": None, "amount": "5"}, {"up_to": None, "amount": "9"}]},
                    {"pt_slabs": [{"up_to": "200", "amount": "5"}, {"up_to": "100", "amount": "9"}, {"up_to": None, "amount": "9"}]}):
            self.assertEqual(self.fin.patch(f"{H}/payroll/settings/", bad).status_code, 400, bad)
        r = self.fin.patch(f"{H}/payroll/settings/", {"pf_wage_ceiling": "20000", "pt_state": "Karnataka",
                                                      "pt_slabs": [{"up_to": "25000", "amount": "0"}, {"up_to": None, "amount": "200"}]})
        self.assertEqual(r.status_code, 200, r.content)
        s = self.slip(self.run_payroll(), self.asha)
        self.assertEqual((D(s["pf_employee"]), D(s["professional_tax"])), (D("2400"), D("200")))  # 12% of 20,000 (new ceiling); new slab
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="payroll_settings", action=Action.UPDATED).exists())


class PayrollPermissionMatrix(PayrollBase):
    def test_run_endpoints_by_role(self):
        clients = {"admin": self.api, "managing_partner": self.api_mp, "partner": self.api_partner, "finance": self.fin, "hr": self.api_hr,
                   "pm": self.api_pm, "employee": self.api_staff}
        allowed = {"admin", "managing_partner", "partner", "finance", "hr"}
        run = self.run_payroll()
        for role, client in clients.items():
            for method, url, body in (("get", f"{H}/payroll/runs/", None), ("get", f"{H}/payroll/runs/{run['id']}/", None),
                                      ("post", f"{H}/payroll/runs/{run['id']}/lock/", {}), ("post", f"{H}/payroll/runs/{run['id']}/unlock/", {}),
                                      ("get", f"{H}/employees/{self.asha['id']}/salary/", None)):
                r = getattr(client, method)(url, body) if body is not None else getattr(client, method)(url)
                if role in allowed:
                    self.assertNotEqual(r.status_code, 403, f"{role} {method} {url}")
                else:
                    self.assertEqual(r.status_code, 403, f"{role} {method} {url}")
        self.assertEqual(self.api_pm.post(f"{H}/payroll/runs/", {"month": "2026-09"}).status_code, 403)
        self.assertEqual(self.anon.get(f"{H}/payroll/runs/").status_code, 401)
        ghost = uuid.uuid4()
        for action in ("lock", "post", "pay", "void", "approve"):
            self.assertEqual(self.fin.post(f"{H}/payroll/runs/{ghost}/{action}/", {}).status_code, 404, action)

    def test_amounts_in_salary_structures_are_validated(self):
        for field, bad in (("basic", "abc"), ("basic", "-5"), ("basic", "100.555"), ("effective", "2020-01-01"), ("effective", "nope")):
            self.assertTrue(self.structure(self.asha, expect=400, effective="2026-09-01", **{field: bad}) if field != "effective"
                            else self.structure(self.asha, expect=400, effective=bad))
        self.assertTrue(self.structure(self.asha, expect=400, effective="2026-09-01", basic="0", hra="0", special="0"))  # nothing to pay
        self.assertEqual(self.api_pm.post(f"{H}/employees/{self.asha['id']}/salary/", {"effective_from": "2026-09-01", "basic": "1"}).status_code, 403)
        row = self.fin.get(f"{H}/employees/{self.asha['id']}/salary/").json()["rows"][0]
        self.assertEqual(self.fin.delete(f"{H}/employees/{self.asha['id']}/salary/{row['id']}/").status_code, 204)
        self.assertEqual(self.fin.delete(f"{H}/employees/{self.asha['id']}/salary/{row['id']}/").status_code, 404)
