"""IDs are allocated automatically: a fresh company needs no numbering or financial-year setup."""
import datetime

from django.test import TestCase

from apps.company.constants import DocType
from apps.company.models import CompanyProfile, CompanyProfileAuditEvent, DocumentNumberSequence, FinancialYear
from apps.company.services import numbering

from .base import API, CompanyTestMixin
from .test_minutes import MINUTES

import datetime as _dt

_TODAY = _dt.date.today()
YY_DDMM = f"{_TODAY:%y}/{_TODAY:%d%m}"        # slash style, e.g. 26/2710
YY_DDMM_H = f"{_TODAY:%y}-{_TODAY:%d%m}"      # hyphen style, e.g. 26-2710


class FreshCompanyTests(CompanyTestMixin, TestCase):
    """A company created through the wizard with nothing else configured (no sequences, no financial year)."""

    def setUp(self):
        super().setUp()
        self.start_company()
        self.api.patch(f"{API}/profile/", {"short_code": "T2N"})
        self.company = CompanyProfile.objects.get()
        self.assertEqual(DocumentNumberSequence.objects.filter(letterhead_template__isnull=True).count(), 0)
        self.assertEqual(FinancialYear.objects.count(), 0)

    def minutes(self, **extra):
        return self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type="meeting_minutes", issue_date="2026-10-27", content={"minutes": MINUTES}, **extra))

    def test_minutes_can_be_issued_straight_away_with_properly_formatted_ids(self):
        r = self.minutes()
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual((d["reference_number"], d["source_id"]), ("T2N-MOM-26-2710-0001", "T2N-MTG-26-2710-0001"))
        again = self.minutes().json()
        self.assertEqual((again["reference_number"], again["source_id"]), ("T2N-MOM-26-2710-0002", "T2N-MTG-26-2710-0002"))

    def test_letter_without_template_gets_a_formatted_id_and_the_financial_year_is_created(self):
        r = self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", issue_date="2026-10-27", content={"paragraphs": ["Hello"]}))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["reference_number"], "T2N/LTR/26/2710/00001")
        fy = FinancialYear.objects.get()
        self.assertEqual((fy.label, str(fy.start_date), str(fy.end_date), fy.is_current), ("2026-27", "2026-04-01", "2027-03-31", True))

    def test_letter_on_a_template_needs_nothing_else(self):
        r = self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", letterhead_template="LHT-002", issue_date="2026-10-27",
            content={"paragraphs": ["Hello"]}))
        self.assertEqual(r.json()["reference_number"], "LHT-002/26/2710/00001")
        self.assertEqual(FinancialYear.objects.count(), 0)  # template numbers do not use the financial year

    def test_every_document_type_gets_its_standard_format(self):
        expected = {
            DocType.QUOTATION: "T2N/QUO/26/2710/00001", DocType.INVOICE: "T2N/INV/26/2710/00001",
            DocType.PROJECT: "T2N-PRJ-26-2710-0001", DocType.EMPLOYEE: "T2N-EMP-26-2710-0001",
            DocType.MEETING: "T2N-MTG-26-2710-0001",
            DocType.MEETING_MINUTES: "T2N-MOM-26-2710-0001", DocType.EXPENSE: "T2N/EXP/26/2710/00001",
            DocType.PAYMENT: "T2N/PAY/26/2710/00001",
        }
        for doc_type, ref in expected.items():
            got = numbering.allocate_number(self.company, doc_type, on_date=datetime.date(2026, 10, 27)).reference_number
            self.assertEqual(got, ref, doc_type)

    def test_automatic_creation_is_audited(self):
        self.minutes()
        reasons = CompanyProfileAuditEvent.objects.filter(reason__contains="automatically").values_list("entity_type", flat=True)
        self.assertEqual(set(reasons), {"numbering"})  # sequences for minutes and meeting; no FY was needed
        self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", issue_date="2026-10-27", content={"paragraphs": ["x"]}))
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="financial_year", reason__contains="automatically").exists())

    def test_existing_configuration_is_never_overridden(self):
        self.api.post(f"{API}/document-numbering/", dict(document_type="meeting_minutes", prefix="MIN", separator="/",
                                                         min_length=3, include_financial_year=False))
        self.assertEqual(self.minutes().json()["reference_number"], "MIN/001")
        DocumentNumberSequence.objects.filter(document_type="meeting").update(is_active=True)

    def test_a_sequence_an_administrator_switched_off_still_blocks(self):
        self.minutes()
        DocumentNumberSequence.objects.filter(document_type="meeting_minutes").update(is_active=False)
        r = self.minutes()
        self.assertEqual(r.status_code, 400)
        self.assertIn("switched off", str(r.json()))

    def test_the_existing_current_year_is_kept_and_a_new_year_is_not_marked_current(self):
        self.api.post(f"{API}/financial-years/", dict(label="2026-27", start_date="2026-04-01", end_date="2027-03-31", is_current=True))
        numbering.allocate_number(self.company, DocType.INVOICE, on_date=datetime.date(2027, 5, 1))
        self.assertEqual(FinancialYear.objects.get(label="2026-27").is_current, True)
        self.assertEqual(FinancialYear.objects.get(label="2027-28").is_current, False)

    def test_a_year_that_cannot_be_created_without_overlap_gives_a_clear_message(self):
        FinancialYear.objects.create(company=self.company, label="2026-short", start_date=datetime.date(2026, 6, 1),
                                     end_date=datetime.date(2026, 9, 30))
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            numbering.allocate_number(self.company, DocType.INVOICE, on_date=datetime.date(2026, 10, 1))
        self.assertIn("Financial Year Settings", str(ctx.exception))

    def test_prefix_falls_back_to_initials_when_there_is_no_short_code(self):
        CompanyProfile.objects.update(short_code="", display_name="Tera2Nano Solutions LLP")
        company = CompanyProfile.objects.get()
        self.assertEqual(numbering.company_code(company), "TS")
        got = numbering.allocate_number(company, DocType.MEETING_MINUTES).reference_number
        self.assertEqual(got, f"TS-MOM-{YY_DDMM_H}-0001")


class EveryIdCarriesYearAndDateTests(CompanyTestMixin, TestCase):
    """Every reference ID contains the last two digits of the year and the day-month of its date."""

    def setUp(self):
        super().setUp()
        self.start_company()
        self.api.patch(f"{API}/profile/", {"short_code": "T2N"})
        self.company = CompanyProfile.objects.get()

    def test_all_standard_types_and_templates_carry_yy_and_ddmm(self):
        import re
        on = datetime.date(2027, 3, 9)
        refs = [numbering.allocate_number(self.company, t, on_date=on).reference_number for t in numbering.DEFAULT_SEQUENCES]
        from apps.company.models import LetterheadTemplate
        refs += [numbering.allocate_number(self.company, "business_letter", on_date=on, template=t).reference_number
                 for t in LetterheadTemplate.objects.all()]
        self.assertEqual(len(refs), len(numbering.DEFAULT_SEQUENCES) + 8)
        self.assertEqual(len(set(refs)), len(refs))
        self.assertTrue(any("-BNK-27-0903-" in r for r in refs))  # bank accounts too
        for ref in refs:
            self.assertRegex(ref, r"[/-]27[/-]0903[/-]\d+$", ref)

    def test_financial_year_restart_never_repeats_a_reference(self):
        # the counter restarts on 1 April, but the date is in the number, so references stay unique
        refs = [numbering.allocate_number(self.company, DocType.INVOICE, on_date=d).reference_number
                for d in (datetime.date(2027, 3, 30), datetime.date(2027, 3, 31), datetime.date(2027, 4, 1),
                          datetime.date(2027, 4, 1), datetime.date(2028, 3, 31))]
        self.assertEqual(refs, ["T2N/INV/27/3003/00001", "T2N/INV/27/3103/00002", "T2N/INV/27/0104/00001",
                                "T2N/INV/27/0104/00002", "T2N/INV/28/3103/00003"])  # same financial year: keeps counting
        self.assertEqual(len(set(refs)), 5)

    def test_financial_year_restart_is_allowed_only_when_the_number_cannot_repeat(self):
        ok = self.api.post(f"{API}/document-numbering/", dict(
            document_type="expense", prefix="EXP", reset_frequency="financial_year",
            include_short_year=True, date_format="ddmm"))
        self.assertEqual(ok.status_code, 201, ok.content)
        bad = self.api.post(f"{API}/document-numbering/", dict(
            document_type="payment", prefix="PAY", reset_frequency="financial_year"))
        self.assertEqual(bad.status_code, 400)

    def test_gst_registered_company_is_warned_when_an_invoice_number_exceeds_16_characters(self):
        from .base import GSTIN, PAN
        self.api.patch(f"{API}/profile/", {"pan": PAN, "gst_registration_status": "registered", "gstin": GSTIN})
        numbering.allocate_number(self.company, DocType.INVOICE)  # creates the invoice sequence
        rows = {r["document_type"]: r for r in self.api.get(f"{API}/document-numbering/").json()["results"]}
        self.assertIn("16 characters", rows["invoice"]["warning"])
        self.assertIsNone(rows["meeting_minutes"]["warning"] if "meeting_minutes" in rows else None)
        seq = DocumentNumberSequence.objects.get(document_type="invoice")
        short = self.api.patch(f"{API}/document-numbering/{seq.id}/", {"prefix": "T2N", "min_length": 3, "separator": "/"})
        self.assertEqual(short.status_code, 200, short.content)
        self.assertIsNone(short.json()["warning"])  # T2N/26/2710/001 is 15 characters

    def test_no_warning_when_the_company_is_not_gst_registered(self):
        numbering.allocate_number(self.company, DocType.INVOICE)
        rows = {r["document_type"]: r for r in self.api.get(f"{API}/document-numbering/").json()["results"]}
        self.assertIsNone(rows["invoice"]["warning"])

    def test_the_migration_upgrades_only_sequences_that_never_issued_anything(self):
        import importlib
        from django.apps import apps as django_apps
        fresh = numbering.ensure_default_sequence(self.company, DocType.PROJECT)   # never used
        used = numbering.ensure_default_sequence(self.company, DocType.EMPLOYEE)   # used below
        numbering.allocate_number(self.company, DocType.EMPLOYEE, on_date=datetime.date(2026, 5, 5))
        custom = numbering.ensure_default_sequence(self.company, DocType.EXPENSE)  # administrator-defined format
        for seq in (fresh, used):
            DocumentNumberSequence.objects.filter(pk=seq.pk).update(include_short_year=False, date_format="")
        DocumentNumberSequence.objects.filter(pk=custom.pk).update(
            include_short_year=False, date_format="", include_financial_year=True, reset_frequency="financial_year")
        used_before = DocumentNumberSequence.objects.get(pk=used.pk)
        migration = importlib.import_module("apps.company.migrations.0008_standard_sequences_carry_year_and_date")
        migration.upgrade_untouched(django_apps, None)
        self.assertEqual(DocumentNumberSequence.objects.get(pk=fresh.pk).preview(on_date=datetime.date(2026, 5, 5)),
                         "T2N-PRJ-26-0505-0001")
        self.assertFalse(DocumentNumberSequence.objects.get(pk=used.pk).include_short_year)  # numbers already issued
        self.assertEqual(DocumentNumberSequence.objects.get(pk=used.pk).prefix, used_before.prefix)
