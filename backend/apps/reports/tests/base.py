"""Shared set-up for the report tests: one company, a user and client for every role, and small builders that put known
figures in the books so every report total can be worked out by hand."""
import datetime
from decimal import Decimal

from apps.accounting.services import ledger
from apps.accounts.models import Role
from apps.company.models import CompanyProfile
from apps.company.tests.base import client_for, make_gstin, make_user
from apps.sales.tests.test_sales import ITEMS, SalesBase

R = "/api/v1/reports"
D = Decimal
TODAY = datetime.date.today()
ROLES = [Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER, Role.HR_MANAGER, Role.PROJECT_MANAGER, Role.EMPLOYEE]
ALL_REPORTS = {"revenue", "project-profitability", "partner-capital", "salary", "receivables", "vendor-outstanding", "gst", "cash-flow"}
# Which reports each role may open: reports.view plus the source module's permission.
EXPECTED = {
    Role.ADMIN: ALL_REPORTS,
    Role.MANAGING_PARTNER: ALL_REPORTS,
    Role.PARTNER: ALL_REPORTS - {"salary"},
    Role.FINANCE_MANAGER: ALL_REPORTS,
    Role.HR_MANAGER: {"salary", "project-profitability"},
    Role.PROJECT_MANAGER: {"receivables", "vendor-outstanding", "project-profitability"},
    Role.EMPLOYEE: set(),
}


def iso(d):
    return d.isoformat()


def ago(days):
    return TODAY - datetime.timedelta(days=days)


class ReportsBase(SalesBase):
    def setUp(self):
        super().setUp()
        self.company = CompanyProfile.objects.get()
        self.clients = {role: client_for(make_user(f"u_{role}", role)) for role in ROLES}

    # -- helpers
    def get(self, key, role=Role.ADMIN, expect=200, **params):
        r = self.clients[role].get(f"{R}/{key}/", params)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json() if r["Content-Type"].startswith("application/json") else r

    def export(self, key, role=Role.ADMIN, expect=200, **params):
        r = self.clients[role].get(f"{R}/{key}/export/", params)
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def book(self, on, debit, credit, amount, project=None):
        """One balanced entry: debit one account and credit another for `amount`."""
        tag = {"project": project} if project else {}
        ledger.post(self.company, date=datetime.date.fromisoformat(on) if isinstance(on, str) else on, actor=self.admin,
                    lines=[{"account": debit, "debit": D(amount), **tag}, {"account": credit, "credit": D(amount), **tag}])

    def vendor(self, name="Local Supplies", state="29", pan="AAAPA1234A"):
        r = self.fin.post("/api/v1/purchases/vendors/", {"name": name, "gst_treatment": "registered", "gstin": make_gstin(state, pan)})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def bill(self, vendor, bill_date, due_date, rate="1000", no="VB-1"):
        items = [{"description": "Servers", "quantity": "1", "rate": rate, "gst_rate": "18", "hsn_sac": "8473"}]
        r = self.fin.post("/api/v1/purchases/bills/", {"vendor": vendor["id"], "vendor_bill_no": no, "bill_date": iso(bill_date),
                                                        "due_date": iso(due_date), "items": items, "record": True})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def project(self, name="Alpha", **over):
        r = self.api.post("/api/v1/projects/projects/", {"name": name, "customer": self.acme["id"], "project_type": "it_solution",
                                                         "status": "active", "start_date": "2026-08-01", "planned_end_date": "2026-12-31",
                                                         "contract_value": "100000.00", "budget": "60000.00", **over}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def payroll(self, month, status, rows):
        """A payroll run with one payslip per (first name, department, gross, deductions, employer extra)."""
        from apps.hr.models import Employee, Payslip, PayrollRun
        run = PayrollRun.objects.create(company=self.company, month=month, status=status)
        for name, dept, gross, ded, extra in rows:
            emp = Employee.objects.filter(company=self.company, first_name=name).first() or Employee.objects.create(
                company=self.company, first_name=name, code=f"E-{name}", date_of_joining=datetime.date(2025, 1, 1))
            Payslip.objects.create(run=run, employee=emp, month=month, who={"department": dept, "name": name}, gross=D(gross),
                                   total_deductions=D(ded), net_pay=D(gross) - D(ded), employer_cost=D(gross) + D(extra))
        return run
