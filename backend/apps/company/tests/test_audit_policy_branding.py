import datetime
import json

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, InternalError, connection, transaction
from django.db.utils import DatabaseError
from django.test import TestCase

from apps.company.models import CompanyPolicy, CompanyProfile, CompanyProfileAuditEvent as Event

from .base import API, CompanyTestMixin, png_bytes


class AuditTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def test_events_are_append_only_in_the_orm(self):
        e = Event.objects.first()
        e.reason = "tampered"
        with self.assertRaises(ValidationError):
            e.save()
        with self.assertRaises(ValidationError):
            e.delete()

    def test_database_blocks_update_and_delete_even_with_raw_sql(self):
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("update company_companyprofileauditevent set reason = 'x'")
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("delete from company_companyprofileauditevent")
        with self.assertRaises(DatabaseError), transaction.atomic():
            Event.objects.all().delete()
        self.assertTrue(Event.objects.exists())

    def test_event_fields(self):
        self.api.patch(f"{API}/profile/", {"industry": "Research"}, HTTP_X_REQUEST_ID="req-12345678")
        e = Event.objects.filter(changed_fields__contains=["industry"]).get()
        self.assertEqual(e.company_id, CompanyProfile.objects.get().id)
        self.assertEqual((e.actor_id, e.actor_label, e.entity_type, e.action), (self.admin.pk, "admin", "profile", "updated"))
        self.assertEqual((e.previous_values["industry"], e.new_values["industry"]), ("", "Research"))
        self.assertEqual(e.request_id, "req-12345678")
        self.assertIsNotNone(e.occurred_at)
        self.assertEqual(str(e.id), str(e.pk))

    def test_history_is_admin_only_and_filterable(self):
        self.api.patch(f"{API}/profile/", {"industry": "Research"})
        self.add_address()
        for client in (self.api_staff, self.api_finance):
            self.assertEqual(client.get(f"{API}/audit-history/").status_code, 403)
        self.assertEqual(self.anon.get(f"{API}/audit-history/").status_code, 401)
        body = self.api.get(f"{API}/audit-history/?entity_type=address").json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["category"], "address")
        self.assertIn("results", self.api.get(f"{API}/audit-history/?page_size=1").json())

    def test_important_changes_are_categorised(self):
        self.complete_setup()
        a = self.add_address(address_type="branch_office")
        self.api.patch(f"{API}/addresses/{a['id']}/", {"city": "Mysuru", "change_reason": "Moved"})
        bank = self.add_bank().json()
        self.api_finance.patch(f"{API}/bank-accounts/{bank['id']}/", {"bank_name": "Other Bank", "change_reason": "Switch"})
        self.api.patch(f"{API}/branding/", {"primary_colour": "#112233"})
        cats = set(Event.objects.values_list("category", flat=True))
        self.assertTrue({"address", "bank", "branding"} <= cats)

    def test_sensitive_personal_data_is_redacted(self):
        p = self.api.post(f"{API}/partners/", dict(full_name="Asha Rao", dpin="12345678", official_email="asha@example.com",
                                                  official_phone="+91 9800000000", partner_type="designated_partner",
                                                  is_designated_partner=True))
        self.assertEqual(p.status_code, 201, p.content)
        blob = json.dumps(list(Event.objects.values("previous_values", "new_values")), default=str)
        for secret in ("12345678", "asha@example.com", "9800000000"):
            self.assertNotIn(secret, blob)
        e = Event.objects.get(entity_type="partner")
        self.assertIn("dpin", e.changed_fields)  # that it changed is recorded; the value is not

    def test_logo_change_is_audited(self):
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")
        self.assertTrue(Event.objects.filter(changed_fields__contains=["logo"], category="branding").exists())

    def test_failed_request_leaves_no_audit_event(self):
        self.complete_setup()
        n = Event.objects.count()
        self.api.patch(f"{API}/profile/", {"legal_name": "No reason LLP"})
        self.assertEqual(Event.objects.count(), n)


class PolicyTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def create(self, **over):
        payload = dict(payment_terms="Net 30", invoice_due_days=30, quotation_validity_days=15,
                       quotation_terms="Valid 15 days", invoice_terms="Late fee 2%", document_footer_text="Footer")
        payload.update(over)
        r = self.api.post(f"{API}/policies/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_versions_are_numbered_and_drafts_are_editable(self):
        a, b = self.create(), self.create(payment_terms="Net 45")
        self.assertEqual((a["version_number"], b["version_number"], a["status"]), (1, 2, "draft"))
        self.assertEqual(self.api.patch(f"{API}/policies/{a['id']}/", {"payment_terms": "Net 7"}).status_code, 200)
        self.assertEqual(CompanyPolicy.objects.get(pk=a["id"]).payment_terms, "Net 7")

    def test_activation_supersedes_and_freezes(self):
        a = self.create()
        self.assertEqual(self.api.post(f"{API}/policies/{a['id']}/activate/", {"change_reason": "Initial"}).status_code, 200)
        r = self.api.patch(f"{API}/policies/{a['id']}/", {"payment_terms": "Changed silently"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "immutable")
        self.assertEqual(self.api.delete(f"{API}/policies/{a['id']}/").status_code, 409)
        start = datetime.date.today() + datetime.timedelta(days=30)
        b = self.api.post(f"{API}/policies/", {"copy_from_current": True, "payment_terms": "Net 45",
                                               "effective_from": start.isoformat()}).json()
        self.assertEqual(b["invoice_terms"], "Late fee 2%")  # copied from the active version
        self.assertEqual(b["payment_terms"], "Net 45")
        self.assertEqual(self.api.post(f"{API}/policies/{b['id']}/activate/", {}).status_code, 200)
        old = CompanyPolicy.objects.get(pk=a["id"])
        self.assertEqual((old.status, old.effective_to), ("superseded", start - datetime.timedelta(days=1)))
        # the successor is not in force yet, so the earlier version still applies today (no gap)
        self.assertEqual(self.api.get(f"{API}/policies/current/").json()["id"], a["id"])
        self.assertEqual(CompanyPolicy.objects.filter(status="active").count(), 1)

    def test_old_version_content_is_unchanged_after_a_new_one_is_activated(self):
        a = self.create()
        self.api.post(f"{API}/policies/{a['id']}/activate/", {})
        b = self.create(payment_terms="Net 90")
        self.api.post(f"{API}/policies/{b['id']}/activate/", {})
        self.assertEqual(CompanyPolicy.objects.get(pk=a["id"]).payment_terms, "Net 30")

    def test_validation_and_permissions(self):
        self.assertEqual(self.api.post(f"{API}/policies/", {"approval_thresholds": [{"scope": "expense", "amount": "abc"}]}).status_code, 400)
        self.assertEqual(self.api.post(f"{API}/policies/", {"retention_settings": {"invoice": 0}}).status_code, 400)
        self.assertEqual(self.api.post(f"{API}/policies/", {"retention_settings": {"invoice": 8},
                                                           "approval_thresholds": [{"scope": "expense", "amount": "50000.00"}]}).status_code, 201)
        self.assertEqual(self.api_staff.post(f"{API}/policies/", {}).status_code, 403)
        self.assertEqual(self.api_staff.get(f"{API}/policies/").status_code, 200)
        a = self.create()
        self.assertEqual(self.api_finance.post(f"{API}/policies/{a['id']}/activate/", {}).status_code, 403)

    def test_threshold_and_payment_term_changes_are_audited_by_category(self):
        a = self.create()
        self.api.patch(f"{API}/policies/{a['id']}/", {"payment_terms": "Net 10", "approval_thresholds":
                                                       [{"scope": "expense", "amount": "1000"}]})
        cats = set(Event.objects.filter(entity_type="policy").values_list("category", flat=True))
        self.assertTrue({"payment_terms"} & cats)


class BrandingTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def test_defaults_and_update_bumps_version(self):
        b = self.api_staff.get(f"{API}/branding/").json()
        self.assertEqual((b["version"], b["default_page_size"], b["font_family"]), (1, "A4", "Helvetica"))
        r = self.api.patch(f"{API}/branding/", {"primary_colour": "#00AA55", "header_alignment": "center", "margin_left_mm": 25})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["version"], 2)
        self.assertEqual(self.api.patch(f"{API}/branding/", {"primary_colour": "#00AA55"}).json()["version"], 2)  # no change

    def test_validation(self):
        for bad in ({"primary_colour": "green"}, {"font_family": "Comic Sans"}, {"body_font_size": 40},
                    {"watermark_enabled": True}, {"signature_block": {"evil": 1}}, {"margin_top_mm": 60, "header_height_mm": 70,
                                                                                        "footer_height_mm": 40, "margin_bottom_mm": 60}):
            self.assertEqual(self.api.patch(f"{API}/branding/", bad).status_code, 400, bad)

    def test_staff_can_read_but_not_write(self):
        self.assertEqual(self.api_staff.patch(f"{API}/branding/", {"primary_colour": "#000000"}).status_code, 403)

    def test_preview_renders_unsaved_values_without_saving_or_numbering(self):
        r = self.api.post(f"{API}/branding/preview/", {"primary_colour": "#CC0000", "watermark_enabled": True,
                                                       "watermark_text": "DRAFT", "default_template": "compact"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertEqual(self.api.get(f"{API}/branding/").json()["primary_colour"], "#1F3A5F")
        self.assertEqual(self.api.post(f"{API}/branding/preview/", {"primary_colour": "nope"}).status_code, 400)
        self.assertEqual(self.api_finance.post(f"{API}/branding/preview/", {}).status_code, 403)

    def test_preview_with_logo_and_alignments(self):
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")
        for pos in ("left", "center", "right"):
            r = self.api.post(f"{API}/branding/preview/", {"logo_position": pos, "header_alignment": pos,
                                                           "footer_alignment": pos, "default_page_size": "LETTER"})
            self.assertEqual(r.status_code, 200, (pos, r.content))

    def test_logo_override_assets(self):
        up = lambda slot: self.api.post(f"{API}/branding/assets/{slot}/", {"file": SimpleUploadedFile("a.png", png_bytes())},
                                        format="multipart")
        self.assertEqual(up("primary_logo").status_code, 200)
        self.assertEqual(up("alternate_logo").status_code, 200)
        self.assertEqual(up("bogus").status_code, 400)
        self.assertEqual(self.api_staff.get(f"{API}/branding/assets/primary_logo/")["Content-Type"], "image/png")
        self.assertEqual(self.api.get(f"{API}/branding/").json()["version"], 3)
        self.assertEqual(self.api.delete(f"{API}/branding/assets/alternate_logo/").status_code, 204)
        self.assertEqual(self.api.get(f"{API}/branding/assets/alternate_logo/").status_code, 404)


class TaxApiTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def test_tax_settings_validation(self):
        r = self.api.patch(f"{API}/tax-settings/", {"tax_codes": [{"code": "GST17", "rate": 17}]})
        self.assertEqual(r.status_code, 400)
        r = self.api.patch(f"{API}/tax-settings/", {"gst_applicable": True, "gst_treatment": "not_applicable"})
        self.assertEqual(r.status_code, 400)
        # regular treatment needs a recorded registration; nothing is assumed
        r = self.api.patch(f"{API}/tax-settings/", {"gst_applicable": True, "gst_treatment": "regular"})
        self.assertEqual(r.status_code, 400)
        r = self.api.patch(f"{API}/tax-settings/", {"tax_codes": [{"code": "GST18", "rate": 18}], "default_tax_code": "GST18"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.api.patch(f"{API}/tax-settings/", {"default_tax_code": "NOPE"}).status_code, 400)

    def test_defaults_assume_nothing(self):
        s = self.api.get(f"{API}/tax-settings/").json()
        self.assertEqual((s["gst_applicable"], s["gst_treatment"], s["tax_codes"]), (False, "not_applicable", []))

    def test_registration_number_is_a_string_and_validated(self):
        from .base import GSTIN
        self.api.patch(f"{API}/profile/", {"pan": "ABCDE1234F"})
        r = self.api.post(f"{API}/tax-registrations/", dict(registration_type="gstin", registration_number=GSTIN.lower()))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["registration_number"], GSTIN)
        self.assertEqual(r.json()["state_code"], "29")
        self.assertEqual(self.api.post(f"{API}/tax-registrations/", dict(registration_type="gstin", registration_number=GSTIN)).status_code, 400)
        r = self.api.post(f"{API}/tax-registrations/", dict(registration_type="msme", registration_number="UDYAM-KA-01-0000123"))
        self.assertEqual(r.status_code, 201)
        r = self.api.post(f"{API}/tax-registrations/", dict(registration_type="other", registration_number="007-00123"))
        self.assertEqual(r.json()["registration_number"], "007-00123")  # leading zeros survive
        self.assertEqual(self.api.post(f"{API}/tax-registrations/", dict(registration_type="pan", registration_number="BAD")).status_code, 400)

    def test_verification_needs_evidence_and_admin(self):
        reg = self.api.post(f"{API}/tax-registrations/", dict(registration_type="other", registration_number="X-1")).json()
        self.assertEqual(self.api.post(f"{API}/tax-registrations/{reg['id']}/verify/", {}).status_code, 400)
        self.assertEqual(self.api_finance.post(f"{API}/tax-registrations/{reg['id']}/verify/", {}).status_code, 403)

    def test_calculator_endpoint(self):
        self.add_address()
        r = self.api.post(f"{API}/tax-settings/calculate/", {"taxable_value": "1000", "rate": "18", "place_of_supply_code": "27"})
        self.assertEqual(r.status_code, 200, r.content)
        # GST not enabled for this company, so no tax is charged: nothing is assumed to apply
        self.assertEqual(r.json()["total_tax"], "0.00")
        self.assertEqual(r.json()["supply_type"], "no_tax")
