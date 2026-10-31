from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from apps.company.models import CompanyPolicy, CompanyProfile, CompanyTaxSettings, IssuedDocument, IssuedReferenceNumber
from apps.company.services import quotations

from .base import API, GSTIN, make_gstin, CompanyTestMixin

try:
    import fitz  # PyMuPDF is only a test-time helper
    HAS_FITZ = True
except ImportError:  # pragma: no cover
    HAS_FITZ = False

ITEMS = [
    {"description": "Nano coating service", "hsn_sac": "998313", "quantity": "2", "unit": "Nos", "rate": "1000",
     "discount_percent": "10", "gst_rate": "18"},
    {"description": "Consumables", "quantity": "1", "unit": "Lot", "rate": "500.50", "gst_rate": "5"},
]


def quote(**over):
    q = {"customer": {"name": "Acme Industries", "address": ["Plot 4, MIDC", "Pune"], "state_code": "29"},
         "items": ITEMS, "subject": "Quotation for coating"}
    q.update(over)
    return q


class FormattingTests(SimpleTestCase):
    def test_indian_grouping(self):
        self.assertEqual(quotations.money("0"), "0.00")
        self.assertEqual(quotations.money("999.5"), "999.50")
        self.assertEqual(quotations.money("1000"), "1,000.00")
        self.assertEqual(quotations.money("123456.7"), "1,23,456.70")
        self.assertEqual(quotations.money("12345678.9"), "1,23,45,678.90")
        self.assertEqual(quotations.money("-2500"), "-2,500.00")
        self.assertEqual(quotations.money("1234567.5", "USD"), "1,234,567.50")

    def test_amount_in_words(self):
        w = quotations.amount_in_words
        self.assertEqual(w("0"), "INR Zero Only")
        self.assertEqual(w("21"), "INR Twenty One Only")
        self.assertEqual(w("100"), "INR One Hundred Only")
        self.assertEqual(w("123456.50"), "INR One Lakh Twenty Three Thousand Four Hundred Fifty Six and Fifty Paise Only")
        self.assertEqual(w("10000000"), "INR One Crore Only")
        self.assertEqual(w("1050000000.05"), "INR One Hundred Five Crore and Five Paise Only")

    def test_plain_number(self):
        self.assertEqual([quotations.plain_number(v) for v in ("18.00", "2.500", "0.25", "10", "100")],
                         ["18", "2.5", "0.25", "10", "100"])


class QuotationBase(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()

    def enable_gst(self, default_code=""):
        CompanyProfile.objects.update(gst_registration_status="registered", gstin=GSTIN)
        CompanyTaxSettings.objects.update(
            gst_applicable=True, gst_treatment="regular", default_tax_code=default_code,
            tax_codes=[{"code": "GST18", "rate": 18}, {"code": "GST5", "rate": 5}])

    def issue_q(self, client=None, issue_date="2026-08-15", **q):
        return (client or self.api_staff).post(f"{API}/issued-documents/", dict(
            document_type="quotation", issue_date=issue_date, content={"quotation": quote(**q)}))

    def working(self, **q):
        return self.api_staff.post(f"{API}/quotations/working/", {"quotation": quote(**q), "issue_date": "2026-08-15"})


class QuotationWithoutGstTests(QuotationBase):
    def test_totals_are_plain_when_gst_is_not_enabled(self):
        r = self.issue_q()
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(d["reference_number"], "T2N/QUO/26/1508/00001")
        q = IssuedDocument.objects.get(pk=d["id"]).snapshot["quotation"]
        self.assertFalse(q["gst_applied"])
        self.assertEqual(q["totals"]["taxable"], "2300.50")  # 2 x 1000 less 10%, plus 500.50
        self.assertEqual((q["totals"]["tax_total"], q["totals"]["grand_total"]), ("0.00", "2300.50"))
        self.assertEqual(q["totals"]["tax"], [])
        self.assertIsNone(q["place_of_supply"])
        self.assertEqual(q["amount_in_words"], "INR Two Thousand Three Hundred and Fifty Paise Only")

    def test_gst_rates_are_ignored_while_gst_is_off(self):
        q = self.working().json()
        self.assertTrue(all(line["gst_rate"] is None for line in q["lines"]))

    def test_both_pdfs_are_made_and_staff_may_issue(self):
        d = self.issue_q().json()
        for variant in ("", "?variant=print"):
            resp = self.api_staff.get(f"{API}/issued-documents/{d['id']}/download/{variant}")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(b"".join(resp.streaming_content).startswith(b"%PDF"))


class QuotationWithGstTests(QuotationBase):
    def setUp(self):
        super().setUp()
        self.enable_gst()

    def test_same_state_splits_into_cgst_and_sgst_per_rate(self):
        q = self.working().json()  # customer in Karnataka (29), like the company
        self.assertEqual(q["supply_type"], "intra_state")
        self.assertEqual(q["place_of_supply"], {"code": "29", "name": "Karnataka"})
        self.assertEqual({r["label"]: r["amount"] for r in q["totals"]["tax"]},
                         {"CGST @ 9%": "162.00", "SGST @ 9%": "162.00", "CGST @ 2.5%": "12.51", "SGST @ 2.5%": "12.51"})
        self.assertEqual(q["totals"]["taxable"], "2300.50")
        self.assertEqual((q["totals"]["tax_total"], q["totals"]["grand_total"]), ("349.02", "2649.52"))
        self.assertEqual(q["lines"][0]["taxable"], "1800.00")
        self.assertEqual(q["lines"][0]["total"], "2124.00")

    def test_other_state_charges_igst(self):
        q = self.working(customer={"name": "Acme", "state_code": "27"}).json()
        self.assertEqual(q["supply_type"], "inter_state")
        self.assertEqual({r["label"]: r["amount"] for r in q["totals"]["tax"]}, {"IGST @ 18%": "324.00", "IGST @ 5%": "25.03"})
        self.assertEqual(q["totals"]["grand_total"], "2649.53")

    def test_customer_gstin_decides_the_place_of_supply(self):
        q = self.working(customer={"name": "Acme", "gstin": make_gstin("27", "AAAPA1234A")}).json()
        self.assertEqual(q["place_of_supply"]["code"], "27")
        self.assertEqual(q["supply_type"], "inter_state")

    def test_place_of_supply_falls_back_to_the_company_state(self):
        q = self.working(customer={"name": "Acme"}).json()
        self.assertEqual((q["place_of_supply"]["code"], q["supply_type"]), ("29", "intra_state"))

    def test_item_without_a_rate_uses_the_default_tax_code_or_is_refused(self):
        items = [{"description": "x", "quantity": "1", "rate": "100"}]
        r = self.working(items=items)
        self.assertEqual(r.status_code, 400)
        self.assertIn("Item 1: choose a GST rate", str(r.json()))
        CompanyTaxSettings.objects.update(default_tax_code="GST18")
        r = self.working(items=items)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["totals"]["tax_total"], "18.00")

    def test_a_rate_that_is_not_a_current_gst_rate_is_refused(self):
        r = self.working(items=[{"description": "x", "quantity": "1", "rate": "100", "gst_rate": "17"}])
        self.assertEqual(r.status_code, 400)
        self.assertIn("not a currently applicable GST rate", str(r.json()))

    def test_zero_rate_item_adds_no_tax_rows(self):
        q = self.working(items=[{"description": "Exempt", "quantity": "1", "rate": "100", "gst_rate": "0"}]).json()
        self.assertEqual((q["totals"]["tax"], q["totals"]["tax_total"], q["totals"]["grand_total"]), ([], "0.00", "100.00"))

    def test_working_matches_what_is_issued_and_uses_no_number(self):
        before = IssuedReferenceNumber.objects.count()
        worked = self.working().json()
        self.assertEqual(IssuedReferenceNumber.objects.count(), before)
        issued = IssuedDocument.objects.get(pk=self.issue_q().json()["id"]).snapshot["quotation"]
        self.assertEqual(worked, issued)

    @staticmethod
    def pdf_text(doc, variant=None):
        field = doc.print_file if variant == "print" else doc.file
        with field.open("rb") as fh:
            return "".join(p.get_text() for p in fitz.open(stream=fh.read(), filetype="pdf"))

    def test_pdf_prints_customer_lines_tax_and_total(self):
        if not HAS_FITZ:
            self.skipTest("PyMuPDF not installed")
        doc = IssuedDocument.objects.get(pk=self.issue_q(enquiry_ref="ENQ-77", notes="Delivery in 2 weeks.").json()["id"])
        for variant in (None, "print"):
            text = self.pdf_text(doc, variant)
            for needle in ("T2N/QUO/26/1508/00001", "Acme Industries", "Plot 4, MIDC", "Nano coating service", "998313",
                           "CGST @ 9%", "SGST @ 2.5%", "2,649.52", "INR Two Thousand Six Hundred",
                           "Place of supply", "Karnataka (29)", "ENQ-77", "Delivery in 2 weeks.", "Disc %", "GST %"):
                self.assertIn(needle, text, (variant, needle))

    def test_payment_terms_print_under_the_totals(self):
        if not HAS_FITZ:
            self.skipTest("PyMuPDF not installed")
        doc = IssuedDocument.objects.get(pk=self.issue_q().json()["id"])
        text = self.pdf_text(doc)
        self.assertGreater(text.index("Payment terms: Net 30"), text.index("2,649.52"))


class QuotationValidityAndTermsTests(QuotationBase):
    def test_valid_until_defaults_from_the_policy_and_can_be_set(self):
        CompanyPolicy.objects.update(quotation_validity_days=15)
        self.assertEqual(self.working().json()["valid_until"], "2026-08-30")
        self.assertEqual(self.working(valid_until="2026-09-30").json()["valid_until"], "2026-09-30")
        self.assertEqual(self.working(valid_until="2026-08-01").status_code, 400)

    def test_no_validity_when_the_policy_has_none(self):
        CompanyPolicy.objects.update(quotation_validity_days=None)
        self.assertEqual(self.working().json()["valid_until"], "")

    def test_terms_override_is_printed(self):
        if not HAS_FITZ:
            self.skipTest("PyMuPDF not installed")
        doc = IssuedDocument.objects.get(pk=self.issue_q(terms="Prices are ex-works.").json()["id"])
        with doc.file.open("rb") as fh:
            text = "".join(p.get_text() for p in fitz.open(stream=fh.read(), filetype="pdf"))
        self.assertIn("Prices are ex-works.", text)


class QuotationValidationTests(QuotationBase):
    def bad(self, message, **q):
        for call in (self.working, self.issue_q):
            r = call(**q)
            self.assertEqual(r.status_code, 400, (message, r.content))
            self.assertIn(message, str(r.json()), r.content)
        self.assertEqual(IssuedDocument.objects.count(), 0)

    def test_needs_a_customer_and_items(self):
        self.bad("Customer name is required", customer={"name": " "})
        self.bad("Add at least one item", items=[])
        self.bad("Add at least one item", items="x")

    def test_items_are_checked(self):
        item = {"description": "x", "quantity": "1", "rate": "10"}
        self.bad("Item 1 description is required", items=[{**item, "description": ""}])
        self.bad("Item 1 quantity must be between", items=[{**item, "quantity": "0"}])
        self.bad("Item 1 quantity must be a number", items=[{**item, "quantity": "abc"}])
        self.bad("Item 1 rate must be between", items=[{**item, "rate": "-5"}])
        self.bad("Item 1 discount must be between", items=[{**item, "discount_percent": "101"}])
        self.bad("Item 1: HSN/SAC must be 2 to 8 digits", items=[{**item, "hsn_sac": "AB"}])
        self.bad("Item 2 quantity must be a number", items=[item, {**item, "quantity": None}])
        self.bad("at most 3 decimal places", items=[{**item, "quantity": "1.2345"}])

    def test_customer_gstin_and_state_are_checked(self):
        self.bad("Customer GSTIN", customer={"name": "A", "gstin": "NOTAGSTIN"})
        self.bad("unknown GST state code", customer={"name": "A", "state_code": "55"})

    def test_a_date_that_is_not_a_date_is_refused(self):
        self.bad("Valid until must be a date", valid_until="tomorrow")

    def test_quotation_content_cannot_be_mixed_or_used_on_other_documents(self):
        mixed = self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type="quotation", content={"quotation": quote(), "table": {"columns": ["a"], "rows": [["1"]]}}))
        self.assertEqual(mixed.status_code, 400)
        letter = self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", content={"quotation": quote()}))
        self.assertEqual(letter.status_code, 400)

    def test_failed_quotation_uses_no_number(self):
        self.issue_q(items=[])
        self.assertEqual(IssuedReferenceNumber.objects.filter(document_type="quotation").count(), 0)
        self.assertEqual(self.issue_q().json()["reference_number"], "T2N/QUO/26/1508/00001")

    def test_many_items_flow_onto_further_pages(self):
        items = [{"description": f"Item number {n} " + "long description " * 6, "quantity": "1", "rate": "10"}
                 for n in range(60)]
        r = self.issue_q(items=items)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(IssuedDocument.objects.get().snapshot["quotation"]["totals"]["grand_total"]), Decimal("600.00"))


class QuotationSectionsTests(QuotationBase):
    SECTIONS = [{"heading": "Scope of work", "body": "Design and build the portal.\n\nHost it for a year."},
                {"heading": "Timeline", "body": "8 weeks from the purchase order."},
                {"heading": "", "body": ""}]

    def test_sections_are_kept_and_blank_ones_dropped(self):
        d = self.issue_q(sections=self.SECTIONS).json()
        q = IssuedDocument.objects.get(pk=d["id"]).snapshot["quotation"]
        self.assertEqual([s["heading"] for s in q["sections"]], ["Scope of work", "Timeline"])

    def test_sections_print_under_the_totals_before_the_terms(self):
        if not HAS_FITZ:
            self.skipTest("PyMuPDF not installed")
        doc = IssuedDocument.objects.get(pk=self.issue_q(sections=self.SECTIONS, terms="Ex-works prices.").json()["id"])
        for field in (doc.file, doc.print_file):
            with field.open("rb") as fh:
                text = "".join(p.get_text() for p in fitz.open(stream=fh.read(), filetype="pdf"))
            order = [text.index(s) for s in ("2,300.50", "Scope of work", "Host it for a year.", "Timeline", "Ex-works prices.")]
            self.assertEqual(order, sorted(order))

    def test_sections_are_checked(self):
        self.assertEqual(self.working(sections="x").status_code, 400)
        self.assertEqual(self.working(sections=["x"]).status_code, 400)
        self.assertEqual(self.working(sections=[{"heading": "h", "body": "b" * 20001}]).status_code, 400)


class NextReferenceTests(QuotationBase):
    def next(self, date="2026-08-15", doc="quotation"):
        r = self.api_staff.get(f"{API}/document-numbering/next/", {"document_type": doc, "issue_date": date})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["reference_number"]

    def test_preview_matches_what_is_issued_and_uses_nothing(self):
        before = IssuedReferenceNumber.objects.count()
        self.assertEqual(self.next(), "T2N/QUO/26/1508/00001")
        self.assertEqual(self.next(), "T2N/QUO/26/1508/00001")  # asking again does not move it on
        self.assertEqual(IssuedReferenceNumber.objects.count(), before)
        self.assertEqual(self.issue_q().json()["reference_number"], "T2N/QUO/26/1508/00001")
        self.assertEqual(self.next(), "T2N/QUO/26/1508/00002")
        self.assertEqual(self.next("2026-09-01"), "T2N/QUO/26/0109/00002")  # same year: the count carries on
        self.assertEqual(self.issue_q(issue_date="2026-09-01").json()["reference_number"], "T2N/QUO/26/0109/00002")

    def test_preview_follows_a_new_financial_year_and_back_dating(self):
        self.issue_q()
        self.assertEqual(self.next("2027-04-02"), "T2N/QUO/27/0204/00001")  # a new financial year starts again at 1
        self.assertEqual(self.next("2026-05-01"), "T2N/QUO/26/0105/00002")  # same financial year: the count carries on
        self.assertEqual(self.next("2026-03-15"), "T2N/QUO/26/1503/00001")  # the previous year has its own numbers

    def test_preview_works_before_a_sequence_exists_and_creates_none(self):
        from apps.company.models import DocumentNumberSequence
        DocumentNumberSequence.objects.filter(document_type="meeting").delete()
        self.assertEqual(self.next(doc="meeting"), "T2N-MTG-26-1508-0001")
        self.assertFalse(DocumentNumberSequence.objects.filter(document_type="meeting").exists())

    def test_switched_off_and_unknown(self):
        from apps.company.models import DocumentNumberSequence
        DocumentNumberSequence.objects.filter(document_type="quotation").update(is_active=False)
        self.assertIsNone(self.next())
        self.assertEqual(self.api_staff.get(f"{API}/document-numbering/next/", {"document_type": "x"}).status_code, 400)
        self.assertEqual(self.api_staff.get(f"{API}/document-numbering/next/", {"document_type": "quotation", "issue_date": "x"}).status_code, 400)
        self.assertEqual(self.anon.get(f"{API}/document-numbering/next/", {"document_type": "quotation"}).status_code, 401)


class QuotationOptionsTests(QuotationBase):
    def test_options_describe_the_form(self):
        o = self.api_staff.get(f"{API}/quotations/working/").json()
        self.assertEqual((o["gst_applied"], o["gst_rates"], o["currency"]), (False, [], "INR"))
        self.assertEqual(o["payment_terms"], "Net 30")
        self.enable_gst(default_code="GST18")
        o = self.api_staff.get(f"{API}/quotations/working/").json()
        self.assertTrue(o["gst_applied"])
        self.assertEqual((o["default_gst_rate"], o["supplier_state_code"]), ("18", "29"))
        self.assertIn("18", o["gst_rates"])

    def test_requires_sign_in(self):
        self.assertEqual(self.anon.get(f"{API}/quotations/working/").status_code, 401)
        self.assertEqual(self.anon.post(f"{API}/quotations/working/", {}).status_code, 401)
