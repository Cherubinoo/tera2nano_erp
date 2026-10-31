import datetime
import io

from django.contrib.auth import get_user_model
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.core import india

User = get_user_model()
PASSWORD = "Str0ng-Test-Pass!"
API = "/api/v1/company"

PAN = "ABCDE1234F"


def make_gstin(state="29", pan=PAN):
    first14 = f"{state}{pan}1Z"
    return first14 + india.gstin_check_char(first14)


GSTIN = make_gstin()


def png_bytes(size=(120, 40), colour=(31, 58, 95)):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def pdf_stub():
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def make_user(username, role):
    return User.objects.create_user(username=username, email=f"{username}@example.com", password=PASSWORD, role=role)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


class CompanyTestMixin:
    """Creates admin/finance/staff users and helpers to build a configured company."""

    def setUp(self):
        super().setUp()
        self.admin = make_user("admin", Role.ADMIN)
        self.admin2 = make_user("admin2", Role.ADMIN)
        self.finance = make_user("finance", Role.FINANCE)
        self.staff = make_user("staff", Role.STAFF)
        self.api = client_for(self.admin)
        self.api_admin2 = client_for(self.admin2)
        self.api_finance = client_for(self.finance)
        self.api_staff = client_for(self.staff)
        self.anon = APIClient()

    # -- builders -----------------------------------------------------------
    def start_company(self, client=None):
        r = (client or self.api).post(
            f"{API}/setup/start/", {"legal_name": "Example Ventures LLP", "display_name": "Example Ventures"}
        )
        self.assertIn(r.status_code, (200, 201), r.content)
        return r

    def complete_setup(self):
        r = self.api.post(f"{API}/setup/complete/", {})
        self.assertEqual(r.status_code, 200, r.content)
        return r

    def add_address(self, **over):
        payload = dict(label="Registered office", address_type="registered_office", line1="12 Industrial Road",
                       city="Bengaluru", state="Karnataka", state_code="29", postal_code="560001",
                       is_primary=True)
        payload.update(over)
        r = self.api.post(f"{API}/addresses/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def add_financial_year(self, label="2026-27", start="2026-04-01", end="2027-03-31", current=True, **over):
        payload = dict(label=label, start_date=start, end_date=end, is_current=current)
        payload.update(over)
        r = self.api.post(f"{API}/financial-years/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def seed_numbering(self, short_code="T2N"):
        r = self.api.post(f"{API}/document-numbering/seed/", {"short_code": short_code})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def upload_document(self, client=None, **over):
        data = dict(document_type="bank_verification", title="Bank letter", access_classification="confidential")
        data.update(over)
        from django.core.files.uploadedfile import SimpleUploadedFile
        data["file"] = SimpleUploadedFile("proof.pdf", pdf_stub(), content_type="application/pdf")
        r = (client or self.api).post(f"{API}/documents/", data, format="multipart")
        return r

    def add_bank(self, client=None, number="123456789012", **over):
        payload = dict(display_name="Operating account", bank_name="Example Bank", branch="MG Road",
                       account_holder_name="Example Ventures LLP", account_number=number, ifsc_code="HDFC0001234",
                       account_type="current")
        payload.update(over)
        return (client or self.api_finance).post(f"{API}/bank-accounts/", payload)

    def full_company(self):
        """Company with address, contact, FY, numbering, policy, completed setup."""
        self.start_company()
        self.add_address(document_types=[])
        self.api.post(f"{API}/contacts/", dict(contact_type="finance", display_name="Accounts", email="accounts@example.com",
                                               is_primary=True))
        self.add_financial_year()
        self.seed_numbering()
        r = self.api.post(f"{API}/policies/", dict(payment_terms="Net 30", payment_terms_days=30, effective_from="2026-04-01",
                                                   invoice_terms="Late fee applies.", document_footer_text="Example Ventures LLP"))
        self.assertEqual(r.status_code, 201, r.content)
        r = self.api.post(f"{API}/policies/{r.json()['id']}/activate/", {})
        self.assertEqual(r.status_code, 200, r.content)
        self.complete_setup()

    def issue(self, doc_type="business_letter", client=None, **content):
        content.setdefault("paragraphs", ["Hello."])
        return (client or self.api_finance).post(
            f"{API}/issued-documents/", dict(document_type=doc_type, content=content, issue_date="2026-08-15")
        )
