"""Each report with hand-computed figures."""
import datetime
from decimal import Decimal

from apps.accounts.models import Role
from apps.company.models import CompanyProfileAuditEvent

from .base import ALL_REPORTS, R, ReportsBase, TODAY, ago, iso

D = Decimal
FY = {"fy": "2026-27"}


class RevenueTests(ReportsBase):
    def test_months_total_and_previous_year(self):
        self.book("2026-08-10", "bank", "sales", "10000")
        self.book("2026-09-05", "bank", "sales", "5000")
        self.book("2026-09-20", "sales_returns", "bank", "1000")     # returns come off the sales
        self.book("2026-09-21", "bank", "other_income", "700")       # other income is not revenue
        self.book("2025-08-12", "bank", "sales", "4000")             # the same month a year earlier
        d = self.get("revenue", **FY)
        months = {m["label"]: m for m in d["months"]}
        self.assertEqual(len(d["months"]), 12)
        self.assertEqual((months["Aug 26"]["revenue"], months["Aug 26"]["previous"], months["Aug 26"]["change_percent"]), ("10000.00", "4000.00", "150.00"))
        self.assertEqual((months["Sep 26"]["revenue"], months["Sep 26"]["previous"], months["Sep 26"]["change_percent"]), ("4000.00", "0.00", None))
        self.assertEqual(months["Apr 26"]["revenue"], "0.00")
        k = d["kpis"]
        self.assertEqual((k["revenue"], k["previous_revenue"], k["change_percent"], k["monthly_average"]), ("14000.00", "4000.00", "250.00", "1166.67"))
        self.assertEqual(k["best_month"], {"label": "Aug 26", "amount": "10000.00"})
        self.assertEqual(d["period"]["label"], "FY 2026-27")

    def test_by_customer_includes_credit_notes_and_other_entries(self):
        beta = self.customer("Beta Traders")
        self.invoice(issue_date="2026-08-15")                                                        # 1,800.00
        self.invoice(beta, issue_date="2026-08-16", items=[{"description": "Support", "quantity": "1", "rate": "500"}])
        inv = self.invoice(beta, issue_date="2026-08-17", items=[{"description": "Extra", "quantity": "1", "rate": "300"}])
        self.issue(self.draft(beta, kind="credit_note", against=inv["id"], reason="Return", issue_date="2026-08-20",
                              items=[{"description": "Return", "quantity": "1", "rate": "100"}]))
        self.book("2026-08-25", "bank", "sales", "200")                                              # not from an invoice
        d = self.get("revenue", **FY)
        rows = {r["customer"]: r for r in d["by_customer"]}
        self.assertEqual(rows["Acme Industries"]["revenue"], "1800.00")
        self.assertEqual(rows["Beta Traders"]["revenue"], "700.00")      # 500 + 300 - 100
        self.assertEqual(rows["Other entries (not from invoices)"]["revenue"], "200.00")
        self.assertEqual(d["kpis"]["revenue"], "2700.00")
        self.assertEqual(rows["Acme Industries"]["share_percent"], "66.67")
        self.assertEqual([r["customer"] for r in d["by_customer"]][:2], ["Acme Industries", "Beta Traders"])

    def test_customer_section_needs_sales_view_but_months_stay(self):
        from apps.accounts import access
        from apps.accounts.models import RolePermission
        self.invoice(issue_date="2026-08-15")
        self.assertIsNotNone(self.get("revenue", Role.FINANCE_MANAGER, **FY)["by_customer"])
        RolePermission.objects.create(role=Role.FINANCE_MANAGER, code="sales.view", granted=False)
        access.matrix_changed()
        d = self.get("revenue", Role.FINANCE_MANAGER, **FY)
        self.assertIsNone(d["by_customer"])
        self.assertEqual(d["kpis"]["revenue"], "1800.00")

    def test_custom_range_compares_with_the_same_dates_last_year(self):
        self.book("2026-08-10", "bank", "sales", "900")
        self.book("2025-08-10", "bank", "sales", "300")
        d = self.get("revenue", **{"from": "2026-08-01", "to": "2026-08-31"})
        self.assertEqual([m["label"] for m in d["months"]], ["Aug 26"])
        self.assertEqual((d["kpis"]["revenue"], d["kpis"]["previous_revenue"], d["kpis"]["change_percent"]), ("900.00", "300.00", "200.00"))
        self.assertEqual(d["period"]["custom"], True)


class CashFlowTests(ReportsBase):
    def test_statement_and_monthly_movements(self):
        self.book("2026-08-02", "bank", "capital", "50000")          # financing
        self.book("2026-08-10", "bank", "sales", "10000")
        self.book("2026-08-15", "salaries", "bank", "3000")
        self.book("2026-08-18", "cash", "bank", "1000")              # between our own accounts: neither in nor out
        d = self.get("cash-flow", **FY)
        k = d["kpis"]
        self.assertEqual((k["opening_cash"], k["closing_cash"], k["net_change"]), ("0.00", "57000.00", "57000.00"))
        self.assertEqual((k["operating"], k["investing"], k["financing"]), ("7000.00", "0.00", "50000.00"))
        self.assertEqual((k["inflow"], k["outflow"]), ("60000.00", "3000.00"))
        self.assertEqual(d["statement"]["net_profit"], "7000.00")
        self.assertTrue(d["statement"]["ties"])
        months = {m["label"]: m for m in d["months"]}
        self.assertEqual((months["Aug 26"]["inflow"], months["Aug 26"]["outflow"], months["Aug 26"]["net"], months["Aug 26"]["closing"]),
                         ("60000.00", "3000.00", "57000.00", "57000.00"))
        self.assertEqual(months["Sep 26"]["closing"], "57000.00")   # the balance carries forward
        self.assertEqual(months["Apr 26"]["closing"], "0.00")

    def test_opening_balance_comes_from_before_the_period(self):
        self.book("2026-05-01", "bank", "capital", "20000")
        self.book("2026-08-10", "bank", "sales", "1000")
        d = self.get("cash-flow", **{"from": "2026-08-01", "to": "2026-08-31"})
        self.assertEqual((d["kpis"]["opening_cash"], d["kpis"]["closing_cash"]), ("20000.00", "21000.00"))
        self.assertEqual(d["months"][0]["closing"], "21000.00")


class ReceivablesTests(ReportsBase):
    def test_ageing_by_customer(self):
        beta = self.customer("Beta Traders")
        current = self.invoice(issue_date=iso(ago(60)), due_date=iso(ago(-10)))                               # 1,800.00 not yet due
        self.pay(amount="800", date=iso(ago(1)), allocations=[{"document": current["id"], "amount": "800"}])  # leaves 1,000.00
        self.invoice(beta, issue_date=iso(ago(50)), due_date=iso(ago(20)), items=[{"description": "S", "quantity": "1", "rate": "500"}])
        self.invoice(beta, issue_date=iso(ago(100)), due_date=iso(ago(60)), items=[{"description": "T", "quantity": "1", "rate": "250"}])
        d = self.get("receivables")
        b = {x["key"]: x["amount"] for x in d["buckets"]}
        self.assertEqual(b, {"current": "1000.00", "d1_15": "0.00", "d16_30": "500.00", "d31_45": "0.00", "d46_plus": "250.00"})
        k = d["kpis"]
        self.assertEqual((k["outstanding"], k["overdue"], k["overdue_percent"], k["customers_owing"], k["invoices"]),
                         ("1750.00", "750.00", "42.86", 2, 3))
        self.assertEqual(k["largest"], {"customer": "Acme Industries", "amount": "1000.00"})
        beta_row = next(r for r in d["customers"] if r["customer"] == "Beta Traders")
        self.assertEqual((beta_row["total"], beta_row["invoices"], beta_row["oldest_overdue_days"]), ("750.00", 2, 60))

    def test_date_range_limits_the_invoices_counted(self):
        self.invoice(issue_date=iso(ago(60)), due_date=iso(ago(30)))
        self.invoice(self.customer("Beta Traders"), issue_date=iso(ago(5)), due_date=iso(ago(-25)))
        d = self.get("receivables", **{"from": iso(ago(10)), "to": iso(TODAY)})
        self.assertEqual((d["kpis"]["outstanding"], d["kpis"]["customers_owing"]), ("1800.00", 1))
        self.assertEqual(d["customers"][0]["customer"], "Beta Traders")

    def test_paid_and_void_invoices_are_left_out(self):
        paid = self.invoice(issue_date=iso(ago(10)), due_date=iso(ago(-20)))
        self.pay(amount="1800", date=iso(ago(1)), allocations=[{"document": paid["id"], "amount": "1800"}])
        gone = self.invoice(issue_date=iso(ago(10)), due_date=iso(ago(-20)))
        self.assertEqual(self.fin.post(f"/api/v1/sales/documents/{gone['id']}/void/", {"change_reason": "mistake"}).status_code, 200)
        d = self.get("receivables")
        self.assertEqual((d["kpis"]["outstanding"], d["customers"]), ("0.00", []))


class VendorOutstandingTests(ReportsBase):
    def test_ageing_by_vendor(self):
        local, far = self.vendor("Local Supplies"), self.vendor("Far Away Traders", state="27", pan="BBBPB1234B")
        b = self.bill(local, ago(50), ago(20), no="VB-1")            # 1,000 + 18% = 1,180.00 due 20 days ago
        self.assertEqual(self.fin.post("/api/v1/purchases/payments/", {"vendor": local["id"], "date": iso(ago(1)), "amount": "180", "mode": "bank",
                                                                       "allocations": [{"bill": b["id"], "amount": "180"}]}).status_code, 201)
        self.bill(far, ago(5), ago(-25), rate="2000", no="VB-2")     # 2,360.00 not yet due
        d = self.get("vendor-outstanding")
        buckets = {x["key"]: x["amount"] for x in d["buckets"]}
        self.assertEqual((buckets["current"], buckets["d1_30"], buckets["d31_60"]), ("2360.00", "1000.00", "0.00"))
        k = d["kpis"]
        self.assertEqual((k["outstanding"], k["overdue"], k["overdue_percent"], k["vendors_owed"], k["bills"]), ("3360.00", "1000.00", "29.76", 2, 2))
        self.assertEqual(d["vendors"][0]["vendor"], "Far Away Traders")
        self.assertEqual(d["vendors"][1]["oldest_overdue_days"], 20)

    def test_date_range_limits_the_bills_counted(self):
        v = self.vendor()
        self.bill(v, ago(80), ago(50), no="VB-1")
        self.bill(v, ago(5), ago(-20), rate="2000", no="VB-2")
        d = self.get("vendor-outstanding", **{"from": iso(ago(10)), "to": iso(TODAY)})
        self.assertEqual((d["kpis"]["outstanding"], d["kpis"]["bills"]), ("2360.00", 1))


class GstTests(ReportsBase):
    def test_output_credit_and_net_by_month_and_head(self):
        self.enable_gst()
        gst_items = [{"description": "Development", "quantity": "2", "rate": "1000", "discount_percent": "10", "gst_rate": "18"}]
        self.invoice(issue_date="2026-08-15", items=gst_items)                 # 1,800.00 taxable, GST 324.00 (162 CGST + 162 SGST)
        v = self.vendor()
        self.bill(v, datetime.date(2026, 8, 20), datetime.date(2026, 9, 20))   # 1,000.00 taxable, ITC 180.00 (90 + 90)
        d = self.get("gst", **FY)
        k = d["kpis"]
        self.assertEqual((k["taxable"], k["output"], k["itc"], k["net_payable"], k["paid"], k["closing_liability"]),
                         ("1800.00", "324.00", "180.00", "144.00", "0.00", "324.00"))
        aug = next(m for m in d["months"] if m["month"] == "2026-08")
        self.assertEqual((aug["taxable"], aug["output"], aug["itc"], aug["net"]), ("1800.00", "324.00", "180.00", "144.00"))
        heads = {h["head"]: h for h in d["heads"]}
        self.assertEqual((heads["cgst"]["output"], heads["sgst"]["output"], heads["igst"]["output"]), ("162.00", "162.00", "0.00"))
        self.assertEqual((heads["cgst"]["itc"], heads["sgst"]["itc"]), ("90.00", "90.00"))
        self.assertEqual(len(d["months"]), 12)


class ProjectProfitabilityTests(ReportsBase):
    def test_revenue_cost_profit_per_project(self):
        alpha, beta = self.project("Alpha"), self.project("Beta")
        gone = self.project("Dropped")
        self.assertEqual(self.api.post(f"/api/v1/projects/projects/{gone['id']}/status/", {"status": "cancelled", "reason": "x"}, format="json").status_code, 200)
        self.invoice(issue_date="2026-08-15", project=alpha["id"])                     # revenue 1,800.00 on Alpha
        self.book("2026-08-20", "salaries", "bank", "600", project=alpha["id"])         # cost 600.00 on Alpha
        self.book("2026-09-01", "bank", "sales", "1000")                               # not tagged to any project
        d = self.get("project-profitability")
        rows = {r["name"]: r for r in d["projects"]}
        self.assertEqual((rows["Alpha"]["revenue"], rows["Alpha"]["cost"], rows["Alpha"]["profit"], rows["Alpha"]["margin_percent"]),
                         ("1800.00", "600.00", "1200.00", "66.67"))
        self.assertEqual((rows["Beta"]["revenue"], rows["Beta"]["profit"], rows["Beta"]["margin_percent"]), ("0.00", "0.00", None))
        self.assertNotIn("Dropped", rows)                                              # cancelled and nothing booked
        k = d["kpis"]
        self.assertEqual((k["revenue"], k["cost"], k["profit"], k["projects"], k["untagged_revenue"], k["loss_making"]),
                         ("1800.00", "600.00", "1200.00", 2, "1000.00", 0))
        self.assertEqual(d["projects"][0]["name"], "Alpha")
        self.assertEqual(k["best"], {"name": "Alpha", "profit": "1200.00"})

    def test_period_filter_and_loss(self):
        alpha = self.project("Alpha")
        self.book("2026-05-10", "salaries", "bank", "900", project=alpha["id"])
        self.book("2026-08-10", "bank", "sales", "400", project=alpha["id"])
        both = self.get("project-profitability")["projects"][0]
        self.assertEqual((both["revenue"], both["cost"], both["profit"]), ("400.00", "900.00", "-500.00"))
        aug = self.get("project-profitability", **{"from": "2026-08-01", "to": "2026-08-31"})
        self.assertEqual((aug["projects"][0]["cost"], aug["kpis"]["profit"], aug["kpis"]["loss_making"]), ("0.00", "400.00", 0))
        self.assertEqual(self.get("project-profitability")["kpis"]["loss_making"], 1)


class PartnerCapitalTests(ReportsBase):
    def partner(self, name):
        r = self.api.post("/api/v1/company/partners/", dict(full_name=name, designation="Partner", partner_type="partner"))
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def tx(self, partner, kind, amount, date):
        r = self.api.post("/api/v1/partners/transactions/", {"partner": partner["id"], "kind": kind, "amount": amount, "date": date})
        self.assertEqual(r.status_code, 201, r.content)

    def test_capital_current_account_and_drawings(self):
        asha, bala = self.partner("Asha Rao"), self.partner("Bala Menon")
        self.tx(asha, "capital_introduced", "100000", "2026-08-10")
        self.tx(bala, "capital_introduced", "50000", "2026-08-10")
        self.tx(asha, "drawing", "10000", "2026-09-10")
        d = self.get("partner-capital", **FY)
        rows = {r["name"]: r for r in d["partners"]}
        self.assertEqual((rows["Asha Rao"]["capital"], rows["Asha Rao"]["current"], rows["Asha Rao"]["drawings"], rows["Asha Rao"]["contribution_percent"]),
                         ("100000.00", "-10000.00", "10000.00", "66.67"))
        self.assertEqual((rows["Bala Menon"]["capital"], rows["Bala Menon"]["drawings"]), ("50000.00", "0.00"))
        k = d["kpis"]
        self.assertEqual((k["capital"], k["current"], k["drawings"], k["loan"], k["partners"]), ("150000.00", "-10000.00", "10000.00", "0.00", 2))
        self.assertEqual(d["partners"][0]["name"], "Asha Rao")  # largest capital first
        self.assertEqual(rows["Asha Rao"]["opening_capital"], "0.00")

    def test_opening_capital_and_range(self):
        asha = self.partner("Asha Rao")
        self.tx(asha, "capital_introduced", "100000", "2026-08-10")
        self.tx(asha, "capital_introduced", "25000", "2026-10-10")
        self.tx(asha, "drawing", "4000", "2026-09-10")
        d = self.get("partner-capital", **{"from": "2026-09-01", "to": "2026-12-31"})
        r = d["partners"][0]
        self.assertEqual((r["opening_capital"], r["capital"], r["capital_change"], r["drawings"]), ("100000.00", "125000.00", "25000.00", "4000.00"))
        self.assertEqual(d["as_of"], "2026-12-31")
        early = self.get("partner-capital", **{"from": "2026-04-01", "to": "2026-08-31"})["partners"][0]
        self.assertEqual((early["capital"], early["drawings"]), ("100000.00", "0.00"))

    def test_partner_with_nothing_and_inactive_is_left_out(self):
        self.partner("Asha Rao")
        d = self.get("partner-capital", **FY)
        self.assertEqual([r["name"] for r in d["partners"]], ["Asha Rao"])  # active partners always show, even at zero


class SalaryTests(ReportsBase):
    def setUp(self):
        super().setUp()
        aug, sep, octo = datetime.date(2026, 8, 1), datetime.date(2026, 9, 1), datetime.date(2026, 10, 1)
        self.payroll(aug, "paid", [("Asha", "Engineering", "50000", "5000", "5000"), ("Bala", "Engineering", "40000", "4000", "4000"),
                                   ("Chitra", "Sales", "30000", "3000", "3000")])
        self.payroll(sep, "posted", [("Asha", "Engineering", "50000", "5000", "5000")])
        self.payroll(octo, "draft", [("Asha", "Engineering", "99999", "0", "0")])  # a draft run is not counted

    def test_totals_by_month_and_department(self):
        d = self.get("salary", **FY)
        k = d["kpis"]
        self.assertEqual((k["gross"], k["deductions"], k["net"], k["employer_cost"], k["months_paid"], k["headcount"]),
                         ("170000.00", "17000.00", "153000.00", "187000.00", 2, 1))
        months = {m["label"]: m for m in d["months"]}
        self.assertEqual((months["Aug 26"]["employees"], months["Aug 26"]["gross"], months["Aug 26"]["net"], months["Aug 26"]["employer_cost"]),
                         (3, "120000.00", "108000.00", "132000.00"))
        self.assertEqual((months["Sep 26"]["employees"], months["Sep 26"]["gross"]), (1, "50000.00"))
        self.assertEqual((months["Oct 26"]["employees"], months["Oct 26"]["gross"]), (0, "0.00"))
        depts = {r["department"]: r for r in d["departments"]}
        self.assertEqual((depts["Engineering"]["employees"], depts["Engineering"]["gross"], depts["Engineering"]["employer_cost"]), (2, "140000.00", "154000.00"))
        self.assertEqual((depts["Sales"]["employees"], depts["Sales"]["net"]), (1, "27000.00"))
        self.assertEqual(d["departments"][0]["department"], "Engineering")

    def test_no_one_person_pay_in_the_response(self):
        text = self.clients[Role.HR_MANAGER].get(f"{R}/salary/", FY).content.decode()
        for name in ("Asha", "Bala", "Chitra", "E-Asha"):
            self.assertNotIn(name, text)
        csv_text = self.export("salary", Role.HR_MANAGER, **FY).content.decode()
        for name in ("Asha", "Bala", "Chitra"):
            self.assertNotIn(name, csv_text)

    def test_every_salary_csv_export_is_audited(self):
        events = CompanyProfileAuditEvent.objects.filter(entity_type="report_export")
        self.assertEqual(events.count(), 0)
        self.export("salary", Role.HR_MANAGER, **FY)
        self.export("salary", Role.FINANCE_MANAGER, **FY)
        self.assertEqual(events.count(), 2)
        e = events.order_by("occurred_at").first()
        self.assertEqual((e.action, e.new_values["report"]), ("downloaded", "salary"))
        self.export("salary", Role.PROJECT_MANAGER, expect=403, **FY)  # refused: no event
        self.assertEqual(events.count(), 2)
        self.get("salary", Role.HR_MANAGER, **FY)                   # viewing alone is not an export
        self.assertEqual(events.count(), 2)


class HubTests(ReportsBase):
    def test_home_cards_carry_live_headlines(self):
        self.book(TODAY, "bank", "sales", "5000")
        d = self.get("home")
        cards = {c["key"]: c for c in d["reports"]}
        self.assertEqual(set(cards), ALL_REPORTS)
        self.assertEqual(cards["revenue"]["headline"]["value"], "5000.00")
        self.assertEqual(cards["cash-flow"]["headline"]["value"], "5000.00")
        self.assertEqual(cards["receivables"]["headline"]["value"], "0.00")
        self.assertEqual(cards["revenue"]["path"], "/reports/revenue")

    def test_dashboard_tiles(self):
        self.book(TODAY, "bank", "sales", "5000")
        self.book(TODAY, "salaries", "bank", "2000")
        self.invoice(issue_date=iso(TODAY), due_date=iso(ago(-10)))                   # 1,800.00 owed by a customer
        self.payroll(datetime.date(2026, 8, 1), "paid", [("Asha", "Engineering", "100", "0", "0")])
        from apps.hr.models import Employee
        Employee.objects.create(company=self.company, first_name="Gone", code="E-G", date_of_joining=datetime.date(2024, 1, 1), status="exited")
        # a deal worth 100,000 through the CRM
        crm = self.clients[Role.PARTNER]
        a = crm.post("/api/v1/crm/accounts/", {"name": "Prospect Ltd"})
        self.assertEqual(a.status_code, 201, a.content)
        deal = crm.post("/api/v1/crm/deals/", {"name": "ERP rollout", "account": a.json()["id"], "amount": "100000"})
        self.assertEqual(deal.status_code, 201, deal.content)
        d = self.get("dashboard")
        tiles = {t["key"]: t for t in d["tiles"]}
        self.assertEqual({k: tiles[k]["value"] for k in ("revenue", "expenses", "profit", "cash", "receivables", "payables", "headcount", "deals")},
                         {"revenue": "6800.00", "expenses": "2000.00", "profit": "4800.00", "cash": "3000.00", "receivables": "1800.00",
                          "payables": "0.00", "headcount": 1, "deals": 1})
        self.assertEqual(tiles["deals"]["hint"], "Worth 100000.00")
        self.assertEqual(len(d["trend"]), 6)
        last = d["trend"][-1]
        self.assertEqual((last["revenue"], last["expenses"], last["profit"]), ("6800.00", "2000.00", "4800.00"))
        self.assertEqual(d["trend"][0]["revenue"], "0.00")
