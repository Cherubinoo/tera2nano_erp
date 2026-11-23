"""Shared helpers for the reports: the period a report covers, percentages, months, and CSV rows.

Reports only ever read: they never write to another module's tables. Money is `Decimal`, rounded to the paisa.
"""
import datetime
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.core.exceptions import ValidationError

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_DAYS = 366 * 5  # the longest span a report accepts (five years)
_FY_RE = re.compile(r"^(\d{4})-(\d{2})$")


def money(value):
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def percent(part, whole, places="0.01"):
    """part as a percentage of whole, two decimal places; None when there is nothing to compare with."""
    whole = Decimal(whole or 0)
    if whole == 0:
        return None
    return (Decimal(part or 0) / whole * 100).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def change_percent(current, previous):
    """How much `current` moved against `previous`, in percent (None when previous is zero)."""
    previous = Decimal(previous or 0)
    if previous == 0:
        return None
    return ((Decimal(current or 0) - previous) / abs(previous) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- periods

def fy_label(start_year):
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def fy_start_year(d):
    return d.year if d.month >= 4 else d.year - 1


def fy_dates(start_year):
    return datetime.date(start_year, 4, 1), datetime.date(start_year + 1, 3, 31)


def shift_year(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28)


@dataclass(frozen=True)
class Period:
    start: Optional[datetime.date]
    end: Optional[datetime.date]
    fy: str = ""        # "2026-27" when a financial year was asked for
    custom: bool = False

    @property
    def is_all(self):
        return self.start is None

    @property
    def label(self):
        if self.is_all:
            return "All dates"
        if self.fy:
            return f"FY {self.fy}"
        return f"{self.start.isoformat()} to {self.end.isoformat()}"

    def as_dict(self):
        return {"fy": self.fy, "from": self.start, "to": self.end, "custom": self.custom, "all": self.is_all, "label": self.label}


def _date(value, field):
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({field: "Use a date in YYYY-MM-DD format."})


def resolve_period(params, today, *, allow_all=False):
    """The period a request asks for: `fy=2026-27`, or `from` and `to` dates, or `fy=all` (only where `allow_all`).

    With nothing given, the financial year (April to March) that contains `today`, or all dates where `allow_all`."""
    fy, frm, to = (params.get("fy") or "").strip(), params.get("from") or "", params.get("to") or ""
    if frm or to:
        if not frm:
            raise ValidationError({"from": "Enter the start date."})
        if not to:
            raise ValidationError({"to": "Enter the end date."})
        start, end = _date(frm, "from"), _date(to, "to")
        if start > end:
            raise ValidationError({"to": "The end date is before the start date."})
        if (end - start).days > MAX_DAYS:
            raise ValidationError({"to": "Choose a range of at most five years."})
        return Period(start, end, custom=True)
    if fy.lower() == "all":
        if not allow_all:
            raise ValidationError({"fy": "This report needs a financial year or a date range."})
        return Period(None, None)
    if fy:
        m = _FY_RE.match(fy)
        if not m or int(m.group(2)) != (int(m.group(1)) + 1) % 100 or not 2000 <= int(m.group(1)) <= 2100:
            raise ValidationError({"fy": "Use a financial year such as 2026-27."})
        start, end = fy_dates(int(m.group(1)))
        return Period(start, end, fy=fy)
    if allow_all:
        return Period(None, None)
    year = fy_start_year(today)
    start, end = fy_dates(year)
    return Period(start, end, fy=fy_label(year))


def months_between(start, end):
    """First days of every month from `start`'s month to `end`'s month."""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(datetime.date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def last_months(today, count):
    """First days of the `count` months ending with today's month, oldest first."""
    y, m, out = today.year, today.month, []
    for _ in range(count):
        out.append(datetime.date(y, m, 1))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def month_label(first):
    return first.strftime("%b %y")


def month_key(first):
    return f"{first.year}-{first.month:02d}"


# --------------------------------------------------------------------------- CSV

def csv_safe(value):
    """Stop spreadsheet programs from running a cell that starts like a formula."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "@", "\t", "\r") or (s[:1] == "-" and not _is_number(s)) else s


def _is_number(s):
    try:
        Decimal(s)
        return True
    except Exception:  # noqa: BLE001 - anything that is not a number
        return False


def block(title, header, rows):
    """One table of a CSV file: a title row, the header, the rows and a blank line after."""
    return [[title], list(header), *[list(r) for r in rows], []]
