import datetime

from django.test import SimpleTestCase

from apps.accounts.models import Role
from apps.attachments import registry
from apps.company.tests.base import client_for, make_user
from apps.tax import constants as C
from apps.tax.services import filings
from .base import T, TaxBase

UNKNOWN = "00000000-0000-0000-0000-000000000000"

READ_PATHS = ["dashboard/", "options/", "calendar/", "gst/summary/", "gst/sales-register/", "gst/sales-register/export/", "gst/purchase-register/",
              "gst/purchase-register/export/", "gst/reconciliation/", "gst/liability-ledger/", "filings/", "payments/", "tds/ledger/", "tds/credits/",
              "income-tax/"]


class PermissionMatrixTests(TaxBase):
    """tax.view: admin, managing partner, partner, finance manager.  tax.manage: admin, managing partner, finance manager."""

    def setUp(self):
        super().setUp()
        self.clients = {role: client_for(make_user(f"u_{role}", role)) for role in
                        (Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER, Role.HR_MANAGER, Role.PROJECT_MANAGER, Role.EMPLOYEE)}
        self.viewers = {Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER}
        self.managers = {Role.ADMIN, Role.MANAGING_PARTNER, Role.FINANCE_MANAGER}

    def test_every_read_endpoint_follows_tax_view(self):
        for role, client in self.clients.items():
            for path in READ_PATHS:
                code = client.get(f"{T}/{path}").status_code
                self.assertEqual(code, 200 if role in self.viewers else 403, (role, path, code))

    def test_anonymous_users_are_refused(self):
        for path in ("dashboard/", "filings/", "payments/", "calendar/"):
            self.assertIn(self.anon.get(f"{T}/{path}").status_code, (401, 403))
        self.assertIn(self.anon.post(f"{T}/payments/", {}).status_code, (401, 403))

    def test_every_write_endpoint_follows_tax_manage(self):
        f = self.filing("gstr1", "2026-08")
        pay = self.pay()
        credit = self.fin.post(f"{T}/tds/credits/", {"deductor_name": "X", "section": "194J", "date": "2026-08-20", "tds_amount": "10"}).json()
        writes = [
            ("post", "filings/", {"return_type": "gstr1", "period": "2026-09"}), ("post", "filings/generate/", {"financial_year": "2026-27"}),
            ("patch", f"filings/{f['id']}/", {"notes": "x"}), ("post", f"filings/{f['id']}/prepare/", {}),
            ("post", f"filings/{f['id']}/file/", {}), ("post", f"filings/{f['id']}/reopen/", {}), ("post", f"filings/{f['id']}/setoff/", {}),
            ("post", f"filings/{f['id']}/setoff/undo/", {}), ("delete", f"filings/{f['id']}/", None),
            ("post", "payments/", {}), ("patch", f"payments/{pay['id']}/", {"notes": "x"}), ("post", f"payments/{pay['id']}/void/", {}),
            ("post", "tds/credits/", {}), ("patch", f"tds/credits/{credit['id']}/", {"notes": "x"}), ("delete", f"tds/credits/{credit['id']}/", None),
            ("put", "income-tax/?fy=2026-27", {}), ("post", "gst/purchase-register/flag/", {}),
        ]
        for role, client in self.clients.items():
            if role in self.managers:
                continue  # allowed roles are exercised by the other tests; here they must not be refused with 403
            for method, path, body in writes:
                r = getattr(client, method)(f"{T}/{path}", body, format="json") if body is not None else getattr(client, method)(f"{T}/{path}")
                self.assertEqual(r.status_code, 403, (role, method, path, r.status_code))
        for role in self.managers:
            r = self.clients[role].post(f"{T}/filings/generate/", {"financial_year": "2027-28", "types": ["itr5"]}, format="json")
            self.assertIn(r.status_code, (200, 201), role)
        # nothing was changed by the refused calls
        self.assertEqual(self.fin.get(f"{T}/filings/{f['id']}/").json()["notes"], "")
        self.assertEqual(self.fin.get(f"{T}/payments/{pay['id']}/").json()["status"], "recorded")
        self.assertEqual(self.fin.get(f"{T}/tds/credits/{credit['id']}/").status_code, 200)

    def test_attachments_are_registered_for_filings_and_payments(self):
        f = self.filing("gstr1", "2026-08")
        self.assertEqual(registry.target("tax_filing")["view"], "tax.view")
        self.assertEqual(registry.target("tax_filing")["manage"], "tax.manage")
        self.assertEqual(registry.target("tax_payment")["manage"], "tax.manage")
        q = {"entity_type": "tax_filing", "entity_id": f["id"]}
        self.assertEqual(self.clients[Role.PARTNER].get("/api/v1/attachments/", q).status_code, 200)
        self.assertEqual(self.clients[Role.EMPLOYEE].get("/api/v1/attachments/", q).status_code, 403)

    def test_acknowledgement_can_be_attached_by_a_manager_and_not_by_a_viewer(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.company.tests.base import pdf_stub
        f = self.filing("gstr1", "2026-08")

        def upload(client):
            return client.post("/api/v1/attachments/", {"entity_type": "tax_filing", "entity_id": f["id"], "title": "Acknowledgement",
                                                        "file": SimpleUploadedFile("ack.pdf", pdf_stub(), content_type="application/pdf")}, format="multipart")
        self.assertEqual(upload(self.clients[Role.PARTNER]).status_code, 403)
        self.assertEqual(upload(self.fin).status_code, 201)
        self.assertEqual(len(self.fin.get("/api/v1/attachments/", {"entity_type": "tax_filing", "entity_id": f["id"]}).json()), 1)


class DashboardTests(TaxBase):
    def test_dashboard_has_kpis_charts_data_and_upcoming_dates(self):
        self.sales_fixture()
        self.purchases_fixture()
        self.tds_fixture()
        j = self.fin.get(f"{T}/dashboard/").json()
        for key in ("kpis", "returns", "trend", "liability_by_head", "paid_by_type", "upcoming", "calendar_counts", "financial_year"):
            self.assertIn(key, j)
        self.assertEqual(len(j["liability_by_head"]), 3)
        self.assertEqual({p["type"] for p in j["paid_by_type"]}, {"gst", "tds", "advance_tax", "income_tax"})
        k = j["kpis"]
        self.assertEqual((k["itc_available"], k["gst_outstanding"], k["tds_payable"]), ("5040.00", "58780.00", "13500.00"))

    def test_dashboard_on_an_empty_company_is_not_an_error(self):
        r = self.fin.get(f"{T}/dashboard/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["kpis"]["itc_available"], "0.00")
        self.assertEqual(self.fin.get(f"{T}/gst/summary/").status_code, 200)
        self.assertEqual(self.fin.get(f"{T}/gst/liability-ledger/").status_code, 200)

    def test_options_lists_what_the_forms_need(self):
        o = self.fin.get(f"{T}/options/").json()
        self.assertEqual({t["value"] for t in o["return_types"]}, set(C.RETURN_TYPES))
        self.assertEqual([h["value"] for h in next(t for t in o["payment_types"] if t["value"] == "gst")["heads"]],
                         ["cgst", "sgst", "igst", "cess", "interest", "penalty"])
        self.assertEqual(len(o["advance_tax"]), 4)
        self.assertIn(o["current_fy"], o["financial_years"])


class RuleConfigTests(SimpleTestCase):
    def test_due_rules_and_figures_are_defined_for_every_return(self):
        for rt in C.RETURN_TYPES:
            self.assertIn(rt, C.DUE_RULES)
            self.assertIn(rt, C.FIGURE_KEYS)
        self.assertEqual(C.DUE_RULES["gstr1"]["day"], 11)
        self.assertEqual(C.DUE_RULES["gstr3b"]["day"], 20)

    def test_expected_returns_per_year(self):
        got = filings.expected("2026-27")
        self.assertEqual(len(got), 35)
        self.assertEqual(len(filings.expected("2026-27", include_gst=False)), 10)
        self.assertEqual(sum(1 for g in got if g["return_type"] == "tds_26q"), 4)
