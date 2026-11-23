"""A raise that falls under an approval rule waits for approval; approving creates the salary structure."""
import datetime
from decimal import Decimal
from unittest import mock

from apps.approvals import api as approvals
from apps.approvals import rules
from apps.company.models import CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user
from apps.accounts.models import Role
from apps.hr import approval_hooks
from apps.hr.models import SalaryRevision, SalaryStructure
from apps.hr.services import payroll

from .base import H, HRBase

D = Decimal
A = "/api/v1/approvals"
GHOST = "00000000-0000-0000-0000-000000000000"


class RevisionBase(HRBase):
    def setUp(self):
        super().setUp()
        rules.save_rule(self.company, {"kind": "salary_revision", "name": "Raises", "priority": 1, "steps": [{"approver_type": "role", "role": "managing_partner"}]})
        self.asha = self.emp("Asha", "Rao")
        self.structure(self.asha)                                   # 20,000 + 8,000 + 12,000 = 40,000 a month from 2025-04-01

    def raise_to(self, emp=None, basic="25000", hra="10000", special="15000", effective="2026-09-01", expect=202, client=None, **over):
        emp = emp or self.asha
        r = (client or self.fin).post(f"{H}/employees/{emp['id']}/salary/", {
            "effective_from": effective, "basic": basic, "hra": hra, "special_allowance": special, **over})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def rev(self):
        return SalaryRevision.objects.order_by("-created_at").first()

    def request_of(self):
        return approvals.latest_request("salary_revision", self.rev().pk)

    def decide(self, verb="approve", comment="", client=None, expect=200):
        r = (client or self.api_mp).post(f"{A}/requests/{self.request_of().pk}/{verb}/", {"comment": comment})
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def structures(self):
        return SalaryStructure.objects.filter(employee_id=self.asha["id"]).count()


class RaiseWaitsTests(RevisionBase):
    def test_a_raise_waits_and_payroll_sees_no_change(self):
        out = self.raise_to()                                     # 50,000 gross: (50,000 - 40,000) x 12 = 1,20,000 a year
        self.assertEqual((out["pending_approval"], out["status"], out["previous_gross"], out["new_gross"], out["annual_increase"]),
                         (True, "pending_approval", "40000.00", "50000.00", "120000.00"))
        self.assertEqual(self.structures(), 1)
        self.assertEqual(payroll.structure_for(SalaryRevision.objects.get().employee, datetime.date(2026, 9, 30)).gross, D("40000"))
        req = self.request_of()
        self.assertEqual((req.kind, req.status, str(req.amount), req.url), ("salary_revision", "pending", "120000.00", f"/hr/employees/{self.asha['id']}"))
        self.assertIn("Salary revision: Asha Rao", req.title)
        self.assertEqual(approvals.status_of("salary_revision", self.rev().pk), "pending")

    def test_the_first_structure_a_cut_and_an_unchanged_gross_apply_at_once(self):
        ravi = self.emp("Ravi", "Kumar")
        self.assertEqual(self.structure(ravi)["gross"], "40000.00")                    # nothing to compare with
        cut = self.raise_to(basic="15000", hra="6000", special="9000", effective="2026-09-01", expect=201)   # 30,000: lower
        self.assertEqual(cut["gross"], "30000.00")
        same = self.raise_to(basic="15000", hra="6000", special="9000", effective="2026-10-01", expect=201)  # 30,000: same
        self.assertEqual(same["gross"], "30000.00")
        self.assertFalse(SalaryRevision.objects.exists())

    def test_the_comparison_is_with_the_structure_in_force_on_the_effective_date(self):
        self.raise_to(basic="15000", hra="6000", special="9000", effective="2026-09-01", expect=201)    # a cut to 30,000 from 1 Sep
        out = self.raise_to(basic="20000", hra="8000", special="12000", effective="2026-10-01")          # back to 40,000: +10,000 x 12
        self.assertEqual((out["previous_gross"], out["annual_increase"]), ("30000.00", "120000.00"))
        # a date before the cut compares with 40,000: no increase, so it applies at once
        self.assertEqual(self.raise_to(basic="20000", hra="8000", special="12000", effective="2026-06-01", expect=201)["gross"], "40000.00")

    def test_an_amount_band_decides_which_raises_wait(self):
        from apps.approvals.models import ApprovalRule
        ApprovalRule.objects.filter(kind="salary_revision").update(amount_from="120000.01")
        self.raise_to(expect=201)                                                                      # exactly 1,20,000.00: applied
        ravi = self.emp("Ravi", "Kumar")
        self.structure(ravi)                                                                           # 40,000 as well
        out = self.raise_to(emp=ravi, basic="25000", hra="10000", special="15000.01")                  # 50,000.01 -> 1,20,000.12
        self.assertEqual(out["annual_increase"], "120000.12")

    def test_with_no_active_rule_or_without_approvals_a_raise_applies_at_once(self):
        from apps.approvals.models import ApprovalRule
        ApprovalRule.objects.filter(kind="salary_revision").update(is_active=False)
        self.raise_to(expect=201)
        ApprovalRule.objects.filter(kind="salary_revision").update(is_active=True)
        with mock.patch("apps.hr.services.salary._api", return_value=None):
            self.raise_to(basic="30000", effective="2026-12-01", expect=201)
        self.assertFalse(SalaryRevision.objects.exists())

    def test_a_second_revision_is_refused_while_one_waits_and_validation_still_applies(self):
        self.raise_to()
        r = self.raise_to(basic="30000", effective="2026-10-01", expect=400)
        self.assertIn("already waiting", r["effective_from"][0] if isinstance(r["effective_from"], list) else r["effective_from"])
        self.raise_to(effective="2024-01-01", expect=400)                                            # before joining
        self.raise_to(effective="2025-04-01", expect=400)                                            # a structure starts that day
        self.raise_to(basic="0", hra="0", special="0", effective="2026-12-01", expect=400)
        self.assertEqual(SalaryRevision.objects.count(), 1)

    def test_the_employee_page_lists_waiting_revisions_for_those_who_see_pay(self):
        self.raise_to()
        d = self.fin.get(f"{H}/employees/{self.asha['id']}/salary/").json()
        self.assertEqual((len(d["rows"]), len(d["revisions"]), d["revisions"][0]["status"], d["approval_managed"]), (1, 1, "pending_approval", True))
        self.assertEqual(d["revisions"][0]["basic"], "25000.00")
        self.assertEqual(self.api_pm.get(f"{H}/employees/{self.asha['id']}/salary/").status_code, 403)


class RevisionDecisionTests(RevisionBase):
    def test_approving_creates_the_structure_from_the_effective_date(self):
        self.raise_to(notes="Annual review", pf_on_full_basic=True)
        self.decide("approve", "Well earned")
        s = SalaryStructure.objects.get(employee_id=self.asha["id"], effective_from=datetime.date(2026, 9, 1))
        self.assertEqual((s.basic, s.hra, s.special_allowance, s.gross, s.notes, s.pf_on_full_basic), (D("25000"), D("10000"), D("15000"), D("50000"), "Annual review", True))
        rev = self.rev()
        self.assertEqual((rev.status, rev.decision_note, rev.decided_by.username, rev.result_structure_id), ("approved", "Well earned", "mp", s.pk))
        emp = rev.employee
        self.assertEqual((payroll.structure_for(emp, datetime.date(2026, 8, 31)).gross, payroll.structure_for(emp, datetime.date(2026, 9, 1)).gross),
                         (D("40000"), D("50000")))
        events = CompanyProfileAuditEvent.objects.filter(entity_type__in=("salary_revision", "salary_structure")).order_by("occurred_at")
        # the decision events are written in the same instant, so their timestamps can tie: compare without relying on order
        self.assertEqual(sorted((e.entity_type, e.reason) for e in events if str(e.entity_id) in (str(rev.pk), str(s.pk))),
                         sorted([("salary_revision", "Sent for approval"), ("salary_structure", "Salary revision approved"), ("salary_revision", "Well earned")]))

    def test_rejecting_creates_nothing_and_a_new_revision_can_follow(self):
        self.raise_to()
        self.decide("reject", expect=400)
        self.decide("reject", "Not this cycle")
        self.assertEqual((self.structures(), self.rev().status, self.rev().decision_note), (1, "rejected", "Not this cycle"))
        self.assertEqual(approvals.status_of("salary_revision", self.rev().pk), "rejected")
        self.raise_to(basic="22000", hra="9000", special="13000", effective="2026-10-01")             # 44,000: +4,000 x 12 = 48,000
        self.assertEqual((SalaryRevision.objects.count(), str(self.request_of().amount)), (2, "48000.00"))

    def test_a_rejected_request_sent_again_waits_once_more_and_can_then_be_approved(self):
        self.raise_to()
        self.decide("reject", "Later")
        r = self.api_mp.post(f"{A}/requests/{self.request_of().pk}/resubmit/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.rev().status, "pending_approval")
        self.decide("approve")
        self.assertEqual(self.structures(), 2)

    def test_withdrawing_in_approvals_or_on_the_employee_leaves_the_pay_unchanged(self):
        self.raise_to()
        r = self.fin.post(f"{A}/requests/{self.request_of().pk}/withdraw/", {"comment": "wrong date"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((self.rev().status, self.structures()), ("withdrawn", 1))
        self.raise_to(effective="2026-10-01")
        rid = self.rev().pk
        r = self.fin.post(f"{H}/employees/{self.asha['id']}/salary-revisions/{rid}/withdraw/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "withdrawn"))
        self.assertEqual(approvals.status_of("salary_revision", rid), "cancelled")
        self.assertEqual(self.fin.post(f"{H}/employees/{self.asha['id']}/salary-revisions/{rid}/withdraw/").status_code, 400)

    def test_a_decision_that_cannot_be_applied_is_rolled_back_with_the_reason(self):
        self.raise_to()
        # a cut starting the same day needs no approval, so it is saved at once and takes the date
        self.raise_to(basic="15000", hra="6000", special="9000", effective="2026-09-01", expect=201)
        r = self.decide("approve", expect=400)
        self.assertIn("already starts on this date", str(r.json()))
        self.assertEqual((self.rev().status, self.request_of().status, self.structures()), ("pending_approval", "pending", 2))

    def test_the_requester_cannot_approve_their_own_raise(self):
        self.raise_to()
        self.decide("approve", client=self.fin, expect=403)
        self.assertEqual(self.structures(), 1)

    def test_the_inbox_links_back_to_the_employee(self):
        self.raise_to()
        row = self.api_mp.get(f"{A}/requests/", {"scope": "inbox"}).json()["results"][0]
        self.assertEqual((row["kind_label"], row["url"], row["amount"]), ("Salary revision", f"/hr/employees/{self.asha['id']}", "120000.00"))


class StandaloneAndPermissionTests(RevisionBase):
    def test_a_person_with_hr_payroll_decides_directly_when_approvals_are_not_installed(self):
        self.raise_to()
        rid = self.rev().pk
        url = f"{H}/employees/{self.asha['id']}/salary-revisions/{rid}"
        with mock.patch("apps.hr.services.salary._api", return_value=None):
            r = self.fin.post(f"{url}/reject/", {})
            self.assertEqual(r.status_code, 400)
            self.assertIn("change_reason", r.json())
            r = self.fin.post(f"{url}/approve/", {"note": "ok"})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "approved"))
        self.assertEqual(self.structures(), 2)
        self.assertEqual(self.fin.post(f"{url}/approve/").status_code, 400)

    def test_with_approvals_installed_the_employee_endpoints_do_not_decide(self):
        self.raise_to()
        for verb in ("approve", "reject"):
            r = self.fin.post(f"{H}/employees/{self.asha['id']}/salary-revisions/{self.rev().pk}/{verb}/", {"change_reason": "x"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("Approvals module", str(r.json()))

    def test_every_role_against_the_actions_and_404s(self):
        self.raise_to()
        rid = self.rev().pk
        allowed = {Role.ADMIN, Role.MANAGING_PARTNER, Role.FINANCE_MANAGER, Role.HR_MANAGER}
        for role in (Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER, Role.HR_MANAGER, Role.PROJECT_MANAGER, Role.EMPLOYEE):
            c = client_for(make_user(f"u_{role}", role))
            for verb in ("approve", "reject"):
                code = c.post(f"{H}/employees/{self.asha['id']}/salary-revisions/{rid}/{verb}/", {"change_reason": "x"}).status_code
                self.assertEqual(code, 400 if role in allowed else 403, (role, verb))
            code = c.post(f"{H}/employees/{self.asha['id']}/salary/", {"effective_from": "2027-01-01", "basic": "90000"}).status_code
            self.assertEqual(code, 400 if role in allowed else 403, (role, "new salary"))   # 400: one revision already waits
        self.assertEqual(self.anon.post(f"{H}/employees/{self.asha['id']}/salary-revisions/{rid}/withdraw/").status_code in (401, 403), True)
        for verb in ("approve", "reject", "withdraw"):
            self.assertEqual(self.fin.post(f"{H}/employees/{self.asha['id']}/salary-revisions/{GHOST}/{verb}/", {}).status_code, 404, verb)
            self.assertEqual(self.fin.post(f"{H}/employees/{GHOST}/salary-revisions/{rid}/{verb}/", {}).status_code, 404, verb)

    def test_an_employee_sees_their_own_waiting_raise_but_not_a_colleagues(self):
        user, client, mine = self.employee_user("meera")
        self.structure(mine)
        self.raise_to(emp=mine)
        d = client.get(f"{H}/employees/{mine['id']}/salary/").json()
        self.assertEqual(len(d["revisions"]), 1)
        self.assertEqual(client.get(f"{H}/employees/{self.asha['id']}/salary/").status_code, 403)
        self.assertEqual(client.post(f"{H}/employees/{mine['id']}/salary-revisions/{self.rev().pk}/withdraw/").status_code, 403)

    def test_describe_hooks_and_the_button_path(self):
        self.raise_to()
        rev = self.rev()
        info = approval_hooks.describe_revision(str(rev.pk))
        self.assertEqual((info["amount"], info["url"]), (D("120000.00"), f"/hr/employees/{self.asha['id']}"))
        self.assertTrue(info["requested_by_ok"](self.finance))
        self.assertFalse(info["requested_by_ok"](self.pm))
        self.assertIsNone(approval_hooks.describe_revision(GHOST))
        self.assertIsNone(approval_hooks.describe_revision("nonsense"))
        approvals.cancel_for_source("salary_revision", rev.pk, self.finance)
        r = self.fin.post(f"{A}/submit/", {"kind": "salary_revision", "source_id": str(rev.pk)}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (201, "pending"))
        self.decide("approve")
        self.assertEqual(self.structures(), 2)
        req = mock.MagicMock(source_id=str(rev.pk), requested_by=self.finance)
        req.decisions.select_related.return_value.order_by.return_value.first.return_value = None
        approval_hooks.revision_approved(req)
        approval_hooks.revision_rejected(req)                        # already approved: ignored
        gone = mock.MagicMock(source_id=GHOST, requested_by=self.finance)
        for fn in (approval_hooks.revision_approved, approval_hooks.revision_rejected, approval_hooks.revision_cancelled,
                   approval_hooks.revision_resubmitted):
            fn(gone)
        kinds = {k["value"]: k for k in self.api.get(f"{A}/kinds/").json()["kinds"]}
        self.assertEqual((kinds["salary_revision"]["label"], kinds["salary_revision"]["can_submit_from_ui"]), ("Salary revision", True))
