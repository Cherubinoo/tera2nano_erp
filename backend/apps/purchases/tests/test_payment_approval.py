"""Vendor payments above an approval rule wait for approval before they are numbered, applied and posted; voiding one waits too."""
from decimal import Decimal
from unittest import mock

from apps.accounting.models import JournalEntry
from apps.accounts.models import Role
from apps.approvals import api as approvals
from apps.approvals import rules
from apps.company.models import CompanyProfile, CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user
from apps.purchases import approval_hooks
from apps.purchases.models import VendorPayment

from .base import ALL_ROLES, P, PurchasesBase

D = Decimal
A = "/api/v1/approvals"
GHOST = "00000000-0000-0000-0000-000000000000"
# 100 x 1,000 = 1,00,000.00 taxable; 18% GST = 18,000.00; the bill is 1,18,000.00
BIG = [{"description": "Racks", "quantity": "100", "rate": "1000", "gst_rate": "18", "hsn_sac": "8473"}]


class PaymentApprovalBase(PurchasesBase):
    def setUp(self):
        super().setUp()
        self.company = CompanyProfile.objects.first()
        self.mp = make_user("mp", Role.MANAGING_PARTNER)
        self.api_mp = client_for(self.mp)
        # payments of more than 50,000.00 go to the Managing Partner
        rules.save_rule(self.company, {"kind": "payment", "name": "Big payments", "priority": 1, "amount_from": "50000.01",
                                       "steps": [{"approver_type": "role", "role": "managing_partner"}]})
        self.bill_big = self.bill(self.local, items=BIG)                # 1,18,000.00 owed to Local Supplies
        self.assertEqual(self.bill_big["status"], "open")

    def request_of(self, p):
        return approvals.latest_request("payment", p["id"])

    def decide(self, p, verb="approve", comment="", client=None, expect=200):
        r = (client or self.api_mp).post(f"{A}/requests/{self.request_of(p).pk}/{verb}/", {"comment": comment})
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def get_payment(self, p):
        return self.fin.get(f"{P}/payments/{p['id']}/").json()

    def balance(self, bill=None):
        return D(self.get_bill(bill or self.bill_big)["balance"])

    def pending(self, amount="60000", allocate=True, **kw):
        extra = {"allocations": [{"bill": self.bill_big["id"], "amount": amount}]} if allocate else {}
        p = self.pay(self.local, amount=amount, **extra, **kw)
        self.assertEqual(p["status"], "pending_approval", p)
        return p

    def posted(self, p):
        return JournalEntry.objects.filter(source="vendor_payment", source_id=p["id"])

    def void(self, p, reason="Wrong vendor", client=None, expect=200):
        r = (client or self.fin).post(f"{P}/payments/{p['id']}/void/", {"change_reason": reason})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()


class PaymentWaitsForApprovalTests(PaymentApprovalBase):
    def test_a_payment_at_the_limit_is_posted_at_once(self):
        p = self.pay(self.local, amount="50000", allocations=[{"bill": self.bill_big["id"], "amount": "50000"}])
        self.assertEqual((p["status"], p["applied"]), ("recorded", "50000.00"))
        self.assertRegex(p["number"], r"^T2N/VPY/26/2008/00001$")
        self.assertEqual(self.lines_of("vendor_payment", p["id"]), {"2000": (D("50000"), D("0")), "1010": (D("0"), D("50000"))})
        self.assertEqual(self.balance(), D("68000"))
        self.assertIsNone(self.request_of(p))

    def test_one_paisa_over_waits_and_changes_nothing(self):
        p = self.pending(amount="50000.01", allocate=False)
        self.assertEqual((p["number"], p["status_label"], p["applied"], p["unapplied"]), ("", "Pending approval", "0.00", "0.00"))
        self.assertFalse(self.posted(p).exists())

    def test_a_large_payment_waits_without_a_number_allocation_or_entry(self):
        p = self.pending()
        self.assertEqual((p["number"], p["amount"], p["applied"]), ("", "60000.00", "0.00"))
        self.assertFalse(self.posted(p).exists())
        self.assertEqual(self.balance(), D("118000"))                  # the bill is still owed in full
        self.assertEqual(self.get_bill(self.bill_big)["status"], "open")
        req = self.request_of(p)
        self.assertEqual((req.status, str(req.amount), req.kind, req.url), ("pending", "60000.00", "payment", f"/purchases/payments/{p['id']}"))
        self.assertEqual(approvals.status_of("payment", p["id"]), "pending")
        self.assert_books_balance()
        self.assertEqual(self.fin.get(f"{P}/payments/", {"status": "recorded"}).json()["total_amount"], "0.00")
        self.assertEqual(self.fin.get(f"{P}/payments/", {"status": "pending_approval"}).json()["count"], 1)

    def test_a_waiting_payment_cannot_be_applied_or_voided(self):
        p = self.pending()
        self.assertEqual(self.fin.post(f"{P}/payments/{p['id']}/apply/", {"allocations": []}).status_code, 400)
        r = self.fin.post(f"{P}/payments/{p['id']}/void/", {"change_reason": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Take it back", str(r.json()))
        self.assertEqual(self.fin.delete(f"{P}/payments/{p['id']}/").status_code, 400)

    def test_the_payment_does_not_reserve_the_bill_a_second_payment_can_use_it(self):
        p = self.pending()
        q = self.pay(self.local, amount="50000", allocations=[{"bill": self.bill_big["id"], "amount": "50000"}])
        self.assertEqual(q["status"], "recorded")
        self.assertEqual(self.balance(), D("68000"))
        self.decide(p)                                                 # 60,000.00 still fits in the 68,000.00 left
        self.assertEqual(self.balance(), D("8000"))


class PaymentDecisionTests(PaymentApprovalBase):
    def test_approving_numbers_applies_and_posts_exactly_as_a_direct_payment(self):
        p = self.pending()
        self.decide(p, "approve", "Cleared by the MP")
        done = self.get_payment(p)
        self.assertEqual((done["status"], done["applied"], done["decision_note"]), ("recorded", "60000.00", "Cleared by the MP"))
        self.assertRegex(done["number"], r"^T2N/VPY/26/2008/00001$")
        self.assertEqual(self.lines_of("vendor_payment", p["id"]), {"2000": (D("60000"), D("0")), "1010": (D("0"), D("60000"))})
        self.assertEqual(self.balance(), D("58000"))
        self.assertEqual([x["amount"] for x in self.get_bill(self.bill_big)["payments"]], ["60000.00"])
        self.assert_books_balance()
        self.assertEqual(approvals.status_of("payment", p["id"]), "approved")
        reasons = [e.reason for e in CompanyProfileAuditEvent.objects.filter(entity_type="vendor_payment", entity_id=p["id"]).order_by("occurred_at")]
        self.assertEqual(reasons, ["Sent for approval", "Approved: Cleared by the MP"])

    def test_an_unapplied_payment_is_kept_as_an_advance_when_approved(self):
        p = self.pending(amount="70000", allocate=False)
        self.decide(p)
        done = self.get_payment(p)
        self.assertEqual((done["status"], done["applied"], done["unapplied"]), ("recorded", "0.00", "70000.00"))
        # an advance goes to the vendor advances account (1510), not to payables
        self.assertEqual(self.lines_of("vendor_payment", p["id"]), {"1510": (D("70000"), D("0")), "1010": (D("0"), D("70000"))})
        self.assert_books_balance()

    def test_a_payment_split_over_two_bills_is_applied_as_planned(self):
        second = self.bill(self.local, items=BIG)                      # another 1,18,000.00
        p = self.pay(self.local, amount="90000", allocations=[{"bill": self.bill_big["id"], "amount": "50000"},
                                                                {"bill": second["id"], "amount": "40000"}])
        self.assertEqual(p["status"], "pending_approval")
        self.decide(p)
        self.assertEqual((self.balance(), self.balance(second)), (D("68000"), D("78000")))
        self.assertEqual(self.lines_of("vendor_payment", p["id"]), {"2000": (D("90000"), D("0")), "1010": (D("0"), D("90000"))})

    def test_the_decision_runs_once(self):
        p = self.pending()
        self.decide(p)
        self.decide(p, "approve", expect=409)
        self.decide(p, "reject", comment="late", expect=409)
        self.assertEqual(self.posted(p).count(), 1)
        self.assertEqual(self.balance(), D("58000"))

    def test_rejecting_closes_it_as_rejected_with_the_note_and_nothing_posts(self):
        p = self.pending()
        self.decide(p, "reject", expect=400)                           # a reason is required
        self.decide(p, "reject", "Not budgeted")
        d = self.get_payment(p)
        self.assertEqual((d["status"], d["decision_note"], d["number"]), ("rejected", "Rejected: Not budgeted", ""))
        self.assertFalse(self.posted(p).exists())
        self.assertEqual(self.balance(), D("118000"))
        self.assertEqual(approvals.status_of("payment", p["id"]), "rejected")
        self.assert_books_balance()
        self.assertEqual(self.fin.delete(f"{P}/payments/{p['id']}/").status_code, 204)        # clean up the rejected record
        self.assertFalse(VendorPayment.objects.filter(pk=p["id"]).exists())

    def test_a_rejected_request_sent_again_from_approvals_puts_the_payment_back_to_waiting(self):
        p = self.pending()
        self.decide(p, "reject", "Not budgeted")
        r = self.api_mp.post(f"{A}/requests/{self.request_of(p).pk}/resubmit/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.get_payment(p)["status"], "pending_approval")
        self.decide(p)
        self.assertEqual(self.get_payment(p)["status"], "recorded")
        self.assertEqual(self.balance(), D("58000"))

    def test_withdrawing_in_approvals_or_on_the_payment_closes_it(self):
        p = self.pending()
        r = self.fin.post(f"{A}/requests/{self.request_of(p).pk}/withdraw/", {"comment": "wrong amount"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((self.get_payment(p)["status"], self.get_payment(p)["decision_note"]), ("rejected", "Withdrawn before it was approved"))
        p2 = self.pending()
        r = self.fin.post(f"{P}/payments/{p2['id']}/withdraw/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "rejected"))
        self.assertEqual(approvals.status_of("payment", p2["id"]), "cancelled")
        self.assertEqual(self.fin.post(f"{P}/payments/{p2['id']}/withdraw/").status_code, 400)    # no longer waiting
        self.assertFalse(self.posted(p).exists())

    def test_approval_checks_what_is_owed_again_and_rolls_back_when_it_no_longer_fits(self):
        p = self.pending()                                             # plans 60,000.00 against the 1,18,000.00 bill
        for _ in range(2):                                             # 2 x 50,000.00 paid meanwhile: 18,000.00 left
            self.pay(self.local, amount="50000", allocations=[{"bill": self.bill_big["id"], "amount": "50000"}])
        self.assertEqual(self.balance(), D("18000"))
        r = self.decide(p, "approve", expect=400)
        self.assertIn("18000.00 left to pay", str(r.json()))
        self.assertEqual(self.get_payment(p)["status"], "pending_approval")
        self.assertEqual(self.request_of(p).status, "pending")
        self.assertFalse(self.posted(p).exists())
        self.assertEqual(self.balance(), D("18000"))
        self.assert_books_balance()

    def test_a_decision_that_cannot_post_is_rolled_back_and_uses_no_number(self):
        p = self.pending()
        with mock.patch("apps.purchases.services.payments.posting.post_payment", side_effect=RuntimeError("the financial year is locked")):
            r = self.decide(p, "approve", expect=400)
        self.assertIn("financial year is locked", str(r.json()))
        self.assertEqual((self.get_payment(p)["status"], self.get_payment(p)["number"]), ("pending_approval", ""))
        self.assertEqual(self.request_of(p).status, "pending")
        self.assertEqual(self.balance(), D("118000"))
        self.decide(p)
        self.assertEqual(self.get_payment(p)["number"], "T2N/VPY/26/2008/00001")      # the number was not burnt by the failed attempt

    def test_the_requester_cannot_approve_their_own_payment(self):
        p = self.pending()
        self.decide(p, "approve", client=self.fin, expect=403)
        self.assertEqual(self.get_payment(p)["status"], "pending_approval")


class VoidingAPaymentTests(PaymentApprovalBase):
    def recorded_large(self):
        p = self.pending()
        self.decide(p)
        return self.get_payment(p)

    def test_voiding_a_small_payment_is_immediate(self):
        p = self.pay(self.local, amount="40000", allocations=[{"bill": self.bill_big["id"], "amount": "40000"}])
        self.assertEqual(self.void(p)["status"], "void")
        self.assertEqual(self.balance(), D("118000"))

    def test_voiding_a_large_payment_waits_and_nothing_moves(self):
        p = self.recorded_large()
        r = self.void(p, "Paid the wrong vendor")
        self.assertEqual((r["status"], r["void_requested"], r["status_label"], r["void_request_reason"]),
                         ("recorded", True, "Void pending approval", "Paid the wrong vendor"))
        self.assertEqual(self.balance(), D("58000"))                    # still counted as paid
        self.assertEqual(self.lines_of("vendor_payment", p["id"])["2000"], (D("60000"), D("0")))
        self.assertEqual(JournalEntry.objects.filter(source="reversal").count(), 0)
        req = self.request_of(p)
        self.assertEqual((req.status, req.kind, str(req.amount)), ("pending", "payment", "60000.00"))
        self.assertIn("Void payment T2N/VPY/26/2008/00001", req.title)
        self.void(p, expect=400)                                        # already waiting
        self.assert_books_balance()

    def test_approving_the_void_reverses_the_entry_and_reopens_the_bill(self):
        p = self.recorded_large()
        self.void(p, "Paid the wrong vendor")
        self.decide(p, "approve", "Agreed")
        d = self.get_payment(p)
        self.assertEqual((d["status"], d["void_reason"], d["void_requested"], d["decision_note"]), ("void", "Paid the wrong vendor", False, "Agreed"))
        self.assertEqual((self.balance(), self.get_bill(self.bill_big)["status"]), (D("118000"), "open"))
        self.assertEqual(JournalEntry.objects.filter(source="reversal").count(), 1)
        self.assert_books_balance()

    def test_rejecting_the_void_keeps_the_payment(self):
        p = self.recorded_large()
        self.void(p, "Mistake")
        self.decide(p, "reject", "It was correct")
        d = self.get_payment(p)
        self.assertEqual((d["status"], d["void_requested"], d["decision_note"]), ("recorded", False, "Void rejected: It was correct"))
        self.assertEqual(self.balance(), D("58000"))
        self.void(p, "Mistake again")                                   # a new request can be made
        self.assertTrue(self.get_payment(p)["void_requested"])

    def test_withdrawing_the_void_request_keeps_the_payment(self):
        p = self.recorded_large()
        self.void(p)
        r = self.fin.post(f"{P}/payments/{p['id']}/withdraw/")
        self.assertEqual((r.status_code, r.json()["void_requested"], r.json()["status"]), (200, False, "recorded"))
        self.assertEqual(approvals.status_of("payment", p["id"]), "cancelled")
        self.assertEqual(self.balance(), D("58000"))

    def test_a_rejected_void_request_sent_again_from_approvals_waits_once_more(self):
        p = self.recorded_large()
        self.void(p, "Mistake")
        self.decide(p, "reject", "Not yet")
        r = self.api_mp.post(f"{A}/requests/{self.request_of(p).pk}/resubmit/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(self.get_payment(p)["void_requested"])
        self.decide(p)
        self.assertEqual(self.get_payment(p)["status"], "void")

    def test_a_void_that_cannot_complete_is_rolled_back(self):
        p = self.recorded_large()
        self.void(p)
        with mock.patch("apps.purchases.services.payments.posting.reverse_payment", side_effect=RuntimeError("the financial year is locked")):
            r = self.decide(p, "approve", expect=400)
        self.assertIn("financial year is locked", str(r.json()))
        self.assertEqual((self.get_payment(p)["status"], self.get_payment(p)["void_requested"]), ("recorded", True))
        self.assertEqual(self.balance(), D("58000"))
        self.decide(p)
        self.assertEqual(self.get_payment(p)["status"], "void")

    def test_the_requester_cannot_approve_their_own_void(self):
        p = self.recorded_large()
        self.void(p)
        self.decide(p, "approve", client=self.fin, expect=403)
        self.assertTrue(self.get_payment(p)["void_requested"])


class StandaloneAndPermissionTests(PaymentApprovalBase):
    def test_without_the_approvals_module_payments_post_directly(self):
        with mock.patch("apps.purchases.services.payments._approvals", return_value=None):
            p = self.pay(self.local, amount="60000", allocations=[{"bill": self.bill_big["id"], "amount": "60000"}])
            self.assertEqual(p["status"], "recorded")
            self.assertEqual(self.void(p)["status"], "void")

    def test_with_no_active_rule_a_large_payment_posts_directly(self):
        from apps.approvals.models import ApprovalRule
        ApprovalRule.objects.filter(kind="payment").update(is_active=False)
        self.assertEqual(self.pay(self.local, amount="60000")["status"], "recorded")

    def test_a_person_with_purchases_manage_can_decide_directly_when_approvals_are_not_installed(self):
        p = self.pending()
        with mock.patch("apps.purchases.services.payments._approvals", return_value=None):
            self.assertEqual(self.fin.post(f"{P}/payments/{p['id']}/reject/", {}).status_code, 400)
            r = self.fin.post(f"{P}/payments/{p['id']}/reject/", {"change_reason": "Duplicate"})
            self.assertEqual((r.status_code, r.json()["status"], r.json()["decision_note"]), (200, "rejected", "Rejected: Duplicate"))
            self.assertEqual(self.fin.post(f"{P}/payments/{p['id']}/approve/").status_code, 400)       # not waiting any more

    def test_direct_approve_posts_when_approvals_are_not_installed(self):
        p = self.pending()
        with mock.patch("apps.purchases.services.payments._approvals", return_value=None):
            r = self.fin.post(f"{P}/payments/{p['id']}/approve/", {"note": "OK"})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["decision_note"]), (200, "recorded", "OK"))
        self.assertEqual(self.lines_of("vendor_payment", p["id"])["2000"], (D("60000"), D("0")))
        self.assert_books_balance()

    def test_direct_approve_of_a_void_when_approvals_are_not_installed(self):
        p = self.pending()
        self.decide(p)
        p = self.get_payment(p)
        self.void(p, "Mistake")                                         # the real approvals module holds it
        with mock.patch("apps.purchases.services.payments._approvals", return_value=None):
            r = self.fin.post(f"{P}/payments/{p['id']}/approve/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "void"))
        self.assertEqual(self.balance(), D("118000"))

    def test_with_approvals_installed_the_payment_endpoints_do_not_decide(self):
        p = self.pending()
        for verb in ("approve", "reject"):
            r = self.fin.post(f"{P}/payments/{p['id']}/{verb}/", {"change_reason": "x"})
            self.assertEqual(r.status_code, 400, verb)
            self.assertIn("Approvals module", str(r.json()))
        self.assertTrue(self.get_payment(p)["approval_managed"])

    def test_every_role_against_the_payment_actions(self):
        p = self.pending()
        manage = {Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER}
        for role in ALL_ROLES:
            c = client_for(make_user(f"u_{role}", role))
            self.assertEqual(c.delete(f"{P}/payments/{p['id']}/").status_code, 400 if role in manage else 403, role)
            for verb in ("approve", "reject", "void", "withdraw"):      # withdraw last: it closes the waiting payment
                code = c.post(f"{P}/payments/{p['id']}/{verb}/", {"change_reason": "x"}).status_code
                if role in manage:
                    self.assertNotEqual(code, 403, (role, verb))
                else:
                    self.assertEqual(code, 403, (role, verb))
            if role in manage:
                p = self.pending()                                       # a new waiting payment for the next role
        self.assertIn(self.anon.post(f"{P}/payments/{p['id']}/withdraw/").status_code, (401, 403))

    def test_unknown_payment_is_404(self):
        for verb in ("withdraw", "approve", "reject", "void"):
            self.assertEqual(self.fin.post(f"{P}/payments/{GHOST}/{verb}/", {}).status_code, 404, verb)
        self.assertEqual(self.fin.delete(f"{P}/payments/{GHOST}/").status_code, 404)


class PaymentHookTests(PaymentApprovalBase):
    def test_describe_answers_for_a_waiting_payment_and_a_waiting_void_only(self):
        p = self.pending()
        info = approval_hooks.describe_payment(p["id"])
        self.assertEqual((info["amount"], info["url"], info["title"]), (D("60000.00"), f"/purchases/payments/{p['id']}", "Payment to Local Supplies"))
        self.assertTrue(info["requested_by_ok"](self.finance))
        self.assertFalse(info["requested_by_ok"](self.staff))
        self.assertIsNone(approval_hooks.describe_payment(GHOST))
        self.assertIsNone(approval_hooks.describe_payment("nonsense"))
        approvals.cancel_for_source("payment", p["id"], self.finance)
        self.assertEqual(self.get_payment(p)["status"], "pending_approval")     # closed behind the payment's back
        r = self.fin.post(f"{A}/submit/", {"kind": "payment", "source_id": p["id"]}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (201, "pending"))
        self.decide(p)
        done = self.get_payment(p)
        self.assertEqual(done["status"], "recorded")
        self.assertIsNone(approval_hooks.describe_payment(p["id"]))             # recorded and no void waiting

    def test_the_kind_is_registered_with_its_label_and_can_be_submitted_from_the_ui(self):
        kinds = {k["value"]: k for k in self.api_mp.get(f"{A}/kinds/").json()["kinds"]}
        self.assertEqual((kinds["payment"]["label"], kinds["payment"]["can_submit_from_ui"]), ("Payment", True))

    def test_hooks_ignore_a_payment_that_is_not_waiting_and_unknown_ones(self):
        p = self.pay(self.local, amount="100")
        req = mock.MagicMock(source_id=p["id"], requested_by=self.finance)
        req.decisions.select_related.return_value.order_by.return_value.first.return_value = None
        approval_hooks.payment_approved(req)
        approval_hooks.payment_rejected(req)
        approval_hooks.payment_cancelled(req)
        approval_hooks.payment_resubmitted(req)
        self.assertEqual(VendorPayment.objects.get(pk=p["id"]).status, "recorded")
        for fn in (approval_hooks.payment_approved, approval_hooks.payment_rejected, approval_hooks.payment_cancelled,
                   approval_hooks.payment_resubmitted):
            fn(mock.MagicMock(source_id=GHOST, requested_by=self.finance))
            fn(mock.MagicMock(source_id="nonsense", requested_by=self.finance))

    def test_the_request_shows_in_the_inbox_with_a_link_back_to_the_payment(self):
        p = self.pending()
        rows = self.api_mp.get(f"{A}/requests/", {"scope": "inbox"}).json()["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["kind_label"], rows[0]["url"], rows[0]["amount"]),
                         ("payment", "Payment", f"/purchases/payments/{p['id']}", "60000.00"))
