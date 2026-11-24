import re
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Role
from apps.company.models import CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user, pdf_stub
from apps.hr.models import Employee

from .base import ACCOUNT, H, PAN, HRBase

Action = CompanyProfileAuditEvent.Action


class EmployeeTests(HRBase):
    def test_code_is_automatic_with_year_and_date(self):
        a, b = self.emp("Asha"), self.emp("Bala")
        self.assertRegex(a["code"], r"^T2N-EMP-\d{2}-\d{4}-0001$")
        self.assertRegex(b["code"], r"-0002$")

    def test_validation_messages(self):
        bad = [({"first_name": ""}, "first_name"), ({"date_of_birth": "2999-01-01"}, "date_of_birth"), ({"pan": "BAD"}, "pan"),
               ({"aadhaar": "123"}, "aadhaar"), ({"bank_ifsc": "XX"}, "bank_ifsc"), ({"bank_account_number": "12"}, "bank_account_number"),
               ({"bank_account_number": ACCOUNT}, "bank_ifsc"), ({"work_email": "nope"}, "work_email"), ({"phone": "abc"}, "phone"),
               ({"uan": "12"}, "uan"), ({"employment_type": "slave"}, "employment_type"), ({"department": str(uuid.uuid4())}, "department"),
               ({"status": "exited"}, "exit_date"), ({"date_of_joining": ""}, "date_of_joining"),
               ({"date_of_birth": "2020-01-01"}, "date_of_joining")]
        for extra, field in bad:
            payload = {"first_name": "Zed", "date_of_joining": "2025-04-01", **extra}
            r = self.api_hr.post(f"{H}/employees/", payload)
            self.assertEqual(r.status_code, 400, extra)
            self.assertIn(field, r.json(), extra)
        self.assertEqual(Employee.objects.count(), 0)

    def test_duplicate_pan_and_aadhaar_are_refused(self):
        self.emp("Asha", pan=PAN, aadhaar="234567890123")
        for extra, field in (({"pan": PAN}, "pan"), ({"aadhaar": "2345 6789 0123"}, "aadhaar")):
            r = self.api_hr.post(f"{H}/employees/", {"first_name": "B", "date_of_joining": "2025-04-01", **extra})
            self.assertEqual(r.status_code, 400)
            self.assertIn(field, r.json())

    def test_manager_rules(self):
        boss, mid, low = self.emp("Boss"), self.emp("Mid"), self.emp("Low")
        ok = self.api_hr.patch(f"{H}/employees/{mid['id']}/", {"manager": boss["id"]})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["manager"]["name"], "Boss Rao")
        self.api_hr.patch(f"{H}/employees/{low['id']}/", {"manager": mid["id"]})
        self.assertIn("manager", self.api_hr.patch(f"{H}/employees/{boss['id']}/", {"manager": low["id"]}).json())  # loop
        self.assertIn("manager", self.api_hr.patch(f"{H}/employees/{boss['id']}/", {"manager": boss["id"]}).json())
        tree = self.api_hr.get(f"{H}/employees/org-chart/").json()
        self.assertEqual([n["name"] for n in tree], ["Boss Rao"])
        self.assertEqual(tree[0]["reports"][0]["reports"][0]["name"], "Low Rao")

    def test_list_filters_and_status_tabs(self):
        d = self.dept()
        a = self.emp("Asha", department=d["id"])
        self.emp("Bala", employment_type="contract")
        c = self.emp("Chitra")
        self.api_hr.patch(f"{H}/employees/{c['id']}/", {"status": "exited", "exit_date": "2026-06-30", "exit_reason": "Resigned"})
        names = lambda **q: sorted(e["name"] for e in self.api_hr.get(f"{H}/employees/", q).json())  # noqa: E731
        self.assertEqual(names(), ["Asha Rao", "Bala Rao"])
        self.assertEqual(names(status="exited"), ["Chitra Rao"])
        self.assertEqual(names(status="all"), ["Asha Rao", "Bala Rao", "Chitra Rao"])
        self.assertEqual(names(department=d["id"]), ["Asha Rao"])
        self.assertEqual(names(type="contract"), ["Bala Rao"])
        self.assertEqual(names(q="bal"), ["Bala Rao"])
        self.assertEqual(names(q=a["code"]), ["Asha Rao"])

    def test_exit_needs_a_date_and_winds_things_down(self):
        _, _, e = self.employee_user("leaver")
        self.leave_type()
        self.api_hr.post(f"{H}/leave/requests/", {"employee": e["id"], "leave_type": self.api_hr.get(f"{H}/leave/types/").json()[0]["id"],
                                                  "start_date": "2026-09-14", "end_date": "2026-09-14"})
        r = self.api_hr.patch(f"{H}/employees/{e['id']}/", {"status": "exited"})
        self.assertIn("exit_date", r.json())
        r = self.api_hr.patch(f"{H}/employees/{e['id']}/", {"status": "exited", "exit_date": "2026-08-31", "exit_reason": "Moved"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.api_hr.get(f"{H}/leave/requests/").json()[0]["status"], "cancelled")
        self.assertNotIn(e["id"], [x["id"] for x in self.api.get(f"{H}/lookup/").json()])
        back = self.api_hr.patch(f"{H}/employees/{e['id']}/", {"status": "active"}).json()
        self.assertIsNone(back["exit_date"])

    def test_delete_only_when_there_is_no_history(self):
        fresh, used = self.emp("Fresh"), self.emp("Used")
        self.structure(used)
        self.api_hr.post(f"{H}/attendance/", {"date": "2026-08-03", "status": "present", "employees": [used["id"]]})
        self.assertEqual(self.api_hr.delete(f"{H}/employees/{fresh['id']}/").status_code, 204)
        r = self.api_hr.delete(f"{H}/employees/{used['id']}/")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api_hr.get(f"{H}/employees/{used['id']}/").status_code, 200)

    def test_link_user_once(self):
        u = make_user("linked", Role.EMPLOYEE)
        a = self.emp("Asha", user=u)
        self.assertTrue(a["has_login"])
        self.assertNotIn(str(u.pk), [x["id"] for x in self.api_hr.get(f"{H}/employees/available-users/").json()])
        r = self.api_hr.post(f"{H}/employees/", {"first_name": "B", "date_of_joining": "2025-04-01", "user_id": str(u.pk)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("user_id", r.json())
        self.assertEqual(self.api_hr.patch(f"{H}/employees/{a['id']}/", {"user_id": None}).json()["has_login"], False)

    def test_departments_and_designations(self):
        d = self.dept("Design")
        self.assertEqual(self.api_hr.post(f"{H}/departments/", {"name": "design"}).status_code, 400)  # duplicate, any case
        self.emp("Asha", department=d["id"])
        rows = self.api_hr.get(f"{H}/departments/").json()
        self.assertEqual((rows[0]["name"], rows[0]["headcount"]), ("Design", 1))
        self.assertEqual(self.api_hr.delete(f"{H}/departments/{d['id']}/").status_code, 400)
        off = self.api_hr.patch(f"{H}/departments/{d['id']}/", {"is_active": False}).json()
        self.assertFalse(off["is_active"])
        empty = self.dept("Empty")
        self.assertEqual(self.api_hr.delete(f"{H}/departments/{empty['id']}/").status_code, 204)
        g = self.desig("Lead")
        self.assertEqual(self.api_hr.patch(f"{H}/designations/{g['id']}/", {"name": "Tech Lead"}).json()["name"], "Tech Lead")
        self.assertEqual(self.api_hr.delete(f"{H}/designations/{g['id']}/").status_code, 204)
        self.assertEqual(self.api_hr.delete(f"{H}/designations/{g['id']}/").status_code, 404)

    def test_unknown_ids_are_404_not_500(self):
        ghost = uuid.uuid4()
        for method, url in (("get", f"{H}/employees/{ghost}/"), ("patch", f"{H}/employees/{ghost}/"), ("delete", f"{H}/employees/{ghost}/"),
                            ("get", f"{H}/employees/{ghost}/salary/"), ("get", f"{H}/employees/{ghost}/attendance/"),
                            ("get", f"{H}/departments/{ghost}/"), ("get", f"{H}/payroll/runs/{ghost}/"), ("get", f"{H}/payroll/payslips/{ghost}/"),
                            ("get", f"{H}/leave/requests/{ghost}/"), ("delete", f"{H}/holidays/{ghost}/"), ("get", f"{H}/performance/cycles/{ghost}/")):
            self.assertEqual(getattr(self.api, method)(url).status_code, 404, (method, url))
        self.assertEqual(self.api.get(f"{H}/employees/not-a-uuid/").status_code, 404)


class MaskingAndPrivacy(HRBase):
    def setUp(self):
        super().setUp()
        self.e = self.emp("Asha", pan=PAN, aadhaar="234567890123", bank_account_number=ACCOUNT, bank_ifsc="HDFC0001234", bank_name="HDFC",
                          date_of_birth="1990-05-17")
        self.structure(self.e)

    def test_lists_and_detail_are_masked_by_default(self):
        for client in (self.api_hr, self.api):
            row = client.get(f"{H}/employees/").json()[0]
            self.assertEqual((row["pan_masked"], row["aadhaar_masked"], row["bank_account_masked"]), ("XXXXXX234F", "XXXXXX0123", "XXXXXX9012"))
            for key in ("pan", "aadhaar", "bank_account_number"):
                self.assertNotIn(key, row)
            raw = client.get(f"{H}/employees/{self.e['id']}/").content.decode()
            for secret in (PAN, "234567890123", ACCOUNT):
                self.assertNotIn(secret, raw)

    def test_full_values_only_on_request_for_hr_manage_and_it_is_audited(self):
        r = self.api_hr.get(f"{H}/employees/{self.e['id']}/", {"reveal": "true"}).json()
        self.assertEqual((r["pan"], r["aadhaar"], r["bank_account_number"]), (PAN, "234567890123", ACCOUNT))
        self.assertEqual(CompanyProfileAuditEvent.objects.filter(entity_type="employee", action=Action.REVEALED).count(), 1)
        for client in (self.fin, self.api_pm, self.api_staff):  # none of them manage HR (a partner holds every permission, so it is not here)
            self.assertEqual(client.get(f"{H}/employees/{self.e['id']}/", {"reveal": "true"}).status_code, 403)

    def test_directory_viewers_do_not_see_personal_or_pay_data(self):
        for client in (self.api_pm,):
            r = client.get(f"{H}/employees/{self.e['id']}/").json()
            for key in ("pan_masked", "aadhaar_masked", "bank_account_masked", "date_of_birth", "personal_email", "address", "current_salary"):
                self.assertNotIn(key, r, key)
            self.assertNotIn("pan_masked", client.get(f"{H}/employees/").json()[0])
        self.assertIn("current_salary", self.api_hr.get(f"{H}/employees/{self.e['id']}/").json())
        self.assertIn("current_salary", self.fin.get(f"{H}/employees/{self.e['id']}/").json())

    def test_lookup_has_no_pay_or_personal_data_and_is_open_to_everyone(self):
        for client in (self.api, self.api_staff, self.api_pm, self.fin):
            rows = client.get(f"{H}/lookup/").json()
            self.assertEqual(sorted(rows[0]), ["code", "department", "designation", "id", "name", "user_id"])
        self.assertEqual(self.anon.get(f"{H}/lookup/").status_code, 401)

    def test_audit_does_not_copy_identifiers(self):
        self.api_hr.patch(f"{H}/employees/{self.e['id']}/", {"pan": "AAAPA1234A", "phone": "9876543210"})
        text = "".join(str(ev.previous_values) + str(ev.new_values) for ev in CompanyProfileAuditEvent.objects.filter(entity_type="employee"))
        for secret in (PAN, "AAAPA1234A", "9876543210", ACCOUNT, "234567890123"):
            self.assertNotIn(secret, text)
        ev = CompanyProfileAuditEvent.objects.filter(entity_type="employee", action=Action.UPDATED).latest("occurred_at")
        self.assertIn("pan", ev.changed_fields)
        self.assertEqual(ev.category, "hr")

    def test_creating_audits_and_salary_amounts_are_redacted(self):
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="employee", action=Action.CREATED, entity_id=self.e["id"]).exists())
        ev = CompanyProfileAuditEvent.objects.get(entity_type="salary_structure", action=Action.CREATED)
        self.assertEqual(ev.new_values.get("basic"), "[redacted]")


class PermissionMatrix(HRBase):
    def test_who_may_do_what(self):
        e = self.emp("Asha")
        clients = {"admin": self.api, "managing_partner": self.api_mp, "partner": self.api_partner, "finance": self.fin, "hr": self.api_hr,
                   "pm": self.api_pm, "employee": self.api_staff}
        expect = [  # (method, url, payload, who gets through)
            ("get", f"{H}/employees/", None, {"admin", "managing_partner", "partner", "finance", "hr", "pm"}),
            ("get", f"{H}/dashboard/", None, {"admin", "managing_partner", "partner", "finance", "hr", "pm"}),
            ("get", f"{H}/departments/", None, {"admin", "managing_partner", "partner", "finance", "hr", "pm"}),
            ("post", f"{H}/departments/", {"name": "X{n}"}, {"admin", "managing_partner", "partner", "hr"}),
            ("get", f"{H}/employees/{e['id']}/salary/", None, {"admin", "managing_partner", "partner", "finance", "hr"}),
            ("get", f"{H}/payroll/runs/", None, {"admin", "managing_partner", "partner", "finance", "hr"}),
            ("get", f"{H}/payroll/reports/register/", None, {"admin", "managing_partner", "partner", "finance", "hr"}),
            ("post", f"{H}/holidays/", {"date": "2026-10-{n}", "name": "H"}, {"admin", "managing_partner", "partner", "hr"}),
            ("get", f"{H}/holidays/", None, set(clients)),
            ("get", f"{H}/leave/types/", None, set(clients)),
            ("get", f"{H}/assignments/", None, {"admin", "managing_partner", "partner", "finance", "hr", "pm"}),
            ("get", f"{H}/employees/org-chart/", None, {"admin", "managing_partner", "partner", "finance", "hr", "pm"}),
            ("get", f"{H}/payroll/settings/", None, {"admin", "managing_partner", "partner", "finance", "hr", "pm"}),
            ("patch", f"{H}/payroll/settings/", {"pt_state": "Tamil Nadu"}, {"admin", "managing_partner", "partner", "finance", "hr"}),
        ]
        for method, url, payload, allowed in expect:
            for n, (role, client) in enumerate(clients.items(), 10):
                body = payload and {k: v.replace("{n}", str(n)) if isinstance(v, str) else v for k, v in payload.items()}
                r = getattr(client, method)(url, body) if body else getattr(client, method)(url)
                self.assertEqual(r.status_code < 300, role in allowed, f"{method} {url} as {role}: {r.status_code} {r.content[:120]}")
        self.assertEqual(self.anon.get(f"{H}/employees/").status_code, 401)

    def test_only_hr_manage_changes_people(self):
        e = self.emp("Asha")
        for client in (self.fin, self.api_pm, self.api_staff):
            self.assertEqual(client.post(f"{H}/employees/", {"first_name": "X", "date_of_joining": "2025-04-01"}).status_code, 403)
            self.assertEqual(client.patch(f"{H}/employees/{e['id']}/", {"phone": "9999999999"}).status_code, 403)
            self.assertEqual(client.delete(f"{H}/employees/{e['id']}/").status_code, 403)

    def test_attachments_for_employee_documents(self):
        e = self.emp("Asha")
        up = lambda client: client.post("/api/v1/attachments/", {  # noqa: E731
            "entity_type": "employee", "entity_id": e["id"], "title": "Offer letter", "expiry_date": "2027-01-01",
            "file": SimpleUploadedFile("offer.pdf", pdf_stub(), content_type="application/pdf")}, format="multipart")
        self.assertEqual(up(self.api_hr).status_code, 201)
        self.assertEqual(up(self.api_pm).status_code, 403)  # can view, cannot add
        self.assertEqual(up(self.api_staff).status_code, 403)
        listing = self.api_pm.get("/api/v1/attachments/", {"entity_type": "employee", "entity_id": e["id"]})
        self.assertEqual(listing.json()[0]["title"], "Offer letter")
        self.assertEqual(self.api_staff.get("/api/v1/attachments/", {"entity_type": "employee", "entity_id": e["id"]}).status_code, 403)
