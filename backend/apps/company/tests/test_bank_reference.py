import datetime

from django.test import TestCase

from apps.company.models import CompanyBankAccount, CompanyProfileAuditEvent, IssuedReferenceNumber

from .base import API, CompanyTestMixin

NUMBER = "123456789012"


def stamp(today=None):
    today = today or datetime.date.today()
    return f"{today.year % 100:02d}-{today.day:02d}{today.month:02d}"


class BankReferenceCodeTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def test_each_new_account_gets_its_own_reference_with_year_and_date(self):
        a = self.add_bank().json()
        b = self.add_bank(display_name="Second", account_number="998877665544").json()
        code = self.api.get(f"{API}/profile/").json()["short_code"] or "T2N"
        self.assertRegex(a["reference_code"], rf"^[A-Z0-9]+-BNK-{stamp()}-0001$")
        self.assertRegex(b["reference_code"], rf"^[A-Z0-9]+-BNK-{stamp()}-0002$")
        self.assertNotEqual(a["reference_code"], b["reference_code"])
        self.assertTrue(code)

    def test_reference_is_visible_to_staff_and_cannot_be_edited(self):
        a = self.add_bank().json()
        rows = self.api_staff.get(f"{API}/bank-accounts/").json()
        self.assertEqual(rows[0]["reference_code"], a["reference_code"])
        r = self.api_finance.patch(f"{API}/bank-accounts/{a['id']}/", {"reference_code": "HACK-1", "branch": "New"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["reference_code"], a["reference_code"])
        self.assertEqual(r.json()["branch"], "New")

    def test_reference_is_not_reused_after_deactivating(self):
        a = self.add_bank().json()
        self.api_finance.post(f"{API}/bank-accounts/{a['id']}/deactivate/", {"change_reason": "Closed"})
        b = self.add_bank(account_number="998877665544").json()
        self.assertTrue(b["reference_code"].endswith("-0002"))

    def test_reference_is_recorded_as_an_issued_number(self):
        a = self.add_bank().json()
        self.assertTrue(IssuedReferenceNumber.objects.filter(
            document_type="bank_account", reference_number=a["reference_code"]).exists())

    def test_accounts_that_predate_references_get_one_oldest_first_and_it_is_audited(self):
        old = self.add_bank().json()
        newer = self.add_bank(display_name="Second", account_number="998877665544").json()
        CompanyBankAccount.objects.update(reference_code="")
        IssuedReferenceNumber.objects.filter(document_type="bank_account").delete()
        from apps.company.models import DocumentNumberSequence
        DocumentNumberSequence.objects.filter(document_type="bank_account").update(next_value=1, current_period="")
        rows = {r["id"]: r for r in self.api_staff.get(f"{API}/bank-accounts/").json()}
        self.assertTrue(rows[old["id"]]["reference_code"].endswith("-0001"))
        self.assertTrue(rows[newer["id"]]["reference_code"].endswith("-0002"))
        again = {r["id"]: r for r in self.api_staff.get(f"{API}/bank-accounts/").json()}
        self.assertEqual(rows[old["id"]]["reference_code"], again[old["id"]]["reference_code"])
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(
            entity_type="bank_account", reason="Reference code assigned").exists())

    def test_creating_an_account_still_works_when_it_is_the_first_use_of_the_sequence(self):
        self.assertFalse(IssuedReferenceNumber.objects.filter(document_type="bank_account").exists())
        self.assertEqual(self.add_bank().status_code, 201)
