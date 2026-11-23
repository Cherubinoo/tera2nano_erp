from django.core.management.base import BaseCommand

from apps.approvals.rules import seed_missing_rules
from apps.company.api import current_company


class Command(BaseCommand):
    help = ("Create the editable example approval rules. A company that has none gets the full set; a company that was set up "
            "earlier gets an example rule for each connected kind of record (vendor bill, budget, salary revision, ...) that "
            "has no rule yet. Rules that exist are never changed.")

    def handle(self, *args, **options):
        created = seed_missing_rules(current_company())
        self.stdout.write(f"{created} example rule(s) created." if created else "Every connected kind already has a rule; nothing to add.")
