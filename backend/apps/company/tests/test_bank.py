import json

from django.db import connection
from django.test import TestCase

from apps.company.models import CompanyBankAccount, CompanyProfileAuditEvent

from .base import API, CompanyTestMixin

NUMBER = "123456789012"


class BankAccountTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()  # note: no bank account is needed to create the company

    def make(self, **over):
        r = self.add_bank(**over)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def verify_ready(self, bank_id):
        """Attach evidence so the account can be verified."""
        doc = self.upload_document(client=self.api_finance, bank_account=bank_id).json()
        r = self.api_finance.patch(f"{API}/bank-accounts/{bank_id}/", {"supporting_document": doc["id"]})
        self.assertEqual(r.status_code, 200, r.content)

    def test_company_exists_without_any_bank_account(self):
        self.assertEqual(self.api.get(f"{API}/bank-accounts/").json(), [])
        self.assertEqual(self.api.get(f"{API}/profile/").status_code, 200)

    def test_full_number_is_never_returned_or_stored_in_plaintext(self):
        bank = self.make()
        self.assertEqual(bank["account_number_masked"], "XXXXXX9012")
        for client in (self.api, self.api_finance, self.api_staff):
            body = client.get(f"{API}/bank-accounts/").content.decode()
            self.assertNotIn(NUMBER, body)
            self.assertNotIn(NUMBER, client.get(f"{API}/bank-accounts/{bank['id']}/").content.decode())
        with connection.cursor() as cur:
            cur.execute("select * from company_companybankaccount")
            self.assertNotIn(NUMBER, json.dumps(cur.fetchall(), default=str))
        row = CompanyBankAccount.objects.get()
        self.assertNotEqual(row.account_number_encrypted, NUMBER)
        self.assertEqual(row.reveal_account_number(), NUMBER)

    def test_ordinary_employees_see_only_a_masked_summary(self):
        self.make()
        row = self.api_staff.get(f"{API}/bank-accounts/").json()[0]
        for hidden in ("ifsc_code", "account_holder_name", "supporting_document"):
            self.assertNotIn(hidden, row)
        self.assertEqual(row["account_number_masked"], "XXXXXX9012")
        full = self.api_finance.get(f"{API}/bank-accounts/").json()[0]
        self.assertEqual(full["ifsc_code"], "HDFC0001234")

    def test_only_finance_can_create_or_change(self):
        self.assertEqual(self.add_bank(client=self.api_staff).status_code, 403)
        bank = self.make()
        self.assertEqual(self.api_staff.patch(f"{API}/bank-accounts/{bank['id']}/", {"branch": "x"}).status_code, 403)
        self.assertEqual(self.api_staff.post(f"{API}/bank-accounts/{bank['id']}/reveal/").status_code, 403)
        self.assertEqual(self.add_bank(client=self.anon).status_code, 401)

    def test_validation(self):
        self.assertEqual(self.add_bank(number="12AB").status_code, 400)
        self.assertEqual(self.add_bank(ifsc_code="BAD").status_code, 400)
        self.assertEqual(self.api_finance.post(f"{API}/bank-accounts/", dict(
            display_name="x", bank_name="b", account_holder_name="h", ifsc_code="HDFC0001234")).status_code, 400)

    def test_duplicate_account_rejected(self):
        self.make()
        self.assertEqual(self.add_bank(display_name="Again").status_code, 400)

    def test_reveal_is_audited_without_logging_the_number(self):
        bank = self.make()
        r = self.api_finance.post(f"{API}/bank-accounts/{bank['id']}/reveal/", {"change_reason": "Payroll setup"})
        self.assertEqual(r.json()["account_number"], NUMBER)
        self.assertEqual(r["Cache-Control"], "no-store")
        event = CompanyProfileAuditEvent.objects.get(action="revealed")
        self.assertEqual(event.actor_id, self.finance.pk)
        self.assertNotIn(NUMBER, json.dumps([event.new_values, event.previous_values, event.reason]))

    def test_audit_trail_never_contains_the_account_number(self):
        bank = self.make()
        self.api_finance.patch(f"{API}/bank-accounts/{bank['id']}/", {"account_number": "999988887777"})
        blob = json.dumps(list(CompanyProfileAuditEvent.objects.values(
            "previous_values", "new_values", "reason", "changed_fields")), default=str)
        for secret in (NUMBER, "999988887777"):
            self.assertNotIn(secret, blob)
        self.assertIn("XXXXXX9012", blob)  # the masked form is fine

    def test_bank_details_shown_on_documents_only_when_verified(self):
        bank = self.make()
        r = self.api_finance.patch(f"{API}/bank-accounts/{bank['id']}/", {"show_on_invoices": True})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api_finance.post(f"{API}/bank-accounts/{bank['id']}/verify/", {}).status_code, 400)  # no evidence
        self.verify_ready(bank["id"])
        r = self.api_admin2.post(f"{API}/bank-accounts/{bank['id']}/verify/", {})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["verification_status"], "verified")
        r = self.api_finance.patch(f"{API}/bank-accounts/{bank['id']}/", {"show_on_invoices": True})
        self.assertEqual(r.status_code, 200, r.content)

    def test_maker_checker_blocks_self_verification(self):
        bank = self.make()
        self.verify_ready(bank["id"])
        r = self.api_finance.post(f"{API}/bank-accounts/{bank['id']}/verify/", {})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(CompanyBankAccount.objects.get().verification_status, "unverified")
        self.assertEqual(self.api.post(f"{API}/bank-accounts/{bank['id']}/verify/", {}).status_code, 200)

    def test_changing_payment_details_resets_verification(self):
        bank = self.make()
        self.verify_ready(bank["id"])
        self.api_admin2.post(f"{API}/bank-accounts/{bank['id']}/verify/", {})
        self.api_admin2.patch(f"{API}/bank-accounts/{bank['id']}/", {"show_on_invoices": True})
        self.complete_setup()
        r = self.api_finance.patch(f"{API}/bank-accounts/{bank['id']}/", {"ifsc_code": "ICIC0004321"})
        self.assertEqual(r.status_code, 400)  # established payment details need a reason once setup is done
        r = self.api_finance.patch(f"{API}/bank-accounts/{bank['id']}/",
                                   {"ifsc_code": "ICIC0004321", "change_reason": "Branch merger"})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["verification_status"], "pending")
        self.assertFalse(body["show_on_invoices"])

    def test_one_primary_account_and_deactivation_clears_flags(self):
        a = self.make(is_primary=True)
        b = self.make(display_name="Second", number="555566667777", is_primary=True)
        rows = {r["id"]: r for r in self.api_finance.get(f"{API}/bank-accounts/").json()}
        self.assertFalse(rows[a["id"]]["is_primary"])
        r = self.api_finance.post(f"{API}/bank-accounts/{b['id']}/deactivate/", {"change_reason": "Closed"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["is_primary"])

    def test_sole_finance_user_can_self_verify_with_a_record_of_it(self):
        from apps.accounts.models import User
        User.objects.exclude(pk=self.admin.pk).update(is_active=False)
        bank = self.api.post(f"{API}/bank-accounts/", dict(
            display_name="Only", bank_name="B", account_holder_name="H", account_number="111122223333",
            ifsc_code="HDFC0001234")).json()
        doc = self.upload_document(bank_account=bank["id"]).json()
        self.api.patch(f"{API}/bank-accounts/{bank['id']}/", {"supporting_document": doc["id"]})
        self.assertEqual(self.api.post(f"{API}/bank-accounts/{bank['id']}/verify/", {}).status_code, 200)
        event = CompanyProfileAuditEvent.objects.filter(action="verified").latest("occurred_at")
        self.assertIn("self-verified", event.reason)
