import datetime
import uuid
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.urls import Resolver404
from rest_framework.response import Response

from apps.accounts.models import Role
from apps.company.models import CompanyPartner, CompanyProfile, CompanyProfileAuditEvent, CompanyTaxRegistration
from apps.company.tests.base import CompanyTestMixin, client_for, make_user, pdf_stub, viewer_client
from apps.compliance import constants as C
from apps.compliance.models import CalendarItem, Obligation
from apps.compliance.services import filings, reports

B = "/api/v1/compliance"
TODAY = datetime.date.today()
FY = "2026-27"
D = datetime.date


def iso(d):
    return d.isoformat()


def day(n):
    return iso(TODAY + datetime.timedelta(days=n))


class Base(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.partner = make_user("partner1", Role.PARTNER)
        self.hr = make_user("hr1", Role.HR_MANAGER)
        self.api_partner = client_for(self.partner)
        self.api_viewer = viewer_client("viewer1", "compliance.view")  # may look, may not change (a partner holds every permission)
        self.api_hr = client_for(self.hr)
        self.company = CompanyProfile.objects.first()
        # The tax module's own calendar must not leak into counts here; the tax tests switch it back on.
        self.no_tax = mock.patch.object(reports, "resolve", side_effect=Resolver404)
        self.no_tax.start()
        self.addCleanup(mock.patch.stopall)

    def post(self, path, body=None, client=None, expect=None):
        r = (client or self.api).post(f"{B}/{path}", body or {}, format="json")
        if expect:
            self.assertEqual(r.status_code, expect, r.content)
        return r

    def get(self, path, client=None, expect=200, **params):
        r = (client or self.api).get(f"{B}/{path}", params)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def adhoc(self, title="Renew insurance", due=None, **over):
        r = self.post("items/", {"title": title, "due_date": due or day(10), "category": "other", **over}, expect=201)
        return r.json()

    def generate(self, fy=FY):
        return self.post("calendar/generate/", {"fy": fy}, expect=200).json()

    def item_for(self, code, period_label_prefix=None):
        qs = CalendarItem.objects.filter(obligation__code=code, fy_label=FY)
        if period_label_prefix:
            qs = qs.filter(period_label__startswith=period_label_prefix)
        return qs.order_by("due_date").first()


class CatalogueTests(Base):
    def test_catalogue_is_created_lazily_once_with_every_required_obligation(self):
        self.assertEqual(Obligation.objects.count(), 0)
        rows = self.get("obligations/")["results"]
        self.assertEqual(len(rows), len(C.CATALOGUE))
        self.assertEqual(len(self.get("obligations/")["results"]), len(C.CATALOGUE))  # second read creates nothing
        forms = {r["form_no"] for r in rows}
        for needed in ("LLP Form 11", "LLP Form 8", "LLP Form 3", "LLP Form 4", "LLP Form 5", "DIR-3 KYC", "ITR-5", "GSTR-1", "GSTR-3B", "GSTR-9"):
            self.assertIn(needed, forms)
        self.assertTrue({r["category"] for r in rows} >= {"mca", "income_tax", "gst", "tds", "labour", "local", "other"})
        form11 = next(r for r in rows if r["code"] == "LLP-FORM-11")
        self.assertEqual((form11["category_label"], form11["frequency"], form11["penalty_rule"], form11["assignee_role"]),
                         ("MCA / ROC", "annual", "llp_per_day", "managing_partner"))
        self.assertEqual(form11["reminder_days"], [15, 7, 1])

    def test_edits_survive_reseeding_and_switching_off_is_audited(self):
        ob = self.get("obligations/")["results"][0]
        r = self.api.patch(f"{B}/obligations/{ob['id']}/", {"enabled": False, "penalty_note": "Changed"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.post("obligations/seed/", expect=200).json()["created"], 0)
        again = self.get(f"obligations/{ob['id']}/")
        self.assertEqual((again["enabled"], again["penalty_note"]), (False, "Changed"))
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="compliance_obligation", entity_id=ob["id"], action="updated").exists())

    def test_custom_obligations_validate_and_only_custom_ones_are_deleted(self):
        bad = self.post("obligations/", {"name": "X", "code": "x1", "due_rule": {"type": "monthly", "day": 40}})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("due_rule", bad.json())
        self.assertIn("code", self.post("obligations/", {"name": "X", "code": "bad code!", "due_rule": {"type": "monthly", "day": 5}}).json())
        self.assertIn("name", self.post("obligations/", {"code": "ok", "due_rule": {"type": "monthly", "day": 5}}).json())
        mismatch = self.post("obligations/", {"name": "E", "code": "ev", "frequency": "event", "due_rule": {"type": "monthly", "day": 5}})
        self.assertIn("due_rule", mismatch.json())
        made = self.post("obligations/", {"name": "Bonus act return", "code": "bonus", "category": "labour", "frequency": "annual",
                                         "form_no": "Form D", "due_rule": {"type": "dates", "dates": [{"month": 1, "day": 30, "fy": "prev"}]}},
                         expect=201).json()
        self.assertEqual(made["code"], "BONUS")
        self.assertIn("code", self.post("obligations/", {"name": "Dup", "code": "bonus", "due_rule": {"type": "monthly", "day": 5}}).json())
        seed = self.get("obligations/")["results"][0]
        self.assertEqual(self.api.delete(f"{B}/obligations/{seed['id']}/").status_code, 400)
        self.assertEqual(self.api.delete(f"{B}/obligations/{made['id']}/").status_code, 204)

    def test_custom_rule_generates_into_the_calendar(self):
        self.post("obligations/", {"name": "Bonus act return", "code": "bonus", "category": "labour", "frequency": "annual",
                                  "due_rule": {"type": "dates", "dates": [{"month": 1, "day": 30, "fy": "cur", "label": "Bonus FY {fy}"}]}}, expect=201)
        self.generate()
        it = CalendarItem.objects.get(obligation__code="BONUS")
        self.assertEqual((it.due_date, it.period_label), (D(2027, 1, 30), "Bonus FY 2026-27"))


class GenerationTests(Base):
    def test_generates_the_expected_calendar_for_a_fixed_year(self):
        res = self.generate()
        # Form 11, Form 8, DIR-3, ITR-5, GSTR-9 (1 each) + advance tax 4 + TDS returns 4 + six monthly returns x 12
        self.assertEqual(res, {"fy": FY, "created": 5 + 4 + 4 + 6 * 12, "updated": 0, "unchanged": 0, "skipped_closed": 0})
        items = CalendarItem.objects.filter(fy_label=FY)
        self.assertEqual(items.count(), 85)
        f11 = items.get(obligation__code="LLP-FORM-11")
        self.assertEqual((f11.due_date, f11.period_label, f11.priority, f11.penalty_rule), (D(2026, 5, 30), "FY 2025-26", "high", "llp_per_day"))
        self.assertEqual(f11.reminder_days, [15, 7, 1])
        self.assertFalse(items.filter(obligation__code__in=["ITR-5-AUDIT", "TRADE-LICENCE", "LLP-FORM-4"]).exists())  # off, or event driven

    def test_regenerating_is_idempotent(self):
        self.generate()
        again = self.generate()
        self.assertEqual((again["created"], again["updated"], again["unchanged"]), (0, 0, 85))
        self.assertEqual(CalendarItem.objects.count(), 85)

    def test_new_year_is_independent_and_bad_year_is_refused(self):
        self.generate()
        nxt = self.generate("2027-28")
        self.assertEqual(nxt["created"], 85)
        self.assertEqual(CalendarItem.objects.filter(obligation__code="LLP-FORM-11").count(), 2)
        self.assertEqual(self.post("calendar/generate/", {"fy": "2027-30"}).status_code, 400)

    def test_regeneration_moves_open_items_but_never_filed_or_hand_moved_ones(self):
        self.generate()
        april = self.item_for("GSTR-3B")
        may = CalendarItem.objects.filter(obligation__code="GSTR-3B", fy_label=FY).order_by("due_date")[1]
        june = CalendarItem.objects.filter(obligation__code="GSTR-3B", fy_label=FY).order_by("due_date")[2]
        self.post(f"items/{april.id}/file/", {"filed_date": iso(TODAY)}, expect=200)
        self.api.patch(f"{B}/items/{may.id}/", {"due_date": "2026-06-25"}, format="json")  # an extension granted by the department
        ob = Obligation.objects.get(code="GSTR-3B")
        r = self.api.patch(f"{B}/obligations/{ob.id}/", {"due_rule": {"type": "monthly", "day": 21, "lag": 1}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        res = self.generate()
        self.assertEqual((res["created"], res["updated"], res["skipped_closed"]), (0, 10, 1))
        april.refresh_from_db(), may.refresh_from_db(), june.refresh_from_db()
        self.assertEqual(april.due_date, D(2026, 4, 20))  # filed: untouched
        self.assertEqual(may.due_date, D(2026, 6, 25))    # moved by hand: untouched
        self.assertEqual(june.due_date, D(2026, 6, 21))   # open: follows the rule

    def test_generated_items_get_the_role_owner_when_one_exists(self):
        self.generate()
        self.assertEqual(self.item_for("GSTR-3B").assignee, self.finance)       # finance_manager
        self.assertIsNone(self.item_for("LLP-FORM-11").assignee)                # no managing partner user yet
        self.assertEqual(self.item_for("PF-MONTHLY").assignee, self.hr)

    def test_disabled_obligation_is_not_generated(self):
        self.get("obligations/")
        Obligation.objects.filter(code="GSTR-9").update(enabled=False)
        self.generate()
        self.assertFalse(CalendarItem.objects.filter(obligation__code="GSTR-9").exists())


class ItemStatusTests(Base):
    def test_status_keys_with_a_fixed_clock(self):
        self.generate()
        item = self.item_for("LLP-FORM-11")
        today = D(2026, 5, 20)
        self.assertEqual(filings.item_dict(item, today)["status_key"], "due_soon")  # 10 days
        self.assertEqual(filings.item_dict(item, D(2026, 4, 1))["status_key"], "upcoming")
        d = filings.item_dict(item, D(2026, 6, 9))
        self.assertEqual((d["status_key"], d["days_late"], d["estimated_penalty"], d["on_time"]), ("overdue", 10, "1000.00", None))

    def test_list_filters_totals_and_search(self):
        self.adhoc("Overdue one", day(-3))
        self.adhoc("Soon one", day(5))
        self.adhoc("Far one", day(60))
        r = self.get("items/", q="one")
        self.assertEqual({x["title"]: x["status_key"] for x in r["results"]}, {"Overdue one": "overdue", "Soon one": "due_soon", "Far one": "upcoming"})
        self.assertEqual((r["totals"]["overdue"], r["totals"]["due_soon"], r["totals"]["upcoming"]), (1, 1, 1))
        self.assertEqual([x["title"] for x in self.get("items/", status="overdue")["results"]], ["Overdue one"])
        self.assertEqual(self.get("items/", status="overdue")["totals"]["total"], 3)  # tabs keep their counts
        self.get("items/", status="nonsense", expect=400)
        self.get("items/", fy="abc", expect=400)
        self.get("items/", month="2026-13", expect=400)
        self.assertEqual(len(self.get("items/", assignee="me")["results"]), 0)

    def test_month_and_category_filters(self):
        self.generate()
        may = self.get("items/", month="2026-05", fy=FY)["results"]
        self.assertTrue(all(x["due_date"].startswith("2026-05") for x in may))
        self.assertIn("LLP annual return", {x["title"] for x in may})
        gst = self.get("items/", fy=FY, category="gst")["results"]
        self.assertEqual(len(gst), 25)


class FilingTests(Base):
    def test_mark_filed_records_details_and_allocates_a_reference(self):
        item = self.adhoc()
        r = self.post(f"items/{item['id']}/file/", {"filed_date": day(-1), "ack_number": "SRN-123", "fee_paid": "600", "penalty_paid": "0",
                                                    "remarks": "Filed by CA"}, expect=200).json()
        self.assertEqual((r["status"], r["status_key"], r["ack_number"], r["fee_paid"], r["on_time"]), ("filed", "filed", "SRN-123", "600.00", True))
        self.assertRegex(r["reference"], r"^T2N/CF/\d{2}/\d{4}/\d{5}$")
        ev = CompanyProfileAuditEvent.objects.filter(entity_type="compliance_item", entity_id=item["id"]).order_by("-occurred_at").first()
        self.assertIn("filed_date", ev.changed_fields)

    def test_filing_rules(self):
        item = self.adhoc()
        url = f"items/{item['id']}/file/"
        self.assertIn("filed_date", self.post(url, {}, expect=400).json())
        self.assertIn("filed_date", self.post(url, {"filed_date": day(1)}, expect=400).json())
        self.assertIn("filed_date", self.post(url, {"filed_date": "31-12-2026"}, expect=400).json())
        self.assertIn("fee_paid", self.post(url, {"filed_date": day(0), "fee_paid": "-5"}, expect=400).json())
        self.assertIn("fee_paid", self.post(url, {"filed_date": day(0), "fee_paid": "abc"}, expect=400).json())
        self.assertIn("ack_number", self.post(url, {"filed_date": day(0), "ack_number": "x" * 81}, expect=400).json())
        self.post(url, {"filed_date": day(0)}, expect=200)
        self.post(url, {"filed_date": day(0)}, expect=400)  # already filed

    def test_not_applicable_needs_a_reason_and_blocks_filing_until_reopened(self):
        item = self.adhoc()
        self.post(f"items/{item['id']}/not-applicable/", {}, expect=400)
        r = self.post(f"items/{item['id']}/not-applicable/", {"reason": "No employees this year"}, expect=200).json()
        self.assertEqual((r["status_key"], r["na_reason"]), ("not_applicable", "No employees this year"))
        self.post(f"items/{item['id']}/file/", {"filed_date": day(0)}, expect=400)
        self.post(f"items/{item['id']}/reopen/", {}, expect=400)
        self.post(f"items/{item['id']}/reopen/", {"reason": "Hired staff"}, expect=200)
        self.post(f"items/{item['id']}/file/", {"filed_date": day(0)}, expect=200)
        ev = CompanyProfileAuditEvent.objects.filter(entity_type="compliance_item", entity_id=item["id"], reason="No employees this year")
        self.assertEqual(ev.count(), 1)

    def test_reopening_keeps_the_reference_and_a_filed_item_cannot_be_deleted_or_moved(self):
        item = self.adhoc()
        ref = self.post(f"items/{item['id']}/file/", {"filed_date": day(0)}, expect=200).json()["reference"]
        self.assertEqual(self.api.delete(f"{B}/items/{item['id']}/").status_code, 400)
        r = self.api.patch(f"{B}/items/{item['id']}/", {"due_date": day(30)}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("due_date", r.json())
        self.post(f"items/{item['id']}/reopen/", {"reason": "Wrong form"}, expect=200)
        again = self.post(f"items/{item['id']}/file/", {"filed_date": day(0)}, expect=200).json()
        self.assertEqual(again["reference"], ref)

    def test_generated_items_cannot_be_deleted_but_adhoc_ones_can(self):
        self.generate()
        gen = self.item_for("GSTR-3B")
        self.assertEqual(self.api.delete(f"{B}/items/{gen.id}/").status_code, 400)
        item = self.adhoc()
        self.assertEqual(self.api.delete(f"{B}/items/{item['id']}/").status_code, 204)
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="compliance_item", entity_id=item["id"], action="deleted").exists())

    def test_update_item_validation_and_reassign(self):
        item = self.adhoc()
        url = f"{B}/items/{item['id']}/"
        for bad, field in (({"assignee": str(uuid.uuid4())}, "assignee"), ({"priority": "urgent"}, "priority"),
                           ({"reminder_days": [500]}, "reminder_days"), ({"reminder_days": "a,b"}, "reminder_days"),
                           ({"due_date": "nope"}, "due_date"), ({"title": " "}, "title")):
            r = self.api.patch(url, bad, format="json")
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json(), bad)
        r = self.api.patch(url, {"assignee": str(self.finance.pk), "priority": "critical", "reminder_days": "30, 7,1"}, format="json").json()
        self.assertEqual((r["assignee"]["id"], r["priority"], r["reminder_days"]), (str(self.finance.pk), "critical", [30, 7, 1]))
        r = self.api.patch(url, {"assignee": None}, format="json").json()
        self.assertIsNone(r["assignee"])

    def test_adhoc_creation_validation(self):
        self.assertIn("title", self.post("items/", {"due_date": day(3)}, expect=400).json())
        self.assertIn("due_date", self.post("items/", {"title": "x"}, expect=400).json())
        self.assertIn("category", self.post("items/", {"title": "x", "due_date": day(3), "category": "zzz"}, expect=400).json())
        item = self.adhoc(due="2026-02-10")
        self.assertEqual(item["fy"], "2025-26")

    def test_event_filing_is_due_thirty_days_after_the_event_and_not_duplicated(self):
        self.get("obligations/")
        form4 = Obligation.objects.get(code="LLP-FORM-4")
        r = self.post("items/", {"obligation": str(form4.id), "event_date": "2028-02-10"}, expect=201).json()
        self.assertEqual((r["due_date"], r["title"], r["form_no"]), ("2028-03-11", "Change in partners or designated partners", "LLP Form 4"))
        self.assertIn("event_date", self.post("items/", {"obligation": str(form4.id), "event_date": "2028-02-10"}, expect=400).json())
        self.assertIn("event_date", self.post("items/", {"obligation": str(form4.id)}, expect=400).json())
        self.assertEqual(self.api.delete(f"{B}/items/{r['id']}/").status_code, 204)  # an event filing created in error may be removed


class BulkTests(Base):
    def test_bulk_actions_report_what_could_not_be_done(self):
        a, b, c = self.adhoc("A"), self.adhoc("B"), self.adhoc("C")
        self.post(f"items/{c['id']}/file/", {"filed_date": day(0)}, expect=200)
        r = self.post("items/bulk/", {"action": "assign", "ids": [a["id"], b["id"]], "assignee": str(self.finance.pk)}, expect=200).json()
        self.assertEqual((r["done"], r["failed"]), (2, []))
        self.assertEqual(CalendarItem.objects.filter(assignee=self.finance).count(), 2)
        r = self.post("items/bulk/", {"action": "not_applicable", "ids": [a["id"], c["id"], str(uuid.uuid4())], "reason": "Not needed"}, expect=200).json()
        self.assertEqual(r["done"], 1)
        self.assertEqual(sorted(f["error"] for f in r["failed"])[0][:4], "A fi")
        self.assertEqual(len(r["failed"]), 2)
        r = self.post("items/bulk/", {"action": "priority", "ids": [b["id"]], "priority": "high"}, expect=200).json()
        self.assertEqual(r["done"], 1)
        self.assertEqual(CalendarItem.objects.get(pk=b["id"]).priority, "high")

    def test_bulk_validation(self):
        a = self.adhoc()
        self.assertIn("action", self.post("items/bulk/", {"action": "explode", "ids": [a["id"]]}, expect=400).json())
        self.assertIn("ids", self.post("items/bulk/", {"action": "assign", "ids": []}, expect=400).json())
        self.assertIn("reason", self.post("items/bulk/", {"action": "not_applicable", "ids": [a["id"]]}, expect=400).json())
        self.assertIn("assignee", self.post("items/bulk/", {"action": "assign", "ids": [a["id"]], "assignee": str(uuid.uuid4())}, expect=400).json())
        self.assertIn("priority", self.post("items/bulk/", {"action": "priority", "ids": [a["id"]], "priority": "x"}, expect=400).json())


class PenaltyApiTests(Base):
    def test_estimate_endpoint(self):
        r = self.post("penalty/estimate/", {"rule": "llp_per_day", "due_date": "2026-05-30", "filed_date": "2026-06-09"}, expect=200).json()
        self.assertEqual((r["amount"], r["days_late"], r["estimate"]), ("1000.00", 10, True))
        r = self.post("penalty/estimate/", {"rule": "itr_234f", "due_date": "2026-07-31", "filed_date": "2026-08-20", "total_income": "300000"}, expect=200).json()
        self.assertEqual(r["amount"], "1000.00")
        self.assertIn("rule", self.post("penalty/estimate/", {"rule": "x", "due_date": "2026-05-30"}, expect=400).json())
        self.assertIn("due_date", self.post("penalty/estimate/", {"rule": "llp_per_day"}, expect=400).json())
        item = self.adhoc()
        self.assertIn("rule", self.post("penalty/estimate/", {"item": item["id"]}, expect=400).json())  # an item with no penalty rule
        self.assertEqual(len(self.get("penalty/rules/")), 4)
        self.post("penalty/estimate/", {"item": str(uuid.uuid4())}, expect=404)


class LegalEventTests(Base):
    def test_create_validate_and_audit(self):
        r = self.post("legal-events/", {"event_type": "name_change", "title": "Renamed", "event_date": "2025-03-01", "form_no": "LLP Form 5",
                                       "srn": "T1234567"}, expect=201).json()
        self.assertRegex(r["reference"], r"^T2N/LEV/\d{2}/\d{4}/\d{5}$")
        self.assertIn("event_date", self.post("legal-events/", {"event_type": "other", "title": "x", "event_date": day(2)}, expect=400).json())
        self.assertIn("event_type", self.post("legal-events/", {"title": "x", "event_date": day(-2)}, expect=400).json())
        self.assertIn("title", self.post("legal-events/", {"event_type": "other", "event_date": day(-2)}, expect=400).json())
        self.assertEqual(self.api.patch(f"{B}/legal-events/{r['id']}/", {"srn": "T7654321"}, format="json").json()["srn"], "T7654321")
        self.assertEqual(len(self.get("legal-events/", q="T7654321")["results"]), 1)
        self.assertEqual(len(self.get("legal-events/", type="kyc")["results"]), 0)
        self.assertTrue(CompanyProfileAuditEvent.objects.filter(entity_type="legal_event", entity_id=r["id"], action="updated").exists())
        self.assertEqual(self.api.delete(f"{B}/legal-events/{r['id']}/").status_code, 204)

    def test_partner_change_can_raise_the_form_4_filing(self):
        r = self.post("legal-events/", {"event_type": "partner_change", "title": "New partner", "event_date": "2026-10-01",
                                       "create_filing": True}, expect=201).json()
        self.assertEqual((r["filing"]["due_date"], r["filing"]["form_no"], r["filing"]["status_key"] in ("overdue", "due_soon", "upcoming")),
                         ("2026-10-31", "LLP Form 4", True))
        bad = self.post("legal-events/", {"event_type": "charge", "title": "Charge", "event_date": "2026-10-01", "create_filing": True})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.get("legal-events/", q="Charge")["count"], 0)  # nothing half saved

    def test_import_history_from_the_company_records_once(self):
        CompanyProfile.objects.update(date_of_incorporation=D(2024, 4, 1), llpin="AAB-1234")
        CompanyPartner.objects.create(company=self.company, full_name="Asha Rao", partner_type="designated_partner",
                                      appointment_date=D(2024, 4, 1), cessation_date=D(2026, 1, 15))
        first = self.post("legal-events/import-history/", expect=200).json()
        self.assertGreaterEqual(first["created"], 3)  # incorporation, appointed, ceased (and any audit rows)
        titles = {e["title"] for e in self.get("legal-events/")["results"]}
        self.assertIn("Asha Rao joined as designated partner", titles)
        self.assertIn("Asha Rao ceased as designated partner", titles)
        second = self.post("legal-events/import-history/", expect=200).json()
        self.assertEqual(second["created"], 0)
        self.assertTrue(all(e["imported"] for e in self.get("legal-events/")["results"]))


class ContractApiTests(Base):
    def make(self, **over):
        body = {"title": "Office lease", "counterparty": "Landlord Pvt Ltd", "contract_type": "lease", "effective_date": "2026-01-01",
                "expiry_date": day(20), "renewal_notice_days": 30, **over}
        return self.post("contracts/", body, expect=201).json()

    def test_create_and_state(self):
        c = self.make()
        self.assertRegex(c["reference"], r"^T2N/CON/\d{2}/\d{4}/\d{5}$")
        self.assertEqual((c["state"], c["days_left"], c["status"]), ("expiring", 20, "active"))
        far = self.make(title="Far", expiry_date=day(200))
        self.assertEqual(far["state"], "active")
        lapsed = self.make(title="Lapsed", expiry_date=day(-5))
        self.assertEqual(lapsed["state"], "expired")
        rows = self.get("contracts/")
        self.assertEqual(rows["totals"]["expiring"], 1)
        self.assertEqual(rows["totals"]["expired"], 1)
        self.assertEqual([r["title"] for r in rows["results"]], ["Lapsed", "Office lease", "Far"])  # soonest expiry first
        self.assertEqual([r["title"] for r in self.get("contracts/", state="expired")["results"]], ["Lapsed"])

    def test_validation(self):
        for bad, field in (({"title": ""}, "title"), ({"counterparty": ""}, "counterparty"), ({"contract_type": "zzz"}, "contract_type"),
                           ({"expiry_date": "2025-12-31"}, "expiry_date"), ({"auto_renew": True, "expiry_date": None}, "auto_renew"),
                           ({"value": "-1"}, "value"), ({"value": "1.234"}, "value"), ({"renewal_notice_days": 400}, "renewal_notice_days"),
                           ({"renewal_term_months": 0}, "renewal_term_months"), ({"effective_date": ""}, "effective_date"),
                           ({"owner": str(uuid.uuid4())}, "owner"), ({"customer_id": str(uuid.uuid4())}, "customer_id"),
                           ({"vendor_id": "not-a-uuid"}, "vendor_id"), ({"status": "terminated"}, "status")):
            body = {"title": "T", "counterparty": "C", "effective_date": "2026-01-01", **bad}
            r = self.post("contracts/", body)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn(field, r.json(), bad)

    def test_soft_links_value_and_owner(self):
        from apps.sales.models import Customer
        cu = Customer.objects.create(company=self.company, display_name="Acme Industries")
        vid, pid = str(uuid.uuid4()), str(uuid.uuid4())
        c = self.make(customer_id=str(cu.pk), vendor_id=vid, vendor_name="Paper Co", project_id=pid, project_name="Alpha", value="125000.50",
                      owner=str(self.finance.pk))
        self.assertEqual((c["customer"]["name"], c["vendor"]["name"], c["project"]["id"], c["value"], c["owner"]["id"]),
                         ("Acme Industries", "Paper Co", pid, "125000.50", str(self.finance.pk)))
        self.assertEqual(len(self.get("contracts/", customer=str(cu.pk))["results"]), 1)
        self.assertEqual(len(self.get("contracts/", project=str(uuid.uuid4()))["results"]), 0)

    def test_renew_terminate_and_delete_rules(self):
        c = self.make(auto_renew=False)
        url = f"contracts/{c['id']}/"
        self.assertIn("new_expiry_date", self.post(url + "renew/", {"new_expiry_date": day(10)}, expect=400).json())  # earlier than the current expiry
        r = self.post(url + "renew/", {"new_expiry_date": day(385), "note": "Annual renewal"}, expect=200).json()
        self.assertEqual((r["renewal_count"], r["expiry_date"], r["state"]), (1, day(385), "active"))
        self.assertEqual(self.api.delete(f"{B}/{url}").status_code, 400)  # active: terminate, do not delete
        self.post(url + "terminate/", {}, expect=400)
        t = self.post(url + "terminate/", {"reason": "Moved premises"}, expect=200).json()
        self.assertEqual((t["state"], t["termination_reason"]), ("terminated", "Moved premises"))
        self.post(url + "terminate/", {"reason": "again"}, expect=400)
        self.post(url + "renew/", {"new_expiry_date": day(900)}, expect=400)
        self.assertEqual(self.api.patch(f"{B}/{url}", {"title": "New"}, format="json").status_code, 400)
        self.assertEqual(self.api.patch(f"{B}/{url}", {"notes": "kept"}, format="json").status_code, 200)
        events = CompanyProfileAuditEvent.objects.filter(entity_type="contract", entity_id=c["id"])
        self.assertTrue(events.filter(action="created").exists() and events.filter(reason="Moved premises").exists())
        draft = self.make(title="Draft", status="draft")
        self.assertEqual(self.api.delete(f"{B}/contracts/{draft['id']}/").status_code, 204)

    def test_auto_renew_contract_shows_the_rolled_expiry(self):
        c = self.make(effective_date="2020-01-01", expiry_date="2024-01-31", auto_renew=True, renewal_term_months=12)
        self.assertTrue(c["auto_renewed"])
        self.assertGreaterEqual(c["current_expiry"], day(0))
        self.assertIn(c["state"], ("active", "expiring"))


class CaseAndPolicyTests(Base):
    def test_case_lifecycle(self):
        c = self.post("cases/", {"title": "Notice from vendor", "case_type": "legal_notice", "party_role": "notice_received", "forum": "Civil Court",
                                "case_number": "OS 12/2026", "filed_date": "2026-09-01", "next_hearing_date": day(5), "lawyer": "Adv. Rao",
                                "amount_involved": "250000"}, expect=201).json()
        self.assertRegex(c["reference"], r"^T2N/CAS/\d{2}/\d{4}/\d{5}$")
        self.assertEqual(c["hearing_state"], "soon")
        self.assertIn("next_hearing_date", self.api.patch(f"{B}/cases/{c['id']}/", {"next_hearing_date": "2026-08-01"}, format="json").json())
        r = self.api.patch(f"{B}/cases/{c['id']}/", {"status": "closed"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("outcome", r.json())
        r = self.api.patch(f"{B}/cases/{c['id']}/", {"status": "disposed", "outcome": "Settled for Rs 1,00,000"}, format="json").json()
        self.assertEqual((r["status"], r["next_hearing_date"], r["closed_on"] is not None), ("disposed", None, True))
        self.assertEqual(self.api.delete(f"{B}/cases/{c['id']}/").status_code, 400)
        self.assertEqual(self.get("cases/", status="active")["count"], 0)
        self.assertEqual(self.get("cases/")["totals"]["disposed"], 1)
        self.assertEqual(self.get("cases/", q="OS 12")["count"], 1)

    def test_case_validation_and_delete_of_open_matter(self):
        self.assertIn("title", self.post("cases/", {}, expect=400).json())
        self.assertIn("case_type", self.post("cases/", {"title": "x", "case_type": "zzz"}, expect=400).json())
        self.assertIn("amount_involved", self.post("cases/", {"title": "x", "amount_involved": "-1"}, expect=400).json())
        c = self.post("cases/", {"title": "Open"}, expect=201).json()
        self.assertEqual(self.api.delete(f"{B}/cases/{c['id']}/").status_code, 204)

    def test_policy_versions_and_acknowledgement(self):
        p = self.post("policies/", {"name": "Code of conduct", "version": "1.0", "effective_date": "2026-04-01", "status": "draft",
                                   "requires_acknowledgement": True, "review_date": day(10)}, expect=201).json()
        self.assertRegex(p["reference"], r"^T2N/POL/\d{2}/\d{4}/\d{5}$")
        self.post(f"policies/{p['id']}/acknowledge/", expect=400)  # draft
        self.api.patch(f"{B}/policies/{p['id']}/", {"status": "active"}, format="json")
        r = self.post(f"policies/{p['id']}/acknowledge/", client=self.api_partner, expect=200).json()
        self.assertEqual((r["acknowledged_count"], r["acknowledged_by_me"]), (1, True))
        self.post(f"policies/{p['id']}/acknowledge/", client=self.api_partner, expect=200)  # repeating changes nothing
        self.assertEqual(self.get(f"policies/{p['id']}/")["acknowledged_count"], 1)
        r = self.api.patch(f"{B}/policies/{p['id']}/", {"version": "2.0"}, format="json").json()
        self.assertEqual(r["acknowledged_count"], 0)  # a new version needs fresh acknowledgement
        self.assertEqual(self.api.delete(f"{B}/policies/{p['id']}/").status_code, 400)
        self.assertIn("review_date", self.api.patch(f"{B}/policies/{p['id']}/", {"review_date": "2026-01-01"}, format="json").json())
        plain = self.post("policies/", {"name": "Plain", "effective_date": "2026-04-01", "status": "active"}, expect=201).json()
        self.post(f"policies/{plain['id']}/acknowledge/", expect=400)  # does not ask for it
        self.api.patch(f"{B}/policies/{p['id']}/", {"status": "retired"}, format="json")
        self.assertEqual(self.api.delete(f"{B}/policies/{p['id']}/").status_code, 204)


class ReminderTests(Base):
    def test_reminders_gather_every_source_sorted(self):
        self.adhoc("Overdue form", day(-2))
        self.adhoc("Soon form", day(3))
        self.adhoc("Far form", day(90))
        self.post("contracts/", {"title": "Lease", "counterparty": "L", "effective_date": "2026-01-01", "expiry_date": day(10)}, expect=201)
        self.post("cases/", {"title": "Notice", "next_hearing_date": day(5)}, expect=201)
        CompanyTaxRegistration.objects.create(company=self.company, registration_type="msme", registration_number="UDYAM-KA-01-0000001",
                                              effective_to=TODAY + datetime.timedelta(days=20))
        r = self.get("reminders/", days=30)
        kinds = [(x["kind"], x["title"]) for x in r["results"]]
        self.assertEqual([k for k, _ in kinds], ["filing", "filing", "hearing", "contract", "licence"])
        self.assertEqual(r["results"][0]["tone"], "overdue")
        self.assertEqual(r["results"][0]["days_left"], -2)
        self.assertEqual([x["due_date"] for x in r["results"]], sorted(x["due_date"] for x in r["results"]))
        self.assertNotIn("Far form", [t for _, t in kinds])
        self.assertTrue(next(x for x in r["results"] if x["kind"] == "licence")["read_only"])
        self.assertEqual(len(self.get("reminders/", days=365)["results"]), 6)
        self.assertEqual({x["kind"] for x in self.get("reminders/", kind="hearing")["results"]}, {"hearing"})
        self.get("reminders/", days="abc", expect=400)
        self.get("reminders/", days=999, expect=400)

    def test_filed_and_not_applicable_items_do_not_remind(self):
        a, b = self.adhoc("A", day(2)), self.adhoc("B", day(2))
        self.post(f"items/{a['id']}/file/", {"filed_date": day(0)}, expect=200)
        self.post(f"items/{b['id']}/not-applicable/", {"reason": "n/a"}, expect=200)
        self.assertEqual(self.get("reminders/")["count"], 0)

    def test_reminder_today_flag_follows_the_reminder_days(self):
        self.adhoc("Seven days out", day(7))
        self.adhoc("Eight days out", day(8))
        flags = {x["title"]: x["reminder_today"] for x in self.get("reminders/")["results"]}
        self.assertEqual(flags, {"Seven days out": True, "Eight days out": False})

    def test_tax_calendar_is_merged_when_present_and_never_breaks_when_not(self):
        payload = {"today": day(0), "events": [
            {"id": "t1", "kind": "gst", "kind_label": "GST", "title": "GSTR-3B", "period": "Oct 2026", "due_date": day(6), "days_left": 6, "status": "pending", "link": "/tax/gst"},
            {"id": "t2", "title": "Paid", "due_date": day(4), "status": "filed"},
            {"id": "t3", "title": "Far away", "due_date": day(80), "status": "pending"}]}
        match = SimpleNamespace(args=(), kwargs={}, func=lambda req: Response(payload))
        with mock.patch.object(reports, "resolve", return_value=match):
            rows = self.get("reminders/")["results"]
            events = self.get("tax-events/", days=30)["results"]
            summary = self.get("summary/")
        tax = [x for x in rows if x["kind"] == "tax"]
        self.assertEqual([(x["title"], x["source"], x["read_only"], x["link"]) for x in tax], [("GSTR-3B", "Tax", True, "/tax/gst")])
        self.assertEqual([e["title"] for e in events], ["GSTR-3B"])
        self.assertEqual((summary["tax_due_30_days"], summary["tax_overdue"]), (1, 0))

        def boom(req):
            raise RuntimeError("tax module is broken")
        with mock.patch.object(reports, "resolve", return_value=SimpleNamespace(args=(), kwargs={}, func=boom)):
            self.assertEqual(self.get("reminders/")["count"], 0)
            self.assertEqual(self.get("tax-events/")["count"], 0)
            self.assertEqual(self.get("summary/")["tax_due_30_days"], 0)
        forbidden = SimpleNamespace(args=(), kwargs={}, func=lambda req: Response({"detail": "no"}, status=403))
        with mock.patch.object(reports, "resolve", return_value=forbidden):
            self.assertEqual(self.get("tax-events/")["count"], 0)

    def test_real_tax_calendar_endpoint_merges_when_tax_module_is_installed(self):
        self.no_tax.stop()
        r = self.get("tax-events/", days=90)
        self.assertIn("results", r)
        for row in r["results"]:
            self.assertEqual((row["source"], row["read_only"]), ("Tax", True))


class ExpiryWatchAndRegistryTests(Base):
    def test_expiry_watch_combines_company_licences_and_contracts(self):
        CompanyTaxRegistration.objects.create(company=self.company, registration_type="shops_establishment", registration_number="SE-998877",
                                              effective_to=TODAY - datetime.timedelta(days=3))
        CompanyTaxRegistration.objects.create(company=self.company, registration_type="msme", registration_number="U-1",
                                              effective_to=TODAY + datetime.timedelta(days=200))  # beyond the 90-day window
        self.post("contracts/", {"title": "Lease", "counterparty": "L", "effective_date": "2026-01-01", "expiry_date": day(40)}, expect=201)
        rows = self.get("expiry-watch/", days=90)["results"]
        self.assertEqual([(r["source"], r["expired"], r["read_only"]) for r in rows], [("company", True, True), ("contract", False, False)])
        self.assertEqual(rows[0]["tone"], "overdue")
        self.assertIn("••", rows[0]["detail"])  # the registration number is masked
        self.assertEqual(len(self.get("expiry-watch/", days=365)["results"]), 3)

    def test_registry_masks_identifiers_for_viewers_but_not_managers(self):
        CompanyProfile.objects.update(pan="ABCDE1234F", date_of_incorporation=D(2024, 4, 1))
        CompanyPartner.objects.create(company=self.company, full_name="Asha Rao", partner_type="designated_partner", is_designated_partner=True,
                                      dpin="01234567", appointment_date=D(2024, 4, 1))
        admin = self.get("registry/")
        self.assertEqual((admin["pan"], admin["partners"][0]["dpin"], admin["designated_partners"]), ("ABCDE1234F", "01234567", 1))
        viewer = self.get("registry/", client=self.api_viewer)
        self.assertEqual((viewer["pan"], viewer["partners"][0]["dpin"]), ("••••••234F", "••••4567"))
        self.assertEqual(viewer["date_of_incorporation"], "2024-04-01")


class DashboardTests(Base):
    def make(self, title, due, status="open", filed=None, category="mca", assignee=None):
        return CalendarItem.objects.create(company=self.company, fy_label=FY, period_key=f"adhoc:{uuid.uuid4()}", title=title, due_date=due,
                                           status=status, filed_date=filed, category=category, assignee=assignee)

    def test_dashboard_numbers_with_a_fixed_clock(self):
        self.make("A on time", D(2026, 5, 30), "filed", D(2026, 5, 20))
        self.make("B late", D(2026, 6, 15), "filed", D(2026, 6, 20), category="gst")
        self.make("C overdue", D(2026, 7, 31), category="income_tax")
        self.make("D future", D(2026, 12, 15), assignee=self.finance, category="gst")
        self.make("E not applicable", D(2026, 8, 11), "not_applicable")
        d = reports.dashboard(self.company, SimpleNamespace(user=self.admin), FY, today=D(2026, 11, 12))
        self.assertEqual(d["score"], 33.3)  # 1 of the 3 items already due was filed on time
        self.assertEqual((d["overdue"], d["due_in_30_days"], d["filed"], d["total"]), (1, 0, 2, 4))
        self.assertEqual({s["key"]: s["value"] for s in d["by_status"]}, {"upcoming": 1, "due_soon": 0, "overdue": 1, "filed": 2, "not_applicable": 1})
        self.assertEqual({c["key"]: c["value"] for c in d["by_category"]}, {"mca": 1, "gst": 2, "income_tax": 1})
        self.assertEqual(d["on_time_split"], {"on_time": 1, "late": 1, "overdue": 1, "pending": 1})
        months = {m["label"]: m for m in d["monthly"]}
        self.assertEqual(len(d["monthly"]), 12)
        self.assertEqual(d["monthly"][0]["label"], "Apr 26")
        self.assertEqual((months["May 26"]["on_time"], months["Jun 26"]["late"], months["Jul 26"]["overdue"], months["Dec 26"]["pending"]), (1, 1, 1, 1))
        self.assertEqual(sorted((w["label"], w["value"]) for w in d["workload"]), [("Unassigned", 1), (self.finance.get_username(), 1)])
        self.assertEqual([u["title"] for u in d["upcoming"]], ["C overdue", "D future"])
        self.assertEqual(d["fys"][0], FY)

    def test_score_is_none_before_anything_is_due_and_summary_shape(self):
        self.make("Future", D(2099, 1, 1))
        d = reports.dashboard(self.company, SimpleNamespace(user=self.admin), FY, today=D(2026, 11, 12))
        self.assertIsNone(d["score"])
        s = self.get("summary/")
        self.assertEqual(set(s), {"fy", "overdue", "due_soon", "due_in_30_days", "contracts_expiring", "licences_expiring", "hearings_30_days",
                                  "score", "attention", "next_due", "tax_due_30_days", "tax_overdue"})
        self.assertEqual(s["next_due"]["title"], "Future")
        dash = self.get("dashboard/", fy=FY)
        for key in ("fy", "fys", "score", "overdue", "upcoming", "by_category", "by_status", "monthly", "on_time_split", "workload", "expiry_watch"):
            self.assertIn(key, dash)
        self.get("dashboard/", fy="bogus", expect=400)

    def test_empty_company_summary(self):
        s = self.get("summary/")
        self.assertEqual((s["overdue"], s["attention"], s["next_due"], s["score"]), (0, 0, None, None))

    def test_summary_counts(self):
        self.adhoc("Overdue", day(-1))
        self.adhoc("Soon", day(10))
        self.adhoc("Month", day(25))
        s = self.get("summary/")
        self.assertEqual((s["overdue"], s["due_soon"], s["due_in_30_days"], s["attention"]), (1, 1, 2, 2))


class PermissionTests(Base):
    def test_every_role_on_read_and_write_endpoints(self):
        item = self.adhoc()
        reads = ["summary/", "dashboard/", "reminders/", "expiry-watch/", "registry/", "assignees/", "options/", "obligations/", "items/",
                 f"items/{item['id']}/", "legal-events/", "contracts/", "cases/", "policies/", "penalty/rules/"]
        can_view = {"admin": True, "partner": True, "viewer": True, "finance": True, "hr": False, "staff": False}
        clients = {"admin": self.api, "partner": self.api_partner, "viewer": self.api_viewer, "finance": self.api_finance, "hr": self.api_hr,
                   "staff": self.api_staff}
        for who, client in clients.items():
            for path in reads:
                r = client.get(f"{B}/{path}")
                self.assertEqual(r.status_code, 200 if can_view[who] else 403, f"{who} GET {path}")
        writes = [("items/", {"title": "x", "due_date": day(4)}), ("calendar/generate/", {"fy": FY}), ("contracts/", {"title": "T", "counterparty": "C", "effective_date": "2026-01-01"}),
                  ("cases/", {"title": "x"}), ("policies/", {"name": "x", "effective_date": "2026-01-01"}), ("legal-events/", {"event_type": "other", "title": "x", "event_date": "2026-01-01"}),
                  ("items/bulk/", {"action": "priority", "ids": [item["id"]], "priority": "low"}), ("obligations/seed/", {}),
                  ("legal-events/import-history/", {}), (f"items/{item['id']}/file/", {"filed_date": iso(TODAY)})]
        for who, client in clients.items():
            expected = 403 if who not in ("admin", "finance", "partner") else None
            for path, body in writes:
                r = client.post(f"{B}/{path}", body, format="json")
                if expected:
                    self.assertEqual(r.status_code, 403, f"{who} POST {path}")
                else:
                    self.assertIn(r.status_code, (200, 201), f"{who} POST {path}: {r.content}")
                    if path.startswith("items/") and path.endswith("/file/"):
                        self.post(f"items/{item['id']}/reopen/", {"reason": "retest"}, expect=200)

    def test_viewer_cannot_change_but_can_estimate_a_penalty(self):
        item = self.adhoc()
        self.assertEqual(self.api_viewer.patch(f"{B}/items/{item['id']}/", {"priority": "low"}, format="json").status_code, 403)
        self.assertEqual(self.api_viewer.delete(f"{B}/items/{item['id']}/").status_code, 403)
        r = self.api_viewer.post(f"{B}/penalty/estimate/", {"rule": "llp_per_day", "due_date": "2026-05-30", "filed_date": "2026-06-02"}, format="json")
        self.assertEqual(r.json()["amount"], "300.00")

    def test_anonymous_is_refused_everywhere(self):
        for path in ("summary/", "items/", "contracts/", "reminders/"):
            self.assertIn(self.anon.get(f"{B}/{path}").status_code, (401, 403))
        self.assertIn(self.anon.post(f"{B}/items/", {}, format="json").status_code, (401, 403))

    def test_attachments_follow_the_compliance_permissions(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        item = self.adhoc()
        data = {"entity_type": "compliance_filing", "entity_id": item["id"], "title": "Acknowledgement",
                "file": SimpleUploadedFile("ack.pdf", pdf_stub(), content_type="application/pdf")}
        self.assertEqual(self.api_viewer.post("/api/v1/attachments/", data, format="multipart").status_code, 403)
        data["file"] = SimpleUploadedFile("ack.pdf", pdf_stub(), content_type="application/pdf")
        self.assertEqual(self.api_finance.post("/api/v1/attachments/", data, format="multipart").status_code, 201)
        r = self.api_viewer.get("/api/v1/attachments/", {"entity_type": "compliance_filing", "entity_id": item["id"]})
        self.assertEqual((r.status_code, len(r.json())), (200, 1))
        self.assertEqual(self.api_staff.get("/api/v1/attachments/", {"entity_type": "contract", "entity_id": "x"}).status_code, 403)
        for entity in ("legal_event", "contract", "legal_case", "compliance_policy"):
            self.assertEqual(self.api.get("/api/v1/attachments/", {"entity_type": entity, "entity_id": "x"}).status_code, 200, entity)


class NotFoundTests(Base):
    def test_unknown_ids_are_404_not_500(self):
        ghost = uuid.uuid4()
        for method, path in (("get", f"items/{ghost}/"), ("patch", f"items/{ghost}/"), ("delete", f"items/{ghost}/"), ("post", f"items/{ghost}/file/"),
                             ("post", f"items/{ghost}/not-applicable/"), ("post", f"items/{ghost}/reopen/"), ("get", f"obligations/{ghost}/"),
                             ("patch", f"obligations/{ghost}/"), ("get", f"legal-events/{ghost}/"), ("delete", f"legal-events/{ghost}/"),
                             ("get", f"contracts/{ghost}/"), ("post", f"contracts/{ghost}/renew/"), ("post", f"contracts/{ghost}/terminate/"),
                             ("get", f"cases/{ghost}/"), ("get", f"policies/{ghost}/"), ("post", f"policies/{ghost}/acknowledge/"),
                             ("get", "items/not-a-uuid/")):
            r = getattr(self.api, method)(f"{B}/{path}", {}, format="json") if method != "get" else self.api.get(f"{B}/{path}")
            self.assertEqual(r.status_code, 404, f"{method} {path}")
        self.assertEqual(self.api.get(f"{B}/items/", {"obligation": "junk"}).json()["count"], 0)
