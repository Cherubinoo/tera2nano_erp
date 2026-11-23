"""Depreciation rules: the month-by-month schedule of one asset, as pure arithmetic (no database).

THE RULES (April to March financial years; every amount is rounded to the paisa)

* Depreciable amount = cost - salvage, where salvage = cost x salvage %. Depreciation never goes beyond it, so an asset stops
  at its salvage value (nil for a written-down-value asset with a salvage of 0%, which only ever approaches zero).
* Depreciation starts on the put-to-use date (not the purchase date). The month it starts in is charged for the days it is
  used: days from the put-to-use date to the month end (both counted) / days in the month. Every later month counts in full.
  On a disposal the last month is charged the same way, up to and including the disposal date.
* Straight line (Companies Act, Schedule II style): yearly charge = depreciable amount / useful life in years;
  monthly charge = yearly charge / 12 x the part of the month used.
* Written-down value (income-tax block rate): yearly charge = rate % x the book value at the start of the financial year
  (the cost in the year the asset is put to use). The yearly charge is spread over the months of use in that year in
  proportion to the part of each month used (a full year is simply charge / 12 each month).
    - Half-year rule OFF (default): the first year is pro-rata, that is rate % x cost x months used / 12.
    - Half-year rule ON (the income-tax rule): in the year of acquisition the full yearly rate is charged when the asset is used
      for 180 days or more up to 31 March, and half of it when it is used for fewer than 180 days. The charge is still spread
      over the months of use. Later years are the same with the rule on or off.
* Rounding: the running total is rounded each month and the month's charge is the difference, so the months always add up
  exactly to the year's charge and the total never drifts by a paisa.
"""
import calendar
import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
TWELVE = Decimal(12)
HUNDRED = Decimal(100)
SLM, WDV = "slm", "wdv"
HALF_YEAR_DAYS = 180
# A written-down-value asset never reaches its floor exactly; the schedule shows this many financial years at most.
WDV_YEARS = 10
MAX_MONTHS = 1200


def rnd(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Terms:
    cost: Decimal
    method: str
    life_years: Optional[Decimal]
    rate_percent: Optional[Decimal]
    salvage_percent: Decimal
    half_year_rule: bool
    put_to_use: datetime.date

    @property
    def salvage(self):
        return rnd(self.cost * self.salvage_percent / HUNDRED)

    @property
    def depreciable(self):
        return rnd(self.cost - self.salvage)


def terms_of(asset):
    return Terms(cost=asset.cost, method=asset.method, life_years=asset.useful_life_years, rate_percent=asset.rate_percent,
                 salvage_percent=asset.salvage_percent, half_year_rule=asset.half_year_rule, put_to_use=asset.put_to_use_date)


# --------------------------------------------------------------------------- calendar helpers

def fy_start(d):
    return datetime.date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def fy_end(d):
    return datetime.date(fy_start(d).year + 1, 3, 31)


def fy_label(d):
    y = fy_start(d).year
    return f"{y}-{(y + 1) % 100:02d}"


def month_start(d):
    return d.replace(day=1)


def month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def next_month(d):
    return datetime.date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def months_between(first, last):
    """Month starts from the month of `first` to the month of `last`, inclusive."""
    out, m = [], month_start(first)
    while m <= last:
        out.append(m)
        m = next_month(m)
    return out


def used_fraction(put_to_use, ms, stop=None):
    """The part of the month starting `ms` the asset is in use: 0 to 1."""
    me = month_end(ms)
    first = max(put_to_use, ms)
    last = min(stop, me) if stop else me
    if last < first:
        return Decimal(0)
    return Decimal((last - first).days + 1) / Decimal(me.day)


# --------------------------------------------------------------------------- the schedule

def _year_plan(t, ms, cum):
    """For the financial year starting with month `ms`: (yearly charge, weight of a full year). Monthly charge = yearly x f / weight."""
    fs = fy_start(ms)
    months = months_between(max(month_start(t.put_to_use), fs), datetime.date(fs.year + 1, 3, 1))
    weight = sum((used_fraction(t.put_to_use, m) for m in months), Decimal(0))
    if t.method == SLM:
        return t.depreciable / t.life_years, TWELVE
    base = t.cost - cum
    first_year = fy_start(t.put_to_use) == fs
    factor = Decimal(1)
    if first_year:
        if t.half_year_rule:
            days = (fy_end(t.put_to_use) - t.put_to_use).days + 1
            factor = Decimal(1) if days >= HALF_YEAR_DAYS else Decimal("0.5")
        else:
            factor = weight / TWELVE
    return base * t.rate_percent / HUNDRED * factor, weight


def schedule(t, *, stop=None, through=None):
    """The month-by-month depreciation of an asset, from the month it is put to use.

    `stop`: the last day in use (a disposal); the schedule ends with that month. `through`: do not go beyond the month
    containing this date. Returns [{period_start, period_end, fy, fraction, charge, accumulated, wdv}] (only months that charge).
    """
    cap = t.depreciable
    if cap <= 0:
        return []
    if through is None and t.method == WDV:
        through = datetime.date(fy_start(t.put_to_use).year + WDV_YEARS, 3, 31)
    rows, cum, cum_r = [], Decimal(0), ZERO
    ms, plan = month_start(t.put_to_use), None
    while len(rows) < MAX_MONTHS:
        if stop and ms > stop:
            break
        if through and ms > through:
            break
        if plan is None or ms.month == 4:
            plan = _year_plan(t, ms, cum)
        yearly, weight = plan
        f = used_fraction(t.put_to_use, ms, stop)
        raw = yearly / TWELVE * f if t.method == SLM else yearly * f / weight
        cum = min(cap, cum + raw)
        new_r = rnd(cum)
        charge = new_r - cum_r
        cum_r = new_r
        if charge > 0:
            rows.append({"period_start": ms, "period_end": month_end(ms), "fy": fy_label(ms), "fraction": f, "charge": charge,
                         "accumulated": cum_r, "wdv": t.cost - cum_r})
        if cum_r >= cap:
            break
        ms = next_month(ms)
    return rows
