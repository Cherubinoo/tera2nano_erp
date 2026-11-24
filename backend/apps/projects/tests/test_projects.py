import datetime

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Role
from apps.attachments import registry
from apps.company.models import CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user, pdf_stub
from apps.projects.models import Project

from .base import D, P, S, TODAY, ProjectsBase, iso


class LookupTests(ProjectsBase):
    def test_lookup_shape_and_open_to_every_signed_in_user(self):
        a = self.project(name="Alpha")
        b = self.project(name="Beta", status="planned", customer=None, project_type="internal")
        for who in (self.api, self.api_staff, self.api_finance, self.pm, self.hr, self.partner):
            r = who.get(f"{P}/lookup/")
            self.assertEqual(r.status_code, 200)
            self.assertEqual([x["name"] for x in r.json()], ["Alpha", "Beta"])
        row = self.api_staff.get(f"{P}/lookup/").json()[0]
        self.assertEqual(set(row) >= {"id", "code", "name", "status", "customer"}, True)
        self.assertEqual(row, {"id": a["id"], "code": a["code"], "name": "Alpha", "status": "active",
                               "customer": self.acme["id"], "customer_name": "Acme Industries"})
        self.assertIsNone(self.api.get(f"{P}/lookup/").json()[1]["customer"])
        self.assertEqual(b["customer"], None)

    def test_lookup_needs_sign_in_and_hides_cancelled(self):
        self.assertIn(self.anon.get(f"{P}/lookup/").status_code, (401, 403))
        a = self.project()
        r = self.pm.post(f"{P}/projects/{a['id']}/status/", {"status": "cancelled", "reason": "Client withdrew"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.pm.get(f"{P}/lookup/").json(), [])


class ProjectCrudTests(ProjectsBase):
    def test_create_allocates_code_with_year_and_date_and_audits(self):
        a = self.project()
        self.assertRegex(a["code"], r"^T2N-PRJ-\d{2}-\d{4}-0001$")
        self.assertRegex(self.project(name="Two")["code"], r"-0002$")
        self.assertEqual((a["customer"]["name"], a["status"], a["billing_method"]), ("Acme Industries", "active", "fixed"))
        ev = CompanyProfileAuditEvent.objects.get(entity_type="project", entity_id=a["id"], action="created")
        self.assertEqual(ev.category, "projects")

    def test_validation_messages(self):
        for bad, field in (({"name": ""}, "name"), ({"planned_end_date": "2026-07-01"}, "planned_end_date"),
                           ({"project_type": "weird"}, "project_type"), ({"contract_value": "-5"}, "contract_value"),
                           ({"budget": "abc"}, "budget"), ({"start_date": "01/08/2026"}, "start_date"),
                           ({"customer": None}, "customer"), ({"customer": "not-a-uuid"}, "customer"),
                           ({"status": "completed"}, "status"), ({"contract_value": "1.234"}, "contract_value"),
                           ({"tags": "a" * 50 + ",b"}, "tags"),
                           ({"budget_lines": [{"category": "labour", "amount": "70000"}]}, "budget_lines"),
                           ({"budget_lines": [{"category": "labour", "amount": "1"}, {"category": "labour", "amount": "2"}]}, "budget_lines"),
                           ({"budget_lines": [{"category": "bogus", "amount": "1"}]}, "budget_lines")):
            r = self.pm.post(f"{P}/projects/", {"name": "X", "customer": self.acme["id"], "start_date": "2026-08-01",
                                                "budget": "60000", **bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json(), bad)

    def test_internal_project_needs_no_client_and_unknown_client_is_refused(self):
        p = self.project(customer=None, project_type="internal", contract_value="0", name="R&D lab")
        self.assertIsNone(p["customer"])
        r = self.pm.post(f"{P}/projects/", {"name": "X", "customer": "00000000-0000-0000-0000-000000000000"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["customer"], "Client not found.")

    def test_budget_lines_tags_and_update(self):
        p = self.project(tags="nano, rd ,NANO", budget_lines=[{"category": "labour", "amount": "30000"}, {"category": "travel", "amount": "5000"},
                                                              {"category": "other", "amount": "0"}])
        self.assertEqual(p["tags"], ["nano", "rd"])
        self.assertEqual([(l["category"], l["amount"]) for l in p["budget_lines"]], [("labour", "30000.00"), ("travel", "5000.00")])
        r = self.pm.patch(f"{P}/projects/{p['id']}/", {"name": "Alpha 2", "priority": "high", "tags": [],
                                                       "budget_lines": [{"category": "materials", "amount": "100"}]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["name"], body["priority"], body["tags"]), ("Alpha 2", "high", []))
        self.assertEqual([l["category"] for l in body["budget_lines"]], ["materials"])
        ev = CompanyProfileAuditEvent.objects.filter(entity_type="project", entity_id=p["id"], action="updated").first()
        self.assertIn("name", ev.changed_fields)
        self.assertEqual(ev.previous_values["name"], "Alpha")

    def test_patch_cannot_make_dates_cross(self):
        p = self.project()
        r = self.pm.patch(f"{P}/projects/{p['id']}/", {"start_date": "2027-02-01"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("planned_end_date", r.json())

    def test_list_filters_search_and_totals(self):
        self.project(name="Alpha", contract_value="1000", budget="0")
        other = self.customer("Beta Corp")
        self.project(name="Gamma", customer=other["id"], status="planned", contract_value="500", budget="0", priority="urgent")
        r = self.pm.get(f"{P}/projects/").json()
        self.assertEqual((r["count"], r["totals"]["contract_value"]), (2, "1500.00"))
        self.assertEqual(self.pm.get(f"{P}/projects/", {"status": "planned"}).json()["count"], 1)
        self.assertEqual(self.pm.get(f"{P}/projects/", {"status": "open"}).json()["count"], 2)
        self.assertEqual(self.pm.get(f"{P}/projects/", {"q": "beta"}).json()["results"][0]["name"], "Gamma")
        self.assertEqual(self.pm.get(f"{P}/projects/", {"customer": other["id"]}).json()["count"], 1)
        self.assertEqual(self.pm.get(f"{P}/projects/", {"priority": "urgent"}).json()["count"], 1)
        self.assertEqual(self.pm.get(f"{P}/projects/", {"health": "delayed"}).json()["count"], 2)
        self.assertEqual(self.pm.get(f"{P}/projects/", {"health": "on_track"}).json()["count"], 0)

    def test_unknown_ids_are_404_never_500(self):
        ghost = "00000000-0000-0000-0000-000000000000"
        for method, url in (("get", f"/projects/{ghost}/"), ("patch", f"/projects/{ghost}/"), ("delete", f"/projects/{ghost}/"),
                            ("get", f"/projects/{ghost}/financials/"), ("get", f"/projects/{ghost}/budget/"),
                            ("get", f"/projects/{ghost}/invoices/"), ("get", f"/projects/{ghost}/expenses/"),
                            ("get", f"/projects/{ghost}/activity/"), ("post", f"/projects/{ghost}/status/"),
                            ("patch", f"/milestones/{ghost}/"), ("delete", f"/milestones/{ghost}/"), ("post", f"/milestones/{ghost}/create-invoice/"),
                            ("patch", f"/tasks/{ghost}/"), ("get", f"/tasks/{ghost}/"), ("delete", f"/tasks/{ghost}/"),
                            ("patch", f"/team/{ghost}/"), ("delete", f"/team/{ghost}/"),
                            ("patch", f"/timesheets/{ghost}/"), ("delete", f"/timesheets/{ghost}/")):
            r = getattr(self.api, method)(f"{P}{url}", {} if method in ("post", "patch") else None, format="json")
            self.assertEqual(r.status_code, 404, (method, url, r.content))
        # an unknown or malformed project in a body is a field error
        for body in ({"project": ghost}, {"project": "nope"}, {}):
            self.assertEqual(self.api.post(f"{P}/tasks/", {"title": "x", **body}, format="json").status_code, 400)

    def test_delete_rules(self):
        free = self.project(name="Free")
        self.assertEqual(self.pm.delete(f"{P}/projects/{free['id']}/").status_code, 204)
        self.assertEqual(self.pm.get(f"{P}/projects/{free['id']}/").status_code, 404)
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="project", entity_id=free["id"], action="deleted").exists())
        busy = self.project(name="Busy")
        self.entry(busy)
        r = self.pm.delete(f"{P}/projects/{busy['id']}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("time has been logged", str(r.json()))
        booked = self.project(name="Booked")
        self.post_books(booked, revenue="100")
        self.assertIn("books", str(self.pm.delete(f"{P}/projects/{booked['id']}/").json()))

    def test_status_changes(self):
        p = self.project()
        r = self.pm.post(f"{P}/projects/{p['id']}/status/", {"status": "cancelled"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("reason", r.json())
        r = self.pm.post(f"{P}/projects/{p['id']}/status/", {"status": "completed", "actual_end_date": "2026-12-20"}, format="json").json()
        self.assertEqual((r["status"], r["actual_end_date"], r["progress"]), ("completed", "2026-12-20", 100))
        self.assertEqual(self.pm.post(f"{P}/projects/{p['id']}/status/", {"status": "bogus"}, format="json").status_code, 400)
        r = self.pm.post(f"{P}/projects/{p['id']}/status/", {"status": "active"}, format="json").json()
        self.assertEqual((r["status"], r["actual_end_date"]), ("active", None))
        early = self.pm.post(f"{P}/projects/{p['id']}/status/", {"status": "completed", "actual_end_date": "2026-07-01"}, format="json")
        self.assertEqual(early.status_code, 400)
        events = CompanyProfileAuditEvent.objects.filter(entity_type="project", entity_id=p["id"], action="updated")
        self.assertEqual(events.count(), 2)
        self.assertIn("Status changed from Active to Completed", events.last().reason)


class PermissionTests(ProjectsBase):
    def test_every_role(self):
        p = self.project()
        m = self.milestone(p)
        t = self.task(p)
        can_view = {"admin": self.api, "managing": self.managing, "pm": self.pm, "finance": self.api_finance, "hr": self.hr,
                    "partner": self.partner}
        for name, c in can_view.items():
            for url in ("/projects/", f"/projects/{p['id']}/", f"/projects/{p['id']}/financials/", f"/projects/{p['id']}/budget/",
                        f"/projects/{p['id']}/invoices/", f"/projects/{p['id']}/expenses/", f"/projects/{p['id']}/activity/",
                        "/milestones/", "/tasks/", "/team/", "/timesheets/", "/dashboard/", "/portfolio/", "/options/"):
                self.assertEqual(c.get(f"{P}{url}").status_code, 200, (name, url))
        for url in ("/projects/", "/milestones/", "/tasks/", "/team/", "/timesheets/", "/dashboard/", "/portfolio/", "/options/",
                    f"/projects/{p['id']}/", f"/projects/{p['id']}/financials/", f"/tasks/{t['id']}/"):
            self.assertEqual(self.api_staff.get(f"{P}{url}").status_code, 403, url)
            self.assertIn(self.anon.get(f"{P}{url}").status_code, (401, 403), url)
        writes = [("post", "/projects/", {"name": "Z", "project_type": "internal"}),
                  ("patch", f"/projects/{p['id']}/", {"name": "Z"}), ("delete", f"/projects/{p['id']}/", None),
                  ("post", f"/projects/{p['id']}/status/", {"status": "on_hold"}),
                  ("post", "/milestones/", {"project": p["id"], "name": "M", "due_date": "2099-01-01"}),
                  ("patch", f"/milestones/{m['id']}/", {"name": "M2"}), ("delete", f"/milestones/{m['id']}/", None),
                  ("post", f"/milestones/{m['id']}/create-invoice/", {}),
                  ("post", "/tasks/", {"project": p["id"], "title": "T"}), ("patch", f"/tasks/{t['id']}/", {"title": "T2"}),
                  ("delete", f"/tasks/{t['id']}/", None), ("post", "/team/", {"project": p["id"], "name": "N"}),
                  ("post", "/timesheets/", {"project": p["id"], "date": "2026-08-10", "hours": "1"})]
        for name, c in (("finance", self.api_finance), ("hr", self.hr), ("staff", self.api_staff)):
            for method, url, body in writes:
                self.assertEqual(getattr(c, method)(f"{P}{url}", body, format="json").status_code, 403, (name, method, url))
        # still intact after all the refused writes
        self.assertEqual(self.get(p)["name"], "Alpha")
        for name, c in (("admin", self.api), ("managing", self.managing), ("partner", self.partner), ("pm", self.pm)):  # a partner holds projects.manage
            r = c.post(f"{P}/projects/", {"name": f"By {name}", "project_type": "internal"}, format="json")
            self.assertEqual(r.status_code, 201, (name, r.content))

    def test_roles_follow_the_saved_matrix(self):
        self.assertTrue(self.pm_user.has_code("projects.manage"))
        self.assertTrue(self.hr_user.has_code("projects.view") and not self.hr_user.has_code("projects.manage"))
        self.assertFalse(self.staff.has_code("projects.view"))


class MilestoneTests(ProjectsBase):
    def test_status_is_computed_and_completion_date_follows(self):
        p = self.project(start_date=iso(TODAY - datetime.timedelta(days=30)), planned_end_date=iso(TODAY + datetime.timedelta(days=60)))
        late = self.milestone(p, "Late", due=iso(TODAY - datetime.timedelta(days=5)))
        ahead = self.milestone(p, "Ahead", due=iso(TODAY + datetime.timedelta(days=5)))
        self.assertEqual((late["status"], late["status_key"], late["days_late"]), ("pending", "overdue", 5))
        self.assertEqual(ahead["status_key"], "pending")
        done = self.pm.patch(f"{P}/milestones/{late['id']}/", {"status": "done"}, format="json").json()
        self.assertEqual((done["status_key"], done["completed_on"]), ("done", iso(TODAY)))
        back = self.pm.patch(f"{P}/milestones/{late['id']}/", {"status": "in_progress"}, format="json").json()
        self.assertEqual((back["status_key"], back["completed_on"]), ("overdue", None))
        lst = self.pm.get(f"{P}/milestones/", {"status": "overdue"}).json()
        self.assertEqual((lst["count"], lst["overdue_count"]), (1, 1))
        self.assertEqual(self.pm.get(f"{P}/milestones/", {"status": "pending"}).json()["count"], 1)
        self.assertEqual(self.get(p)["overdue_milestones"], 1)

    def test_validation(self):
        p = self.project()
        for bad, field in (({"name": ""}, "name"), ({"due_date": "soon"}, "due_date"), ({"due_date": "2026-01-01"}, "due_date"),
                           ({"amount": "-1"}, "amount"), ({"status": "weird"}, "status"),
                           ({"status": "done", "completed_on": "2999-01-01"}, "completed_on")):
            body = {"project": p["id"], "name": "M", "due_date": "2026-09-01", **bad}
            r = self.pm.post(f"{P}/milestones/", body, format="json")
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json(), bad)

    def test_create_invoice_from_milestone_makes_a_tagged_draft_once(self):
        p = self.project()
        m = self.milestone(p, "Phase 1", amount="25000.00")
        self.assertEqual(self.pm.post(f"{P}/milestones/{m['id']}/create-invoice/").status_code, 403)  # PM cannot manage sales
        r = self.api.post(f"{P}/milestones/{m['id']}/create-invoice/")
        self.assertEqual(r.status_code, 201, r.content)
        inv = r.json()["invoice"]
        doc = self.api_finance.get(f"{S}/documents/{inv['id']}/").json()
        self.assertEqual((doc["kind"], doc["status"], doc["grand_total"], doc["project"]), ("invoice", "draft", "25000.00", p["id"]))
        self.assertEqual(doc["customer"]["id"], self.acme["id"])
        self.assertEqual(r.json()["milestone"]["invoice"]["id"], inv["id"])
        again = self.api.post(f"{P}/milestones/{m['id']}/create-invoice/")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(self.api.patch(f"{P}/milestones/{m['id']}/", {"amount": "1"}, format="json").status_code, 400)
        self.assertEqual(self.api.delete(f"{P}/milestones/{m['id']}/").status_code, 400)
        # once the draft is gone the milestone can be billed again
        self.assertEqual(self.api_finance.delete(f"{S}/documents/{inv['id']}/").status_code, 204)
        self.assertEqual(self.api.post(f"{P}/milestones/{m['id']}/create-invoice/").status_code, 201)

    def test_create_invoice_needs_client_and_amount(self):
        internal = self.project(customer=None, project_type="internal", name="Lab")
        m = self.milestone(internal, "X", amount="100")
        self.assertEqual(self.api.post(f"{P}/milestones/{m['id']}/create-invoice/").status_code, 400)
        p = self.project()
        free = self.milestone(p, "Free")
        r = self.api.post(f"{P}/milestones/{free['id']}/create-invoice/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("amount", r.json())


class TaskTests(ProjectsBase):
    def test_task_code_status_and_logged_hours(self):
        p = self.project()
        m = self.milestone(p)
        t = self.task(p, "Wire up", milestone=m["id"], estimated_hours="8", assignee_name="Ravi", due_date="2026-09-01")
        self.assertRegex(t["code"], r"^T2N-TSK-\d{2}-\d{4}-0001$")
        self.assertEqual((t["status"], t["logged_hours"], t["milestone"]["name"]), ("todo", "0.00", "Design"))
        self.entry(p, "2.5", task=t["id"])
        self.entry(p, "1.5", task=t["id"])
        moved = self.pm.patch(f"{P}/tasks/{t['id']}/", {"status": "review"}, format="json").json()
        self.assertEqual((moved["status"], moved["logged_hours"], moved["completed_on"]), ("review", "4.00", None))
        done = self.pm.patch(f"{P}/tasks/{t['id']}/", {"status": "done"}, format="json").json()
        self.assertEqual(done["completed_on"], iso(TODAY))
        reopened = self.pm.patch(f"{P}/tasks/{t['id']}/", {"status": "in_progress"}, format="json").json()
        self.assertIsNone(reopened["completed_on"])
        listing = self.pm.get(f"{P}/tasks/", {"project": p["id"]}).json()
        self.assertEqual((listing["count"], listing["estimated_hours"], listing["logged_hours"]), (1, "8.00", "4.00"))
        self.assertEqual(self.pm.delete(f"{P}/tasks/{t['id']}/").status_code, 204)
        # hours logged stay on the project when the task goes
        self.assertEqual(self.pm.get(f"{P}/timesheets/", {"project": p["id"]}).json()["hours"], "4.00")

    def test_filters_and_validation(self):
        p, q = self.project(name="A"), self.project(name="B")
        self.task(p, "Past", due_date="2020-01-01")
        self.task(p, "Hot", priority="urgent", assignee_name="Meera")
        done = self.task(q, "Shipped")
        self.pm.patch(f"{P}/tasks/{done['id']}/", {"status": "done"}, format="json")
        self.assertEqual(self.pm.get(f"{P}/tasks/", {"status": "overdue"}).json()["count"], 1)
        self.assertEqual(self.pm.get(f"{P}/tasks/", {"status": "open"}).json()["count"], 2)
        self.assertEqual(self.pm.get(f"{P}/tasks/", {"project": q["id"]}).json()["count"], 1)
        self.assertEqual(self.pm.get(f"{P}/tasks/", {"assignee": "meer"}).json()["count"], 1)
        self.assertEqual(self.pm.get(f"{P}/tasks/", {"priority": "urgent"}).json()["count"], 1)
        for bad, field in (({"title": ""}, "title"), ({"estimated_hours": "-2"}, "estimated_hours"), ({"status": "x"}, "status"),
                           ({"priority": "x"}, "priority"), ({"due_date": "tomorrow"}, "due_date"), ({"assignee_id": "zzz"}, "assignee_id")):
            r = self.pm.post(f"{P}/tasks/", {"project": p["id"], "title": "T", **bad}, format="json")
            self.assertEqual((r.status_code, field in r.json()), (400, True), bad)
        other_milestone = self.milestone(q)
        r = self.pm.post(f"{P}/tasks/", {"project": p["id"], "title": "T", "milestone": other_milestone["id"]}, format="json")
        self.assertIn("milestone", r.json())


class TeamTests(ProjectsBase):
    def test_team_members_rates_and_duplicates(self):
        p = self.project()
        emp = "11111111-1111-1111-1111-111111111111"
        r = self.pm.post(f"{P}/team/", {"project": p["id"], "name": "Asha", "employee_id": emp, "role": "Developer",
                                         "allocation_percent": 50, "hourly_rate": "1200"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        m = r.json()
        self.assertEqual((m["role"], m["allocation_percent"], m["hourly_rate"]), ("Developer", 50, "1200.00"))
        self.assertIn("employee_id", self.pm.post(f"{P}/team/", {"project": p["id"], "name": "Other", "employee_id": emp}, format="json").json())
        self.assertIn("name", self.pm.post(f"{P}/team/", {"project": p["id"], "name": "asha"}, format="json").json())
        self.assertIn("allocation_percent", self.pm.post(f"{P}/team/", {"project": p["id"], "name": "B", "allocation_percent": 150}, format="json").json())
        self.entry(p, "3", employee_id=emp, employee_name="Asha")
        row = self.pm.get(f"{P}/team/", {"project": p["id"]}).json()["results"][0]
        self.assertEqual(row["logged_hours"], "3.00")
        self.assertEqual(self.pm.patch(f"{P}/team/{m['id']}/", {"is_active": False}, format="json").json()["is_active"], False)
        self.assertEqual(self.pm.delete(f"{P}/team/{m['id']}/").status_code, 204)


class AuditAndAttachmentTests(ProjectsBase):
    def test_activity_lists_every_change_in_words(self):
        p = self.project()
        m = self.milestone(p, "Kickoff")
        t = self.task(p, "Plan")
        self.entry(p, "2")
        self.pm.patch(f"{P}/milestones/{m['id']}/", {"status": "done"}, format="json")
        self.pm.patch(f"{P}/tasks/{t['id']}/", {"status": "done"}, format="json")
        self.pm.post(f"{P}/team/", {"project": p["id"], "name": "Asha"}, format="json")
        rows = self.pm.get(f"{P}/projects/{p['id']}/activity/").json()
        texts = " | ".join(r["text"] for r in rows)
        for needle in ("created", "Milestone added: Kickoff", "Milestone changed: Kickoff (Done", "Task added", "Task changed",
                       "Time logged", "Team member added: Asha"):
            self.assertIn(needle, texts)
        self.assertTrue(all(r["actor"] == "pm" for r in rows))
        self.assertEqual(rows[0]["action"], "updated")

    def test_project_is_an_attachment_target_guarded_by_the_module_permissions(self):
        self.assertEqual(registry.target("project")["view"], "projects.view")
        self.assertEqual(registry.target("project")["manage"], "projects.manage")
        p = self.project()

        def upload(client):
            return client.post("/api/v1/attachments/", {"entity_type": "project", "entity_id": p["id"], "title": "Agreement",
                                                       "expiry_date": "2027-03-31",
                                                       "file": SimpleUploadedFile("a.pdf", pdf_stub(), content_type="application/pdf")},
                               format="multipart")
        self.assertEqual(upload(self.api_staff).status_code, 403)
        self.assertEqual(upload(self.hr).status_code, 403)
        r = upload(self.pm)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["expiry_date"], "2027-03-31")
        for who, code in ((self.hr, 200), (self.api_staff, 403)):
            self.assertEqual(who.get("/api/v1/attachments/", {"entity_type": "project", "entity_id": p["id"]}).status_code, code)
        # a project with files cannot be deleted, it is cancelled instead
        r = self.pm.delete(f"{P}/projects/{p['id']}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("files are attached", str(r.json()))
