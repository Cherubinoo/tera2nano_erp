import datetime

from apps.company.models import CompanyProfileAuditEvent
from apps.crm.models import Account, Activity, Contact, Deal, Lead
from apps.crm.services import activities as activity_service
from apps.sales.models import Customer

from .base import C, CrmBase

Event = CompanyProfileAuditEvent


class LeadScoringTests(CrmBase):
    def test_score_adds_source_value_contact_and_engagement_points(self):
        # referral 25 + value of 6 lakh 20 + email 5 + phone 5 = 55 (warm)
        lead = self.lead(source="referral", estimated_value="600000", email="a@acme.in", phone="9876543210")
        self.assertEqual(lead["score"], 55)
        self.assertEqual(lead["suggested_rating"], "warm")
        parts = {p["label"].split(" (")[0]: p["points"] for p in lead["score_breakdown"]}
        self.assertEqual(parts["Source: referral"], 25)
        self.assertEqual(parts["Estimated value"], 20)
        # two completed calls add 5 points each: 65, which is hot
        for subject in ("Intro call", "Demo call"):
            a = self.activity(type="call", subject=subject, lead=lead["id"])
            self.assertEqual(self.crm.post(f"{C}/activities/{a['id']}/complete/", {"outcome": "Spoke"}).status_code, 200)
        self.assertEqual(self.crm.get(f"{C}/leads/{lead['id']}/").json()["score"], 65)
        self.assertEqual(self.crm.get(f"{C}/leads/{lead['id']}/").json()["suggested_rating"], "hot")

    def test_lowest_score_and_the_engagement_cap(self):
        cold = self.lead(company_name="Tiny Co", source="cold_call")  # 5 + 0 + 0 + 0 + 0
        self.assertEqual((cold["score"], cold["suggested_rating"]), (5, "cold"))
        big = self.lead(company_name="Big Co", source="partner", estimated_value="2500000", email="b@big.in", phone="9123456780")
        self.assertEqual(big["score"], 20 + 30 + 5 + 5)  # 60 -> hot
        for i in range(9):  # nine completed meetings: 45 points would be earned, the cap is 30
            a = self.activity(type="meeting", subject=f"Meeting {i}", lead=big["id"], status="completed")
            self.assertEqual(a["status"], "completed")
        self.assertEqual(self.crm.get(f"{C}/leads/{big['id']}/").json()["score"], 60 + 30)

    def test_value_thresholds(self):
        for value, points in (("0", 0), ("1", 5), ("100000", 10), ("499999.99", 10), ("500000", 20), ("1000000", 30)):
            lead = self.lead(company_name=f"Value {value}", source="other", estimated_value=value)
            self.assertEqual(lead["score"], 3 + points, value)


class DuplicateTests(CrmBase):
    def test_email_phone_and_company_name_are_matched_in_any_spelling(self):
        first = self.lead(company_name="Acme Pvt Ltd", email="Info@Acme.in", phone="+91 98765 43210")
        for payload, matched in (
            ({"company_name": "Other", "email": "info@acme.in"}, "email"),
            ({"company_name": "Other", "phone": "098765-43210"}, "phone"),
            ({"company_name": "ACME Private Limited"}, "company"),
        ):
            r = self.crm.post(f"{C}/leads/", {"source": "website", **payload})
            self.assertEqual(r.status_code, 409, payload)
            body = r.json()
            self.assertEqual(body["code"], "duplicate")
            self.assertEqual(body["duplicates"][0]["id"], first["id"])
            self.assertIn(matched, body["duplicates"][0]["matched_on"])
        self.assertEqual(Lead.objects.count(), 1)

    def test_a_confirmed_duplicate_is_saved_and_unrelated_leads_are_not_flagged(self):
        self.lead(company_name="Acme", email="a@acme.in")
        self.lead(company_name="Different", email="d@other.in")
        again = self.lead(company_name="Acme Again", email="a@acme.in", expect=409)
        self.assertEqual(again["code"], "duplicate")
        r = self.crm.post(f"{C}/leads/", {"company_name": "Acme Again", "email": "a@acme.in", "allow_duplicates": True})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Lead.objects.count(), 3)

    def test_contacts_and_accounts_count_as_duplicates_and_the_check_endpoint_works(self):
        acct = self.account("Zen Labs", phone="080 4000 1234")
        self.contact(acct, "Mira Rao", email="mira@zen.in")
        r = self.crm.get(f"{C}/duplicates/", {"email": "MIRA@zen.in", "phone": "+91 80 4000 1234", "company": "Zen Labs Pvt. Ltd."})
        types = {(d["type"], tuple(sorted(d["matched_on"]))) for d in r.json()}
        self.assertEqual(types, {("contact", ("email",)), ("account", ("company", "phone"))})
        self.assertEqual(self.crm.get(f"{C}/duplicates/", {"email": "none@x.in"}).json(), [])


class ConversionTests(CrmBase):
    def test_convert_makes_contact_account_and_deal_but_no_customer(self):
        lead = self.lead(email="ravi@acme.in", phone="9876500000", estimated_value="250000", product_interest="Consulting", status="qualified")
        self.activity(type="call", subject="Intro", lead=lead["id"])
        customers = Customer.objects.count()
        r = self.crm.post(f"{C}/leads/{lead['id']}/convert/", {"expected_close_date": "2027-01-31"})
        self.assertEqual(r.status_code, 200, r.content)
        out = r.json()
        self.assertEqual(out["lead"]["status"], "converted")
        deal = self.crm.get(f"{C}/deals/{out['deal']['id']}/").json()
        self.assertEqual((deal["amount"], deal["stage"]["name"], deal["probability"], deal["status"]), ("250000.00", "Qualification", 10, "open"))
        self.assertEqual(deal["account"]["name"], "Acme Industries")
        self.assertEqual(deal["contact"]["name"], "Ravi Kumar")
        self.assertEqual(Customer.objects.count(), customers)  # a customer only appears when the deal is won
        self.assertIsNone(self.crm.get(f"{C}/accounts/{out['account']['id']}/").json()["customer_id"])
        # the lead's earlier activity now also shows on the deal and the account
        self.assertEqual(Activity.objects.filter(deal_id=out["deal"]["id"], account_id=out["account"]["id"]).count(), 1)
        self.assertRegex(deal["reference_code"], r"^T2N-DL-\d{2}-\d{4}-0001$")

    def test_converted_and_unqualified_leads_cannot_be_converted(self):
        lead = self.lead(email="x@acme.in")
        self.assertEqual(self.crm.post(f"{C}/leads/{lead['id']}/convert/", {}).status_code, 200)
        again = self.crm.post(f"{C}/leads/{lead['id']}/convert/", {})
        self.assertEqual(again.status_code, 400)
        self.assertIn("already been converted", again.json()["non_field_errors"][0])
        bad = self.lead(company_name="Nope Ltd", status="unqualified")
        r = self.crm.post(f"{C}/leads/{bad['id']}/convert/", {})
        self.assertEqual(r.status_code, 400)
        self.assertIn("status", r.json())
        # nor can a converted lead be edited back to an earlier status, or deleted
        self.assertEqual(self.crm.patch(f"{C}/leads/{lead['id']}/", {"status": "new"}).status_code, 400)
        self.assertEqual(self.crm.delete(f"{C}/leads/{lead['id']}/").status_code, 400)

    def test_status_cannot_be_set_to_converted_by_hand(self):
        lead = self.lead()
        r = self.crm.patch(f"{C}/leads/{lead['id']}/", {"status": "converted"})
        self.assertEqual(r.status_code, 400)

    def test_an_existing_account_with_the_same_name_is_reused_and_a_deal_is_optional(self):
        acct = self.account("Acme Industries")
        lead = self.lead(company_name="ACME Industries Pvt Ltd", email="n@acme.in", allow_duplicates=True)
        out = self.crm.post(f"{C}/leads/{lead['id']}/convert/", {"create_deal": False}).json()
        self.assertEqual(out["account"]["id"], acct["id"])
        self.assertIsNone(out["deal"])
        self.assertEqual(Account.objects.count(), 1)
        self.assertEqual(Deal.objects.count(), 0)
        self.assertEqual(Contact.objects.get().account_id.hex, acct["id"].replace("-", ""))

    def test_a_lead_without_a_company_becomes_an_account_named_after_the_person(self):
        lead = self.lead(company_name="", contact_name="Sita Menon", email="sita@x.in")
        out = self.crm.post(f"{C}/leads/{lead['id']}/convert/", {}).json()
        self.assertEqual(out["account"]["name"], "Sita Menon")

    def test_conversion_is_audited(self):
        lead = self.lead()
        self.crm.post(f"{C}/leads/{lead['id']}/convert/", {})
        ev = Event.objects.filter(entity_type="lead", entity_id=lead["id"], reason="Converted").get()
        self.assertEqual(ev.new_values["status"], "converted")
        self.assertTrue(Event.objects.filter(entity_type="crm_account", action="created").exists())
        self.assertTrue(Event.objects.filter(entity_type="deal", action="created").exists())


class CsvImportTests(CrmBase):
    CSV = (
        "Company,Contact Person,Email,Phone,Source,Estimated Value,Status,Rating\n"
        "Alpha Works,Anil,anil@alpha.in,9811111111,Referral,150000,new,hot\n"           # row 2: fine
        "Beta Labs,Bina,not-an-email,9822222222,website,,new,\n"                        # row 3: bad email
        "Gamma Ltd,Gita,gita@gamma.in,9833333333,Billboard,,new,\n"                     # row 4: unknown source
        "Delta Co,Dev,dev@delta.in,9844444444,event,12x,new,\n"                         # row 5: bad value
        ",,nobody@x.in,,website,,,\n"                                                   # row 6: no company or contact
        "Existing Co,Esha,esha@exist.in,9855555555,social,,contacted,warm\n"            # row 7: duplicate of existing lead
        "Alpha Works Pvt Ltd,Anil B,other@alpha.in,,partner,,,\n"                       # row 8: same company as row 2
        "Eta Corp,Eli,eli@eta.in,,cold call,200000,qualified,cold\n"                    # row 9: fine (source by label)
    )

    def setUp(self):
        super().setUp()
        self.lead(company_name="Existing Co", email="esha@exist.in")

    def test_preview_reports_every_row_and_creates_nothing(self):
        r = self.crm.post(f"{C}/leads/import/", {"csv": self.CSV, "commit": False})
        self.assertEqual(r.status_code, 200, r.content)
        out = r.json()
        by_row = {x["row"]: x for x in out["rows"]}
        self.assertEqual({k: v["status"] for k, v in by_row.items()},
                         {2: "ok", 3: "error", 4: "error", 5: "error", 6: "error", 7: "duplicate", 8: "duplicate", 9: "ok"})
        self.assertIn("email", by_row[3]["errors"])
        self.assertIn("source", by_row[4]["errors"])
        self.assertIn("estimated_value", by_row[5]["errors"])
        self.assertIn("company_name", by_row[6]["errors"])
        self.assertTrue(any("row 2" in d for d in by_row[8]["duplicates"]))
        self.assertEqual((out["total"], out["errors"], out["duplicates"], out["valid"], out["created"]), (8, 4, 2, 2, 0))
        self.assertEqual(Lead.objects.count(), 1)

    def test_commit_loads_only_the_good_rows_with_scores_and_references(self):
        out = self.crm.post(f"{C}/leads/import/", {"csv": self.CSV, "commit": True}).json()
        self.assertEqual(out["created"], 2)
        alpha = Lead.objects.get(company_name="Alpha Works")
        self.assertEqual((alpha.source, alpha.rating, alpha.status), ("referral", "hot", "new"))
        self.assertEqual(alpha.score, 25 + 10 + 5 + 5)  # referral, 1.5 lakh, email, phone
        eta = Lead.objects.get(company_name="Eta Corp")
        self.assertEqual((eta.source, eta.status), ("cold_call", "qualified"))
        self.assertRegex(eta.reference_code, r"^T2N-LD-\d{2}-\d{4}-000\d$")
        self.assertEqual(Lead.objects.count(), 3)
        self.assertTrue(Event.objects.filter(entity_type="lead", reason="CSV import").count() == 2)

    def test_duplicates_can_be_imported_on_request(self):
        out = self.crm.post(f"{C}/leads/import/", {"csv": self.CSV, "commit": True, "import_duplicates": True}).json()
        self.assertEqual(out["created"], 4)

    def test_file_level_problems(self):
        for text, field in (("", "csv"), ("foo,bar\n1,2\n", "csv"), ("email,phone\na@b.in,98\n", "csv")):
            r = self.crm.post(f"{C}/leads/import/", {"csv": text})
            self.assertEqual(r.status_code, 400, text)
            self.assertIn(field, r.json())
        self.assertEqual(self.crm.post(f"{C}/leads/import/", {}).status_code, 400)

    def test_semicolon_files_and_heading_aliases_work(self):
        text = "Organisation;Name;E-mail;Value\nOmega Pvt;Om;om@omega.in;50000\n"
        out = self.crm.post(f"{C}/leads/import/", {"csv": text, "commit": True}).json()
        self.assertEqual(out["created"], 1)
        self.assertEqual(Lead.objects.get(company_name="Omega Pvt").estimated_value, 50000)


class LeadCrudTests(CrmBase):
    def test_validation_messages(self):
        r = self.crm.post(f"{C}/leads/", {"source": "website"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("company_name", r.json())
        for bad, field in (({"email": "nope"}, "email"), ({"phone": "12"}, "phone"), ({"estimated_value": "-5"}, "estimated_value"),
                           ({"estimated_value": "abc"}, "estimated_value"), ({"status": "weird"}, "status"),
                           ({"rating": "lukewarm"}, "rating"), ({"source": "billboard"}, "source"), ({"owner": "nobody"}, "owner")):
            r = self.crm.post(f"{C}/leads/", {"company_name": "V Ltd", **bad})
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json(), bad)

    def test_list_filters_tabs_and_pagination(self):
        self.lead(company_name="A One", status="new", source="website", estimated_value="1000")
        self.lead(company_name="B Two", status="qualified", source="referral", rating="hot", estimated_value="3000")
        self.lead(company_name="C Three", status="qualified", source="referral", estimated_value="2000")
        out = self.crm.get(f"{C}/leads/", {"status": "qualified"}).json()
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["status_counts"], {"new": 1, "qualified": 2})
        self.assertEqual(out["total_value"], "5000.00")
        self.assertEqual(self.crm.get(f"{C}/leads/", {"rating": "hot"}).json()["count"], 1)
        self.assertEqual(self.crm.get(f"{C}/leads/", {"q": "three"}).json()["results"][0]["company_name"], "C Three")
        page = self.crm.get(f"{C}/leads/", {"page_size": 2, "page": 2, "sort": "estimated_value"}).json()
        self.assertEqual((page["count"], len(page["results"]), page["results"][0]["company_name"]), (3, 1, "B Two"))
        self.assertEqual(self.crm.get(f"{C}/leads/", {"owner": "me"}).json()["count"], 3)
        self.assertEqual(self.crm.get(f"{C}/leads/", {"owner": "none"}).json()["count"], 0)
        self.assertEqual(self.crm.get(f"{C}/leads/", {"page": "x"}).status_code, 400)

    def test_bulk_assign_and_status_and_delete(self):
        a, b, c = (self.lead(company_name=n) for n in ("L1", "L2", "L3"))
        ids = [a["id"], b["id"]]
        r = self.crm.post(f"{C}/leads/bulk/", {"ids": ids, "action": "assign", "owner": str(self.pm.id)})
        self.assertEqual(r.json(), {"done": 2, "skipped_converted": 0})
        self.assertEqual(Lead.objects.filter(owner=self.pm).count(), 2)
        self.assertEqual(self.crm.post(f"{C}/leads/bulk/", {"ids": ids, "action": "status", "status": "contacted"}).json()["done"], 2)
        self.assertEqual(Lead.objects.filter(status="contacted").count(), 2)
        self.crm.post(f"{C}/leads/{c['id']}/convert/", {})
        r = self.crm.post(f"{C}/leads/bulk/", {"ids": [c["id"], a["id"]], "action": "delete"})
        self.assertEqual(r.json(), {"done": 1, "skipped_converted": 1})
        self.assertEqual(Lead.objects.count(), 2)
        self.assertEqual(self.crm.post(f"{C}/leads/bulk/", {"ids": ids, "action": "status", "status": "converted"}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/leads/bulk/", {"ids": [], "action": "delete"}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/leads/bulk/", {"ids": ids, "action": "assign"}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/leads/bulk/", {"ids": ["00000000-0000-0000-0000-000000000000"], "action": "delete"}).status_code, 400)

    def test_timeline_lists_activities_notes_and_audit_newest_first(self):
        lead = self.lead()
        self.crm.patch(f"{C}/leads/{lead['id']}/", {"status": "contacted"})
        self.activity(type="note", subject="Prefers evening calls", lead=lead["id"])
        rows = self.crm.get(f"{C}/leads/{lead['id']}/timeline/").json()
        kinds = [r["kind"] for r in rows]
        self.assertEqual(kinds[0], "activity")
        self.assertEqual(rows[0]["type"], "note")
        self.assertEqual([r["title"] for r in rows if r["kind"] == "audit"], ["Updated", "Created"])

    def test_lead_audit_hides_email_and_phone(self):
        lead = self.lead(email="secret@acme.in", phone="9876543210")
        self.crm.patch(f"{C}/leads/{lead['id']}/", {"email": "new@acme.in", "status": "contacted"})
        events = list(Event.objects.filter(entity_type="lead", entity_id=lead["id"]))
        self.assertEqual({e.action for e in events}, {"created", "updated"})
        blob = " ".join(str(e.new_values) + str(e.previous_values) for e in events)
        self.assertNotIn("secret@acme.in", blob)
        self.assertNotIn("new@acme.in", blob)
        self.assertNotIn("9876543210", blob)

    def test_delete_lead_audited_and_activity_removed(self):
        lead = self.lead()
        a = self.activity(lead=lead["id"])
        self.assertEqual(self.crm.delete(f"{C}/leads/{lead['id']}/").status_code, 204)
        self.assertFalse(Activity.objects.filter(pk=a["id"]).exists())
        self.assertTrue(Event.objects.filter(entity_type="lead", action="deleted").exists())


class ActivityDueLogicTests(CrmBase):
    """Fixed dates: 'today' is 12 November 2026."""

    def test_buckets(self):
        today = datetime.date(2026, 11, 12)
        mk = lambda **kw: Activity(status=kw.pop("status", "open"), **kw)  # noqa: E731
        self.assertEqual(activity_service.bucket(mk(due_date=datetime.date(2026, 11, 11)), today), "overdue")
        self.assertEqual(activity_service.bucket(mk(due_date=today), today), "today")
        self.assertEqual(activity_service.bucket(mk(due_date=datetime.date(2026, 11, 13)), today), "upcoming")
        self.assertEqual(activity_service.bucket(mk(due_date=None), today), "no_date")
        self.assertEqual(activity_service.bucket(mk(due_date=datetime.date(2026, 1, 1), status="completed"), today), "completed")

    def test_views_filter_by_the_given_date(self):
        lead = self.lead()
        days = {"old": datetime.date(2026, 11, 1), "yesterday": datetime.date(2026, 11, 11), "now": datetime.date(2026, 11, 12),
                "soon": datetime.date(2026, 11, 20)}
        for name, d in days.items():
            self.activity(subject=name, lead=lead["id"], due_date=d.isoformat())
        today = days["now"]
        qs = Activity.objects.all()
        names = lambda view: sorted(activity_service.filter_view(qs, view, today).values_list("subject", flat=True))  # noqa: E731
        self.assertEqual(names("overdue"), ["old", "yesterday"])
        self.assertEqual(names("today"), ["now"])
        self.assertEqual(names("upcoming"), ["soon"])
        self.assertEqual(names("open"), ["now", "old", "soon", "yesterday"])

    def test_api_counts_use_the_real_today_and_completion_moves_an_item_out_of_overdue(self):
        lead = self.lead()
        real = datetime.date.today()
        a = self.activity(subject="Late", lead=lead["id"], due_date=(real - datetime.timedelta(days=3)).isoformat())
        self.activity(subject="Now", lead=lead["id"], due_date=real.isoformat(), due_time="15:30")
        self.activity(subject="Later", lead=lead["id"], due_date=(real + datetime.timedelta(days=2)).isoformat())
        out = self.crm.get(f"{C}/activities/").json()
        self.assertEqual(out["counts"], {"overdue": 1, "today": 1, "upcoming": 1, "open": 3, "completed": 0})
        self.assertEqual([x["subject"] for x in out["results"]], ["Late", "Now", "Later"])  # soonest due first
        self.assertEqual({x["subject"]: x["bucket"] for x in out["results"]}, {"Late": "overdue", "Now": "today", "Later": "upcoming"})
        self.assertEqual(self.crm.get(f"{C}/activities/", {"view": "overdue"}).json()["count"], 1)
        done = self.crm.post(f"{C}/activities/{a['id']}/complete/", {"outcome": "Sent the quote"}).json()
        self.assertEqual((done["status"], done["outcome"], done["bucket"]), ("completed", "Sent the quote", "completed"))
        self.assertEqual(self.crm.get(f"{C}/activities/").json()["counts"]["overdue"], 0)
        self.assertEqual(self.crm.post(f"{C}/activities/{a['id']}/complete/", {}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/activities/{a['id']}/reopen/", {}).json()["bucket"], "overdue")

    def test_validation_and_my_activities(self):
        lead = self.lead()
        r = self.crm.post(f"{C}/activities/", {"type": "task", "subject": "Orphan"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("related_to", r.json())
        r = self.crm.post(f"{C}/activities/", {"type": "task", "subject": "", "lead": lead["id"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("subject", r.json())
        self.assertEqual(self.crm.post(f"{C}/activities/", {"type": "task", "subject": "x", "lead": lead["id"], "due_time": "10:00"}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/activities/", {"type": "fax", "subject": "x", "lead": lead["id"]}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/activities/", {"type": "task", "subject": "x", "lead": "bad"}).status_code, 400)
        mine = self.activity(subject="Mine", lead=lead["id"])
        other = self.crm.post(f"{C}/activities/", {"type": "task", "subject": "Theirs", "lead": lead["id"], "owner": str(self.mp.id)}).json()
        self.assertEqual(mine["owner"]["id"], str(self.partner.id))
        self.assertEqual([x["subject"] for x in self.crm.get(f"{C}/activities/", {"owner": "me"}).json()["results"]], ["Mine"])
        self.assertEqual(other["owner"]["id"], str(self.mp.id))
        self.assertEqual(self.crm.patch(f"{C}/activities/{mine['id']}/", {"subject": "Renamed", "priority": "high"}).json()["priority"], "high")
        self.assertEqual(self.crm.delete(f"{C}/activities/{mine['id']}/").status_code, 204)
