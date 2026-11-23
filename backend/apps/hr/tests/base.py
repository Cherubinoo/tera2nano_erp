import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounting.models import Account
from apps.accounting.services import chart
from apps.accounts.models import Role
from apps.company.models import CompanyProfile
from apps.company.tests.base import CompanyTestMixin, client_for, make_user
from apps.hr.models import Employee

H = "/api/v1/hr"
A = "/api/v1/accounting"
D = Decimal
ACCOUNT = "123456789012"
PAN = "ABCDE1234F"


class HRBase(CompanyTestMixin, TestCase):
    """A configured company plus one user per role. Fixed dates in August 2026 (a 31-day month; Sunday is the weekly off)."""

    def setUp(self):
        super().setUp()
        self.full_company()
        self.company = CompanyProfile.objects.get()
        chart.ensure_chart(self.company)
        self.hr = make_user("hrm", Role.HR_MANAGER)
        self.pm = make_user("pm", Role.PROJECT_MANAGER)
        self.partner = make_user("partner", Role.PARTNER)
        self.mp = make_user("mp", Role.MANAGING_PARTNER)
        self.api_hr, self.api_pm, self.api_partner, self.api_mp = (client_for(u) for u in (self.hr, self.pm, self.partner, self.mp))
        self.fin = self.api_finance
        self.seq = 0

    # -- builders
    def dept(self, name="Engineering", client=None):
        r = (client or self.api_hr).post(f"{H}/departments/", {"name": name})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def desig(self, name="Developer"):
        r = self.api_hr.post(f"{H}/designations/", {"name": name})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def emp(self, first="Asha", last="Rao", user=None, expect=201, **over):
        payload = {"first_name": first, "last_name": last, "date_of_joining": "2025-04-01", **over}
        if user is not None:
            payload["user_id"] = str(user.pk)
        r = self.api_hr.post(f"{H}/employees/", payload)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def structure(self, emp, basic="20000", hra="8000", special="12000", other="0", effective="2025-04-01", expect=201, **over):
        r = self.fin.post(f"{H}/employees/{emp['id']}/salary/", {
            "effective_from": effective, "basic": basic, "hra": hra, "special_allowance": special, "other_allowance": other, **over})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def employee_user(self, username, role=Role.EMPLOYEE, **over):
        u = make_user(username, role)
        e = self.emp(username.capitalize(), "Test", user=u, **over)
        return u, client_for(u), e

    def leave_type(self, code="CL", name="Casual leave", quota="12", paid=True, **over):
        r = self.api_hr.post(f"{H}/leave/types/", {"code": code, "name": name, "annual_quota": quota, "is_paid": paid, **over})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def trial_balance(self, as_of="2027-03-31"):
        r = self.fin.get(f"{A}/reports/trial-balance/", {"as_of": as_of}).json()
        self.assertTrue(r["balanced"], r)
        return {row["name"]: D(row["debit"]) - D(row["credit"]) for row in r["rows"]}

    def run_payroll(self, month="2026-08", expect=201):
        r = self.fin.post(f"{H}/payroll/runs/", {"month": month})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def step(self, run, action, expect=200, client=None, **data):
        r = (client or self.fin).post(f"{H}/payroll/runs/{run['id']}/{action}/", data)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()
