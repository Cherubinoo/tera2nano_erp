import copy
import datetime
import os
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase

from apps.company.models import CompanyProfile, DocumentNumberSequence, IssuedDocument, IssuedReferenceNumber

from .base import API, CompanyTestMixin, png_bytes

MINUTES = {
    "title": "Quarterly project review",
    "date": "2026-10-27", "start_time": "10:00", "end_time": "11:30",
    "venue": "Head office, Ranipet", "mode": "hybrid",
    "minute_taker": "Sanjay",
    "attendees": [
        {"name": "Delight Cherubino", "designation": "Managing Partner", "status": "present"},
        {"name": "Sanjay", "designation": "Technical Partner", "status": "present"},
        {"name": "A. Kumar", "designation": "Client, Acme Industries", "status": "apologies"},
    ],
    "agenda": ["Project status", "Budget", "Next steps"],
    "items": [
        {"heading": "Project status", "discussion": "Phase 1 is complete and phase 2 has started.",
         "decision": "Proceed to phase 2 as planned."},
        {"heading": "Budget", "discussion": "Spend is within the approved budget.", "decision": ""},
    ],
    "actions": [
        {"action": "Share the revised timeline", "owner": "Sanjay", "due_date": "2026-11-03"},
        {"action": "Confirm the phase 2 budget", "owner": "Delight Cherubino", "due_date": ""},
    ],
    "next_meeting": "27 November 2026, 10:00",
}


class MinutesTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()

    def issue(self, minutes=None, **extra):
        payload = dict(document_type="meeting_minutes", issue_date="2026-10-27",
                       content={"minutes": minutes if minutes is not None else copy.deepcopy(MINUTES)})
        payload.update(extra)
        return self.api_finance.post(f"{API}/issued-documents/", payload)

    def body(self, resp):
        return b"".join(resp.streaming_content)

    def test_minutes_get_a_minutes_id_and_a_meeting_id(self):
        r = self.issue()
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(d["reference_number"], "T2N-MOM-26-2710-0001")
        self.assertEqual((d["source_module"], d["source_id"]), ("meeting", "T2N-MTG-26-2710-0001"))
        self.assertEqual(d["title"], "Minutes of Meeting")
        again = self.issue().json()
        self.assertEqual((again["reference_number"], again["source_id"]), ("T2N-MOM-26-2710-0002", "T2N-MTG-26-2710-0002"))

    def test_existing_meeting_id_is_reused_without_allocating_a_new_one(self):
        first = self.issue().json()
        m = copy.deepcopy(MINUTES)
        m["meeting_id"] = first["source_id"]
        second = self.issue(minutes=m).json()
        self.assertEqual(second["source_id"], "T2N-MTG-26-2710-0001")
        self.assertEqual(second["reference_number"], "T2N-MOM-26-2710-0002")
        self.assertEqual(IssuedReferenceNumber.objects.filter(document_type="meeting").count(), 1)

    def test_both_versions_render_and_the_plain_one_has_no_letterhead_artwork(self):
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")
        d = self.issue().json()
        letterhead = self.body(self.api.get(f"{API}/issued-documents/{d['id']}/download/"))
        plain = self.body(self.api.get(f"{API}/issued-documents/{d['id']}/download/?variant=print"))
        self.assertTrue(letterhead.startswith(b"%PDF") and plain.startswith(b"%PDF"))
        self.assertIn(b"/Subtype /Image", letterhead)
        self.assertNotIn(b"/Subtype /Image", plain)

    def test_a_long_set_of_minutes_runs_over_several_pages(self):
        m = copy.deepcopy(MINUTES)
        m["items"] = [{"heading": f"Item {n}", "discussion": "Discussion text. " * 80, "decision": "Decision. " * 20}
                      for n in range(1, 25)]
        m["attendees"] = [{"name": f"Person {n}", "designation": "Team", "status": "present"} for n in range(60)]
        r = self.issue(minutes=m)
        self.assertEqual(r.status_code, 201, r.content)
        pdf = self.body(self.api.get(f"{API}/issued-documents/{r.json()['id']}/download/"))
        self.assertGreater(pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page "), 3)

    def test_minutes_can_use_a_letterhead_template_for_their_id(self):
        d = self.issue(letterhead_template="LHT-006").json()
        self.assertEqual(d["reference_number"], "LHT-006/26/2710/00001")
        self.assertEqual(d["source_id"], "T2N-MTG-26-2710-0001")  # the meeting still has its own ID

    def test_minutes_are_frozen_with_the_issued_record(self):
        d = self.issue().json()
        snap = self.api.get(f"{API}/issued-documents/{d['id']}/snapshot/").json()
        self.assertEqual(snap["company"]["display_name"], "Example Ventures")
        doc = IssuedDocument.objects.get(pk=d["id"])
        self.assertEqual(doc.document_type, "meeting_minutes")
        self.assertTrue(doc.print_file and doc.file)

    def test_validation(self):
        def bad(**over):
            m = copy.deepcopy(MINUTES)
            m.update(over)
            return self.issue(minutes=m).status_code

        self.assertEqual(bad(title=""), 400)
        self.assertEqual(bad(date="27/10/2026"), 400)
        self.assertEqual(bad(start_time="11:00", end_time="10:00"), 400)
        self.assertEqual(bad(start_time="25:00"), 400)
        self.assertEqual(bad(mode="telepathy"), 400)
        self.assertEqual(bad(attendees=[{"name": "A", "status": "maybe"}]), 400)
        self.assertEqual(bad(attendees=[{"name": ""}]), 400)
        self.assertEqual(bad(actions=[{"action": "x", "due_date": "soon"}]), 400)
        self.assertEqual(bad(attendees=[], agenda=[], items=[]), 400)
        self.assertEqual(bad(agenda="not a list"), 400)
        self.assertEqual(bad(items=[{"heading": ""}]), 400)
        self.assertEqual(bad(title="x" * 300), 400)
        r = self.api_finance.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", content={"minutes": MINUTES}, issue_date="2026-10-27"))
        self.assertEqual(r.status_code, 400)

    def test_if_the_meeting_sequence_is_off_no_minutes_number_is_consumed(self):
        DocumentNumberSequence.objects.filter(document_type="meeting").update(is_active=False)
        self.assertEqual(self.issue().status_code, 400)
        self.assertEqual(IssuedReferenceNumber.objects.filter(document_type="meeting_minutes").count(), 0)
        DocumentNumberSequence.objects.filter(document_type="meeting").update(is_active=True)
        self.assertEqual(self.issue().json()["reference_number"], "T2N-MOM-26-2710-0001")

    def test_voiding_minutes_keeps_the_meeting_id(self):
        d = self.issue().json()
        r = self.api.post(f"{API}/issued-documents/{d['id']}/void/", {"change_reason": "Wrong attendee list"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(IssuedReferenceNumber.objects.get(reference_number="T2N-MOM-26-2710-0001").status, "void")
        self.assertEqual(IssuedReferenceNumber.objects.get(reference_number="T2N-MTG-26-2710-0001").status, "issued")

    def test_staff_can_issue_minutes_but_not_anonymous(self):
        r = self.api_staff.post(f"{API}/issued-documents/", dict(
            document_type="meeting_minutes", issue_date="2026-10-27", content={"minutes": MINUTES}))
        self.assertEqual(r.status_code, 201, r.content)
        r = self.anon.post(f"{API}/issued-documents/", dict(
            document_type="meeting_minutes", issue_date="2026-10-27", content={"minutes": MINUTES}))
        self.assertEqual(r.status_code, 401)


class MissingStoredFileTests(CompanyTestMixin, TestCase):
    """The database can list a logo whose file is gone (for example the private folder was cleared)."""

    def setUp(self):
        super().setUp()
        self.full_company()
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")
        company = CompanyProfile.objects.get()
        os.remove(company.logo.path)

    def test_logo_endpoint_answers_404_not_500(self):
        r = self.api.get(f"{API}/profile/logo/")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "file_missing")

    def test_documents_are_still_issued_without_the_missing_logo(self):
        r = self.api_finance.post(f"{API}/issued-documents/", dict(
            document_type="business_letter", issue_date="2026-10-27", content={"paragraphs": ["x"]}))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(IssuedDocument.objects.get().logo_snapshot)

    def test_branding_preview_still_renders(self):
        self.assertEqual(self.api.post(f"{API}/branding/preview/", {}).status_code, 200)

    def test_uploading_again_restores_it(self):
        self.api.post(f"{API}/profile/logo/", {"file": SimpleUploadedFile("l.png", png_bytes())}, format="multipart")
        self.assertEqual(self.api.get(f"{API}/profile/logo/").status_code, 200)


class CommandSeedsNumberingTests(CompanyTestMixin, TestCase):
    def test_apply_command_creates_default_sequences_including_meetings_and_minutes(self):
        out = StringIO()
        call_command("apply_tera2nano_letterhead", stdout=out)
        types = set(DocumentNumberSequence.objects.filter(letterhead_template__isnull=True)
                    .values_list("document_type", flat=True))
        self.assertTrue({"meeting", "meeting_minutes", "invoice", "quotation", "employee", "project"} <= types)
        self.assertEqual(DocumentNumberSequence.objects.get(document_type="meeting_minutes").preview(), f"T2N-MOM-{datetime.date.today():%y}-{datetime.date.today():%d%m}-0001")
        self.assertIn("default numbering sequences", out.getvalue())
        out2 = StringIO()
        call_command("apply_tera2nano_letterhead", stdout=out2)
        self.assertNotIn("default numbering sequences", out2.getvalue())  # nothing created the second time
