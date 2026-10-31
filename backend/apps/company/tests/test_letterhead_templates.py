import datetime
from concurrent.futures import ThreadPoolExecutor

from django.db import connections
from django.test import TestCase, TransactionTestCase

from apps.company.models import DocumentNumberSequence, IssuedDocument, LetterheadTemplate
from apps.company.services import letterheads

from .base import API, CompanyTestMixin

CATALOGUE = {
    "LHT-001": "General Company Letterhead",
    "LHT-002": "Client & Business Correspondence",
    "LHT-003": "Employee & HR Letters",
    "LHT-004": "Bank & Government Correspondence",
    "LHT-005": "Legal & Compliance Letters",
    "LHT-006": "Internal Office Orders",
    "LHT-007": "Vendor & Partnership Letters",
    "LHT-008": "Authorization Letters",
}


class LookupsBeforeSetupTests(CompanyTestMixin, TestCase):
    def test_lookups_work_before_the_company_exists(self):
        """The setup wizard needs every choice list before the company profile is created."""
        r = self.api.get(f"{API}/lookups/")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["letterhead_templates"], [])
        self.assertEqual(len(body["document_types"]), 18)


class TemplateCatalogueTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def test_the_eight_standard_templates_exist_after_setup_start(self):
        rows = self.api_staff.get(f"{API}/letterhead-templates/").json()
        self.assertEqual({r["code"]: r["name"] for r in rows}, CATALOGUE)
        self.assertEqual([r["code"] for r in rows], sorted(CATALOGUE))
        self.assertTrue(all(r["is_active"] for r in rows))

    def test_each_template_has_its_own_sequence_and_the_reference_format(self):
        rows = {r["code"]: r for r in self.api.get(f"{API}/letterhead-templates/").json()}
        today = datetime.date.today()
        expected = f"LHT-002/{today.year % 100:02d}/{today.day:02d}{today.month:02d}/00001"
        self.assertEqual(rows["LHT-002"]["next_reference_preview"], expected)
        self.assertEqual(DocumentNumberSequence.objects.filter(letterhead_template__isnull=False).count(), 8)

    def test_manage_templates_admin_only_with_validation(self):
        self.assertEqual(self.api_staff.post(f"{API}/letterhead-templates/", {"code": "LHT-009", "name": "x"}).status_code, 403)
        for bad in ({"code": "LHT9", "name": "x"}, {"code": "LHT-001", "name": "Duplicate"}, {"code": "LHT-009", "name": ""}):
            self.assertEqual(self.api.post(f"{API}/letterhead-templates/", bad).status_code, 400, bad)
        r = self.api.post(f"{API}/letterhead-templates/", {"code": "lht-009", "name": "Press Releases"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["code"], "LHT-009")
        self.assertTrue(r.json()["next_reference_preview"].startswith("LHT-009/"))

    def test_deactivate_needs_reason_and_blocks_issuing(self):
        t = LetterheadTemplate.objects.get(code="LHT-004")
        self.assertEqual(self.api.post(f"{API}/letterhead-templates/{t.id}/deactivate/", {}).status_code, 400)
        self.assertEqual(self.api.post(f"{API}/letterhead-templates/{t.id}/deactivate/",
                                       {"change_reason": "Not used"}).status_code, 200)

    def test_seed_defaults_restores_missing_ones_without_touching_edits(self):
        t = LetterheadTemplate.objects.get(code="LHT-003")
        self.api.patch(f"{API}/letterhead-templates/{t.id}/", {"name": "People Letters"})
        self.assertEqual(self.api.post(f"{API}/letterhead-templates/seed-defaults/", {}).json(), [])
        self.assertEqual(LetterheadTemplate.objects.get(code="LHT-003").name, "People Letters")


class IssueOnTemplateTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()

    def issue(self, template, **extra):
        payload = dict(document_type="business_letter", letterhead_template=template, issue_date="2026-10-27",
                       content={"paragraphs": ["Hello"]})
        payload.update(extra)
        return self.api_finance.post(f"{API}/issued-documents/", payload)

    def test_reference_is_template_year_date_and_unique_running_number(self):
        a = self.issue("LHT-002").json()
        b = self.issue("LHT-002", issue_date="2026-10-28").json()
        c = self.issue("LHT-003").json()
        self.assertEqual(a["reference_number"], "LHT-002/26/2710/00001")
        self.assertEqual(b["reference_number"], "LHT-002/26/2810/00002")  # same counter, new date
        self.assertEqual(c["reference_number"], "LHT-003/26/2710/00001")  # each template counts on its own
        self.assertEqual((a["letterhead_template_code"], c["letterhead_template_code"]), ("LHT-002", "LHT-003"))

    def test_each_templates_counter_restarts_at_00001_every_year(self):
        refs = lambda tpl, date: self.issue(tpl, issue_date=date).json()["reference_number"]
        self.assertEqual(refs("LHT-001", "2026-03-10"), "LHT-001/26/1003/00001")
        self.assertEqual(refs("LHT-001", "2026-10-27"), "LHT-001/26/2710/00002")
        self.assertEqual(refs("LHT-002", "2026-10-27"), "LHT-002/26/2710/00001")  # its own counter
        self.assertEqual(refs("LHT-001", "2027-01-05"), "LHT-001/27/0501/00001")  # new year, back to 1
        self.assertEqual(refs("LHT-001", "2027-01-06"), "LHT-001/27/0601/00002")
        self.assertEqual(refs("LHT-002", "2027-02-01"), "LHT-002/27/0102/00001")  # restarts independently

    def test_a_letter_dated_in_an_earlier_year_continues_that_years_numbers(self):
        refs = lambda tpl, date: self.issue(tpl, issue_date=date).json()["reference_number"]
        self.assertEqual(refs("LHT-001", "2026-11-01"), "LHT-001/26/0111/00001")
        self.assertEqual(refs("LHT-001", "2027-01-05"), "LHT-001/27/0501/00001")
        # issued in 2027 but dated 2026: carries on from 00001 of 2026 and leaves 2027's counter alone
        self.assertEqual(refs("LHT-001", "2026-12-30"), "LHT-001/26/3012/00002")
        self.assertEqual(refs("LHT-001", "2027-01-07"), "LHT-001/27/0701/00002")

    def test_every_reference_stays_unique_across_the_restart(self):
        refs = [self.issue("LHT-003", issue_date=d).json()["reference_number"]
                for d in ("2026-05-01", "2026-05-01", "2027-05-01", "2027-05-01", "2026-05-01")]
        self.assertEqual(len(set(refs)), 5, refs)
        self.assertEqual(refs[-1], "LHT-003/26/0105/00003")

    def test_template_sequences_are_set_to_restart_yearly(self):
        self.assertEqual(
            set(DocumentNumberSequence.objects.filter(letterhead_template__isnull=False).values_list("reset_frequency", flat=True)),
            {"yearly"})

    def test_the_migration_switches_existing_never_resetting_template_counters_to_yearly(self):
        import importlib
        from django.apps import apps as django_apps
        DocumentNumberSequence.objects.filter(letterhead_template__isnull=False).update(reset_frequency="never")
        DocumentNumberSequence.objects.filter(letterhead_template__isnull=True, document_type="invoice").update(reset_frequency="never")
        migration = importlib.import_module("apps.company.migrations.0007_template_sequences_restart_yearly")
        migration.restart_yearly(django_apps, None)
        self.assertEqual(DocumentNumberSequence.objects.filter(letterhead_template__isnull=False, reset_frequency="yearly").count(), 8)
        # standard (non-template) sequences are left exactly as configured
        self.assertEqual(DocumentNumberSequence.objects.get(document_type="invoice").reset_frequency, "never")

    def test_template_sequences_do_not_disturb_the_plain_letter_sequence(self):
        self.issue("LHT-002")
        plain = self.api_finance.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", content={"paragraphs": ["x"]}, issue_date="2026-10-27")).json()
        self.assertEqual(plain["reference_number"], "T2N/LTR/26/2710/00001")

    def test_snapshot_records_the_template_and_default_title_is_used(self):
        d = self.issue("LHT-006").json()
        self.assertEqual(d["title"], "Office Order")
        snap = self.api.get(f"{API}/issued-documents/{d['id']}/snapshot/").json()
        self.assertEqual((snap["letterhead"]["code"], snap["letterhead"]["name"]), ("LHT-006", "Internal Office Orders"))
        self.assertEqual(self.issue("LHT-006", title="Custom heading").json()["title"], "Custom heading")

    def test_unknown_inactive_or_unnumbered_requests_are_refused(self):
        self.assertEqual(self.issue("LHT-099").status_code, 400)
        self.assertEqual(self.issue("nonsense").status_code, 400)
        self.assertEqual(self.issue("LHT-002", numbered=False).status_code, 400)
        t = LetterheadTemplate.objects.get(code="LHT-005")
        self.api.post(f"{API}/letterhead-templates/{t.id}/deactivate/", {"change_reason": "Retired"})
        self.assertEqual(self.issue("LHT-005").status_code, 400)

    def test_used_template_cannot_be_deleted_or_renumbered(self):
        self.issue("LHT-002")
        t = LetterheadTemplate.objects.get(code="LHT-002")
        r = self.api.delete(f"{API}/letterhead-templates/{t.id}/")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.api.patch(f"{API}/letterhead-templates/{t.id}/", {"code": "LHT-020"}).status_code, 400)
        self.assertEqual(self.api.patch(f"{API}/letterhead-templates/{t.id}/", {"name": "Renamed"}).status_code, 200)
        self.assertEqual(IssuedDocument.objects.get().letterhead_template_id, t.id)

    def test_unused_template_can_be_deleted_only_after_its_sequence_is_gone(self):
        t = self.api.post(f"{API}/letterhead-templates/", {"code": "LHT-010", "name": "Temp"}).json()
        # the template's own sequence references it (PROTECT), so it cannot be silently dropped
        self.assertEqual(self.api.delete(f"{API}/letterhead-templates/{t['id']}/").status_code, 409)

    def test_numbering_page_lists_template_sequences_separately(self):
        body = self.api.get(f"{API}/document-numbering/").json()
        self.assertEqual(len(body["letterhead_sequences"]), 8)
        self.assertFalse(any(r["letterhead_template"] for r in body["results"]))
        self.assertEqual(body["letterhead_sequences"][0]["letterhead_template_code"], "LHT-001")
        seq = body["letterhead_sequences"][1]
        r = self.api.patch(f"{API}/document-numbering/{seq['id']}/", {"date_format": "ddmmyyyy", "change_reason": "Prefer full date"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertRegex(r.json()["next_reference_preview"], r"^LHT-002/\d\d/\d{8}/00001$")

    def test_bulk_numbering_patch_never_touches_template_sequences(self):
        r = self.api.patch(f"{API}/document-numbering/", {"sequences": [{"document_type": "business_letter", "min_length": 6}], "change_reason": "Longer numbers"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["results"][0]["next_reference_preview"], f"T2N/LTR/{datetime.date.today():%y}/{datetime.date.today():%d%m}/000001")
        self.assertEqual(DocumentNumberSequence.objects.filter(letterhead_template__isnull=False, min_length=6).count(), 0)

    def test_lookups_expose_the_catalogue(self):
        look = self.api_staff.get(f"{API}/lookups/").json()
        self.assertEqual(len(look["letterhead_templates"]), 8)
        self.assertEqual(look["letterhead_templates"][0]["label"], "LHT-001 General Company Letterhead")


class TemplateConcurrencyTests(CompanyTestMixin, TransactionTestCase):
    def test_concurrent_letters_on_one_template_get_distinct_references(self):
        self.full_company()

        def worker(_):
            try:
                r = self.api_finance.post(f"{API}/issued-documents/", dict(
                    document_type="business_letter", letterhead_template="LHT-002", issue_date="2026-10-27",
                    content={"paragraphs": ["x"]}))
                return r.status_code, r.json().get("reference_number")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(worker, range(12)))
        self.assertTrue(all(code == 201 for code, _ in results), results)
        refs = sorted(ref for _, ref in results)
        self.assertEqual(refs, [f"LHT-002/26/2710/{n:05d}" for n in range(1, 13)])
