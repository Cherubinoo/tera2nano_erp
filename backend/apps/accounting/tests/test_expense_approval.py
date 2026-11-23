"""Expenses above an approval rule wait for approval; nothing is numbered or posted until it is approved."""
import datetime
from decimal import Decimal
from unittest import mock

from apps.accounting.models import JournalEntry, Voucher
from apps.accounting.approval_hooks import describe, on_approved, on_cancelled, on_rejected, on_resubmitted
from apps.accounts.models import Role
from apps.approvals import api as approvals
from apps.approvals import rules
from apps.company.models import CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user

from .test_accounting import A, Base, D

AP = "/api/v1/approvals"
GHOST = "00000000-0000-0000-0000-000000000000"
ALL_ROLES = [Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER, Role.HR_MANAGER, Role.PROJECT_MANAGER, Role.EMPLOYEE]


class ExpenseApprovalBase(Base):
    def setUp(self):
        super().setUp()
        self.mp = make_user("mp", Role.MANAGING_PARTNER)
        self.api_mp = client_for(self.mp)
        # expenses of more than 50,000.00 (the total, GST included) go to the Managing Partner
        rules.save_rule(self.company, {"kind": "expense", "name": "Big expenses", "priority": 1, "amount_from": "50000.01",
                                       "steps": [{"approver_type": "role", "role": "managing_partner"}]})

    def request_of(self, v):
        return approvals.latest_request("expense", v["id"])

    def decide(self, v, verb="approve", comment="", client=None, expect=200):
        r = (client or self.api_mp).post(f"{AP}/requests/{self.request_of(v).pk}/{verb}/", {"comment": comment})
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def get(self, v):
        return self.fin.get(f"{A}/vouchers/{v['id']}/").json()

    def pending(self, amount="60000", **kw):
        v = self.expense(amount=amount, **kw)
        self.assertEqual(v["status"], "pending_approval", v)
        return v

    def posted(self, v):
        return JournalEntry.objects.filter(source="expense", source_id=v["id"])

    def lines(self, v):
        out = {}
        for line in JournalEntry.objects.get(source="expense", source_id=v["id"]).lines.select_related("account"):
            d, c = out.get(line.account.code, (D("0"), D("0")))
            out[line.account.code] = (d + line.debit, c + line.credit)
        return out


class ExpenseWaitsForApprovalTests(ExpenseApprovalBase):
    def test_a_small_expense_posts_at_once_even_when_a_rule_exists(self):
        v = self.expense(amount="50000")                      # exactly at the limit: not held
        self.assertEqual(v["status"], "recorded")
        self.assertRegex(v["number"], r"^T2N/EXP/26/\d{4}/00001$")
        self.assertEqual(self.lines(v), {"5200": (D("50000"), D("0")), "1010": (D("0"), D("50000"))})
        self.assertIsNone(self.request_of(v))
        self.tb()

    def test_one_paisa_over_the_limit_is_held_and_posts_nothing(self):
        v = self.pending(amount="50000.01")
        self.assertEqual((v["number"], v["status_label"], v["total"], v["entry_id"]), ("", "Pending approval", "50000.01", None))
        self.assertFalse(self.posted(v).exists())
        req = self.request_of(v)
        self.assertEqual((req.status, str(req.amount), req.kind, req.url), ("pending", "50000.01", "expense", f"/accounting/vouchers/{v['id']}"))
        self.assertEqual(approvals.status_of("expense", v["id"]), "pending")
        self.assertEqual(self.bal("bank"), D("0"))
        self.assertEqual(self.bal("5200"), D("0"))
        self.tb()

    def test_the_rule_looks_at_the_total_with_gst(self):
        self.enable_gst()
        # 45,000.00 + 18% (same state: CGST 4,050.00 + SGST 4,050.00) = 53,100.00, which is above 50,000.00
        v = self.expense(amount="45000", tax_mode="intra", tax_rate="18")
        self.assertEqual((v["status"], v["total"], v["tax_amount"]), ("pending_approval", "53100.00", "8100.00"))
        self.decide(v)
        self.assertEqual(self.lines(v), {"5200": (D("45000"), D("0")), "1310": (D("4050"), D("0")), "1320": (D("4050"), D("0")),
                                         "1010": (D("0"), D("53100"))})
        self.tb()

    def test_income_is_never_held(self):
        r = self.fin.post(f"{A}/vouchers/", {"kind": "income", "date": "2026-09-01", "category": str(self.acct("other_income").id),
                                              "money_account": str(self.acct("bank").id), "amount": "90000"})
        self.assertEqual((r.status_code, r.json()["status"]), (201, "recorded"))

    def test_a_waiting_expense_cannot_be_edited_recorded_again_deleted_or_voided(self):
        v = self.pending()
        self.assertEqual(self.fin.patch(f"{A}/vouchers/{v['id']}/", {"notes": "x"}).status_code, 400)
        self.assertEqual(self.fin.post(f"{A}/vouchers/{v['id']}/record/").status_code, 400)
        r = self.fin.delete(f"{A}/vouchers/{v['id']}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Take it back", str(r.json()))
        self.assertEqual(self.fin.post(f"{A}/vouchers/{v['id']}/void/", {"change_reason": "x"}).status_code, 400)
        self.assertEqual(Voucher.objects.get(pk=v["id"]).status, "pending_approval")

    def test_list_filters_and_counts_by_status_and_totals_only_count_recorded(self):
        self.pending()
        self.expense(amount="1000")
        r = self.fin.get(f"{A}/vouchers/", {"kind": "expense", "status": "pending_approval"}).json()
        self.assertEqual((r["count"], r["results"][0]["status"], r["total"]), (1, "pending_approval", 0))
        everything = self.fin.get(f"{A}/vouchers/", {"kind": "expense"}).json()
        self.assertEqual((everything["status_counts"], everything["total"]), ({"pending_approval": 1, "recorded": 1}, "1000.00"))

    def test_a_draft_is_kept_without_asking_for_approval_and_recorded_later(self):
        v = self.expense(amount="60000", record=False)
        self.assertEqual((v["status"], v["number"]), ("draft", ""))
        self.assertIsNone(self.request_of(v))
        r = self.fin.patch(f"{A}/vouchers/{v['id']}/", {"amount": "400", "notes": "smaller"})
        self.assertEqual((r.status_code, r.json()["total"], r.json()["notes"]), (200, "400.00", "smaller"))
        r = self.fin.post(f"{A}/vouchers/{v['id']}/record/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "recorded"))      # 400.00 is under the rule
        self.assertEqual(self.lines(v)["5200"], (D("400"), D("0")))
        self.assertEqual(self.fin.post(f"{A}/vouchers/{v['id']}/record/").status_code, 400)

    def test_only_an_expense_can_be_a_draft(self):
        r = self.fin.post(f"{A}/vouchers/", {"kind": "income", "date": "2026-09-01", "category": str(self.acct("other_income").id),
                                              "money_account": str(self.acct("bank").id), "amount": "10", "record": False})
        self.assertEqual(r.status_code, 400)
        self.assertIn("record", r.json())

    def test_a_draft_can_be_deleted(self):
        v = self.expense(amount="500", record=False)
        self.assertEqual(self.fin.delete(f"{A}/vouchers/{v['id']}/").status_code, 204)
        self.assertFalse(Voucher.objects.filter(pk=v["id"]).exists())
        recorded = self.expense(amount="500")
        self.assertEqual(self.fin.delete(f"{A}/vouchers/{recorded['id']}/").status_code, 400)


class ExpenseDecisionTests(ExpenseApprovalBase):
    def test_approving_numbers_and_posts_exactly_as_before(self):
        v = self.pending()
        self.decide(v, "approve", "Agreed with the vendor")
        done = self.get(v)
        self.assertEqual((done["status"], done["decision_note"], done["total"]), ("recorded", "Agreed with the vendor", "60000.00"))
        self.assertRegex(done["number"], r"^T2N/EXP/26/\d{4}/00001$")
        self.assertEqual(self.lines(v), {"5200": (D("60000"), D("0")), "1010": (D("0"), D("60000"))})
        self.assertEqual((self.bal("5200"), self.bal("bank")), (D("60000"), D("-60000")))
        self.tb()
        self.assertEqual(approvals.status_of("expense", v["id"]), "approved")
        reasons = [e.reason for e in CompanyProfileAuditEvent.objects.filter(entity_type="voucher", entity_id=v["id"]).order_by("occurred_at")]
        self.assertEqual(reasons, ["Sent for approval", "Approved: Agreed with the vendor"])
        # once recorded it is an ordinary entry and can be voided (the books go back to zero)
        r = self.fin.post(f"{A}/vouchers/{v['id']}/void/", {"change_reason": "Entered twice"})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "void"))
        self.assertEqual(self.bal("bank"), D("0"))
        self.tb()

    def test_the_decision_runs_once(self):
        v = self.pending()
        self.decide(v)
        self.decide(v, "approve", expect=409)
        self.decide(v, "reject", comment="too late", expect=409)
        self.assertEqual(self.posted(v).count(), 1)
        self.assertEqual(self.bal("5200"), D("60000"))

    def test_rejecting_returns_the_expense_to_a_draft_with_the_note(self):
        v = self.pending()
        self.decide(v, "reject", expect=400)                         # a reason is required
        self.decide(v, "reject", "No purchase order")
        d = self.get(v)
        self.assertEqual((d["status"], d["decision_note"], d["number"]), ("draft", "Rejected: No purchase order", ""))
        self.assertFalse(self.posted(v).exists())
        self.assertEqual(approvals.status_of("expense", v["id"]), "rejected")
        self.assertEqual(self.bal("bank"), D("0"))
        # correct it and record again: 5,000.00 is under the rules, so it posts at once (the old request stays rejected)
        self.assertEqual(self.fin.patch(f"{A}/vouchers/{v['id']}/", {"amount": "5000"}).status_code, 200)
        r = self.fin.post(f"{A}/vouchers/{v['id']}/record/").json()
        self.assertEqual((r["status"], r["total"]), ("recorded", "5000.00"))
        self.assertEqual(self.lines(v)["5200"], (D("5000"), D("0")))
        self.tb()

    def test_a_rejected_expense_recorded_again_above_the_limit_waits_with_a_fresh_request(self):
        v = self.pending()
        self.decide(v, "reject", "Check the bill")
        old = self.request_of(v)
        r = self.fin.post(f"{A}/vouchers/{v['id']}/record/").json()
        self.assertEqual(r["status"], "pending_approval")
        self.assertNotEqual(self.request_of(v).pk, old.pk)
        self.assertEqual(self.request_of(v).status, "pending")

    def test_a_rejected_request_sent_again_from_approvals_puts_the_expense_back_to_waiting(self):
        v = self.pending()
        self.decide(v, "reject", "Check the bill")
        r = self.api_mp.post(f"{AP}/requests/{self.request_of(v).pk}/resubmit/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.get(v)["status"], "pending_approval")
        self.decide(v)
        self.assertEqual(self.get(v)["status"], "recorded")

    def test_withdrawing_in_approvals_or_on_the_expense_returns_it_to_a_draft(self):
        v = self.pending()
        r = self.fin.post(f"{AP}/requests/{self.request_of(v).pk}/withdraw/", {"comment": "wrong account"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.get(v)["status"], "draft")
        v2 = self.pending()
        r = self.fin.post(f"{A}/vouchers/{v2['id']}/withdraw/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "draft"))
        self.assertEqual(approvals.status_of("expense", v2["id"]), "cancelled")
        self.assertEqual(self.fin.post(f"{A}/vouchers/{v2['id']}/withdraw/").status_code, 400)    # no longer waiting
        self.assertEqual(self.fin.delete(f"{A}/vouchers/{v2['id']}/").status_code, 204)
        self.assertFalse(self.posted(v).exists())

    def test_a_decision_that_cannot_post_is_rolled_back_with_the_reason(self):
        v = self.pending()
        bank = self.acct("bank")
        bank.is_active = False
        bank.save()
        r = self.decide(v, "approve", expect=400)
        self.assertIn("switched off", str(r.json()))
        self.assertEqual(self.get(v)["status"], "pending_approval")
        self.assertEqual(self.request_of(v).status, "pending")
        self.assertFalse(self.posted(v).exists())
        bank.is_active = True
        bank.save()
        self.decide(v)                                                # the same request can now be approved
        self.assertEqual(self.get(v)["status"], "recorded")
        self.tb()

    def test_the_requester_cannot_approve_their_own_expense(self):
        v = self.pending()
        self.decide(v, "approve", client=self.fin, expect=403)
        self.assertEqual(self.get(v)["status"], "pending_approval")

    def test_the_audit_trail_lists_the_steps(self):
        v = self.pending()
        self.decide(v, "reject", "Not now")
        self.fin.post(f"{A}/vouchers/{v['id']}/record/")
        self.decide(v)
        reasons = [e.reason for e in CompanyProfileAuditEvent.objects.filter(entity_type="voucher", entity_id=v["id"]).order_by("occurred_at")]
        self.assertEqual(reasons, ["Sent for approval", "Rejected: Not now", "Sent for approval", "Approved"])


class StandaloneAndPermissionTests(ExpenseApprovalBase):
    def test_without_the_approvals_module_expenses_post_directly(self):
        with mock.patch("apps.accounting.services.vouchers._approvals", return_value=None):
            v = self.expense(amount="90000")
        self.assertEqual(v["status"], "recorded")

    def test_with_no_active_rule_a_large_expense_posts_directly(self):
        from apps.approvals.models import ApprovalRule
        ApprovalRule.objects.filter(kind="expense").update(is_active=False)
        self.assertEqual(self.expense(amount="90000")["status"], "recorded")

    def test_a_person_with_accounting_manage_can_decide_directly_when_approvals_are_not_installed(self):
        v = self.pending()
        with mock.patch("apps.accounting.services.vouchers._approvals", return_value=None):
            self.assertEqual(self.fin.post(f"{A}/vouchers/{v['id']}/reject/", {}).status_code, 400)      # a reason is needed
            r = self.fin.post(f"{A}/vouchers/{v['id']}/reject/", {"change_reason": "Duplicate"})
            self.assertEqual((r.status_code, r.json()["status"], r.json()["decision_note"]), (200, "draft", "Rejected: Duplicate"))
            self.fin.post(f"{A}/vouchers/{v['id']}/record/")        # approvals missing, so it simply posts
            self.assertEqual(self.get(v)["status"], "recorded")
            self.assertEqual(self.fin.post(f"{A}/vouchers/{v['id']}/approve/").status_code, 400)           # not waiting any more

    def test_direct_approve_posts_when_approvals_are_not_installed(self):
        v = self.pending()
        with mock.patch("apps.accounting.services.vouchers._approvals", return_value=None):
            r = self.fin.post(f"{A}/vouchers/{v['id']}/approve/", {"note": "OK"})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["decision_note"]), (200, "recorded", "OK"))
        self.assertEqual(self.lines(v)["5200"], (D("60000"), D("0")))
        self.tb()

    def test_with_approvals_installed_the_expense_endpoints_do_not_decide(self):
        v = self.pending()
        for verb in ("approve", "reject"):
            r = self.fin.post(f"{A}/vouchers/{v['id']}/{verb}/", {"change_reason": "x"})
            self.assertEqual(r.status_code, 400, verb)
            self.assertIn("Approvals module", str(r.json()))
        self.assertEqual(self.get(v)["status"], "pending_approval")
        self.assertTrue(self.get(v)["approval_managed"])

    def test_every_role_against_the_expense_actions(self):
        v = self.pending()
        manage = {Role.ADMIN, Role.MANAGING_PARTNER, Role.FINANCE_MANAGER}
        for role in ALL_ROLES:
            c = client_for(make_user(f"u_{role}", role))
            if self.get(v)["status"] == "draft":
                self.fin.post(f"{A}/vouchers/{v['id']}/record/")          # back to waiting for this role
            self.assertEqual(self.get(v)["status"], "pending_approval")
            who = ((400,) if role in manage else (403,))
            self.assertIn(c.patch(f"{A}/vouchers/{v['id']}/", {"notes": "n"}).status_code, who, role)
            self.assertIn(c.delete(f"{A}/vouchers/{v['id']}/").status_code, who, role)
            self.assertEqual(c.get(f"{A}/vouchers/{v['id']}/").status_code, 200 if role in manage | {Role.PARTNER} else 403, role)
            for verb in ("record", "approve", "reject", "withdraw"):       # withdraw last: it takes the expense back to a draft
                code = c.post(f"{A}/vouchers/{v['id']}/{verb}/", {"change_reason": "x"}).status_code
                if role in manage:
                    self.assertNotEqual(code, 403, (role, verb))
                else:
                    self.assertEqual(code, 403, (role, verb))
        self.assertEqual(self.anon.post(f"{A}/vouchers/{v['id']}/withdraw/").status_code in (401, 403), True)
        self.assertEqual(self.anon.get(f"{A}/vouchers/{v['id']}/").status_code in (401, 403), True)

    def test_an_unknown_expense_is_404(self):
        for verb in ("withdraw", "record", "approve", "reject", "void"):
            self.assertEqual(self.fin.post(f"{A}/vouchers/{GHOST}/{verb}/", {}).status_code, 404, verb)
        self.assertEqual(self.fin.get(f"{A}/vouchers/{GHOST}/").status_code, 404)
        self.assertEqual(self.fin.delete(f"{A}/vouchers/{GHOST}/").status_code, 404)


class ExpenseHookTests(ExpenseApprovalBase):
    def test_describe_lets_the_approval_button_submit_a_waiting_expense_with_no_open_request(self):
        v = self.pending()
        info = describe(v["id"])
        self.assertEqual((info["amount"], info["url"]), (D("60000.00"), f"/accounting/vouchers/{v['id']}"))
        self.assertTrue(info["requested_by_ok"](self.finance))
        self.assertFalse(info["requested_by_ok"](self.staff))
        self.assertIsNone(describe(GHOST))
        self.assertIsNone(describe("nonsense"))
        approvals.cancel_for_source("expense", v["id"], self.finance)       # closed behind the expense's back
        self.assertEqual(approvals.status_of("expense", v["id"]), "cancelled")
        r = self.fin.post(f"{AP}/submit/", {"kind": "expense", "source_id": v["id"]}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (201, "pending"))
        self.decide(v)
        self.assertEqual(self.get(v)["status"], "recorded")

    def test_the_kind_is_registered_with_its_label_and_can_be_submitted_from_the_ui(self):
        kinds = {k["value"]: k for k in self.api_mp.get(f"{AP}/kinds/").json()["kinds"]}
        self.assertEqual((kinds["expense"]["label"], kinds["expense"]["can_submit_from_ui"]), ("Expense", True))

    def test_hooks_ignore_an_expense_that_is_no_longer_waiting_and_unknown_ones(self):
        v = self.expense(amount="100")                              # already recorded
        req = mock.MagicMock(source_id=v["id"], requested_by=self.finance)
        req.decisions.select_related.return_value.order_by.return_value.first.return_value = None
        on_approved(req)
        on_rejected(req)
        on_resubmitted(req)
        self.assertEqual(Voucher.objects.get(pk=v["id"]).status, "recorded")
        for fn in (on_approved, on_rejected, on_cancelled, on_resubmitted):
            fn(mock.MagicMock(source_id=GHOST, requested_by=self.finance))
            fn(mock.MagicMock(source_id="nonsense", requested_by=self.finance))

    def test_the_request_shows_in_the_inbox_with_a_link_back_to_the_expense(self):
        v = self.pending(party_name="Cloud Hosting Ltd")
        rows = self.api_mp.get(f"{AP}/requests/", {"scope": "inbox"}).json()["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["kind_label"], rows[0]["url"], rows[0]["amount"], rows[0]["title"]),
                         ("expense", "Expense", f"/accounting/vouchers/{v['id']}", "60000.00", "Expense: Cloud Hosting Ltd"))
