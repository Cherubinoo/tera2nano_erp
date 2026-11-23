from apps.accounts import access
from apps.approvals import analytics, engine

from .base import A, ApprovalBase, at

FIN, MP = access.FINANCE_MANAGER, access.MANAGING_PARTNER


class DashboardTests(ApprovalBase):
    def setUp(self):
        super().setUp()
        self.register("test_auto")
        self.rule([FIN])
        # July: one approved after 4 hours. August: approved after 8 hours, rejected after 2 hours, one still waiting, one automatic.
        r1 = self.submit(source="r1", now=at(20, 9, month=7))
        engine.decide(r1.pk, self.finance, "approve", "", now=at(20, 13, month=7))
        r2 = self.submit(source="r2", now=at(5, 9))
        engine.decide(r2.pk, self.finance, "approve", "", now=at(5, 17))
        r3 = self.submit(source="r3", now=at(6, 9))
        engine.decide(r3.pk, self.finance, "reject", "No", now=at(6, 11))
        self.submit(source="r4", now=at(7, 9))
        self.submit(user=self.finance, source="r5", kind="test_auto", now=at(8, 9))

    def test_figures_with_hand_computed_numbers(self):
        d = analytics.dashboard(self.company, now=at(20, 12))
        t = d["tiles"]
        self.assertEqual((t["pending"], t["approved"], t["rejected"], t["total"], t["overdue"], t["escalated"]), (1, 3, 1, 5, 0, 0))
        # (4 + 8 + 2) / 3 = 4.67 hours; the automatic approval had no approver and is left out
        self.assertEqual(t["avg_turnaround_hours"], 4.7)
        self.assertEqual(t["avg_pending_age_hours"], 13 * 24 + 3)  # 7 Aug 09:00 to 20 Aug 12:00
        months = {m["label"]: m for m in d["monthly"]}
        self.assertEqual(len(d["monthly"]), 6)
        self.assertEqual((months["Jul 26"]["submitted"], months["Jul 26"]["approved"], months["Jul 26"]["rejected"]), (1, 1, 0))
        self.assertEqual((months["Aug 26"]["submitted"], months["Aug 26"]["approved"], months["Aug 26"]["rejected"]), (4, 2, 1))
        kinds = {k["kind"]: k for k in d["by_kind"]}
        self.assertEqual((kinds["test_doc"]["total"], kinds["test_doc"]["pending"], kinds["test_doc"]["approved"], kinds["test_doc"]["rejected"]), (4, 1, 2, 1))
        self.assertEqual(kinds["test_auto"]["approved"], 1)
        self.assertEqual(d["slowest_approvers"][0]["approver"], "finance")
        self.assertEqual((d["slowest_approvers"][0]["avg_hours"], d["slowest_approvers"][0]["decisions"]), (4.7, 3))
        self.assertEqual(d["pending_per_approver"], [{"approver": "finance", "pending": 1}])

    def test_overdue_and_escalated_tiles(self):
        self.rule([(FIN, "any", 1)], kind="test_auto", name="Escalating")
        self.submit(source="r6", kind="test_auto", now=at(10, 9))
        d = analytics.dashboard(self.company, now=at(12, 9))
        self.assertEqual((d["tiles"]["escalated"], d["tiles"]["overdue"]), (1, 1))

    def test_endpoint_permissions_and_shape(self):
        for who in ("admin", "finance", "mp", "hr", "pm", "p1"):
            r = self.c[who].get(f"{A}/dashboard/")
            self.assertEqual(r.status_code, 200, who)
            self.assertIn("tiles", r.json())
        for who in ("staff", "emp2"):
            self.assertEqual(self.c[who].get(f"{A}/dashboard/").status_code, 403, who)
        self.assertIn(self.anon.get(f"{A}/dashboard/").status_code, (401, 403))
