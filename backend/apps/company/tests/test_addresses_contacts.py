from django.test import TestCase

from apps.company.models import CompanyAddress

from .base import API, CompanyTestMixin


class AddressTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def test_create_read_update_persist(self):
        a = self.add_address()
        self.assertEqual(self.api_staff.get(f"{API}/addresses/").json()[0]["id"], a["id"])
        r = self.api.patch(f"{API}/addresses/{a['id']}/", {"line2": "Suite 4", "effective_from": "2024-01-15"})
        self.assertEqual(r.status_code, 200, r.content)
        row = CompanyAddress.objects.get(pk=a["id"])
        self.assertEqual(row.line2, "Suite 4")
        self.assertEqual(str(row.id), a["id"])

    def test_validation(self):
        base = dict(label="HQ", address_type="corporate_office", line1="1 Road", city="Pune", state="Maharashtra")
        self.assertEqual(self.api.post(f"{API}/addresses/", {**base, "postal_code": "12345"}).status_code, 400)
        self.assertEqual(self.api.post(f"{API}/addresses/", {**base, "postal_code": "411001", "state_code": "55"}).status_code, 400)
        r = self.api.post(f"{API}/addresses/", {**base, "postal_code": "411001", "effective_from": "2026-05-01",
                                                 "effective_to": "2026-01-01"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api.post(f"{API}/addresses/", {**base, "postal_code": "411001", "address_type": "moon"}).status_code, 400)

    def test_only_one_primary_address(self):
        first = self.add_address(label="A", address_type="corporate_office", is_primary=True)
        second = self.add_address(label="B", address_type="branch_office", is_primary=True)
        rows = {r["id"]: r for r in self.api.get(f"{API}/addresses/").json()}
        self.assertFalse(rows[first["id"]]["is_primary"])
        self.assertTrue(rows[second["id"]]["is_primary"])

    def test_one_active_registered_office(self):
        self.add_address()
        r = self.api.post(f"{API}/addresses/", dict(label="Old", address_type="registered_office", line1="x", city="c",
                                                    state="s", postal_code="560002"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("registered office", str(r.json()).lower())

    def test_documents_pick_the_configured_address(self):
        reg = self.add_address(is_primary=True)
        branch = self.add_address(label="Billing", address_type="billing", is_primary=False,
                                  document_types=["invoice", "quotation"])
        inv = self.api.get(f"{API}/addresses/for-document/?document_type=invoice").json()
        self.assertEqual(inv["id"], branch["id"])
        letter = self.api.get(f"{API}/addresses/for-document/?document_type=business_letter").json()
        self.assertEqual(letter["id"], reg["id"])  # falls back to the primary address

    def test_same_document_type_cannot_use_two_addresses(self):
        self.add_address(is_primary=True)
        self.add_address(label="Billing", address_type="billing", is_primary=False, document_types=["invoice"])
        r = self.api.post(f"{API}/addresses/", dict(label="B2", address_type="branch_office", line1="x", city="c",
                                                    state="s", postal_code="560003", document_types=["invoice"]))
        self.assertEqual(r.status_code, 400)
        self.assertIn("document_types", r.json())

    def test_invalid_document_type_rejected(self):
        r = self.api.post(f"{API}/addresses/", dict(label="B", address_type="branch_office", line1="x", city="c",
                                                    state="s", postal_code="560003", document_types=["selfie"]))
        self.assertEqual(r.status_code, 400)

    def test_deactivate_needs_reason_and_keeps_record(self):
        a = self.add_address(address_type="branch_office")
        self.assertEqual(self.api.post(f"{API}/addresses/{a['id']}/deactivate/", {}).status_code, 400)
        r = self.api.post(f"{API}/addresses/{a['id']}/deactivate/", {"change_reason": "Office closed"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()["is_active"])
        self.assertFalse(r.json()["is_primary"])
        self.assertEqual(self.api.get(f"{API}/addresses/?active=false").json()[0]["id"], a["id"])
        self.assertEqual(self.api.post(f"{API}/addresses/{a['id']}/activate/", {}).status_code, 200)

    def test_staff_cannot_write(self):
        r = self.api_staff.post(f"{API}/addresses/", dict(label="B", address_type="branch_office", line1="x", city="c",
                                                          state="s", postal_code="560003"))
        self.assertEqual(r.status_code, 403)

    def test_unreferenced_address_can_be_deleted(self):
        a = self.add_address(address_type="branch_office")
        self.assertEqual(self.api.delete(f"{API}/addresses/{a['id']}/").status_code, 204)
        self.assertFalse(CompanyAddress.objects.filter(pk=a["id"]).exists())


class ContactTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def contact(self, **over):
        payload = dict(contact_type="finance", display_name="Accounts", email="accounts@example.com", is_primary=True)
        payload.update(over)
        r = self.api.post(f"{API}/contacts/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_needs_email_or_phone(self):
        r = self.api.post(f"{API}/contacts/", dict(contact_type="sales", display_name="Sales"))
        self.assertEqual(r.status_code, 400)

    def test_one_primary_per_type(self):
        a = self.contact(display_name="First")
        b = self.contact(display_name="Second")
        rows = {r["id"]: r for r in self.api.get(f"{API}/contacts/").json()}
        self.assertFalse(rows[a["id"]]["is_primary"])
        self.assertTrue(rows[b["id"]]["is_primary"])
        other = self.contact(contact_type="sales", display_name="Sales", email="s@example.com")
        self.assertTrue(self.api.get(f"{API}/contacts/{other['id']}/").json()["is_primary"])

    def test_document_type_selects_the_right_contact(self):
        fin = self.contact()
        sales = self.contact(contact_type="sales", display_name="Sales", email="sales@example.com")
        hr = self.contact(contact_type="hr", display_name="HR", email="hr@example.com")
        pick = lambda t: self.api.get(f"{API}/contacts/for-document/?document_type={t}").json()["id"]
        self.assertEqual(pick("invoice"), fin["id"])
        self.assertEqual(pick("quotation"), sales["id"])
        self.assertEqual(pick("offer_letter"), hr["id"])
        # an explicit assignment overrides the natural mapping
        self.api.patch(f"{API}/contacts/{hr['id']}/", {"document_types": ["quotation"]})
        self.assertEqual(pick("quotation"), hr["id"])

    def test_falls_back_to_general_then_none(self):
        self.assertEqual(self.api.get(f"{API}/contacts/for-document/?document_type=invoice").status_code, 404)
        gen = self.contact(contact_type="general", display_name="Front desk", email="hello@example.com")
        self.assertEqual(self.api.get(f"{API}/contacts/for-document/?document_type=invoice").json()["id"], gen["id"])
