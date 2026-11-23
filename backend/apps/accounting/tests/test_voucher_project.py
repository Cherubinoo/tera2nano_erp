"""Expenses and income tagged to a project: validation, the books, project profit and loss and the lists."""
from decimal import Decimal

from apps.accounting.models import Account, JournalEntry, Voucher
from apps.accounting.services import chart
from apps.company.models import CompanyProfile
from apps.sales.tests.test_sales import SalesBase

A = "/api/v1/accounting"
P = "/api/v1/projects"
D = Decimal
ZERO_ID = "00000000-0000-0000-0000-000000000000"


class VoucherProjectTests(SalesBase):
    def setUp(self):
        super().setUp()
        self.company = CompanyProfile.objects.get()
        chart.ensure_chart(self.company)
        self.project = self.make_project("Alpha")

    def make_project(self, name):
        r = self.api.post(f"{P}/projects/", {"name": name, "customer": self.acme["id"], "project_type": "it_solution",
                                             "status": "active", "start_date": "2026-08-01", "planned_end_date": "2026-12-31",
                                             "contract_value": "100000.00", "budget": "60000.00"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def acct(self, key_or_code):
        return Account.objects.get(company=self.company, **({"code": key_or_code} if key_or_code.isdigit() else {"system_key": key_or_code}))

    def expense(self, amount="1000", expect=201, **extra):
        r = self.fin.post(f"{A}/vouchers/", {"kind": "expense", "date": "2026-08-25", "category": str(self.acct("5200").id),
                                             "money_account": str(self.acct("bank").id), "amount": amount, **extra}, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def test_tagged_expense_posts_tagged_lines_and_shows_in_project_profit(self):
        v = self.expense(project=self.project["id"])
        self.assertEqual(v["project"], self.project["id"])
        self.assertEqual((v["project_ref"]["code"], v["project_ref"]["name"]), (self.project["code"], "Alpha"))
        entry = JournalEntry.objects.get(source="expense", source_id=v["id"])
        lines = list(entry.lines.select_related("account"))
        expense_line = next(l for l in lines if l.account.code == "5200")
        self.assertEqual((expense_line.debit, str(expense_line.project_id)), (D("1000.00"), self.project["id"]))
        bank_line = next(l for l in lines if l.account.system_key == "bank")  # the money account stays company-wide
        self.assertEqual((bank_line.credit, bank_line.project_id), (D("1000.00"), None))
        fin = self.api.get(f"{P}/projects/{self.project['id']}/financials/").json()
        self.assertEqual((fin["revenue"], fin["cost"], fin["profit"]), ("0.00", "1000.00", "-1000.00"))
        listed = self.api.get(f"{P}/projects/{self.project['id']}/expenses/").json()
        self.assertEqual((listed["count"], listed["total"]), (1, "1000.00"))
        # an invoice of 1,800 on the same project: profit is 1,800 - 1,000 = 800
        self.invoice(project=self.project["id"])
        fin = self.api.get(f"{P}/projects/{self.project['id']}/financials/").json()
        self.assertEqual((fin["revenue"], fin["cost"], fin["profit"]), ("1800.00", "1000.00", "800.00"))
        # voiding the expense takes its cost out of the project again
        self.assertEqual(self.fin.post(f"{A}/vouchers/{v['id']}/void/", {"change_reason": "wrong"}).status_code, 200)
        fin = self.api.get(f"{P}/projects/{self.project['id']}/financials/").json()
        self.assertEqual((fin["cost"], fin["profit"]), ("0.00", "1800.00"))

    def test_untagged_expense_has_no_project_and_does_not_touch_projects(self):
        v = self.expense()
        self.assertEqual((v["project"], v["project_ref"]), (None, None))
        fin = self.api.get(f"{P}/projects/{self.project['id']}/financials/").json()
        self.assertEqual(fin["cost"], "0.00")

    def test_unknown_malformed_and_cancelled_projects_are_refused(self):
        for bad in (ZERO_ID, "not-a-uuid"):
            r = self.fin.post(f"{A}/vouchers/", {"kind": "expense", "date": "2026-08-25", "category": str(self.acct("5200").id),
                                                 "money_account": str(self.acct("bank").id), "amount": "10", "project": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("project", r.json())
        gone = self.make_project("Dropped")
        self.assertEqual(self.api.post(f"{P}/projects/{gone['id']}/status/", {"status": "cancelled", "reason": "x"}, format="json").status_code, 200)
        self.expense(project=gone["id"], expect=400)
        self.assertEqual(Voucher.objects.count(), 0)

    def test_list_filters_by_project_and_a_draft_can_be_retagged(self):
        other = self.make_project("Beta")
        draft = self.expense(amount="300", project=self.project["id"], record=False)
        self.assertEqual(draft["status"], "draft")
        self.assertFalse(JournalEntry.objects.filter(source="expense").exists())  # nothing posted yet
        self.expense(amount="200", project=other["id"])
        self.expense(amount="50")
        mine = self.fin.get(f"{A}/vouchers/", {"kind": "expense", "project": self.project["id"]}).json()
        self.assertEqual([r["amount"] for r in mine["results"]], ["300.00"])
        self.assertEqual(self.fin.get(f"{A}/vouchers/", {"kind": "expense"}).json()["count"], 3)
        self.assertEqual(self.fin.get(f"{A}/vouchers/", {"project": "nope"}).status_code, 400)
        patched = self.fin.patch(f"{A}/vouchers/{draft['id']}/", {"project": other["id"]}, format="json")
        self.assertEqual((patched.status_code, patched.json()["project"]), (200, other["id"]))
        self.assertEqual(self.fin.patch(f"{A}/vouchers/{draft['id']}/", {"project": ZERO_ID}, format="json").status_code, 400)
        self.assertIsNone(self.fin.patch(f"{A}/vouchers/{draft['id']}/", {"project": None}, format="json").json()["project"])
        rec = self.fin.post(f"{A}/vouchers/{draft['id']}/record/")
        self.assertEqual(rec.status_code, 200, rec.content)
        self.assertIsNone(rec.json()["project"])
