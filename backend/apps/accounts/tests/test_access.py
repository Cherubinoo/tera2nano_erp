from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts import access
from apps.accounts.models import LoginEvent, RolePermission
from apps.company.models import CompanyProfileAuditEvent as Event
from apps.company.tests.base import API, PASSWORD, CompanyTestMixin, client_for, make_user

User = get_user_model()
AUTH = "/api/v1/auth"
STRONG = "Another-Str0ng-Pass!"


def sign_in(username, password):
    """Sign in through the API. The login rate limit (10 a minute) is cleared first, as these tests sign in often."""
    cache.clear()
    return APIClient().post(f"{AUTH}/login/", {"username": username, "password": password})


class RoleTests(CompanyTestMixin, TestCase):
    def test_the_seven_roles_exist(self):
        self.assertEqual([r[0] for r in access.ROLES], [
            "admin", "managing_partner", "partner", "finance_manager", "hr_manager", "project_manager", "employee"])
        self.assertEqual(access.ROLES[1][1], "Managing Partner")

    def test_old_names_still_work_and_default_is_employee(self):
        from apps.accounts.models import Role
        self.assertEqual((Role.FINANCE, Role.STAFF), ("finance_manager", "employee"))
        self.assertEqual(User.objects.create_user("new", password=PASSWORD).role, "employee")

    def test_default_powers_per_role(self):
        expect = {  # is_company_admin, is_finance
            "admin": (True, True), "managing_partner": (True, True), "partner": (False, False),
            "finance_manager": (False, True), "hr_manager": (False, False), "project_manager": (False, False),
            "employee": (False, False),
        }
        for role, (admin, finance) in expect.items():
            me = client_for(make_user(f"u_{role}", role)).get(f"{AUTH}/me/").json()
            self.assertEqual((me["is_company_admin"], me["is_finance"]), (admin, finance), role)
            self.assertEqual(me["role"], role)

    def test_me_lists_permissions_and_role_label(self):
        me = self.api_finance.get(f"{AUTH}/me/").json()
        self.assertEqual(me["role_label"], "Finance Manager")
        self.assertEqual(set(me["permissions"]), set(access.DEFAULT_MATRIX["finance_manager"]))
        self.assertEqual(set(self.api.get(f"{AUTH}/me/").json()["permissions"]), set(access.CODES))

    def test_superuser_and_inactive(self):
        su = User.objects.create_superuser("root", "r@example.com", PASSWORD)
        self.assertEqual(su.role, "admin")
        self.assertTrue(su.has_code("users.manage"))
        self.staff.is_active = False
        self.assertFalse(self.staff.has_code("company.view"))


class MatrixTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def matrix(self):
        return self.api.get(f"{AUTH}/access/").json()

    def save(self, matrix, reason="Testing", client=None):
        return (client or self.api).put(f"{AUTH}/access/", {"matrix": matrix, "change_reason": reason})

    def test_matrix_describes_roles_permissions_and_defaults(self):
        m = self.matrix()
        self.assertEqual(len(m["roles"]), 7)
        self.assertTrue(m["roles"][0]["locked"])
        self.assertEqual({p["code"] for p in m["permissions"]}, set(access.CODES))
        self.assertEqual(set(m["matrix"]["admin"]), set(access.CODES))
        self.assertEqual(m["matrix"]["employee"], m["defaults"]["employee"])

    def test_only_users_manage_can_see_or_change_it(self):
        for client in (self.api_staff, self.api_finance):
            self.assertEqual(client.get(f"{AUTH}/access/").status_code, 403)
            self.assertEqual(self.save({"employee": []}, client=client).status_code, 403)
        self.assertEqual(self.anon.get(f"{AUTH}/access/").status_code, 401)

    def test_change_takes_effect_at_once_and_is_audited(self):
        self.assertEqual(self.api_staff.get(f"{API}/profile/").status_code, 200)
        r = self.save({"employee": ["documents.issue"]})  # drops company.view
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn("company.view", self.matrix()["matrix"]["employee"])
        self.assertEqual(set(self.api_staff.get(f"{AUTH}/me/").json()["permissions"]), {"documents.issue"})
        event = Event.objects.filter(entity_type="access_matrix").latest("occurred_at")
        self.assertEqual((event.action, event.reason, event.actor_label), ("updated", "Testing", "admin"))
        self.assertIn("employee", event.changed_fields)
        self.assertNotIn("partner", event.changed_fields)

    def test_removing_a_permission_blocks_the_action(self):
        self.full_company()
        self.assertEqual(self.issue("business_letter", client=self.api_staff).status_code, 201)
        self.save({"employee": ["company.view"]})
        r = self.issue("business_letter", client=self.api_staff)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.issue("business_letter", client=self.api_finance).status_code, 201)

    def test_granting_a_permission_works(self):
        self.assertEqual(self.api_staff.get(f"{API}/audit-history/").status_code, 403)
        self.save({"employee": ["company.view", "documents.issue", "audit.view"]})
        self.assertEqual(self.api_staff.get(f"{API}/audit-history/").status_code, 200)

    def test_confidential_documents_follow_the_matrix(self):
        doc = self.upload_document(access_classification="confidential").json()
        self.assertEqual(self.api_staff.get(f"{API}/documents/{doc['id']}/").status_code, 404)
        self.save({"employee": ["company.view", "documents.view_confidential"]})
        self.assertEqual(self.api_staff.get(f"{API}/documents/{doc['id']}/").status_code, 200)

    def test_admin_cannot_be_reduced(self):
        self.save({"admin": []})
        self.assertEqual(set(self.matrix()["matrix"]["admin"]), set(access.CODES))
        self.assertFalse(RolePermission.objects.filter(role="admin").exists())
        self.assertEqual(self.api.get(f"{AUTH}/access/").status_code, 200)

    def test_validation(self):
        bad = [({"nobody": []}, "Unknown roles"), ({"employee": ["made.up"]}, "unknown permissions"),
               ({"employee": "company.view"}, "list"), ("x", "matrix")]
        for matrix, word in bad:
            r = self.save(matrix)
            self.assertEqual(r.status_code, 400, matrix)
            self.assertIn(word, str(r.json()))
        self.assertEqual(self.api.put(f"{AUTH}/access/", {"matrix": {"employee": []}}).status_code, 400)  # no reason

    def test_reset_returns_to_the_defaults(self):
        self.save({"employee": []})
        self.assertEqual(self.api.post(f"{AUTH}/access/reset/", {}).status_code, 400)
        r = self.api.post(f"{AUTH}/access/reset/", {"change_reason": "Back to normal"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["matrix"]["employee"], r.json()["defaults"]["employee"])
        self.assertFalse(RolePermission.objects.exists())

    def test_the_second_approver_rule_follows_the_roles_holding_finance_manage(self):
        self.save({"hr_manager": ["company.view", "finance.manage"]})
        self.assertIn("hr_manager", access.roles_with("finance.manage"))
        self.assertNotIn("employee", access.roles_with("finance.manage"))


class UserManagementTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.start_company()

    def create(self, **over):
        payload = dict(username="meera", first_name="Meera", last_name="Nair", email="meera@example.com",
                       role="hr_manager", password=STRONG)
        payload.update(over)
        return self.api.post(f"{AUTH}/users/", payload)

    def test_create_list_and_sign_in(self):
        r = self.create()
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((body["username"], body["role"], body["role_label"], body["is_active"]),
                         ("meera", "hr_manager", "HR Manager", True))
        self.assertNotIn("password", body)
        rows = self.api.get(f"{AUTH}/users/").json()
        self.assertIn("meera", [u["username"] for u in rows])
        self.assertEqual(sign_in("meera", STRONG).status_code, 200)
        event = Event.objects.filter(entity_type="user", action="created").get()
        self.assertEqual(event.new_values["role"], "hr_manager")
        self.assertEqual(event.new_values["email"], "[redacted]")
        self.assertNotIn(STRONG, str(event.new_values))

    def test_only_users_manage_may_manage_users(self):
        for client in (self.api_staff, self.api_finance):
            self.assertEqual(client.get(f"{AUTH}/users/").status_code, 403)
            self.assertEqual(client.post(f"{AUTH}/users/", {"username": "x"}).status_code, 403)

    def test_validation(self):
        self.create()
        for over, field in ((dict(username=""), "username"), (dict(username="has space"), "username"),
                            (dict(username="MEERA"), "username"), (dict(username="n2", role="boss"), "role"),
                            (dict(username="n3", email="not-an-email"), "email"), (dict(username="n4", password="short"), "password"),
                            (dict(username="n5", password="password1234"), "password"), (dict(username="n6", password=""), "password")):
            r = self.create(**over)
            self.assertEqual(r.status_code, 400, over)
            self.assertIn(field, r.json(), over)
        self.assertEqual(User.objects.filter(username__startswith="n").count(), 0)

    def test_change_role_name_and_email(self):
        u = self.create().json()
        r = self.api.patch(f"{AUTH}/users/{u['id']}/", {"role": "project_manager", "first_name": "Meera K", "change_reason": "Moved"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["role"], r.json()["first_name"]), ("project_manager", "Meera K"))
        event = Event.objects.filter(entity_type="user", action="updated").latest("occurred_at")
        self.assertEqual(set(event.changed_fields), {"role", "first_name"})
        self.assertEqual((event.previous_values["role"], event.reason), ("hr_manager", "Moved"))
        self.assertEqual(self.api.patch(f"{AUTH}/users/{u['id']}/", {"role": "boss"}).status_code, 400)
        self.assertEqual(self.api.patch(f"{AUTH}/users/{u['id']}/", {"email": "bad"}).status_code, 400)
        self.assertEqual(self.api.patch(f"{AUTH}/users/00000000-0000-0000-0000-000000000000/", {}).status_code, 404)

    def test_new_role_takes_effect_on_the_next_request(self):
        u = make_user("shift", "employee")
        client = client_for(u)
        self.assertEqual(client.get(f"{AUTH}/users/").status_code, 403)
        self.api.patch(f"{AUTH}/users/{u.id}/", {"role": "admin"})
        u.refresh_from_db()
        self.assertEqual(client_for(u).get(f"{AUTH}/users/").status_code, 200)

    def test_switching_off_signs_the_user_out_and_blocks_login(self):
        u = self.create().json()
        login = sign_in("meera", STRONG).json()
        self.assertTrue(Token.objects.filter(key=login["token"]).exists())
        r = self.api.patch(f"{AUTH}/users/{u['id']}/", {"is_active": False})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Token.objects.filter(key=login["token"]).exists())
        self.assertEqual(sign_in("meera", STRONG).status_code, 400)
        self.assertTrue(Event.objects.filter(entity_type="user", action="deactivated").exists())
        self.api.patch(f"{AUTH}/users/{u['id']}/", {"is_active": True})
        self.assertEqual(sign_in("meera", STRONG).status_code, 200)
        self.assertTrue(Event.objects.filter(entity_type="user", action="activated").exists())
        self.assertEqual(self.api.patch(f"{AUTH}/users/{u['id']}/", {"is_active": "no"}).status_code, 400)

    def test_you_cannot_lock_yourself_or_the_last_administrator_out(self):
        me = self.api.get(f"{AUTH}/me/").json()["id"]
        self.assertEqual(self.api.patch(f"{AUTH}/users/{me}/", {"role": "employee"}).status_code, 400)
        self.assertEqual(self.api.patch(f"{AUTH}/users/{me}/", {"is_active": False}).status_code, 400)
        # admin2 may act on admin, but not once admin is the only one left
        self.assertEqual(self.api_admin2.patch(f"{AUTH}/users/{me}/", {"role": "employee"}).status_code, 200)
        self.assertEqual(self.api_admin2.patch(f"{AUTH}/users/{self.admin2.id}/", {"role": "employee"}).status_code, 400)
        only = self.api_admin2.patch(f"{AUTH}/users/{me}/", {"role": "admin"})
        self.assertEqual(only.status_code, 200)

    def test_the_last_administrator_cannot_be_switched_off_or_demoted_by_a_managing_user(self):
        self.save_matrix_for_managing_partner()
        mp = make_user("mp", "managing_partner")
        c = client_for(mp)
        for admin in (self.admin, self.admin2):
            self.assertEqual(c.patch(f"{AUTH}/users/{admin.id}/", {"is_active": False}).status_code, 200
                             if admin is self.admin else 400)
        self.assertTrue(User.objects.get(pk=self.admin2.pk).is_active)

    def save_matrix_for_managing_partner(self):
        self.api.put(f"{AUTH}/access/", {"matrix": {"managing_partner": ["company.view", "users.manage"]}, "change_reason": "Test"})

    def test_set_password(self):
        u = self.create().json()
        login = sign_in("meera", STRONG).json()
        self.assertEqual(self.api.post(f"{AUTH}/users/{u['id']}/set-password/", {"password": "short"}).status_code, 400)
        r = self.api.post(f"{AUTH}/users/{u['id']}/set-password/", {"password": "Brand-New-Passw0rd!"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Token.objects.filter(key=login["token"]).exists())
        self.assertEqual(sign_in("meera", STRONG).status_code, 400)
        self.assertEqual(sign_in("meera", "Brand-New-Passw0rd!").status_code, 200)
        event = Event.objects.filter(entity_type="user").latest("occurred_at")
        self.assertNotIn("Brand-New", str(event.new_values) + str(event.previous_values) + event.reason)


class LoginHistoryTests(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.start_company()

    def history(self, client=None, **params):
        r = (client or self.api).get(f"{AUTH}/login-history/", params)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_success_failure_and_logout_are_recorded(self):
        c = APIClient()
        c.post(f"{AUTH}/login/", {"username": "staff", "password": "wrong"}, HTTP_USER_AGENT="TestBrowser/1.0")
        ok = c.post(f"{AUTH}/login/", {"username": "staff", "password": PASSWORD}, HTTP_USER_AGENT="TestBrowser/1.0").json()
        c.credentials(HTTP_AUTHORIZATION=f"Token {ok['token']}")
        c.post(f"{AUTH}/logout/")
        rows = self.history(user="staff")
        self.assertEqual([r["outcome"] for r in rows], ["logout", "success", "failed"])
        self.assertEqual(rows[0]["outcome_label"], "Signed out")
        self.assertEqual((rows[1]["username"], rows[1]["user_agent"]), ("staff", "TestBrowser/1.0"))
        self.assertTrue(rows[1]["ip_address"])
        self.assertIsNotNone(User.objects.get(username="staff").last_login)

    def test_unknown_names_are_not_stored(self):
        sign_in("My-Secret-Password-123", "x")
        rows = self.history()
        self.assertEqual(rows[0]["username"], "(unknown user)")
        self.assertEqual(rows[0]["outcome"], "failed")
        self.assertFalse(LoginEvent.objects.filter(username__contains="Secret").exists())

    def test_switched_off_account_is_recorded_as_such(self):
        self.staff.is_active = False
        self.staff.save()
        sign_in("staff", PASSWORD)
        self.assertEqual(self.history(outcome="inactive")[0]["username"], "staff")

    def test_filters_and_limit(self):
        for _ in range(3):
            sign_in("finance", PASSWORD)
        sign_in("staff", PASSWORD)
        self.assertEqual(len(self.history(user="finance")), 3)
        self.assertEqual(len(self.history(user=str(self.staff.id))), 1)
        self.assertEqual(len(self.history(limit=2)), 2)
        self.assertEqual(self.api.get(f"{AUTH}/login-history/", {"limit": "x"}).status_code, 400)

    def test_visibility(self):
        self.assertEqual(self.api_staff.get(f"{AUTH}/login-history/").status_code, 403)
        self.assertEqual(self.api_finance.get(f"{AUTH}/activity/").status_code, 403)
        mp = client_for(make_user("mp", "managing_partner"))
        self.assertEqual(mp.get(f"{AUTH}/login-history/").status_code, 200)
        self.assertEqual(mp.get(f"{AUTH}/users/").status_code, 403)  # may watch, not manage
        self.assertEqual(self.anon.get(f"{AUTH}/login-history/").status_code, 401)

    def test_activity_lists_user_and_permission_changes(self):
        u = self.api.post(f"{AUTH}/users/", dict(username="meera", role="partner", password=STRONG, first_name="M", last_name="N")).json()
        self.api.patch(f"{AUTH}/users/{u['id']}/", {"role": "employee"})
        self.api.put(f"{AUTH}/access/", {"matrix": {"employee": ["company.view"]}, "change_reason": "Tighten"})
        rows = self.api.get(f"{AUTH}/activity/").json()
        self.assertEqual([(r["entity_type"], r["action"]) for r in rows],
                         [("access_matrix", "updated"), ("user", "updated"), ("user", "created")])
        self.assertEqual(rows[0]["reason"], "Tighten")


class RoleMigrationTests(TestCase):
    def test_first_version_roles_carry_over(self):
        import importlib
        from django.apps import apps as django_apps

        migration = importlib.import_module("apps.accounts.migrations.0003_rename_first_roles")
        old_finance = User.objects.create_user("oldfin", password=PASSWORD)
        old_staff = User.objects.create_user("oldstaff", password=PASSWORD)
        old_admin = User.objects.create_user("oldadmin", password=PASSWORD, role="admin")
        User.objects.filter(pk=old_finance.pk).update(role="finance")
        User.objects.filter(pk=old_staff.pk).update(role="staff")
        migration.forwards(django_apps, None)
        self.assertEqual(User.objects.get(pk=old_finance.pk).role, "finance_manager")
        self.assertEqual(User.objects.get(pk=old_staff.pk).role, "employee")
        self.assertEqual(User.objects.get(pk=old_admin.pk).role, "admin")
        migration.backwards(django_apps, None)
        self.assertEqual(User.objects.get(pk=old_finance.pk).role, "finance")
        self.assertEqual(User.objects.get(pk=old_staff.pk).role, "staff")
