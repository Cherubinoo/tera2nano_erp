import hashlib
import os
import sys

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.company.models import IssuedDocument

from .base import API, CompanyTestMixin, png_bytes


def pdf_has_images(data):
    """Plain PDFs carry no letterhead artwork; letterhead PDFs embed the logo as an image."""
    return b"/Subtype /Image" in data


class PrintVariantTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")
        self.api.patch(f"{API}/profile/", {"primary_phone": "+91 8220789878", "change_reason": "contacts"})
        self.api.patch(f"{API}/branding/", {"default_template": "footer_contact", "page_background_colour": "#F5F5F3",
                                            "watermark_enabled": True, "watermark_type": "logo"})

    def issue(self, **extra):
        payload = dict(document_type="business_letter", letterhead_template="LHT-002", issue_date="2026-10-27",
                       content={"recipient": ["Acme"], "subject": "Hello", "paragraphs": ["Body text"]})
        payload.update(extra)
        return self.api_finance.post(f"{API}/issued-documents/", payload)

    def body(self, resp):
        return b"".join(resp.streaming_content)

    def test_every_issued_document_has_both_versions_stored_with_checksums(self):
        d = self.issue().json()
        self.assertTrue(d["has_print_file"])
        doc = IssuedDocument.objects.get(pk=d["id"])
        self.assertTrue(doc.print_file.name.startswith("company/issued/business_letter/print/"))
        letterhead = self.body(self.api.get(f"{API}/issued-documents/{d['id']}/download/"))
        plain = self.body(self.api.get(f"{API}/issued-documents/{d['id']}/download/?variant=print"))
        self.assertTrue(letterhead.startswith(b"%PDF") and plain.startswith(b"%PDF"))
        self.assertEqual(hashlib.sha256(letterhead).hexdigest(), doc.checksum_sha256)
        self.assertEqual(hashlib.sha256(plain).hexdigest(), doc.print_checksum_sha256)
        self.assertNotEqual(letterhead, plain)

    def test_plain_version_leaves_out_the_letterhead_artwork(self):
        d = self.issue().json()
        letterhead = self.body(self.api.get(f"{API}/issued-documents/{d['id']}/download/"))
        plain = self.body(self.api.get(f"{API}/issued-documents/{d['id']}/download/?variant=print"))
        self.assertTrue(pdf_has_images(letterhead))   # logo and watermark
        self.assertFalse(pdf_has_images(plain))       # nothing printed over the pre-printed paper

    def test_download_filenames_distinguish_the_two_versions(self):
        d = self.issue().json()
        a = self.api.get(f"{API}/issued-documents/{d['id']}/download/")
        b = self.api.get(f"{API}/issued-documents/{d['id']}/download/?variant=print")
        self.assertIn("LHT-002-26-2710-00001.pdf", a["Content-Disposition"])
        self.assertIn("LHT-002-26-2710-00001-print.pdf", b["Content-Disposition"])
        self.body(a), self.body(b)

    def test_older_document_without_a_print_version_returns_404(self):
        d = self.issue().json()
        IssuedDocument.objects.filter(pk=d["id"]).update(print_file="")
        self.assertEqual(self.api.get(f"{API}/issued-documents/{d['id']}/download/?variant=print").status_code, 404)

    def test_print_version_requires_sign_in(self):
        d = self.issue().json()
        self.assertEqual(self.anon.get(f"{API}/issued-documents/{d['id']}/download/?variant=print").status_code, 401)

    def test_preview_plain_variant(self):
        letterhead = self.api.post(f"{API}/branding/preview/", {"variant": "letterhead"}).content
        plain = self.api.post(f"{API}/branding/preview/", {"variant": "plain"}).content
        self.assertTrue(pdf_has_images(letterhead))
        self.assertFalse(pdf_has_images(plain))
        self.assertEqual(self.api.post(f"{API}/branding/preview/", {"variant": "x"}).status_code, 400)

    def test_list_can_be_searched_by_reference_and_template(self):
        self.issue()
        self.issue(letterhead_template="LHT-003", content={"paragraphs": ["x"]})
        find = lambda q: [r["reference_number"] for r in self.api.get(f"{API}/issued-documents/?{q}").json()]
        self.assertEqual(find("template=LHT-003"), ["LHT-003/26/2710/00001"])
        self.assertEqual(find("q=00001").__len__(), 2)
        self.assertEqual(find("q=LHT-002"), ["LHT-002/26/2710/00001"])


class SpacingTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()

    def preview(self, **branding):
        r = self.api.post(f"{API}/branding/preview/", branding)
        self.assertEqual(r.status_code, 200, r.content)
        return r.content

    def test_spacing_settings_are_saved_validated_and_versioned(self):
        b = self.api.get(f"{API}/branding/").json()
        self.assertEqual((b["line_spacing"], b["paragraph_spacing_pt"], b["body_top_gap_mm"], b["justify_body"]),
                         ("1.40", 6, 3, False))
        r = self.api.patch(f"{API}/branding/", {"line_spacing": "1.60", "paragraph_spacing_pt": 10, "body_top_gap_mm": 8,
                                                "justify_body": True})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["version"], b["version"] + 1)
        for bad in ({"line_spacing": "0.50"}, {"line_spacing": "3.00"}, {"paragraph_spacing_pt": 90},
                    {"body_top_gap_mm": 99}):
            self.assertEqual(self.api.patch(f"{API}/branding/", bad).status_code, 400, bad)

    def test_spacing_changes_the_layout_and_is_frozen_with_the_document(self):
        tight = self.preview(line_spacing="1.00", paragraph_spacing_pt=0, body_top_gap_mm=0)
        loose = self.preview(line_spacing="2.00", paragraph_spacing_pt=20, body_top_gap_mm=20, justify_body=True)
        self.assertNotEqual(tight, loose)
        self.api.patch(f"{API}/branding/", {"line_spacing": "1.80", "justify_body": True})
        d = self.api_finance.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", issue_date="2026-10-27", content={"paragraphs": ["x"]})).json()
        snap = self.api.get(f"{API}/issued-documents/{d['id']}/snapshot/").json()["branding"]
        self.assertEqual((snap["line_spacing"], snap["justify_body"]), ("1.80", True))
        self.api.patch(f"{API}/branding/", {"line_spacing": "1.10"})
        again = self.api.get(f"{API}/issued-documents/{d['id']}/snapshot/").json()["branding"]
        self.assertEqual(again["line_spacing"], "1.80")  # issued documents keep their original spacing

    def test_older_snapshots_without_spacing_keys_still_render(self):
        from apps.company.services import company as cs, pdf
        c = cs.get_company()
        snap = cs.build_snapshot(cs.resolve_for_document("business_letter", None, c), "business_letter")
        for key in ("line_spacing", "paragraph_spacing_pt", "body_top_gap_mm", "justify_body"):
            snap["branding"].pop(key)
        import datetime
        out = pdf.render_pdf(snap, {"paragraphs": ["x"]}, title="Old", issue_date=datetime.date.today())
        self.assertTrue(out.startswith(b"%PDF"))
