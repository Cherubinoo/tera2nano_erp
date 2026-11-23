"""Sales documents and payments tagged to a project: validation, the books, project financials, filters."""
from decimal import Decimal

from apps.accounting.models import JournalEntry, JournalLine
from apps.company.models import CompanyProfile
from apps.sales.models import Payment, SalesDocument
from apps.sales.tests.test_sales import ITEMS, SalesBase, S

P = "/api/v1/projects"
D = Decimal
ZERO_ID = "00000000-0000-0000-0000-000000000000"


class ProjectTaggingTests(SalesBase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project("Alpha")

    def make_project(self, name, **over):
        r = self.api.post(f"{P}/projects/", {"name": name, "customer": self.acme["id"], "project_type": "it_solution",
                                             "status": "active", "start_date": "2026-08-01", "planned_end_date": "2026-12-31",
                                             "contract_value": "100000.00", "budget": "60000.00", **over}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def lines_of(self, source, doc_id):
        entry = JournalEntry.objects.get(source=source, source_id=str(doc_id))
        return list(entry.lines.select_related("account"))

    # -- documents
    def test_tagged_invoice_posts_tagged_lines_and_project_financials_follow(self):
        doc = self.invoice(project=self.project["id"])
        self.assertEqual(doc["project"], self.project["id"])
        self.assertEqual(doc["project_ref"]["name"], "Alpha")
        self.assertEqual(doc["project_ref"]["code"], self.project["code"])
        lines = self.lines_of("invoice", doc["id"])
        self.assertTrue(lines)
        self.assertTrue(all(str(l.project_id) == self.project["id"] for l in lines))
        fin = self.api.get(f"{P}/projects/{self.project['id']}/financials/").json()
        self.assertEqual((fin["revenue"], fin["invoiced"], fin["collected"], fin["outstanding"]),
                         ("1800.00", "1800.00", "0.00", "1800.00"))
        self.pay(amount="500", allocations=[{"document": doc["id"], "amount": "500"}])
        fin = self.api.get(f"{P}/projects/{self.project['id']}/financials/").json()
        self.assertEqual((fin["collected"], fin["outstanding"]), ("500.00", "1300.00"))

    def test_untagged_invoice_is_unchanged(self):
        doc = self.invoice()
        self.assertIsNone(doc["project"])
        self.assertIsNone(doc["project_ref"])
        self.assertTrue(all(l.project_id is None for l in self.lines_of("invoice", doc["id"])))
        fin = self.api.get(f"{P}/projects/{self.project['id']}/financials/").json()
        self.assertEqual((fin["revenue"], fin["invoiced"]), ("0.00", "0.00"))
        self.assertEqual(self.fin.get(f"{S}/documents/", {"project": self.project["id"]}).json()["count"], 0)

    def test_unknown_malformed_and_cancelled_projects_are_refused(self):
        for bad in (ZERO_ID, "not-a-uuid", 12):
            r = self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                                  "form": {"items": ITEMS}, "project": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("project", r.json())
        self.assertEqual(SalesDocument.objects.count(), 0)
        gone = self.make_project("Dropped")
        self.assertEqual(self.api.post(f"{P}/projects/{gone['id']}/status/", {"status": "cancelled", "reason": "x"},
                                       format="json").status_code, 200)
        r = self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                              "form": {"items": ITEMS}, "project": gone["id"]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_edit_can_set_change_and_clear_the_project_on_a_draft_only(self):
        other = self.make_project("Beta project")
        d = self.draft(project=self.project["id"])
        self.assertEqual(self.fin.patch(f"{S}/documents/{d['id']}/", {"project": other["id"]}, format="json").json()["project"], other["id"])
        self.assertEqual(self.fin.patch(f"{S}/documents/{d['id']}/", {"issue_date": "2026-08-16"}, format="json").json()["project"], other["id"])
        bad = self.fin.patch(f"{S}/documents/{d['id']}/", {"project": ZERO_ID}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIsNone(self.fin.patch(f"{S}/documents/{d['id']}/", {"project": None}, format="json").json()["project"])
        issued = self.invoice(project=self.project["id"])
        self.assertEqual(self.fin.patch(f"{S}/documents/{issued['id']}/", {"project": other["id"]}, format="json").status_code, 400)

    def test_credit_note_debit_note_and_proforma_carry_the_project(self):
        inv = self.invoice(project=self.project["id"])
        credit = self.issue(self.draft(kind="credit_note", against=inv["id"], reason="Return", project=self.project["id"],
                                       items=[{"description": "Return", "quantity": "1", "rate": "400"}]))
        debit = self.issue(self.draft(kind="debit_note", against=inv["id"], reason="Late fee", project=self.project["id"],
                                      items=[{"description": "Fee", "quantity": "1", "rate": "100"}]))
        pro = self.draft(kind="proforma_invoice", project=self.project["id"])
        self.assertEqual(pro["project"], self.project["id"])
        for kind, doc in (("credit_note", credit), ("debit_note", debit)):
            lines = self.lines_of(kind, doc["id"])
            self.assertTrue(all(str(l.project_id) == self.project["id"] for l in lines), kind)
        # revenue: 1800 sale less 400 returns plus nothing for the debit note (it posts to sales: +100) = 1500
        fin = self.api.get(f"{P}/projects/{self.project['id']}/financials/").json()
        self.assertEqual(fin["revenue"], "1500.00")
        # void undoes the tagged lines too
        self.assertEqual(self.fin.post(f"{S}/documents/{debit['id']}/void/", {"change_reason": "mistake"}).status_code, 200)
        fin = self.api.get(f"{P}/projects/{self.project['id']}/financials/").json()
        self.assertEqual(fin["revenue"], "1400.00")

    def test_clone_keeps_the_project(self):
        pro = self.draft(kind="proforma_invoice", project=self.project["id"])
        r = self.fin.post(f"{S}/documents/{pro['id']}/clone/", {"kind": "invoice"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["project"], self.project["id"])

    def test_list_filters_by_project_and_totals_follow(self):
        a = self.invoice(project=self.project["id"])
        self.invoice()
        b = self.make_project("Other")
        self.invoice(project=b["id"], items=[{"description": "X", "quantity": "1", "rate": "100"}])
        r = self.fin.get(f"{S}/documents/", {"project": self.project["id"]}).json()
        self.assertEqual([x["id"] for x in r["results"]], [a["id"]])
        self.assertEqual((r["count"], r["total_amount"]), (1, "1800.00"))
        self.assertEqual(r["results"][0]["project_ref"]["id"], self.project["id"])
        self.assertEqual(self.fin.get(f"{S}/documents/", {"project": "junk"}).status_code, 400)
        self.assertEqual(self.fin.get(f"{S}/documents/", {"project": ZERO_ID}).json()["count"], 0)

    def test_project_change_is_audited(self):
        from apps.company.models import CompanyProfileAuditEvent
        d = self.draft(project=self.project["id"])
        ev = CompanyProfileAuditEvent.objects.get(entity_type="sales_document", entity_id=d["id"], action="created")
        self.assertEqual(ev.new_values["project"], self.project["id"])

    # -- payments
    def test_tagged_payment_posts_tagged_lines_and_can_be_filtered(self):
        inv = self.invoice(project=self.project["id"])
        pay = self.pay(amount="2000", allocations=[{"document": inv["id"], "amount": "1800"}], project=self.project["id"])
        self.assertEqual((pay["project"], pay["project_ref"]["name"]), (self.project["id"], "Alpha"))
        lines = self.lines_of("payment", pay["id"])
        self.assertEqual(len(lines), 3)  # bank, receivable, advance
        self.assertTrue(all(str(l.project_id) == self.project["id"] for l in lines))
        untagged = self.pay(amount="100")
        self.assertIsNone(untagged["project"])
        self.assertTrue(all(l.project_id is None for l in self.lines_of("payment", untagged["id"])))
        rows = self.fin.get(f"{S}/payments/", {"project": self.project["id"]}).json()
        self.assertEqual([r["id"] for r in rows["results"]], [pay["id"]])
        self.assertEqual(rows["total_amount"], "2000.00")
        # voiding reverses the tagged lines, so the project nets to zero on that entry
        self.assertEqual(self.fin.post(f"{S}/payments/{pay['id']}/void/", {"change_reason": "bounced"}).status_code, 200)
        net = sum((l.debit - l.credit) for l in JournalLine.objects.filter(entry__source__in=("payment", "reversal"),
                                                                           project_id=self.project["id"]))
        self.assertEqual(net, D("0.00"))

    def test_unknown_project_on_a_payment_is_refused_and_nothing_is_recorded(self):
        r = self.fin.post(f"{S}/payments/", {"customer": self.acme["id"], "date": "2026-08-20", "amount": "5",
                                             "mode": "cash", "project": ZERO_ID}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("project", r.json())
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(JournalEntry.objects.filter(source="payment").count(), 0)

    def test_trial_balance_still_balances_with_tagged_entries(self):
        inv = self.invoice(project=self.project["id"])
        self.pay(amount="900", allocations=[{"document": inv["id"], "amount": "900"}], project=self.project["id"])
        company = CompanyProfile.objects.get()
        debit = sum(l.debit for l in JournalLine.objects.filter(entry__company=company))
        credit = sum(l.credit for l in JournalLine.objects.filter(entry__company=company))
        self.assertEqual(debit, credit)

    def test_project_id_is_accepted_as_an_alias_on_documents_and_payments(self):
        r = self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                              "form": {"items": ITEMS}, "project_id": self.project["id"]}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["project"], r.json()["project_id"]), (self.project["id"], self.project["id"]))
        bad = self.fin.post(f"{S}/documents/", {"kind": "invoice", "customer": self.acme["id"], "issue_date": "2026-08-15",
                                                "form": {"items": ITEMS}, "project_id": ZERO_ID}, format="json")
        self.assertEqual(bad.status_code, 400)
        pay = self.pay(amount="10", project_id=self.project["id"])
        self.assertEqual(pay["project_id"], self.project["id"])

    def test_staff_cannot_tag_anything(self):
        r = self.api_staff.post(f"{S}/payments/", {"customer": self.acme["id"], "date": "2026-08-20", "amount": "5",
                                                   "project": self.project["id"]}, format="json")
        self.assertEqual(r.status_code, 403)
