import datetime

from django.test import TestCase

from apps.accounts.models import Role
from apps.approvals import api as approvals
from apps.approvals import engine, registry, rules
from apps.company.models import CompanyProfile
from apps.company.tests.base import CompanyTestMixin, client_for, make_user

A = "/api/v1/approvals"
UTC = datetime.timezone.utc


def at(day, hour=9, month=8):
    return datetime.datetime(2026, month, day, hour, 0, tzinfo=UTC)


class ApprovalBase(CompanyTestMixin, TestCase):
    """Users for every role, a fake kind `test_doc` whose callbacks are recorded in `self.calls`."""

    def setUp(self):
        super().setUp()
        self.full_company()
        self.company = CompanyProfile.objects.first()
        self.mp = make_user("mp", Role.MANAGING_PARTNER)
        self.p1 = make_user("p1", Role.PARTNER)
        self.p2 = make_user("p2", Role.PARTNER)
        self.hr = make_user("hr", Role.HR_MANAGER)
        self.pm = make_user("pm", Role.PROJECT_MANAGER)
        self.emp2 = make_user("emp2", Role.STAFF)
        self.c = {u.username: client_for(u) for u in (self.admin, self.admin2, self.finance, self.staff, self.mp, self.p1,
                                                       self.p2, self.hr, self.pm, self.emp2)}
        # Other modules register real handlers for their kinds; these tests exercise the engine with fakes.
        saved = dict(registry._HANDLERS)
        registry._HANDLERS.clear()
        self.addCleanup(lambda: (registry._HANDLERS.clear(), registry._HANDLERS.update(saved)))
        self.calls = []
        self.records = {}
        self.fail_on = None
        self.register("test_doc")
        self.addCleanup(approvals.unregister_handler, "test_doc")
        self.addCleanup(approvals.unregister_handler, "test_auto")

    # -- helpers
    def register(self, kind, **extra):
        def hook(name):
            def fn(req):
                self.calls.append((name, req.source_id))
                if self.fail_on == name:
                    raise RuntimeError("the record is no longer in a state that can be approved")
            return fn
        approvals.register_handler(kind, on_approved=hook("approved"), on_rejected=hook("rejected"), label="Test document",
                                   required_codes=("sales.manage",), on_cancelled=hook("cancelled"),
                                   on_resubmitted=hook("resubmitted"), describe=self.describe, **extra)

    def describe(self, source_id):
        rec = self.records.get(source_id)
        if rec is None:
            return None
        return {"title": rec["title"], "amount": rec.get("amount"), "summary": "From the record", "url": f"/docs/{source_id}",
                "requested_by_ok": lambda user: user.username != "emp2"}

    def rule(self, steps, kind="test_doc", **data):
        """steps: list of role names, or (role, mode, days) tuples."""
        out = []
        for s in steps:
            role, mode, days = (s, "any", None) if isinstance(s, str) else s
            out.append({"approver_type": "role", "role": role, "mode": mode, "escalate_after_days": days})
        return rules.save_rule(self.company, {"kind": kind, "name": data.pop("name", "Test rule"), "steps": out, **data})

    def submit(self, user=None, source="d1", amount=None, now=None, **kw):
        return engine.submit(self.company, kind=kw.pop("kind", "test_doc"), source_id=source, title=kw.pop("title", f"Doc {source}"),
                             amount=amount, requested_by=user or self.staff, now=now, **kw)

    def act(self, who, req, verb="approve", comment="", expect=200):
        r = self.c[who].post(f"{A}/requests/{req.pk}/{verb}/", {"comment": comment})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def reload(self, req):
        req.refresh_from_db()
        return req

    def inbox(self, who, **params):
        r = self.c[who].get(f"{A}/requests/", {"scope": "inbox", **params})
        self.assertEqual(r.status_code, 200, r.content)
        return [x["reference_number"] for x in r.json()["results"]]
