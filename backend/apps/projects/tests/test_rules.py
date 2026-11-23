import datetime
from decimal import Decimal

from django.test import SimpleTestCase

from apps.accounting.models import JournalEntry
from apps.accounting.services import ledger
from apps.projects import services
from apps.projects.models import Milestone, Project, Task

from .base import D, P, S, TODAY, ProjectsBase, iso

ITEMS = [{"description": "Development", "quantity": "2", "rate": "1000", "discount_percent": "10"}]  # 1,800.00


class HealthRuleTests(SimpleTestCase):
    """Fixed dates; the rules never look at the real date."""

    def proj(self, **kw):
        base = dict(status="active", start_date=datetime.date(2026, 1, 1), planned_end_date=datetime.date(2026, 1, 11))
        return Project(**{**base, **kw})

    def test_schedule_health_against_elapsed_share(self):
        p = self.proj()
        half = datetime.date(2026, 1, 6)  # 5 of 10 days gone = 50% expected
        self.assertEqual(services.schedule_health(p, 50, 0, half), "on_track")
        self.assertEqual(services.schedule_health(p, 40, 0, half), "on_track")   # gap 10 is still fine
        self.assertEqual(services.schedule_health(p, 39, 0, half), "at_risk")     # gap 11
        self.assertEqual(services.schedule_health(p, 25, 0, half), "at_risk")     # gap 25
        self.assertEqual(services.schedule_health(p, 24, 0, half), "delayed")     # gap 26
        self.assertEqual(services.schedule_health(p, 99, 0, datetime.date(2026, 1, 12)), "delayed")  # past the end
        self.assertEqual(services.schedule_health(p, 100, 0, datetime.date(2026, 1, 12)), "on_track")
        self.assertEqual(services.schedule_health(p, 60, 0, datetime.date(2025, 12, 1)), "on_track")  # not started yet

    def test_overdue_milestone_makes_it_at_risk_but_not_worse_by_itself(self):
        p = self.proj()
        self.assertEqual(services.schedule_health(p, 50, 1, datetime.date(2026, 1, 6)), "at_risk")
        self.assertEqual(services.schedule_health(p, 10, 1, datetime.date(2026, 1, 6)), "delayed")  # the gap still counts
        self.assertEqual(services.schedule_health(self.proj(planned_end_date=None), 0, 2, datetime.date(2026, 1, 6)), "at_risk")
        self.assertEqual(services.schedule_health(self.proj(planned_end_date=None), 0, 0, datetime.date(2026, 1, 6)), "on_track")

    def test_finished_projects_are_judged_on_the_end_date(self):
        done = self.proj(status="completed", actual_end_date=datetime.date(2026, 1, 11))
        self.assertEqual(services.schedule_health(done, 100, 0, datetime.date(2026, 6, 1)), "on_track")
        late = self.proj(status="completed", actual_end_date=datetime.date(2026, 1, 12))
        self.assertEqual(services.schedule_health(late, 100, 0, datetime.date(2026, 6, 1)), "delayed")
        self.assertEqual(services.schedule_health(self.proj(status="cancelled"), 0, 5, datetime.date(2026, 6, 1)), "on_track")

    def test_budget_health_thresholds(self):
        h = services.budget_health
        self.assertEqual(h(D("0"), D("500")), "none")
        self.assertEqual(h(D("1000"), D("899.99")), "on_track")
        self.assertEqual(h(D("1000"), D("900")), "at_risk")
        self.assertEqual(h(D("1000"), D("1000")), "at_risk")
        self.assertEqual(h(D("1000"), D("1000.01")), "over_budget")

    def test_overall_health_is_the_worse_one(self):
        o = services.overall_health
        self.assertEqual(o("on_track", "on_track"), "on_track")
        self.assertEqual(o("on_track", "at_risk"), "at_risk")
        self.assertEqual(o("at_risk", "over_budget"), "delayed")
        self.assertEqual(o("delayed", "none"), "delayed")

    def test_progress_rule(self):
        mk = lambda status: Milestone(status=status)
        tk = lambda status, hours: Task(status=status, estimated_hours=D(hours))
        p = Project(status="active")
        self.assertEqual(services.progress_of(p, [], []), 0)
        # milestones: done 1 + in progress .5 + pending 0 = 1.5 / 3 = 50%
        ms = [mk("done"), mk("in_progress"), mk("pending")]
        self.assertEqual(services.progress_of(p, ms, []), 50)
        # tasks weighted by hours: done 10h of 40h = 25%; a task without an estimate weighs 1
        ts = [tk("done", "10"), tk("todo", "30")]
        self.assertEqual(services.progress_of(p, [], ts), 25)
        self.assertEqual(services.progress_of(p, [], [tk("done", "0"), tk("todo", "0")]), 50)
        # both: average of 50 and 25 = 37.5 -> 38
        self.assertEqual(services.progress_of(p, ms, ts), 38)
        # review counts 80%, in progress 50%
        self.assertEqual(services.progress_of(p, [], [tk("review", "1")]), 80)
        self.assertEqual(services.progress_of(p, [], [tk("in_progress", "1")]), 50)
        self.assertEqual(services.progress_of(Project(status="completed"), [], []), 100)


class ApiProgressTests(ProjectsBase):
    def test_progress_and_health_through_the_api(self):
        p = self.project(start_date="2020-01-01", planned_end_date="2099-01-01")
        self.milestone(p, "A", status="done")
        self.milestone(p, "B", status="in_progress")
        self.milestone(p, "C")
        a = self.task(p, "T1", estimated_hours="10")
        self.task(p, "T2", estimated_hours="30")
        self.pm.patch(f"{P}/tasks/{a['id']}/", {"status": "done"}, format="json")
        got = self.get(p)
        self.assertEqual((got["progress"], got["milestones_done"], got["milestones_total"], got["tasks_done"], got["tasks_total"]),
                         (38, 1, 3, 1, 2))
        self.assertEqual((got["schedule_health"], got["budget_health"], got["health"]), ("on_track", "on_track", "on_track"))

    def test_overdue_and_over_budget_show_up_in_health(self):
        p = self.project(start_date="2020-01-01", planned_end_date="2099-01-01", budget="1000")
        self.milestone(p, "Missed", due="2021-01-01")
        self.post_books(p, expenses=[("5400", "1500")])
        got = self.get(p)
        self.assertEqual((got["schedule_health"], got["budget_health"], got["health"]), ("at_risk", "over_budget", "delayed"))
        self.assertEqual(self.pm.get(f"{P}/projects/", {"health": "delayed"}).json()["count"], 1)


class FinancialTests(ProjectsBase):
    def test_profit_and_loss_comes_from_tagged_journal_lines(self):
        p, other = self.project(), self.project(name="Other")
        self.post_books(p, revenue="1000", expenses=[("5400", "200"), ("5100", "300"), ("5000", "150"), ("5500", "100"), ("5900", "50")])
        self.post_books(other, revenue="9999", expenses=[("5100", "5000")])
        ledger.post(self.company, date=datetime.date(2026, 8, 20), narration="Untagged cost", lines=[
            {"account": self.acct("5900"), "debit": D("777")}, {"account": "bank", "credit": D("777")}])
        f = self.pm.get(f"{P}/projects/{p['id']}/financials/").json()
        self.assertEqual((f["revenue"], f["cost"], f["profit"], f["margin_percent"]), ("1000.00", "800.00", "200.00", "20.00"))
        self.assertEqual(f["categories"], {"labour": "300.00", "materials": "150.00", "subcontract": "100.00", "travel": "200.00", "other": "50.00"})
        self.assertEqual([(r["code"], r["amount"]) for r in f["revenue_rows"]], [("4000", "1000.00")])
        self.assertEqual(len(f["cost_rows"]), 5)
        self.assertEqual(f["monthly"][-1]["label"], f["monthly"][-1]["label"])
        got = self.get(p)
        self.assertEqual((got["revenue"], got["cost"], got["profit"]), ("1000.00", "800.00", "200.00"))
        self.assertEqual(self.get(other)["profit"], "4999.00")

    def test_reversal_nets_to_zero_and_no_revenue_gives_no_margin(self):
        p = self.project()
        self.post_books(p, expenses=[("5400", "200")])
        f = self.pm.get(f"{P}/projects/{p['id']}/financials/").json()
        self.assertEqual((f["revenue"], f["cost"], f["profit"], f["margin_percent"]), ("0.00", "200.00", "-200.00", None))
        entry = JournalEntry.objects.get(narration="Expense 5400")
        ledger.reverse(entry, self.admin, "Booked twice")
        self.assertEqual(self.pm.get(f"{P}/projects/{p['id']}/financials/").json()["cost"], "0.00")

    def test_budget_vs_actual_by_category(self):
        p = self.project(budget="10000", budget_lines=[{"category": "labour", "amount": "4000"}, {"category": "travel", "amount": "1000"},
                                                      {"category": "materials", "amount": "3000"}])
        self.post_books(p, expenses=[("5100", "4500"), ("5400", "400"), ("5000", "1200"), ("5900", "75.50")])
        b = self.pm.get(f"{P}/projects/{p['id']}/budget/").json()
        rows = {r["category"]: r for r in b["rows"]}
        self.assertEqual((rows["labour"]["budget"], rows["labour"]["actual"], rows["labour"]["variance"], rows["labour"]["used_percent"]),
                         ("4000.00", "4500.00", "-500.00", "112.50"))
        self.assertEqual((rows["travel"]["actual"], rows["travel"]["variance"], rows["travel"]["used_percent"]), ("400.00", "600.00", "40.00"))
        self.assertEqual((rows["materials"]["actual"], rows["materials"]["variance"]), ("1200.00", "1800.00"))
        self.assertEqual((rows["other"]["budget"], rows["other"]["actual"], rows["other"]["used_percent"]), ("0.00", "75.50", None))
        self.assertEqual((b["budget_total"], b["allocated"], b["unallocated"], b["actual_total"], b["variance"]),
                         ("10000.00", "8000.00", "2000.00", "6175.50", "3824.50"))
        self.assertEqual((b["used_percent"], b["health"]), ("61.80", "on_track"))

    def test_budget_total_falls_back_to_the_sum_of_lines(self):
        p = self.project(budget="0", budget_lines=[{"category": "labour", "amount": "700"}, {"category": "other", "amount": "300"}])
        self.post_books(p, expenses=[("5100", "950")])
        b = self.pm.get(f"{P}/projects/{p['id']}/budget/").json()
        self.assertEqual((b["budget_total"], b["unallocated"], b["health"]), ("1000.00", "0.00", "at_risk"))

    def test_account_to_category_mapping(self):
        from apps.projects.services import category_for_account as cat
        want = {"5100": "labour", "5400": "travel", "5500": "subcontract", "5300": "materials", "5000": "materials",
                "5200": "other", "5600": "other", "5650": "other", "5900": "other", "5700": "other"}
        for code, expected in want.items():
            self.assertEqual(cat(self.acct(code)), expected, code)

    def test_invoices_tagged_to_the_project_receivable_and_listing(self):
        p, other = self.project(), self.project(name="Other")
        inv = self.api_finance.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                                        "form": {"items": ITEMS}, "project": p["id"]}, format="json").json()
        self.assertEqual(inv["project"], p["id"])
        self.api_finance.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                                  "form": {"items": ITEMS}, "project": other["id"]}, format="json")
        draft = self.pm.get(f"{P}/projects/{p['id']}/invoices/").json()
        self.assertEqual((draft["count"], draft["invoiced"]), (1, "0.00"))  # a draft is not yet billed
        self.assertEqual(self.api_finance.post(f"{S}/documents/{inv['id']}/issue/").status_code, 200)
        r = self.api_finance.post(f"{S}/payments/", {"customer": self.acme["id"], "date": "2026-08-20", "amount": "500",
                                                    "mode": "bank_transfer", "allocations": [{"document": inv["id"], "amount": "500"}]},
                                  format="json")
        self.assertEqual(r.status_code, 201, r.content)
        lst = self.pm.get(f"{P}/projects/{p['id']}/invoices/").json()
        self.assertEqual((lst["count"], lst["invoiced"], lst["outstanding"]), (1, "1800.00", "1300.00"))
        row = lst["results"][0]
        self.assertEqual((row["id"], row["grand_total"], row["balance"], row["customer"]), (inv["id"], "1800.00", "1300.00", "Acme Industries"))
        # the same filter in the sales API
        sales = self.api_finance.get(f"{S}/documents/", {"project": p["id"]}).json()
        self.assertEqual([d["id"] for d in sales["results"]], [inv["id"]])
        # revenue is the taxable value posted by the sale; receivables and collections follow
        f = self.pm.get(f"{P}/projects/{p['id']}/financials/").json()
        self.assertEqual((f["revenue"], f["invoiced"], f["collected"], f["outstanding"]), ("1800.00", "1800.00", "500.00", "1300.00"))
        self.assertEqual(self.pm.get(f"{P}/projects/{other['id']}/financials/").json()["revenue"], "0.00")

    def test_expenses_tab_lists_tagged_expense_lines_only(self):
        p = self.project()
        self.post_books(p, revenue="500", expenses=[("5400", "200"), ("5100", "300")])
        e = self.pm.get(f"{P}/projects/{p['id']}/expenses/").json()
        self.assertEqual((e["count"], e["total"]), (2, "500.00"))
        self.assertEqual({r["account"]: r["category"] for r in e["results"]}, {"5400 Travel and conveyance": "travel", "5100 Salaries and wages": "labour"})

    def test_wip_rules(self):
        fixed = Project(status="active", billing_method="fixed", contract_value=D("100000"))
        self.assertEqual(services.wip_of(fixed, 40, D("25000"), D("0")), D("15000.00"))
        self.assertEqual(services.wip_of(fixed, 20, D("25000"), D("0")), D("0.00"))
        tm = Project(status="active", billing_method="time_material", contract_value=D("0"))
        self.assertEqual(services.wip_of(tm, 10, D("1000"), D("3500")), D("2500.00"))
        self.assertEqual(services.wip_of(Project(status="completed", billing_method="fixed", contract_value=D("100")), 50, D("0"), D("0")), D("0.00"))


class TimesheetTests(ProjectsBase):
    def test_hours_amounts_and_weekly_totals(self):
        p = self.project(billing_method="time_material", hourly_rate="900")
        self.entry(p, "2.5", date="2026-08-10", rate="1000")            # Mon 10 Aug: 2,500.00
        self.entry(p, "1.25", date="2026-08-12", rate="800")            # Wed: 1,000.00
        self.entry(p, "3", date="2026-08-14", billable=False)           # Fri: non-billable, rate defaults to 900 but not billed
        self.entry(p, "4", date="2026-08-17", rate="500.50")            # next Monday: 2,002.00
        r = self.pm.get(f"{P}/timesheets/", {"project": p["id"]}).json()
        self.assertEqual((r["hours"], r["billable_hours"], r["non_billable_hours"], r["amount"]), ("10.75", "7.75", "3.00", "5502.00"))
        weeks = {w["week_start"]: w for w in r["weekly"]}
        self.assertEqual(set(weeks), {"2026-08-10", "2026-08-17"})
        self.assertEqual((weeks["2026-08-10"]["hours"], weeks["2026-08-10"]["billable_hours"], weeks["2026-08-10"]["amount"]),
                         ("6.75", "3.75", "3500.00"))
        self.assertEqual((weeks["2026-08-17"]["hours"], weeks["2026-08-17"]["amount"]), ("4.00", "2002.00"))
        amounts = sorted(e["amount"] for e in r["results"])
        self.assertEqual(amounts, ["0.00", "1000.00", "2002.00", "2500.00"])
        # filters
        self.assertEqual(self.pm.get(f"{P}/timesheets/", {"billable": "false"}).json()["hours"], "3.00")
        self.assertEqual(self.pm.get(f"{P}/timesheets/", {"from": "2026-08-12", "to": "2026-08-14"}).json()["hours"], "4.25")
        self.assertEqual(self.pm.get(f"{P}/timesheets/", {"employee": "ash"}).json()["count"], 4)
        self.assertEqual(self.pm.get(f"{P}/timesheets/", {"from": "bad"}).status_code, 400)
        # on the project: billable amount counts toward WIP of a time-and-material job
        got = self.get(p)
        self.assertEqual((got["hours"], got["billable_hours"], got["billable_amount"], got["wip"]), ("10.75", "7.75", "5502.00", "5502.00"))

    def test_rate_defaults_from_team_member_then_project(self):
        p = self.project(hourly_rate="900")
        self.pm.post(f"{P}/team/", {"project": p["id"], "name": "Asha", "hourly_rate": "1500"}, format="json")
        self.assertEqual(self.entry(p, "1", employee_name="Asha")["rate"], "1500.00")
        self.assertEqual(self.entry(p, "1", employee_name="Ravi")["rate"], "900.00")
        self.assertEqual(self.entry(p, "1", employee_name="Ravi", rate="0")["rate"], "0.00")
        solo = self.pm.post(f"{P}/timesheets/", {"project": p["id"], "date": "2026-08-10", "hours": "1"}, format="json").json()
        self.assertEqual(solo["employee_name"], "pm")  # defaults to the person logging
        self.assertEqual(solo["user"], "pm")

    def test_validation_edit_delete(self):
        p = self.project()
        for bad, field in (({"hours": "0"}, "hours"), ({"hours": "24.5"}, "hours"), ({"hours": "x"}, "hours"), ({"hours": "1.234"}, "hours"),
                           ({"date": ""}, "date"), ({"date": iso(TODAY + datetime.timedelta(days=30))}, "date"),
                           ({"rate": "-1"}, "rate"), ({"billable": "yes"}, "billable")):
            r = self.pm.post(f"{P}/timesheets/", {"project": p["id"], "date": "2026-08-10", "hours": "1", **bad}, format="json")
            self.assertEqual((r.status_code, field in r.json()), (400, True), bad)
        self.assertEqual(self.pm.post(f"{P}/timesheets/", {"project": p["id"], "date": "2026-08-10"}, format="json").status_code, 400)
        e = self.entry(p, "2", rate="100")
        r = self.pm.patch(f"{P}/timesheets/{e['id']}/", {"hours": "3.5", "billable": False, "notes": "Fixed"}, format="json").json()
        self.assertEqual((r["hours"], r["amount"], r["notes"]), ("3.50", "0.00", "Fixed"))
        self.assertEqual(self.pm.delete(f"{P}/timesheets/{e['id']}/").status_code, 204)
        self.assertEqual(self.pm.get(f"{P}/timesheets/").json()["count"], 0)
        cancelled = self.project(name="Gone")
        self.pm.post(f"{P}/projects/{cancelled['id']}/status/", {"status": "cancelled", "reason": "x"}, format="json")
        self.entry(cancelled, "1", expect=400)


class DashboardTests(ProjectsBase):
    def test_dashboard_and_portfolio_figures(self):
        a = self.project(name="Alpha", contract_value="100000", budget="50000", billing_method="fixed", start_date="2020-01-01")
        b = self.project(name="Beta", contract_value="40000", budget="10000", status="planned")
        done = self.project(name="Done", contract_value="5000", budget="0")
        self.pm.post(f"{P}/projects/{done['id']}/status/", {"status": "completed"}, format="json")
        self.post_books(a, revenue="30000", expenses=[("5100", "12000"), ("5400", "3000")])
        self.post_books(b, expenses=[("5900", "500")])
        self.milestone(a, "Late one", due="2020-01-01", amount="1000")
        self.milestone(a, "Soon", due=iso(TODAY + datetime.timedelta(days=3)))
        t = self.task(a, "Overdue task", due_date="2020-01-01")
        self.task(a, "Open task")
        self.pm.patch(f"{P}/projects/{a['id']}/", {"status": "active"}, format="json")
        d = self.pm.get(f"{P}/dashboard/").json()
        tiles = d["tiles"]
        self.assertEqual((tiles["active_projects"], tiles["open_projects"], tiles["overdue_milestones"]), (1, 2, 1))
        self.assertEqual(tiles["contract_value"], "140000.00")  # open projects only
        self.assertEqual((tiles["revenue"], tiles["cost"], tiles["profit"], tiles["margin_percent"]), ("30000.00", "15500.00", "14500.00", "48.30"))
        self.assertEqual((tiles["open_tasks"], tiles["overdue_tasks"]), (2, 1))
        # work in progress: Alpha's progress is 0 here so nothing is earned beyond the 30,000 already billed
        self.assertEqual(tiles["wip"], "0.00")
        self.assertEqual(d["status_counts"], {"planned": 1, "active": 1, "on_hold": 0, "completed": 1, "cancelled": 0})
        self.assertEqual([r["name"] for r in d["profitability"]], ["Alpha", "Beta"])
        self.assertEqual(d["profitability"][0]["profit"], "15000.00")
        self.assertEqual(d["revenue_vs_cost"][0]["name"], "Alpha")
        self.assertEqual(d["overdue_milestones"][0]["name"], "Late one")
        self.assertEqual(d["overdue_milestones"][0]["project"]["name"], "Alpha")
        self.assertEqual([m["name"] for m in d["upcoming_milestones"]], ["Soon"])
        self.assertEqual(len(d["monthly"]), 6)
        self.assertEqual(len(d["timeline"]), 2)
        pf = self.pm.get(f"{P}/portfolio/").json()
        self.assertEqual((pf["count"], pf["totals"]["revenue"], pf["totals"]["cost"], pf["totals"]["profit"]), (3, "30000.00", "15500.00", "14500.00"))
        rows = {r["name"]: r for r in pf["results"]}
        self.assertEqual((rows["Alpha"]["profit"], rows["Alpha"]["margin_percent"]), ("15000.00", "50.00"))
        self.assertEqual(self.pm.get(f"{P}/portfolio/", {"status": "planned"}).json()["count"], 1)
        self.assertTrue(t["id"])

    def test_wip_in_the_dashboard(self):
        a = self.project(name="Alpha", contract_value="100000", status="active")
        for s in ("done", "in_progress", "pending", "pending"):
            self.milestone(a, f"M-{s}", status=s)  # progress (1 + .5) / 4 = 37.5 -> 38%
        self.post_books(a, revenue="10000")
        self.assertEqual(self.get(a)["progress"], 38)
        # 100000 x 38% = 38,000 earned less 10,000 billed
        self.assertEqual(self.pm.get(f"{P}/dashboard/").json()["tiles"]["wip"], "28000.00")
