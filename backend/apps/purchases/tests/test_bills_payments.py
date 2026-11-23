import datetime
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.company.models import CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user, pdf_stub
from apps.accounts.models import Role

from .base import ITEMS, P, PurchasesBase

D = Decimal


class BillPostingTests(PurchasesBase):
    def test_intra_state_bill_posts_expense_input_gst_and_payable(self):
        b = self.bill(self.local)
        self.assertEqual((b["status"], b["taxable_total"], b["tax_total"], b["grand_total"], b["net_payable"]),
                         ("open", "9000.00", "1620.00", "10620.00", "10620.00"))
        self.assertRegex(b["number"], r"^T2N/BILL/26/1608/00001$")
        lines = self.lines_of("vendor_bill", b["id"])
        self.assertEqual(lines, {"5000": (D("9000"), D("0")), "1310": (D("810"), D("0")), "1320": (D("810"), D("0")),
                                 "2000": (D("0"), D("10620"))})
        self.assertEqual(JournalLine.objects.get(account__code="2000").party_id, self.local["id"])
        self.assert_books_balance()

    def test_inter_state_bill_posts_igst(self):
        b = self.bill(self.far)
        self.assertEqual(self.lines_of("vendor_bill", b["id"]),
                         {"5000": (D("9000"), D("0")), "1330": (D("1620"), D("0")), "2000": (D("0"), D("10620"))})
        self.assert_books_balance()

    def test_tds_is_withheld_on_the_taxable_value(self):
        b = self.bill(self.local, tds_section="194C", tds_rate="2")
        self.assertEqual((b["tds_amount"], b["net_payable"], b["grand_total"], b["balance"]), ("180.00", "10440.00", "10620.00", "10440.00"))
        self.assertEqual(self.lines_of("vendor_bill", b["id"]),
                         {"5000": (D("9000"), D("0")), "1310": (D("810"), D("0")), "1320": (D("810"), D("0")),
                          "2000": (D("0"), D("10440")), "2150": (D("0"), D("180"))})
        self.assert_books_balance()
        p = self.pay(self.local, amount="10440", allocations=[{"bill": b["id"], "amount": "10440"}])
        self.assertEqual(self.get_bill(b)["status_key"], "paid")

    def test_tds_validation(self):
        self.bill(self.local, tds_rate="2", expect=400)                       # a rate needs a section
        self.bill(self.local, tds_section="194C", expect=400)                 # a section needs a rate
        self.bill(self.local, tds_section="194C", tds_rate="150", expect=400)

    def test_itc_ineligible_or_blocked_adds_the_gst_to_the_cost(self):
        for status in ("ineligible", "blocked"):
            b = self.bill(self.local, itc_status=status)
            self.assertEqual(self.lines_of("vendor_bill", b["id"]), {"5000": (D("10620"), D("0")), "2000": (D("0"), D("10620"))})
        self.bill(self.local, itc_status="maybe", expect=400)
        self.assert_books_balance()

    def test_each_line_can_go_to_its_own_account(self):
        rent = self.account("5200")
        items = [{"description": "Office rent", "quantity": "1", "rate": "20000", "gst_rate": "18", "account": str(rent.id)},
                 {"description": "Stationery", "quantity": "10", "rate": "100", "gst_rate": "5"}]
        b = self.bill(self.local, items=items)
        # 20,000 + 3,600 GST and 1,000 + 50 GST
        self.assertEqual(self.lines_of("vendor_bill", b["id"]),
                         {"5200": (D("20000"), D("0")), "5000": (D("1000"), D("0")), "1310": (D("1825"), D("0")),
                          "1320": (D("1825"), D("0")), "2000": (D("0"), D("24650"))})
        self.assertEqual(b["grand_total"], "24650.00")
        self.bill(self.local, items=[{**items[0], "account": str(self.account("2000").id)}], expect=400)  # not an expense account
        self.bill(self.local, items=[{**items[0], "account": "nope"}], expect=400)

    def test_chosen_expense_account_is_the_default_for_lines(self):
        rent = self.account("5200")
        b = self.bill(self.local, expense_account=str(rent.id))
        self.assertEqual(self.lines_of("vendor_bill", b["id"])["5200"], (D("9000"), D("0")))

    def test_unregistered_vendor_bill_has_no_gst(self):
        u = self.vendor("Unregistered Shop")
        b = self.bill(u, items=[{"description": "Chairs", "quantity": "2", "rate": "500"}])
        self.assertEqual((b["tax_total"], b["grand_total"]), ("0.00", "1000.00"))
        self.assertEqual(self.lines_of("vendor_bill", b["id"]), {"5000": (D("1000"), D("0")), "2000": (D("0"), D("1000"))})

    def test_project_tag_goes_to_every_posted_line(self):
        project = "11111111-2222-3333-4444-555555555555"
        b = self.bill(self.local, project=project)
        entry = JournalEntry.objects.get(source="vendor_bill", source_id=b["id"])
        self.assertEqual({str(l.project_id) for l in entry.lines.all()}, {project})
        self.assertEqual(self.fin.get(f"{P}/bills/", {"project": project}).json()["count"], 1)
        self.bill(self.local, project="not-a-uuid", expect=400)

    def test_draft_posts_nothing_and_can_be_edited_recorded_and_deleted(self):
        b = self.bill(self.local, record=False)
        self.assertEqual((b["status"], b["number"], b["balance"]), ("draft", "", None))
        self.assertFalse(JournalEntry.objects.filter(source="vendor_bill").exists())
        r = self.fin.patch(f"{P}/bills/{b['id']}/", {"items": [{**ITEMS[0], "quantity": "20"}]})
        self.assertEqual(r.json()["grand_total"], "21240.00")
        r = self.fin.post(f"{P}/bills/{b['id']}/record/")
        self.assertEqual(r.json()["status"], "open")
        self.assertTrue(JournalEntry.objects.filter(source="vendor_bill", source_id=b["id"]).exists())
        self.assertEqual(self.fin.patch(f"{P}/bills/{b['id']}/", {"notes": "x"}).status_code, 400)  # frozen
        self.assertEqual(self.fin.delete(f"{P}/bills/{b['id']}/").status_code, 400)
        self.assertEqual(self.fin.post(f"{P}/bills/{b['id']}/record/").status_code, 400)
        d = self.bill(self.local, record=False)
        self.assertEqual(self.fin.delete(f"{P}/bills/{d['id']}/").status_code, 204)
        self.assertEqual(self.fin.get(f"{P}/bills/{d['id']}/").status_code, 404)

    def test_due_date_defaults_to_the_vendor_terms_and_is_checked(self):
        self.assertEqual(self.bill(self.local)["due_date"], "2026-09-15")          # 30 days terms
        self.assertEqual(self.bill(self.far)["due_date"], "2026-08-16")            # no terms: due on the bill date
        self.assertEqual(self.bill(self.far, due_date="2026-10-01")["due_date"], "2026-10-01")
        self.bill(self.far, due_date="2026-08-01", expect=400)
        self.bill(self.far, bill_date="16/08/2026", expect=400)

    def test_the_same_vendor_invoice_cannot_be_booked_twice_unless_voided(self):
        b = self.bill(self.local, no="ABC/77")
        r = self.fin.post(f"{P}/bills/", {"vendor": self.local["id"], "vendor_bill_no": "abc/77", "bill_date": "2026-08-17", "items": ITEMS})
        self.assertEqual(r.status_code, 400)
        self.assertIn("vendor_bill_no", r.json())
        self.bill(self.far, no="ABC/77")  # another vendor may use the same number
        self.fin.post(f"{P}/bills/{b['id']}/void/", {"change_reason": "Entered wrongly"})
        self.bill(self.local, no="ABC/77")
        r = self.fin.post(f"{P}/bills/", {"vendor": self.local["id"], "vendor_bill_no": "", "bill_date": "2026-08-17", "items": ITEMS})
        self.assertEqual(r.status_code, 400)
        self.assertIn("vendor_bill_no", r.json())

    def test_void_reverses_the_entry_and_needs_a_reason(self):
        b = self.bill(self.local, tds_section="194C", tds_rate="2")
        r = self.fin.post(f"{P}/bills/{b['id']}/void/", {})
        self.assertEqual(r.status_code, 400)
        self.assertIn("change_reason", r.json())
        r = self.fin.post(f"{P}/bills/{b['id']}/void/", {"change_reason": "Duplicate"})
        self.assertEqual((r.json()["status"], r.json()["status_key"], r.json()["void_reason"]), ("void", "void", "Duplicate"))
        entry = JournalEntry.objects.get(source="vendor_bill", source_id=b["id"])
        self.assertEqual(entry.status, "reversed")
        back = entry.reversed_by
        self.assertEqual({l.account.code: (l.debit, l.credit) for l in back.lines.select_related("account")},
                         {"5000": (D("0"), D("9000")), "1310": (D("0"), D("810")), "1320": (D("0"), D("810")),
                          "2000": (D("10440"), D("0")), "2150": (D("180"), D("0"))})
        self.assertEqual(self.fin.post(f"{P}/bills/{b['id']}/void/", {"change_reason": "again"}).status_code, 400)
        v = self.fin.get(f"{P}/vendors/{self.local['id']}/").json()
        self.assertEqual((v["billed"], v["outstanding"]), ("0.00", "0.00"))
        from apps.accounting.models import JournalLine
        net = sum(l.credit - l.debit for l in JournalLine.objects.filter(account__code="2000"))
        self.assertEqual(net, D("0"))
        self.assert_books_balance()

    def test_a_bill_with_payments_cannot_be_voided_until_they_are(self):
        b = self.bill(self.local)
        p = self.pay(amount="500", allocations=[{"bill": b["id"], "amount": "500"}])
        r = self.fin.post(f"{P}/bills/{b['id']}/void/", {"change_reason": "x"})
        self.assertEqual(r.status_code, 400)
        self.fin.post(f"{P}/payments/{p['id']}/void/", {"change_reason": "Wrong bank"})
        self.assertEqual(self.fin.post(f"{P}/bills/{b['id']}/void/", {"change_reason": "x"}).status_code, 200)

    def test_zero_total_and_bad_dates_are_refused(self):
        self.bill(self.local, items=[{"description": "Free sample", "quantity": "1", "rate": "0", "gst_rate": "18"}], expect=201, record=False)
        d = self.bill(self.local, items=[{"description": "Free sample", "quantity": "1", "rate": "0", "gst_rate": "18"}], record=False)
        self.assertEqual(self.fin.post(f"{P}/bills/{d['id']}/record/").status_code, 400)

    def test_bill_audit_events(self):
        b = self.bill(self.local)
        self.fin.post(f"{P}/bills/{b['id']}/void/", {"change_reason": "Duplicate"})
        events = list(CompanyProfileAuditEvent.objects.filter(entity_type="vendor_bill", entity_id=b["id"]).order_by("occurred_at"))
        self.assertEqual([e.action for e in events], ["created", "issued", "deactivated"])
        self.assertEqual(events[-1].reason, "Duplicate")
        self.assertEqual(events[-1].new_values["status"], "void")
        self.assertTrue(all(e.category == "purchases" for e in events))

    def test_lists_filter_search_and_total(self):
        a = self.bill(self.local, bill_date="2026-08-01")
        self.bill(self.far, bill_date="2026-09-01", itc_status="blocked")
        d = self.fin.get(f"{P}/bills/").json()
        self.assertEqual((d["count"], d["total_amount"], d["total_balance"]), (2, "21240.00", "21240.00"))
        self.assertEqual(self.fin.get(f"{P}/bills/", {"vendor": self.far["id"]}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{P}/bills/", {"itc": "blocked"}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{P}/bills/", {"q": a["number"]}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{P}/bills/", {"from": "2026-08-15"}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{P}/bills/", {"status": "draft"}).json()["count"], 0)

    def test_scanned_bill_attachments_follow_the_permissions(self):
        b = self.bill(self.local)
        partner = client_for(make_user("pat", Role.PARTNER))
        hr = client_for(make_user("harry", Role.HR_MANAGER))
        up = lambda client: client.post("/api/v1/attachments/", {"entity_type": "vendor_bill", "entity_id": b["id"], "title": "Scan",
                                                                  "file": SimpleUploadedFile("scan.pdf", pdf_stub(), content_type="application/pdf")},
                                        format="multipart")
        self.assertEqual(up(partner).status_code, 403)
        self.assertEqual(up(hr).status_code, 403)
        self.assertEqual(up(self.fin).status_code, 201)
        self.assertEqual(len(partner.get("/api/v1/attachments/", {"entity_type": "vendor_bill", "entity_id": b["id"]}).json()), 1)
        self.assertEqual(hr.get("/api/v1/attachments/", {"entity_type": "vendor_bill", "entity_id": b["id"]}).status_code, 403)
        v = self.fin.post("/api/v1/attachments/", {"entity_type": "vendor", "entity_id": self.local["id"], "title": "Agreement",
                                                    "file": SimpleUploadedFile("a.pdf", pdf_stub(), content_type="application/pdf")}, format="multipart")
        self.assertEqual(v.status_code, 201)


class PaymentTests(PurchasesBase):
    def setUp(self):
        super().setUp()
        self.a = self.bill(self.local, bill_date="2026-08-01", due_date="2026-08-31")                                        # 10,620.00
        self.b = self.bill(self.local, bill_date="2026-08-05", due_date="2026-09-10",
                           items=[{"description": "Cables", "quantity": "10", "rate": "500", "gst_rate": "18"}])             # 5,900.00

    def test_full_payment_clears_the_bill_and_posts_payable_against_bank(self):
        p = self.pay(amount="10620", allocations=[{"bill": self.a["id"], "amount": "10620"}])
        self.assertRegex(p["number"], r"^T2N/VPY/26/2008/00001$")
        self.assertEqual((p["applied"], p["unapplied"], p["status"]), ("10620.00", "0.00", "recorded"))
        a = self.get_bill(self.a)
        self.assertEqual((a["status_key"], a["balance"], a["paid"]), ("paid", "0.00", "10620.00"))
        self.assertEqual(self.lines_of("vendor_payment", p["id"]), {"2000": (D("10620"), D("0")), "1010": (D("0"), D("10620"))})
        self.assert_books_balance()

    def test_partial_payment_leaves_the_balance(self):
        self.pay(amount="4000", allocations=[{"bill": self.a["id"], "amount": "4000"}])
        a = self.get_bill(self.a)
        self.assertEqual((a["status_key"], a["balance"], a["paid"]), ("overdue", "6620.00", "4000.00"))  # past its due date
        b = self.get_bill(self.b)
        self.assertEqual(b["status_key"], "overdue" if datetime.date.today() > datetime.date(2026, 9, 10) else "open")

    def test_one_payment_can_settle_several_bills(self):
        p = self.pay(amount="12000", allocations=[{"bill": self.a["id"], "amount": "10620"}, {"bill": self.b["id"], "amount": "1380"}])
        self.assertEqual(p["applied"], "12000.00")
        self.assertEqual((self.get_bill(self.a)["balance"], self.get_bill(self.b)["balance"]), ("0.00", "4520.00"))
        self.assertEqual(sorted(x["number"] for x in p["allocations"]), sorted([self.a["number"], self.b["number"]]))
        # two payments on one bill
        self.pay(amount="4000", allocations=[{"bill": self.b["id"], "amount": "4000"}])
        b = self.get_bill(self.b)
        self.assertEqual((b["balance"], b["paid"], len(b["payments"])), ("520.00", "5380.00", 2))

    def test_auto_allocate_pays_the_oldest_due_first(self):
        p = self.pay(amount="11000", auto_allocate=True)
        self.assertEqual((p["applied"], p["unapplied"]), ("11000.00", "0.00"))
        self.assertEqual((self.get_bill(self.a)["balance"], self.get_bill(self.b)["balance"]), ("0.00", "5520.00"))

    def test_allocation_rules(self):
        other = self.bill(self.far, bill_date="2026-08-06")
        draft = self.bill(self.local, record=False)
        for rows in ([{"bill": self.a["id"], "amount": "10620.01"}], [{"bill": other["id"], "amount": "10"}],
                     [{"bill": draft["id"], "amount": "10"}], [{"bill": "nope", "amount": "10"}], [{"bill": self.a["id"], "amount": "0"}],
                     [{"bill": self.a["id"], "amount": "5"}, {"bill": self.a["id"], "amount": "5"}],
                     [{"bill": self.a["id"], "amount": "101"}, {"bill": self.b["id"], "amount": "100"}], "x", ["x"],
                     [{"bill": self.a["id"], "amount": "1.234"}]):
            # the row that applies 201 in total is only wrong when the payment is smaller than that; every other row is wrong on its own
            amount = "200" if rows == [{"bill": self.a["id"], "amount": "101"}, {"bill": self.b["id"], "amount": "100"}] else "20000"
            r = self.fin.post(f"{P}/payments/", {"vendor": self.local["id"], "date": "2026-08-20", "amount": amount, "mode": "bank", "allocations": rows})
            self.assertEqual(r.status_code, 400, rows)
            self.assertIn("allocations", r.json(), rows)
        r = self.fin.post(f"{P}/payments/", {"vendor": self.local["id"], "date": "2026-08-20", "amount": "100", "mode": "bank",
                                              "allocations": [{"bill": self.a["id"], "amount": "100.01"}]})
        self.assertIn("allocations", r.json())
        self.assertEqual(JournalEntry.objects.filter(source="vendor_payment").count(), 0)

    def test_payment_validation(self):
        for bad, field in (({"amount": "0"}, "amount"), ({"amount": "-5"}, "amount"), ({"amount": "1.234"}, "amount"),
                           ({"amount": "abc"}, "amount"), ({"mode": "barter"}, "mode"), ({"date": ""}, "date"),
                           ({"vendor": ""}, "vendor"), ({"paid_from": "00000000-0000-0000-0000-000000000000"}, "paid_from"),
                           ({"mode": "cash", "paid_from": "x"}, "paid_from")):
            payload = {"vendor": self.local["id"], "date": "2026-08-20", "amount": "100", "mode": "bank", **bad}
            r = self.fin.post(f"{P}/payments/", payload)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json(), bad)

    def test_an_unapplied_amount_is_the_vendors_advance_and_can_be_applied_later(self):
        p = self.pay(amount="15000", allocations=[{"bill": self.a["id"], "amount": "10620"}])
        self.assertEqual((p["applied"], p["unapplied"]), ("10620.00", "4380.00"))
        adv = Account.objects.get(name="Advances to vendors")
        self.assertEqual(self.lines_of("vendor_payment", p["id"]),
                         {"1010": (D("0"), D("15000")), "2000": (D("10620"), D("0")), adv.code: (D("4380"), D("0"))})
        v = self.fin.get(f"{P}/vendors/{self.local['id']}/").json()
        self.assertEqual((v["outstanding"], v["advance"], v["net_payable"]), ("5900.00", "4380.00", "1520.00"))
        self.assertEqual(self.fin.get(f"{P}/vendors/{self.local['id']}/open-items/").json()["advances"][0]["id"], p["id"])
        # apply the advance to the second bill: Dr payable / Cr advances, no cash moves
        r = self.fin.post(f"{P}/payments/{p['id']}/apply/", {"allocations": [{"bill": self.b["id"], "amount": "4380"}], "date": "2026-09-01"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["applied"], r.json()["unapplied"]), ("15000.00", "0.00"))
        self.assertEqual(self.get_bill(self.b)["balance"], "1520.00")
        v = self.fin.get(f"{P}/vendors/{self.local['id']}/").json()
        self.assertEqual((v["outstanding"], v["advance"]), ("1520.00", "0.00"))
        self.assertEqual(self.fin.post(f"{P}/payments/{p['id']}/apply/", {"allocations": [{"bill": self.b["id"], "amount": "1"}]}).status_code, 400)
        self.assert_books_balance()
        # voiding the payment reverses the payment and the later application
        self.fin.post(f"{P}/payments/{p['id']}/void/", {"change_reason": "Cheque bounced"})
        self.assertEqual((self.get_bill(self.a)["balance"], self.get_bill(self.b)["balance"]), ("10620.00", "5900.00"))
        net = lambda code: sum(l.debit - l.credit for l in JournalLine.objects.filter(account__code=code))
        self.assertEqual((net("1010"), net(adv.code), net("2000")), (D("0"), D("0"), D("-16520")))
        self.assert_books_balance()

    def test_advance_with_no_bills_at_all(self):
        p = self.pay(self.far, amount="2500")
        self.assertEqual((p["applied"], p["unapplied"]), ("0.00", "2500.00"))
        v = self.fin.get(f"{P}/vendors/{self.far['id']}/").json()
        self.assertEqual((v["advance"], v["net_payable"]), ("2500.00", "-2500.00"))
        self.assert_books_balance()

    def test_apply_advance_rules(self):
        p = self.pay(amount="3000", allocations=[{"bill": self.a["id"], "amount": "1000"}])  # 2,000 unapplied
        for rows in ([], [{"bill": self.b["id"], "amount": "2000.01"}], [{"bill": self.b["id"], "amount": "0"}]):
            self.assertEqual(self.fin.post(f"{P}/payments/{p['id']}/apply/", {"allocations": rows}).status_code, 400, rows)
        self.assertEqual(self.fin.post(f"{P}/payments/{p['id']}/apply/", {"allocations": [{"bill": self.b["id"], "amount": "2000"}]}).status_code, 200)

    def test_cash_and_bank_account_choose_the_ledger_account(self):
        c = self.pay(amount="100", mode="cash")
        self.assertIn("1000", self.lines_of("vendor_payment", c["id"]))
        bank = self.add_bank().json()
        p = self.pay(amount="200", paid_from=bank["id"])
        code = Account.objects.get(bank_account_id=bank["id"]).code
        self.assertEqual(self.lines_of("vendor_payment", p["id"])[code], (D("0"), D("200")))
        self.assertEqual(p["paid_from"], "Operating account")
        self.assertEqual(self.fin.post(f"{P}/payments/", {"vendor": self.local["id"], "date": "2026-08-20", "amount": "5", "mode": "cash",
                                                          "paid_from": bank["id"]}).status_code, 400)
        for mode in ("upi", "cheque"):
            self.pay(amount="10", mode=mode, reference="REF1")

    def test_void_payment_restores_the_bill_and_the_books(self):
        p = self.pay(amount="10620", allocations=[{"bill": self.a["id"], "amount": "10620"}])
        r = self.fin.post(f"{P}/payments/{p['id']}/void/", {})
        self.assertIn("change_reason", r.json())
        r = self.fin.post(f"{P}/payments/{p['id']}/void/", {"change_reason": "Entered twice"})
        self.assertEqual((r.json()["status"], r.json()["void_reason"]), ("void", "Entered twice"))
        self.assertEqual(self.get_bill(self.a)["paid"], "0.00")
        self.assertEqual(JournalEntry.objects.get(source="vendor_payment", source_id=p["id"]).status, "reversed")
        self.assertEqual(self.fin.post(f"{P}/payments/{p['id']}/void/", {"change_reason": "again"}).status_code, 400)
        self.assertEqual(self.fin.get(f"{P}/vendors/{self.local['id']}/").json()["paid"], "0.00")
        self.assertEqual(self.fin.post(f"{P}/payments/{p['id']}/apply/", {"allocations": []}).status_code, 400)
        events = list(CompanyProfileAuditEvent.objects.filter(entity_type="vendor_payment", entity_id=p["id"]).order_by("occurred_at"))
        self.assertEqual([e.action for e in events], ["created", "deactivated"])
        self.assert_books_balance()

    def test_payment_list_filters_and_totals(self):
        self.pay(amount="100", date="2026-08-20")
        p2 = self.pay(self.far, amount="250", date="2026-09-02", mode="upi")
        self.fin.post(f"{P}/payments/{p2['id']}/void/", {"change_reason": "x"})
        d = self.fin.get(f"{P}/payments/").json()
        self.assertEqual((d["count"], d["total_amount"]), (2, "100.00"))
        self.assertEqual(self.fin.get(f"{P}/payments/", {"status": "void"}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{P}/payments/", {"vendor": self.local["id"]}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{P}/payments/", {"mode": "upi"}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{P}/payments/", {"to": "2026-08-31"}).json()["count"], 1)

    def test_vendor_statement_has_a_running_balance(self):
        self.pay(amount="4000", date="2026-08-20", allocations=[{"bill": self.a["id"], "amount": "4000"}])
        rows = self.fin.get(f"{P}/vendors/{self.local['id']}/statement/").json()["rows"]
        self.assertEqual([(r["type"], r["debit"], r["credit"], r["balance"]) for r in rows],
                         [("Bill", "0.00", "10620.00", "10620.00"), ("Bill", "0.00", "5900.00", "16520.00"),
                          ("Payment", "4000.00", "0.00", "12520.00")])
        v = self.fin.get(f"{P}/vendors/{self.local['id']}/").json()
        self.assertEqual((v["billed"], v["paid"], v["outstanding"]), ("16520.00", "4000.00", "12520.00"))

    def test_open_items_lists_what_can_be_paid_oldest_first(self):
        items = self.fin.get(f"{P}/vendors/{self.local['id']}/open-items/").json()["owed"]
        self.assertEqual([(i["label"], i["balance"]) for i in items], [(self.a["number"], "10620.00"), (self.b["number"], "5900.00")])

    def test_payable_in_the_books_matches_what_the_vendor_balances_say(self):
        self.pay(amount="7000", allocations=[{"bill": self.a["id"], "amount": "7000"}])
        self.pay(self.far, amount="500")
        v = self.fin.get(f"{P}/vendors/{self.local['id']}/").json()
        payable = sum(l.credit - l.debit for l in JournalLine.objects.filter(account__code="2000", party_id=self.local["id"]))
        self.assertEqual(payable, D(v["outstanding"]))
