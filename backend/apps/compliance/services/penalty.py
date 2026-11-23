"""Late-filing penalty estimates. Formulas are data in `constants.PENALTY_RULES`; these are ESTIMATES, never a demand."""
import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError

from .. import constants as C
from .schedule import days_late

ZERO = Decimal("0.00")


def _money(v):
    return Decimal(v).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _amount(value, field):
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value))
    except Exception:
        raise ValidationError({field: "Enter a number."})
    if d < 0:
        raise ValidationError({field: "Cannot be negative."})
    return d


def estimate(rule, due_date, filed_date, *, total_income=None, tax_amount=None, nil_return=False, rules=None):
    """{"rule", "days_late", "amount", "formula", "basis", "estimate": True}. `rules` lets a test supply its own constants."""
    rules = rules or C.PENALTY_RULES
    late = days_late(due_date, filed_date)
    out = {"rule": rule, "days_late": late, "amount": ZERO, "formula": "No penalty: filed on or before the due date." if not late else "",
           "basis": "", "estimate": True}
    if rule not in rules:
        out["formula"] = "No automatic estimate for this obligation; see the penalty note."
        return out
    spec = rules[rule]
    out["basis"] = spec["basis"]
    if not late:
        return out
    if rule == "llp_per_day":
        amount = spec["per_day"] * late
        if spec.get("cap") is not None:
            amount = min(amount, spec["cap"])
        out["formula"] = f"{late} days x Rs {spec['per_day']:,.0f} a day" + (f", capped at Rs {spec['cap']:,.0f}" if spec.get("cap") is not None else ", no cap")
    elif rule == "itr_234f":
        income = _amount(total_income, "total_income")
        cutoff = datetime.date(due_date.year, spec["cutoff_month"], spec["cutoff_day"])
        if income is not None and income <= spec["low_income_limit"]:
            amount, why = spec["low_income_fee"], f"total income up to Rs {spec['low_income_limit']:,.0f}"
        elif filed_date > cutoff:
            amount, why = spec["fee_after_cutoff"], f"filed after {cutoff:%d %B %Y}"
        else:
            amount, why = spec["fee"], f"filed on or before {cutoff:%d %B %Y}"
        out["formula"] = f"Fixed late fee Rs {amount:,.0f} ({why})"
    elif rule == "gst_late_fee":
        per_day = spec["per_day_nil"] if nil_return else spec["per_day"]
        cap = spec["cap_nil"] if nil_return else spec["cap"]
        amount = min(per_day * late, cap)
        out["formula"] = f"{late} days x Rs {per_day:,.0f}, capped at Rs {cap:,.0f}" + (" (nil return)" if nil_return else "")
    elif rule == "tds_234e":
        amount = spec["per_day"] * late
        limit = _amount(tax_amount, "tax_amount")
        text = f"{late} days x Rs {spec['per_day']:,.0f}"
        if limit is not None:
            amount = min(amount, limit)
            text += f", limited to the TDS of Rs {limit:,.2f}"
        else:
            text += " (enter the TDS amount to apply the limit)"
        out["formula"] = text
    else:  # pragma: no cover - guarded by the `in rules` check for the shipped rules
        amount = ZERO
    out["amount"] = _money(amount)
    return out


def rules_for_display():
    return [{"key": k, "label": v["label"], "basis": v["basis"]} for k, v in C.PENALTY_RULES.items()]
