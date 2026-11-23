import datetime

from django.test import TestCase

from apps.accounts.models import Role
from apps.company.tests.base import CompanyTestMixin, client_for, make_user

C = "/api/v1/crm"
TODAY = datetime.date.today()


class CrmBase(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.partner = make_user("partner", Role.PARTNER)
        self.mp = make_user("mpartner", Role.MANAGING_PARTNER)
        self.pm = make_user("pm", Role.PROJECT_MANAGER)
        self.hr = make_user("hrm", Role.HR_MANAGER)
        self.api_partner = client_for(self.partner)
        self.api_mp = client_for(self.mp)
        self.api_pm = client_for(self.pm)
        self.api_hr = client_for(self.hr)
        self.crm = self.api_partner  # the everyday client: a partner holds crm.manage
        self._stages = None

    # -- builders
    def stages(self):
        if self._stages is None:
            rows = self.crm.get(f"{C}/lookups/").json()["stages"]
            self._stages = {s["name"]: s["id"] for s in rows}
        return self._stages

    def stage(self, name):
        return self.stages()[name]

    def lead(self, client=None, expect=201, **over):
        payload = {"company_name": "Acme Industries", "contact_name": "Ravi Kumar", "source": "website", **over}
        r = (client or self.crm).post(f"{C}/leads/", payload)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def account(self, name="Acme Industries", **over):
        r = self.crm.post(f"{C}/accounts/", {"name": name, **over})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def contact(self, account=None, name="Ravi Kumar", **over):
        payload = {"name": name, **over}
        if account:
            payload["account"] = account["id"]
        r = self.crm.post(f"{C}/contacts/", payload)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def deal(self, account=None, name="ERP rollout", amount="100000", expect=201, **over):
        account = account or self.account()
        payload = {"name": name, "account": account["id"], "amount": amount, **over}
        r = self.crm.post(f"{C}/deals/", payload)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def move(self, deal, stage_name, expect=200, **extra):
        r = self.crm.post(f"{C}/deals/{deal['id']}/move/", {"stage": self.stage(stage_name), **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def activity(self, expect=201, **over):
        payload = {"type": "task", "subject": "Follow up", **over}
        r = self.crm.post(f"{C}/activities/", payload)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()
