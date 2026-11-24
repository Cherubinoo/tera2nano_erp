"""Quotations above an approval rule wait before they are issued; voiding a payment received above a rule waits too.

Receipts themselves are never held: the money has already arrived, and holding the record would leave the books and the
bank statement disagreeing."""
import datetime
from decimal import Decimal
from unittest import mock

from apps.accounting.models import JournalEntry
from apps.accounting.services import reports
from apps.accounts.models import Role
from apps.approvals import api as approvals
from apps.approvals import rules
from apps.company.models import CompanyProfile, CompanyProfileAuditEvent, IssuedDocument
from apps.company.tests.base import API, client_for, make_user
from apps.company.tests.test_quotations import ITEMS as Q_ITEMS, QuotationBase, quote
from apps.sales import approval_hooks
from apps.sales.models import Payment, Quotation

from .test_sales import ITEMS as INV_ITEMS, S, SalesBase

D = Decimal
AP = "/api/v1/approvals"
GHOST = "00000000-0000-0000-0000-000000000000"
ALL_ROLES = [Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER, Role.HR_MANAGER, Role.PROJECT_MANAGER, Role.EMPLOYEE]
# 1 x 3,000.00 = 3,000.00 (no GST): above the 2,300.51 limit set below; the default quote of the helpers is 2,300.50
BIG_Q = [{"description": "Platform build", "quantity": "1", "rate": "3000"}]


def tb_balanced(test, company):
    tb = reports.trial_balance(company, datetime.date(2027, 3, 31))
    test.assertTrue(tb["balanced"], tb)
    test.assertEqual(tb["total_debit"], tb["total_credit"])


class QuoteApprovalBase(QuotationBase):
    def setUp(self):
        super().setUp()
        self.company = CompanyProfile.objects.first()
        self.mp = make_user("mp", Role.MANAGING_PARTNER)
        self.api_mp = client_for(self.mp)
        # quotations of more than 2,300.50 go to the Managing Partner
        rules.save_rule(self.company, {"kind": "quotation", "name": "Big quotes", "priority": 1, "amount_from": "2300.51",
                                       "steps": [{"approver_type": "role", "role": "managing_partner"}]})

    def held(self, client=None, items=None, expect=202, **q):
        r = (client or self.api_staff).post(f"{API}/issued-documents/", dict(
            document_type="quotation", issue_date="2026-08-15", content={"quotation": quote(items=items or BIG_Q, **q)}), format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()["quotation"] if expect == 202 else r.json()

    def request_of(self, q):
        return approvals.latest_request("quotation", q["id"])

    def decide(self, q, verb="approve", comment="", client=None, expect=200):
        r = (client or self.api_mp).post(f"{AP}/requests/{self.request_of(q).pk}/{verb}/", {"comment": comment})
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def get(self, q, client=None):
        return (client or self.api_staff).get(f"{S}/quotes/{q['id']}/").json()


class QuotationWaitsTests(QuoteApprovalBase):
    def test_a_quotation_at_the_limit_is_issued_at_once_even_when_a_rule_exists(self):
        r = self.issue_q()                                            # 2,300.50 exactly
        self.assertEqual((r.status_code, r.json()["reference_number"]), (201, "T2N/QUO/26/1508/00001"))
        self.assertFalse(Quotation.objects.exists())

    def test_one_paisa_over_the_limit_waits_and_issues_nothing(self):
        d = self.held(items=[{"description": "Lump sum", "quantity": "1", "rate": "2300.51"}])
        self.assertEqual((d["status"], d["total"], d["customer_name"], d["issued"]), ("pending_approval", "2300.51", "Acme Industries", None))
        self.assertFalse(IssuedDocument.objects.filter(document_type="quotation").exists())

    def test_a_large_quotation_waits_without_a_number_or_pdf(self):
        d = self.held()
        self.assertEqual((d["status"], d["total"], d["currency"], d["created_by"]), ("pending_approval", "3000.00", "INR", "staff"))
        self.assertFalse(IssuedDocument.objects.filter(document_type="quotation").exists())
        req = self.request_of(d)
        self.assertEqual((req.status, str(req.amount), req.kind, req.url, req.title),
                         ("pending", "3000.00", "quotation", f"/company/quotations/{d['id']}", "Quotation for Acme Industries"))
        self.assertEqual(approvals.status_of("quotation", d["id"]), "pending")
        # no number was used up: the next quotation to be issued is still number 1
        nxt = self.api_staff.get(f"{API}/document-numbering/next/", {"document_type": "quotation", "issue_date": "2026-08-15"}).json()
        self.assertEqual(nxt["reference_number"], "T2N/QUO/26/1508/00001")

    def test_the_rule_looks_at_the_total_with_gst(self):
        self.enable_gst()
        # 2,000.00 + 18% = 2,360.00 which is above 2,300.50 although the taxable amount is not
        d = self.held(items=[{"description": "Work", "quantity": "1", "rate": "2000", "gst_rate": "18"}])
        self.assertEqual(d["total"], "2360.00")

    def test_other_documents_are_not_held(self):
        r = self.api_staff.post(f"{API}/issued-documents/", dict(document_type="business_letter", issue_date="2026-08-15",
                                                                  content={"paragraphs": ["Hello."]}))
        self.assertEqual(r.status_code, 201)

    def test_the_list_shows_waiting_quotations_with_counts(self):
        self.held()
        r = self.api_staff.get(f"{S}/quotes/", {"status": "pending_approval"}).json()
        self.assertEqual((r["count"], r["status_counts"], r["results"][0]["customer_name"]), (1, {"pending_approval": 1}, "Acme Industries"))
        self.assertEqual(self.api_staff.get(f"{S}/quotes/", {"q": "nobody"}).json()["count"], 0)

    def test_a_waiting_quotation_cannot_be_edited_issued_again_or_deleted(self):
        d = self.held()
        self.assertEqual(self.api_staff.patch(f"{S}/quotes/{d['id']}/", {"content": quote()}, format="json").status_code, 400)
        self.assertEqual(self.api_staff.post(f"{S}/quotes/{d['id']}/issue/").status_code, 400)
        r = self.api_staff.delete(f"{S}/quotes/{d['id']}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Take it back", str(r.json()))


class QuotationDecisionTests(QuoteApprovalBase):
    def test_approving_issues_it_exactly_as_a_direct_issue(self):
        d = self.held()
        self.decide(d, "approve", "Fine for this client")
        done = self.get(d)
        self.assertEqual((done["status"], done["decision_note"]), ("issued", "Fine for this client"))
        self.assertEqual(done["issued"]["reference_number"], "T2N/QUO/26/1508/00001")
        doc = IssuedDocument.objects.get(pk=done["issued"]["id"])
        self.assertEqual((doc.document_type, doc.issue_date, doc.generated_by.username, doc.source_module, doc.source_id),
                         ("quotation", datetime.date(2026, 8, 15), "staff", "sales", d["id"]))
        self.assertEqual(doc.snapshot["quotation"]["totals"]["grand_total"], "3000.00")
        for variant in ("", "?variant=print"):
            resp = self.api_staff.get(f"{API}/issued-documents/{doc.pk}/download/{variant}")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(b"".join(resp.streaming_content).startswith(b"%PDF"))
        self.assertEqual(approvals.status_of("quotation", d["id"]), "approved")
        reasons = [e.reason for e in CompanyProfileAuditEvent.objects.filter(entity_type="quotation", entity_id=d["id"]).order_by("occurred_at")]
        self.assertEqual(reasons, ["Sent for approval", "Approved: Fine for this client"])
        # the next quotation continues the numbering
        self.assertEqual(self.issue_q().json()["reference_number"], "T2N/QUO/26/1508/00002")

    def test_the_decision_runs_once(self):
        d = self.held()
        self.decide(d)
        self.decide(d, "approve", expect=409)
        self.decide(d, "reject", comment="late", expect=409)
        self.assertEqual(IssuedDocument.objects.filter(document_type="quotation").count(), 1)

    def test_rejecting_returns_it_to_a_draft_with_the_note_and_it_can_be_corrected(self):
        d = self.held()
        self.decide(d, "reject", expect=400)                          # a reason is required
        self.decide(d, "reject", "Discount too deep")
        draft = self.get(d)
        self.assertEqual((draft["status"], draft["decision_note"], draft["issued"]), ("draft", "Rejected: Discount too deep", None))
        self.assertFalse(IssuedDocument.objects.filter(document_type="quotation").exists())
        self.assertEqual(approvals.status_of("quotation", d["id"]), "rejected")
        # lower the price to 2,000.00 (under the limit) and send it: it is issued at once
        r = self.api_staff.patch(f"{S}/quotes/{d['id']}/", {"content": quote(items=[{"description": "Platform build", "quantity": "1", "rate": "2000"}])}, format="json")
        self.assertEqual((r.status_code, r.json()["total"]), (200, "2000.00"))
        r = self.api_staff.post(f"{S}/quotes/{d['id']}/issue/")
        self.assertEqual((r.status_code, r.json()["status"], r.json()["issued"]["reference_number"]), (200, "issued", "T2N/QUO/26/1508/00001"))

    def test_sending_a_rejected_quotation_unchanged_waits_again_with_a_fresh_request(self):
        d = self.held()
        self.decide(d, "reject", "Check the scope")
        old = self.request_of(d)
        r = self.api_staff.post(f"{S}/quotes/{d['id']}/issue/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "pending_approval"))
        self.assertNotEqual(self.request_of(d).pk, old.pk)

    def test_a_rejected_request_sent_again_from_approvals_puts_it_back_to_waiting(self):
        d = self.held()
        self.decide(d, "reject", "Check the scope")
        r = self.api_mp.post(f"{AP}/requests/{self.request_of(d).pk}/resubmit/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.get(d)["status"], "pending_approval")
        self.decide(d)
        self.assertEqual(self.get(d)["status"], "issued")

    def test_withdrawing_in_approvals_or_on_the_quotation_returns_it_to_a_draft(self):
        d = self.held()
        r = self.api_staff.post(f"{AP}/requests/{self.request_of(d).pk}/withdraw/", {"comment": "wrong client"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.get(d)["status"], "draft")
        d2 = self.held()
        r = self.api_staff.post(f"{S}/quotes/{d2['id']}/withdraw/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "draft"))
        self.assertEqual(approvals.status_of("quotation", d2["id"]), "cancelled")
        self.assertEqual(self.api_staff.post(f"{S}/quotes/{d2['id']}/withdraw/").status_code, 400)
        self.assertEqual(self.api_staff.delete(f"{S}/quotes/{d2['id']}/").status_code, 204)
        self.assertFalse(IssuedDocument.objects.filter(document_type="quotation").exists())

    def test_a_decision_that_cannot_issue_is_rolled_back_with_the_reason(self):
        d = self.held()
        with mock.patch("apps.company.services.documents.issue_document", side_effect=RuntimeError("the letterhead file is missing")):
            r = self.decide(d, "approve", expect=400)
        self.assertIn("letterhead file is missing", str(r.json()))
        self.assertEqual(self.get(d)["status"], "pending_approval")
        self.assertEqual(self.request_of(d).status, "pending")
        self.assertFalse(IssuedDocument.objects.filter(document_type="quotation").exists())
        self.decide(d)                                                # nothing was consumed: the same request now succeeds
        self.assertEqual(self.get(d)["issued"]["reference_number"], "T2N/QUO/26/1508/00001")

    def test_a_quotation_started_from_a_crm_deal_keeps_its_link_through_approval(self):
        deal = "5a0c1f0e-0000-4000-8000-000000000001"
        r = self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type="quotation", issue_date="2026-08-15", source_module="crm", source_id=deal,
            content={"quotation": quote(items=BIG_Q)}), format="json")
        self.assertEqual(r.status_code, 202, r.content)
        d = r.json()["quotation"]
        self.decide(d)
        doc = IssuedDocument.objects.get(pk=self.get(d)["issued"]["id"])
        self.assertEqual((doc.source_module, doc.source_id), ("crm", deal))
        # a direct issue (no rule) keeps it too
        with mock.patch("apps.sales.quotes._approvals", return_value=None):
            direct = self.api_staff.post(f"{API}/issued-documents/", dict(
                document_type="quotation", issue_date="2026-08-15", source_module="crm", source_id=deal,
                content={"quotation": quote()}), format="json")
        self.assertEqual((direct.status_code, direct.json()["source_module"], direct.json()["source_id"]), (201, "crm", deal))

    def test_the_requester_cannot_approve_their_own_quotation(self):
        d = self.held(client=self.api_finance)
        self.decide(d, "approve", client=self.api_finance, expect=403)
        self.assertEqual(self.get(d, self.api_finance)["status"], "pending_approval")

    def test_an_issued_quotation_cannot_be_edited_deleted_or_sent_again(self):
        d = self.held()
        self.decide(d)
        self.assertEqual(self.api_staff.patch(f"{S}/quotes/{d['id']}/", {"content": quote()}, format="json").status_code, 400)
        self.assertEqual(self.api_staff.delete(f"{S}/quotes/{d['id']}/").status_code, 400)
        self.assertEqual(self.api_staff.post(f"{S}/quotes/{d['id']}/issue/").status_code, 400)

    def test_an_invoice_can_still_be_started_from_the_approved_quotation(self):
        d = self.held()
        self.decide(d)
        issued_id = self.get(d)["issued"]["id"]
        r = self.api_finance.get(f"{S}/quotations/{issued_id}/prefill/")
        self.assertEqual(r.status_code, 200, r.content)


class StandaloneAndPermissionTests(QuoteApprovalBase):
    def test_without_the_approvals_module_quotations_are_issued_directly(self):
        with mock.patch("apps.sales.quotes._approvals", return_value=None):
            r = self.api_staff.post(f"{API}/issued-documents/", dict(document_type="quotation", issue_date="2026-08-15",
                                                                      content={"quotation": quote(items=BIG_Q)}), format="json")
        self.assertEqual(r.status_code, 201)

    def test_with_no_active_rule_a_large_quotation_is_issued_directly(self):
        from apps.approvals.models import ApprovalRule
        ApprovalRule.objects.filter(kind="quotation").update(is_active=False)
        self.assertEqual(self.issue_q(items=BIG_Q).status_code, 201)

    def test_a_person_with_sales_manage_can_decide_directly_when_approvals_are_not_installed(self):
        d = self.held()
        with mock.patch("apps.sales.quotes._approvals", return_value=None):
            self.assertEqual(self.api_finance.post(f"{S}/quotes/{d['id']}/reject/", {}).status_code, 400)     # reason needed
            r = self.api_finance.post(f"{S}/quotes/{d['id']}/reject/", {"change_reason": "Duplicate"})
            self.assertEqual((r.status_code, r.json()["status"], r.json()["decision_note"]), (200, "draft", "Rejected: Duplicate"))
            r = self.api_staff.post(f"{S}/quotes/{d['id']}/issue/")      # approvals missing, so it is simply issued
            self.assertEqual((r.status_code, r.json()["status"]), (200, "issued"))

    def test_direct_approve_issues_when_approvals_are_not_installed(self):
        d = self.held()
        with mock.patch("apps.sales.quotes._approvals", return_value=None):
            r = self.api_finance.post(f"{S}/quotes/{d['id']}/approve/", {"note": "OK"})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["decision_note"]), (200, "issued", "OK"))
        self.assertEqual(r.json()["issued"]["reference_number"], "T2N/QUO/26/1508/00001")

    def test_with_approvals_installed_the_quotation_endpoints_do_not_decide(self):
        d = self.held()
        for verb in ("approve", "reject"):
            r = self.api_finance.post(f"{S}/quotes/{d['id']}/{verb}/", {"change_reason": "x"})
            self.assertEqual(r.status_code, 400, verb)
            self.assertIn("Approvals module", str(r.json()))
        self.assertTrue(self.get(d)["approval_managed"])
        self.assertEqual(self.get(d)["status"], "pending_approval")

    def test_every_role_against_the_quotation_actions(self):
        d = self.held()                                                # prepared by staff
        decide = {Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER}      # sales.manage
        for role in ALL_ROLES:
            c = client_for(make_user(f"u_{role}", role))
            self.assertEqual(c.get(f"{S}/quotes/{d['id']}/").status_code, 200, role)    # documents.issue: every role
            for verb in ("approve", "reject"):
                code = c.post(f"{S}/quotes/{d['id']}/{verb}/", {"change_reason": "x"}).status_code
                self.assertEqual(code, 400 if role in decide else 403, (role, verb))
            if role not in decide:
                # not the preparer and without sales.manage
                self.assertEqual(c.post(f"{S}/quotes/{d['id']}/withdraw/").status_code, 403, role)
                self.assertEqual(c.patch(f"{S}/quotes/{d['id']}/", {"content": quote()}, format="json").status_code, 403, role)
                self.assertEqual(c.delete(f"{S}/quotes/{d['id']}/").status_code, 403, role)
        self.assertEqual(self.get(d)["status"], "pending_approval")
        self.assertIn(self.anon.get(f"{S}/quotes/{d['id']}/").status_code, (401, 403))
        # the preparer (an employee) may take it back; someone who manages sales may too
        self.assertEqual(self.api_staff.post(f"{S}/quotes/{d['id']}/withdraw/").status_code, 200)

    def test_unknown_quotation_is_404(self):
        for verb in ("issue", "withdraw", "approve", "reject"):
            self.assertEqual(self.api_finance.post(f"{S}/quotes/{GHOST}/{verb}/", {}).status_code, 404, verb)
        self.assertEqual(self.api_finance.get(f"{S}/quotes/{GHOST}/").status_code, 404)
        self.assertEqual(self.api_finance.get(f"{S}/quotes/nonsense/").status_code, 404)


class QuotationHookTests(QuoteApprovalBase):
    def test_describe_lets_the_approval_button_submit_a_waiting_quotation_with_no_open_request(self):
        d = self.held()
        info = approval_hooks.describe_quotation(d["id"])
        self.assertEqual((info["amount"], info["url"], info["title"]), (D("3000.00"), f"/company/quotations/{d['id']}",
                                                                          "Quotation for Acme Industries"))
        self.assertTrue(info["requested_by_ok"](self.staff))
        self.assertIsNone(approval_hooks.describe_quotation(GHOST))
        self.assertIsNone(approval_hooks.describe_quotation("nonsense"))
        approvals.cancel_for_source("quotation", d["id"], self.staff)
        r = self.api_staff.post(f"{AP}/submit/", {"kind": "quotation", "source_id": d["id"]}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (201, "pending"))
        self.decide(d)
        self.assertEqual(self.get(d)["status"], "issued")

    def test_the_kind_is_registered_and_submittable(self):
        kinds = {k["value"]: k for k in self.api_mp.get(f"{AP}/kinds/").json()["kinds"]}
        self.assertEqual((kinds["quotation"]["label"], kinds["quotation"]["can_submit_from_ui"]), ("Quotation", True))
        self.assertEqual((kinds["payment"]["label"], kinds["payment"]["can_submit_from_ui"]), ("Payment", True))

    def test_hooks_ignore_a_quotation_that_is_no_longer_waiting_and_unknown_ones(self):
        d = self.held()
        self.decide(d)
        req = mock.MagicMock(source_id=d["id"], requested_by=self.staff)
        req.decisions.select_related.return_value.order_by.return_value.first.return_value = None
        approval_hooks.quotation_approved(req)
        approval_hooks.quotation_rejected(req)
        self.assertEqual(Quotation.objects.get(pk=d["id"]).status, "issued")
        for fn in (approval_hooks.quotation_approved, approval_hooks.quotation_rejected, approval_hooks.quotation_cancelled,
                   approval_hooks.quotation_resubmitted, approval_hooks.payment_approved, approval_hooks.payment_rejected,
                   approval_hooks.payment_cancelled, approval_hooks.payment_resubmitted):
            fn(mock.MagicMock(source_id=GHOST, requested_by=self.staff))
            fn(mock.MagicMock(source_id="nonsense", requested_by=self.staff))

    def test_the_request_shows_in_the_inbox_with_a_link_back_to_the_quotation(self):
        d = self.held()
        rows = self.api_mp.get(f"{AP}/requests/", {"scope": "inbox"}).json()["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["kind"], rows[0]["kind_label"], rows[0]["url"], rows[0]["amount"]),
                         ("quotation", "Quotation", f"/company/quotations/{d['id']}", "3000.00"))


# --------------------------------------------------------------------------- payments received

class PaymentVoidApprovalBase(SalesBase):
    def setUp(self):
        super().setUp()
        self.company = CompanyProfile.objects.first()
        self.mp = make_user("mp", Role.MANAGING_PARTNER)
        self.api_mp = client_for(self.mp)
        # voiding a payment of more than 50,000.00 goes to the Managing Partner
        rules.save_rule(self.company, {"kind": "payment", "name": "Big payments", "priority": 1, "amount_from": "50000.01",
                                       "steps": [{"approver_type": "role", "role": "managing_partner"}]})
        # 100 x 1,000 = 1,00,000.00, no GST
        self.big_invoice = self.invoice(items=[{"description": "Platform", "quantity": "100", "rate": "1000"}])

    def bal(self, key):
        from apps.accounting.models import Account
        code = Account.objects.get(company=self.company, system_key=key).code
        rows = {r["code"]: r for r in reports.trial_balance(self.company, datetime.date(2027, 3, 31))["rows"]}
        r = rows.get(code)
        return D("0") if r is None else D(r["debit"]) - D(r["credit"])

    def request_of(self, p):
        return approvals.latest_request("payment", p["id"])

    def decide(self, p, verb="approve", comment="", client=None, expect=200):
        r = (client or self.api_mp).post(f"{AP}/requests/{self.request_of(p).pk}/{verb}/", {"comment": comment})
        self.assertEqual(r.status_code, expect, r.content)
        return r

    def get(self, p):
        return self.fin.get(f"{S}/payments/{p['id']}/").json()

    def void(self, p, reason="Cheque bounced", client=None, expect=200):
        r = (client or self.fin).post(f"{S}/payments/{p['id']}/void/", {"change_reason": reason})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def paid_60k(self):
        return self.pay(amount="60000", allocations=[{"document": self.big_invoice["id"], "amount": "60000"}])


class ReceiptsAreNeverHeldTests(PaymentVoidApprovalBase):
    def test_a_large_receipt_is_recorded_and_posted_at_once(self):
        p = self.paid_60k()
        self.assertEqual((p["status"], p["void_requested"], p["status_label"]), ("recorded", False, "Recorded"))
        self.assertRegex(p["reference_code"], r"^T2N/PAY/26/\d{4}/00001$")
        self.assertEqual((self.bal("bank"), self.bal("receivable")), (D("60000"), D("40000")))
        self.assertIsNone(self.request_of(p))
        tb_balanced(self, self.company)


class VoidWaitsForApprovalTests(PaymentVoidApprovalBase):
    def test_voiding_a_payment_at_the_limit_is_immediate(self):
        p = self.pay(amount="50000", allocations=[{"document": self.big_invoice["id"], "amount": "50000"}])
        self.assertEqual(self.void(p)["status"], "void")
        self.assertEqual((self.bal("bank"), self.bal("receivable")), (D("0"), D("100000")))

    def test_voiding_a_large_payment_waits_and_the_books_do_not_move(self):
        p = self.paid_60k()
        r = self.void(p, "Entered against the wrong invoice")
        self.assertEqual((r["status"], r["void_requested"], r["status_label"], r["void_request_reason"]),
                         ("recorded", True, "Void pending approval", "Entered against the wrong invoice"))
        self.assertEqual((self.bal("bank"), self.bal("receivable")), (D("60000"), D("40000")))       # still counted
        self.assertEqual(Payment.objects.get(pk=p["id"]).status, "recorded")
        req = self.request_of(p)
        self.assertEqual((req.status, str(req.amount), req.kind, req.url), ("pending", "60000.00", "payment", f"/sales/payments/{p['id']}"))
        self.assertIn("Void payment", req.title)
        self.void(p, expect=400)                                       # already waiting
        tb_balanced(self, self.company)

    def test_approving_voids_the_payment_and_reverses_the_entry(self):
        p = self.paid_60k()
        self.void(p, "Wrong invoice")
        self.decide(p, "approve", "Agreed")
        done = self.get(p)
        self.assertEqual((done["status"], done["void_reason"], done["void_requested"], done["decision_note"]), ("void", "Wrong invoice", False, "Agreed"))
        self.assertEqual((self.bal("bank"), self.bal("receivable")), (D("0"), D("100000")))
        self.assertEqual(JournalEntry.objects.filter(source="reversal").count(), 1)
        tb_balanced(self, self.company)
        self.assertEqual(approvals.status_of("payment", p["id"]), "approved")

    def test_the_decision_runs_once(self):
        p = self.paid_60k()
        self.void(p)
        self.decide(p)
        self.decide(p, "approve", expect=409)
        self.assertEqual(JournalEntry.objects.filter(source="reversal").count(), 1)

    def test_rejecting_keeps_the_payment_recorded_with_the_note(self):
        p = self.paid_60k()
        self.void(p, "Mistake")
        self.decide(p, "reject", expect=400)
        self.decide(p, "reject", "It is correct")
        d = self.get(p)
        self.assertEqual((d["status"], d["void_requested"], d["decision_note"]), ("recorded", False, "Void rejected: It is correct"))
        self.assertEqual((self.bal("bank"), self.bal("receivable")), (D("60000"), D("40000")))
        self.void(p, "Mistake again")                                  # a fresh request can be made
        self.assertEqual(self.get(p)["void_requested"], True)

    def test_a_rejected_void_request_sent_again_from_approvals_waits_once_more(self):
        p = self.paid_60k()
        self.void(p, "Mistake")
        self.decide(p, "reject", "Not yet")
        r = self.api_mp.post(f"{AP}/requests/{self.request_of(p).pk}/resubmit/")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(self.get(p)["void_requested"])
        self.decide(p)
        self.assertEqual(self.get(p)["status"], "void")

    def test_withdrawing_the_void_request_keeps_the_payment(self):
        p = self.paid_60k()
        self.void(p)
        r = self.fin.post(f"{S}/payments/{p['id']}/withdraw/")
        self.assertEqual((r.status_code, r.json()["void_requested"], r.json()["status"]), (200, False, "recorded"))
        self.assertEqual(approvals.status_of("payment", p["id"]), "cancelled")
        self.assertEqual(self.fin.post(f"{S}/payments/{p['id']}/withdraw/").status_code, 400)
        self.assertEqual(self.bal("bank"), D("60000"))

    def test_a_decision_that_cannot_complete_is_rolled_back(self):
        p = self.paid_60k()
        self.void(p)
        with mock.patch("apps.sales.services.posting.reverse_payment", side_effect=RuntimeError("the financial year is locked")):
            r = self.decide(p, "approve", expect=400)
        self.assertIn("financial year is locked", str(r.json()))
        self.assertEqual((self.get(p)["status"], self.get(p)["void_requested"]), ("recorded", True))
        self.assertEqual(self.request_of(p).status, "pending")
        self.assertEqual(self.bal("bank"), D("60000"))
        self.decide(p)
        self.assertEqual(self.get(p)["status"], "void")

    def test_the_requester_cannot_approve_their_own_void(self):
        p = self.paid_60k()
        self.void(p)
        self.decide(p, "approve", client=self.fin, expect=403)
        self.assertTrue(self.get(p)["void_requested"])

    def test_payments_list_shows_the_pending_void_label(self):
        p = self.paid_60k()
        self.void(p)
        row = self.fin.get(f"{S}/payments/").json()["results"][0]
        self.assertEqual((row["void_requested"], row["status_label"]), (True, "Void pending approval"))
        self.assertEqual(self.fin.get(f"{S}/payments/", {"status": "recorded"}).json()["total_amount"], "60000.00")


class PaymentStandaloneAndPermissionTests(PaymentVoidApprovalBase):
    def test_without_the_approvals_module_a_large_payment_is_voided_directly(self):
        p = self.paid_60k()
        with mock.patch("apps.sales.services._approvals", return_value=None):
            self.assertEqual(self.void(p)["status"], "void")

    def test_a_person_with_sales_manage_can_decide_directly_when_approvals_are_not_installed(self):
        p = self.paid_60k()
        self.void(p, "Mistake")
        with mock.patch("apps.sales.services._approvals", return_value=None):
            self.assertEqual(self.fin.post(f"{S}/payments/{p['id']}/reject/", {}).status_code, 400)
            r = self.fin.post(f"{S}/payments/{p['id']}/reject/", {"change_reason": "Keep it"})
            self.assertEqual((r.status_code, r.json()["void_requested"], r.json()["decision_note"]), (200, False, "Void rejected: Keep it"))
            self.assertEqual(self.fin.post(f"{S}/payments/{p['id']}/approve/").status_code, 400)       # nothing waiting

    def test_direct_approve_voids_when_approvals_are_not_installed(self):
        p = self.paid_60k()
        self.void(p, "Mistake")
        with mock.patch("apps.sales.services._approvals", return_value=None):
            r = self.fin.post(f"{S}/payments/{p['id']}/approve/", {"note": "OK"})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "void"))
        self.assertEqual(self.bal("bank"), D("0"))

    def test_with_approvals_installed_the_payment_endpoints_do_not_decide(self):
        p = self.paid_60k()
        self.void(p)
        for verb in ("approve", "reject"):
            r = self.fin.post(f"{S}/payments/{p['id']}/{verb}/", {"change_reason": "x"})
            self.assertEqual(r.status_code, 400, verb)
            self.assertIn("Approvals module", str(r.json()))
        self.assertTrue(self.get(p)["approval_managed"])

    def test_every_role_against_the_void_actions(self):
        p = self.paid_60k()
        self.void(p)
        manage = {Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER}
        for role in ALL_ROLES:
            c = client_for(make_user(f"u_{role}", role))
            for verb in ("void", "approve", "reject", "withdraw"):
                code = c.post(f"{S}/payments/{p['id']}/{verb}/", {"change_reason": "x"}).status_code
                if role in manage:
                    self.assertNotEqual(code, 403, (role, verb))
                else:
                    self.assertEqual(code, 403, (role, verb))
            if role in manage and not self.get(p)["void_requested"]:
                self.void(p)                                           # waiting again for the next role
        self.assertIn(self.anon.post(f"{S}/payments/{p['id']}/withdraw/").status_code, (401, 403))

    def test_unknown_payment_is_404(self):
        for verb in ("void", "withdraw", "approve", "reject"):
            self.assertEqual(self.fin.post(f"{S}/payments/{GHOST}/{verb}/", {}).status_code, 404, verb)


class PaymentHookTests(PaymentVoidApprovalBase):
    def test_describe_only_answers_for_a_payment_whose_void_is_waiting(self):
        p = self.paid_60k()
        self.assertIsNone(approval_hooks.describe_payment(p["id"]))
        self.void(p)
        info = approval_hooks.describe_payment(p["id"])
        self.assertEqual((info["amount"], info["url"]), (D("60000.00"), f"/sales/payments/{p['id']}"))
        self.assertTrue(info["requested_by_ok"](self.finance))
        self.assertFalse(info["requested_by_ok"](self.staff))
        self.assertIsNone(approval_hooks.describe_payment(GHOST))
        approvals.cancel_for_source("payment", p["id"], self.finance)
        r = self.fin.post(f"{AP}/submit/", {"kind": "payment", "source_id": p["id"]}, format="json")
        self.assertEqual((r.status_code, r.json()["status"]), (201, "pending"))
        self.decide(p)
        self.assertEqual(self.get(p)["status"], "void")
