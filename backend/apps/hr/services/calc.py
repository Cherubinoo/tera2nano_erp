"""Payroll arithmetic. Pure functions: no database, so every rule can be checked by hand.

Rules (all amounts are rounded half up to the paisa at each step shown):
- Each component is paid in proportion to the days worked: `component * (basis_days - lop_days) / basis_days`.
- Gross = the earned components + any bonus for the month.
- Provident fund: `pf_rate`% of the earned basic, but only on the first `pf_wage_ceiling` of it unless the employee is on
  full-basic PF. The employer pays the same amount.
- ESI applies when the employee's *contractual* monthly gross (the full structure, before loss of pay) is within
  `esi_ceiling`; it is then charged on the month's gross (employee `esi_employee_rate`%, employer `esi_employer_rate`%).
- Professional tax comes from the configured slabs, on the month's gross.
- Net pay = gross - PF - ESI - professional tax - TDS - loan recovery - other deductions.
"""
from decimal import Decimal

from .common import ZERO, money

COMPONENTS = ("basic", "hra", "special_allowance", "other_allowance")


def professional_tax(gross, slabs):
    """The slab amount for a monthly gross. Slabs are `[{"up_to": "21000", "amount": "0"}, ..., {"up_to": None, ...}]`."""
    for slab in slabs or []:
        limit = slab.get("up_to")
        if limit in (None, "") or gross <= Decimal(str(limit)):
            return money(slab.get("amount") or 0)
    return ZERO


def compute(full, *, basis_days, lop_days, settings, bonus=ZERO, tds=ZERO, loan_recovery=ZERO, other_deductions=ZERO):
    """`full`: the monthly structure: basic, hra, special_allowance, other_allowance (Decimal) and the flags pf_enabled,
    pf_on_full_basic, esi_enabled, pt_enabled. Returns every worked-out amount of a payslip."""
    basis_days = Decimal(basis_days)
    lop_days = Decimal(lop_days)
    paid_days = max(basis_days - lop_days, Decimal(0))
    earned = {c: money(Decimal(full[c]) * paid_days / basis_days) if basis_days else ZERO for c in COMPONENTS}
    gross = money(sum(earned.values()) + Decimal(bonus))

    pf_employee = pf_employer = ZERO
    if full.get("pf_enabled"):
        base = earned["basic"] if full.get("pf_on_full_basic") else min(earned["basic"], Decimal(settings.pf_wage_ceiling))
        pf_employee = money(base * Decimal(settings.pf_rate) / 100)
        pf_employer = pf_employee

    esi_employee = esi_employer = ZERO
    contractual_gross = sum(Decimal(full[c]) for c in COMPONENTS)
    if full.get("esi_enabled") and contractual_gross <= Decimal(settings.esi_ceiling):
        esi_employee = money(gross * Decimal(settings.esi_employee_rate) / 100)
        esi_employer = money(gross * Decimal(settings.esi_employer_rate) / 100)

    pt = professional_tax(gross, settings.pt_slabs) if full.get("pt_enabled") else ZERO

    deductions = money(pf_employee + esi_employee + pt + Decimal(tds) + Decimal(loan_recovery) + Decimal(other_deductions))
    return {
        **earned, "gross": gross, "paid_days": paid_days,
        "pf_employee": pf_employee, "pf_employer": pf_employer, "esi_employee": esi_employee, "esi_employer": esi_employer,
        "professional_tax": pt, "total_deductions": deductions, "net_pay": money(gross - deductions),
        "employer_cost": money(gross + pf_employer + esi_employer),
    }
