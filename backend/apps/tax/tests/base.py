"""Shared fixtures for the tax tests. All numbers are hand-computed in the test docstrings (August 2026, FY 2026-27).

Sales (company is in Karnataka, state 29; every item is 18% GST unless said otherwise):
  INV1  5 Aug   Acme (registered, 29)      10,000            -> CGST 900   SGST 900    value 11,800   B2B
  INV2  10 Aug  Beta (registered, 27)      10,000 @18% + 2,000 @5%  -> IGST 1,800 + 100 = 1,900   value 13,900   B2B
  INV3  12 Aug  Walk-in consumer (29)       5,000            -> CGST 450   SGST 450    value 5,900    B2CS
  INV4  20 Aug  Delta (unregistered, 27)  300,000            -> IGST 54,000            value 354,000  B2CL
  CN1   22 Aug  credit note against INV1    1,000            -> CGST  90   SGST  90    value 1,180    CDNR
  INV5  3 Sep   Acme                        2,000            -> CGST 180   SGST 180    (September)
Purchases (journal, vendor_bill):
  BILL1 8 Aug   20,000 + CGST 1,800 + SGST 1,800 (vendor GSTIN in the narration)
  BILL2 18 Aug   8,000 + IGST 1,440 (no GSTIN)
"""
import datetime
import uuid
from decimal import Decimal

from django.test import TestCase

from apps.accounting.models import Account, JournalEntry
from apps.accounting.services import chart, ledger, reports
from apps.company.models import CompanyProfile, CompanyTaxSettings
from apps.company.tests.base import GSTIN, CompanyTestMixin, make_gstin

T = "/api/v1/tax"
S = "/api/v1/sales"
D = Decimal
D0 = D("0.00")

GSTIN_ACME = make_gstin("29", "AAAPA1234A")
GSTIN_BETA = make_gstin("27", "BBBPB1234B")
GSTIN_VENDOR = make_gstin("29", "AAACC1234C")


def item(rate, description="Consulting", qty="1", hsn="998314", gst_rate=None):
    out = {"description": description, "quantity": qty, "rate": rate, "hsn_sac": hsn}
    if gst_rate is not None:
        out["gst_rate"] = gst_rate
    return out


class TaxBase(CompanyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.full_company()
        self.fin = self.api_finance
        self.company = CompanyProfile.objects.get()
        chart.ensure_chart(self.company)

    # ---- company
    def enable_gst(self):
        CompanyProfile.objects.update(gst_registration_status="registered", gstin=GSTIN)
        CompanyTaxSettings.objects.update(gst_applicable=True, gst_treatment="regular", default_tax_code="GST18",
                                          tax_codes=[{"code": "GST18", "rate": 18}])
        self.company = CompanyProfile.objects.get()

    # ---- sales
    def customer(self, name, **over):
        r = self.fin.post(f"{S}/customers/", {"display_name": name, **over})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def invoice(self, customer, items, date, kind="invoice", **extra):
        d = self.fin.post(f"{S}/documents/", {"kind": kind, "customer": customer["id"], "issue_date": date, "form": {"items": items}, **extra})
        self.assertEqual(d.status_code, 201, d.content)
        r = self.fin.post(f"{S}/documents/{d.json()['id']}/issue/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def sales_fixture(self):
        self.enable_gst()
        self.acme = self.customer("Acme Registered", gst_treatment="registered", gstin=GSTIN_ACME, pan="AAAPA1234A")
        self.beta = self.customer("Beta Maharashtra", gst_treatment="registered", gstin=GSTIN_BETA, pan="BBBPB1234B")
        self.walkin = self.customer("Walk-in Consumer")
        self.delta = self.customer("Delta Unregistered", state_code="27")
        self.inv1 = self.invoice(self.acme, [item("10000")], "2026-08-05")
        self.inv2 = self.invoice(self.beta, [item("5000", qty="2"), item("2000", "Training", hsn="997331", gst_rate="5")], "2026-08-10")
        self.inv3 = self.invoice(self.walkin, [item("5000")], "2026-08-12")
        self.inv4 = self.invoice(self.delta, [item("300000")], "2026-08-20")
        self.cn1 = self.invoice(self.acme, [{"description": "Return", "quantity": "1", "rate": "1000"}], "2026-08-22", kind="credit_note",
                                against=self.inv1["id"], reason="Part returned")
        self.inv5 = self.invoice(self.acme, [item("2000")], "2026-09-03")

    # ---- books
    def acct(self, key_or_code):
        q = {"system_key": key_or_code} if not key_or_code.isdigit() else {"code": key_or_code}
        return Account.objects.get(company=self.company, **q)

    def post(self, lines, date, source="manual", source_id="", narration="Test entry", reference=""):
        return ledger.post(self.company, date=datetime.date.fromisoformat(date), lines=lines, narration=narration, source=source,
                           source_id=source_id or (str(uuid.uuid4()) if source != "manual" else ""), reference=reference)

    def bill(self, date, taxable, taxes, narration, ref, vendor=None):
        """A vendor bill: Dr expense (taxable) + Dr input GST, Cr payable."""
        vendor = vendor or str(uuid.uuid4())
        total = D(taxable) + sum((D(a) for _, a in taxes), D0)
        lines = [{"account": self.acct("5300"), "debit": D(taxable), "memo": narration},
                 *[{"account": k, "debit": D(a), "memo": narration} for k, a in taxes],
                 {"account": "payable", "credit": total, "memo": narration, "party": {"type": "vendor", "id": vendor}}]
        return self.post(lines, date, source="vendor_bill", narration=narration, reference=ref)

    def purchases_fixture(self):
        self.bill1 = self.bill("2026-08-08", "20000", [("gst_cgst_in", "1800"), ("gst_sgst_in", "1800")], f"Bill B-1: Cloud Co {GSTIN_VENDOR}", "B-1")
        self.bill2 = self.bill("2026-08-18", "8000", [("gst_igst_in", "1440")], "Bill B-2: Far Away Traders", "B-2")

    def tds_fixture(self):
        """TDS deducted in August: 10,000 under 194J, 500 under 194C, 3,000 salary TDS (payroll)."""
        for date, source, taxable, tds, memo in (("2026-08-10", "vendor_payment", "100000", "10000", "Payment to Lawyer: TDS 194J on bill L-1"),
                                                 ("2026-08-12", "vendor_payment", "50000", "500", "Payment to Courier: TDS 194C on bill C-1"),
                                                 ("2026-08-31", "payroll", "30000", "3000", "Payroll August")):
            self.post([{"account": "payable", "debit": D(taxable), "memo": memo}, {"account": "tds_payable", "credit": D(tds), "memo": memo},
                       {"account": "bank", "credit": D(taxable) - D(tds), "memo": memo}], date, source=source, narration=memo)

    def trial_balanced(self, as_of=datetime.date(2027, 3, 31)):
        tb = reports.trial_balance(self.company, as_of)
        self.assertTrue(tb["balanced"], tb)
        return tb

    def balance(self, key, as_of=datetime.date(2027, 3, 31)):
        from apps.tax.services import books
        return books.account_balance(self.company, key, as_of)

    # ---- api helpers
    def filing(self, return_type="gstr3b", period="2026-08", client=None, expect=201, **extra):
        payload = {"return_type": return_type, "financial_year": "2026-27", **extra}
        if return_type in ("gstr1", "gstr3b"):
            payload["period"] = period
        r = (client or self.fin).post(f"{T}/filings/", payload)
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def pay(self, tax_type="gst", heads=None, date="2026-09-18", expect=201, **extra):
        r = self.fin.post(f"{T}/payments/", {"tax_type": tax_type, "date": date, "heads": heads or {"igst": "1000.00"}, **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()
