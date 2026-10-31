import datetime
import io

from django.test import TestCase

from apps.company.models import DocumentNumberSequence, IssuedDocument, LetterheadTemplate
from apps.company.services import letterheads

from .base import API, CompanyTestMixin

OPS = {code: name for code, name, _ in letterheads.DEFAULT_OPERATIONS}


def pdf_text(doc, variant=None):
    import fitz  # PyMuPDF is only a test-time helper; skip the text assertions when it is not installed
    field = doc.print_file if variant == "print" else doc.file
    with field.open("rb") as fh:
        return "".join(p.get_text() for p in fitz.open(stream=fh.read(), filetype="pdf"))


try:
    import fitz  # noqa: F401
    HAS_FITZ = True
except ImportError:  # pragma: no cover
    HAS_FITZ = False


class OperationsCatalogueTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def ops(self, **params):
        return self.api_staff.get(f"{API}/letterhead-templates/", {"kind": "operation", **params})

    def test_operations_are_listed_with_their_own_ids_and_previews(self):
        rows = self.ops().json()
        self.assertEqual({r["code"]: r["name"] for r in rows}, OPS)
        self.assertTrue(all(r["kind"] == "operation" for r in rows))
        today = datetime.date.today()
        first = {r["code"]: r for r in rows}["OPS-001"]
        self.assertEqual(first["next_reference_preview"],
                         f"OPS-001/{today.year % 100:02d}/{today.day:02d}{today.month:02d}/00001")

    def test_letter_templates_and_operations_stay_apart(self):
        letters = self.api_staff.get(f"{API}/letterhead-templates/").json()
        self.assertTrue(all(r["code"].startswith("LHT-") and r["kind"] == "letter" for r in letters))
        self.ops()
        everything = self.api_staff.get(f"{API}/letterhead-templates/", {"kind": "all"}).json()
        self.assertEqual(len(everything), len(letters) + len(OPS))
        self.assertEqual(self.api_staff.get(f"{API}/letterhead-templates/", {"kind": "x"}).status_code, 400)
        kinds = {t["value"]: t["kind"] for t in self.api_staff.get(f"{API}/lookups/").json()["letterhead_templates"]}
        self.assertEqual(kinds["LHT-001"], "letter")
        self.assertEqual(kinds["OPS-001"], "operation")

    def test_operation_sequence_is_its_own_counter_that_restarts_yearly(self):
        self.ops()
        seq = DocumentNumberSequence.objects.get(letterhead_template__code="OPS-003")
        self.assertEqual((seq.document_type, seq.reset_frequency, seq.prefix), ("operation_document", "yearly", "OPS-003"))

    def test_add_an_operation_with_the_right_id_format(self):
        self.ops()
        for bad in ({"code": "LHT-020", "name": "x"}, {"code": "OPS1", "name": "x"}, {"code": "OPS-001", "name": "Dup"}):
            r = self.api.post(f"{API}/letterhead-templates/", {**bad, "kind": "operation"})
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.api.post(f"{API}/letterhead-templates/", {"code": "OPS-020", "name": "x"}).status_code, 400)
        r = self.api.post(f"{API}/letterhead-templates/", {"code": "ops-020", "name": "Calibration Record", "kind": "operation"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["code"], "OPS-020")
        self.assertTrue(r.json()["next_reference_preview"].startswith("OPS-020/"))
        self.assertEqual(self.api_staff.post(f"{API}/letterhead-templates/", {"code": "OPS-021", "name": "x", "kind": "operation"}).status_code, 403)

    def test_kind_cannot_change_and_seed_defaults_restores_operations(self):
        t = LetterheadTemplate.objects.get(code="OPS-002") if self.ops() else None
        self.assertEqual(self.api.patch(f"{API}/letterhead-templates/{t.id}/", {"kind": "letter"}).status_code, 400)
        self.api.patch(f"{API}/letterhead-templates/{t.id}/", {"name": "Task Card"})
        self.assertEqual(self.api.post(f"{API}/letterhead-templates/seed-defaults/", {"kind": "operation"}).json(), [])
        self.assertEqual(LetterheadTemplate.objects.get(code="OPS-002").name, "Task Card")
        self.assertEqual(self.api.post(f"{API}/letterhead-templates/seed-defaults/", {"kind": "x"}).status_code, 400)


class IssueOperationTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.api_staff.get(f"{API}/letterhead-templates/", {"kind": "operation"})

    def issue(self, template="OPS-001", **extra):
        payload = dict(document_type="operation_document", letterhead_template=template, issue_date="2026-10-27",
                       content={"sections": [{"heading": "Scope", "body": "Install the unit.\n\nTest it."}]})
        payload.update(extra)
        return self.api_staff.post(f"{API}/issued-documents/", payload)

    def test_issue_gets_the_operation_id_and_both_pdfs(self):
        r = self.issue()
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(d["reference_number"], "OPS-001/26/2710/00001")
        self.assertEqual((d["letterhead_template_code"], d["title"], d["has_print_file"]), ("OPS-001", "Work Order", True))
        for variant in ("", "?variant=print"):
            resp = self.api_staff.get(f"{API}/issued-documents/{d['id']}/download/{variant}")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(b"".join(resp.streaming_content).startswith(b"%PDF"))

    def test_each_operation_counts_on_its_own_and_restarts_each_year(self):
        refs = lambda tpl, date: self.issue(tpl, issue_date=date).json()["reference_number"]
        self.assertEqual(refs("OPS-001", "2026-10-27"), "OPS-001/26/2710/00001")
        self.assertEqual(refs("OPS-001", "2026-10-28"), "OPS-001/26/2810/00002")
        self.assertEqual(refs("OPS-005", "2026-10-28"), "OPS-005/26/2810/00001")
        self.assertEqual(refs("OPS-001", "2027-01-04"), "OPS-001/27/0401/00001")

    def test_operation_documents_need_an_operation_and_letters_need_a_letter_template(self):
        self.assertEqual(self.issue(template="").status_code, 400)
        self.assertEqual(self.issue(template="LHT-002").status_code, 400)
        letter = self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", letterhead_template="OPS-001", content={"paragraphs": ["x"]}))
        self.assertEqual(letter.status_code, 400)
        self.assertEqual(self.issue(template="OPS-099").status_code, 400)
        self.assertEqual(IssuedDocument.objects.count(), 0)

    def test_a_switched_off_operation_cannot_be_used(self):
        t = LetterheadTemplate.objects.get(code="OPS-004")
        self.assertEqual(self.api.post(f"{API}/letterhead-templates/{t.id}/deactivate/", {"change_reason": "Not used"}).status_code, 200)
        self.assertEqual(self.issue("OPS-004").status_code, 400)

    def test_content_is_validated(self):
        self.assertEqual(self.issue(content={}).status_code, 400)
        self.assertEqual(self.issue(content={"sections": [{"heading": "", "body": ""}]}).status_code, 400)
        self.assertEqual(self.issue(content={"sections": "x"}).status_code, 400)
        self.assertEqual(self.issue(content={"sections": [{"heading": "h", "body": "b" * 20001}]}).status_code, 400)
        self.assertEqual(self.issue(content={"paragraphs": ["x"], "meta": [["Only label"]]}).status_code, 400)
        self.assertEqual(self.issue(content={"paragraphs": ["Pasted text only"]}).status_code, 201)

    def test_details_and_title_are_printed(self):
        d = self.issue(title="Work Order - Site A", content={
            "meta": [["Project", "Alpha"], ["Location", "Chennai"]],
            "paragraphs": ["Intro line."],
            "sections": [{"heading": "Materials", "body": "Cable\nPipes"}, {"heading": "Safety", "body": "Wear PPE"}]}).json()
        self.assertEqual(d["title"], "Work Order - Site A")
        if HAS_FITZ:
            doc = IssuedDocument.objects.get(pk=d["id"])
            for variant in (None, "print"):
                text = pdf_text(doc, variant)
                for needle in ("OPS-001/26/2710/00001", "Project", "Alpha", "Materials", "Cable", "Pipes", "Safety", "Wear PPE"):
                    self.assertIn(needle, text, (variant, needle))
            self.assertLess(text.index("Intro line."), text.index("Materials"))

    def test_long_sections_flow_onto_more_pages_without_error(self):
        body = "\n\n".join(f"Paragraph {i} " + "word " * 80 for i in range(30))
        r = self.issue(content={"sections": [{"heading": f"Part {n}", "body": body} for n in range(3)]})
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(self.api_staff.get(f"{API}/issued-documents/{d['id']}/download/?variant=print").status_code, 200)

    def test_operations_filter_in_the_issued_list(self):
        a = self.issue("OPS-001").json()
        self.issue("OPS-002")
        rows = self.api_staff.get(f"{API}/issued-documents/", {"document_type": "operation_document", "template": "ops-001"}).json()
        rows = rows["results"] if isinstance(rows, dict) else rows
        self.assertEqual([r["id"] for r in rows], [a["id"]])
