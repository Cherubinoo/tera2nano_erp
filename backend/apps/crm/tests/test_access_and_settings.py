import csv
import io

from apps.company.models import CompanyProfileAuditEvent
from apps.crm.models import Account, Contact, Deal, LeadSource, PipelineStage

from .base import C, CrmBase

Event = CompanyProfileAuditEvent
NIL = "00000000-0000-0000-0000-000000000000"

READ_URLS = ["lookups/", "dashboard/", "leads/", "accounts/", "contacts/", "deals/", "deals/board/", "activities/", "products/",
             "settings/stages/", "settings/sources/", "settings/lost-reasons/", "reports/summary/", "reports/leads.csv",
             "reports/deals.csv", "reports/activities.csv", "duplicates/"]


class PermissionTests(CrmBase):
    def test_roles_that_may_view(self):
        for name, client in (("admin", self.api), ("partner", self.api_partner), ("managing partner", self.api_mp), ("project manager", self.api_pm)):
            for url in READ_URLS:
                r = client.get(f"{C}/{url}")
                self.assertEqual(r.status_code, 200, f"{name} {url} {r.content[:200]}")

    def test_roles_without_crm_access_are_refused_everywhere(self):
        for name, client in (("employee", self.api_staff), ("finance", self.api_finance), ("hr", self.api_hr)):
            for url in READ_URLS:
                self.assertEqual(client.get(f"{C}/{url}").status_code, 403, f"{name} {url}")
            self.assertEqual(client.post(f"{C}/leads/", {"company_name": "X"}).status_code, 403)
            self.assertEqual(client.post(f"{C}/deals/", {"name": "X"}).status_code, 403)

    def test_anonymous_is_refused(self):
        self.assertEqual(self.anon.get(f"{C}/leads/").status_code, 401)
        self.assertEqual(self.anon.post(f"{C}/leads/", {}).status_code, 401)

    def test_view_only_role_cannot_change_anything(self):
        lead, acct = self.lead(), self.account()
        contact, deal = self.contact(acct), self.deal(acct)
        act = self.activity(lead=lead["id"])
        stage = self.stage("Negotiation")
        pm = self.api_pm
        writes = [
            ("post", "leads/", {"company_name": "N"}), ("patch", f"leads/{lead['id']}/", {"notes": "x"}), ("delete", f"leads/{lead['id']}/", None),
            ("post", f"leads/{lead['id']}/convert/", {}), ("post", "leads/bulk/", {"ids": [lead["id"]], "action": "delete"}),
            ("post", "leads/import/", {"csv": "company_name\nX\n"}),
            ("post", "accounts/", {"name": "N"}), ("patch", f"accounts/{acct['id']}/", {"notes": "x"}), ("delete", f"accounts/{acct['id']}/", None),
            ("post", "contacts/", {"name": "N"}), ("patch", f"contacts/{contact['id']}/", {"notes": "x"}), ("delete", f"contacts/{contact['id']}/", None),
            ("post", "deals/", {"name": "N", "account": acct["id"]}), ("patch", f"deals/{deal['id']}/", {"notes": "x"}),
            ("delete", f"deals/{deal['id']}/", None), ("post", f"deals/{deal['id']}/move/", {"stage": stage}),
            ("put", f"deals/{deal['id']}/items/", {"items": []}), ("post", f"deals/{deal['id']}/customer/", {}),
            ("post", "activities/", {"subject": "x", "lead": lead["id"]}), ("patch", f"activities/{act['id']}/", {"subject": "y"}),
            ("post", f"activities/{act['id']}/complete/", {}), ("delete", f"activities/{act['id']}/", None),
            ("post", "products/", {"name": "P"}), ("post", "settings/stages/", {"name": "S"}),
            ("post", "settings/sources/", {"name": "S"}), ("post", "settings/lost-reasons/", {"name": "S"}),
            ("post", "settings/stages/reorder/", {"ids": []}),
        ]
        for method, url, body in writes:
            r = getattr(pm, method)(f"{C}/{url}", body) if body is not None else getattr(pm, method)(f"{C}/{url}")
            self.assertEqual(r.status_code, 403, f"{method} {url}")
        self.assertEqual(Deal.objects.count(), 1)

    def test_partner_and_managing_partner_may_write(self):
        for client in (self.api_partner, self.api_mp):
            self.assertEqual(client.post(f"{C}/leads/", {"company_name": f"By {id(client)}"}).status_code, 201)
        self.assertEqual(self.api_partner.get(f"{C}/lookups/").json()["can_manage"], True)
        self.assertEqual(self.api_pm.get(f"{C}/lookups/").json()["can_manage"], False)

    def test_owner_choices_only_list_people_who_can_use_the_crm(self):
        names = {o["name"] for o in self.crm.get(f"{C}/lookups/").json()["owners"]}
        self.assertIn("partner", names)
        self.assertNotIn("staff", names)
        self.assertNotIn("finance", names)
        r = self.crm.post(f"{C}/leads/", {"company_name": "Owned", "owner": str(self.pm.id)})
        self.assertEqual(r.json()["owner"]["id"], str(self.pm.id))


class NotFoundTests(CrmBase):
    def test_unknown_ids_give_404_not_500(self):
        for url in ("leads", "accounts", "contacts", "deals", "activities", "products"):
            self.assertEqual(self.crm.get(f"{C}/{url}/{NIL}/").status_code, 404 if url != "products" else 405, url)
        for method, url in (("patch", f"leads/{NIL}/"), ("delete", f"leads/{NIL}/"), ("get", f"leads/{NIL}/timeline/"),
                            ("post", f"leads/{NIL}/convert/"), ("patch", f"accounts/{NIL}/"), ("delete", f"accounts/{NIL}/"),
                            ("get", f"accounts/{NIL}/timeline/"), ("patch", f"contacts/{NIL}/"), ("delete", f"contacts/{NIL}/"),
                            ("get", f"contacts/{NIL}/timeline/"), ("patch", f"deals/{NIL}/"), ("delete", f"deals/{NIL}/"),
                            ("post", f"deals/{NIL}/move/"), ("put", f"deals/{NIL}/items/"), ("post", f"deals/{NIL}/customer/"),
                            ("get", f"deals/{NIL}/timeline/"), ("patch", f"activities/{NIL}/"), ("delete", f"activities/{NIL}/"),
                            ("post", f"activities/{NIL}/complete/"), ("post", f"activities/{NIL}/reopen/"), ("patch", f"products/{NIL}/"),
                            ("delete", f"products/{NIL}/"), ("patch", f"settings/stages/{NIL}/"), ("delete", f"settings/stages/{NIL}/"),
                            ("patch", f"settings/sources/{NIL}/"), ("patch", f"settings/lost-reasons/{NIL}/"), ("get", "reports/nothing.csv")):
            r = getattr(self.crm, method)(f"{C}/{url}", {} if method != "get" and method != "delete" else None) if method in ("post", "patch", "put") \
                else getattr(self.crm, method)(f"{C}/{url}")
            self.assertEqual(r.status_code, 404, f"{method} {url}")

    def test_malformed_ids_in_the_body_are_400(self):
        acct = self.account()
        self.assertEqual(self.crm.post(f"{C}/deals/", {"name": "x", "account": "not-a-uuid"}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/deals/", {"name": "x", "account": NIL}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/contacts/", {"name": "x", "account": NIL}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/deals/", {"name": "x", "account": acct["id"], "owner": "zzz"}).status_code, 400)


class AccountContactTests(CrmBase):
    def test_account_validation_and_unique_name(self):
        self.account("Dup Co")
        self.assertEqual(self.crm.post(f"{C}/accounts/", {"name": "dup co"}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/accounts/", {"name": ""}).status_code, 400)
        r = self.crm.post(f"{C}/accounts/", {"name": "Bad GST", "gstin": "12345"})
        self.assertIn("gstin", r.json())
        self.assertEqual(self.crm.post(f"{C}/accounts/", {"name": "Bad mail", "email": "x"}).status_code, 400)

    def test_account_detail_shows_contacts_deals_and_counts(self):
        acct = self.account("Detail Co")
        self.contact(acct, "Primary One", is_primary=True)
        second = self.contact(acct, "Primary Two", is_primary=True)  # only one contact can be primary
        d = self.deal(acct, "Open", "1000")
        self.move(self.deal(acct, "Won", "9000"), "Closed Won")
        out = self.crm.get(f"{C}/accounts/{acct['id']}/").json()
        self.assertEqual([c["name"] for c in out["contacts"] if c["is_primary"]], [second["name"]])
        self.assertEqual((out["contacts"] and len(out["contacts"]), len(out["deals"]), out["open_deals"], out["open_value"], out["won_deals"]),
                         (2, 2, 1, "1000.00", 1))
        self.assertEqual(self.crm.get(f"{C}/deals/", {"account": acct["id"]}).json()["count"], 2)
        listing = self.crm.get(f"{C}/accounts/", {"q": "detail"}).json()["results"][0]
        self.assertEqual((listing["contacts"], listing["open_deals"]), (2, 1))
        self.assertEqual(self.crm.get(f"{C}/accounts/", {"customer": "yes"}).json()["count"], 1)
        self.assertEqual(d["account"]["name"], "Detail Co")

    def test_account_with_deals_cannot_be_deleted_or_switched_off_while_open(self):
        acct = self.account("Busy Co")
        deal = self.deal(acct)
        self.assertEqual(self.crm.delete(f"{C}/accounts/{acct['id']}/").status_code, 400)
        self.assertEqual(self.crm.patch(f"{C}/accounts/{acct['id']}/", {"is_active": False}).status_code, 400)
        self.crm.delete(f"{C}/deals/{deal['id']}/")
        self.assertEqual(self.crm.patch(f"{C}/accounts/{acct['id']}/", {"is_active": False}).status_code, 200)
        self.assertEqual(self.crm.get(f"{C}/accounts/").json()["count"], 0)
        self.assertEqual(self.crm.get(f"{C}/accounts/", {"active": "false"}).json()["count"], 1)
        self.assertEqual(self.crm.post(f"{C}/deals/", {"name": "x", "account": acct["id"]}).status_code, 400)

    def test_contact_crud_search_and_audit(self):
        acct = self.account("People Co")
        c = self.contact(acct, "Nita Shah", designation="CTO", email="nita@people.in", phone="9000000001")
        self.assertEqual(self.crm.get(f"{C}/contacts/", {"q": "people"}).json()["count"], 1)  # found by account name too
        out = self.crm.patch(f"{C}/contacts/{c['id']}/", {"designation": "CEO"}).json()
        self.assertEqual(out["designation"], "CEO")
        self.assertEqual(self.crm.patch(f"{C}/contacts/{c['id']}/", {"name": ""}).status_code, 400)
        self.assertEqual(self.crm.post(f"{C}/contacts/", {"name": "N", "email": "bad"}).status_code, 400)
        self.assertEqual(Event.objects.filter(entity_type="crm_contact", entity_id=c["id"]).count(), 2)
        blob = str([e.new_values for e in Event.objects.filter(entity_type="crm_contact")])
        self.assertNotIn("nita@people.in", blob)
        self.assertEqual(self.crm.delete(f"{C}/contacts/{c['id']}/").status_code, 204)
        self.assertEqual(Contact.objects.count(), 0)
        self.assertEqual(Account.objects.count(), 1)

    def test_account_and_contact_changes_are_audited(self):
        acct = self.account("Audit Co")
        self.crm.patch(f"{C}/accounts/{acct['id']}/", {"city": "Chennai"})
        events = Event.objects.filter(entity_type="crm_account", entity_id=acct["id"])
        self.assertEqual(sorted(e.action for e in events), ["created", "updated"])
        self.assertEqual(events.get(action="updated").new_values, {"city": "Chennai"})


class SettingsTests(CrmBase):
    def test_default_stages_sources_and_reasons_are_seeded_once(self):
        stages = self.crm.get(f"{C}/settings/stages/").json()
        self.assertEqual([(s["name"], s["probability"], s["kind"]) for s in stages],
                         [("Qualification", 10, "open"), ("Needs Analysis", 25, "open"), ("Proposal/Quotation", 50, "open"),
                          ("Negotiation", 75, "open"), ("Closed Won", 100, "won"), ("Closed Lost", 0, "lost")])
        self.crm.get(f"{C}/settings/stages/")
        self.assertEqual(PipelineStage.objects.count(), 6)
        self.assertEqual([s["key"] for s in self.crm.get(f"{C}/settings/sources/").json()],
                         ["website", "referral", "cold_call", "event", "social", "partner", "other"])
        self.assertEqual(len(self.crm.get(f"{C}/settings/lost-reasons/").json()), 6)

    def test_stage_crud_rules(self):
        r = self.crm.post(f"{C}/settings/stages/", {"name": "Demo", "probability": 40, "colour": "#112233"})
        self.assertEqual(r.status_code, 201, r.content)
        demo = r.json()
        names = [s["name"] for s in self.crm.get(f"{C}/settings/stages/").json()]
        self.assertEqual(names.index("Demo"), 4)  # new open stages go before Closed Won / Closed Lost
        for bad, field in (({"name": ""}, "name"), ({"name": "demo"}, "name"), ({"name": "X", "probability": 101}, "probability"),
                           ({"name": "X", "colour": "red"}, "colour"), ({"name": "X", "kind": "won"}, "kind"),
                           ({"name": "X", "kind": "sideways"}, "kind")):
            r = self.crm.post(f"{C}/settings/stages/", bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json())
        self.assertEqual(self.crm.patch(f"{C}/settings/stages/{demo['id']}/", {"probability": 45, "colour": "#445566"}).json()["probability"], 45)
        deal = self.deal()
        self.move(deal, "Qualification")
        d2 = self.crm.post(f"{C}/deals/{deal['id']}/move/", {"stage": demo["id"]}).json()
        self.assertEqual(d2["probability"], 45)
        self.assertEqual(self.crm.delete(f"{C}/settings/stages/{demo['id']}/").status_code, 400)  # in use
        self.assertEqual(self.crm.patch(f"{C}/settings/stages/{demo['id']}/", {"is_active": False}).status_code, 400)  # open deals inside
        self.move(deal, "Qualification")
        self.assertEqual(self.crm.patch(f"{C}/settings/stages/{demo['id']}/", {"is_active": False}).status_code, 200)
        self.assertEqual(self.crm.delete(f"{C}/settings/stages/{demo['id']}/").status_code, 400)  # history still points at it
        won = self.stage("Closed Won")
        self.assertEqual(self.crm.patch(f"{C}/settings/stages/{won}/", {"probability": 90}).status_code, 400)
        self.assertEqual(self.crm.patch(f"{C}/settings/stages/{won}/", {"kind": "open"}).status_code, 400)
        self.assertEqual(self.crm.patch(f"{C}/settings/stages/{won}/", {"is_active": False}).status_code, 400)
        self.assertEqual(self.crm.delete(f"{C}/settings/stages/{won}/").status_code, 400)
        self.assertTrue(Event.objects.filter(entity_type="crm_stage", action="created").exists())

    def test_reorder_stages(self):
        ids = [s["id"] for s in self.crm.get(f"{C}/settings/stages/").json()]
        swapped = [ids[1], ids[0]] + ids[2:]
        out = self.crm.post(f"{C}/settings/stages/reorder/", {"ids": swapped}).json()
        self.assertEqual([s["id"] for s in out], swapped)
        board = self.crm.get(f"{C}/deals/board/").json()
        self.assertEqual(board["columns"][0]["name"], "Needs Analysis")
        self.assertEqual(self.crm.post(f"{C}/settings/stages/reorder/", {"ids": ids[:3]}).status_code, 400)
        closed_first = ids[4:] + ids[:4]
        self.assertEqual(self.crm.post(f"{C}/settings/stages/reorder/", {"ids": closed_first}).status_code, 400)

    def test_sources_and_lost_reasons(self):
        s = self.crm.post(f"{C}/settings/sources/", {"name": "Trade show"}).json()
        self.assertEqual(s["key"], "trade_show")
        self.assertEqual(self.crm.post(f"{C}/settings/sources/", {"name": "trade show"}).status_code, 400)
        lead = self.lead(source="trade_show")
        self.assertEqual(lead["source_label"], "Trade show")
        self.assertEqual(self.crm.delete(f"{C}/settings/sources/{s['id']}/").status_code, 400)  # leads use it
        self.crm.patch(f"{C}/settings/sources/{s['id']}/", {"is_active": False})
        self.lead(company_name="Old src", source="trade_show", expect=400)  # a switched-off source cannot be chosen
        self.assertEqual(self.crm.patch(f"{C}/leads/{lead['id']}/", {"notes": "ok"}).status_code, 200)  # existing leads keep it
        self.assertEqual(LeadSource.objects.filter(key="trade_show").count(), 1)
        r = self.crm.post(f"{C}/settings/lost-reasons/", {"name": "Went in-house"}).json()
        self.assertEqual(r["deals"], 0)
        self.assertIn("Went in-house", self.crm.get(f"{C}/lookups/").json()["lost_reasons"])
        self.assertEqual(self.crm.post(f"{C}/settings/lost-reasons/", {"name": "WENT IN-HOUSE"}).status_code, 400)
        self.assertEqual(self.crm.delete(f"{C}/settings/lost-reasons/{r['id']}/").status_code, 204)


class ReportTests(CrmBase):
    def test_csv_exports_have_headers_rows_and_are_formula_safe(self):
        self.lead(company_name="=HYPERLINK(\"x\")", contact_name="Plain, Name", source="referral", estimated_value="1200")
        deal = self.deal(name="Export deal", amount="777")
        self.activity(subject="Call back", deal=deal["id"])
        for kind, expect in (("leads", "Plain, Name"), ("deals", "Export deal"), ("activities", "Call back")):
            r = self.crm.get(f"{C}/reports/{kind}.csv")
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/csv", r["Content-Type"])
            self.assertIn("attachment", r["Content-Disposition"])
            text = r.content.decode("utf-8-sig")
            rows = list(csv.reader(io.StringIO(text)))
            self.assertEqual(len(rows), 2, kind)
            self.assertIn(expect, text)
        lead_row = list(csv.reader(io.StringIO(self.crm.get(f"{C}/reports/leads.csv").content.decode("utf-8-sig"))))[1]
        self.assertTrue(lead_row[1].startswith("'="))
        self.assertEqual(lead_row[5], "Referral")
        self.assertEqual(self.crm.get(f"{C}/reports/deals.csv", {"status": "won"}).content.decode("utf-8-sig").count("\n"), 1)
        self.assertEqual(self.crm.get(f"{C}/reports/leads.csv", {"from": "bad"}).status_code, 400)

    def test_summary_conversion_and_team_numbers(self):
        a = self.lead(company_name="One", source="referral", status="qualified")
        self.lead(company_name="Two", source="referral")
        self.lead(company_name="Three", source="website")
        self.crm.post(f"{C}/leads/{a['id']}/convert/", {})
        s = self.crm.get(f"{C}/reports/summary/").json()
        ref = next(x for x in s["by_source"] if x["key"] == "referral")
        self.assertEqual((ref["leads"], ref["converted"], ref["rate"]), (2, 1, 50.0))
        acct = self.account(name="Acme Reports")
        deal = self.deal(acct, name="W", amount="1000")
        self.move(deal, "Closed Won")
        lost = self.deal(acct, name="L", amount="500")
        self.move(lost, "Closed Lost", lost_reason="No budget")
        s = self.crm.get(f"{C}/reports/summary/").json()
        me = next(t for t in s["team"] if t["won"] == 1)
        self.assertEqual((me["won_value"], me["lost"], me["win_rate"]), ("1000.00", 1, 50.0))
        self.assertEqual(s["lost_reasons"], [{"reason": "No budget", "count": 1, "value": "500.00"}])
