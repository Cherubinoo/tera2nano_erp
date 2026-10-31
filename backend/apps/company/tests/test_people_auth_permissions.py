import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.company.models import AuthorizedSignatory, CompanyPartner, CompanyProfileAuditEvent as Event

from .base import API, PASSWORD, CompanyTestMixin, png_bytes


class AuthTests(CompanyTestMixin, TestCase):
    def test_login_me_logout(self):
        c = APIClient()
        self.assertEqual(c.post("/api/v1/auth/login/", {"username": "admin", "password": "wrong"}).status_code, 400)
        r = c.post("/api/v1/auth/login/", {"username": "admin", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        c.credentials(HTTP_AUTHORIZATION=f"Token {r.json()['token']}")
        me = c.get("/api/v1/auth/me/").json()
        self.assertEqual((me["username"], me["role"], me["is_company_admin"]), ("admin", "admin", True))
        self.assertEqual(c.post("/api/v1/auth/logout/").status_code, 204)
        self.assertEqual(c.get("/api/v1/auth/me/").status_code, 401)

    def test_roles_are_reported_correctly(self):
        for client, expect in ((self.api_finance, (False, True)), (self.api_staff, (False, False))):
            me = client.get("/api/v1/auth/me/").json()
            self.assertEqual((me["is_company_admin"], me["is_finance"]), expect)

    def test_inactive_user_cannot_log_in(self):
        self.staff.is_active = False
        self.staff.save()
        r = APIClient().post("/api/v1/auth/login/", {"username": "staff", "password": PASSWORD})
        self.assertEqual(r.status_code, 400)

    def test_every_company_endpoint_requires_authentication(self):
        self.start_company()
        for path in ("profile/", "addresses/", "partners/", "signatories/", "contacts/", "tax-registrations/",
                     "bank-accounts/", "branding/", "document-numbering/", "financial-years/", "policies/", "documents/",
                     "issued-documents/", "audit-history/", "overview/", "lookups/", "tax-settings/", "setup/"):
            self.assertEqual(self.anon.get(f"{API}/{path}").status_code, 401, path)


class PartnerSignatoryTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def partner(self, **over):
        payload = dict(full_name="Asha Rao", designation="Designated Partner", partner_type="designated_partner",
                       is_designated_partner=True, dpin="01234567", official_email="asha@example.com",
                       official_phone="+91 98000 00000", appointment_date="2024-01-15")
        payload.update(over)
        r = self.api.post(f"{API}/partners/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_sensitive_partner_fields_are_admin_only(self):
        p = self.partner()
        admin_view = self.api.get(f"{API}/partners/{p['id']}/").json()
        self.assertEqual(admin_view["dpin"], "01234567")
        for client in (self.api_staff, self.api_finance):
            row = client.get(f"{API}/partners/").json()[0]
            self.assertEqual(row["full_name"], "Asha Rao")
            for hidden in ("dpin", "official_email", "official_phone", "has_signature"):
                self.assertNotIn(hidden, row)

    def test_partner_validation(self):
        self.assertEqual(self.api.post(f"{API}/partners/", dict(full_name="X", dpin="123")).status_code, 400)
        r = self.api.post(f"{API}/partners/", dict(full_name="X", appointment_date="2024-05-01", cessation_date="2024-01-01"))
        self.assertEqual(r.status_code, 400)
        r = self.api.post(f"{API}/partners/", dict(full_name="X", cessation_date="2020-01-01", is_active=True))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api_staff.post(f"{API}/partners/", dict(full_name="X")).status_code, 403)

    def test_cessation_deactivates_partner(self):
        p = self.partner()
        past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        r = self.api.patch(f"{API}/partners/{p['id']}/", {"cessation_date": past})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()["is_active"])

    def test_signature_image_is_protected(self):
        p = self.partner()
        url = f"{API}/partners/{p['id']}/signature/"
        up = lambda c: c.post(url, {"file": SimpleUploadedFile("s.png", png_bytes())}, format="multipart")
        self.assertEqual(up(self.api_staff).status_code, 403)
        self.assertEqual(up(self.api_finance).status_code, 403)
        self.assertEqual(up(self.api).status_code, 200)
        for c in (self.api_staff, self.api_finance, self.anon):
            self.assertIn(c.get(url).status_code, (401, 403))
        r = self.api.get(url)
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))
        self.assertEqual(b"".join(r.streaming_content)[:4], b"\x89PNG")  # also releases the handle on Windows
        self.assertTrue(Event.objects.filter(action="revealed", entity_type="partner").exists())
        self.assertEqual(self.api.get(f"{API}/partners/{p['id']}/").json()["has_signature"], True)
        bad = self.api.post(url, {"file": SimpleUploadedFile("s.png", b"not an image")}, format="multipart")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.api.delete(url).status_code, 200)
        self.assertFalse(CompanyPartner.objects.get().signature_image)

    def test_signatory_rules(self):
        base = dict(name="Asha Rao", designation="Partner", authorization_type="specific_documents")
        self.assertEqual(self.api.post(f"{API}/signatories/", base).status_code, 400)  # needs document types
        r = self.api.post(f"{API}/signatories/", {**base, "authorized_document_types": ["invoice"], "valid_from": "2026-01-01",
                                                  "valid_to": "2025-01-01"})
        self.assertEqual(r.status_code, 400)
        r = self.api.post(f"{API}/signatories/", {**base, "authorized_document_types": ["invoice"], "status": "active"})
        self.assertEqual(r.status_code, 400)  # active needs a start date
        r = self.api.post(f"{API}/signatories/", {**base, "authorized_document_types": ["invoice"], "status": "active",
                                                  "valid_from": "2026-01-01", "valid_to": "2026-12-31"})
        self.assertEqual(r.status_code, 201, r.content)

    def test_authorization_window_and_scope(self):
        s = AuthorizedSignatory(authorization_type="specific_documents", authorized_document_types=["invoice"],
                                valid_from=datetime.date(2026, 1, 1), valid_to=datetime.date(2026, 12, 31), status="active")
        self.assertTrue(s.authorizes("invoice", datetime.date(2026, 6, 1)))
        self.assertFalse(s.authorizes("quotation", datetime.date(2026, 6, 1)))
        self.assertFalse(s.authorizes("invoice", datetime.date(2027, 1, 1)))
        self.assertFalse(s.authorizes("invoice", datetime.date(2025, 12, 31)))
        s.status = "revoked"
        self.assertFalse(s.authorizes("invoice", datetime.date(2026, 6, 1)))
        s.status, s.authorization_type = "active", "all_documents"
        self.assertTrue(s.authorizes("anything", datetime.date(2026, 6, 1)))

    def test_non_admins_only_see_active_signatories_without_references(self):
        self.api.post(f"{API}/signatories/", dict(name="Draft", authorization_type="all_documents"))
        self.api.post(f"{API}/signatories/", dict(name="Live", authorization_type="all_documents", status="active",
                                                  valid_from="2026-01-01", authorization_reference="Resolution 7"))
        self.assertEqual({s["name"] for s in self.api.get(f"{API}/signatories/").json()}, {"Draft", "Live"})
        rows = self.api_staff.get(f"{API}/signatories/").json()
        self.assertEqual([s["name"] for s in rows], ["Live"])
        self.assertNotIn("authorization_reference", rows[0])

    def test_signatory_on_a_document_is_not_an_approval_record(self):
        """The signature asset is optional and off by default; generation records the user, not the signatory."""
        self.full_company_for_signature()
        d = self.issue("business_letter").json()
        self.assertEqual(d["generated_by_name"], "finance")
        branding = self.api.get(f"{API}/branding/").json()
        self.assertFalse(branding["signature_block"]["show_signature_image"])

    def full_company_for_signature(self):
        self.add_address()
        self.add_financial_year()
        self.seed_numbering()

    def test_signatory_deactivate_revokes(self):
        s = self.api.post(f"{API}/signatories/", dict(name="Live", authorization_type="all_documents", status="active",
                                                      valid_from="2026-01-01")).json()
        r = self.api.post(f"{API}/signatories/{s['id']}/deactivate/", {"change_reason": "Left the firm"})
        self.assertEqual(r.json()["status"], "revoked")


class PermissionMatrixTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def test_staff_read_only_everywhere_in_the_company_module(self):
        writes = [
            ("post", "addresses/"), ("post", "contacts/"), ("post", "tax-registrations/"), ("post", "bank-accounts/"),
            ("post", "financial-years/"), ("post", "policies/"), ("post", "documents/"), ("post", "document-numbering/"),
            ("patch", "profile/"), ("patch", "branding/"), ("patch", "tax-settings/"), ("post", "setup/complete/"),
            ("post", "document-numbering/seed/"), ("post", "branding/preview/"),
        ]
        for method, path in writes:
            self.assertEqual(getattr(self.api_staff, method)(f"{API}/{path}", {}).status_code, 403, (method, path))

    def test_finance_can_manage_bank_and_documents_but_not_company_identity(self):
        self.assertEqual(self.api_finance.patch(f"{API}/profile/", {"industry": "x"}).status_code, 403)
        self.assertEqual(self.api_finance.post(f"{API}/addresses/", {}).status_code, 403)
        self.assertEqual(self.api_finance.patch(f"{API}/branding/", {}).status_code, 403)
        self.assertEqual(self.add_bank().status_code, 201)
        self.assertEqual(self.upload_document(client=self.api_finance).status_code, 201)

    def test_numbers_cannot_be_allocated_through_the_api_directly(self):
        self.assertEqual(self.api.post(f"{API}/document-numbering/allocate/", {}).status_code, 404)

    def test_overview_shows_attention_items_by_role(self):
        bank = self.add_bank().json()
        over = lambda c: c.get(f"{API}/overview/").json()
        kinds = lambda c: {a["kind"] for a in over(c)["attention"]}
        self.assertIn("bank_unverified", kinds(self.api_finance))
        self.assertNotIn("bank_unverified", kinds(self.api_staff))
        self.assertIn("checklist", over(self.api_staff))
        self.assertEqual(bank["account_number_masked"], "XXXXXX9012")

    def test_expiring_documents_flagged_in_overview(self):
        soon = (datetime.date.today() + datetime.timedelta(days=20)).isoformat()
        self.upload_document(title="Insurance", document_type="insurance", expiry_date=soon, access_classification="internal")
        attention = self.api_staff.get(f"{API}/overview/").json()["attention"]
        self.assertTrue(any(a["kind"] == "document_expiry" and "Insurance" in a["message"] for a in attention))

    def test_lookups_available_to_everyone_signed_in(self):
        body = self.api_staff.get(f"{API}/lookups/").json()
        self.assertEqual(len(body["document_types"]), 18)
        self.assertIn("29 - Karnataka", {s["label"] for s in body["gst_states"]})
