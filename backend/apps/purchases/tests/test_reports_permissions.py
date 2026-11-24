import datetime
from decimal import Decimal

from apps.accounts.models import Role
from apps.accounting.models import Account
from apps.company.tests.base import client_for, make_user

from .base import ALL_ROLES, ITEMS, P, PurchasesBase

ONE = [{"description": "Item", "quantity": "1", "rate": "1000", "gst_rate": "0"}]  # exactly 1,000.00 with no GST
AS_OF = "2026-10-31"


class ReportTests(PurchasesBase):
    def bill_due(self, vendor, due, amount="1000", bill_date="2026-05-01", **extra):
        return self.bill(vendor, bill_date=bill_date, due_date=due, items=[{"description": "Item", "quantity": "1", "rate": amount, "gst_rate": "0"}], **extra)

    def test_ageing_buckets_with_fixed_dates(self):
        self.bill_due(self.local, "2026-11-05", "100")    # not yet due: current
        self.bill_due(self.local, "2026-10-31", "200")    # due today: current
        self.bill_due(self.local, "2026-10-25", "300")    # 6 days: 1-30
        self.bill_due(self.local, "2026-10-01", "400")    # 30 days: 1-30
        self.bill_due(self.far, "2026-09-30", "500")      # 31 days: 31-60
        self.bill_due(self.far, "2026-09-01", "600")      # 60 days: 31-60
        self.bill_due(self.far, "2026-08-31", "700")      # 61 days: 61-90
        self.bill_due(self.local, "2026-08-02", "800")    # 90 days: 61-90
        self.bill_due(self.local, "2026-08-01", "900")    # 91 days: over 90
        paid = self.bill_due(self.far, "2026-06-01", "5000")
        self.pay(self.far, amount="5000", allocations=[{"bill": paid["id"], "amount": "5000"}])  # fully paid: not listed
        d = self.fin.get(f"{P}/reports/ageing/", {"as_of": AS_OF}).json()
        self.assertEqual({b["key"]: b["amount"] for b in d["buckets"]},
                         {"current": "300.00", "d1_30": "700.00", "d31_60": "1100.00", "d61_90": "1500.00", "d90_plus": "900.00"})
        self.assertEqual(d["total"], "4500.00")
        rows = {r["vendor"]: r for r in d["rows"]}
        self.assertEqual((rows["Local Supplies"]["current"], rows["Local Supplies"]["d1_30"], rows["Local Supplies"]["d61_90"],
                          rows["Local Supplies"]["d90_plus"], rows["Local Supplies"]["total"]), ("300.00", "700.00", "800.00", "900.00", "2700.00"))
        self.assertEqual((rows["Far Away Traders"]["d31_60"], rows["Far Away Traders"]["d61_90"], rows["Far Away Traders"]["total"]),
                         ("1100.00", "700.00", "1800.00"))
        self.assertEqual([r["vendor"] for r in d["rows"]], ["Local Supplies", "Far Away Traders"])
        self.assertEqual(self.fin.get(f"{P}/reports/ageing/", {"as_of": "bad"}).status_code, 400)

    def test_ageing_includes_opening_balances_and_shows_advances_apart(self):
        v = self.vendor("Old Supplier", opening_balance="1500", opening_balance_date="2026-06-15")
        self.pay(v, amount="200", date="2026-10-01")  # a pure advance
        d = self.fin.get(f"{P}/reports/ageing/", {"as_of": AS_OF}).json()
        row = d["rows"][0]
        self.assertEqual((row["vendor"], row["d90_plus"], row["total"], row["advance"]), ("Old Supplier", "1500.00", "1500.00", "200.00"))

    def test_purchases_report_by_vendor_month_and_category(self):
        rent = str(self.account("5200").id)
        self.bill(self.local, bill_date="2026-08-10")                                           # 10,620 (9,000 cost)
        self.bill(self.local, bill_date="2026-09-10", items=[{**ITEMS[0], "account": rent}])    # 10,620 (9,000 rent)
        self.bill(self.far, bill_date="2026-09-20", items=ONE)                                  # 1,000
        self.bill(self.far, bill_date="2026-09-21", items=ONE, record=False)                    # draft: ignored
        d = self.fin.get(f"{P}/reports/purchases/", {"from": "2026-08-01", "to": "2026-09-30"}).json()
        self.assertEqual((d["total"], d["bills"]), ("22240.00", 3))
        self.assertEqual([(r["vendor"], r["total"], r["bills"]) for r in d["by_vendor"]], [("Local Supplies", "21240.00", 2), ("Far Away Traders", "1000.00", 1)])
        self.assertEqual([(m["month"], m["total"]) for m in d["by_month"]], [("2026-08", "10620.00"), ("2026-09", "11620.00")])
        self.assertEqual([(c["category"], c["amount"]) for c in d["by_category"]],
                         [("5000 Direct costs of services", "10000.00"), ("5200 Rent", "9000.00")])
        self.assertEqual(self.fin.get(f"{P}/reports/purchases/", {"from": "2026-10-01", "to": "2026-09-01"}).status_code, 400)

    def test_due_calendar_lists_the_next_30_days_and_totals_the_overdue(self):
        self.bill_due(self.local, "2026-10-20", "111")    # overdue
        self.bill_due(self.local, "2026-11-02", "222")
        self.bill_due(self.far, "2026-11-02", "333")
        self.bill_due(self.far, "2026-11-30", "444")      # day 30
        self.bill_due(self.far, "2026-12-01", "555")      # beyond 30 days
        d = self.fin.get(f"{P}/reports/due-calendar/", {"as_of": AS_OF}).json()
        self.assertEqual([(x["date"], x["amount"], len(x["bills"])) for x in d["days"]], [("2026-11-02", "555.00", 2), ("2026-11-30", "444.00", 1)])
        self.assertEqual((d["due_total"], d["overdue_total"], d["overdue_count"]), ("999.00", "111.00", 1))

    def test_dashboard_numbers(self):
        self.bill_due(self.local, "2026-10-01", "1000", bill_date="2026-09-01")
        self.bill(self.far, bill_date="2026-10-05", due_date="2026-11-30", items=ONE)
        o = self.issue_order(self.order())
        d = self.fin.get(f"{P}/dashboard/", {"as_of": AS_OF}).json()
        self.assertEqual(d["payables"]["outstanding"], "2000.00")
        self.assertEqual(d["payables"]["overdue"], "1000.00")
        self.assertEqual(d["this_month"], {"billed": "1000.00", "bills": 1})
        self.assertEqual((d["open_orders"]["count"], d["open_orders"]["value"]), (1, "10620.00"))
        self.assertEqual([m["month"] for m in d["monthly"]], ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"])
        self.assertEqual(d["monthly"][-1]["billed"], "1000.00")
        self.assertEqual(len(d["aging"]), 5)
        self.assertEqual(d["top_vendors"][0]["total"], "1000.00")
        self.assertEqual(d["order_counts"], {"issued": 1})
        self.assertEqual(d["bill_counts"]["overdue"], 1)


class PermissionTests(PurchasesBase):
    VIEW = {Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER, Role.PROJECT_MANAGER}
    MANAGE = {Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER}

    def setUp(self):
        super().setUp()
        self.clients = {role: client_for(make_user(f"u_{role}", role)) for role in ALL_ROLES}
        self.bill_ = self.bill(self.local)
        self.order_ = self.order()

    def test_every_read_endpoint_needs_purchases_view(self):
        urls = ["dashboard/", "options/", "vendors/", f"vendors/{self.local['id']}/", f"vendors/{self.local['id']}/statement/",
                f"vendors/{self.local['id']}/open-items/", "requests/", "quotations/", "orders/", f"orders/{self.order_['id']}/",
                "bills/", f"bills/{self.bill_['id']}/", "payments/", "reports/ageing/", "reports/purchases/", "reports/due-calendar/"]
        for role, client in self.clients.items():
            for url in urls:
                r = client.get(f"{P}/{url}")
                self.assertEqual(r.status_code, 200 if role in self.VIEW else 403, (role, url))

    def test_lookup_is_open_to_purchases_or_accounting_viewers(self):
        for role, client in self.clients.items():
            r = client.get(f"{P}/vendors/lookup/")
            self.assertEqual(r.status_code, 200 if role in self.VIEW else 403, role)

    def test_every_write_endpoint_needs_purchases_manage(self):
        o, b = self.order_["id"], self.bill_["id"]
        writes = [("post", "vendors/", {"name": "X"}), ("patch", f"vendors/{self.local['id']}/", {"city": "X"}),
                  ("delete", f"vendors/{self.far['id']}/", None), ("post", "requests/", {}), ("post", "quotations/", {}),
                  ("post", "orders/", {}), ("patch", f"orders/{o}/", {}), ("delete", f"orders/{o}/", None),
                  ("post", f"orders/{o}/submit/", {}), ("post", f"orders/{o}/approve/", {}), ("post", f"orders/{o}/reject/", {}),
                  ("post", f"orders/{o}/issue/", {}), ("post", f"orders/{o}/receive/", {}), ("post", f"orders/{o}/close/", {}),
                  ("post", f"orders/{o}/cancel/", {}), ("post", f"orders/{o}/bill/", {}), ("post", "bills/", {}),
                  ("post", f"bills/{b}/void/", {}), ("post", f"bills/{b}/record/", {}), ("patch", f"bills/{b}/", {}),
                  ("post", "payments/", {}), ("post", f"payments/{b}/void/", {}), ("post", f"payments/{b}/apply/", {})]
        for role, client in self.clients.items():
            if role in self.MANAGE:
                continue
            for method, url, body in writes:
                r = getattr(client, method)(f"{P}/{url}", *([body] if body is not None else []))
                self.assertEqual(r.status_code, 403, (role, method, url))
        # nothing was changed by the refused calls
        self.assertEqual(self.get_bill(self.bill_)["status"], "open")
        self.assertEqual(self.fin.get(f"{P}/orders/{o}/").json()["status"], "draft")

    def test_managers_can_write(self):
        for role in self.MANAGE:
            r = self.clients[role].post(f"{P}/vendors/", {"name": f"By {role}"})
            self.assertEqual(r.status_code, 201, role)

    def test_anonymous_is_refused(self):
        for url in ("vendors/", "bills/", "dashboard/"):
            self.assertEqual(self.anon.get(f"{P}/{url}").status_code, 401)
        self.assertEqual(self.anon.post(f"{P}/vendors/", {"name": "x"}).status_code, 401)

    def test_unknown_ids_are_404(self):
        ghost = "00000000-0000-0000-0000-000000000000"
        for url in (f"vendors/{ghost}/", f"vendors/{ghost}/statement/", f"vendors/{ghost}/open-items/", f"requests/{ghost}/",
                    f"quotations/{ghost}/", f"orders/{ghost}/", f"orders/{ghost}/pdf/", f"bills/{ghost}/", f"payments/{ghost}/"):
            self.assertEqual(self.fin.get(f"{P}/{url}").status_code, 404, url)
        for url in (f"vendors/{ghost}/", f"orders/{ghost}/", f"bills/{ghost}/", f"requests/{ghost}/", f"quotations/{ghost}/"):
            self.assertEqual(self.fin.patch(f"{P}/{url}", {}).status_code, 404, url)
            self.assertEqual(self.fin.delete(f"{P}/{url}").status_code, 404, url)
        for url in (f"orders/{ghost}/issue/", f"orders/{ghost}/receive/", f"orders/{ghost}/bill/", f"bills/{ghost}/record/",
                    f"bills/{ghost}/void/", f"payments/{ghost}/void/", f"payments/{ghost}/apply/", f"requests/{ghost}/submit/",
                    f"requests/{ghost}/convert/", f"quotations/{ghost}/accept/", f"quotations/{ghost}/convert/"):
            self.assertEqual(self.fin.post(f"{P}/{url}", {}).status_code, 404, url)
        self.assertEqual(self.fin.get(f"{P}/vendors/not-a-uuid/").status_code, 404)

    def test_options_describe_the_forms(self):
        d = self.fin.get(f"{P}/options/").json()
        self.assertEqual(d["gst_rates"], ["0", "0.25", "3", "5", "18", "40"])
        self.assertEqual(d["company_state_code"], "29")
        self.assertIn("5000", [a["code"] for a in d["expense_accounts"]])
        self.assertEqual([m["value"] for m in d["modes"]], ["bank", "cash", "upi", "cheque"])
        self.assertTrue(any(t["section"] == "194C" for t in d["tds_sections"]))
