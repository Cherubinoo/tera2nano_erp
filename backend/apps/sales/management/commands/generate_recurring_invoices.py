"""Create the recurring invoices that are due. Schedule it once a day (Windows Task Scheduler, cron ...):

    python manage.py generate_recurring_invoices            # everything due up to today
    python manage.py generate_recurring_invoices --date 2026-12-01

Safe to run as often as you like: a run that was already made is never made again, and one profile failing (the reason is
shown on the profile as "last error") does not stop the others.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.company.api import current_company
from apps.sales import recurring


class Command(BaseCommand):
    help = "Create the recurring invoices that are due on or before today (or --date)."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Run as if this were today (YYYY-MM-DD).")
        parser.add_argument("--limit", type=int, default=recurring.MAX_CATCH_UP,
                            help="Most invoices to make per profile in this run (default %(default)s).")

    def handle(self, *args, **opts):
        try:
            today = datetime.date.fromisoformat(opts["date"]) if opts.get("date") else datetime.date.today()
        except ValueError:
            raise CommandError("--date must look like 2026-12-01.")
        try:
            company = current_company()
        except Exception:  # NotConfigured: nothing to do before the company exists
            raise CommandError("No company has been set up yet.")
        result = recurring.run_due(company, today, actor=None, can_issue=True, limit=max(opts["limit"], 1))
        for row in result["generated"]:
            self.stdout.write(f"Created {row['number'] or 'draft invoice'} for {row['name']} (run of {row['run_date']}).")
        for row in result["errors"]:
            self.stderr.write(f"FAILED {row['name']} (run of {row['run_date']}): {row['error']}")
        if result["still_due"]:
            self.stdout.write(f"{result['still_due']} profile(s) still have runs waiting; run the command again to continue.")
        self.stdout.write(f"{len(result['generated'])} invoice(s) created, {len(result['errors'])} failure(s).")
