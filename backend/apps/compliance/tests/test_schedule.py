"""Due-date rules, status computation and penalty maths. No database: a fixed clock and hand-computed dates."""
import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.compliance import constants as C
from apps.compliance.models import Contract
from apps.compliance.services import legal, penalty, schedule

D = datetime.date


def rule_of(code):
    return next(c["due_rule"] for c in C.CATALOGUE if c["code"] == code)


class FinancialYearTests(SimpleTestCase):
    def test_label_parse_and_bounds(self):
        self.assertEqual(schedule.fy_label(2026), "2026-27")
        self.assertEqual(schedule.fy_label(2099), "2099-00")
        self.assertEqual(schedule.parse_fy("2026-27"), 2026)
        self.assertEqual(schedule.fy_bounds("2027-28"), (D(2027, 4, 1), D(2028, 3, 31)))
        self.assertEqual(schedule.fy_for_date(D(2027, 3, 31)), "2026-27")
        self.assertEqual(schedule.fy_for_date(D(2027, 4, 1)), "2027-28")

    def test_bad_labels_are_refused(self):
        for bad in ("2026-29", "26-27", "2026", "", None, "2026/27", "abcd-ef"):
            with self.assertRaises(ValidationError, msg=bad):
                schedule.parse_fy(bad)


class DueDateTests(SimpleTestCase):
    def dues(self, code, fy="2026-27"):
        return [(d.due_date, d.period_label) for d in schedule.due_dates(rule_of(code), fy)]

    def test_llp_annual_dates_fall_in_the_year_after_the_period(self):
        self.assertEqual(self.dues("LLP-FORM-11"), [(D(2026, 5, 30), "FY 2025-26")])
        self.assertEqual(self.dues("LLP-FORM-8"), [(D(2026, 10, 30), "FY 2025-26")])
        self.assertEqual(self.dues("DIR-3-KYC"), [(D(2026, 9, 30), "KYC for FY 2025-26")])
        self.assertEqual(self.dues("GSTR-9"), [(D(2026, 12, 31), "FY 2025-26")])

    def test_income_tax_return_names_the_assessment_year(self):
        self.assertEqual(self.dues("ITR-5"), [(D(2026, 7, 31), "FY 2025-26 (AY 2026-27)")])
        self.assertEqual(self.dues("ITR-5-AUDIT"), [(D(2026, 10, 31), "FY 2025-26 (AY 2026-27)")])

    def test_advance_tax_rolls_into_the_next_calendar_year_for_march(self):
        got = self.dues("ADV-TAX")
        self.assertEqual([d for d, _ in got], [D(2026, 6, 15), D(2026, 9, 15), D(2026, 12, 15), D(2027, 3, 15)])
        self.assertTrue(got[3][1].startswith("4th instalment (100%) FY 2026-27"))

    def test_tds_returns_start_with_last_years_fourth_quarter(self):
        got = self.dues("TDS-RETURN")
        self.assertEqual([d for d, _ in got], [D(2026, 5, 31), D(2026, 7, 31), D(2026, 10, 31), D(2027, 1, 31)])
        self.assertEqual(got[0][1], "Q4 FY 2025-26 (Jan-Mar)")
        self.assertEqual(got[1][1], "Q1 FY 2026-27 (Apr-Jun)")

    def test_monthly_returns_cover_march_of_last_year_to_february(self):
        got = schedule.due_dates(rule_of("GSTR-3B"), "2026-27")
        self.assertEqual(len(got), 12)
        self.assertEqual((got[0].due_date, got[0].period_label), (D(2026, 4, 20), "March 2026"))
        self.assertEqual((got[-1].due_date, got[-1].period_label), (D(2027, 3, 20), "February 2027"))
        january = next(d for d in got if d.due_date == D(2027, 1, 20))
        self.assertEqual(january.period_label, "December 2026")  # year rollover
        self.assertEqual(len({d.period_key for d in got}), 12)

    def test_last_day_and_day_31_follow_the_calendar_including_leap_years(self):
        rule = {"type": "monthly", "day": "last", "lag": 0}
        by_month = {d.due_date.month: d.due_date for d in schedule.due_dates(rule, "2027-28")}
        self.assertEqual(by_month[2], D(2028, 2, 29))  # 2028 is a leap year
        self.assertEqual(by_month[4], D(2027, 4, 30))
        plain = {d.due_date.month: d.due_date for d in schedule.due_dates(rule, "2026-27")}
        self.assertEqual(plain[2], D(2027, 2, 28))
        thirty_first = {d.due_date.month: d.due_date for d in schedule.due_dates({"type": "monthly", "day": 31, "lag": 0}, "2027-28")}
        self.assertEqual((thirty_first[2], thirty_first[4], thirty_first[12]), (D(2028, 2, 29), D(2027, 4, 30), D(2027, 12, 31)))

    def test_fixed_29_february_is_clamped_in_a_common_year(self):
        rule = {"type": "dates", "dates": [{"month": 2, "day": 29, "fy": "cur", "label": "X"}]}
        self.assertEqual(schedule.due_dates(rule, "2026-27")[0].due_date, D(2027, 2, 28))
        self.assertEqual(schedule.due_dates(rule, "2027-28")[0].due_date, D(2028, 2, 29))

    def test_event_rules_generate_nothing_and_give_a_due_date_from_the_event(self):
        self.assertEqual(schedule.due_dates(rule_of("LLP-FORM-4"), "2026-27"), [])
        self.assertEqual(schedule.event_due(rule_of("LLP-FORM-4"), D(2028, 2, 10)), D(2028, 3, 11))  # 30 days across a leap February
        self.assertEqual(schedule.event_due({"type": "event", "days": 30}, D(2026, 12, 15)), D(2027, 1, 14))

    def test_add_months_clamps_to_month_end(self):
        self.assertEqual(schedule.add_months(D(2028, 1, 31), 1), D(2028, 2, 29))
        self.assertEqual(schedule.add_months(D(2026, 11, 30), 3), D(2027, 2, 28))
        self.assertEqual(schedule.add_months(D(2026, 12, 15), 12), D(2027, 12, 15))

    def test_rule_validation(self):
        self.assertEqual(schedule.validate_rule({"type": "monthly", "day": "last"}), {"type": "monthly", "day": "last", "lag": 1})
        for bad in ({}, {"type": "x"}, {"type": "monthly", "day": 32}, {"type": "monthly", "day": 5, "lag": 13},
                    {"type": "dates", "dates": []}, {"type": "dates", "dates": [{"month": 13, "day": 1}]},
                    {"type": "event", "days": -1}, {"type": "dates", "dates": [{"month": 1, "day": 1, "fy": "next"}]}, "text"):
            with self.assertRaises(ValidationError, msg=bad):
                schedule.validate_rule(bad)


class StatusTests(SimpleTestCase):
    TODAY = D(2026, 11, 12)

    def key(self, status, due):
        return schedule.status_key(status, due, self.TODAY)

    def test_boundaries_with_a_fixed_clock(self):
        self.assertEqual(self.key("open", D(2026, 11, 11)), "overdue")
        self.assertEqual(self.key("open", D(2026, 11, 12)), "due_soon")  # due today is not overdue yet
        self.assertEqual(self.key("open", D(2026, 11, 27)), "due_soon")  # exactly 15 days
        self.assertEqual(self.key("open", D(2026, 11, 28)), "upcoming")  # 16 days
        self.assertEqual(self.key("filed", D(2020, 1, 1)), "filed")
        self.assertEqual(self.key("not_applicable", D(2020, 1, 1)), "not_applicable")

    def test_days_late(self):
        self.assertEqual(schedule.days_late(D(2026, 5, 30), D(2026, 5, 30)), 0)
        self.assertEqual(schedule.days_late(D(2026, 5, 30), D(2026, 5, 20)), 0)
        self.assertEqual(schedule.days_late(D(2026, 5, 30), D(2026, 6, 9)), 10)


class PenaltyTests(SimpleTestCase):
    DUE = D(2026, 5, 30)

    def test_llp_is_100_a_day_with_no_cap(self):
        self.assertEqual(penalty.estimate("llp_per_day", self.DUE, D(2026, 5, 30))["amount"], Decimal("0.00"))
        self.assertEqual(penalty.estimate("llp_per_day", self.DUE, D(2026, 6, 9))["amount"], Decimal("1000.00"))
        self.assertEqual(penalty.estimate("llp_per_day", self.DUE, D(2027, 5, 30))["amount"], Decimal("36500.00"))
        r = penalty.estimate("llp_per_day", self.DUE, D(2026, 6, 9))
        self.assertEqual((r["days_late"], r["estimate"]), (10, True))
        self.assertIn("no cap", r["formula"])

    def test_constants_are_data(self):
        custom = {"llp_per_day": {**C.PENALTY_RULES["llp_per_day"], "per_day": Decimal("50"), "cap": Decimal("500")}}
        self.assertEqual(penalty.estimate("llp_per_day", self.DUE, D(2026, 6, 9), rules=custom)["amount"], Decimal("500.00"))
        self.assertEqual(penalty.estimate("llp_per_day", self.DUE, D(2026, 6, 4), rules=custom)["amount"], Decimal("250.00"))

    def test_income_tax_234f_tiers(self):
        due = D(2026, 7, 31)
        self.assertEqual(penalty.estimate("itr_234f", due, D(2026, 7, 31))["amount"], Decimal("0.00"))
        self.assertEqual(penalty.estimate("itr_234f", due, D(2026, 8, 15), total_income="800000")["amount"], Decimal("5000.00"))
        self.assertEqual(penalty.estimate("itr_234f", due, D(2026, 8, 15), total_income="500000")["amount"], Decimal("1000.00"))
        self.assertEqual(penalty.estimate("itr_234f", due, D(2026, 12, 31), total_income="800000")["amount"], Decimal("5000.00"))
        self.assertEqual(penalty.estimate("itr_234f", due, D(2027, 1, 1), total_income="800000")["amount"], Decimal("10000.00"))
        self.assertEqual(penalty.estimate("itr_234f", due, D(2027, 1, 1), total_income="100000")["amount"], Decimal("1000.00"))

    def test_gst_late_fee_caps(self):
        due = D(2026, 5, 20)
        self.assertEqual(penalty.estimate("gst_late_fee", due, D(2026, 5, 30))["amount"], Decimal("500.00"))
        self.assertEqual(penalty.estimate("gst_late_fee", due, D(2027, 5, 20))["amount"], Decimal("10000.00"))
        self.assertEqual(penalty.estimate("gst_late_fee", due, D(2026, 5, 30), nil_return=True)["amount"], Decimal("200.00"))
        self.assertEqual(penalty.estimate("gst_late_fee", due, D(2027, 5, 20), nil_return=True)["amount"], Decimal("500.00"))

    def test_tds_234e_is_limited_to_the_tds(self):
        due = D(2026, 7, 31)
        self.assertEqual(penalty.estimate("tds_234e", due, D(2026, 8, 10))["amount"], Decimal("2000.00"))
        self.assertEqual(penalty.estimate("tds_234e", due, D(2026, 8, 10), tax_amount="1500")["amount"], Decimal("1500.00"))

    def test_unknown_rule_and_bad_numbers(self):
        self.assertEqual(penalty.estimate("none", self.DUE, D(2026, 8, 1))["amount"], Decimal("0.00"))
        with self.assertRaises(ValidationError):
            penalty.estimate("itr_234f", D(2026, 7, 31), D(2026, 8, 5), total_income="abc")
        with self.assertRaises(ValidationError):
            penalty.estimate("tds_234e", D(2026, 7, 31), D(2026, 8, 5), tax_amount="-5")


def contract(**kw):
    base = dict(status="active", effective_date=D(2025, 1, 1), expiry_date=D(2026, 6, 30), auto_renew=False, renewal_term_months=12,
                renewal_notice_days=30)
    base.update(kw)
    return Contract(**base)


class ContractRuleTests(SimpleTestCase):
    def test_plain_contract_states(self):
        c = contract(expiry_date=D(2026, 12, 31))
        self.assertEqual(legal.contract_state(c, D(2026, 11, 12)), "active")        # 49 days
        self.assertEqual(legal.contract_state(c, D(2026, 12, 1)), "expiring")       # 30 days: inside the notice window
        self.assertEqual(legal.contract_state(c, D(2026, 12, 31)), "expiring")      # the last day still counts
        self.assertEqual(legal.contract_state(c, D(2027, 1, 1)), "expired")
        self.assertEqual(legal.contract_state(contract(expiry_date=None), D(2030, 1, 1)), "active")
        self.assertEqual(legal.contract_state(contract(status="draft"), D(2030, 1, 1)), "draft")
        self.assertEqual(legal.contract_state(contract(status="terminated"), D(2020, 1, 1)), "terminated")

    def test_auto_renewal_rolls_forward_by_the_term(self):
        c = contract(expiry_date=D(2026, 6, 30), auto_renew=True, renewal_term_months=12)
        self.assertEqual(legal.effective_expiry(c, D(2026, 11, 12)), D(2027, 6, 30))
        self.assertEqual(legal.effective_expiry(c, D(2026, 6, 30)), D(2026, 6, 30))
        self.assertEqual(legal.effective_expiry(c, D(2028, 7, 1)), D(2029, 6, 30))
        quarterly = contract(expiry_date=D(2026, 6, 30), auto_renew=True, renewal_term_months=3)
        self.assertEqual(legal.effective_expiry(quarterly, D(2026, 11, 12)), D(2026, 12, 30))
        self.assertEqual(legal.contract_state(quarterly, D(2026, 11, 12)), "active")   # 48 days to go
        self.assertEqual(legal.contract_state(quarterly, D(2026, 12, 10)), "expiring")  # 20 days to go

    def test_auto_renew_keeps_the_original_day_through_short_months(self):
        c = contract(expiry_date=D(2026, 1, 31), auto_renew=True, renewal_term_months=1)
        self.assertEqual(legal.effective_expiry(c, D(2026, 3, 1)), D(2026, 3, 31))  # not 28 March: counted from the original date

    def test_expired_without_auto_renew_and_dict_shape(self):
        c = contract(expiry_date=D(2026, 6, 30), title="Lease", counterparty="Landlord", contract_type="lease", reference="X")
        c.owner_id = None
        d = legal.contract_dict(c, D(2026, 11, 12))
        self.assertEqual((d["state"], d["days_left"], d["auto_renewed"]), ("expired", -135, False))
        c.auto_renew = True
        d = legal.contract_dict(c, D(2026, 11, 12))
        self.assertEqual((d["state"], d["current_expiry"], d["auto_renewed"]), ("active", D(2027, 6, 30), True))
        self.assertIsInstance(SimpleNamespace(**{"a": 1}), SimpleNamespace)
