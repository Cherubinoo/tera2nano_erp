import hashlib
import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings

from apps.company.models import (
    CompanyAddress, CompanyDocument, CompanyProfile, IssuedDocument, IssuedReferenceNumber,
)

from .base import API, CompanyTestMixin, pdf_stub, png_bytes


class DocumentRepositoryTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def test_upload_records_metadata_and_checksum(self):
        r = self.upload_document()
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body["checksum_sha256"], hashlib.sha256(pdf_stub()).hexdigest())
        self.assertEqual((body["mime_type"], body["version"], body["file_size"]), ("application/pdf", 1, len(pdf_stub())))
        doc = CompanyDocument.objects.get()
        self.assertEqual(doc.uploaded_by, self.admin)
        self.assertEqual(doc.original_filename, "proof.pdf")
        self.assertNotIn("proof", doc.file.name)  # stored under an unguessable name

    def test_content_is_sniffed_not_trusted(self):
        bad = SimpleUploadedFile("evil.pdf", b"<html><script>alert(1)</script></html>", content_type="application/pdf")
        r = self.api.post(f"{API}/documents/", dict(document_type="other", title="x", file=bad), format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(CompanyDocument.objects.count(), 0)

    @override_settings(MAX_UPLOAD_BYTES=100)
    def test_size_limit(self):
        big = SimpleUploadedFile("big.pdf", pdf_stub() + b"0" * 200, content_type="application/pdf")
        r = self.api.post(f"{API}/documents/", dict(document_type="other", title="x", file=big), format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_storage_has_no_public_url(self):
        self.upload_document()
        doc = CompanyDocument.objects.get()
        with self.assertRaises(ValueError):
            doc.file.url
        self.assertNotIn("file", self.api.get(f"{API}/documents/").json()[0])  # no path or URL in the API

    def test_download_requires_authentication_and_returns_exact_bytes(self):
        doc = self.upload_document(access_classification="internal").json()
        self.assertEqual(self.anon.get(f"{API}/documents/{doc['id']}/download/").status_code, 401)
        r = self.api_staff.get(f"{API}/documents/{doc['id']}/download/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), pdf_stub())
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(r["Cache-Control"], "private, no-store")

    def test_classification_controls_visibility_and_download(self):
        internal = self.upload_document(title="I", access_classification="internal").json()
        conf = self.upload_document(title="C", access_classification="confidential").json()
        restricted = self.upload_document(title="R", access_classification="restricted").json()
        titles = lambda c: sorted(d["title"] for d in c.get(f"{API}/documents/").json())
        self.assertEqual(titles(self.api), ["C", "I", "R"])
        self.assertEqual(titles(self.api_finance), ["C", "I"])
        self.assertEqual(titles(self.api_staff), ["I"])
        self.assertEqual(self.api_staff.get(f"{API}/documents/{conf['id']}/download/").status_code, 404)
        self.assertEqual(self.api_finance.get(f"{API}/documents/{restricted['id']}/download/").status_code, 404)
        self.assertEqual(self.api_finance.get(f"{API}/documents/{conf['id']}/download/").status_code, 200)

    def test_only_admins_store_restricted_and_staff_cannot_upload(self):
        self.assertEqual(self.upload_document(client=self.api_finance, access_classification="restricted").status_code, 403)
        self.assertEqual(self.upload_document(client=self.api_staff).status_code, 403)
        self.assertEqual(self.upload_document(client=self.anon).status_code, 401)

    def test_new_version_keeps_history(self):
        v1 = self.upload_document(title="GST certificate", document_type="gst_certificate").json()
        v2 = self.upload_document(title="GST certificate", document_type="gst_certificate", replaces=v1["id"]).json()
        self.assertEqual((v2["version"], v2["document_group"]), (2, v1["document_group"]))
        current = self.api.get(f"{API}/documents/").json()
        self.assertEqual([d["id"] for d in current], [v2["id"]])
        history = self.api.get(f"{API}/documents/?history=true&group={v1['document_group']}").json()
        self.assertEqual(sorted(d["version"] for d in history), [1, 2])
        # an old version can no longer be replaced again
        self.assertEqual(self.upload_document(replaces=v1["id"]).status_code, 400)

    def test_downloads_of_confidential_documents_are_audited(self):
        from apps.company.models import CompanyProfileAuditEvent as E
        doc = self.upload_document().json()
        self.api.get(f"{API}/documents/{doc['id']}/download/")
        self.assertTrue(E.objects.filter(action="downloaded", entity_id=doc["id"]).exists())

    def test_document_referenced_by_a_registration_cannot_be_deleted(self):
        doc = self.upload_document(document_type="incorporation_certificate").json()
        self.api.patch(f"{API}/profile/", {"incorporation_certificate": doc["id"]})
        self.assertEqual(self.api.delete(f"{API}/documents/{doc['id']}/").status_code, 409)
        self.assertTrue(CompanyDocument.objects.filter(pk=doc["id"]).exists())
        other = self.upload_document(title="unused").json()
        self.assertEqual(self.api.delete(f"{API}/documents/{other['id']}/").status_code, 204)

    def test_wrong_document_type_for_incorporation_certificate_rejected(self):
        doc = self.upload_document(document_type="pan").json()
        self.assertEqual(self.api.patch(f"{API}/profile/", {"incorporation_certificate": doc["id"]}).status_code, 400)

    def test_images_for_logo_must_be_png_or_jpeg(self):
        r = self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.pdf", pdf_stub())}, format="multipart")
        self.assertEqual(r.status_code, 400)
        r = self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")
        self.assertEqual(r.status_code, 200, r.content)
        got = self.api_staff.get(f"{API}/profile/logo/")
        self.assertEqual(got["Content-Type"], "image/png")
        self.assertEqual(self.anon.get(f"{API}/profile/logo/").status_code, 401)
        self.assertEqual(self.api_staff.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())},
                                             format="multipart").status_code, 403)


class IssuedDocumentTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")

    def body(self, resp):
        return b"".join(resp.streaming_content)

    def test_issue_stores_pdf_snapshot_and_metadata(self):
        r = self.issue("business_letter", recipient=["Acme"], subject="Hello", paragraphs=["Body text"])
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(d["reference_number"], "T2N/LTR/26/1508/00001")
        doc = IssuedDocument.objects.get()
        self.assertEqual(doc.generated_by, self.finance)
        self.assertIsNotNone(doc.generated_at)
        self.assertEqual(doc.template_version, "1.0")
        self.assertEqual(doc.branding_version, 1)
        self.assertEqual(doc.policy_version, 1)
        self.assertGreater(doc.profile_version, 1)
        self.assertEqual(doc.snapshot["company"]["display_name"], "Example Ventures")
        self.assertEqual(doc.snapshot["address"]["state_code"], "29")
        self.assertTrue(doc.logo_snapshot)
        pdf = self.body(self.api.get(f"{API}/issued-documents/{d['id']}/download/"))
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(hashlib.sha256(pdf).hexdigest(), doc.checksum_sha256)
        self.assertEqual(doc.reference.reference_number, doc.reference_number)

    def test_every_printable_document_type_renders(self):
        for t in ("quotation", "invoice", "credit_note", "meeting_minutes", "business_letter", "offer_letter",
                  "appointment_letter", "experience_letter", "handover_letter", "completion_certificate"):
            r = self.issue(t, numbered=False) if False else self.api_finance.post(f"{API}/issued-documents/", dict(
                document_type=t, numbered=False, content={"paragraphs": ["x"]}, issue_date="2026-08-15"))
            self.assertEqual(r.status_code, 201, (t, r.content))

    def test_table_totals_terms_and_bank_render(self):
        content = {"table": {"columns": ["Item", "Amount"], "numeric_columns": [1], "rows": [["Service", "1,000.00"]]},
                   "totals": [["Total", "1,000.00"]], "meta": [["Due date", "30 Sep 2026"]]}
        self.assertEqual(self.issue("invoice", **content).status_code, 201)

    def test_unsupported_characters_do_not_break_rendering(self):
        self.assertEqual(self.issue("business_letter", paragraphs=["Rate ₹500 — 你好 <b>bold</b> & more"]).status_code, 201)

    def test_content_validation(self):
        bad = self.api_finance.post(f"{API}/issued-documents/", dict(document_type="business_letter",
                                    content={"table": {"columns": ["a"], "rows": [["1", "2"]]}}))
        self.assertEqual(bad.status_code, 400)
        bad = self.api_finance.post(f"{API}/issued-documents/", dict(document_type="business_letter", content={"evil": 1}))
        self.assertEqual(bad.status_code, 400)
        bad = self.api_finance.post(f"{API}/issued-documents/", dict(document_type="project", content={}))
        self.assertEqual(bad.status_code, 400)

    def test_only_finance_issues_invoices_but_staff_can_issue_letters(self):
        self.assertEqual(self.issue("invoice", client=self.api_staff).status_code, 403)
        self.assertEqual(self.issue("business_letter", client=self.api_staff).status_code, 201)
        self.assertEqual(self.issue("invoice", client=self.anon).status_code, 401)

    def test_failed_render_does_not_consume_a_number(self):
        from unittest import mock
        with mock.patch("apps.company.services.pdf.render_pdf", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.issue("business_letter")
        self.assertEqual(IssuedReferenceNumber.objects.count(), 0)
        self.assertEqual(self.issue("business_letter").json()["reference_number"], "T2N/LTR/26/1508/00001")

    def test_issued_documents_keep_their_history_when_company_details_change(self):
        first = self.issue("invoice", paragraphs=["Original"]).json()
        original_pdf = self.body(self.api.get(f"{API}/issued-documents/{first['id']}/download/"))
        original_snapshot = self.api.get(f"{API}/issued-documents/{first['id']}/snapshot/").json()

        # change name, address, logo and letterhead afterwards
        self.assertEqual(self.api.patch(f"{API}/profile/", {"legal_name": "Renamed LLP", "display_name": "Renamed",
                                                              "change_reason": "Name change"}).status_code, 200)
        addr = CompanyAddress.objects.get()
        self.assertEqual(self.api.patch(f"{API}/addresses/{addr.id}/", {"line1": "99 New Street",
                                                                          "change_reason": "Relocated"}).status_code, 200)
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("n.png", png_bytes(colour=(200, 0, 0)))},
                      format="multipart")
        self.assertEqual(self.api.patch(f"{API}/branding/", {"primary_colour": "#AA0000"}).status_code, 200)

        again = self.body(self.api.get(f"{API}/issued-documents/{first['id']}/download/"))
        self.assertEqual(again, original_pdf)  # the exact file is preserved
        self.assertEqual(self.api.get(f"{API}/issued-documents/{first['id']}/snapshot/").json(), original_snapshot)
        self.assertEqual(original_snapshot["company"]["display_name"], "Example Ventures")
        self.assertEqual(original_snapshot["branding"]["primary_colour"], "#1F3A5F")

        # a newly generated document reflects the new settings
        second = self.issue("invoice", paragraphs=["New"]).json()
        snap = self.api.get(f"{API}/issued-documents/{second['id']}/snapshot/").json()
        self.assertEqual(snap["company"]["display_name"], "Renamed")
        self.assertIn("99 New Street", snap["address"]["lines"][0])
        self.assertEqual(snap["branding"]["primary_colour"], "#AA0000")
        self.assertEqual(snap["branding"]["version"], 2)
        self.assertGreater(second["profile_version"], first["profile_version"])
        self.assertEqual(second["branding_version"], 2)

    def test_address_used_by_an_issued_document_cannot_be_deleted(self):
        self.issue("invoice")
        addr = CompanyAddress.objects.get()
        r = self.api.delete(f"{API}/addresses/{addr.id}/")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "protected")
        self.assertTrue(CompanyAddress.objects.filter(pk=addr.pk).exists())
        self.assertEqual(self.api.post(f"{API}/addresses/{addr.id}/deactivate/", {"change_reason": "x"}).status_code, 200)

    def test_issued_documents_are_immutable(self):
        self.issue("business_letter")
        doc = IssuedDocument.objects.get()
        doc.title = "Tampered"
        with self.assertRaises(ValidationError):
            doc.save()
        with self.assertRaises(ValidationError):
            doc.delete()
        self.assertEqual(self.api.delete(f"{API}/issued-documents/{doc.id}/").status_code, 405)
        self.assertEqual(self.api.patch(f"{API}/issued-documents/{doc.id}/", {"title": "x"}).status_code, 405)

    def test_void_keeps_the_document_and_never_reuses_the_number(self):
        d = self.issue("business_letter").json()
        self.assertEqual(self.api.post(f"{API}/issued-documents/{d['id']}/void/", {}).status_code, 400)
        self.assertEqual(self.api_finance.post(f"{API}/issued-documents/{d['id']}/void/", {"change_reason": "x"}).status_code, 403)
        r = self.api.post(f"{API}/issued-documents/{d['id']}/void/", {"change_reason": "Issued in error"})
        self.assertEqual(r.json()["status"], "void")
        self.assertEqual(IssuedReferenceNumber.objects.get().status, "void")
        self.assertEqual(self.issue("business_letter").json()["reference_number"], "T2N/LTR/26/1508/00002")

    def test_revision_supersedes_without_altering_the_original_file(self):
        first = self.issue("quotation").json()
        before = self.body(self.api.get(f"{API}/issued-documents/{first['id']}/download/"))
        r = self.api_finance.post(f"{API}/issued-documents/", dict(
            document_type="quotation", content={"paragraphs": ["Revised"]}, supersedes=first["id"], issue_date="2026-08-20"))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.api.get(f"{API}/issued-documents/{first['id']}/").json()["status"], "superseded")
        self.assertEqual(self.body(self.api.get(f"{API}/issued-documents/{first['id']}/download/")), before)

    def test_bank_details_print_only_when_verified_and_enabled_and_never_in_full(self):
        bank = self.add_bank(number="123456789012").json()
        doc = self.upload_document(client=self.api_finance, bank_account=bank["id"]).json()
        self.api_finance.patch(f"{API}/bank-accounts/{bank['id']}/", {"supporting_document": doc["id"]})
        snap = lambda: self.api.get(f"{API}/issued-documents/{self.issue('invoice').json()['id']}/snapshot/").json()
        self.assertIsNone(snap()["bank"])  # unverified
        self.api_admin2.post(f"{API}/bank-accounts/{bank['id']}/verify/", {})
        self.assertIsNone(snap()["bank"])  # verified but not enabled for documents
        self.api_finance.patch(f"{API}/bank-accounts/{bank['id']}/", {"show_on_invoices": True, "change_reason": "Enable on invoices"})
        bank_snap = snap()["bank"]
        self.assertEqual(bank_snap["account_number_masked"], "XXXXXX9012")
        self.assertNotIn("123456789012", str(bank_snap))
        self.assertIsNone(self.api_finance.get(f"{API}/resolve/?document_type=quotation").json()["bank"])

    def test_gstin_prints_only_for_registered_verified_registration(self):
        from .base import GSTIN, PAN
        self.api.patch(f"{API}/profile/", {"pan": PAN, "gst_registration_status": "registered", "gstin": GSTIN,
                                           "change_reason": "Registered"})
        snap = lambda: self.api.get(f"{API}/resolve/?document_type=invoice").json()["tax"]["gstin"]
        self.assertEqual(snap(), "")  # a GSTIN on the profile alone is not enough
        doc = self.upload_document(document_type="gst_certificate").json()
        reg = self.api.post(f"{API}/tax-registrations/", dict(registration_type="gstin", registration_number=GSTIN,
                                                               supporting_document=doc["id"]))
        self.assertEqual(reg.status_code, 201, reg.content)
        self.assertEqual(snap(), "")  # still unverified
        self.assertEqual(self.api.post(f"{API}/tax-registrations/{reg.json()['id']}/verify/", {}).status_code, 200)
        self.assertEqual(snap(), GSTIN)

    def test_resolve_endpoint_returns_company_details_for_a_document_type(self):
        r = self.api_staff.get(f"{API}/resolve/?document_type=invoice").json()
        self.assertEqual(r["company"]["display_name"], "Example Ventures")
        self.assertEqual(r["contact"]["type"], "finance")
        self.assertEqual(self.api_staff.get(f"{API}/resolve/?document_type=nope").status_code, 400)
