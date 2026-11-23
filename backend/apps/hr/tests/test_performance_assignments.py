import datetime
import uuid

from django.apps import apps as django_apps

from apps.company.models import CompanyProfileAuditEvent

from .base import H, HRBase


class PerformanceTests(HRBase):
    def setUp(self):
        super().setUp()
        self.boss_user, self.boss_client, self.boss = self.employee_user("boss")
        self.a_user, self.a_client, self.a = self.employee_user("asha")
        self.b_user, self.b_client, self.b = self.employee_user("bala")
        for e in (self.a, self.b):
            self.api_hr.patch(f"{H}/employees/{e['id']}/", {"manager": self.boss["id"]})
        self.cycle = self.api_hr.post(f"{H}/performance/cycles/", {"name": "FY 2026-27 H1", "start_date": "2026-04-01", "end_date": "2026-09-30"}).json()

    def reviews(self, client=None, **q):
        return (client or self.api_hr).get(f"{H}/performance/reviews/", {"cycle": self.cycle["id"], **q}).json()

    def test_cycle_validation_and_lifecycle(self):
        self.assertEqual(self.cycle["status"], "draft")
        for bad in ({"name": ""}, {"name": "fy 2026-27 h1"}, {"end_date": "2026-01-01"}, {"start_date": "x"}):
            r = self.api_hr.post(f"{H}/performance/cycles/", {"name": "Other", "start_date": "2026-04-01", "end_date": "2026-09-30", **bad})
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.api_staff.post(f"{H}/performance/cycles/", {}).status_code, 403)
        edited = self.api_hr.patch(f"{H}/performance/cycles/{self.cycle['id']}/", {"end_date": "2026-10-31"}).json()
        self.assertEqual(edited["end_date"], "2026-10-31")
        launched = self.api_hr.post(f"{H}/performance/cycles/{self.cycle['id']}/launch/").json()
        self.assertEqual((launched["status"], launched["reviews"]), ("open", 3))
        self.assertEqual(self.api_hr.post(f"{H}/performance/cycles/{self.cycle['id']}/launch/").status_code, 400)
        self.assertEqual(self.api_hr.delete(f"{H}/performance/cycles/{self.cycle['id']}/").status_code, 400)  # only a draft
        closed = self.api_hr.post(f"{H}/performance/cycles/{self.cycle['id']}/close/").json()
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(self.api_hr.patch(f"{H}/performance/cycles/{self.cycle['id']}/", {"name": "x"}).status_code, 400)

    def launch(self):
        self.api_hr.post(f"{H}/performance/cycles/{self.cycle['id']}/launch/")

    def test_self_review_then_manager_rating_and_summary(self):
        self.launch()
        mine = {r["employee"]["name"]: r for r in self.reviews()}
        ra, rb = mine["Asha Test"], mine["Bala Test"]
        self.assertEqual(ra["reviewer"]["name"], "Boss Test")
        sr = lambda client, r, **d: client.post(f"{H}/performance/reviews/{r['id']}/self/", d)  # noqa: E731
        mr = lambda client, r, **d: client.post(f"{H}/performance/reviews/{r['id']}/manager/", d)  # noqa: E731
        self.assertEqual(sr(self.a_client, ra, self_rating=6).status_code, 400)
        self.assertEqual(sr(self.a_client, ra, self_rating=0).status_code, 400)
        self.assertEqual(sr(self.a_client, ra, self_comments="no rating").status_code, 400)
        self.assertEqual(sr(self.b_client, ra, self_rating=5).status_code, 403)  # someone else's review
        self.assertEqual(sr(self.a_client, ra, self_rating=4, self_comments="Shipped the portal").json()["status"], "self_done")
        sr(self.b_client, rb, self_rating=5)
        self.assertEqual(mr(self.a_client, ra, manager_rating=5).status_code, 403)  # nobody rates themselves
        self.assertEqual(mr(self.b_client, ra, manager_rating=1).status_code, 403)  # a colleague is not the manager
        self.assertEqual(mr(self.boss_client, ra, manager_rating=7).status_code, 400)
        self.assertEqual(mr(self.boss_client, ra, manager_rating=3, manager_comments="Solid").json()["status"], "completed")
        mr(self.api_hr, rb, manager_rating=5)  # HR may rate too
        s = self.api_hr.get(f"{H}/performance/cycles/{self.cycle['id']}/summary/").json()
        self.assertEqual((s["reviews"], s["completed"], s["waiting_self"], s["self_average"], s["manager_average"]), (3, 2, 1, 4.5, 4.0))
        self.assertEqual({d["rating"]: d["count"] for d in s["distribution"]}, {1: 0, 2: 0, 3: 1, 4: 0, 5: 1})
        # visibility: an employee sees only their own review; the manager sees the team
        self.assertEqual([r["employee"]["name"] for r in self.reviews(self.a_client)], ["Asha Test"])
        self.assertEqual(sorted(r["employee"]["name"] for r in self.reviews(self.boss_client)), ["Asha Test", "Bala Test", "Boss Test"])  # their own and their team's
        self.api_hr.post(f"{H}/performance/cycles/{self.cycle['id']}/close/")
        self.assertEqual(mr(self.boss_client, rb, manager_rating=2).status_code, 400)  # closed
        self.assertEqual(self.api_staff.get(f"{H}/performance/cycles/{self.cycle['id']}/summary/").status_code, 403)

    def test_goals(self):
        self.launch()
        g = self.boss_client.post(f"{H}/performance/goals/", {"employee": self.a["id"], "cycle": self.cycle["id"], "title": "Ship the portal", "weight": 60})
        self.assertEqual(g.status_code, 201, g.content)
        gid = g.json()["id"]
        over = self.boss_client.post(f"{H}/performance/goals/", {"employee": self.a["id"], "cycle": self.cycle["id"], "title": "Another", "weight": 50})
        self.assertEqual(over.status_code, 400)
        self.assertIn("weight", over.json())
        self.assertEqual(self.b_client.post(f"{H}/performance/goals/", {"employee": self.a["id"], "cycle": self.cycle["id"], "title": "x"}).status_code, 403)
        # the employee reports progress but cannot retitle or reweight
        r = self.a_client.patch(f"{H}/performance/goals/{gid}/", {"progress": 40, "status": "in_progress", "title": "Hacked", "weight": 1})
        self.assertEqual((r.json()["progress"], r.json()["status"], r.json()["title"], r.json()["weight"]), (40, "in_progress", "Ship the portal", 60))
        self.assertEqual(self.a_client.patch(f"{H}/performance/goals/{gid}/", {"progress": 101}).status_code, 400)
        self.assertEqual(self.boss_client.patch(f"{H}/performance/goals/{gid}/", {"manager_comment": "Good pace"}).json()["manager_comment"], "Good pace")
        self.assertEqual(self.b_client.patch(f"{H}/performance/goals/{gid}/", {"progress": 100}).status_code, 403)
        self.assertEqual(self.a_client.delete(f"{H}/performance/goals/{gid}/").status_code, 403)
        mine = self.a_client.get(f"{H}/performance/goals/").json()
        self.assertEqual([x["title"] for x in mine], ["Ship the portal"])
        self.assertEqual(self.b_client.get(f"{H}/performance/goals/").json(), [])
        self.assertEqual(self.boss_client.delete(f"{H}/performance/goals/{gid}/").status_code, 204)

    def test_unknown_ids_and_audit(self):
        ghost = uuid.uuid4()
        self.assertEqual(self.api_hr.post(f"{H}/performance/reviews/{ghost}/self/", {}).status_code, 404)
        self.assertEqual(self.api_hr.patch(f"{H}/performance/goals/{ghost}/", {}).status_code, 404)
        self.assertEqual(self.api_hr.post(f"{H}/performance/cycles/{ghost}/launch/").status_code, 404)
        self.launch()
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="review_cycle").count() >= 2)


class AssignmentTests(HRBase):
    def setUp(self):
        super().setUp()
        Project = django_apps.get_model("projects", "Project")
        self.p1 = Project.objects.create(company=self.company, code="T2N-PRJ-26-1114-0001", name="Portal")
        self.p2 = Project.objects.create(company=self.company, code="T2N-PRJ-26-1114-0002", name="Sensor lab")
        self.e = self.emp("Asha", "Rao")

    def assign(self, project=None, expect=201, **over):
        payload = {"employee": self.e["id"], "project_id": str((project or self.p1).pk), "role": "Developer", "allocation_percent": 60,
                   "start_date": "2026-08-01", **over}
        r = self.api_hr.post(f"{H}/assignments/", payload)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def test_assignment_validation_and_the_100_percent_rule(self):
        a = self.assign(end_date="2026-12-31")
        self.assertEqual((a["project_name"], a["project_code"], a["allocation_percent"]), ("Portal", "T2N-PRJ-26-1114-0001", 60))
        self.assertIn("allocation_percent", self.assign(self.p2, expect=400, allocation_percent=50, start_date="2026-10-01"))  # 60 + 50 overlaps
        self.assign(self.p2, allocation_percent=40, start_date="2026-10-01", end_date="2026-12-31")                 # 60 + 40 = 100 is allowed
        self.assign(self.p2, allocation_percent=100, start_date="2027-01-01")                # after the first one ended
        for bad, field in (({"allocation_percent": 0}, "allocation_percent"), ({"allocation_percent": 101}, "allocation_percent"),
                           ({"start_date": ""}, "start_date"), ({"end_date": "2026-01-01"}, "end_date"),
                           ({"project_id": "nope"}, "project_id"), ({"project_id": str(uuid.uuid4())}, "project_id")):
            payload = {"employee": self.e["id"], "project_id": str(self.p2.pk), "allocation_percent": 10, "start_date": "2026-08-01", **bad}
            r = self.api_hr.post(f"{H}/assignments/", payload)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json(), bad)
        self.assertEqual(self.api_hr.post(f"{H}/assignments/", {"project_id": str(self.p1.pk), "start_date": "2026-08-01"}).status_code, 400)  # no employee

    def test_listing_by_project_and_who_can_read_or_write(self):
        self.assign()
        other = self.emp("Bala", "Iyer")
        self.api_hr.post(f"{H}/assignments/", {"employee": other["id"], "project_id": str(self.p2.pk), "start_date": "2026-08-01"})
        rows = self.api_pm.get(f"{H}/assignments/", {"project": str(self.p1.pk)}).json()
        self.assertEqual([r["employee"]["name"] for r in rows], ["Asha Rao"])
        self.assertEqual(len(self.api_pm.get(f"{H}/assignments/").json()), 2)
        self.assertEqual(self.api_pm.get(f"{H}/assignments/", {"project": "not-a-uuid"}).json(), [])
        self.assertEqual(self.api_staff.get(f"{H}/assignments/").status_code, 403)
        for client in (self.api_pm, self.api_partner, self.fin, self.api_staff):
            self.assertEqual(client.post(f"{H}/assignments/", {}).status_code, 403)

    def test_edit_delete_and_utilisation(self):
        a = self.assign(start_date="2026-01-01")
        self.assertEqual(self.api_hr.patch(f"{H}/assignments/{a['id']}/", {"allocation_percent": 80, "role": "Lead"}).json()["allocation_percent"], 80)
        self.assertEqual(self.api_hr.patch(f"{H}/assignments/{a['id']}/", {"allocation_percent": 120}).status_code, 400)
        bench = self.emp("Chitra", "Bench")
        u = self.api_pm.get(f"{H}/assignments/utilisation/").json()
        by = {r["employee"]["name"]: r["allocation"] for r in u["rows"]}
        self.assertEqual(by, {"Asha Rao": 80, "Chitra Bench": 0})
        self.assertEqual((u["average"], u["bench"], u["over"]), (40, 1, 0))
        self.assertEqual(self.api_hr.delete(f"{H}/assignments/{a['id']}/").status_code, 204)
        self.assertEqual(self.api_hr.delete(f"{H}/assignments/{a['id']}/").status_code, 404)
        self.assertIsNotNone(bench)

    def test_leaving_the_company_ends_open_assignments(self):
        a = self.assign()
        self.api_hr.patch(f"{H}/employees/{self.e['id']}/", {"status": "exited", "exit_date": "2026-09-30", "exit_reason": "Moved"})
        self.assertEqual(self.api_hr.get(f"{H}/assignments/", {"employee": self.e["id"]}).json()[0]["end_date"], "2026-09-30")
        self.assertIn("employee", self.api_hr.post(f"{H}/assignments/", {"employee": self.e["id"], "project_id": str(self.p2.pk), "start_date": "2026-10-05"}).json())
        self.assertIsNotNone(a)


class DashboardTests(HRBase):
    def test_dashboard_numbers(self):
        today = datetime.date.today()
        dept = self.dept("Engineering")
        self.emp("Asha", department=dept["id"], date_of_joining=today.replace(day=1).isoformat(),
                 date_of_birth=(today + datetime.timedelta(days=100)).replace(year=1990, day=15).isoformat())
        soon = today + datetime.timedelta(days=5)
        self.emp("Bala", department=dept["id"], date_of_birth=soon.replace(year=1992).isoformat())
        c = self.emp("Chitra")
        self.api_hr.patch(f"{H}/employees/{c['id']}/", {"status": "on_notice"})
        d = self.emp("Dev")
        self.api_hr.patch(f"{H}/employees/{d['id']}/", {"status": "exited", "exit_date": today.isoformat(), "exit_reason": "x"})
        r = self.api_hr.get(f"{H}/dashboard/").json()
        self.assertEqual((r["headcount"], r["active"], r["on_notice"], r["exited"]), (3, 2, 1, 1))
        self.assertEqual(r["new_joiners"], 1)
        self.assertEqual(r["exits_this_month"], 1)
        self.assertEqual({x["name"]: x["count"] for x in r["by_department"]}, {"Engineering": 2, "No department": 1})
        self.assertEqual(len(r["trend"]), 6)
        self.assertEqual(r["trend"][-1]["joined"], 1)
        self.assertIn("Bala Rao", [u["employee"]["name"] for u in r["upcoming"] if u["kind"] == "birthday"])
        self.assertNotIn("Asha Rao", [u["employee"]["name"] for u in r["upcoming"] if u["kind"] == "birthday"])
        self.assertEqual(r["attendance_today"]["expected"], 3)
        self.api_hr.post(f"{H}/attendance/", {"date": today.isoformat(), "status": "present"})
        r = self.api_hr.get(f"{H}/dashboard/").json()
        self.assertEqual((r["attendance_today"]["present"], r["attendance_today"]["marked"]), (3, 3))

    def test_leave_on_the_dashboard(self):
        self.leave_type("CL", "Casual leave", "30")
        self.fin.patch(f"{H}/payroll/settings/", {"weekly_off_days": []})  # so that "today" always counts as a working day
        user, client, e = self.employee_user("emp")
        day = datetime.date.today()
        lt = self.api_hr.get(f"{H}/leave/types/").json()[0]
        pending = client.post(f"{H}/leave/requests/", {"leave_type": lt["id"], "start_date": day.isoformat(), "submit": True}).json()
        r = self.api_hr.get(f"{H}/dashboard/").json()
        self.assertEqual((r["pending_leave_count"], r["on_leave_today"]), (1, []))
        self.api_hr.post(f"{H}/leave/requests/{pending['id']}/approve/", {})
        r = self.api_hr.get(f"{H}/dashboard/").json()
        self.assertEqual((r["pending_leave_count"], [x["employee"]["name"] for x in r["on_leave_today"]]), (0, ["Emp Test"]))
