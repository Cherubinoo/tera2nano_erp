"""Reading bank statement files: CSV, Excel (.xlsx and old .xls), text-based PDF and OFX, and turning their cells into dates
and amounts.

Banks lay their statements out differently, so a CSV, Excel or PDF file is read as a plain grid of text; the column mapping
(which column holds the date, the narration, the withdrawals ...) is guessed from the header names and confirmed by the
person importing. OFX files are structured and need no mapping. Nothing here touches the database.
"""
import csv
import datetime
import io
import re
import zipfile
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from xml.etree import ElementTree

import fitz  # PyMuPDF, for text-based PDF statements
import xlrd
from django.core.exceptions import ValidationError

CENT = Decimal("0.01")
MAX_ROWS = 5000
MAX_XML_BYTES = 60 * 1024 * 1024  # an .xlsx sheet larger than this when unzipped is refused (protects against zip bombs)
MAX_PDF_BYTES = 20 * 1024 * 1024  # a PDF statement larger than this is refused (protects against pathological files)
MAX_PDF_PAGES = 50  # only the first 50 pages of a PDF statement are read
FIELDS = ("date", "description", "reference", "debit", "credit", "amount", "drcr", "balance")
FIELD_LABELS = {
    "date": "Date", "description": "Description / narration", "reference": "Reference / cheque no.",
    "debit": "Withdrawal (money out)", "credit": "Deposit (money in)", "amount": "Single amount column",
    "drcr": "Dr / Cr column", "balance": "Balance",
}


# --------------------------------------------------------------------------- file type

def detect_format(head, filename=""):
    """'xlsx', 'xls', 'pdf', 'ofx' or 'csv' from the first bytes of the file; anything else is refused in plain words."""
    name = (filename or "").lower()
    if head.startswith(b"PK\x03\x04"):
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    if head.startswith(b"%PDF"):
        return "pdf"
    text = head.decode("latin-1", errors="replace").upper()
    if "OFXHEADER" in text or "<OFX>" in text or name.endswith((".ofx", ".qfx")):
        return "ofx"
    if b"\x00" in head[:512]:
        raise ValidationError({"file": "This file cannot be read as a statement. Upload a CSV, Excel (.xlsx or .xls), PDF or OFX file."})
    return "csv"


def _decode(data):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


# --------------------------------------------------------------------------- grids

def read_csv(data):
    text = _decode(data)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        rows.append([c.strip() for c in row])
        if len(rows) > MAX_ROWS + 100:
            raise ValidationError({"file": f"The statement has more than {MAX_ROWS} rows. Download a shorter period and import it in parts."})
    return rows


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _col_index(ref):
    letters = "".join(ch for ch in ref if ch.isalpha())
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _xml(zf, name):
    info = zf.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise ValidationError({"file": "This Excel file is too large to read. Download a shorter period."})
    return ElementTree.fromstring(zf.read(name))


def read_xlsx(data):
    """The first worksheet of an .xlsx file as rows of text, using only the standard library. Dates stored as Excel serial
    numbers come back as numbers; `parse_date` understands them."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = set(zf.namelist())
    except zipfile.BadZipFile:
        raise ValidationError({"file": "This Excel file is damaged or is not an .xlsx file."})
    if "xl/workbook.xml" not in names:
        raise ValidationError({"file": "This file is not an Excel workbook (.xlsx)."})
    shared = []
    if "xl/sharedStrings.xml" in names:
        for si in _xml(zf, "xl/sharedStrings.xml").findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    sheet = "xl/worksheets/sheet1.xml"
    try:
        wb = _xml(zf, "xl/workbook.xml")
        first = wb.find("m:sheets/m:sheet", NS)
        rels = _xml(zf, "xl/_rels/workbook.xml.rels") if "xl/_rels/workbook.xml.rels" in names else None
        if first is not None and rels is not None:
            rid = first.get(REL_NS)
            for rel in rels:
                if rel.get("Id") == rid:
                    target = rel.get("Target", "").lstrip("/")
                    sheet = target if target.startswith("xl/") else f"xl/{target}"
    except ElementTree.ParseError:
        raise ValidationError({"file": "This Excel file is damaged and cannot be read."})
    if sheet not in names:
        raise ValidationError({"file": "The workbook has no worksheet to read."})
    try:
        root = _xml(zf, sheet)
    except ElementTree.ParseError:
        raise ValidationError({"file": "This Excel file is damaged and cannot be read."})
    rows = []
    for row in root.iter(f"{{{NS['m']}}}row"):
        cells = {}
        for pos, c in enumerate(row.findall("m:c", NS)):
            idx = _col_index(c.get("r")) if c.get("r") else pos
            kind = c.get("t", "n")
            v = c.find("m:v", NS)
            if kind == "s":
                value = shared[int(v.text)] if v is not None and v.text and v.text.isdigit() and int(v.text) < len(shared) else ""
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
            elif kind == "b":
                value = "TRUE" if v is not None and v.text == "1" else "FALSE"
            else:
                value = v.text if v is not None and v.text is not None else ""
            cells[idx] = value.strip()
        if cells:
            width = max(cells) + 1
            line = [cells.get(i, "") for i in range(min(width, 60))]
        else:
            line = []
        rownum = int(row.get("r")) if (row.get("r") or "").isdigit() else len(rows) + 1
        while len(rows) < rownum - 1:  # keep row numbers true to the sheet when empty rows are left out of the file
            rows.append([])
        rows.append(line)
        if len(rows) > MAX_ROWS + 100:
            raise ValidationError({"file": f"The statement has more than {MAX_ROWS} rows. Download a shorter period and import it in parts."})
    return rows


def read_xls(data):
    """The first worksheet of an old .xls (Excel 97-2003) file as rows of text, using xlrd. Dates come back as ISO text
    (`parse_date` understands that too) so the rest of the pipeline does not need to know which Excel format it read."""
    try:
        wb = xlrd.open_workbook(file_contents=data)
    except Exception:
        raise ValidationError({"file": "This Excel file is damaged or cannot be read."})
    if wb.nsheets == 0:
        raise ValidationError({"file": "The workbook has no worksheet to read."})
    sheet = wb.sheet_by_index(0)
    rows = []
    for r in range(sheet.nrows):
        line = []
        for c in range(min(sheet.ncols, 60)):
            cell = sheet.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                try:
                    dt = xlrd.xldate.xldate_as_datetime(cell.value, wb.datemode)
                    value = dt.date().isoformat() if dt.time() == datetime.time() else dt.isoformat(sep=" ")
                except xlrd.xldate.XLDateError:
                    value = str(cell.value)
            elif cell.ctype == xlrd.XL_CELL_NUMBER:
                value = str(int(cell.value)) if cell.value == int(cell.value) else str(cell.value)
            elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                value = "TRUE" if cell.value else "FALSE"
            elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                value = ""
            else:
                value = str(cell.value)
            line.append(value.strip())
        rows.append(line)
        if len(rows) > MAX_ROWS + 100:
            raise ValidationError({"file": f"The statement has more than {MAX_ROWS} rows. Download a shorter period and import it in parts."})
    return rows


def _pdf_lines(words):
    """The words of one page (`page.get_text("words")`) grouped into visual lines by y-position, top to bottom."""
    if not words:
        return []
    heights = [w[3] - w[1] for w in words if w[3] > w[1]]
    tol = max(2.0, (sorted(heights)[len(heights) // 2] if heights else 4.0) * 0.6)
    lines = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        y_mid = (w[1] + w[3]) / 2
        if lines and abs(y_mid - lines[-1]["y"]) <= tol:
            lines[-1]["words"].append(w)
        else:
            lines.append({"y": y_mid, "words": [w]})
    return lines


def _word_clusters(words):
    """Words of one line, split into left-to-right clusters wherever the x-gap between two of them is wide (a reasonable
    stand-in for a column boundary, since a text-based PDF carries no column structure of its own)."""
    ws = sorted(words, key=lambda w: w[0])
    clusters, cur = [], [ws[0]]
    for w in ws[1:]:
        gap = w[0] - cur[-1][2]
        threshold = max(8.0, (cur[-1][3] - cur[-1][1]) * 1.5)
        if gap > threshold:
            clusters.append(cur)
            cur = [w]
        else:
            cur.append(w)
    clusters.append(cur)
    return clusters


def read_pdf(data):
    """A text-based bank statement PDF as rows of text, using PyMuPDF: words are grouped into visual lines, then into
    cells by the gaps between them. Column positions are taken from the line with the most clusters (usually the header
    row, which rarely has a blank cell); every other line's clusters are matched to the nearest of those columns, so a row
    with a blank withdrawal or deposit cell still lines up under the right heading instead of shifting the row along. A
    scanned or image-only PDF (no real text layer) is refused."""
    if len(data) > MAX_PDF_BYTES:
        raise ValidationError({"file": f"This PDF is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB. Download a shorter period."})
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise ValidationError({"file": "This PDF is damaged and cannot be read."})
    try:
        if doc.page_count == 0:
            raise ValidationError({"file": "This PDF has no pages."})
        pages = min(doc.page_count, MAX_PDF_PAGES)
        lines = []
        for i in range(pages):
            lines.extend(_pdf_lines(doc[i].get_text("words")))
    finally:
        doc.close()
    total_chars = sum(len(w[4]) for line in lines for w in line["words"])
    if total_chars < 30 * pages:
        raise ValidationError({"file": "This PDF has no readable text (it looks like a scanned image). Upload a CSV or Excel statement instead."})
    clustered = [_word_clusters(line["words"]) for line in lines]
    anchors = [(min(w[0] for w in c), max(w[2] for w in c)) for c in max(clustered, key=len, default=[])]
    rows = []
    for clusters in clustered:
        cells = [""] * len(anchors)
        for c in clusters:
            cx = (min(w[0] for w in c) + max(w[2] for w in c)) / 2
            idx = min(range(len(anchors)), key=lambda i: abs(cx - (anchors[i][0] + anchors[i][1]) / 2)) if anchors else 0
            text = " ".join(w[4] for w in sorted(c, key=lambda w: w[0]))
            cells[idx] = f"{cells[idx]} {text}".strip() if cells[idx] else text
        rows.append(cells)
        if len(rows) > MAX_ROWS + 100:
            raise ValidationError({"file": f"The statement has more than {MAX_ROWS} rows. Download a shorter period and import it in parts."})
    return rows


def read_grid(data, fmt):
    if fmt == "xlsx":
        rows = read_xlsx(data)
    elif fmt == "xls":
        rows = read_xls(data)
    elif fmt == "pdf":
        rows = read_pdf(data)
    else:
        rows = read_csv(data)
    if not any(any(c for c in r) for r in rows):
        raise ValidationError({"file": "The file has no rows."})
    return rows


# --------------------------------------------------------------------------- OFX

_OFX_TAG = re.compile(r"<([A-Z0-9.]+)>([^<\r\n]*)", re.I)


def read_ofx(data):
    """[{row, date, amount, description, reference, fitid}] and the ledger balance {amount, date} if the file gives one."""
    text = _decode(data)
    blocks = re.findall(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|(?=</BANKTRANLIST>))", text, re.S | re.I)
    out = []
    for n, block in enumerate(blocks, 1):
        tags = {}
        for k, v in _OFX_TAG.findall(block):
            tags.setdefault(k.upper(), v.strip())
        out.append({"row": n, "date_text": tags.get("DTPOSTED", ""), "amount_text": tags.get("TRNAMT", ""),
                    "description": " ".join(x for x in (tags.get("NAME", ""), tags.get("MEMO", "")) if x).strip(),
                    "reference": tags.get("CHECKNUM") or tags.get("REFNUM") or "", "fitid": tags.get("FITID", "")})
    balance = None
    m = re.search(r"<LEDGERBAL>(.*?)(?:</LEDGERBAL>|$)", text, re.S | re.I)
    if m:
        tags = {k.upper(): v.strip() for k, v in _OFX_TAG.findall(m.group(1))}
        if tags.get("BALAMT"):
            balance = {"amount": tags["BALAMT"], "date": tags.get("DTASOF", "")}
    if not out:
        raise ValidationError({"file": "No transactions were found in this OFX file."})
    return out, balance


def ofx_date(text):
    m = re.match(r"\s*(\d{4})(\d{2})(\d{2})", text or "")
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# --------------------------------------------------------------------------- dates and amounts

MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
MONTHS["sept"] = 9
EXCEL_EPOCH = datetime.date(1899, 12, 30)


def _year(y):
    y = int(y)
    if y < 100:
        y += 2000 if y < 80 else 1900
    return y


def parse_date(value, order="dmy"):
    """A date from the ways banks write them: 05/08/2026, 05-08-26, 05.08.2026, 05-Aug-2026, 05 Aug 26, 2026-08-05, Aug 5, 2026,
    with or without a time, or an Excel serial number. `order` 'dmy' (day first, as in India) or 'mdy' settles 05/08/2026."""
    s = str(value or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{5}(\.\d+)?", s):  # an Excel date serial (e.g. 46239 = 05 Aug 2026)
        n = int(float(s))
        if 20000 <= n <= 80000:
            return EXCEL_EPOCH + datetime.timedelta(days=n)
        return None
    s = re.split(r"[T ](?=\d{1,2}:\d{2})", s)[0].strip()  # drop a time part
    try:
        m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.fullmatch(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{2}|\d{4})", s)
        if m:
            a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
            d, mo = (a, b) if order != "mdy" else (b, a)
            return datetime.date(y, mo, d)
        m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?[-/. ]*([A-Za-z]{3,9})[-/., ]*(\d{2}|\d{4})", s)
        if m and m.group(2)[:3].lower() in MONTHS:
            return datetime.date(_year(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        m = re.fullmatch(r"([A-Za-z]{3,9})[-/. ]+(\d{1,2}),?[-/. ]+(\d{2}|\d{4})", s)
        if m and m.group(1)[:3].lower() in MONTHS:
            return datetime.date(_year(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except (ValueError, TypeError):
        return None
    return None


_DRCR_SUFFIX = re.compile(r"^(?P<pre>dr\.?|cr\.?)?\s*(?P<num>.*?)\s*(?P<post>dr\.?|cr\.?)?$", re.I)


def parse_amount(value):
    """(amount, side) from text such as "1,23,456.00", "₹ 500", "(250.00)", "500-", "1,000.00 Cr", "Dr 75".

    amount is a Decimal rounded to paise (its sign as written; brackets and a trailing minus mean negative), or None when the
    cell is empty or a dash. side is 'dr', 'cr' or None when the text carries a Dr / Cr marker. Raises ValueError for text
    that is not an amount."""
    s = str(value or "").strip()
    if s in ("", "-", "--", "—", "–", "nil", "NIL", "Nil", "N/A", "n/a"):
        return None, None
    s = re.sub(r"(?i)(₹|inr|rs\.?)", "", s).replace(",", "").replace(" ", " ").strip()
    m = _DRCR_SUFFIX.match(s)
    side = None
    if m and (m.group("pre") or m.group("post")):
        side = (m.group("pre") or m.group("post")).lower()[:2]
        s = m.group("num").strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.endswith("-"):
        negative, s = True, s[:-1].strip()
    if s.startswith("+"):
        s = s[1:].strip()
    if s.startswith("-"):
        negative, s = not negative, s[1:].strip()
    s = s.replace(" ", "")
    if not s:
        return None, side
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(value)
    if not d.is_finite():
        raise ValueError(value)
    d = d.quantize(CENT, rounding=ROUND_HALF_UP)
    if abs(d) > Decimal("100000000000"):
        raise ValueError(value)
    return (-d if negative else d), side


# --------------------------------------------------------------------------- column mapping

def _norm(text):
    return re.sub(r"[^a-z]+", " ", str(text or "").lower()).strip()


# In order: the first rule a header matches decides its field; each field is taken by the first header that matches it.
RULES = [
    ("drcr", r"^(dr cr|cr dr|dr or cr|debit credit|credit debit|txn type|transaction type|type|d c|c d)$"),
    ("balance", r"balance|bal$|^bal "),
    ("reference", r"chq|cheque|check no|ref|utr|instrument"),
    ("debit", r"withdraw|debit|^dr$|^dr amount|paid out|money out|payments?$"),
    ("credit", r"deposit|credit|^cr$|^cr amount|paid in|money in|receipts?$"),
    ("date", r"^(txn|tran|transaction|trans|posting|post|book|booking)?\s*date$|^date$|^txn dt$|^tran dt$"),
    ("date", r"value date|value dt|date"),
    ("description", r"narration|description|particulars|details|remarks|transaction details|memo|naration"),
    ("amount", r"amount|amt"),
]


def guess_mapping(headers):
    """{field: column index} guessed from header names; fields with no matching header are left out.

    Each header belongs to the first rule it matches; a field goes to the header with the strongest rule (so "Transaction
    Date" wins over an earlier "Value Date"), the leftmost on a tie."""
    candidates = []
    for idx, header in enumerate(headers):
        h = _norm(header)
        if not h:
            continue
        for rank, (field, pattern) in enumerate(RULES):
            if re.search(pattern, h):
                candidates.append((rank, idx, field))
                break
    mapping = {}
    for _rank, idx, field in sorted(candidates):
        if field not in mapping:
            mapping[field] = idx
    if ("debit" in mapping or "credit" in mapping) and "amount" in mapping:
        mapping.pop("amount")
    if "amount" not in mapping:
        mapping.pop("drcr", None)
    return mapping


def find_header_row(rows):
    """The index of the row that looks like the column headings (statements often start with account details)."""
    for i, row in enumerate(rows[:40]):
        m = guess_mapping(row)
        if "date" in m and ({"debit", "credit", "amount"} & set(m)) and sum(1 for c in row if c) >= 3:
            return i
    for i, row in enumerate(rows[:40]):
        if sum(1 for c in row if c) >= 3:
            return i
    return 0


def clean_mapping(raw, width):
    """Validate a mapping sent by the browser: column indices in range, the options known. Returns a tidy dict."""
    if not isinstance(raw, dict):
        raise ValidationError({"mapping": "Send the column mapping."})
    out = {}
    for field in FIELDS:
        v = raw.get(field)
        if v in (None, "", -1, "-1"):
            continue
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise ValidationError({"mapping": f"Choose a column for {FIELD_LABELS[field]}."})
        if not 0 <= v < width:
            raise ValidationError({"mapping": f"The column chosen for {FIELD_LABELS[field]} is not in the file."})
        out[field] = v
    if "date" not in out:
        raise ValidationError({"mapping": "Choose the column that holds the date."})
    if not ({"debit", "credit", "amount"} & set(out)):
        raise ValidationError({"mapping": "Choose the withdrawal and deposit columns, or the single amount column."})
    if "amount" in out and ({"debit", "credit"} & set(out)):
        raise ValidationError({"mapping": "Use either withdrawal and deposit columns or a single amount column, not both."})
    if "drcr" in out and "amount" not in out:
        raise ValidationError({"mapping": "A Dr / Cr column goes with a single amount column."})
    order = raw.get("date_order") or "dmy"
    if order not in ("dmy", "mdy"):
        raise ValidationError({"mapping": "Choose how dates are written."})
    sign = raw.get("amount_sign") or "deposit_positive"
    if sign not in ("deposit_positive", "withdrawal_positive"):
        raise ValidationError({"mapping": "Choose what a plus amount means."})
    try:
        header_row = int(raw.get("header_row") or 0)
    except (TypeError, ValueError):
        header_row = 0
    out.update(date_order=order, amount_sign=sign, header_row=max(header_row, 0))
    return out


# --------------------------------------------------------------------------- rows to transactions

SUMMARY_WORDS = re.compile(r"opening balance|closing balance|total|brought forward|carried forward|\bb/f\b|\bc/f\b|balance b|"
                           r"statement summary|end of statement|grand total|available balance", re.I)
DR_WORDS = {"dr", "d", "debit", "withdrawal", "wdl", "w", "out", "db"}
CR_WORDS = {"cr", "c", "credit", "deposit", "dep", "in"}


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def interpret_row(row, mapping):
    """('ok', data) | ('skip', reason) | ('problem', reason) for one row of the grid under a mapping.

    data: {date, description, reference, amount (signed, + money in), balance}."""
    if not any(str(c or "").strip() for c in row):
        return "skip", "blank"
    order = mapping.get("date_order", "dmy")
    text = " ".join(str(c or "") for c in row)
    date_text = _cell(row, mapping.get("date"))
    try:
        amount, problem = _row_amount(row, mapping)
    except ValueError as exc:
        amount, problem = None, str(exc)
    on = parse_date(date_text, order)
    if on is None:
        # Text rows (headings repeated on each page, totals, footers) have neither a date nor a readable amount.
        if amount is None or SUMMARY_WORDS.search(text):
            return "skip", "heading or total"
        if not date_text:
            return "problem", "There is an amount but no date."
        return "problem", f"Could not read the date \"{date_text[:40]}\"."
    if problem:
        return "problem", problem
    if amount is None or amount == 0:
        if SUMMARY_WORDS.search(text):
            return "skip", "heading or total"
        return "problem", "There is no withdrawal or deposit amount."
    balance = None
    if mapping.get("balance") is not None:
        try:
            balance, side = parse_amount(_cell(row, mapping["balance"]))
        except ValueError:
            balance, side = None, None
        if balance is not None and side == "dr":
            balance = -abs(balance)
    return "ok", {"date": on, "description": _cell(row, mapping.get("description"))[:500],
                  "reference": _cell(row, mapping.get("reference"))[:120], "amount": amount, "balance": balance}


def _row_amount(row, mapping):
    """(signed amount or None, problem text or None)."""
    if mapping.get("amount") is not None:
        raw = _cell(row, mapping["amount"])
        try:
            value, side = parse_amount(raw)
        except ValueError:
            raise ValueError(f"Could not read the amount \"{raw[:40]}\".")
        if value is None or value == 0:
            return None, None
        if mapping.get("drcr") is not None:
            marker = _norm(_cell(row, mapping["drcr"])).replace(" ", "")
            if marker in DR_WORDS:
                side = "dr"
            elif marker in CR_WORDS:
                side = "cr"
            elif side is None:
                return None, f"The Dr / Cr column says \"{_cell(row, mapping['drcr'])[:20]}\"; expected Dr or Cr."
        if side == "dr":
            return -abs(value), None
        if side == "cr":
            return abs(value), None
        return (value if mapping.get("amount_sign", "deposit_positive") == "deposit_positive" else -value), None
    out_raw, in_raw = _cell(row, mapping.get("debit")), _cell(row, mapping.get("credit"))
    try:
        out_v, _ = parse_amount(out_raw) if mapping.get("debit") is not None else (None, None)
    except ValueError:
        raise ValueError(f"Could not read the withdrawal \"{out_raw[:40]}\".")
    try:
        in_v, _ = parse_amount(in_raw) if mapping.get("credit") is not None else (None, None)
    except ValueError:
        raise ValueError(f"Could not read the deposit \"{in_raw[:40]}\".")
    out_v = abs(out_v) if out_v else None
    in_v = abs(in_v) if in_v else None
    if out_v and in_v:
        return None, "Both a withdrawal and a deposit are filled in on this row."
    if out_v:
        return -out_v, None
    if in_v:
        return in_v, None
    return None, None
