import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import access, invitations
from apps.accounts.models import Invitation, MemberProfile
from apps.company.models import CompanyProfileAuditEvent as Event
from apps.company.tests.base import PASSWORD, CompanyTestMixin, client_for, make_user

User = get_user_model()
AUTH = "/api/v1/auth"
STRONG = "Another-Str0ng-Pass!"
ROLES = [r[0] for r in access.ROLES]


def details(**over):
    body = {"full_name": "Meera Nair", "username": "meera", "password": STRONG, "password_confirm": STRONG, "phone": "+91 98765 43210",
            "personal_email": "meera.n@example.com", "designation": "Site Engineer", "department": "Engineering",
            "date_of_birth": "1995-03-14", "address": "12 Lake Road, Chennai", "emergency_name": "Ravi Nair",
            "emergency_phone": "9876500000"}
    body.update(over)
    return body


class InviteBase(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.full_company()
        self.mp = make_user("mp", "managing_partner")
        self.partner = make_user("partner", "partner")
        self.hr = make_user("hrm", "hr_manager")
        self.pm = make_user("pm", "project_manager")
        self.api_mp, self.api_partner, self.api_hr, self.api_pm = (client_for(u) for u in (self.mp, self.partner, self.hr, self.pm))
        self.public = APIClient()

    def invite(self, client=None, expect=201, **over):
        body = {"role": "employee", **over}
        r = (client or self.api_partner).post(f"{AUTH}/invitations/", body)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def accept(self, token, expect=201, **over):
        cache.clear()  # the public endpoints are throttled; only the throttle test counts calls
        r = self.public.post(f"{AUTH}/invitations/accept/", {"token": token, **details(**over)})
        self.assertEqual(r.status_code, expect, r.content)
        return r


class CreationAndRolesTests(InviteBase):
    def test_allowed_roles_table(self):
        everyone_but_admin = [r for r in ROLES if r != "admin"]
        expect = {"admin": ROLES, "managing_partner": everyone_but_admin, "partner": everyone_but_admin,
                  "hr_manager": ["project_manager", "employee"]}
        self.assertEqual({k: list(v) for k, v in access.INVITE_GRANTS.items()}, {k: v for k, v in expect.items()})
        for user, name in ((self.admin, "admin"), (self.mp, "managing_partner"), (self.partner, "partner"), (self.hr, "hr_manager")):
            r = client_for(user).get(f"{AUTH}/invitations/").json()
            self.assertEqual([o["value"] for o in r["allowed_roles"]], expect[name], name)

    def test_every_allowed_role_can_be_invited_and_nothing_else(self):
        for who, client in (("admin", self.api), ("mp", self.api_mp), ("partner", self.api_partner), ("hr", self.api_hr)):
            allowed = [o["value"] for o in client.get(f"{AUTH}/invitations/").json()["allowed_roles"]]
            for role in ROLES:
                r = client.post(f"{AUTH}/invitations/", {"role": role})
                self.assertEqual(r.status_code, 201 if role in allowed else 403, (who, role, r.content))

    def test_hr_cannot_invite_a_partner_finance_manager_or_admin(self):
        for role in ("partner", "managing_partner", "finance_manager", "hr_manager", "admin"):
            self.assertEqual(self.api_hr.post(f"{AUTH}/invitations/", {"role": role}).status_code, 403, role)
        self.assertEqual(Invitation.objects.count(), 0)
        self.assertEqual(self.api_hr.post(f"{AUTH}/invitations/", {"role": "project_manager"}).status_code, 201)

    def test_partner_cannot_invite_an_admin(self):
        for c in (self.api_partner, self.api_mp):
            self.assertEqual(c.post(f"{AUTH}/invitations/", {"role": "admin"}).status_code, 403)
        self.assertEqual(self.api.post(f"{AUTH}/invitations/", {"role": "admin"}).status_code, 201)

    def test_unknown_role_and_bad_input_are_refused(self):
        for over, field in (({"role": "boss"}, "role"), ({"role": ""}, "role"), ({"expires_in_days": 0}, "expires_in_days"),
                            ({"expires_in_days": 31}, "expires_in_days"), ({"expires_in_days": "abc"}, "expires_in_days"),
                            ({"email_hint": "nope"}, "email_hint"), ({"note": "x" * 501}, "note")):
            r = self.api_partner.post(f"{AUTH}/invitations/", {"role": "employee", **over})
            self.assertEqual(r.status_code, 400, over)
            self.assertIn(field, r.json(), over)

    def test_token_is_returned_once_and_only_its_hash_is_stored(self):
        made = self.invite(name_hint="Meera", email_hint="m@example.com", note="Welcome aboard", expires_in_days=10)
        token = made["token"]
        self.assertGreaterEqual(len(token), 40)
        self.assertEqual(made["path"], f"/join/{token}")
        inv = Invitation.objects.get()
        self.assertEqual(inv.token_hash, invitations.hash_token(token))
        self.assertEqual(len(inv.token_hash), 64)
        self.assertNotIn(token, [str(getattr(inv, f.name)) for f in Invitation._meta.fields])
        self.assertEqual((inv.role, inv.name_hint, inv.email_hint, inv.note, inv.created_by), ("employee", "Meera", "m@example.com", "Welcome aboard", self.partner))
        self.assertAlmostEqual((inv.expires_at - inv.created_at).total_seconds(), 10 * 86400, delta=5)

    def test_default_expiry_is_seven_days(self):
        self.invite()
        inv = Invitation.objects.get()
        self.assertAlmostEqual((inv.expires_at - inv.created_at).total_seconds(), 7 * 86400, delta=5)

    def test_listing_never_exposes_tokens_and_filters_by_status(self):
        a, b = self.invite(), self.invite(role="project_manager")
        self.api_partner.post(f"{AUTH}/invitations/{b['id']}/revoke/")
        r = self.api_partner.get(f"{AUTH}/invitations/")
        text = r.content.decode()
        self.assertNotIn(a["token"], text)
        self.assertNotIn(b["token"], text)
        self.assertNotIn("token", {k for row in r.json()["results"] for k in row})
        self.assertNotIn(Invitation.objects.get(pk=a["id"]).token_hash, text)
        by = lambda s: {row["id"] for row in self.api_partner.get(f"{AUTH}/invitations/", {"status": s}).json()["results"]}  # noqa: E731
        self.assertEqual((by("pending"), by("revoked"), by("used"), by("expired")), ({a["id"]}, {b["id"]}, set(), set()))
        self.assertEqual(self.api_partner.get(f"{AUTH}/invitations/", {"status": "x"}).status_code, 400)

    def test_hr_sees_only_invitations_for_roles_it_may_grant(self):
        mine = self.invite(self.api_hr)
        theirs = self.invite(self.api_partner, role="finance_manager")
        ids = {row["id"] for row in self.api_hr.get(f"{AUTH}/invitations/").json()["results"]}
        self.assertIn(mine["id"], ids)
        self.assertNotIn(theirs["id"], ids)
        self.assertEqual(self.api_hr.post(f"{AUTH}/invitations/{theirs['id']}/revoke/").status_code, 403)
        self.assertEqual(self.api_hr.post(f"{AUTH}/invitations/{theirs['id']}/regenerate/").status_code, 403)
        self.assertEqual(self.api_partner.get(f"{AUTH}/invitations/").json()["results"][0]["can_manage"], True)

    def test_employee_finance_and_project_manager_get_403_everywhere(self):
        made = self.invite()
        for client in (self.api_staff, self.api_finance, self.api_pm):
            self.assertEqual(client.get(f"{AUTH}/invitations/").status_code, 403)
            self.assertEqual(client.post(f"{AUTH}/invitations/", {"role": "employee"}).status_code, 403)
            self.assertEqual(client.post(f"{AUTH}/invitations/{made['id']}/revoke/").status_code, 403)
            self.assertEqual(client.post(f"{AUTH}/invitations/{made['id']}/regenerate/").status_code, 403)
        self.assertEqual(self.public.get(f"{AUTH}/invitations/").status_code, 401)
        self.assertEqual(self.public.post(f"{AUTH}/invitations/", {"role": "employee"}).status_code, 401)
        self.assertEqual(self.public.post(f"{AUTH}/invitations/{made['id']}/revoke/").status_code, 401)
        self.assertEqual(Invitation.objects.get().status_at(timezone.now()), "pending")

    def test_the_permission_follows_the_matrix_for_other_roles(self):
        self.assertEqual(self.api_finance.get(f"{AUTH}/invitations/").status_code, 403)
        self.api.put(f"{AUTH}/access/", {"matrix": {"finance_manager": ["company.view", "users.invite"]}, "change_reason": "Test"})
        self.assertEqual(self.api_finance.get(f"{AUTH}/invitations/").status_code, 200)
        # a role with the code but no entry in the grants table may invite employees only
        self.assertEqual(self.api_finance.post(f"{AUTH}/invitations/", {"role": "project_manager"}).status_code, 403)
        self.assertEqual(self.api_finance.post(f"{AUTH}/invitations/", {"role": "employee"}).status_code, 201)
        self.assertEqual(self.api_finance.post(f"{AUTH}/invitations/", {"role": "partner"}).status_code, 403)

    def test_both_partner_roles_and_hr_hold_the_invite_code_by_default(self):
        for role in ("admin", "managing_partner", "partner", "hr_manager"):
            self.assertIn("users.invite", access.codes_for_role(role), role)
        for role in ("finance_manager", "project_manager", "employee"):
            self.assertNotIn("users.invite", access.codes_for_role(role), role)


class StatusTests(InviteBase):
    def lookup(self, token, clear=True):
        if clear:
            cache.clear()
        return self.public.post(f"{AUTH}/invitations/lookup/", {"token": token})

    def test_lookup_returns_the_form_description(self):
        made = self.invite(role="project_manager", name_hint="Meera")
        r = self.lookup(made["token"])
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body["company_name"], body["role"], body["role_label"]), ("Example Ventures", "project_manager", "Project Manager"))
        self.assertEqual(body["name_hint"], "Meera")
        self.assertEqual(body["fields"], invitations.INVITE_FIELDS)
        keys = [f["key"] for f in body["fields"]]
        self.assertEqual(keys[:4], ["full_name", "username", "password", "password_confirm"])
        self.assertTrue(all(set(f) >= {"key", "label", "type", "required", "help"} for f in body["fields"]))
        self.assertNotIn("token", body)

    def test_expiry_uses_the_clock(self):
        made = self.invite(expires_in_days=2)
        start = timezone.now()
        with mock.patch.object(invitations, "now", lambda: start + datetime.timedelta(days=1, hours=23)):
            self.assertEqual(self.lookup(made["token"]).status_code, 200)
        with mock.patch.object(invitations, "now", lambda: start + datetime.timedelta(days=2, minutes=1)):
            self.assertEqual(self.lookup(made["token"]).status_code, 404)
            self.assertEqual(self.accept(made["token"], expect=404).status_code, 404)
            row = self.api_partner.get(f"{AUTH}/invitations/").json()["results"][0]
            self.assertEqual(row["status"], "expired")
            self.assertEqual(len(self.api_partner.get(f"{AUTH}/invitations/", {"status": "expired"}).json()["results"]), 1)
        self.assertFalse(User.objects.filter(username="meera").exists())

    def test_invalid_expired_used_and_revoked_all_give_the_same_answer(self):
        used = self.invite()
        self.accept(used["token"])
        revoked = self.invite()
        self.api_partner.post(f"{AUTH}/invitations/{revoked['id']}/revoke/")
        expired = self.invite()
        Invitation.objects.filter(pk=expired["id"]).update(expires_at=timezone.now() - datetime.timedelta(minutes=1))
        answers = set()
        for token in ("a-token-that-never-existed-0123456789", used["token"], revoked["token"], expired["token"], "", None, 5, "x"):
            r = self.lookup(token)
            self.assertEqual(r.status_code, 404, token)
            answers.add(r.content)
            cache.clear()
            r2 = self.public.post(f"{AUTH}/invitations/accept/", {"token": token, **details(username="zed")})
            self.assertEqual(r2.status_code, 404, token)
            answers.add(r2.content)
        self.assertEqual(len(answers), 1, answers)
        self.assertFalse(User.objects.filter(username="zed").exists())

    def test_lookup_and_accept_need_no_sign_in_but_are_throttled(self):
        made = self.invite()
        cache.clear()
        for _ in range(10):
            self.assertEqual(self.lookup("nothing-here-nothing-here-nothing", clear=False).status_code, 404)
        self.assertEqual(self.lookup(made["token"], clear=False).status_code, 429)
        self.assertEqual(self.public.post(f"{AUTH}/invitations/accept/", {"token": made["token"], **details()}).status_code, 429)
        cache.clear()
        self.assertEqual(self.lookup(made["token"]).status_code, 200)

    def test_revoke_then_the_link_is_dead_and_the_action_is_audited(self):
        made = self.invite()
        r = self.api_partner.post(f"{AUTH}/invitations/{made['id']}/revoke/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["status"], r.json()["revoked_by"]["name"]), ("revoked", "partner"))
        self.assertEqual(self.lookup(made["token"]).status_code, 404)
        self.assertEqual(self.accept(made["token"], expect=404).status_code, 404)
        self.assertEqual(self.api_partner.post(f"{AUTH}/invitations/{made['id']}/revoke/").status_code, 400)
        event = Event.objects.filter(entity_type="invitation", action="deactivated").get()
        self.assertEqual(event.actor_label, "partner")

    def test_regenerate_revokes_and_creates_a_fresh_link_with_the_same_settings(self):
        made = self.invite(role="project_manager", name_hint="Meera", email_hint="m@example.com", note="Hi", expires_in_days=14)
        r = self.api_hr.post(f"{AUTH}/invitations/{made['id']}/regenerate/")
        self.assertEqual(r.status_code, 201, r.content)
        fresh = r.json()
        self.assertNotEqual(fresh["token"], made["token"])
        self.assertNotEqual(fresh["id"], made["id"])
        old, new = Invitation.objects.get(pk=made["id"]), Invitation.objects.get(pk=fresh["id"])
        self.assertEqual(old.status_at(timezone.now()), "revoked")
        self.assertEqual((new.role, new.name_hint, new.email_hint, new.note), ("project_manager", "Meera", "m@example.com", "Hi"))
        self.assertAlmostEqual((new.expires_at - new.created_at).total_seconds(), 14 * 86400, delta=5)
        self.assertEqual(self.lookup(made["token"]).status_code, 404)
        self.assertEqual(self.lookup(fresh["token"]).status_code, 200)
        self.assertEqual(new.created_by, self.hr)
        self.assertEqual(Event.objects.filter(entity_type="invitation", action="created").count(), 2)

    def test_regenerate_works_for_an_expired_link_but_not_a_used_one(self):
        old = self.invite()
        Invitation.objects.filter(pk=old["id"]).update(expires_at=timezone.now() - datetime.timedelta(days=1))
        self.assertEqual(self.api_partner.post(f"{AUTH}/invitations/{old['id']}/regenerate/").status_code, 201)
        used = self.invite()
        self.accept(used["token"])
        self.assertEqual(self.api_partner.post(f"{AUTH}/invitations/{used['id']}/regenerate/").status_code, 400)
        self.assertEqual(self.api_partner.post(f"{AUTH}/invitations/{used['id']}/revoke/").status_code, 400)
        self.assertEqual(self.api_partner.post(f"{AUTH}/invitations/00000000-0000-0000-0000-000000000000/revoke/").status_code, 404)


class AcceptTests(InviteBase):
    def test_accept_creates_the_user_with_the_invited_role_and_profile(self):
        made = self.invite(role="project_manager")
        r = self.accept(made["token"])
        self.assertEqual(r.json()["ok"], True)
        self.assertNotIn(STRONG, r.content.decode())
        user = User.objects.get(username="meera")
        self.assertEqual((user.role, user.first_name, user.last_name, user.email, user.is_active),
                         ("project_manager", "Meera", "Nair", "meera.n@example.com", True))
        self.assertTrue(user.check_password(STRONG))
        self.assertEqual(client_for(user).get(f"{AUTH}/me/").json()["role"], "project_manager")
        p = MemberProfile.objects.get(user=user)
        self.assertEqual((p.designation, p.phone, p.personal_email, p.department, p.date_of_birth, p.address, p.emergency_name,
                          p.emergency_phone),
                         ("Site Engineer", "+91 98765 43210", "meera.n@example.com", "Engineering", datetime.date(1995, 3, 14),
                          "12 Lake Road, Chennai", "Ravi Nair", "9876500000"))
        self.assertEqual((p.invited_by, p.invitation_id), (self.partner, Invitation.objects.get().pk))
        inv = Invitation.objects.get()
        self.assertEqual((inv.used_by, inv.status_at(timezone.now())), (user, "used"))
        signed = APIClient().post(f"{AUTH}/login/", {"username": "meera", "password": STRONG})
        self.assertEqual(signed.status_code, 200)

    def test_the_invitee_cannot_choose_their_own_access_role(self):
        made = self.invite(role="employee")
        self.accept(made["token"], role="admin", access_role="admin", is_superuser=True)
        user = User.objects.get(username="meera")
        self.assertEqual(user.role, "employee")
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_code("users.manage"))
        self.assertEqual(MemberProfile.objects.get(user=user).designation, "Site Engineer")  # their title is free text only

    def test_a_link_works_once(self):
        made = self.invite()
        self.accept(made["token"])
        again = self.accept(made["token"], expect=404, username="meera2")
        self.assertEqual(again.json()["detail"], invitations.NOT_VALID)
        self.assertEqual(User.objects.filter(username__startswith="meera").count(), 1)

    def test_a_failed_attempt_does_not_use_the_link(self):
        made = self.invite()
        self.accept(made["token"], expect=400, personal_email="bad")
        self.assertEqual(self.public.post(f"{AUTH}/invitations/lookup/", {"token": made["token"]}).status_code, 200)
        self.assertFalse(User.objects.filter(username="meera").exists())
        self.assertFalse(MemberProfile.objects.exists())
        self.accept(made["token"])

    def test_required_fields_come_from_the_config_list(self):
        made = self.invite()
        required = [f["key"] for f in invitations.INVITE_FIELDS if f["required"]]
        for key in required:
            r = self.accept(made["token"], expect=400, **{key: ""})
            self.assertIn(key, r.json(), key)
        optional = {f["key"]: "" for f in invitations.INVITE_FIELDS if not f["required"]}
        self.accept(made["token"], **optional)
        self.assertEqual(MemberProfile.objects.get().department, "")

    def test_field_validation(self):
        made = self.invite()
        cases = [({"personal_email": "not-an-email"}, "personal_email"), ({"phone": "abc"}, "phone"), ({"phone": "12"}, "phone"),
                 ({"username": "has space"}, "username"), ({"username": "bad/char"}, "username"),
                 ({"password_confirm": "Different-Pass-123!"}, "password_confirm"), ({"date_of_birth": "14-03-1995"}, "date_of_birth"),
                 ({"date_of_birth": "2999-01-01"}, "date_of_birth"), ({"full_name": "x" * 151}, "full_name"),
                 ({"emergency_phone": "call me"}, "emergency_phone")]
        for over, key in cases:
            r = self.accept(made["token"], expect=400, **over)
            self.assertIn(key, r.json(), over)
        # every problem is reported together
        r = self.accept(made["token"], expect=400, personal_email="bad", phone="x", full_name="")
        self.assertTrue({"personal_email", "phone", "full_name"} <= set(r.json()))

    def test_password_validators_apply(self):
        made = self.invite()
        for weak in ("short1!", "password1234", "12345678901234"):
            r = self.accept(made["token"], expect=400, password=weak, password_confirm=weak)
            self.assertIn("password", r.json(), weak)
        self.assertFalse(User.objects.filter(username="meera").exists())

    def test_duplicate_username_is_refused_case_insensitively(self):
        first, second = self.invite(), self.invite()
        self.accept(first["token"])
        r = self.accept(second["token"], expect=400, username="MEERA", personal_email="other@example.com")
        self.assertIn("already taken", r.json()["username"])
        r = self.accept(second["token"], expect=400, username="staff")
        self.assertIn("username", r.json())
        self.accept(second["token"], username="meera2")

    def test_audit_entries_and_nothing_secret_in_them(self):
        made = self.invite(email_hint="hint@example.com")
        self.accept(made["token"])
        events = Event.objects.filter(entity_type__in=["invitation", "user"]).order_by("occurred_at")
        self.assertEqual([(e.entity_type, e.action, e.actor_label) for e in events],
                         [("invitation", "created", "partner"), ("invitation", "updated", "meera"), ("user", "created", "meera")])
        blob = " ".join(str(e.new_values) + str(e.previous_values) + e.reason for e in events)
        for secret in (made["token"], STRONG, "hint@example.com", Invitation.objects.get().token_hash):
            self.assertNotIn(secret, blob)
        self.assertIn("partner", events[2].reason)
        feed = self.api.get(f"{AUTH}/activity/").json()
        self.assertEqual({r["entity_type"] for r in feed}, {"invitation", "user"})

    def test_employee_record_is_created_with_the_user_for_staff_roles(self):
        from apps.hr.models import Designation, Employee

        made = self.invite(role="employee")
        self.accept(made["token"])
        user = User.objects.get(username="meera")
        e = Employee.objects.get(user=user)
        self.assertEqual((e.first_name, e.last_name, e.phone, e.personal_email, e.date_of_birth, e.emergency_name, e.status),
                         ("Meera", "Nair", "+91 98765 43210", "meera.n@example.com", datetime.date(1995, 3, 14), "Ravi Nair", "active"))
        self.assertEqual(e.designation.name, "Site Engineer")
        self.assertEqual(e.date_of_joining, datetime.date.today())
        self.assertTrue(e.code)
        self.assertEqual(Designation.objects.filter(name="Site Engineer").count(), 1)
        people = client_for(self.hr).get("/api/v1/hr/employees/").json()
        rows = people["results"] if isinstance(people, dict) else people
        self.assertIn("Meera", str(rows))
        self.assertTrue(Event.objects.filter(entity_type="employee", entity_id=e.pk).exists())

    def test_existing_designation_and_department_are_matched_not_duplicated(self):
        from apps.hr.models import Department, Designation, Employee

        self.api_hr.post("/api/v1/hr/departments/", {"name": "Engineering"})
        self.api_hr.post("/api/v1/hr/designations/", {"name": "site engineer"})
        self.accept(self.invite()["token"], department="engineering", designation="Site Engineer")
        e = Employee.objects.get(user__username="meera")
        self.assertEqual((e.department.name, e.designation.name), ("Engineering", "site engineer"))
        self.assertEqual((Department.objects.count(), Designation.objects.count()), (1, 1))

    def test_partners_and_admins_get_no_hr_record(self):
        from apps.hr.models import Employee

        self.accept(self.invite(role="partner")["token"])
        self.assertEqual(User.objects.get(username="meera").role, "partner")
        self.assertFalse(Employee.objects.filter(user__username="meera").exists())
        self.assertTrue(MemberProfile.objects.filter(user__username="meera").exists())

    def test_user_profile_and_employee_are_created_together_or_not_at_all(self):
        from apps.hr.models import Employee
        from apps.hr.services import people

        made = self.invite()
        with mock.patch.object(people, "create", side_effect=RuntimeError("boom")):
            cache.clear()
            with self.assertRaises(RuntimeError):
                self.public.post(f"{AUTH}/invitations/accept/", {"token": made["token"], **details()})
        self.assertFalse(User.objects.filter(username="meera").exists())
        self.assertFalse(MemberProfile.objects.exists())
        self.assertFalse(Employee.objects.exists())
        self.assertEqual(Invitation.objects.get().status_at(timezone.now()), "pending")

    def test_extra_fields_added_to_the_config_are_validated_and_stored(self):
        extra = {"key": "blood_group", "label": "Blood group", "type": "select", "required": True, "options": ["A+", "B+", "O+"], "help": ""}
        with mock.patch.object(invitations, "INVITE_FIELDS", invitations.INVITE_FIELDS + [extra]):
            made = self.invite()
            self.assertIn("blood_group", [f["key"] for f in self.public.post(f"{AUTH}/invitations/lookup/", {"token": made["token"]}).json()["fields"]])
            self.accept(made["token"], expect=400)
            self.accept(made["token"], expect=400, blood_group="Z")
            self.accept(made["token"], blood_group="O+")
        self.assertEqual(MemberProfile.objects.get().extra, {"blood_group": "O+"})
