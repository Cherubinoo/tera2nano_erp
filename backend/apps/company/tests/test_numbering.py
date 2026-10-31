import datetime
from concurrent.futures import ThreadPoolExecutor

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

from apps.company.models import CompanyProfile, DocumentNumberSequence, FinancialYear, IssuedReferenceNumber
from apps.company.services import numbering

from .base import API, CompanyTestMixin

import datetime as _dt

_TODAY = _dt.date.today()
YY_DDMM = f"{_TODAY:%y}/{_TODAY:%d%m}"        # slash style, e.g. 26/2710
YY_DDMM_H = f"{_TODAY:%y}-{_TODAY:%d%m}"      # hyphen style, e.g. 26-2710


class NumberingConfigTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()
        self.add_financial_year()
        self.company = CompanyProfile.objects.get()

    def test_seed_creates_every_document_type_without_overwriting(self):
        self.seed_numbering("T2N")
        rows = {r["document_type"]: r for r in self.api.get(f"{API}/document-numbering/").json()["results"]}
        self.assertEqual(rows["quotation"]["next_reference_preview"], f"T2N/QUO/{YY_DDMM}/00001")
        self.assertEqual(rows["invoice"]["next_reference_preview"], f"T2N/INV/{YY_DDMM}/00001")
        self.assertEqual(rows["employee"]["next_reference_preview"], f"T2N-EMP-{YY_DDMM_H}-0001")
        self.assertEqual(rows["project"]["next_reference_preview"], f"T2N-PRJ-{YY_DDMM_H}-0001")
        self.assertEqual(rows["meeting"]["next_reference_preview"], f"T2N-MTG-{YY_DDMM_H}-0001")
        self.api.patch(f"{API}/document-numbering/{rows['invoice']['id']}/", {"prefix": "ACME/INV"})
        again = self.api.post(f"{API}/document-numbering/seed/", {"short_code": "XYZ"})
        self.assertEqual(again.json()["created"], [])
        inv = self.api.get(f"{API}/document-numbering/{rows['invoice']['id']}/").json()
        self.assertEqual(inv["prefix"], "ACME/INV")

    def test_seed_needs_short_code(self):
        self.assertEqual(self.api.post(f"{API}/document-numbering/seed/", {}).status_code, 400)

    def test_formats_are_configurable(self):
        r = self.api.post(f"{API}/document-numbering/", dict(
            document_type="expense", prefix="EXP", include_financial_year=True, separator=".", min_length=3,
            reset_frequency="financial_year"))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["next_reference_preview"], "EXP.2026-27.001")

    def test_validation(self):
        base = dict(document_type="expense", prefix="EXP")
        self.assertEqual(self.api.post(f"{API}/document-numbering/", {**base, "min_length": 0}).status_code, 400)
        self.assertEqual(self.api.post(f"{API}/document-numbering/", {**base, "reset_frequency": "financial_year",
                                                                       "include_financial_year": False}).status_code, 400)
        self.assertEqual(self.api.post(f"{API}/document-numbering/", {"document_type": "expense", "prefix": " "}).status_code, 400)

    def test_duplicate_document_type_sequence_rejected(self):
        self.api.post(f"{API}/document-numbering/", dict(document_type="expense", prefix="EXP"))
        self.assertEqual(self.api.post(f"{API}/document-numbering/", dict(document_type="expense", prefix="E2")).status_code, 400)

    def test_bulk_patch_updates_several_in_one_transaction(self):
        self.seed_numbering()
        r = self.api.patch(f"{API}/document-numbering/", {"sequences": [
            {"document_type": "invoice", "min_length": 6}, {"document_type": "quotation", "min_length": 4}]})
        self.assertEqual(r.status_code, 200, r.content)
        rows = {x["document_type"]: x for x in r.json()["results"]}
        self.assertEqual(rows["invoice"]["next_reference_preview"], f"T2N/INV/{YY_DDMM}/000001")
        # one bad item rolls everything back
        r = self.api.patch(f"{API}/document-numbering/", {"sequences": [
            {"document_type": "invoice", "min_length": 7}, {"document_type": "quotation", "min_length": 99}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(DocumentNumberSequence.objects.get(document_type="invoice").min_length, 6)

    def test_staff_cannot_change_numbering(self):
        self.seed_numbering()
        self.assertEqual(self.api_staff.patch(f"{API}/document-numbering/", {"sequences": [
            {"document_type": "invoice", "min_length": 6}]}).status_code, 403)

    def test_next_value_cannot_go_backwards_over_issued_numbers(self):
        self.seed_numbering()
        for _ in range(3):
            numbering.allocate_number(self.company, "invoice", on_date=datetime.date(2026, 6, 1))
        seq = DocumentNumberSequence.objects.get(document_type="invoice")
        r = self.api.patch(f"{API}/document-numbering/{seq.id}/", {"next_value": 2})
        self.assertEqual(r.status_code, 400)
        self.assertIn("next_value", r.json())
        self.assertEqual(self.api.patch(f"{API}/document-numbering/{seq.id}/", {"next_value": 10}).status_code, 200)
        ref = numbering.allocate_number(self.company, "invoice", on_date=datetime.date(2026, 6, 1))
        self.assertEqual(ref.reference_number, "T2N/INV/26/0106/00010")

    def test_changes_to_numbering_are_audited_with_reason_after_setup(self):
        self.seed_numbering()
        self.complete_setup()
        seq = DocumentNumberSequence.objects.get(document_type="invoice")
        self.assertEqual(self.api.patch(f"{API}/document-numbering/{seq.id}/", {"prefix": "NEW/INV"}).status_code, 400)
        r = self.api.patch(f"{API}/document-numbering/{seq.id}/", {"prefix": "NEW/INV", "change_reason": "Rebrand"})
        self.assertEqual(r.status_code, 200)
        from apps.company.models import CompanyProfileAuditEvent as E
        e = E.objects.filter(entity_type="numbering", changed_fields__contains=["prefix"]).first()
        self.assertEqual((e.previous_values["prefix"], e.new_values["prefix"], e.category), ("T2N/INV", "NEW/INV", "numbering"))


class AllocationTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()
        self.add_financial_year()
        self.seed_numbering()
        self.company = CompanyProfile.objects.get()

    def alloc(self, t="invoice", d=datetime.date(2026, 6, 1)):
        return numbering.allocate_number(self.company, t, actor=self.admin, on_date=d)

    def test_sequential_and_formatted(self):
        self.assertEqual([self.alloc().reference_number for _ in range(3)],
                         ["T2N/INV/26/0106/00001", "T2N/INV/26/0106/00002", "T2N/INV/26/0106/00003"])
        self.assertEqual(self.alloc("employee").reference_number, "T2N-EMP-26-0106-0001")

    def test_independent_sequences_per_document_type(self):
        self.alloc("invoice")
        self.assertEqual(self.alloc("quotation").reference_number, "T2N/QUO/26/0106/00001")

    def test_counter_does_not_depend_on_row_counts(self):
        a = self.alloc()
        IssuedReferenceNumber.objects.filter(pk=a.pk).update(status="void", void_reason="mistake")
        self.assertEqual(self.alloc().sequence_value, 2)  # voided numbers are not reused

    def test_database_refuses_reference_reuse(self):
        a = self.alloc()
        with self.assertRaises(IntegrityError), transaction.atomic():
            IssuedReferenceNumber.objects.create(
                company=self.company, sequence=a.sequence, document_type="invoice",
                reference_number=a.reference_number, sequence_value=99, period=a.period)

    def test_issued_numbers_cannot_be_deleted(self):
        a = self.alloc()
        with self.assertRaises(ValidationError):
            a.delete()

    def test_fy_reset_starts_again_at_one_in_the_next_year(self):
        self.add_financial_year("2027-28", "2027-04-01", "2028-03-31", current=False)
        self.alloc(d=datetime.date(2026, 6, 1))
        self.alloc(d=datetime.date(2027, 1, 1))
        first_next = self.alloc(d=datetime.date(2027, 5, 1))
        self.assertEqual(first_next.reference_number, "T2N/INV/27/0105/00001")
        self.assertEqual(self.alloc(d=datetime.date(2027, 5, 2)).reference_number, "T2N/INV/27/0205/00002")

    def test_missing_financial_year_is_created_automatically(self):
        ref = self.alloc(d=datetime.date(2030, 1, 1))  # January 2030 belongs to the 2029-30 year
        self.assertEqual(ref.reference_number, "T2N/INV/30/0101/00001")
        fy = FinancialYear.objects.get(label="2029-30")
        self.assertEqual((str(fy.start_date), str(fy.end_date), fy.is_current), ("2029-04-01", "2030-03-31", False))
        self.assertEqual(self.alloc(d=datetime.date(2030, 1, 2)).reference_number, "T2N/INV/30/0201/00002")

    def test_locked_financial_year_blocks_issue(self):
        fy = FinancialYear.objects.get()
        r = self.api.post(f"{API}/financial-years/{fy.id}/lock/", {"change_reason": "Year-end close"})
        self.assertEqual(r.status_code, 200, r.content)
        with self.assertRaises(ValidationError):
            self.alloc()
        # sequences that do not use the financial year are unaffected
        self.assertEqual(self.alloc("employee").reference_number, "T2N-EMP-26-0106-0001")

    def test_a_sequence_switched_off_blocks_issue_but_a_missing_one_is_created(self):
        DocumentNumberSequence.objects.filter(document_type="invoice").update(is_active=False)
        with self.assertRaises(ValidationError):
            self.alloc()  # an administrator turned it off on purpose
        DocumentNumberSequence.objects.filter(document_type="invoice").delete()
        self.assertEqual(self.alloc().reference_number, "T2N/INV/26/0106/00001")  # recreated with the standard format

    def test_when_the_number_carries_year_and_date_a_restart_adds_nothing_extra(self):
        DocumentNumberSequence.objects.filter(document_type="expense").update(
            reset_frequency="monthly", prefix="EXP", separator="-", min_length=3)  # standard segments: year and date
        self.assertEqual(self.alloc("expense", datetime.date(2026, 6, 1)).reference_number, "EXP-26-0106-001")
        self.assertEqual(self.alloc("expense", datetime.date(2026, 6, 20)).reference_number, "EXP-26-2006-002")
        self.assertEqual(self.alloc("expense", datetime.date(2026, 7, 1)).reference_number, "EXP-26-0107-001")
        DocumentNumberSequence.objects.filter(document_type="expense").update(reset_frequency="yearly")
        self.assertEqual(self.alloc("expense", datetime.date(2026, 7, 2)).reference_number, "EXP-26-0207-001")
        self.assertEqual(self.alloc("expense", datetime.date(2027, 1, 2)).reference_number, "EXP-27-0201-001")

    def test_monthly_and_yearly_resets(self):
        DocumentNumberSequence.objects.filter(document_type="expense").update(
            include_financial_year=False, include_short_year=False, date_format="",
            reset_frequency="monthly", prefix="EXP", separator="-", min_length=3)
        # without year and date in the number, a restart adds the period so it never reissues a reference
        self.assertEqual(self.alloc("expense", datetime.date(2026, 6, 1)).reference_number, "EXP-202606-001")
        self.assertEqual(self.alloc("expense", datetime.date(2026, 6, 20)).reference_number, "EXP-202606-002")
        self.assertEqual(self.alloc("expense", datetime.date(2026, 7, 1)).reference_number, "EXP-202607-001")
        DocumentNumberSequence.objects.filter(document_type="expense").update(reset_frequency="yearly")
        self.assertEqual(self.alloc("expense", datetime.date(2026, 7, 2)).reference_number, "EXP-2026-001")
        self.assertEqual(self.alloc("expense", datetime.date(2027, 1, 2)).reference_number, "EXP-2027-001")

    def test_failed_issue_does_not_consume_a_number(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.alloc()
                raise RuntimeError("PDF failed")
        self.assertEqual(self.alloc().sequence_value, 1)


class ConcurrencyTests(CompanyTestMixin, TransactionTestCase):
    """Numbers must stay unique and gap-free when many requests arrive at once."""

    def setUp(self):
        super().setUp()
        self.start_company()
        self.add_financial_year()
        self.seed_numbering()
        self.company = CompanyProfile.objects.get()

    def test_allocation_under_contention_is_unique_and_gap_free(self):
        def worker(_):
            try:
                return numbering.allocate_number(self.company, "invoice", on_date=datetime.date(2026, 6, 1)).reference_number
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=12) as pool:
            refs = list(pool.map(worker, range(36)))
        self.assertEqual(len(set(refs)), 36)
        self.assertEqual(sorted(refs), [f"T2N/INV/26/0106/{n:05d}" for n in range(1, 37)])
        self.assertEqual(DocumentNumberSequence.objects.get(document_type="invoice").next_value, 37)

    def test_concurrent_api_requests_get_distinct_numbers(self):
        def worker(_):
            try:
                r = self.issue("business_letter", client=self.api_finance, paragraphs=["Concurrent"])
                return r.status_code, r.json().get("reference_number")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(worker, range(12)))
        self.assertTrue(all(code == 201 for code, _ in results), results)
        refs = [ref for _, ref in results]
        self.assertEqual(len(set(refs)), 12)
        self.assertEqual(sorted(refs), [f"T2N/LTR/26/1508/{n:05d}" for n in range(1, 13)])


class FinancialYearTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def post(self, **kw):
        payload = dict(label="2026-27", start_date="2026-04-01", end_date="2027-03-31")
        payload.update(kw)
        return self.api.post(f"{API}/financial-years/", payload)

    def test_create_and_persist(self):
        r = self.post(is_current=True)
        self.assertEqual(r.status_code, 201, r.content)
        fy = FinancialYear.objects.get()
        self.assertEqual((fy.label, str(fy.start_date), fy.is_locked, fy.status), ("2026-27", "2026-04-01", False, "open"))

    def test_overlap_is_rejected(self):
        self.post()
        r = self.post(label="X", start_date="2026-12-01", end_date="2027-11-30")
        self.assertEqual(r.status_code, 400)
        self.assertIn("overlap", str(r.json()).lower())

    def test_database_enforces_non_overlap_independently_of_the_api(self):
        self.post()
        company = CompanyProfile.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinancialYear.objects.create(company=company, label="Sneaky", start_date=datetime.date(2026, 9, 1),
                                         end_date=datetime.date(2027, 8, 31))

    def test_adjacent_years_are_fine(self):
        self.post()
        self.assertEqual(self.post(label="2027-28", start_date="2027-04-01", end_date="2028-03-31").status_code, 201)

    def test_only_one_current_year(self):
        a = self.post(is_current=True).json()
        b = self.post(label="2027-28", start_date="2027-04-01", end_date="2028-03-31", is_current=True).json()
        rows = {r["id"]: r for r in self.api.get(f"{API}/financial-years/").json()}
        self.assertFalse(rows[a["id"]]["is_current"])
        self.assertTrue(rows[b["id"]]["is_current"])
        r = self.api.post(f"{API}/financial-years/{a['id']}/set-current/", {"change_reason": "Back-dated entries"})
        self.assertEqual(r.status_code, 200, r.content)
        rows = {r["id"]: r for r in self.api.get(f"{API}/financial-years/").json()}
        self.assertTrue(rows[a["id"]]["is_current"])
        self.assertFalse(rows[b["id"]]["is_current"])

    def test_dates_and_length_validation(self):
        self.assertEqual(self.post(end_date="2026-03-31").status_code, 400)
        r = self.post(end_date="2026-09-30")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.post(end_date="2026-09-30", irregular_period=True).status_code, 201)

    def test_duplicate_label_rejected(self):
        self.post()
        self.assertEqual(self.post(start_date="2028-04-01", end_date="2029-03-31").status_code, 400)

    def test_lock_needs_reason_and_blocks_date_changes(self):
        fy = self.post().json()
        self.assertEqual(self.api.post(f"{API}/financial-years/{fy['id']}/lock/", {}).status_code, 400)
        self.assertEqual(self.api.post(f"{API}/financial-years/{fy['id']}/lock/", {"change_reason": "Closed"}).status_code, 200)
        self.assertEqual(self.api.patch(f"{API}/financial-years/{fy['id']}/", {"end_date": "2027-03-30"}).status_code, 400)

    def test_close_year(self):
        fy = self.post(is_current=True).json()
        r = self.api.post(f"{API}/financial-years/{fy['id']}/close/", {"change_reason": "Audit signed off"})
        body = r.json()
        self.assertEqual((body["status"], body["is_locked"], body["is_current"]), ("closed", True, False))
