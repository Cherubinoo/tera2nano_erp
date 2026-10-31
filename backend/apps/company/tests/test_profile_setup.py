from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.company.models import CompanyBranding, CompanyProfile, CompanyProfileAuditEvent

from .base import API, GSTIN, PAN, CompanyTestMixin, make_gstin


class SetupWizardTests(CompanyTestMixin, TestCase):
    def test_requires_authentication_and_admin(self):
        self.assertEqual(self.anon.post(f"{API}/setup/start/", {}).status_code, 401)
        self.assertEqual(self.api_staff.post(f"{API}/setup/start/", {"legal_name": "A", "display_name": "A"}).status_code, 403)
        self.assertEqual(self.api_finance.get(f"{API}/setup/").status_code, 403)

    def test_profile_is_not_configured_before_setup(self):
        r = self.api_staff.get(f"{API}/profile/")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "not_configured")

    def test_start_requires_both_names(self):
        r = self.api.post(f"{API}/setup/start/", {"legal_name": "Only legal"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("display_name", r.json())

    def test_start_is_idempotent_and_never_duplicates_the_profile(self):
        first = self.start_company()
        second = self.api_admin2.post(f"{API}/setup/start/", {"legal_name": "Another Name", "display_name": "Other"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["company_id"], second.json()["company_id"])
        self.assertEqual(CompanyProfile.objects.count(), 1)
        self.assertEqual(CompanyProfile.objects.get().legal_name, "Example Ventures LLP")  # not overwritten

    def test_database_refuses_a_second_company_row(self):
        self.start_company()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CompanyProfile.objects.create(legal_name="Dup", display_name="Dup")

    def test_start_creates_branding_and_audit_event(self):
        self.start_company()
        company = CompanyProfile.objects.get()
        self.assertTrue(CompanyBranding.objects.filter(company=company).exists())
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(company=company, action="created").exists())
        self.assertEqual(company.setup_status, "in_progress")

    def test_nothing_is_assumed_about_registrations(self):
        self.start_company()
        p = self.api.get(f"{API}/profile/").json()
        self.assertEqual(p["gst_registration_status"], "not_declared")
        for blank in ("gstin", "pan", "tan", "llpin", "legal_entity_type"):
            self.assertEqual(p[blank], "")
        self.assertEqual(self.api.get(f"{API}/tax-registrations/").json(), [])

    def test_state_resumes_from_stored_data(self):
        self.start_company()
        state = self.api.get(f"{API}/setup/").json()
        done = {s["key"]: s["complete"] for s in state["steps"]}
        self.assertTrue(done["identity"])
        self.assertFalse(done["address"])
        self.add_address()
        self.api.patch(f"{API}/setup/progress/", {"step": 4, "skipped_steps": [2]})
        state = self.api_admin2.get(f"{API}/setup/").json()  # a different session resumes the same setup
        by_key = {s["key"]: s for s in state["steps"]}
        self.assertTrue(by_key["address"]["complete"])
        self.assertTrue(by_key["legal"]["skipped"])
        self.assertEqual(state["current_step"], 4)

    def test_required_steps_cannot_be_skipped(self):
        self.start_company()
        r = self.api.patch(f"{API}/setup/progress/", {"step": 3, "skipped_steps": [1, 6]})
        self.assertEqual(r.json()["skipped_steps"], [6])

    def test_setup_completes_without_any_optional_information(self):
        self.start_company()
        r = self.api.post(f"{API}/setup/complete/", {})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "completed")
        self.assertTrue(body["warnings"])  # optional gaps are reported, never blocking
        self.assertFalse(any("bank" in w.lower() for w in body["warnings"]))
        self.assertEqual(self.api.get(f"{API}/bank-accounts/").json(), [])
        self.assertEqual(CompanyProfile.objects.get().status, "active")


class ProfileTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def test_edits_persist_in_postgres(self):
        r = self.api.patch(f"{API}/profile/", {"industry": "Nanotechnology", "primary_email": "hello@example.com",
                                                 "date_of_incorporation": "2024-01-15", "website": "https://example.com"})
        self.assertEqual(r.status_code, 200, r.content)
        row = CompanyProfile.objects.values().get()
        self.assertEqual(row["industry"], "Nanotechnology")
        self.assertEqual(str(row["date_of_incorporation"]), "2024-01-15")
        again = self.api_staff.get(f"{API}/profile/").json()  # a different user sees the stored value
        self.assertEqual(again["primary_email"], "hello@example.com")

    def test_only_admins_can_edit(self):
        for client in (self.api_staff, self.api_finance):
            self.assertEqual(client.patch(f"{API}/profile/", {"industry": "x"}).status_code, 403)
        self.assertEqual(self.anon.patch(f"{API}/profile/", {"industry": "x"}).status_code, 401)

    def test_pan_is_normalised_and_validated(self):
        r = self.api.patch(f"{API}/profile/", {"pan": " abcde1234f "})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["pan"], PAN)
        self.assertEqual(self.api.patch(f"{API}/profile/", {"pan": "BADPAN"}).status_code, 400)
        self.assertEqual(self.api.patch(f"{API}/profile/", {"llpin": "AB-12"}).status_code, 400)
        self.assertEqual(self.api.patch(f"{API}/profile/", {"tan": "XYZ"}).status_code, 400)

    def test_gstin_rules(self):
        self.api.patch(f"{API}/profile/", {"pan": PAN})
        # GSTIN cannot be stored unless the status says registered.
        r = self.api.patch(f"{API}/profile/", {"gstin": GSTIN})
        self.assertEqual(r.status_code, 400)
        # Registered status without a GSTIN is also refused.
        self.assertEqual(self.api.patch(f"{API}/profile/", {"gst_registration_status": "registered"}).status_code, 400)
        ok = self.api.patch(f"{API}/profile/", {"gst_registration_status": "registered", "gstin": GSTIN})
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["gst_state_name"], "Karnataka")
        wrong_pan = self.api.patch(f"{API}/profile/", {"gstin": make_gstin(pan="ZZZZZ9999Z")})
        self.assertEqual(wrong_pan.status_code, 400)
        self.assertIn("PAN", str(wrong_pan.json()))

    def test_unknown_timezone_rejected(self):
        self.assertEqual(self.api.patch(f"{API}/profile/", {"default_timezone": "Mars/Base"}).status_code, 400)

    def test_important_changes_need_a_reason_only_after_setup(self):
        r = self.api.patch(f"{API}/profile/", {"legal_name": "Corrected Name LLP"})
        self.assertEqual(r.status_code, 200)  # setup in progress: corrections are free
        self.complete_setup()
        r = self.api.patch(f"{API}/profile/", {"legal_name": "Renamed Ventures LLP"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("change_reason", r.json())
        self.assertEqual(CompanyProfile.objects.get().legal_name, "Corrected Name LLP")  # rolled back
        r = self.api.patch(f"{API}/profile/", {"legal_name": "Renamed Ventures LLP",
                                                 "change_reason": "Certificate of change of name"})
        self.assertEqual(r.status_code, 200, r.content)
        event = CompanyProfileAuditEvent.objects.filter(entity_type="profile", changed_fields__contains=["legal_name"]).first()
        self.assertEqual(event.previous_values["legal_name"], "Corrected Name LLP")
        self.assertEqual(event.new_values["legal_name"], "Renamed Ventures LLP")
        self.assertEqual(event.reason, "Certificate of change of name")
        self.assertEqual(event.category, "legal_name")
        self.assertEqual(event.actor_id, self.admin.pk)

    def test_unimportant_changes_do_not_need_a_reason(self):
        self.complete_setup()
        self.assertEqual(self.api.patch(f"{API}/profile/", {"industry": "Research"}).status_code, 200)

    def test_profile_version_increments_with_changes(self):
        v0 = self.api.get(f"{API}/profile/").json()["version"]
        self.api.patch(f"{API}/profile/", {"industry": "Research"})
        self.assertGreater(self.api.get(f"{API}/profile/").json()["version"], v0)
