import datetime
import re

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.accounting.models import JournalEntry
from apps.company.models import CompanyProfileAuditEvent
from apps.tax.models import TaxFiling
from apps.tax.services import periods, taxcalendar
from .base import D, D0, T, TaxBase

d = datetime.date


def money(v):
    return D(str(v))


class DueDateRuleTests(SimpleTestCase):
    def test_gst_returns_fall_on_the_11th_and_20th_of_the_next_month(self):
        self.assertEqual(periods.due_date("gstr1", "2026-27", d(2026, 8, 1)), d(2026, 9, 11))
        self.assertEqual(periods.due_date("gstr3b", "2026-27", d(2026, 8, 1)), d(2026, 9, 20))
        self.assertEqual(periods.due_date("gstr1", "2026-27", d(2026, 12, 1)), d(2027, 1, 11))  # across the year end
        self.assertEqual(periods.due_date("gstr3b", "2026-27", d(2027, 3, 1)), d(2027, 4, 20))

    def test_tds_returns_follow_the_quarter_dates(self):
        got = [periods.due_date("tds_26q", "2026-27", s) for _q, s, _e, _l in periods.fy_quarters("2026-27")]
        self.assertEqual(got, [d(2026, 7, 31), d(2026, 10, 31), d(2027, 1, 31), d(2027, 5, 31)])
        self.assertEqual(periods.due_date("tds_24q", "2026-27", d(2026, 7, 1)), d(2026, 10, 31))

    def test_itr_audit_and_annual_return(self):
        self.assertEqual(periods.due_date("itr5", "2026-27", d(2026, 4, 1)), d(2027, 7, 31))
        self.assertEqual(periods.due_date("itr5", "2026-27", d(2026, 4, 1), audit_applicable=True), d(2027, 10, 31))
        self.assertEqual(periods.due_date("tax_audit", "2026-27", d(2026, 4, 1)), d(2027, 9, 30))
        self.assertEqual(periods.due_date("gstr9", "2026-27", d(2026, 4, 1)), d(2027, 12, 31))

    def test_financial_year_helpers_and_validation(self):
        self.assertEqual((periods.fy_label_for(d(2027, 3, 31)), periods.fy_label_for(d(2027, 4, 1))), ("2026-27", "2027-28"))
        self.assertEqual(periods.parse_fy("2026-27")[1:], (d(2026, 4, 1), d(2027, 3, 31)))
        for bad in ("2026", "2026-28", "26-27", "", None, "abcd-ef"):
            with self.assertRaises(ValidationError):
                periods.parse_fy(bad)
        self.assertEqual([m[2] for m in periods.fy_months("2026-27")][:2], ["Apr 2026", "May 2026"])
        self.assertEqual(periods.parse_month("2028-02"), (d(2028, 2, 1), d(2028, 2, 29)))
        for bad in ("2026-13", "2026-8", "x"):
            with self.assertRaises(ValidationError):
                periods.parse_month(bad)

    def test_date_ranges_are_checked(self):
        self.assertEqual(periods.date_range("2026-08-01", "2026-08-31"), (d(2026, 8, 1), d(2026, 8, 31)))
        for args in (("2026-09-01", "2026-08-01"), ("nope", "2026-08-01"), ("2020-01-01", "2026-08-01")):
            with self.assertRaises(ValidationError):
                periods.date_range(*args)


class FilingTests(TaxBase):
    def test_generate_creates_every_return_of_the_year_once(self):
        self.enable_gst()
        r = self.fin.post(f"{T}/filings/generate/", {"financial_year": "2026-27"})
        self.assertEqual(r.status_code, 201, r.content)
        # 12 GSTR-1, 12 GSTR-3B, GSTR-9, 4 + 4 TDS returns, ITR-5, tax audit
        self.assertEqual(r.json()["created"], 35)
        again = self.fin.post(f"{T}/filings/generate/", {"financial_year": "2026-27"})
        self.assertEqual((again.status_code, again.json()["created"], again.json()["existing"]), (200, 0, 35))
        rows = self.fin.get(f"{T}/filings/", {"fy": "2026-27"}).json()
        self.assertEqual(rows["count"], 35)
        aug = next(x for x in rows["results"] if x["return_type"] == "gstr3b" and x["period_label"] == "Aug 2026")
        self.assertEqual(aug["due_date"], "2026-09-20")
        self.assertRegex(aug["reference"], r"^T2N/TF/\d{2}/\d{4}/\d{5}$")
        self.assertEqual(len({x["reference"] for x in rows["results"]}), 35)

    def test_generate_skips_gst_returns_when_not_registered_and_can_pick_types(self):
        r = self.fin.post(f"{T}/filings/generate/", {"financial_year": "2026-27"})
        self.assertEqual(r.json()["created"], 10)  # TDS 4 + 4, ITR-5, tax audit, no GST
        r = self.fin.post(f"{T}/filings/generate/", {"financial_year": "2027-28", "types": ["itr5"], "audit_applicable": True})
        self.assertEqual(r.json()["created"], 1)
        self.assertEqual(r.json()["results"][0]["due_date"], "2028-10-31")
        self.assertEqual(self.fin.post(f"{T}/filings/generate/", {"financial_year": "2026-27", "types": ["nope"]}).status_code, 400)
        self.assertEqual(self.fin.post(f"{T}/filings/generate/", {"financial_year": "bad"}).status_code, 400)
        self.assertEqual(self.fin.post(f"{T}/filings/generate/", {"financial_year": "2026-27", "types": "gstr1"}).status_code, 400)

    def test_create_one_computes_the_due_date_and_refuses_duplicates_and_bad_periods(self):
        f = self.filing("gstr1", "2026-08")
        self.assertEqual((f["due_date"], f["status"], f["period_label"], f["financial_year"]), ("2026-09-11", "late", "Aug 2026", "2026-27"))
        self.assertEqual(self.fin.post(f"{T}/filings/", {"return_type": "gstr1", "period": "2026-08"}).status_code, 400)
        self.filing("gstr1", "2026-09", expect=201, due_date="2026-10-15")
        self.assertEqual(self.fin.post(f"{T}/filings/", {"return_type": "gstr3b"}).status_code, 400)
        self.assertEqual(self.fin.post(f"{T}/filings/", {"return_type": "gstr3b", "period": "2026-13"}).status_code, 400)
        self.assertEqual(self.fin.post(f"{T}/filings/", {"return_type": "gstr3b", "period": "2026-08", "financial_year": "2025-26"}).status_code, 400)
        self.assertEqual(self.fin.post(f"{T}/filings/", {"return_type": "bogus", "period": "2026-08"}).status_code, 400)
        self.assertEqual(self.fin.post(f"{T}/filings/", {"return_type": "tds_26q", "financial_year": "2026-27", "quarter": "9"}).status_code, 400)
        q = self.fin.post(f"{T}/filings/", {"return_type": "tds_26q", "financial_year": "2026-27", "quarter": "3"}).json()
        self.assertEqual((q["period_label"], q["due_date"]), ("Q3 2026-27", "2027-01-31"))
        itr = self.fin.post(f"{T}/filings/", {"return_type": "itr5", "financial_year": "2026-27", "audit_applicable": True}).json()
        self.assertEqual(itr["due_date"], "2027-10-31")
        self.assertEqual(self.fin.post(f"{T}/filings/", {"return_type": "gstr1", "period": "2026-08", "due_date": "31/12/2026"}).status_code, 400)

    def test_prepare_gstr3b_takes_outward_supplies_itc_and_net_liability_from_the_registers(self):
        self.sales_fixture()
        self.purchases_fixture()
        f = self.filing("gstr3b", "2026-08")
        r = self.fin.post(f"{T}/filings/{f['id']}/prepare/")
        self.assertEqual(r.status_code, 200, r.content)
        fig, out = r.json()["figures"], r.json()
        self.assertEqual(out["status"], "late")  # past its due date and not filed
        self.assertEqual(out["stored_status"], "prepared")
        want = {"outward_taxable": "326000.00", "igst": "55900.00", "cgst": "1260.00", "sgst": "1260.00", "cess": "0.00",
                "itc_igst": "1440.00", "itc_cgst": "1800.00", "itc_sgst": "1800.00", "itc_reversed": "0.00",
                "net_igst": "54460.00", "net_cgst": "-540.00", "net_sgst": "-540.00",
                "net_payable": "53380.00", "cash_payable": "53380.00"}  # 58,420 output - 5,040 credit
        for k, v in want.items():
            self.assertEqual(fig[k], v, k)

    def test_ineligible_credit_is_reversed_in_the_prepared_return(self):
        self.sales_fixture()
        self.purchases_fixture()
        reg = self.fin.get(f"{T}/gst/purchase-register/", {"from": "2026-08-01", "to": "2026-08-31"}).json()
        self.fin.post(f"{T}/gst/purchase-register/flag/", {"entry_id": reg["rows"][1]["entry_id"], "eligibility": "ineligible", "reason": "Blocked"})
        f = self.filing("gstr3b", "2026-08")
        fig = self.fin.post(f"{T}/filings/{f['id']}/prepare/").json()["figures"]
        self.assertEqual((fig["itc_igst"], fig["itc_reversed"]), ("0.00", "1440.00"))
        # IGST credit 0; CGST 1,800 -> 1,260 CGST + 540 IGST; SGST the same: cash = 55,900 - 1,080
        self.assertEqual(fig["cash_payable"], "54820.00")

    def test_prepare_gstr1_snapshot(self):
        self.sales_fixture()
        f = self.filing("gstr1", "2026-08")
        fig = self.fin.post(f"{T}/filings/{f['id']}/prepare/").json()["figures"]
        self.assertEqual((fig["outward_taxable"], fig["b2b_taxable"], fig["b2c_taxable"], fig["credit_debit_notes_taxable"]),
                         ("326000.00", "22000.00", "305000.00", "-1000.00"))
        self.assertEqual((fig["igst"], fig["cgst"], fig["sgst"], fig["invoice_value"]), ("55900.00", "1260.00", "1260.00", "384420.00"))

    def test_prepare_gstr9_adds_the_year(self):
        self.sales_fixture()
        self.purchases_fixture()
        self.pay(heads={"igst": "1000.00", "cgst": "200.00"})
        f = self.filing("gstr9", financial_year="2026-27")
        fig = self.fin.post(f"{T}/filings/{f['id']}/prepare/").json()["figures"]
        self.assertEqual((fig["outward_taxable"], fig["cgst"], fig["itc_cgst"], fig["tax_paid"]), ("328000.00", "1440.00", "1800.00", "1200.00"))

    def test_figures_can_be_edited_with_validation_and_the_status_follows(self):
        f = self.filing("gstr1", "2026-08")
        url = f"{T}/filings/{f['id']}/"
        r = self.fin.patch(url, {"figures": {"igst": "500.50"}, "late_fee": "50", "interest": "12.25", "arn": "AA2909260001", "notes": "Filed via portal"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["figures"]["igst"], r.json()["stored_status"], r.json()["late_fee"], r.json()["interest"]), ("500.50", "prepared", "50.00", "12.25"))
        for bad in ({"figures": {"nope": "1"}}, {"figures": {"igst": "abc"}}, {"figures": {"igst": "1.234"}}, {"figures": [1]},
                    {"late_fee": "-5"}, {"late_fee": "1.234"}, {"due_date": "tomorrow"}, {"arn": "x" * 61}, {"notes": "n" * 501}):
            self.assertEqual(self.fin.patch(url, bad).status_code, 400, bad)

    def test_mark_filed_records_arn_and_date_and_locks_the_figures(self):
        f = self.filing("gstr1", "2026-08")
        base = f"{T}/filings/{f['id']}/"
        self.assertEqual(self.fin.post(base + "file/", {}).status_code, 400)  # a date is needed
        self.assertEqual(self.fin.post(base + "file/", {"filed_date": "2999-01-01"}).status_code, 400)
        self.assertEqual(self.fin.post(base + "file/", {"filed_date": "2026-07-31"}).status_code, 400)  # before the period began
        r = self.fin.post(base + "file/", {"filed_date": "2026-09-10", "arn": "AA290926123456X"})
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual((j["status"], j["arn"], j["filed_date"], j["filed_late"], j["overdue"]), ("filed", "AA290926123456X", "2026-09-10", False, False))
        self.assertEqual(self.fin.post(base + "file/", {"filed_date": "2026-09-10"}).status_code, 400)
        self.assertEqual(self.fin.patch(base, {"figures": {"igst": "1"}}).status_code, 400)
        self.assertEqual(self.fin.patch(base, {"arn": "AA290926999999Z"}).status_code, 200)
        self.assertEqual(self.fin.delete(base).status_code, 400)
        self.assertEqual(self.fin.post(base + "prepare/").status_code, 400)

    def test_late_filing_is_flagged_and_reopening_needs_a_reason(self):
        f = self.filing("gstr3b", "2026-08")
        base = f"{T}/filings/{f['id']}/"
        j = self.fin.post(base + "file/", {"filed_date": "2026-09-25", "late_fee": "250"}).json()
        self.assertEqual((j["filed_late"], j["late_fee"]), (True, "250.00"))  # due 20 Sep
        self.assertEqual(self.fin.post(base + "reopen/", {}).status_code, 400)
        r = self.fin.post(base + "reopen/", {"reason": "Figures were wrong"})
        self.assertEqual((r.status_code, r.json()["stored_status"], r.json()["filed_date"]), (200, "prepared", None))
        self.assertEqual(self.fin.post(base + "reopen/", {"reason": "again"}).status_code, 400)  # not filed any more

    def test_status_chips_and_counts(self):
        self.filing("gstr1", "2026-08")
        b = self.filing("gstr3b", "2026-08")
        self.fin.post(f"{T}/filings/{b['id']}/file/", {"filed_date": "2026-09-15"})
        far = self.filing("itr5", financial_year="2030-31")  # due in 2031: pending, not late
        r = self.fin.get(f"{T}/filings/").json()
        self.assertEqual(r["counts"], {"all": 3, "pending": 1, "prepared": 0, "filed": 1, "late": 1})
        self.assertEqual(self.fin.get(f"{T}/filings/", {"status": "late"}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{T}/filings/", {"status": "filed"}).json()["results"][0]["reference"], b["reference"])
        self.assertEqual(self.fin.get(f"{T}/filings/", {"type": "itr5"}).json()["results"][0]["id"], far["id"])
        self.assertEqual(self.fin.get(f"{T}/filings/", {"group": "gst"}).json()["count"], 2)
        self.assertEqual(self.fin.get(f"{T}/filings/", {"q": b["reference"]}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{T}/filings/", {"fy": "bad"}).status_code, 400)

    def test_delete_a_pending_filing_and_unknown_ids_are_404(self):
        f = self.filing("gstr1", "2026-08")
        self.assertEqual(self.fin.delete(f"{T}/filings/{f['id']}/", {"reason": "Created by mistake"}).status_code, 204)
        self.assertEqual(self.fin.get(f"{T}/filings/{f['id']}/").status_code, 404)
        unknown = "00000000-0000-0000-0000-000000000000"
        for method, tail in (("get", ""), ("patch", ""), ("delete", ""), ("post", "prepare/"), ("post", "file/"), ("post", "reopen/"),
                             ("get", "setoff/"), ("post", "setoff/"), ("post", "setoff/undo/")):
            self.assertEqual(getattr(self.fin, method)(f"{T}/filings/{unknown}/{tail}").status_code, 404, (method, tail))

    def test_changes_are_audited(self):
        f = self.filing("gstr1", "2026-08")
        self.fin.post(f"{T}/filings/{f['id']}/prepare/")
        self.fin.post(f"{T}/filings/{f['id']}/file/", {"filed_date": "2026-09-10", "arn": "AA1"})
        events = CompanyProfileAuditEvent.objects.filter(entity_type="tax_filing", entity_id=f["id"]).order_by("occurred_at")
        self.assertEqual([e.action for e in events], ["created", "updated", "updated"])
        self.assertEqual(events[2].reason, "Marked as filed")
        self.assertIn("arn", events[2].changed_fields)


class SetoffTests(TaxBase):
    """Aug 2026 balances: output CGST 1,260  SGST 1,260  IGST 55,900; credit CGST 1,800  SGST 1,800  IGST 1,440.

    IGST credit 1,440 -> IGST. CGST credit: 1,260 -> CGST, the other 540 -> IGST. SGST the same. Cash left: IGST 55,900 - 1,440 - 540 - 540 = 53,380."""

    def setUp(self):
        super().setUp()
        self.sales_fixture()
        self.purchases_fixture()
        self.f = self.filing("gstr3b", "2026-08")
        self.url = f"{T}/filings/{self.f['id']}/setoff/"

    def test_preview_applies_the_statutory_order(self):
        p = self.fin.get(self.url).json()
        self.assertFalse(p["posted"])
        self.assertEqual(p["date"], "2026-08-31")
        self.assertEqual({k: money(v) for k, v in p["output"].items()}, {"cgst": D("1260"), "sgst": D("1260"), "igst": D("55900")})
        self.assertEqual({k: money(v) for k, v in p["credit"].items()}, {"cgst": D("1800"), "sgst": D("1800"), "igst": D("1440")})
        m = sorted((x["credit"], x["against"], money(x["amount"])) for x in p["matrix"])
        self.assertEqual(m, [("cgst", "cgst", D("1260")), ("cgst", "igst", D("540")), ("igst", "igst", D("1440")), ("sgst", "igst", D("540")),
                             ("sgst", "sgst", D("1260"))])
        self.assertEqual({k: money(v) for k, v in p["cash_payable"].items()}, {"cgst": D0, "sgst": D0, "igst": D("53380")})
        self.assertEqual(money(p["cash_total"]), D("53380"))

    def test_posting_debits_the_liability_and_credits_the_input_credit(self):
        r = self.fin.post(self.url)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()["has_setoff"])
        entry = JournalEntry.objects.get(source="tax_setoff", source_id=self.f["id"])
        self.assertEqual(entry.date, d(2026, 8, 31))
        lines = {(l.account.system_key, l.debit, l.credit) for l in entry.lines.select_related("account")}
        self.assertEqual(lines, {("gst_cgst_out", D("1260"), D0), ("gst_sgst_out", D("1260"), D0), ("gst_igst_out", D("2520"), D0),
                                 ("gst_cgst_in", D0, D("1800")), ("gst_sgst_in", D0, D("1800")), ("gst_igst_in", D0, D("1440"))})
        self.assertEqual((self.balance("gst_cgst_out", d(2026, 8, 31)), self.balance("gst_sgst_out", d(2026, 8, 31)),
                          self.balance("gst_igst_out", d(2026, 8, 31))), (D0, D0, D("53380")))
        self.assertEqual((self.balance("gst_cgst_in"), self.balance("gst_igst_in")), (D0, D0))
        self.trial_balanced()
        self.assertEqual(self.fin.post(self.url).status_code, 400)  # only once
        again = self.fin.get(self.url).json()
        self.assertTrue(again["posted"])
        self.assertEqual((money(again["cash_total"]), money(again["paid_so_far"]), money(again["still_to_pay"])), (D("53380"), D0, D("53380")))
        self.pay(heads={"igst": "20000"}, period="2026-08", date="2026-09-18")
        part = self.fin.get(self.url).json()
        self.assertEqual((money(part["cash_total"]), money(part["paid_so_far"]), money(part["still_to_pay"])), (D("53380"), D("20000"), D("33380")))

    def test_undo_reverses_the_entry_and_needs_a_reason(self):
        self.fin.post(self.url)
        undo = self.url + "undo/"
        self.assertEqual(self.fin.post(undo, {}).status_code, 400)
        self.assertEqual(self.fin.delete(f"{T}/filings/{self.f['id']}/").status_code, 400)  # set-off still stands
        r = self.fin.post(undo, {"reason": "Wrong period"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()["has_setoff"])
        self.assertEqual(self.balance("gst_igst_in"), D("1440"))
        self.assertEqual(self.balance("gst_igst_out", d(2026, 8, 31)), D("55900"))  # the reversal is dated like the set-off
        self.trial_balanced()
        self.assertEqual(self.fin.post(undo, {"reason": "again"}).status_code, 400)
        redo = self.fin.post(self.url)
        self.assertEqual(redo.status_code, 201, redo.content)  # can be redone

    def test_ineligible_credit_is_left_out_of_the_set_off(self):
        reg = self.fin.get(f"{T}/gst/purchase-register/", {"from": "2026-08-01", "to": "2026-08-31"}).json()
        self.fin.post(f"{T}/gst/purchase-register/flag/", {"entry_id": reg["rows"][1]["entry_id"], "eligibility": "ineligible", "reason": "Blocked"})
        p = self.fin.get(self.url).json()
        self.assertEqual(money(p["credit"]["igst"]), D0)
        self.assertEqual(money(p["cash_total"]), D("54820"))

    def test_only_a_gstr3b_has_a_set_off_and_nothing_to_set_off_is_refused(self):
        g1 = self.filing("gstr1", "2026-08")
        self.assertEqual(self.fin.get(f"{T}/filings/{g1['id']}/setoff/").status_code, 400)
        self.assertEqual(self.fin.post(f"{T}/filings/{g1['id']}/setoff/").status_code, 400)
        empty = self.filing("gstr3b", "2026-06")
        self.assertEqual(self.fin.post(f"{T}/filings/{empty['id']}/setoff/").status_code, 400)

    def test_setoff_algorithm_edge_cases(self):
        from apps.tax.services.gst import compute_setoff
        # IGST credit larger than IGST output flows to CGST then SGST
        r = compute_setoff({"igst": D("100"), "cgst": D("50"), "sgst": D("30")}, {"igst": D("150"), "cgst": D("0"), "sgst": D("0")})
        self.assertEqual(r["cash"], {"igst": D0, "cgst": D0, "sgst": D("30.00")})
        self.assertEqual(r["credit_left"], {"igst": D0, "cgst": D0, "sgst": D0})
        self.assertEqual([(m["credit"], m["against"], m["amount"]) for m in r["matrix"]], [("igst", "igst", D("100.00")), ("igst", "cgst", D("50.00"))])
        # CGST credit cannot pay SGST liability
        r = compute_setoff({"igst": D0, "cgst": D0, "sgst": D("80")}, {"igst": D0, "cgst": D("500"), "sgst": D0})
        self.assertEqual((r["cash"]["sgst"], r["credit_left"]["cgst"], r["matrix"]), (D("80.00"), D("500.00"), []))
        # credit above liability is carried forward
        r = compute_setoff({"igst": D("10"), "cgst": D("10"), "sgst": D("10")}, {"igst": D("100"), "cgst": D("0"), "sgst": D("0")})
        self.assertEqual((r["cash"], r["credit_left"]["igst"]), ({"igst": D0, "cgst": D0, "sgst": D0}, D("70.00")))


class ReconciliationAndLedgerTests(TaxBase):
    def setUp(self):
        super().setUp()
        self.sales_fixture()
        self.purchases_fixture()
        self.url = f"{T}/gst/reconciliation/"
        self.q = {"from": "2026-08-01", "to": "2026-08-31"}

    def rows(self):
        return {r["key"]: r for r in self.fin.get(self.url, self.q).json()["rows"]}

    def test_before_filing_everything_agrees_internally_and_is_marked_not_filed(self):
        rows = self.rows()
        self.assertEqual({k: r["status"] for k, r in rows.items()}, {k: "not_filed" for k in rows})
        self.assertEqual((money(rows["taxable"]["books"]), money(rows["taxable"]["register"])), (D("326000"), D("326000")))
        self.assertEqual((money(rows["out_igst"]["books"]), money(rows["itc_igst"]["books"]), money(rows["itc_cgst"]["register"])), (D("55900"), D("1440"), D("1800")))
        self.assertTrue(all(money(r["register_difference"]) == D0 for r in rows.values()))

    def test_filed_figures_that_match_the_books_show_no_difference(self):
        for rt in ("gstr1", "gstr3b"):
            f = self.filing(rt, "2026-08")
            self.fin.post(f"{T}/filings/{f['id']}/prepare/")
            self.fin.post(f"{T}/filings/{f['id']}/file/", {"filed_date": "2026-09-10"})
        rows = self.rows()
        self.assertEqual({r["status"] for r in rows.values()}, {"match"})
        self.assertEqual(self.fin.get(self.url, self.q).json()["mismatches"], 0)

    def test_a_difference_between_the_return_and_the_books_is_flagged(self):
        f = self.filing("gstr3b", "2026-08")
        self.fin.post(f"{T}/filings/{f['id']}/prepare/")
        self.fin.patch(f"{T}/filings/{f['id']}/", {"figures": {"igst": "55000.00", "itc_cgst": "1900.00"}})
        self.fin.post(f"{T}/filings/{f['id']}/file/", {"filed_date": "2026-09-19"})
        data = self.fin.get(self.url, self.q).json()
        rows = {r["key"]: r for r in data["rows"]}
        self.assertEqual((rows["out_igst"]["status"], money(rows["out_igst"]["difference"])), ("mismatch", D("-900")))
        self.assertEqual((rows["itc_cgst"]["status"], money(rows["itc_cgst"]["difference"])), ("mismatch", D("100")))
        self.assertEqual(rows["out_cgst"]["status"], "match")
        self.assertEqual(data["mismatches"], 2)
        self.assertEqual(data["filed"], {"gstr1": 0, "gstr3b": 1})

    def test_period_inputs_are_validated(self):
        for q in ({"from": "2026-09-01", "to": "2026-08-01"}, {"from": "x"}, {"to": "31-08-2026"}, {"from": "2010-01-01", "to": "2026-01-01"}):
            for path in ("gst/reconciliation/", "gst/liability-ledger/", "gst/sales-register/", "gst/purchase-register/", "tds/ledger/"):
                self.assertEqual(self.fin.get(f"{T}/{path}", q).status_code, 400, (path, q))
        for path in ("gst/summary/", "income-tax/"):
            self.assertEqual(self.fin.get(f"{T}/{path}", {"fy": "2026"}).status_code, 400, path)

    def test_liability_ledger_opening_liability_itc_cash_and_closing(self):
        f = self.filing("gstr3b", "2026-08")
        self.fin.post(f"{T}/filings/{f['id']}/setoff/")
        self.pay(heads={"igst": "53380.00", "interest": "100.00"}, date="2026-09-18")
        led = self.fin.get(f"{T}/gst/liability-ledger/", {"from": "2026-08-01", "to": "2026-09-30"}).json()
        heads = {h["head"]: h for h in led["heads"]}
        got = {k: tuple(money(h[f]) for f in ("opening", "liability", "itc_utilised", "cash_paid", "closing")) for k, h in heads.items()}
        self.assertEqual(got, {"cgst": (D0, D("1440"), D("1260"), D0, D("180")),   # 1,260 + 180 raised, 1,260 set off
                               "sgst": (D0, D("1440"), D("1260"), D0, D("180")),
                               "igst": (D0, D("55900"), D("2520"), D("53380"), D0)})
        itc = {k: tuple(money(h[f]) for f in ("itc_opening", "itc_accrued", "itc_used", "itc_closing")) for k, h in heads.items()}
        self.assertEqual(itc, {"cgst": (D0, D("1800"), D("1800"), D0), "sgst": (D0, D("1800"), D("1800"), D0), "igst": (D0, D("1440"), D("1440"), D0)})
        self.assertEqual(money(led["total"]["closing"]), D("360"))
        months = {m["month"]: m for m in led["months"]}
        self.assertEqual((money(months["2026-08"]["closing"]), money(months["2026-09"]["closing"])), (D("53380"), D("360")))
        self.assertEqual(money(months["2026-09"]["cash_paid"]), D("53380"))
        # the next period opens with September's closing
        nxt = self.fin.get(f"{T}/gst/liability-ledger/", {"from": "2026-10-01", "to": "2026-10-31"}).json()
        self.assertEqual({h["head"]: money(h["opening"]) for h in nxt["heads"]}, {"cgst": D("180"), "sgst": D("180"), "igst": D0})

    def test_summary_by_month_and_kpis(self):
        s = self.fin.get(f"{T}/gst/summary/", {"fy": "2026-27"}).json()
        months = {m["month"]: m for m in s["months"]}
        self.assertEqual(len(s["months"]), 12)
        aug = months["2026-08"]
        self.assertEqual((money(aug["output_total"]), money(aug["itc_total"]), money(aug["net"]), money(aug["taxable"])),
                         (D("58420"), D("5040"), D("53380"), D("326000")))
        self.assertEqual({h: money(v) for h, v in aug["output"].items()}, {"cgst": D("1260"), "sgst": D("1260"), "igst": D("55900")})
        sep = months["2026-09"]
        self.assertEqual((money(sep["output_total"]), money(sep["itc_total"]), money(sep["net"])), (D("360"), D0, D("360")))
        self.assertEqual(money(s["totals"]["output"]), D("58780"))
        self.assertEqual(money(s["kpis"]["itc_available"]), D("5040"))
        self.assertEqual(money(s["kpis"]["liability_outstanding"]), D("58780"))
        self.assertEqual({money(r["rate"]) for r in s["by_rate"]}, {D("5"), D("18")})
        self.assertEqual(s["kpis"]["returns"], {"filed": 0, "prepared": 0, "pending": 0, "late": 0, "total": 0})

    def test_summary_counts_returns_filed_pending_and_late(self):
        self.fin.post(f"{T}/filings/generate/", {"financial_year": "2026-27", "types": ["gstr1", "gstr3b"]})
        rows = self.fin.get(f"{T}/filings/", {"type": "gstr1", "fy": "2026-27"}).json()["results"]
        aug = next(r for r in rows if r["period_label"] == "Aug 2026")
        self.fin.post(f"{T}/filings/{aug['id']}/file/", {"filed_date": "2026-09-10"})
        ret = self.fin.get(f"{T}/gst/summary/", {"fy": "2026-27"}).json()["kpis"]["returns"]
        self.assertEqual(ret["total"], 24)
        self.assertEqual(ret["filed"], 1)
        self.assertEqual(ret["filed"] + ret["prepared"] + ret["pending"] + ret["late"], 24)


class CalendarTests(TaxBase):
    TODAY = d(2026, 11, 12)

    def test_events_in_the_next_90_days_across_gst_tds_and_advance_tax(self):
        self.enable_gst()
        cal = taxcalendar.calendar(self.company, today=self.TODAY, days=90)
        by = {(e["type"], e["period"]): e for e in cal["events"]}
        self.assertEqual(cal["counts"], {"overdue": 4, "next_7": 0, "next_30": 2, "total": 13})
        self.assertEqual(by[("gstr3b", "Oct 2026")]["due_date"], d(2026, 11, 20))
        self.assertEqual(by[("gstr3b", "Oct 2026")]["days_left"], 8)
        self.assertEqual(by[("gstr1", "Oct 2026")]["due_date"], d(2026, 11, 11))
        self.assertTrue(by[("gstr1", "Oct 2026")]["overdue"])
        self.assertEqual(by[("gstr1", "Oct 2026")]["status"], "late")
        self.assertEqual(by[("tds_26q", "Q3 2026-27")]["due_date"], d(2027, 1, 31))
        self.assertEqual(by[("tds_24q", "Q2 2026-27")]["due_date"], d(2026, 10, 31))
        self.assertEqual(by[("gstr9", "FY 2025-26")]["due_date"], d(2026, 12, 31))
        adv = by[("advance_tax", "FY 2026-27 instalment 3")]
        self.assertEqual((adv["due_date"], adv["kind"]), (d(2026, 12, 15), "advance_tax"))
        self.assertEqual([e["due_date"] for e in cal["events"]], sorted(e["due_date"] for e in cal["events"]))
        self.assertEqual({e["kind"] for e in cal["events"]}, {"gst", "tds", "advance_tax"})
        for key in ("id", "kind", "kind_label", "type", "title", "period", "due_date", "days_left", "overdue", "status", "filing_id", "amount", "link"):
            self.assertIn(key, cal["events"][0])

    def test_gst_is_left_out_when_the_company_is_not_registered(self):
        cal = taxcalendar.calendar(self.company, today=self.TODAY, days=90)
        self.assertEqual({e["kind"] for e in cal["events"]}, {"tds", "advance_tax"})

    def test_filed_returns_drop_off_and_a_record_can_move_the_date(self):
        self.enable_gst()
        TaxFiling.objects.create(company=self.company, return_type="gstr3b", financial_year="2026-27", period_label="Oct 2026",
                                 period_start=d(2026, 10, 1), period_end=d(2026, 10, 31), due_date=d(2026, 11, 20), status="filed", filed_date=d(2026, 11, 15))
        TaxFiling.objects.create(company=self.company, return_type="gstr1", financial_year="2026-27", period_label="Oct 2026",
                                 period_start=d(2026, 10, 1), period_end=d(2026, 10, 31), due_date=d(2026, 11, 30))
        cal = taxcalendar.calendar(self.company, today=self.TODAY, days=90)
        by = {(e["type"], e["period"]): e for e in cal["events"]}
        self.assertNotIn(("gstr3b", "Oct 2026"), by)
        moved = by[("gstr1", "Oct 2026")]
        self.assertEqual((moved["due_date"], moved["overdue"], moved["filing_id"] is not None), (d(2026, 11, 30), False, True))
        self.assertEqual(cal["counts"]["total"], 12)

    def test_endpoint_and_day_validation(self):
        r = self.fin.get(f"{T}/calendar/", {"days": 90})
        self.assertEqual(r.status_code, 200)
        self.assertIn("events", r.json())
        for bad in ("0", "abc", "9999"):
            self.assertEqual(self.fin.get(f"{T}/calendar/", {"days": bad}).status_code, 400, bad)
