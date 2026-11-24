from decimal import Decimal

from apps.accounting.models import Account, JournalEntry
from apps.company.models import CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_gstin, make_user
from apps.accounts.models import Role

from .base import ITEMS, P, PAN_IN, PurchasesBase


class VendorTests(PurchasesBase):
    def test_new_vendor_gets_an_automatic_code_with_year_and_date(self):
        self.assertRegex(self.local["code"], r"^T2N-VEN-\d{2}-\d{4}-0001$")
        self.assertRegex(self.far["code"], r"^T2N-VEN-\d{2}-\d{4}-0002$")

    def test_gstin_state_and_pan_are_checked_and_state_is_derived(self):
        self.assertEqual((self.far["state_code"], self.far["state"]), ("27", "Maharashtra"))
        g = make_gstin("33", "CCCPC1234C")
        v = self.vendor("Chennai Co", gst_treatment="registered", gstin=g.lower(), pan="cccpc1234c")
        self.assertEqual((v["gstin"], v["state_code"], v["state"]), (g, "33", "Tamil Nadu"))
        bad = [({"gstin": "NOTAGSTIN"}, "gstin"), ({"gst_treatment": "registered"}, "gstin"), ({"pan": "BAD"}, "pan"),
               ({"state_code": "55"}, "state_code"), ({"gstin": make_gstin("29", "DDDPD1234D"), "state_code": "27"}, "state_code"),
               ({"gstin": make_gstin("29", "DDDPD1234D"), "pan": "EEEPE1234E"}, "gstin"),
               ({"bank_ifsc": "bad"}, "bank_ifsc"), ({"bank_account_number": "12"}, "bank_account_number"),
               ({"postal_code": "123"}, "postal_code"), ({"tds_section": "194C"}, "tds_rate"), ({"tds_rate": "5"}, "tds_section"),
               ({"tds_section": "194C", "tds_rate": "101"}, "tds_rate"), ({"payment_terms_days": "abc"}, "payment_terms_days"),
               ({"opening_balance": "-5"}, "opening_balance"), ({"email": "not-an-email"}, "email")]
        for payload, field in bad:
            r = self.fin.post(f"{P}/vendors/", {"name": "Bad " + field, **payload})
            self.assertEqual(r.status_code, 400, payload)
            self.assertIn(field, r.json(), payload)
        self.assertEqual(self.fin.post(f"{P}/vendors/", {"name": ""}).status_code, 400)

    def test_duplicates_are_refused(self):
        self.assertEqual(self.fin.post(f"{P}/vendors/", {"name": "local supplies"}).status_code, 400)
        r = self.fin.post(f"{P}/vendors/", {"name": "Twin", "gst_treatment": "registered", "gstin": self.local["gstin"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("gstin", r.json())

    def test_pan_and_bank_details_are_masked_in_lists_and_for_people_who_cannot_manage(self):
        v = self.vendor("Banked", pan="FFFPF1234F", bank_account_number="123456789012", bank_ifsc="HDFC0001234",
                        bank_name="HDFC", bank_account_holder="Banked LLP")
        row = [x for x in self.fin.get(f"{P}/vendors/").json() if x["id"] == v["id"]][0]
        self.assertEqual((row["pan"], row["bank_account_number"], row["bank_ifsc"]), ("XXXXXX234F", "XXXXXX9012", "XXXXXXX1234"))
        self.assertNotIn("123456789012", str(self.fin.get(f"{P}/vendors/").json()))
        manager = self.fin.get(f"{P}/vendors/{v['id']}/").json()
        self.assertEqual((manager["pan"], manager["bank_account_number"], manager["bank_ifsc"]), ("FFFPF1234F", "123456789012", "HDFC0001234"))
        partner = client_for(make_user("pat", Role.PROJECT_MANAGER)).get(  # can view, cannot manage; a Partner now holds every permission
            f"{P}/vendors/{v['id']}/").json()
        self.assertEqual((partner["pan"], partner["bank_account_number"]), ("XXXXXX234F", "XXXXXX9012"))
        self.assertNotIn("123456789012", str(partner))
        # a masked value sent back unchanged does not overwrite the stored number
        r = self.fin.patch(f"{P}/vendors/{v['id']}/", {"bank_account_number": "XXXXXX9012", "city": "Pune"})
        self.assertEqual((r.json()["bank_account_number"], r.json()["city"]), ("123456789012", "Pune"))

    def test_edit_search_and_switch_off(self):
        r = self.fin.patch(f"{P}/vendors/{self.local['id']}/", {"city": "Mysuru", "payment_terms_days": 45, "tds_section": "194C", "tds_rate": "2"})
        self.assertEqual((r.json()["city"], r.json()["payment_terms_days"], r.json()["tds_rate"]), ("Mysuru", 45, "2.00"))
        self.assertEqual([v["name"] for v in self.fin.get(f"{P}/vendors/", {"q": "far away"}).json()], ["Far Away Traders"])
        self.assertEqual([v["name"] for v in self.fin.get(f"{P}/vendors/", {"q": self.local["code"]}).json()], ["Local Supplies"])
        self.assertEqual(self.fin.patch(f"{P}/vendors/{self.far['id']}/", {"is_active": False}).status_code, 200)
        self.assertEqual(len(self.fin.get(f"{P}/vendors/", {"active": "true"}).json()), 1)
        r = self.fin.post(f"{P}/orders/", {"vendor": self.far["id"], "order_date": "2026-08-15", "form": {"items": ITEMS}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("vendor", r.json())

    def test_a_vendor_we_owe_money_cannot_be_switched_off_and_one_with_records_cannot_be_deleted(self):
        self.bill()
        r = self.fin.patch(f"{P}/vendors/{self.local['id']}/", {"is_active": False})
        self.assertEqual(r.status_code, 400)
        self.assertIn("is_active", r.json())
        self.assertEqual(self.fin.delete(f"{P}/vendors/{self.local['id']}/").status_code, 400)
        self.assertEqual(self.fin.delete(f"{P}/vendors/{self.far['id']}/").status_code, 204)
        self.assertEqual(self.fin.get(f"{P}/vendors/{self.far['id']}/").status_code, 404)

    def test_lookup_follows_the_contract(self):
        rows = self.fin.get(f"{P}/vendors/lookup/").json()
        self.assertEqual(sorted(rows[0].keys()), ["code", "gstin", "id", "name"])
        self.assertEqual([r["name"] for r in rows], ["Far Away Traders", "Local Supplies"])
        self.fin.patch(f"{P}/vendors/{self.far['id']}/", {"is_active": False})
        self.assertEqual([r["name"] for r in self.fin.get(f"{P}/vendors/lookup/").json()], ["Local Supplies"])

    def test_vendor_changes_are_audited_without_personal_data(self):
        self.fin.patch(f"{P}/vendors/{self.local['id']}/", {"email": "a@b.com", "city": "Hassan"})
        events = CompanyProfileAuditEvent.objects.filter(entity_type="vendor", entity_id=self.local["id"]).order_by("occurred_at")
        self.assertEqual([e.action for e in events], ["created", "updated"])
        created, updated = events
        self.assertEqual(created.new_values["gstin"], "[redacted]")
        self.assertEqual(updated.new_values, {"city": "Hassan", "email": "[redacted]"})
        self.assertEqual(created.category, "purchases")


class OpeningBalanceTests(PurchasesBase):
    def test_no_opening_balance_posts_nothing(self):
        self.assertFalse(JournalEntry.objects.filter(source="opening").exists())

    def test_opening_balance_is_posted_against_payable_and_cannot_be_changed(self):
        v = self.vendor("Old Supplier", opening_balance="25000.50", opening_balance_date="2026-04-01")
        self.assertEqual((v["outstanding"], v["opening_outstanding"], v["opening_posted"]), ("25000.50", "25000.50", True))
        lines = self.lines_of("opening", f"vendor:{v['id']}")
        self.assertEqual(lines["2000"], (Decimal("0"), Decimal("25000.50")))
        self.assertEqual(lines["3900"], (Decimal("25000.50"), Decimal("0")))
        from apps.accounting.models import JournalLine
        self.assertEqual(JournalLine.objects.get(account__code="2000").party_id, v["id"])
        self.assert_books_balance()
        r = self.fin.patch(f"{P}/vendors/{v['id']}/", {"opening_balance": "1"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("opening_balance", r.json())
        self.assertEqual(self.fin.patch(f"{P}/vendors/{v['id']}/", {"city": "Pune"}).status_code, 200)

    def test_opening_balance_can_be_added_later_once(self):
        r = self.fin.patch(f"{P}/vendors/{self.local['id']}/", {"opening_balance": "1000", "opening_balance_date": "2026-04-01"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(JournalEntry.objects.filter(source="opening").count(), 1)

    def test_a_payment_can_be_applied_to_the_opening_balance(self):
        v = self.vendor("Old Supplier", opening_balance="1000", opening_balance_date="2026-04-01")
        self.pay(v, amount="400", allocations=[{"bill": "opening", "amount": "400"}])
        d = self.fin.get(f"{P}/vendors/{v['id']}/").json()
        self.assertEqual((d["opening_outstanding"], d["outstanding"], d["advance"]), ("600.00", "600.00", "0.00"))
        rows = self.fin.get(f"{P}/vendors/{v['id']}/statement/").json()["rows"]
        self.assertEqual([(r["type"], r["balance"]) for r in rows], [("Opening balance", "1000.00"), ("Payment", "600.00")])
        self.assert_books_balance()
        self.pay(v, amount="700", auto_allocate=True)  # 600 clears the opening balance, 100 stays as an advance
        d = self.fin.get(f"{P}/vendors/{v['id']}/").json()
        self.assertEqual((d["outstanding"], d["advance"], d["net_payable"]), ("0.00", "100.00", "-100.00"))
