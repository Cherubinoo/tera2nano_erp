"""Payment reminders: which invoice needs which reminder (fixed dates), never for paid, void or draft invoices, send and skip,
history, rule editing, permissions and audit. Nothing is emailed; sending only records the prepared text."""
import datetime

from django.db import IntegrityError, transaction

from apps.accounts.models import Role
from apps.company.models import CompanyProfile, CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user
from apps.sales import reminders
from apps.sales.models import PaymentReminder, ReminderRule
from apps.sales.tests.test_sales import ITEMS, S, SalesBase

D = datetime.date
TODAY = datetime.date.today()
ZERO_ID = "00000000-0000-0000-0000-000000000000"
NUMBER = "T2N/INV/26/1508/00001"


def iso(d):
    return d.isoformat()


class ReminderBase(SalesBase):
    def setUp(self):
        super().setUp()
        self.company = CompanyProfile.objects.get()

    def due_invoice(self, customer=None, due="2026-09-14", issue="2026-08-15", **kw):
        return self.invoice(customer, issue_date=issue, due_date=due, **kw)

    def late_invoice(self, days_late, customer=None):
        """An invoice that is `days_late` days past due as of the real today (for tests that use the API)."""
        return self.due_invoice(customer, due=iso(TODAY - datetime.timedelta(days=days_late)),
                                issue=iso(TODAY - datetime.timedelta(days=days_late + 30)))

    def due_on(self, today):
        return [(d.number, r.days_offset) for d, r in reminders.due_reminders(self.company, today)]

    def rule(self, offset):
        return ReminderRule.objects.get(company=self.company, days_offset=offset)

    def send(self, doc, offset, client=None, expect=201, on=None):
        if on is not None:  # as of a fixed date, straight through the service
            return reminders.send(self.company, self.finance, doc["id"], offset, today=on)
        r = (client or self.fin).post(f"{S}/reminders/send/", {"document": doc["id"], "days_offset": offset}, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def skip(self, doc, offset, note="", client=None, expect=201, on=None):
        if on is not None:
            return reminders.skip(self.company, self.finance, doc["id"], offset, note, today=on)
        r = (client or self.fin).post(f"{S}/reminders/skip/", {"document": doc["id"], "days_offset": offset, "note": note}, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()


class DueComputationTests(ReminderBase):
    def test_standard_rules_appear_the_first_time_they_are_needed(self):
        self.assertEqual(ReminderRule.objects.count(), 0)
        rules = self.fin.get(f"{S}/reminders/rules/").json()
        self.assertEqual([(r["days_offset"], r["label"]) for r in rules["results"]],
                         [(-3, "3 days before the due date"), (0, "On the due date"), (7, "7 days after the due date"),
                          (15, "15 days after the due date"), (30, "30 days after the due date")])
        self.assertTrue(all(r["is_active"] for r in rules["results"]))
        self.assertIn("customer_name", [p["key"] for p in rules["placeholders"]])
        self.fin.get(f"{S}/reminders/rules/")
        self.assertEqual(ReminderRule.objects.count(), 5)  # asking again does not add more

    def test_the_rule_that_applies_follows_days_from_the_due_date(self):
        self.due_invoice()  # due 14 Sep 2026
        table = [(D(2026, 9, 10), None), (D(2026, 9, 11), -3), (D(2026, 9, 13), -3), (D(2026, 9, 14), 0), (D(2026, 9, 20), 0),
                 (D(2026, 9, 21), 7), (D(2026, 9, 28), 7), (D(2026, 9, 29), 15), (D(2026, 10, 13), 15), (D(2026, 10, 14), 30),
                 (D(2027, 3, 1), 30)]
        for today, expected in table:
            got = self.due_on(today)
            self.assertEqual(got, [] if expected is None else [(NUMBER, expected)], today)

    def test_the_same_reminder_is_never_offered_twice_and_the_next_rule_follows(self):
        doc = self.due_invoice()
        self.assertEqual(self.due_on(D(2026, 9, 14)), [(NUMBER, 0)])
        self.assertEqual(self.due_on(D(2026, 9, 14)), [(NUMBER, 0)])  # asking again changes nothing
        self.send(doc, 0, on=D(2026, 9, 14))
        self.assertEqual(self.due_on(D(2026, 9, 14)), [])
        self.assertEqual(self.due_on(D(2026, 9, 20)), [])  # still the "on the day" rule, already done
        self.assertEqual(self.due_on(D(2026, 9, 21)), [(NUMBER, 7)])

    def test_a_skipped_reminder_counts_as_handled(self):
        doc = self.due_invoice()
        self.skip(doc, -3, "Customer called", on=D(2026, 9, 12))
        self.assertEqual(self.due_on(D(2026, 9, 12)), [])
        self.assertEqual(self.due_on(D(2026, 9, 14)), [(NUMBER, 0)])

    def test_a_long_overdue_invoice_is_offered_one_reminder_not_all_of_them(self):
        self.due_invoice()
        self.assertEqual(self.due_on(D(2026, 10, 24)), [(NUMBER, 30)])  # 40 days late: only the latest rule
        doc = self.fin.get(f"{S}/documents/", {"q": NUMBER}).json()["results"][0]
        self.send(doc, 30, on=D(2026, 10, 24))
        self.assertEqual(self.due_on(D(2026, 10, 24)), [])

    def test_paid_void_draft_and_non_receivable_documents_are_never_reminded(self):
        paid = self.due_invoice(self.customer("Paid Co"))
        self.pay(paid["customer"], amount="1800", allocations=[{"document": paid["id"], "amount": "1800"}])
        voided = self.due_invoice(self.customer("Void Co"))
        self.assertEqual(self.fin.post(f"{S}/documents/{voided['id']}/void/", {"change_reason": "Wrong"}).status_code, 200)
        self.draft(self.customer("Draft Co"), due_date="2026-09-14")
        self.draft(self.customer("Proforma Co"), kind="proforma_invoice")
        self.issue(self.draft(self.customer("Proforma Issued"), kind="proforma_invoice"))
        self.assertEqual(self.due_on(D(2026, 12, 1)), [])
        live = self.due_invoice(self.customer("Owes"))
        self.assertEqual(self.due_on(D(2026, 12, 1)), [(live["number"], 30)])

    def test_a_credit_note_is_not_reminded_but_a_debit_note_is(self):
        inv = self.due_invoice()
        cn = self.issue(self.draft(kind="credit_note", against=inv["id"], reason="Return", items=[
            {"description": "Return", "quantity": "1", "rate": "100"}]))
        dn = self.issue(self.draft(kind="debit_note", against=inv["id"], reason="Late fee", items=[
            {"description": "Late fee", "quantity": "1", "rate": "50"}], due_date="2026-09-14"))
        got = {n for n, _ in self.due_on(D(2026, 12, 1))}
        self.assertEqual(got, {inv["number"], dn["number"]})
        self.assertNotIn(cn["number"], got)

    def test_a_part_paid_invoice_is_reminded_for_what_is_left(self):
        doc = self.due_invoice()
        self.pay(amount="500", allocations=[{"document": doc["id"], "amount": "500"}])
        (d, rule), = reminders.due_reminders(self.company, D(2026, 9, 22))
        subject, body = reminders.render(d, rule, D(2026, 9, 22))
        self.assertIn("INR 1,300.00", body)
        self.assertNotIn("1,800.00", body)

    def test_a_payment_that_settles_the_invoice_removes_the_reminder(self):
        doc = self.due_invoice()
        self.assertEqual(len(self.due_on(D(2026, 9, 22))), 1)
        self.pay(amount="1800", allocations=[{"document": doc["id"], "amount": "1800"}])
        self.assertEqual(self.due_on(D(2026, 9, 22)), [])

    def test_inactive_rules_are_left_out_and_the_previous_rule_applies(self):
        self.due_invoice()
        self.fin.get(f"{S}/reminders/rules/")
        r = self.fin.patch(f"{S}/reminders/rules/{self.rule(7).pk}/", {"is_active": False}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.due_on(D(2026, 9, 22)), [(NUMBER, 0)])  # 8 days late, but the 7-day rule is off
        self.assertEqual(self.due_on(D(2026, 9, 29)), [(NUMBER, 15)])

    def test_the_database_allows_one_reminder_per_invoice_and_rule(self):
        doc = self.due_invoice()
        self.send(doc, 0, on=D(2026, 9, 14))
        row = PaymentReminder.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentReminder.objects.create(company=self.company, document=row.document, days_offset=0, status="skipped")


class RenderTests(ReminderBase):
    def test_placeholders_are_filled_from_the_invoice(self):
        self.due_invoice()
        (d, rule), = reminders.due_reminders(self.company, D(2026, 9, 22))  # 8 days late
        subject, body = reminders.render(d, rule, D(2026, 9, 22))
        self.assertEqual(subject, f"Overdue: invoice {NUMBER} (8 days past due)")
        self.assertEqual(body, f"Dear Acme Industries,\n\nOur records show that invoice {NUMBER} for INR 1,800.00, due on 14 Sep 2026, "
                               "is now 8 days overdue.\n\nPlease arrange payment or tell us when we can expect it.\n\n"
                               "Regards,\nExample Ventures")

    def test_before_the_due_date_days_overdue_is_zero(self):
        self.due_invoice()
        (d, rule), = reminders.due_reminders(self.company, D(2026, 9, 12))
        subject, body = reminders.render(d, rule, D(2026, 9, 12))
        self.assertEqual(subject, f"Reminder: invoice {NUMBER} is due on 14 Sep 2026")
        rule.body_template = "{days_overdue}|{days_until_due}|{invoice_date}|{invoice_total}"
        self.assertEqual(reminders.render(d, rule, D(2026, 9, 12))[1], "0|2|15 Aug 2026|INR 1,800.00")

    def test_edited_wording_is_used(self):
        self.due_invoice()
        self.fin.get(f"{S}/reminders/rules/")
        r = self.fin.patch(f"{S}/reminders/rules/{self.rule(0).pk}/", {
            "subject_template": "Due today: {invoice_no}", "body_template": "Hi {customer_name}, please pay {amount_due}."}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        (d, rule), = reminders.due_reminders(self.company, D(2026, 9, 14))
        self.assertEqual(reminders.render(d, rule, D(2026, 9, 14)),
                         (f"Due today: {NUMBER}", "Hi Acme Industries, please pay INR 1,800.00."))


class RuleTests(ReminderBase):
    def test_create_edit_and_validate_rules(self):
        r = self.fin.post(f"{S}/reminders/rules/", {"days_offset": 45, "subject_template": "Final notice {invoice_no}",
                                                    "body_template": "{customer_name}, {amount_due} is {days_overdue} days late."}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["label"], r.json()["is_active"]), ("45 days after the due date", True))
        bad = [({"days_offset": 45}, "days_offset"), ({"days_offset": "x"}, "days_offset"), ({"days_offset": 400}, "days_offset"),
               ({"days_offset": -61}, "days_offset"), ({"subject_template": " "}, "subject_template"),
               ({"body_template": ""}, "body_template"), ({"subject_template": "Hello {nobody}"}, "subject_template"),
               ({"body_template": "Pay {amount}"}, "body_template"), ({"days_offset": 7}, "days_offset"),
               ({"subject_template": "x" * 201}, "subject_template")]
        for over, field in bad:
            body = {"days_offset": 50, "subject_template": "S", "body_template": "B", **over}
            resp = self.fin.post(f"{S}/reminders/rules/", body, format="json")
            self.assertEqual(resp.status_code, 400, over)
            self.assertIn(field, resp.json(), over)
        rid = r.json()["id"]
        r = self.fin.patch(f"{S}/reminders/rules/{rid}/", {"days_offset": 60}, format="json")
        self.assertEqual((r.status_code, r.json()["label"]), (200, "60 days after the due date"))
        self.assertEqual(self.fin.patch(f"{S}/reminders/rules/{rid}/", {"days_offset": 7}, format="json").status_code, 400)
        self.assertEqual(self.fin.patch(f"{S}/reminders/rules/{ZERO_ID}/", {"days_offset": 9}, format="json").status_code, 404)
        self.assertEqual(ReminderRule.objects.count(), 6)

    def test_before_due_rules_use_negative_days(self):
        r = self.fin.post(f"{S}/reminders/rules/", {"days_offset": -1, "subject_template": "S", "body_template": "B"}, format="json")
        self.assertEqual((r.status_code, r.json()["label"]), (201, "1 day before the due date"))


class ApiTests(ReminderBase):
    def test_list_shows_what_is_due_with_the_prepared_text(self):
        a, b = self.late_invoice(8, self.customer("Alpha Ltd", email="ap@alpha.example")), self.late_invoice(40, self.customer("Beta Ltd"))
        self.late_invoice(-10, self.customer("Not due yet"))  # due in 10 days: before the first rule's 3-day lead
        r = self.fin.get(f"{S}/reminders/").json()
        self.assertEqual(r["count"], 2)
        by_customer = {x["customer"]["name"]: x for x in r["results"]}
        alpha = by_customer["Alpha Ltd"]
        self.assertEqual((alpha["rule"]["days_offset"], alpha["days_overdue"], alpha["balance"], alpha["customer"]["email"]),
                         (7, 8, "1800.00", "ap@alpha.example"))
        self.assertIn("8 days", alpha["subject"])
        self.assertTrue(alpha["mailto"].startswith("mailto:ap@alpha.example?subject="))
        self.assertEqual(by_customer["Beta Ltd"]["rule"]["days_offset"], 30)
        self.assertEqual(by_customer["Beta Ltd"]["customer"]["email"], "")
        self.assertEqual([x["customer"]["name"] for x in r["results"]], ["Beta Ltd", "Alpha Ltd"])  # oldest due date first
        self.assertEqual(self.fin.get(f"{S}/dashboard/").json()["reminders_due"], 2)
        self.send(a, 7)
        self.assertEqual(self.fin.get(f"{S}/reminders/").json()["count"], 1)
        self.assertEqual(self.fin.get(f"{S}/dashboard/").json()["reminders_due"], 1)
        self.assertEqual(b["status"], "issued")

    def test_send_records_the_rendered_text_and_cannot_be_repeated(self):
        doc = self.late_invoice(8, self.customer("Alpha Ltd", email="ap@alpha.example"))
        row = self.send(doc, 7)
        self.assertEqual((row["status"], row["to_email"], row["amount_due"], row["days_overdue"], row["by"]),
                         ("sent", "ap@alpha.example", "1800.00", 8, self.finance.get_username()))
        self.assertIn(doc["number"], row["subject"])
        self.assertIn("INR 1,800.00", row["body"])
        self.assertTrue(row["mailto"].startswith("mailto:ap@alpha.example?subject="))
        self.assertIn("Alpha%20Ltd", row["mailto"])
        self.send(doc, 7, expect=400)
        self.assertEqual(PaymentReminder.objects.count(), 1)

    def test_skip_records_a_note_and_no_text(self):
        doc = self.late_invoice(8)
        row = self.skip(doc, 7, "Promised payment on Friday")
        self.assertEqual((row["status"], row["note"], row["subject"], row["body"], row["mailto"]), ("skipped", "Promised payment on Friday", "", "", ""))
        self.skip(doc, 7, expect=400)
        self.assertEqual(self.fin.get(f"{S}/reminders/").json()["count"], 0)

    def test_only_a_reminder_that_is_due_can_be_sent(self):
        doc = self.late_invoice(8)
        self.send(doc, 15, expect=400)   # not the rule that applies
        self.send(doc, 0, expect=400)    # superseded by the 7-day rule
        self.send({"id": ZERO_ID}, 7, expect=400)
        r = self.fin.post(f"{S}/reminders/send/", {"document": doc["id"], "days_offset": "x"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.fin.post(f"{S}/reminders/send/", {"document": "not-an-id", "days_offset": 7}, format="json")
        self.assertEqual(r.status_code, 400)
        paid = self.late_invoice(8, self.customer("Paid"))
        self.pay(paid["customer"], amount="1800", allocations=[{"document": paid["id"], "amount": "1800"}])
        self.send(paid, 7, expect=400)
        draft = self.draft(self.customer("Drafty"), due_date=iso(TODAY - datetime.timedelta(days=8)))
        self.send(draft, 7, expect=400)
        self.assertEqual(PaymentReminder.objects.count(), 0)

    def test_history_and_the_invoice_page_list_every_reminder(self):
        doc = self.late_invoice(40)
        self.assertEqual(self.fin.get(f"{S}/documents/{doc['id']}/").json()["reminders"], [])
        self.skip(doc, 30, "On hold")
        other = self.late_invoice(8, self.customer("Other"))
        self.send(other, 7)
        history = self.fin.get(f"{S}/reminders/history/").json()["results"]
        self.assertEqual({(h["document"]["id"], h["status"]) for h in history}, {(doc["id"], "skipped"), (other["id"], "sent")})
        mine = self.fin.get(f"{S}/reminders/history/", {"document": doc["id"]}).json()["results"]
        self.assertEqual([(h["status"], h["note"]) for h in mine], [("skipped", "On hold")])
        self.assertEqual(len(self.fin.get(f"{S}/reminders/history/", {"status": "sent"}).json()["results"]), 1)
        shown = self.fin.get(f"{S}/documents/{other['id']}/").json()["reminders"]
        self.assertEqual([(x["status"], x["days_offset"], x["label"]) for x in shown], [("sent", 7, "7 days after the due date")])
        self.assertIn(other["number"], shown[0]["subject"])
        self.assertEqual(self.fin.get(f"{S}/reminders/history/", {"document": "bad"}).status_code, 400)

    def test_dashboard_count_ignores_settled_invoices(self):
        paid = self.late_invoice(8)
        self.pay(amount="1800", allocations=[{"document": paid["id"], "amount": "1800"}])
        self.assertEqual(self.fin.get(f"{S}/dashboard/").json()["reminders_due"], 0)


class PermissionAndAuditTests(ReminderBase):
    def test_roles(self):
        doc = self.late_invoice(8)
        pm = client_for(make_user("pm", Role.PROJECT_MANAGER))  # can view sales, cannot manage it
        self.fin.get(f"{S}/reminders/rules/")
        rid = self.rule(7).pk
        for client in (self.api_staff, self.anon):
            code = 403 if client is self.api_staff else 401
            for method, url in (("get", "reminders/"), ("get", "reminders/history/"), ("get", "reminders/rules/")):
                self.assertEqual(getattr(client, method)(f"{S}/{url}").status_code, code, url)
        for url, body in (("reminders/send/", {"document": doc["id"], "days_offset": 7}), ("reminders/skip/", {"document": doc["id"], "days_offset": 7}),
                          ("reminders/rules/", {"days_offset": 50, "subject_template": "S", "body_template": "B"})):
            for who in (pm, self.api_staff):
                self.assertEqual(who.post(f"{S}/{url}", body, format="json").status_code, 403, url)
        self.assertEqual(pm.patch(f"{S}/reminders/rules/{rid}/", {"is_active": False}, format="json").status_code, 403)
        for url in ("reminders/", "reminders/history/", "reminders/rules/"):
            self.assertEqual(pm.get(f"{S}/{url}").status_code, 200, url)  # may look, not act
        self.assertEqual(PaymentReminder.objects.count(), 0)
        self.assertTrue(ReminderRule.objects.get(pk=rid).is_active)
        for client in (self.api, self.fin):
            self.assertEqual(client.get(f"{S}/reminders/").status_code, 200)
        self.assertEqual(self.api.post(f"{S}/reminders/send/", {"document": doc["id"], "days_offset": 7}, format="json").status_code, 201)

    def events(self, entity, action=None):
        qs = CompanyProfileAuditEvent.objects.filter(entity_type=entity)
        return qs.filter(action=action) if action else qs

    def test_sent_and_skipped_reminders_and_rule_changes_are_audited(self):
        a = self.late_invoice(8, self.customer("Alpha Ltd"))
        b = self.late_invoice(40, self.customer("Beta Ltd"))
        self.send(a, 7)
        self.skip(b, 30, "Disputed")
        sent = self.events("payment_reminder").filter(new_values__status="sent").get()
        self.assertEqual((sent.new_values["invoice"], sent.new_values["customer"], sent.new_values["rule"], sent.actor_id),
                         (a["number"], "Alpha Ltd", "7 days after the due date", self.finance.pk))
        skipped = self.events("payment_reminder").filter(new_values__status="skipped").get()
        self.assertEqual(skipped.reason, "Disputed")
        rid = self.rule(0).pk
        self.fin.patch(f"{S}/reminders/rules/{rid}/", {"subject_template": "Due today {invoice_no}"}, format="json")
        ev = self.events("reminder_rule", "updated").get()
        self.assertEqual((ev.previous_values["subject"], ev.new_values["subject"]), ("Invoice {invoice_no} is due today", "Due today {invoice_no}"))
        self.fin.patch(f"{S}/reminders/rules/{rid}/", {"is_active": False}, format="json")
        self.assertEqual(self.events("reminder_rule", "deactivated").count(), 1)
        self.fin.post(f"{S}/reminders/rules/", {"days_offset": 90, "subject_template": "S", "body_template": "B"}, format="json")
        self.assertEqual(self.events("reminder_rule", "created").count(), 1)
