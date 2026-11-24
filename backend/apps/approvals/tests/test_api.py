import uuid

from django.core.exceptions import ValidationError as DjangoValidationError

from apps.accounts import access
from apps.approvals import engine
from apps.approvals.models import ApprovalEvent, ApprovalRequest, ApprovalRule
from apps.company.models import CompanyProfileAuditEvent

from .base import A, ApprovalBase, at

FIN, HR, MP, PARTNER = access.FINANCE_MANAGER, access.HR_MANAGER, access.MANAGING_PARTNER, access.PARTNER


class InboxAndPermissionTests(ApprovalBase):
    def setUp(self):
        super().setUp()
        self.rule([FIN], name="Finance", priority=10, amount_from="1000")
        self.rule([HR], name="HR", priority=20, amount_to="999.99")
        self.rule([MP], name="Partner", priority=30, kind="budget")
        self.r_fin = self.submit(source="a", amount="5000")
        self.r_hr = self.submit(source="b", amount="10")
        self.r_mp = self.submit(source="c", kind="budget")

    def test_each_role_sees_only_its_own_inbox(self):
        self.assertEqual(self.inbox("finance"), [self.r_fin.reference_number])
        self.assertEqual(self.inbox("hr"), [self.r_hr.reference_number])
        self.assertEqual(self.inbox("mp"), [self.r_mp.reference_number])
        for who in ("staff", "pm", "p1", "emp2", "admin"):
            self.assertEqual(self.inbox(who), [], who)

    def test_all_approvals_tab_needs_approvals_view(self):
        for who in ("staff", "emp2"):
            self.assertEqual(self.c[who].get(f"{A}/requests/", {"scope": "all"}).status_code, 403, who)
        for who in ("admin", "finance", "hr", "mp", "pm", "p1"):
            r = self.c[who].get(f"{A}/requests/", {"scope": "all"})
            self.assertEqual(r.status_code, 200, who)
            self.assertEqual(r.json()["count"], 3)
        self.assertEqual(self.c["admin"].get(f"{A}/requests/", {"scope": "bogus"}).status_code, 400)

    def test_my_requests_lists_only_the_requesters_own(self):
        mine = self.c["staff"].get(f"{A}/requests/", {"scope": "mine"}).json()
        self.assertEqual(mine["count"], 3)
        self.assertEqual(self.c["emp2"].get(f"{A}/requests/", {"scope": "mine"}).json()["count"], 0)

    def test_detail_is_for_the_requester_approvers_and_viewers_only(self):
        url = f"{A}/requests/{self.r_fin.pk}/"
        for who, code in (("staff", 200), ("finance", 200), ("admin", 200), ("mp", 200), ("emp2", 404)):
            self.assertEqual(self.c[who].get(url).status_code, code, who)
        self.assertEqual(self.c["staff"].get(url).status_code, 200)
        self.assertIn(self.anon.get(url).status_code, (401, 403))

    def test_an_employee_who_is_a_named_approver_can_open_and_decide(self):
        from apps.approvals import rules
        rules.save_rule(self.company, {"kind": "expense", "name": "By name", "priority": 1, "steps": [{"approver_type": "user", "user": str(self.emp2.pk)}]})
        req = self.submit(source="e1", kind="expense")
        self.assertEqual(self.inbox("emp2"), [req.reference_number])
        self.assertEqual(self.c["emp2"].get(f"{A}/requests/{req.pk}/").status_code, 200)
        self.act("emp2", req)
        self.assertEqual(self.c["emp2"].get(f"{A}/requests/", {"scope": "all"}).status_code, 403)

    def test_filters(self):
        self.assertEqual(len(self.c["admin"].get(f"{A}/requests/", {"scope": "all", "kind": "budget"}).json()["results"]), 1)
        self.assertEqual(self.c["admin"].get(f"{A}/requests/", {"scope": "all", "min_amount": "1000"}).json()["count"], 1)
        self.assertEqual(self.c["admin"].get(f"{A}/requests/", {"scope": "all", "max_amount": "100"}).json()["count"], 1)
        self.act("finance", self.r_fin)
        self.assertEqual(self.c["admin"].get(f"{A}/requests/", {"scope": "all", "status": "approved"}).json()["count"], 1)
        self.assertEqual(self.c["admin"].get(f"{A}/requests/", {"scope": "all", "status": "open"}).json()["count"], 2)
        self.assertEqual(self.c["admin"].get(f"{A}/requests/", {"scope": "all", "q": self.r_hr.reference_number}).json()["count"], 1)
        self.assertEqual(self.c["admin"].get(f"{A}/requests/", {"scope": "all", "older_than_days": "1"}).json()["count"], 0)
        self.assertEqual(self.c["admin"].get(f"{A}/requests/", {"scope": "all", "min_amount": "x"}).status_code, 400)

    def test_summary_counts_for_every_signed_in_user(self):
        self.assertEqual(self.c["finance"].get(f"{A}/summary/").json(), {"pending_for_me": 1, "overdue_for_me": 0, "my_open_requests": 0})
        self.assertEqual(self.c["staff"].get(f"{A}/summary/").json()["my_open_requests"], 3)
        self.assertEqual(self.c["staff"].get(f"{A}/summary/").json()["pending_for_me"], 0)
        self.act("finance", self.r_fin)
        self.assertEqual(self.c["finance"].get(f"{A}/summary/").json()["pending_for_me"], 0)
        self.assertEqual(self.c["staff"].get(f"{A}/summary/").json()["my_open_requests"], 2)
        self.assertEqual(self.anon.get(f"{A}/summary/").status_code in (401, 403), True)

    def test_bulk_approve(self):
        extra = self.submit(source="d", amount="2000")
        r = self.c["finance"].post(f"{A}/requests/bulk-approve/", {"ids": [str(self.r_fin.pk), str(extra.pk), str(self.r_hr.pk), str(uuid.uuid4())], "comment": "ok"})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["approved"], 2)
        self.assertEqual(len(body["failed"]), 2)
        self.assertEqual(self.reload(self.r_fin).status, "approved")
        self.assertEqual(self.reload(self.r_hr).status, "pending")
        self.assertEqual(self.c["finance"].post(f"{A}/requests/bulk-approve/", {"ids": []}).status_code, 400)
        self.assertEqual(self.calls.count(("approved", "a")), 1)

    def test_unknown_ids_are_404(self):
        ghost = uuid.uuid4()
        for verb in ("approve", "reject", "withdraw", "resubmit", "reassign"):
            self.assertEqual(self.c["admin"].post(f"{A}/requests/{ghost}/{verb}/", {"comment": "x", "user": str(self.pm.pk)}).status_code, 404, verb)
        self.assertEqual(self.c["admin"].get(f"{A}/requests/{ghost}/").status_code, 404)
        self.assertEqual(self.c["admin"].get(f"{A}/rules/{ghost}/").status_code, 404)
        self.assertEqual(self.c["admin"].patch(f"{A}/rules/{ghost}/", {"name": "x"}).status_code, 404)
        self.assertEqual(self.c["admin"].delete(f"{A}/rules/{ghost}/").status_code, 404)


class RuleApiTests(ApprovalBase):
    def body(self, **over):
        return {"kind": "test_doc", "name": "My rule", "amount_from": "100", "steps": [{"approver_type": "role", "role": FIN, "mode": "any", "escalate_after_days": 2}], **over}

    def test_only_managers_change_rules_and_viewers_read_them(self):
        self.assertEqual(self.c["mp"].post(f"{A}/rules/", self.body()).status_code, 201)
        self.assertEqual(self.c["p1"].post(f"{A}/rules/", self.body()).status_code, 201)  # a partner holds every permission
        for who in ("finance", "hr", "staff", "pm"):
            self.assertEqual(self.c[who].post(f"{A}/rules/", self.body()).status_code, 403, who)
        self.assertEqual(self.c["finance"].get(f"{A}/rules/").status_code, 200)
        self.assertEqual(self.c["staff"].get(f"{A}/rules/").status_code, 403)
        self.assertEqual(self.c["staff"].get(f"{A}/users/").status_code, 403)
        self.assertEqual(self.c["staff"].get(f"{A}/kinds/").status_code, 200)

    def test_create_edit_delete_with_audit(self):
        r = self.c["admin"].post(f"{A}/rules/", self.body())
        self.assertEqual(r.status_code, 201, r.content)
        rid = r.json()["id"]
        self.assertEqual(r.json()["steps"][0]["who"], "Finance Manager")
        r = self.c["admin"].patch(f"{A}/rules/{rid}/", {"name": "Renamed", "steps": [
            {"approver_type": "role", "role": HR, "mode": "all"}, {"approver_type": "user", "user": str(self.pm.pk)}]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([s["who"] for s in r.json()["steps"]], ["HR Manager", "pm"])
        self.assertEqual(self.c["admin"].delete(f"{A}/rules/{rid}/").status_code, 204)
        events = CompanyProfileAuditEvent.objects.filter(entity_type="approval_rule", entity_id=rid).order_by("occurred_at")
        self.assertEqual([e.action for e in events], ["created", "updated", "deleted"])

    def test_validation(self):
        bad = [
            ({"steps": []}, "steps"), ({"steps": [{"role": "employee"}]}, "steps"), ({"steps": [{"approver_type": "user", "user": str(uuid.uuid4())}]}, "steps"),
            ({"steps": [{"role": FIN, "mode": "some"}]}, "steps"), ({"steps": [{"role": FIN, "escalate_after_days": 0}]}, "steps"),
            ({"kind": "nonsense"}, "kind"), ({"name": " "}, "name"), ({"amount_from": "5", "amount_to": "2"}, "amount_to"),
            ({"amount_from": "abc"}, "amount_from"), ({"amount_from": "-1"}, "amount_from"),
        ]
        for over, field in bad:
            r = self.c["admin"].post(f"{A}/rules/", self.body(**over))
            self.assertEqual(r.status_code, 400, over)
            self.assertIn(field, r.json(), over)

    def test_reorder_changes_which_rule_wins(self):
        a = self.c["admin"].post(f"{A}/rules/", self.body(name="A", amount_from=None)).json()
        b = self.c["admin"].post(f"{A}/rules/", self.body(name="B", amount_from=None, steps=[{"role": HR}])).json()
        self.assertEqual(self.submit(source="x").rule_name, "A")
        self.c["admin"].post(f"{A}/requests/{ApprovalRequest.objects.get(source_id='x').pk}/withdraw/")
        r = self.c["admin"].post(f"{A}/rules/reorder/", {"ids": [b["id"], a["id"]]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.submit(source="y").rule_name, "B")
        self.assertEqual(self.c["admin"].post(f"{A}/rules/reorder/", {"ids": [str(uuid.uuid4())]}).status_code, 400)
        self.assertEqual(self.c["finance"].post(f"{A}/rules/reorder/", {"ids": [a["id"]]}).status_code, 403)

    def test_changing_a_rule_does_not_touch_a_request_in_flight(self):
        rule = self.rule([FIN])
        req = self.submit()
        self.c["admin"].patch(f"{A}/rules/{rule.pk}/", {"steps": [{"role": HR}]})
        self.assertEqual(req.steps.get().role, FIN)
        self.assertEqual(self.inbox("finance"), [req.reference_number])

    def test_users_and_kinds_lookups(self):
        users = self.c["admin"].get(f"{A}/users/").json()
        self.assertIn("pm", [u["name"] for u in users])
        kinds = self.c["staff"].get(f"{A}/kinds/").json()
        self.assertIn("purchase_order", [k["value"] for k in kinds["kinds"]])
        self.assertEqual(len(kinds["roles"]), 6)


class SubmitEndpointAndAuditTests(ApprovalBase):
    def test_http_submit_only_for_kinds_with_a_describe_function(self):
        self.records["d9"] = {"title": "Doc nine", "amount": "75.00"}
        self.rule([FIN])
        r = self.c["staff"].post(f"{A}/submit/", {"kind": "test_doc", "source_id": "d9"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["title"], r.json()["amount"], r.json()["url"]), ("Doc nine", "75.00", "/docs/d9"))
        self.assertEqual(self.c["staff"].post(f"{A}/submit/", {"kind": "test_doc", "source_id": "d9"}).status_code, 400)  # already open
        self.assertEqual(self.c["staff"].post(f"{A}/submit/", {"kind": "payroll_run", "source_id": "p"}).status_code, 400)  # no describe
        self.assertEqual(self.c["staff"].post(f"{A}/submit/", {"kind": "test_doc", "source_id": "missing"}).status_code, 404)
        self.records["d10"] = {"title": "Ten"}
        self.assertEqual(self.c["emp2"].post(f"{A}/submit/", {"kind": "test_doc", "source_id": "d10"}).status_code, 403)
        self.assertEqual(self.c["staff"].post(f"{A}/submit/", {}).status_code, 400)
        self.assertIn(self.anon.post(f"{A}/submit/", {}).status_code, (401, 403))

    def test_status_endpoint(self):
        self.rule([FIN])
        req = self.submit()
        r = self.c["staff"].get(f"{A}/status/", {"kind": "test_doc", "source_id": "d1"}).json()
        self.assertEqual((r["status"], r["reference_number"], r["can_view"]), ("pending", req.reference_number, True))
        r = self.c["emp2"].get(f"{A}/status/", {"kind": "test_doc", "source_id": "d1"}).json()
        self.assertEqual((r["status"], r["can_view"]), ("pending", False))
        self.assertEqual(self.c["emp2"].get(f"{A}/status/", {"kind": "test_doc", "source_id": "nope"}).json()["status"], "none")

    def test_every_step_is_audited(self):
        self.rule([FIN])
        req = self.submit()
        self.act("finance", req, "approve", "Looks fine")
        events = CompanyProfileAuditEvent.objects.filter(entity_type="approval_request", entity_id=req.pk).order_by("occurred_at")
        self.assertEqual([e.action for e in events], ["created", "updated"])
        last = events.last()
        self.assertEqual((last.previous_values["status"], last.new_values["status"], last.reason, last.actor_label),
                         ("pending", "approved", "Looks fine", "finance"))

    def test_history_cannot_be_changed_or_deleted(self):
        self.rule([FIN])
        req = self.submit()
        event = req.events.first()
        event.comment = "tampered"
        with self.assertRaises(DjangoValidationError):
            event.save()
        with self.assertRaises(DjangoValidationError):
            event.delete()
        self.act("finance", req)
        with self.assertRaises(DjangoValidationError):
            req.decisions.first().delete()

    def test_detail_shows_steps_decisions_and_timeline(self):
        self.rule([FIN, (MP, "any", 3)])
        req = self.submit(amount="1234.50", summary="Why", url="/x/1")
        self.act("finance", req, comment="Fine")
        d = self.c["staff"].get(f"{A}/requests/{req.pk}/").json()
        self.assertEqual((d["amount"], d["summary"], d["url"], d["current_step"], d["total_steps"], d["waiting_on_role"]),
                         ("1234.50", "Why", "/x/1", 2, 2, "Managing Partner"))
        self.assertEqual([s["state"] for s in d["steps"]], ["approved", "active"])
        self.assertEqual(d["steps"][0]["decisions"][0]["comment"], "Fine")
        self.assertIsNotNone(d["steps"][1]["due_at"])
        self.assertEqual(d["waiting_on"], ["mp"])
        self.assertFalse(d["can_act"])
        self.assertTrue(d["can_withdraw"])
        self.assertTrue(self.c["mp"].get(f"{A}/requests/{req.pk}/").json()["can_act"])
