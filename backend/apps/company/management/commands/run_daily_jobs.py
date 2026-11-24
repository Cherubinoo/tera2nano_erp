"""Run the daily jobs now (recurring invoices and anything else registered in apps/company/daily.py).

The app already runs them the first time someone opens it each day. Schedule this as well so they run even on days
nobody signs in:

    Windows Task Scheduler:  schtasks /Create /SC DAILY /ST 06:00 /TN "T2N daily jobs" ^
                                 /TR "C:\\path\\to\\backend\\.venv\\Scripts\\python.exe C:\\path\\to\\backend\\manage.py run_daily_jobs"
    cron (Linux):            0 6 * * *  cd /srv/t2n/backend && .venv/bin/python manage.py run_daily_jobs

Safe to run as often as you like.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.company import daily
from apps.company.models import CompanyProfile


class Command(BaseCommand):
    help = "Run the daily jobs (recurring invoices and others) now."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Run as if this were today (YYYY-MM-DD).")

    def handle(self, *args, **opts):
        try:
            today = datetime.date.fromisoformat(opts["date"]) if opts.get("date") else datetime.date.today()
        except ValueError:
            raise CommandError("--date must look like 2026-12-01.")
        company = CompanyProfile.objects.first()
        if company is None:
            raise CommandError("No company has been set up yet.")
        failed = 0
        for name, ok, summary in daily.run_all(company, today):
            if ok:
                self.stdout.write(f"{name}: {summary}")
            else:
                failed += 1
                self.stderr.write(f"FAILED {name}: {summary}")
        if failed:
            raise CommandError(f"{failed} job(s) failed.")
