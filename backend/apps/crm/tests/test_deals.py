import datetime
from decimal import Decimal

from apps.company.models import CompanyProfileAuditEvent
from apps.crm.models import Account, Deal, DealStageHistory
from apps.crm.services import deals as deal_service
from apps.sales.models import Customer

from .base import C, CrmBase

Event = CompanyProfileAuditEvent


class StageMoveTests(CrmBase):
    def test_probability_and_weighted_value_follow_the_stage(self):
        deal = self.deal(amount="100000")
        self.assertEqual((deal["stage"]["name"], deal["probability"], deal["weighted"]), ("Qualification", 10, "10000.00"))
        for stage, prob, weighted in (("Needs Analysis", 25, "25000.00"), ("Proposal/Quotation", 50, "50000.00"),
                                      ("Negotiation", 75, "75000.00"), ("Qualification", 10, "10000.00")):
            d = self.move(deal, stage)
            self.assertEqual((d["probability"], d["weighted"], d["status"]), (prob, weighted, "open"))
        history = self.crm.get(f"{C}/deals/{deal['id']}/").json()["history"]
        self.assertEqual([(h["from"], h["to"]) for h in history],
                         [("", "Qualification"), ("Qualification", "Needs Analysis"), ("Needs Analysis", "Proposal/Quotation"),
                          ("Proposal/Quotation", "Negotiation"), ("Negotiation", "Qualification")])

    def test_losing_needs_a_reason_and_records_the_competitor(self):
        deal = self.deal()
        r = self.crm.post(f"{C}/deals/{deal['id']}/move/", {"stage": self.stage("Closed Lost")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("lost_reason", r.json())
        d = self.move(deal, "Closed Lost", lost_reason="Price too high", competitor="Rival Tech")
        self.assertEqual((d["status"], d["probability"], d["weighted"], d["lost_reason"], d["competitor"]),
                         ("lost", 0, "0.00", "Price too high", "Rival Tech"))
        self.assertEqual(d["closed_on"], datetime.date.today().isoformat())
        reopened = self.move(deal, "Negotiation")
        self.assertEqual((reopened["status"], reopened["lost_reason"], reopened["closed_on"], reopened["probability"]), ("open", "", None, 75))

    def test_move_to_unknown_or_switched_off_stage_and_unknown_deal(self):
        deal = self.deal()
        self.assertEqual(self.crm.post(f"{C}/deals/{deal['id']}/move/", {"stage": "00000000-0000-0000-0000-000000000000"}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/deals/{deal['id']}/move/", {}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/deals/00000000-0000-0000-0000-000000000000/move/", {"stage": self.stage("Negotiation")}).status_code, 404)

    def test_moving_is_audited_with_before_and_after(self):
        deal = self.deal()
        self.move(deal, "Needs Analysis")
        ev = Event.objects.filter(entity_type="deal", entity_id=deal["id"], reason="Moved to Needs Analysis").get()
        self.assertEqual(ev.previous_values["probability"], 10)
        self.assertEqual(ev.new_values["probability"], 25)
        self.assertIn("stage", ev.changed_fields)


class ForecastTests(CrmBase):
    def test_weighted_forecast_is_hand_computed_and_rounds_each_deal(self):
        a = self.account()
        self.deal(a, "D1", "100000")                                       # Qualification 10%  = 10000.00
        d2 = self.deal(a, "D2", "250000.50")
        self.move(d2, "Needs Analysis")                                    # 25%  = 62500.125 -> 62500.13
        d3 = self.deal(a, "D3", "40000")
        self.move(d3, "Negotiation")                                       # 75%  = 30000.00
        d4 = self.deal(a, "D4", "80000")
        self.move(d4, "Proposal/Quotation")                                # 50%  = 40000.00
        d5 = self.deal(a, "D5", "999999")
        self.move(d5, "Closed Lost", lost_reason="No budget")              # excluded
        value, weighted, count = deal_service.forecast(a_company(), owner=None)
        self.assertEqual((value, weighted, count), (Decimal("470000.50"), Decimal("142500.13"), 4))
        tiles = self.crm.get(f"{C}/dashboard/").json()["tiles"]
        self.assertEqual((tiles["open_deals"], tiles["open_value"], tiles["weighted_forecast"]), (4, "470000.50", "142500.13"))
        self.assertEqual(tiles["win_rate"], 0.0)  # one closed deal in the year, and it was lost
        pipeline = {p["label"]: (p["count"], p["value"], p["weighted"]) for p in self.crm.get(f"{C}/dashboard/").json()["pipeline"]}
        self.assertEqual(pipeline["Needs Analysis"], (1, "250000.50", "62500.13"))
        self.assertEqual(pipeline["Qualification"], (1, "100000.00", "10000.00"))
        board = self.crm.get(f"{C}/deals/board/").json()
        self.assertEqual(board["totals"], {"open_count": 4, "open_value": "470000.50", "weighted": "142500.13"})
        listing = self.crm.get(f"{C}/deals/", {"status": "open"}).json()
        self.assertEqual((listing["total_amount"], listing["total_weighted"]), ("470000.50", "142500.13"))

    def test_forecast_by_close_month_and_ageing(self):
        a = self.account()
        today = datetime.date.today()
        this_month = today.replace(day=28) if today.day <= 28 else today
        next_month = (today.replace(day=1) + datetime.timedelta(days=32)).replace(day=15)
        self.deal(a, "Soon", "100000", expected_close_date=this_month.isoformat())
        later = self.deal(a, "Next", "200000", expected_close_date=next_month.isoformat())
        self.move(later, "Negotiation")
        self.deal(a, "Nodate", "50000")
        old = self.deal(a, "Old", "70000", expected_close_date=(today - datetime.timedelta(days=10)).isoformat())
        Deal.objects.filter(pk=old["id"]).update(created_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=95))
        dash = self.crm.get(f"{C}/dashboard/").json()
        by = {f["key"]: f for f in dash["forecast"]}
        self.assertEqual(by[this_month.strftime("%Y-%m")]["value"], "100000.00")
        self.assertEqual(by[next_month.strftime("%Y-%m")]["weighted"], "150000.00")  # 200000 x 75%
        self.assertEqual(dash["forecast_other"]["undated"]["value"], "50000.00")
        self.assertEqual(dash["forecast_other"]["past_due"]["value"], "70000.00")
        aging = {x["key"]: (x["count"], x["value"]) for x in dash["aging"]}
        self.assertEqual(aging["d91_plus"], (1, "70000.00"))
        self.assertEqual(aging["d0_30"], (3, "350000.00"))

    def test_win_rate_cycle_and_the_funnel(self):
        a = self.account()
        now = datetime.datetime.now(datetime.timezone.utc)
        won1, won2, lost = self.deal(a, "W1", "100"), self.deal(a, "W2", "300"), self.deal(a, "L1", "50")
        self.move(won1, "Proposal/Quotation")
        self.move(won1, "Closed Won")
        self.move(won2, "Closed Won")
        self.move(lost, "Closed Lost", lost_reason="Timing not right")
        Deal.objects.filter(pk=won1["id"]).update(created_at=now - datetime.timedelta(days=10))
        Deal.objects.filter(pk=won2["id"]).update(created_at=now - datetime.timedelta(days=20))
        self.lead(company_name="L-A", status="qualified")
        self.lead(company_name="L-B", status="new")
        self.lead(company_name="L-C", status="contacted")
        dash = self.crm.get(f"{C}/dashboard/").json()
        t = dash["tiles"]
        self.assertEqual((t["won_12m"], t["lost_12m"], t["win_rate"], t["avg_cycle_days"]), (2, 1, 66.7, 15.0))
        self.assertEqual(t["won_value_12m"], "400.00")
        self.assertEqual(t["leads_this_month"], 3)
        funnel = {f["key"]: f["count"] for f in dash["funnel"]}
        # 3 leads, 1 qualified, 2 won deals reached the proposal stage (W1 via history, W2 by winning), 2 won
        self.assertEqual(funnel, {"leads": 3, "qualified": 1, "proposal": 2, "won": 2})
        trend = dash["trend"][-1]
        self.assertEqual((trend["won"], trend["lost"], trend["won_value"]), (2, 1, "400.00"))
        self.assertEqual(sum(s["count"] for s in dash["sources"]), 3)


def a_company():
    from apps.company.api import current_company
    return current_company()


class WinFlowTests(CrmBase):
    def test_winning_creates_a_customer_and_links_the_account_and_deal(self):
        acct = self.account("Zenith Labs", email="hello@zenith.in", phone="9876500011", city="Pune", address="12 MG Road")
        self.contact(acct, "Mira Rao", email="mira@zenith.in", is_primary=True)
        deal = self.deal(acct, "Zenith ERP", "500000")
        self.assertEqual(Customer.objects.count(), 0)
        self.move(deal, "Negotiation")
        self.assertEqual(Customer.objects.count(), 0)  # still no customer while the deal is open
        won = self.move(deal, "Closed Won")
        self.assertEqual((won["status"], won["probability"], won["weighted"]), ("won", 100, "500000.00"))
        cust = Customer.objects.get()
        self.assertEqual((cust.display_name, cust.contact_name, cust.email, cust.phone, cust.city, cust.address_line1),
                         ("Zenith Labs", "Mira Rao", "hello@zenith.in", "9876500011", "Pune", "12 MG Road"))
        self.assertRegex(cust.reference_code, r"^T2N-CUS-\d{2}-\d{4}-0001$")
        self.assertEqual(won["customer"]["id"], str(cust.pk))
        self.assertEqual(self.crm.get(f"{C}/accounts/{acct['id']}/").json()["customer"]["reference_code"], cust.reference_code)
        self.assertTrue(Event.objects.filter(entity_type="customer", entity_id=cust.pk, action="created").exists())
        # the sales module sees it as an ordinary customer
        self.assertEqual(self.api.get("/api/v1/sales/customers/").json()[0]["display_name"], "Zenith Labs")

    def test_a_second_win_for_the_same_account_reuses_the_customer(self):
        acct = self.account("Repeat Buyers")
        for name in ("First", "Second"):
            self.move(self.deal(acct, name, "1000"), "Closed Won")
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(Deal.objects.filter(customer_id=Customer.objects.get().pk).count(), 2)

    def test_an_existing_customer_with_the_same_name_is_linked_not_duplicated(self):
        r = self.api.post("/api/v1/sales/customers/", {"display_name": "Old Client"})
        self.assertEqual(r.status_code, 201)
        acct = self.account("old client")
        won = self.move(self.deal(acct), "Closed Won")
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(won["customer"]["id"], r.json()["id"])

    def test_an_account_that_cannot_become_a_customer_blocks_the_win_and_changes_nothing(self):
        acct = self.account("Broken GST Co")
        deal = self.deal(acct)
        Account.objects.filter(pk=acct["id"]).update(gstin="BADGSTIN")
        r = self.crm.post(f"{C}/deals/{deal['id']}/move/", {"stage": self.stage("Closed Won")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("account", r.json())
        now = self.crm.get(f"{C}/deals/{deal['id']}/").json()
        self.assertEqual((now["status"], now["stage"]["name"]), ("open", "Qualification"))
        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(DealStageHistory.objects.filter(deal_id=deal["id"]).count(), 1)

    def test_a_valid_gstin_makes_a_registered_customer(self):
        from apps.company.tests.base import make_gstin
        gstin = make_gstin("27", "AAAPA1234A")
        acct = self.account("Mumbai Traders", gstin=gstin.lower())
        self.move(self.deal(acct), "Closed Won")
        cust = Customer.objects.get()
        self.assertEqual((cust.gstin, cust.gst_treatment, cust.state_code), (gstin, "registered", "27"))

    def test_won_deal_cannot_be_deleted_and_the_customer_link_can_be_repaired(self):
        acct = self.account("Keep Me")
        deal = self.deal(acct)
        self.move(deal, "Closed Won")
        self.assertEqual(self.crm.delete(f"{C}/deals/{deal['id']}/").status_code, 400)
        Deal.objects.filter(pk=deal["id"]).update(customer_id=None)
        r = self.crm.post(f"{C}/deals/{deal['id']}/customer/", {})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["customer"]["name"], "Keep Me")
        open_deal = self.deal(acct, "Open one")
        self.assertEqual(self.crm.post(f"{C}/deals/{open_deal['id']}/customer/", {}).status_code, 400)

    def test_project_can_be_linked_after_the_win(self):
        deal = self.deal()
        won = self.move(deal, "Closed Won")
        pid = "11111111-2222-3333-4444-555555555555"
        r = self.crm.patch(f"{C}/deals/{won['id']}/", {"project_id": pid})
        self.assertEqual(r.json()["project_id"], pid)
        self.assertEqual(self.crm.patch(f"{C}/deals/{won['id']}/", {"project_id": "nope"}).status_code, 400)
        self.assertIsNone(self.crm.patch(f"{C}/deals/{won['id']}/", {"project_id": ""}).json()["project_id"])


class DealItemsTests(CrmBase):
    def test_deal_value_is_the_sum_of_the_lines(self):
        catalogue = self.crm.post(f"{C}/products/", {"name": "Support plan", "code": "sup-1", "unit_price": "999.99", "gst_rate": "18"}).json()
        self.assertEqual(catalogue["code"], "SUP-1")
        deal = self.deal(amount="5")
        items = [{"description": "Custom software", "quantity": "2", "unit_price": "15000.50", "gst_rate": "18"},
                 {"product": catalogue["id"], "quantity": "3", "unit_price": "333.33", "gst_rate": "0"}]
        r = self.crm.put(f"{C}/deals/{deal['id']}/items/", {"items": items})
        self.assertEqual(r.status_code, 200, r.content)
        out = r.json()
        # 2 x 15000.50 = 30001.00 ; 3 x 333.33 = 999.99 ; value 31000.99 ; GST 18% of 30001.00 = 5400.18
        self.assertEqual(out["amount"], "31000.99")
        self.assertEqual(out["item_totals"], {"taxable": "31000.99", "tax": "5400.18", "total": "36401.17"})
        self.assertEqual([i["line_total"] for i in out["items"]], ["30001.00", "999.99"])
        self.assertEqual(out["weighted"], "3100.10")  # 10% of 31000.99 = 3100.099 -> 3100.10
        self.assertEqual(self.crm.patch(f"{C}/deals/{deal['id']}/", {"amount": "10"}).status_code, 400)
        # a catalogue line with no description or price takes them from the product
        r = self.crm.put(f"{C}/deals/{deal['id']}/items/", {"items": [{"product": catalogue["id"], "quantity": "1"}]}).json()
        self.assertEqual((r["items"][0]["description"], r["amount"], r["items"][0]["gst_rate"]), ("Support plan", "999.99", "18.00"))

    def test_item_validation_and_closed_deals(self):
        deal = self.deal()
        for bad, key in (([{"description": "", "quantity": "1", "unit_price": "5"}], "items.0.description"),
                         ([{"description": "x", "quantity": "0", "unit_price": "5"}], "items.0.quantity"),
                         ([{"description": "x", "quantity": "1", "unit_price": "-5"}], "items.0.unit_price"),
                         ([{"description": "x", "quantity": "1", "unit_price": "5", "gst_rate": "150"}], "items.0.gst_rate"),
                         ([{"description": "x", "product": "00000000-0000-0000-0000-000000000000"}], "items.0.product")):
            r = self.crm.put(f"{C}/deals/{deal['id']}/items/", {"items": bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(key, r.json())
        self.assertEqual(self.crm.put(f"{C}/deals/{deal['id']}/items/", {"items": "x"}).status_code, 400)
        self.move(deal, "Closed Won")
        self.assertEqual(self.crm.put(f"{C}/deals/{deal['id']}/items/", {"items": []}).status_code, 400)

    def test_products_cannot_be_deleted_once_used_and_codes_are_unique(self):
        p = self.crm.post(f"{C}/products/", {"name": "Audit", "code": "AUD"}).json()
        self.assertEqual(self.crm.post(f"{C}/products/", {"name": "Other", "code": "aud"}).status_code, 400)
        deal = self.deal()
        self.crm.put(f"{C}/deals/{deal['id']}/items/", {"items": [{"product": p["id"], "unit_price": "10"}]})
        self.assertEqual(self.crm.delete(f"{C}/products/{p['id']}/").status_code, 400)
        self.assertEqual(self.crm.patch(f"{C}/products/{p['id']}/", {"is_active": False}).json()["is_active"], False)
        self.assertEqual(self.crm.get(f"{C}/products/", {"active": "true"}).json(), [])
        q = self.crm.post(f"{C}/products/", {"name": "Spare"}).json()
        self.assertEqual(self.crm.delete(f"{C}/products/{q['id']}/").status_code, 204)


class DealCrudTests(CrmBase):
    def test_create_validation(self):
        acct = self.account()
        for bad, field in (({"account": ""}, "account"), ({"name": ""}, "name"), ({"amount": "-1"}, "amount"),
                           ({"expected_close_date": "31/12/2026"}, "expected_close_date"), ({"stage": self.stage("Closed Won")}, "stage"),
                           ({"probability": "150"}, "probability")):
            payload = {"name": "Deal", "account": acct["id"], **bad}
            r = self.crm.post(f"{C}/deals/", payload)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json(), bad)
        other = self.account("Other Co")
        stranger = self.contact(other, "Not theirs")
        r = self.crm.post(f"{C}/deals/", {"name": "Deal", "account": acct["id"], "contact": stranger["id"]})
        self.assertEqual(r.status_code, 400)

    def test_edit_filters_board_and_delete(self):
        a = self.account("Filter Co")
        d1 = self.deal(a, "Alpha deal", "1000", expected_close_date="2026-12-31", next_step="Send proposal")
        d2 = self.deal(a, "Beta deal", "5000")
        self.move(d2, "Negotiation")
        r = self.crm.patch(f"{C}/deals/{d1['id']}/", {"name": "Alpha deal 2", "next_step": "Call", "owner": str(self.mp.id)})
        self.assertEqual((r.json()["name"], r.json()["owner"]["id"]), ("Alpha deal 2", str(self.mp.id)))
        self.assertEqual(self.crm.get(f"{C}/deals/", {"stage": self.stage("Negotiation")}).json()["count"], 1)
        self.assertEqual(self.crm.get(f"{C}/deals/", {"q": "alpha"}).json()["count"], 1)
        self.assertEqual(self.crm.get(f"{C}/deals/", {"close_from": "2026-12-01", "close_to": "2026-12-31"}).json()["count"], 1)
        board = self.crm.get(f"{C}/deals/board/").json()
        cols = {c["name"]: c for c in board["columns"]}
        self.assertEqual(list(cols), ["Qualification", "Needs Analysis", "Proposal/Quotation", "Negotiation", "Closed Won", "Closed Lost"])
        self.assertEqual([x["name"] for x in cols["Negotiation"]["deals"]], ["Beta deal"])
        self.assertEqual(cols["Negotiation"]["probability"], 75)
        self.assertEqual(self.crm.get(f"{C}/deals/board/", {"q": "nomatch"}).json()["totals"]["open_count"], 0)
        self.assertEqual(self.crm.delete(f"{C}/deals/{d1['id']}/").status_code, 204)
        self.assertTrue(Event.objects.filter(entity_type="deal", entity_id=d1["id"], action="deleted").exists())

    def test_closed_probability_is_fixed_and_open_probability_can_be_overridden(self):
        deal = self.deal()
        self.assertEqual(self.crm.patch(f"{C}/deals/{deal['id']}/", {"probability": 40}).json()["probability"], 40)
        self.assertEqual(self.crm.patch(f"{C}/deals/{deal['id']}/", {"probability": 40}).json()["weighted"], "40000.00")
        self.move(deal, "Closed Lost", lost_reason="No budget")
        self.assertEqual(self.crm.patch(f"{C}/deals/{deal['id']}/", {"probability": 40}).status_code, 400)

    def test_quotations_of_the_customer_are_listed_read_only(self):
        acct = self.account("Quoted Co")
        deal = self.deal(acct)
        r = self.crm.get(f"{C}/deals/{deal['id']}/").json()
        self.assertEqual(r["quotations"], [])
        self.assertEqual(self.crm.get(f"{C}/accounts/{acct['id']}/").json()["quotations"], [])
