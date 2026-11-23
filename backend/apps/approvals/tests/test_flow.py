from decimal import Decimal

from rest_framework.exceptions import PermissionDenied

from apps.accounts import access
from apps.approvals import engine
from apps.approvals.models import ApprovalEvent, ApprovalRequest, ApprovalRule

from .base import A, ApprovalBase, at

FIN, HR, MP, PARTNER, ADMIN = (access.FINANCE_MANAGER, access.HR_MANAGER, access.MANAGING_PARTNER, access.PARTNER, access.ADMIN)


class RuleMatchingTests(ApprovalBase):
    def test_amount_bands_and_priority_pick_the_rule(self):
        big = self.rule([FIN, MP], name="Big", amount_from="1000", priority=10)
        small = self.rule([HR], name="Everything else", priority=20)
        self.assertEqual(self.submit(amount="5000", source="a").rule_id, big.pk)
        self.assertEqual(self.submit(amount="999.99", source="b").rule_id, small.pk)
        self.assertEqual(self.submit(amount="1000", source="c").rule_id, big.pk)  # the lower limit is inclusive

    def test_upper_limit_is_inclusive_and_swapping_priority_changes_the_winner(self):
        banded = self.rule([FIN], name="Up to 50,000", amount_to="50000", priority=20)
        catch_all = self.rule([HR], name="Catch all", priority=10)
        self.assertEqual(self.submit(amount="50000", source="a").rule_id, catch_all.pk)  # lower number wins
        catch_all.priority = 30
        catch_all.save()
        self.assertEqual(self.submit(amount="50000", source="b").rule_id, banded.pk)
        self.assertEqual(self.submit(amount="50000.01", source="c").rule_id, catch_all.pk)

    def test_inactive_rules_are_skipped_and_project_text_must_match(self):
        self.rule([FIN], name="Off", priority=1, is_active=False)
        on = self.rule([HR], name="Alpha only", priority=2, project_text="alpha")
        self.assertIsNone(self.submit(source="a", meta={"project": "Beta"}).rule_id)
        self.assertEqual(self.submit(source="b", meta={"project": "Project ALPHA 2"}).rule_id, on.pk)

    def test_no_rule_goes_to_the_managing_partner_unless_the_requester_may_approve_it(self):
        req = self.submit(source="a")  # staff holds no sales.manage
        self.assertEqual((req.status, req.rule_id, req.steps.get().role), ("pending", None, MP))
        self.assertEqual(self.inbox("mp"), [req.reference_number])
        auto = self.submit(user=self.finance, source="b")  # finance holds sales.manage
        self.assertEqual(auto.status, "approved")
        self.assertEqual(self.calls, [("approved", "b")])
        self.assertTrue(auto.events.filter(action="auto_approved").exists())

    def test_reference_number_has_year_and_date(self):
        req = self.submit()
        self.assertRegex(req.reference_number, r"^T2N/APR/\d{2}/\d{4}/00001$")
        self.assertRegex(self.submit(source="d2").reference_number, r"/00002$")

    def test_default_rules_are_seeded_once_and_work(self):
        r = self.c["admin"].get(f"{A}/rules/")
        self.assertEqual(r.status_code, 200)
        names = [x["name"] for x in r.json()]
        self.assertIn("Purchase orders above 50,000", names)
        count = len(names)
        self.assertEqual(len(self.c["admin"].get(f"{A}/rules/").json()), count)  # not duplicated
        po = self.submit(kind="purchase_order", source="po1", amount="60000")
        self.assertEqual([s.role for s in po.steps.all()], [FIN, MP])
        wd = self.submit(user=self.mp, kind="partner_withdrawal", source="w1", amount="1000")
        step = wd.steps.get()
        self.assertEqual((step.role, step.mode), (PARTNER, "all"))
        leave = self.submit(kind="leave_request", source="l1")
        self.assertEqual(leave.steps.get().role, HR)

    def test_deleting_every_rule_is_respected(self):
        self.c["admin"].get(f"{A}/rules/")
        for rule in self.c["admin"].get(f"{A}/rules/").json():
            self.assertEqual(self.c["admin"].delete(f"{A}/rules/{rule['id']}/").status_code, 204)
        self.assertEqual(self.c["admin"].get(f"{A}/rules/").json(), [])
        self.assertEqual(ApprovalRule.objects.count(), 0)

    def test_small_purchase_order_without_a_rule_is_approved_by_a_buyer(self):
        req = self.submit(user=self.finance, kind="purchase_order", source="po2", amount="40000")
        self.assertEqual(req.status, "approved")


class ChainTests(ApprovalBase):
    def test_two_step_chain_runs_the_handler_once_at_the_end(self):
        self.rule([FIN, MP])
        req = self.submit()
        self.assertEqual(self.inbox("finance"), [req.reference_number])
        self.assertEqual(self.inbox("mp"), [])
        self.act("finance", req)
        req = self.reload(req)
        self.assertEqual((req.status, req.current_step), ("pending", 2))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.inbox("finance"), [])
        self.assertEqual(self.inbox("mp"), [req.reference_number])
        self.act("mp", req, comment="Go ahead")
        req = self.reload(req)
        self.assertEqual(req.status, "approved")
        self.assertIsNotNone(req.decided_at)
        self.assertEqual(self.calls, [("approved", "d1")])
        self.assertEqual([e.action for e in req.events.all()], ["submitted", "step_approved", "approved"])

    def test_a_step_out_of_turn_is_refused(self):
        self.rule([FIN, MP])
        req = self.submit()
        self.act("mp", req, expect=403)

    def test_any_mode_one_approval_is_enough(self):
        self.rule([PARTNER])
        req = self.submit()
        self.act("p1", req)
        self.assertEqual(self.reload(req).status, "approved")
        self.act("p2", req, expect=409)  # already decided

    def test_all_mode_needs_every_approver(self):
        self.rule([(PARTNER, "all", None)])
        req = self.submit()
        self.act("p1", req)
        req = self.reload(req)
        self.assertEqual((req.status, req.current_step), ("pending", 1))
        self.assertEqual(self.inbox("p1"), [])  # p1 has done their part
        self.assertEqual(self.inbox("p2"), [req.reference_number])
        self.act("p1", req, expect=409)
        self.assertEqual(self.calls, [])
        self.act("p2", req)
        self.assertEqual(self.reload(req).status, "approved")
        self.assertEqual(len(self.calls), 1)

    def test_all_mode_one_partner_rejecting_ends_it(self):
        self.rule([(PARTNER, "all", None)])
        req = self.submit()
        self.act("p1", req)
        self.act("p2", req, "reject", "Not now")
        self.assertEqual(self.reload(req).status, "rejected")
        self.assertEqual(self.calls, [("rejected", "d1")])

    def test_requester_among_the_partners_is_left_out_of_all_of_them(self):
        self.rule([(PARTNER, "all", None)])
        req = self.submit(user=self.p1)
        self.act("p1", req, expect=403)
        self.act("p2", req)  # only the other partner is needed
        self.assertEqual(self.reload(req).status, "approved")

    def test_a_specific_user_step(self):
        from apps.approvals import rules
        rules.save_rule(self.company, {"kind": "test_doc", "name": "By name", "steps": [{"approver_type": "user", "user": str(self.pm.pk)}]})
        req = self.submit()
        self.act("finance", req, expect=403)
        self.act("pm", req)
        self.assertEqual(self.reload(req).status, "approved")


class SelfApprovalTests(ApprovalBase):
    def test_requester_cannot_approve_their_own_request(self):
        self.rule([FIN])
        req = self.submit(user=self.finance)
        self.act("finance", req, expect=403)
        self.assertEqual(self.reload(req).status, "pending")
        # nobody else holds the finance role, so a Managing Partner or administrator takes it
        self.assertEqual(self.inbox("mp"), [req.reference_number])
        self.act("mp", req)
        self.assertEqual(self.reload(req).status, "approved")

    def test_a_step_with_no_approvers_falls_back_to_managing_partner_and_admin(self):
        self.rule([PARTNER])
        for p in (self.p1, self.p2):
            p.is_active = False
            p.save()
        req = self.submit()
        self.assertEqual(self.inbox("admin"), [req.reference_number])
        self.assertEqual(self.inbox("mp"), [req.reference_number])
        self.assertEqual(self.inbox("finance"), [])

    def test_admin_requester_with_another_admin_cannot_self_approve(self):
        self.rule([ADMIN])
        req = self.submit(user=self.admin)
        self.act("admin", req, expect=403)
        self.act("admin2", req)
        self.assertEqual(self.reload(req).status, "approved")

    def test_sole_administrator_may_approve_their_own_request_and_it_is_recorded(self):
        self.rule([ADMIN])
        self.admin2.is_active = False
        self.admin2.save()
        self.mp.is_active = False
        self.mp.save()
        req = self.submit(user=self.admin)
        self.act("admin", req, comment="Mine")
        req = self.reload(req)
        self.assertEqual(req.status, "approved")
        self.assertIn("requester", req.events.get(action="approved").comment)

    def test_non_admin_requester_never_self_approves(self):
        self.rule([FIN])
        self.admin2.is_active = False
        self.admin2.save()
        self.admin.is_active = False
        self.admin.save()
        self.mp.is_active = False
        self.mp.save()
        req = self.submit(user=self.finance)
        self.assertEqual(self.inbox("finance"), [])
        self.act("finance", req, expect=403)


class DecisionTests(ApprovalBase):
    def test_reject_needs_a_comment_and_records_it(self):
        self.rule([FIN])
        req = self.submit()
        r = self.c["finance"].post(f"{A}/requests/{req.pk}/reject/", {"comment": "  "})
        self.assertEqual(r.status_code, 400)
        self.assertIn("comment", r.json())
        self.assertEqual(self.reload(req).status, "pending")
        data = self.act("finance", req, "reject", "Over budget")
        self.assertEqual(data["status"], "rejected")
        self.assertEqual(data["steps"][0]["decisions"][0]["comment"], "Over budget")
        self.assertEqual(data["events"][-1]["action"], "rejected")
        self.assertEqual(self.calls, [("rejected", "d1")])

    def test_double_decision_is_refused(self):
        self.rule([FIN])
        req = self.submit()
        self.act("finance", req)
        self.act("finance", req, expect=409)
        self.act("finance", req, "reject", "late", expect=409)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.reload(req).decisions.count(), 1)

    def test_failing_handler_rolls_the_decision_back(self):
        self.rule([FIN])
        req = self.submit()
        self.fail_on = "approved"
        r = self.c["finance"].post(f"{A}/requests/{req.pk}/approve/", {})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no longer in a state", str(r.json()))
        req = self.reload(req)
        self.assertEqual(req.status, "pending")
        self.assertEqual(req.decisions.count(), 0)
        self.assertEqual(req.steps.get().state, "active")
        self.assertEqual([e.action for e in req.events.all()], ["submitted"])
        self.fail_on = None
        self.act("finance", req)  # works once the record is fixed
        self.assertEqual(self.reload(req).status, "approved")

    def test_failing_reject_handler_rolls_back_too(self):
        self.rule([FIN])
        req = self.submit()
        self.fail_on = "rejected"
        self.act("finance", req, "reject", "no", expect=400)
        self.assertEqual(self.reload(req).status, "pending")

    def test_handler_exception_inside_a_multi_step_chain_keeps_the_earlier_step(self):
        self.rule([FIN, MP])
        req = self.submit()
        self.act("finance", req)
        self.fail_on = "approved"
        self.act("mp", req, expect=400)
        req = self.reload(req)
        self.assertEqual((req.status, req.current_step), ("pending", 2))
        self.assertEqual(req.decisions.count(), 1)

    def test_unknown_kind_still_decides_without_a_handler(self):
        from apps.approvals import rules
        rules.save_rule(self.company, {"kind": "budget", "name": "b", "steps": [{"role": MP}]})
        req = self.submit(kind="budget", source="bud1")
        self.act("mp", req)
        self.assertEqual(self.reload(req).status, "approved")

    def test_double_submit_is_refused_and_status_follows(self):
        from apps.approvals import api
        self.assertEqual(api.status_of("test_doc", "d1"), "none")
        self.rule([FIN])
        req = self.submit()
        self.assertEqual(api.status_of("test_doc", "d1"), "pending")
        with self.assertRaises(Exception):
            self.submit()
        self.act("finance", req)
        self.assertEqual(api.status_of("test_doc", "d1"), "approved")


class WithdrawResubmitDelegateTests(ApprovalBase):
    def test_requester_can_withdraw_and_the_module_is_told(self):
        self.rule([FIN])
        req = self.submit()
        self.act("emp2", req, "withdraw", expect=404)  # not involved at all
        self.act("pm", req, "withdraw", expect=403)  # may see it? pm has approvals.view but is not the requester
        data = self.act("staff", req, "withdraw", "Changed my mind")
        self.assertEqual(data["status"], "cancelled")
        self.assertEqual(self.calls, [("cancelled", "d1")])
        self.act("staff", req, "withdraw", expect=409)
        self.act("finance", req, expect=409)

    def test_cancel_for_source_closes_it_without_calling_back(self):
        from apps.approvals import api
        self.rule([FIN])
        req = self.submit()
        api.cancel_for_source("test_doc", "d1", self.finance)
        self.assertEqual(self.reload(req).status, "cancelled")
        self.assertEqual(self.calls, [])
        self.assertIsNone(api.cancel_for_source("test_doc", "d1", self.finance))
        self.assertEqual(api.status_of("test_doc", "d1"), "cancelled")

    def test_resubmit_after_rejection_makes_a_new_linked_request(self):
        self.rule([FIN])
        self.records["d1"] = {"title": "Corrected title", "amount": "250.00"}
        req = self.submit(amount="100")
        self.act("finance", req, "reject", "Fix the amount")
        self.act("emp2", req, "resubmit", expect=404)
        self.act("pm", req, "resubmit", expect=403)
        data = self.act("staff", req, "resubmit", expect=201)
        self.assertNotEqual(data["reference_number"], req.reference_number)
        self.assertEqual((data["status"], data["previous"], data["title"], data["amount"]),
                         ("pending", req.reference_number, "Corrected title", "250.00"))
        self.assertIn(("resubmitted", "d1"), self.calls)
        self.assertEqual(data["events"][0]["action"], "resubmitted")
        # the old request keeps its history untouched
        self.assertEqual(self.reload(req).status, "rejected")
        # a pending one cannot be sent again
        again = self.c["staff"].post(f"{A}/requests/{data['id']}/resubmit/")
        self.assertEqual(again.status_code, 400)

    def test_withdrawn_request_can_be_resubmitted(self):
        self.rule([FIN])
        req = self.submit()
        self.act("staff", req, "withdraw")
        data = self.act("staff", req, "resubmit", expect=201)
        self.assertEqual(data["status"], "pending")

    def test_reassigning_a_step(self):
        self.rule([FIN])
        req = self.submit()
        r = self.c["finance"].post(f"{A}/requests/{req.pk}/reassign/", {"user": str(self.pm.pk)})
        self.assertEqual(r.status_code, 403)  # finance holds neither approvals.manage nor hr.manage
        r = self.c["hr"].post(f"{A}/requests/{req.pk}/reassign/", {"user": str(self.pm.pk), "comment": "Away"})
        self.assertEqual(r.status_code, 200, r.content)  # hr.manage may reassign
        self.assertEqual(r.json()["steps"][0]["who"], "pm")
        self.assertTrue(r.json()["steps"][0]["delegated"])
        self.assertEqual(self.inbox("pm"), [req.reference_number])
        self.assertEqual(self.inbox("finance"), [])
        self.act("finance", req, expect=403)
        self.act("pm", req)
        self.assertEqual(self.reload(req).status, "approved")
        self.assertTrue(req.events.filter(action="delegated").exists())

    def test_reassign_checks_the_target_and_the_state(self):
        self.rule([FIN])
        req = self.submit()
        for payload in ({}, {"user": "not-a-uuid"}, {"user": str(self.staff.pk)}):
            r = self.c["admin"].post(f"{A}/requests/{req.pk}/reassign/", payload)
            self.assertEqual(r.status_code, 400, payload)
        self.act("finance", req)
        r = self.c["admin"].post(f"{A}/requests/{req.pk}/reassign/", {"user": str(self.pm.pk)})
        self.assertEqual(r.status_code, 409)

    def test_reassigning_a_partial_all_step_needs_the_new_person(self):
        self.rule([(PARTNER, "all", None)])
        req = self.submit()
        self.act("p1", req)
        self.c["admin"].post(f"{A}/requests/{req.pk}/reassign/", {"user": str(self.pm.pk)})
        self.assertEqual(self.reload(req).status, "pending")
        self.act("p2", req, expect=403)
        self.act("pm", req)
        self.assertEqual(self.reload(req).status, "approved")


class EscalationTests(ApprovalBase):
    def test_deadline_and_sweep_with_fixed_dates(self):
        self.rule([(HR, "any", 2)])
        req = self.submit(now=at(10, 10))
        step = req.steps.get()
        self.assertEqual(engine.deadline(step), at(12, 10))
        self.assertFalse(engine.is_overdue(step, at(12, 9)))
        self.assertTrue(engine.is_overdue(step, at(12, 10)))
        self.assertEqual(engine.sweep(self.company, at(12, 9)), 0)
        self.assertEqual(self.reload(req).status, "pending")
        self.assertEqual(engine.sweep(self.company, at(12, 10)), 1)
        req = self.reload(req)
        self.assertEqual((req.status, req.escalated_at), ("escalated", at(12, 10)))
        self.assertEqual(engine.sweep(self.company, at(13, 10)), 0)  # only once
        self.assertEqual(req.events.filter(action="escalated").count(), 1)

    def test_managing_partner_may_approve_only_after_escalation(self):
        self.rule([(HR, "any", 2)])
        req = self.submit(now=at(10, 10))
        with self.assertRaises(PermissionDenied):
            engine.decide(req.pk, self.mp, "approve", "", now=at(11, 10))
        done = engine.decide(req.pk, self.mp, "approve", "Escalated approval", now=at(12, 11))
        self.assertEqual(done.status, "approved")

    def test_escalated_request_moves_back_to_pending_on_the_next_step(self):
        self.rule([(FIN, "any", 1), (MP, "any", None)])
        req = self.submit(now=at(10, 10))
        engine.sweep(self.company, at(11, 11))
        self.assertEqual(self.reload(req).status, "escalated")
        engine.decide(req.pk, self.finance, "approve", "", now=at(11, 12))
        req = self.reload(req)
        self.assertEqual((req.status, req.current_step), ("pending", 2))

    def test_overdue_flag_and_inbox_via_api(self):
        self.rule([(HR, "any", 1)])
        req = self.submit(now=at(1))  # long overdue by the real clock
        self.assertEqual(self.inbox("mp"), [req.reference_number])  # escalated: the MP can act
        self.assertEqual(self.inbox("hr"), [req.reference_number])
        row = self.c["hr"].get(f"{A}/requests/", {"scope": "inbox"}).json()["results"][0]
        self.assertTrue(row["overdue"])
        self.assertEqual(row["status"], "escalated")
        self.assertEqual(self.c["mp"].get(f"{A}/summary/").json()["overdue_for_me"], 1)
