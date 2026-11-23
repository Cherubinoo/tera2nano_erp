"""The tax module reading what the purchases module posts: vendor bills (with TDS) and their input GST."""
from apps.company.tests.base import make_gstin
from .base import D, D0, T, TaxBase

P = "/api/v1/purchases"
ITEMS = [{"description": "Server rack", "quantity": "10", "rate": "1000", "discount_percent": "10", "gst_rate": "18", "hsn_sac": "8473"}]
# 10 x 1,000 less 10% = 9,000 taxable; 18% = 1,620 (CGST 810 + SGST 810, or IGST 1,620)


def money(v):
    return D(str(v))


class PurchasesBillsInTheTaxModuleTests(TaxBase):
    def setUp(self):
        super().setUp()
        self.local = self.fin.post(f"{P}/vendors/", {"name": "Local Supplies", "gst_treatment": "registered", "gstin": make_gstin("29", "AAAPA1234A")}).json()
        self.far = self.fin.post(f"{P}/vendors/", {"name": "Far Away Traders", "gst_treatment": "registered", "gstin": make_gstin("27", "BBBPB1234B")}).json()
        for vendor, no, extra in ((self.local, "VB-1", {"tds_section": "194C", "tds_rate": "2"}), (self.far, "VB-2", {})):
            r = self.fin.post(f"{P}/bills/", {"vendor": vendor["id"], "vendor_bill_no": no, "bill_date": "2026-08-16", "items": ITEMS, "record": True, **extra})
            self.assertEqual(r.status_code, 201, r.content)
            self.bills = {**getattr(self, "bills", {}), no: r.json()}
        self.q = {"from": "2026-08-01", "to": "2026-08-31"}

    def test_posted_bills_appear_in_the_purchase_register_with_vendor_and_gstin(self):
        reg = self.fin.get(f"{T}/gst/purchase-register/", self.q).json()
        rows = {r["supplier"]: r for r in reg["rows"]}
        self.assertEqual(set(rows), {"Local Supplies", "Far Away Traders"})
        loc, far = rows["Local Supplies"], rows["Far Away Traders"]
        self.assertEqual(loc["gstin"], self.local["gstin"])
        self.assertEqual((money(loc["taxable"]), money(loc["cgst"]), money(loc["sgst"]), money(loc["igst"]), money(loc["itc"]), loc["eligibility"]),
                         (D("9000"), D("810"), D("810"), D0, D("1620"), "eligible"))
        self.assertEqual((money(far["taxable"]), money(far["igst"]), far["supply_type"], far["gstin"], far["eligibility"]),
                         (D("9000"), D("1620"), "inter_state", self.far["gstin"], "eligible"))
        t = reg["totals"]
        self.assertEqual((money(t["taxable"]), money(t["cgst"]), money(t["sgst"]), money(t["igst"]), money(t["itc"])),
                         (D("18000"), D("810"), D("810"), D("1620"), D("3240")))

    def test_input_credit_shows_in_the_liability_ledger_and_the_summary(self):
        led = self.fin.get(f"{T}/gst/liability-ledger/", self.q).json()
        itc = {h["head"]: money(h["itc_accrued"]) for h in led["heads"]}
        self.assertEqual(itc, {"cgst": D("810"), "sgst": D("810"), "igst": D("1620")})
        self.assertEqual({h["head"]: money(h["itc_closing"]) for h in led["heads"]}, itc)
        s = self.fin.get(f"{T}/gst/summary/", {"fy": "2026-27"}).json()
        aug = next(m for m in s["months"] if m["month"] == "2026-08")
        self.assertEqual((money(aug["itc_total"]), money(aug["output_total"]), money(aug["net"])), (D("3240"), D0, D("-3240")))  # refundable / carried forward
        self.assertEqual(money(s["kpis"]["itc_available"]), D("3240"))

    def test_tds_withheld_on_a_bill_is_in_the_tds_ledger_and_the_quarter_return(self):
        led = self.fin.get(f"{T}/tds/ledger/", self.q).json()
        self.assertEqual([(s["section"], money(s["deducted"]), money(s["closing"])) for s in led["sections"]], [("194C", D("180"), D("180"))])
        f = self.fin.post(f"{T}/filings/", {"return_type": "tds_26q", "financial_year": "2026-27", "quarter": "2"}).json()
        fig = self.fin.post(f"{T}/filings/{f['id']}/prepare/").json()["figures"]
        self.assertEqual((fig["tds_deducted"], fig["tds_paid"]), ("180.00", "0.00"))
        self.pay("tds", {"tds": "180"}, section="194C", date="2026-09-07")
        led = self.fin.get(f"{T}/tds/ledger/", {"from": "2026-08-01", "to": "2026-09-30"}).json()
        self.assertEqual((money(led["paid"]), money(led["closing"])), (D("180"), D0))

    def test_gstr3b_prepared_from_the_bills_sets_off_nothing_but_carries_credit_forward(self):
        f = self.filing("gstr3b", "2026-08")
        fig = self.fin.post(f"{T}/filings/{f['id']}/prepare/").json()["figures"]
        self.assertEqual((fig["itc_cgst"], fig["itc_sgst"], fig["itc_igst"], fig["net_payable"], fig["cash_payable"]),
                         ("810.00", "810.00", "1620.00", "-3240.00", "0.00"))
        self.assertEqual(self.fin.post(f"{T}/filings/{f['id']}/setoff/").status_code, 400)  # no output tax to set the credit against

    def test_a_voided_bill_leaves_the_register_through_its_reversal(self):
        far = self.bills["VB-2"]
        r = self.fin.post(f"{P}/bills/{far['id']}/void/", {"change_reason": "Duplicate bill", "reason": "Duplicate bill"})
        self.assertEqual(r.status_code, 200, r.content)
        import datetime
        reg = self.fin.get(f"{T}/gst/purchase-register/", {"from": "2026-08-01", "to": datetime.date.today().isoformat()}).json()
        self.assertEqual(money(reg["totals"]["igst"]), D0)
        self.assertEqual(money(reg["totals"]["itc"]), D("1620"))
