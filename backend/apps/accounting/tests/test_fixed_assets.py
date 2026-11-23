"""Fixed assets: the depreciation rules with hand-computed schedules, and every posting the register makes."""
import datetime
from decimal import Decimal

from django.test import SimpleTestCase

from apps.accounting.models import Account, AssetCategory, AssetDisposal, DepreciationLine, FixedAsset, JournalEntry
from apps.accounting.services import asset_reports, assets as asset_service, chart, depreciation as dep
from apps.accounts.models import Role
from apps.attachments import registry
from apps.company.models import CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user

from .test_accounting import A, Base, D

d = datetime.date
GHOST = "00000000-0000-0000-0000-000000000000"
ALL_ROLES = [Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER, Role.HR_MANAGER, Role.PROJECT_MANAGER, Role.EMPLOYEE]
CAN_VIEW = {Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER}
CAN_MANAGE = {Role.ADMIN, Role.MANAGING_PARTNER, Role.FINANCE_MANAGER}


def terms(cost, method, put, *, life=None, rate=None, salvage="0", half=False):
    return dep.Terms(cost=D(cost), method=method, life_years=D(life) if life else None, rate_percent=D(rate) if rate else None,
                     salvage_percent=D(salvage), half_year_rule=half, put_to_use=put)


def charges(rows):
    return [str(r["charge"]) for r in rows]


def fy_total(rows, fy):
    return sum((r["charge"] for r in rows if r["fy"] == fy), D("0"))


class ScheduleRuleTests(SimpleTestCase):
    """The month-by-month arithmetic, worked out by hand (see the rules at the top of services/depreciation.py)."""

    def test_straight_line_full_months_with_a_five_percent_residual(self):
        # cost 120,000, residual 5% = 6,000, depreciable 114,000 over 3 years = 38,000 a year = 3,166.666... a month.
        rows = dep.schedule(terms("120000", "slm", d(2026, 4, 1), life="3", salvage="5"))
        self.assertEqual(len(rows), 36)
        self.assertEqual(charges(rows)[:6], ["3166.67", "3166.66", "3166.67", "3166.67", "3166.66", "3166.67"])
        self.assertEqual(rows[2]["accumulated"], D("9500.00"))      # three months = 9,500.00 exactly, no drift
        self.assertEqual(fy_total(rows, "2026-27"), D("38000.00"))
        self.assertEqual(fy_total(rows, "2027-28"), D("38000.00"))
        self.assertEqual(fy_total(rows, "2028-29"), D("38000.00"))
        self.assertEqual((rows[-1]["accumulated"], rows[-1]["wdv"]), (D("114000.00"), D("6000.00")))  # stops at the residual value
        self.assertEqual(rows[-1]["period_start"], d(2029, 3, 1))

    def test_straight_line_first_month_is_charged_for_the_days_used(self):
        # cost 12,000, no residual, 1 year: 1,000 a month. Put to use 16 April: 15 of 30 days in April = 500.00.
        rows = dep.schedule(terms("12000", "slm", d(2026, 4, 16), life="1"))
        self.assertEqual(charges(rows), ["500.00"] + ["1000.00"] * 11 + ["500.00"])
        self.assertEqual(rows[-1]["period_start"], d(2027, 4, 1))
        self.assertEqual(rows[-1]["accumulated"], D("12000.00"))

    def test_straight_line_never_goes_past_the_depreciable_amount(self):
        # cost 10,000, residual 10% = 1,000, depreciable 9,000 over 2 years = 375 a month for 24 months, then nothing more.
        t = terms("10000", "slm", d(2026, 4, 1), life="2", salvage="10")
        rows = dep.schedule(t)
        self.assertEqual((len(rows), set(charges(rows))), (24, {"375.00"}))
        self.assertEqual(rows[-1]["wdv"], D("1000.00"))
        self.assertEqual(sum(r["charge"] for r in dep.schedule(t, through=d(2040, 3, 31))), D("9000.00"))

    def test_straight_line_stops_in_the_month_of_disposal_for_the_days_used(self):
        # 12,000 over 1 year, put to use 1 Apr; disposed 15 June: April 1,000 + May 1,000 + June 15/30 x 1,000 = 500.
        rows = dep.schedule(terms("12000", "slm", d(2026, 4, 1), life="1"), stop=d(2026, 6, 15))
        self.assertEqual(charges(rows), ["1000.00", "1000.00", "500.00"])
        self.assertEqual(rows[-1]["wdv"], D("9500.00"))

    def test_written_down_value_full_years(self):
        # 100,000 at 40%: year 1 = 40,000 (3,333.33 a month); year 2 = 40% of 60,000 = 24,000 (2,000); year 3 = 40% of 36,000 = 14,400 (1,200).
        rows = dep.schedule(terms("100000", "wdv", d(2026, 4, 1), rate="40"), through=d(2029, 3, 31))
        self.assertEqual(charges(rows)[:3], ["3333.33", "3333.34", "3333.33"])
        self.assertEqual([fy_total(rows, fy) for fy in ("2026-27", "2027-28", "2028-29")],
                         [D("40000.00"), D("24000.00"), D("14400.00")])
        self.assertEqual(set(charges(rows)[12:24]), {"2000.00"})
        self.assertEqual(set(charges(rows)[24:36]), {"1200.00"})

    def test_half_year_rule_halves_the_rate_when_used_for_under_180_days(self):
        # Put to use 1 Nov 2026: 1 Nov to 31 Mar is 151 days, under 180. Rule ON: half of 40% x 100,000 = 20,000 over the 5 months = 4,000 each.
        # Next year: 40% of (100,000 - 20,000) = 32,000, i.e. 2,666.67 a month.
        rows = dep.schedule(terms("100000", "wdv", d(2026, 11, 1), rate="40", half=True), through=d(2028, 3, 31))
        self.assertEqual(charges(rows)[:5], ["4000.00"] * 5)
        self.assertEqual((fy_total(rows, "2026-27"), fy_total(rows, "2027-28")), (D("20000.00"), D("32000.00")))

    def test_without_the_rule_the_first_year_is_pro_rata_by_months_used(self):
        # Same asset, rule OFF: 40% x 100,000 x 5/12 = 16,666.67; next year 40% of 83,333.33 = 33,333.33.
        rows = dep.schedule(terms("100000", "wdv", d(2026, 11, 1), rate="40", half=False), through=d(2028, 3, 31))
        self.assertEqual((fy_total(rows, "2026-27"), fy_total(rows, "2027-28")), (D("16666.67"), D("33333.33")))

    def test_half_year_rule_gives_the_full_rate_at_180_days_or_more(self):
        # Put to use 15 Sep 2026: 16 + 31 + 30 + 31 + 31 + 28 + 31 = 198 days. Rule ON: the full 40,000, spread over 6 + 16/30 months
        # of use, so September (16/30 of a month) is 40,000 x 8 / 98 = 3,265.31.
        rows = dep.schedule(terms("100000", "wdv", d(2026, 9, 15), rate="40", half=True), through=d(2027, 3, 31))
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["charge"], D("3265.31"))
        self.assertEqual(fy_total(rows, "2026-27"), D("40000.00"))
        # Rule OFF: pro-rata, 40,000 x (6 + 8/15)/12 = 21,777.78.
        off = dep.schedule(terms("100000", "wdv", d(2026, 9, 15), rate="40", half=False), through=d(2027, 3, 31))
        self.assertEqual(fy_total(off, "2026-27"), D("21777.78"))

    def test_written_down_value_stops_at_the_salvage_value(self):
        # 10,000, salvage 10% = 1,000, rate 60%: year 1 6,000; year 2 40% x... 60% of 4,000 = 2,400 (total 8,400); year 3 would be 960
        # (80 a month) but only 600 is left: seven months of 80 = 560, an eighth of 40, then nothing.
        rows = dep.schedule(terms("10000", "wdv", d(2026, 4, 1), rate="60", salvage="10"), through=d(2035, 3, 31))
        self.assertEqual([fy_total(rows, fy) for fy in ("2026-27", "2027-28", "2028-29")], [D("6000.00"), D("2400.00"), D("600.00")])
        self.assertEqual(charges(rows)[24:], ["80.00"] * 7 + ["40.00"])
        self.assertEqual((rows[-1]["accumulated"], rows[-1]["wdv"]), (D("9000.00"), D("1000.00")))

    def test_nothing_to_depreciate_when_the_whole_cost_is_residual(self):
        self.assertEqual(dep.schedule(terms("1000", "slm", d(2026, 4, 1), life="3", salvage="0.00")) != [], True)
        self.assertEqual(dep.schedule(dep.Terms(D("1000"), "slm", D("3"), None, D("99.99"), False, d(2026, 4, 1)))[-1]["accumulated"], D("0.10"))


class AssetBase(Base):
    def setUp(self):
        super().setUp()
        self.cat = self.category()

    # -- helpers
    def category(self, name="Computers", method="slm", life="3", rate="40", salvage="5", half=False, expect=201, **extra):
        r = self.fin.post(f"{A}/asset-categories/", {"name": name, "method": method, "useful_life_years": life, "rate_percent": rate,
                                                     "salvage_percent": salvage, "half_year_rule": half, **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def asset(self, name="Laptop", cost="120000", purchase="2026-04-01", put=None, category=None, acquisition="post", paid_from="bank",
              expect=201, **extra):
        payload = {"name": name, "category": (category or self.cat)["id"], "cost": cost, "purchase_date": purchase,
                   "put_to_use_date": put or purchase, "acquisition": acquisition, **extra}
        if acquisition == "post" and paid_from == "bank":
            payload["paid_from"] = str(self.acct("bank").id)
        elif paid_from not in (None, "bank"):
            payload["paid_from"] = paid_from
        r = self.fin.post(f"{A}/assets/", payload)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def do_run(self, period, to=None, expect=200, client=None):
        body = {"period": period, **({"to": to} if to else {})}
        r = (client or self.fin).post(f"{A}/depreciation/runs/", body)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def dispose(self, asset, kind="sold", date="2026-10-15", proceeds="110000", expect=201, **extra):
        body = {"kind": kind, "date": date, "proceeds": proceeds, **extra}
        if D(str(proceeds or 0)) > 0:
            body.setdefault("proceeds_account", str(self.acct("bank").id))
        r = self.fin.post(f"{A}/assets/{asset['id']}/dispose/", body)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def get_asset(self, asset):
        return self.fin.get(f"{A}/assets/{asset['id']}/").json()

    def entries(self, source="depreciation"):
        return JournalEntry.objects.filter(company=self.company, source=source)

    def audit(self, entity, action=None):
        qs = CompanyProfileAuditEvent.objects.filter(entity_type=entity)
        return qs.filter(action=action) if action else qs


class ChartAndNumberingTests(AssetBase):
    def test_disposal_accounts_exist_as_system_accounts_and_are_made_lazily_for_older_companies(self):
        self.assertEqual((self.acct("gain_on_disposal").type, self.acct("loss_on_disposal").type), ("income", "expense"))
        self.assertTrue(self.acct("gain_on_disposal").is_system and self.acct("loss_on_disposal").is_system)
        for key in ("gain_on_disposal", "loss_on_disposal"):                       # a company set up before these accounts existed
            Account.objects.filter(company=self.company, system_key=key).delete()
        made = chart.ensure_chart(self.company)
        self.assertEqual(sorted(a.system_key for a in made if a.system_key), ["gain_on_disposal", "loss_on_disposal"])
        self.assertEqual(chart.system_account(self.company, "loss_on_disposal").subtype, "other_expense")
        self.assertEqual(chart.ensure_chart(self.company), [])

    def test_asset_codes_are_numbered_by_the_company_sequence(self):
        a1 = self.asset(acquisition="later")
        a2 = self.asset(name="Printer", acquisition="later")
        self.assertRegex(a1["code"], r"^T2N-FAS-26-\d{4}-0001$")
        self.assertRegex(a2["code"], r"^T2N-FAS-26-\d{4}-0002$")
        r = self.fin.post(f"{A}/assets/", {"name": "x", "category": self.cat["id"], "cost": "100", "purchase_date": "2026-05-01", "code": "MINE-1"})
        self.assertNotEqual(r.json()["code"], "MINE-1")


class CategoryTests(AssetBase):
    def test_a_category_keeps_its_terms_and_default_accounts(self):
        c = self.cat
        self.assertEqual((c["method"], c["useful_life_years"], c["salvage_percent"], c["half_year_rule"]), ("slm", "3.00", "5.00", False))
        self.assertEqual((c["cost_account"]["code"], c["accumulated_account"]["code"], c["expense_account"]["code"]), ("1600", "1650", "5800"))

    def test_validation_of_the_terms_and_accounts(self):
        bad = [({"method": "slm", "useful_life_years": ""}, "useful_life_years"), ({"method": "wdv", "rate_percent": ""}, "rate_percent"),
               ({"method": "wdv", "rate_percent": "101"}, "rate_percent"), ({"method": "slm", "useful_life_years": "0"}, "useful_life_years"),
               ({"method": "slm", "salvage_percent": "100"}, "salvage_percent"), ({"method": "slm", "salvage_percent": "-1"}, "salvage_percent"),
               ({"method": "magic"}, "method"), ({"name": ""}, "name"), ({"name": "computers"}, "name"),
               ({"cost_account": str(self.acct("bank").id)}, "cost_account"),
               ({"accumulated_account": str(self.acct("1600").id)}, "accumulated_account"),
               ({"expense_account": str(self.acct("sales").id)}, "expense_account"),
               ({"half_year_rule": "yes"}, "half_year_rule")]
        for i, (over, field) in enumerate(bad):
            payload = {"name": f"Cat {i}", "method": "slm", "useful_life_years": "5", "salvage_percent": "5", **over}
            r = self.fin.post(f"{A}/asset-categories/", payload)
            self.assertEqual(r.status_code, 400, (over, r.content))
            self.assertIn(field, r.json(), over)

    def test_standard_categories_are_added_once(self):
        r = self.fin.post(f"{A}/asset-categories/setup/")
        self.assertEqual(r.json()["created"], 3)
        names = sorted(c["name"] for c in self.fin.get(f"{A}/asset-categories/").json())
        self.assertEqual(names, ["Computers", "Computers and IT equipment", "Furniture and fittings", "Office equipment"])
        self.assertEqual(self.fin.post(f"{A}/asset-categories/setup/").json()["created"], 0)
        furniture = next(c for c in self.fin.get(f"{A}/asset-categories/").json() if c["name"] == "Furniture and fittings")
        self.assertEqual((furniture["cost_account"]["code"], furniture["useful_life_years"], furniture["rate_percent"]), ("1610", "10.00", "10.00"))

    def test_edit_delete_and_the_lock_once_assets_are_in_the_books(self):
        r = self.fin.patch(f"{A}/asset-categories/{self.cat['id']}/", {"useful_life_years": "5", "name": "IT equipment"})
        self.assertEqual((r.status_code, r.json()["useful_life_years"], r.json()["name"]), (200, "5.00", "IT equipment"))
        spare = self.category(name="Spare")
        self.assertEqual(self.fin.delete(f"{A}/asset-categories/{spare['id']}/").status_code, 204)
        self.asset()
        other = self.acct("1610")
        r = self.fin.patch(f"{A}/asset-categories/{self.cat['id']}/", {"cost_account": str(other.id)})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.fin.delete(f"{A}/asset-categories/{self.cat['id']}/").status_code, 400)
        self.assertEqual(self.fin.patch(f"{A}/asset-categories/{self.cat['id']}/", {"is_active": False}).status_code, 200)
        self.asset(acquisition="later", expect=400)           # a switched-off category cannot take new assets
        self.assertEqual(self.audit("asset_category").count(), 5)   # 2 created, 2 edited, 1 deleted


class AssetRecordTests(AssetBase):
    def test_create_validates_what_was_entered(self):
        cases = [({"name": ""}, "name"), ({"cost": "0"}, "cost"), ({"cost": "-5"}, "cost"), ({"cost": "10.123"}, "cost"),
                 ({"category": GHOST}, "category"), ({"purchase_date": ""}, "purchase_date"), ({"purchase_date": "01-04-2026"}, "purchase_date"),
                 ({"put_to_use_date": "2026-03-01"}, "put_to_use_date"), ({"acquisition": "maybe"}, "acquisition"),
                 ({"vendor": GHOST}, "vendor"), ({"bill": GHOST}, "bill"), ({"vendor": "not-an-id"}, "vendor"),
                 ({"salvage_percent": "100"}, "salvage_percent"), ({"method": "wdv", "rate_percent": ""}, "rate_percent")]
        for over, field in cases:
            payload = {"name": "Laptop", "category": self.cat["id"], "cost": "1000", "purchase_date": "2026-04-01", "acquisition": "later", **over}
            if over.get("method") == "wdv":
                payload["rate_percent"] = ""
            r = self.fin.post(f"{A}/assets/", payload)
            self.assertEqual(r.status_code, 400, (over, r.content))
            self.assertIn(field, r.json(), (over, r.json()))
        self.assertEqual(FixedAsset.objects.count(), 0)

    def test_an_asset_copies_its_terms_from_the_category_and_can_override_them(self):
        a = self.asset(acquisition="later")
        self.assertEqual((a["method"], a["useful_life_years"], a["salvage_percent"], a["salvage_value"], a["depreciable"]),
                         ("slm", "3.00", "5.00", "6000.00", "114000.00"))
        b = self.asset(name="Server", acquisition="later", useful_life_years="6", salvage_percent="10")
        self.assertEqual((b["useful_life_years"], b["salvage_value"], b["depreciable"]), ("6.00", "12000.00", "108000.00"))
        self.fin.patch(f"{A}/asset-categories/{self.cat['id']}/", {"useful_life_years": "8"})
        self.assertEqual(self.get_asset(a)["useful_life_years"], "3.00")      # a category change does not rewrite existing assets

    def test_vendor_and_bill_are_soft_links_with_names_kept(self):
        v = self.fin.post("/api/v1/purchases/vendors/", {"name": "Dell India", "gst_treatment": "unregistered"})
        self.assertEqual(v.status_code, 201, v.content)
        a = self.asset(acquisition="post", paid_from="payable", vendor=v.json()["id"])
        self.assertEqual(a["vendor"], {"id": v.json()["id"], "name": "Dell India"})
        line = JournalEntry.objects.get(source="fixed_asset", source_id__startswith="acq:").lines.get(account=self.acct("payable"))
        self.assertEqual((line.party_type, line.party_id), ("vendor", v.json()["id"]))

    def test_edit_rules_and_delete(self):
        a = self.asset(acquisition="later")
        r = self.fin.patch(f"{A}/assets/{a['id']}/", {"name": "Dev laptop", "location": "Bengaluru", "serial_number": "SN-1", "cost": "130000"})
        self.assertEqual((r.status_code, r.json()["name"], r.json()["cost"], r.json()["location"]), (200, "Dev laptop", "130000.00", "Bengaluru"))
        self.assertEqual(self.fin.delete(f"{A}/assets/{a['id']}/").status_code, 204)
        b = self.asset()                                                         # purchase posted: money fields are locked
        self.assertEqual(self.fin.patch(f"{A}/assets/{b['id']}/", {"cost": "1"}).status_code, 400)
        self.assertEqual(self.fin.patch(f"{A}/assets/{b['id']}/", {"location": "Mumbai"}).status_code, 200)
        self.assertEqual(self.fin.delete(f"{A}/assets/{b['id']}/").status_code, 400)
        c = self.asset(name="Old PC", acquisition="existing")
        self.do_run("2026-04")
        r = self.fin.patch(f"{A}/assets/{c['id']}/", {"put_to_use_date": "2026-05-01"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Depreciation has been posted", str(r.json()))


class AcquisitionTests(AssetBase):
    def test_recording_the_purchase_posts_dr_asset_cr_bank(self):
        a = self.asset(acquisition="later")
        self.assertEqual((a["acquisition"], a["in_books"]), ("pending", False))
        self.assertEqual(self.entries("fixed_asset").count(), 0)
        r = self.fin.post(f"{A}/assets/{a['id']}/acquisition/", {"mode": "post", "paid_from": str(self.acct("bank").id)})
        self.assertEqual((r.status_code, r.json()["acquisition"]), (200, "posted"), r.content)
        e = self.entries("fixed_asset").get()
        self.assertEqual((e.date, e.reference, e.source_id.startswith("acq:")), (d(2026, 4, 1), a["code"], True))
        self.assertEqual(self.lines(e), [("1010", D("0"), D("120000")), ("1600", D("120000"), D("0"))])
        self.assertEqual((self.bal("1600"), self.bal("bank")), (D("120000"), D("-120000")))
        self.tb()
        r = self.fin.post(f"{A}/assets/{a['id']}/acquisition/", {"mode": "post"})        # no double posting
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.entries("fixed_asset").count(), 1)

    def test_default_credit_is_accounts_payable_and_only_money_accounts_are_accepted(self):
        a = self.asset(acquisition="later")
        r = self.fin.post(f"{A}/assets/{a['id']}/acquisition/", {"mode": "post", "paid_from": str(self.acct("sales").id)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("paid_from", r.json())
        self.fin.post(f"{A}/assets/{a['id']}/acquisition/", {"mode": "post"})
        self.assertEqual(self.bal("payable"), D("-120000"))
        self.tb()

    def test_an_asset_already_in_the_books_posts_nothing(self):
        # e.g. created from a vendor bill that already debited the asset account: flag it, never double post.
        a = self.asset(acquisition="existing")
        self.assertEqual((a["acquisition"], a["in_books"]), ("existing", True))
        self.assertEqual(self.entries("fixed_asset").count(), 0)
        r = self.fin.post(f"{A}/assets/{a['id']}/acquisition/", {"mode": "post"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.entries("fixed_asset").count(), 0)
        self.do_run("2026-04")                                                  # depreciation works for it
        self.assertEqual(self.bal("5800"), D("3166.67"))

    def test_undo_reverses_the_entry_and_the_purchase_can_be_posted_again(self):
        a = self.asset()
        r = self.fin.post(f"{A}/assets/{a['id']}/acquisition/undo/", {})
        self.assertEqual(r.status_code, 400)                                  # a reason is needed
        r = self.fin.post(f"{A}/assets/{a['id']}/acquisition/undo/", {"change_reason": "Wrong account"})
        self.assertEqual((r.status_code, r.json()["acquisition"]), (200, "pending"), r.content)
        self.assertEqual((self.bal("1600"), self.bal("bank")), (D("0"), D("0")))
        self.tb()
        r = self.fin.post(f"{A}/assets/{a['id']}/acquisition/", {"mode": "post", "paid_from": str(self.acct("cash").id)})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((self.bal("1600"), self.bal("cash")), (D("120000"), D("-120000")))
        self.tb()

    def test_the_purchase_cannot_be_undone_while_depreciation_stands(self):
        a = self.asset()
        run = self.do_run("2026-04")["runs"][0]
        r = self.fin.post(f"{A}/assets/{a['id']}/acquisition/undo/", {"change_reason": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Reverse the depreciation runs", str(r.json()))
        self.fin.post(f"{A}/depreciation/runs/{run['id']}/reverse/", {"change_reason": "redo"})
        self.assertEqual(self.fin.post(f"{A}/assets/{a['id']}/acquisition/undo/", {"change_reason": "x"}).status_code, 200)


class DepreciationRunTests(AssetBase):
    def test_a_month_posts_dr_depreciation_cr_accumulated_and_the_books_balance(self):
        a = self.asset()
        out = self.do_run("2026-04")
        self.assertEqual((out["total"], len(out["runs"])), ("3166.67", 1))
        e = self.entries().get()
        self.assertEqual((e.date, e.reference), (d(2026, 4, 30), "DEP-2026-04"))
        self.assertEqual(self.lines(e), [("1650", D("0"), D("3166.67")), ("5800", D("3166.67"), D("0"))])
        self.assertEqual((self.bal("5800"), self.bal("1650")), (D("3166.67"), D("-3166.67")))
        self.assertEqual((self.get_asset(a)["accumulated"], self.get_asset(a)["wdv"]), ("3166.67", "116833.33"))
        self.tb()

    def test_running_the_same_period_again_never_charges_twice(self):
        self.asset()
        self.do_run("2026-04", "2026-06")
        before = (self.entries().count(), DepreciationLine.objects.count(), self.bal("5800"))
        again = self.do_run("2026-04", "2026-06")
        self.assertEqual((again["runs"], again["total"]), ([], "0.00"))
        self.do_run("2026-05")
        self.assertEqual((self.entries().count(), DepreciationLine.objects.count(), self.bal("5800")), before)
        self.assertEqual(before[2], D("9500.00"))                             # 3,166.67 + 3,166.66 + 3,166.67
        self.tb()

    def test_a_range_skips_months_already_done_and_posts_one_entry_per_month(self):
        self.asset()
        self.do_run("2026-04")
        out = self.do_run("2026-04", "2026-06")
        self.assertEqual([r["label"] for r in out["runs"]], ["May 2026", "June 2026"])
        self.assertEqual([r["total"] for r in out["runs"]], ["3166.66", "3166.67"])
        self.assertEqual(self.entries().count(), 3)

    def test_a_new_asset_added_later_is_picked_up_by_running_the_month_again(self):
        self.asset()
        self.do_run("2026-05")
        self.asset(name="Printer", cost="12000", purchase="2026-05-01", category=self.category(name="Printers", life="1", salvage="0"))
        out = self.do_run("2026-05")                                              # the first asset is skipped, the printer charged
        self.assertEqual((out["total"], out["runs"][0]["asset_count"]), ("1000.00", 1))

    def test_assets_not_in_the_books_yet_are_skipped_with_a_reason(self):
        a = self.asset(acquisition="later")
        pv = self.fin.get(f"{A}/depreciation/preview/", {"period": "2026-04"}).json()
        self.assertEqual((pv["months"][0]["rows"], pv["total"]), ([], "0.00"))
        self.assertIn("not in the books yet", pv["months"][0]["skipped"][0]["reason"])
        out = self.do_run("2026-04")
        self.assertEqual((out["runs"], len(out["skipped"])), ([], 1))
        self.assertEqual(self.entries().count(), 0)
        self.fin.post(f"{A}/assets/{a['id']}/acquisition/", {"mode": "post"})
        self.assertEqual(self.do_run("2026-04")["total"], "3166.67")

    def test_preview_matches_the_run_and_posts_nothing(self):
        self.asset()
        pv = self.fin.get(f"{A}/depreciation/preview/", {"period": "2026-04", "to": "2026-05"}).json()
        self.assertEqual([m["total"] for m in pv["months"]], ["3166.67", "3166.66"])
        self.assertEqual(pv["total"], "6333.33")
        self.assertEqual((pv["months"][1]["rows"][0]["accumulated"], pv["months"][1]["rows"][0]["wdv"]), ("6333.33", "113666.67"))
        self.assertEqual(self.entries().count(), 0)
        self.assertEqual(self.do_run("2026-04", "2026-05")["total"], "6333.33")

    def test_the_month_before_the_asset_is_put_to_use_charges_nothing(self):
        self.asset(purchase="2026-04-01", put="2026-06-01")
        self.assertEqual(self.do_run("2026-04", "2026-05")["runs"], [])
        self.assertEqual(self.do_run("2026-06")["total"], "3166.67")

    def test_months_that_have_not_started_and_bad_input_are_refused(self):
        self.asset()
        for body in ({"period": "2999-01"}, {"period": ""}, {"period": "2026-13"}, {"period": "2026-05", "to": "2026-04"},
                     {"period": "2026-04", "to": "2030-04"}):
            r = self.fin.post(f"{A}/depreciation/runs/", body)
            self.assertEqual(r.status_code, 400, body)
        self.assertEqual(self.entries().count(), 0)

    def test_depreciation_never_exceeds_the_depreciable_amount(self):
        # 12,000 over one year: after twelve months it stops, however many more months are run.
        cat = self.category(name="Short", life="1", salvage="0")
        a = self.asset(cost="12000", category=cat)
        out = asset_service.run_depreciation(self.company, self.admin, d(2026, 4, 1), d(2027, 9, 1), today=d(2030, 1, 1))
        self.assertEqual((len(out["runs"]), out["total"]), (12, D("12000.00")))
        again = asset_service.run_depreciation(self.company, self.admin, d(2027, 4, 1), d(2028, 3, 1), today=d(2030, 1, 1))
        self.assertEqual(again["runs"], [])
        self.assertEqual((self.get_asset(a)["accumulated"], self.get_asset(a)["wdv"]), ("12000.00", "0.00"))
        self.assertEqual(self.bal("5800", "2028-03-31"), D("12000.00"))
        self.tb("2028-03-31")

    def test_salvage_stops_the_charge_in_the_books_too(self):
        a = self.asset(cost="10000", category=self.category(name="Residual", life="2", salvage="10"))
        asset_service.run_depreciation(self.company, self.admin, d(2026, 4, 1), d(2028, 6, 1), today=d(2030, 1, 1))
        self.assertEqual(self.get_asset(a)["accumulated"], "9000.00")
        self.assertEqual(self.get_asset(a)["wdv"], "1000.00")                  # the residual value stays on the books

    def test_a_reversal_takes_the_charge_back_and_frees_the_month(self):
        a = self.asset()
        run = self.do_run("2026-04", "2026-05")["runs"][0]
        r = self.fin.post(f"{A}/depreciation/runs/{run['id']}/reverse/", {})
        self.assertEqual(r.status_code, 400)                                    # a reason is needed
        r = self.fin.post(f"{A}/depreciation/runs/{run['id']}/reverse/", {"change_reason": "Wrong rate"})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "reversed"), r.content)
        self.assertEqual(self.bal("5800"), D("3166.66"))                       # only May is left
        self.assertEqual(self.get_asset(a)["accumulated"], "3166.66")
        self.assertEqual(self.entries("reversal").count(), 1)
        self.assertEqual(self.fin.post(f"{A}/depreciation/runs/{run['id']}/reverse/", {"change_reason": "again"}).status_code, 400)
        self.tb()
        out = self.do_run("2026-04")                                               # April can be charged again
        self.assertEqual(out["total"], "3166.67")
        self.assertEqual(self.get_asset(a)["accumulated"], "6333.33")
        self.tb()

    def test_depreciation_entries_cannot_be_reversed_from_the_journal(self):
        self.asset()
        self.do_run("2026-04")
        e = self.entries().get()
        r = self.fin.post(f"{A}/journal/{e.id}/reverse/", {"change_reason": "x"})
        self.assertEqual(r.status_code, 400)

    def test_runs_list_and_detail(self):
        a = self.asset()
        self.do_run("2026-04")
        runs = self.fin.get(f"{A}/depreciation/runs/").json()
        self.assertEqual((len(runs), runs[0]["label"], runs[0]["total"], runs[0]["asset_count"]), (1, "April 2026", "3166.67", 1))
        det = self.fin.get(f"{A}/depreciation/runs/{runs[0]['id']}/").json()
        self.assertEqual((det["lines"][0]["asset"]["code"], det["lines"][0]["amount"]), (a["code"], "3166.67"))

    def test_the_half_year_rule_is_a_category_setting_that_reaches_the_schedule(self):
        wdv = self.category(name="Plant", method="wdv", rate="40", salvage="0", half=True)
        a = self.asset(cost="100000", purchase="2026-11-01", category=wdv)
        sc = self.fin.get(f"{A}/assets/{a['id']}/schedule/", {"through": "2028-03-31"}).json()
        self.assertEqual([r["charge"] for r in sc["rows"][:5]], ["4000.00"] * 5)
        self.assertEqual([(y["fy"], y["charge"]) for y in sc["years"]], [("2026-27", "20000.00"), ("2027-28", "32000.00")])
        off = self.category(name="Plant no rule", method="wdv", rate="40", salvage="0", half=False)
        b = self.asset(name="Press", cost="100000", purchase="2026-11-01", category=off)
        sc = self.fin.get(f"{A}/assets/{b['id']}/schedule/", {"through": "2028-03-31"}).json()
        self.assertEqual([(y["fy"], y["charge"]) for y in sc["years"]], [("2026-27", "16666.67"), ("2027-28", "33333.33")])

    def test_schedule_marks_posted_months(self):
        a = self.asset()
        self.do_run("2026-04", "2026-05")
        sc = self.fin.get(f"{A}/assets/{a['id']}/schedule/").json()
        self.assertEqual((len(sc["rows"]), sc["total"], sc["asset"]["depreciable"]), (36, "114000.00", "114000.00"))
        self.assertEqual([r["state"] for r in sc["rows"][:3]], ["posted", "posted", "due"])
        self.assertEqual(sc["rows"][35]["state"], "upcoming")
        self.assertEqual([(y["fy"], y["charge"]) for y in sc["years"]], [("2026-27", "38000.00"), ("2027-28", "38000.00"), ("2028-29", "38000.00")])

    def test_editing_terms_is_locked_while_depreciation_stands_and_opens_after_reversal(self):
        a = self.asset(acquisition="existing")
        run = self.do_run("2026-04")["runs"][0]
        self.assertEqual(self.fin.patch(f"{A}/assets/{a['id']}/", {"useful_life_years": "5"}).status_code, 400)
        self.fin.post(f"{A}/depreciation/runs/{run['id']}/reverse/", {"change_reason": "life changed"})
        r = self.fin.patch(f"{A}/assets/{a['id']}/", {"useful_life_years": "5"})
        self.assertEqual((r.status_code, r.json()["useful_life_years"]), (200, "5.00"))
        self.assertEqual(self.do_run("2026-04")["total"], "1900.00")             # 114,000 / 5 / 12


class DisposalTests(AssetBase):
    """Asset 120,000 (5% residual, 3 years) bought 1 Apr 2026 and depreciated April to September: 19,000.00 accumulated.
    Disposed 15 Oct 2026: October's part charge is 3,166.666... x 15/31 = 1,532.258..., so 20,532.26 in all
    and the written-down value is 120,000 - 20,532.26 = 99,467.74."""

    def setUp(self):
        super().setUp()
        self.a = self.asset()
        self.do_run("2026-04", "2026-09")

    def test_the_figures_before_posting(self):
        r = self.fin.get(f"{A}/assets/{self.a['id']}/disposal-preview/", {"date": "2026-10-15", "proceeds": "110000"}).json()
        self.assertEqual((r["final_depreciation"], r["accumulated"], r["wdv"], r["gain_loss"]),
                         ("1532.26", "20532.26", "99467.74", "10532.26"))
        self.assertEqual(self.entries("fixed_asset").count(), 1)               # nothing posted by a preview

    def test_a_sale_above_book_value_posts_a_gain(self):
        disposal = self.dispose(self.a, proceeds="110000")
        self.assertEqual((disposal["wdv"], disposal["gain_loss"], disposal["final_depreciation"]), ("99467.74", "10532.26", "1532.26"))
        e = self.entries("fixed_asset").get(source_id=f"disp:{disposal['id']}")
        self.assertEqual(self.lines(e), sorted([("1010", D("110000"), D("0")), ("1650", D("19000"), D("0")), ("5800", D("1532.26"), D("0")),
                                               ("1600", D("0"), D("120000")), ("4600", D("0"), D("10532.26"))]))
        self.assertEqual(e.date, d(2026, 10, 15))
        self.assertEqual((self.bal("1600"), self.bal("1650"), self.bal("4600")), (D("0"), D("0"), D("-10532.26")))
        self.assertEqual((self.bal("bank"), self.bal("5800")), (D("-120000") + D("110000"), D("19000") + D("1532.26")))
        self.tb()
        asset = self.get_asset(self.a)
        self.assertEqual((asset["status"], asset["accumulated"], asset["wdv"]), ("sold", "20532.26", "0.00"))
        self.assertEqual(asset["disposal"]["gain_loss"], "10532.26")

    def test_a_sale_below_book_value_posts_a_loss(self):
        disposal = self.dispose(self.a, proceeds="90000")
        self.assertEqual(disposal["gain_loss"], "-9467.74")
        e = self.entries("fixed_asset").get(source_id=f"disp:{disposal['id']}")
        self.assertEqual(self.lines(e), sorted([("1010", D("90000"), D("0")), ("1650", D("19000"), D("0")), ("5800", D("1532.26"), D("0")),
                                               ("5970", D("9467.74"), D("0")), ("1600", D("0"), D("120000"))]))
        self.assertEqual((self.bal("5970"), self.bal("4600")), (D("9467.74"), D("0")))
        self.tb()

    def test_a_sale_at_book_value_has_neither_gain_nor_loss(self):
        disposal = self.dispose(self.a, proceeds="99467.74")
        self.assertEqual(disposal["gain_loss"], "0.00")
        self.assertEqual((self.bal("4600"), self.bal("5970")), (D("0"), D("0")))
        self.tb()

    def test_a_write_off_charges_the_whole_book_value_as_a_loss(self):
        disposal = self.dispose(self.a, kind="written_off", proceeds="0")
        self.assertEqual((disposal["gain_loss"], disposal["wdv"]), ("-99467.74", "99467.74"))
        self.assertEqual((self.bal("5970"), self.bal("1600"), self.bal("1650")), (D("99467.74"), D("0"), D("0")))
        self.assertEqual(self.get_asset(self.a)["status"], "written_off")
        self.tb()

    def test_scrapping_for_a_scrap_value_goes_to_cash(self):
        disposal = self.dispose(self.a, kind="disposed", proceeds="5000", proceeds_account=str(self.acct("cash").id))
        self.assertEqual(disposal["gain_loss"], "-94467.74")
        self.assertEqual((self.bal("cash"), self.get_asset(self.a)["status"]), (D("5000"), "disposed"))
        self.tb()

    def test_a_credit_sale_debits_receivable(self):
        self.dispose(self.a, proceeds="110000", proceeds_account=str(self.acct("receivable").id))
        self.assertEqual(self.bal("receivable"), D("110000"))
        self.tb()

    def test_the_last_day_of_a_posted_month_adds_no_part_charge(self):
        disposal = self.dispose(self.a, date="2026-09-30", proceeds="105000")
        self.assertEqual((disposal["final_depreciation"], disposal["accumulated"], disposal["wdv"], disposal["gain_loss"]),
                         ("0.00", "19000.00", "101000.00", "4000.00"))
        self.assertEqual(DepreciationLine.objects.filter(asset_id=self.a["id"], active=True).count(), 6)
        self.tb()

    def test_months_never_run_are_charged_as_part_of_the_disposal(self):
        b = self.asset(name="Tablet", purchase="2026-04-01")                   # nothing run for it except April to September above
        c = self.asset(name="Monitor", purchase="2026-10-01", cost="12000", category=self.category(name="Screens", life="1", salvage="0"))
        disposal = self.dispose(c, date="2026-10-31", proceeds="9000")
        self.assertEqual((disposal["final_depreciation"], disposal["wdv"], disposal["gain_loss"]), ("1000.00", "11000.00", "-2000.00"))
        self.tb()
        self.assertEqual(b["status"], "in_use")

    def test_depreciation_posted_for_the_full_month_blocks_a_mid_month_disposal(self):
        r = self.dispose(self.a, date="2026-09-15", proceeds="100000", expect=400)
        self.assertIn("date", r)
        self.assertIn("Reverse that run", str(r["date"]))
        self.do_run("2026-10")
        r = self.dispose(self.a, date="2026-09-30", proceeds="100000", expect=400)   # October already charged: later months stand
        self.assertIn("Reverse those runs first", str(r["date"]))

    def test_validation(self):
        self.dispose(self.a, kind="sold", proceeds="0", expect=400)
        self.dispose(self.a, kind="written_off", proceeds="500", expect=400)
        self.dispose(self.a, kind="melted", proceeds="0", expect=400)
        self.dispose(self.a, date="2026-03-01", proceeds="1", expect=400)               # before it was put to use
        self.dispose(self.a, date="", proceeds="1", expect=400)
        r = self.fin.post(f"{A}/assets/{self.a['id']}/dispose/", {"kind": "sold", "date": "2026-10-15", "proceeds": "1000"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("proceeds_account", r.json())
        r = self.fin.post(f"{A}/assets/{self.a['id']}/dispose/", {"kind": "sold", "date": "2026-10-15", "proceeds": "1000",
                                                                  "proceeds_account": str(self.acct("sales").id)})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(AssetDisposal.objects.count(), 0)
        later = self.asset(name="Not booked", acquisition="later")
        self.dispose(later, kind="written_off", proceeds="0", expect=400)

    def test_a_disposed_asset_leaves_depreciation_and_cannot_be_disposed_twice(self):
        self.dispose(self.a)
        self.dispose(self.a, expect=400)
        out = self.do_run("2026-11")
        self.assertEqual(out["runs"], [])
        self.assertEqual(self.fin.patch(f"{A}/assets/{self.a['id']}/", {"cost": "1"}).status_code, 400)
        self.assertEqual(self.fin.patch(f"{A}/assets/{self.a['id']}/", {"location": "Sold to Ravi"}).status_code, 200)

    def test_reversing_a_disposal_brings_the_asset_back(self):
        disposal = self.dispose(self.a, proceeds="110000")
        r = self.fin.post(f"{A}/asset-disposals/{disposal['id']}/reverse/", {})
        self.assertEqual(r.status_code, 400)
        r = self.fin.post(f"{A}/asset-disposals/{disposal['id']}/reverse/", {"change_reason": "Buyer cancelled"})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "reversed"), r.content)
        asset = self.get_asset(self.a)
        self.assertEqual((asset["status"], asset["accumulated"], asset["disposal"]), ("in_use", "19000.00", None))
        self.assertEqual((self.bal("1600"), self.bal("1650"), self.bal("4600"), self.bal("5800")),
                         (D("120000"), D("-19000"), D("0"), D("19000")))
        self.assertEqual(self.bal("bank"), D("-120000"))
        self.tb()
        self.assertEqual(self.fin.post(f"{A}/asset-disposals/{disposal['id']}/reverse/", {"change_reason": "x"}).status_code, 400)
        self.assertEqual(self.do_run("2026-10")["total"], "3166.67")                # October can be depreciated again
        self.dispose(self.a, date="2026-11-30", proceeds="100000")              # and the asset can be disposed of again
        self.tb()

    def test_an_earlier_run_cannot_be_reversed_after_the_asset_is_disposed(self):
        self.dispose(self.a)
        run = self.fin.get(f"{A}/depreciation/runs/").json()[-1]
        r = self.fin.post(f"{A}/depreciation/runs/{run['id']}/reverse/", {"change_reason": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Reverse the disposal first", str(r.json()))


class ReportTests(AssetBase):
    def setUp(self):
        super().setUp()
        self.a = self.asset()                                                  # 120,000 laptop
        self.b = self.asset(name="Chair", cost="30000", purchase="2026-06-10", category=self.category(name="Furniture", life="10", salvage="0"))
        self.later = self.asset(name="Pending", cost="5000", purchase="2026-07-01", acquisition="later")
        self.do_run("2026-04", "2026-09")

    def reg(self, **params):
        r = self.fin.get(f"{A}/reports/asset-register/", params)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_register_as_on_a_date(self):
        # Laptop: April to September = 19,000.00. Chair 30,000 over 10 years = 250 a month; from 10 June (21 of 30 days = 175.00),
        # July, August, September 250 each: 925.00 by 30 September.
        r = self.reg(as_on="2026-09-30")
        rows = {x["name"]: x for x in r["rows"]}
        self.assertEqual((rows["Laptop"]["cost"], rows["Laptop"]["accumulated"], rows["Laptop"]["wdv"]), ("120000.00", "19000.00", "101000.00"))
        self.assertEqual((rows["Chair"]["accumulated"], rows["Chair"]["wdv"]), ("925.00", "29075.00"))
        self.assertEqual(rows["Pending"]["in_books"], False)
        self.assertEqual((r["totals"]["cost"], r["totals"]["accumulated"], r["totals"]["wdv"], r["totals"]["count"]),
                         ("155000.00", "19925.00", "135075.00", 3))
        cats = {c["name"]: c for c in r["by_category"]}
        self.assertEqual((cats["Computers"]["wdv"], cats["Furniture"]["cost"]), ("106000.00", "30000.00"))   # laptop 101,000 + pending 5,000
        # earlier dates: only what had happened by then
        r = self.reg(as_on="2026-06-30")
        rows = {x["name"]: x for x in r["rows"]}
        self.assertEqual((rows["Laptop"]["accumulated"], rows["Chair"]["accumulated"]), ("9500.00", "175.00"))
        self.assertNotIn("Pending", rows)                                      # bought in July
        self.assertEqual([x["name"] for x in self.reg(as_on="2026-05-31")["rows"]], ["Laptop"])

    def test_register_agrees_with_the_ledger(self):
        chk = self.reg(as_on="2026-09-30")["ledger_check"]
        self.assertEqual((chk["register_cost"], chk["ledger_cost"], chk["register_accumulated"], chk["ledger_accumulated"], chk["agrees"]),
                         ("150000.00", "150000.00", "19925.00", "19925.00", True))

    def test_register_filters(self):
        self.assertEqual([x["name"] for x in self.reg(category=self.cat["id"])["rows"]], ["Laptop", "Pending"])
        self.assertEqual([x["name"] for x in self.reg(q="chair")["rows"]], ["Chair"])
        self.dispose(self.b, date="2026-10-31", proceeds="29000")
        r = self.reg(as_on="2026-11-30")
        self.assertEqual({x["name"]: x["status"] for x in r["rows"]}, {"Laptop": "in_use", "Chair": "sold", "Pending": "in_use"})
        self.assertEqual(r["totals"]["count"], 2)
        self.assertEqual([x["name"] for x in self.reg(status="sold", as_on="2026-11-30")["rows"]], ["Chair"])
        before = {x["name"]: x["status"] for x in self.reg(as_on="2026-10-15")["rows"]}
        self.assertEqual(before["Chair"], "in_use")                             # not sold yet on that date

    def test_depreciation_by_financial_year(self):
        r = self.fin.get(f"{A}/reports/depreciation-by-year/").json()
        y = {x["fy"]: x for x in r["years"]}
        # posted April to September: 19,000 + 925; scheduled for the year is the whole schedule
        self.assertEqual((y["2026-27"]["posted"], y["2026-27"]["scheduled"]), ("19925.00", "41612.50"))   # scheduled: laptop 38,000 + chair 2,425 (175 + 9 x 250) + pending 1,187.50 (9 x 4,750 / 36)
        cats = {c["name"]: c for c in y["2026-27"]["categories"]}
        self.assertEqual((cats["Computers"]["posted"], cats["Furniture"]["posted"]), ("19000.00", "925.00"))
        self.assertEqual(y["2027-28"]["posted"], "0.00")
        self.assertGreater(D(y["2027-28"]["scheduled"]), 0)
        rows = {x["name"]: x for x in r["assets"]}
        self.assertEqual(rows["Laptop"]["by_fy"], {"2026-27": "19000.00"})
        one = self.fin.get(f"{A}/reports/depreciation-by-year/", {"fy": "2027-28"}).json()
        self.assertEqual([x["fy"] for x in one["years"]], ["2027-28"])

    def test_disposals_report(self):
        self.dispose(self.a, date="2026-10-15", proceeds="110000")             # gain 10,532.26
        self.dispose(self.b, date="2026-10-31", proceeds="29000")              # Chair: 175 + 750 + Oct 250 = 1,175 -> wdv 28,825.00, gain 175.00
        r = self.fin.get(f"{A}/reports/asset-disposals/", {"from": "2026-04-01", "to": "2027-03-31"}).json()
        self.assertEqual([x["asset"]["name"] for x in r["rows"]], ["Laptop", "Chair"])
        self.assertEqual((r["rows"][1]["wdv"], r["rows"][1]["gain_loss"]), ("28825.00", "175.00"))
        self.assertEqual((r["total_proceeds"], r["total_gain"], r["total_loss"], r["net"]), ("139000.00", "10707.26", "0.00", "10707.26"))
        r = self.fin.get(f"{A}/reports/asset-disposals/", {"from": "2026-10-20", "to": "2026-12-31"}).json()
        self.assertEqual([x["asset"]["name"] for x in r["rows"]], ["Chair"])
        self.assertEqual(self.fin.get(f"{A}/reports/asset-disposals/", {"from": "2026-12-31", "to": "2026-01-01"}).status_code, 400)

    def test_dashboard(self):
        r = self.fin.get(f"{A}/assets/dashboard/").json()
        self.assertEqual(r["totals"]["count"], 3)
        self.assertEqual(r["not_in_books"], 1)
        self.assertEqual(r["counts"]["in_use"], 3)
        self.assertEqual(len(r["monthly"]), 12)
        self.assertEqual(r["by_category"][0]["name"], "Computers")
        for key in ("pending", "depreciation_this_year", "disposals_this_year", "ledger_check"):
            self.assertIn(key, r)

    def test_months_waiting_to_be_charged(self):
        # Laptop and chair are charged to September. As on 15 Dec: October, November and December are due.
        p = asset_reports.pending_depreciation(self.company, d(2026, 12, 15))
        self.assertEqual((p["months"], p["first"], p["last"]), (3, d(2026, 10, 1), d(2026, 12, 1)))
        # laptop 3,166.67 + 3,166.66... Oct 3,166.67, Nov 3,166.67, Dec 3,166.66 = 9,500.00; chair 250 x 3 = 750.00
        self.assertEqual(p["amount"], D("10250.00"))


class AccessAndAuditTests(AssetBase):
    ENDPOINTS = ["assets/", "assets/options/", "assets/dashboard/", "asset-categories/", "asset-disposals/", "depreciation/runs/",
                 "depreciation/preview/?period=2026-04", "reports/asset-register/", "reports/depreciation-by-year/",
                 "reports/asset-disposals/"]

    def test_every_role_sees_or_is_refused_the_pages(self):
        for role in ALL_ROLES:
            api = client_for(make_user(f"u_{role}", role))
            for url in self.ENDPOINTS:
                r = api.get(f"{A}/{url}")
                self.assertEqual(r.status_code, 200 if role in CAN_VIEW else 403, (role, url, r.content))
            if role in CAN_VIEW:
                a = self.asset(name=f"Asset {role}", acquisition="later")
                for path in (f"assets/{a['id']}/", f"assets/{a['id']}/schedule/"):
                    self.assertEqual(api.get(f"{A}/{path}").status_code, 200, (role, path))
            self.assertEqual(api.get(f"{A}/assets/{GHOST}/").status_code, 403 if role not in CAN_VIEW else 404, role)

    def test_only_managers_can_change_anything(self):
        a = self.asset()
        for role in ALL_ROLES:
            api = client_for(make_user(f"w_{role}", role))
            ok = role in CAN_MANAGE
            checks = [
                ("post", f"{A}/asset-categories/", {"name": f"C {role}", "method": "slm", "useful_life_years": "5"}, 201),
                ("post", f"{A}/asset-categories/setup/", {}, 200),
                ("post", f"{A}/assets/", {"name": "x", "category": self.cat["id"], "cost": "100", "purchase_date": "2026-05-01"}, 201),
                ("patch", f"{A}/assets/{a['id']}/", {"location": "somewhere"}, 200),
                ("post", f"{A}/depreciation/runs/", {"period": "2026-04"}, 200),
                ("post", f"{A}/assets/{a['id']}/dispose/", {"kind": "written_off", "date": "2026-05-31", "proceeds": "0"}, 201),
            ]
            for method, url, body, success in checks:
                r = getattr(api, method)(url, body, format="json")
                if ok:
                    self.assertEqual(r.status_code, success, (role, url, r.content))
                    if url.endswith("/dispose/"):
                        self.fin.post(f"{A}/asset-disposals/{r.json()['id']}/reverse/", {"change_reason": "test"})
                else:
                    self.assertEqual(r.status_code, 403, (role, url))

    def test_unknown_ids_are_404_for_a_manager(self):
        for method, url in [("get", f"assets/{GHOST}/"), ("patch", f"assets/{GHOST}/"), ("delete", f"assets/{GHOST}/"),
                            ("get", f"assets/{GHOST}/schedule/"), ("post", f"assets/{GHOST}/acquisition/"),
                            ("post", f"assets/{GHOST}/acquisition/undo/"), ("post", f"assets/{GHOST}/dispose/"),
                            ("get", f"assets/{GHOST}/disposal-preview/?date=2026-05-01"),
                            ("get", f"asset-categories/{GHOST}/"), ("patch", f"asset-categories/{GHOST}/"),
                            ("delete", f"asset-categories/{GHOST}/"), ("get", f"depreciation/runs/{GHOST}/"),
                            ("post", f"depreciation/runs/{GHOST}/reverse/"), ("post", f"asset-disposals/{GHOST}/reverse/")]:
            r = getattr(self.fin, method)(f"{A}/{url}", {}, format="json") if method != "get" and method != "delete" else getattr(self.fin, method)(f"{A}/{url}")
            self.assertEqual(r.status_code, 404, (method, url, r.content))

    def test_attachments_are_registered_for_assets(self):
        t = registry.target("fixed_asset")
        self.assertEqual((t["view"], t["manage"]), ("accounting.view", "accounting.manage"))

    def test_every_action_is_audited(self):
        a = self.asset()
        run = self.do_run("2026-04")["runs"][0]
        self.fin.post(f"{A}/depreciation/runs/{run['id']}/reverse/", {"change_reason": "redo"})
        self.do_run("2026-04")
        disposal = self.dispose(a, date="2026-04-30", proceeds="100000")
        self.fin.post(f"{A}/asset-disposals/{disposal['id']}/reverse/", {"change_reason": "cancelled"})
        self.fin.patch(f"{A}/assets/{a['id']}/", {"location": "Pune"})
        for entity, action, minimum in [("asset_category", "created", 1), ("fixed_asset", "created", 1), ("fixed_asset", "updated", 3),
                                        ("depreciation_run", "created", 2), ("depreciation_run", "deactivated", 1),
                                        ("asset_disposal", "created", 1), ("asset_disposal", "deactivated", 1)]:
            self.assertGreaterEqual(self.audit(entity, action).count(), minimum, (entity, action))
        ev = self.audit("asset_disposal", "deactivated").get()
        self.assertEqual((ev.reason, ev.actor_label != ""), ("cancelled", True))
        self.assertEqual(self.audit("fixed_asset", "created").get().new_values["code"], a["code"])
        self.assertTrue(self.audit("fixed_asset", "updated").filter(new_values__location="Pune").exists())
