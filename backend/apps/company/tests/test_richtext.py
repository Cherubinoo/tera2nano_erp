from django.test import SimpleTestCase, TestCase
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import HRFlowable, Paragraph, Table

from apps.company.models import IssuedDocument
from apps.company.services import richtext

from .base import API
from .test_quotations import HAS_FITZ, QuotationBase, fitz

BASE = ParagraphStyle("b", fontName="Helvetica", fontSize=9, leading=12)
ST = {"h2": ParagraphStyle("h2", parent=BASE, fontName="Helvetica-Bold", spaceBefore=8),
      "bold": ParagraphStyle("bold", parent=BASE, fontName="Helvetica-Bold")}


def render(text):
    return richtext.flowables(text, ST, base=BASE, gap=6, width=500, primary=colors.HexColor("#304352"))


def kinds(flows):
    return [type(f).__name__ for f in flows if type(f).__name__ not in ("Spacer", "CondPageBreak")]


class InlineTests(SimpleTestCase):
    def test_bold_italic_and_escaping(self):
        self.assertEqual(richtext.inline("a **b** c *d* <e> & f"), "a <b>b</b> c <i>d</i> &lt;e&gt; &amp; f")

    def test_unbalanced_markers_and_snake_case_are_left_alone(self):
        self.assertEqual(richtext.inline("2 * 3 = 6 and T7_2244_x"), "2 * 3 = 6 and T7_2244_x")
        self.assertEqual(richtext.inline("**open"), "**open")

    def test_rupee_sign_prints_as_rs(self):
        self.assertEqual(richtext.clean("₹8,00,000 and ₹ 500"), "Rs. 8,00,000 and Rs. 500")
        self.assertEqual(richtext.clean("你好"), "??")


class BlockTests(SimpleTestCase):
    def test_headings_lists_rules_and_tables_become_structure(self):
        text = ("## SECTION 7\n### 7.1 Amount\nPlain line one\nline two\n\n---\n\n"
                "1. First\n2. Second\n   continued\n- bullet\n\n"
                "| Description | Amount |\n|---|---:|\n| Software | 8,00,000.00 |\n| **Total** | **9,44,000.00** |")
        flows = render(text)
        self.assertEqual(kinds(flows), ["Paragraph", "Paragraph", "Paragraph", "HRFlowable", "Paragraph", "Paragraph",
                                        "Paragraph", "Table"])
        self.assertIsInstance([f for f in flows if isinstance(f, Table)][0], Table)
        self.assertIsInstance([f for f in flows if isinstance(f, HRFlowable)][0], HRFlowable)
        shown = " ".join(getattr(f, "text", "") for f in flows if isinstance(f, Paragraph))
        self.assertNotIn("##", shown)
        self.assertNotIn("**", shown)
        self.assertIn("Plain line one<br/>line two", shown)  # single line breaks are kept
        self.assertIn("Second<br/>continued", shown)

    def test_table_without_a_separator_row_is_all_body_and_ragged_rows_are_padded(self):
        flows = render("| a | b |\n| c |")
        table = [f for f in flows if isinstance(f, Table)][0]
        self.assertEqual((len(table._cellvalues), len(table._cellvalues[0])), (2, 2))

    def test_numeric_columns_are_right_aligned(self):
        table = [f for f in render("| Item | Amount |\n|---|---|\n| X | Rs. 1,000.00 |\n| Y | 25% |") if isinstance(f, Table)][0]
        self.assertEqual(table._cellvalues[1][1].style.alignment, 2)
        self.assertEqual(table._cellvalues[1][0].style.alignment, 0)

    def test_nothing_special_stays_a_paragraph_and_blank_text_is_empty(self):
        self.assertEqual(kinds(render("Just a sentence.")), ["Paragraph"])
        self.assertEqual(render("  \n\n "), [])
        self.assertEqual(kinds(render("#hashtag and 5 - 3 and -5")), ["Paragraph"])

    def test_bad_markup_does_not_break_rendering(self):
        flows = render("text with <b>unclosed and **bold <i>**")
        self.assertEqual(kinds(flows), ["Paragraph"])


PASTED = {
    "sections": [{"heading": "Price and Commercial Terms", "body": (
        "## SECTION 7 – PRICE AND COMMERCIAL TERMS\n\n### 7.1 Quotation Amount\n\n"
        "Submits its quotation for **Marayoor Bank**, as per the tender.\n\n"
        "| Description | Amount |\n|---|---:|\n| ERP Software | ₹8,00,000.00 |\n| GST @ 18% | ₹1,44,000.00 |\n"
        "| **Total Quotation Amount** | **₹9,44,000.00** |\n\n"
        "1. ERP software supply and implementation.\n2. User training.\n\n- Timely receipt of the purchase order.\n")}],
    "terms": "## Payment\n\n**Net 30** days.\n\n* Late fee applies.",
}


class PastedContentInThePdfTests(QuotationBase):
    def pdf(self, doc, variant=None):
        field = doc.print_file if variant == "print" else doc.file
        with field.open("rb") as fh:
            return "".join(p.get_text() for p in fitz.open(stream=fh.read(), filetype="pdf"))

    def issue_doc(self, **q):
        r = self.issue_q(**q)
        self.assertEqual(r.status_code, 201, r.content)
        return IssuedDocument.objects.get(pk=r.json()["id"])

    def test_pasted_markdown_prints_as_structure_not_symbols(self):
        if not HAS_FITZ:
            self.skipTest("PyMuPDF not installed")
        doc = self.issue_doc(**PASTED)
        for variant in (None, "print"):
            text = self.pdf(doc, variant)
            for gone in ("##", "###", "**", "|---", "| Description", "₹"):
                self.assertNotIn(gone, text, (variant, gone))
            for shown in ("SECTION 7 – PRICE AND COMMERCIAL TERMS", "7.1 Quotation Amount", "Marayoor Bank", "ERP Software",
                          "Rs. 8,00,000.00", "Rs. 9,44,000.00", "Total Quotation Amount", "ERP software supply and implementation.",
                          "Timely receipt of the purchase order.", "Net 30", "Late fee applies."):
                self.assertIn(shown, text, (variant, shown))
            self.assertNotIn("?8,00,000", text)

    def test_no_signature_block_by_default_and_opt_in(self):
        if not HAS_FITZ:
            self.skipTest("PyMuPDF not installed")
        self.assertNotIn("Authorised Signatory", self.pdf(self.issue_doc()))
        self.assertNotIn("Authorised Signatory", self.pdf(self.issue_doc(), "print"))
        with_block = self.issue_doc(show_signature=True)
        self.assertIn("Authorised Signatory", self.pdf(with_block))

    def test_other_documents_keep_their_signature_block(self):
        if not HAS_FITZ:
            self.skipTest("PyMuPDF not installed")
        d = self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", issue_date="2026-08-15", content={"paragraphs": ["Hello"]})).json()
        self.assertIn("Authorised Signatory", self.pdf(IssuedDocument.objects.get(pk=d["id"])))

    def test_key_details_print_as_a_table(self):
        if not HAS_FITZ:
            self.skipTest("PyMuPDF not installed")
        doc = self.issue_doc(details=[{"label": "Project", "value": "ERP for jaggery production"},
                                      {"label": "Completion period", "value": "4 weeks"}, {"label": "", "value": ""}])
        text = self.pdf(doc)
        for shown in ("Key details", "Project", "ERP for jaggery production", "Completion period", "4 weeks"):
            self.assertIn(shown, text)
        self.assertEqual([d["label"] for d in doc.snapshot["quotation"]["details"]], ["Project", "Completion period"])

    def test_key_details_are_checked(self):
        self.assertEqual(self.working(details=[{"label": "Project", "value": ""}]).status_code, 400)
        self.assertEqual(self.working(details="x").status_code, 400)
        self.assertEqual(self.working(details=["x"]).status_code, 400)

    def test_long_pasted_terms_flow_over_pages(self):
        body = "\n\n".join(f"### {n}. Clause\n\n" + "The supplier shall comply with all terms. " * 20 for n in range(1, 25))
        doc = self.issue_doc(terms=body)
        self.assertTrue(doc.file.size > 0)
