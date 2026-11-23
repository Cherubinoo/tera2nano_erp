import csv
import datetime
import io
from decimal import Decimal
from unittest import mock

from apps.tax.services import books, gst, registers
from .base import D, D0, GSTIN_ACME, GSTIN_BETA, GSTIN_VENDOR, S, T, TaxBase


def money(v):
    return D(str(v))


class SalesRegisterTests(TaxBase):
    def setUp(self):
        super().setUp()
        self.sales_fixture()
        self.reg = self.fin.get(f"{T}/gst/sales-register/", {"from": "2026-08-01", "to": "2026-08-31"}).json()

    def test_sections_are_split_by_customer_gstin_state_and_size(self):
        r = self.reg
        self.assertEqual([x["number"] for x in r["b2b"]], [self.inv1["number"], self.inv2["number"]])
        self.assertEqual([x["number"] for x in r["b2cl"]], [self.inv4["number"]])
        self.assertEqual([x["number"] for x in r["cdnr"]], [self.cn1["number"]])
        self.assertEqual(r["cdnur"], [])
        self.assertEqual(len(r["b2cs"]), 1)
        self.assertEqual(r["totals"]["documents"], 5)

    def test_b2b_rows_carry_gstin_place_of_supply_and_rate_wise_split(self):
        inv1, inv2 = self.reg["b2b"]
        self.assertEqual((inv1["gstin"], inv1["place_of_supply"], inv1["supply_type"]), (GSTIN_ACME, "29-Karnataka", "intra_state"))
        self.assertEqual((money(inv1["taxable"]), money(inv1["cgst"]), money(inv1["sgst"]), money(inv1["igst"]), money(inv1["invoice_value"])),
                         (D("10000"), D("900"), D("900"), D("0"), D("11800")))
        self.assertEqual((inv2["gstin"], inv2["place_of_supply"], inv2["supply_type"]), (GSTIN_BETA, "27-Maharashtra", "inter_state"))
        self.assertEqual((money(inv2["taxable"]), money(inv2["igst"]), money(inv2["invoice_value"])), (D("12000"), D("1900"), D("13900")))
        self.assertEqual([(money(x["rate"]), money(x["taxable"]), money(x["igst"])) for x in inv2["rates"]],
                         [(D("5"), D("2000"), D("100")), (D("18"), D("10000"), D("1800"))])

    def test_b2c_small_is_aggregated_by_place_and_rate_and_large_inter_state_is_listed(self):
        row = self.reg["b2cs"][0]
        self.assertEqual((row["place_of_supply"], money(row["rate"]), money(row["taxable"]), money(row["cgst"]), money(row["sgst"])),
                         ("29-Karnataka", D("18"), D("5000"), D("450"), D("450")))
        big = self.reg["b2cl"][0]
        self.assertEqual((big["place_of_supply"], money(big["taxable"]), money(big["igst"]), money(big["invoice_value"])),
                         ("27-Maharashtra", D("300000"), D("54000"), D("354000")))

    def test_credit_note_is_listed_with_its_type_and_reduces_the_totals(self):
        cn = self.reg["cdnr"][0]
        self.assertEqual((cn["note_type"], cn["gstin"], cn["against"], money(cn["taxable"]), money(cn["cgst"])),
                         ("C", GSTIN_ACME, self.inv1["number"], D("1000"), D("90")))
        t = self.reg["totals"]
        # 10,000 + 12,000 + 5,000 + 300,000 - 1,000
        self.assertEqual(money(t["taxable"]), D("326000"))
        self.assertEqual((money(t["igst"]), money(t["cgst"]), money(t["sgst"]), money(t["tax"])), (D("55900"), D("1260"), D("1260"), D("58420")))
        self.assertEqual(money(t["invoice_value"]), D("384420"))  # 11,800 + 13,900 + 5,900 + 354,000 - 1,180

    def test_hsn_summary_groups_by_code_and_rate_and_nets_credit_notes(self):
        rows = {(r["hsn"], money(r["rate"])): r for r in self.reg["hsn"]}
        main = rows[("998314", D("18"))]
        self.assertEqual((money(main["quantity"]), money(main["taxable"]), money(main["igst"]), money(main["cgst"]), money(main["sgst"]), money(main["value"])),
                         (D("5"), D("325000"), D("55800"), D("1350"), D("1350"), D("383500")))
        self.assertEqual((money(rows[("997331", D("5"))]["taxable"]), money(rows[("997331", D("5"))]["igst"])), (D("2000"), D("100")))
        self.assertEqual((money(rows[("NA", D("18"))]["taxable"]), money(rows[("NA", D("18"))]["quantity"])), (D("-1000"), D("-1")))

    def test_by_month_and_tax_by_rate(self):
        both = self.fin.get(f"{T}/gst/sales-register/", {"from": "2026-08-01", "to": "2026-09-30"}).json()
        self.assertEqual([(m["month"], m["documents"], money(m["taxable"]), money(m["tax"])) for m in both["by_month"]],
                         [("2026-08", 5, D("326000"), D("58420")), ("2026-09", 1, D("2000"), D("360"))])
        rates = {money(r["rate"]): (money(r["taxable"]), money(r["tax"])) for r in both["tax_by_rate"]}
        self.assertEqual(rates[D("18")], (D("326000"), D("58680")))
        self.assertEqual(rates[D("5")], (D("2000"), D("100")))

    def test_register_agrees_with_the_books(self):
        act = books.activity(self.company, datetime.date(2026, 8, 1), datetime.date(2026, 8, 31))
        self.assertEqual((books.raised(act, "cgst"), books.raised(act, "sgst"), books.raised(act, "igst")), (D("1260"), D("1260"), D("55900")))

    def test_voided_invoice_leaves_the_register_and_the_books(self):
        r = self.fin.post(f"{S}/documents/{self.inv3['id']}/void/", {"change_reason": "Wrong customer"})
        self.assertEqual(r.status_code, 200, r.content)
        reg = self.fin.get(f"{T}/gst/sales-register/", {"from": "2026-08-01", "to": "2026-08-31"}).json()
        self.assertEqual(reg["b2cs"], [])
        self.assertEqual(money(reg["totals"]["taxable"]), D("321000"))
        # the books reverse the sale on the day it is voided (today), so they agree with the register from then on
        act = books.activity(self.company, datetime.date(2026, 8, 1), datetime.date.today())
        self.assertEqual(books.raised(act, "cgst"), D("990"))  # 900 + 450 - 90 - 450 voided + 180 (September invoice)

    def test_csv_export_uses_gstr1_columns(self):
        r = self.fin.get(f"{T}/gst/sales-register/export/", {"from": "2026-08-01", "to": "2026-08-31", "section": "b2b"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r["Content-Type"])
        rows = list(csv.reader(io.StringIO(r.content.decode())))
        self.assertEqual(rows[0][:6], ["GSTIN/UIN of Recipient", "Receiver Name", "Invoice Number", "Invoice date", "Invoice Value", "Place Of Supply"])
        self.assertEqual(len(rows), 4)  # header + INV1 + INV2 at two rates
        self.assertEqual(rows[1][0], GSTIN_ACME)
        self.assertEqual((rows[1][3], rows[1][4], rows[1][5], rows[1][10], rows[1][11]), ("05-Aug-26", "11800.00", "29-Karnataka", "18", "10000.00"))
        self.assertEqual([(x[10], x[11]) for x in rows[2:]], [("5", "2000.00"), ("18", "10000.00")])
        for section, header0 in (("b2cl", "Invoice Number"), ("b2cs", "Type"), ("cdnr", "GSTIN/UIN of Recipient"), ("cdnur", "UR Type"), ("hsn", "HSN"), ("exp", "Export Type")):
            res = self.fin.get(f"{T}/gst/sales-register/export/", {"from": "2026-08-01", "to": "2026-08-31", "section": section})
            self.assertEqual(res.status_code, 200, section)
            self.assertEqual(list(csv.reader(io.StringIO(res.content.decode())))[0][0], header0)
        self.assertEqual(self.fin.get(f"{T}/gst/sales-register/export/", {"section": "nope"}).status_code, 400)

    def test_register_never_fails_when_a_source_is_broken(self):
        with mock.patch.object(registers, "_sales_register", side_effect=RuntimeError("table missing")):
            r = self.fin.get(f"{T}/gst/sales-register/", {"from": "2026-08-01", "to": "2026-08-31"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["b2b"], r.json()["hsn"], money(r.json()["totals"]["taxable"])), ([], [], D0))
        with mock.patch.object(registers, "_purchase_register", side_effect=RuntimeError("no journal")):
            r = self.fin.get(f"{T}/gst/purchase-register/", {"from": "2026-08-01", "to": "2026-08-31"})
        self.assertEqual((r.status_code, r.json()["rows"]), (200, []))



class PurchaseRegisterTests(TaxBase):
    def setUp(self):
        super().setUp()
        self.purchases_fixture()
        self.url = f"{T}/gst/purchase-register/"
        self.reg = self.fin.get(self.url, {"from": "2026-08-01", "to": "2026-08-31"}).json()

    def test_rows_come_from_the_input_gst_postings(self):
        r1, r2 = self.reg["rows"]
        self.assertEqual((r1["reference"], r1["supplier"], r1["gstin"], money(r1["taxable"]), money(r1["cgst"]), money(r1["sgst"]), money(r1["itc"])),
                         ("B-1", f"Cloud Co {GSTIN_VENDOR}", GSTIN_VENDOR, D("20000"), D("1800"), D("1800"), D("3600")))
        self.assertEqual((r2["gstin"], money(r2["igst"]), money(r2["itc"]), r2["supply_type"], money(r2["taxable"])), ("", D("1440"), D("1440"), "inter_state", D("8000")))

    def test_totals_split_eligible_and_unverified(self):
        t = self.reg["totals"]
        self.assertEqual((money(t["taxable"]), money(t["cgst"]), money(t["sgst"]), money(t["igst"]), money(t["itc"])),
                         (D("28000"), D("1800"), D("1800"), D("1440"), D("5040")))
        self.assertEqual((money(t["eligible"]), money(t["unverified"]), money(t["ineligible"])), (D("3600"), D("1440"), D("0")))
        self.assertEqual([r["eligibility"] for r in self.reg["rows"]], ["eligible", "unverified"])
        self.assertTrue(self.reg["warnings"])

    def test_marking_credit_ineligible_needs_a_reason_and_moves_the_amount(self):
        e2 = self.reg["rows"][1]["entry_id"]
        flag = f"{T}/gst/purchase-register/flag/"
        self.assertEqual(self.fin.post(flag, {"entry_id": e2, "eligibility": "ineligible"}).status_code, 400)
        self.assertEqual(self.fin.post(flag, {"entry_id": e2, "eligibility": "maybe"}).status_code, 400)
        r = self.fin.post(flag, {"entry_id": e2, "eligibility": "ineligible", "reason": "Blocked u/s 17(5)"})
        self.assertEqual(r.status_code, 200, r.content)
        reg = self.fin.get(self.url, {"from": "2026-08-01", "to": "2026-08-31"}).json()
        self.assertEqual((money(reg["totals"]["ineligible"]), money(reg["totals"]["unverified"])), (D("1440"), D("0")))
        self.assertEqual(reg["rows"][1]["reason"], "Blocked u/s 17(5)")
        self.assertEqual(self.fin.post(flag, {"entry_id": e2, "eligibility": "clear"}).status_code, 200)
        reg = self.fin.get(self.url, {"from": "2026-08-01", "to": "2026-08-31"}).json()
        self.assertEqual(money(reg["totals"]["ineligible"]), D0)

    def test_flag_for_an_unknown_entry_is_404_and_staff_cannot_flag(self):
        flag = f"{T}/gst/purchase-register/flag/"
        self.assertEqual(self.fin.post(flag, {"entry_id": "11111111-1111-1111-1111-111111111111", "eligibility": "eligible"}).status_code, 404)
        self.assertEqual(self.fin.post(flag, {"entry_id": "not-a-uuid", "eligibility": "eligible"}).status_code, 404)
        self.assertEqual(self.api_staff.post(flag, {"entry_id": self.reg["rows"][0]["entry_id"], "eligibility": "eligible"}).status_code, 403)

    def test_a_reversed_bill_nets_out_and_set_off_entries_are_not_purchases(self):
        from apps.accounting.services import ledger
        ledger.reverse(self.bill2, self.finance, "Wrong bill", on_date=datetime.date(2026, 8, 25))
        reg = self.fin.get(self.url, {"from": "2026-08-01", "to": "2026-08-31"}).json()
        self.assertEqual(money(reg["totals"]["igst"]), D0)
        self.assertEqual(money(reg["totals"]["itc"]), D("3600"))

    def test_csv_export(self):
        r = self.fin.get(f"{T}/gst/purchase-register/export/", {"from": "2026-08-01", "to": "2026-08-31"})
        rows = list(csv.reader(io.StringIO(r.content.decode())))
        self.assertEqual(rows[0][0], "GSTIN of Supplier")
        self.assertEqual((rows[1][0], rows[1][2], rows[1][5], rows[1][10], rows[1][11]), (GSTIN_VENDOR, "B-1", "20000.00", "3600.00", "eligible"))

    def test_by_month_and_top_suppliers(self):
        self.assertEqual([(m["month"], money(m["itc"])) for m in self.reg["by_month"]], [("2026-08", D("5040"))])
        self.assertEqual(money(self.reg["by_supplier"][0]["itc"]), D("3600"))
