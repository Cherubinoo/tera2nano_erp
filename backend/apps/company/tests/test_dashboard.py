import datetime
from unittest import mock

from django.test import TestCase

from apps.company.models import IssuedDocument

from .base import API, CompanyTestMixin
from .test_quotations import ITEMS, quote


def today():
    return datetime.date.today()


class DashboardTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()
        self.add_financial_year(label="2025-26", start="2025-04-01", end="2026-03-31", current=False)
        self.add_financial_year(label="2026-27", start="2026-04-01", end="2027-03-31", current=True)
        self.add_financial_year(label="2027-28", start="2027-04-01", end="2028-03-31", current=False)
        self.seed_numbering()

    def issue_on(self, doc_type, date, **content):
        content = content or {"paragraphs": ["x"]}
        r = self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type=doc_type, issue_date=date.isoformat(), content=content,
            **({"letterhead_template": "OPS-001"} if doc_type == "operation_document" else {})))
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def get(self, client=None):
        r = (client or self.api_staff).get(f"{API}/dashboard/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_empty_company_gives_zeroes_and_six_months(self):
        d = self.get()
        self.assertEqual((d["month"]["documents"], d["month"]["quotations"], d["month"]["quotation_value"]), (0, 0, "0.00"))
        self.assertEqual(len(d["monthly"]), 6)
        self.assertEqual(d["monthly"][-1]["month"], f"{today().year}-{today().month:02d}")
        self.assertEqual((d["recent"], d["month"]["currency"]), ([], "INR"))
        self.assertEqual(d["month_label"], today().strftime("%B %Y"))

    def test_counts_by_kind_and_quotation_value_this_month(self):
        self.api_staff.get(f"{API}/letterhead-templates/", {"kind": "operation"})
        self.issue_on("business_letter", today())
        self.issue_on("offer_letter", today())
        self.issue_on("operation_document", today(), paragraphs=["work"])
        self.issue_on("quotation", today(), quotation=quote())
        self.issue_on("quotation", today(), quotation=quote(items=[{"description": "x", "quantity": "1", "rate": "100"}]))
        m = self.get()["month"]
        self.assertEqual((m["documents"], m["quotations"], m["letters"], m["operations"], m["minutes"]), (5, 2, 2, 1, 0))
        self.assertEqual(m["quotation_value"], "2400.50")  # 2,300.50 + 100.00, no GST switched on

    def test_a_plain_text_quotation_counts_but_has_no_value(self):
        self.issue_on("quotation", today(), paragraphs=["Lump sum quotation"])
        m = self.get()["month"]
        self.assertEqual((m["quotations"], m["quotation_value"]), (1, "0.00"))

    def test_void_documents_are_left_out(self):
        doc = self.issue_on("business_letter", today())
        self.api.post(f"{API}/issued-documents/{doc['id']}/void/", {"change_reason": "Mistake"})
        self.assertEqual(self.get()["month"]["documents"], 0)
        self.assertEqual(self.get()["recent"], [])

    def test_other_months_show_in_the_trend_but_not_in_this_month(self):
        first = today().replace(day=1)
        last_month = first - datetime.timedelta(days=1)
        old = first - datetime.timedelta(days=200)
        if old.year < 2025 or (old.year, old.month) < (2025, 4):
            self.skipTest("test years not set up for this date")
        self.issue_on("business_letter", last_month)
        self.issue_on("business_letter", last_month)
        self.issue_on("business_letter", today())
        d = self.get()
        self.assertEqual(d["month"]["documents"], 1)
        trend = {m["month"]: m["count"] for m in d["monthly"]}
        self.assertEqual(trend[f"{last_month.year}-{last_month.month:02d}"], 2)
        self.assertEqual(trend[f"{today().year}-{today().month:02d}"], 1)
        self.assertEqual(sum(trend.values()), 3)

    def test_recent_is_newest_first_limited_to_eight_and_has_what_the_page_needs(self):
        for _ in range(10):
            self.issue_on("business_letter", today())
        recent = self.get()["recent"]
        self.assertEqual(len(recent), 8)
        refs = [r["reference_number"] for r in recent]
        self.assertEqual(refs, sorted(refs, reverse=True))
        self.assertEqual(set(recent[0]), {"id", "reference_number", "title", "document_type", "document_type_label",
                                          "issue_date", "has_print_file", "generated_by_name"})
        self.assertEqual((recent[0]["document_type_label"], recent[0]["has_print_file"]), ("Business letter", True))

    def test_available_to_every_signed_in_role_and_not_to_anyone_else(self):
        self.assertEqual(self.api_finance.get(f"{API}/dashboard/").status_code, 200)
        self.assertEqual(self.anon.get(f"{API}/dashboard/").status_code, 401)
