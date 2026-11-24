"""Emailing an invitation link: the email carries the working link, the log never keeps the token (only its hash is stored by
the invitation design), the logged copy cannot be sent again, and only people who may manage that invitation can send it."""
from django.core import mail
from django.test import override_settings

from apps.accounts.models import Invitation
from apps.accounts.tests.test_invitations import AUTH, InviteBase
from apps.company.models import CompanyProfileAuditEvent
from apps.emails.models import EmailMessage

from .test_emails import REFUSING, SMTP

ORIGIN = "https://erp.example.com"


class InviteEmailTests(InviteBase):
    def setUp(self):
        super().setUp()
        self.made = self.invite(name_hint="Meera Nair", email_hint="meera@example.com", note="Welcome aboard.")
        self.token = self.made["token"]
        mail.outbox = []

    def draft(self, client=None, expect=200, **over):
        body = {"token": self.token, "origin": ORIGIN, **over}
        r = (client or self.api_partner).post(f"{AUTH}/invitations/{self.made['id']}/email-draft/", body, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def send(self, client=None, expect=201, **over):
        d = self.draft()
        body = {"token": self.token, "to": d["to"], "subject": d["subject"], "body": d["body"], **over}
        r = (client or self.api_partner).post(f"{AUTH}/invitations/{self.made['id']}/email/", body, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def test_the_draft_is_addressed_to_the_invitee_and_holds_the_link(self):
        d = self.draft()
        self.assertEqual(d["to"], ["meera@example.com"])
        self.assertIn("Your invitation to join", d["subject"])
        self.assertIn(f"{ORIGIN}/join/{self.token}", d["body"])
        self.assertIn("Hello Meera Nair", d["body"])
        self.assertIn("Welcome aboard.", d["body"])

    @override_settings(APP_BASE_URL="https://erp.company.example")
    def test_the_server_address_setting_wins_over_the_browser_address(self):
        self.assertIn("https://erp.company.example/join/", self.draft(origin="http://evil.example")["body"])

    @SMTP
    def test_the_email_has_the_link_but_the_log_never_keeps_the_token(self):
        sent = self.send()
        self.assertEqual(sent["status"], "sent")
        self.assertIn(f"/join/{self.token}", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["meera@example.com"])
        row = EmailMessage.objects.get()
        for value in (row.body, row.subject, row.object_label, row.url, str(row.to), str(row.attachments)):
            self.assertNotIn(self.token, value)
        self.assertIn("/join/[hidden]", row.body)
        self.assertFalse(row.can_resend)
        self.assertNotIn(self.token, str(sent))
        # nothing in the change history holds it either
        for ev in CompanyProfileAuditEvent.objects.filter(entity_type="invitation"):
            self.assertNotIn(self.token, str(ev.new_values) + str(ev.previous_values) + ev.reason)
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="invitation", reason__icontains="emailed").exists())

    def test_a_token_typed_into_the_subject_is_hidden_too(self):
        self.send(subject=f"Join with {self.token}")
        self.assertNotIn(self.token, EmailMessage.objects.get().subject)

    @REFUSING
    def test_a_failed_invite_email_cannot_be_sent_again_from_the_log(self):
        sent = self.send()
        self.assertEqual(sent["status"], "failed")
        self.assertFalse(sent["can_resend"])
        r = self.api.post(f"/api/v1/email/messages/{sent['id']}/resend/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("new invitation link", str(r.json()))

    def test_the_token_must_belong_to_the_invitation_and_the_link_must_be_open(self):
        other = self.invite()
        self.draft(token=other["token"], expect=400)
        self.draft(token="x" * 43, expect=400)
        self.draft(origin="javascript:alert(1)", expect=400)
        self.api_partner.post(f"{AUTH}/invitations/{self.made['id']}/revoke/")
        self.draft(expect=400)
        r = self.api_partner.post(f"{AUTH}/invitations/{self.made['id']}/email/", {"token": self.token, "to": ["a@example.com"],
                                                                                  "subject": "s", "body": "b"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_only_people_who_may_manage_the_invitation_can_email_it(self):
        self.draft(client=self.api_pm, expect=403)  # no users.invite
        admin_link = self.invite(client=self.api, role="partner")
        r = self.api_hr.post(f"{AUTH}/invitations/{admin_link['id']}/email-draft/", {"token": admin_link["token"], "origin": ORIGIN},
                             format="json")
        self.assertEqual(r.status_code, 403)  # an HR manager may not handle a partner invitation
        self.assertEqual(Invitation.objects.count(), 2)
