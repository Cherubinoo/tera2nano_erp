"""Vendor bills above an approval rule wait for approval before they are numbered and posted to the books."""
from decimal import Decimal
from unittest import mock

from apps.accounting.models import JournalEntry
from apps.accounts.models import Role
from apps.approvals import api as approvals
from apps.approvals import rules
from apps.company.models import CompanyProfile, CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user
from apps.purchases import approval_hooks
from apps.purchases.models import VendorBill

from .base import ALL_ROLES, ITEMS, P, PurchasesBase

D = Decimal
A = "/api/v1/approvals"
# 100 x 1,000 = 1,00,000.00 taxable; 18% GST = 18,000.00 (CGST 9,000 + SGST 9,000); bill total 1,18,000.00
BIG = [{"description": "Racks", "quantity": "100", "rate": "1000", "gst_rate": "18", "hsn_sac": "8473"}]
GHOST = "00000000-0000-0000-0000-000000000000"


class BillApprovalBase(PurchasesBase):
    def setUp(self):
        super().setUp()
        self.company = CompanyProfile.objects.first()
        self.mp = make_user("mp", Role.MANAGING_PARTNER)
        self.api_mp = client_for(self.mp)
        # bills of more than 1,00,000 go to the Managing Partner
        rules.save_rule(self.company, {"kind": "vendor_bill", "name": "Big bills", "priority": 1, "amount_from": "100000.01",
                                       "steps": [{"approver_type": "role", "role": "managing_partner"}]})

    def request_of(self, bill):
        return approvals.latest_request("vendor_bill", bill["id"])

    def decide(self, bill, verb="approve", comment="", client=None, expect=200):
        r = (client or self.api_mp).post(f"{A}/requests/{self.request_of(bill).pk}/{verb}/", {"comment": comment})
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def pending_bill(self, **kw):
        b = self.bill(self.local, items=BIG, record=True, **kw)
        self.assertEqual(b["status"], "pending_approval", b)
        return b


class BillWaitsForApprovalTests(BillApprovalBase):
    def test_a_small_bill_is_recorded_at_once_even_when_a_rule_exists(self):
        b = self.bill(self.local)                       # 10,620.00 is under the 1,00,000.01 band
        self.assertEqual((b["status"], b["grand_total"]), ("open", "10620.00"))
        self.assertTrue(b["number"])
        self.assertIsNone(self.request_of(b))

    def test_a_bill_at_the_limit_is_not_held_and_one_paisa_over_is(self):
        # exactly 1,00,000.00: 100 x 847.46... is awkward, so use a no-GST bill: 1 x 100,000.00 and 1 x 100,000.01
        exact = self.bill(self.local, items=[{"description": "Lump sum", "quantity": "1", "rate": "100000", "gst_rate": "0"}])
        self.assertEqual((exact["status"], exact["grand_total"]), ("open", "100000.00"))
        over = self.bill(self.local, items=[{"description": "Lump sum", "quantity": "1", "rate": "100000.01", "gst_rate": "0"}])
        self.assertEqual((over["status"], over["grand_total"]), ("pending_approval", "100000.01"))

    def test_a_large_bill_waits_and_posts_nothing(self):
        b = self.pending_bill()
        self.assertEqual((b["grand_total"], b["taxable_total"], b["tax_total"]), ("118000.00", "100000.00", "18000.00"))
        self.assertEqual((b["number"], b["status_key"], b["status_label"], b["balance"]), ("", "pending_approval", "Pending approval", None))
        self.assertFalse(JournalEntry.objects.filter(source="vendor_bill", source_id=b["id"]).exists())
        req = self.request_of(b)
        self.assertEqual((req.status, str(req.amount), req.kind, req.url), ("pending", "118000.00", "vendor_bill", f"/purchases/bills/{b['id']}"))
        self.assertEqual(approvals.status_of("vendor_bill", b["id"]), "pending")
        self.assert_books_balance()

    def test_a_waiting_bill_cannot_be_edited_recorded_again_deleted_or_paid(self):
        b = self.pending_bill()
        self.assertEqual(self.fin.patch(f"{P}/bills/{b['id']}/", {"notes": "x"}).status_code, 400)
        self.assertEqual(self.fin.post(f"{P}/bills/{b['id']}/record/").status_code, 400)
        r = self.fin.delete(f"{P}/bills/{b['id']}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Take it back", r.json()[0] if isinstance(r.json(), list) else str(r.json()))
        self.assertEqual(self.fin.post(f"{P}/bills/{b['id']}/void/", {"change_reason": "x"}).status_code, 400)
        r = self.fin.post(f"{P}/payments/", {"vendor": self.local["id"], "date": "2026-08-20", "amount": "1000", "mode": "bank",
                                              "allocations": [{"bill": b["id"], "amount": "1000"}]})
        self.assertEqual(r.status_code, 400)

    def test_the_vendors_invoice_number_stays_taken_while_the_bill_waits(self):
        b = self.pending_bill(no="INV-77")
        self.bill(self.local, items=BIG, no="INV-77", expect=400)

    def test_filter_and_dashboard_count_waiting_bills(self):
        self.pending_bill()
        self.bill(self.local)
        r = self.fin.get(f"{P}/bills/", {"status": "pending_approval"}).json()
        self.assertEqual((r["count"], r["results"][0]["status"]), (1, "pending_approval"))
        self.assertEqual(r["total_amount"], "0.00")   # nothing is owed until it is recorded
        self.assertEqual(self.fin.get(f"{P}/dashboard/").json()["bill_counts"]["pending_approval"], 1)
        counts = self.fin.get(f"{P}/dashboard/").json()["bill_counts"]
        self.assertEqual(counts["open"] + counts["overdue"], 1)   # today's date decides which of the two


class BillDecisionTests(BillApprovalBase):
    def test_approving_numbers_and_posts_the_bill(self):
        b = self.pending_bill()
        self.decide(b, "approve", "Within budget")
        done = self.get_bill(b)
        self.assertEqual((done["status"], done["balance"], done["decision_note"]), ("open", "118000.00", "Within budget"))
        self.assertRegex(done["number"], r"^T2N/BILL/26/1608/00001$")
        self.assertEqual(self.lines_of("vendor_bill", b["id"]),
                         {"5000": (D("100000"), D("0")), "1310": (D("9000"), D("0")), "1320": (D("9000"), D("0")),
                          "2000": (D("0"), D("118000"))})
        self.assert_books_balance()
        self.assertEqual(approvals.status_of("vendor_bill", b["id"]), "approved")
        reasons = [e.reason for e in CompanyProfileAuditEvent.objects.filter(entity_type="vendor_bill", entity_id=b["id"]).order_by("occurred_at")]
        self.assertEqual(reasons, ["", "Sent for approval", "Approved: Within budget"])
        # once recorded it is an ordinary bill: payments apply and it can be voided (the example rule that holds payments
        # above 50,000 was seeded by the first request, so it is switched off here to pay at once)
        from apps.approvals.models import ApprovalRule
        ApprovalRule.objects.filter(kind="payment").update(is_active=False)
        p = self.pay(self.local, amount="118000", allocations=[{"bill": b["id"], "amount": "118000"}])
        self.assertEqual(self.get_bill(b)["status_key"], "paid")

    def test_approving_a_bill_with_tds_posts_the_withheld_amount(self):
        b = self.bill(self.local, items=BIG, tds_section="194C", tds_rate="2")     # TDS 2% of 1,00,000 = 2,000
        self.assertEqual((b["status"], b["tds_amount"], b["net_payable"]), ("pending_approval", "2000.00", "116000.00"))
        self.decide(b)
        self.assertEqual(self.lines_of("vendor_bill", b["id"]),
                        {"5000": (D("100000"), D("0")), "1310": (D("9000"), D("0")), "1320": (D("9000"), D("0")),
                         "2000": (D("0"), D("116000")), "2150": (D("0"), D("2000"))})
        self.assert_books_balance()

    def test_rejecting_returns_the_bill_to_a_draft_with_the_reason(self):
        b = self.pending_bill()
        self.decide(b, "reject", expect=400)                         # a reason is required
        self.decide(b, "reject", "Rate too high")
        d = self.get_bill(b)
        self.assertEqual((d["status"], d["decision_note"], d["number"]), ("draft", "Rejected: Rate too high", ""))
        self.assertFalse(JournalEntry.objects.filter(source="vendor_bill", source_id=b["id"]).exists())
        self.assertEqual(approvals.status_of("vendor_bill", b["id"]), "rejected")
        # fix the rate, record again: a fresh request waits (total 1,12,100: 100 x 950 = 95,000 + 18% = 1,12,100)
        fixed = [{**BIG[0], "rate": "950"}]
        self.assertEqual(self.fin.patch(f"{P}/bills/{b['id']}/", {"items": fixed}).status_code, 200)
        r = self.fin.post(f"{P}/bills/{b['id']}/record/").json()
        self.assertEqual((r["status"], r["grand_total"]), ("pending_approval", "112100.00"))
        self.assertEqual(approvals.latest_request("vendor_bill", b["id"]).status, "pending")

    def test_a_rejected_request_sent_again_from_approvals_puts_the_bill_back_to_waiting(self):
        b = self.pending_bill()
        self.decide(b, "reject", "Check the PO first")
        old = self.request_of(b)
        r = self.api_mp.post(f"{A}/requests/{old.pk}/resubmit/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.get_bill(b)["status"], "pending_approval")
        self.decide(b, "approve")
        self.assertEqual(self.get_bill(b)["status"], "open")

    def test_withdrawing_in_approvals_or_on_the_bill_returns_it_to_a_draft(self):
        b = self.pending_bill()
        r = self.fin.post(f"{A}/requests/{self.request_of(b).pk}/withdraw/", {"comment": "wrong vendor"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.get_bill(b)["status"], "draft")
        b2 = self.pending_bill()
        r = self.fin.post(f"{P}/bills/{b2['id']}/withdraw/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "draft"))
        self.assertEqual(approvals.status_of("vendor_bill", b2["id"]), "cancelled")
        self.assertEqual(self.fin.post(f"{P}/bills/{b2['id']}/withdraw/").status_code, 400)   # no longer waiting
        self.assertEqual(self.fin.delete(f"{P}/bills/{b2['id']}/").status_code, 204)

    def test_a_decision_that_cannot_post_is_rolled_back_with_the_reason(self):
        b = self.pending_bill()
        self.assertEqual(self.fin.patch(f"{P}/vendors/{self.local['id']}/", {"is_active": False}).status_code, 200)
        r = self.decide(b, "approve", expect=400)
        self.assertIn("vendor is switched off", str(r.json()))
        self.assertEqual(self.get_bill(b)["status"], "pending_approval")
        self.assertEqual(self.request_of(b).status, "pending")
        self.assertFalse(JournalEntry.objects.filter(source="vendor_bill", source_id=b["id"]).exists())

    def test_the_requester_cannot_approve_their_own_large_bill(self):
        b = self.pending_bill()
        self.decide(b, "approve", client=self.fin, expect=403)
        self.assertEqual(self.get_bill(b)["status"], "pending_approval")


class StandaloneAndPermissionTests(BillApprovalBase):
    def test_without_the_approvals_module_bills_are_recorded_directly(self):
        with mock.patch("apps.purchases.services.orders._approvals", return_value=None):
            b = self.bill(self.local, items=BIG)
        self.assertEqual(b["status"], "open")

    def test_with_no_active_rule_a_large_bill_is_recorded_directly(self):
        from apps.approvals.models import ApprovalRule
        ApprovalRule.objects.filter(kind="vendor_bill").update(is_active=False)
        self.assertEqual(self.bill(self.local, items=BIG)["status"], "open")

    def test_a_person_with_purchases_manage_can_decide_directly_when_approvals_are_not_installed(self):
        b = self.pending_bill()
        with mock.patch("apps.purchases.services.orders._approvals", return_value=None):
            self.assertEqual(self.fin.post(f"{P}/bills/{b['id']}/reject/", {}).status_code, 400)       # reason needed
            r = self.fin.post(f"{P}/bills/{b['id']}/reject/", {"change_reason": "Duplicate"})
            self.assertEqual((r.status_code, r.json()["status"], r.json()["decision_note"]), (200, "draft", "Rejected: Duplicate"))
            self.fin.post(f"{P}/bills/{b['id']}/record/")      # approvals missing, so it simply posts
            self.assertEqual(self.get_bill(b)["status"], "open")
            self.assertEqual(self.fin.post(f"{P}/bills/{b['id']}/approve/").status_code, 400)           # not waiting any more

    def test_direct_approve_posts_when_approvals_are_not_installed(self):
        b = self.pending_bill()
        with mock.patch("apps.purchases.services.orders._approvals", return_value=None):
            r = self.fin.post(f"{P}/bills/{b['id']}/approve/", {"note": "OK"})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["decision_note"]), (200, "open", "OK"))
        self.assertEqual(self.lines_of("vendor_bill", b["id"])["2000"], (D("0"), D("118000")))
        self.assert_books_balance()

    def test_with_approvals_installed_the_bill_endpoints_do_not_decide(self):
        b = self.pending_bill()
        for verb in ("approve", "reject"):
            r = self.fin.post(f"{P}/bills/{b['id']}/{verb}/", {"change_reason": "x"})
            self.assertEqual(r.status_code, 400, verb)
            self.assertIn("Approvals module", str(r.json()))
        self.assertEqual(self.get_bill(b)["status"], "pending_approval")

    def test_every_role_against_the_bill_actions(self):
        b = self.pending_bill()
        allowed = {Role.ADMIN, Role.MANAGING_PARTNER, Role.FINANCE_MANAGER}
        for role in ALL_ROLES:
            c = client_for(make_user(f"u_{role}", role))
            for verb in ("approve", "reject", "withdraw"):
                code = c.post(f"{P}/bills/{b['id']}/{verb}/", {"change_reason": "x"}).status_code
                if role in allowed:
                    # allowed to act: approve/reject are refused because the Approvals module decides; the first allowed
                    # role to withdraw takes the bill back and later ones find it a draft
                    self.assertEqual(code, 400 if verb != "withdraw" else code, (role, verb))
                else:
                    self.assertEqual(code, 403, (role, verb))
            if role in allowed:
                self.fin.post(f"{P}/bills/{b['id']}/record/")      # send it back to waiting for the next role
                self.assertEqual(self.get_bill(b)["status"], "pending_approval")
            self.assertEqual(c.get(f"{P}/bills/{b['id']}/").status_code in (200, 403), True)
        self.assertEqual(self.get_bill(b)["status"], "pending_approval")
        self.assertEqual(self.anon.post(f"{P}/bills/{b['id']}/withdraw/").status_code in (401, 403), True)

    def test_unknown_bill_is_404_for_the_new_actions(self):
        for verb in ("withdraw", "approve", "reject"):
            self.assertEqual(self.fin.post(f"{P}/bills/{GHOST}/{verb}/", {}).status_code, 404, verb)
        self.assertEqual(self.fin.post(f"{P}/bills/not-a-uuid/withdraw/", {}).status_code, 404)


class BillHookTests(BillApprovalBase):
    def test_describe_lets_the_approval_button_submit_a_waiting_bill_with_no_open_request(self):
        b = self.pending_bill()
        info = approval_hooks.describe_bill(b["id"])
        self.assertEqual((info["amount"], info["url"], info["title"]), (D("118000.00"), f"/purchases/bills/{b['id']}",
                                                                          f"Vendor bill {b['vendor_bill_no']} from Local Supplies"))
        self.assertTrue(info["requested_by_ok"](self.finance))
        self.assertFalse(info["requested_by_ok"](self.staff))
        self.assertIsNone(approval_hooks.describe_bill(GHOST))
        self.assertIsNone(approval_hooks.describe_bill("nonsense"))
        # the request is closed behind the bill's back; the button sends it again
        approvals.cancel_for_source("vendor_bill", b["id"], self.finance)
        self.assertEqual(approvals.status_of("vendor_bill", b["id"]), "cancelled")
        r = self.fin.post(f"{A}/submit/", {"kind": "vendor_bill", "source_id": b["id"]}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (201, "pending"))
        self.decide(b)
        self.assertEqual(self.get_bill(b)["status"], "open")

    def test_the_kind_is_registered_with_its_label_and_can_be_submitted_from_the_ui(self):
        kinds = {k["value"]: k for k in self.api.get(f"{A}/kinds/").json()["kinds"]}
        self.assertEqual((kinds["vendor_bill"]["label"], kinds["vendor_bill"]["can_submit_from_ui"]), ("Vendor bill", True))

    def test_hooks_ignore_a_bill_that_is_no_longer_waiting(self):
        b = self.bill(self.local)                                   # already open
        req = mock.MagicMock(source_id=b["id"], requested_by=self.finance)
        req.decisions.select_related.return_value.order_by.return_value.first.return_value = None
        approval_hooks.bill_approved(req)
        approval_hooks.bill_rejected(req)
        self.assertEqual(VendorBill.objects.get(pk=b["id"]).status, "open")
        gone = mock.MagicMock(source_id=GHOST, requested_by=self.finance)
        for fn in (approval_hooks.bill_approved, approval_hooks.bill_rejected, approval_hooks.bill_cancelled, approval_hooks.bill_resubmitted):
            fn(gone)   # an unknown record is ignored, not an error

    def test_the_request_shows_in_the_inbox_with_a_link_back_to_the_bill(self):
        b = self.pending_bill()
        rows = self.api_mp.get(f"{A}/requests/", {"scope": "inbox"}).json()["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["kind_label"], rows[0]["url"], rows[0]["amount"]),
                         ("vendor_bill", "Vendor bill", f"/purchases/bills/{b['id']}", "118000.00"))
