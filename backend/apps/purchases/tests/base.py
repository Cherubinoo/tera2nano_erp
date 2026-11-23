import datetime

from django.test import TestCase

from apps.accounts.models import Role
from apps.company.tests.base import CompanyTestMixin, client_for, make_gstin, make_user

P = "/api/v1/purchases"
PAN_IN = "AAAPA1234A"   # a vendor in the company's own state (29, Karnataka)
PAN_OUT = "BBBPB1234B"  # a vendor in another state (27, Maharashtra)
# 10 x 1,000 less 10%: 9,000.00 taxable; 18% GST = 1,620.00 (CGST 810.00 + SGST 810.00, or IGST 1,620.00); total 10,620.00
ITEMS = [{"description": "Server rack", "quantity": "10", "rate": "1000", "discount_percent": "10", "gst_rate": "18", "hsn_sac": "8473"}]
ALL_ROLES = [Role.ADMIN, Role.MANAGING_PARTNER, Role.PARTNER, Role.FINANCE_MANAGER, Role.HR_MANAGER, Role.PROJECT_MANAGER,
             Role.EMPLOYEE]


class PurchasesBase(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.fin = self.api_finance
        self.local = self.vendor("Local Supplies", gst_treatment="registered", gstin=make_gstin("29", PAN_IN), payment_terms_days=30)
        self.far = self.vendor("Far Away Traders", gst_treatment="registered", gstin=make_gstin("27", PAN_OUT))

    # -- builders
    def vendor(self, name, expect=201, **over):
        r = self.fin.post(f"{P}/vendors/", {"name": name, **over})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def order(self, vendor=None, items=None, order_date="2026-08-15", expect=201, **extra):
        r = self.fin.post(f"{P}/orders/", {"vendor": (vendor or self.local)["id"], "order_date": order_date,
                                           "form": {"items": items or ITEMS}, **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def issue_order(self, order):
        r = self.fin.post(f"{P}/orders/{order['id']}/issue/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def bill(self, vendor=None, items=None, bill_date="2026-08-16", no=None, record=True, expect=201, **extra):
        self._bill_seq = getattr(self, "_bill_seq", 0) + 1
        no = no or f"VB-{self._bill_seq:04d}"
        r = self.fin.post(f"{P}/bills/", {"vendor": (vendor or self.local)["id"], "vendor_bill_no": no, "bill_date": bill_date,
                                          "items": items or ITEMS, "record": record, **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def pay(self, vendor=None, amount="1000", date="2026-08-20", expect=201, **extra):
        r = self.fin.post(f"{P}/payments/", {"vendor": (vendor or self.local)["id"], "date": date, "amount": amount,
                                             "mode": "bank", **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def get_bill(self, bill):
        return self.fin.get(f"{P}/bills/{bill['id']}/").json()

    def lines_of(self, source, source_id):
        """{account code: (debit, credit)} of the entry a business event posted."""
        from decimal import Decimal
        from apps.accounting.models import JournalEntry
        entry = JournalEntry.objects.get(source=source, source_id=str(source_id))
        out = {}
        for line in entry.lines.select_related("account"):
            d, c = out.get(line.account.code, (Decimal("0"), Decimal("0")))
            out[line.account.code] = (d + line.debit, c + line.credit)
        return out

    def account(self, code):
        """A chart account by code (the standard chart is created the first time the purchases options are read)."""
        from apps.accounting.models import Account
        self.fin.get(f"{P}/options/")
        return Account.objects.get(code=code)

    def trial_balance(self):
        from apps.accounting.services import reports
        from apps.company.api import current_company
        return reports.trial_balance(current_company(), datetime.date(2027, 3, 31))

    def assert_books_balance(self):
        tb = self.trial_balance()
        self.assertTrue(tb["balanced"], tb)
        self.assertEqual(tb["total_debit"], tb["total_credit"])
