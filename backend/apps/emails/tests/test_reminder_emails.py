"""Payment reminders by email: sending from the Reminders page emails the text when email is set up (and only then), keeps the
reminder record even when the email fails, and the daily job sends due reminders automatically, never twice for one step."""
import datetime

from django.core import mail
from django.test import override_settings

from apps.company import daily
from apps.emails import jobs
from apps.emails.models import EmailMessage, EmailSettings
from apps.sales import reminders
from apps.sales.models import PaymentReminder
from apps.sales.tests.test_reminders import ReminderBase

from .test_emails import REFUSING, SMTP

D = datetime.date
S = "/api/v1/sales"


class ManualReminderTests(ReminderBase):
    def setUp(self):
        super().setUp()
        self.buyer = self.customer("Bright Buyers", email="accounts@brightbuyers.example")
        self.doc = self.late_invoice(8, self.buyer)  # the 7-days-after rule is due
        mail.outbox = []

    def test_without_a_mail_server_the_reminder_is_only_recorded(self):
        row = self.send(self.doc, 7)
        self.assertEqual((row["status"], row["email"]), ("sent", None))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(EmailMessage.objects.count(), 0)

    @SMTP
    def test_with_a_mail_server_the_rendered_text_is_emailed_and_logged(self):
        EmailSettings.objects.update_or_create(company=self.company, defaults={"signature": "Asha Rao, Accounts"})
        row = self.send(self.doc, 7)
        self.assertEqual(row["email"]["status"], "sent")
        m = mail.outbox[0]
        self.assertEqual(m.to, ["accounts@brightbuyers.example"])
        self.assertIn(self.doc["number"], m.subject)
        self.assertTrue(m.body.startswith("Dear Bright Buyers"))
        self.assertTrue(m.body.endswith("Asha Rao, Accounts"))
        log = EmailMessage.objects.get()
        rec = PaymentReminder.objects.get()
        self.assertEqual((log.kind, log.object_id, log.url), ("payment_reminder", str(rec.pk), f"/sales/documents/{self.doc['id']}"))
        # the history and the invoice page show how the email went
        hist = self.fin.get(f"{S}/reminders/history/").json()["results"][0]
        self.assertEqual(hist["email"]["status"], "sent")
        self.assertEqual(self.fin.get(f"{S}/documents/{self.doc['id']}/").json()["reminders"][0]["email"]["status"], "sent")

    @SMTP
    def test_the_dialog_can_change_the_recipients_and_the_text(self):
        r = self.fin.post(f"{S}/reminders/send/", {"document": self.doc["id"], "days_offset": 7, "to": ["ap@brightbuyers.example"],
                                                  "cc": ["cfo@brightbuyers.example"], "subject": "Please pay", "body": "Kindly pay today."},
                          format="json")
        self.assertEqual(r.status_code, 201, r.content)
        m = mail.outbox[0]
        self.assertEqual((m.to, m.cc, m.subject, m.body), (["ap@brightbuyers.example"], ["cfo@brightbuyers.example"], "Please pay", "Kindly pay today."))
        rec = PaymentReminder.objects.get()
        self.assertEqual((rec.subject, rec.body, rec.to_email), ("Please pay", "Kindly pay today.", "ap@brightbuyers.example"))
        bad = self.late_invoice(8, self.customer("Other Co", email="x@other.example"))
        r = self.fin.post(f"{S}/reminders/send/", {"document": bad["id"], "days_offset": 7, "to": ["broken"]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(PaymentReminder.objects.filter(document_id=bad["id"]).exists())  # nothing recorded for a bad address

    @REFUSING
    def test_a_failed_email_keeps_the_reminder_record_and_can_be_sent_again(self):
        row = self.send(self.doc, 7)
        self.assertEqual((row["status"], row["email"]["status"]), ("sent", "failed"))
        self.assertTrue(PaymentReminder.objects.filter(document_id=self.doc["id"], days_offset=7).exists())
        failed = EmailMessage.objects.get()
        with SMTP:
            r = self.api.post(f"/api/v1/email/messages/{failed.pk}/resend/")
        self.assertEqual((r.status_code, r.json()["status"]), (201, "sent"))
        self.assertEqual(self.fin.get(f"{S}/reminders/history/").json()["results"][0]["email"]["status"], "sent")

    @SMTP
    def test_a_customer_without_an_address_is_recorded_without_email(self):
        doc = self.late_invoice(8, self.customer("No Mail Co"))
        row = self.send(doc, 7)
        self.assertEqual((row["status"], row["email"]), ("sent", None))
        self.assertEqual(len(mail.outbox), 0)


class AutomaticReminderTests(ReminderBase):
    def setUp(self):
        super().setUp()
        self.buyer = self.customer("Bright Buyers", email="accounts@brightbuyers.example")
        self.silent = self.customer("Silent Co")  # no email address
        self.with_mail = self.due_invoice(self.buyer, due="2026-09-14")
        self.without_mail = self.due_invoice(self.silent, due="2026-09-14")
        mail.outbox = []

    def switch_on(self, on=True):
        EmailSettings.objects.update_or_create(company=self.company, defaults={"auto_reminders": on})

    def test_the_job_is_registered_with_the_daily_jobs(self):
        self.assertIn("Payment reminder emails", [name for name, _ in daily.jobs()])

    @SMTP
    def test_switched_off_nothing_is_sent(self):
        self.assertIn("switched off", jobs.send_due_reminders(self.company, D(2026, 9, 22)))
        self.assertEqual((len(mail.outbox), PaymentReminder.objects.count()), (0, 0))

    def test_without_a_mail_server_nothing_is_sent_or_recorded(self):
        self.switch_on()
        self.assertIn("not set up", jobs.send_due_reminders(self.company, D(2026, 9, 22)))
        self.assertEqual((len(mail.outbox), PaymentReminder.objects.count()), (0, 0))

    @SMTP
    def test_due_reminders_go_out_once_per_step_only_to_customers_with_an_address(self):
        self.switch_on()
        summary = jobs.send_due_reminders(self.company, D(2026, 9, 22))  # 8 days late: the 7-day rule
        self.assertIn("1 reminder email(s) sent", summary)
        self.assertIn("1 left for you", summary)
        self.assertEqual([m.to for m in mail.outbox], [["accounts@brightbuyers.example"]])
        rec = PaymentReminder.objects.get()
        self.assertEqual((rec.document_id.hex, rec.days_offset, rec.created_by), (self.with_mail["id"].replace("-", ""), 7, None))
        log = EmailMessage.objects.get()
        self.assertTrue(log.automatic)
        self.assertIsNone(log.sent_by)
        # running again (the same day, or the next day while the same step is current) sends nothing more
        jobs.send_due_reminders(self.company, D(2026, 9, 22))
        jobs.send_due_reminders(self.company, D(2026, 9, 23))
        daily.run_all(self.company, D(2026, 9, 24))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(PaymentReminder.objects.count(), 1)
        # the next step (15 days after) goes out once when it comes due
        jobs.send_due_reminders(self.company, D(2026, 9, 29))
        jobs.send_due_reminders(self.company, D(2026, 9, 29))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(sorted(PaymentReminder.objects.values_list("days_offset", flat=True)), [7, 15])
        # the customer without an address is still waiting on the Reminders page
        self.assertIn(self.without_mail["number"], [d.number for d, _ in reminders.due_reminders(self.company, D(2026, 9, 29))])

    @REFUSING
    def test_a_failing_mail_server_is_reported_and_the_step_is_not_repeated(self):
        self.switch_on()
        self.assertIn("1 failed", jobs.send_due_reminders(self.company, D(2026, 9, 22)))
        self.assertEqual(EmailMessage.objects.get().status, "failed")
        jobs.send_due_reminders(self.company, D(2026, 9, 22))
        self.assertEqual(EmailMessage.objects.count(), 1)  # the failed one is sent again from the log, not by the job

    @override_settings(EMAIL_HOST="smtp.example.com")
    def test_a_reminder_already_sent_by_hand_is_not_sent_again(self):
        self.switch_on()
        reminders.send(self.company, self.finance, self.with_mail["id"], 7, today=D(2026, 9, 22))
        self.assertEqual(len(mail.outbox), 1)
        jobs.send_due_reminders(self.company, D(2026, 9, 22))
        self.assertEqual(len(mail.outbox), 1)
