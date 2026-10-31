from io import BytesIO, StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase

from apps.company.models import (
    CompanyAddress, CompanyBranding, CompanyDocument, CompanyProfile, CompanyProfileAuditEvent, CompanyTaxRegistration,
    IssuedDocument,
)

from .base import API, CompanyTestMixin, pdf_stub, png_bytes


class FolderPerDocumentTypeTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()

    def test_issued_documents_are_stored_in_a_folder_per_document_type(self):
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")
        for doc_type in ("business_letter", "meeting_minutes", "offer_letter"):
            self.assertEqual(self.api_finance.post(f"{API}/issued-documents/", dict(
                document_type=doc_type, numbered=False, content={"paragraphs": ["x"]}, issue_date="2026-08-15")).status_code, 201)
        for doc in IssuedDocument.objects.all():
            self.assertTrue(doc.file.name.startswith(f"company/issued/{doc.document_type}/"), doc.file.name)
            self.assertTrue(doc.logo_snapshot.name.startswith(f"company/issued/{doc.document_type}/logos/"))
        self.assertEqual(len({d.file.name.split("/")[2] for d in IssuedDocument.objects.all()}), 3)

    def test_repository_documents_are_stored_in_a_folder_per_document_type(self):
        for kind in ("pan", "gst_certificate"):
            data = dict(document_type=kind, title=kind, file=SimpleUploadedFile("a.pdf", pdf_stub()))
            self.assertEqual(self.api.post(f"{API}/documents/", data, format="multipart").status_code, 201)
        names = {d.document_type: d.file.name for d in CompanyDocument.objects.all()}
        self.assertTrue(names["pan"].startswith("company/documents/pan/"))
        self.assertTrue(names["gst_certificate"].startswith("company/documents/gst_certificate/"))


class LetterheadTemplateTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.api.patch(f"{API}/profile/", {"primary_phone": "+91 8220789878", "primary_email": "admin@example.com",
                                            "website": "https://www.example.com", "change_reason": "contacts"})
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")

    def brand(self, **over):
        payload = dict(default_template="footer_contact", phone_label="Mob", page_background_colour="#F5F5F3",
                       watermark_enabled=True, watermark_type="logo", watermark_size_mm=100, watermark_opacity="0.25")
        payload.update(over)
        r = self.api.patch(f"{API}/branding/", payload)
        self.assertEqual(r.status_code, 200, r.content)

    def test_footer_contact_letterhead_is_saved_and_frozen_into_the_snapshot(self):
        self.brand()
        d = self.api_finance.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", content={"paragraphs": ["Body"]}, issue_date="2026-08-15")).json()
        snap = self.api.get(f"{API}/issued-documents/{d['id']}/snapshot/").json()
        b = snap["branding"]
        self.assertEqual((b["template"], b["phone_label"], b["page_background_colour"], b["watermark_type"]),
                         ("footer_contact", "Mob", "#F5F5F3", "logo"))
        self.assertEqual(snap["address"]["single_line"], "12 Industrial Road, Bengaluru - 560001, Karnataka, India")
        # a later change does not touch the issued record
        self.brand(page_background_colour="#FFFFFF", phone_label="Tel")
        again = self.api.get(f"{API}/issued-documents/{d['id']}/snapshot/").json()["branding"]
        self.assertEqual((again["page_background_colour"], again["phone_label"]), ("#F5F5F3", "Mob"))

    def test_all_watermark_and_background_combinations_render(self):
        for extra in ({}, {"watermark_type": "text", "watermark_text": "DRAFT"}, {"watermark_enabled": False},
                      {"page_background_colour": ""}, {"default_template": "standard"}, {"default_template": "compact"}):
            self.brand(**extra)
            r = self.api.post(f"{API}/branding/preview/", {})
            self.assertEqual(r.status_code, 200, (extra, r.content))
            self.assertTrue(r.content.startswith(b"%PDF"))

    def test_logo_watermark_without_logo_does_not_fail(self):
        self.api.delete(f"{API}/profile/logo/")
        self.brand()
        self.assertEqual(self.api.post(f"{API}/branding/preview/", {}).status_code, 200)

    def test_validation_of_new_fields(self):
        for bad in ({"page_background_colour": "grey"}, {"watermark_type": "sticker"}, {"watermark_size_mm": 5},
                    {"phone_label": "x" * 20}):
            self.assertEqual(self.api.patch(f"{API}/branding/", bad).status_code, 400, bad)
        # logo watermarks need no text; text watermarks still do
        self.brand(watermark_text="")
        self.assertEqual(self.api.patch(f"{API}/branding/", {"watermark_type": "text", "watermark_text": ""}).status_code, 400)


class LogoPreparationTests(TestCase):
    def img(self, bg, fg, size=(200, 120)):
        from PIL import Image, ImageDraw
        im = Image.new("RGB", size, bg)
        ImageDraw.Draw(im).rectangle([60, 40, 140, 80], fill=fg)
        buf = BytesIO(); im.save(buf, "PNG"); return buf.getvalue()

    def prepared(self, data):
        from PIL import Image
        from apps.core.images import prepare_logo
        return Image.open(BytesIO(prepare_logo(data))).convert("RGBA")

    def test_white_background_becomes_transparent_and_artwork_is_cropped(self):
        out = self.prepared(self.img((255, 255, 255), (48, 67, 82)))
        self.assertEqual(out.getpixel((0, 0))[3], 0)
        self.assertLess(out.width, 120)  # 80px artwork plus padding, not the original 200px
        centre = out.getpixel((out.width // 2, out.height // 2))
        self.assertEqual(centre[:3], (48, 67, 82))
        self.assertEqual(centre[3], 255)

    def test_black_background_keeps_dark_artwork_opaque(self):
        out = self.prepared(self.img((0, 0, 0), (10, 40, 110)))
        self.assertEqual(out.getpixel((0, 0))[3], 0)
        centre = out.getpixel((out.width // 2, out.height // 2))
        self.assertEqual(centre[3], 255)
        self.assertEqual(centre[:3], (10, 40, 110))

    def test_existing_transparency_is_kept_and_large_images_are_capped(self):
        from PIL import Image
        im = Image.new("RGBA", (3000, 2000), (0, 0, 0, 0))
        im.paste((200, 0, 0, 255), (1000, 800, 2000, 1200))
        buf = BytesIO(); im.save(buf, "PNG")
        out = self.prepared(buf.getvalue())
        self.assertLessEqual(out.width, 1400)
        self.assertEqual(out.getpixel((out.width // 2, out.height // 2)), (200, 0, 0, 255))


class WatermarkImageTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")

    def test_dedicated_watermark_image_is_stored_used_and_frozen_with_the_document(self):
        up = self.api.post(f"{API}/branding/assets/watermark_image/",
                           {"file": SimpleUploadedFile("w.png", png_bytes(colour=(0, 100, 255)))}, format="multipart")
        self.assertEqual(up.status_code, 200, up.content)
        self.assertTrue(self.api.get(f"{API}/branding/").json()["has_watermark_image"])
        self.api.patch(f"{API}/branding/", {"watermark_enabled": True, "watermark_type": "logo", "watermark_opacity": "0.25"})
        d = self.api_finance.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", content={"paragraphs": ["x"]}, issue_date="2026-08-15")).json()
        doc = IssuedDocument.objects.get(pk=d["id"])
        self.assertTrue(doc.watermark_snapshot)
        self.assertTrue(doc.watermark_snapshot.name.startswith("company/issued/business_letter/logos/"))
        self.assertEqual(self.api_staff.get(f"{API}/branding/assets/watermark_image/")["Content-Type"], "image/png")
        self.assertEqual(self.api_staff.post(f"{API}/branding/assets/watermark_image/", {}).status_code, 403)

    def test_without_watermark_image_the_logo_is_used_and_nothing_extra_is_stored(self):
        self.api.patch(f"{API}/branding/", {"watermark_enabled": True, "watermark_type": "logo"})
        d = self.api_finance.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", content={"paragraphs": ["x"]}, issue_date="2026-08-15"))
        self.assertEqual(d.status_code, 201)

    def test_watermark_snapshot_is_skipped_when_the_watermark_is_off(self):
        d = self.api_finance.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", content={"paragraphs": ["x"]}, issue_date="2026-08-15")).json()
        self.assertFalse(IssuedDocument.objects.get(pk=d["id"]).watermark_snapshot)


class ApplyLetterheadCommandTests(CompanyTestMixin, TestCase):
    def run_cmd(self, *args):
        out = StringIO()
        call_command("apply_tera2nano_letterhead", *args, stdout=out)
        return out.getvalue()

    def test_creates_company_address_branding_without_inventing_registrations(self):
        self.run_cmd()
        c = CompanyProfile.objects.get()
        self.assertEqual((c.legal_name, c.primary_email, c.primary_phone), ("TERA2NANO SOLUTIONS LLP", "admin@tera2nano.com", "+91 8220789878"))
        a = CompanyAddress.objects.get()
        self.assertEqual((a.city, a.postal_code, a.state_code), ("Vellore", "632401", "33"))
        b = CompanyBranding.objects.get()
        self.assertEqual((b.default_template, b.phone_label, b.page_background_colour, b.watermark_type),
                         ("footer_contact", "Mob", "#F5F5F3", "logo"))
        for blank in ("pan", "tan", "llpin", "gstin"):
            self.assertEqual(getattr(c, blank), "")
        self.assertEqual(c.gst_registration_status, "not_declared")
        self.assertFalse(CompanyTaxRegistration.objects.exists())
        self.assertEqual(c.setup_status, "in_progress")  # the wizard can still be completed
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="branding", reason__contains="letterhead").exists())

    def test_adds_partners_and_contact_from_the_business_cards_only_once(self):
        from apps.company.models import CompanyContact, CompanyPartner
        self.run_cmd()
        self.run_cmd()
        self.assertEqual(CompanyContact.objects.count(), 1)
        partners = {p.full_name: p for p in CompanyPartner.objects.all()}
        self.assertEqual(set(partners), {"Delight Cherubino", "Sanjay"})
        self.assertEqual(partners["Sanjay"].designation, "Co-Founder & Technical Partner")
        for p in partners.values():  # both are confirmed designated partners; nothing else is invented
            self.assertEqual((p.partner_type, p.is_designated_partner), ("designated_partner", True))
            self.assertEqual((p.dpin, p.appointment_date), ("", None))

    def test_corrects_partners_added_earlier_as_ordinary_partners(self):
        from apps.company.models import CompanyPartner
        self.run_cmd()
        CompanyPartner.objects.update(partner_type="partner", is_designated_partner=False)
        out = self.run_cmd()
        self.assertIn("Marked Sanjay as a designated partner", out)
        self.assertTrue(all(p.is_designated_partner and p.partner_type == "designated_partner"
                            for p in CompanyPartner.objects.all()))
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(
            entity_type="partner", reason__contains="designated partner", action="updated").exists())
        self.assertEqual(CompanyPartner.objects.count(), 2)

    def test_prepares_logo_and_watermark_images_and_the_pdf_renders(self):
        import tempfile, os
        from PIL import Image, ImageDraw
        paths = []
        for name, bg in (("logo", (255, 255, 255)), ("wm", (0, 0, 0))):
            im = Image.new("RGB", (300, 200), bg)
            ImageDraw.Draw(im).rectangle([80, 60, 220, 140], fill=(48, 67, 82) if name == "logo" else (20, 80, 220))
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
                im.save(fh, "PNG"); paths.append(fh.name)
        try:
            self.run_cmd("--logo", paths[0], "--watermark", paths[1])
        finally:
            for p in paths:
                os.unlink(p)
        b = CompanyBranding.objects.get()
        self.assertTrue(b.watermark_image and CompanyProfile.objects.get().logo)
        from apps.company.services import company as cs, documents
        pdf = documents.preview_letterhead(cs.get_company(), b)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_is_idempotent_and_respects_customised_branding(self):
        self.run_cmd()
        self.run_cmd()
        self.assertEqual(CompanyAddress.objects.count(), 1)
        self.assertEqual(CompanyProfile.objects.count(), 1)
        v = CompanyBranding.objects.get().version
        b = CompanyBranding.objects.get()
        b.primary_colour = "#112233"
        b.save()
        self.assertIn("left unchanged", self.run_cmd())
        self.assertEqual(CompanyBranding.objects.get().primary_colour, "#112233")
        self.run_cmd("--force")
        self.assertEqual(CompanyBranding.objects.get().primary_colour, "#304352")
        self.assertEqual(CompanyBranding.objects.get().version, v + 1)

    def test_does_not_overwrite_existing_profile_values(self):
        self.start_company()
        self.api.patch(f"{API}/profile/", {"primary_email": "keep@example.com"})
        self.run_cmd()
        c = CompanyProfile.objects.get()
        self.assertEqual((c.legal_name, c.primary_email, c.primary_phone), ("Example Ventures LLP", "keep@example.com", "+91 8220789878"))

    def test_logo_option_stores_the_logo_privately(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
            fh.write(png_bytes())
        try:
            self.run_cmd("--logo", fh.name)
        finally:
            os.unlink(fh.name)
        c = CompanyProfile.objects.get()
        self.assertTrue(c.logo)
        with self.assertRaises(ValueError):
            c.logo.url
        self.assertEqual(len(c.logo_checksum), 64)
