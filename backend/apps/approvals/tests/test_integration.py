"""What the other modules rely on: requires_approval, the example rules for newly connected kinds, and a fast summary."""
import io
from decimal import Decimal

from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.accounts import access
from apps.approvals import api as approvals
from apps.approvals import registry, rules
from apps.approvals.models import ApprovalRule, ApprovalSettings

from .base import A, ApprovalBase, at

MP = access.MANAGING_PARTNER


class RequiresApprovalTests(ApprovalBase):
    def test_matches_the_amount_band_without_creating_any_rule(self):
        self.rule([MP], kind="vendor_bill", amount_from="100000.01")
        co = self.company
        self.assertFalse(approvals.requires_approval(co, "vendor_bill", Decimal("100000.00")))
        self.assertTrue(approvals.requires_approval(co, "vendor_bill", "100000.01"))
        self.assertTrue(approvals.requires_approval(co, "vendor_bill", 250000))
        self.assertFalse(approvals.requires_approval(co, "budget", 250000))          # another kind
        self.assertEqual(ApprovalRule.objects.count(), 1)                            # nothing was seeded as a side effect
        self.assertFalse(ApprovalSettings.objects.filter(defaults_seeded=True).exists())

    def test_an_inactive_rule_does_not_apply_and_no_amount_means_zero(self):
        r = self.rule([MP], kind="budget")
        self.assertTrue(approvals.requires_approval(self.company, "budget"))
        r.is_active = False
        r.save()
        self.assertFalse(approvals.requires_approval(self.company, "budget", 5))

    def test_project_text_rules_use_the_meta(self):
        self.rule([MP], kind="budget", project_text="nano")
        self.assertTrue(approvals.requires_approval(self.company, "budget", 1, {"project": "Nano lab"}))
        self.assertFalse(approvals.requires_approval(self.company, "budget", 1, {"project": "Website"}))


class SeedMissingRulesTests(ApprovalBase):
    def setUp(self):
        super().setUp()
        for kind in ("vendor_bill", "budget", "salary_revision"):
            registry.register_handler(kind, on_approved=lambda r: None, label=kind)
            self.addCleanup(registry.unregister_handler, kind)

    def kinds(self):
        return sorted(ApprovalRule.objects.values_list("kind", flat=True))

    def test_a_fresh_company_gets_the_full_example_set(self):
        self.assertEqual(rules.seed_missing_rules(self.company), 11)
        self.assertEqual(rules.seed_missing_rules(self.company), 0)

    def test_a_company_seeded_earlier_gets_only_the_kinds_that_have_no_rule(self):
        rules.seed_default_rules(self.company)
        ApprovalRule.objects.filter(kind__in=("vendor_bill", "salary_revision")).delete()
        ApprovalRule.objects.filter(kind="budget").update(name="Our own budget rule", is_active=False)
        self.assertEqual(rules.seed_missing_rules(self.company), 2)
        self.assertEqual(ApprovalRule.objects.filter(kind="vendor_bill").count(), 1)
        self.assertEqual(ApprovalRule.objects.get(kind="salary_revision").steps.count(), 2)          # HR, then the Managing Partner
        self.assertEqual(ApprovalRule.objects.get(kind="budget").name, "Our own budget rule")      # untouched
        self.assertEqual(ApprovalRule.objects.get(kind="vendor_bill").amount_from, Decimal("100000.01"))

    def test_the_management_command_reports_what_it_did(self):
        out = io.StringIO()
        call_command("seed_approval_rules", stdout=out)
        self.assertIn("11 example rule(s) created", out.getvalue())
        ApprovalRule.objects.filter(kind="vendor_bill").delete()
        out = io.StringIO()
        call_command("seed_approval_rules", stdout=out)
        self.assertIn("1 example rule(s) created", out.getvalue())
        out = io.StringIO()
        call_command("seed_approval_rules", stdout=out)
        self.assertIn("nothing to add", out.getvalue())


class SummaryIsFastTests(ApprovalBase):
    def queries(self, who):
        with CaptureQueriesContext(connection) as ctx:
            r = self.c[who].get(f"{A}/summary/")
        self.assertEqual(r.status_code, 200)
        return len(ctx), r.json()

    def test_the_number_of_queries_does_not_grow_with_the_number_of_open_requests(self):
        self.rule([MP, "partner"], name="Two steps")
        for i in range(3):
            self.submit(source=f"a{i}", amount="10")
        few, body = self.queries("mp")
        self.assertEqual(body["pending_for_me"], 3)
        for i in range(27):
            self.submit(source=f"b{i}", amount="10")
        many, body = self.queries("mp")
        self.assertEqual(body["pending_for_me"], 30)
        self.assertEqual(few, many)
        self.assertLessEqual(many, 14)

    def test_it_takes_no_row_locks_when_nothing_is_overdue(self):
        self.rule([(MP, "any", 3)])
        self.submit(source="x", amount="10", now=at(1))
        with CaptureQueriesContext(connection) as ctx:
            from apps.approvals import engine
            engine.sweep(self.company, at(2))
        self.assertFalse(any("FOR UPDATE" in q["sql"] for q in ctx.captured_queries))

    def test_an_overdue_step_is_still_escalated_and_counted(self):
        self.rule([(MP, "any", 1)])
        req = self.submit(source="x", amount="10", now=at(1))
        from apps.approvals import engine
        self.assertEqual(engine.sweep(self.company, at(3)), 1)
        self.assertEqual(engine.sweep(self.company, at(3)), 0)                      # already escalated
        self.assertEqual(self.reload(req).status, "escalated")
        self.assertEqual(self.c["mp"].get(f"{A}/summary/").json()["pending_for_me"], 1)

    def test_summary_is_open_to_every_signed_in_user_and_refuses_anonymous(self):
        for who in self.c:
            self.assertEqual(self.c[who].get(f"{A}/summary/").status_code, 200, who)
        self.assertIn(self.anon.get(f"{A}/summary/").status_code, (401, 403))
