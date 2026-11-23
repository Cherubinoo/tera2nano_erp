"""Financial years, tax periods and due dates. Pure date arithmetic; nothing here touches the database."""
import calendar
import datetime
import re
from decimal import Decimal

from django.core.exceptions import ValidationError

from .. import constants as C

MAX_RANGE_DAYS = 366 * 3 + 1
_FY_RE = re.compile(r"^(\d{4})-(\d{2})$")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fy_label_for(d):
    start = d.year if d.month >= 4 else d.year - 1
    return fy_label(start)


def fy_label(start_year):
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def parse_fy(label, field="financial_year"):
    """(start year, start date, end date) of "2026-27"."""
    m = _FY_RE.match(str(label or "").strip())
    if not m or int(m.group(2)) != (int(m.group(1)) + 1) % 100 or not 2000 <= int(m.group(1)) <= 2100:
        raise ValidationError({field: "Use a financial year such as 2026-27."})
    y = int(m.group(1))
    return y, datetime.date(y, 4, 1), datetime.date(y + 1, 3, 31)


def month_end(y, m):
    return datetime.date(y, m, calendar.monthrange(y, m)[1])


def month_label(d):
    return f"{MONTHS[d.month - 1]} {d.year}"


def month_key(d):
    return f"{d.year}-{d.month:02d}"


def parse_month(value, field="period"):
    """(first day, last day) of "2026-08"."""
    m = re.match(r"^(\d{4})-(\d{2})$", str(value or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12 or not 2000 <= int(m.group(1)) <= 2100:
        raise ValidationError({field: "Use a month such as 2026-08."})
    y, mo = int(m.group(1)), int(m.group(2))
    return datetime.date(y, mo, 1), month_end(y, mo)


def fy_months(fy):
    """[(first day, last day, "Apr 2026")] for the twelve months of a financial year."""
    y, _, _ = parse_fy(fy)
    out = []
    for i in range(12):
        yy, mm = (y, 4 + i) if 4 + i <= 12 else (y + 1, 4 + i - 12)
        out.append((datetime.date(yy, mm, 1), month_end(yy, mm), month_label(datetime.date(yy, mm, 1))))
    return out


def fy_quarters(fy):
    """[(quarter, first day, last day, "Q1 2026-27")]."""
    y, _, _ = parse_fy(fy)
    spans = [(1, (y, 4, 1), (y, 6, 30)), (2, (y, 7, 1), (y, 9, 30)), (3, (y, 10, 1), (y, 12, 31)), (4, (y + 1, 1, 1), (y + 1, 3, 31))]
    return [(q, datetime.date(*a), datetime.date(*b), f"Q{q} {fy}") for q, a, b in spans]


def quarter_of(d):
    """The financial-year quarter (1-4) a date falls in."""
    return {4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3, 1: 4, 2: 4, 3: 4}[d.month]


def date_range(start, end, *, default=None):
    """Validate a from/to pair (ISO strings or dates): from <= to and a sensible span."""
    def one(value, field):
        if isinstance(value, datetime.date):
            return value
        try:
            return datetime.date.fromisoformat(str(value))
        except ValueError:
            raise ValidationError({field: "Use a date in YYYY-MM-DD format."})

    if not start and not end and default:
        return default
    s = one(start, "from") if start else default[0]
    e = one(end, "to") if end else default[1]
    if e < s:
        raise ValidationError({"to": "The end date is before the start date."})
    if (e - s).days > MAX_RANGE_DAYS:
        raise ValidationError({"to": "Choose a period of three years or less."})
    return s, e


def _safe_date(y, m, d):
    return datetime.date(y, m, min(d, calendar.monthrange(y, m)[1]))


def due_date(return_type, fy, period_start, audit_applicable=False):
    """The due date of a return for a period, from the rules in constants.DUE_RULES."""
    rule = C.DUE_RULES.get(return_type)
    if rule is None:
        raise ValidationError({"return_type": "Unknown return type."})
    y, _, _ = parse_fy(fy)
    if rule["kind"] == "monthly":
        total = period_start.year * 12 + (period_start.month - 1) + rule["months_after"]
        return _safe_date(total // 12, total % 12 + 1, rule["day"])
    if rule["kind"] == "quarterly":
        month, day, years = rule["quarters"][quarter_of(period_start)]
        return _safe_date(y + years, month, day)
    spec = rule["audit"] if audit_applicable and "audit" in rule else rule
    return _safe_date(y + rule["years_after"], spec["month"], spec["day"])


def periods_for(return_type, fy):
    """[(period_start, period_end, label)] a return of this type is due for in a financial year."""
    y, start, end = parse_fy(fy)
    kind = C.DUE_RULES[return_type]["kind"]
    if kind == "monthly":
        return fy_months(fy)
    if kind == "quarterly":
        return [(s, e, label) for _q, s, e, label in fy_quarters(fy)]
    return [(start, end, f"FY {fy}")]


def advance_tax_dates(fy):
    """[(instalment, due date, cumulative percent)]."""
    y, _, _ = parse_fy(fy)
    return [(n, datetime.date(y + 1 if (m, d) == (3, 15) else y, m, d), Decimal(pct)) for n, (m, d), pct in C.ADVANCE_TAX_SCHEDULE]
