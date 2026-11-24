"""Bank statement import (CSV, Excel, old .xls, PDF, OFX), matching statement lines to the books, making entries from lines,
and the reconciliation from imported statements."""
import datetime
import io
import os
import zipfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from apps.accounting.models import (Account, BankMatch, BankReconciliation, BankStatement, BankStatementLine, JournalEntry,
                                    JournalLine, Voucher)
from apps.accounting.services import statement_files as sf
from apps.accounts.models import Role
from apps.approvals import rules
from apps.company.models import CompanyProfileAuditEvent
from apps.company.tests.base import client_for, make_user, viewer_client

from .test_accounting import A, Base, D

P = "/api/v1/purchases"


# --------------------------------------------------------------------------- file builders

def csv_bytes(rows, sep=","):
    return ("\n".join(sep.join(f'"{c}"' if ("," in c and sep == ",") else c for c in r) for r in rows) + "\n").encode()


def xlsx_bytes(rows):
    """A minimal .xlsx: text in shared strings, numbers (including Excel date serials) as numeric cells."""
    strings, sheet_rows = [], []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if value is None or value == "":
                continue
            if isinstance(value, (int, float, Decimal)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                if value not in strings:
                    strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{strings.index(value)}</v></c>')
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><workbook {ns} {rel_ns}><sheets><sheet name="Statement" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0"?><worksheet {ns}><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>')
    return buf.getvalue()


def serial(d):
    return (d - datetime.date(1899, 12, 30)).days


def pdf_table_bytes(rows):
    """A simple text-based PDF statement: one table, one transaction per row, built with reportlab (already a project
    dependency) so PyMuPDF has real, well-separated columns of text to extract. Landscape, with each column wide enough
    for its own header, so reportlab never wraps a header word onto a second line (which would otherwise split it into
    an extra, misaligned row of its own)."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=24, bottomMargin=24, leftMargin=24, rightMargin=24)
    widths = [max(stringWidth(str(r[c]), "Helvetica", 9) for r in rows) + 24 for c in range(len(rows[0]))]
    table = Table(rows, hAlign="LEFT", colWidths=widths)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 9),
                               ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10)]))
    doc.build([table])
    return buf.getvalue()


def scanned_pdf_bytes():
    """A one-page PDF with a drawn shape but no text layer at all, standing in for a scanned/photographed statement."""
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=letter)
    c.rect(72, 72, 200, 200, fill=1)
    c.showPage()
    c.save()
    return buf.getvalue()


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def read_fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


HDFC_HEAD = ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]
PREAMBLE = [["Example Bank Ltd"], ["Account statement for Example Ventures LLP"], ["Account No: XXXXXXXX9012", "", "Period: Aug 2026"], []]

# Also used by the .xls fixture below (apps/accounting/tests/fixtures/sample_statement.xls), so the three file formats can
# be compared on exactly the same statement. That fixture was made once with a short script using xlwt:
#
#   import xlwt, datetime
#   wb = xlwt.Workbook(); ws = wb.add_sheet('Statement')
#   date_style = xlwt.XFStyle(); date_style.num_format_str = 'DD/MM/YYYY'
#   for c, h in enumerate(["Date", "Narration", "Chq / Ref No.", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]):
#       ws.write(0, c, h)
#   rows = [("", "Opening Balance", "", "", "", 5000.00),
#           (datetime.date(2026, 9, 3), "NEFT RECEIVED FROM VENDOR X", "UTR555", "", 2500.00, 7500.00),
#           (datetime.date(2026, 9, 5), "ATM WITHDRAWAL", "", 1000.00, "", 6500.00),
#           (datetime.date(2026, 9, 7), "SALARY PAYMENT", "CHQ100", 500.00, "", 6000.00)]
#   for r, row in enumerate(rows, start=1):
#       for c, v in enumerate(row):
#           if v != "":
#               ws.write(r, c, v, date_style if isinstance(v, datetime.date) else None)
#   wb.save('sample_statement.xls')
SEPT_HEAD = ["Date", "Narration", "Chq / Ref No.", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]
SEPT_ROWS = [["", "Opening Balance", "", "", "", "5000.00"],
             ["03/09/2026", "NEFT RECEIVED FROM VENDOR X", "UTR555", "", "2500.00", "7500.00"],
             ["05/09/2026", "ATM WITHDRAWAL", "", "1000.00", "", "6500.00"],
             ["07/09/2026", "SALARY PAYMENT", "CHQ100", "500.00", "", "6000.00"]]
SEPT_TRANSACTIONS = [(datetime.date(2026, 9, 3), "NEFT RECEIVED FROM VENDOR X", D("2500.00")),
                     (datetime.date(2026, 9, 5), "ATM WITHDRAWAL", D("-1000.00")),
                     (datetime.date(2026, 9, 7), "SALARY PAYMENT", D("-500.00"))]


# --------------------------------------------------------------------------- parsing, no database

class ParsingTests(SimpleTestCase):
    def test_indian_date_formats(self):
        want = datetime.date(2026, 8, 5)
        for text in ("05/08/2026", "5/8/2026", "05-08-26", "05.08.2026", "05-Aug-2026", "05 Aug 26", "5-AUG-2026", "05Aug2026",
                     "2026-08-05", "Aug 5, 2026", "05/08/2026 10:22:01", "2026-08-05T00:00:00", str(serial(want)), "20260805",
                     "05 August 2026", "05-Sept-2026".replace("Sept", "Aug")):
            self.assertEqual(sf.parse_date(text), want, text)
        self.assertEqual(sf.parse_date("08/05/2026", "mdy"), want)
        self.assertEqual(sf.parse_date("05-Sept-2026"), datetime.date(2026, 9, 5))
        for bad in ("", "31/02/2026", "Opening Balance", "12345", "13/13/2026", "Date"):
            self.assertIsNone(sf.parse_date(bad), bad)

    def test_amounts_with_commas_currency_brackets_and_dr_cr(self):
        cases = {"1,23,456.00": (D("123456.00"), None), "₹ 500": (D("500.00"), None), "Rs. 1,000.5": (D("1000.50"), None),
                 "(250.00)": (D("-250.00"), None), "500-": (D("-500.00"), None), "-75": (D("-75.00"), None),
                 "1,000.00 Cr": (D("1000.00"), "cr"), "Dr 75": (D("75.00"), "dr"), "12,345.67 DR": (D("12345.67"), "dr"),
                 "": (None, None), "-": (None, None), "0.004": (D("0.00"), None), "INR 10.005": (D("10.01"), None)}
        for text, want in cases.items():
            self.assertEqual(sf.parse_amount(text), want, text)
        for bad in ("abc", "1.2.3", "12 34x"):
            with self.assertRaises(ValueError):
                sf.parse_amount(bad)

    def test_column_guessing_for_common_layouts(self):
        m = sf.guess_mapping(HDFC_HEAD)
        self.assertEqual((m["date"], m["description"], m["reference"], m["debit"], m["credit"], m["balance"]), (0, 1, 2, 4, 5, 6))
        self.assertNotIn("amount", m)
        sbi = sf.guess_mapping(["Txn Date", "Value Date", "Description", "Ref No./Cheque No.", "Debit", "Credit", "Balance"])
        self.assertEqual((sbi["date"], sbi["description"], sbi["reference"], sbi["debit"], sbi["credit"], sbi["balance"]), (0, 2, 3, 4, 5, 6))
        # "Transaction Date" wins over an earlier "Value Date"
        icici = sf.guess_mapping(["S No.", "Value Date", "Transaction Date", "Cheque Number", "Transaction Remarks",
                                  "Withdrawal Amount (INR )", "Deposit Amount (INR )", "Balance (INR )"])
        self.assertEqual((icici["date"], icici["reference"], icici["description"], icici["debit"], icici["credit"], icici["balance"]),
                         (2, 3, 4, 5, 6, 7))
        kotak = sf.guess_mapping(["Sl. No.", "Transaction Date", "Value Date", "Description", "Chq / Ref No.", "Amount", "Dr / Cr", "Balance"])
        self.assertEqual((kotak["date"], kotak["amount"], kotak["drcr"], kotak["balance"]), (1, 5, 6, 7))
        axis = sf.guess_mapping(["Tran Date", "CHQNO", "PARTICULARS", "DR", "CR", "BAL", "SOL"])
        self.assertEqual((axis["date"], axis["reference"], axis["description"], axis["debit"], axis["credit"], axis["balance"]), (0, 1, 2, 3, 4, 5))

    def test_header_row_found_below_the_account_details(self):
        self.assertEqual(sf.find_header_row(PREAMBLE + [HDFC_HEAD, ["01/08/26", "x", "", "", "1", "", "1"]]), 4)

    def test_rows_become_signed_transactions(self):
        m = {**sf.guess_mapping(HDFC_HEAD), "date_order": "dmy"}
        self.assertEqual(sf.interpret_row(["05/08/26", "NEFT IN", "UTR1", "05/08/26", "", "1,000.00", "5,000.00"], m),
                         ("ok", {"date": datetime.date(2026, 8, 5), "description": "NEFT IN", "reference": "UTR1", "amount": D("1000.00"),
                                 "balance": D("5000.00")}))
        self.assertEqual(sf.interpret_row(["05/08/26", "ATM", "", "", "500.00", "", "4,500.00 Dr"], m)[1]["amount"], D("-500.00"))
        self.assertEqual(sf.interpret_row(["05/08/26", "ATM", "", "", "500.00", "", "4,500.00 Dr"], m)[1]["balance"], D("-4500.00"))
        self.assertEqual(sf.interpret_row(["", "Opening Balance", "", "", "", "", "4,000.00"], m), ("skip", "heading or total"))
        self.assertEqual(sf.interpret_row(["", "Total", "", "", "500.00", "1,000.00", ""], m), ("skip", "heading or total"))
        self.assertEqual(sf.interpret_row(["", "", "", "", "", "", ""], m), ("skip", "blank"))
        self.assertEqual(sf.interpret_row(["32/08/26", "x", "", "", "5", "", ""], m)[0], "problem")
        self.assertIn("Both", sf.interpret_row(["05/08/26", "x", "", "", "5", "6", ""], m)[1])
        self.assertIn("no withdrawal", sf.interpret_row(["05/08/26", "x", "", "", "", "", ""], m)[1])
        single = {"date": 0, "amount": 1, "drcr": 2, "date_order": "dmy"}
        self.assertEqual(sf.interpret_row(["05-Aug-2026", "250.00", "DR"], single)[1]["amount"], D("-250.00"))
        self.assertEqual(sf.interpret_row(["05-Aug-2026", "250.00", "Cr"], single)[1]["amount"], D("250.00"))
        self.assertEqual(sf.interpret_row(["05-Aug-2026", "250.00", "??"], single)[0], "problem")
        signed = {"date": 0, "amount": 1, "date_order": "dmy", "amount_sign": "withdrawal_positive"}
        self.assertEqual(sf.interpret_row(["05-Aug-2026", "250.00"], signed)[1]["amount"], D("-250.00"))

    def test_file_type_detection(self):
        self.assertEqual(sf.detect_format(b"PK\x03\x04rest"), "xlsx")
        self.assertEqual(sf.detect_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest"), "xls")
        self.assertEqual(sf.detect_format(b"%PDF-1.4 rest"), "pdf")
        self.assertEqual(sf.detect_format(b"OFXHEADER:100\nDATA:OFXSGML"), "ofx")
        self.assertEqual(sf.detect_format(b"Date,Narration\n"), "csv")
        with self.assertRaises(Exception):
            sf.detect_format(b"\x00\x01\x02binary")


# --------------------------------------------------------------------------- API

class BankBase(Base):
    def setUp(self):
        super().setUp()
        self.bank = self.acct("bank")
        self.B = f"{A}/bank/{self.bank.id}"

    def upload(self, data, name="statement.csv", client=None, expect=201, account=None):
        f = SimpleUploadedFile(name, data, content_type="application/octet-stream")
        url = f"{A}/bank/{(account or self.bank).id}/statements/"
        r = (client or self.fin).post(url, {"file": f}, format="multipart")
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def do_import(self, up, mapping=None, dry_run=False, expect=200):
        r = self.fin.post(f"{A}/statements/{up['statement']['id']}/import/", {"mapping": mapping if mapping is not None else up["mapping"],
                                                                             "dry_run": dry_run})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def import_csv(self, rows, name="statement.csv"):
        up = self.upload(csv_bytes(rows), name)
        return self.do_import(up)["summary"]

    def lines(self, status="unmatched"):
        r = self.fin.get(f"{self.B}/lines/", {"status": status})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["results"]

    def line(self, description):
        return BankStatementLine.objects.filter(description=description).order_by("date", "seq").first()

    def book_line(self, source, source_id):
        return JournalLine.objects.get(entry__source=source, entry__source_id=str(source_id), account=self.bank)

    def events(self, entity):
        return CompanyProfileAuditEvent.objects.filter(entity_type=entity)


AUGUST = PREAMBLE + [HDFC_HEAD,
                     ["", "Opening Balance", "", "", "", "", "0.00"],
                     ["02/08/26", "CAPITAL INTRODUCED BY PARTNERS", "", "02/08/26", "", "10,000.00", "10,000.00"],
                     ["21/08/26", "NEFT-UTR123456-ACME INDUSTRIES", "UTR123456", "21/08/26", "", "1,800.00", "11,800.00"],
                     ["25/08/26", "CHQ PAID RENT AUG LANDLORD", "000123", "25/08/26", "1,000.00", "", "10,800.00"],
                     ["26/08/26", "SMS CHARGES", "", "26/08/26", "15.00", "", "10,785.00"],
                     ["26/08/26", "SMS CHARGES", "", "26/08/26", "15.00", "", "10,770.00"],
                     ["31/13/26", "BAD DATE ROW", "", "", "5.00", "", "10,765.00"],
                     ["", "", "", "", "", "", ""],
                     ["", "Total", "", "", "1,030.00", "11,800.00", ""]]


class ImportTests(BankBase):
    def test_csv_upload_guesses_the_columns_then_imports_with_a_summary(self):
        up = self.upload(csv_bytes(AUGUST))
        self.assertTrue(up["needs_mapping"])
        self.assertEqual(up["mapping_source"], "guessed")
        self.assertEqual(up["mapping"]["header_row"], 4)
        self.assertEqual((up["mapping"]["date"], up["mapping"]["debit"], up["mapping"]["credit"], up["mapping"]["balance"]), (0, 4, 5, 6))
        self.assertEqual(up["statement"]["status"], "draft")
        dry = self.do_import(up, dry_run=True)
        self.assertEqual(dry["summary"]["imported"], 5)
        self.assertEqual(BankStatementLine.objects.count(), 0)  # a check imports nothing
        out = self.do_import(up)
        s = out["summary"]
        self.assertEqual((s["rows_read"], s["imported"], s["duplicates"], s["skipped"]), (8, 5, 0, 2))
        self.assertEqual(len(s["problems"]), 1)
        self.assertEqual(s["problems"][0]["row"], 12)
        self.assertIn("Could not read the date", s["problems"][0]["reason"])
        st = BankStatement.objects.get()
        self.assertEqual((st.status, st.closing_balance, st.period_start, st.period_end),
                         ("imported", D("10770.00"), datetime.date(2026, 8, 2), datetime.date(2026, 8, 26)))
        amounts = list(BankStatementLine.objects.order_by("date", "seq").values_list("amount", flat=True))
        self.assertEqual(amounts, [D("10000.00"), D("1800.00"), D("-1000.00"), D("-15.00"), D("-15.00")])
        self.assertEqual(self.line("NEFT-UTR123456-ACME INDUSTRIES").reference, "UTR123456")
        ev = self.events("bank_statement").get()
        self.assertEqual(ev.new_values["imported"], 5)
        # importing the same draft twice is refused
        self.do_import(up, expect=400)

    def test_reimporting_an_overlapping_statement_adds_only_new_lines(self):
        self.import_csv(AUGUST)
        later = [HDFC_HEAD,
                 ["26/08/26", "SMS CHARGES", "", "26/08/26", "15.00", "", "10,785.00"],
                 ["26/08/26", "SMS CHARGES", "", "26/08/26", "15.00", "", "10,770.00"],
                 ["03/09/26", "INTEREST CREDIT", "", "03/09/26", "", "12.00", "10,782.00"]]
        up = self.upload(csv_bytes(later), "september.csv")
        self.assertEqual(up["mapping_source"], "saved")  # remembered from the August import
        s = self.do_import(up)["summary"]
        self.assertEqual((s["imported"], s["duplicates"]), (1, 2))
        self.assertEqual(BankStatementLine.objects.filter(description="SMS CHARGES").count(), 2)
        again = self.do_import(self.upload(csv_bytes(AUGUST)))["summary"]
        self.assertEqual((again["imported"], again["duplicates"]), (0, 5))
        self.assertEqual(BankStatementLine.objects.count(), 6)

    def test_newest_first_files_are_put_in_date_order(self):
        rows = [HDFC_HEAD, ["05/08/26", "B", "", "", "100", "", "900"], ["03/08/26", "A", "", "", "", "1000", "1000"]]
        self.import_csv(rows)
        self.assertEqual(list(BankStatementLine.objects.order_by("seq").values_list("description", flat=True)), ["A", "B"])
        self.assertEqual(BankStatement.objects.get().closing_balance, D("900.00"))

    def test_excel_file_with_serial_dates_and_a_dr_cr_column(self):
        data = xlsx_bytes([["Statement of account"], [],
                           ["Transaction Date", "Description", "Chq / Ref No.", "Amount", "Dr / Cr", "Balance"],
                           [serial(datetime.date(2026, 8, 4)), "UPI/ACME", "R1", 2500, "CR", 2500],
                           ["06-Aug-2026", "ATM WDL", "", "1,000.00", "DR", 1500],
                           [serial(datetime.date(2026, 8, 7)), "POS SHOP", "", 99.5, "DR", 1400.5]])
        up = self.upload(data, "statement.xlsx")
        self.assertEqual(up["statement"]["format"], "xlsx")
        self.assertEqual(up["mapping"]["header_row"], 2)
        self.assertEqual((up["mapping"]["amount"], up["mapping"]["drcr"]), (3, 4))
        s = self.do_import(up)["summary"]
        self.assertEqual((s["imported"], s["problems"]), (3, []))
        self.assertEqual(list(BankStatementLine.objects.order_by("date").values_list("date", "amount")),
                         [(datetime.date(2026, 8, 4), D("2500.00")), (datetime.date(2026, 8, 6), D("-1000.00")),
                          (datetime.date(2026, 8, 7), D("-99.50"))])
        self.assertEqual(BankStatement.objects.get().closing_balance, D("1400.50"))

    def test_ofx_file_needs_no_mapping(self):
        ofx = b"""OFXHEADER:100
DATA:OFXSGML

<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260810<TRNAMT>4000.00<FITID>A1<NAME>ACME INDUSTRIES</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260812120000<TRNAMT>-250.00<FITID>A2<NAME>BANK CHARGES<MEMO>QTR</STMTTRN>
</BANKTRANLIST><LEDGERBAL><BALAMT>3750.00<DTASOF>20260812</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"""
        up = self.upload(ofx, "statement.ofx")
        self.assertFalse(up["needs_mapping"])
        s = self.do_import(up, mapping={})["summary"]
        self.assertEqual(s["imported"], 2)
        self.assertEqual(BankStatementLine.objects.get(amount=D("-250.00")).description, "BANK CHARGES QTR")
        self.assertEqual(BankStatement.objects.get().closing_balance, D("3750.00"))
        again = self.do_import(self.upload(ofx, "statement.ofx"), mapping={})["summary"]
        self.assertEqual((again["imported"], again["duplicates"]), (0, 2))

    def test_old_xls_file_is_read_like_other_excel_files(self):
        up = self.upload(read_fixture("sample_statement.xls"), "statement.xls")
        self.assertEqual(up["statement"]["format"], "xls")
        self.assertEqual((up["mapping"]["date"], up["mapping"]["debit"], up["mapping"]["credit"], up["mapping"]["balance"]), (0, 3, 4, 5))
        s = self.do_import(up)["summary"]
        self.assertEqual((s["imported"], s["skipped"], s["problems"]), (3, 1, []))  # "Opening Balance" is skipped
        got = list(BankStatementLine.objects.order_by("date").values_list("date", "description", "amount"))
        self.assertEqual(got, SEPT_TRANSACTIONS)
        self.assertEqual(BankStatement.objects.get().closing_balance, D("6000.00"))

    def test_text_based_pdf_statement_is_extracted_into_rows_and_imported(self):
        up = self.upload(pdf_table_bytes([SEPT_HEAD] + SEPT_ROWS), "statement.pdf")
        self.assertEqual(up["statement"]["format"], "pdf")
        self.assertTrue(up["needs_mapping"])
        self.assertIn("date", up["mapping"])
        self.assertTrue({"debit", "credit"} <= set(up["mapping"]))
        s = self.do_import(up)["summary"]
        self.assertEqual((s["imported"], s["problems"]), (3, []))
        got = list(BankStatementLine.objects.order_by("date").values_list("date", "description", "amount"))
        self.assertEqual(got, SEPT_TRANSACTIONS)

    def test_scanned_pdf_with_no_text_is_refused(self):
        up = self.upload(scanned_pdf_bytes(), "scanned.pdf", expect=400)
        self.assertIn("scanned image", up["file"][0])

    def test_xls_pdf_and_csv_of_the_same_statement_produce_identical_transactions(self):
        def preview_of(data, name):
            up = self.upload(data, name)
            summary = self.do_import(up, dry_run=True)["summary"]
            return [(datetime.date.fromisoformat(p["date"]), p["description"], D(str(p["amount"]))) for p in summary["preview"]]

        csv_got = preview_of(csv_bytes([SEPT_HEAD] + SEPT_ROWS), "statement.csv")
        xls_got = preview_of(read_fixture("sample_statement.xls"), "statement.xls")
        pdf_got = preview_of(pdf_table_bytes([SEPT_HEAD] + SEPT_ROWS), "statement.pdf")
        self.assertEqual(csv_got, SEPT_TRANSACTIONS)
        self.assertEqual(xls_got, SEPT_TRANSACTIONS)
        self.assertEqual(pdf_got, SEPT_TRANSACTIONS)

    def test_files_that_cannot_be_read_are_refused(self):
        self.upload(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", "old.xls", expect=400)
        self.upload(b"%PDF-1.4 stuff", "statement.pdf", expect=400)
        self.upload(b"PK\x03\x04broken", "broken.xlsx", expect=400)
        r = self.fin.post(f"{self.B}/statements/", {}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.upload(b"", "empty.csv", expect=400)
        self.assertEqual(BankStatement.objects.count(), 0)
        # a cash account or an account of no kind is not a bank account
        cash = self.acct("cash")
        self.assertEqual(self.fin.post(f"{A}/bank/{cash.id}/statements/", {"file": SimpleUploadedFile("s.csv", b"a,b")},
                                       format="multipart").status_code, 404)

    def test_mapping_must_name_a_date_and_an_amount(self):
        up = self.upload(csv_bytes(AUGUST))
        m = dict(up["mapping"])
        for bad in ({**m, "date": None}, {**m, "debit": None, "credit": None}, {**m, "amount": 3}, {**m, "date": 99},
                    {**m, "drcr": 2}, {**m, "date_order": "ymd"}, "x"):
            self.do_import(up, mapping=bad, expect=400)
        nothing = {**m, "date": 1}  # the narration column read as dates: nothing imports
        self.do_import(up, mapping=nothing, expect=400)
        self.assertEqual(BankStatementLine.objects.count(), 0)

    def test_header_row_can_be_chosen_again(self):
        up = self.upload(csv_bytes(AUGUST))
        r = self.fin.get(f"{A}/statements/{up['statement']['id']}/", {"header_row": 0}).json()
        self.assertEqual(r["mapping"]["header_row"], 0)
        self.assertEqual(r["headers"][0], "Example Bank Ltd")
        self.assertEqual(len(r["fields"]), 8)

    def test_statement_files_are_private(self):
        up = self.upload(csv_bytes(AUGUST))
        st = BankStatement.objects.get()
        with self.assertRaises(ValueError):
            st.file.url
        self.assertTrue(st.file.name.startswith("bank-statements/"))
        self.assertNotIn("statement.csv", st.file.name)  # never the uploaded name on disk
        self.assertNotIn("url", str(up["statement"]))
        r = self.fin.get(f"{A}/statements/{st.id}/file/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, no-store")
        self.assertEqual(self.api_staff.get(f"{A}/statements/{st.id}/file/").status_code, 403)
        self.assertEqual(self.anon.get(f"{A}/statements/{st.id}/file/").status_code, 401)

    def test_removing_an_import(self):
        up = self.upload(csv_bytes(AUGUST))
        self.assertEqual(self.fin.delete(f"{A}/statements/{up['statement']['id']}/").status_code, 204)  # a draft
        self.import_csv(AUGUST)
        st = BankStatement.objects.get()
        line = BankStatementLine.objects.filter(amount=D("-15.00")).first()
        self.fin.post(f"{A}/statement-lines/{line.id}/ignore/", {"reason": "test"})
        self.assertEqual(self.fin.delete(f"{A}/statements/{st.id}/").status_code, 400)
        self.fin.post(f"{A}/statement-lines/{line.id}/unignore/")
        self.assertEqual(self.fin.delete(f"{A}/statements/{st.id}/").status_code, 204)
        self.assertEqual(BankStatementLine.objects.count(), 0)
        self.assertEqual(self.events("bank_statement").filter(action="deleted").count(), 1)
        listing = self.fin.get(f"{self.B}/statements/").json()
        self.assertEqual(listing, [])


class MatchingBase(BankBase):
    def setUp(self):
        super().setUp()
        self.journal([("bank", {"debit": "10000"}), ("capital", {"credit": "10000"})], date="2026-08-02", narration="Capital introduced")
        self.acme = self.customer("Acme Industries")
        self.inv = self.invoice(self.acme, date="2026-08-10")                                                   # 1,800.00
        self.payment = self.pay(self.acme, "1800", date="2026-08-20", reference="UTR123456",
                                allocations=[{"document": self.inv["id"], "amount": "1800"}])
        self.rent = self.expense(amount="1000", date="2026-08-25", party_name="Landlord Properties", reference="000123")
        self.import_csv(AUGUST)

    def suggestions(self, description):
        for l in self.lines():
            if l["description"] == description:
                return l["suggestions"]
        self.fail(f"no unmatched line {description}")

    def accept(self, description, journal_lines, expect=201, **extra):
        r = self.fin.post(f"{self.B}/matches/", {"statement_lines": [str(self.line(description).id)] if isinstance(description, str)
                                                 else [str(self.line(d).id) for d in description],
                                                 "journal_lines": journal_lines, **extra})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()


class MatchingTests(MatchingBase):
    def test_suggestions_rank_amount_date_and_reference(self):
        s = self.suggestions("NEFT-UTR123456-ACME INDUSTRIES")
        pay_line = self.book_line("payment", self.payment["id"])
        self.assertEqual(s[0]["journal_lines"], [str(pay_line.id)])
        self.assertEqual(s[0]["confidence"], "high")
        self.assertTrue(any("UTR123456" in r for r in s[0]["reasons"]), s[0]["reasons"])
        self.assertTrue(any("Acme" in r for r in s[0]["reasons"]), s[0]["reasons"])
        rent = self.suggestions("CHQ PAID RENT AUG LANDLORD")
        self.assertEqual(rent[0]["journal_lines"], [str(self.book_line("expense", self.rent["id"]).id)])
        self.assertEqual(rent[0]["confidence"], "high")  # same amount, same date, cheque number
        cap = self.suggestions("CAPITAL INTRODUCED BY PARTNERS")
        self.assertEqual(cap[0]["confidence"], "high")  # same amount and date
        self.assertEqual(self.suggestions("SMS CHARGES"), [])  # nothing in the books for 15.00

    def test_accept_and_undo_a_match(self):
        pay_line = self.book_line("payment", self.payment["id"])
        m = self.accept("NEFT-UTR123456-ACME INDUSTRIES", [str(pay_line.id)], kind="suggested")
        line = self.line("NEFT-UTR123456-ACME INDUSTRIES")
        pay_line.refresh_from_db()
        self.assertEqual((line.status, str(line.match_id), str(pay_line.bank_match_id)), ("matched", m["id"], m["id"]))
        self.assertEqual(BankMatch.objects.get().kind, "suggested")
        matched = self.lines("matched")
        self.assertEqual(matched[0]["matched_with"][0]["id"], str(pay_line.id))
        self.assertEqual(self.events("bank_match").filter(action="created").count(), 1)
        # the same entry or line cannot be matched twice
        self.accept("NEFT-UTR123456-ACME INDUSTRIES", [str(pay_line.id)], expect=400)
        self.assertEqual(self.fin.post(f"{A}/bank-matches/{m['id']}/undo/").status_code, 204)
        line.refresh_from_db()
        pay_line.refresh_from_db()
        self.assertEqual((line.status, line.match_id, pay_line.bank_match_id), ("unmatched", None, None))
        self.assertEqual(self.events("bank_match").filter(action="deleted").count(), 1)

    def test_amounts_must_agree(self):
        rent = self.book_line("expense", self.rent["id"])
        r = self.fin.post(f"{self.B}/matches/", {"statement_lines": [str(self.line("NEFT-UTR123456-ACME INDUSTRIES").id)],
                                                 "journal_lines": [str(rent.id)]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("difference", str(r.json()))
        self.assertEqual(self.fin.post(f"{self.B}/matches/", {"statement_lines": [], "journal_lines": [str(rent.id)]}).status_code, 400)
        self.assertEqual(self.fin.post(f"{self.B}/matches/", {"statement_lines": ["x"], "journal_lines": ["y"]}).status_code, 400)
        self.assertEqual(self.fin.post(f"{self.B}/matches/", {"statement_lines": "x"}).status_code, 400)

    def test_one_deposit_for_two_payments(self):
        globex = self.customer("Globex Corporation")
        a, b = self.invoice(globex, date="2026-08-01"), self.invoice(globex, date="2026-08-02")
        p1 = self.pay(globex, "1000", date="2026-08-27", allocations=[{"document": a["id"], "amount": "1000"}])
        p2 = self.pay(globex, "800", date="2026-08-27", allocations=[{"document": b["id"], "amount": "800"}])
        self.import_csv([HDFC_HEAD, ["28/08/26", "CLEARING DEPOSIT GLOBEX", "", "", "", "1,800.00", "12,570.00"]], "extra.csv")
        s = self.suggestions("CLEARING DEPOSIT GLOBEX")
        pair = {str(self.book_line("payment", p1["id"]).id), str(self.book_line("payment", p2["id"]).id)}
        # the Acme payment is a single 1,800.00 too, but it is a week away; the Globex pair is offered as well
        self.assertTrue(any(set(x["journal_lines"]) == pair for x in s), s)
        self.accept("CLEARING DEPOSIT GLOBEX", sorted(pair))
        self.assertEqual(JournalLine.objects.filter(bank_match__isnull=False).count(), 2)

    def test_several_lines_for_one_entry(self):
        self.import_csv([HDFC_HEAD, ["27/08/26", "PART ONE", "", "", "", "300.00", ""], ["27/08/26", "PART TWO", "", "", "", "200.00", ""]], "parts.csv")
        e = self.journal([("bank", {"debit": "500"}), ("other_income", {"credit": "500"})], date="2026-08-27", narration="Refund")
        jl = JournalLine.objects.get(entry_id=e["id"], account=self.bank)
        self.accept(["PART ONE", "PART TWO"], [str(jl.id)])
        self.assertEqual(BankStatementLine.objects.filter(match__isnull=False).count(), 2)

    def test_ignore_needs_a_reason_and_can_be_taken_back(self):
        line = BankStatementLine.objects.filter(description="SMS CHARGES").first()
        self.assertEqual(self.fin.post(f"{A}/statement-lines/{line.id}/ignore/", {"reason": " "}).status_code, 400)
        r = self.fin.post(f"{A}/statement-lines/{line.id}/ignore/", {"reason": "Reversed by the bank next day"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["status"], r.json()["ignore_reason"]), ("ignored", "Reversed by the bank next day"))
        self.assertEqual(self.fin.post(f"{A}/statement-lines/{line.id}/ignore/", {"reason": "again"}).status_code, 400)
        self.assertEqual(self.events("statement_line").count(), 1)
        self.assertEqual(self.fin.post(f"{A}/statement-lines/{line.id}/unignore/").json()["status"], "unmatched")
        self.assertEqual(self.fin.post(f"{A}/statement-lines/{line.id}/unignore/").status_code, 400)

    def test_accept_all_sure_matches(self):
        r = self.fin.post(f"{self.B}/matches/auto/")
        self.assertEqual(r.json()["matched"], 3)  # capital, Acme payment, rent; the bank charges have no entry
        self.assertEqual(BankStatementLine.objects.filter(status="unmatched").count(), 2)
        self.assertEqual(set(BankMatch.objects.values_list("kind", flat=True)), {"suggested"})

    def test_search_book_entries_by_hand(self):
        r = self.fin.get(f"{self.B}/book-entries/", {"q": "1000"}).json()
        self.assertEqual(sorted(x["amount"] for x in r["results"]), ["-1000.00"])
        r = self.fin.get(f"{self.B}/book-entries/", {"direction": "in"}).json()
        self.assertEqual(sorted(x["amount"] for x in r["results"]), ["10000.00", "1800.00"])
        r = self.fin.get(f"{self.B}/book-entries/", {"q": "Acme"}).json()
        self.assertEqual(len(r["results"]), 1)

    def test_lines_listing_filters(self):
        self.assertEqual(len(self.lines("all")), 5)
        r = self.fin.get(f"{self.B}/lines/", {"status": "nope"})
        self.assertEqual(r.status_code, 400)
        r = self.fin.get(f"{self.B}/lines/", {"q": "rent"}).json()
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["status_counts"]["unmatched"], 5)


class CreateFromLineTests(MatchingBase):
    def create(self, description, kind, expect=201, client=None, **data):
        r = (client or self.fin).post(f"{A}/statement-lines/{self.line(description).id}/create/", {"kind": kind, **data})
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def test_customer_payment_against_an_open_invoice(self):
        globex = self.customer("Globex Corporation")
        inv = self.invoice(globex, date="2026-08-05")
        self.import_csv([HDFC_HEAD, ["28/08/26", "NEFT GLOBEX CORP", "UTR999", "", "", "1,800.00", ""]], "g.csv")
        opts = self.fin.get(f"{A}/statement-lines/{self.line('NEFT GLOBEX CORP').id}/create-options/",
                            {"kind": "customer_payment"}).json()
        self.assertEqual(opts["invoices"][0]["id"], inv["id"])
        out = self.create("NEFT GLOBEX CORP", "customer_payment", customer=globex["id"], invoices=[inv["id"]])
        self.assertFalse(out["pending"])
        self.assertEqual(out["line"]["status"], "matched")
        self.assertEqual(out["record"]["applied_to"], [inv["number"]])
        doc = self.fin.get(f"/api/v1/sales/documents/{inv['id']}/").json()
        self.assertEqual(D(doc["balance"]), D("0"))
        entry = JournalEntry.objects.get(source="payment", source_id=out["record"]["id"])
        self.assertEqual({(l.account.system_key, l.debit, l.credit) for l in entry.lines.select_related("account")},
                         {("bank", D("1800"), D("0")), ("receivable", D("0"), D("1800"))})
        self.assertEqual(BankMatch.objects.get(statement_lines__description="NEFT GLOBEX CORP").kind, "created")
        self.tb()

    def test_customer_payment_needs_money_in_and_a_customer(self):
        self.create("SMS CHARGES", "customer_payment", expect=400, customer=self.acme["id"])
        self.import_csv([HDFC_HEAD, ["28/08/26", "NEFT X", "", "", "", "50.00", ""]], "x.csv")
        self.create("NEFT X", "customer_payment", expect=400)
        self.create("NEFT X", "nonsense", expect=400)
        self.create("NEFT X", "customer_payment", expect=400, customer=self.acme["id"], invoices=["00000000-0000-0000-0000-000000000000"])
        out = self.create("NEFT X", "customer_payment", customer=self.acme["id"])  # no invoice: kept as an advance
        entry = JournalEntry.objects.get(source="payment", source_id=out["record"]["id"])
        self.assertIn("customer_advances", {l.account.system_key for l in entry.lines.select_related("account")})

    def test_vendor_payment_against_a_bill(self):
        vendor = self.fin.post(f"{P}/vendors/", {"name": "Local Supplies"}).json()
        bill = self.fin.post(f"{P}/bills/", {"vendor": vendor["id"], "vendor_bill_no": "LS-77", "bill_date": "2026-08-10", "record": True,
                                             "items": [{"description": "Cables", "quantity": "10", "rate": "500"}]}).json()
        self.assertEqual(bill["status"], "open", bill)
        total = D(bill["net_payable"])
        self.import_csv([HDFC_HEAD, ["29/08/26", "NEFT LOCAL SUPPLIES LS-77", "", "", f"{total}", "", ""]], "v.csv")
        opts = self.fin.get(f"{A}/statement-lines/{self.line('NEFT LOCAL SUPPLIES LS-77').id}/create-options/",
                            {"kind": "vendor_payment", "q": "LS-77"}).json()
        self.assertEqual(opts["bills"][0]["id"], bill["id"])
        out = self.create("NEFT LOCAL SUPPLIES LS-77", "vendor_payment", vendor=vendor["id"], bills=[bill["id"]])
        self.assertEqual(out["line"]["status"], "matched")
        b = self.fin.get(f"{P}/bills/{bill['id']}/").json()
        self.assertEqual(D(b["balance"]), D("0"))
        entry = JournalEntry.objects.get(source="vendor_payment", source_id=out["record"]["id"])
        self.assertEqual({(l.account.system_key, l.debit, l.credit) for l in entry.lines.select_related("account")},
                         {("bank", D("0"), total), ("payable", total, D("0"))})
        self.tb()

    def test_vendor_payment_held_for_approval_is_matched_after_approval(self):
        rules.save_rule(self.company, {"kind": "payment", "name": "All payments", "priority": 1, "amount_from": "1",
                                       "steps": [{"approver_type": "role", "role": "managing_partner"}]})
        vendor = self.fin.post(f"{P}/vendors/", {"name": "Local Supplies"}).json()
        self.import_csv([HDFC_HEAD, ["29/08/26", "NEFT LOCAL SUPPLIES ADV", "", "", "700.00", "", ""]], "v.csv")
        out = self.create("NEFT LOCAL SUPPLIES ADV", "vendor_payment", vendor=vendor["id"])
        self.assertTrue(out["pending"])
        line = self.line("NEFT LOCAL SUPPLIES ADV")
        self.assertEqual((line.status, line.created_source), ("unmatched", "vendor_payment"))
        self.assertFalse(JournalEntry.objects.filter(source="vendor_payment").exists())
        self.create("NEFT LOCAL SUPPLIES ADV", "expense", expect=400, category=str(self.acct("5900").id))  # one entry per line
        from apps.approvals import api as approvals
        mp = client_for(make_user("mp", Role.MANAGING_PARTNER))
        req = approvals.latest_request("payment", out["record"]["id"])
        self.assertEqual(mp.post(f"/api/v1/approvals/requests/{req.pk}/approve/", {"comment": ""}).status_code, 200)
        s = self.suggestions("NEFT LOCAL SUPPLIES ADV")
        self.assertEqual((s[0]["score"], s[0]["reasons"]), (100, ["Made from this statement line"]))

    def test_expense_bank_charge_income_and_transfer(self):
        out = self.create("CHQ PAID RENT AUG LANDLORD", "expense", category=str(self.acct("5600").id), party_name="Office")
        self.assertEqual(Voucher.objects.get(pk=out["record"]["id"]).total, D("1000.00"))
        charge = BankStatementLine.objects.filter(description="SMS CHARGES").first()
        r = self.fin.post(f"{A}/statement-lines/{charge.id}/create/", {"kind": "bank_charge"})
        self.assertEqual(r.status_code, 201, r.content)
        v = Voucher.objects.get(pk=r.json()["record"]["id"])
        self.assertEqual((v.category.code, v.total, v.money_account_id), ("5700", D("15.00"), self.bank.id))
        self.import_csv([HDFC_HEAD, ["30/08/26", "INTEREST PAID", "", "", "", "42.00", ""], ["30/08/26", "CASH WITHDRAWAL ATM", "", "", "2,000.00", "", ""]], "x.csv")
        out = self.create("INTEREST PAID", "income", category=str(self.acct("other_income").id))
        self.assertEqual(Voucher.objects.get(pk=out["record"]["id"]).kind, "income")
        self.create("INTEREST PAID", "income", expect=400, category=str(self.acct("other_income").id))  # already matched
        out = self.create("CASH WITHDRAWAL ATM", "transfer", other_account=str(self.acct("cash").id))
        self.assertEqual(self.bal("cash"), D("2000"))
        self.assertEqual(out["line"]["status"], "matched")
        self.create("SMS CHARGES", "expense", expect=400)  # no account chosen for a plain expense
        self.tb()

    def test_held_expense_waits_for_approval(self):
        rules.save_rule(self.company, {"kind": "expense", "name": "All expenses", "priority": 1, "amount_from": "1",
                                       "steps": [{"approver_type": "role", "role": "managing_partner"}]})
        out = self.create("CHQ PAID RENT AUG LANDLORD", "expense", category=str(self.acct("5600").id))
        self.assertTrue(out["pending"])
        self.assertEqual(Voucher.objects.get(pk=out["record"]["id"]).status, "pending_approval")

    def test_creating_payments_needs_the_sales_or_purchases_permission(self):
        acct_only = viewer_client("acct", "accounting.view", "accounting.manage", role=Role.PROJECT_MANAGER)
        self.import_csv([HDFC_HEAD, ["28/08/26", "NEFT Y", "", "", "", "50.00", ""]], "y.csv")
        self.create("NEFT Y", "customer_payment", expect=403, client=acct_only, customer=self.acme["id"])
        self.create("NEFT Y", "income", client=acct_only, category=str(self.acct("other_income").id))

    def test_customer_and_vendor_payments_can_be_created_from_any_bank_account_statement(self):
        """A bank-subtype ledger account with no linked Company Bank Account (and not the default bank account) used to
        refuse customer/vendor payments; it now posts straight to that ledger account instead."""
        other = Account.objects.create(company=self.company, code="1099", name="HDFC Current (not linked)", type="asset", subtype=Account.Subtype.BANK)
        up = self.upload(csv_bytes([HDFC_HEAD, ["28/08/26", "NEFT GLOBEX CORP", "UTR999", "", "", "1,800.00", ""]]), "o.csv", account=other)
        self.do_import(up)
        line = BankStatementLine.objects.get(account=other, description="NEFT GLOBEX CORP")
        globex = self.customer("Globex Corporation")
        inv = self.invoice(globex, date="2026-08-05")
        r = self.fin.post(f"{A}/statement-lines/{line.id}/create/", {"kind": "customer_payment", "customer": globex["id"], "invoices": [inv["id"]]})
        self.assertEqual(r.status_code, 201, r.content)
        out = r.json()
        self.assertEqual(out["line"]["status"], "matched")
        from apps.sales.models import Payment
        payment = Payment.objects.get(pk=out["record"]["id"])
        self.assertEqual((payment.deposit_account_id, payment.bank_ledger_account_id), (None, other.id))
        entry = JournalEntry.objects.get(source="payment", source_id=str(payment.pk))
        self.assertIn(("1099", other.id), {(l.account.code, l.account_id) for l in entry.lines.select_related("account")})
        self.tb()

        vendor = self.fin.post(f"{P}/vendors/", {"name": "Other Bank Supplies"}).json()
        bill = self.fin.post(f"{P}/bills/", {"vendor": vendor["id"], "vendor_bill_no": "OB-1", "bill_date": "2026-08-10", "record": True,
                                             "items": [{"description": "Cables", "quantity": "1", "rate": "500"}]}).json()
        total = D(bill["net_payable"])
        up = self.upload(csv_bytes([HDFC_HEAD, ["29/08/26", "NEFT OTHER BANK SUPPLIES OB-1", "", "", f"{total}", "", ""]]), "o2.csv", account=other)
        self.do_import(up)
        vline = BankStatementLine.objects.get(account=other, description="NEFT OTHER BANK SUPPLIES OB-1")
        r = self.fin.post(f"{A}/statement-lines/{vline.id}/create/", {"kind": "vendor_payment", "vendor": vendor["id"], "bills": [bill["id"]]})
        self.assertEqual(r.status_code, 201, r.content)
        from apps.purchases.models import VendorPayment
        pay = VendorPayment.objects.get(pk=r.json()["record"]["id"])
        self.assertEqual((pay.paid_from_id, pay.bank_ledger_account_id), (None, other.id))
        self.tb()


class ReconciliationTests(MatchingBase):
    def rec(self, as_of="2026-08-31", **extra):
        r = self.fin.get(f"{self.B}/reconciliation/", {"to": as_of, **extra})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_balances_before_and_after_matching(self):
        # a cheque written on the 31st that the bank has not paid yet
        self.expense(amount="500", date="2026-08-31", party_name="Printer")
        r = self.rec()
        self.assertEqual(D(r["book_balance"]), D("10300.00"))       # 10,000 + 1,800 - 1,000 - 500
        self.assertEqual(D(r["statement_balance"]), D("10770.00"))
        self.assertEqual(D(r["expected_bank_balance"]), D("10770.00"))
        self.assertEqual(D(r["difference"]), D("0"))
        self.assertFalse(r["can_close"])
        self.assertEqual(r["unmatched_count"], 5)
        self.fin.post(f"{self.B}/matches/auto/")
        for line in BankStatementLine.objects.filter(description="SMS CHARGES"):
            self.fin.post(f"{A}/statement-lines/{line.id}/create/", {"kind": "bank_charge"})
        r = self.rec()
        self.assertEqual(D(r["book_balance"]), D("10270.00"))
        self.assertEqual((D(r["uncleared_out"]["total"]), len(r["uncleared_out"]["lines"])), (D("500.00"), 1))
        self.assertEqual((D(r["uncleared_in"]["total"]), D(r["bank_in"]["total"]), D(r["bank_out"]["total"])), (D("0"), D("0"), D("0")))
        self.assertEqual(D(r["expected_bank_balance"]), D("10770.00"))
        self.assertTrue(r["can_close"], r["blockers"])
        close = self.fin.post(f"{self.B}/reconciliation/close/", {"as_of": "2026-08-31"})
        self.assertEqual(close.status_code, 201, close.content)
        rec = BankReconciliation.objects.get()
        self.assertEqual((rec.statement_balance, rec.statement_date), (D("10770.00"), datetime.date(2026, 8, 31)))
        self.assertEqual(BankStatementLine.objects.filter(reconciliation=rec).count(), 5)
        self.assertEqual(JournalLine.objects.filter(reconciliation=rec).count(), 5)  # the 500 cheque is still open
        self.assertEqual(self.rec()["last_reconciled"], "2026-08-31")
        self.assertEqual(self.events("reconciliation").filter(action="created").count(), 1)
        # matches inside a finished reconciliation are locked
        m = BankMatch.objects.first()
        self.assertEqual(self.fin.post(f"{A}/bank-matches/{m.id}/undo/").status_code, 400)
        self.assertEqual(self.fin.post(f"{self.B}/reconciliation/close/", {"as_of": "2026-08-31"}).status_code, 400)
        # an older line imported now would land inside the closed period
        up = self.upload(csv_bytes([HDFC_HEAD, ["15/08/26", "LATE LINE", "", "", "1.00", "", ""]]), "late.csv")
        r = self.fin.post(f"{A}/statements/{up['statement']['id']}/import/", {"mapping": up["mapping"]})
        self.assertEqual(r.status_code, 400)  # its only line is refused, so nothing imports
        dry = self.do_import(up, dry_run=True)["summary"]
        self.assertIn("last reconciled", dry["problems"][0]["reason"])
        # undoing the reconciliation frees the lines again
        self.assertEqual(self.fin.post(f"{A}/reconciliations/{rec.id}/undo/").status_code, 204)
        self.assertEqual(BankStatementLine.objects.filter(reconciliation__isnull=False).count(), 0)
        self.assertEqual(self.fin.post(f"{A}/bank-matches/{m.id}/undo/").status_code, 204)

    def test_missing_items_show_as_a_difference(self):
        # books started with 2,000 already in the bank but the opening balance was never entered
        self.import_csv([HDFC_HEAD, ["30/08/26", "OPENING FUNDS LEFT", "", "", "", "", ""], ["31/08/26", "X", "", "", "", "1.00", "12,771.00"]], "z.csv")
        r = self.rec()
        self.assertEqual(D(r["difference"]), D("2000.00"))
        self.assertIn("differ", " ".join(r["blockers"]))

    def test_balance_can_be_entered_when_the_statement_has_none(self):
        BankStatementLine.objects.update(balance=None)
        r = self.rec()
        self.assertIsNone(r["statement_balance"])
        self.assertIn("Enter the closing balance", " ".join(r["blockers"]))
        r = self.rec(statement_balance="10770")
        self.assertEqual((D(r["difference"]), r["statement_balance_source"]), (D("0"), "entered"))
        self.assertEqual(self.fin.get(f"{self.B}/reconciliation/", {"statement_balance": "abc"}).status_code, 400)

    def test_entries_from_before_the_first_statement_can_be_marked_cleared(self):
        e = self.journal([("bank", {"debit": "300"}), ("other_income", {"credit": "300"})], date="2026-07-15", narration="Old receipt")
        jl = JournalLine.objects.get(entry_id=e["id"], account=self.bank)
        self.assertIn(str(jl.id), {x["id"] for x in self.rec()["uncleared_in"]["lines"]})
        late = self.book_line("expense", self.rent["id"])
        self.assertEqual(self.fin.post(f"{self.B}/cleared/", {"journal_lines": [str(late.id)]}).status_code, 400)  # after the statement starts
        r = self.fin.post(f"{self.B}/cleared/", {"journal_lines": [str(jl.id)]})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotIn(str(jl.id), {x["id"] for x in self.rec()["uncleared_in"]["lines"]})
        self.assertEqual(BankMatch.objects.get().kind, "cleared")

    def test_overview_lists_each_bank_account(self):
        rows = self.fin.get(f"{A}/bank-rec/").json()
        row = next(r for r in rows if r["id"] == str(self.bank.id))
        self.assertEqual((row["to_match"], D(row["statement_balance"]), row["statements"]), (5, D("10770.00"), 1))
        self.assertFalse(any(r["code"] == self.acct("cash").code for r in rows))  # cash has no statement


class PermissionTests(MatchingBase):
    def test_view_needs_accounting_view_and_changes_need_accounting_manage(self):
        viewer = viewer_client("viewer", "accounting.view", role=Role.HR_MANAGER)
        line = BankStatementLine.objects.first()
        st = BankStatement.objects.get()
        for url in (f"{A}/bank-rec/", f"{self.B}/lines/", f"{self.B}/statements/", f"{self.B}/book-entries/", f"{self.B}/reconciliation/",
                    f"{A}/statements/{st.id}/", f"{A}/statement-lines/{line.id}/create-options/"):
            self.assertEqual(viewer.get(url).status_code, 200, url)
            self.assertEqual(self.api_staff.get(url).status_code, 403, url)
            self.assertEqual(self.anon.get(url).status_code, 401, url)
        f = SimpleUploadedFile("s.csv", csv_bytes(AUGUST))
        self.assertEqual(viewer.post(f"{self.B}/statements/", {"file": f}, format="multipart").status_code, 403)
        for url, body in ((f"{self.B}/matches/", {"statement_lines": [str(line.id)], "journal_lines": []}),
                          (f"{self.B}/matches/auto/", {}), (f"{self.B}/cleared/", {"journal_lines": []}),
                          (f"{self.B}/reconciliation/close/", {"as_of": "2026-08-31"}),
                          (f"{A}/statements/{st.id}/import/", {"mapping": {}}),
                          (f"{A}/statement-lines/{line.id}/ignore/", {"reason": "x"}),
                          (f"{A}/statement-lines/{line.id}/unignore/", {}),
                          (f"{A}/statement-lines/{line.id}/create/", {"kind": "bank_charge"})):
            self.assertEqual(viewer.post(url, body).status_code, 403, url)
        self.assertEqual(viewer.delete(f"{A}/statements/{st.id}/").status_code, 403)
        partner = client_for(make_user("part", Role.PARTNER))  # partners hold every permission
        self.assertEqual(partner.post(f"{A}/statement-lines/{line.id}/ignore/", {"reason": "Partner checked"}).status_code, 200)

    def test_other_companies_records_are_not_found(self):
        self.assertEqual(self.fin.get(f"{A}/bank/00000000-0000-0000-0000-000000000000/lines/").status_code, 404)
        self.assertEqual(self.fin.post(f"{A}/bank-matches/00000000-0000-0000-0000-000000000000/undo/").status_code, 404)
        self.assertEqual(self.fin.post(f"{A}/statement-lines/00000000-0000-0000-0000-000000000000/ignore/", {"reason": "x"}).status_code, 404)
