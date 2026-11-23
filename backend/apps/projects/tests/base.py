import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounting.models import Account
from apps.accounting.services import chart
from apps.accounts.models import Role
from apps.company.models import CompanyProfile
from apps.company.tests.base import CompanyTestMixin, client_for, make_user

P = "/api/v1/projects"
S = "/api/v1/sales"
D = Decimal
TODAY = datetime.date.today()


def iso(d):
    return d.isoformat()


class ProjectsBase(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.company = CompanyProfile.objects.get()
        chart.ensure_chart(self.company)
        self.pm_user = make_user("pm", Role.PROJECT_MANAGER)
        self.hr_user = make_user("hr", Role.HR_MANAGER)
        self.partner_user = make_user("partner", Role.PARTNER)
        self.managing_user = make_user("managing", Role.MANAGING_PARTNER)
        self.pm = client_for(self.pm_user)
        self.hr = client_for(self.hr_user)
        self.partner = client_for(self.partner_user)
        self.managing = client_for(self.managing_user)
        self.acme = self.customer("Acme Industries")

    # -- builders
    def customer(self, name, **over):
        r = self.api_finance.post(f"{S}/customers/", {"display_name": name, **over})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def project(self, client=None, expect=201, **over):
        payload = {"name": "Alpha", "customer": self.acme["id"], "project_type": "it_solution", "status": "active",
                   "start_date": "2026-08-01", "planned_end_date": "2026-12-31", "contract_value": "100000.00",
                   "budget": "60000.00", **over}
        r = (client or self.pm).post(f"{P}/projects/", payload, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def milestone(self, project, name="Design", due="2099-01-01", expect=201, **over):
        r = self.pm.post(f"{P}/milestones/", {"project": project["id"], "name": name, "due_date": due, **over}, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def task(self, project, title="Build", expect=201, **over):
        r = self.pm.post(f"{P}/tasks/", {"project": project["id"], "title": title, **over}, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def entry(self, project, hours="2", expect=201, **over):
        payload = {"project": project["id"], "date": "2026-08-10", "hours": hours, "employee_name": "Asha", **over}
        r = self.pm.post(f"{P}/timesheets/", payload, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def get(self, project):
        r = self.pm.get(f"{P}/projects/{project['id']}/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def acct(self, code):
        return Account.objects.get(company=self.company, code=code)

    def post_books(self, project, *, revenue=None, expenses=(), date=datetime.date(2026, 8, 15), source="manual", source_id=""):
        """Journal entries tagged to the project: a sale (Dr receivable, Cr sales) and expenses (Dr expense account, Cr bank)."""
        from apps.accounting.services import ledger
        pid = project["id"]
        if revenue:
            ledger.post(self.company, date=date, lines=[
                {"account": "receivable", "debit": D(revenue), "project": pid},
                {"account": "sales", "credit": D(revenue), "project": pid}], narration="Sale")
        for code, amount in expenses:
            ledger.post(self.company, date=date, lines=[
                {"account": self.acct(code), "debit": D(amount), "project": pid, "memo": f"Cost {code}"},
                {"account": "bank", "credit": D(amount)}], narration=f"Expense {code}")
