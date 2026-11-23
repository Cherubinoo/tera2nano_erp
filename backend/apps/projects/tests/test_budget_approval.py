"""A project budget change under an approval rule waits for approval; approving applies it."""
from decimal import Decimal
from unittest import mock

from apps.accounts.models import Role
from apps.approvals import api as approvals
from apps.approvals import rules
from apps.company.models import CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user
from apps.projects import budget_approval
from apps.projects.models import BudgetRevision, Project

from .base import P, ProjectsBase

D = Decimal
A = "/api/v1/approvals"
GHOST = "00000000-0000-0000-0000-000000000000"
LINES = [{"category": "labour", "amount": "30000"}, {"category": "travel", "amount": "5000"}]


class BudgetApprovalBase(ProjectsBase):
    def setUp(self):
        super().setUp()
        # every budget change goes to the Managing Partner
        rules.save_rule(self.company, {"kind": "budget", "name": "Budgets", "priority": 1, "steps": [{"approver_type": "role", "role": "managing_partner"}]})
        self.p = self.project(budget="60000.00", budget_lines=LINES)

    def change(self, client=None, expect=200, **body):
        r = (client or self.pm).patch(f"{P}/projects/{self.p['id']}/", body, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def rev(self):
        return BudgetRevision.objects.filter(project_id=self.p["id"]).order_by("-created_at").first()

    def request_of(self, rev=None):
        return approvals.latest_request("budget", (rev or self.rev()).pk)

    def decide(self, verb="approve", comment="", client=None, expect=200):
        r = (client or self.managing).post(f"{A}/requests/{self.request_of().pk}/{verb}/", {"comment": comment})
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def fresh(self):
        return self.pm.get(f"{P}/projects/{self.p['id']}/").json()


class BudgetChangeWaitsTests(BudgetApprovalBase):
    def test_a_new_budget_total_waits_and_the_project_keeps_the_old_one(self):
        out = self.change(budget="80000", name="Alpha 2")
        self.assertEqual((out["name"], out["budget"]), ("Alpha 2", "60000.00"))          # the other edits are saved now
        pend = out["pending_budget_change"]
        self.assertEqual((pend["status"], pend["previous_budget"], pend["proposed_budget"], pend["proposed_total"]),
                         ("pending_approval", "60000.00", "80000.00", "80000.00"))
        req = self.request_of()
        self.assertEqual((req.kind, req.status, str(req.amount), req.url), ("budget", "pending", "80000.00", f"/projects/{self.p['id']}"))
        self.assertEqual(approvals.status_of("budget", self.rev().pk), "pending")
        self.assertTrue(out["budget_approval_managed"])

    def test_changed_lines_wait_too_and_the_amount_is_the_total_in_force(self):
        out = self.change(budget_lines=[{"category": "materials", "amount": "100"}])
        self.assertEqual([l["category"] for l in out["budget_lines"]], ["labour", "travel"])      # unchanged
        self.assertEqual([(l["category"], l["amount"]) for l in out["pending_budget_change"]["proposed_lines"]], [("materials", "100")])
        self.assertEqual(str(self.request_of().amount), "60000.00")                           # total stays 60,000

    def test_when_no_total_is_entered_the_sum_of_the_lines_is_the_amount(self):
        q = self.project(name="Beta", budget="0", budget_lines=[{"category": "labour", "amount": "700"}, {"category": "other", "amount": "300"}])
        r = self.pm.patch(f"{P}/projects/{q['id']}/", {"budget_lines": [{"category": "labour", "amount": "900"}, {"category": "other", "amount": "300"}]},
                          format="json")
        self.assertEqual(r.status_code, 200, r.content)
        rev = BudgetRevision.objects.get(project_id=q["id"])
        self.assertEqual((rev.proposed_total, str(approvals.latest_request("budget", rev.pk).amount)), (D("1200.00"), "1200.00"))

    def test_edits_that_do_not_change_the_budget_are_saved_at_once(self):
        out = self.change(name="Alpha 3", priority="high", budget="60000.00", budget_lines=LINES)
        self.assertEqual((out["name"], out["priority"], out["pending_budget_change"]), ("Alpha 3", "high", None))
        self.assertFalse(BudgetRevision.objects.exists())

    def test_a_second_change_is_refused_while_one_waits(self):
        self.change(budget="80000")
        r = self.change(budget="90000", expect=400)
        self.assertIn("already waiting", r["budget"])
        self.assertEqual(BudgetRevision.objects.count(), 1)
        self.assertEqual(self.change(name="Still editable")["name"], "Still editable")          # non-budget edits are fine

    def test_creating_a_project_sets_its_first_budget_directly(self):
        self.assertEqual(self.p["budget"], "60000.00")
        self.assertEqual(self.p["pending_budget_change"], None)

    def test_with_no_active_rule_or_without_approvals_the_change_applies_at_once(self):
        from apps.approvals.models import ApprovalRule
        ApprovalRule.objects.filter(kind="budget").update(is_active=False)
        self.assertEqual(self.change(budget="80000")["budget"], "80000.00")
        ApprovalRule.objects.filter(kind="budget").update(is_active=True)
        with mock.patch("apps.projects.budget_approval._api", return_value=None):
            self.assertEqual(self.change(budget="90000")["budget"], "90000.00")
        self.assertFalse(BudgetRevision.objects.exists())

    def test_an_amount_band_decides_which_changes_wait(self):
        from apps.approvals.models import ApprovalRule
        ApprovalRule.objects.filter(kind="budget").update(amount_from="100000.01")
        self.assertEqual(self.change(budget="100000")["budget"], "100000.00")                   # at the limit: applied
        out = self.change(budget="100000.01")
        self.assertEqual((out["budget"], out["pending_budget_change"]["proposed_budget"]), ("100000.00", "100000.01"))

    def test_validation_still_applies(self):
        self.change(budget="abc", expect=400)
        r = self.change(budget="50000", budget_lines=[{"category": "labour", "amount": "70000"}], expect=400)
        self.assertIn("budget_lines", r)
        self.assertFalse(BudgetRevision.objects.exists())


class BudgetDecisionTests(BudgetApprovalBase):
    def test_approving_applies_the_total_and_the_lines(self):
        self.change(budget="80000", budget_lines=[{"category": "labour", "amount": "50000"}, {"category": "materials", "amount": "10000"}])
        self.decide("approve", "Client agreed the extra scope")
        now = self.fresh()
        self.assertEqual(now["budget"], "80000.00")
        self.assertEqual([(l["category"], l["amount"]) for l in now["budget_lines"]], [("labour", "50000.00"), ("materials", "10000.00")])
        self.assertIsNone(now["pending_budget_change"])
        rev = self.rev()
        self.assertEqual((rev.status, rev.decision_note, rev.decided_by.username), ("approved", "Client agreed the extra scope", "managing"))
        b = self.pm.get(f"{P}/projects/{self.p['id']}/budget/").json()
        self.assertEqual((b["budget_total"], b["allocated"], b["unallocated"]), ("80000.00", "60000.00", "20000.00"))
        texts = [e.reason for e in CompanyProfileAuditEvent.objects.filter(entity_type="project", entity_id=self.p["id"]).order_by("occurred_at")]
        self.assertTrue(any(t.startswith("Budget change sent for approval") for t in texts))
        self.assertTrue(any(t.startswith("Budget change approved") for t in texts))

    def test_rejecting_keeps_the_budget_and_records_the_reason(self):
        self.change(budget="80000")
        self.decide("reject", expect=400)                                  # a reason is needed
        self.decide("reject", "Too much for this contract")
        now = self.fresh()
        self.assertEqual((now["budget"], now["pending_budget_change"]), ("60000.00", None))
        self.assertEqual((self.rev().status, self.rev().decision_note), ("rejected", "Too much for this contract"))
        self.assertEqual(approvals.status_of("budget", self.rev().pk), "rejected")
        self.change(budget="70000")                                          # a new change can now be asked for
        self.assertEqual(BudgetRevision.objects.count(), 2)

    def test_a_rejected_request_sent_again_waits_once_more_and_can_then_be_approved(self):
        self.change(budget="80000")
        self.decide("reject", "Not now")
        r = self.managing.post(f"{A}/requests/{self.request_of().pk}/resubmit/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.rev().status, "pending_approval")
        self.decide("approve")
        self.assertEqual(self.fresh()["budget"], "80000.00")

    def test_withdrawing_in_approvals_or_on_the_project_leaves_the_budget(self):
        self.change(budget="80000")
        r = self.pm.post(f"{A}/requests/{self.request_of().pk}/withdraw/", {"comment": "changed my mind"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((self.rev().status, self.fresh()["budget"]), ("withdrawn", "60000.00"))
        self.change(budget="90000")
        rid = self.rev().pk
        r = self.pm.post(f"{P}/budget-changes/{rid}/withdraw/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "withdrawn"))
        self.assertEqual(approvals.status_of("budget", rid), "cancelled")
        self.assertEqual(self.pm.post(f"{P}/budget-changes/{rid}/withdraw/").status_code, 400)

    def test_the_requester_cannot_approve_their_own_budget_change(self):
        self.change(budget="80000")
        self.decide("approve", client=self.pm, expect=403)
        self.assertEqual(self.fresh()["budget"], "60000.00")

    def test_the_inbox_links_back_to_the_project(self):
        self.change(budget="80000")
        row = self.managing.get(f"{A}/requests/", {"scope": "inbox"}).json()["results"][0]
        self.assertEqual((row["kind_label"], row["url"], row["amount"]), ("Project budget", f"/projects/{self.p['id']}", "80000.00"))

    def test_the_change_list_shows_every_revision(self):
        self.change(budget="80000")
        self.decide("reject", "no")
        self.change(budget="70000")
        rows = self.pm.get(f"{P}/projects/{self.p['id']}/budget-changes/").json()
        self.assertEqual([(r["status"], r["proposed_budget"]) for r in rows], [("pending_approval", "70000.00"), ("rejected", "80000.00")])


class StandaloneAndPermissionTests(BudgetApprovalBase):
    def test_a_person_with_projects_manage_decides_directly_when_approvals_are_not_installed(self):
        self.change(budget="80000")
        rid = self.rev().pk
        with mock.patch("apps.projects.budget_approval._api", return_value=None):
            r = self.pm.post(f"{P}/budget-changes/{rid}/reject/", {})
            self.assertEqual(r.status_code, 400)
            self.assertIn("change_reason", r.json())
            r = self.pm.post(f"{P}/budget-changes/{rid}/approve/", {"note": "ok"})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "approved"))
        self.assertEqual(self.fresh()["budget"], "80000.00")
        self.assertEqual(self.pm.post(f"{P}/budget-changes/{rid}/approve/").status_code, 400)       # already decided

    def test_with_approvals_installed_the_project_endpoints_do_not_decide(self):
        self.change(budget="80000")
        rid = self.rev().pk
        for verb in ("approve", "reject"):
            r = self.pm.post(f"{P}/budget-changes/{rid}/{verb}/", {"change_reason": "x"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("Approvals module", str(r.json()))

    def test_permissions_and_404s(self):
        self.change(budget="80000")
        rid = self.rev().pk
        for who in (self.hr, self.partner):
            self.assertEqual(who.post(f"{P}/budget-changes/{rid}/withdraw/").status_code, 403)
            self.assertEqual(who.patch(f"{P}/projects/{self.p['id']}/", {"budget": "1"}, format="json").status_code, 403)
        self.assertEqual(self.partner.get(f"{P}/projects/{self.p['id']}/budget-changes/").status_code, 200)   # projects.view
        self.assertEqual(client_for(make_user("emp", Role.EMPLOYEE)).get(f"{P}/projects/{self.p['id']}/budget-changes/").status_code, 403)
        self.assertEqual(self.anon.get(f"{P}/projects/{self.p['id']}/budget-changes/").status_code in (401, 403), True)
        for verb in ("approve", "reject", "withdraw"):
            self.assertEqual(self.pm.post(f"{P}/budget-changes/{GHOST}/{verb}/", {}).status_code, 404, verb)
        self.assertEqual(self.pm.get(f"{P}/projects/{GHOST}/budget-changes/").status_code, 404)
        self.assertEqual(self.pm.post(f"{P}/budget-changes/nope/withdraw/").status_code, 404)

    def test_describe_and_the_button_path_for_a_waiting_change_whose_request_was_closed(self):
        self.change(budget="80000")
        rev = self.rev()
        info = budget_approval.describe(str(rev.pk))
        self.assertEqual((info["amount"], info["url"]), (D("80000.00"), f"/projects/{self.p['id']}"))
        self.assertTrue(info["requested_by_ok"](self.pm_user))
        self.assertFalse(info["requested_by_ok"](self.hr_user))
        self.assertIsNone(budget_approval.describe(GHOST))
        self.assertIsNone(budget_approval.describe("nonsense"))
        approvals.cancel_for_source("budget", rev.pk, self.pm_user)
        r = self.pm.post(f"{A}/submit/", {"kind": "budget", "source_id": str(rev.pk)}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (201, "pending"))
        self.decide("approve")
        self.assertEqual(self.fresh()["budget"], "80000.00")

    def test_hooks_ignore_a_missing_or_decided_revision(self):
        self.change(budget="80000")
        self.decide("approve")
        req = mock.MagicMock(source_id=str(self.rev().pk), requested_by=self.pm_user)
        req.decisions.select_related.return_value.order_by.return_value.first.return_value = None
        budget_approval.on_approved(req)
        budget_approval.on_rejected(req)
        self.assertEqual(self.rev().status, "approved")
        gone = mock.MagicMock(source_id=GHOST, requested_by=self.pm_user)
        for fn in (budget_approval.on_approved, budget_approval.on_rejected, budget_approval.on_cancelled, budget_approval.on_resubmitted):
            fn(gone)

    def test_the_kind_is_registered(self):
        kinds = {k["value"]: k for k in self.admin_kinds()}
        self.assertEqual((kinds["budget"]["label"], kinds["budget"]["can_submit_from_ui"]), ("Project budget", True))

    def admin_kinds(self):
        return self.api.get(f"{A}/kinds/").json()["kinds"]
