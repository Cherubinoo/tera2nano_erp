"""Budgets: phasing, revisions, status, budget against actual (variance maths, filtering by year, cost centre) and the expense warning."""
import datetime
import uuid
from decimal import Decimal

from django.test import SimpleTestCase

from apps.accounting.models import Budget, BudgetRevision, JournalEntry
from apps.accounting.services import budgets, ledger
from apps.accounts.models import Role
from apps.company.models import CompanyProfileAuditEvent, FinancialYear
from apps.company.tests.base import client_for, make_user

from .test_accounting import A, Base, D

d = datetime.date
GHOST = "00000000-0000-0000-0000-000000000000"
ALL_ROLES = [Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER, Role.HR_MANAGER, Role.PROJECT_MANAGER, Role.EMPLOYEE]
CAN_VIEW = {Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER}
CAN_MANAGE = {Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER}  # both partner roles hold every permission
P1, P2 = str(uuid.UUID(int=101)), str(uuid.UUID(int=102))


class PhasingTests(SimpleTestCase):
    def test_even_phasing_adds_up_to_the_yearly_amount(self):
        # 100,000 / 12 = 8,333.33 (rounded) for eleven months; the twelfth takes the remainder, 8,333.37.
        parts = budgets.even_split(D("100000"))
        self.assertEqual(parts, [D("8333.33")] * 11 + [D("8333.37")])
        self.assertEqual(sum(parts), D("100000.00"))
        self.assertEqual(budgets.even_split(D("12000")), [D("1000.00")] * 12)
        self.assertEqual(sum(budgets.even_split(D("1.00"))), D("1.00"))           # 0.08 x 11 + 0.12
        self.assertEqual(budgets.even_split(D("1.00"))[-1], D("0.12"))
        self.assertEqual(budgets.even_split(D("0")), [D("0.00")] * 12)

    def test_the_report_runs_to_this_month_kept_inside_the_year(self):
        fy = FinancialYear(label="2026-27", start_date=d(2026, 4, 1), end_date=d(2027, 3, 31))
        self.assertEqual(budgets.through_index(fy, None, today=d(2026, 8, 20)), 4)
        self.assertEqual(budgets.through_index(fy, None, today=d(2026, 1, 5)), 0)
        self.assertEqual(budgets.through_index(fy, None, today=d(2029, 1, 5)), 11)
        self.assertEqual(budgets.through_index(fy, "2027-03"), 11)
        self.assertEqual([m.month for m in budgets.fy_months(fy)], [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3])


class BudgetBase(Base):
    def setUp(self):
        super().setUp()
        from apps.projects.models import Project
        for n, pid in enumerate((P1, P2), 1):  # real projects: an expense can only be tagged to one that exists
            Project.objects.create(id=pid, company=self.company, code=f"T2N-PRJ-{n}", name=f"Project {n}", status="active")
        self.fy = str(FinancialYear.objects.get(label="2026-27").pk)
        self.rent, self.software, self.travel = self.acct("5200"), self.acct("5300"), self.acct("5400")

    def line(self, account, annual=None, monthly=None, project=None):
        row = {"account": str(account.id)}
        if monthly is not None:
            row.update(phasing="custom", monthly=monthly)
        else:
            row.update(phasing="even", annual=annual)
        if project:
            row["project"] = project
        return row

    def budget(self, lines=None, name="Plan 2026-27", expect=201, **extra):
        payload = {"name": name, "financial_year": self.fy, "lines": lines if lines is not None else [self.line(self.rent, "120000")], **extra}
        r = self.fin.post(f"{A}/budgets/", payload)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def activate(self, b, expect=200):
        r = self.fin.post(f"{A}/budgets/{b['id']}/status/", {"status": "active"})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def report(self, b, **params):
        r = self.fin.get(f"{A}/reports/budget-vs-actual/", {"budget": b["id"], **params})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def audit(self, action):
        return CompanyProfileAuditEvent.objects.filter(entity_type="budget", action=action)


class BudgetRecordTests(BudgetBase):
    def test_a_budget_with_even_and_month_by_month_lines(self):
        custom = ["1000"] * 6 + ["2000"] * 6                                    # 18,000 for the year
        b = self.budget([self.line(self.rent, "120000"), self.line(self.software, monthly=custom), self.line(self.acct("sales"), "600000")])
        self.assertEqual((b["version"], b["status"], b["total_expense"], b["total_income"], b["line_count"]), (1, "draft", "138000.00", "600000.00", 3))
        lines = {l["account"]["code"]: l for l in b["lines"]}
        self.assertEqual(lines["5200"]["monthly"], ["10000.00"] * 12)
        self.assertEqual((lines["5300"]["annual"], lines["5300"]["phasing"]), ("18000.00", "custom"))
        self.assertEqual(lines["5300"]["monthly"][:7], ["1000.00"] * 6 + ["2000.00"])
        self.assertEqual(sum(D(x) for x in lines["4000"]["monthly"]), D("600000.00"))     # phasing totals
        self.assertEqual([m["label"] for m in b["months"]][:3], ["Apr", "May", "Jun"])
        self.assertEqual([(r["version"], r["reason"]) for r in b["revisions"]], [(1, "First version")])

    def test_uneven_yearly_amounts_still_add_up(self):
        b = self.budget([self.line(self.rent, "100000")])
        monthly = b["lines"][0]["monthly"]
        self.assertEqual((monthly[0], monthly[10], monthly[11]), ("8333.33", "8333.33", "8333.37"))
        self.assertEqual(sum(D(x) for x in monthly), D("100000.00"))

    def test_validation(self):
        rent = self.line(self.rent, "1000")
        bad = [
            {"name": ""}, {"financial_year": GHOST}, {"financial_year": ""}, {"lines": "nope"},
            {"lines": [rent, rent]},
            {"lines": [self.line(self.acct("bank"), "10")]},                     # a bank account is neither income nor expense
            {"lines": [{"account": GHOST, "phasing": "even", "annual": "1"}]},
            {"lines": [self.line(self.rent, "-5")]}, {"lines": [self.line(self.rent, "1.234")]}, {"lines": [self.line(self.rent, "abc")]},
            {"lines": [self.line(self.rent, monthly=["1"] * 11)]}, {"lines": [self.line(self.rent, monthly=["1"] * 11 + ["-1"])]},
            {"lines": [{"account": str(self.rent.id), "phasing": "weekly", "annual": "1"}]},
            {"lines": [{"account": str(self.rent.id), "project": "not-a-uuid", "annual": "1"}]},
        ]
        for over in bad:
            r = self.fin.post(f"{A}/budgets/", {"name": "B", "financial_year": self.fy, "lines": [rent], **over})
            self.assertEqual(r.status_code, 400, (over, r.content))
        self.assertEqual(Budget.objects.count(), 0)
        self.budget(name="Same")
        self.budget(name="same", expect=400)                                      # one name per year

    def test_same_account_can_have_a_line_per_cost_centre(self):
        b = self.budget([self.line(self.rent, "1200"), self.line(self.rent, "600", project=P1), self.line(self.rent, "300", project=P2)])
        self.assertEqual(b["line_count"], 3)
        self.budget([self.line(self.rent, "1", project=P1), self.line(self.rent, "2", project=P1)], name="dup", expect=400)

    def test_list_and_options(self):
        self.budget()
        self.budget(name="Second")
        rows = self.fin.get(f"{A}/budgets/").json()
        self.assertEqual(sorted(r["name"] for r in rows), ["Plan 2026-27", "Second"])
        self.assertEqual(self.fin.get(f"{A}/budgets/", {"status": "active"}).json(), [])
        o = self.fin.get(f"{A}/budgets/options/").json()
        self.assertIn("2026-27", [f["label"] for f in o["financial_years"]])
        self.assertTrue(all(a["type"] in ("income", "expense") for a in o["accounts"]))


class RevisionAndStatusTests(BudgetBase):
    def test_changing_the_lines_saves_a_new_version(self):
        b = self.budget()
        r = self.fin.patch(f"{A}/budgets/{b['id']}/", {"lines": [self.line(self.rent, "120000")]})           # same lines: nothing new
        self.assertEqual(r.json()["version"], 1)
        r = self.fin.patch(f"{A}/budgets/{b['id']}/", {"name": "Renamed", "notes": "n"})
        self.assertEqual((r.json()["version"], r.json()["name"]), (1, "Renamed"))
        r = self.fin.patch(f"{A}/budgets/{b['id']}/", {"lines": [self.line(self.rent, "144000"), self.line(self.software, "6000")],
                                                       "change_reason": "Rent went up"})
        self.assertEqual((r.status_code, r.json()["version"], r.json()["total_expense"]), (200, 2, "150000.00"))
        revs = self.fin.get(f"{A}/budgets/{b['id']}/revisions/").json()
        self.assertEqual([(x["version"], x["total_expense"], x["lines"], x["reason"]) for x in revs],
                         [(2, "150000.00", 2, "Rent went up"), (1, "120000.00", 1, "First version")])
        old = self.fin.get(f"{A}/budgets/{b['id']}/revisions/1/").json()
        self.assertEqual((old["snapshot"][0]["code"], old["snapshot"][0]["annual"]), ("5200", "120000.00"))
        self.assertEqual(self.fin.get(f"{A}/budgets/{b['id']}/revisions/99/").status_code, 404)
        self.assertEqual(BudgetRevision.objects.filter(budget_id=b["id"]).count(), 2)

    def test_an_active_budget_needs_a_reason_to_be_revised_and_a_closed_one_cannot_change(self):
        b = self.budget()
        self.activate(b)
        r = self.fin.patch(f"{A}/budgets/{b['id']}/", {"lines": [self.line(self.rent, "130000")]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("change_reason", r.json())
        r = self.fin.patch(f"{A}/budgets/{b['id']}/", {"lines": [self.line(self.rent, "130000")], "change_reason": "Mid-year review"})
        self.assertEqual((r.status_code, r.json()["version"]), (200, 2))
        self.assertEqual(self.fin.patch(f"{A}/budgets/{b['id']}/", {"financial_year": GHOST}).status_code, 400)
        self.fin.post(f"{A}/budgets/{b['id']}/status/", {"status": "closed"})
        r = self.fin.patch(f"{A}/budgets/{b['id']}/", {"name": "x"})
        self.assertEqual(r.status_code, 400)

    def test_status_rules(self):
        empty = self.budget(lines=[], name="Empty")
        self.activate(empty, expect=400)                                          # nothing to budget yet
        first, second = self.budget(name="First"), self.budget(name="Second")
        self.assertEqual(self.activate(first)["status"], "active")
        r = self.activate(second, expect=400)
        self.assertIn("already the active budget", str(r["status"]))
        self.assertEqual(self.fin.delete(f"{A}/budgets/{first['id']}/").status_code, 400)       # only a draft can be deleted
        self.assertEqual(self.fin.post(f"{A}/budgets/{first['id']}/status/", {"status": "draft"}).status_code, 400)
        self.assertEqual(self.fin.post(f"{A}/budgets/{first['id']}/status/", {"status": "closed"}).json()["status"], "closed")
        self.assertEqual(self.activate(second)["status"], "active")
        self.activate(first, expect=400)                                          # a closed budget cannot reopen beside another active one
        self.assertEqual(self.fin.post(f"{A}/budgets/{first['id']}/status/", {"status": "nonsense"}).status_code, 400)
        self.assertEqual(self.fin.delete(f"{A}/budgets/{empty['id']}/").status_code, 204)
        self.assertEqual(Budget.objects.count(), 2)

    def test_every_change_is_audited(self):
        b = self.budget()
        self.fin.patch(f"{A}/budgets/{b['id']}/", {"lines": [self.line(self.rent, "1")], "change_reason": "cut"})
        self.activate(b)
        other = self.budget(name="Scrap")
        self.fin.delete(f"{A}/budgets/{other['id']}/")
        self.assertEqual((self.audit("created").count(), self.audit("deleted").count()), (2, 1))
        self.assertEqual(self.audit("updated").count(), 2)                        # a revision and the activation
        self.assertTrue(self.audit("updated").filter(new_values__status="active").exists())
        self.assertEqual(self.audit("updated").filter(reason="cut").count(), 1)


class BudgetVsActualTests(BudgetBase):
    def setUp(self):
        super().setUp()
        # Rent 120,000 (10,000 a month), software 12,000 (1,000 a month), sales 600,000 (50,000 a month).
        self.b = self.budget([self.line(self.rent, "120000"), self.line(self.software, "12000"), self.line(self.acct("sales"), "600000")])
        self.expense("5200", amount="10000", date="2026-04-10")
        self.expense("5200", amount="15000", date="2026-05-10")
        self.journal([("bank", {"debit": "50000"}), ("sales", {"credit": "50000"})], date="2026-04-20")

    def rows(self, rep, kind):
        return {r["account"]["code"]: r for r in rep[kind]["rows"]}

    def test_variance_maths_for_expenses_and_income(self):
        rep = self.report(self.b, through="2026-05")
        rent = self.rows(rep, "expense")["5200"]
        # to May: budget 20,000, actual 25,000 -> 5,000 over: variance -5,000.00 = -25.00% of the budget
        self.assertEqual((rent["budget_total"], rent["budget_to_date"], rent["actual_to_date"]), ("120000.00", "20000.00", "25000.00"))
        self.assertEqual((rent["variance"], rent["variance_pct"], rent["over"], rent["remaining"]), ("-5000.00", "-25.00", True, "95000.00"))
        sw = self.rows(rep, "expense")["5300"]
        self.assertEqual((sw["actual_to_date"], sw["variance"], sw["variance_pct"], sw["over"]), ("0.00", "2000.00", "100.00", False))
        # income: budget 100,000 to May, actual 50,000: short of target by 50,000 (variance = actual - budget)
        sales = self.rows(rep, "income")["4000"]
        self.assertEqual((sales["budget_to_date"], sales["actual_to_date"], sales["variance"], sales["variance_pct"], sales["over"]),
                         ("100000.00", "50000.00", "-50000.00", "-50.00", True))
        t = rep["expense"]["totals"]
        self.assertEqual((t["budget_total"], t["budget_to_date"], t["actual_to_date"], t["variance"], t["variance_pct"]),
                         ("132000.00", "22000.00", "25000.00", "-3000.00", "-13.64"))
        self.assertEqual((rep["income"]["totals"]["variance"], rep["through"]), ("-50000.00", "2026-05"))

    def test_an_income_line_above_target_is_favourable(self):
        self.journal([("bank", {"debit": "70000"}), ("sales", {"credit": "70000"})], date="2026-05-20")
        rep = self.report(self.b, through="2026-05")
        sales = self.rows(rep, "income")["4000"]
        self.assertEqual((sales["actual_to_date"], sales["variance"], sales["variance_pct"], sales["over"]), ("120000.00", "20000.00", "20.00", False))

    def test_month_by_month_and_cumulative(self):
        rep = self.report(self.b, through="2026-06")
        m = rep["expense"]["monthly"]
        self.assertEqual(len(m), 12)
        # budget 11,000 a month (rent 10,000 + software 1,000)
        self.assertEqual((m[0]["budget"], m[0]["actual"], m[0]["variance"], m[0]["over"]), ("11000.00", "10000.00", "1000.00", False))
        self.assertEqual((m[1]["actual"], m[1]["variance"], m[1]["over"]), ("15000.00", "-4000.00", True))
        self.assertEqual((m[1]["cum_budget"], m[1]["cum_actual"], m[1]["cum_variance"]), ("22000.00", "25000.00", "-3000.00"))
        self.assertEqual((m[2]["actual"], m[2]["cum_actual"], m[2]["cum_budget"]), ("0.00", "25000.00", "33000.00"))
        self.assertEqual(m[0]["month"], "2026-04")
        rent = self.rows(rep, "expense")["5200"]
        self.assertEqual(rent["actual_months"][:3], ["10000.00", "15000.00", "0.00"])
        self.assertEqual(len(rent["budget_months"]), 12)
        inc = rep["income"]["monthly"]
        self.assertEqual((inc[0]["budget"], inc[0]["actual"], inc[0]["cum_variance"]), ("50000.00", "50000.00", "0.00"))

    def test_only_the_budgets_financial_year_counts(self):
        self.expense("5200", amount="99999", date="2027-04-05")                  # next year
        self.expense("5200", amount="88888", date="2026-03-20")                  # last year
        self.journal([("bank", {"debit": "7000"}), ("sales", {"credit": "7000"})], date="2027-04-06")
        rep = self.report(self.b, through="2027-03")
        self.assertEqual(self.rows(rep, "expense")["5200"]["actual_to_date"], "25000.00")
        self.assertEqual(self.rows(rep, "income")["4000"]["actual_to_date"], "50000.00")
        self.assertEqual(rep["through_index"], 11)
        self.assertEqual(rep["budget"]["financial_year"], "2026-27")

    def test_a_reversed_expense_nets_out(self):
        v = self.expense("5200", amount="5000", date="2026-06-02")
        rep = self.report(self.b, through="2026-06")
        self.assertEqual(self.rows(rep, "expense")["5200"]["actual_to_date"], "30000.00")
        entry = JournalEntry.objects.get(source="expense", source_id=v["id"])
        ledger.reverse(entry, self.admin, "mistake", on_date=d(2026, 6, 10))       # a reversal in the same month
        rep = self.report(self.b, through="2026-06")
        self.assertEqual(self.rows(rep, "expense")["5200"]["actual_to_date"], "25000.00")

    def test_spending_with_no_budget_line_is_listed_separately(self):
        self.expense("5400", amount="700", date="2026-04-12")
        rep = self.report(self.b, through="2026-05")
        u = rep["unbudgeted"]
        self.assertEqual([(r["account"]["code"], r["actual_to_date"]) for r in u["rows"]], [("5400", "700.00")])
        self.assertEqual((u["total_expense"], u["total_income"]), ("700.00", "0.00"))
        self.assertNotIn("5400", self.rows(rep, "expense"))

    def test_by_project_cost_centre_and_the_project_filter(self):
        b = self.budget([self.line(self.software, "24000"), self.line(self.software, "12000", project=P1)], name="By project")
        self.expense("5300", amount="1500", date="2026-04-05")                    # no project
        self.expense("5300", amount="500", date="2026-04-06", project=P1)
        self.expense("5300", amount="300", date="2026-04-07", project=P2)         # another project: counted with the company-wide line
        rep = self.report(b, through="2026-04")
        sw = self.rows(rep, "expense")["5300"]
        # whole account: budget 36,000 / 12 = 3,000 for April; actual 1,500 + 500 + 300 = 2,300
        self.assertEqual((sw["budget_to_date"], sw["actual_to_date"], sw["variance"]), ("3000.00", "2300.00", "700.00"))
        self.assertEqual(len(sw["cost_centres"]), 1)
        by = rep["expense"]["by_project"]
        self.assertEqual(len(by), 2)
        company_wide = next(r for r in by if r["project"] is None)
        p1 = next(r for r in by if r["project"] and r["project"]["id"] == P1)
        self.assertEqual((company_wide["budget_to_date"], company_wide["actual_to_date"], company_wide["variance"]), ("2000.00", "1800.00", "200.00"))
        self.assertEqual((p1["budget_to_date"], p1["actual_to_date"], p1["variance"], p1["variance_pct"]), ("1000.00", "500.00", "500.00", "50.00"))
        only = self.report(b, through="2026-04", project=P1)
        self.assertEqual([(r["account"]["code"], r["budget_to_date"], r["actual_to_date"]) for r in only["expense"]["rows"]], [("5300", "1000.00", "500.00")])
        self.assertEqual(len(only["expense"]["by_project"]), 1)
        self.assertEqual(only["project"], P1)
        self.assertEqual(self.fin.get(f"{A}/reports/budget-vs-actual/", {"budget": b["id"], "project": "bad"}).status_code, 400)

    def test_an_account_with_only_cost_centre_lines_shows_other_spend_as_unbudgeted(self):
        b = self.budget([self.line(self.software, "12000", project=P1)], name="Only P1")
        self.expense("5300", amount="400", date="2026-04-06", project=P1)
        self.expense("5300", amount="250", date="2026-04-07")
        rep = self.report(b, through="2026-04")
        self.assertEqual(self.rows(rep, "expense")["5300"]["actual_to_date"], "400.00")
        self.assertEqual([(r["account"]["code"], r["actual_to_date"], r["project"]) for r in rep["unbudgeted"]["rows"]
                          if r["account"]["code"] == "5300"], [("5300", "250.00", None)])

    def test_the_default_budget_is_the_active_one_and_missing_budgets_are_404(self):
        r = self.fin.get(f"{A}/reports/budget-vs-actual/")
        self.assertEqual(r.status_code, 404)                                       # nothing active
        self.assertEqual(self.fin.get(f"{A}/reports/budget-vs-actual/", {"budget": GHOST}).status_code, 404)
        self.assertEqual(self.fin.get(f"{A}/reports/budget-vs-actual/", {"budget": self.b["id"], "through": "May"}).status_code, 400)


class ExpenseWarningTests(BudgetBase):
    """Rent is budgeted at 12,000 for the year. Going past that gives a warning on the expense; the expense is still saved."""

    def setUp(self):
        super().setUp()
        self.b = self.budget([self.line(self.rent, "12000")])
        self.activate(self.b)

    def test_within_the_budget_there_is_no_warning(self):
        v = self.expense("5200", amount="8000", date="2026-05-01")
        self.assertIsNone(v["budget_warning"])
        v = self.expense("5200", amount="4000", date="2026-06-01")                 # exactly 12,000 in all
        self.assertIsNone(v["budget_warning"])

    def test_going_over_warns_but_does_not_block(self):
        self.expense("5200", amount="8000", date="2026-05-01")
        v = self.expense("5200", amount="5000", date="2026-06-01")
        w = v["budget_warning"]
        self.assertEqual((v["status"], w["spent_so_far"], w["this_amount"], w["projected"], w["over_by"], w["budget_amount"]),
                         ("recorded", "8000.00", "5000.00", "13000.00", "1000.00", "12000.00"))
        self.assertIn("Over budget", w["message"])
        self.assertIn("1,000.00 over", w["message"])
        self.assertEqual(self.bal("5200"), D("13000"))                              # it was posted all the same
        self.tb()

    def test_a_single_large_expense_warns_too(self):
        v = self.expense("5200", amount="12000.01", date="2026-05-01")
        self.assertEqual((v["budget_warning"]["over_by"], v["status"]), ("0.01", "recorded"))

    def test_only_an_active_budget_for_that_year_is_checked(self):
        self.fin.post(f"{A}/budgets/{self.b['id']}/status/", {"status": "closed"})
        self.assertIsNone(self.expense("5200", amount="50000", date="2026-05-01")["budget_warning"])
        draft = self.budget([self.line(self.travel, "100")], name="Draft")
        self.assertIsNone(self.expense("5400", amount="5000", date="2026-05-01")["budget_warning"])
        self.activate(draft)
        self.assertIsNotNone(self.expense("5400", amount="5000", date="2026-05-02")["budget_warning"])
        self.assertIsNone(self.expense("5400", amount="5000", date="2027-05-01")["budget_warning"])   # next year: no budget

    def test_accounts_without_a_line_and_income_are_not_checked(self):
        self.assertIsNone(self.expense("5600", amount="999999", date="2026-05-01")["budget_warning"])
        r = self.fin.post(f"{A}/vouchers/", {"kind": "income", "date": "2026-05-01", "category": str(self.acct("other_income").id),
                                             "money_account": str(self.acct("bank").id), "amount": "100"})
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(r.json()["budget_warning"])

    def test_a_project_line_is_checked_for_that_projects_expenses(self):
        both = self.budget([self.line(self.software, "12000"), self.line(self.software, "1000", project=P1)], name="Both")
        self.fin.post(f"{A}/budgets/{self.b['id']}/status/", {"status": "closed"})
        self.activate(both)
        v = self.expense("5300", amount="1500", date="2026-05-01", project=P1)
        self.assertEqual((v["budget_warning"]["budget_amount"], v["budget_warning"]["over_by"]), ("1000.00", "500.00"))
        self.assertIn("T2N-PRJ-1 Project 1", v["budget_warning"]["message"])
        self.assertIsNone(self.expense("5300", amount="1500", date="2026-05-02")["budget_warning"])   # company-wide line is 12,000
        self.assertIsNone(self.expense("5300", amount="100", date="2026-05-03", project=P2)["budget_warning"])  # falls back to the company-wide line

    def test_the_check_endpoint_for_the_form(self):
        rent = str(self.rent.id)
        r = self.fin.get(f"{A}/budgets/check/", {"account": rent, "amount": "13000", "date": "2026-05-01"})
        self.assertEqual((r.status_code, r.json()["warning"]["over_by"]), (200, "1000.00"))
        self.assertIsNone(self.fin.get(f"{A}/budgets/check/", {"account": rent, "amount": "100", "date": "2026-05-01"}).json()["warning"])
        self.expense("5200", amount="11950", date="2026-05-01")
        self.assertEqual(self.fin.get(f"{A}/budgets/check/", {"account": rent, "amount": "100", "date": "2026-05-02"}).json()["warning"]["over_by"], "50.00")
        self.assertIsNone(self.fin.get(f"{A}/budgets/check/", {"account": str(self.acct("sales").id), "amount": "100"}).json()["warning"])
        self.assertIsNone(self.fin.get(f"{A}/budgets/check/", {"account": GHOST, "amount": "100"}).json()["warning"])
        self.assertEqual(self.fin.get(f"{A}/budgets/check/", {"account": rent, "amount": "abc"}).status_code, 400)
        self.assertEqual(self.fin.get(f"{A}/budgets/check/", {"account": rent, "amount": "1", "date": "31/05/2026"}).status_code, 400)
        self.assertEqual(client_for(make_user("emp9", Role.EMPLOYEE)).get(f"{A}/budgets/check/").status_code, 403)

    def test_an_expense_held_for_approval_or_kept_as_a_draft_still_gets_the_warning_without_double_counting(self):
        r = self.fin.post(f"{A}/vouchers/", {"kind": "expense", "date": "2026-05-01", "category": str(self.rent.id),
                                             "money_account": str(self.acct("bank").id), "amount": "13000", "record": False})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["budget_warning"]["spent_so_far"], "0.00")        # nothing posted yet, so nothing counted yet
        self.assertEqual(r.json()["budget_warning"]["projected"], "13000.00")


class BudgetAccessTests(BudgetBase):
    def test_every_role_sees_or_is_refused(self):
        b = self.budget()
        urls = ["budgets/", "budgets/options/", f"budgets/{b['id']}/", f"budgets/{b['id']}/revisions/", f"budgets/{b['id']}/revisions/1/",
                f"reports/budget-vs-actual/?budget={b['id']}", "budgets/check/"]
        for role in ALL_ROLES:
            api = client_for(make_user(f"v_{role}", role))
            for url in urls:
                r = api.get(f"{A}/{url}")
                self.assertEqual(r.status_code, 200 if role in CAN_VIEW else 403, (role, url, r.content))

    def test_only_managers_write(self):
        b = self.budget()
        for role in ALL_ROLES:
            api = client_for(make_user(f"m_{role}", role))
            ok = role in CAN_MANAGE
            mine = self.budget(name=f"For {role}")
            for method, url, body, success in [
                    ("post", f"{A}/budgets/", {"name": f"New {role}", "financial_year": self.fy, "lines": []}, 201),
                    ("patch", f"{A}/budgets/{mine['id']}/", {"notes": "x"}, 200),
                    ("post", f"{A}/budgets/{mine['id']}/status/", {"status": "active"}, 200),
                    ("delete", f"{A}/budgets/{b['id']}/", None, 204)]:
                r = getattr(api, method)(url, body, format="json") if body is not None else getattr(api, method)(url)
                self.assertEqual(r.status_code, success if ok else 403, (role, method, url, r.content))
                if ok and method == "post" and url.endswith("/status/"):
                    self.fin.post(f"{A}/budgets/{mine['id']}/status/", {"status": "closed"})
                if ok and method == "delete":
                    b = self.budget(name=f"Again {role}")
        self.assertEqual(self.fin.get(f"{A}/budgets/{GHOST}/").status_code, 404)

    def test_unknown_ids_are_404(self):
        for method, url in [("get", f"budgets/{GHOST}/"), ("patch", f"budgets/{GHOST}/"), ("delete", f"budgets/{GHOST}/"),
                            ("post", f"budgets/{GHOST}/status/"), ("get", f"budgets/{GHOST}/revisions/"),
                            ("get", f"budgets/{GHOST}/revisions/1/")]:
            r = getattr(self.fin, method)(f"{A}/{url}") if method in ("get", "delete") else getattr(self.fin, method)(f"{A}/{url}", {}, format="json")
            self.assertEqual(r.status_code, 404, (method, url))
