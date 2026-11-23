import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.accounting.services import chart, ledger, reports
from apps.accounts.models import Role
from apps.company.constants import DocType
from apps.company.models import CompanyProfile, CompanyProfileAuditEvent
from apps.company.tests.base import API, CompanyTestMixin, client_for, make_user
from apps.partners import services
from apps.partners.models import PartnerTransaction, ProfitAllocation

P = "/api/v1/partners"
D = Decimal
AUG = "2026-08-10"


class Base(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.company = CompanyProfile.objects.first()
        chart.ensure_chart(self.company)
        self.asha = self.partner("Asha Rao")
        self.bala = self.partner("Bala Menon")
        self.chitra = self.partner("Chitra Nair")
        self.mp = make_user("mpartner", Role.MANAGING_PARTNER)
        self.api_mp = client_for(self.mp)
        self.pr = make_user("plainpartner", Role.PARTNER)
        self.api_pr = client_for(self.pr)

    # -- builders
    def partner(self, name):
        r = self.api.post(f"{API}/partners/", dict(full_name=name, designation="Partner", partner_type="partner"))
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def tx(self, partner, kind, amount, date=AUG, client=None, expect=201, **extra):
        r = (client or self.api).post(f"{P}/transactions/", {"partner": partner["id"], "kind": kind, "amount": str(amount), "date": date, **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def acct(self, key):
        return Account.objects.get(company=self.company, **({"system_key": key} if not key.isdigit() else {"code": key}))

    def entry(self, source_id, source="partner"):
        return JournalEntry.objects.get(company=self.company, source=source, source_id=source_id)

    def lines(self, entry):
        return sorted((l.account.code, l.debit, l.credit) for l in entry.lines.select_related("account"))

    def balanced(self, on="2026-12-31"):
        tb = reports.trial_balance(self.company, datetime.date.fromisoformat(on))
        self.assertTrue(tb["balanced"], tb)

    def sales(self, amount, date="2026-08-05"):
        """Income in the books, so there is a profit to share."""
        ledger.post(self.company, date=datetime.date.fromisoformat(date), actor=self.admin,
                    lines=[{"account": "bank", "debit": D(str(amount))}, {"account": "sales", "credit": D(str(amount))}])

    def ratio(self, shares, effective_from="2026-04-01", client=None, expect=201):
        body = {"effective_from": effective_from, "shares": [{"partner": p["id"], "percent": str(pct)} for p, pct in shares]}
        r = (client or self.api).post(f"{P}/profit-shares/", body)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def ratio_even(self, effective_from="2026-04-01"):
        return self.ratio([(self.asha, "33.33"), (self.bala, "33.33"), (self.chitra, "33.34")], effective_from)

    def bal(self, partner):
        return self.api.get(f"{P}/partners/{partner['id']}/").json()


class PostingTests(Base):
    def test_capital_introduced_posts_to_bank_and_capital_with_partner_tag(self):
        t = self.tx(self.asha, "capital_introduced", "500000", mode="bank_transfer", reference="UTR1", narration="Initial capital")
        self.assertRegex(t["number"], r"^T2N/PTX/26/1008/\d{5}$")
        self.assertEqual(t["status"], "posted")
        e = self.entry(t["id"])
        self.assertEqual((e.reference, e.date), (t["number"], datetime.date(2026, 8, 10)))
        self.assertEqual(self.lines(e), [("1010", D("500000.00"), D("0")), ("3000", D("0"), D("500000.00"))])
        self.assertTrue(all(l.party_type == "partner" and l.party_id == self.asha["id"] for l in e.lines.all()))
        self.assertEqual(D(self.bal(self.asha)["capital"]), D("500000"))
        self.balanced()

    def test_cash_mode_posts_to_cash_and_a_chosen_bank_account_is_used(self):
        t = self.tx(self.asha, "capital_introduced", "1000", mode="cash")
        self.assertEqual(self.lines(self.entry(t["id"]))[0][0], "1000")
        bank = chart.money_accounts(self.company).filter(subtype="bank").first()
        t2 = self.tx(self.asha, "capital_introduced", "2000", money_account=str(bank.id))
        self.assertIn(bank.code, [l[0] for l in self.lines(self.entry(t2["id"]))])
        self.assertEqual(self.api.post(f"{P}/transactions/", {"partner": self.asha["id"], "kind": "capital_introduced", "amount": "5", "date": AUG,
                                                              "money_account": str(self.acct("sales").id)}).status_code, 400)

    def test_each_kind_posts_the_expected_lines(self):
        self.tx(self.asha, "capital_introduced", "400000")
        cases = {
            "capital_withdrawn": [("1010", D("0"), D("1000.00")), ("3000", D("1000.00"), D("0"))],
            "drawing": [("1010", D("0"), D("1000.00")), ("3100", D("1000.00"), D("0"))],
            "remuneration": [("3100", D("0"), D("1000.00")), ("5150", D("1000.00"), D("0"))],
            "interest_on_capital": [("3100", D("0"), D("1000.00")), ("5750", D("1000.00"), D("0"))],
            "loan_from_partner": [("1010", D("1000.00"), D("0")), ("2500", D("0"), D("1000.00"))],
            "profit_share_credit": [("3100", D("0"), D("1000.00")), ("3200", D("1000.00"), D("0"))],
        }
        for kind, expected in cases.items():
            t = self.tx(self.asha, kind, "1000")
            self.assertEqual(self.lines(self.entry(t["id"])), expected, kind)
        t = self.tx(self.asha, "loan_repayment", "400")
        self.assertEqual(self.lines(self.entry(t["id"])), [("1010", D("0"), D("400.00")), ("2500", D("400.00"), D("0"))])
        t = self.tx(self.asha, "profit_share_transfer", "300")
        self.assertEqual(self.lines(self.entry(t["id"])), [("3000", D("0"), D("300.00")), ("3100", D("300.00"), D("0"))])
        self.balanced()

    def test_balances_are_hand_computed(self):
        a = self.asha
        self.tx(a, "capital_introduced", "500000")
        self.tx(a, "capital_withdrawn", "100000", date="2026-09-01")
        self.tx(a, "drawing", "30000", date="2026-09-05")
        self.tx(a, "remuneration", "20000", date="2026-09-30")
        self.tx(a, "loan_from_partner", "50000", date="2026-10-01")
        self.tx(a, "profit_share_credit", "25000", date="2026-10-01")
        self.tx(a, "profit_share_transfer", "5000", date="2026-10-02")
        row = self.bal(a)
        # capital 500,000 - 100,000 + 5,000; current -30,000 + 20,000 + 25,000 - 5,000
        self.assertEqual((D(row["capital"]), D(row["current"]), D(row["loan"])), (D("405000"), D("10000"), D("50000")))
        self.assertEqual(D(row["total"]), D("415000"))
        self.assertEqual(self.api.get(f"{P}/dashboard/").json()["books_agree"]["agree"], True)
        self.balanced()

    def test_validation(self):
        base = {"partner": self.asha["id"], "kind": "capital_introduced", "amount": "100", "date": AUG}
        for patch, field in (({"amount": "0"}, "amount"), ({"amount": "-5"}, "amount"), ({"amount": "abc"}, "amount"),
                             ({"amount": "1.234"}, "amount"), ({"kind": "gift"}, "kind"), ({"date": "10/08/2026"}, "date"),
                             ({"date": ""}, "date"), ({"mode": "barter"}, "mode"), ({"partner": ""}, "partner"),
                             ({"reference": "x" * 101}, "reference"), ({"narration": "x" * 301}, "narration")):
            r = self.api.post(f"{P}/transactions/", {**base, **patch})
            self.assertEqual(r.status_code, 400, patch)
            self.assertIn(field, r.json(), patch)
        self.assertEqual(self.api.post(f"{P}/transactions/", {**base, "partner": "00000000-0000-0000-0000-000000000000"}).status_code, 404)

    def test_cannot_withdraw_more_than_the_capital_or_repay_more_than_the_loan(self):
        self.tx(self.asha, "capital_introduced", "10000")
        r = self.api.post(f"{P}/transactions/", {"partner": self.asha["id"], "kind": "capital_withdrawn", "amount": "10000.01", "date": AUG})
        self.assertEqual(r.status_code, 400)
        self.assertIn("amount", r.json())
        self.tx(self.asha, "capital_withdrawn", "10000")  # exactly all of it is fine
        self.assertEqual(self.api.post(f"{P}/transactions/", {"partner": self.asha["id"], "kind": "loan_repayment", "amount": "1", "date": AUG}).status_code, 400)
        self.assertEqual(self.api.post(f"{P}/transactions/", {"partner": self.asha["id"], "kind": "profit_share_transfer", "amount": "1", "date": AUG}).status_code, 400)

    def test_locked_financial_year_refuses_posting_and_consumes_no_number(self):
        fy = self.company.financial_years.first()
        fy.is_locked = True
        fy.save()
        r = self.api.post(f"{P}/transactions/", {"partner": self.asha["id"], "kind": "capital_introduced", "amount": "5", "date": AUG})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(PartnerTransaction.objects.count(), 0)

    def test_interest_on_capital_from_a_rate_is_worked_out(self):
        self.tx(self.asha, "capital_introduced", "1000000", date="2026-04-01")
        r = self.api.get(f"{P}/interest-preview/", {"partner": self.asha["id"], "rate": "10", "from": "2026-05-01", "to": "2026-07-29"})
        # opening capital 10,00,000 at 10% for 90 days = 1,000,000 x 0.10 x 90 / 365 = 24,657.53
        self.assertEqual((D(r.json()["interest"]), r.json()["days"]), (D("24657.53"), 90))
        t = self.tx(self.asha, "interest_on_capital", "", rate="10", period_from="2026-05-01", period_to="2026-07-29", date="2026-07-31")
        self.assertEqual((D(t["amount"]), D(t["rate"])), (D("24657.53"), D("10")))
        self.assertEqual(self.lines(self.entry(t["id"])), [("3100", D("0"), D("24657.53")), ("5750", D("24657.53"), D("0"))])
        bad = self.api.get(f"{P}/interest-preview/", {"partner": self.asha["id"], "rate": "10", "from": "2026-07-01", "to": "2026-06-01"})
        self.assertEqual(bad.status_code, 400)

    def test_notes_can_change_but_not_the_money(self):
        t = self.tx(self.asha, "drawing", "100")
        r = self.api.patch(f"{P}/transactions/{t['id']}/", {"narration": "Personal", "reference": "CHQ 7", "amount": "999"})
        self.assertEqual((r.json()["narration"], r.json()["reference"], D(r.json()["amount"])), ("Personal", "CHQ 7", D("100")))


class ApprovalTests(Base):
    def test_a_large_drawing_waits_and_posts_nothing_until_approved(self):
        t = self.tx(self.asha, "drawing", "150000")
        self.assertEqual(t["status"], "pending_approval")
        self.assertFalse(JournalEntry.objects.filter(source="partner", source_id=t["id"]).exists())
        self.assertEqual(D(self.bal(self.asha)["current"]), D("0"))
        r = self.api.post(f"{P}/transactions/{t['id']}/approve/")  # an administrator may approve what they requested
        self.assertEqual((r.status_code, r.json()["status"]), (200, "posted"))
        self.assertEqual(self.lines(self.entry(t["id"])), [("1010", D("0"), D("150000.00")), ("3100", D("150000.00"), D("0"))])
        self.assertEqual(D(self.bal(self.asha)["current"]), D("-150000"))
        self.assertEqual(self.api.post(f"{P}/transactions/{t['id']}/approve/").status_code, 400)  # already decided

    def test_exactly_at_the_threshold_posts_at_once_and_zero_threshold_switches_approval_off(self):
        self.assertEqual(self.tx(self.asha, "drawing", "100000")["status"], "posted")
        self.assertEqual(self.api.patch(f"{P}/settings/", {"withdrawal_approval_threshold": "0"}).status_code, 200)
        self.assertEqual(self.tx(self.asha, "drawing", "900000")["status"], "posted")
        self.api.patch(f"{P}/settings/", {"withdrawal_approval_threshold": "50000"})
        self.assertEqual(self.tx(self.asha, "drawing", "50001")["status"], "pending_approval")
        self.assertEqual(self.tx(self.asha, "loan_from_partner", "5000000")["status"], "posted")  # only withdrawals wait

    def test_a_non_admin_cannot_approve_their_own_request_or_their_own_account(self):
        t = self.tx(self.asha, "drawing", "150000", client=self.api_mp)
        r = self.api_mp.post(f"{P}/transactions/{t['id']}/approve/")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api_mp.post(f"{P}/transactions/{t['id']}/reject/", {"reason": "no"}).status_code, 400)
        # a managing partner who is the partner concerned (linked by user) cannot approve someone else's request for it either
        link = self.api.patch(f"{P}/partners/{self.bala['id']}/", {"user": str(self.mp.pk)})
        self.assertEqual(link.status_code, 200, link.content)
        t2 = self.tx(self.bala, "drawing", "150000")  # requested by the admin
        self.assertEqual(self.api_mp.post(f"{P}/transactions/{t2['id']}/approve/").status_code, 400)
        mp2 = client_for(make_user("mpartner2", Role.MANAGING_PARTNER))
        self.assertEqual(mp2.post(f"{P}/transactions/{t2['id']}/approve/").json()["status"], "posted")
        self.assertEqual(mp2.post(f"{P}/transactions/{t['id']}/approve/").json()["status"], "posted")  # someone else's request

    def test_reject_needs_a_reason_and_posts_nothing(self):
        self.tx(self.asha, "capital_introduced", "500000")
        t = self.tx(self.asha, "capital_withdrawn", "150000")
        self.assertEqual(t["status"], "pending_approval")
        self.assertEqual(self.api.post(f"{P}/transactions/{t['id']}/reject/", {}).status_code, 400)
        r = self.api.post(f"{P}/transactions/{t['id']}/reject/", {"reason": "Not agreed by partners"})
        self.assertEqual((r.json()["status"], r.json()["decision_reason"]), ("rejected", "Not agreed by partners"))
        self.assertEqual(D(self.bal(self.asha)["capital"]), D("500000"))
        self.assertEqual(self.api.post(f"{P}/transactions/{t['id']}/void/", {"reason": "x"}).status_code, 400)  # rejected is final

    def test_approval_actions_need_the_manage_permission(self):
        t = self.tx(self.asha, "drawing", "150000")
        for c in (self.api_pr, self.api_finance, self.api_staff):
            self.assertEqual(c.post(f"{P}/transactions/{t['id']}/approve/").status_code, 403)

    def test_approvals_module_hook_functions(self):
        t = PartnerTransaction.objects.get(pk=self.tx(self.asha, "drawing", "150000")["id"])
        services.approve(t, self.admin)
        t.refresh_from_db()
        self.assertEqual(t.status, "posted")


class VoidTests(Base):
    def test_void_reverses_the_posting_and_keeps_the_record(self):
        t = self.tx(self.asha, "capital_introduced", "100000")
        self.assertEqual(self.api.post(f"{P}/transactions/{t['id']}/void/", {}).status_code, 400)  # a reason is needed
        r = self.api.post(f"{P}/transactions/{t['id']}/void/", {"reason": "Wrong partner"})
        self.assertEqual((r.status_code, r.json()["status"], r.json()["decision_reason"]), (200, "void", "Wrong partner"))
        self.assertEqual(self.entry(t["id"]).status, "reversed")
        self.assertEqual(D(self.bal(self.asha)["capital"]), D("0"))
        self.assertEqual(self.api.post(f"{P}/transactions/{t['id']}/void/", {"reason": "again"}).status_code, 400)
        # the ledger accounts net to nothing
        self.assertEqual(D(self.api.get(f"{P}/dashboard/").json()["books_agree"]["ledger"]["capital"]), D("0"))
        self.balanced()

    def test_pending_can_be_voided_without_touching_the_books(self):
        t = self.tx(self.asha, "drawing", "150000")
        self.assertEqual(self.api.post(f"{P}/transactions/{t['id']}/void/", {"reason": "Changed mind"}).json()["status"], "void")
        self.assertEqual(JournalEntry.objects.filter(source="partner").count(), 0)

    def test_voiding_capital_that_has_been_withdrawn_is_refused(self):
        a = self.tx(self.asha, "capital_introduced", "100000")
        self.tx(self.asha, "capital_withdrawn", "60000")
        r = self.api.post(f"{P}/transactions/{a['id']}/void/", {"reason": "oops"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(D(self.bal(self.asha)["capital"]), D("40000"))

    def test_voiding_needs_manage(self):
        t = self.tx(self.asha, "drawing", "10")
        self.assertEqual(self.api_pr.post(f"{P}/transactions/{t['id']}/void/", {"reason": "x"}).status_code, 403)


class RatioTests(Base):
    def test_ratio_must_total_exactly_100(self):
        for shares in ([(self.asha, "50"), (self.bala, "49.99")], [(self.asha, "50"), (self.bala, "50.01")], [(self.asha, "100"), (self.bala, "0")]):
            r = self.ratio(shares, expect=400)
            self.assertIn("shares", r)
        self.assertIn("100%", self.ratio([(self.asha, "60"), (self.bala, "30")], expect=400)["shares"][0])
        self.ratio([(self.asha, "50"), (self.asha, "50")], expect=400)  # the same partner twice
        r = self.api.post(f"{P}/profit-shares/", {"effective_from": "2026-04-01", "shares": [{"partner": self.asha["id"], "percent": "x"}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api.post(f"{P}/profit-shares/", {"effective_from": "2026-04-01", "shares": []}).status_code, 400)
        self.assertEqual(self.api.post(f"{P}/profit-shares/", {"effective_from": "bad", "shares": [{"partner": self.asha["id"], "percent": "100"}]}).status_code, 400)
        ok = self.ratio_even()
        self.assertEqual(D(ok["total"]), D("100"))

    def test_effective_dating_and_history(self):
        self.ratio([(self.asha, "50"), (self.bala, "50")], "2026-04-01")
        self.ratio([(self.asha, "40"), (self.bala, "40"), (self.chitra, "20")], "2026-10-01")
        self.assertEqual(self.ratio([(self.asha, "100")], "2026-10-01", expect=400)["effective_from"][0], "A ratio already starts on this date. Choose another date or remove that one.")
        rows = self.api.get(f"{P}/profit-shares/").json()
        self.assertEqual([r["effective_from"] for r in rows], ["2026-10-01", "2026-04-01"])
        self.assertEqual(services.schedule_on(self.company, datetime.date(2026, 9, 30)).effective_from, datetime.date(2026, 4, 1))
        self.assertEqual(services.schedule_on(self.company, datetime.date(2026, 10, 1)).effective_from, datetime.date(2026, 10, 1))
        self.assertIsNone(services.schedule_on(self.company, datetime.date(2026, 3, 31)))
        # the ratio on a date sets the "profit share %" shown for each partner
        rows = {r["name"]: r for r in self.api.get(f"{P}/partners/", {"as_of": "2026-05-01"}).json()["results"]}
        self.assertEqual(D(rows["Asha Rao"]["profit_share_percent"]), D("50"))
        self.assertEqual(D(rows["Chitra Nair"]["profit_share_percent"]), D("0"))

    def test_remove_a_ratio_unless_an_allocation_used_it(self):
        s = self.ratio_even()
        self.sales(1000)
        a = self.api.post(f"{P}/allocations/", {"period_start": "2026-04-01", "period_end": "2026-09-30"}).json()
        self.assertEqual(self.api.delete(f"{P}/profit-shares/{s['id']}/").status_code, 400)
        self.api.delete(f"{P}/allocations/{a['id']}/")
        self.assertEqual(self.api.delete(f"{P}/profit-shares/{s['id']}/").status_code, 204)

    def test_ratio_needs_manage(self):
        self.ratio_even(), self.ratio([(self.asha, "100")], "2027-01-01", client=self.api_pr, expect=403)


class AllocationTests(Base):
    def test_remainder_goes_to_the_largest_share_hand_computed(self):
        self.ratio_even()
        self.sales("10.00")
        pv = self.api.post(f"{P}/allocations/preview/", {"period_start": "2026-04-01", "period_end": "2026-09-30"}).json()
        self.assertEqual(D(pv["net_profit"]), D("10.00"))
        # 33.33% of 10.00 = 3.333 -> 3.33; 33.34% = 3.334 -> 3.33; rounded parts add to 9.99, so the 0.01 left goes to the largest (33.34%)
        got = {s["name"]: D(s["amount"]) for s in pv["shares"]}
        self.assertEqual(got, {"Asha Rao": D("3.33"), "Bala Menon": D("3.33"), "Chitra Nair": D("3.34")})
        self.assertEqual(sum(got.values()), D("10.00"))

    def test_apportion_unit_cases(self):
        P1, P2, P3 = (type("P", (), {"full_name": n}) for n in ("A", "B", "C"))
        out = services.apportion(D("100.00"), [(P1, D("33.33")), (P2, D("33.33")), (P3, D("33.34"))])
        self.assertEqual([a for _p, _c, a in out], [D("33.33"), D("33.33"), D("33.34")])
        # equal shares: 100.01 / 3 = 33.33 each (99.99), remainder 0.02 to the first by name
        out = services.apportion(D("100.01"), [(P2, D("33.33")), (P1, D("33.33")), (P3, D("33.34"))])
        self.assertEqual([a for _p, _c, a in out], [D("33.33"), D("33.33"), D("33.35")])
        out = services.apportion(D("100.00"), [(P2, D("50")), (P1, D("50"))])
        self.assertEqual([a for _p, _c, a in out], [D("50.00"), D("50.00")])
        out = services.apportion(D("0.05"), [(P2, D("50")), (P1, D("50"))])  # 0.025 -> 0.03 each (half up) = 0.06; 0.01 too much, taken off the first by name
        self.assertEqual([a for _p, _c, a in out], [D("0.03"), D("0.02")])
        loss = services.apportion(D("-10.00"), [(P1, D("33.33")), (P2, D("33.33")), (P3, D("33.34"))])
        self.assertEqual([a for _p, _c, a in loss], [D("-3.33"), D("-3.33"), D("-3.34")])

    def test_profit_is_taken_after_remuneration_and_interest_and_other_deductions(self):
        self.ratio([(self.asha, "50"), (self.bala, "30"), (self.chitra, "20")])
        self.tx(self.asha, "capital_introduced", "1000000", date="2026-04-02")
        self.sales("100000")
        self.tx(self.asha, "remuneration", "15000", date="2026-06-30")  # an expense: net profit falls to 85,000
        self.tx(self.asha, "interest_on_capital", "5000", date="2026-06-30")  # and to 80,000
        pv = self.api.post(f"{P}/allocations/preview/", {"period_start": "2026-04-01", "period_end": "2026-09-30", "other_deductions": "10000"}).json()
        self.assertEqual((D(pv["net_profit"]), D(pv["remuneration_charged"]), D(pv["interest_charged"]), D(pv["distributable"])),
                         (D("80000"), D("15000"), D("5000"), D("70000")))
        self.assertEqual([D(s["amount"]) for s in pv["shares"]], [D("35000"), D("21000"), D("14000")])

    def test_preview_then_post_credits_each_current_account(self):
        self.ratio([(self.asha, "50"), (self.bala, "30"), (self.chitra, "20")])
        self.sales("100000")
        a = self.api.post(f"{P}/allocations/", {"period_start": "2026-04-01", "period_end": "2026-09-30"}).json()
        self.assertEqual((a["status"], a["number"]), ("draft", ""))
        self.assertEqual(JournalEntry.objects.filter(source="partner").count(), 0)  # a draft posts nothing
        r = self.api.post(f"{P}/allocations/{a['id']}/post/", {})
        self.assertEqual(r.status_code, 200, r.content)
        a = r.json()
        self.assertEqual((a["status"], a["post_date"]), ("posted", "2026-09-30"))
        self.assertRegex(a["number"], r"^T2N/PTX/26/3009/\d{5}$")
        e = self.entry(a["id"])
        self.assertEqual(self.lines(e), [("3100", D("0"), D("20000.00")), ("3100", D("0"), D("30000.00")), ("3100", D("0"), D("50000.00")),
                                         ("3200", D("100000.00"), D("0"))])
        by_party = {l.party_id: l.credit for l in e.lines.filter(credit__gt=0)}
        self.assertEqual(by_party[self.asha["id"]], D("50000.00"))
        rows = {r["name"]: D(r["current"]) for r in self.api.get(f"{P}/partners/").json()["results"]}
        self.assertEqual(rows, {"Asha Rao": D("50000"), "Bala Menon": D("30000"), "Chitra Nair": D("20000")})
        self.balanced()
        self.assertEqual(self.api.post(f"{P}/allocations/{a['id']}/post/", {}).status_code, 400)  # not twice

    def test_overlapping_period_is_refused_and_reverse_frees_it(self):
        self.ratio_even()
        self.sales("1000")
        a = self.api.post(f"{P}/allocations/", {"period_start": "2026-04-01", "period_end": "2026-09-30"}).json()
        b = self.api.post(f"{P}/allocations/", {"period_start": "2026-07-01", "period_end": "2026-12-31"}).json()
        self.api.post(f"{P}/allocations/{a['id']}/post/", {})
        self.assertEqual(self.api.post(f"{P}/allocations/{b['id']}/post/", {}).status_code, 400)
        self.assertEqual(self.api.post(f"{P}/allocations/{a['id']}/reverse/", {}).status_code, 400)  # reason needed
        r = self.api.post(f"{P}/allocations/{a['id']}/reverse/", {"reason": "Wrong period"})
        self.assertEqual((r.json()["status"], r.json()["reverse_reason"]), ("reversed", "Wrong period"))
        rows = {x["name"]: D(x["current"]) for x in self.api.get(f"{P}/partners/").json()["results"]}
        self.assertEqual(set(rows.values()), {D("0")})
        self.assertEqual(self.api.post(f"{P}/allocations/{b['id']}/post/", {}).status_code, 200)
        self.balanced()
        self.assertEqual(self.api.post(f"{P}/allocations/{a['id']}/reverse/", {"reason": "again"}).status_code, 400)

    def test_a_loss_is_charged_to_the_partners(self):
        self.ratio([(self.asha, "60"), (self.bala, "40")])
        ledger.post(self.company, date=datetime.date(2026, 5, 1), actor=self.admin,
                    lines=[{"account": self.acct("5200"), "debit": D("1000")}, {"account": "bank", "credit": D("1000")}])
        a = self.api.post(f"{P}/allocations/", {"period_start": "2026-04-01", "period_end": "2026-06-30"}).json()
        self.assertEqual(D(a["distributable"]), D("-1000"))
        self.assertEqual([D(s["amount"]) for s in a["shares"]], [D("-600.00"), D("-400.00")])
        self.api.post(f"{P}/allocations/{a['id']}/post/", {})
        self.assertEqual(self.lines(self.entry(a["id"])), [("3100", D("400.00"), D("0")), ("3100", D("600.00"), D("0")), ("3200", D("0"), D("1000.00"))])
        self.balanced()

    def test_validation_and_no_ratio(self):
        r = self.api.post(f"{P}/allocations/preview/", {"period_start": "2026-04-01", "period_end": "2026-09-30"})
        self.assertEqual(r.status_code, 400)  # no ratio yet
        self.ratio_even()
        for body in ({"period_start": "2026-09-30", "period_end": "2026-04-01"}, {"period_start": "", "period_end": "2026-04-01"},
                     {"period_start": "2026-04-01", "period_end": "x"}, {"period_start": "2026-04-01", "period_end": "2026-09-30", "other_deductions": "abc"}):
            self.assertEqual(self.api.post(f"{P}/allocations/preview/", body).status_code, 400, body)
        zero = self.api.post(f"{P}/allocations/", {"period_start": "2026-04-01", "period_end": "2026-06-30"}).json()
        self.assertTrue(zero["id"])
        self.assertEqual(self.api.post(f"{P}/allocations/{zero['id']}/post/", {}).status_code, 400)  # nothing to share

    def test_a_draft_can_be_rerun_with_new_figures_and_deleted(self):
        self.ratio([(self.asha, "50"), (self.bala, "50")])
        self.sales("1000")
        a = self.api.post(f"{P}/allocations/", {"period_start": "2026-04-01", "period_end": "2026-09-30"}).json()
        self.sales("1000", "2026-08-20")
        r = self.api.patch(f"{P}/allocations/{a['id']}/", {"other_deductions": "500", "deduction_note": "Reserve"})
        self.assertEqual((D(r.json()["distributable"]), D(r.json()["net_profit"])), (D("1500"), D("2000")))
        self.api.post(f"{P}/allocations/{a['id']}/post/", {})
        self.assertEqual(self.api.patch(f"{P}/allocations/{a['id']}/", {"other_deductions": "0"}).status_code, 400)
        self.assertEqual(self.api.delete(f"{P}/allocations/{a['id']}/").status_code, 400)


class StatementTests(Base):
    def scenario(self):
        a = self.asha
        self.ratio([(a, "60"), (self.bala, "40")])
        self.tx(a, "capital_introduced", "300000", date="2026-04-05")
        self.tx(a, "capital_withdrawn", "50000", date="2026-05-10")
        self.tx(a, "drawing", "20000", date="2026-06-15")
        self.tx(a, "remuneration", "12000", date="2026-06-30")
        self.tx(a, "interest_on_capital", "3000", date="2026-06-30")
        self.sales("100000")
        alloc = self.api.post(f"{P}/allocations/", {"period_start": "2026-04-01", "period_end": "2026-09-30"}).json()
        # profit: sales 100,000 less remuneration 12,000 and interest 3,000 = 85,000; Asha 60% = 51,000
        self.api.post(f"{P}/allocations/{alloc['id']}/post/", {})
        self.tx(a, "profit_share_transfer", "10000", date="2026-10-15")
        self.tx(a, "drawing", "5000", date="2026-10-20")
        return alloc

    def test_statement_balances_by_hand(self):
        self.scenario()
        r = self.api.get(f"{P}/partners/{self.asha['id']}/statement/", {"from": "2026-04-01", "to": "2026-09-30"}).json()
        m = {x["key"]: (D(x["capital"]), D(x["current"])) for x in r["movements"]}
        self.assertEqual(m["capital_introduced"], (D("300000"), D("0")))
        self.assertEqual(m["capital_withdrawn"], (D("-50000"), D("0")))
        self.assertEqual(m["drawings"], (D("0"), D("-20000")))
        self.assertEqual(m["remuneration"], (D("0"), D("12000")))
        self.assertEqual(m["interest"], (D("0"), D("3000")))
        self.assertEqual(m["profit_share"], (D("0"), D("51000")))
        self.assertEqual((D(r["opening"]["total"]), D(r["closing"]["capital"]), D(r["closing"]["current"]), D(r["closing"]["total"])),
                         (D("0"), D("250000"), D("46000"), D("296000")))
        self.assertEqual(D(r["rows"][-1]["balance"]), D("296000"))
        # next period: opening is the previous closing; transfer moves 10,000 between accounts with no change to the total
        r2 = self.api.get(f"{P}/partners/{self.asha['id']}/statement/", {"from": "2026-10-01", "to": "2027-03-31"}).json()
        self.assertEqual((D(r2["opening"]["capital"]), D(r2["opening"]["current"])), (D("250000"), D("46000")))
        self.assertEqual((D(r2["closing"]["capital"]), D(r2["closing"]["current"]), D(r2["closing"]["total"])), (D("260000"), D("31000"), D("291000")))
        self.assertEqual(sum(D(x["total"]) for x in r2["movements"]), D("-5000"))
        # statement agrees with the ledger
        self.assertTrue(self.api.get(f"{P}/dashboard/").json()["books_agree"]["agree"])
        self.balanced()

    def test_a_voided_or_reversed_entry_is_not_in_the_statement(self):
        alloc = self.scenario()
        self.api.post(f"{P}/allocations/{alloc['id']}/reverse/", {"reason": "redo"})
        r = self.api.get(f"{P}/partners/{self.asha['id']}/statement/", {"from": "2026-04-01", "to": "2026-09-30"}).json()
        self.assertEqual(D(r["closing"]["current"]), D("-5000"))  # 12,000 + 3,000 - 20,000

    def test_period_defaults_to_the_financial_year_and_bad_dates_fail(self):
        self.tx(self.asha, "capital_introduced", "1000", date="2026-06-01")
        r = self.api.get(f"{P}/partners/{self.asha['id']}/statement/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(D(r.json()["closing"]["capital"]), D("1000"))
        self.assertEqual(self.api.get(f"{P}/partners/{self.asha['id']}/statement/", {"from": "2026-09-01", "to": "2026-08-01"}).status_code, 400)
        self.assertEqual(self.api.get(f"{P}/partners/{self.asha['id']}/statement/", {"from": "nope"}).status_code, 400)

    def test_pdf_statement_needs_the_document_type_to_be_printable(self):
        self.scenario()
        r = self.api.get(f"{P}/partners/{self.asha['id']}/statement/").json()
        self.assertEqual(r["pdf_available"], DocType.PARTNER_TRANSACTION in DocType.PRINTABLE)
        if DocType.PARTNER_TRANSACTION not in DocType.PRINTABLE:
            self.assertEqual(self.api.post(f"{P}/partners/{self.asha['id']}/statement/pdf/", {}).status_code, 400)
        with mock.patch.object(DocType, "PRINTABLE", DocType.PRINTABLE + [DocType.PARTNER_TRANSACTION]):
            r = self.api.post(f"{P}/partners/{self.asha['id']}/statement/pdf/", {"from": "2026-04-01", "to": "2026-09-30"})
            self.assertEqual(r.status_code, 201, r.content)
            self.assertRegex(r.json()["reference_number"], r"^T2N/PTX/")
            pdf = self.api.get(f"{API}/issued-documents/{r.json()['id']}/download/")
            self.assertEqual(pdf.status_code, 200)

    def test_capital_history_by_month(self):
        self.tx(self.asha, "capital_introduced", "1000", date="2026-04-15")
        self.tx(self.bala, "capital_introduced", "500", date="2026-05-10")
        self.tx(self.asha, "capital_withdrawn", "200", date="2026-06-02")
        h = self.api.get(f"{P}/history/", {"from": "2026-04-01", "to": "2026-07-31"}).json()
        self.assertEqual([(m["label"], D(m["capital"])) for m in h["months"]],
                         [("Apr 26", D("1000")), ("May 26", D("1500")), ("Jun 26", D("1300")), ("Jul 26", D("1300"))])
        one = self.api.get(f"{P}/history/", {"from": "2026-04-01", "to": "2026-06-30", "partner": self.bala["id"]}).json()
        self.assertEqual([D(m["capital"]) for m in one["months"]], [D("0"), D("500"), D("500")])


class LoanTests(Base):
    def test_loan_ledger_with_interest_estimate_by_hand(self):
        a = self.asha
        self.tx(a, "loan_from_partner", "100000", date="2026-06-01", rate="12")
        self.tx(a, "loan_repayment", "40000", date="2026-07-01")
        r = self.api.get(f"{P}/loans/", {"as_of": "2026-07-31"}).json()
        row = r["rows"][0]
        # 100,000 x 12% x 30/365 = 986.30 (to 1 Jul), then 60,000 x 12% x 30/365 = 591.78 (to 31 Jul): total 1,578.08
        self.assertEqual((D(row["lent"]), D(row["repaid"]), D(row["outstanding"]), D(row["interest_estimate"])),
                         (D("100000"), D("40000"), D("60000"), D("1578.08")))
        self.assertEqual(D(r["total_outstanding"]), D("60000"))
        self.assertEqual(len(row["open_loans"]), 1)
        self.assertEqual(self.api.get(f"{P}/loans/", {"partner": self.bala["id"]}).json()["rows"], [])

    def test_repayments_reduce_the_oldest_loan_first(self):
        a = self.asha
        self.tx(a, "loan_from_partner", "10000", date="2026-04-01", rate="10")
        self.tx(a, "loan_from_partner", "10000", date="2026-04-01", rate="20")
        self.tx(a, "loan_repayment", "10000", date="2026-04-01")
        row = self.api.get(f"{P}/loans/", {"as_of": "2026-04-30"}).json()["rows"][0]
        # only the 20% loan remains: 10,000 x 20% x 29 / 365 = 158.90
        self.assertEqual(D(row["interest_estimate"]), D("158.90"))

    def test_loan_rate_is_validated(self):
        r = self.api.post(f"{P}/transactions/", {"partner": self.asha["id"], "kind": "loan_from_partner", "amount": "1", "date": AUG, "rate": "101"})
        self.assertEqual(r.status_code, 400)


class DashboardTests(Base):
    def test_dashboard_numbers(self):
        self.ratio([(self.asha, "50"), (self.bala, "50")])
        self.tx(self.asha, "capital_introduced", "600000", date="2026-04-02")
        self.tx(self.bala, "capital_introduced", "400000", date="2026-04-02")
        self.tx(self.asha, "drawing", "20000", date="2026-05-10")
        self.tx(self.bala, "drawing", "30000", date="2026-05-20")
        self.tx(self.asha, "drawing", "10000", date="2026-07-01")
        self.tx(self.bala, "loan_from_partner", "70000", date="2026-06-01")
        self.tx(self.asha, "drawing", "150000", date="2026-08-01")  # waiting: not counted
        self.sales(1000, "2026-05-05")
        al = self.api.post(f"{P}/allocations/", {"period_start": "2026-04-01", "period_end": "2026-06-30"}).json()
        self.api.post(f"{P}/allocations/{al['id']}/post/", {})
        d = services.dashboard(self.company, datetime.date(2026, 11, 12))
        self.assertEqual((D(d["total_capital"]), d["partner_count"], D(d["loans_outstanding"])), (D("1000000"), 3, D("70000")))
        self.assertEqual(D(d["drawings_this_fy"]), D("60000"))
        self.assertEqual({m["label"]: D(m["amount"]) for m in d["drawings_by_month"] if D(m["amount"])}, {"May": D("50000"), "Jul": D("10000")})
        self.assertEqual(len(d["drawings_by_month"]), 12)
        self.assertEqual(D(d["profit_share_ytd"]), D("1000"))
        self.assertEqual((d["pending_approvals"], D(d["pending_amount"])), (1, D("150000")))
        cap = {r["name"]: D(r["capital"]) for r in d["capital_by_partner"]}
        self.assertEqual((cap["Asha Rao"], cap["Bala Menon"]), (D("600000"), D("400000")))
        comp = {r["name"]: (D(r["contribution"]), D(r["profit_share"])) for r in d["comparison"]}
        self.assertEqual(comp["Asha Rao"], (D("60.00"), D("50")))
        self.assertEqual(comp["Bala Menon"], (D("40.00"), D("50")))
        self.assertTrue(d["books_agree"]["agree"])

    def test_dashboard_endpoint(self):
        r = self.api.get(f"{P}/dashboard/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(D(r.json()["total_capital"]), D("0"))


class PermissionTests(Base):
    def setUp(self):
        super().setUp()
        self.me_user = make_user("ashauser", Role.EMPLOYEE)  # an ordinary employee who is also a partner
        self.api_me = client_for(self.me_user)
        self.assertEqual(self.api.patch(f"{P}/partners/{self.asha['id']}/", {"user": str(self.me_user.pk)}).status_code, 200)
        self.t_asha = self.tx(self.asha, "capital_introduced", "1000")
        self.t_bala = self.tx(self.bala, "capital_introduced", "2000")
        self.hr = client_for(make_user("hrm", Role.HR_MANAGER))
        self.pm = client_for(make_user("pmgr", Role.PROJECT_MANAGER))

    def test_view_permission_matrix(self):
        views = ("dashboard/", "partners/", f"partners/{self.asha['id']}/", f"partners/{self.asha['id']}/statement/", "transactions/",
                 "loans/", "profit-shares/", "allocations/", "history/", "agreements/", "lookups/", "settings/")
        allowed = {"admin": self.api, "managing partner": self.api_mp, "partner": self.api_pr, "finance": self.api_finance}
        denied = {"employee": self.api_staff, "hr": self.hr, "project manager": self.pm, "anonymous": self.anon}
        for path in views:
            for who, c in allowed.items():
                self.assertEqual(c.get(f"{P}/{path}").status_code, 200, (who, path))
            for who, c in denied.items():
                self.assertIn(c.get(f"{P}/{path}").status_code, (401, 403), (who, path))

    def test_manage_permission_matrix(self):
        body = {"partner": self.asha["id"], "kind": "drawing", "amount": "10", "date": AUG}
        for who, c, ok in (("admin", self.api, True), ("managing partner", self.api_mp, True), ("partner", self.api_pr, False),
                           ("finance", self.api_finance, False), ("employee", self.api_staff, False), ("hr", self.hr, False),
                           ("anonymous", self.anon, False)):
            code = c.post(f"{P}/transactions/", body).status_code
            self.assertEqual(code == 201, ok, (who, code))
            if not ok:
                self.assertIn(code, (401, 403))
            self.assertEqual(c.patch(f"{P}/settings/", {"loan_interest_rate": "9"}).status_code == 200, ok, who)
            self.assertEqual(c.post(f"{P}/profit-shares/", {"effective_from": "2030-01-01", "shares": []}).status_code in (400, 201), ok, who)
            self.assertEqual(c.post(f"{P}/allocations/", {"period_start": "2026-04-01", "period_end": "2026-06-30"}).status_code in (400, 201), ok, who)
            self.assertEqual(c.patch(f"{P}/partners/{self.asha['id']}/", {"notes": "n"}).status_code == 200, ok, who)

    def test_a_partner_user_sees_only_their_own_account(self):
        c = self.api_me
        rows = c.get(f"{P}/partners/").json()["results"]
        self.assertEqual([r["name"] for r in rows], ["Asha Rao"])
        self.assertEqual(c.get(f"{P}/partners/{self.asha['id']}/").status_code, 200)
        self.assertEqual(c.get(f"{P}/partners/{self.bala['id']}/").status_code, 403)
        self.assertEqual(c.get(f"{P}/partners/{self.asha['id']}/statement/").status_code, 200)
        self.assertEqual(c.get(f"{P}/partners/{self.bala['id']}/statement/").status_code, 403)
        self.assertEqual(c.post(f"{P}/partners/{self.bala['id']}/statement/pdf/", {}).status_code, 403)
        tx = c.get(f"{P}/transactions/").json()["results"]
        self.assertEqual({t["partner"]["name"] for t in tx}, {"Asha Rao"})
        self.assertEqual(c.get(f"{P}/transactions/", {"partner": self.bala["id"]}).json()["results"], [])
        self.assertEqual(c.get(f"{P}/transactions/{self.t_asha['id']}/").status_code, 200)
        self.assertEqual(c.get(f"{P}/transactions/{self.t_bala['id']}/").status_code, 403)
        self.assertEqual(c.get(f"{P}/loans/").status_code, 200)
        self.assertEqual(c.get(f"{P}/loans/", {"partner": self.bala["id"]}).status_code, 200)  # ignored: still their own
        self.assertEqual(c.get(f"{P}/history/").status_code, 200)
        me = c.get(f"{P}/me/")
        self.assertEqual((me.status_code, me.json()["name"], D(me.json()["capital"])), (200, "Asha Rao", D("1000")))
        # but nothing company-wide and nothing that changes data
        for path in ("dashboard/", "profit-shares/", "allocations/", "agreements/", "lookups/", "settings/"):
            self.assertEqual(c.get(f"{P}/{path}").status_code, 403, path)
        self.assertEqual(c.post(f"{P}/transactions/", {"partner": self.asha["id"], "kind": "drawing", "amount": "1", "date": AUG}).status_code, 403)
        self.assertEqual(c.post(f"{P}/transactions/{self.t_asha['id']}/void/", {"reason": "x"}).status_code, 403)

    def test_a_partner_linked_by_official_email_also_sees_their_own_account(self):
        self.api.patch(f"{API}/partners/{self.bala['id']}/", {"official_email": "bala@example.com"})
        user = make_user("bala", Role.EMPLOYEE)  # email bala@example.com
        c = client_for(user)
        self.assertEqual([r["name"] for r in c.get(f"{P}/partners/").json()["results"]], ["Bala Menon"])

    def test_a_user_who_is_not_a_partner_has_no_account(self):
        self.assertEqual(self.api_staff.get(f"{P}/me/").status_code, 404)
        self.assertEqual(self.api_staff.get(f"{P}/partners/{self.asha['id']}/").status_code, 403)

    def test_linking_a_user_rules(self):
        r = self.api.patch(f"{P}/partners/{self.bala['id']}/", {"user": str(self.me_user.pk)})
        self.assertEqual(r.status_code, 400)  # already linked to another partner
        self.assertEqual(self.api.patch(f"{P}/partners/{self.bala['id']}/", {"user": "not-a-user"}).status_code, 400)
        self.assertEqual(self.api.patch(f"{P}/partners/{self.asha['id']}/", {"user": ""}).status_code, 200)
        self.assertEqual(self.api_me.get(f"{P}/me/").status_code, 404)

    def test_partner_account_settings(self):
        r = self.api.patch(f"{P}/partners/{self.bala['id']}/", {"agreed_capital": "250000", "notes": "Agreed in LLP deed"})
        self.assertEqual((D(r.json()["agreed_capital"]), r.json()["notes"]), (D("250000"), "Agreed in LLP deed"))
        self.assertEqual(self.api.patch(f"{P}/partners/{self.bala['id']}/", {"agreed_capital": "-1"}).status_code, 400)
        self.assertEqual(self.api.patch(f"{P}/partners/{self.bala['id']}/", {"agreed_capital": "0"}).status_code, 200)

    def test_settings_validation(self):
        self.assertEqual(self.api.patch(f"{P}/settings/", {"withdrawal_approval_threshold": "-5"}).status_code, 400)
        self.assertEqual(self.api.patch(f"{P}/settings/", {"interest_on_capital_rate": "150"}).status_code, 400)
        r = self.api.patch(f"{P}/settings/", {"interest_on_capital_rate": "9.5", "loan_interest_rate": "12"})
        self.assertEqual((D(r.json()["interest_on_capital_rate"]), D(r.json()["loan_interest_rate"])), (D("9.5"), D("12")))


class NotFoundTests(Base):
    def test_unknown_ids_are_404_never_500(self):
        zero = "00000000-0000-0000-0000-000000000000"
        for bad in (zero, "not-a-uuid", "123"):
            for method, path in (("get", f"partners/{bad}/"), ("patch", f"partners/{bad}/"), ("get", f"partners/{bad}/statement/"),
                                 ("post", f"partners/{bad}/statement/pdf/"), ("get", f"transactions/{bad}/"), ("patch", f"transactions/{bad}/"),
                                 ("post", f"transactions/{bad}/approve/"), ("post", f"transactions/{bad}/reject/"), ("post", f"transactions/{bad}/void/"),
                                 ("delete", f"profit-shares/{bad}/"), ("get", f"allocations/{bad}/"), ("patch", f"allocations/{bad}/"),
                                 ("delete", f"allocations/{bad}/"), ("post", f"allocations/{bad}/post/"), ("post", f"allocations/{bad}/reverse/")):
                self.assertEqual(getattr(self.api, method)(f"{P}/{path}", {}).status_code, 404, (method, path))
        self.assertEqual(self.api.get(f"{P}/loans/", {"partner": zero}).status_code, 404)
        self.assertEqual(self.api.get(f"{P}/history/", {"partner": "junk"}).status_code, 404)
        self.assertEqual(self.api.get(f"{P}/interest-preview/", {"partner": zero, "from": "2026-04-01", "to": "2026-04-30"}).status_code, 404)


class AuditTests(Base):
    def events(self, entity):
        return list(CompanyProfileAuditEvent.objects.filter(entity_type=entity).order_by("occurred_at"))

    def test_transaction_lifecycle_is_audited(self):
        t = self.tx(self.asha, "drawing", "150000")
        self.api.post(f"{P}/transactions/{t['id']}/approve/")
        self.api.post(f"{P}/transactions/{t['id']}/void/", {"reason": "Entered twice"})
        ev = self.events("partner_transaction")
        self.assertEqual([e.action for e in ev], ["created", "verified", "deactivated"])
        self.assertEqual(ev[-1].reason, "Entered twice")
        self.assertEqual(ev[0].new_values["amount"], "150000.00")
        self.assertEqual(ev[0].category, "partners")
        self.assertEqual(ev[0].actor_label, "admin")

    def test_ratio_allocation_settings_and_account_changes_are_audited(self):
        s = self.ratio_even()
        self.sales(100)
        a = self.api.post(f"{P}/allocations/", {"period_start": "2026-04-01", "period_end": "2026-09-30"}).json()
        self.api.post(f"{P}/allocations/{a['id']}/post/", {})
        self.api.post(f"{P}/allocations/{a['id']}/reverse/", {"reason": "redo"})
        self.api.patch(f"{P}/settings/", {"withdrawal_approval_threshold": "1"})
        self.api.patch(f"{P}/partners/{self.asha['id']}/", {"agreed_capital": "5"})
        s2 = self.ratio([(self.asha, "100")], "2030-01-01")
        self.api.delete(f"{P}/profit-shares/{s2['id']}/")
        self.assertEqual([e.action for e in self.events("partner_allocation")], ["created", "issued", "deactivated"])
        self.assertEqual([e.action for e in self.events("partner_profit_share")], ["created", "created", "deleted"])
        self.assertEqual(len(self.events("partner_settings")), 1)
        self.assertEqual(len(self.events("partner_account")), 1)

    def test_note_edits_are_audited(self):
        t = self.tx(self.asha, "drawing", "10")
        self.api.patch(f"{P}/transactions/{t['id']}/", {"narration": "Fees"})
        self.assertEqual([e.action for e in self.events("partner_transaction")], ["created", "updated"])


class DocumentTests(Base):
    def test_partner_attachments_are_registered_with_partner_permissions(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.company.tests.base import pdf_stub

        def up(client):
            return client.post("/api/v1/attachments/", {"entity_type": "partner", "entity_id": self.asha["id"], "title": "DPIN letter",
                                                        "expiry_date": "2027-03-31", "file": SimpleUploadedFile("dpin.pdf", pdf_stub(), content_type="application/pdf")},
                               format="multipart")
        self.assertEqual(up(self.api_mp).status_code, 201)
        self.assertEqual(up(self.api_pr).status_code, 403)  # view only
        self.assertEqual(up(self.api_staff).status_code, 403)
        r = self.api_pr.get("/api/v1/attachments/", {"entity_type": "partner", "entity_id": self.asha["id"]})
        self.assertEqual((r.status_code, r.json()[0]["title"], r.json()[0]["expiry_date"]), (200, "DPIN letter", "2027-03-31"))
        self.assertEqual(self.api_staff.get("/api/v1/attachments/", {"entity_type": "partner", "entity_id": self.asha["id"]}).status_code, 403)

    def test_agreement_versions_come_from_the_company_module_read_only(self):
        r = self.upload_document(document_type="llp_agreement", title="LLP agreement", access_classification="internal")
        self.assertEqual(r.status_code, 201, r.content)
        r2 = self.upload_document(document_type="llp_agreement", title="LLP agreement (restricted)", access_classification="restricted")
        self.assertEqual(r2.status_code, 201, r2.content)
        self.upload_document(document_type="gst_certificate", title="GST", access_classification="internal")
        rows = self.api.get(f"{P}/agreements/").json()
        self.assertEqual(sorted(x["title"] for x in rows), ["LLP agreement", "LLP agreement (restricted)"])
        seen = {x["title"] for x in self.api_pr.get(f"{P}/agreements/").json()}
        self.assertNotIn("LLP agreement (restricted)", seen)
        self.assertEqual(self.api.post(f"{P}/agreements/", {}).status_code, 405)  # read only


class LookupTests(Base):
    def test_lookups_for_the_forms(self):
        r = self.api.get(f"{P}/lookups/").json()
        self.assertEqual({k["value"] for k in r["kinds"]}, set(PartnerTransaction.Kind.values))
        self.assertEqual(len(r["partners"]), 3)
        self.assertTrue(any(a["subtype"] == "bank" for a in r["money_accounts"]))
        self.assertEqual(D(r["settings"]["withdrawal_approval_threshold"]), D("100000"))
        self.assertTrue(r["can_manage"])
        self.assertFalse(self.api_pr.get(f"{P}/lookups/").json()["can_manage"])

    def test_list_filters_and_search(self):
        self.tx(self.asha, "capital_introduced", "1000", reference="UTR-ALPHA")
        self.tx(self.bala, "drawing", "50", date="2026-09-01")
        self.tx(self.bala, "drawing", "150000", date="2026-09-02")
        g = lambda **p: self.api.get(f"{P}/transactions/", p).json()
        self.assertEqual(g()["count"], 3)
        self.assertEqual(g(kind="drawing")["count"], 2)
        self.assertEqual(g(status="pending_approval")["count"], 1)
        self.assertEqual(g(partner=self.asha["id"])["count"], 1)
        self.assertEqual(g(q="alpha")["count"], 1)
        self.assertEqual(g(q="bala")["count"], 2)
        self.assertEqual(g(**{"from": "2026-09-01", "to": "2026-09-01"})["count"], 1)
        self.assertEqual(D(g()["posted_total"]), D("1050"))
        self.assertEqual(self.api.get(f"{P}/transactions/", {"from": "x"}).status_code, 400)
