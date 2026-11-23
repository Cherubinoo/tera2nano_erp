"""Two modules may own different records of one kind (customer receipts and vendor payments are both "payment")."""
from decimal import Decimal

from django.test import SimpleTestCase

from apps.accounts import access
from apps.approvals import registry, rules

from .base import ApprovalBase


class ChainedHandlerTests(SimpleTestCase):
    def setUp(self):
        saved = dict(registry._HANDLERS)
        self.addCleanup(lambda: (registry._HANDLERS.clear(), registry._HANDLERS.update(saved)))
        self.calls = []

    def hook(self, tag):
        def fn(req):
            self.calls.append((tag, req))
        return fn

    def test_a_chained_handler_runs_every_callback_and_asks_each_module_to_describe(self):
        registry.register_handler("shared", on_approved=self.hook("a"), on_rejected=self.hook("a"), label="Shared",
                                  required_codes=("x.manage",), describe=lambda i: {"title": "from a"} if i == "1" else None)
        registry.register_handler("shared", on_approved=self.hook("b"), on_cancelled=self.hook("b"), label="Other label",
                                  required_codes=("y.manage",), describe=lambda i: {"title": "from b"} if i == "2" else None, chain=True)
        h = registry.handler_for("shared")
        h.on_approved("req")
        h.on_rejected("req")
        h.on_cancelled("req")
        self.assertEqual(self.calls, [("a", "req"), ("b", "req"), ("a", "req"), ("b", "req")])
        self.assertIsNone(h.on_resubmitted)
        self.assertEqual((h.describe("1")["title"], h.describe("2")["title"], h.describe("3")), ("from a", "from b", None))
        self.assertEqual((registry.kind_label("shared"), registry.manage_codes("shared")), ("Shared", ("x.manage",)))

    def test_without_chain_the_later_registration_replaces_the_earlier_one(self):
        registry.register_handler("shared", on_approved=self.hook("a"))
        registry.register_handler("shared", on_approved=self.hook("b"))
        registry.handler_for("shared").on_approved("req")
        self.assertEqual(self.calls, [("b", "req")])

    def test_chain_on_a_kind_nobody_registered_yet_is_a_plain_registration(self):
        registry.register_handler("fresh", on_approved=self.hook("a"), chain=True)
        registry.handler_for("fresh").on_approved("req")
        self.assertEqual(self.calls, [("a", "req")])


class ExampleRuleBandsTests(ApprovalBase):
    """The seeded expense rules: small expenses post at once, mid-sized ones go to the Finance Manager, large ones to the
    Finance Manager and then the Managing Partner."""

    def test_expense_bands(self):
        rules.seed_default_rules(self.company)
        match = lambda amount: rules.match_rule(self.company, "expense", Decimal(amount))
        self.assertIsNone(match("10000.00"))
        mid = match("10000.01")
        self.assertEqual((mid.name, [s.role for s in mid.steps.all()]), ("Expenses of 10,000 to 50,000", [access.FINANCE_MANAGER]))
        self.assertEqual(match("50000.00").pk, mid.pk)
        big = match("50000.01")
        self.assertEqual([s.role for s in big.steps.all()], [access.FINANCE_MANAGER, access.MANAGING_PARTNER])

    def test_the_payment_and_quotation_examples_are_unchanged_in_their_limits(self):
        rules.seed_default_rules(self.company)
        self.assertIsNone(rules.match_rule(self.company, "payment", Decimal("50000.00")))
        self.assertIsNotNone(rules.match_rule(self.company, "payment", Decimal("50000.01")))
        self.assertIsNone(rules.match_rule(self.company, "quotation", Decimal("500000.00")))
        self.assertIsNotNone(rules.match_rule(self.company, "quotation", Decimal("500000.01")))
