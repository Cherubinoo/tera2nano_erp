"""Email: server status and settings, test email, the send dialog for invoices, quotations and letters (with the PDF attached),
permissions, failures that are logged and never undo the business action, sending again, and the Sent emails log.

The test runner always uses Django's in-memory mail backend (mail.outbox). "Configured" means EMAIL_HOST is set; without it the
message still goes through the mail system but its status is "saved" (the app says email is not set up)."""
import smtplib

from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.test import override_settings

from apps.accounts.models import Role
from apps.company.models import CompanyProfile, CompanyProfileAuditEvent, IssuedDocument
from apps.company.tests.base import API, client_for, grant, make_user
from apps.emails import services
from apps.emails.models import EmailMessage, EmailSettings
from apps.sales.models import SalesDocument
from apps.sales.tests.test_sales import S, SalesBase

E = "/api/v1/email"
SMTP = override_settings(EMAIL_HOST="smtp.example.com", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")


class RefusingBackend(BaseEmailBackend):
    """A mail server that refuses the sign-in."""

    def send_messages(self, messages):
        raise smtplib.SMTPAuthenticationError(535, b"Authentication failed")


class DownBackend(BaseEmailBackend):
    def send_messages(self, messages):
        raise ConnectionRefusedError("connection refused")


REFUSING = override_settings(EMAIL_HOST="smtp.example.com", EMAIL_BACKEND="apps.emails.tests.test_emails.RefusingBackend")
DOWN = override_settings(EMAIL_HOST="smtp.example.com", EMAIL_BACKEND="apps.emails.tests.test_emails.DownBackend")


class EmailBase(SalesBase):
    def setUp(self):
        super().setUp()
        self.company = CompanyProfile.objects.get()
        self.buyer = self.customer("Bright Buyers", email="accounts@brightbuyers.example")
        mail.outbox = []

    def draft_for(self, kind, obj_id, client=None, expect=200):
        r = (client or self.fin).get(f"{E}/draft/", {"kind": kind, "id": obj_id})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def send(self, kind, obj_id, client=None, expect=201, **over):
        body = {"kind": kind, "id": obj_id, "to": ["accounts@brightbuyers.example"], "subject": "Your invoice", "body": "Hello.", **over}
        r = (client or self.fin).post(f"{E}/send/", body, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()


class StatusAndSettingsTests(EmailBase):
    def test_without_a_mail_server_the_app_says_email_is_not_set_up(self):
        r = self.api_staff.get(f"{E}/status/").json()
        self.assertFalse(r["configured"])
        self.assertEqual(r["message"], services.NOT_SET_UP)
        self.assertNotIn("password", str(r).lower())

    @override_settings(EMAIL_HOST="smtp.example.com", EMAIL_PORT=465, EMAIL_USE_SSL=True, EMAIL_USE_TLS=False,
                       EMAIL_HOST_USER="mailer", EMAIL_HOST_PASSWORD="s3cret-pass")
    def test_with_a_mail_server_the_status_shows_host_but_never_user_or_password(self):
        r = self.api.get(f"{E}/status/").json()
        self.assertEqual((r["configured"], r["host"], r["port"], r["security"], r["message"]), (True, "smtp.example.com", 465, "SSL", ""))
        self.assertNotIn("s3cret-pass", str(r))
        self.assertNotIn("mailer", str(r))
        settings_page = self.api.get(f"{E}/settings/").json()
        self.assertNotIn("s3cret-pass", str(settings_page))

    def test_settings_are_saved_per_company_validated_and_audited(self):
        r = self.api.patch(f"{E}/settings/", {"sender_name": "  Example   Accounts ", "reply_to": "billing@example.com",
                                             "signature": "Asha Rao\nAccounts team"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["sender_name"], body["reply_to"], body["signature"]), ("Example Accounts", "billing@example.com", "Asha Rao\nAccounts team"))
        self.assertIn("Example Accounts", body["from_address"])
        self.assertEqual(EmailSettings.objects.get().company, self.company)
        ev = CompanyProfileAuditEvent.objects.filter(entity_type="email_settings").latest("occurred_at")
        self.assertEqual(set(ev.changed_fields), {"sender_name", "reply_to", "signature"})
        for bad, field in (({"reply_to": "not-an-address"}, "reply_to"), ({"sender_name": "x" * 151}, "sender_name"),
                           ({"signature": "x" * 2001}, "signature"), ({"auto_reminders": "yes"}, "auto_reminders")):
            r = self.api.patch(f"{E}/settings/", bad, format="json")
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json())
        self.assertFalse(hasattr(EmailSettings, "password") or any("password" in f.name for f in EmailSettings._meta.fields))

    def test_who_may_change_the_settings(self):
        # an employee may not even read them; finance (sales.manage) may switch automatic reminders only
        self.assertEqual(self.api_staff.get(f"{E}/settings/").status_code, 403)
        self.assertEqual(self.api_staff.patch(f"{E}/settings/", {"auto_reminders": True}, format="json").status_code, 403)
        r = self.fin.get(f"{E}/settings/").json()
        self.assertEqual((r["can_edit"], r["can_edit_reminders"]), (False, True))
        self.assertEqual(self.fin.patch(f"{E}/settings/", {"sender_name": "Me"}, format="json").status_code, 400)
        r = self.fin.patch(f"{E}/settings/", {"auto_reminders": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(EmailSettings.objects.get().auto_reminders)
        for role in (Role.MANAGING_PARTNER, Role.PARTNER):  # partners always hold email.manage
            client = client_for(make_user(f"u-{role}", role))
            self.assertEqual(client.patch(f"{E}/settings/", {"sender_name": "P"}, format="json").status_code, 200)

    def test_test_email_goes_to_the_given_address_and_is_logged(self):
        r = self.api.post(f"{E}/test/", {"to": "me@example.com"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["status"], "saved")  # no mail server: saved, not delivered
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["me@example.com"])
        with SMTP:
            r = self.api.post(f"{E}/test/", {}, format="json")  # defaults to the sender's own address
        self.assertEqual((r.json()["status"], r.json()["to"]), ("sent", ["admin@example.com"]))
        self.assertEqual(EmailMessage.objects.filter(kind="test").count(), 2)
        self.assertEqual(self.fin.post(f"{E}/test/", {"to": "x@example.com"}, format="json").status_code, 403)
        self.assertEqual(self.api.post(f"{E}/test/", {"to": "nope"}, format="json").status_code, 400)


class InvoiceEmailTests(EmailBase):
    def setUp(self):
        super().setUp()
        self.inv = self.invoice(self.buyer, issue_date="2026-08-15", due_date="2026-09-14")

    def test_the_dialog_is_prefilled_with_the_customer_the_number_and_the_pdf(self):
        self.api.patch(f"{E}/settings/", {"signature": "Asha Rao\nAccounts"}, format="json")
        d = self.draft_for("sales_document", self.inv["id"])
        self.assertEqual(d["to"], ["accounts@brightbuyers.example"])
        self.assertIn(self.inv["number"], d["subject"])
        self.assertIn("Dear Bright Buyers", d["body"])
        self.assertIn("is due by 14 Sep 2026", d["body"])
        self.assertTrue(d["body"].endswith("Asha Rao\nAccounts"))
        self.assertEqual(d["attachments"], [self.inv["number"].replace("/", "-") + ".pdf"])
        self.assertFalse(d["server"]["configured"])
        self.assertEqual(d["history"], [])

    @SMTP
    def test_sending_attaches_the_issued_pdf_and_logs_everything(self):
        self.api.patch(f"{E}/settings/", {"sender_name": "Example Accounts", "reply_to": "billing@example.com"}, format="json")
        sent = self.send("sales_document", self.inv["id"], cc="boss@brightbuyers.example; second@brightbuyers.example",
                         subject="Invoice attached", body="Dear customer,\nplease pay.")
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual((m.to, m.cc, m.subject, m.reply_to), (["accounts@brightbuyers.example"],
                                                                ["boss@brightbuyers.example", "second@brightbuyers.example"],
                                                                "Invoice attached", ["billing@example.com"]))
        self.assertIn("Example Accounts", m.from_email)
        name, content, mime = m.attachments[0]
        doc = SalesDocument.objects.get(pk=self.inv["id"])
        with doc.issued_document.file.open("rb") as fh:
            self.assertEqual(content, fh.read())
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertEqual((name, mime), (self.inv["number"].replace("/", "-") + ".pdf", "application/pdf"))
        row = EmailMessage.objects.get()
        self.assertEqual((row.kind, row.object_id, row.url, row.sent_by, row.status, row.body),
                         ("sales_document", self.inv["id"], f"/sales/documents/{self.inv['id']}", self.finance, "sent", "Dear customer,\nplease pay."))
        self.assertIn(self.inv["number"], row.object_label)
        self.assertEqual(self.draft_for("sales_document", self.inv["id"])["history"][0]["id"], str(row.pk))

    def test_only_people_who_manage_sales_may_send_and_drafts_cannot_be_sent(self):
        viewer = client_for(make_user("viewer", Role.PROJECT_MANAGER))  # holds sales.view, not sales.manage
        self.draft_for("sales_document", self.inv["id"], client=viewer, expect=403)
        self.send("sales_document", self.inv["id"], client=viewer, expect=403)
        self.send("sales_document", self.inv["id"], client=self.api_staff, expect=403)
        draft = self.draft(self.buyer)
        r = self.fin.get(f"{E}/draft/", {"kind": "sales_document", "id": draft["id"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Issue the document first", str(r.json()))
        self.send("sales_document", "00000000-0000-0000-0000-000000000000", expect=404)
        self.send("unknown", self.inv["id"], expect=400)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(EmailMessage.objects.count(), 0)

    def test_bad_addresses_and_empty_text_are_refused_before_anything_is_sent(self):
        for over, field in (({"to": []}, "to"), ({"to": ["not an address"]}, "to"), ({"cc": "a@b.example, broken@"}, "cc"),
                            ({"subject": "  "}, "subject"), ({"body": ""}, "body"),
                            ({"to": [f"p{i}@example.com" for i in range(21)]}, "to")):
            r = self.send("sales_document", self.inv["id"], expect=400, **over)
            self.assertIn(field, r, over)
        self.assertEqual((len(mail.outbox), EmailMessage.objects.count()), (0, 0))

    def test_a_subject_with_line_breaks_is_flattened(self):
        self.send("sales_document", self.inv["id"], subject="Invoice\r\nBcc: victim@example.com")
        self.assertEqual(mail.outbox[0].subject, "Invoice Bcc: victim@example.com")
        self.assertEqual(mail.outbox[0].bcc, [])

    @REFUSING
    def test_a_refused_sign_in_is_logged_as_failed_and_the_invoice_is_untouched(self):
        sent = self.send("sales_document", self.inv["id"])
        self.assertEqual(sent["status"], "failed")
        self.assertIn("refused the sign-in", sent["error"])
        self.assertTrue(sent["can_resend"])
        self.assertEqual(SalesDocument.objects.get(pk=self.inv["id"]).status, "issued")
        row = EmailMessage.objects.get()
        self.assertEqual((row.status, row.attachments), ("failed", [self.inv["number"].replace("/", "-") + ".pdf"]))

    @DOWN
    def test_an_unreachable_server_is_explained(self):
        sent = self.send("sales_document", self.inv["id"])
        self.assertEqual(sent["status"], "failed")
        self.assertIn("Could not connect to the mail server", sent["error"])

    def test_a_missing_pdf_file_is_logged_as_a_failure(self):
        doc = SalesDocument.objects.select_related("issued_document").get(pk=self.inv["id"])
        doc.issued_document.file.storage.delete(doc.issued_document.file.name)
        with SMTP:
            sent = self.send("sales_document", self.inv["id"])
        self.assertEqual(sent["status"], "failed")
        self.assertIn("PDF to attach is missing", sent["error"])
        self.assertEqual(len(mail.outbox), 0)

    def test_send_again_fetches_the_pdf_again_and_links_the_new_message(self):
        with REFUSING:
            failed = self.send("sales_document", self.inv["id"])
        with SMTP:
            r = self.api.post(f"{E}/messages/{failed['id']}/resend/")
        self.assertEqual(r.status_code, 201, r.content)
        again = r.json()
        self.assertEqual((again["status"], again["resend_of"]), ("sent", failed["id"]))
        self.assertTrue(mail.outbox[0].attachments[0][1].startswith(b"%PDF"))
        listing = self.api.get(f"{E}/messages/").json()
        old = next(x for x in listing["results"] if x["id"] == failed["id"])
        self.assertTrue(old["resent"])
        # only failed messages; only people who hold email.manage
        self.assertEqual(self.api.post(f"{E}/messages/{again['id']}/resend/").status_code, 400)
        self.assertEqual(self.fin.post(f"{E}/messages/{failed['id']}/resend/").status_code, 403)


class LogTests(EmailBase):
    def test_the_log_lists_searches_and_filters_and_needs_email_manage(self):
        inv = self.invoice(self.buyer)
        self.send("sales_document", inv["id"], subject="August invoice")
        with REFUSING:
            self.send("sales_document", inv["id"], subject="Second try", to=["other@elsewhere.example"])
        self.api.post(f"{E}/test/", {"to": "me@example.com"}, format="json")
        r = self.api.get(f"{E}/messages/").json()
        self.assertEqual(r["count"], 3)
        self.assertEqual(r["counts"], {"sent": 0, "saved": 2, "failed": 1})
        self.assertEqual([m["subject"] for m in self.api.get(f"{E}/messages/", {"status": "failed"}).json()["results"]], ["Second try"])
        self.assertEqual(self.api.get(f"{E}/messages/", {"q": "elsewhere"}).json()["count"], 1)
        self.assertEqual(self.api.get(f"{E}/messages/", {"q": "August"}).json()["count"], 1)
        self.assertEqual(self.api.get(f"{E}/messages/", {"kind": "test"}).json()["count"], 1)
        self.assertEqual(self.api.get(f"{E}/messages/", {"page_size": 2, "page": 2}).json()["results"].__len__(), 1)
        self.assertEqual(self.api.get(f"{E}/messages/", {"status": "bogus"}).status_code, 400)
        failed = EmailMessage.objects.get(status="failed")
        detail = self.api.get(f"{E}/messages/{failed.pk}/").json()
        self.assertIn("refused the sign-in", detail["error"])
        self.assertEqual(detail["body"], "Hello.")
        for client in (self.fin, self.api_staff):
            self.assertEqual(client.get(f"{E}/messages/").status_code, 403)
            self.assertEqual(client.get(f"{E}/messages/{failed.pk}/").status_code, 403)
        grant(Role.FINANCE_MANAGER, "email.manage")
        self.assertEqual(self.fin.get(f"{E}/messages/").status_code, 200)


class QuotationAndLetterTests(EmailBase):
    def issue_quotation(self, customer_name="Bright Buyers"):
        content = {"quotation": {"customer": {"name": customer_name, "address": ["1 Main Road", "Bengaluru"]},
                                 "items": [{"description": "Website", "quantity": "1", "rate": "50000"}]}}
        r = self.fin.post(f"{API}/issued-documents/", {"document_type": "quotation", "content": content, "issue_date": "2026-08-15"},
                          format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def letter(self):
        r = self.fin.post(f"{API}/issued-documents/", {"document_type": "business_letter", "content": {"paragraphs": ["Hello."]},
                                                       "issue_date": "2026-08-15"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def test_a_quotation_is_addressed_to_the_matching_customer_and_carries_its_pdf(self):
        q = self.issue_quotation()
        d = self.draft_for("issued_document", q["id"])
        self.assertEqual(d["to"], ["accounts@brightbuyers.example"])
        self.assertIn("Quotation", d["subject"])
        self.assertIn("Dear Bright Buyers", d["body"])
        self.assertTrue(d["attachments"][0].endswith(".pdf"))
        self.send("issued_document", q["id"], client=self.api_staff)  # employees issue quotations (documents.issue)
        self.assertTrue(mail.outbox[0].attachments[0][1].startswith(b"%PDF"))
        self.assertEqual(EmailMessage.objects.get().url, "/company/quotations")

    def test_an_unknown_customer_leaves_the_address_empty_and_letters_can_be_sent(self):
        self.assertEqual(self.draft_for("issued_document", self.issue_quotation("Somebody New")["id"])["to"], [])
        letter = self.letter()
        d = self.draft_for("issued_document", letter["id"])
        self.assertEqual(d["to"], [])
        self.send("issued_document", letter["id"])
        self.assertEqual(len(mail.outbox), 1)

    def test_money_documents_and_void_documents_are_not_sent_through_this_kind(self):
        inv = self.invoice(self.buyer)
        issued_invoice = SalesDocument.objects.get(pk=inv["id"]).issued_document_id
        r = self.fin.get(f"{E}/draft/", {"kind": "issued_document", "id": str(issued_invoice)})
        self.assertEqual(r.status_code, 400)
        letter = self.letter()
        IssuedDocument.objects.filter(pk=letter["id"]).update(status="void")
        self.assertEqual(self.fin.get(f"{E}/draft/", {"kind": "issued_document", "id": letter["id"]}).status_code, 400)
        from apps.accounts import access
        from apps.accounts.models import RolePermission

        no_docs = client_for(make_user("nodocs", Role.EMPLOYEE))
        RolePermission.objects.update_or_create(role=Role.EMPLOYEE, code="documents.issue", defaults={"granted": False})
        access.matrix_changed()
        q = self.issue_quotation()
        self.assertEqual(no_docs.get(f"{E}/draft/", {"kind": "issued_document", "id": q["id"]}).status_code, 403)
