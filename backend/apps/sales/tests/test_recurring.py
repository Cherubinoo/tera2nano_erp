"""Recurring invoices: schedule arithmetic, generation (idempotent, catch-up limit), month-end and leap years, GST, issuing,
failures, pause/stop, permissions, audit and the management command. All dates are fixed; nothing depends on today."""
import datetime
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase

from apps.accounting.models import JournalEntry
from apps.accounts.models import Role
from apps.company.models import CompanyProfile, CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user, viewer_client
from apps.sales import recurring
from apps.sales.models import RecurringInvoice, SalesDocument
from apps.sales.tests.test_sales import ITEMS, S, SalesBase

D = datetime.date
ZERO_ID = "00000000-0000-0000-0000-000000000000"


def step(freq, unit="", count=None):
    return recurring.step_for(freq, unit, count)


class ScheduleTests(SimpleTestCase):
    """Run dates worked out by hand, no database."""

    def dates(self, start, freq, n=5, unit="", count=None, **kw):
        return recurring.schedule_dates(start, step(freq, unit, count), count=n, **kw)

    def test_monthly_from_the_31st_clamps_to_month_end_and_returns_to_the_31st(self):
        self.assertEqual(self.dates(D(2026, 1, 31), "monthly"),
                         [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31), D(2026, 4, 30), D(2026, 5, 31)])

    def test_leap_year_february(self):
        self.assertEqual(self.dates(D(2028, 1, 31), "monthly", 3), [D(2028, 1, 31), D(2028, 2, 29), D(2028, 3, 31)])
        self.assertEqual(self.dates(D(2027, 1, 31), "monthly", 3), [D(2027, 1, 31), D(2027, 2, 28), D(2027, 3, 31)])

    def test_yearly_from_29_february(self):
        self.assertEqual(self.dates(D(2028, 2, 29), "yearly", 5),
                         [D(2028, 2, 29), D(2029, 2, 28), D(2030, 2, 28), D(2031, 2, 28), D(2032, 2, 29)])

    def test_weekly_quarterly_half_yearly(self):
        self.assertEqual(self.dates(D(2026, 11, 2), "weekly", 3), [D(2026, 11, 2), D(2026, 11, 9), D(2026, 11, 16)])
        self.assertEqual(self.dates(D(2026, 11, 30), "quarterly", 4), [D(2026, 11, 30), D(2027, 2, 28), D(2027, 5, 30), D(2027, 8, 30)])
        self.assertEqual(self.dates(D(2026, 8, 31), "half_yearly", 3), [D(2026, 8, 31), D(2027, 2, 28), D(2027, 8, 31)])

    def test_custom_every_n_days_or_months(self):
        self.assertEqual(self.dates(D(2026, 12, 25), "custom", 3, unit="days", count=10), [D(2026, 12, 25), D(2027, 1, 4), D(2027, 1, 14)])
        self.assertEqual(self.dates(D(2026, 1, 31), "custom", 4, unit="months", count=2),
                         [D(2026, 1, 31), D(2026, 3, 31), D(2026, 5, 31), D(2026, 7, 31)])

    def test_first_on_or_after_and_next_after(self):
        s = step("monthly")
        anchor = D(2026, 1, 31)
        self.assertEqual(recurring.first_on_or_after(anchor, s, D(2025, 12, 1)), anchor)
        self.assertEqual(recurring.first_on_or_after(anchor, s, D(2026, 2, 1)), D(2026, 2, 28))
        self.assertEqual(recurring.first_on_or_after(anchor, s, D(2026, 2, 28)), D(2026, 2, 28))
        self.assertEqual(recurring.next_after(anchor, s, D(2026, 2, 28)), D(2026, 3, 31))
        w = step("weekly")
        self.assertEqual(recurring.first_on_or_after(D(2026, 1, 5), w, D(2026, 1, 6)), D(2026, 1, 12))
        self.assertEqual(recurring.first_on_or_after(D(2026, 1, 5), w, D(2026, 1, 12)), D(2026, 1, 12))

    def test_end_date_and_remaining_runs_stop_the_list(self):
        self.assertEqual(self.dates(D(2026, 8, 31), "monthly", 6, end_date=D(2026, 10, 30)), [D(2026, 8, 31), D(2026, 9, 30)])
        self.assertEqual(self.dates(D(2026, 8, 31), "monthly", 6, remaining=2), [D(2026, 8, 31), D(2026, 9, 30)])


class RecurringBase(SalesBase):
    def setUp(self):
        super().setUp()
        self.company = CompanyProfile.objects.get()

    def payload(self, **over):
        body = {"name": "Acme retainer", "customer": self.acme["id"], "frequency": "monthly", "start_date": "2026-08-31",
                "mode": "draft", "form": {"items": ITEMS}}
        body.update(over)
        return body

    def make(self, client=None, expect=201, **over):
        r = (client or self.fin).post(f"{S}/recurring-invoices/", self.payload(**over), format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def profile(self, p):
        return RecurringInvoice.objects.get(pk=p["id"])

    def children(self, p):
        return SalesDocument.objects.filter(recurring_id=p["id"]).order_by("recurring_run_date")

    def go(self, today, **kw):
        return recurring.run_due(self.company, today, **kw)

    def audit(self, p, action=None):
        qs = CompanyProfileAuditEvent.objects.filter(entity_type="recurring_invoice", entity_id=p["id"])
        return qs.filter(action=action) if action else qs


class GenerationTests(RecurringBase):
    def test_profile_gets_an_automatic_reference_and_first_run_is_the_start_date(self):
        p = self.make()
        self.assertRegex(p["reference_code"], r"^T2N-RIN-\d{2}-\d{4}-0001$")
        self.assertEqual((p["status"], p["next_run_date"], p["occurrences_done"]), ("active", "2026-08-31", 0))
        self.assertRegex(self.make(name="Second")["reference_code"], r"-0002$")
        self.assertEqual(p["upcoming"][:3], ["2026-08-31", "2026-09-30", "2026-10-31"])

    def test_run_due_makes_each_due_invoice_once(self):
        p = self.make()
        before = self.go(D(2026, 8, 30))
        self.assertEqual(before["generated"], [])  # one day early
        r = self.go(D(2026, 9, 5))
        self.assertEqual([g["run_date"] for g in r["generated"]], ["2026-08-31"])
        self.assertEqual(self.go(D(2026, 9, 5))["generated"], [])  # asking again changes nothing
        docs = list(self.children(p))
        self.assertEqual(len(docs), 1)
        d = docs[0]
        # 2 x 1000 less 10% = 1800.00; dated on the run date; due after the company's 30-day terms
        self.assertEqual((d.status, d.kind, str(d.grand_total), d.issue_date, d.due_date),
                         ("draft", "invoice", "1800.00", D(2026, 8, 31), D(2026, 9, 30)))
        self.assertEqual((str(d.customer_id), d.recurring_run_date), (self.acme["id"], D(2026, 8, 31)))
        prof = self.profile(p)
        self.assertEqual((prof.next_run_date, prof.last_run_date, prof.occurrences_done), (D(2026, 9, 30), D(2026, 8, 31), 1))

    def test_the_database_refuses_a_second_invoice_for_the_same_run(self):
        p = self.make()
        self.go(D(2026, 9, 5))
        doc = self.children(p).get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SalesDocument.objects.create(company=self.company, customer=doc.customer, kind="invoice", issue_date=D(2026, 8, 31),
                                         recurring_id=doc.recurring_id, recurring_run_date=doc.recurring_run_date)

    def test_a_rewound_schedule_does_not_duplicate_an_existing_run(self):
        p = self.make()
        self.go(D(2026, 9, 5))
        RecurringInvoice.objects.filter(pk=p["id"]).update(next_run_date=D(2026, 8, 31), occurrences_done=0)
        self.go(D(2026, 9, 5))
        self.assertEqual(self.children(p).count(), 1)  # the existing invoice was recognised, the schedule just moved on
        self.assertEqual(self.profile(p).next_run_date, D(2026, 9, 30))

    def test_missed_periods_are_caught_up_one_invoice_each_on_their_own_dates(self):
        p = self.make()
        r = self.go(D(2026, 11, 30))
        self.assertEqual([g["run_date"] for g in r["generated"]], ["2026-08-31", "2026-09-30", "2026-10-31", "2026-11-30"])
        self.assertEqual([d.issue_date for d in self.children(p)], [D(2026, 8, 31), D(2026, 9, 30), D(2026, 10, 31), D(2026, 11, 30)])
        self.assertEqual(self.profile(p).next_run_date, D(2026, 12, 31))

    def test_catch_up_is_limited_per_call_and_the_rest_follows(self):
        p = self.make(frequency="weekly", start_date="2026-01-05")
        today = D(2026, 11, 15)  # Mondays from 5 Jan: 5 Jan + 7k <= 15 Nov gives k = 0..44, so 45 runs
        sizes, still = [], []
        for _ in range(6):
            r = self.go(today)
            sizes.append(len(r["generated"]))
            still.append(r["still_due"])
        self.assertEqual(sizes, [12, 12, 12, 9, 0, 0])
        self.assertEqual(still, [1, 1, 1, 0, 0, 0])
        prof = self.profile(p)
        self.assertEqual((prof.occurrences_done, prof.last_run_date, prof.next_run_date), (45, D(2026, 11, 9), D(2026, 11, 16)))
        self.assertEqual(self.children(p).count(), 45)
        self.assertEqual(len({d.recurring_run_date for d in self.children(p)}), 45)

    def test_a_smaller_limit_is_respected(self):
        p = self.make()
        self.assertEqual(len(self.go(D(2026, 11, 30), limit=2)["generated"]), 2)
        self.assertEqual(self.profile(p).next_run_date, D(2026, 10, 31))

    def test_month_end_profile_starting_on_the_31st(self):
        p = self.make(start_date="2026-01-31")
        self.go(D(2026, 5, 31))
        self.assertEqual([d.recurring_run_date for d in self.children(p)],
                         [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31), D(2026, 4, 30), D(2026, 5, 31)])
        self.assertEqual(self.profile(p).next_run_date, D(2026, 6, 30))

    def test_leap_day_run(self):
        p = self.make(start_date="2028-01-31")
        self.go(D(2028, 3, 31))
        self.assertEqual([d.recurring_run_date for d in self.children(p)], [D(2028, 1, 31), D(2028, 2, 29), D(2028, 3, 31)])
        self.assertEqual(self.children(p)[1].issue_date, D(2028, 2, 29))

    def test_a_chosen_due_period_overrides_the_payment_terms(self):
        p = self.make(due_days=10)
        self.go(D(2026, 8, 31))
        self.assertEqual(self.children(p).get().due_date, D(2026, 9, 10))
        q = self.make(name="Same day", due_days=0, start_date="2026-09-01")
        self.go(D(2026, 9, 1))
        self.assertEqual(self.children(q).get().due_date, D(2026, 9, 1))

    def test_notes_and_terms_are_copied_only_when_auto_apply_is_on(self):
        on = self.make(name="With terms", apply_terms=True, notes="Thanks for your business.", terms="Pay within 10 days.")
        off = self.make(name="Without", apply_terms=False, notes="Not printed", terms="Not printed either", start_date="2026-08-31")
        self.go(D(2026, 8, 31))
        a, b = self.children(on).get(), self.children(off).get()
        self.assertEqual((a.form["notes"], a.form["terms"]), ("Thanks for your business.", "Pay within 10 days."))
        self.assertNotIn("notes", b.form)
        self.assertNotIn("terms", b.form)

    def test_end_date_and_maximum_runs_complete_the_profile(self):
        by_date = self.make(end_date="2026-10-15")
        by_count = self.make(name="Twice", max_occurrences=2)
        self.go(D(2026, 12, 31))
        for p, n in ((by_date, 2), (by_count, 2)):
            prof = self.profile(p)
            self.assertEqual((prof.status, prof.next_run_date, prof.occurrences_done), ("completed", None, n), p["name"])
            self.assertEqual(self.children(p).count(), n)
        self.assertEqual(self.go(D(2027, 6, 30))["generated"], [])

    def test_a_generated_invoice_that_is_voided_or_deleted_is_not_made_again(self):
        p = self.make(mode="issue")
        self.go(D(2026, 8, 31))
        doc = self.children(p).get()
        self.assertEqual(self.fin.post(f"{S}/documents/{doc.pk}/void/", {"change_reason": "Wrong"}).status_code, 200)
        self.assertEqual(self.go(D(2026, 8, 31))["generated"], [])
        self.assertEqual(self.children(p).count(), 1)


class IssueAndGstTests(RecurringBase):
    def lines(self, doc_id, kind="invoice"):
        entry = JournalEntry.objects.get(source=kind, source_id=str(doc_id))
        return sorted((l.account.name, str(l.debit), str(l.credit)) for l in entry.lines.select_related("account"))

    def test_create_and_issue_numbers_and_posts_like_a_manual_invoice(self):
        p = self.make(mode="issue")
        r = self.go(D(2026, 8, 31))
        doc = self.children(p).get()
        self.assertEqual((doc.status, doc.number), ("issued", "T2N/INV/26/3108/00001"))
        self.assertEqual(r["generated"][0]["number"], "T2N/INV/26/3108/00001")
        self.assertIsNotNone(doc.issued_document_id)
        manual = self.invoice(issue_date="2026-08-31")
        self.assertEqual(manual["number"], "T2N/INV/26/3108/00002")  # one sequence, no gap, no clash
        self.assertEqual(self.lines(doc.pk), self.lines(manual["id"]))
        entry = JournalEntry.objects.get(source="invoice", source_id=str(doc.pk))
        self.assertEqual(sum(l.debit for l in entry.lines.all()), sum(l.credit for l in entry.lines.all()))

    def test_gst_intra_and_inter_state_totals_match_a_manual_invoice(self):
        self.enable_gst("GST18")
        local = self.customer("Local Co", state_code="29")
        far = self.customer("Far Co", state_code="27")
        cases = {"local": (local, "2124.00", "324.00", ["CGST @ 9%", "SGST @ 9%"]),
                 "far": (far, "2124.00", "324.00", ["IGST @ 18%"])}
        for name, (customer, grand, tax, labels) in cases.items():
            p = self.make(name=name, customer=customer["id"], mode="issue")
            self.go(D(2026, 8, 31))
            doc = self.children(p).get()
            manual = self.invoice(customer, issue_date="2026-08-31")
            self.assertEqual((str(doc.grand_total), str(doc.tax_total)), (grand, tax), name)
            self.assertEqual([t["label"] for t in doc.record["totals"]["tax"]], labels, name)
            self.assertEqual((str(doc.grand_total), doc.record["totals"], doc.record["place_of_supply"], doc.record["supply_type"]),
                             (manual["grand_total"], manual["record"]["totals"], manual["record"]["place_of_supply"],
                              manual["record"]["supply_type"]), name)
            self.assertEqual(self.lines(doc.pk), self.lines(manual["id"]), name)
        detail = self.fin.get(f"{S}/recurring-invoices/{RecurringInvoice.objects.get(name='far').pk}/").json()
        self.assertEqual((detail["preview"]["supply_type"], detail["preview"]["place_of_supply"]["code"]), ("inter_state", "27"))
        self.assertEqual(detail["preview"]["totals"]["grand_total"], "2124.00")

    def test_the_customers_current_place_of_supply_is_used_at_each_run(self):
        self.enable_gst("GST18")
        c = self.customer("Mover", state_code="29")
        p = self.make(customer=c["id"], mode="issue")
        self.go(D(2026, 8, 31))
        self.assertEqual(self.fin.patch(f"{S}/customers/{c['id']}/", {"state_code": "27"}).status_code, 200)
        self.go(D(2026, 9, 30))
        first, second = self.children(p)
        self.assertEqual([t["label"] for t in first.record["totals"]["tax"]], ["CGST @ 9%", "SGST @ 9%"])
        self.assertEqual([t["label"] for t in second.record["totals"]["tax"]], ["IGST @ 18%"])

    def test_bad_gst_rate_is_refused_when_the_profile_is_saved(self):
        self.enable_gst("GST18")
        bad = [{"description": "Dev", "quantity": "1", "rate": "100", "gst_rate": "7"}]
        r = self.fin.post(f"{S}/recurring-invoices/", self.payload(form={"items": bad}), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("items", r.json())

    def test_project_tag_reaches_the_invoice_and_the_books(self):
        proj = self.api.post("/api/v1/projects/projects/", {
            "name": "Alpha", "customer": self.acme["id"], "project_type": "it_solution", "status": "active",
            "start_date": "2026-08-01", "planned_end_date": "2026-12-31", "contract_value": "100000.00", "budget": "60000.00"},
            format="json").json()
        p = self.make(mode="issue", project=proj["id"])
        self.go(D(2026, 8, 31))
        doc = self.children(p).get()
        self.assertEqual(str(doc.project_id), proj["id"])
        entry = JournalEntry.objects.get(source="invoice", source_id=str(doc.pk))
        self.assertTrue(all(str(l.project_id) == proj["id"] for l in entry.lines.all()))


class FailureAndStateTests(RecurringBase):
    def test_one_failing_profile_does_not_stop_the_others_and_is_recorded(self):
        broken_customer = self.customer("Soon switched off")
        bad = self.make(name="Broken", customer=broken_customer["id"])
        good = self.make(name="Fine")
        from apps.sales.models import Customer
        Customer.objects.filter(pk=broken_customer["id"]).update(is_active=False)
        r = self.go(D(2026, 8, 31))
        self.assertEqual([g["name"] for g in r["generated"]], ["Fine"])
        self.assertEqual([e["name"] for e in r["errors"]], ["Broken"])
        self.assertEqual(self.children(good).count(), 1)
        prof = self.profile(bad)
        self.assertIn("switched off", prof.last_error)
        self.assertIsNotNone(prof.last_error_at)
        self.assertEqual((prof.next_run_date, prof.occurrences_done, self.children(bad).count()), (D(2026, 8, 31), 0, 0))
        detail = self.fin.get(f"{S}/recurring-invoices/{bad['id']}/").json()
        self.assertIn("switched off", detail["last_error"])
        Customer.objects.filter(pk=broken_customer["id"]).update(is_active=True)
        self.go(D(2026, 8, 31))
        prof = self.profile(bad)
        self.assertEqual((prof.last_error, prof.occurrences_done), ("", 1))

    def test_an_issue_failure_rolls_the_whole_run_back(self):
        p = self.make(mode="issue")
        from unittest import mock
        with mock.patch("apps.sales.services.issue", side_effect=__import__("django").core.exceptions.ValidationError("Numbering is off")):
            r = self.go(D(2026, 8, 31))
        self.assertEqual(r["generated"], [])
        self.assertIn("Numbering is off", r["errors"][0]["error"])
        self.assertEqual(self.children(p).count(), 0)  # no half-made draft left behind
        self.assertEqual(self.profile(p).next_run_date, D(2026, 8, 31))
        self.assertEqual(len(self.go(D(2026, 8, 31))["generated"]), 1)  # and it simply works next time

    def test_pause_resume_and_stop(self):
        p = self.make()
        pid = p["id"]
        self.assertEqual(self.fin.post(f"{S}/recurring-invoices/{pid}/resume/").status_code, 400)  # not paused
        r = self.fin.post(f"{S}/recurring-invoices/{pid}/pause/")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "paused"))
        self.assertEqual(self.fin.post(f"{S}/recurring-invoices/{pid}/pause/").status_code, 400)
        self.assertEqual(self.go(D(2026, 11, 15))["generated"], [])
        self.assertEqual(self.fin.post(f"{S}/recurring-invoices/{pid}/generate/").status_code, 400)  # resume first
        # runs missed while paused are not made: the next run is the first scheduled date from the resume date
        recurring.resume(self.profile(p), self.finance, today=D(2026, 11, 15))
        self.assertEqual(self.profile(p).next_run_date, D(2026, 11, 30))
        self.assertEqual(self.profile(p).status, "active")
        r = self.fin.post(f"{S}/recurring-invoices/{pid}/stop/", {"reason": "Contract ended"})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["next_run_date"]), (200, "stopped", None))
        self.assertEqual(self.profile(p).stop_reason, "Contract ended")
        self.assertEqual(self.go(D(2027, 1, 31))["generated"], [])
        for action in ("pause", "resume", "stop", "generate"):
            self.assertEqual(self.fin.post(f"{S}/recurring-invoices/{pid}/{action}/").status_code, 400, action)
        self.assertEqual(self.fin.patch(f"{S}/recurring-invoices/{pid}/", {"name": "Renamed"}, format="json").status_code, 400)
        self.assertEqual(self.children(p).count(), 0)

    def test_resume_keeps_a_future_next_run(self):
        p = self.make(start_date="2026-12-31")
        recurring.pause(self.profile(p), self.finance)
        recurring.resume(self.profile(p), self.finance, today=D(2026, 11, 15))
        self.assertEqual(self.profile(p).next_run_date, D(2026, 12, 31))

    def test_generate_next_makes_one_invoice_now_even_before_its_date(self):
        p = self.make(start_date="2030-01-31")
        r = self.fin.post(f"{S}/recurring-invoices/{p['id']}/generate/")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["invoice"]["status"], body["occurrences_done"], body["next_run_date"]), ("draft", 1, "2030-02-28"))
        doc = self.children(p).get()
        self.assertEqual(doc.recurring_run_date, D(2030, 1, 31))
        self.assertEqual(doc.issue_date, datetime.date.today())  # generated early, so dated today
        self.fin.post(f"{S}/recurring-invoices/{p['id']}/generate/")
        self.assertEqual(self.children(p).count(), 2)
        self.assertEqual([c["recurring_run_date"] for c in self.fin.get(f"{S}/recurring-invoices/{p['id']}/").json()["children"]],
                         ["2030-02-28", "2030-01-31"])

    def test_generate_failure_is_returned_and_recorded(self):
        c = self.customer("Gone")
        p = self.make(customer=c["id"])
        from apps.sales.models import Customer
        Customer.objects.filter(pk=c["id"]).update(is_active=False)
        r = self.fin.post(f"{S}/recurring-invoices/{p['id']}/generate/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("switched off", self.profile(p).last_error)

    def test_run_due_endpoint(self):
        p = self.make(start_date="2020-01-31", frequency="yearly")
        r = self.fin.post(f"{S}/recurring-invoices/run-due/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["generated"]), self.children(p).count())
        self.assertGreaterEqual(self.children(p).count(), 6)  # 2020 to 2026
        self.assertEqual(self.fin.post(f"{S}/recurring-invoices/run-due/").json()["generated"], [])

    def test_delete_only_before_any_invoice_exists(self):
        a, b = self.make(name="A"), self.make(name="B")
        self.go(D(2026, 8, 31), limit=1)
        self.assertEqual(self.fin.delete(f"{S}/recurring-invoices/{a['id']}/").status_code, 400)
        self.assertEqual(self.fin.delete(f"{S}/recurring-invoices/{b['id']}/").status_code, 400)  # both ran once
        c = self.make(name="C", start_date="2030-01-01")
        self.assertEqual(self.fin.delete(f"{S}/recurring-invoices/{c['id']}/").status_code, 204)
        self.assertEqual(self.fin.get(f"{S}/recurring-invoices/{c['id']}/").status_code, 404)
        self.assertEqual(self.audit(c, "deleted").count(), 1)


class EditTests(RecurringBase):
    def test_an_edit_applies_to_future_invoices_only(self):
        p = self.make()
        self.go(D(2026, 8, 31))
        r = self.fin.patch(f"{S}/recurring-invoices/{p['id']}/", {"form": {"items": [
            {"description": "Development", "quantity": "2", "rate": "2000", "discount_percent": "10"}]}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.go(D(2026, 9, 30))
        first, second = self.children(p)
        self.assertEqual((str(first.grand_total), str(second.grand_total)), ("1800.00", "3600.00"))
        self.assertEqual(first.form["items"][0]["rate"], "1000")

    def test_changing_the_rhythm_never_back_fills_and_start_date_is_locked_after_runs(self):
        p = self.make()
        self.go(D(2026, 8, 31))
        prof = self.profile(p)
        recurring.update(prof, self.finance, {"frequency": "weekly"}, can_issue=True, today=D(2026, 9, 10))
        # weekly from 31 Aug: 7, 14 Sep ...; the first on or after 10 Sep is 14 Sep
        self.assertEqual(self.profile(p).next_run_date, D(2026, 9, 14))
        r = self.fin.patch(f"{S}/recurring-invoices/{p['id']}/", {"start_date": "2026-09-01"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("start_date", r.json())

    def test_changing_the_schedule_before_any_run_moves_the_first_run(self):
        p = self.make()
        r = self.fin.patch(f"{S}/recurring-invoices/{p['id']}/", {"start_date": "2026-10-31", "frequency": "custom",
                                                                  "interval_unit": "days", "interval_count": 10}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["next_run_date"], r.json()["schedule_label"]), ("2026-10-31", "Every 10 days"))
        self.assertEqual(r.json()["upcoming"][:3], ["2026-10-31", "2026-11-10", "2026-11-20"])

    def test_setting_an_earlier_end_completes_the_profile(self):
        p = self.make()
        self.go(D(2026, 9, 30))
        r = self.fin.patch(f"{S}/recurring-invoices/{p['id']}/", {"end_date": "2026-10-01"}, format="json")
        self.assertEqual((r.json()["status"], r.json()["next_run_date"]), ("completed", None))

    def test_validation(self):
        bad = [({"name": " "}, "name"), ({"customer": ""}, "customer"), ({"customer": ZERO_ID}, "customer"),
               ({"customer": "nope"}, "customer"), ({"frequency": "daily"}, "frequency"), ({"mode": "email"}, "mode"),
               ({"start_date": ""}, "start_date"), ({"start_date": "31/08/2026"}, "start_date"),
               ({"end_date": "2026-08-30"}, "end_date"), ({"max_occurrences": 0}, "max_occurrences"),
               ({"due_days": -1}, "due_days"), ({"frequency": "custom"}, "interval_unit"),
               ({"frequency": "custom", "interval_unit": "days", "interval_count": 0}, "interval_count"),
               ({"frequency": "custom", "interval_unit": "months", "interval_count": 61}, "interval_count"),
               ({"form": {"items": []}}, "items"), ({"form": {"items": [{"description": "x", "quantity": "0", "rate": "5"}]}}, "items"),
               ({"project": ZERO_ID}, "project")]
        for over, field in bad:
            r = self.fin.post(f"{S}/recurring-invoices/", self.payload(**over), format="json")
            self.assertEqual(r.status_code, 400, over)
            self.assertIn(field, r.json(), over)
        self.assertEqual(RecurringInvoice.objects.count(), 0)

    def test_inactive_customer_is_refused(self):
        c = self.customer("Off")
        from apps.sales.models import Customer
        Customer.objects.filter(pk=c["id"]).update(is_active=False)
        r = self.fin.post(f"{S}/recurring-invoices/", self.payload(customer=c["id"]), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("customer", r.json())

    def test_unknown_profile_is_404(self):
        for method, url in (("get", ""), ("patch", ""), ("delete", ""), ("post", "pause/"), ("post", "generate/")):
            r = getattr(self.fin, method)(f"{S}/recurring-invoices/{ZERO_ID}/{url}")
            self.assertEqual(r.status_code, 404, (method, url))


class PermissionAndAuditTests(RecurringBase):
    def test_roles(self):
        pm = client_for(make_user("pm", Role.PROJECT_MANAGER))  # can view sales, cannot manage it
        p = self.make()
        self.assertEqual(self.api_staff.get(f"{S}/recurring-invoices/").status_code, 403)
        self.assertEqual(self.api_staff.get(f"{S}/recurring-invoices/{p['id']}/").status_code, 403)
        self.assertEqual(self.anon.get(f"{S}/recurring-invoices/").status_code, 401)
        self.assertEqual(pm.get(f"{S}/recurring-invoices/").status_code, 200)
        self.assertEqual(pm.get(f"{S}/recurring-invoices/{p['id']}/").status_code, 200)
        for method, url, body in (("post", "", self.payload()), ("patch", f"{p['id']}/", {"name": "x"}), ("delete", f"{p['id']}/", None),
                                  ("post", f"{p['id']}/pause/", None), ("post", f"{p['id']}/generate/", None),
                                  ("post", "run-due/", None)):
            for who in (pm, self.api_staff):
                r = getattr(who, method)(f"{S}/recurring-invoices/{url}", *([body] if body else []), format="json")
                self.assertEqual(r.status_code, 403, (method, url))
        for client in (self.fin, self.api):
            self.assertEqual(client.get(f"{S}/recurring-invoices/").status_code, 200)
        self.assertEqual(RecurringInvoice.objects.count(), 1)

    def test_a_manager_who_cannot_issue_can_only_use_draft_mode(self):
        # the partner roles always hold sales.issue, so the manager here is a project manager given sales.manage only
        mgr = viewer_client("mgr", "sales.manage")
        r = mgr.post(f"{S}/recurring-invoices/", self.payload(mode="issue"), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("mode", r.json())
        draft = mgr.post(f"{S}/recurring-invoices/", self.payload(mode="draft"), format="json")
        self.assertEqual(draft.status_code, 201)
        issuing = self.make(name="Issues", mode="issue")  # made by finance, who can issue
        # this person's Run now / run-due never issues: the issue-mode profile is left alone
        self.assertEqual(mgr.post(f"{S}/recurring-invoices/{issuing['id']}/generate/").status_code, 400)
        result = mgr.post(f"{S}/recurring-invoices/run-due/").json()
        self.assertEqual({g["name"] for g in result["generated"]}, {"Acme retainer"})
        self.assertEqual(self.children(issuing).count(), 0)
        # the same person may still edit an issue-mode profile they did not switch
        self.assertEqual(mgr.patch(f"{S}/recurring-invoices/{issuing['id']}/", {"name": "Issues monthly"}, format="json").status_code, 200)

    def test_every_action_is_audited(self):
        p = self.make()
        self.assertEqual(self.audit(p, "created").count(), 1)
        self.fin.patch(f"{S}/recurring-invoices/{p['id']}/", {"name": "Renamed"}, format="json")
        ev = self.audit(p, "updated").get()
        self.assertEqual((ev.previous_values["name"], ev.new_values["name"]), ("Acme retainer", "Renamed"))
        self.fin.post(f"{S}/recurring-invoices/{p['id']}/pause/")
        self.assertEqual(self.audit(p, "deactivated").count(), 1)
        recurring.resume(self.profile(p), self.finance, today=D(2026, 8, 1))
        self.assertEqual(self.audit(p, "activated").count(), 1)
        self.go(D(2026, 8, 31))
        gen = self.audit(p, "updated").order_by("occurred_at").last()
        self.assertEqual((gen.new_values["run_date"], gen.new_values["occurrences_done"]), ("2026-08-31", 1))
        self.fin.post(f"{S}/recurring-invoices/{p['id']}/stop/", {"reason": "Done"})
        stopped = self.audit(p, "deactivated").order_by("occurred_at").last()
        self.assertEqual((stopped.new_values["status"], stopped.reason), ("stopped", "Done"))
        self.assertEqual(CompanyProfileAuditEvent.objects.get(entity_type="recurring_invoice", action="created").actor_id, self.finance.pk)


class VisibilityTests(RecurringBase):
    def test_generated_invoices_link_back_to_their_profile(self):
        p = self.make(mode="issue")
        self.go(D(2026, 8, 31))
        doc = self.children(p).get()
        detail = self.fin.get(f"{S}/documents/{doc.pk}/").json()
        self.assertEqual((detail["recurring"]["id"], detail["recurring"]["name"], detail["recurring"]["run_date"]),
                         (p["id"], "Acme retainer", "2026-08-31"))
        self.assertEqual(detail["recurring_id"], p["id"])
        manual = self.fin.get(f"{S}/documents/{self.invoice()['id']}/").json()
        self.assertIsNone(manual["recurring"])
        listed = self.fin.get(f"{S}/documents/", {"recurring": p["id"]}).json()
        self.assertEqual([d["number"] for d in listed["results"]], [doc.number])
        profile = self.fin.get(f"{S}/recurring-invoices/{p['id']}/").json()
        self.assertEqual([c["number"] for c in profile["children"]], [doc.number])
        self.assertEqual(self.fin.get(f"{S}/documents/", {"recurring": "bad"}).status_code, 400)

    def test_list_filters_and_summary(self):
        a = self.make(name="Alpha monthly", start_date="2026-08-31")
        self.make(name="Beta", start_date="2030-01-01")
        self.fin.post(f"{S}/recurring-invoices/{a['id']}/pause/")
        r = self.fin.get(f"{S}/recurring-invoices/").json()
        self.assertEqual((r["count"], r["summary"]["active"], r["summary"]["paused"]), (2, 1, 1))
        self.assertEqual(self.fin.get(f"{S}/recurring-invoices/", {"status": "paused"}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{S}/recurring-invoices/", {"q": "beta"}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{S}/recurring-invoices/", {"customer": self.acme["id"]}).json()["count"], 2)

    def test_preview_endpoint_lists_the_next_dates(self):
        r = self.fin.post(f"{S}/recurring-invoices/preview/", {"start_date": "2026-01-31", "frequency": "monthly"}, format="json")
        self.assertEqual(r.json()["dates"], ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30"])
        r = self.fin.post(f"{S}/recurring-invoices/preview/", {"start_date": "2026-01-31", "frequency": "monthly", "max_occurrences": 2},
                          format="json")
        self.assertEqual(r.json()["dates"], ["2026-01-31", "2026-02-28"])
        self.assertEqual(self.fin.post(f"{S}/recurring-invoices/preview/", {"start_date": "2026-01-31", "frequency": "x"},
                                       format="json").status_code, 400)

    def test_dashboard_counts_profiles_waiting_to_run(self):
        self.make(start_date="2020-01-01")
        self.assertEqual(self.fin.get(f"{S}/dashboard/").json()["recurring_due"], 1)


class CommandTests(RecurringBase):
    def call(self, *args):
        out, err = StringIO(), StringIO()
        call_command("generate_recurring_invoices", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_command_creates_due_invoices_once(self):
        p = self.make(mode="issue")
        out, _ = self.call("--date", "2026-09-30")
        self.assertIn("2 invoice(s) created, 0 failure(s)", out)
        self.assertIn("T2N/INV/26/3108/00001", out)
        self.assertEqual(self.children(p).count(), 2)
        out, _ = self.call("--date", "2026-09-30")
        self.assertIn("0 invoice(s) created", out)
        self.assertEqual(self.children(p).count(), 2)
        self.assertEqual(self.profile(p).next_run_date, D(2026, 10, 31))
        # run by the scheduler, so there is no signed-in user: the audit trail still records it
        self.assertEqual(self.audit(p, "updated").filter(actor_id=None).count(), 2)

    def test_command_reports_failures_and_bad_input(self):
        from apps.sales.models import Customer
        c = self.customer("Off soon")
        self.make(customer=c["id"])
        Customer.objects.filter(pk=c["id"]).update(is_active=False)
        out, err = self.call("--date", "2026-09-30")
        self.assertIn("FAILED", err)
        self.assertIn("0 invoice(s) created, 1 failure(s)", out)
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.call("--date", "30-09-2026")


class DailyJobTests(RecurringBase):
    """Recurring invoices run on their own: from `run_daily_jobs` on a schedule, or on the first app load of the day."""

    def test_run_daily_jobs_command_makes_due_invoices_once(self):
        p = self.make(mode="issue")
        out = StringIO()
        call_command("run_daily_jobs", "--date", "2026-09-30", stdout=out, stderr=StringIO())
        self.assertIn("Recurring invoices: 2 recurring invoice(s) created, 0 failure(s)", out.getvalue())
        call_command("run_daily_jobs", "--date", "2026-09-30", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(self.children(p).count(), 2)

    def test_first_app_load_of_the_day_runs_the_jobs_once(self):
        from unittest import mock
        from django.test import override_settings
        from apps.company import daily
        p = self.make(mode="issue", start_date=datetime.date.today().isoformat())
        daily._state["date"] = None
        with override_settings(DAILY_JOBS_ON_REQUEST=True), mock.patch.object(daily, "run_all", wraps=daily.run_all) as spy:
            self.assertEqual(self.fin.get("/api/v1/auth/me/").status_code, 200)
            self.assertEqual(self.fin.get("/api/v1/auth/me/").status_code, 200)
            self.assertEqual(spy.call_count, 1)  # only the first request of the day
        self.assertEqual(self.children(p).count(), 1)
        daily._state["date"] = None
        with override_settings(DAILY_JOBS_ON_REQUEST=False):  # switched off (and always off in tests)
            self.fin.get("/api/v1/auth/me/")
        self.assertIsNone(daily._state["date"])

    def test_a_failing_job_does_not_stop_the_others(self):
        from apps.company import daily
        ran = []
        daily.register("Boom", lambda company, today: 1 / 0)
        daily.register("After", lambda company, today: ran.append(today) or "ok")
        try:
            result = daily.run_all(self.company, D(2026, 9, 30))
        finally:
            daily._JOBS[:] = [j for j in daily._JOBS if j[0] not in ("Boom", "After")]
        self.assertIn(("Boom", False, "division by zero"), result)
        self.assertIn(("After", True, "ok"), result)
        self.assertEqual(ran, [D(2026, 9, 30)])
