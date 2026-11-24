"""Daily jobs: work that must happen once a day without anybody clicking a button.

Each job is `fn(company, today) -> str` (a one-line summary) and must be safe to run more than once a day: a second run
finds nothing left to do. Jobs run in two ways, and both may be used together:

* on their own, the first time someone opens the app each day (`run_if_due`, called from the "who am I" request the app
  makes when it loads; switch off with DAILY_JOBS_ON_REQUEST=false);
* on a schedule: `python manage.py run_daily_jobs` from Windows Task Scheduler or cron (see docs/DEPLOYMENT notes in README).

One job failing is logged and does not stop the others.
"""
import datetime
import logging
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

_JOBS = []
_state = {"date": None}
_lock = threading.Lock()


def register(name, fn):
    """Add a job. Modules call this from AppConfig.ready(); registering the same name twice replaces the first."""
    _JOBS[:] = [(n, f) for n, f in _JOBS if n != name]
    _JOBS.append((name, fn))


def jobs():
    return list(_JOBS)


def _recurring_invoices(company, today):
    from apps.sales import recurring
    result = recurring.run_due(company, today, actor=None, can_issue=True)
    text = f"{len(result['generated'])} recurring invoice(s) created, {len(result['errors'])} failure(s)"
    if result["still_due"]:
        text += f", {result['still_due']} profile(s) still catching up"
    return text


register("Recurring invoices", _recurring_invoices)


def run_all(company, today=None):
    """Run every job now. Returns [(name, ok, summary)]."""
    today = today or datetime.date.today()
    out = []
    for name, fn in jobs():
        try:
            out.append((name, True, fn(company, today) or "done"))
        except Exception as exc:  # one job failing must not stop the rest
            logger.exception("Daily job %s failed", name)
            out.append((name, False, str(exc)[:300]))
    return out


def run_if_due():
    """Run the jobs if they have not run today in this server process. Cheap after the first call of the day; never raises."""
    if not getattr(settings, "DAILY_JOBS_ON_REQUEST", False):
        return
    today = datetime.date.today()
    if _state["date"] == today or not _lock.acquire(blocking=False):
        return
    try:
        if _state["date"] == today:
            return
        _state["date"] = today  # set first: a failing job is retried tomorrow, not on every request
        from apps.company.models import CompanyProfile
        company = CompanyProfile.objects.first()
        if company is None:
            _state["date"] = None  # no company yet: try again once it exists
            return
        for name, ok, summary in run_all(company, today):
            (logger.info if ok else logger.warning)("Daily job %s: %s", name, summary)
    except Exception:
        logger.exception("Daily jobs could not run")
    finally:
        _lock.release()
