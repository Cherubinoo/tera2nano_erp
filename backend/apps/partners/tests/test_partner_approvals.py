"""Partner withdrawals decided through the approvals module, and the two staying in step."""
from decimal import Decimal
from unittest import mock

from apps.approvals import api as approvals
from apps.approvals import rules
from apps.partners import approvals_hook
from apps.partners.models import PartnerTransaction

from .test_partners import Base, D, JournalEntry, P

A = "/api/v1/approvals"
GHOST = "00000000-0000-0000-0000-000000000000"


class WithdrawalApprovalTests(Base):
    def setUp(self):
        super().setUp()
        rules.save_rule(self.company, {"kind": "partner_withdrawal", "name": "MP signs", "priority": 1,
                                       "steps": [{"approver_type": "role", "role": "managing_partner"}]})
        self.t = self.tx(self.asha, "drawing", "150000")        # over the 1,00,000 threshold: waits
        self.req = approvals.latest_request("partner_withdrawal", self.t["id"])

    def decide(self, verb, comment="", expect=200):
        r = self.api_mp.post(f"{A}/requests/{self.req.pk}/{verb}/", {"comment": comment})
        self.assertEqual(r.status_code, expect, r.content)

    def txn(self):
        return PartnerTransaction.objects.get(pk=self.t["id"])

    def test_the_transaction_links_to_its_approval_request(self):
        self.assertEqual((self.t["status"], self.t["approval_request_id"]), ("pending_approval", str(self.req.pk)))
        self.assertEqual(approvals.status_of("partner_withdrawal", self.t["id"]), "pending")
        row = self.api_mp.get(f"{A}/requests/", {"scope": "inbox"}).json()["results"][0]
        self.assertEqual((row["kind_label"], row["amount"], row["url"]), ("Partner withdrawal", "150000.00", "/partners/transactions"))

    def test_approving_in_approvals_posts_the_drawing(self):
        self.decide("approve", "Agreed in the partners' meeting")
        self.assertEqual(self.txn().status, "posted")
        self.assertEqual(self.lines(self.entry(self.t["id"])), [("1010", D("0"), D("150000.00")), ("3100", D("150000.00"), D("0"))])
        self.balanced()

    def test_rejecting_in_approvals_keeps_the_approvers_comment_as_the_reason(self):
        self.decide("reject", expect=400)                            # a comment is required
        self.decide("reject", "Not agreed by partners")
        t = self.txn()
        self.assertEqual((t.status, t.decision_reason, t.decided_by.username), ("rejected", "Not agreed by partners", "mpartner"))
        self.assertFalse(JournalEntry.objects.filter(source="partner", source_id=self.t["id"]).exists())

    def test_withdrawing_the_request_cancels_the_waiting_withdrawal(self):
        r = self.api.post(f"{A}/requests/{self.req.pk}/withdraw/", {"comment": "keyed wrongly"})
        self.assertEqual(r.status_code, 200, r.content)
        t = self.txn()
        self.assertEqual((t.status, t.decision_reason), ("void", "Approval request withdrawn"))
        self.assertFalse(JournalEntry.objects.filter(source="partner", source_id=self.t["id"]).exists())

    def test_deciding_on_the_partners_page_closes_the_open_request(self):
        r = self.api.post(f"{P}/transactions/{self.t['id']}/approve/")
        self.assertEqual(r.json()["status"], "posted")
        self.assertEqual(approvals.status_of("partner_withdrawal", self.t["id"]), "cancelled")
        self.assertEqual(self.api_mp.get(f"{A}/summary/").json()["pending_for_me"], 0)

    def test_rejecting_or_voiding_on_the_partners_page_closes_the_open_request(self):
        self.api.post(f"{P}/transactions/{self.t['id']}/reject/", {"reason": "no"})
        self.assertEqual(approvals.status_of("partner_withdrawal", self.t["id"]), "cancelled")
        t2 = self.tx(self.bala, "drawing", "150000")
        self.api.post(f"{P}/transactions/{t2['id']}/void/", {"reason": "typo"})
        self.assertEqual(approvals.status_of("partner_withdrawal", t2["id"]), "cancelled")
        self.assertEqual(self.api_mp.get(f"{A}/summary/").json()["pending_for_me"], 0)

    def test_describe_lets_the_button_resend_a_waiting_withdrawal_whose_request_was_closed(self):
        info = approvals_hook.describe(self.t["id"])
        self.assertEqual((info["amount"], info["title"]), (D("150000.00"), "Drawing: Asha Rao"))
        self.assertTrue(info["requested_by_ok"](self.admin))
        self.assertFalse(info["requested_by_ok"](self.staff))
        self.assertIsNone(approvals_hook.describe(GHOST))
        self.assertIsNone(approvals_hook.describe("nonsense"))
        approvals.cancel_for_source("partner_withdrawal", self.t["id"], self.admin)
        r = self.api.post(f"{A}/submit/", {"kind": "partner_withdrawal", "source_id": self.t["id"]}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (201, "pending"))
        self.req = approvals.latest_request("partner_withdrawal", self.t["id"])
        self.decide("approve")
        self.assertEqual(self.txn().status, "posted")

    def test_hooks_ignore_an_unknown_or_already_decided_transaction(self):
        req = mock.MagicMock(source_id=GHOST, requested_by=self.admin)
        approvals_hook.on_cancelled(req)                              # nothing to cancel: no error
        self.decide("approve")
        req = mock.MagicMock(source_id=self.t["id"], requested_by=self.admin)
        approvals_hook.on_cancelled(req)                              # already posted: left alone
        self.assertEqual(self.txn().status, "posted")

    def test_the_kind_is_registered_with_a_label_and_the_button(self):
        kinds = {k["value"]: k for k in self.api.get(f"{A}/kinds/").json()["kinds"]}
        self.assertEqual((kinds["partner_withdrawal"]["label"], kinds["partner_withdrawal"]["can_submit_from_ui"]), ("Partner withdrawal", True))
