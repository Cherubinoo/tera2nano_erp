import datetime
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import Role
from apps.company.models import CompanyProfile, CompanyProfileAuditEvent, CompanyTaxSettings, IssuedDocument, IssuedReferenceNumber
from apps.company.tests.base import API, GSTIN, CompanyTestMixin, client_for, make_gstin, make_user
from apps.sales.models import CreditApplication, Customer, Payment, SalesDocument

S = "/api/v1/sales"
ITEMS = [{"description": "Development", "quantity": "2", "rate": "1000", "discount_percent": "10"}]  # 1,800.00 before GST
TODAY = datetime.date.today()


def iso(d):
    return d.isoformat()


class SalesBase(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.fin = self.api_finance
        self.acme = self.customer("Acme Industries")

    # -- builders
    def customer(self, name, client=None, **over):
        payload = {"display_name": name, **over}
        r = (client or self.fin).post(f"{S}/customers/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def draft(self, customer=None, kind="invoice", items=None, issue_date="2026-08-15", **extra):
        payload = {"kind": kind, "customer": (customer or self.acme)["id"], "issue_date": issue_date,
                   "form": {"items": items or ITEMS}, **extra}
        r = self.fin.post(f"{S}/documents/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def issue(self, doc):
        r = self.fin.post(f"{S}/documents/{doc['id']}/issue/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def invoice(self, customer=None, **kw):
        return self.issue(self.draft(customer, **kw))

    def pay(self, customer=None, amount="500", date="2026-08-20", expect=201, **extra):
        payload = {"customer": (customer or self.acme)["id"], "date": date, "amount": amount, "mode": "bank_transfer", **extra}
        r = self.fin.post(f"{S}/payments/", payload)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def get(self, doc):
        return self.fin.get(f"{S}/documents/{doc['id']}/").json()

    def enable_gst(self, default_code=""):
        CompanyProfile.objects.update(gst_registration_status="registered", gstin=GSTIN)
        CompanyTaxSettings.objects.update(gst_applicable=True, gst_treatment="regular", default_tax_code=default_code,
                                          tax_codes=[{"code": "GST18", "rate": 18}])


class CustomerTests(SalesBase):
    def test_new_customer_gets_an_automatic_reference_with_year_and_date(self):
        self.assertRegex(self.acme["reference_code"], r"^T2N-CUS-\d{2}-\d{4}-0001$")
        self.assertRegex(self.customer("Beta")["reference_code"], r"-0002$")

    def test_gstin_state_and_pan_are_checked_and_state_is_derived(self):
        g = make_gstin("27", "AAAPA1234A")
        c = self.customer("Maharashtra Co", gst_treatment="registered", gstin=g.lower(), pan="aaapa1234a")
        self.assertEqual((c["gstin"], c["state_code"], c["state"]), (g, "27", "Maharashtra"))
        for bad, field in (({"gstin": "NOTAGSTIN"}, "gstin"), ({"gst_treatment": "registered"}, "gstin"),
                           ({"pan": "BAD"}, "pan"), ({"state_code": "55"}, "state_code"),
                           ({"gstin": make_gstin("29", "AAAPA1234A"), "state_code": "27"}, "state_code")):
            r = self.fin.post(f"{S}/customers/", {"display_name": "Bad " + field, **bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json(), bad)

    def test_duplicates_are_refused(self):
        self.assertEqual(self.fin.post(f"{S}/customers/", {"display_name": "acme industries"}).status_code, 400)
        g = make_gstin("27", "AAAPA1234A")
        self.customer("One", gst_treatment="registered", gstin=g)
        self.assertEqual(self.fin.post(f"{S}/customers/", {"display_name": "Two", "gst_treatment": "registered", "gstin": g}).status_code, 400)
        self.assertEqual(self.fin.post(f"{S}/customers/", {"display_name": ""}).status_code, 400)

    def test_edit_search_and_switch_off(self):
        r = self.fin.patch(f"{S}/customers/{self.acme['id']}/", {"city": "Pune", "payment_terms_days": 45})
        self.assertEqual((r.json()["city"], r.json()["payment_terms_days"]), ("Pune", 45))
        self.customer("Zed Traders")
        self.assertEqual([c["display_name"] for c in self.fin.get(f"{S}/customers/", {"q": "zed"}).json()], ["Zed Traders"])
        r = self.fin.patch(f"{S}/customers/{self.acme['id']}/", {"is_active": False})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.fin.get(f"{S}/customers/", {"active": "true"}).json()), 1)
        self.assertEqual(self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                                           "form": {"items": ITEMS}}).status_code, 400)

    def test_a_customer_that_owes_money_cannot_be_switched_off(self):
        self.invoice()
        r = self.fin.patch(f"{S}/customers/{self.acme['id']}/", {"is_active": False})
        self.assertEqual(r.status_code, 400)
        self.assertIn("still owes 1800.00", str(r.json()))

    def test_owing_filter_and_balances(self):
        other = self.customer("Quiet Co")
        self.invoice()
        rows = {c["display_name"]: c for c in self.fin.get(f"{S}/customers/").json()}
        self.assertEqual((rows["Acme Industries"]["outstanding"], rows["Quiet Co"]["outstanding"]), ("1800.00", "0.00"))
        self.assertEqual([c["display_name"] for c in self.fin.get(f"{S}/customers/", {"owing": "true"}).json()], ["Acme Industries"])
        self.assertEqual(other["net_balance"], "0.00")

    def test_changes_are_audited_without_bumping_the_company_version(self):
        before = CompanyProfile.objects.get().version
        c = self.customer("Audited Co", email="a@example.com")
        self.fin.patch(f"{S}/customers/{c['id']}/", {"city": "Chennai", "email": "b@example.com"})
        self.assertEqual(CompanyProfile.objects.get().version, before)
        events = CompanyProfileAuditEvent.objects.filter(entity_type="customer", entity_id=c["id"]).order_by("occurred_at")
        self.assertEqual([e.action for e in events], ["created", "updated"])
        self.assertEqual(events[1].new_values["email"], "[redacted]")  # personal contact details are not copied into history


class DraftTests(SalesBase):
    def test_a_draft_works_out_totals_has_no_number_and_uses_no_number(self):
        d = self.draft()
        self.assertEqual((d["status"], d["number"], d["grand_total"], d["balance"]), ("draft", "", "1800.00", None))
        self.assertEqual(d["record"]["lines"][0]["taxable"], "1800.00")
        self.assertEqual(IssuedReferenceNumber.objects.filter(document_type="invoice").count(), 0)
        self.assertIsNone(d["issued"])

    def test_due_date_comes_from_the_customer_terms_else_the_policy(self):
        self.assertEqual(self.draft()["due_date"], "2026-09-14")  # policy: Net 30
        c = self.customer("Slow Payer", payment_terms_days=60)
        self.assertEqual(self.draft(c)["due_date"], "2026-10-14")
        self.assertEqual(self.draft(due_date="2026-08-31")["due_date"], "2026-08-31")
        self.assertEqual(self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                                          "due_date": "2026-08-01", "form": {"items": ITEMS}}).status_code, 400)

    def test_a_draft_can_be_edited_and_deleted_but_an_issued_document_cannot(self):
        d = self.draft()
        r = self.fin.patch(f"{S}/documents/{d['id']}/", {"form": {"items": [{"description": "More", "quantity": "1", "rate": "500"}]}})
        self.assertEqual((r.status_code, r.json()["grand_total"]), (200, "500.00"))
        self.assertEqual(self.fin.delete(f"{S}/documents/{d['id']}/").status_code, 204)
        self.assertFalse(SalesDocument.objects.filter(pk=d["id"]).exists())
        issued = self.invoice()
        self.assertEqual(self.fin.patch(f"{S}/documents/{issued['id']}/", {"form": {"items": ITEMS}}).status_code, 400)
        self.assertEqual(self.fin.delete(f"{S}/documents/{issued['id']}/").status_code, 400)

    def test_bad_items_are_refused_at_save_time(self):
        for items, word in (([], "at least one item"), ([{"description": "x", "quantity": "0", "rate": "1"}], "quantity"),
                            ([{"description": "", "quantity": "1", "rate": "1"}], "description"),
                            ([{"description": "x", "quantity": "1", "rate": "-1"}], "rate")):
            r = self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                                  "form": {"items": items}})
            self.assertEqual(r.status_code, 400, items)
            self.assertIn(word, str(r.json()), items)
        self.assertEqual(SalesDocument.objects.count(), 0)

    def test_working_endpoint_shows_totals_without_saving(self):
        r = self.fin.post(f"{S}/documents/working/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                                      "form": {"items": ITEMS}})
        self.assertEqual((r.status_code, r.json()["record"]["totals"]["grand_total"], r.json()["due_date"]), (200, "1800.00", "2026-09-14"))
        self.assertEqual(SalesDocument.objects.count(), 0)

    def test_options_describe_the_form(self):
        o = self.fin.get(f"{S}/documents/options/").json()
        self.assertEqual((o["gst_applied"], o["payment_terms_days"], o["currency"]), (False, 30, "INR"))
        self.enable_gst("GST18")
        o = self.fin.get(f"{S}/documents/options/").json()
        self.assertEqual((o["gst_applied"], o["default_gst_rate"]), (True, "18"))

    def test_prefill_carries_notes_terms_customer_and_gst_for_the_invoice_form(self):
        self.enable_gst("GST18")
        q = self.api_staff.post(f"{API}/issued-documents/", dict(document_type="quotation", issue_date="2026-08-10", content={"quotation": {
            "customer": {"name": "Far Away Ltd", "address": ["1 Mall Road", "Pune"], "state_code": "27"}, "subject": "Portal",
            "notes": "Delivery in 6 weeks", "terms": "50% advance", "show_signature": True,
            "items": [{"description": "Portal build", "quantity": "2", "rate": "5000", "gst_rate": 18}]}})).json()
        pre = self.fin.get(f"{S}/quotations/{q['id']}/prefill/").json()
        self.assertEqual((pre["matched_customer"], pre["customer_name"]), (None, "Far Away Ltd"))
        self.assertEqual(pre["customer"], {"name": "Far Away Ltd", "gstin": "", "state_code": "27", "address": ["1 Mall Road", "Pune"]})
        self.assertEqual((pre["gst_applied"], pre["supply_type"], pre["place_of_supply"]["code"]), (True, "inter_state", "27"))
        self.assertEqual((pre["form"]["notes"], pre["form"]["terms"], pre["form"]["show_signature"]), ("Delivery in 6 weeks", "50% advance", True))
        item = pre["form"]["items"][0]
        self.assertEqual((item["quantity"], item["rate"], str(item["gst_rate"])), ("2", "5000", "18"))
        far = self.customer("Far Away Ltd", state_code="27")
        inv = self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": far["id"], "issue_date": "2026-08-15", "form": pre["form"],
                                                "source_quotation": q["id"]}, format="json")
        self.assertEqual(inv.status_code, 201, inv.content)
        d = inv.json()
        # 2 x 5,000 = 10,000 taxable; IGST at 18% = 1,800; notes and terms came across
        self.assertEqual((d["taxable_total"], d["tax_total"], d["grand_total"], d["source_quotation"]["number"]),
                         ("10000.00", "1800.00", "11800.00", q["reference_number"]))
        self.assertEqual((d["form"]["notes"], d["form"]["terms"]), ("Delivery in 6 weeks", "50% advance"))

    def test_gst_splits_by_state_on_a_draft(self):
        self.enable_gst("GST18")
        same = self.draft(self.customer("Local", state_code="29"))
        self.assertEqual((same["grand_total"], same["tax_total"]), ("2124.00", "324.00"))
        self.assertEqual([t["label"] for t in same["record"]["totals"]["tax"]], ["CGST @ 9%", "SGST @ 9%"])
        other = self.draft(self.customer("Far Away", state_code="27"))
        self.assertEqual([t["label"] for t in other["record"]["totals"]["tax"]], ["IGST @ 18%"])

    def test_a_quotation_can_be_the_source(self):
        q = self.api_staff.post(f"{API}/issued-documents/", dict(document_type="quotation", issue_date="2026-08-10", content={"quotation": {
            "customer": {"name": "Acme Industries"}, "subject": "Portal", "items": [{"description": "Portal build", "quantity": "1",
                                                                                      "rate": "5000", "hsn_sac": "998314"}]}})).json()
        pre = self.fin.get(f"{S}/quotations/{q['id']}/prefill/").json()
        self.assertEqual((pre["matched_customer"], pre["form"]["items"][0]["description"]), (self.acme["id"], "Portal build"))
        d = self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                              "form": pre["form"], "source_quotation": q["id"]}).json()
        self.assertEqual((d["grand_total"], d["source_quotation"]["number"]), ("5000.00", q["reference_number"]))
        self.assertEqual(self.fin.get(f"{S}/quotations/{IssuedDocument.objects.filter(document_type='business_letter').count() or '00000000-0000-0000-0000-000000000000'}/prefill/").status_code, 404)


class IssueTests(SalesBase):
    def test_issuing_numbers_freezes_and_makes_both_pdfs(self):
        d = self.issue(self.draft(due_date=iso(TODAY + datetime.timedelta(days=30))))
        self.assertEqual((d["status"], d["number"], d["status_key"], d["balance"]), ("issued", "T2N/INV/26/1508/00001", "unpaid", "1800.00"))
        issued = IssuedDocument.objects.get(pk=d["issued"]["id"])
        self.assertEqual((issued.document_type, issued.source_module, issued.reference_number), ("invoice", "sales", d["number"]))
        for variant in ("", "?variant=print"):
            resp = self.fin.get(f"{API}/issued-documents/{issued.id}/download/{variant}")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(b"".join(resp.streaming_content).startswith(b"%PDF"))
        self.assertEqual(self.fin.post(f"{S}/documents/{d['id']}/issue/").status_code, 400)  # only once

    def test_numbers_run_on_in_order_and_failed_issue_uses_none(self):
        a, b = self.invoice(), self.invoice(issue_date="2026-08-16")
        self.assertEqual((a["number"], b["number"]), ("T2N/INV/26/1508/00001", "T2N/INV/26/1608/00002"))
        zero = self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                                 "form": {"items": [{"description": "Free", "quantity": "1", "rate": "0"}]}}).json()
        self.assertEqual(self.fin.post(f"{S}/documents/{zero['id']}/issue/").status_code, 400)
        self.assertEqual(self.invoice(issue_date="2026-08-17")["number"], "T2N/INV/26/1708/00003")

    def test_the_pdf_shows_customer_due_date_and_total(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF not installed")
        d = self.invoice()
        doc = IssuedDocument.objects.get(pk=d["issued"]["id"])
        with doc.file.open("rb") as fh:
            text = "".join(p.get_text() for p in fitz.open(stream=fh.read(), filetype="pdf"))
        for needle in (d["number"], "Acme Industries", "Due date", "14 September 2026", "1,800.00", "Development"):
            self.assertIn(needle, text)

    def test_issuing_needs_the_issue_permission_and_the_generic_endpoint_refuses_billing(self):
        d = self.draft()
        viewer = client_for(make_user("pm", Role.PROJECT_MANAGER))
        self.assertEqual(viewer.post(f"{S}/documents/{d['id']}/issue/").status_code, 403)
        r = self.fin.post(f"{API}/issued-documents/", {"document_type": "invoice", "content": {"billing": {"items": []}}})
        self.assertEqual(r.status_code, 400)

    def test_proforma_is_numbered_separately_and_is_not_receivable(self):
        p = self.invoice(kind="proforma_invoice")
        self.assertEqual((p["number"], p["status_key"], p["balance"]), ("T2N/PI/26/1508/00001", "issued", None))
        self.assertEqual(self.pay(amount="100", allocations=[{"document": p["id"], "amount": "100"}], expect=400)["allocations"].__class__, list)
        clone = self.fin.post(f"{S}/documents/{p['id']}/clone/", {"kind": "invoice"}).json()
        self.assertEqual((clone["kind"], clone["status"], clone["grand_total"]), ("invoice", "draft", "1800.00"))

    def test_void_keeps_the_number_but_frees_it_from_use(self):
        d = self.invoice()
        r = self.fin.post(f"{S}/documents/{d['id']}/void/", {})
        self.assertEqual(r.status_code, 400)
        r = self.fin.post(f"{S}/documents/{d['id']}/void/", {"change_reason": "Wrong customer"})
        self.assertEqual((r.status_code, r.json()["status_key"], r.json()["void_reason"]), (200, "void", "Wrong customer"))
        self.assertEqual(IssuedReferenceNumber.objects.get(reference_number=d["number"]).status, "void")
        self.assertEqual(IssuedDocument.objects.get(pk=d["issued"]["id"]).status, "void")
        self.assertEqual(self.invoice(issue_date="2026-08-16")["number"], "T2N/INV/26/1608/00002")  # never reused
        self.assertEqual(self.fin.get(f"{S}/customers/{self.acme['id']}/").json()["outstanding"], "1800.00")  # only the second


class PaymentTests(SalesBase):
    def test_part_payment_then_full_payment_moves_the_status(self):
        inv = self.invoice()
        p = self.pay(amount="500", allocations=[{"document": inv["id"], "amount": "500"}])
        self.assertRegex(p["reference_code"], r"^T2N/PAY/26/2008/00001$")
        d = self.get(inv)
        self.assertEqual((d["status_key"], d["paid"], d["balance"]), ("overdue", "500.00", "1300.00"))  # due in September, long past
        self.pay(amount="1300", date="2026-08-25", allocations=[{"document": inv["id"], "amount": "1300"}])
        d = self.get(inv)
        self.assertEqual((d["status_key"], d["balance"]), ("paid", "0.00"))
        self.assertEqual([x["amount"] for x in d["payments"]], ["500.00", "1300.00"])

    def test_auto_allocation_pays_the_oldest_due_first(self):
        a = self.invoice(due_date="2026-09-01")
        b = self.invoice(due_date="2026-08-20")
        p = self.pay(amount="2000", auto_allocate=True)
        self.assertEqual(sorted((x["number"], x["amount"]) for x in p["allocations"]), sorted([(b["number"], "1800.00"), (a["number"], "200.00")]))
        self.assertEqual((self.get(b)["balance"], self.get(a)["balance"]), ("0.00", "1600.00"))

    def test_money_not_applied_stays_as_the_customers_advance(self):
        inv = self.invoice()
        p = self.pay(amount="3000", allocations=[{"document": inv["id"], "amount": "1800"}])
        self.assertEqual((p["applied"], p["unapplied"]), ("1800.00", "1200.00"))
        row = self.fin.get(f"{S}/customers/{self.acme['id']}/").json()
        self.assertEqual((row["outstanding"], row["advance"], row["net_balance"]), ("0.00", "1200.00", "-1200.00"))
        later = self.invoice(issue_date="2026-09-01")
        self.assertEqual(self.get(later)["balance"], "1800.00")  # the advance is not applied on its own

    def test_applying_more_than_is_owed_or_more_than_received_is_refused(self):
        inv = self.invoice()
        other = self.invoice(self.customer("Other Co"))
        self.pay(amount="2000", allocations=[{"document": inv["id"], "amount": "1801"}], expect=400)
        self.pay(amount="1000", allocations=[{"document": inv["id"], "amount": "1500"}], expect=400)
        self.pay(amount="1000", allocations=[{"document": other["id"], "amount": "100"}], expect=400)  # someone else's invoice
        self.pay(amount="1000", allocations=[{"document": inv["id"], "amount": "100"}, {"document": inv["id"], "amount": "100"}], expect=400)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(self.get(inv)["balance"], "1800.00")

    def test_amount_and_date_and_mode_are_checked(self):
        for over in ({"amount": "0"}, {"amount": "-5"}, {"amount": "abc"}, {"amount": "10.123"}, {"date": "nope"}, {"mode": "barter"}):
            self.pay(expect=400, **over) if "date" not in over else self.assertEqual(
                self.fin.post(f"{S}/payments/", {"customer": self.acme["id"], "amount": "5", "mode": "cash", **over}).status_code, 400)

    def test_an_invoice_cannot_be_overpaid_by_two_payments(self):
        inv = self.invoice()
        self.pay(amount="1800", allocations=[{"document": inv["id"], "amount": "1800"}])
        self.pay(amount="100", allocations=[{"document": inv["id"], "amount": "100"}], expect=400)

    def test_voiding_a_payment_restores_the_balance(self):
        inv = self.invoice()
        p = self.pay(amount="1800", allocations=[{"document": inv["id"], "amount": "1800"}])
        self.assertEqual(self.fin.post(f"{S}/payments/{p['id']}/void/", {}).status_code, 400)
        r = self.fin.post(f"{S}/payments/{p['id']}/void/", {"change_reason": "Cheque bounced"})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "void"))
        self.assertEqual(self.get(inv)["balance"], "1800.00")
        self.assertEqual(self.fin.post(f"{S}/payments/{p['id']}/void/", {"change_reason": "again"}).status_code, 400)

    def test_an_invoice_with_a_payment_cannot_be_voided_until_the_payment_is(self):
        inv = self.invoice()
        p = self.pay(amount="100", allocations=[{"document": inv["id"], "amount": "100"}])
        self.assertEqual(self.fin.post(f"{S}/documents/{inv['id']}/void/", {"change_reason": "x"}).status_code, 400)
        self.fin.post(f"{S}/payments/{p['id']}/void/", {"change_reason": "Mistake"})
        self.assertEqual(self.fin.post(f"{S}/documents/{inv['id']}/void/", {"change_reason": "x"}).status_code, 200)

    def test_overdue_status_and_open_items(self):
        inv = self.invoice(due_date="2026-08-20")  # long past
        self.assertEqual(self.get(inv)["status_key"], "overdue")
        items = self.fin.get(f"{S}/customers/{self.acme['id']}/open-items/").json()
        self.assertEqual([i["number"] for i in items["owed"]], [inv["number"]])

    def test_payment_is_audited_and_listed(self):
        inv = self.invoice()
        p = self.pay(amount="900", allocations=[{"document": inv["id"], "amount": "900"}], reference="UTR123")
        e = CompanyProfileAuditEvent.objects.filter(entity_type="payment", entity_id=p["id"]).get()
        self.assertEqual(e.new_values["applied_to"], [inv["number"]])
        rows = self.fin.get(f"{S}/payments/", {"q": "utr123"}).json()
        self.assertEqual((rows["count"], rows["total_amount"]), (1, "900.00"))


class NoteTests(SalesBase):
    def credit(self, inv, items=None, reason="Goods returned", expect=201, **kw):
        r = self.fin.post(f"{S}/documents/", {"kind": "credit_note", "customer": inv["customer"]["id"], "issue_date": "2026-08-20",
                                              "against": inv["id"], "reason": reason, "form": {"items": items or [
                                                  {"description": "Return", "quantity": "1", "rate": "300"}]}, **kw})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def test_credit_note_reduces_what_the_invoice_owes_at_once(self):
        inv = self.invoice()
        cn = self.issue(self.credit(inv))
        self.assertEqual((cn["number"], cn["against"]["number"], cn["credit_left"]), ("T2N/CN/26/2008/00001", inv["number"], "0.00"))
        d = self.get(inv)
        self.assertEqual((d["balance"], d["credits_received"][0]["amount"]), ("1500.00", "300.00"))
        self.assertEqual(d["notes"][0]["number"], cn["number"])
        self.assertEqual(self.fin.get(f"{S}/customers/{self.acme['id']}/").json()["outstanding"], "1500.00")

    def test_credit_larger_than_the_invoice_balance_keeps_the_rest_as_credit_to_use_elsewhere(self):
        inv = self.invoice()
        self.pay(amount="1700", allocations=[{"document": inv["id"], "amount": "1700"}])  # 100 left
        cn = self.issue(self.credit(inv))                                                  # credit of 300
        self.assertEqual((self.get(inv)["balance"], self.get(cn)["credit_left"]), ("0.00", "200.00"))
        nxt = self.invoice(issue_date="2026-09-01")
        r = self.fin.post(f"{S}/credits/apply/", {"credit_note": cn["id"], "document": nxt["id"], "amount": "150"})
        self.assertEqual((r.status_code, r.json()["balance"]), (200, "1650.00"))
        self.assertEqual(self.get(cn)["credit_left"], "50.00")
        self.assertEqual(self.fin.post(f"{S}/credits/apply/", {"credit_note": cn["id"], "document": nxt["id"], "amount": "60"}).status_code, 400)

    def test_credit_note_rules(self):
        inv = self.invoice()
        self.credit(inv, reason="", expect=400)
        draft_inv = self.draft()
        self.credit(draft_inv, expect=400)                                                  # not issued
        self.credit(inv, items=[{"description": "Too much", "quantity": "1", "rate": "5000"}])  # a draft is fine; issue is not
        too_much = SalesDocument.objects.get(reason="Goods returned", grand_total=Decimal("5000"))
        self.assertEqual(self.fin.post(f"{S}/documents/{too_much.id}/issue/").status_code, 400)
        other = self.customer("Other Co")
        self.assertEqual(self.fin.post(f"{S}/documents/", {"kind": "credit_note", "customer": other["id"], "issue_date": "2026-08-20",
                                                          "against": inv["id"], "reason": "x", "form": {"items": ITEMS}}).status_code, 400)
        self.assertEqual(self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": inv["customer"]["id"], "issue_date": "2026-08-20",
                                                          "against": inv["id"], "form": {"items": ITEMS}}).status_code, 400)

    def test_credit_notes_together_cannot_exceed_the_invoice(self):
        inv = self.invoice()  # 1,800
        self.issue(self.credit(inv, items=[{"description": "a", "quantity": "1", "rate": "1000"}]))
        second = self.credit(inv, items=[{"description": "b", "quantity": "1", "rate": "900"}])
        self.assertEqual(self.fin.post(f"{S}/documents/{second['id']}/issue/").status_code, 400)

    def test_debit_note_adds_to_what_is_owed_and_can_be_paid(self):
        inv = self.invoice()
        r = self.fin.post(f"{S}/documents/", {"kind": "debit_note", "customer": inv["customer"]["id"], "issue_date": "2026-08-20",
                                              "against": inv["id"], "reason": "Late fee", "form": {"items": [
                                                  {"description": "Late fee", "quantity": "1", "rate": "200"}]}})
        dn = self.issue(r.json())
        self.assertEqual((dn["number"], dn["balance"]), ("T2N/DN/26/2008/00001", "200.00"))
        self.assertEqual(self.fin.get(f"{S}/customers/{self.acme['id']}/").json()["outstanding"], "2000.00")
        self.pay(amount="200", allocations=[{"document": dn["id"], "amount": "200"}])
        self.assertEqual(self.get(dn)["status_key"], "paid")

    def test_an_invoice_with_a_live_note_cannot_be_voided_and_a_used_credit_note_cannot_either(self):
        inv = self.invoice()
        cn = self.issue(self.credit(inv))
        self.assertEqual(self.fin.post(f"{S}/documents/{inv['id']}/void/", {"change_reason": "x"}).status_code, 400)
        self.assertEqual(self.fin.post(f"{S}/documents/{cn['id']}/void/", {"change_reason": "x"}).status_code, 400)  # already used

    def test_unused_credit_note_can_be_voided(self):
        inv = self.invoice()
        self.pay(amount="1800", allocations=[{"document": inv["id"], "amount": "1800"}])
        cn = self.issue(self.credit(inv))  # nothing owed, so nothing applied
        self.assertEqual(self.get(cn)["credit_left"], "300.00")
        self.assertEqual(self.fin.post(f"{S}/documents/{cn['id']}/void/", {"change_reason": "Raised in error"}).status_code, 200)
        self.assertFalse(CreditApplication.objects.exists())


class ReportTests(SalesBase):
    def test_statement_runs_a_balance(self):
        inv = self.invoice()
        self.pay(amount="500", allocations=[{"document": inv["id"], "amount": "500"}])
        cn = self.issue(NoteTests.credit(self, inv))
        rows = self.fin.get(f"{S}/customers/{self.acme['id']}/statement/").json()["rows"]
        self.assertEqual([(r["type"], r["debit"], r["credit"], r["balance"]) for r in rows], [
            ("Invoice", "1800.00", "0.00", "1800.00"), ("Credit note", "0.00", "300.00", "1500.00"),
            ("Payment", "0.00", "500.00", "1000.00")])  # same date: documents come before payments
        self.assertEqual(cn["number"], rows[1]["number"])

    def test_ageing_buckets_add_up(self):
        for days_overdue, name in ((-5, "Not due"), (3, "A"), (20, "B"), (40, "C"), (60, "D")):
            due = TODAY - datetime.timedelta(days=days_overdue)
            self.invoice(self.customer(name), issue_date=iso(due - datetime.timedelta(days=1)), due_date=iso(due))
        rep = self.fin.get(f"{S}/reports/receivables/").json()
        self.assertEqual({b["key"]: b["amount"] for b in rep["buckets"]}, {
            "current": "1800.00", "d1_15": "1800.00", "d16_30": "1800.00", "d31_45": "1800.00", "d46_plus": "1800.00"})
        self.assertEqual(rep["total"], "9000.00")
        self.assertEqual(len(rep["rows"]), 5)
        self.assertEqual(sum(Decimal(r["total"]) for r in rep["rows"]), Decimal("9000.00"))

    def test_dashboard_figures(self):
        a = self.invoice(due_date=iso(TODAY - datetime.timedelta(days=10)), issue_date=iso(TODAY - datetime.timedelta(days=11)))
        self.invoice(self.customer("Beta"), issue_date=iso(TODAY), due_date=iso(TODAY + datetime.timedelta(days=10)))
        self.draft()
        self.pay(amount="800", date=iso(TODAY), allocations=[{"document": a["id"], "amount": "800"}])
        d = self.fin.get(f"{S}/dashboard/").json()
        self.assertEqual((d["receivables"]["outstanding"], d["receivables"]["overdue"], d["receivables"]["current"]), ("2800.00", "1000.00", "1800.00"))
        self.assertEqual(sum(Decimal(b["amount"]) for b in d["aging"]), Decimal("2800.00"))
        self.assertEqual({k: d["status_counts"][k] for k in ("draft", "unpaid", "overdue", "partial")}, {"draft": 1, "unpaid": 2, "overdue": 1, "partial": 0})
        this = d["monthly"][-1]
        self.assertEqual(len(d["monthly"]), 6)
        self.assertEqual(this["received"], "800.00")
        self.assertEqual(d["top_customers"][0]["outstanding"], "1800.00")
        self.assertEqual(d["customers"], 2)

    def test_list_filters_and_totals(self):
        a = self.invoice(due_date="2026-08-20")
        beta = self.customer("Beta")
        b = self.invoice(beta, issue_date="2026-08-16")
        self.pay(beta, amount="1800", allocations=[{"document": b["id"], "amount": "1800"}])
        self.draft()
        def ids(**p):
            return {r["id"] for r in self.fin.get(f"{S}/documents/", p).json()["results"]}
        self.assertEqual(ids(status="paid"), {b["id"]})
        self.assertEqual(ids(status="overdue"), {a["id"]})
        self.assertEqual(ids(status="unpaid"), {a["id"]})
        self.assertEqual(len(ids(status="draft")), 1)
        self.assertEqual(ids(customer=self.acme["id"], kind="invoice", status="unpaid"), {a["id"]})
        self.assertEqual(ids(q=b["number"]), {b["id"]})
        self.assertEqual(ids(q="beta"), {b["id"]})
        self.assertEqual(ids(**{"from": "2026-08-16", "to": "2026-08-16"}) - ids(status="draft"), {b["id"]})
        r = self.fin.get(f"{S}/documents/", {"status": "unpaid"}).json()
        self.assertEqual((r["count"], r["total_amount"], r["total_balance"]), (1, "1800.00", "1800.00"))


class PermissionTests(SalesBase):
    def test_roles(self):
        inv = self.invoice()
        employee, partner = self.api_staff, client_for(make_user("part", Role.PROJECT_MANAGER))  # views sales, cannot manage
        for client in (employee,):
            for path in ("dashboard/", "customers/", "documents/", "payments/", "reports/receivables/"):
                self.assertEqual(client.get(f"{S}/{path}").status_code, 403, path)
        self.assertEqual(partner.get(f"{S}/documents/{inv['id']}/").status_code, 200)
        self.assertEqual(partner.post(f"{S}/customers/", {"display_name": "X"}).status_code, 403)
        self.assertEqual(partner.post(f"{S}/payments/", {"customer": self.acme["id"], "date": "2026-08-20", "amount": "1"}).status_code, 403)
        self.assertEqual(partner.post(f"{S}/documents/{inv['id']}/void/", {"change_reason": "x"}).status_code, 403)
        self.assertEqual(self.anon.get(f"{S}/customers/").status_code, 401)
        self.assertEqual(self.api.get(f"{S}/customers/").status_code, 200)

    def test_unknown_ids_are_404_not_errors(self):
        zero = "00000000-0000-0000-0000-000000000000"
        for path in (f"customers/{zero}/", f"documents/{zero}/", f"payments/{zero}/", f"customers/{zero}/statement/"):
            self.assertEqual(self.fin.get(f"{S}/{path}").status_code, 404, path)
        self.assertEqual(self.fin.get(f"{S}/documents/not-a-uuid/").status_code, 404)
