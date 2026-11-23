"""Payroll arithmetic checked against hand-worked figures (no database)."""
from decimal import Decimal as D

from django.test import SimpleTestCase

from apps.hr.models import DEFAULT_PT_SLABS, HRSettings
from apps.hr.services import calc

S = HRSettings(pf_rate=D("12"), pf_wage_ceiling=D("15000"), esi_ceiling=D("21000"), esi_employee_rate=D("0.75"),
               esi_employer_rate=D("3.25"), pt_slabs=DEFAULT_PT_SLABS)


def full(basic, hra=0, special=0, other=0, **flags):
    return {"basic": D(basic), "hra": D(hra), "special_allowance": D(special), "other_allowance": D(other),
            "pf_enabled": True, "pf_on_full_basic": False, "esi_enabled": True, "pt_enabled": True, **flags}


def run(f, lop=0, basis=30, **kw):
    return calc.compute(f, basis_days=basis, lop_days=D(lop), settings=S, **kw)


class SalaryMath(SimpleTestCase):
    def test_full_month_above_esi_ceiling(self):
        r = run(full(20000, 8000, 12000))
        self.assertEqual(r["gross"], D("40000.00"))
        self.assertEqual(r["pf_employee"], D("1800.00"))     # 12% of the 15,000 wage ceiling, not of 20,000
        self.assertEqual(r["pf_employer"], D("1800.00"))
        self.assertEqual((r["esi_employee"], r["esi_employer"]), (D("0.00"), D("0.00")))  # gross 40,000 > 21,000
        self.assertEqual(r["professional_tax"], D("52.50"))   # 30,001-45,000 slab
        self.assertEqual(r["total_deductions"], D("1852.50"))
        self.assertEqual(r["net_pay"], D("38147.50"))
        self.assertEqual(r["employer_cost"], D("41800.00"))

    def test_loss_of_pay_prorates_each_component(self):
        r = run(full(20000, 8000, 12000), lop=2)
        self.assertEqual(r["basic"], D("18666.67"))   # 20000 * 28 / 30
        self.assertEqual(r["hra"], D("7466.67"))
        self.assertEqual(r["special_allowance"], D("11200.00"))
        self.assertEqual(r["gross"], D("37333.34"))
        self.assertEqual(r["pf_employee"], D("1800.00"))  # basic still above the ceiling
        self.assertEqual(r["paid_days"], D("28"))
        self.assertEqual(r["net_pay"], D("35480.84"))     # 37333.34 - 1800 - 52.50

    def test_esi_applies_within_the_ceiling(self):
        r = run(full(10000, 4000, 3000, 1000))            # gross 18,000
        self.assertEqual(r["gross"], D("18000.00"))
        self.assertEqual(r["pf_employee"], D("1200.00"))  # 12% of 10,000 (below the ceiling)
        self.assertEqual(r["esi_employee"], D("135.00"))  # 0.75% of 18,000
        self.assertEqual(r["esi_employer"], D("585.00"))  # 3.25% of 18,000
        self.assertEqual(r["professional_tax"], D("0.00"))  # up to 21,000 pays nothing
        self.assertEqual(r["net_pay"], D("16665.00"))
        self.assertEqual(r["employer_cost"], D("19785.00"))

    def test_esi_eligibility_uses_the_contract_gross_not_the_month_after_loss_of_pay(self):
        r = run(full(14000, 6000), lop=3)                 # contract 20,000; earned 18,000
        self.assertEqual(r["gross"], D("18000.00"))
        self.assertEqual(r["esi_employee"], D("135.00"))
        over = run(full(14001, 7000), lop=3)              # contract 21,001: not eligible even though the month is lower
        self.assertEqual(over["esi_employee"], D("0.00"))
        edge = run(full(14000, 7000))                     # exactly 21,000 is eligible
        self.assertEqual(edge["esi_employee"], D("157.50"))

    def test_bonus_adds_to_gross_and_esi_base(self):
        r = run(full(10000, 4000, 3000, 1000), bonus=D("5000"))
        self.assertEqual(r["gross"], D("23000.00"))
        self.assertEqual(r["esi_employee"], D("172.50"))
        self.assertEqual(r["esi_employer"], D("747.50"))
        self.assertEqual(r["professional_tax"], D("22.50"))  # gross now in the 21,001-30,000 slab

    def test_pf_on_full_basic_ignores_the_ceiling_and_pf_can_be_off(self):
        self.assertEqual(run(full(30000, pf_on_full_basic=True))["pf_employee"], D("3600.00"))
        r = run(full(30000, pf_enabled=False))
        self.assertEqual((r["pf_employee"], r["pf_employer"]), (D("0.00"), D("0.00")))
        self.assertEqual(r["professional_tax"], D("22.50"))  # 30,000 is the top of the 21,001-30,000 slab

    def test_other_deductions_reduce_net_pay(self):
        r = run(full(20000, 8000, 12000), tds=D("1000"), loan_recovery=D("500"), other_deductions=D("200"))
        self.assertEqual(r["total_deductions"], D("3552.50"))  # 1800 + 52.50 + 1000 + 500 + 200
        self.assertEqual(r["net_pay"], D("36447.50"))

    def test_professional_tax_slab_edges(self):
        pt = lambda g: calc.professional_tax(D(g), DEFAULT_PT_SLABS)  # noqa: E731
        self.assertEqual(pt("21000"), D("0.00"))
        self.assertEqual(pt("21000.01"), D("22.50"))
        self.assertEqual(pt("75000"), D("171.25"))
        self.assertEqual(pt("75000.01"), D("208.33"))
        self.assertEqual(calc.professional_tax(D("5000"), []), D("0.00"))

    def test_switching_pt_off_and_working_day_basis(self):
        self.assertEqual(run(full(20000, 8000, 12000, pt_enabled=False))["professional_tax"], D("0.00"))
        r = run(full(26000), basis=26, lop=1)             # 26 working days, one lost: 26000 * 25 / 26 = 25000
        self.assertEqual(r["basic"], D("25000.00"))
