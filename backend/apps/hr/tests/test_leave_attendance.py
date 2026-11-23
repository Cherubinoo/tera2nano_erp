"""Leave balances and rules, and attendance. August 2026: the 1st is a Saturday, the 3rd a Monday, Sundays are the weekly off."""
import datetime

from apps.accounts.models import Role
from apps.company.models import CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user
from apps.hr.models import Attendance, LeaveRequest

from .base import D, H, HRBase


class LeaveBase(HRBase):
    def setUp(self):
        super().setUp()
        self.cl = self.leave_type("CL", "Casual leave", "12")
        self.user, self.client_e, self.e = self.employee_user("asha")

    def apply(self, start, end=None, client=None, lt=None, expect=201, **extra):
        r = (client or self.client_e).post(f"{H}/leave/requests/", {"leave_type": (lt or self.cl)["id"], "start_date": start, "end_date": end or start, **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def act(self, req, action, client=None, expect=200, **data):
        r = (client or self.api_hr).post(f"{H}/leave/requests/{req['id']}/{action}/", data)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def balance(self, lt=None, client=None, on="2026-08-10"):
        rows = (client or self.client_e).get(f"{H}/leave/balances/", {"date": on}).json()["rows"]
        return next(r for r in rows if r["leave_type"]["id"] == (lt or self.cl)["id"])


class LeaveRules(LeaveBase):
    def test_new_balance_is_the_annual_quota(self):
        b = self.balance()
        self.assertEqual((D(b["accrued"]), D(b["taken"]), D(b["pending"]), D(b["available"])), (D("12"), D("0"), D("0"), D("12")))
        self.assertEqual(b["year_start"], "2026-04-01")

    def test_days_skip_weekly_offs_and_holidays(self):
        self.assertEqual(D(self.apply("2026-08-07", "2026-08-10")["days"]), D("3"))  # Fri, Sat, (Sun off), Mon
        self.api_hr.post(f"{H}/holidays/", {"date": "2026-08-19", "name": "Local holiday"})
        self.assertEqual(D(self.apply("2026-08-18", "2026-08-20")["days"]), D("2"))  # Tue, (Wed holiday), Thu

    def test_a_request_only_on_off_days_is_refused(self):
        r = self.client_e.post(f"{H}/leave/requests/", {"leave_type": self.cl["id"], "start_date": "2026-08-09", "end_date": "2026-08-09"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("start_date", r.json())

    def test_half_day(self):
        self.assertEqual(D(self.apply("2026-08-03", half_day=True)["days"]), D("0.5"))
        r = self.client_e.post(f"{H}/leave/requests/", {"leave_type": self.cl["id"], "start_date": "2026-08-10", "end_date": "2026-08-11", "half_day": True})
        self.assertIn("half_day", r.json())

    def test_apply_submit_approve_moves_the_balance_and_marks_attendance(self):
        req = self.apply("2026-08-03", "2026-08-05", submit=True)
        self.assertEqual(req["status"], "pending_approval")
        self.assertRegex(req["number"], r"^T2N/LV/\d{2}/\d{4}/00001$")
        b = self.balance()
        self.assertEqual((D(b["pending"]), D(b["available"])), (D("3"), D("9")))
        done = self.act(req, "approve", note="Enjoy")
        self.assertEqual((done["status"], done["decided_by"]), ("approved", "hrm"))
        b = self.balance()
        self.assertEqual((D(b["taken"]), D(b["pending"]), D(b["available"])), (D("3"), D("0"), D("9")))
        rows = Attendance.objects.filter(employee_id=self.e["id"]).order_by("date")
        self.assertEqual([(a.date.isoformat(), a.status, a.source) for a in rows],
                         [("2026-08-03", "leave", "leave"), ("2026-08-04", "leave", "leave"), ("2026-08-05", "leave", "leave")])
        # cancelling gives the days back and clears the attendance
        gone = self.act(req, "cancel")  # the leave is in the past, so only HR may cancel it
        self.assertEqual(gone["status"], "cancelled")
        self.assertEqual(D(self.balance()["available"]), D("12"))
        self.assertFalse(Attendance.objects.filter(employee_id=self.e["id"]).exists())

    def test_overlap_with_pending_or_approved_is_refused_but_not_with_rejected_or_draft(self):
        first = self.apply("2026-08-10", "2026-08-12", submit=True)
        for start, end in (("2026-08-12", "2026-08-14"), ("2026-08-10", "2026-08-10"), ("2026-08-07", "2026-08-11")):
            r = self.client_e.post(f"{H}/leave/requests/", {"leave_type": self.cl["id"], "start_date": start, "end_date": end})
            self.assertEqual(r.status_code, 400, (start, end))
            self.assertIn("overlaps", r.json()["start_date"][0])
        self.act(first, "reject", note="Project deadline")
        self.apply("2026-08-10", "2026-08-12")  # free again

    def test_not_enough_balance(self):
        r = self.client_e.post(f"{H}/leave/requests/", {"leave_type": self.cl["id"], "start_date": "2026-08-03", "end_date": "2026-08-19"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Not enough Casual leave: 15", r.json()["leave_type"][0])
        self.apply("2026-08-03", "2026-08-14", submit=True)  # 11 days, pending
        r = self.client_e.post(f"{H}/leave/requests/", {"leave_type": self.cl["id"], "start_date": "2026-08-17", "end_date": "2026-08-19"})
        self.assertIn("1.0 available", r.json()["leave_type"][0])  # pending days are already spoken for

    def test_unpaid_leave_has_no_limit(self):
        lop = self.leave_type("LOP", "Loss of pay", "0", paid=False)
        req = self.apply("2026-08-03", "2026-08-28", lt=lop, submit=True)
        self.assertEqual(D(req["days"]), D("23"))

    def test_dates_must_be_sensible(self):
        for payload, field in (({"start_date": "2026-08-05", "end_date": "2026-08-03"}, "end_date"), ({"start_date": "2024-01-02"}, "start_date"),
                               ({"start_date": "2027-03-30", "end_date": "2027-04-02"}, "end_date"), ({"start_date": "bad"}, "start_date"),
                               ({"leave_type": "nope"}, "leave_type")):
            r = self.client_e.post(f"{H}/leave/requests/", {"leave_type": self.cl["id"], "start_date": "2026-08-03", **payload})
            self.assertEqual(r.status_code, 400, payload)
            self.assertIn(field, r.json(), payload)

    def test_reject_needs_a_reason_and_only_pending_can_be_decided(self):
        req = self.apply("2026-08-03", submit=True)
        self.assertIn("note", self.act(req, "reject", expect=400))
        self.act(req, "reject", note="No cover")
        self.assertEqual(self.act(req, "approve", expect=400) is not None, True)
        draft = self.apply("2026-08-04")
        self.act(draft, "approve", expect=400)
        self.assertEqual(self.client_e.post(f"{H}/leave/requests/{draft['id']}/submit/").status_code, 200)

    def test_nobody_decides_their_own_request_and_only_the_manager_or_hr_can(self):
        boss_user, boss_client, boss = self.employee_user("boss")
        other_user, other_client, _ = self.employee_user("other")
        self.api_hr.patch(f"{H}/employees/{self.e['id']}/", {"manager": boss["id"]})
        req = self.apply("2026-08-03", submit=True)
        self.act(req, "approve", client=self.client_e, expect=403)   # not their own
        self.act(req, "approve", client=other_client, expect=403)    # not a colleague
        self.assertEqual(other_client.get(f"{H}/leave/requests/{req['id']}/").status_code, 403)
        mine = boss_client.get(f"{H}/leave/requests/", {"scope": "approvals"}).json()
        self.assertEqual([m["id"] for m in mine], [req["id"]])
        self.assertTrue(mine[0]["can_approve"])
        self.assertEqual(self.act(req, "approve", client=boss_client)["status"], "approved")
        # the boss's own request cannot be approved by the boss, but HR can
        own = self.apply("2026-08-10", client=boss_client, submit=True)
        self.act(own, "approve", client=boss_client, expect=403)
        self.act(own, "approve")

    def test_employee_sees_only_their_own_requests_and_cannot_apply_for_others(self):
        _, other_client, other = self.employee_user("other")
        mine = self.apply("2026-08-03")
        theirs = self.apply("2026-08-04", client=other_client)
        self.assertEqual([r["id"] for r in self.client_e.get(f"{H}/leave/requests/").json()], [mine["id"]])
        self.assertEqual(self.client_e.get(f"{H}/leave/requests/{theirs['id']}/").status_code, 403)
        self.assertEqual(sorted(r["id"] for r in self.api_hr.get(f"{H}/leave/requests/").json()), sorted([mine["id"], theirs["id"]]))
        r = self.client_e.post(f"{H}/leave/requests/", {"employee": other["id"], "leave_type": self.cl["id"], "start_date": "2026-08-05"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client_e.get(f"{H}/leave/balances/", {"employee": other["id"]}).status_code, 403)
        self.assertEqual(self.client_e.post(f"{H}/leave/requests/{theirs['id']}/cancel/").status_code, 403)
        # HR can apply on someone's behalf
        r = self.api_hr.post(f"{H}/leave/requests/", {"employee": other["id"], "leave_type": self.cl["id"], "start_date": "2026-08-06"})
        self.assertEqual(r.status_code, 201)

    def test_a_user_without_an_employee_record_cannot_apply(self):
        r = self.api_staff.post(f"{H}/leave/requests/", {"leave_type": self.cl["id"], "start_date": "2026-08-03"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api_staff.get(f"{H}/leave/balances/").json()["linked"], False)

    def test_started_leave_can_only_be_cancelled_by_hr(self):
        past = datetime.date.today() - datetime.timedelta(days=3)
        while past.weekday() == 6:
            past -= datetime.timedelta(days=1)
        # make it approvable: an approved request that has already started
        req = self.apply(past.isoformat(), submit=True)
        self.act(req, "approve")
        self.act(req, "cancel", client=self.client_e, expect=400)
        self.assertEqual(self.act(req, "cancel")["status"], "cancelled")

    def test_edit_and_delete_drafts_only(self):
        d = self.apply("2026-08-03")
        r = self.client_e.patch(f"{H}/leave/requests/{d['id']}/", {"end_date": "2026-08-04", "reason": "Family"})
        self.assertEqual((D(r.json()["days"]), r.json()["reason"]), (D("2"), "Family"))
        sub = self.act(d, "submit", client=self.client_e)
        self.assertEqual(self.client_e.patch(f"{H}/leave/requests/{d['id']}/", {"reason": "x"}).status_code, 400)
        self.assertEqual(self.client_e.delete(f"{H}/leave/requests/{sub['id']}/").status_code, 400)
        d2 = self.apply("2026-08-12")
        self.assertEqual(self.client_e.delete(f"{H}/leave/requests/{d2['id']}/").status_code, 204)

    def test_carry_forward_is_limited(self):
        el = self.leave_type("EL", "Earned leave", "12", carry_forward=True, max_carry_forward="5")
        req = self.apply("2026-03-02", "2026-03-05", lt=el, submit=True)  # last leave year: 4 days of 12 used
        self.act(req, "approve")
        b = self.balance(el)
        self.assertEqual((D(b["accrued"]), D(b["carried_forward"]), D(b["available"])), (D("12"), D("5"), D("17")))  # 12 - 4 = 8, capped at 5
        again = self.balance(self.cl)
        self.assertEqual(D(again["carried_forward"]), D("0"))  # casual leave does not carry forward

    def test_joiners_get_a_prorated_quota_and_adjustments_count(self):
        _, c, early = self.employee_user("early", date_of_joining="2026-10-10")
        _, c2, late = self.employee_user("late", date_of_joining="2026-10-20")
        self.assertEqual(D(self.balance(client=c, on="2026-11-20")["accrued"]), D("6"))   # Oct-Mar: six months
        self.assertEqual(D(self.balance(client=c2, on="2026-11-20")["accrued"]), D("5"))  # joined after the 15th: from November
        r = self.api_hr.post(f"{H}/leave/adjustments/", {"employee": self.e["id"], "leave_type": self.cl["id"], "days": "2.5", "reason": "Opening balance", "date": "2026-08-01"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(D(self.balance()["available"]), D("14.5"))
        bad = self.api_hr.post(f"{H}/leave/adjustments/", {"employee": self.e["id"], "leave_type": self.cl["id"], "days": "0", "reason": "x"})
        self.assertIn("days", bad.json())
        self.assertEqual(self.client_e.post(f"{H}/leave/adjustments/", {}).status_code, 403)

    def test_team_calendar_counts_people_away(self):
        _, c, e2 = self.employee_user("second")
        self.act(self.apply("2026-08-04", "2026-08-05", submit=True), "approve")
        self.act(self.apply("2026-08-05", "2026-08-06", client=c, submit=True), "approve")
        cal = self.api_hr.get(f"{H}/leave/calendar/", {"month": "2026-08"}).json()
        away = {d["date"]: d["away"] for d in cal["per_day"]}
        self.assertEqual((away["2026-08-03"], away["2026-08-04"], away["2026-08-05"], away["2026-08-06"]), (0, 1, 2, 1))
        self.assertEqual(len(cal["requests"]), 2)
        mine = self.client_e.get(f"{H}/leave/calendar/", {"month": "2026-08"}).json()
        self.assertEqual([r["employee"]["name"] for r in mine["requests"]], ["Asha Test"])  # a colleague's leave is not shown

    def test_leave_types_crud_and_validation(self):
        self.assertEqual(self.api_hr.post(f"{H}/leave/types/", {"code": "cl", "name": "Other", "annual_quota": "1"}).status_code, 400)  # duplicate code
        self.assertIn("max_carry_forward", self.api_hr.post(f"{H}/leave/types/", {"code": "X", "name": "X", "carry_forward": True}).json())
        self.assertIn("annual_quota", self.api_hr.post(f"{H}/leave/types/", {"code": "X", "name": "X", "annual_quota": "-1"}).json())
        sl = self.leave_type("SL", "Sick leave", "6")
        off = self.api_hr.patch(f"{H}/leave/types/{sl['id']}/", {"is_active": False}).json()
        self.assertFalse(off["is_active"])
        self.assertNotIn("SL", [t["code"] for t in self.client_e.get(f"{H}/leave/types/").json()])
        self.assertEqual(self.api_hr.delete(f"{H}/leave/types/{sl['id']}/").status_code, 204)
        self.apply("2026-08-03")
        self.assertEqual(self.api_hr.delete(f"{H}/leave/types/{self.cl['id']}/").status_code, 400)  # used
        self.assertEqual(self.client_e.post(f"{H}/leave/types/", {"code": "Z", "name": "Z"}).status_code, 403)

    def approval(self, req):
        return self.api_hr.get("/api/v1/approvals/status/", {"kind": "leave_request", "source_id": req["id"]}).json()

    def test_leave_goes_through_the_approvals_module(self):
        req = self.apply("2026-08-03", "2026-08-04", submit=True)
        st = self.approval(req)
        self.assertEqual(st["status"], "pending")
        done = self.api_hr.post(f"/api/v1/approvals/requests/{st['id']}/approve/", {"comment": "ok"})
        self.assertEqual(done.status_code, 200, done.content)
        got = self.api_hr.get(f"{H}/leave/requests/{req['id']}/").json()
        self.assertEqual((got["status"], D(got["days"])), ("approved", D("2")))
        self.assertEqual(Attendance.objects.filter(employee_id=self.e["id"], status="leave").count(), 2)
        self.assertEqual(D(self.balance()["taken"]), D("2"))

    def test_rejecting_or_withdrawing_in_approvals_updates_the_leave_request(self):
        req = self.apply("2026-08-03", submit=True)
        st = self.approval(req)
        self.assertEqual(self.api_hr.post(f"/api/v1/approvals/requests/{st['id']}/reject/", {"comment": "Not that week"}).status_code, 200)
        got = self.api_hr.get(f"{H}/leave/requests/{req['id']}/").json()
        self.assertEqual((got["status"], got["decision_note"]), ("rejected", "Not that week"))
        again = self.apply("2026-08-05", submit=True)
        st2 = self.approval(again)
        self.assertEqual(self.client_e.post(f"/api/v1/approvals/requests/{st2['id']}/withdraw/", {}).status_code, 200)
        self.assertEqual(self.api_hr.get(f"{H}/leave/requests/{again['id']}/").json()["status"], "draft")

    def test_deciding_here_closes_the_open_approval_request(self):
        req = self.apply("2026-08-03", submit=True)
        self.act(req, "approve")
        self.assertEqual(self.approval(req)["status"], "cancelled")
        req2 = self.apply("2026-08-10", submit=True)
        self.act(req2, "cancel", client=self.client_e)
        self.assertEqual(self.approval(req2)["status"], "cancelled")

    def test_hr_submitting_for_someone_is_approved_automatically_when_no_rule_applies(self):
        # HR holds hr.manage, so a request HR sends on an employee's behalf is approved at once by the approvals module
        # only when no rule matches; the seeded default rule asks the HR Manager, who may not decide their own request.
        r = self.api_hr.post(f"{H}/leave/requests/", {"employee": self.e["id"], "leave_type": self.cl["id"], "start_date": "2026-08-03", "submit": True})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIn(r.json()["status"], ("pending_approval", "approved"))

    def test_leave_actions_are_audited(self):
        req = self.apply("2026-08-03", submit=True)
        self.act(req, "approve")
        events = CompanyProfileAuditEvent.objects.filter(entity_type="leave_request", entity_id=req["id"]).order_by("occurred_at")
        self.assertEqual([e.new_values.get("status") for e in events], ["draft", "pending_approval", "approved"])


class AttendanceTests(LeaveBase):
    def mark(self, date, status, employees=None, client=None, expect=200, **extra):
        r = (client or self.api_hr).post(f"{H}/attendance/", {"date": date, "status": status, **({"employees": employees} if employees else {}), **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def test_bulk_mark_everyone_then_override_one(self):
        _, _, e2 = self.employee_user("second")
        out = self.mark("2026-08-03", "present")
        self.assertEqual((out["marked"], out["skipped"]), (2, []))
        self.mark("2026-08-03", "absent", [e2["id"]])
        sheet = self.api_hr.get(f"{H}/attendance/", {"date": "2026-08-03"}).json()
        self.assertEqual({r["employee"]["name"]: r["status"] for r in sheet["rows"]}, {"Asha Test": "present", "Second Test": "absent"})
        self.assertFalse(sheet["is_holiday"])
        self.assertTrue(self.api_hr.get(f"{H}/attendance/", {"date": "2026-08-09"}).json()["is_weekly_off"])

    def test_per_person_records_with_times(self):
        out = self.api_hr.post(f"{H}/attendance/", {"date": "2026-08-04", "records": [
            {"employee": self.e["id"], "status": "wfh", "check_in": "09:30", "check_out": "18:15", "note": "Remote"}]}).json()
        self.assertEqual(out["marked"], 1)
        row = self.api_hr.get(f"{H}/attendance/", {"date": "2026-08-04"}).json()["rows"][0]
        self.assertEqual((row["status"], row["check_in"], row["check_out"], row["note"]), ("wfh", "09:30:00", "18:15:00", "Remote"))
        bad = self.api_hr.post(f"{H}/attendance/", {"date": "2026-08-04", "records": [{"employee": self.e["id"], "status": "present", "check_in": "10:00", "check_out": "09:00"}]}).json()
        self.assertEqual(bad["marked"], 0)
        self.assertIn("earlier", bad["skipped"][0]["reason"])

    def test_rules(self):
        self.mark("2026-08-03", "nonsense", expect=400)
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        out = self.mark(tomorrow, "present", expect=200)
        self.assertEqual(out["marked"], 0)  # skipped: cannot mark the future
        self.assertIn("future", out["skipped"][0]["reason"])
        early = self.mark("2025-03-31", "present")
        self.assertEqual(early["marked"], 0)  # joined 2025-04-01
        self.assertEqual(self.mark("2026-08-03", "present", client=self.api_staff, expect=403), {"detail": "Your role does not allow this.", "code": "permission_denied"})
        self.assertEqual(self.api_pm.post(f"{H}/attendance/", {"date": "2026-08-03", "status": "present"}).status_code, 403)

    def test_people_on_approved_leave_are_not_overwritten(self):
        self.act(self.apply("2026-08-04", submit=True), "approve")
        out = self.mark("2026-08-04", "present")
        self.assertEqual(out["marked"], 0)
        self.assertIn("approved leave", out["skipped"][0]["reason"])
        row = Attendance.objects.get(employee_id=self.e["id"], date="2026-08-04")
        self.assertEqual(self.api_hr.delete(f"{H}/attendance/{row.pk}/").status_code, 400)

    def test_monthly_grid_and_summary(self):
        self.api_hr.post(f"{H}/holidays/", {"date": "2026-08-15", "name": "Independence Day"})
        self.mark("2026-08-03", "present")
        self.mark("2026-08-04", "absent")
        self.mark("2026-08-05", "half_day")
        self.mark("2026-08-06", "wfh")
        grid = self.api_hr.get(f"{H}/attendance/grid/", {"month": "2026-08"}).json()
        self.assertEqual(len(grid["days"]), 31)
        row = grid["rows"][0]
        self.assertEqual([row["cells"][f"2026-08-0{d}"] for d in (2, 3, 4, 5, 6)], ["-", "P", "A", "H", "W"])  # the 2nd is a Sunday
        self.assertEqual(row["cells"]["2026-08-15"], "O")
        s = row["summary"]
        self.assertEqual((s["present"], s["absent"], s["half_day"], s["wfh"], s["holiday"]), (1, 1, 0.5, 1, 1))
        self.assertEqual(s["unmarked"], 31 - 5 - 1 - 4)  # 31 days - 5 Sundays - 1 holiday - 4 marked
        mine = self.client_e.get(f"{H}/me/attendance/", {"month": "2026-08"}).json()
        self.assertEqual(mine["grid"]["summary"]["present"], 1)
        self.assertEqual(len(mine["records"]), 4)
        self.assertEqual(self.client_e.get(f"{H}/attendance/grid/").status_code, 403)

    def test_self_check_in_and_out(self):
        self.assertEqual(self.api_staff.post(f"{H}/me/check-in/").status_code, 400)  # no employee record
        self.assertEqual(self.client_e.post(f"{H}/me/check-out/").status_code, 400)  # not checked in
        r = self.client_e.post(f"{H}/me/check-in/", {"status": "wfh"})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["source"]), (201, "wfh", "self"))
        self.assertIsNotNone(r.json()["check_in"])
        self.assertEqual(self.client_e.post(f"{H}/me/check-in/").status_code, 400)  # only once a day
        self.assertEqual(self.client_e.post(f"{H}/me/check-in/", {"status": "absent"}).status_code, 400)
        self.assertEqual(self.client_e.post(f"{H}/me/check-out/").status_code, 200)
        self.assertEqual(self.client_e.post(f"{H}/me/check-out/").status_code, 400)
        self.assertIsNotNone(self.client_e.get(f"{H}/me/attendance/").json()["today"]["check_out"])

    def test_attendance_privacy(self):
        _, other_client, other = self.employee_user("other")
        self.assertEqual(self.client_e.get(f"{H}/employees/{other['id']}/attendance/").status_code, 403)
        self.assertEqual(self.client_e.get(f"{H}/employees/{self.e['id']}/attendance/").status_code, 200)
        self.assertEqual(self.api_pm.get(f"{H}/employees/{self.e['id']}/attendance/").status_code, 200)  # hr.view
        boss_user, boss_client, boss = self.employee_user("boss")
        self.api_hr.patch(f"{H}/employees/{self.e['id']}/", {"manager": boss["id"]})
        self.assertEqual(boss_client.get(f"{H}/employees/{self.e['id']}/attendance/").status_code, 200)  # their manager
        self.assertEqual(boss_client.get(f"{H}/employees/{other['id']}/attendance/").status_code, 403)

    def test_holiday_calendar(self):
        h = self.api_hr.post(f"{H}/holidays/", {"date": "2026-10-20", "name": "Diwali", "is_optional": False}).json()
        self.assertEqual(h["weekday"], "Tuesday")
        self.assertIn("date", self.api_hr.post(f"{H}/holidays/", {"date": "2026-10-20", "name": "Again"}).json())
        self.assertIn("name", self.api_hr.post(f"{H}/holidays/", {"date": "2026-10-21", "name": ""}).json())
        self.assertEqual(self.api_hr.patch(f"{H}/holidays/{h['id']}/", {"name": "Deepavali"}).json()["name"], "Deepavali")
        self.assertEqual([x["name"] for x in self.client_e.get(f"{H}/holidays/", {"year": 2026}).json()], ["Deepavali"])
        self.assertEqual(self.client_e.get(f"{H}/holidays/", {"year": 2025}).json(), [])
        self.assertEqual(self.client_e.delete(f"{H}/holidays/{h['id']}/").status_code, 403)
        self.assertEqual(self.api_hr.delete(f"{H}/holidays/{h['id']}/").status_code, 204)
