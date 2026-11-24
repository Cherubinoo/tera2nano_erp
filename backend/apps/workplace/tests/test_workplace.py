import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Role
from apps.company.models import CompanyProfileAuditEvent, IssuedDocument, IssuedReferenceNumber
from apps.company.tests.base import CompanyTestMixin, client_for, make_user
from apps.workplace import services
from apps.workplace.models import Meeting, Task

try:
    import fitz  # PyMuPDF is only a test-time helper
    HAS_FITZ = True
except ImportError:  # pragma: no cover
    fitz, HAS_FITZ = None, False

W = "/api/v1/workplace"
TODAY = datetime.date.today()
User = get_user_model()


def iso(d):
    return d.isoformat()


def pdf_text(resp):
    data = b"".join(resp.streaming_content)
    assert data.startswith(b"%PDF")
    return "".join(p.get_text() for p in fitz.open(stream=data, filetype="pdf")) if HAS_FITZ else ""


class Base(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.hr = make_user("hr", Role.HR_MANAGER)
        self.mp = make_user("mp", Role.MANAGING_PARTNER)
        self.pm = make_user("pm", Role.PROJECT_MANAGER)
        self.partner = make_user("partner", Role.PARTNER)
        self.emp2 = make_user("emp2", Role.EMPLOYEE)
        self.api_hr, self.api_mp, self.api_pm = client_for(self.hr), client_for(self.mp), client_for(self.pm)
        self.api_partner, self.api_emp2 = client_for(self.partner), client_for(self.emp2)
        self.mgr = self.api_hr

    def meeting(self, client=None, expect=201, **over):
        payload = {"title": "Weekly sync", "meeting_type": "team", "date": "2026-10-26", "start_time": "10:00", "end_time": "11:00",
                   "venue": "Head office", **over}
        r = (client or self.mgr).post(f"{W}/meetings/", payload, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def task(self, client=None, expect=201, **over):
        payload = {"title": "Send the report", **over}
        r = (client or self.mgr).post(f"{W}/tasks/", payload, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def audit(self, entity, action=None):
        qs = CompanyProfileAuditEvent.objects.filter(entity_type=entity)
        return qs.filter(action=action) if action else qs


class RecurrenceTests(Base):
    def test_weekly_dates_are_seven_days_apart(self):
        got = services.occurrence_dates(datetime.date(2026, 10, 26), "weekly", datetime.date(2026, 10, 26), 3)
        self.assertEqual(got, [datetime.date(2026, 11, 2), datetime.date(2026, 11, 9), datetime.date(2026, 11, 16)])

    def test_monthly_dates_keep_the_day_and_clamp_to_the_month_end(self):
        got = services.occurrence_dates(datetime.date(2026, 1, 31), "monthly", datetime.date(2026, 1, 31), 4)
        self.assertEqual(got, [datetime.date(2026, 2, 28), datetime.date(2026, 3, 31), datetime.date(2026, 4, 30),
                               datetime.date(2026, 5, 31)])

    def test_nothing_is_generated_for_a_meeting_that_does_not_repeat(self):
        self.assertEqual(services.occurrence_dates(datetime.date(2026, 1, 1), "none", datetime.date(2026, 1, 1), 3), [])

    def test_creating_a_weekly_meeting_with_occurrences_makes_the_series(self):
        m = self.meeting(recurrence="weekly", occurrences=3, attendees=[{"user": str(self.emp2.pk)}, {"name": "A. Kumar"}],
                         agenda=[{"title": "Status", "minutes": 15}])
        rows = Meeting.objects.filter(series_id=m["id"]).order_by("date")
        self.assertEqual([r.date.isoformat() for r in rows], ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16"])
        self.assertEqual(len({r.number for r in rows}), 4)
        last = rows.last()
        self.assertEqual(last.attendees.count(), 2)
        self.assertEqual(last.agenda.first().title, "Status")

    def test_generate_next_continues_after_the_last_and_never_duplicates(self):
        m = self.meeting(recurrence="monthly", date="2026-01-31", occurrences=1)
        r = self.mgr.post(f"{W}/meetings/{m['id']}/generate/", {"count": 2}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual([x["date"] for x in r.json()["created"]], ["2026-03-31", "2026-04-30"])
        dates = sorted(Meeting.objects.filter(series_id=m["id"]).values_list("date", flat=True))
        self.assertEqual(len(dates), len(set(dates)))
        self.assertEqual(len(dates), 4)

    def test_generate_needs_a_repeating_meeting_and_a_sane_count(self):
        m = self.meeting()
        self.assertEqual(self.mgr.post(f"{W}/meetings/{m['id']}/generate/", {"count": 2}, format="json").status_code, 400)
        rec = self.meeting(recurrence="weekly")
        self.assertEqual(self.mgr.post(f"{W}/meetings/{rec['id']}/generate/", {"count": 0}, format="json").status_code, 400)
        self.assertEqual(self.mgr.post(f"{W}/meetings/{rec['id']}/generate/", {"count": 99}, format="json").status_code, 400)


class MeetingTests(Base):
    def test_meeting_gets_a_meeting_id_with_year_and_day_month(self):
        m = self.meeting(date="2026-10-27")
        self.assertEqual(m["number"], "T2N-MTG-26-2710-0001")
        self.assertEqual(self.meeting(date="2026-10-27")["number"], "T2N-MTG-26-2710-0002")
        self.assertEqual(m["status"], "scheduled")
        self.assertEqual(m["organiser"]["name"], "hr")

    def test_validation(self):
        bad = lambda **o: self.meeting(expect=400, **o)  # noqa: E731
        self.assertIn("title", bad(title="  "))
        self.assertIn("end_time", bad(start_time="11:00", end_time="10:00"))
        self.assertIn("link", bad(link="ftp://x"))
        self.assertIn("meeting_type", bad(meeting_type="party"))
        self.assertIn("date", bad(date="26/10/2026"))
        self.assertIn("organiser", bad(organiser="00000000-0000-0000-0000-000000000000"))
        self.assertIn("recurrence", bad(recurrence="daily"))
        self.assertIn("name", bad(attendees=[{"name": ""}]))

    def test_attendees_users_and_external_names_agenda_and_edit(self):
        m = self.meeting(attendees=[{"user": str(self.emp2.pk)}, {"name": "A. Kumar", "designation": "Client"}],
                         agenda=[{"title": "Budget", "owner_name": "Sanjay", "minutes": 20}, {"title": "Next steps"}])
        d = self.mgr.get(f"{W}/meetings/{m['id']}/").json()
        self.assertEqual([(a["name"], a["external"]) for a in d["attendees"]], [("emp2", False), ("A. Kumar", True)])
        self.assertEqual([(a["title"], a["minutes"]) for a in d["agenda"]], [("Budget", 20), ("Next steps", None)])
        r = self.mgr.patch(f"{W}/meetings/{m['id']}/", {"title": "Weekly review", "agenda": [{"title": "Only"}]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["title"], "Weekly review")
        self.assertEqual(len(r.json()["agenda"]), 1)
        self.assertEqual(len(r.json()["attendees"]), 2)  # not sent, so unchanged

    def test_status_flow_and_reschedule(self):
        m = self.meeting()
        url = f"{W}/meetings/{m['id']}"
        self.assertEqual(self.mgr.post(f"{url}/status/", {"status": "cancelled"}, format="json").status_code, 400)  # reason needed
        r = self.mgr.post(f"{url}/status/", {"status": "cancelled", "reason": "Clash"}, format="json")
        self.assertEqual((r.status_code, r.json()["status"], r.json()["status_reason"]), (200, "cancelled", "Clash"))
        self.assertEqual(self.mgr.post(f"{url}/status/", {"status": "held"}, format="json").status_code, 400)
        self.assertEqual(self.mgr.post(f"{url}/reschedule/", {"date": "2026-11-02"}, format="json").status_code, 400)
        self.assertEqual(self.mgr.post(f"{url}/status/", {"status": "scheduled"}, format="json").json()["status"], "scheduled")
        r = self.mgr.post(f"{url}/reschedule/", {"date": "2026-11-02", "reason": "Public holiday"}, format="json")
        self.assertEqual((r.json()["status"], r.json()["date"]), ("rescheduled", "2026-11-02"))
        self.assertEqual(self.mgr.post(f"{url}/reschedule/", {"date": "2026-11-02"}, format="json").status_code, 400)  # no change
        self.assertEqual(self.mgr.post(f"{url}/status/", {"status": "held"}, format="json").json()["status"], "held")
        self.assertEqual(self.mgr.post(f"{url}/status/", {"status": "cancelled", "reason": "x"}, format="json").status_code, 400)
        self.assertEqual(self.mgr.post(f"{url}/status/", {"status": "rescheduled"}, format="json").status_code, 400)

    def test_decisions_and_action_items_become_tasks(self):
        m = self.meeting()
        url = f"{W}/meetings/{m['id']}"
        self.assertEqual(self.mgr.post(f"{url}/decisions/", {"text": ""}, format="json").status_code, 400)
        d = self.mgr.post(f"{url}/decisions/", {"text": "Go ahead with phase 2"}, format="json").json()
        r = self.mgr.post(f"{url}/actions/", {"title": "Share the timeline", "assignee": str(self.emp2.pk), "due_date": "2026-11-03",
                                              "priority": "high"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        t = r.json()
        self.assertEqual((t["source"], t["source_id"], t["assignee"]["name"]), ("meeting", m["id"], "emp2"))
        self.assertIn(m["number"], t["source_label"])
        detail = self.mgr.get(f"{url}/").json()
        self.assertEqual([x["text"] for x in detail["decisions"]], ["Go ahead with phase 2"])
        self.assertEqual([x["title"] for x in detail["actions"]], ["Share the timeline"])
        # the action item shows in the employee's own task list
        mine = self.api_emp2.get(f"{W}/tasks/", {"mine": 1}).json()
        self.assertEqual([x["title"] for x in mine["results"]], ["Share the timeline"])
        self.assertEqual(self.mgr.delete(f"{url}/decisions/{d['id']}/").status_code, 204)
        self.assertEqual(self.mgr.get(f"{url}/").json()["decisions"], [])

    def test_delete_and_audit(self):
        m = self.meeting()
        self.assertEqual(self.mgr.delete(f"{W}/meetings/{m['id']}/").status_code, 204)
        self.assertEqual(self.mgr.get(f"{W}/meetings/{m['id']}/").status_code, 404)
        self.assertEqual(self.audit("meeting", "created").count(), 1)
        self.assertEqual(self.audit("meeting", "deleted").count(), 1)

    def test_employee_sees_only_meetings_they_attend_or_organise(self):
        mine = self.meeting(title="With emp2", attendees=[{"user": str(self.emp2.pk)}])
        other = self.meeting(title="Partners only", meeting_type="partners")
        got = self.api_emp2.get(f"{W}/meetings/").json()
        self.assertEqual([x["title"] for x in got["results"]], ["With emp2"])
        self.assertEqual(self.api_emp2.get(f"{W}/meetings/{mine['id']}/").status_code, 200)
        self.assertEqual(self.api_emp2.get(f"{W}/meetings/{other['id']}/").status_code, 404)
        self.assertEqual(got["results"][0]["attendee_count"], 1)
        everyone = self.mgr.get(f"{W}/meetings/").json()
        self.assertEqual(everyone["count"], 2)

    def test_filters_and_calendar_range(self):
        self.meeting(title="Oct", date="2026-10-26")
        self.meeting(title="Nov", date="2026-11-09", meeting_type="client")
        r = self.mgr.get(f"{W}/meetings/", {"from": "2026-11-01", "to": "2026-11-30"}).json()
        self.assertEqual([x["title"] for x in r["results"]], ["Nov"])
        self.assertEqual(self.mgr.get(f"{W}/meetings/", {"type": "client"}).json()["count"], 1)
        self.assertEqual(self.mgr.get(f"{W}/meetings/", {"q": "oct"}).json()["count"], 1)
        future = self.meeting(title="Soon", date=iso(TODAY + datetime.timedelta(days=3)))
        up = self.mgr.get(f"{W}/meetings/", {"upcoming": 1}).json()
        self.assertEqual([x["id"] for x in up["results"]], [future["id"]])


class MinutesLinkTests(Base):
    def setUp(self):
        super().setUp()
        self.m = self.meeting(date="2026-10-27", title="Quarterly review", attendees=[{"user": str(self.emp2.pk), "designation": "Engineer"},
                                                                                       {"name": "A. Kumar", "designation": "Client"}],
                              agenda=[{"title": "Project status", "notes": "Phase 1 done"}])
        self.url = f"{W}/meetings/{self.m['id']}"
        self.mgr.post(f"{self.url}/decisions/", {"text": "Proceed to phase 2"}, format="json")
        self.mgr.post(f"{self.url}/actions/", {"title": "Share the timeline", "assignee": str(self.emp2.pk), "due_date": "2026-11-03"}, format="json")

    def test_draft_is_prefilled_from_the_meeting(self):
        d = self.mgr.get(f"{self.url}/minutes/").json()
        draft = d["draft"]
        self.assertEqual((draft["title"], draft["date"], draft["start_time"], draft["mode"]), ("Quarterly review", "2026-10-27", "10:00", "in_person"))
        self.assertEqual([a["name"] for a in draft["attendees"]], ["emp2", "A. Kumar"])
        self.assertEqual(draft["agenda"], ["Project status"])
        self.assertEqual([i["decision"] for i in draft["items"]], ["", "Proceed to phase 2"])
        self.assertEqual(draft["actions"], [{"action": "Share the timeline", "owner": "emp2", "due_date": "2026-11-03"}])
        self.assertEqual(d["meeting_id"], "T2N-MTG-26-2710-0001")

    def record(self, client=None):
        minutes = self.mgr.get(f"{self.url}/minutes/").json()["draft"]
        return (client or self.mgr).post(f"{self.url}/minutes/", {"minutes": minutes}, format="json")

    def test_recording_minutes_uses_the_company_generator_and_links_back(self):
        r = self.record()
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body["minutes"]["reference_number"], "T2N-MOM-26-2710-0001")
        self.assertEqual(body["meeting"]["status"], "held")
        doc = IssuedDocument.objects.get(pk=body["minutes"]["id"])
        # the very same issued record the company module lists: same type, tagged with this meeting's ID
        self.assertEqual((doc.document_type, doc.source_module, doc.source_id), ("meeting_minutes", "meeting", "T2N-MTG-26-2710-0001"))
        # the meeting ID was reused, not allocated a second time
        self.assertEqual(IssuedReferenceNumber.objects.filter(document_type="meeting").count(), 1)
        listed = self.mgr.get(f"/api/v1/company/issued-documents/?source_id={self.m['number']}").json()
        self.assertEqual(len(listed["results"] if isinstance(listed, dict) else listed), 1)
        detail = self.mgr.get(f"{self.url}/").json()
        self.assertEqual([x["reference_number"] for x in detail["minutes"]], ["T2N-MOM-26-2710-0001"])
        self.assertEqual({a["name"]: a["attended"] for a in detail["attendees"]}["emp2"], True)
        self.assertEqual(self.mgr.get(f"{W}/meetings/").json()["results"][0]["minutes_count"], 1)

    def test_minutes_pdf_has_a_seal_space_and_no_chairperson_line(self):
        doc_id = self.record().json()["minutes"]["id"]
        resp = self.mgr.get(f"{self.url}/minutes/{doc_id}/download/")
        self.assertEqual(resp.status_code, 200)
        if HAS_FITZ:
            text = pdf_text(resp)
            self.assertIn("Quarterly review", text)
            self.assertIn("Proceed to phase 2", text)
            self.assertNotIn("hairperson", text)
            self.assertNotIn("Authorised Signatory", text)
        self.assertEqual(self.api_emp2.get(f"{self.url}/minutes/{doc_id}/download/?variant=print").status_code, 200)

    def test_a_second_set_of_minutes_for_the_same_meeting_is_allowed_and_a_cancelled_meeting_has_none(self):
        self.assertEqual(self.record().status_code, 201)
        again = self.record()
        self.assertEqual(again.json()["minutes"]["reference_number"], "T2N-MOM-26-2710-0002")
        c = self.meeting(date="2026-10-28")
        self.mgr.post(f"{W}/meetings/{c['id']}/status/", {"status": "cancelled", "reason": "Off"}, format="json")
        r = self.mgr.post(f"{W}/meetings/{c['id']}/minutes/", {"minutes": {"title": "x", "agenda": ["a"]}}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_minutes_rules_are_the_company_modules(self):
        r = self.mgr.post(f"{self.url}/minutes/", {"minutes": {"title": "Only a title"}}, format="json")
        self.assertEqual(r.status_code, 400)  # needs attendees, agenda or a discussion item
        r = self.mgr.post(f"{self.url}/minutes/", {"minutes": {"title": "x", "agenda": ["a"], "start_time": "10:00", "end_time": "09:00"}},
                          format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.mgr.post(f"{self.url}/minutes/", {}, format="json").status_code, 400)

    def test_a_meeting_with_minutes_cannot_be_deleted(self):
        self.record()
        self.assertEqual(self.mgr.delete(f"{self.url}/").status_code, 400)

    def test_only_managers_record_minutes(self):
        self.assertEqual(self.api_emp2.get(f"{self.url}/minutes/").status_code, 403)
        self.assertEqual(self.record(self.api_emp2).status_code, 403)
        self.assertEqual(self.record(self.api_finance).status_code, 403)


class TaskTests(Base):
    def test_task_number_and_defaults(self):
        t = self.task(title="Prepare the budget", assignee=str(self.emp2.pk), due_date="2026-11-20", labels=["finance", "q3", "finance"])
        self.assertEqual(t["number"], f"T2N-TSK-{TODAY:%y}-{TODAY:%d%m}-0001")
        self.assertEqual((t["status"], t["priority"], t["source"], t["labels"]), ("todo", "medium", "manual", ["finance", "q3"]))
        self.assertEqual((t["assignee"]["name"], t["reporter"]["name"]), ("emp2", "hr"))
        self.assertTrue(self.task()["number"].endswith("-0002"))

    def test_validation(self):
        self.task(expect=400, title="")
        self.task(expect=400, due_date="soon")
        self.task(expect=400, priority="asap")
        self.task(expect=400, assignee="00000000-0000-0000-0000-000000000000")
        self.task(expect=400, labels=["x" * 31])

    def test_status_flow(self):
        t = self.task()
        url = f"{W}/tasks/{t['id']}/status/"
        post = lambda s: self.mgr.post(url, {"status": s}, format="json")  # noqa: E731
        self.assertEqual(post("in_progress").json()["status"], "in_progress")
        self.assertEqual(post("blocked").json()["status"], "blocked")
        self.assertEqual(post("blocked").status_code, 400)  # no change
        done = post("done").json()
        self.assertEqual(done["status"], "done")
        self.assertIsNotNone(done["completed_at"])
        self.assertEqual(post("in_progress").status_code, 400)  # a finished task can only be reopened
        reopened = post("todo").json()
        self.assertEqual((reopened["status"], reopened["completed_at"]), ("todo", None))
        self.assertEqual(post("cancelled").json()["status"], "cancelled")
        self.assertEqual(post("done").status_code, 400)
        self.assertEqual(post("nonsense").status_code, 400)

    def test_overdue_flag_and_filters(self):
        yesterday, tomorrow = iso(TODAY - datetime.timedelta(days=1)), iso(TODAY + datetime.timedelta(days=1))
        late = self.task(title="Late", due_date=yesterday, priority="urgent")
        self.task(title="Fine", due_date=tomorrow)
        finished = self.task(title="Finished late", due_date=yesterday)
        self.mgr.post(f"{W}/tasks/{finished['id']}/status/", {"status": "done"}, format="json")
        r = self.mgr.get(f"{W}/tasks/", {"overdue": 1}).json()
        self.assertEqual([t["title"] for t in r["results"]], ["Late"])
        self.assertTrue(r["results"][0]["overdue"])
        self.assertEqual(self.mgr.get(f"{W}/tasks/").json()["overdue"], 1)
        self.assertEqual(self.mgr.get(f"{W}/tasks/", {"status": "open"}).json()["count"], 2)
        self.assertEqual(self.mgr.get(f"{W}/tasks/", {"priority": "urgent"}).json()["results"][0]["id"], late["id"])
        self.assertEqual(self.mgr.get(f"{W}/tasks/", {"q": "fine"}).json()["count"], 1)
        self.assertEqual(self.mgr.get(f"{W}/tasks/", {"label": "nothing"}).json()["count"], 0)

    def test_subtasks_and_comments(self):
        t = self.task(assignee=str(self.emp2.pk))
        base = f"{W}/tasks/{t['id']}"
        i1 = self.api_emp2.post(f"{base}/items/", {"text": "Draft"}, format="json").json()
        self.mgr.post(f"{base}/items/", {"text": "Review"}, format="json")
        self.assertEqual(self.mgr.post(f"{base}/items/", {"text": ""}, format="json").status_code, 400)
        r = self.api_emp2.patch(f"{base}/items/{i1['id']}/", {"done": True}, format="json")
        self.assertTrue(r.json()["done"])
        c = self.api_emp2.post(f"{base}/comments/", {"body": "Started"}, format="json")
        self.assertEqual(c.status_code, 201)
        d = self.mgr.get(f"{base}/").json()
        self.assertEqual((d["subtasks_done"], d["subtasks_total"], d["comment_count"]), (1, 2, 1))
        self.assertEqual(d["comments"][0]["author"]["name"], "emp2")
        # only the author or a manager removes a comment
        self.assertEqual(self.api_emp2.delete(f"{base}/comments/{d['comments'][0]['id']}/").status_code, 204)
        self.assertEqual(self.mgr.delete(f"{base}/items/{i1['id']}/").status_code, 204)
        self.assertEqual(self.mgr.get(f"{base}/").json()["subtasks_total"], 1)


class MyTasksIsolationTests(Base):
    def test_every_user_sees_only_their_own_tasks_and_cannot_open_others(self):
        a = self.task(title="For staff", assignee=str(self.staff.pk))
        b = self.task(title="For emp2", assignee=str(self.emp2.pk))
        mine = self.api_staff.get(f"{W}/tasks/", {"mine": 1}).json()
        self.assertEqual([t["title"] for t in mine["results"]], ["For staff"])
        self.assertEqual([t["title"] for t in self.api_staff.get(f"{W}/tasks/").json()["results"]], ["For staff"])  # even without mine
        self.assertEqual(self.api_staff.get(f"{W}/tasks/{a['id']}/").status_code, 200)
        self.assertEqual(self.api_staff.get(f"{W}/tasks/{b['id']}/").status_code, 404)
        self.assertEqual(self.api_staff.post(f"{W}/tasks/{b['id']}/status/", {"status": "done"}, format="json").status_code, 404)
        self.assertEqual(self.api_staff.get(f"{W}/tasks/", {"assignee": str(self.emp2.pk)}).json()["count"], 0)
        self.assertEqual(self.mgr.get(f"{W}/tasks/").json()["count"], 2)
        self.assertEqual(self.mgr.get(f"{W}/tasks/", {"mine": 1}).json()["count"], 0)

    def test_employee_creates_own_tasks_but_not_for_others_and_cannot_edit_assigned_ones(self):
        r = self.api_staff.post(f"{W}/tasks/", {"title": "My note"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["assignee"]["name"], "staff")
        self.assertEqual(self.api_staff.post(f"{W}/tasks/", {"title": "x", "assignee": str(self.emp2.pk)}, format="json").status_code, 403)
        self.assertEqual(self.api_staff.post(f"{W}/tasks/", {"title": "x", "source": "meeting"}, format="json").status_code, 403)
        assigned = self.task(title="From boss", assignee=str(self.staff.pk))
        url = f"{W}/tasks/{assigned['id']}/"
        self.assertEqual(self.api_staff.patch(url, {"title": "Renamed"}, format="json").status_code, 403)
        self.assertEqual(self.api_staff.patch(url, {"status": "in_progress"}, format="json").json()["status"], "in_progress")
        self.assertEqual(self.api_staff.delete(url).status_code, 403)
        own = r.json()
        self.assertEqual(self.api_staff.patch(f"{W}/tasks/{own['id']}/", {"title": "Renamed note"}, format="json").status_code, 200)
        self.assertEqual(self.api_staff.delete(f"{W}/tasks/{own['id']}/").status_code, 204)
        self.assertEqual(self.mgr.delete(url).status_code, 204)

    def test_anonymous_is_refused_everywhere(self):
        for path in ("tasks/", "dashboard/", "announcements/", "people/", "meetings/", "circulars/", "assets/", "visitors/"):
            self.assertIn(self.anon.get(f"{W}/{path}").status_code, (401, 403), path)


class AnalyticsTests(Base):
    def test_counts_by_status_assignee_and_week_with_fixed_dates(self):
        today = datetime.date(2026, 11, 12)  # a Thursday; this week starts Monday 9 November
        from apps.company.models import CompanyProfile
        co = CompanyProfile.objects.first()

        def make(title, status, assignee, due=None, done_on=None):
            t = Task.objects.create(company=co, title=title, status=status, assignee=assignee, due_date=due)
            if done_on:
                t.completed_at = timezone.make_aware(datetime.datetime.combine(done_on, datetime.time(12, 0)))
                t.save()
            return t
        make("a", "todo", self.emp2, due=datetime.date(2026, 11, 10))      # overdue
        make("b", "in_progress", self.emp2, due=datetime.date(2026, 11, 20))
        make("c", "blocked", self.staff)
        make("d", "done", self.staff, done_on=datetime.date(2026, 11, 11))  # this week
        make("e", "done", self.staff, done_on=datetime.date(2026, 11, 9))   # this week (Monday)
        make("f", "done", self.emp2, done_on=datetime.date(2026, 11, 8))    # last week (Sunday)
        make("g", "cancelled", self.emp2)
        result = services.task_analytics(Task.objects.all(), today, weeks=3)
        self.assertEqual(result["by_status"], {"todo": 1, "in_progress": 1, "blocked": 1, "done": 3, "cancelled": 1})
        self.assertEqual((result["open"], result["total"], result["overdue"]), (3, 7, 1))
        self.assertEqual({r["name"]: r["open"] for r in result["by_assignee"]}, {"emp2": 2, "staff": 1})
        self.assertEqual([(w["week_start"], w["completed"]) for w in result["completed_per_week"]],
                         [("2026-10-26", 0), ("2026-11-02", 1), ("2026-11-09", 2)])

    def test_api_scope_follows_visibility(self):
        self.task(assignee=str(self.emp2.pk))
        self.task(assignee=str(self.staff.pk))
        self.assertEqual(self.mgr.get(f"{W}/tasks/analytics/").json()["total"], 2)
        self.assertEqual(self.api_staff.get(f"{W}/tasks/analytics/").json()["total"], 1)


class AnnouncementTests(Base):
    def ann(self, expect=201, **over):
        payload = {"title": "Office closed", "body": "**Diwali** holiday.", "audience": "all", **over}
        r = self.mgr.post(f"{W}/announcements/", payload, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def titles(self, client, **params):
        return [a["title"] for a in client.get(f"{W}/announcements/", params).json()]

    def test_audience_visibility_per_role(self):
        self.ann(title="Everyone")
        self.ann(title="Finance only", audience="roles", roles=["finance_manager"])
        self.ann(title="Staff and PMs", audience="roles", roles=["employee", "project_manager"])
        self.assertEqual(sorted(self.titles(self.api_staff)), ["Everyone", "Staff and PMs"])
        self.assertEqual(sorted(self.titles(self.api_finance)), ["Everyone", "Finance only"])
        self.assertEqual(sorted(self.titles(self.api_pm)), ["Everyone", "Staff and PMs"])
        self.assertEqual(sorted(self.titles(self.api_partner)), ["Everyone"])

    def test_publish_and_expiry_dates_and_pinning(self):
        d = lambda n: iso(TODAY + datetime.timedelta(days=n))  # noqa: E731
        self.ann(title="Old", publish_on=d(-10), expires_on=d(-1))
        self.ann(title="Soon", publish_on=d(2))
        self.ann(title="Live", publish_on=d(-3))
        self.ann(title="Pinned", publish_on=d(-8), pinned=True)
        self.assertEqual(self.titles(self.api_staff), ["Pinned", "Live"])
        everything = self.mgr.get(f"{W}/announcements/", {"all": 1}).json()
        self.assertEqual({a["title"]: a["state"] for a in everything},
                         {"Old": "expired", "Soon": "scheduled", "Live": "live", "Pinned": "live"})

    def test_employee_cannot_manage_or_list_everything(self):
        a = self.ann()
        self.assertEqual(self.api_staff.get(f"{W}/announcements/", {"all": 1}).status_code, 403)
        self.assertEqual(self.api_staff.post(f"{W}/announcements/", {"title": "x", "body": "y"}, format="json").status_code, 403)
        self.assertEqual(self.api_staff.patch(f"{W}/announcements/{a['id']}/", {"title": "x"}, format="json").status_code, 403)
        self.assertEqual(self.api_staff.delete(f"{W}/announcements/{a['id']}/").status_code, 403)
        self.assertEqual(self.api_staff.get(f"{W}/announcements/{a['id']}/").status_code, 200)

    def test_hidden_announcement_is_a_404_for_a_user_outside_the_audience(self):
        a = self.ann(audience="roles", roles=["finance_manager"])
        self.assertEqual(self.api_staff.get(f"{W}/announcements/{a['id']}/").status_code, 404)
        self.assertEqual(self.api_finance.get(f"{W}/announcements/{a['id']}/").status_code, 200)

    def test_validation_edit_delete_and_audit(self):
        self.ann(expect=400, title="")
        self.ann(expect=400, body="")
        self.ann(expect=400, audience="roles", roles=[])
        self.ann(expect=400, audience="roles", roles=["wizard"])
        self.ann(expect=400, publish_on="2026-10-10", expires_on="2026-10-01")
        a = self.ann()
        r = self.mgr.patch(f"{W}/announcements/{a['id']}/", {"pinned": True, "title": "Changed"}, format="json")
        self.assertEqual((r.json()["pinned"], r.json()["title"]), (True, "Changed"))
        self.assertEqual(self.mgr.delete(f"{W}/announcements/{a['id']}/").status_code, 204)
        self.assertEqual(self.mgr.get(f"{W}/announcements/{a['id']}/").status_code, 404)
        self.assertEqual([self.audit("announcement", x).count() for x in ("created", "updated", "deleted")], [1, 1, 1])


class CircularTests(Base):
    def circular(self, expect=201, **over):
        payload = {"subject": "Revised working hours", "body": "Office hours are **9:30 to 6:00** from Monday.\n\n- Lunch 1 to 2",
                   "addressed_to": "All employees", "issue_date": "2026-10-27", "audience": "all", **over}
        r = self.mgr.post(f"{W}/circulars/", payload, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def issue(self, c):
        r = self.mgr.post(f"{W}/circulars/{c['id']}/issue/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_issuing_makes_a_numbered_pdf_on_the_letterhead(self):
        c = self.circular()
        self.assertEqual((c["status"], c["number"]), ("draft", ""))
        self.assertEqual(self.api_staff.get(f"{W}/circulars/{c['id']}/").status_code, 404)  # drafts are not visible to staff
        issued = self.issue(c)
        self.assertEqual(issued["status"], "issued")
        self.assertEqual(issued["number"], "T2N/CIR/26/2710/00001")
        self.assertEqual(issued["issued"]["reference_number"], issued["number"])
        doc = IssuedDocument.objects.get(pk=issued["issued"]["id"])
        self.assertEqual((doc.document_type, doc.source_module, doc.source_id), ("circular", "workplace", c["id"]))
        resp = self.api_staff.get(f"{W}/circulars/{c['id']}/pdf/")
        self.assertEqual(resp.status_code, 200)
        if HAS_FITZ:
            text = pdf_text(resp)
            self.assertIn("Revised working hours", text)
            self.assertIn("9:30 to 6:00", text)
            self.assertIn("All employees", text)
        self.assertEqual(self.api_staff.get(f"{W}/circulars/{c['id']}/pdf/?variant=print").status_code, 200)

    def test_issued_circular_is_frozen_and_a_draft_can_be_edited_or_deleted(self):
        c = self.circular()
        r = self.mgr.patch(f"{W}/circulars/{c['id']}/", {"subject": "New subject"}, format="json")
        self.assertEqual(r.json()["subject"], "New subject")
        self.issue(c)
        self.assertEqual(self.mgr.patch(f"{W}/circulars/{c['id']}/", {"subject": "x"}, format="json").status_code, 400)
        self.assertEqual(self.mgr.delete(f"{W}/circulars/{c['id']}/").status_code, 400)
        self.assertEqual(self.mgr.post(f"{W}/circulars/{c['id']}/issue/").status_code, 400)
        d = self.circular()
        self.assertEqual(self.mgr.delete(f"{W}/circulars/{d['id']}/").status_code, 204)
        self.assertEqual(self.mgr.get(f"{W}/circulars/{d['id']}/").status_code, 404)

    def test_acknowledgement_counts_and_report(self):
        everyone = User.objects.filter(is_active=True).count()  # admin, admin2, finance, staff, hr, mp, pm, partner, emp2
        self.assertEqual(everyone, 9)
        c = self.issue(self.circular())
        self.assertEqual((c["recipients"], c["acknowledged_count"], c["acknowledged"]), (9, 0, False))
        r = self.api_staff.post(f"{W}/circulars/{c['id']}/acknowledge/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["acknowledged"])
        first = r.json()["acknowledged_at"]
        self.assertEqual(self.api_staff.post(f"{W}/circulars/{c['id']}/acknowledge/").json()["acknowledged_at"], first)  # idempotent
        self.api_emp2.post(f"{W}/circulars/{c['id']}/acknowledge/")
        report = self.mgr.get(f"{W}/circulars/{c['id']}/acknowledgements/").json()
        self.assertEqual((report["recipients"], report["acknowledged"], report["pending"]), (9, 2, 7))
        by_name = {r["name"]: r["acknowledged"] for r in report["rows"]}
        self.assertTrue(by_name["staff"] and by_name["emp2"])
        self.assertFalse(by_name["finance"])
        listed = self.mgr.get(f"{W}/circulars/").json()["results"][0]
        self.assertEqual((listed["recipients"], listed["acknowledged_count"]), (9, 2))
        pending = self.api_finance.get(f"{W}/circulars/", {"pending": 1}).json()
        self.assertEqual(pending["count"], 1)
        self.assertEqual(self.api_staff.get(f"{W}/circulars/", {"pending": 1}).json()["count"], 0)
        self.assertEqual(self.audit("circular", "issued").count(), 1)

    def test_role_audience_limits_recipients_and_visibility(self):
        c = self.issue(self.circular(audience="roles", roles=["employee", "finance_manager"]))
        self.assertEqual(c["recipients"], 3)  # staff, emp2, finance
        self.assertEqual(self.api_staff.get(f"{W}/circulars/{c['id']}/").status_code, 200)
        # someone outside the audience who is not a manager cannot open or acknowledge it
        only_finance = self.issue(self.circular(audience="roles", roles=["finance_manager"]))
        self.assertEqual(self.api_staff.get(f"{W}/circulars/{only_finance['id']}/").status_code, 404)
        self.assertEqual(self.api_staff.post(f"{W}/circulars/{only_finance['id']}/acknowledge/").status_code, 404)
        self.assertEqual(self.api_partner.get(f"{W}/circulars/{c['id']}/").status_code, 200)  # managers (both partner roles) see every circular
        report = self.mgr.get(f"{W}/circulars/{c['id']}/acknowledgements/").json()
        self.assertEqual(sorted(r["name"] for r in report["rows"]), ["emp2", "finance", "staff"])
        self.assertEqual(self.mgr.post(f"{W}/circulars/{c['id']}/acknowledge/").status_code, 400)  # a manager outside the audience

    def test_acknowledgement_report_is_for_managers_only_and_drafts_cannot_be_acknowledged(self):
        draft = self.circular()
        self.assertEqual(self.mgr.post(f"{W}/circulars/{draft['id']}/acknowledge/").status_code, 400)
        c = self.issue(self.circular())
        self.assertEqual(self.api_staff.get(f"{W}/circulars/{c['id']}/acknowledgements/").status_code, 403)
        self.assertEqual(self.api_staff.post(f"{W}/circulars/", {"subject": "x", "body": "y"}, format="json").status_code, 403)
        self.assertEqual(self.api_staff.post(f"{W}/circulars/{draft['id']}/issue/").status_code, 403)

    def test_validation(self):
        self.circular(expect=400, subject="")
        self.circular(expect=400, body="")
        self.circular(expect=400, audience="roles", roles=[])
        self.circular(expect=400, issue_date="tomorrow")

    def test_the_dashboard_lists_pending_acknowledgements(self):
        c = self.issue(self.circular())
        d = self.api_staff.get(f"{W}/dashboard/").json()
        self.assertEqual([x["id"] for x in d["pending_acknowledgements"]], [c["id"]])
        self.api_staff.post(f"{W}/circulars/{c['id']}/acknowledge/")
        self.assertEqual(self.api_staff.get(f"{W}/dashboard/").json()["pending_acknowledgement_count"], 0)


class AssetTests(Base):
    def asset(self, expect=201, **over):
        r = self.mgr.post(f"{W}/assets/", {"name": "Dell laptop", "category": "Laptop", **over}, format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def test_asset_tag_is_automatic_and_totals_are_exact(self):
        a = self.asset(purchase_value="55000.50", purchase_date="2026-04-01", warranty_expiry="2029-03-31", assigned_to=str(self.emp2.pk),
                       status="in_use")
        self.assertRegex(a["tag"], rf"^T2N-AST-{TODAY:%y}-{TODAY:%d%m}-0001$")
        self.assertEqual(a["assigned_to"]["name"], "emp2")
        b = self.asset(name="Monitor", category="Display", purchase_value="12000")
        self.assertTrue(b["tag"].endswith("-0002"))
        r = self.mgr.get(f"{W}/assets/").json()
        self.assertEqual((r["count"], r["total_value"]), (2, "67000.50"))
        self.assertEqual(r["categories"], ["Display", "Laptop"])
        self.assertEqual(self.mgr.get(f"{W}/assets/", {"status": "in_use"}).json()["count"], 1)
        self.assertEqual(self.mgr.get(f"{W}/assets/", {"q": "monitor"}).json()["count"], 1)

    def test_validation_and_warranty_state(self):
        self.asset(expect=400, name="")
        self.asset(expect=400, purchase_value="-5")
        self.asset(expect=400, purchase_value="abc")
        self.asset(expect=400, purchase_value="1.234")
        self.asset(expect=400, status="stolen")
        self.asset(expect=400, purchase_date="2026-05-01", warranty_expiry="2026-04-01")
        soon = self.asset(warranty_expiry=iso(TODAY + datetime.timedelta(days=30)))
        gone = self.asset(warranty_expiry=iso(TODAY - datetime.timedelta(days=1)))
        self.assertEqual((soon["warranty_state"], gone["warranty_state"]), ("expiring", "expired"))
        self.assertEqual(self.mgr.get(f"{W}/assets/", {"warranty_expiring": 1}).json()["count"], 2)

    def test_edit_delete_audit_404(self):
        a = self.asset()
        r = self.mgr.patch(f"{W}/assets/{a['id']}/", {"status": "in_repair", "notes": "Screen"}, format="json")
        self.assertEqual(r.json()["status"], "in_repair")
        self.assertEqual(self.mgr.delete(f"{W}/assets/{a['id']}/").status_code, 204)
        self.assertEqual(self.mgr.get(f"{W}/assets/{a['id']}/").status_code, 404)
        self.assertEqual([self.audit("asset", x).count() for x in ("created", "updated", "deleted")], [1, 1, 1])

    def test_only_managers_use_the_register(self):
        for client in (self.api_staff, self.api_finance):  # a partner holds workplace.manage, so it is not here
            self.assertEqual(client.get(f"{W}/assets/").status_code, 403)
            self.assertEqual(client.post(f"{W}/assets/", {"name": "x"}, format="json").status_code, 403)


class VisitorTests(Base):
    def visitor(self, expect=201, **over):
        r = self.mgr.post(f"{W}/visitors/", {"name": "R. Sharma", "visitor_company": "Acme", "purpose": "Demo", "phone": "9876543210", **over},
                          format="json")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def test_check_in_and_out(self):
        v = self.visitor(host=str(self.emp2.pk))
        self.assertTrue(v["inside"])
        self.assertEqual(v["host"]["name"], "emp2")
        self.assertEqual(self.mgr.get(f"{W}/visitors/", {"inside": 1}).json()["inside"], 1)
        out = self.mgr.post(f"{W}/visitors/{v['id']}/checkout/").json()
        self.assertFalse(out["inside"])
        self.assertEqual(self.mgr.post(f"{W}/visitors/{v['id']}/checkout/").status_code, 400)
        self.assertEqual(self.mgr.get(f"{W}/visitors/", {"inside": 1}).json()["count"], 0)

    def test_validation_and_filters(self):
        self.visitor(expect=400, name="")
        self.visitor(expect=400, purpose="")
        self.visitor(expect=400, time_in="2026-10-27T10:00", time_out="2026-10-27T09:00")
        self.visitor(time_in="2026-10-27T10:00", host_name="Front desk")
        self.visitor(name="Other", time_in="2026-10-28T10:00")
        self.assertEqual(self.mgr.get(f"{W}/visitors/", {"date": "2026-10-27"}).json()["count"], 1)
        self.assertEqual(self.mgr.get(f"{W}/visitors/", {"q": "other"}).json()["count"], 1)

    def test_phone_is_redacted_in_the_history_and_staff_are_refused(self):
        v = self.visitor()
        event = self.audit("visitor", "created").get()
        self.assertEqual(event.new_values["phone"], "[redacted]")
        self.assertEqual(self.api_staff.get(f"{W}/visitors/").status_code, 403)
        self.assertEqual(self.api_staff.post(f"{W}/visitors/{v['id']}/checkout/").status_code, 403)
        self.assertEqual(self.mgr.delete(f"{W}/visitors/{v['id']}/").status_code, 204)


class PermissionMatrixTests(Base):
    def test_who_can_schedule_meetings_and_tasks_for_others(self):
        expected = {"admin": 201, "mp": 201, "hr": 201, "pm": 201, "partner": 201, "finance": 403, "staff": 403}
        clients = {"admin": self.api, "mp": self.api_mp, "hr": self.api_hr, "pm": self.api_pm, "partner": self.api_partner,
                   "finance": self.api_finance, "staff": self.api_staff}
        for name, code in expected.items():
            r = clients[name].post(f"{W}/meetings/", {"title": f"By {name}", "date": "2026-10-26"}, format="json")
            self.assertEqual(r.status_code, code, (name, r.content))
            r = clients[name].post(f"{W}/tasks/", {"title": f"For emp2 by {name}", "assignee": str(self.emp2.pk)}, format="json")
            self.assertEqual(r.status_code, code, (name, r.content))

    def test_every_role_with_view_can_read_meetings_and_the_dashboard(self):
        for client in (self.api, self.api_mp, self.api_hr, self.api_pm, self.api_partner, self.api_finance, self.api_staff):
            self.assertEqual(client.get(f"{W}/meetings/").status_code, 200)
            self.assertEqual(client.get(f"{W}/circulars/").status_code, 200)
            d = client.get(f"{W}/dashboard/")
            self.assertEqual(d.status_code, 200)
            self.assertEqual(client.get(f"{W}/people/").status_code, 200)

    def test_without_the_view_permission_meetings_are_closed_but_my_tasks_still_work(self):
        from apps.accounts.models import RolePermission
        RolePermission.objects.create(role="employee", code="workplace.view", granted=False)
        self.assertEqual(self.api_staff.get(f"{W}/meetings/").status_code, 403)
        self.assertEqual(self.api_staff.get(f"{W}/circulars/").status_code, 403)
        self.assertEqual(self.api_staff.get(f"{W}/tasks/", {"mine": 1}).status_code, 200)
        self.assertEqual(self.api_staff.get(f"{W}/announcements/").status_code, 200)
        d = self.api_staff.get(f"{W}/dashboard/").json()
        self.assertEqual(d["upcoming_meetings"], [])

    def test_attachments_are_registered_for_circulars_assets_and_meetings(self):
        c = self.mgr.post(f"{W}/circulars/", {"subject": "S", "body": "B"}, format="json").json()
        for entity, who, code in (("circular", self.api_staff, 200), ("asset", self.api_staff, 403), ("asset", self.mgr, 200)):
            r = who.get("/api/v1/attachments/", {"entity_type": entity, "entity_id": c["id"]})
            self.assertEqual(r.status_code, code, (entity, r.content))


class DashboardTests(Base):
    def test_dashboard_contents_and_isolation(self):
        soon = self.meeting(title="Soon", date=iso(TODAY + datetime.timedelta(days=2)))
        self.meeting(title="Later", date=iso(TODAY + datetime.timedelta(days=30)))
        self.meeting(title="Past", date=iso(TODAY - datetime.timedelta(days=5)))
        self.task(title="Late for staff", assignee=str(self.staff.pk), due_date=iso(TODAY - datetime.timedelta(days=2)))
        self.task(title="Soon for staff", assignee=str(self.staff.pk), due_date=iso(TODAY + datetime.timedelta(days=2)))
        self.task(title="Late for emp2", assignee=str(self.emp2.pk), due_date=iso(TODAY - datetime.timedelta(days=2)))
        self.mgr.post(f"{W}/announcements/", {"title": "Hello", "body": "Welcome"}, format="json")
        d = self.mgr.get(f"{W}/dashboard/").json()
        self.assertEqual([m["title"] for m in d["upcoming_meetings"]], ["Soon", "Later"])
        self.assertEqual(d["upcoming_count"], 1)
        self.assertEqual((d["overdue_total"], d["my_open_count"]), (2, 0))
        self.assertEqual(d["analytics"]["total"], 3)
        self.assertEqual(len(d["meetings_by_month"]), 6)
        self.assertEqual([a["title"] for a in d["announcements"]], ["Hello"])
        s = self.api_staff.get(f"{W}/dashboard/").json()
        self.assertEqual([t["title"] for t in s["my_tasks_due"]], ["Late for staff", "Soon for staff"])
        self.assertEqual((s["my_overdue"], s["my_due_soon"], s["overdue_total"], s["analytics"]["total"]), (1, 1, 1, 2))
        self.assertEqual(s["upcoming_meetings"], [])  # not an attendee of any
        self.assertEqual(soon["status"], "scheduled")


class NotFoundTests(Base):
    def test_unknown_ids_are_404_for_every_resource(self):
        ghost = "11111111-1111-4111-8111-111111111111"
        for path in ("meetings", "tasks", "announcements", "circulars", "assets"):
            self.assertEqual(self.mgr.get(f"{W}/{path}/{ghost}/").status_code, 404, path)
        for path in ("meetings/%s/status/", "meetings/%s/generate/", "meetings/%s/decisions/", "meetings/%s/actions/",
                     "tasks/%s/status/", "tasks/%s/comments/", "tasks/%s/items/", "circulars/%s/issue/", "circulars/%s/acknowledge/",
                     "visitors/%s/checkout/"):
            self.assertEqual(self.mgr.post(f"{W}/{path % ghost}", {}, format="json").status_code, 404, path)
        self.assertEqual(self.mgr.get(f"{W}/meetings/{ghost}/minutes/").status_code, 404)
        self.assertEqual(self.mgr.delete(f"{W}/visitors/{ghost}/").status_code, 404)
        self.assertEqual(self.mgr.get(f"{W}/meetings/not-a-uuid/").status_code, 404)
        m = self.meeting()
        self.assertEqual(self.mgr.get(f"{W}/meetings/{m['id']}/minutes/{ghost}/download/").status_code, 404)
        self.assertEqual(self.mgr.delete(f"{W}/meetings/{m['id']}/decisions/{ghost}/").status_code, 404)
