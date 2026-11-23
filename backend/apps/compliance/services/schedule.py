"""Due-date rules and status computation. Pure functions: nothing here touches the database or the real clock.

A financial year runs 1 April to 31 March. The calendar of a financial year holds every due date that FALLS inside it, so
the LLP annual return (due 30 May) in the calendar for 2026-27 is for the PREVIOUS year (2025-26) and says so in its period.
"""
import calendar
import datetime
from dataclasses import dataclass

from django.core.exceptions import ValidationError

from .. import constants as C


@dataclass(frozen=True)
class Due:
    period_key: str
    period_label: str
    due_date: datetime.date


# ---------------------------------------------------------------------------- financial years
def fy_label(start_year):
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def parse_fy(label):
    """2026 from '2026-27'. Raises ValidationError for anything else, including a wrong second year ('2026-29')."""
    text = str(label or "").strip()
    try:
        first, second = text.split("-")
        start = int(first)
        if len(first) != 4 or len(second) != 2 or int(second) != (start + 1) % 100:
            raise ValueError
    except (ValueError, TypeError):
        raise ValidationError({"fy": "Use a financial year like 2026-27."})
    return start


def fy_bounds(label):
    start = parse_fy(label)
    return datetime.date(start, 4, 1), datetime.date(start + 1, 3, 31)


def fy_for_date(d):
    return fy_label(d.year if d.month >= 4 else d.year - 1)


def fy_months(label):
    """The 12 (year, month) pairs of the financial year, April first."""
    start = parse_fy(label)
    return [(start if m >= 4 else start + 1, m) for m in (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3)]


# ---------------------------------------------------------------------------- date helpers
def clamp_day(year, month, day):
    """A real date: `day` ("last" or a number) is pulled back to the month's last day, so 31 February never happens."""
    last = calendar.monthrange(year, month)[1]
    if day == "last":
        return datetime.date(year, month, last)
    return datetime.date(year, month, min(int(day), last))


def add_months(d, months):
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    return clamp_day(year, month + 1, d.day)


def add_days(d, days):
    return d + datetime.timedelta(days=days)


# ---------------------------------------------------------------------------- due-date rules
def validate_rule(rule):
    """Check a due rule typed by a person; returns it normalised or raises ValidationError({"due_rule": ...})."""
    def bad(msg):
        raise ValidationError({"due_rule": msg})

    if not isinstance(rule, dict) or "type" not in rule:
        bad("Choose how the due date is worked out.")
    kind = rule["type"]
    if kind == "event":
        days = rule.get("days", 30)
        if not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= 365:
            bad("Days after the event must be a whole number from 0 to 365.")
        return {"type": "event", "days": days}
    if kind == "monthly":
        day, lag = rule.get("day"), rule.get("lag", 1)
        if day != "last" and (not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 31):
            bad("Day of the month must be 1 to 31, or 'last'.")
        if not isinstance(lag, int) or isinstance(lag, bool) or not 0 <= lag <= 12:
            bad("Months after the period must be 0 to 12.")
        return {"type": "monthly", "day": day, "lag": lag}
    if kind == "dates":
        entries = rule.get("dates")
        if not isinstance(entries, list) or not entries:
            bad("Give at least one due date.")
        out = []
        for e in entries:
            if not isinstance(e, dict):
                bad("Each due date needs a month and a day.")
            month, day = e.get("month"), e.get("day")
            if not isinstance(month, int) or isinstance(month, bool) or not 1 <= month <= 12:
                bad("Month must be 1 to 12.")
            if day != "last" and (not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 31):
                bad("Day must be 1 to 31, or 'last'.")
            fy = e.get("fy", "prev")
            if fy not in ("prev", "cur"):
                bad("Period must be the previous or the current financial year.")
            out.append({"month": month, "day": day, "fy": fy, "label": str(e.get("label") or "FY {fy}")[:80]})
        return {"type": "dates", "dates": out}
    bad("Unknown due-date rule.")


def _label(template, fy_start):
    return template.replace("{fy}", fy_label(fy_start)).replace("{ay}", fy_label(fy_start + 1))


def due_dates(rule, fy):
    """Every due date of `rule` that falls inside financial year `fy` ('2026-27'), earliest first. Event rules give none."""
    start = parse_fy(fy)
    kind = (rule or {}).get("type")
    out = []
    if kind == "dates":
        for e in rule["dates"]:
            year = start if e["month"] >= 4 else start + 1
            period_start = start - 1 if e.get("fy", "prev") == "prev" else start
            out.append(Due(f"{fy}:{e['month']:02d}{e['day'] if e['day'] != 'last' else 'LL'}",
                           _label(e.get("label") or "FY {fy}", period_start), clamp_day(year, e["month"], e["day"])))
    elif kind == "monthly":
        lag = rule.get("lag", 1)
        for year, month in fy_months(fy):
            index = year * 12 + (month - 1) - lag
            py, pm = divmod(index, 12)
            out.append(Due(f"M{py}-{pm + 1:02d}", f"{calendar.month_name[pm + 1]} {py}", clamp_day(year, month, rule["day"])))
    return sorted(out, key=lambda d: d.due_date)


def event_due(rule, event_date):
    days = (rule or {}).get("days", 30)
    return add_days(event_date, days)


# ---------------------------------------------------------------------------- status
def status_key(status, due_date, today=None, *, due_soon_days=C.DUE_SOON_DAYS):
    """upcoming / due_soon / overdue / filed / not_applicable. `today` is injectable so tests use a fixed clock."""
    if status == "filed":
        return "filed"
    if status == "not_applicable":
        return "not_applicable"
    today = today or datetime.date.today()
    if due_date < today:
        return "overdue"
    if (due_date - today).days <= due_soon_days:
        return "due_soon"
    return "upcoming"


def days_late(due_date, filed_date):
    return max(0, (filed_date - due_date).days)


def reminder_dates(due_date, reminder_days):
    """The dates on which a reminder is due, latest first removed duplicates."""
    return sorted({add_days(due_date, -int(n)) for n in reminder_days}, reverse=True)
