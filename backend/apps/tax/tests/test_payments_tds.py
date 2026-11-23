import datetime

from apps.accounting.models import Account, JournalEntry
from apps.company.models import CompanyProfileAuditEvent
from apps.tax.models import TaxPayment
from apps.tax.services import incometax, periods, tds
from .base import D, D0, T, TaxBase

d = datetime.date


def money(v):
    return D(str(v))


class GstPaymentTests(TaxBase):
    def entry(self, p):
        return JournalEntry.objects.get(source="tax_payment", source_id=p["id"])

    def lines(self, entry):
        return {(l.account.code, l.account.system_key, l.debit, l.credit) for l in entry.lines.select_related("account")}

    def test_head_wise_payment_posts_dr_liability_cr_bank(self):
        p = self.pay(heads={"cgst": "1000.00", "sgst": "1000.00", "igst": "2500.50", "interest": "100", "penalty": "50"}, cin="CPIN12345678",
                     bank_name="HDFC", period="2026-08", notes="Aug liability")
        self.assertRegex(p["reference"], r"^T2N/TP/\d{2}/\d{4}/\d{5}$")
        self.assertEqual((p["total"], p["period_label"], p["financial_year"], p["status"], p["cin"]), ("4650.50", "Aug 2026", "2026-27", "recorded", "CPIN12345678"))
        self.assertEqual(p["heads"], {"cgst": "1000.00", "sgst": "1000.00", "igst": "2500.50", "interest": "100.00", "penalty": "50.00"})
        lines = self.lines(self.entry(p))
        self.assertEqual(lines, {("2110", "gst_cgst_out", D("1000"), D0), ("2120", "gst_sgst_out", D("1000"), D0), ("2130", "gst_igst_out", D("2500.50"), D0),
                                 ("5960", "", D("150"), D0), ("1010", "bank", D0, D("4650.50"))})
        self.assertEqual(self.entry(p).reference, p["reference"])
        self.trial_balanced()
        # the payment shows up as a negative liability balance until the sales are posted
        self.assertEqual(self.balance("gst_igst_out"), D("-2500.50"))

    def test_cess_goes_to_its_own_account_and_a_chosen_bank_is_credited(self):
        bank = self.add_bank().json()
        p = self.pay(heads={"cess": "75"}, bank_account=bank["id"])
        lines = self.lines(self.entry(p))
        cess = Account.objects.get(company=self.company, name="GST cess payable")
        self.assertIn((cess.code, "", D("75"), D0), lines)
        bank_line = next(l for l in lines if l[3] == D("75"))
        self.assertTrue(Account.objects.get(code=bank_line[0]).bank_account_id is not None)
        self.assertEqual(p["bank_name"], "Example Bank")
        self.trial_balanced()

    def test_validation(self):
        bad = [({"heads": {}}, "heads"), ({"heads": {"igst": "0"}}, "heads"), ({"heads": {"igst": "-5"}}, "heads"),
               ({"heads": {"igst": "1.234"}}, "heads"), ({"heads": {"igst": "abc"}}, "heads"), ({"heads": {"tds": "100"}}, "heads"),
               ({"heads": "igst"}, "heads"), ({"date": ""}, "date"), ({"date": "2999-01-01"}, "date"), ({"date": "12/08/2026"}, "date"),
               ({"tax_type": "vat"}, "tax_type"), ({"cin": "bad cin!"}, "cin"), ({"period": "2026-13"}, "period"),
               ({"bank_account": "00000000-0000-0000-0000-000000000000"}, "bank_account"), ({"bank_account": "junk"}, "bank_account"),
               ({"financial_year": "2026"}, "financial_year")]
        for over, field in bad:
            payload = {"tax_type": "gst", "date": "2026-09-18", "heads": {"igst": "100"}, **over}
            r = self.fin.post(f"{T}/payments/", payload)
            self.assertEqual(r.status_code, 400, over)
            self.assertIn(field, r.json(), over)
        self.assertEqual(TaxPayment.objects.count(), 0)
        self.assertEqual(JournalEntry.objects.filter(source="tax_payment").count(), 0)

    def test_void_reverses_the_books_and_needs_a_reason(self):
        p = self.pay(heads={"igst": "1000"})
        url = f"{T}/payments/{p['id']}/void/"
        self.assertEqual(self.fin.post(url, {}).status_code, 400)
        r = self.fin.post(url, {"reason": "Paid twice"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["status"], r.json()["void_reason"]), ("void", "Paid twice"))
        self.assertEqual(self.balance("gst_igst_out"), D0)
        self.assertEqual(self.balance("bank"), D0)
        self.assertEqual(self.entry(p).status, "reversed")
        self.trial_balanced()
        self.assertEqual(self.fin.post(url, {"reason": "again"}).status_code, 400)
        lst = self.fin.get(f"{T}/payments/", {"status": "void"}).json()
        self.assertEqual((lst["count"], money(lst["total"])), (1, D0))

    def test_list_filters_totals_and_notes_edit(self):
        a = self.pay(heads={"igst": "1000"}, cin="A1")
        b = self.pay("tds", {"tds": "500"}, section="194j", quarter="2", date="2026-09-07")
        self.pay("advance_tax", {"tax": "30000"}, date="2026-06-10", instalment=1)
        r = self.fin.get(f"{T}/payments/").json()
        self.assertEqual((r["count"], money(r["total"])), (3, D("31500")))
        self.assertEqual(self.fin.get(f"{T}/payments/", {"type": "tds"}).json()["results"][0]["section"], "194J")
        self.assertEqual(self.fin.get(f"{T}/payments/", {"q": "A1"}).json()["count"], 1)
        self.assertEqual(self.fin.get(f"{T}/payments/", {"fy": "2025-26"}).json()["count"], 0)
        self.assertEqual(self.fin.get(f"{T}/payments/", {"fy": "20"}).status_code, 400)
        r = self.fin.patch(f"{T}/payments/{a['id']}/", {"cin": "NEW-9", "notes": "checked", "total": "1"})
        self.assertEqual((r.status_code, r.json()["cin"], r.json()["notes"], r.json()["total"]), (200, "NEW-9", "checked", "1000.00"))
        self.assertEqual(self.fin.patch(f"{T}/payments/{a['id']}/", {"cin": "bad!"}).status_code, 400)
        self.assertEqual(self.fin.get(f"{T}/payments/{b['id']}/").json()["period_label"], "Q2 2026-27")

    def test_unknown_payment_is_404(self):
        unknown = "00000000-0000-0000-0000-000000000000"
        for m, tail in (("get", ""), ("patch", ""), ("post", "void/")):
            self.assertEqual(getattr(self.fin, m)(f"{T}/payments/{unknown}/{tail}").status_code, 404)

    def test_payment_and_void_are_audited(self):
        p = self.pay()
        self.fin.post(f"{T}/payments/{p['id']}/void/", {"reason": "Duplicate"})
        ev = list(CompanyProfileAuditEvent.objects.filter(entity_type="tax_payment", entity_id=p["id"]).order_by("occurred_at"))
        self.assertEqual([e.action for e in ev], ["created", "deactivated"])
        self.assertEqual(ev[1].reason, "Duplicate")

    def test_a_payment_in_a_locked_year_is_refused_and_nothing_is_saved(self):
        from apps.company.models import FinancialYear
        FinancialYear.objects.filter(label="2026-27").update(is_locked=True)
        r = self.fin.post(f"{T}/payments/", {"tax_type": "gst", "date": "2026-09-18", "heads": {"igst": "100"}})
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(TaxPayment.objects.count(), 0)


class TdsTests(TaxBase):
    def setUp(self):
        super().setUp()
        self.tds_fixture()
        self.q = {"from": "2026-08-01", "to": "2026-09-30"}

    def sections(self, **extra):
        led = self.fin.get(f"{T}/tds/ledger/", {**self.q, **extra}).json()
        return {s["section"]: tuple(money(s[k]) for k in ("opening", "deducted", "paid", "closing")) for s in led["sections"]}, led

    def test_ledger_by_section_from_the_books(self):
        secs, led = self.sections()
        self.assertEqual(secs, {"194J": (D0, D("10000"), D0, D("10000")), "194C": (D0, D("500"), D0, D("500")), "192": (D0, D("3000"), D0, D("3000"))})
        self.assertEqual((money(led["deducted"]), money(led["paid"]), money(led["closing"])), (D("13500"), D0, D("13500")))
        self.assertEqual([r["section"] for r in led["rows"]], ["194J", "194C", "192"])
        self.assertEqual(money(led["rows"][-1]["balance"]), D("13500"))

    def test_challan_payment_settles_the_section_and_keeps_the_trial_balance(self):
        p = self.pay("tds", {"tds": "10000", "interest": "20"}, section="194J", date="2026-09-07", cin="281-CIN")
        secs, led = self.sections()
        self.assertEqual(secs["194J"], (D0, D("10000"), D("10000"), D0))
        self.assertEqual(money(led["closing"]), D("3500"))
        self.assertEqual(self.balance("tds_payable"), D("3500"))
        self.trial_balanced()
        self.fin.post(f"{T}/payments/{p['id']}/void/", {"reason": "Wrong challan"})
        secs, led = self.sections(to="2026-12-31")  # the reversal is dated the day it is made
        self.assertEqual(secs["194J"], (D0, D("10000"), D0, D("10000")))
        self.assertEqual(money(led["closing"]), D("13500"))

    def test_opening_balance_for_a_later_period(self):
        secs, led = self.sections(**{"from": "2026-09-01", "to": "2026-09-30"})
        self.assertEqual(secs["194J"], (D("10000"), D0, D0, D("10000")))
        self.assertEqual(led["rows"], [])
        self.assertEqual(money(led["opening"]), D("13500"))

    def test_quarterly_return_figures_split_salary_and_other(self):
        self.pay("tds", {"tds": "10000"}, section="194J", date="2026-09-07")
        f24 = self.fin.post(f"{T}/filings/", {"return_type": "tds_24q", "financial_year": "2026-27", "quarter": "2"}).json()
        f26 = self.fin.post(f"{T}/filings/", {"return_type": "tds_26q", "financial_year": "2026-27", "quarter": "2"}).json()
        a = self.fin.post(f"{T}/filings/{f24['id']}/prepare/").json()["figures"]
        b = self.fin.post(f"{T}/filings/{f26['id']}/prepare/").json()["figures"]
        self.assertEqual((a["tds_deducted"], a["tds_paid"]), ("3000.00", "0.00"))
        self.assertEqual((b["tds_deducted"], b["tds_paid"]), ("10500.00", "10000.00"))

    def test_payment_validation_for_tds(self):
        for over in ({"section": "XYZ"}, {"quarter": "7"}):
            r = self.fin.post(f"{T}/payments/", {"tax_type": "tds", "date": "2026-09-07", "heads": {"tds": "10"}, **over})
            self.assertEqual(r.status_code, 400, over)

    # ---- receivable tracker
    def credit(self, **over):
        payload = {"deductor_name": "Big Customer Ltd", "deductor_tan": "MUMB12345C", "section": "194j", "date": "2026-08-20",
                   "gross_amount": "100000", "tds_amount": "10000", **over}
        r = self.fin.post(f"{T}/tds/credits/", payload)
        return r

    def test_tracker_crud_and_summary(self):
        r = self.credit()
        self.assertEqual(r.status_code, 201, r.content)
        c = r.json()
        self.assertEqual((c["section"], c["financial_year"], c["quarter"], c["status"], c["tds_amount"], c["gross_amount"]),
                         ("194J", "2026-27", 2, "expected", "10000.00", "100000.00"))
        self.credit(deductor_name="Another Co", deductor_tan="", section="194C", tds_amount="2500.50", gross_amount="")
        r = self.fin.patch(f"{T}/tds/credits/{c['id']}/", {"status": "received", "certificate_no": "16A-001", "certificate_date": "2026-10-30"})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["certificate_no"]), (200, "received", "16A-001"))
        lst = self.fin.get(f"{T}/tds/credits/", {"fy": "2026-27"}).json()
        self.assertEqual(len(lst["results"]), 2)
        s = lst["summary"]
        self.assertEqual((money(s["total"]), money(s["by_status"]["received"]), money(s["by_status"]["expected"])), (D("12500.50"), D("10000"), D("2500.50")))
        self.assertEqual({x["section"]: money(x["amount"]) for x in s["by_section"]}, {"194C": D("2500.50"), "194J": D("10000")})
        self.assertEqual(money(s["books_balance"]), D0)
        self.assertEqual(self.fin.get(f"{T}/tds/credits/", {"q": "another"}).json()["results"][0]["deductor_name"], "Another Co")
        self.assertEqual(len(self.fin.get(f"{T}/tds/credits/", {"status": "received"}).json()["results"]), 1)
        self.assertEqual(self.fin.delete(f"{T}/tds/credits/{c['id']}/").status_code, 204)
        self.assertEqual(self.fin.get(f"{T}/tds/credits/{c['id']}/").status_code, 404)

    def test_tracker_validation(self):
        cases = [({"deductor_name": ""}, "deductor_name"), ({"deductor_tan": "BAD"}, "deductor_tan"), ({"section": "ZZ"}, "section"),
                 ({"section": ""}, "section"), ({"tds_amount": "0"}, "tds_amount"), ({"tds_amount": "-1"}, "tds_amount"),
                 ({"tds_amount": "1.234"}, "tds_amount"), ({"tds_amount": "abc"}, "tds_amount"), ({"date": ""}, "date"), ({"date": "bad"}, "date"),
                 ({"gross_amount": "-5"}, "gross_amount"), ({"quarter": "5"}, "quarter"), ({"status": "weird"}, "status"),
                 ({"status": "received"}, "certificate_no"), ({"financial_year": "x"}, "financial_year"), ({"customer_id": "nope"}, "customer_id")]
        for over, field in cases:
            r = self.credit(**over)
            self.assertEqual(r.status_code, 400, over)
            self.assertIn(field, r.json(), over)

    def test_tracker_changes_are_audited(self):
        c = self.credit().json()
        self.fin.patch(f"{T}/tds/credits/{c['id']}/", {"tds_amount": "9000"})
        self.fin.delete(f"{T}/tds/credits/{c['id']}/")
        ev = CompanyProfileAuditEvent.objects.filter(entity_type="tax_credit", entity_id=c["id"]).order_by("occurred_at")
        self.assertEqual([e.action for e in ev], ["created", "updated", "deleted"])

    def test_unknown_credit_is_404(self):
        unknown = "00000000-0000-0000-0000-000000000000"
        for m in ("get", "patch", "delete"):
            self.assertEqual(getattr(self.fin, m)(f"{T}/tds/credits/{unknown}/").status_code, 404)


class AdvanceTaxAndIncomeTaxTests(TaxBase):
    FY = "2026-27"

    def put(self, **data):
        r = self.fin.put(f"{T}/income-tax/?fy={self.FY}", data, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_instalment_dates_and_percentages(self):
        rows = periods.advance_tax_dates(self.FY)
        self.assertEqual([(n, due, int(p)) for n, due, p in rows], [(1, d(2026, 6, 15), 15), (2, d(2026, 9, 15), 45), (3, d(2026, 12, 15), 75), (4, d(2027, 3, 15), 100)])

    def test_schedule_from_the_estimated_liability(self):
        j = self.put(estimated_liability="200000")
        rows = j["schedule"]["instalments"]
        # 15% / 45% / 75% / 100% of 2,00,000 cumulative: 30,000 / 90,000 / 1,50,000 / 2,00,000
        self.assertEqual([(money(r["cumulative_required"]), money(r["required"])) for r in rows],
                         [(D("30000"), D("30000")), (D("90000"), D("60000")), (D("150000"), D("60000")), (D("200000"), D("50000"))])

    def test_payments_fill_the_instalments_and_shortfalls_show(self):
        self.put(estimated_liability="200000")
        self.pay("advance_tax", {"tax": "30000"}, date="2026-06-10", instalment=1)
        self.pay("advance_tax", {"tax": "50000"}, date="2026-09-14", instalment=2)
        sched = incometax.schedule(self.company, self.FY, today=d(2026, 10, 1))
        got = [(r["instalment"], money(r["paid"]), money(r["cumulative_paid"]), money(r["shortfall"]), r["status"]) for r in sched["instalments"]]
        self.assertEqual(got, [(1, D("30000"), D("30000"), D0, "paid"), (2, D("50000"), D("80000"), D("10000"), "overdue"),
                               (3, D0, D("80000"), D("70000"), "upcoming"), (4, D0, D("80000"), D("120000"), "upcoming")])
        self.assertEqual((money(sched["total_paid"]), money(sched["balance"])), (D("80000"), D("120000")))
        near = incometax.schedule(self.company, self.FY, today=d(2026, 11, 20))
        self.assertEqual(near["instalments"][2]["status"], "due_soon")

    def test_advance_tax_payment_posts_to_the_advance_tax_account_and_defaults_the_instalment(self):
        p = self.pay("advance_tax", {"tax": "30000"}, date="2026-06-10")
        self.assertEqual((p["instalment"], p["period_label"]), (1, "Instalment 1 2026-27"))
        later = self.pay("advance_tax", {"tax": "1000"}, date="2026-11-01")
        self.assertEqual(later["instalment"], 3)
        entry = JournalEntry.objects.get(source="tax_payment", source_id=p["id"])
        self.assertEqual({(l.account.name, l.debit, l.credit) for l in entry.lines.select_related("account")},
                         {("Advance tax paid", D("30000"), D0), ("Bank (default account)", D0, D("30000"))})
        self.trial_balanced()
        bad = self.fin.post(f"{T}/payments/", {"tax_type": "advance_tax", "date": "2026-06-10", "heads": {"tax": "1"}, "instalment": 7})
        self.assertEqual(bad.status_code, 400)

    def test_empty_schedule_before_an_estimate_is_set(self):
        j = self.fin.get(f"{T}/income-tax/", {"fy": self.FY}).json()
        self.assertEqual({r["status"] for r in j["schedule"]["instalments"]}, {"not_set"})
        self.assertIsNone(j["schedule"]["estimated_liability"])

    def book_profit(self):
        """Revenue 10,00,000 and rent 4,00,000 inside FY 2026-27: profit 6,00,000."""
        self.post([{"account": "bank", "debit": D("1000000")}, {"account": "sales", "credit": D("1000000")}], "2026-08-10")
        self.post([{"account": self.acct("5200"), "debit": D("400000")}, {"account": "bank", "credit": D("400000")}], "2026-08-11")
        # last year's profit is outside the year
        self.post([{"account": "bank", "debit": D("77777")}, {"account": "sales", "credit": D("77777")}], "2026-03-01")

    def test_computation_from_the_books_with_addbacks_and_deductions(self):
        self.book_profit()
        j = self.put(addbacks=[{"label": "Penalties", "amount": "10000"}], deductions=[{"label": "Depreciation difference", "amount": "50000"}])
        c = j["computation"]
        # 6,00,000 + 10,000 - 50,000 = 5,60,000 at 30% = 1,68,000; cess 4% = 6,720; total 1,74,720
        self.assertEqual((money(c["book_profit"]), money(c["taxable_income"]), money(c["tax"]), money(c["surcharge"]), money(c["cess"]), money(c["total_tax"])),
                         (D("600000"), D("560000"), D("168000"), D0, D("6720"), D("174720")))
        self.assertEqual(money(c["net_payable"]), D("174720"))
        self.assertEqual(money(j["schedule"]["suggested_liability"]), D("174720"))

    def test_surcharge_cess_rounding_and_credits(self):
        self.book_profit()
        self.fin.post(f"{T}/tds/credits/", {"deductor_name": "Big", "section": "194J", "date": "2026-08-20", "tds_amount": "20000"})
        self.pay("advance_tax", {"tax": "30000"}, date="2026-06-10")
        self.pay("income_tax", {"tax": "1000"}, date="2026-09-20")
        c = self.put(surcharge_pct="12", cess_pct="4", rate="30")["computation"]
        # tax 1,80,000 (on 6,00,000); surcharge 12% = 21,600; cess 4% of 2,01,600 = 8,064; total 2,09,664 -> 2,09,660 (nearest ten)
        self.assertEqual((money(c["tax"]), money(c["surcharge"]), money(c["cess"]), money(c["total_tax"])), (D("180000"), D("21600"), D("8064"), D("209660")))
        self.assertEqual((money(c["tds_credit"]), money(c["advance_tax_paid"]), money(c["self_assessment_paid"]), money(c["net_payable"])),
                         (D("20000"), D("30000"), D("1000"), D("158660")))

    def test_rounding_to_the_nearest_ten_rounds_a_five_up(self):
        self.assertEqual((incometax.round_ten(D("195686.40")), incometax.round_ten(D("100005")), incometax.round_ten(D("100004.99"))),
                         (D("195690"), D("100010"), D("100000")))

    def test_loss_means_no_tax_and_a_profit_override_replaces_the_books(self):
        self.post([{"account": self.acct("5200"), "debit": D("50000")}, {"account": "bank", "credit": D("50000")}], "2026-08-11")
        c = self.fin.get(f"{T}/income-tax/", {"fy": self.FY}).json()["computation"]
        self.assertEqual((money(c["book_profit"]), money(c["taxable_income"]), money(c["total_tax"])), (D("-50000"), D0, D0))
        c = self.put(profit_override="100000")["computation"]
        self.assertEqual((c["profit_overridden"], money(c["taxable_income"]), money(c["total_tax"])), (True, D("100000"), D("31200")))  # 30,000 + 4% cess
        c = self.put(profit_override="")["computation"]
        self.assertFalse(c["profit_overridden"])

    def test_worksheet_validation(self):
        for bad in ({"rate": "101"}, {"rate": "abc"}, {"surcharge_pct": "-1"}, {"cess_pct": "4.123"}, {"estimated_liability": "-5"}, {"estimated_liability": "x"},
                    {"profit_override": "1.234"}, {"addbacks": "text"}, {"addbacks": [{"label": "", "amount": "5"}]}, {"addbacks": [{"label": "x", "amount": "0"}]},
                    {"deductions": [{"label": "x", "amount": "abc"}]}, {"deductions": [1]}, {"addbacks": [{"label": "x", "amount": "1"}] * 51}, {"notes": "n" * 501}):
            r = self.fin.put(f"{T}/income-tax/?fy={self.FY}", bad, format="json")
            self.assertEqual(r.status_code, 400, bad)

    def test_worksheet_saves_are_audited(self):
        self.put(estimated_liability="1000")
        self.put(estimated_liability="2000")
        ev = CompanyProfileAuditEvent.objects.filter(entity_type="tax_worksheet").order_by("occurred_at")
        self.assertEqual(ev.count(), 2)
        self.assertIn("estimated_liability", ev[1].changed_fields)

    def test_itr_record_snapshot(self):
        self.book_profit()
        f = self.fin.post(f"{T}/filings/", {"return_type": "itr5", "financial_year": self.FY}).json()
        fig = self.fin.post(f"{T}/filings/{f['id']}/prepare/").json()["figures"]
        self.assertEqual((fig["book_profit"], fig["taxable_income"], fig["total_tax"], fig["net_payable"]), ("600000.00", "600000.00", "187200.00", "187200.00"))
        ta = self.fin.post(f"{T}/filings/", {"return_type": "tax_audit", "financial_year": self.FY}).json()
        fig = self.fin.post(f"{T}/filings/{ta['id']}/prepare/").json()["figures"]
        self.assertEqual((fig["turnover"], fig["book_profit"]), ("1000000.00", "600000.00"))
